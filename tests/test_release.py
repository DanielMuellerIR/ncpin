#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prüft einen frühen Release-Abbruch ohne Schlüsselbund, Mount oder GUI."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent


class ReleaseTests(unittest.TestCase):
    def test_failed_build_preserves_the_previous_release_image(self):
        with tempfile.TemporaryDirectory(prefix="ncpin-release-") as temp:
            root = Path(temp)
            shutil.copy2(REPO / "release.sh", root / "release.sh")
            installer = root / "install.sh"
            installer.write_text("#!/bin/zsh\nexit 1\n")
            installer.chmod(0o755)
            fakebin = root / "bin"
            fakebin.mkdir()
            xcrun = fakebin / "xcrun"
            xcrun.write_text("#!/bin/zsh\nexit 0\n")
            xcrun.chmod(0o755)
            image = root / "build" / "dmg" / "ncpin.dmg"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"previous-verified-image")
            env = dict(os.environ, PATH=str(fakebin) + ":/usr/bin:/bin",
                       NOTARY_PROFILE="fixture", NCPIN_SIGN_ID="fixture")
            result = subprocess.run(["/bin/zsh", str(root / "release.sh"), "--no-finder-layout"],
                                    cwd=root, env=env, capture_output=True, text=True, timeout=15)
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(image.exists(), result.stdout + result.stderr)
            self.assertEqual(image.read_bytes(), b"previous-verified-image")


if __name__ == "__main__":
    unittest.main()
