#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministische Tests fuer Protokoll, Pfadschutz und CLI von ncpin."""

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from unittest import mock


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NCPIN_PATH = os.path.join(REPO, "ncpin")


def load_ncpin():
    """Laedt das erweiterungslose CLI-Skript als testbares Python-Modul."""
    loader = importlib.machinery.SourceFileLoader("ncpin_test_module", NCPIN_PATH)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class FakeNextcloudSocket:
    """Kleiner Unix-Socket-Server mit Nextcloud-Handshake und Menueantwort."""

    def __init__(self, socket_path, roots, handshake_chunks=None):
        self.socket_path = socket_path
        self.roots = roots
        self.handshake_chunks = handshake_chunks
        self.commands = []
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._serve)
        self._thread.daemon = True

    def start(self):
        self._thread.start()
        if not self._ready.wait(2):
            raise RuntimeError("Fake-Socket wurde nicht bereit")
        return self

    def close(self):
        self._stop.set()
        wake = None
        try:
            wake = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            wake.connect(self.socket_path)
        except OSError:
            pass
        finally:
            if wake is not None:
                wake.close()
        self._thread.join(2)

    def _serve(self):
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            server.bind(self.socket_path)
            server.listen(8)
            server.settimeout(0.1)
            self._ready.set()
            while not self._stop.is_set():
                try:
                    conn, _ = server.accept()
                except socket.timeout:
                    continue
                with conn:
                    handshake = "".join("REGISTER_PATH:%s\n" % root for root in self.roots)
                    try:
                        chunks = self.handshake_chunks
                        if chunks is None:
                            chunks = [handshake.encode("utf-8")]
                        for chunk in chunks:
                            conn.sendall(chunk)
                            time.sleep(0.02)
                    except BrokenPipeError:
                        continue
                    conn.settimeout(0.3)
                    try:
                        data = conn.recv(65536)
                    except socket.timeout:
                        data = b""
                    if not data:
                        continue
                    command = data.decode("utf-8", "replace").strip()
                    self.commands.append(command)
                    if command.startswith("GET_MENU_ITEMS:"):
                        conn.sendall(
                            b"MENU_ITEM:MAKE_AVAILABLE_LOCALLY:d:Lokal\n"
                            b"MENU_ITEM:MAKE_ONLINE_ONLY::Online\n"
                            b"GET_MENU_ITEMS:END\n"
                        )
        finally:
            server.close()
            try:
                os.unlink(self.socket_path)
            except OSError:
                pass


class SocketDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.ncpin = load_ncpin()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "Nextcloud")
        os.mkdir(self.root)
        self.servers = []

    def tearDown(self):
        for server in reversed(self.servers):
            server.close()
        self.tmp.cleanup()

    def server(self, name, chunks=None):
        path = os.path.join(self.tmp.name, name)
        server = FakeNextcloudSocket(path, [self.root], chunks).start()
        self.servers.append(server)
        return server

    def discover_with(self, candidates, override=None, env_socket=""):
        with mock.patch.object(self.ncpin.glob, "glob",
                               return_value=candidates), \
                mock.patch.dict(os.environ,
                                {"NCPIN_SOCKET": env_socket}):
            return self.ncpin.discover_socket(override)

    def test_auto_discovery_skips_regular_stale_file_for_active_socket(self):
        stale = os.path.join(self.tmp.name, "a-stale")
        with open(stale, "w", encoding="utf-8") as handle:
            handle.write("kein Socket")
        active = self.server("b-active.sock")

        discovered = self.discover_with([stale, active.socket_path])

        self.assertEqual(discovered, active.socket_path)

    def test_auto_discovery_skips_dead_unix_socket_for_active_socket(self):
        stale = os.path.join(self.tmp.name, "a-dead.sock")
        dead = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        dead.bind(stale)
        dead.close()
        active = self.server("b-active.sock")

        discovered = self.discover_with([stale, active.socket_path])

        self.assertEqual(discovered, active.socket_path)

    def test_auto_discovery_skips_unresponsive_socket(self):
        silent = self.server("a-silent.sock", chunks=[])
        active = self.server("b-active.sock")

        discovered = self.discover_with(
            [silent.socket_path, active.socket_path])

        self.assertEqual(discovered, active.socket_path)

    def test_explicit_stale_socket_never_falls_back(self):
        stale = os.path.join(self.tmp.name, "stale")
        with open(stale, "w", encoding="utf-8") as handle:
            handle.write("kein Socket")
        active = self.server("active.sock")

        discovered = self.discover_with([active.socket_path], override=stale,
                                        env_socket=active.socket_path)

        self.assertIsNone(discovered)

    def test_doctor_reports_stale_explicit_socket_as_not_ok(self):
        stale = os.path.join(self.tmp.name, "stale")
        with open(stale, "w", encoding="utf-8") as handle:
            handle.write("kein Socket")
        stdout = io.StringIO()
        stderr = io.StringIO()

        with contextlib.redirect_stdout(stdout), \
                contextlib.redirect_stderr(stderr):
            rc = self.ncpin.main(
                ["doctor", "--json", "--socket", stale])

        self.assertEqual(rc, 1)
        info = json.loads(stdout.getvalue())
        self.assertIsNone(info["socket"])
        self.assertFalse(info["socket_ok"])
        self.assertFalse(info["client_responds"])

    def test_environment_socket_is_also_strict(self):
        stale = os.path.join(self.tmp.name, "stale")
        with open(stale, "w", encoding="utf-8") as handle:
            handle.write("kein Socket")
        active = self.server("active.sock")

        discovered = self.discover_with([active.socket_path], env_socket=stale)

        self.assertIsNone(discovered)

    def test_fragmented_register_handshake_is_accepted(self):
        chunks = [b"REGISTER_", ("PATH:%s\n" % self.root).encode("utf-8")]
        fragmented = self.server("fragmented.sock", chunks=chunks)

        discovered = self.discover_with([fragmented.socket_path])

        self.assertEqual(discovered, fragmented.socket_path)


class CliParserTests(unittest.TestCase):
    def setUp(self):
        self.ncpin = load_ncpin()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "Nextcloud")
        os.mkdir(self.root)
        self.first = os.path.join(self.root, "eins.txt")
        self.second = os.path.join(self.root, "zwei.txt")
        for path in (self.first, self.second):
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("test")

    def tearDown(self):
        self.tmp.cleanup()

    def assert_usage_error_before_socket(self, arguments):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(
                self.ncpin, "discover_socket_with_paths") as discovery, \
                contextlib.redirect_stdout(stdout), \
                contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as raised:
                self.ncpin.main(arguments)
        self.assertEqual(raised.exception.code, 2)
        discovery.assert_not_called()

    def test_invalid_arity_and_options_fail_before_socket_access(self):
        invalid = [
            ["doctor", self.first],
            ["doctor", "--wait"],
            ["doctor", "--timeout", "1"],
            ["list"],
            ["list", self.first, self.second],
            ["list", "--wait", self.root],
            ["status", "--wait", self.first],
            ["status", "--timeout", "1", self.first],
            ["local"],
        ]
        for arguments in invalid:
            with self.subTest(arguments=arguments):
                self.assert_usage_error_before_socket(arguments)

    def test_mutation_accepts_options_before_between_and_after_paths(self):
        stdout = io.StringIO()
        with mock.patch.object(
                self.ncpin, "discover_socket_with_paths",
                return_value=("fake.sock", [self.root])), \
                mock.patch.object(self.ncpin, "do_action") as action, \
                mock.patch.object(self.ncpin, "wait_for_state",
                                  return_value=True) as waiter, \
                contextlib.redirect_stdout(stdout):
            rc = self.ncpin.main([
                "--json", "local", self.first, "--wait", self.second,
                "--timeout", "1.5", "--socket=fake.sock",
            ])

        self.assertEqual(rc, 0)
        self.assertEqual(action.call_count, 2)
        self.assertEqual(waiter.call_count, 2)
        for call in waiter.call_args_list:
            self.assertEqual(call.args[3], 1.5)
        self.assertEqual(len(json.loads(stdout.getvalue())), 2)

    def test_double_dash_allows_path_beginning_with_dash(self):
        dashed = os.path.join(self.root, "-datei.txt")
        with open(dashed, "w", encoding="utf-8") as handle:
            handle.write("test")
        stdout = io.StringIO()
        previous_cwd = os.getcwd()
        try:
            os.chdir(self.root)
            with mock.patch.object(
                    self.ncpin, "discover_socket_with_paths",
                    return_value=("fake.sock", [self.root])), \
                    mock.patch.object(self.ncpin, "query_state",
                                      return_value="local"), \
                    contextlib.redirect_stdout(stdout):
                rc = self.ncpin.main(
                    ["status", "--json", "--", "-datei.txt"])
        finally:
            os.chdir(previous_cwd)

        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(stdout.getvalue())[0]["state"], "local")

    def test_doctor_quiet_suppresses_normal_output(self):
        stdout = io.StringIO()
        with mock.patch.object(
                self.ncpin, "discover_socket_with_paths",
                return_value=("fake.sock", [self.root])), \
                contextlib.redirect_stdout(stdout):
            rc = self.ncpin.main(["--quiet", "doctor"])

        self.assertEqual(rc, 0)
        self.assertEqual(stdout.getvalue(), "")


class PathBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.ncpin = load_ncpin()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "Nextcloud")
        self.outside = os.path.join(self.tmp.name, "outside")
        os.mkdir(self.root)
        os.mkdir(self.outside)
        self.socket_path = os.path.join(self.tmp.name, "nextcloud.sock")
        self.server = FakeNextcloudSocket(self.socket_path, [self.root]).start()

    def tearDown(self):
        self.server.close()
        self.tmp.cleanup()

    def run_cli(self, *args):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = self.ncpin.main(list(args) + ["--socket", self.socket_path])
        return rc, stdout.getvalue(), stderr.getvalue()

    def test_mutation_rejects_symlink_escape_without_make_command(self):
        outside_file = os.path.join(self.outside, "fremd.txt")
        with open(outside_file, "w", encoding="utf-8") as handle:
            handle.write("nicht anfassen")
        link = os.path.join(self.root, "link.txt")
        os.symlink(outside_file, link)

        rc, _out, _err = self.run_cli("online", link)

        self.assertEqual(rc, 3)
        self.assertFalse(any(cmd.startswith("MAKE_") for cmd in self.server.commands))

    def test_list_rejects_symlink_escape_without_leaking_entries(self):
        with open(os.path.join(self.outside, "privat.txt"), "w", encoding="utf-8") as handle:
            handle.write("privat")
        link = os.path.join(self.root, "fremder-ordner")
        os.symlink(self.outside, link)

        rc, out, _err = self.run_cli("list", link)

        self.assertEqual(rc, 3)
        self.assertNotIn("privat.txt", out)

    def test_inside_symlink_is_canonicalized_before_make_command(self):
        target = os.path.join(self.root, "echt.txt")
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("ok")
        link = os.path.join(self.root, "alias.txt")
        os.symlink(target, link)

        rc, _out, _err = self.run_cli("online", link)

        self.assertEqual(rc, 0)
        self.assertIn("MAKE_ONLINE_ONLY:" + os.path.realpath(target), self.server.commands)

    def test_status_rejects_symlink_escape_without_query(self):
        outside_file = os.path.join(self.outside, "status.txt")
        with open(outside_file, "w", encoding="utf-8") as handle:
            handle.write("fremd")
        link = os.path.join(self.root, "status-link.txt")
        os.symlink(outside_file, link)

        rc, _out, _err = self.run_cli("status", link)

        self.assertEqual(rc, 3)
        self.assertFalse(any(cmd.startswith("GET_MENU_ITEMS:") for cmd in self.server.commands))

    def test_dotdot_escape_is_rejected(self):
        outside_file = os.path.join(self.outside, "traversal.txt")
        with open(outside_file, "w", encoding="utf-8") as handle:
            handle.write("fremd")
        escaped = os.path.join(self.root, "..", "outside", "traversal.txt")

        rc, _out, _err = self.run_cli("online", escaped)

        self.assertEqual(rc, 3)
        self.assertFalse(any(cmd.startswith("MAKE_") for cmd in self.server.commands))

    def test_suffix_placeholder_is_canonicalized_and_queried(self):
        logical = os.path.join(self.root, "online.txt")
        placeholder = logical + self.ncpin.SUFFIX
        with open(placeholder, "w", encoding="utf-8") as handle:
            handle.write("x")

        rc, _out, _err = self.run_cli("status", logical)

        self.assertEqual(rc, 0)
        self.assertIn("GET_MENU_ITEMS:" + os.path.realpath(placeholder), self.server.commands)

    def test_second_registered_root_is_allowed(self):
        second_root = os.path.join(self.tmp.name, "Nextcloud Team")
        os.mkdir(second_root)
        self.server.roots.append(second_root)
        target = os.path.join(second_root, "team.txt")
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("ok")

        rc, _out, _err = self.run_cli("online", target)

        self.assertEqual(rc, 0)
        self.assertIn("MAKE_ONLINE_ONLY:" + os.path.realpath(target), self.server.commands)

    def test_low_level_operations_recheck_boundary(self):
        outside_file = os.path.join(self.outside, "direkt.txt")
        with open(outside_file, "w", encoding="utf-8") as handle:
            handle.write("fremd")
        with mock.patch.object(self.ncpin, "send_command") as sender:
            with self.assertRaises(self.ncpin.PathOutsideRoots):
                self.ncpin.query_state(self.socket_path, outside_file, [self.root])
            with self.assertRaises(self.ncpin.PathOutsideRoots):
                self.ncpin.do_action(self.socket_path, outside_file, "online", [self.root])
        sender.assert_not_called()


if __name__ == "__main__":
    unittest.main()
