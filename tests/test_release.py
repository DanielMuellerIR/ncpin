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
    def run_finalization(self, root):
        source = (REPO / "release.sh").read_text()
        finalization = source[source.index("# Erst jetzt den kanonischen Namen vergeben:"):]
        script = ('set -euo pipefail\nPARTIAL_DMG="ncpin-unfertig.dmg"\nDMG="ncpin.dmg"\n'
                  + finalization)
        return subprocess.run(["/bin/zsh", "-c", script], cwd=root,
                              capture_output=True, text=True, timeout=10)

    def test_directory_collision_preserves_verified_image_and_reports_failure(self):
        with tempfile.TemporaryDirectory(prefix="ncpin-release-") as temp:
            root = Path(temp)
            image = root / "ncpin-unfertig.dmg"
            image.write_bytes(b"verified-image")
            destination = root / "ncpin.dmg"
            destination.mkdir()
            (destination / "sentinel").write_text("keep")
            result = self.run_finalization(root)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("RELEASE OK", result.stdout)
            self.assertEqual(image.read_bytes(), b"verified-image")
            self.assertEqual((destination / "sentinel").read_text(), "keep")
            self.assertEqual(list(destination.iterdir()), [destination / "sentinel"])

    def test_finalization_replaces_previous_image_at_exact_path(self):
        with tempfile.TemporaryDirectory(prefix="ncpin-release-") as temp:
            root = Path(temp)
            (root / "ncpin-unfertig.dmg").write_bytes(b"verified-image")
            destination = root / "ncpin.dmg"
            destination.write_bytes(b"old-image")
            result = self.run_finalization(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("RELEASE OK", result.stdout)
            self.assertEqual(destination.read_bytes(), b"verified-image")
            self.assertFalse((root / "ncpin-unfertig.dmg").exists())

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
