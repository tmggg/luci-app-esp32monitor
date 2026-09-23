#!/usr/bin/env python3
"""Check initialization with a minimal PATH that deliberately has no od."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

PACKAGE = Path(__file__).resolve().parents[1]

class Initialization(unittest.TestCase):
    def test_without_od_and_preserve_existing_token(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / 'token'
            config = root / 'config'
            config.touch()
            for command in ('tr', 'chmod'):
                (root / command).symlink_to(shutil.which(command))
            uci = root / 'uci'
            uci.write_text('''#!/bin/sh
case "$*" in
 '-q get esp32monitor.main.api_token')
    [ -f "$TEST_STATE" ] || exit 1
    IFS= read -r value < "$TEST_STATE"
    printf '%s' "$value" ;;
 set*) printf '%s\\n' "${2#*=}" > "$TEST_STATE" ;;
 commit*) exit 0 ;;
esac
''')
            uci.chmod(0o755)
            script = root / 'init'
            script.write_text((PACKAGE / 'root/etc/uci-defaults/90-esp32monitor').read_text().replace('/etc/config/esp32monitor', str(config)))
            env = dict(os.environ, PATH=str(root), TEST_STATE=str(state))
            subprocess.run(['/bin/sh', str(script)], env=env, check=True)
            token = state.read_text().strip()
            self.assertRegex(token, r'^[0-9a-fA-F]{32}$')
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)
            # Upgrades must retain even older, differently sized tokens.
            state.write_text('existing-token-12345\n')
            subprocess.run(['/bin/sh', str(script)], env=env, check=True)
            self.assertEqual(state.read_text().strip(), 'existing-token-12345')

if __name__ == '__main__':
    unittest.main(verbosity=2)
