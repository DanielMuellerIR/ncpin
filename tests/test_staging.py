#!/usr/bin/env python3
"""Stage-Ausgaben bleiben bei Pfadaustausch von Installationen getrennt."""

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO = Path(__file__).resolve().parent.parent


class StagingTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("stage_helper", REPO / "tools/atomic_replace.py")
        self.helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.helper)
        self.temp = tempfile.TemporaryDirectory(prefix="ncpin-stage-")
        self.root = Path(self.temp.name).resolve()
        self.stage = self.root / "stage"
        self.protected = self.root / "protected"
        self.installed = self.root / "installed"
        self.protected.mkdir()
        self.installed.mkdir()
        self.sources = []
        for name in ("Lokal halten.app", "Speicher freigeben.app"):
            source = self.root / "sources" / name
            source.mkdir(parents=True)
            (source / "data").write_text("new")
            target = self.installed / name
            target.mkdir()
            (target / "data").write_text("old")
            self.sources.append(str(source))

    def tearDown(self):
        self.temp.cleanup()

    def run_stage(self):
        self.helper.stage_artifacts(str(self.stage), str(self.protected),
                                    [str(path) for path in self.installed.iterdir()], self.sources)

    def assert_installation_unchanged(self):
        for name in ("Lokal halten.app", "Speicher freigeben.app"):
            self.assertEqual((self.installed / name / "data").read_text(), "old")

    def test_stage_is_idempotent_and_preserves_unrelated_files(self):
        self.stage.mkdir()
        (self.stage / "keep").write_text("unrelated")
        for _ in range(2):
            self.run_stage()
            self.assertEqual((self.stage / "keep").read_text(), "unrelated")
            for source in self.sources:
                self.assertEqual((self.stage / Path(source).name / "data").read_text(), "new")
            self.assertFalse(any(path.name.startswith(".ncpin-build-") for path in self.stage.iterdir()))
        self.assert_installation_unchanged()

    def test_failed_copy_cleans_partial_tree_and_retry_succeeds(self):
        def fail_copy(args, **kwargs):
            partial = Path(args[-1])
            partial.mkdir()
            (partial / "partial").write_text("incomplete")
            raise self.helper.subprocess.CalledProcessError(1, args, stderr="injected ditto failure")

        with mock.patch.object(self.helper.subprocess, "run", side_effect=fail_copy):
            with self.assertRaises(self.helper.subprocess.CalledProcessError):
                self.run_stage()
        self.assertEqual(list(self.stage.iterdir()), [])
        self.run_stage()
        self.assertFalse(list(self.stage.glob(".ncpin-build-*")))
        self.assert_installation_unchanged()

    def test_failure_after_extract_cleans_unpublished_artifact(self):
        with mock.patch.object(self.helper, "atomic_replace", side_effect=OSError("publish failed")):
            with self.assertRaisesRegex(OSError, "publish failed"):
                self.run_stage()
        self.assertEqual(list(self.stage.iterdir()), [])
        self.run_stage()
        self.assert_installation_unchanged()

    def test_failure_after_swap_preserves_and_reports_old_artifact(self):
        self.run_stage()
        for source in self.sources:
            (self.stage / Path(source).name / "data").write_text("previous")
        original = self.helper.atomic_replace

        def swap_then_fail(*args):
            original(*args)
            raise OSError("after swap")

        with mock.patch.object(self.helper, "atomic_replace", side_effect=swap_then_fail):
            with self.assertRaisesRegex(RuntimeError, "Altstand erhalten unter"):
                self.run_stage()
        preserved = list(self.stage.glob(".ncpin-build-*-artifact"))
        self.assertEqual(len(preserved), 1)
        self.assertEqual((preserved[0] / "data").read_text(), "previous")
        self.assert_installation_unchanged()

    def test_interrupted_copy_cleans_partial_tree(self):
        def interrupt(args, **kwargs):
            Path(args[-1]).mkdir()
            raise SystemExit(143)

        with mock.patch.object(self.helper.subprocess, "run", side_effect=interrupt):
            with self.assertRaises(SystemExit):
                self.run_stage()
        self.assertEqual(list(self.stage.iterdir()), [])

    def test_replaced_pending_artifact_is_preserved(self):
        def replace_pending(source, destination):
            held = Path(source)
            held.rename(self.root / "saved-new")
            held.mkdir()
            (held / "data").write_text("foreign")
            raise OSError("publish failed")

        with mock.patch.object(self.helper, "atomic_replace", side_effect=replace_pending):
            with self.assertRaisesRegex(RuntimeError, "erhalten unter"):
                self.run_stage()
        preserved = list(self.stage.glob(".ncpin-build-*-artifact"))
        self.assertEqual(len(preserved), 1)
        self.assertEqual((preserved[0] / "data").read_text(), "foreign")
        self.assert_installation_unchanged()

    def test_swap_after_identity_check_preserves_foreign_tree(self):
        self.run_stage()
        rename = self.helper._renamex
        replaced = False

        def replace_before_claim(source, destination, flags):
            nonlocal replaced
            if (not replaced and str(source).endswith("-artifact")
                    and Path(destination).parent.name.startswith(".ncpin-cleanup-")):
                replaced = True
                Path(source).rename(self.root / "saved-original")
                Path(source).mkdir()
                (Path(source) / "foreign").write_text("preserve")
            return rename(source, destination, flags)

        with mock.patch.object(self.helper, "_renamex", side_effect=replace_before_claim):
            with self.assertRaisesRegex(RuntimeError, "ersetzt; erhalten unter"):
                self.run_stage()
        self.assertTrue(replaced)
        foreign = list(self.stage.glob(".ncpin-cleanup-*/artifact/foreign"))
        self.assertEqual(len(foreign), 1)
        self.assertEqual(foreign[0].read_text(), "preserve")
        self.assertTrue((self.root / "saved-original" / "data").is_file())
        self.assert_installation_unchanged()

    def test_stage_swap_after_first_copy_cannot_overwrite_installation(self):
        original = self.helper.subprocess.run

        def copy_then_redirect(*args, **kwargs):
            result = original(*args, **kwargs)
            self.stage.rename(self.root / "moved-stage")
            self.stage.symlink_to(self.installed, target_is_directory=True)
            return result

        with mock.patch.object(self.helper.subprocess, "run", side_effect=copy_then_redirect):
            with self.assertRaisesRegex(RuntimeError, "verschoben"):
                self.run_stage()
        self.assert_installation_unchanged()

    def test_stage_swap_between_artifacts_cannot_overwrite_installation(self):
        original = self.helper.atomic_replace

        def publish_then_redirect(*args):
            original(*args)
            self.stage.rename(self.root / "moved-stage")
            self.stage.symlink_to(self.installed, target_is_directory=True)

        with mock.patch.object(self.helper, "atomic_replace", side_effect=publish_then_redirect):
            with self.assertRaisesRegex(RuntimeError, "verschoben"):
                self.run_stage()
        self.assert_installation_unchanged()

    def test_stage_replacement_at_same_path_is_rejected(self):
        original = self.helper.subprocess.run

        def copy_then_replace(*args, **kwargs):
            result = original(*args, **kwargs)
            self.stage.rename(self.protected / "moved-stage")
            self.stage.mkdir()
            (self.stage / "sentinel").write_text("replacement")
            return result

        with mock.patch.object(self.helper.subprocess, "run", side_effect=copy_then_replace):
            with self.assertRaisesRegex(RuntimeError, "verschoben"):
                self.run_stage()
        self.assertEqual((self.stage / "sentinel").read_text(), "replacement")
        self.assert_installation_unchanged()

    def test_intermediate_symlink_inserted_before_open_is_rejected(self):
        self.stage = self.root / "parent" / "stage"
        parent = self.stage.parent
        parent.mkdir()
        original = self.helper._open_directory

        def redirect_before_open(path, create=False, **kwargs):
            if create:
                parent.rename(self.root / "moved-parent")
                parent.symlink_to(self.installed, target_is_directory=True)
            return original(path, create=create, **kwargs)

        with mock.patch.object(self.helper, "_open_directory", side_effect=redirect_before_open):
            with self.assertRaises(OSError):
                self.run_stage()
        self.assertFalse((self.installed / "stage").exists())
        self.assert_installation_unchanged()


if __name__ == "__main__":
    unittest.main()
