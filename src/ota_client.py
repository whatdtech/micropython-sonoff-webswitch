"""
    soft-OTA Client for micropython devices
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    Wait for OTA Server and run commands from him
    to update local files.
"""
import gc
import sys

import machine
import uasyncio
import ubinascii
import uhashlib
import uos
import utime
from micropython import const

sys.modules.clear()  # noqa isort:skip
gc.collect()  # noqa isort:skip


_CONNECTION_TIMEOUT = const(15)
_OTA_TIMEOUT = const(60)
_PORT = const(8267)
_CHUNK_SIZE = const(512)
_BUFFER = bytearray(_CHUNK_SIZE)
_FILE_TYPE = const(0x8000)


def reset(reason):
    for no in range(2, 0, -1):
        print('%i Reset because: %s' % (no, reason))
        utime.sleep(1)
    machine.reset()
    utime.sleep(1)
    sys.exit(-1)


class Timeout:
    def __init__(self, reason, timeout_sec):
        self.reason = reason
        self.satisfied = False
        self.timer = machine.Timer(-1)
        self.timer.init(
            period=timeout_sec * 1000,
            mode=machine.Timer.PERIODIC,
            callback=self._timer_callback
        )

    def _timer_callback(self, timer):

        if not self.satisfied:
            reset(self.reason)

    def deinit(self):
        self.timer.deinit()


class SoftOtaUpdate:
    def __init__(self):
        self.timeout = None  # Will be set in run() and __call__()

    def run(self):
        loop = uasyncio.get_event_loop()
        loop.create_task(uasyncio.start_server(self, '0.0.0.0', _PORT))

        print('Wait %i sec for soft-OTA connection on port %i' % (_CONNECTION_TIMEOUT, _PORT))
        self.timeout = Timeout(reason='soft-OTA no connection', timeout_sec=_CONNECTION_TIMEOUT)

        try:
            loop.run_forever()
        except Exception as e:
            sys.print_exception(e)
        except SystemExit as e:
            if e.args[0] == 0:
                reset('soft-OTA Update complete successfully!')
            reset('soft-OTA unknown sys exit code.')
        reset('soft-OTA unknown error')

    async def read_line_string(self, reader):
        data = await reader.readline()
        return data.rstrip(b'\n').decode('utf-8')

    async def write_line_string(self, writer, text):
        await writer.awrite(b'%s\n' % text.encode('utf-8'))

    async def error(self, writer, text, do_reset=True):
        print('ERROR: %s' % text)
        await self.write_line_string(writer, text)
        if do_reset:
            reset(text)

    async def __call__(self, reader, writer):
        self.timeout.deinit()

        self.timeout = Timeout(reason='soft-OTA timeout', timeout_sec=_OTA_TIMEOUT)
        address = writer.get_extra_info('peername')
        print('Accepted connection from %s:%s' % address)
        while True:
            command = await self.read_line_string(reader)
            print('Receive command:', command)

            gc.collect()
            command = 'command_%s' % command
            if not hasattr(self, command):
                await self.error(writer, 'Command unknown!', do_reset=False)
            else:
                try:
                    await getattr(self, command)(reader, writer)
                except Exception as e:
                    sys.print_exception(e)
                    await self.error(writer, 'Command error', do_reset=False)

    async def command_send_ok(self, reader, writer):
        await self.write_line_string(writer, 'OK')

    async def command_exit(self, reader, writer):
        await self.command_send_ok(reader, writer)
        self.timeout.deinit()
        utime.sleep(1)  # Don't close connection before server processed 'OK'
        sys.exit(0)

    async def command_chunk_size(self, reader, writer):
        await self.write_line_string(writer, '%i' % _CHUNK_SIZE)

    async def command_mpy_version(self, reader, writer):
        """
        Return sys.implementation.mpy that contains all information about
        current mpy version and flags supported by your MicroPython system.
        See:
            http://docs.micropython.org/en/latest/reference/mpyfiles.html
            https://forum.micropython.org/viewtopic.php?f=2&t=7506
        """
        await self.write_line_string(writer, '%i' % sys.implementation.mpy)

    async def command_frozen_info(self, reader, writer):
        """
        Send information about own frozen modules.
        """
        print('Send frozen modules info...')
        from frozen_modules_info import FROZEN_FILE_INFO
        for filename, size, sha256 in FROZEN_FILE_INFO:
            await writer.awrite(b'%s\r%i\r%s\r\n' % (filename, size, sha256))
        await writer.awrite(b'\n\n')
        print('Frozen modules info send, ok.')

    async def command_flash_info(self, reader, writer):
        """
        Send information about files stored in flash filesystem
        """
        print('Send files info...')
        for name, file_type, inode, size in uos.ilistdir():
            if file_type != _FILE_TYPE:
                print(' *** Skip: %s' % name)
                continue

            await writer.awrite(b'%s\r%i\r' % (name, size))

            sha256 = uhashlib.sha256()
            with open(name, 'rb') as f:
                while True:
                    count = f.readinto(_BUFFER, _CHUNK_SIZE)
                    if count < _CHUNK_SIZE:
                        sha256.update(_BUFFER[:count])
                        break
                    else:
                        sha256.update(_BUFFER)

            await writer.awrite(ubinascii.hexlify(sha256.digest()))
            await writer.awrite(b'\r\n')
        await writer.awrite(b'\n\n')
        print('Files info send, ok.')

    async def command_receive_file(self, reader, writer):
        """
        Store a new/updated file on local micropython device.
        """
        print('receive file', end=' ')
        file_name = await self.read_line_string(reader)
        file_size = int(await self.read_line_string(reader))
        file_sha256 = await self.read_line_string(reader)
        await self.command_send_ok(reader, writer)
        print('%r %i Bytes SHA256: %s' % (file_name, file_size, file_sha256))

        temp_file_name = '%s.temp' % file_name
        try:
            with open(temp_file_name, 'wb') as f:
                sha256 = uhashlib.sha256()
                received = 0
                while True:
                    print('.', end='')
                    data = await reader.read(_CHUNK_SIZE)
                    if not data:
                        await self.error(writer, 'No file data')

                    f.write(data)
                    sha256.update(data)
                    received += len(data)
                    if received >= file_size:
                        print('completed!')
                        break

            print('Received %i Bytes' % received, end=' ')

            local_file_size = uos.stat(temp_file_name)[6]
            if local_file_size != file_size:
                await self.error(writer, 'Size error!')

            hexdigest = ubinascii.hexlify(sha256.digest()).decode('utf-8')
            if hexdigest == file_sha256:
                print('Hash OK:', hexdigest)
                print('Compare written file content', end=' ')
                sha256 = uhashlib.sha256()
                with open(temp_file_name, 'rb') as f:
                    while True:
                        count = f.readinto(_BUFFER, _CHUNK_SIZE)
                        if count < _CHUNK_SIZE:
                            sha256.update(_BUFFER[:count])
                            break
                        else:
                            sha256.update(_BUFFER)

                hexdigest = ubinascii.hexlify(sha256.digest()).decode('utf-8')
                if hexdigest == file_sha256:
                    print('Hash OK:', hexdigest)
                    try:
                        uos.remove(file_name)
                    except OSError:
                        pass  # e.g.: new file that doesn't exist, yet.

                    uos.rename(temp_file_name, file_name)

                    if file_name.endswith('.mpy'):
                        py_filename = '%s.py' % file_name.rsplit('.', 1)[0]
                        try:
                            uos.remove(py_filename)
                        except OSError:
                            pass  # *.py file doesn't exists

                    await self.command_send_ok(reader, writer)
                    return

            await self.error(writer, 'Hash error: %s' % hexdigest)
        finally:
            print('Remove temp file')
            try:
                uos.remove(temp_file_name)
            except OSError:
                pass


if __name__ == '__main__':
    SoftOtaUpdate().run()
