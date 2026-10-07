#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ersetzt Installationsartefakte atomar und entfernt nur erneut geprüfte eigene Ziele.

Quelle und Ziel muessen im selben Verzeichnis/Dateisystem liegen. Existiert das
Ziel, tauscht renamex_np(RENAME_SWAP) beide Namen in einem Schritt; danach liegt
der alte Stand am Quellpfad und kann vom aufrufenden Installer entfernt werden.
Fehlt das Ziel, benennt renamex_np(RENAME_EXCL) exklusiv um: Taucht zwischen
Existenzpruefung und Rename doch noch ein fremdes Ziel auf, bricht der Kernel
mit EEXIST ab, statt es still zu ersetzen — der Installer sieht die Kollision.
"""

import ctypes
import os
import plistlib
import shutil
import signal
import sys
import tempfile


RENAME_SWAP = 0x00000002
RENAME_EXCL = 0x00000004


def _renamex(source, destination, flags):
    """renamex_np mit Fehlerbehandlung; wirft OSError bei Misserfolg."""
    libc = ctypes.CDLL(None, use_errno=True)
    renamex = libc.renamex_np
    renamex.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
    renamex.restype = ctypes.c_int
    rc = renamex(os.fsencode(source), os.fsencode(destination), flags)
    if rc != 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err), destination)


def atomic_replace(source, destination):
    """Ersetzt ziel durch quelle (per RENAME_SWAP wenn vorhanden, sonst RENAME_EXCL)."""
    if os.path.dirname(source) != os.path.dirname(destination):
        raise ValueError("Quelle und Ziel muessen im selben Verzeichnis liegen")
    if not os.path.lexists(source):
        raise FileNotFoundError(source)
    if not os.path.lexists(destination):
        # Exklusiv statt os.rename: ein spaet aufgetauchtes fremdes Ziel
        # darf nicht verloren gehen (EEXIST -> Abbruch, Installer meldet).
        _renamex(source, destination, RENAME_EXCL)
        return

    _renamex(source, destination, RENAME_SWAP)


def _owned_artifact(target, kind, owner):
    if kind == "link":
        return os.path.islink(target) and os.readlink(target) == owner
    if os.path.islink(target) or not os.path.isdir(target):
        return False
    try:
        with open(os.path.join(target, "Contents", "Info.plist"), "rb") as handle:
            return plistlib.load(handle).get("NCPINOwnerIdentifier") == owner
    except (OSError, ValueError, AttributeError):
        return False


def remove_owned(target, kind, owner):
    """Bindet ein Uninstall-Ziel vor der letzten Besitzprüfung an einen privaten Ort.

    Ein zwischen Prüfung und Verschieben ersetztes Ziel wird exklusiv
    zurückgelegt. Ist sein alter Name inzwischen belegt, bleibt es erhalten
    und der Fehler nennt den Wiederherstellungspfad.
    """
    if not os.path.lexists(target):
        return False
    original = os.lstat(target)
    if not _owned_artifact(target, kind, owner):
        return False
    holding = tempfile.mkdtemp(prefix=".ncpin-uninstall-", dir=os.path.dirname(target))
    held = os.path.join(holding, "artifact")
    try:
        _renamex(target, held, RENAME_EXCL)
        current = os.lstat(held)
        if (not os.path.samestat(original, current)
                or not _owned_artifact(held, kind, owner)):
            return False
        if kind == "link":
            os.unlink(held)
        else:
            shutil.rmtree(held)
        return True
    finally:
        if os.path.lexists(held):
            try:
                _renamex(held, target, RENAME_EXCL)
            except OSError as exc:
                raise RuntimeError("Uninstall-Ziel erhalten unter %s; Rücklegen nach %s fehlgeschlagen: %s"
                                   % (held, target, exc))
        os.rmdir(holding)


def main(argv):
    if len(argv) == 3 and argv[0] in ("--remove-tree", "--remove-link"):
        kind = "tree" if argv[0] == "--remove-tree" else "link"
        return 0 if remove_owned(argv[1], kind, argv[2]) else 3
    if len(argv) != 2:
        sys.stderr.write("Aufruf: atomic_replace.py <quelle> <ziel>\n")
        return 2
    atomic_replace(argv[0], argv[1])
    return 0


if __name__ == "__main__":
    def interrupted(signum, _frame):
        raise SystemExit(128 + signum)

    for signum in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(signum, interrupted)
    sys.exit(main(sys.argv[1:]))
