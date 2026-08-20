#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ersetzt ein Installationsartefakt auf macOS atomar.

Quelle und Ziel muessen im selben Verzeichnis/Dateisystem liegen. Existiert das
Ziel, tauscht renamex_np(RENAME_SWAP) beide Namen in einem Schritt; danach liegt
der alte Stand am Quellpfad und kann vom aufrufenden Installer entfernt werden.
Fehlt das Ziel, benennt renamex_np(RENAME_EXCL) exklusiv um: Taucht zwischen
Existenzpruefung und Rename doch noch ein fremdes Ziel auf, bricht der Kernel
mit EEXIST ab, statt es still zu ersetzen — der Installer sieht die Kollision.
"""

import ctypes
import os
import sys


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


def main(argv):
    if len(argv) != 2:
        sys.stderr.write("Aufruf: atomic_replace.py <quelle> <ziel>\n")
        return 2
    atomic_replace(argv[0], argv[1])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
