#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministische Tests fuer Protokoll, Pfadschutz und CLI von ncpin."""

import contextlib
import errno
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
import unicodedata
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
        self.menu_chunks = None
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
                        chunks = self.menu_chunks or [
                            b"MENU_ITEM:MAKE_AVAILABLE_LOCALLY:d:Lokal\n"
                            b"MENU_ITEM:MAKE_ONLINE_ONLY::Online\n"
                            b"GET_MENU_ITEMS:END\n"
                        ]
                        try:
                            for chunk in chunks:
                                conn.sendall(chunk)
                                time.sleep(0.02)
                        except BrokenPipeError:
                            pass
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

    def protocol_fixture(self):
        path = os.path.join(self.root, "fixture.txt")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("fixture")
        return path

    def test_fragmented_menu_response_including_utf8_and_end_marker(self):
        path = self.protocol_fixture()
        response = ("MENU_ITEM:OTHER::Zusätzlich\n"
                    "MENU_ITEM:MAKE_AVAILABLE_LOCALLY:d:Lokal\n"
                    "MENU_ITEM:MAKE_ONLINE_ONLY::Online\n"
                    "GET_MENU_ITEMS:END\n").encode("utf-8")
        split = response.index("ä".encode("utf-8")) + 1
        self.server.menu_chunks = [response[:split], response[split:65],
                                   response[65:-3], response[-3:]]
        rc, out, err = self.run_cli("status", "--json", path)
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)[0]["state"], "local")

    def test_unknown_toggle_sends_no_make_command(self):
        path = self.protocol_fixture()
        self.server.menu_chunks = [b"MENU_ITEM:OTHER::Other\nGET_MENU_ITEMS:END\n"]
        rc, out, _err = self.run_cli("toggle", "--json", path)
        self.assertEqual(rc, 1)
        self.assertIn("toggle abgebrochen", json.loads(out)[0]["error"])
        self.assertFalse(any(cmd.startswith("MAKE_") for cmd in self.server.commands))

    def test_wait_times_out_after_make_without_state_change(self):
        path = self.protocol_fixture()
        started = time.monotonic()
        rc, out, _err = self.run_cli("online", "--wait", "--timeout", "0.2",
                                     "--json", path)
        elapsed = time.monotonic() - started
        self.assertEqual(rc, 1)
        result = json.loads(out)[0]
        self.assertTrue(result["waited"])
        self.assertFalse(result["reached"])
        self.assertGreaterEqual(elapsed, 0.2)
        self.assertLess(elapsed, 3)
        self.assertIn("MAKE_ONLINE_ONLY:" + os.path.realpath(path), self.server.commands)
        self.assertTrue(any(cmd.startswith("GET_MENU_ITEMS:")
                            for cmd in self.server.commands))

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
        conn.execute(
            "CREATE TABLE metadata "
            "(path TEXT PRIMARY KEY, filesize INTEGER, modtime INTEGER, "
            "inode INTEGER, type INTEGER)")
        conn.commit()
        conn.close()

    def tearDown(self):
        self.tmp.cleanup()

    def journal_add(self, rel_path, filesize, modtime, inode, item_type=0):
        conn = sqlite3.connect(self.journal)
        conn.execute("INSERT OR REPLACE INTO metadata VALUES (?, ?, ?, ?, ?)",
                     (rel_path, filesize, int(modtime), int(inode), item_type))
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
            self.journal_add(rel_path, st.st_size, int(st.st_mtime), st.st_ino)
        return full

    def make_placeholder(self, rel_path, synced=True, journal_inode=None,
                         journal_modtime=None, journal_type=4):
        """Legt einen dehydrierten 1-Byte-Platzhalter samt Journal-Eintrag an."""
        full = os.path.join(self.root, rel_path) + self.ncpin.SUFFIX
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as handle:
            handle.write(b" ")
        if synced:
            st = os.stat(full)
            inode = st.st_ino if journal_inode is None else journal_inode
            modtime = (int(st.st_mtime) if journal_modtime is None
                       else journal_modtime)
            self.journal_add(rel_path + self.ncpin.SUFFIX, 1234, modtime,
                             inode, journal_type)
        return full

    def run_cli(self, *args, race=None):
        """Fuehrt die CLI gegen die Fixtures aus.

        ``race`` ist ein Rueckruf, der beim Client-Check laeuft. Der findet in
        rename_do_action GENAU zwischen der Grenzpruefung (checked_path) und
        dem Oeffnen des Verzeichnisses statt und ist damit ein
        deterministischer Aufhaenger fuer TOCTOU-Szenarien — ohne Threads und
        ohne Warten.
        """
        stdout = io.StringIO()
        stderr = io.StringIO()
        if race is None:
            client_patch = mock.patch.object(
                self.ncpin, "client_process_running", return_value=True)
        else:
            def running_after_race():
                race()
                return True
            client_patch = mock.patch.object(
                self.ncpin, "client_process_running",
                side_effect=running_after_race)
        with mock.patch.object(self.ncpin, "discover_socket_with_paths",
                               return_value=(None, [])), \
                client_patch, \
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

    def test_local_refuses_placeholder_missing_from_journal(self):
        placeholder = self.make_placeholder("ohne-journal.mp4", synced=False)
        logical = os.path.join(self.root, "ohne-journal.mp4")

        rc, out, _err = self.run_cli("local", "--json", logical)

        self.assertEqual(rc, 1)
        self.assertIn("Hydrierung abgelehnt", json.loads(out)[0]["error"])
        self.assertTrue(os.path.exists(placeholder))
        self.assertFalse(os.path.exists(logical))

    def test_local_refuses_placeholder_with_stale_journal_inode(self):
        placeholder = self.make_placeholder("stale-inode.mp4", synced=False)
        st = os.stat(placeholder)
        self.journal_add("stale-inode.mp4" + self.ncpin.SUFFIX,
                         1234, int(st.st_mtime),
                         st.st_ino + 1, item_type=4)
        logical = os.path.join(self.root, "stale-inode.mp4")

        rc, out, _err = self.run_cli("local", "--json", logical)

        self.assertEqual(rc, 1)
        self.assertIn("Journal-Inode", json.loads(out)[0]["error"])
        self.assertTrue(os.path.exists(placeholder))
        self.assertFalse(os.path.exists(logical))

    def test_local_refuses_placeholder_with_stale_journal_mtime(self):
        placeholder = self.make_placeholder("stale-mtime.mp4", synced=False)
        st = os.stat(placeholder)
        self.journal_add("stale-mtime.mp4" + self.ncpin.SUFFIX,
                         1234, int(st.st_mtime) - 10,
                         st.st_ino, item_type=4)
        logical = os.path.join(self.root, "stale-mtime.mp4")

        rc, out, _err = self.run_cli("local", "--json", logical)

        self.assertEqual(rc, 1)
        self.assertIn("Änderungszeit", json.loads(out)[0]["error"])
        self.assertTrue(os.path.exists(placeholder))
        self.assertFalse(os.path.exists(logical))

    def test_local_refuses_nonvirtual_journal_record(self):
        placeholder = self.make_placeholder("falscher-typ.mp4", synced=False)
        st = os.stat(placeholder)
        self.journal_add("falscher-typ.mp4" + self.ncpin.SUFFIX,
                         1234, int(st.st_mtime),
                         st.st_ino, item_type=0)
        logical = os.path.join(self.root, "falscher-typ.mp4")

        rc, out, _err = self.run_cli("local", "--json", logical)

        self.assertEqual(rc, 1)
        self.assertIn("kein virtueller Journal-Eintrag",
                      json.loads(out)[0]["error"])
        self.assertTrue(os.path.exists(placeholder))
        self.assertFalse(os.path.exists(logical))

    def test_local_folder_preflight_prevents_partial_renames(self):
        good = self.make_placeholder("baum/a-gueltig.mp4")
        stale = self.make_placeholder("baum/z-stale.mp4", synced=False)
        st = os.stat(stale)
        self.journal_add("baum/z-stale.mp4" + self.ncpin.SUFFIX,
                         1234, int(st.st_mtime),
                         st.st_ino + 1, item_type=4)
        folder = os.path.join(self.root, "baum")

        rc, out, _err = self.run_cli("local", "--json", folder)

        self.assertEqual(rc, 1)
        self.assertIn("vor dem ersten Rename", json.loads(out)[0]["error"])
        self.assertTrue(os.path.exists(good))
        self.assertTrue(os.path.exists(stale))
        self.assertFalse(os.path.exists(good[:-len(self.ncpin.SUFFIX)]))
        self.assertFalse(os.path.exists(stale[:-len(self.ncpin.SUFFIX)]))

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

    def test_traversal_never_descends_into_a_symlinked_subdirectory(self):
        # Ein Symlink im Ordner darf beim rekursiven Lauf nie betreten werden.
        # Die an Verzeichnis-fds verankerte Traversierung erkennt ihn ueber
        # DirEntry.is_symlink() und laesst ihn aus; ausserhalb der Syncwurzel
        # wird nichts umbenannt.
        outside_placeholder = os.path.join(
            self.outside, "geheim.txt") + self.ncpin.SUFFIX
        with open(outside_placeholder, "wb") as handle:
            handle.write(b" ")
        folder = os.path.join(self.root, "ordner")
        os.makedirs(folder)
        inside_placeholder = self.make_placeholder("ordner/drin.txt")
        os.symlink(self.outside, os.path.join(folder, "evil"))

        rc, _out, _err = self.run_cli("local", folder)

        self.assertEqual(rc, 0)
        # Innerhalb der Wurzel wurde gearbeitet, ausserhalb nicht.
        self.assertFalse(os.path.exists(inside_placeholder))
        self.assertTrue(os.path.exists(outside_placeholder))
        self.assertFalse(os.path.exists(
            os.path.join(self.outside, "geheim.txt")))

    def test_intermediate_directory_swap_after_check_cannot_escape_root(self):
        # Echtes TOCTOU-Fenster: NACH checked_path() wird ein ZWISCHEN-
        # verzeichnis des Pfads durch einen Symlink nach draussen ersetzt.
        # os.open(pfad, O_NOFOLLOW) haette dem gefolgt (O_NOFOLLOW schuetzt nur
        # die letzte Komponente) und ausserhalb der Syncwurzel umbenannt.
        # Der komponentenweise Aufbau muss hier scheitern statt auszubrechen.
        inside_placeholder = self.make_placeholder("zwischen/tief/datei.txt")
        os.makedirs(os.path.join(self.outside, "tief"))
        outside_placeholder = os.path.join(
            self.outside, "tief", "datei.txt") + self.ncpin.SUFFIX
        with open(outside_placeholder, "wb") as handle:
            handle.write(b" ")
        logical = os.path.join(self.root, "zwischen", "tief", "datei.txt")

        def swap_intermediate_for_symlink():
            victim = os.path.join(self.root, "zwischen")
            os.rename(victim, victim + ".weg")
            os.symlink(self.outside, victim)

        rc, _out, _err = self.run_cli("local", logical,
                                      race=swap_intermediate_for_symlink)

        # ELOOP beim komponentenweisen Oeffnen heisst: Der aufgeloeste Pfad ist
        # nicht mehr der gepruefte. Das ist laut CLI-Vertrag ein Pfadfehler
        # (Exit 3) und kein Laufzeitfehler (Exit 1).
        self.assertEqual(rc, 3)
        # Ausserhalb wurde nichts angefasst; der echte Platzhalter liegt noch da.
        self.assertTrue(os.path.exists(outside_placeholder))
        self.assertFalse(os.path.exists(
            os.path.join(self.outside, "tief", "datei.txt")))
        self.assertTrue(os.path.exists(
            inside_placeholder.replace("/zwischen/", "/zwischen.weg/")))

    def test_open_folder_moved_out_of_root_is_not_renamed_outside(self):
        # Ein Verzeichnis-fd haengt an der Inode, nicht am Pfad: Wird der schon
        # geoeffnete Ordner danach aus der Syncwurzel geschoben, wuerden weitere
        # Renames ausserhalb jeder registrierten Wurzel wirken. Die Pruefung
        # unmittelbar vor dem Rename muss das mit Exit 3 stoppen.
        self.make_placeholder("ordner/datei.txt")
        folder = os.path.join(self.root, "ordner")
        moved = os.path.join(self.outside, "verschoben")
        original_open = self.ncpin._open_canonical_dir

        def open_then_move(path):
            dirfd = original_open(path)
            if os.path.basename(path) == "ordner":
                os.rename(folder, moved)
            return dirfd

        with mock.patch.object(self.ncpin, "_open_canonical_dir",
                               side_effect=open_then_move):
            rc, _out, _err = self.run_cli("local", folder)

        self.assertEqual(rc, 3)
        # Der Platzhalter liegt unveraendert am neuen (fremden) Ort.
        self.assertTrue(os.path.exists(
            os.path.join(moved, "datei.txt") + self.ncpin.SUFFIX))
        self.assertFalse(os.path.exists(os.path.join(moved, "datei.txt")))

    def _run_with_folder_moved_during_journal_load(self, target, rel_path):
        """Verschiebt den Elternordner WAEHREND des Journal-Ladens nach draussen.

        Das Journal wird erst nach dem Oeffnen des Verzeichnis-fds geladen (Klon
        + SQLite-Lesen). Ein Verschieben in genau diesem Fenster laesst den
        Deskriptor an seiner Inode haengen, die jetzt ausserhalb der Wurzel
        liegt. Ohne erneute Pruefung direkt vor dem Rename wuerde ncpin dort
        draussen umbenennen.
        """
        folder = os.path.join(self.root, "ordner")
        moved = os.path.join(self.outside, "verschoben")
        original_load = self.ncpin._load_journal_entries

        def load_then_move(journal_folder):
            entries = original_load(journal_folder)
            os.rename(folder, moved)
            return entries

        with mock.patch.object(self.ncpin, "_load_journal_entries",
                               side_effect=load_then_move):
            rc, _out, _err = self.run_cli(target, os.path.join(folder, rel_path))
        return rc, moved

    def test_folder_moved_after_journal_check_blocks_hydration_rename(self):
        # Direkte Dateiaktion: Grenzpruefung, dann Journal-Klon, dann Rename.
        # Wird der geoeffnete Ordner zwischen Journalpruefung und Rename aus
        # der Syncwurzel geschoben, muss der Rename mit Exit 3 unterbleiben.
        self.make_placeholder("ordner/datei.txt")

        rc, moved = self._run_with_folder_moved_during_journal_load(
            "local", "datei.txt")

        self.assertEqual(rc, 3)
        self.assertTrue(os.path.exists(
            os.path.join(moved, "datei.txt") + self.ncpin.SUFFIX))
        self.assertFalse(os.path.exists(os.path.join(moved, "datei.txt")))

    def test_folder_moved_after_journal_check_blocks_dehydration_rename(self):
        # Dasselbe Fenster fuer die Gegenrichtung: _require_synced laedt das
        # Journal, danach darf ohne erneute Grenzpruefung kein Rename folgen.
        self.make_file("ordner/datei.txt", b"voller inhalt")

        rc, moved = self._run_with_folder_moved_during_journal_load(
            "online", "datei.txt")

        self.assertEqual(rc, 3)
        self.assertTrue(os.path.exists(os.path.join(moved, "datei.txt")))
        self.assertFalse(os.path.exists(
            os.path.join(moved, "datei.txt") + self.ncpin.SUFFIX))

    def _skip_unless_case_insensitive(self):
        probe = os.path.join(self.tmp.name, "CaseProbe")
        with open(probe, "wb") as handle:
            handle.write(b"x")
        if not os.path.exists(os.path.join(self.tmp.name, "caseprobe")):
            self.skipTest("Dateisystem ist case-sensitive")

    def test_case_variant_file_name_hydrates_the_stored_placeholder(self):
        # Direkte Dateiaktion mit anders geschriebenem DATEInamen: Der Kernel
        # findet CaseName.txt.nextcloud auch ueber CASENAME.TXT, der
        # Journalabgleich braucht aber die gespeicherte Schreibweise. Vorher
        # endete das mit Exit 1 ("nicht im Sync-Journal").
        self._skip_unless_case_insensitive()
        placeholder = self.make_placeholder("CaseName.txt")
        variant = os.path.join(self.root, "CASENAME.TXT")

        rc, _out, err = self.run_cli("local", variant)

        self.assertEqual(rc, 0, err)
        self.assertFalse(os.path.exists(placeholder))
        self.assertIn("CaseName.txt", os.listdir(self.root))

    def test_case_variant_file_name_dehydrates_the_stored_file(self):
        self._skip_unless_case_insensitive()
        stored = self.make_file("CaseName.txt", b"voller inhalt")
        variant = os.path.join(self.root, "casename.txt")

        rc, _out, err = self.run_cli("online", variant)

        self.assertEqual(rc, 0, err)
        self.assertFalse(os.path.exists(stored))
        self.assertIn("CaseName.txt" + self.ncpin.SUFFIX, os.listdir(self.root))

    def test_case_variant_selects_requested_hardlink(self):
        self._skip_unless_case_insensitive()
        other = self.make_file("z-target.txt", b"voller inhalt")
        requested = os.path.join(self.root, "a-other.txt")
        os.link(other, requested)
        dirfd = os.open(self.root, os.O_RDONLY)
        try:
            self.assertEqual(self.ncpin._stored_name_at(dirfd, "A-OTHER.TXT"), "a-other.txt")
        finally:
            os.close(dirfd)
        self.assertTrue(os.path.exists(other))

    def test_unicode_case_variant_hydrates_the_stored_placeholder(self):
        self._skip_unless_case_insensitive()
        placeholder = self.make_placeholder('\u0390.txt')
        variant = os.path.join(self.root, '\u03aa\u0301.TXT')

        rc, _out, err = self.run_cli('local', variant)

        self.assertEqual(rc, 0, err)
        self.assertFalse(os.path.exists(placeholder))
        self.assertIn('\u0390.txt', os.listdir(self.root))

    def test_unicode_case_variant_dehydrates_the_stored_file(self):
        self._skip_unless_case_insensitive()
        stored = self.make_file('\u0390.txt', b'voller inhalt')
        variant = os.path.join(self.root, '\u03aa\u0301.TXT')

        rc, _out, err = self.run_cli('online', variant)

        self.assertEqual(rc, 0, err)
        self.assertFalse(os.path.exists(stored))
        self.assertIn('\u0390.txt' + self.ncpin.SUFFIX, os.listdir(self.root))

    def test_folder_scan_budget_counts_directories_and_hidden_entries(self):
        for name_prefix, is_directory in [('folder-', True), ('.hidden-', False)]:
            with self.subTest(prefix=name_prefix):
                folder = os.path.join(self.root, name_prefix + 'sample')
                os.makedirs(folder)
                for i in range(self.ncpin.FOLDER_SCAN_CAP + 100):
                    path = os.path.join(folder, name_prefix + str(i))
                    if is_directory:
                        os.mkdir(path)
                    else:
                        with open(path, 'wb') as handle:
                            handle.write(b'xx')
                consumed = [0]
                real_scandir = os.scandir
                class CountingScandir:
                    def __init__(self, inner): self.inner = inner
                    def __iter__(self): return self
                    def __next__(self):
                        value = next(self.inner)
                        consumed[0] += 1
                        return value
                    def __enter__(self): return self
                    def __exit__(self, *args): self.inner.close()
                with mock.patch('os.scandir', side_effect=lambda *a, **k: CountingScandir(real_scandir(*a, **k))):
                    self.ncpin.fs_folder_state(folder)
                self.assertLessEqual(consumed[0], self.ncpin.FOLDER_SCAN_CAP)

    def test_dehydrating_a_folder_preflights_every_file_before_the_first_rename(self):
        # Die READMEs versprechen: Bei einer Journalabweichung bricht ncpin ab,
        # BEVOR es im Ordner die erste Datei umbenennt — auch bei Dehydrierung.
        # Die erste Datei ist sauber gesynct, die zweite wurde nach dem
        # Journal-Eintrag geaendert. Ohne Vorpruefung waere die erste schon
        # suffigiert, wenn die zweite abgelehnt wird.
        first = self.make_file("ordner/a-sauber.txt", b"sauber")
        second = self.make_file("ordner/b-geaendert.txt", b"alt")
        with open(second, "wb") as handle:
            handle.write(b"neuer, laengerer inhalt")
        folder = os.path.join(self.root, "ordner")

        rc, out, _err = self.run_cli("online", "--json", folder)

        self.assertEqual(rc, 1)
        self.assertIn("vor dem ersten Rename abgelehnt",
                      json.loads(out)[0]["error"])
        self.assertTrue(os.path.exists(first))
        self.assertFalse(os.path.exists(first + self.ncpin.SUFFIX))
        self.assertTrue(os.path.exists(second))

    def test_umlaut_folder_in_the_other_unicode_form_still_renames(self):
        # APFS speichert einen Namen so, wie er angelegt wurde, findet ihn aber
        # auch in der anderen Unicode-Normalform. Der Kernel meldet den
        # GESPEICHERTEN Namen (hier NFD), der geprüfte Pfad kommt vom Nutzer
        # (hier NFC). Die Grenzprüfung vor dem Rename muss deshalb normalisiert
        # vergleichen — sonst scheitert schon ein deutscher Ordnername.
        nfd = unicodedata.normalize("NFD", "Büro")
        nfc = unicodedata.normalize("NFC", "Büro")
        os.makedirs(os.path.join(self.root, nfd))
        self.make_placeholder(os.path.join(nfd, "datei.txt"))
        folder = os.path.join(self.root, nfc)

        rc, _out, err = self.run_cli("local", folder)

        self.assertEqual(rc, 0, err)
        self.assertTrue(os.path.exists(
            os.path.join(self.root, nfd, "datei.txt")))

    def test_case_variant_folder_under_root_still_renames(self):
        # Auf APFS (case-insensitive) kann eine Pfadkomponente anders geschrieben
        # sein als auf der Platte (z.B. Buero vs BUERO). Die Grenzpruefung
        # muss pruefen, dass der aktuelle Pfad in der Wurzel liegt, statt
        # Zeichenkettengleichheit mit checked_path() zu erzwingen.
        os.makedirs(os.path.join(self.root, "Buero"))
        self.make_placeholder(os.path.join("Buero", "datei.txt"))
        folder_variant = os.path.join(self.root, "BUERO")

        rc, _out, err = self.run_cli("local", folder_variant)

        self.assertEqual(rc, 0, err)
        self.assertTrue(os.path.exists(
            os.path.join(self.root, "Buero", "datei.txt")))

    def test_deep_folder_path_beyond_maxpathlen_fails_closed_with_path_error(self):
        # Wenn _fd_path() mit ENOSPC scheitert (z.B. Pfad laenger als MAXPATHLEN),
        # muss _assert_dir_unmoved fail-closed mit PathOutsideRoots (Exit 3) reagieren,
        # statt einen ungeschuetzten Laufzeitfehler (Exit 1) zu werfen.
        self.make_placeholder("ordner/datei.txt")
        folder = os.path.join(self.root, "ordner")

        def fake_fd_path(dirfd):
            raise OSError(errno.ENOSPC, "No space left on device")

        with mock.patch.object(self.ncpin, "_fd_path", side_effect=fake_fd_path):
            rc, _out, _err = self.run_cli("local", folder)

        self.assertEqual(rc, 3)

    def test_folder_with_no_regular_files_reports_error(self):
        # Ein Ordner ohne regulaere Dateien darf von rename_do_action nicht
        # als Erfolg gewertet werden, da fs_folder_state "unknown" meldet und
        # ein nachfolgendes --wait sonst in den vollen Timeout laufen wuerde.
        foreign = os.path.join(self.outside, "fremd.txt")
        with open(foreign, "wb") as handle:
            handle.write(b"xy")
        folder = os.path.join(self.root, "nurlink")
        os.makedirs(folder)
        os.symlink(foreign, os.path.join(folder, "link.txt"))

        rc, out, _err = self.run_cli("local", "--json", folder)
        self.assertEqual(rc, 1)
        self.assertIn("keine regulären Dateien", json.loads(out)[0]["error"])

    def test_folder_scan_cap_bounds_consumed_directory_entries(self):
        # FOLDER_SCAN_CAP muss die GELESENEN Verzeichniseintraege begrenzen,
        # nicht nur nachgelagerte stat-Aufrufe: os.walk haette den kompletten
        # Ordner materialisiert, bevor die Schleife am Deckel abbricht. Gezaehlt
        # wird deshalb, wie viele Eintraege der scandir-Iterator herausgibt.
        folder = os.path.join(self.root, "viele_links")
        os.makedirs(folder)
        for i in range(self.ncpin.FOLDER_SCAN_CAP + 100):
            os.symlink("/nonexistent", os.path.join(folder, "link_%d" % i))
        consumed = [0]
        real_scandir = os.scandir

        class CountingScandir(object):
            def __init__(self, inner):
                self.inner = inner

            def __iter__(self):
                return self

            def __next__(self):
                consumed[0] += 1
                return next(self.inner)

            def __enter__(self):
                return self

            def __exit__(self, *exc_info):
                self.inner.close()

            def close(self):
                self.inner.close()

        with mock.patch("os.scandir",
                        side_effect=lambda *a, **k:
                        CountingScandir(real_scandir(*a, **k))):
            state = self.ncpin.fs_folder_state(folder, exact=False)

        self.assertEqual(state, "unknown")
        # +1: Der Eintrag, an dem der Deckel greift, wurde bereits gelesen.
        self.assertLessEqual(consumed[0], self.ncpin.FOLDER_SCAN_CAP + 1)

    def test_folder_state_ignores_symlink_to_a_file_outside_the_root(self):
        # Der Aktionspfad ueberspringt Symlinks; der Zustandsleser muss das auch
        # tun. Sonst leitet toggle seine Richtung aus einer fremden Datei
        # AUSSERHALB der Syncwurzel ab und meldet danach Exit 0, ohne einen
        # einzigen bearbeitbaren Eintrag umgeschaltet zu haben.
        foreign = os.path.join(self.outside, "fremd.txt")
        with open(foreign, "wb") as handle:
            handle.write(b"xy")          # 2 Byte -> saehe wie "lokal" aus
        folder = os.path.join(self.root, "nurlink")
        os.makedirs(folder)
        os.symlink(foreign, os.path.join(folder, "link.txt"))

        self.assertEqual(self.ncpin.fs_folder_state(folder, exact=True),
                         "unknown")
        rc, out, _err = self.run_cli("toggle", "--json", folder)

        self.assertEqual(rc, 1)
        self.assertIn("Zustand nicht ermittelbar", json.loads(out)[0]["error"])
        self.assertEqual(os.stat(foreign).st_size, 2)

    def test_unreadable_entry_is_not_taken_for_an_already_dehydrated_file(self):
        # Ein Zugriffsfehler beim Pruefen der Namen darf nie als "existiert
        # nicht" durchgehen: online haette die Datei sonst fuer bereits
        # dehydriert gehalten und Exit 0 gemeldet, ohne etwas zu tun.
        self.make_file("sperr/inhalt.txt", b"voller inhalt")
        folder = os.path.join(self.root, "sperr")

        os.chmod(folder, 0o400)  # lesbar, aber nicht durchsuchbar -> EACCES
        try:
            rc, out, _err = self.run_cli("online", "--json", folder)
        finally:
            os.chmod(folder, 0o755)

        self.assertEqual(rc, 1)
        # Die Ordner-Vorpruefung meldet den Fehler schon vor dem ersten Rename.
        self.assertIn("vor dem ersten Rename abgelehnt",
                      json.loads(out)[0]["error"])
        self.assertTrue(os.path.exists(os.path.join(folder, "inhalt.txt")))

    def test_folder_action_reports_failed_rename_instead_of_swallowing_it(self):
        # Ein echter Dateisystemfehler (hier EPERM) ist kein Verschwinde-Rennen.
        # Er darf nicht still uebergangen werden: Die Datei liegt unveraendert
        # da, also muss der Lauf Exit 1 melden statt Erfolg vorzutaeuschen.
        logical = self.make_file("ordner/wichtig.txt", b"voller inhalt")
        folder = os.path.join(self.root, "ordner")

        def refuse(dirfd, source_name, target_name):
            raise PermissionError(1, "Operation not permitted", target_name)

        with mock.patch.object(self.ncpin, "_rename_excl_at",
                               side_effect=refuse):
            rc, out, _err = self.run_cli("online", "--json", folder)

        self.assertEqual(rc, 1)
        self.assertIn("übersprungen", json.loads(out)[0]["error"])
        self.assertTrue(os.path.exists(logical))
        self.assertFalse(os.path.exists(logical + self.ncpin.SUFFIX))

    def test_dehydration_reads_metadata_after_loading_the_journal(self):
        # Das Journal wird beim ersten Zugriff geklont und ausgelesen; das
        # dauert. Aendert der Nutzer die Datei in genau diesem Fenster, darf
        # ncpin nicht gegen die vorher gelesenen Werte pruefen und den neuen
        # Inhalt freigeben — der Client wuerde die Umbenennung still ignorieren.
        logical = self.make_file("waechst.txt", b"kurz")
        original_load = self.ncpin._load_journal_entries

        def slow_load(folder):
            entries = original_load(folder)
            with open(logical, "wb") as handle:
                handle.write(b"viel laengerer neuer inhalt")
            return entries

        with mock.patch.object(self.ncpin, "_load_journal_entries",
                               side_effect=slow_load):
            rc, out, _err = self.run_cli("online", "--json", logical)

        self.assertEqual(rc, 1)
        self.assertIn("noch nicht gesynct", json.loads(out)[0]["error"])
        self.assertTrue(os.path.exists(logical))
        self.assertFalse(os.path.exists(logical + self.ncpin.SUFFIX))

    def test_folder_state_exact_reports_unreadable_subtree_as_unknown(self):
        # os.walk uebergeht einen nicht lesbaren Unterordner STILL. Im exakten
        # Modus darf daraus nie "alles lokal" werden: toggle wuerde sonst seine
        # Richtung aus einem unvollstaendigen Baum ableiten.
        self.make_file("baum/sichtbar.txt")
        gesperrt = os.path.join(self.root, "baum", "gesperrt")
        os.makedirs(gesperrt)
        with open(os.path.join(gesperrt, "drin.txt") + self.ncpin.SUFFIX,
                  "wb") as handle:
            handle.write(b" ")
        folder = os.path.join(self.root, "baum")

        os.chmod(gesperrt, 0o000)
        try:
            self.assertEqual(
                self.ncpin.fs_folder_state(folder, exact=True), "unknown")
            # Die unverbindliche Anzeige-Stichprobe darf weiterhin antworten.
            self.assertEqual(self.ncpin.fs_folder_state(folder), "local")
        finally:
            os.chmod(gesperrt, 0o755)

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
