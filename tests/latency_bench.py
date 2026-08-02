#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
latency_bench.py — ncpin-Latenz headless messen + Regressions-Gate.

Wozu (fuer Einsteiger):
  Der Drop auf eine Droplet-App fuehlte sich frueher traege an. Ursache war NICHT
  der Python-Start, sondern ein blockierendes Socket-recv in ncpin (~3s pro
  Aufruf). Dieser Benchmark misst den reinen *Overhead* eines ncpin-Aufrufs
  (Verbinden + Handshake + eine Zustands-Abfrage, OHNE Netzwerk) und schlaegt
  Alarm, falls er wieder ueber eine Schwelle klettert — damit kuenftige
  Aenderungen die Latenz nicht heimlich hochziehen.

Das Nicht-Offensichtliche (WICHTIG, sonst gruen-aber-falsch gemessen):
  Die GUI-Apps laufen via LaunchServices -> `do shell script` -> minimaler PATH
  -> Apples System-Python /usr/bin/python3 (3.9.6), NICHT das Homebrew-Python der
  Login-Shell. Darum misst dieser Bench standardmaessig GEGEN GENAU DIESEN
  Interpreter (`env -i PATH=/usr/bin:/bin /usr/bin/python3`). Nur so entspricht
  die Messung dem echten App-Pfad.

Voraussetzung:
  Laufender Nextcloud-Client mit Sync-Ordner; `ncpin doctor` muss gruen sein.
  Ohne das -> Exit 2 (Voraussetzung fehlt), KEIN Regressions-Fehler.

Aufruf (AI-Agent-/CI-freundlich):
  python3 tests/latency_bench.py                 # Overhead messen + Gate pruefen
  python3 tests/latency_bench.py --json          # maschinenlesbare Ausgabe
  python3 tests/latency_bench.py --threshold 1.0 # Gate-Schwelle (Sek Overhead)
  python3 tests/latency_bench.py --runs 7        # Wiederholungen je Szenario
  python3 tests/latency_bench.py --roundtrip --sample <pfad>
                                                 # zusaetzlich echten Hydrate/
                                                 #   Dehydrate-Zyklus messen (Netz!);
                                                 #   --sample (entbehrliches
                                                 #   Fixture) ist dafuer Pflicht
  python3 tests/latency_bench.py --sample <pfad> # eigene Beispiel-Datei
  python3 tests/latency_bench.py --python <bin> --no-clean-env  # Interpreter/Env
                                                 #   ueberschreiben (Default: 3.9.6)

Exit-Codes:
  0  alle gegateten Overhead-Messungen unter Schwelle (Gate gruen)
  1  Overhead ueber Schwelle ODER Korrektheits-Regression (Zustand falsch)
  2  Voraussetzung fehlt (kein Client/Socket, doctor rot, Sample nicht gefunden)
"""

import argparse
import json
import math
import os
import subprocess
import sys
import time

# Repo-Wurzel = Elternordner von tests/. So findet der Bench die CLI relativ,
# egal aus welchem Verzeichnis er gestartet wird (keine absoluten Pfade -> laeuft
# auf jedem Mac).
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
NCPIN = os.path.join(REPO, "ncpin")

# Der Interpreter + die Umgebung, die die GUI-Apps real treffen (siehe Modul-Doku).
GUI_PYTHON = "/usr/bin/python3"
GUI_CLEAN_ENV = ["env", "-i", "PATH=/usr/bin:/bin"]

SUFFIX = os.environ.get("NCPIN_SUFFIX", ".nextcloud")


# ---------------------------------------------------------------------------
# ncpin aufrufen + Zeit messen
# ---------------------------------------------------------------------------

def build_runner(python_bin, clean_env):
    """Baut die Aufruf-Vorsilbe (Liste) fuer einen ncpin-Aufruf.

    clean_env=True -> exakt der GUI-Pfad (env -i + System-Python). Explizit
    gesetzte ncpin-Fixture-Variablen (NCPIN_SOCKET/NCPIN_CONFIG/NCPIN_SUFFIX)
    ueberleben den env -i-Schnitt: Ohne das koennte der Bench nie
    deterministisch gegen einen Fake-Socket laufen, sondern hinge immer am
    Zustand eines echten Clients. Sind sie nicht gesetzt, bleibt die
    Messumgebung unveraendert der reale GUI-Pfad.
    clean_env=False -> der uebergebene Interpreter mit der aktuellen Umgebung.
    """
    if clean_env:
        prefix = list(GUI_CLEAN_ENV)
        for variable in ("NCPIN_SOCKET", "NCPIN_CONFIG", "NCPIN_SUFFIX"):
            value = os.environ.get(variable)
            if value:
                prefix.append("%s=%s" % (variable, value))
        return prefix + [python_bin, NCPIN]
    return [python_bin, NCPIN]


def run_ncpin(runner, args):
    """Ruft ncpin einmal auf. Gibt (dauer_sek, returncode, stdout, stderr).

    Misst die Wandzeit eines kompletten Prozess-Starts — also genau das, was der
    Nutzer als Latenz spuert (inkl. Interpreter-Start).
    """
    t0 = time.perf_counter()
    p = subprocess.run(runner + args, capture_output=True, text=True)
    dt = time.perf_counter() - t0
    return dt, p.returncode, p.stdout, p.stderr


def measure(runner, args, runs):
    """Fuehrt denselben Aufruf 'runs'-mal aus und liefert Statistik in ms.

    Min/Median/Max statt nur Mittelwert: der Median ist robust gegen einzelne
    Ausreisser (z.B. wenn das System gerade beschaeftigt ist).
    """
    if runs <= 0:
        raise ValueError("runs muss positiv sein")
    durations = []
    samples = []
    for _ in range(runs):
        dt, rc, out, err = run_ncpin(runner, args)
        durations.append(dt)
        samples.append({
            "elapsed_ms": round(dt * 1000, 1),
            "rc": rc,
            "stdout": out.strip(),
            "stderr": err.strip(),
        })
    sorted_durations = sorted(durations)
    n = len(sorted_durations)
    median = (sorted_durations[n // 2] if n % 2 else
              (sorted_durations[n // 2 - 1] + sorted_durations[n // 2]) / 2)
    return {
        "runs": runs,
        "min_ms": round(sorted_durations[0] * 1000, 1),
        "median_ms": round(median * 1000, 1),
        "max_ms": round(sorted_durations[-1] * 1000, 1),
        "median_s": round(median, 3),
        "samples": samples,
    }


def validate_measurement(name, measurement):
    """Prueft jede Wiederholung semantisch, getrennt von der Zeitstatistik."""
    samples = measurement.get("samples") or []
    if not samples:
        return False
    if name in ("status-file", "status-folder"):
        states = []
        valid = True
        # Ordner koennen bei gemischten Kindzustaenden legitim "unknown"
        # liefern. Eine einzelne Datei muss dagegen eindeutig aufloesbar sein.
        allowed_states = (("local", "online") if name == "status-file" else
                          ("local", "online", "unknown"))
        for sample in samples:
            state = parse_status_state(sample.get("stdout", ""))
            sample["state"] = state
            states.append(state)
            if sample.get("rc") != 0 or state not in allowed_states:
                valid = False
        measurement["states"] = states
        measurement["state"] = states[0] if len(set(states)) == 1 else "wechselnd"
        return valid
    if name == "list-folder":
        entry_counts = []
        valid = True
        for sample in samples:
            count = len([line for line in sample.get("stdout", "").splitlines() if line])
            sample["entries"] = count
            entry_counts.append(count)
            if sample.get("rc") != 0 or count == 0:
                valid = False
        measurement["entries_per_run"] = entry_counts
        measurement["entries"] = min(entry_counts)
        return valid
    return all(sample.get("rc") == 0 for sample in samples)


# ---------------------------------------------------------------------------
# Voraussetzungen + Beispiel-Datei
# ---------------------------------------------------------------------------

def check_doctor(runner):
    """Prueft per `ncpin doctor --json`, ob Transport + Client bereit sind.

    Gibt (ok, info_dict). Nutzt denselben Interpreter wie die Messung — so faellt
    auch ein interpreter-spezifisches Problem (z.B. 3.9.6) sofort auf.
    "backend_ok" deckt beide Transporte (Socket bis v33, Rename ab v34);
    der Fallback auf "socket_ok" laesst den Bench gegen ein aelteres ncpin laufen.
    """
    dt, rc, out, err = run_ncpin(runner, ["doctor", "--json"])
    try:
        info = json.loads(out)
    except (ValueError, json.JSONDecodeError):
        return False, {"error": "doctor lieferte kein JSON", "stderr": err.strip()}
    backend_ok = info.get("backend_ok", info.get("socket_ok"))
    ok = bool(backend_ok) and bool(info.get("client_responds"))
    return ok, info


def _resolve_existing(path):
    """Liefert die vorhandene reale oder Suffix-Variante eines logischen Pfads."""
    expanded = os.path.abspath(os.path.expanduser(path))
    for candidate in (expanded, expanded + SUFFIX,
                      expanded[:-len(SUFFIX)] if expanded.endswith(SUFFIX) else None):
        if candidate and os.path.lexists(candidate):
            return candidate
    return None


def _inside_roots(path, roots):
    candidate = os.path.realpath(path)
    for root in roots:
        canonical_root = os.path.realpath(os.path.abspath(os.path.expanduser(root)))
        try:
            if os.path.commonpath([candidate, canonical_root]) == canonical_root:
                return True
        except ValueError:
            continue
    return False


def _validated_sample(roots, candidate):
    """Validiert Existenz, Dateityp und reale Syncroot-Zugehoerigkeit."""
    existing = _resolve_existing(candidate)
    if not existing or not os.path.isfile(existing) or not _inside_roots(existing, roots):
        return None
    logical = os.path.abspath(os.path.expanduser(candidate))
    return logical[:-len(SUFFIX)] if logical.endswith(SUFFIX) else logical


def discover_sample(roots, override):
    """Findet eine Beispiel-DATEI im Sync-Ordner (fuer status/Roundtrip).

    Reihenfolge:
      1. --sample (explizit)
      2. der dokumentierte Beispiel-Platzhalter .../Beispiele/Nextcloud intro.mp4
      3. erste gefundene Datei (max. Tiefe 3) in einem registrierten Ordner
    Gibt einen logischen Pfad (ohne Suffix) zurueck oder None.
    """
    if override:
        return _validated_sample(roots, override)
    for r in roots:
        cand = os.path.join(r, "Beispiele", "Nextcloud intro.mp4")
        validated = _validated_sample(roots, cand)
        if validated:
            return validated
    for r in roots:
        base_depth = r.rstrip("/").count(os.sep)
        for dirpath, dirnames, filenames in os.walk(r):
            # Keine Verzeichnis-Symlinks als Suchraum akzeptieren. Einzelne
            # Dateikandidaten werden unten zusaetzlich per realpath geprueft.
            dirnames[:] = [name for name in dirnames
                           if not os.path.islink(os.path.join(dirpath, name))]
            if dirpath.count(os.sep) - base_depth > 3:
                # Nicht weiter in Unterverzeichnisse absteigen, aber Dateien
                # im aktuellen Verzeichnis noch beruecksichtigen.
                dirnames[:] = []
            for fn in filenames:
                if fn.startswith("."):
                    continue
                validated = _validated_sample(roots, os.path.join(dirpath, fn))
                if validated:
                    return validated
    return None


def discover_folder(roots, sample):
    """Waehlt einen ORDNER-Pfad fuer die rekursive Abdeckung.

    Bevorzugt den Ordner der Beispiel-Datei, sonst den ersten Sync-Ordner.
    """
    if sample:
        existing = _resolve_existing(sample)
        if existing:
            directory = os.path.realpath(os.path.dirname(existing))
            if os.path.isdir(directory) and _inside_roots(directory, roots):
                return directory
    for root in roots:
        canonical = os.path.realpath(os.path.abspath(os.path.expanduser(root)))
        if os.path.isdir(canonical):
            return canonical
    return None


def parse_status_state(stdout):
    """Liest den Zustand aus der menschenlesbaren status-Ausgabe.

    Format: "<label>  <pfad>" mit label in {lokal, online-only, unbekannt}.
    Ob ``unknown`` fuer das jeweilige Szenario gueltig ist, entscheidet danach
    ``validate_measurement`` (Ordner duerfen gemischte Kindzustaende haben).
    """
    line = stdout.strip().splitlines()[0] if stdout.strip() else ""
    first = line.split(None, 1)[0] if line else ""
    return {"lokal": "local", "online-only": "online", "unbekannt": "unknown"}.get(first, "?")


def read_status_state(runner, sample):
    """Liest einen einzelnen Zustand robust aus der JSON-CLI-Ausgabe."""
    dt, rc, out, err = run_ncpin(runner, ["status", sample, "--json"])
    try:
        payload = json.loads(out)
        state = payload[0].get("state") if len(payload) == 1 else "unknown"
    except (ValueError, TypeError, KeyError, IndexError):
        state = "unknown"
    if rc != 0 or state not in ("local", "online"):
        state = "unknown"
    return state, {"elapsed_ms": round(dt * 1000, 1), "rc": rc,
                   "state": state, "stderr": err.strip()}


# ---------------------------------------------------------------------------
# Roundtrip (optional, mit Netzwerk)
# ---------------------------------------------------------------------------

def _ondisk_representation(sample):
    """Der tatsaechlich vorhandene On-Disk-Pfad des Samples (oder None).

    Beim Rename-Transport unterscheidet erst der Pfadname (Suffix vorhanden
    oder nicht) einen AUSSTEHENDEN Uebergang vom abstrakten Zustand: Ein
    frisch fuer den Download umbenannter 1-Byte-Stub meldet weiterhin
    "online", liegt aber schon unter dem suffixlosen Namen — der abstrakte
    Zustand allein wuerde die Wiederherstellung faelschlich fuer erledigt
    erklaeren, obwohl der Hydrierungswunsch weiterlaeuft.
    """
    return _resolve_existing(sample)


def _revert_pending_transition(runner, sample, timeout, initial_state,
                               initial_ondisk, result):
    """Nimmt einen ausstehenden Rename-Uebergang transportgerecht zurueck.

    Der Plan ergibt sich aus dem On-Disk-Pfad, nicht aus dem abstrakten
    Zustand:
      - Anfangs online-only, jetzt suffixlos: Der Downloadwunsch steht. Ein
        1-Byte-Stub laesst sich nicht direkt zurueckdrehen (ncpin sieht dort
        "nichts zu tun") — also erst fertig hydrieren lassen ("local"), dann
        wieder dehydrieren ("online").
      - Anfangs lokal, jetzt suffigiert: Die Rueck-Umbenennung ("local")
        genuegt; der Inhalt liegt noch (oder wieder) vor.
    Gibt True nur zurueck, wenn am Ende Zustand UND Pfad dem Anfang
    entsprechen.
    """
    if initial_ondisk.endswith(SUFFIX):
        plan = ["local", "online"]
    else:
        plan = ["local"]
    for step_target in plan:
        dt, rc, out, err = run_ncpin(
            runner, [step_target, sample, "--wait", "--timeout", str(timeout)])
        observed, _probe = read_status_state(runner, sample)
        result["steps"].append({
            "target": step_target,
            "elapsed_ms": round(dt * 1000, 1),
            "reached": rc == 0 and observed == step_target,
            "rc": rc,
            "stdout": out.strip(),
            "stderr": err.strip(),
            "observed_state": observed,
            "restoration": True,
        })
    final_state, final_probe = read_status_state(runner, sample)
    result["final_probe"] = final_probe
    result["final_ondisk"] = _ondisk_representation(sample)
    return (final_state == initial_state
            and result["final_ondisk"] == initial_ondisk)


def roundtrip(runner, sample, timeout):
    """Wechselt ein explizites Fixture und restauriert den gelesenen Zustand."""
    initial_state, initial_probe = read_status_state(runner, sample)
    initial_ondisk = _ondisk_representation(sample)
    result = {
        "ok": False,
        "initial_state": initial_state,
        "initial_probe": initial_probe,
        "initial_ondisk": initial_ondisk,
        "steps": [],
        "restore_verified": False,
    }
    if initial_state not in ("local", "online"):
        result["error"] = "Ausgangszustand nicht sicher ermittelbar; nichts veraendert."
        return result

    target = "online" if initial_state == "local" else "local"
    transition_ok = False
    try:
        dt, rc, out, err = run_ncpin(
            runner, [target, sample, "--wait", "--timeout", str(timeout)])
        observed, _probe = read_status_state(runner, sample)
        transition_ok = rc == 0 and observed == target
        result["steps"].append({
            "target": target,
            "elapsed_ms": round(dt * 1000, 1),
            "reached": transition_ok,
            "rc": rc,
            "stdout": out.strip(),
            "stderr": err.strip(),
            "observed_state": observed,
        })
    finally:
        # Nie blind die Gegenoperation ausfuehren: Zustand im finally erneut
        # lesen. Wiederhergestellt heisst: abstrakter Zustand UND On-Disk-Pfad
        # entsprechen dem Anfang (siehe _ondisk_representation) — sonst wird
        # ein sicher beobachteter Uebergang aktiv zurueckgenommen und danach
        # erneut verifiziert.
        current_state, restore_probe = read_status_state(runner, sample)
        current_ondisk = _ondisk_representation(sample)
        result["restore_probe"] = restore_probe
        result["restore_ondisk"] = current_ondisk
        path_stable = (initial_ondisk is None
                       or current_ondisk == initial_ondisk)
        if current_state == initial_state and path_stable:
            result["restore_verified"] = True
        elif (initial_ondisk is not None and current_ondisk is not None
                and current_ondisk != initial_ondisk):
            # Der Pfadname weicht ab -> ein Rename-Uebergang steht noch aus,
            # auch wenn der abstrakte Zustand scheinbar schon wieder passt.
            result["restore_verified"] = _revert_pending_transition(
                runner, sample, timeout, initial_state, initial_ondisk, result)
        elif current_state in ("local", "online"):
            dt, rc, out, err = run_ncpin(
                runner, [initial_state, sample, "--wait", "--timeout", str(timeout)])
            final_state, final_probe = read_status_state(runner, sample)
            final_ondisk = _ondisk_representation(sample)
            restored = (rc == 0 and final_state == initial_state
                        and (initial_ondisk is None
                             or final_ondisk == initial_ondisk))
            result["steps"].append({
                "target": initial_state,
                "elapsed_ms": round(dt * 1000, 1),
                "reached": restored,
                "rc": rc,
                "stdout": out.strip(),
                "stderr": err.strip(),
                "observed_state": final_state,
                "restoration": True,
            })
            result["final_probe"] = final_probe
            result["final_ondisk"] = final_ondisk
            result["restore_verified"] = restored
        else:
            result["error"] = "Zustand nach Roundtrip unbekannt; Restore nicht blind ausgefuehrt."
    result["ok"] = transition_ok and result["restore_verified"]
    return result


def positive_int(value):
    """argparse-Typ fuer strikt positive Ganzzahlen."""
    try:
        parsed = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("muss eine positive Ganzzahl sein")
    if parsed <= 0:
        raise argparse.ArgumentTypeError("muss groesser als 0 sein")
    return parsed


def positive_finite(value):
    """argparse-Typ fuer strikt positive, endliche Sekundenwerte."""
    try:
        parsed = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("muss eine positive Zahl sein")
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("muss endlich und groesser als 0 sein")
    return parsed


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv):
    ap = argparse.ArgumentParser(
        prog="latency_bench.py",
        description="ncpin-Latenz messen + Regressions-Gate (misst gegen das "
                    "System-Python 3.9.6, das die GUI-Apps real nutzen).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--json", action="store_true", help="maschinenlesbare Ausgabe")
    ap.add_argument("--threshold", type=positive_finite, default=1.0, metavar="SEK",
                    help="Gate: Overhead-Median muss darunter liegen (Standard 1.0s)")
    ap.add_argument("--runs", type=positive_int, default=5, help="Wiederholungen je Szenario (Standard 5)")
    ap.add_argument("--roundtrip", action="store_true",
                    help="zusaetzlich echten Hydrate/Dehydrate-Zyklus messen (braucht Netz)")
    ap.add_argument("--timeout", type=positive_finite, default=30.0, help="--wait-Timeout fuer --roundtrip")
    ap.add_argument("--sample", help="Beispiel-Datei; fuer --roundtrip entbehrliches Fixture (Pflicht)")
    ap.add_argument("--python", default=GUI_PYTHON,
                    help="Interpreter fuer ncpin (Standard: %s)" % GUI_PYTHON)
    ap.add_argument("--no-clean-env", action="store_true",
                    help="aktuelle Umgebung statt 'env -i PATH=/usr/bin:/bin' nutzen")
    args = ap.parse_args(argv)
    if args.roundtrip and not args.sample:
        ap.error("--roundtrip erfordert --sample mit einem entbehrlichen Fixture")

    if not os.path.exists(NCPIN):
        sys.stderr.write("Fehler: ncpin nicht gefunden unter %s\n" % NCPIN)
        return 2

    runner = build_runner(args.python, clean_env=not args.no_clean_env)

    report = {
        "interpreter": " ".join(runner[:-1]),
        "threshold_s": args.threshold,
        "runs": args.runs,
        "measurements": [],
        "roundtrip": None,
        "gate_pass": False,
    }

    # 1) Voraussetzung: doctor gruen?
    ok, info = check_doctor(runner)
    report["doctor"] = info
    if not ok:
        report["error"] = "Voraussetzung fehlt: Nextcloud-Client/Socket nicht bereit (doctor rot)."
        _emit(report, args.json)
        return 2

    roots = info.get("registered_folders") or []
    sample = discover_sample(roots, args.sample)
    folder = discover_folder(roots, sample)
    if not sample:
        report["error"] = "Voraussetzung fehlt: keine Beispiel-Datei im Sync-Ordner gefunden."
        _emit(report, args.json)
        return 2

    report["sample"] = sample
    report["folder"] = folder

    # 2) Overhead-Szenarien (kein Netzwerk) — das sind die gegateten Messungen.
    scenarios = [
        ("status-file", ["status", sample], True),
        ("status-folder", ["status", folder], True),
        # 'list' speist das Auswahlmenue der Toolbar-App -> dessen Drop->fertig
        # haengt an genau diesem Overhead, also mitmessen + gaten.
        ("list-folder", ["list", folder], True),
    ]

    gate_pass = True
    correctness_ok = True
    for name, cmd, gated in scenarios:
        m = measure(runner, cmd, args.runs)
        m["name"] = name
        m["cmd"] = "ncpin " + " ".join(cmd[:1])  # nur das Verb, keine Pfade ins JSON
        m["gated"] = gated
        correct = validate_measurement(name, m)
        m["correct"] = correct
        if not correct:
            correctness_ok = False
        under = m["median_s"] < args.threshold
        m["pass"] = under and correct
        if gated and not m["pass"]:
            gate_pass = False
        report["measurements"].append(m)

    # 3) Optional: echter Roundtrip (beide Richtungen, netzabhaengig, nicht gegated).
    if args.roundtrip:
        rt = roundtrip(runner, sample, args.timeout)
        report["roundtrip"] = rt
        if not rt["ok"]:
            correctness_ok = False

    report["correctness_ok"] = correctness_ok
    report["gate_pass"] = gate_pass and correctness_ok
    _emit(report, args.json)

    if not correctness_ok:
        return 1
    return 0 if gate_pass else 1


def _emit(report, as_json):
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    print("ncpin Latenz-Benchmark")
    print("  Interpreter:  %s" % report.get("interpreter"))
    print("  Schwelle:     %.2fs Overhead" % report["threshold_s"])
    if "error" in report:
        print("  FEHLER:       %s" % report["error"])
        return
    print("  Sample:       %s" % report.get("sample"))
    print("  Ordner:       %s" % report.get("folder"))
    print("  Sync-Ordner:  %s" % ", ".join(report["doctor"].get("registered_folders", [])))
    print("")
    for m in report["measurements"]:
        mark = "OK " if m["pass"] else "!! "
        gate = " [Gate]" if m["gated"] else ""
        extra = ""
        if "state" in m:
            extra = "  Zustand=%s" % m["state"]
        elif "entries" in m:
            extra = "  Eintraege=%d" % m["entries"]
        print("  %s%-14s median=%6.1fms  min=%6.1fms  max=%6.1fms%s%s"
              % (mark, m["name"], m["median_ms"], m["min_ms"], m["max_ms"], gate, extra))
    rt = report.get("roundtrip")
    if rt:
        print("")
        print("  Roundtrip (netzabhaengig, nicht gegated): %s" % ("OK" if rt["ok"] else "FEHLER"))
        for s in rt["steps"]:
            print("    -> %-7s %8.1fms  %s"
                  % (s["target"], s["elapsed_ms"], "✓" if s["reached"] else "✗"))
    print("")
    verdict = "GRUEN" if report["gate_pass"] else "ROT"
    print("  Gate: %s" % verdict)


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        sys.exit(130)
