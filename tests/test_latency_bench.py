#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit-Tests fuer das nicht mutierende Latenzgate und den sicheren Roundtrip."""

import importlib.machinery
import importlib.util
import json
import math
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
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

    def settle_quickly(self, polls=2):
        """Macht das Nachbeobachtungsfenster im Test kurz und wartefrei."""
        for attribute, value in (("SETTLE_POLLS", polls), ("SETTLE_PAUSE", 0)):
            patcher = mock.patch.object(self.bench, attribute, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_failed_transition_reasserts_initial_state_before_verifying(self):
        # Der Uebergang scheitert und der Zustand bleibt ueber das ganze
        # Nachbeobachtungsfenster beim Anfang. Das ist bei fire-and-forget KEIN
        # Beweis (AGENTS: "Ein Timeout beweist bei fire-and-forget nichts") —
        # der gesendete Befehl kann auch danach noch wirken. Der Anfangszustand
        # wird deshalb aktiv erneut beauftragt und nachgeprueft; die
        # Gegenrichtung des Anfangszustands wird dabei nie ein zweites Mal
        # gesendet.
        self.settle_quickly()
        calls = [
            status_result("local"),      # Anfangszustand
            (0.2, 1, "", "Fehler"),      # online: schlaegt fehl
            status_result("local"),      # Beobachtung direkt danach
            status_result("local"),      # erste Abfrage im finally
            status_result("local"),      # Nachbeobachtung 1
            status_result("local"),      # Nachbeobachtung 2
            (0.2, 0, "lokal ✓", ""),     # aktive Wiederbeauftragung
            status_result("local"),      # Nachweis
        ]
        with mock.patch.object(self.bench, "run_ncpin", side_effect=calls) as run:
            result = self.bench.roundtrip(self.runner, self.sample, 5.0)

        self.assertFalse(result["ok"])
        self.assertTrue(result["restore_verified"])
        commands = [call.args[1][0] for call in run.call_args_list]
        self.assertEqual(commands, ["status", "online"] + ["status"] * 4
                         + ["local", "status"])
        self.assertEqual(commands.count("online"), 1)

    def test_transient_unknown_keeps_the_settle_window_open(self):
        # read_status_state() meldet schon bei einem einzelnen Status-, Exit-
        # Code- oder JSON-Fehler "unknown". Das darf das Nachbeobachtungsfenster
        # nicht beenden: Sonst wird die Wiederherstellung verweigert, obwohl der
        # naechste Poll wieder einen sicheren Zustand liefert — und genau dort
        # zeigt sich die verspaetete Wirkung des gesendeten Befehls.
        self.settle_quickly()
        calls = [
            status_result("local"),      # Anfangszustand
            (0.2, 1, "", "Timeout"),     # online: --wait laeuft ab
            status_result("local"),      # Beobachtung direkt danach
            status_result("unknown"),    # erste Abfrage im finally: Lesefehler
            status_result("online"),     # Nachbeobachtung: Befehl wirkt doch
            (0.2, 0, "lokal ✓", ""),     # aktive Wiederherstellung
            status_result("local"),      # Nachweis
        ]
        with mock.patch.object(self.bench, "run_ncpin", side_effect=calls) as run:
            result = self.bench.roundtrip(self.runner, self.sample, 5.0)

        self.assertFalse(result["ok"])
        self.assertTrue(result["restore_verified"])
        self.assertNotIn("error", result)
        commands = [call.args[1][0] for call in run.call_args_list]
        self.assertEqual(commands, ["status", "online", "status", "status",
                                    "status", "local", "status"])

    def test_late_effect_after_wait_timeout_is_restored_not_declared_verified(self):
        # MAKE-Befehle sind fire-and-forget: Nach dem Wait-Timeout sieht die
        # erste Abfrage noch den Anfangszustand, der Client fuehrt den Befehl
        # aber kurz danach doch aus. Eine einzelne Abfrage wuerde hier
        # faelschlich "wiederhergestellt" melden und enden.
        self.settle_quickly()
        calls = [
            status_result("local"),      # Anfangszustand
            (0.2, 1, "", "Timeout"),     # online: --wait laeuft ab
            status_result("local"),      # Beobachtung direkt danach
            status_result("local"),      # erste Abfrage im finally
            status_result("online"),     # Nachbeobachtung: Befehl wirkt doch
            (0.2, 0, "local ✓", ""),     # aktive Wiederherstellung
            status_result("local"),
        ]
        with mock.patch.object(self.bench, "run_ncpin", side_effect=calls) as run:
            result = self.bench.roundtrip(self.runner, self.sample, 5.0)

        self.assertFalse(result["ok"])
        self.assertTrue(result["restore_verified"])
        commands = [call.args[1][0] for call in run.call_args_list]
        self.assertEqual(commands, ["status", "online", "status", "status",
                                    "status", "local", "status"])

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


class RenameTransportRoundtripTests(unittest.TestCase):
    """Roundtrip-Restore gegen simulierte Rename-Transport-Zustaende.

    run_ncpin ist gemockt; die Seiteneffekte auf echten Temp-Dateien bilden
    das Verhalten des Rename-Transports nach: Die Umbenennung passiert sofort,
    den Inhalt liefert bzw. entfernt erst spaeter der Client. Kein echter
    Server, kein echter Client.
    """

    def setUp(self):
        self.bench = load_bench()
        self.tmp = tempfile.TemporaryDirectory()
        self.logical = os.path.join(self.tmp.name, "fixture.bin")
        self.placeholder = self.logical + self.bench.SUFFIX
        self.verbs = []
        for attribute, value in (("SETTLE_POLLS", 0), ("SETTLE_PAUSE", 0)):
            patcher = mock.patch.object(self.bench, attribute, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def fs_status_result(self):
        """Abstrakter Zustand wie ihn ncpin vom Dateisystem lesen wuerde."""
        if os.path.lexists(self.placeholder):
            size = os.stat(self.placeholder).st_size
            state = "online" if size == 1 else "unknown"
        elif os.path.lexists(self.logical):
            state = "online" if os.stat(self.logical).st_size == 1 else "local"
        else:
            state = "unknown"
        return status_result(state)

    def make_fake_run(self, op_effects, on_status=None,
                      status_after_first=None):
        """Baut ein run_ncpin-Double.

        op_effects ist die erwartete Abfolge der MUTIERENDEN Aufrufe als Liste
        von (verb, seiteneffekt, rc); status-Aufrufe lesen den echten
        Temp-Dateizustand. Gibt ein Seiteneffekt eine Zahl zurueck, ersetzt sie
        den geplanten rc (so kann ein Double die Ablehnung der echten CLI
        nachbilden). ``on_status`` laeuft vor jedem Statusaufruf (simuliert
        den Client, der im Hintergrund weiterarbeitet). ``status_after_first``
        laesst jeden Statusaufruf nach dem ersten fest diesen Zustand melden.
        """
        status_calls = [0]

        def fake_run(runner, args):
            verb = args[0]
            self.verbs.append(verb)
            if verb == "status":
                status_calls[0] += 1
                if on_status is not None:
                    on_status(status_calls[0])
                if status_after_first is not None and status_calls[0] > 1:
                    return status_result(status_after_first)
                return self.fs_status_result()
            self.assertTrue(op_effects,
                            "unerwarteter Mutationsaufruf: %s" % verb)
            expected_verb, effect, rc = op_effects.pop(0)
            self.assertEqual(verb, expected_verb)
            override = effect()
            if override is not None:
                rc = override
            return (0.2, rc, "", "" if rc == 0 else "Timeout")
        return fake_run

    def cli_like_local(self):
        """Seiteneffekt, der ``ncpin local`` treu nachbildet.

        Die echte CLI hydriert nur einen 1-Byte-Platzhalter mit virtuellem
        Journal-Eintrag. Eine suffigierte Datei mit vollem Inhalt lehnt sie ab
        (Exit 1) und laesst sie unveraendert liegen.
        """
        if os.path.exists(self.placeholder):
            if os.stat(self.placeholder).st_size != 1:
                return 1
            os.rename(self.placeholder, self.logical)
            with open(self.logical, "wb") as handle:
                handle.write(b"voller inhalt")
        return 0

    def test_pending_download_stub_is_reverted_not_false_verified(self):
        # Wait-Timeout nach dem Download-Rename: Der 1-Byte-Stub liegt schon
        # suffixlos, meldet aber weiter "online". Der abstrakte Zustand passt
        # damit scheinbar zum Anfang — die Wiederherstellung muss den Pfad
        # pruefen und den ausstehenden Download erst abschliessen lassen,
        # dann wieder dehydrieren.
        with open(self.placeholder, "wb") as handle:
            handle.write(b" ")

        def start_download():
            os.rename(self.placeholder, self.logical)  # Stub bleibt 1 Byte

        def finish_download():
            with open(self.logical, "wb") as handle:
                handle.write(b"voller inhalt")

        def dehydrate():
            os.rename(self.logical, self.placeholder)
            with open(self.placeholder, "wb") as handle:
                handle.write(b" ")

        effects = [("local", start_download, 1),
                   ("local", finish_download, 0),
                   ("online", dehydrate, 0)]
        with mock.patch.object(self.bench, "run_ncpin",
                               side_effect=self.make_fake_run(effects)):
            result = self.bench.roundtrip(["runner"], self.logical, 5.0)

        self.assertFalse(result["ok"])  # der Hin-Uebergang selbst schlug fehl
        self.assertTrue(result["restore_verified"])
        self.assertEqual(effects, [])   # alle Wiederherstellungsschritte liefen
        self.assertTrue(os.path.exists(self.placeholder))
        self.assertFalse(os.path.exists(self.logical))
        self.assertEqual(self.verbs,
                         ["status", "local", "status", "status",
                          "local", "status", "online", "status", "status"])

    def test_successful_roundtrip_from_placeholder_skips_redundant_hydration(self):
        # Anfangs online-only, die Hydrierung gelingt vollstaendig: Der Pfad
        # ist danach suffixlos und der Zustand sicher "local". Fuer die
        # Wiederherstellung genuegt dann die Dehydrierung — ein zweiter
        # "local"-Aufruf waere ein wirkungsloser CLI-Start mit Statusabfrage.
        with open(self.placeholder, "wb") as handle:
            handle.write(b" ")

        def download():
            os.rename(self.placeholder, self.logical)
            with open(self.logical, "wb") as handle:
                handle.write(b"voller inhalt")

        def dehydrate():
            os.rename(self.logical, self.placeholder)
            with open(self.placeholder, "wb") as handle:
                handle.write(b" ")

        effects = [("local", download, 0), ("online", dehydrate, 0)]
        with mock.patch.object(self.bench, "run_ncpin",
                               side_effect=self.make_fake_run(effects)):
            result = self.bench.roundtrip(["runner"], self.logical, 5.0)

        self.assertTrue(result["ok"])
        self.assertTrue(result["restore_verified"])
        self.assertEqual(effects, [])
        self.assertTrue(os.path.exists(self.placeholder))
        self.assertFalse(os.path.exists(self.logical))
        self.assertEqual(self.verbs,
                         ["status", "local", "status", "status",
                          "online", "status", "status"])

    def test_pending_dehydration_waits_for_the_client_then_rehydrates(self):
        # Wait-Timeout nach dem Dehydrierungs-Rename: Die Suffixdatei traegt
        # noch den vollen Inhalt und meldet "unknown". ``ncpin local`` lehnt
        # diesen Zwischenstand ab (kein Platzhalter im Journal). Der Bench muss
        # deshalb warten, bis der Client die Dehydrierung abgeschlossen hat
        # (1-Byte-Platzhalter, "online"), und erst DANN per local zurueck.
        with open(self.logical, "wb") as handle:
            handle.write(b"voller inhalt")

        def dehydrate_rename_only():
            os.rename(self.logical, self.placeholder)  # Inhalt bleibt voll

        def client_finishes_dehydration_late(status_call):
            # Der Client kommt erst beim 5. Statusaufruf zum Zug.
            if status_call >= 5 and os.path.exists(self.placeholder):
                with open(self.placeholder, "wb") as handle:
                    handle.write(b" ")

        effects = [("online", dehydrate_rename_only, 1),
                   ("local", self.cli_like_local, 0)]
        with mock.patch.object(
                self.bench, "run_ncpin",
                side_effect=self.make_fake_run(
                    effects, on_status=client_finishes_dehydration_late)):
            result = self.bench.roundtrip(["runner"], self.logical, 5.0)

        self.assertFalse(result["ok"])
        self.assertTrue(result["restore_verified"], result)
        self.assertEqual(effects, [])
        self.assertFalse(os.path.exists(self.placeholder))
        with open(self.logical, "rb") as handle:
            self.assertEqual(handle.read(), b"voller inhalt")
        # status(Anfang), online, status(Ergebnis), status(Restore-Lesung),
        # dann Warten auf "online" (2 Polls), local, status, status.
        self.assertEqual(self.verbs,
                         ["status", "online", "status", "status",
                          "status", "status", "local", "status", "status"])
        waited = [s for s in result["steps"] if s.get("waited_only")]
        self.assertEqual(len(waited), 1)
        self.assertTrue(waited[0]["reached"])

    def test_pending_dehydration_never_finishing_is_not_reported_restored(self):
        # Bleibt die Suffixdatei mit vollem Inhalt liegen, gibt es keinen
        # sicheren Rueckweg: local darf nicht mit einer Datei beauftragt
        # werden, die die CLI ohnehin ablehnt, und die Wiederherstellung gilt
        # als NICHT erledigt (Fehler im Ergebnis), statt still als gruen.
        with open(self.logical, "wb") as handle:
            handle.write(b"voller inhalt")

        def dehydrate_rename_only():
            os.rename(self.logical, self.placeholder)

        effects = [("online", dehydrate_rename_only, 1)]
        with mock.patch.object(self.bench, "run_ncpin",
                               side_effect=self.make_fake_run(effects)):
            result = self.bench.roundtrip(["runner"], self.logical, 0.2)

        self.assertFalse(result["ok"])
        self.assertFalse(result["restore_verified"])
        self.assertIn("Dehydrierung nicht abgeschlossen", result["error"])
        self.assertNotIn("local", self.verbs)
        self.assertTrue(os.path.exists(self.placeholder))
        with open(self.placeholder, "rb") as handle:
            self.assertEqual(handle.read(), b"voller inhalt")

    def test_reassert_initial_state_detects_ondisk_mismatch_with_real_files(self):
        with open(self.logical, "wb") as handle:
            handle.write(b"voller inhalt")

        # Anfang: lokal. Hin-Befehl schlaegt fehl.
        # Im Restore wird 'local' ausgefuehrt, aber die Datei wurde z.B. suffigiert.
        def fail_online():
            pass

        def reassert_local_leaves_placeholder():
            # Simuliert, dass 'local' zwar rc=0 liefert, die Datei aber noch als Platzhalter liegt
            if os.path.exists(self.logical):
                os.rename(self.logical, self.placeholder)
                with open(self.placeholder, "wb") as h:
                    h.write(b" ")

        effects = [("online", fail_online, 1),
                   ("local", reassert_local_leaves_placeholder, 0)]
        with mock.patch.object(self.bench, "run_ncpin",
                               side_effect=self.make_fake_run(effects)):
            result = self.bench.roundtrip(["runner"], self.logical, 5.0)

        self.assertFalse(result["ok"])
        # Da initial_ondisk (logical) != final_ondisk (placeholder), darf restore_verified nicht True sein
        self.assertFalse(result["restore_verified"])

    def test_persistent_unknown_state_attempts_reassertion(self):
        # Nach dem fehlgeschlagenen Hin-Befehl meldet JEDER weitere Statusaufruf
        # "unknown" (z.B. JSON-/Exit-Code-Fehler der CLI). Der Bench muss den
        # Ausgangszustand 'local' trotzdem aktiv beauftragen, darf die
        # Wiederherstellung aber NICHT bestaetigen und setzt den Fehler.
        with open(self.logical, "wb") as handle:
            handle.write(b"voller inhalt")

        def fail_online():
            pass

        effects = [("online", fail_online, 1),
                   ("local", lambda: None, 0)]
        with mock.patch.object(
                self.bench, "run_ncpin",
                side_effect=self.make_fake_run(effects,
                                               status_after_first="unknown")):
            result = self.bench.roundtrip(["runner"], self.logical, 5.0)

        self.assertFalse(result["ok"])
        self.assertIn("local", self.verbs)
        self.assertFalse(result["restore_verified"])
        self.assertIn("unbekannt", result["error"])
        self.assertEqual(self.verbs,
                         ["status", "online", "status", "status",
                          "local", "status"])


class FixtureSocketIntegrationTests(unittest.TestCase):
    """Ende-zu-Ende: Bench-Subprozess mit /usr/bin/python3, env -i und
    Fixture-Socket — deckt Prozessstart, Handshake und Burst-Reader ohne
    echten Client ab (NCPIN_SOCKET ueberlebt den env -i-Schnitt).
    """

    def test_bench_runs_deterministically_against_fixture_socket(self):
        sys.path.insert(0, HERE)
        try:
            from test_ncpin import FakeNextcloudSocket
        finally:
            sys.path.remove(HERE)

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = os.path.join(tmp.name, "Nextcloud")
        os.mkdir(root)
        sample = os.path.join(root, "beispiel.txt")
        with open(sample, "w", encoding="utf-8") as handle:
            handle.write("inhalt")
        socket_path = os.path.join(tmp.name, "nc.sock")
        server = FakeNextcloudSocket(socket_path, [root]).start()
        self.addCleanup(server.close)

        env = {"PATH": "/usr/bin:/bin", "HOME": tmp.name,
               "NCPIN_SOCKET": socket_path}
        # Grosszuegige Schwelle: Hier zaehlt die deterministische Korrektheit
        # des Ende-zu-Ende-Pfads, nicht die enge Latenzgrenze des echten Gates.
        proc = subprocess.run(
            ["/usr/bin/python3", BENCH_PATH, "--json", "--runs", "1",
             "--threshold", "3.0", "--sample", sample],
            env=env, capture_output=True, text=True)

        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        report = json.loads(proc.stdout)
        self.assertTrue(report["gate_pass"])
        self.assertTrue(report["correctness_ok"])
        self.assertIn("NCPIN_SOCKET=", report["interpreter"])
        measurements = {m["name"]: m for m in report["measurements"]}
        self.assertEqual(measurements["status-file"]["state"], "local")
        self.assertGreaterEqual(measurements["list-folder"]["entries"], 1)


if __name__ == "__main__":
    unittest.main()
