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
import sqlite3
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

    def test_handshake_reset_falls_back_to_next_candidate(self):
        # Ein Reset NACH erfolgreichem Connect (ConnectionResetError im ersten
        # recv) darf die Discovery nicht crashen, sondern muss zum naechsten
        # Kandidaten weitergehen.
        first = self.server("a-reset.sock")
        active = self.server("b-active.sock")
        real_recv = self.ncpin._recv_burst
        calls = {"count": 0}

        def flaky_recv(sock, first_wait, sentinel=None):
            calls["count"] += 1
            if calls["count"] == 1:
                raise ConnectionResetError(54, "Connection reset by peer")
            return real_recv(sock, first_wait, sentinel)

        with mock.patch.object(self.ncpin, "_recv_burst",
                               side_effect=flaky_recv):
            discovered = self.discover_with(
                [first.socket_path, active.socket_path])

        self.assertEqual(discovered, active.socket_path)

    def test_explicit_socket_handshake_reset_reports_no_socket(self):
        # Beim explizit gesetzten Socket gibt es keinen Fallback: Der Reset
        # endet als "kein Socket" (und damit spaeter als sauberer Exit 1).
        active = self.server("active.sock")

        with mock.patch.object(
                self.ncpin, "_recv_burst",
                side_effect=ConnectionResetError(54, "reset")):
            discovered = self.discover_with(
                [active.socket_path], override=active.socket_path)

        self.assertIsNone(discovered)


class ParseStateTests(unittest.TestCase):
    """Zustandsparser: unvollstaendige Bursts duerfen keinen Zustand liefern."""

    def setUp(self):
        self.ncpin = load_ncpin()

    def test_missing_command_line_yields_unknown(self):
        # Ein abgebrochener Burst mit nur EINER der beiden Command-IDs ist
        # keine Zustandsaussage — sonst koennte toggle aufgrund einer halben
        # Antwort eine zustandsaendernde Gegenaktion senden.
        only_online = "MENU_ITEM:MAKE_ONLINE_ONLY::Online\n"
        only_avail = "MENU_ITEM:MAKE_AVAILABLE_LOCALLY:d:Lokal\n"
        self.assertEqual(self.ncpin.parse_state(only_online), "unknown")
        self.assertEqual(self.ncpin.parse_state(only_avail), "unknown")
        self.assertEqual(self.ncpin.parse_state(""), "unknown")

    def test_complete_bursts_still_resolve_both_states(self):
        local_burst = ("MENU_ITEM:MAKE_AVAILABLE_LOCALLY:d:Lokal\n"
                       "MENU_ITEM:MAKE_ONLINE_ONLY::Online\n")
        online_burst = ("MENU_ITEM:MAKE_AVAILABLE_LOCALLY::Lokal\n"
                        "MENU_ITEM:MAKE_ONLINE_ONLY:d:Online\n")
        self.assertEqual(self.ncpin.parse_state(local_burst), "local")
        self.assertEqual(self.ncpin.parse_state(online_burst), "online")


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


class RenameTransportTests(unittest.TestCase):
    """Deterministische Tests des Rename-Transports (Client v34+).

    Es gibt weder Socket noch echten Client: Config und Sync-Journal sind
    Fixtures, die Socket-Discovery ist auf "nichts gefunden" gemockt und der
    Client-Prozess-Check auf "laeuft". So testen wir exakt die Logik von
    Zustand-Lesen, Umbenennen, Journal-Vorpruefung und Grenzschutz.
    """

    def setUp(self):
        self.ncpin = load_ncpin()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "Nextcloud")
        self.outside = os.path.join(self.tmp.name, "outside")
        os.mkdir(self.root)
        os.mkdir(self.outside)
        self.cfg = os.path.join(self.tmp.name, "nextcloud.cfg")
        with open(self.cfg, "w", encoding="utf-8") as handle:
            handle.write(
                "[Accounts]\n"
                "0\\FoldersWithPlaceholders\\1\\localPath=%s/\n"
                "0\\FoldersWithPlaceholders\\1\\virtualFilesMode=suffix\n"
                "0\\FoldersWithPlaceholders\\1\\journalPath=.sync_test.db\n"
                % self.root)
        self.journal = os.path.join(self.root, ".sync_test.db")
        conn = sqlite3.connect(self.journal)
        conn.execute("CREATE TABLE metadata "
                     "(path TEXT PRIMARY KEY, filesize INTEGER, modtime INTEGER)")
        conn.commit()
        conn.close()

    def tearDown(self):
        self.tmp.cleanup()

    def journal_add(self, rel_path, filesize, modtime):
        conn = sqlite3.connect(self.journal)
        conn.execute("INSERT OR REPLACE INTO metadata VALUES (?, ?, ?)",
                     (rel_path, filesize, int(modtime)))
        conn.commit()
        conn.close()

    def make_file(self, rel_path, content=b"inhalt", synced=True):
        """Legt eine hydrierte Datei an, optional mit passendem Journal-Eintrag."""
        full = os.path.join(self.root, rel_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as handle:
            handle.write(content)
        if synced:
            st = os.stat(full)
            self.journal_add(rel_path, st.st_size, int(st.st_mtime))
        return full

    def make_placeholder(self, rel_path):
        """Legt einen dehydrierten 1-Byte-Platzhalter an."""
        full = os.path.join(self.root, rel_path) + self.ncpin.SUFFIX
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as handle:
            handle.write(b" ")
        return full

    def run_cli(self, *args):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(self.ncpin, "discover_socket_with_paths",
                               return_value=(None, [])), \
                mock.patch.object(self.ncpin, "client_process_running",
                                  return_value=True), \
                mock.patch.dict(os.environ, {"NCPIN_CONFIG": self.cfg,
                                             "NCPIN_SOCKET": ""}), \
                contextlib.redirect_stdout(stdout), \
                contextlib.redirect_stderr(stderr):
            rc = self.ncpin.main(list(args))
        return rc, stdout.getvalue(), stderr.getvalue()

    def test_status_reads_states_from_filesystem(self):
        local_file = self.make_file("lokal.txt")
        self.make_placeholder("online.txt")
        logical_online = os.path.join(self.root, "online.txt")

        rc, out, _err = self.run_cli("status", "--json", local_file, logical_online)

        self.assertEqual(rc, 0)
        states = {r["path"]: r["state"] for r in json.loads(out)}
        self.assertEqual(states[os.path.realpath(local_file)], "local")
        self.assertEqual(states[os.path.realpath(logical_online +
                                                 self.ncpin.SUFFIX)], "online")

    def test_local_renames_placeholder_for_download(self):
        placeholder = self.make_placeholder("film.mp4")
        logical = os.path.join(self.root, "film.mp4")

        rc, _out, _err = self.run_cli("local", logical)

        self.assertEqual(rc, 0)
        self.assertFalse(os.path.exists(placeholder))
        self.assertTrue(os.path.exists(logical))

    def test_online_renames_synced_file(self):
        logical = self.make_file("doku/bericht.pdf", b"voller inhalt")

        rc, _out, _err = self.run_cli("online", logical)

        self.assertEqual(rc, 0)
        self.assertFalse(os.path.exists(logical))
        self.assertTrue(os.path.exists(logical + self.ncpin.SUFFIX))

    def test_online_refuses_file_missing_from_journal(self):
        logical = self.make_file("neu.txt", synced=False)

        rc, _out, _err = self.run_cli("online", "--json", logical)

        self.assertEqual(rc, 1)
        self.assertTrue(os.path.exists(logical))
        self.assertFalse(os.path.exists(logical + self.ncpin.SUFFIX))

    def test_online_refuses_locally_modified_file(self):
        logical = self.make_file("geaendert.txt", b"alter inhalt")
        # Datei nachtraeglich veraendern: Journal passt nicht mehr.
        with open(logical, "ab") as handle:
            handle.write(b" plus neue bytes")

        rc, out, _err = self.run_cli("online", "--json", logical)

        self.assertEqual(rc, 1)
        self.assertIn("Dehydrierung abgelehnt", json.loads(out)[0]["error"])
        self.assertTrue(os.path.exists(logical))

    def test_boundary_outside_root_is_rejected_without_rename(self):
        foreign = os.path.join(self.outside, "fremd.txt")
        with open(foreign, "w", encoding="utf-8") as handle:
            handle.write("nicht anfassen")

        rc, _out, _err = self.run_cli("online", foreign)

        self.assertEqual(rc, 3)
        self.assertTrue(os.path.exists(foreign))
        self.assertFalse(os.path.exists(foreign + self.ncpin.SUFFIX))

    def test_symlink_escape_is_rejected_without_rename(self):
        foreign = os.path.join(self.outside, "geheim.txt")
        with open(foreign, "w", encoding="utf-8") as handle:
            handle.write("privat")
        link = os.path.join(self.root, "link.txt")
        os.symlink(foreign, link)

        rc, _out, _err = self.run_cli("online", link)

        self.assertEqual(rc, 3)
        self.assertTrue(os.path.exists(foreign))
        self.assertFalse(os.path.exists(foreign + self.ncpin.SUFFIX))

    def test_toggle_aborts_on_mixed_folder_without_renames(self):
        local_file = self.make_file("gemischt/lokal.txt")
        placeholder = self.make_placeholder("gemischt/online.txt")
        folder = os.path.join(self.root, "gemischt")

        rc, _out, _err = self.run_cli("toggle", folder)

        self.assertEqual(rc, 1)
        self.assertTrue(os.path.exists(local_file))
        self.assertTrue(os.path.exists(placeholder))

    def test_folder_online_dehydrates_all_synced_files(self):
        first = self.make_file("ordner/a.txt", b"aaaa")
        second = self.make_file("ordner/tief/b.txt", b"bbbb")
        folder = os.path.join(self.root, "ordner")

        rc, _out, _err = self.run_cli("online", folder)

        self.assertEqual(rc, 0)
        for logical in (first, second):
            self.assertFalse(os.path.exists(logical))
            self.assertTrue(os.path.exists(logical + self.ncpin.SUFFIX))

    def test_action_refused_when_client_not_running(self):
        logical = self.make_file("ohne-client.txt")
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(self.ncpin, "discover_socket_with_paths",
                               return_value=(None, [])), \
                mock.patch.object(self.ncpin, "client_process_running",
                                  return_value=False), \
                mock.patch.dict(os.environ, {"NCPIN_CONFIG": self.cfg,
                                             "NCPIN_SOCKET": ""}), \
                contextlib.redirect_stdout(stdout), \
                contextlib.redirect_stderr(stderr):
            rc = self.ncpin.main(["online", logical])

        self.assertEqual(rc, 1)
        self.assertTrue(os.path.exists(logical))

    def test_doctor_reports_rename_transport(self):
        rc, out, _err = self.run_cli("doctor", "--json")

        self.assertEqual(rc, 0)
        info = json.loads(out)
        self.assertEqual(info["transport"], "rename")
        self.assertTrue(info["backend_ok"])
        self.assertTrue(info["client_responds"])
        self.assertIsNone(info["socket"])
        self.assertEqual(info["registered_folders"], [os.path.realpath(self.root)])

    def test_status_reports_empty_placeholder_as_unknown(self):
        # Der Vertrag verlangt EXAKT 1 Byte: eine leere Suffixdatei ist kein
        # gueltiger Platzhalter und darf nicht als "online" durchgehen.
        empty = os.path.join(self.root, "leer.txt") + self.ncpin.SUFFIX
        with open(empty, "wb"):
            pass

        rc, out, _err = self.run_cli("status", "--json", empty)

        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)[0]["state"], "unknown")

    def test_online_with_full_content_placeholder_is_an_error(self):
        # Platzhalter mit Vollinhalt = vom Client ignorierter Dehydrierungs-
        # wunsch. "online" darauf darf nicht mit Exit 0 Erfolg vortaeuschen.
        stale = os.path.join(self.root, "haengt.txt") + self.ncpin.SUFFIX
        with open(stale, "wb") as handle:
            handle.write(b"voller inhalt")

        rc, out, _err = self.run_cli(
            "online", "--json", os.path.join(self.root, "haengt.txt"))

        self.assertEqual(rc, 1)
        self.assertIn("Zustand unklar", json.loads(out)[0]["error"])
        self.assertTrue(os.path.exists(stale))

    def test_folder_state_exact_ignores_scan_cap_and_unknown_children(self):
        # Stichprobe (exact=False) darf am Deckel abbrechen; die
        # Entscheidungsvariante (exact=True) muss den ganzen Baum sehen und
        # "unknown"-Kinder weiterreichen.
        self.make_file("kaputt/echt.txt")
        broken = os.path.join(self.root, "kaputt",
                              "muell.txt") + self.ncpin.SUFFIX
        with open(broken, "wb") as handle:
            handle.write(b"vollinhalt")
        folder = os.path.join(self.root, "kaputt")

        self.assertEqual(self.ncpin.fs_folder_state(folder), "local")
        self.assertEqual(self.ncpin.fs_folder_state(folder, exact=True),
                         "unknown")

    def test_toggle_folder_ignores_scan_cap_and_aborts_on_mixed_tree(self):
        # Mit Deckel 1 sieht die Stichprobe nur eine Datei; toggle muss den
        # gemischten Baum trotzdem vollstaendig erkennen und abbrechen.
        local_file = self.make_file("gross/a-lokal.txt")
        placeholder = self.make_placeholder("gross/z-online.txt")
        folder = os.path.join(self.root, "gross")

        with mock.patch.object(self.ncpin, "FOLDER_SCAN_CAP", 1):
            rc, _out, _err = self.run_cli("toggle", folder)

        self.assertEqual(rc, 1)
        self.assertTrue(os.path.exists(local_file))
        self.assertTrue(os.path.exists(placeholder))

    def test_wait_folder_requires_full_tree_not_sample(self):
        # --wait darf Erfolg nicht aus einer gedeckelten Stichprobe ableiten:
        # hinter dem Deckel liegt noch ein Platzhalter -> Ziel "local" ist
        # NICHT erreicht.
        self.make_file("warte/a.txt")
        self.make_placeholder("warte/z.txt")
        folder = os.path.join(self.root, "warte")

        with mock.patch.object(self.ncpin, "FOLDER_SCAN_CAP", 1):
            reached = self.ncpin.rename_wait_for_state(
                folder, "local", 0.6, [self.root])

        self.assertFalse(reached)

    def test_traversal_is_anchored_against_directory_symlink_swap(self):
        # Simuliert das TOCTOU-Fenster der frueheren os.walk-Traversierung:
        # os.path.islink meldet (wie nach einem Austausch KURZ NACH der
        # Pruefung) faelschlich "kein Symlink". Die an Verzeichnis-fds
        # verankerte Traversierung (O_NOFOLLOW) darf dem Symlink trotzdem
        # nicht folgen und nichts ausserhalb der Syncwurzel umbenennen.
        outside_placeholder = os.path.join(
            self.outside, "geheim.txt") + self.ncpin.SUFFIX
        with open(outside_placeholder, "wb") as handle:
            handle.write(b" ")
        folder = os.path.join(self.root, "ordner")
        os.makedirs(folder)
        inside_placeholder = self.make_placeholder("ordner/drin.txt")
        os.symlink(self.outside, os.path.join(folder, "evil"))

        with mock.patch.object(self.ncpin.os.path, "islink",
                               return_value=False):
            rc, _out, _err = self.run_cli("local", folder)

        self.assertEqual(rc, 0)
        # Innerhalb der Wurzel wurde gearbeitet, ausserhalb nicht.
        self.assertFalse(os.path.exists(inside_placeholder))
        self.assertTrue(os.path.exists(outside_placeholder))
        self.assertFalse(os.path.exists(
            os.path.join(self.outside, "geheim.txt")))

    def test_exclusive_rename_refuses_when_target_appears(self):
        # Der exklusive Rename (renameatx_np + RENAME_EXCL) darf ein parallel
        # entstandenes Ziel nie still ueberschreiben.
        folder = os.path.join(self.root, "excl")
        os.makedirs(folder)
        for name in ("quelle.txt", "ziel.txt"):
            with open(os.path.join(folder, name), "wb") as handle:
                handle.write(name.encode("utf-8"))
        dirfd = os.open(folder, self.ncpin._O_DIR_NOFOLLOW)
        try:
            with self.assertRaises(RuntimeError):
                self.ncpin._rename_excl_at(dirfd, "quelle.txt", "ziel.txt")
            with open(os.path.join(folder, "ziel.txt"), "rb") as handle:
                self.assertEqual(handle.read(), b"ziel.txt")
            os.unlink(os.path.join(folder, "ziel.txt"))
            self.ncpin._rename_excl_at(dirfd, "quelle.txt", "ziel.txt")
        finally:
            os.close(dirfd)
        self.assertTrue(os.path.exists(os.path.join(folder, "ziel.txt")))
        self.assertFalse(os.path.exists(os.path.join(folder, "quelle.txt")))

    def write_config_without_journal_path(self):
        with open(self.cfg, "w", encoding="utf-8") as handle:
            handle.write(
                "[Accounts]\n"
                "0\\FoldersWithPlaceholders\\1\\localPath=%s/\n"
                "0\\FoldersWithPlaceholders\\1\\virtualFilesMode=suffix\n"
                % self.root)

    def test_dehydration_refuses_ambiguous_journal_fallback(self):
        # Ohne konfigurierten Journalpfad und mit ZWEI .sync_*.db-Kandidaten
        # darf nicht blind der erste gewaehlt werden.
        logical = self.make_file("mehrdeutig.txt", b"inhalt")
        self.write_config_without_journal_path()
        second = os.path.join(self.root, ".sync_zweit.db")
        conn = sqlite3.connect(second)
        conn.execute("CREATE TABLE metadata "
                     "(path TEXT PRIMARY KEY, filesize INTEGER, modtime INTEGER)")
        conn.commit()
        conn.close()

        rc, out, _err = self.run_cli("online", "--json", logical)

        self.assertEqual(rc, 1)
        self.assertIn("mehrere Sync-Journale", json.loads(out)[0]["error"])
        self.assertTrue(os.path.exists(logical))

    def test_dehydration_uses_unique_journal_fallback_without_config(self):
        # Genau EIN Journal im Ordner bleibt als eindeutiger Fallback erlaubt.
        logical = self.make_file("eindeutig.txt", b"inhalt")
        self.write_config_without_journal_path()

        rc, _out, _err = self.run_cli("online", logical)

        self.assertEqual(rc, 0)
        self.assertTrue(os.path.exists(logical + self.ncpin.SUFFIX))

    def test_path_error_exit_code_survives_later_runtime_error(self):
        # README: Exit 3 gilt fuer "ein/mehrere Pfade" — ein spaeterer
        # Laufzeitfehler (Exit 1) eines anderen Pfads darf das nicht
        # herabstufen.
        missing = os.path.join(self.root, "fehlt.txt")
        unsynced = self.make_file("ungesynct.txt", synced=False)

        rc, out, _err = self.run_cli("online", "--json", missing, unsynced)

        self.assertEqual(rc, 3)
        self.assertEqual(len(json.loads(out)), 2)

    def test_explicit_stale_socket_never_falls_back_to_rename(self):
        stale = os.path.join(self.tmp.name, "stale")
        with open(stale, "w", encoding="utf-8") as handle:
            handle.write("kein Socket")
        logical = self.make_file("strikt.txt")
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.dict(os.environ, {"NCPIN_CONFIG": self.cfg,
                                          "NCPIN_SOCKET": ""}), \
                contextlib.redirect_stdout(stdout), \
                contextlib.redirect_stderr(stderr):
            rc = self.ncpin.main(["online", logical, "--socket", stale])

        self.assertEqual(rc, 1)
        self.assertTrue(os.path.exists(logical))
        self.assertFalse(os.path.exists(logical + self.ncpin.SUFFIX))


if __name__ == "__main__":
    unittest.main()
