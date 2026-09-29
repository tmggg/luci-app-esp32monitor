#!/usr/bin/env python3
"""Run the CGI under BusyBox ash with isolated proc/sys and UCI fixtures."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

PACKAGE = Path(__file__).resolve().parents[1]

class CGI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        def write(name, text):
            p = self.root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        write('proc/stat', 'cpu 4000000000 0 100 4000000000 0 0 0 0\n')
        write('proc/meminfo', 'MemTotal: 1000 kB\nMemAvailable: 400 kB\n')
        write('proc/uptime', '123.45 0\n')
        write('proc/sys/kernel/hostname', 'test-router\n')
        for dev, link in [('eth0', '1'), ('eth1', '0'), ('lo', '1')]:
            write(f'sys/class/net/{dev}/carrier', link + '\n')
            write(f'sys/class/net/{dev}/statistics/rx_bytes', '5000000000\n')
            write(f'sys/class/net/{dev}/statistics/tx_bytes', '6000000000\n')
        write('sys/class/thermal/thermal_zone0/temp', '500\n')
        write('sys/class/hwmon/hwmon0/temp2_input', '-1200\n')
        write('functions.sh', '''config_load() { :; }
config_get() {
    local val
    eval 'val=${TEST_'"$3"'-'"$4"'}'
    export "$1=$val"
}
''')
        write('bin/sleep', f'''#!/bin/sh
printf '124.45 0\\n' > '{self.root}/proc/uptime'
printf '5000001024\\n' > '{self.root}/sys/class/net/eth0/statistics/rx_bytes'
''')
        (self.root / 'bin/sleep').chmod(0o755)
        source = (PACKAGE / 'root/www/cgi-bin/esp32-status').read_text()
        source = source.replace('/lib/functions.sh', str(self.root / 'functions.sh'))
        for prefix in ['/proc/', '/sys/', '/tmp/esp32-status.']:
            source = source.replace(prefix, str(self.root) + prefix)
        source = source.replace('sleep 1', str(self.root / 'bin/sleep') + ' 1')
        self.script = self.root / 'cgi'
        self.script.write_text(source)

    def run_cgi(self, **changes):
        env = dict(os.environ, PATH=str(self.root / 'bin') + ':' + os.environ['PATH'],
                   TEST_enabled='1', TEST_api_token='test12345', HTTP_X_API_TOKEN='test12345',
                   TEST_wan_device='eth0', QUERY_STRING='', REQUEST_METHOD='GET')
        env.update(changes)
        (self.root / 'tmp').mkdir(exist_ok=True)
        result = subprocess.run(['busybox', 'ash', str(self.script)], env=env, capture_output=True, text=True, check=True)
        header, body = result.stdout.split('\n\n', 1)
        self.assertFalse(list((self.root / 'tmp').glob('esp32-status.*')))
        return header, json.loads(body)

    def test_default_metrics(self):
        _, body = self.run_cgi()
        self.assertTrue(body['ok'])
        self.assertEqual(body['memory_percent'], 60)
        self.assertEqual(body['temperature_c'], 0.5)
        self.assertEqual(body['wan_device'], 'eth0')
        self.assertEqual(body['interface_count'], 1)
        self.assertEqual(body['interfaces'][0]['download_bps'], 1024)
        self.assertEqual(body['interfaces'][0]['download_bytes'], 5000001024)

    def test_system_date_time(self):
        # POSIX TZ uses the opposite sign: CST-8 is UTC+08:00.
        for tz, offset in [('UTC0', 0), ('CST-8', 8), ('EST5', -5)]:
            with self.subTest(tz=tz):
                local_zone = timezone(timedelta(hours=offset))
                before = datetime.now(local_zone).replace(microsecond=0, tzinfo=None)
                body = self.run_cgi(TZ=tz)[1]
                after = datetime.now(local_zone).replace(microsecond=0, tzinfo=None)
                self.assertRegex(body['system_date'], r'^\d{4}-\d{2}-\d{2}$')
                self.assertRegex(body['system_time'], r'^\d{2}:\d{2}:\d{2}$')
                actual = datetime.strptime(body['system_date'] + ' ' + body['system_time'], '%Y-%m-%d %H:%M:%S')
                self.assertLessEqual(before, actual)
                self.assertLessEqual(actual, after)

    def test_screensaver_type(self):
        self.assertEqual(self.run_cgi()[1]['screensaver_type'], 'clock')
        for value, expected in [('clock', 'clock'), ('gif', 'gif'),
                                ('', 'clock'), ('unknown', 'clock'),
                                ('GIF', 'clock'), ('gif"', 'clock')]:
            with self.subTest(value=value):
                body = self.run_cgi(TEST_screensaver_type=value,
                                    TEST_screensaver_timeout='0')[1]
                self.assertEqual(body['screensaver_type'], expected)
                self.assertEqual(body['screensaver_timeout'], 0)

    def test_screensaver_timeout(self):
        self.assertEqual(self.run_cgi()[1]['screensaver_timeout'], 60)
        for value, expected in [('0', 0), ('120', 120), ('86400', 86400),
                                ('00090', 90), ('-1', 60), ('86401', 60),
                                ('1.5', 60), ('bad', 60), ('', 60), ('1\n2', 60)]:
            with self.subTest(value=value):
                result = self.run_cgi(TEST_screensaver_timeout=value)[1]['screensaver_timeout']
                self.assertIsInstance(result, int)
                self.assertEqual(result, expected)

    def test_disabled(self):
        self.assertIn('503', self.run_cgi(TEST_enabled='0')[0])

    def test_authentication(self):
        self.assertIn('401', self.run_cgi(HTTP_X_API_TOKEN='bad')[0])
        self.assertIn('401', self.run_cgi(TEST_api_token='', HTTP_X_API_TOKEN='')[0])

    def test_method(self):
        self.assertIn('405', self.run_cgi(REQUEST_METHOD='POST')[0])

    def test_query_token(self):
        self.assertTrue(self.run_cgi(HTTP_X_API_TOKEN='', QUERY_STRING='other=1&token=test12345')[1]['ok'])
        self.assertIn('401', self.run_cgi(HTTP_X_API_TOKEN='', QUERY_STRING='token=test12345', TEST_allow_query_token='0')[0])
        self.assertIn('401', self.run_cgi(HTTP_X_API_TOKEN='bad', QUERY_STRING='token=test12345')[0])

    def test_interface_filter(self):
        body = self.run_cgi(TEST_interfaces='eth1', TEST_only_link_up='0')[1]
        self.assertEqual([i['name'] for i in body['interfaces']], ['eth1'])
        self.assertEqual(self.run_cgi(TEST_interfaces='missing')[1]['interfaces'], [])

    def test_all_links(self):
        self.assertEqual(self.run_cgi(TEST_only_link_up='0')[1]['interface_count'], 2)

    def test_temperature_selection(self):
        path = str(self.root / 'sys/class/hwmon/hwmon0/temp2_input')
        self.assertEqual(self.run_cgi(TEST_temperature_source=path)[1]['temperature_c'], -1.2)
        self.assertIsNone(self.run_cgi(TEST_temperature_source='/etc/passwd')[1]['temperature_c'])

    def test_missing_temperature(self):
        for path in (self.root / 'sys').rglob('*'):
            if path.is_file() and (path.name == 'temp' or path.name.endswith('_input')):
                path.unlink()
        self.assertIsNone(self.run_cgi()[1]['temperature_c'])

if __name__ == '__main__':
    unittest.main(verbosity=2)
