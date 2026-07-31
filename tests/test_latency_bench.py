#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit-Tests fuer das nicht mutierende Latenzgate und den sicheren Roundtrip."""

import importlib.machinery
import importlib.util
import json
import math
import os
import tempfile
import unittest
from unittest import mock


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BENCH_PATH = os.path.join(REPO, "tests", "latency_bench.py")


def load_bench():
    loader = importlib.machinery.SourceFileLoader("latency_bench_test_module", BENCH_PATH)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def status_result(state, rc=0):
    payload = [{"path": "/fixture", "op": "status", "state": state}]
    return 0.01, rc, json.dumps(payload), ""


class ParserValueTests(unittest.TestCase):
    def setUp(self):
        self.bench = load_bench()

    def test_positive_integer_rejects_zero_negative_and_fraction(self):
        for value in ("0", "-1", "1.5"):
            with self.assertRaises(Exception):
                self.bench.positive_int(value)
        self.assertEqual(self.bench.positive_int("3"), 3)

    def test_positive_finite_rejects_zero_negative_nan_and_infinity(self):
        for value in ("0", "-0.1", "nan", "inf", "-inf"):
            with self.assertRaises(Exception):
                self.bench.positive_finite(value)
        self.assertTrue(math.isfinite(self.bench.positive_finite("0.25")))

    def test_invalid_cli_values_and_roundtrip_without_sample_are_usage_errors(self):
        for argv in (["--runs", "0"], ["--threshold", "nan"],
                     ["--timeout", "-1"], ["--roundtrip"]):
            with self.subTest(argv=argv), self.assertRaises(SystemExit) as caught:
                self.bench.main(argv)
            self.assertEqual(caught.exception.code, 2)


class MeasurementTests(unittest.TestCase):
    def setUp(self):
        self.bench = load_bench()

    def test_measure_preserves_every_run_result(self):
        results = [
            (0.2, 1, "", "kaputt"),
            (0.1, 0, "lokal /fixture", ""),
        ]
        with mock.patch.object(self.bench, "run_ncpin", side_effect=results):
            measured = self.bench.measure(["runner"], ["status", "/fixture"], 2)

        self.assertEqual([sample["rc"] for sample in measured["samples"]], [1, 0])
        self.assertFalse(self.bench.validate_measurement("status-file", measured))

    def test_status_folder_and_list_validate_every_sample(self):
        status = {
            "samples": [
                {"rc": 0, "stdout": "lokal /folder", "stderr": ""},
                {"rc": 1, "stdout": "lokal /folder", "stderr": "Fehler"},
            ]
        }
        listing = {
            "samples": [
                {"rc": 0, "stdout": "Datei", "stderr": ""},
                {"rc": 0, "stdout": "", "stderr": ""},
            ]
        }
        self.assertFalse(self.bench.validate_measurement("status-folder", status))
        self.assertFalse(self.bench.validate_measurement("list-folder", listing))

    def test_mixed_folder_unknown_is_valid_but_unknown_file_is_not(self):
        folder = {
            "samples": [
                {"rc": 0, "stdout": "unbekannt /folder", "stderr": ""},
            ]
        }
        file_status = {"samples": [dict(folder["samples"][0])]}

        self.assertTrue(
            self.bench.validate_measurement("status-folder", folder))
        self.assertFalse(
            self.bench.validate_measurement("status-file", file_status))


class SampleValidationTests(unittest.TestCase):
    def setUp(self):
        self.bench = load_bench()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "Nextcloud")
        self.outside = os.path.join(self.tmp.name, "outside")
        os.mkdir(self.root)
        os.mkdir(self.outside)

    def tearDown(self):
        self.tmp.cleanup()

    def test_explicit_sample_must_be_regular_file_inside_root(self):
        directory = os.path.join(self.root, "ordner")
        os.mkdir(directory)
        outside_file = os.path.join(self.outside, "fremd.txt")
        with open(outside_file, "w", encoding="utf-8") as handle:
            handle.write("fremd")
        link = os.path.join(self.root, "link.txt")
        os.symlink(outside_file, link)

        self.assertIsNone(self.bench.discover_sample([self.root], directory))
        self.assertIsNone(self.bench.discover_sample([self.root], link))

    def test_suffix_placeholder_is_validated_and_returned_logically(self):
        logical = os.path.join(self.root, "fixture.txt")
        with open(logical + self.bench.SUFFIX, "w", encoding="utf-8") as handle:
            handle.write("x")

        self.assertEqual(self.bench.discover_sample([self.root], logical), logical)


class RoundtripTests(unittest.TestCase):
    def setUp(self):
        self.bench = load_bench()
        self.runner = ["runner"]
        self.sample = "/fixture"

    def test_roundtrip_reads_initial_state_and_restores_it_in_finally(self):
        calls = [
            status_result("local"),
            (0.2, 0, "online ✓", ""),
            status_result("online"),
            status_result("online"),
            (0.2, 0, "local ✓", ""),
            status_result("local"),
        ]
        with mock.patch.object(self.bench, "run_ncpin", side_effect=calls) as run:
            result = self.bench.roundtrip(self.runner, self.sample, 5.0)

        self.assertTrue(result["ok"])
        self.assertEqual(result["initial_state"], "local")
        self.assertTrue(result["restore_verified"])
        commands = [call.args[1][0] for call in run.call_args_list]
        self.assertEqual(commands, ["status", "online", "status", "status", "local", "status"])

    def test_failed_transition_does_not_blindly_run_counteroperation(self):
        calls = [
            status_result("local"),
            (0.2, 1, "", "Fehler"),
            status_result("local"),
            status_result("local"),
        ]
        with mock.patch.object(self.bench, "run_ncpin", side_effect=calls) as run:
            result = self.bench.roundtrip(self.runner, self.sample, 5.0)

        self.assertFalse(result["ok"])
        self.assertTrue(result["restore_verified"])
        commands = [call.args[1][0] for call in run.call_args_list]
        self.assertEqual(commands, ["status", "online", "status", "status"])

    def test_failed_transition_is_restored_when_state_did_change(self):
        calls = [
            status_result("local"),
            (0.2, 1, "", "Timeout"),
            status_result("online"),
            status_result("online"),
            (0.2, 0, "local ✓", ""),
            status_result("local"),
        ]
        with mock.patch.object(self.bench, "run_ncpin", side_effect=calls) as run:
            result = self.bench.roundtrip(self.runner, self.sample, 5.0)

        self.assertFalse(result["ok"])
        self.assertTrue(result["restore_verified"])
        commands = [call.args[1][0] for call in run.call_args_list]
        self.assertEqual(commands, ["status", "online", "status", "status", "local", "status"])


if __name__ == "__main__":
    unittest.main()
