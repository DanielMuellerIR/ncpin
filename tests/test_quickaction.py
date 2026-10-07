#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Führt erzeugte Quick Actions mit isolierter CLI und Dialogattrappe aus."""

import importlib.util
import json
import os
import plistlib
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent


class QuickActionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ncpin-quickaction-")
        self.root = Path(self.temp.name)
        spec = importlib.util.spec_from_file_location(
            "quickaction_test", REPO / "quickactions" / "make_quickaction.py")
        self.generator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.generator)
        self.calls = self.root / "calls.json"
        self.cli = self.root / "CLI with O'Brien.py"
        self.cli.write_text(
            "#!/usr/bin/python3\n"
            "import json, os, sys\n"
            "with open(os.environ['FIXTURE_CALLS'], 'w') as handle:\n"
            "    json.dump(sys.argv[1:], handle)\n"
            "sys.exit(int(os.environ['FIXTURE_RC']))\n", encoding="utf-8")
        self.cli.chmod(0o755)
        self.dialog = self.root / "dialog.sh"
        self.dialog.write_text(
            '#!/bin/zsh\n'
            'if [[ "$2" == *"display dialog"* ]]; then\n'
            '  print -r -- "$FIXTURE_CONFIRM"\n'
            'else\n'
            '  exit "$FIXTURE_NOTIFY_RC"\n'
            'fi\n', encoding="utf-8")
        self.dialog.chmod(0o755)

    def tearDown(self):
        self.temp.cleanup()

    def run_workflow(self, action, selected, rc=0, confirm="confirm", notify_rc=0):
        workflow = self.root / "fixture.workflow"
        self.generator.build(action, "Fixture", str(self.cli), str(workflow))
        with (workflow / "Contents" / "document.wflow").open("rb") as handle:
            document = plistlib.load(handle)
        command = document["actions"][0]["action"]["ActionParameters"]["COMMAND_STRING"]
        command = command.replace("/usr/bin/osascript", self.generator._q(str(self.dialog)))
        env = dict(os.environ, FIXTURE_CALLS=str(self.calls), FIXTURE_RC=str(rc),
                   FIXTURE_CONFIRM=confirm, FIXTURE_NOTIFY_RC=str(notify_rc))
        return subprocess.run(["/bin/zsh", "-c", command, "workflow", str(selected)],
                              env=env, capture_output=True, text=True, timeout=10)

    def test_cli_failure_survives_successful_notification(self):
        selected = self.root / "file.txt"
        selected.write_text("fixture")
        result = self.run_workflow("local", selected, rc=3)
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(json.loads(self.calls.read_text()), ["local", str(selected)])

    def test_notification_failure_does_not_replace_successful_cli_status(self):
        selected = self.root / "file.txt"
        selected.write_text("fixture")
        result = self.run_workflow("local", selected, notify_rc=1)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.calls.exists())

    def test_cancelled_folder_release_never_calls_cli(self):
        selected = self.root / "Folder with O'Brien"
        selected.mkdir()
        result = self.run_workflow("online", selected, confirm="cancel")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.calls.exists())

    def test_confirmed_folder_release_calls_cli(self):
        selected = self.root / "folder"
        selected.mkdir()
        result = self.run_workflow("online", selected)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.calls.read_text()), ["online", str(selected)])

    def test_single_file_does_not_require_folder_confirmation(self):
        selected = self.root / "file.txt"
        selected.write_text("fixture")
        result = self.run_workflow("online", selected, confirm="cancel")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.calls.exists())


if __name__ == "__main__":
    unittest.main()
