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
            write(f'sys/class/net/{dev}/ifindex', '2\n')
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
        def rewrite(source):
            source = source.replace('/lib/functions.sh', str(self.root / 'functions.sh'))
            for prefix in ['/proc/', '/sys/', '/tmp/esp32monitor']:
                source = source.replace(prefix, str(self.root) + prefix)
            source = source.replace(str(self.root) + '/proc/self/fd/', '/proc/self/fd/')
            source = source.replace('/etc/esp32monitor/', str(self.root / 'gif') + '/')
            source = source.replace('/usr/share/esp32monitor/rates.awk', str(PACKAGE / 'root/usr/share/esp32monitor/rates.awk'))
            return source
        self.script = self.root / 'cgi'
        self.script.write_text(rewrite((PACKAGE / 'root/www/cgi-bin/esp32-status').read_text()))
        self.library = self.root / 'collector.sh'
        self.library.write_text(rewrite((PACKAGE / 'root/usr/share/esp32monitor/collector.sh').read_text()))
        self.cache = self.root / 'tmp/esp32monitor'
        self.cache.mkdir(parents=True)
        write('bin/jsonfilter', '#!/usr/bin/python3\nimport sys,json\na=sys.argv\nf=open(a[a.index("-i")+1]) if "-i" in a else sys.stdin\nv=json.load(f)[a[a.index("-e")+1][2:]]\nprint(json.dumps(v))\n')
        (self.root / 'bin/awk').symlink_to('/usr/bin/busybox')
        (self.root / 'bin/jsonfilter').chmod(0o755)

    def collect(self, env, middle=None):
        middle = middle or str(self.root / 'bin/sleep')
        code = f"""
. '{self.library}'
CACHE_DIR='{self.cache}'
SNAPSHOT_1="$CACHE_DIR/previous"
SNAPSHOT_2="$CACHE_DIR/current"
BOOT_ID=test-boot
COLLECTOR_ID=test-collector
: > "$SNAPSHOT_1"
sample_once || exit 1
cp "$CACHE_DIR/snapshot.json" "$CACHE_DIR/first.json"
{middle}
sample_once || exit 1
"""
        subprocess.run(['busybox', 'ash', '-c', code], env=env, check=True, capture_output=True)

    def run_cgi(self, **changes):
        env = dict(os.environ, PATH=str(self.root / 'bin') + ':' + os.environ['PATH'],
                   TEST_enabled='1', TEST_api_token='test12345', HTTP_X_API_TOKEN='test12345',
                   TEST_wan_device='eth0', QUERY_STRING='', REQUEST_METHOD='GET')
        env.update(changes)
        (self.root / 'tmp').mkdir(exist_ok=True)
        self.collect(dict(env, TEST_enabled='1'))
        result = subprocess.run(['busybox', 'ash', str(self.script)], env=env, capture_output=True, text=True, check=True)
        header, body = result.stdout.split('\n\n', 1)
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
                body = self.run_cgi(TEST_s3_gif_enabled='1', TEST_screensaver_type=value,
                                    TEST_screensaver_timeout='0')[1]
                self.assertEqual(body['screensaver_type'], expected)
                self.assertEqual(body['screensaver_timeout'], 0)

    def test_gif_metadata(self):
        import hashlib
        directory = self.root / 'gif'
        directory.mkdir()
        data = b'GIF89a' + bytes(20)
        (directory / 'screensaver.gif').write_bytes(data)
        body = self.run_cgi(TEST_s3_gif_enabled='1', TEST_screensaver_type='gif')[1]
        self.assertEqual(body['gif_url'], '/cgi-bin/esp32-gif')
        self.assertEqual(body['gif_size'], len(data))
        self.assertEqual(body['gif_version'], hashlib.sha256(data).hexdigest())
        self.assertIsNone(self.run_cgi()[1]['gif_url'])
        for configured_type in ('clock', 'invalid'):
            clock = self.run_cgi(TEST_s3_gif_enabled='1', TEST_screensaver_type=configured_type)[1]
            self.assertEqual(clock['screensaver_type'], 'clock')
            self.assertIsNone(clock['gif_url'])
            self.assertIsNone(clock['gif_version'])
            self.assertEqual(clock['gif_size'], 0)
        (directory / 'screensaver.gif').unlink()
        self.assertIsNone(self.run_cgi(TEST_s3_gif_enabled='1', TEST_screensaver_type='gif')[1]['gif_url'])

    def test_s3_gif_support_gate(self):
        self.assertEqual(self.run_cgi(TEST_screensaver_type='gif')[1]['screensaver_type'], 'clock')
        for enabled, expected in [('0', 'clock'), ('1', 'gif'), ('', 'clock'), ('invalid', 'clock')]:
            with self.subTest(enabled=enabled):
                body = self.run_cgi(TEST_s3_gif_enabled=enabled, TEST_screensaver_type='gif')[1]
                self.assertEqual(body['screensaver_type'], expected)

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

    def test_sampling_lifecycle(self):
        env = dict(os.environ, PATH=str(self.root / 'bin') + ':' + os.environ['PATH'], TEST_enabled='1', TEST_api_token='test12345', TEST_wan_device='eth0')
        root = self.root
        dev = root / 'sys/class/net/eth0'
        # Above 2^53: preserve a tiny delta and the raw cumulative integer.
        (dev / 'statistics/rx_bytes').write_text('18446744073709550000\n')
        middle = f"""
printf '125.45 0\\n' > '{root}/proc/uptime'
printf '18446744073709550006\\n' > '{dev}/statistics/rx_bytes'
"""
        self.collect(env, middle)
        first = json.loads((self.cache / 'first.json').read_text())
        second = json.loads((self.cache / 'snapshot.json').read_text())
        self.assertIsNone(first['interfaces'][0]['download_bps'])
        self.assertIsNone(first['cpu_percent'])
        self.assertIsNone(first['interval_ms'])
        self.assertEqual(second['sample_seq'], 2)
        self.assertEqual(second['interval_ms'], 2000)
        self.assertEqual(second['interfaces'][0]['download_bps'], 3)
        self.assertEqual(second['interfaces'][0]['upload_bps'], 0)
        self.assertEqual(second['interfaces'][0]['download_bytes'], 18446744073709550006)

    def test_reset_recreation_and_read_failure(self):
        env = dict(os.environ, PATH=str(self.root / 'bin') + ':' + os.environ['PATH'], TEST_enabled='1', TEST_wan_device='eth0')
        dev = self.root / 'sys/class/net/eth0'
        cases = [
            ("printf '1\\n' > '{dev}/statistics/rx_bytes'", 1),
            ("printf '3\\n' > '{dev}/ifindex'", 5000000000),
            ("rm '{dev}/statistics/rx_bytes'", None),
        ]
        for action, expected in cases:
            with self.subTest(action=action):
                (dev / 'ifindex').write_text('2\n')
                (dev / 'statistics/rx_bytes').write_text('5000000000\n')
                (self.root / 'proc/uptime').write_text('123.45 0\n')
                self.collect(env, "printf '124.45 0\\n' > '%s/proc/uptime'\n" % self.root + action.format(dev=dev))
                row = json.loads((self.cache / 'snapshot.json').read_text())['interfaces'][0]
                self.assertIsNone(row['download_bps'])
                self.assertEqual(row['download_bytes'], expected)

    def test_cache_freshness_and_parallel_readers(self):
        from concurrent.futures import ThreadPoolExecutor
        env = dict(os.environ, PATH=str(self.root / 'bin') + ':' + os.environ['PATH'],
                   TEST_enabled='1', TEST_api_token='test12345', HTTP_X_API_TOKEN='test12345')
        self.collect(env)
        def request():
            result = subprocess.run(['busybox', 'ash', str(self.script)], env=env,
                                    capture_output=True, text=True, check=True)
            header, body = result.stdout.split('\n\n', 1)
            return header, json.loads(body)
        with ThreadPoolExecutor(max_workers=3) as pool:
            responses = list(pool.map(lambda _: request()[1], range(3)))
        self.assertEqual([r['sample_seq'] for r in responses], [2, 2, 2])
        self.assertTrue(all(not r['stale'] for r in responses))
        (self.root / 'proc/uptime').write_text('128.45 0\n')
        stale = request()[1]
        self.assertTrue(stale['stale'])
        self.assertEqual(stale['age_ms'], 4000)
        (self.cache / 'snapshot.json').unlink()
        self.assertIn('503', request()[0])

    def test_collector_restart_and_atomic_publication(self):
        import signal
        import time
        from concurrent.futures import ThreadPoolExecutor
        (self.root / 'proc/sys/kernel/random').mkdir(parents=True)
        (self.root / 'proc/sys/kernel/random/boot_id').write_text('boot-one\n')
        uuid = self.root / 'proc/sys/kernel/random/uuid'
        uuid.write_text('collector-one\n')
        source = (PACKAGE / 'root/usr/libexec/esp32monitor-collector').read_text()
        source = source.replace('/tmp/esp32monitor', str(self.cache))
        source = source.replace('/usr/share/esp32monitor/collector.sh', str(self.library))
        source = source.replace('/proc/sys/', str(self.root) + '/proc/sys/')
        source = source.replace('sleep "$DELAY"', '/bin/sleep 0.05')
        executable = self.root / 'daemon'
        executable.write_text(source)
        env = dict(os.environ, PATH=str(self.root / 'bin') + ':' + os.environ['PATH'],
                   TEST_enabled='1', TEST_wan_device='eth0')
        def start():
            proc = subprocess.Popen(['busybox', 'ash', str(executable)], env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                    start_new_session=True)
            self.addCleanup(clean, proc)
            return proc
        def clean(proc):
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
                proc.wait(timeout=5)
            proc.stderr.close()
        def wait_for(identity):
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if (self.cache / 'snapshot.json').exists():
                    data = json.loads((self.cache / 'snapshot.json').read_text())
                    if data['collector_id'] == identity:
                        return data
                time.sleep(0.01)
            self.fail('collector did not publish')
        proc = start()
        self.assertEqual(wait_for('collector-one')['sample_seq'], 1)
        def read_repeatedly():
            for _ in range(40):
                data = json.loads((self.cache / 'snapshot.json').read_text())
                self.assertEqual(data['interface_count'], len(data['interfaces']))
                time.sleep(0.005)
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(lambda _: read_repeatedly(), range(3)))
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=5)
        uuid.write_text('collector-two\n')
        start()
        restarted = wait_for('collector-two')
        self.assertEqual(restarted['boot_id'], 'boot-one')
        self.assertEqual(restarted['sample_seq'], 1)
        self.assertIsNone(restarted['interfaces'][0]['download_bps'])

if __name__ == '__main__':
    unittest.main(verbosity=2)
