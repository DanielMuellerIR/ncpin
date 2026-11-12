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
import fcntl
import os
import plistlib
import shutil
import signal
import subprocess
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


def _directory_path(fd):
    return os.fsdecode(fcntl.fcntl(fd, 50, bytes(1024)).split(b"\0", 1)[0])


def _open_directory(path, create=False, before_create=None):
    """Öffnet jede Komponente ohne Symlinks, auch beim Anlegen des Stage-Ziels."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open("/", flags)
    try:
        for part in path.strip("/").split("/"):
            if not part:
                continue
            try:
                child = os.open(part, flags, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise
                if before_create:
                    before_create(os.path.join(_directory_path(fd), part))
                os.mkdir(part, dir_fd=fd)
                child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _physical_path(path):
    """Kernelpfad des vorhandenen Vorfahren, ergänzt um fehlende Komponenten.

    F_GETPATH vereinheitlicht auch Firmlinks, die realpath nicht auflöst.
    """
    probe = os.path.realpath(path)
    tail = []
    while not os.path.isdir(probe):
        parent, name = os.path.split(probe)
        if parent == probe:
            raise RuntimeError("Verzeichnispfad nicht prüfbar: %s" % path)
        tail.insert(0, name)
        probe = parent
    fd = _open_directory(probe)
    try:
        return os.path.join(_directory_path(fd), *tail)
    finally:
        os.close(fd)


def _contains(parent, child):
    return os.path.commonpath([parent, child]) == parent


def stage_artifacts(destination, protected, installed, sources):
    """Kopiert relativ zum fest geöffneten Stage-Verzeichnis.

    Der Arbeitsordner bindet auch ditto und rmtree an dieselbe Inode. Ein
    Austausch des ursprünglichen Pfads kann sie nicht in ein anderes Ziel
    umleiten. Vor jedem Kopieren, Austausch und Entfernen wird zusätzlich
    geprüft, ob das Verzeichnis verschoben oder ein Installationsziel ist.
    """
    destination = os.path.realpath(destination)
    sources = [os.path.abspath(source) for source in sources]
    names = [os.path.basename(source) for source in sources]

    def check_boundaries(current):
        if _contains(_physical_path(protected), current):
            raise RuntimeError("--stage-only darf nicht nach %s bauen" % current)
        for name in names:
            output = _physical_path(os.path.join(current, name))
            for target in installed:
                entry = _physical_path(os.path.dirname(target)) + "/" + os.path.basename(target)
                resolved = _physical_path(target)
                if any(_contains(a, b) or _contains(b, a)
                       for a, b in ((entry, output), (resolved, output))):
                    raise RuntimeError("--stage-only überschneidet sich mit Installationsziel %s" % target)

    check_boundaries(_physical_path(destination))
    fd = _open_directory(destination, create=True, before_create=check_boundaries)
    previous = os.open(".", os.O_RDONLY | os.O_DIRECTORY)
    original = _directory_path(fd)
    identity = os.fstat(fd)

    def validate():
        if _directory_path(fd) != original:
            raise RuntimeError("Stage-Verzeichnis wurde verschoben — Build abgebrochen")
        probe = _open_directory(destination)
        try:
            if not os.path.samestat(identity, os.fstat(probe)):
                raise RuntimeError("Stage-Verzeichnis wurde ersetzt — Build abgebrochen")
        finally:
            os.close(probe)
        check_boundaries(original)

    def remove_checked(path, expected):
        validate()
        if not os.path.lexists(path):
            return
        if expected is None or not os.path.samestat(expected, os.lstat(path)):
            raise RuntimeError("Zwischenstand wurde ersetzt; erhalten unter %s" %
                               os.path.join(_directory_path(fd), path))
        # Der öffentliche Stage-Name kann nach der Prüfung ausgetauscht werden.
        # Erst exklusiv in einen privaten Baum übernehmen und dort erneut prüfen.
        cleanup = tempfile.mkdtemp(prefix=".ncpin-cleanup-", dir=".")
        claimed = os.path.join(cleanup, "artifact")
        try:
            _renamex(path, claimed, RENAME_EXCL)
        except BaseException:
            os.rmdir(cleanup)
            raise
        if not os.path.samestat(expected, os.lstat(claimed)):
            raise RuntimeError("Zwischenstand wurde ersetzt; erhalten unter %s" %
                               os.path.join(_directory_path(fd), claimed))
        if os.path.islink(claimed) or not os.path.isdir(claimed):
            os.unlink(claimed)
        else:
            shutil.rmtree(claimed)
        os.rmdir(cleanup)

    try:
        os.fchdir(fd)
        for source, name in zip(sources, names):
            validate()
            holding = os.path.join(".", os.path.basename(tempfile.mkdtemp(prefix=".ncpin-build-", dir=".")))
            holding_identity = os.lstat(holding)
            artifact = os.path.join(holding, "artifact")
            pending = holding + "-artifact"
            new_identity = None
            try:
                # ditto schreibt nur in einen neuen privaten Baum, niemals in ein
                # vorhandenes Bundle oder einen nachträglich eingesetzten Symlink.
                validate()
                subprocess.run(["/usr/bin/ditto", source, artifact], check=True)
                validate()
                new_identity = os.lstat(artifact)
                _renamex(artifact, pending, RENAME_EXCL)
                remove_checked(holding, holding_identity)
                validate()
                target = os.path.join(".", name)
                old_identity = os.lstat(target) if os.path.lexists(target) else None
                atomic_replace(pending, target)
                remove_checked(pending, old_identity)
            except BaseException as exc:
                problems = []
                for path, expected in ((holding, holding_identity), (pending, new_identity)):
                    try:
                        if not os.path.lexists(path):
                            continue
                        # Nach dem Swap gehört der Zwischenstand nicht mehr zum
                        # Neubau. Bei Fehlern bleibt er für die Wiederherstellung.
                        if path == pending and (expected is None or not os.path.samestat(
                                expected, os.lstat(path))):
                            raise RuntimeError("Altstand erhalten unter %s" %
                                               os.path.join(_directory_path(fd), path))
                        remove_checked(path, expected)
                    except BaseException as cleanup_error:
                        problems.append("%s (Pfad: %s)" %
                                        (cleanup_error, os.path.join(_directory_path(fd), path)))
                if problems:
                    raise RuntimeError("%s; %s" % (exc, "; ".join(problems))) from exc
                raise

        validate()
    finally:
        os.fchdir(previous)
        os.close(previous)
        os.close(fd)


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
    if argv and argv[0] == "--stage":
        separator = argv.index("--")
        stage_artifacts(argv[1], argv[2], argv[3:separator], argv[separator + 1:])
        return 0
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
