
from pathlib import Path
from unittest import TestCase

import asynctest
import machine
from context import Context
from uasyncio import StreamReader, StreamWriter
from utils.constants import SRC_PATH
from watchdog import Watchdog
from webswitch import WebServer


def get_all_config_files():
    path = Path('.').resolve()
    assert path == SRC_PATH, 'Error: Current work dir: %s != %s' % (path, SRC_PATH)
    config_files = list(path.glob('_config_*.py')) + list(path.glob('_config_*.json'))
    return config_files


class MicropythonMixin:
    def setUp(self):
        super().setUp()
        machine.RTC().datetime((2019, 5, 1, 4, 13, 12, 11, 0))
        config_files = get_all_config_files()
        assert not config_files, f'Config files exists before test start: %s' % config_files
        self.context = Context
        self.context.power_timer_timers = None
        print('No config files, ok.')

    def tearDown(self):
        config_files = get_all_config_files()
        for config_file in config_files:
            print('Test cleanup, remove: %s' % config_file)
            config_file.unlink()

        super().tearDown()


class MicropythonBaseTestCase(MicropythonMixin, TestCase):
    pass


class WebServerTestCase(MicropythonMixin, asynctest.TestCase):

    def setUp(self):
        super().setUp()
        context = Context  # no instance!

        context.watchdog = Watchdog(context)

        self.web_server = WebServer(context=context, version='v0.1')

    async def get_request(self, request_line):
        reader = asynctest.mock.Mock(StreamReader)
        writer = StreamWriter()

        reader.readline.return_value = request_line

        await self.web_server.request_handler(reader, writer)

        return writer.get_response()

    def assert_response(self, response, expected_response):
        if response == expected_response:
            return
        print('-' * 100)
        print(response)
        print('-' * 100)
        assert expected_response == response

    def assert_response_parts(self, response, parts):
        missing_parts = []
        for part in parts:
            if part not in response:
                missing_parts.append(part)

        if not missing_parts:
            return

        print('-' * 100)
        print(response)
        print('-' * 100)
        print('Missing parts:')
        for part in missing_parts:
            print(f'\t* {part!r}')
        print('-' * 100)
        raise AssertionError(f'Missing {len(missing_parts)} parts in html!')
