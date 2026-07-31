#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ersetzt ein Installationsartefakt auf macOS atomar.

Quelle und Ziel muessen im selben Verzeichnis/Dateisystem liegen. Existiert das
Ziel, tauscht renamex_np(RENAME_SWAP) beide Namen in einem Schritt; danach liegt
der alte Stand am Quellpfad und kann vom aufrufenden Installer entfernt werden.
"""

import ctypes
import os
import sys


RENAME_SWAP = 0x00000002


def atomic_replace(source, destination):
    if os.path.dirname(source) != os.path.dirname(destination):
        raise ValueError("Quelle und Ziel muessen im selben Verzeichnis liegen")
    if not os.path.lexists(source):
        raise FileNotFoundError(source)
    if not os.path.lexists(destination):
        os.rename(source, destination)
        return False

    libc = ctypes.CDLL(None, use_errno=True)
    renamex = libc.renamex_np
    renamex.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
    renamex.restype = ctypes.c_int
    rc = renamex(os.fsencode(source), os.fsencode(destination), RENAME_SWAP)
    if rc != 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err), destination)
    return True


def main(argv):
    if len(argv) != 2:
        sys.stderr.write("Aufruf: atomic_replace.py <quelle> <ziel>\n")
        return 2
    atomic_replace(argv[0], argv[1])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
