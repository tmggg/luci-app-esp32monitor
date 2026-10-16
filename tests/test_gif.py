import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from test_cgi import PACKAGE

class GIF(unittest.TestCase):
    def test_upload_and_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            upload = root / 'upload.gif'
            store = root / 'store'
            helper = root / 'helper'
            helper.write_text((PACKAGE / 'root/usr/libexec/esp32monitor-gif').read_text().replace('/etc/esp32monitor', str(store)).replace('/tmp/esp32monitor-upload.gif', str(upload)))
            data = b'GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00\x3b'
            upload.write_bytes(data)
            subprocess.run(['busybox', 'ash', str(helper)], check=True)
            saved = store / 'screensaver.gif'
            self.assertEqual(saved.read_bytes(), data)
            self.assertEqual(saved.stat().st_mode & 0o777, 0o600)
            for bad in (b'not a GIF file!', b'GIF89a' + bytes(2097152)):
                upload.write_bytes(bad)
                result = subprocess.run(['busybox', 'ash', str(helper)], capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(saved.read_bytes(), data)
            self.assertFalse(list(store.glob('.gif.*')))
            functions = root / 'functions'
            functions.write_text('''config_load() { :; }
config_get() {
 local value
 eval 'value=${TEST_'"$3"'-'"$4"'}'
 export "$1=$value"
}
''')
            cgi = root / 'cgi'
            cgi.write_text((PACKAGE / 'root/www/cgi-bin/esp32-gif').read_text().replace('/lib/functions.sh', str(functions)).replace('/etc/esp32monitor', str(store)))
            def request(**options):
                env = dict(os.environ, TEST_enabled='1', TEST_api_token='test12345', TEST_s3_gif_enabled='1', HTTP_X_API_TOKEN='test12345', REQUEST_METHOD='GET', QUERY_STRING='')
                env.update(options)
                return subprocess.run(['busybox', 'ash', str(cgi)], env=env, capture_output=True, check=True).stdout
            response = request()
            head, body = response.split(b'\r\n\r\n', 1)
            self.assertIn(b'Content-Type: image/gif', head)
            self.assertEqual(body, data)
            for options, code in [({'HTTP_X_API_TOKEN': 'wrong'}, b'401'), ({'TEST_enabled':'0'}, b'503'), ({'TEST_s3_gif_enabled':'0'}, b'404'), ({'REQUEST_METHOD':'POST'}, b'405'), ({'HTTP_X_API_TOKEN':'', 'QUERY_STRING':'token=test12345', 'TEST_allow_query_token':'0'}, b'401')]:
                self.assertIn(code, request(**options).split(b'\r\n', 1)[0])
            self.assertEqual(request(HTTP_X_API_TOKEN='', QUERY_STRING='token=test12345').split(b'\r\n\r\n', 1)[1], data)
            saved.unlink()
            self.assertIn(b'404', request().split(b'\r\n', 1)[0])
