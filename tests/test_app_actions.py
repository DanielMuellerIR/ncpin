#!/usr/bin/env python3
"""Der kompilierte App-Handler übergibt Aufträge ohne Download-Wartephase."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent


class AppActionTests(unittest.TestCase):
    def test_compiled_handlers_submit_without_wait_and_preserve_paths(self):
        template = (REPO / 'apps/ncpin-app.applescript.tmpl').read_text()
        with tempfile.TemporaryDirectory(prefix='ncpin-app-action-') as tmp:
            root = Path(tmp)
            recorded = root / 'arguments.json'
            cli = root / "CLI O'Brien.py"
            cli.write_text(
                'import json, sys\nfrom pathlib import Path\n'
                'Path(%r).write_text(json.dumps(sys.argv[1:]))\n' % str(recorded))
            paths = [str(root / "Datei O'Brien.txt"), str(root / 'Zweite Datei.txt')]
            for action in ('local', 'online'):
                with self.subTest(action=action):
                    source = template.replace('@@ACTION@@', action).replace('@@LABEL@@', action)
                    # Nur die Bundle-Auflösung wird ersetzt; der produktive
                    # Auftrags-Handler samt Shell-Quoting läuft unverändert.
                    start = source.index('on ncpinPath()')
                    end = source.index('end ncpinPath', start) + len('end ncpinPath')
                    source = (source[:start] + 'on ncpinPath()\nreturn ' +
                              json.dumps(str(cli)) + '\nend ncpinPath' + source[end:])
                    script = root / 'action.applescript'
                    script.write_text(source)
                    bundle = root / (action + '.app')
                    compiled = subprocess.run(
                        ['/usr/bin/osacompile', '-o', str(bundle), str(script)],
                        capture_output=True, text=True, timeout=15)
                    self.assertEqual(compiled.returncode, 0, compiled.stderr)
                    driver = root / 'driver.applescript'
                    driver.write_text(
                        'on run argv\nset appScript to load script POSIX file (item 1 of argv)\n'
                        'tell appScript to runNcpin({item 2 of argv, item 3 of argv})\nend run\n')
                    result = subprocess.run(
                        ['/usr/bin/osascript', str(driver),
                         str(bundle / 'Contents/Resources/Scripts/main.scpt')] + paths,
                        capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(json.loads(recorded.read_text()), [action] + paths)
                    recorded.unlink()


if __name__ == '__main__':
    unittest.main()
