#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Isolierte Tests fuer den macOS-Installer.

Alle Zielpfade liegen in einem temporaeren HOME. Die Tests verwenden niemals
/Applications, den Schluesselbund oder ein echtes Notary-Profil.
"""

import importlib.util
import os
import plistlib
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class InstallerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ncpin-installer-")
        self.root = self.temp.name
        # Leerzeichen und Apostroph decken beide kritischen Quoting-Faelle ab.
        self.copy = os.path.join(self.root, "Kopie mit Leerraum und O'Brien")
        os.makedirs(self.copy)
        for filename in ("install.sh", "ncpin"):
            shutil.copy2(os.path.join(REPO, filename), self.copy)
        for directory in ("apps", "quickactions", "tools"):
            shutil.copytree(os.path.join(REPO, directory),
                            os.path.join(self.copy, directory))

        self.home = os.path.join(self.root, "home")
        self.apps = os.path.join(self.root, "targets", "apps")
        self.services = os.path.join(self.root, "targets", "services")
        self.links = os.path.join(self.root, "targets", "bin")
        self.tmpdir = os.path.join(self.root, "tmp")
        for path in (self.home, self.apps, self.services, self.links,
                     self.tmpdir):
            os.makedirs(path)

        self.env = os.environ.copy()
        self.env.update({
            "HOME": self.home,
            "TMPDIR": self.tmpdir,
            "PATH": "/usr/bin:/bin",
            "NCPIN_APPS_DIR": self.apps,
            "NCPIN_SERVICES_DIR": self.services,
            "NCPIN_LINK_DIR": self.links,
            "NCPIN_SKIP_REGISTRATION": "1",
            # Explizites Leerzeichen: kein security-/Keychain-Zugriff.
            "NCPIN_SIGN_ID": "",
            "NCPIN_NOTARIZE": "0",
        })

    def tearDown(self):
        self.temp.cleanup()

    def run_installer(self, *arguments, **kwargs):
        env = kwargs.pop("env", self.env)
        self.assertFalse(kwargs)
        return subprocess.run(
            ["/bin/zsh", os.path.join(self.copy, "install.sh")] +
            list(arguments),
            cwd=self.copy,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def app(self, name="Lokal halten.app"):
        return os.path.join(self.apps, name)

    def workflow(self, name="Lokal halten (Nextcloud).workflow"):
        return os.path.join(self.services, name)

    @staticmethod
    def plist(path):
        with open(os.path.join(path, "Contents", "Info.plist"), "rb") as fh:
            return plistlib.load(fh)

    def install_ok(self):
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def fake_command(self, directory, name, exit_code):
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("#!/bin/sh\nexit %d\n" % exit_code)
        os.chmod(path, 0o755)

    def release_env(self, fakebin):
        env = self.env.copy()
        env.update({
            "PATH": fakebin + ":/usr/bin:/bin",
            "NCPIN_SIGN_ID": "Developer ID Application: Test",
            "NCPIN_NOTARIZE": "1",
        })
        return env

    def assert_old_app_preserved(self):
        self.assertTrue(os.path.exists(os.path.join(self.app(), "sentinel")))

    def patched_installer(self):
        """Kopie von install.sh, deren geschuetztes Ziel das Testverzeichnis ist.

        Die Regel "nur notarisierte Bundles" haengt an der Konstanten
        /Applications. Ein Test darf dort niemals hinschreiben, auch nicht
        versehentlich bei kaputtem Gate. Deshalb wird genau dieses eine
        case-Muster auf das temporaere Zielverzeichnis umgebogen; der gepruefte
        Code drumherum bleibt der echte. Schlaegt die Ersetzung fehl, faellt der
        Test auf — der Gate-Code kann also nicht unbemerkt verschwinden.
        """
        original = os.path.join(self.copy, "install.sh")
        with open(original, encoding="utf-8") as handle:
            text = handle.read()
        needle = "\t\t/Applications|/Applications/*) return 0 ;;\n"
        self.assertIn(needle, text)
        protected = os.path.realpath(self.apps)
        patched = text.replace(
            needle, "\t\t%s|%s/*) return 0 ;;\n" % (protected, protected))
        target = os.path.join(self.copy, "install-testziel.sh")
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(patched)
        os.chmod(target, 0o755)
        return target

    def run_patched_installer(self, env):
        return subprocess.run(
            ["/bin/zsh", self.patched_installer()],
            cwd=self.copy,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def test_install_builds_owned_signed_artifacts_with_bundle_relative_cli(self):
        self.install_ok()

        self.assertEqual(
            self.plist(self.app())["NCPINOwnerIdentifier"],
            "com.ethermac.ncpin.local",
        )
        self.assertEqual(
            self.plist(self.workflow())["NCPINOwnerIdentifier"],
            "com.ethermac.ncpin.quickaction.local",
        )
        self.assertEqual(
            os.readlink(os.path.join(self.links, "ncpin")),
            os.path.realpath(os.path.join(self.copy, "ncpin")),
        )
        verify = subprocess.run(
            ["/usr/bin/codesign", "--verify", "--strict", "--deep",
             self.app()],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(verify.returncode, 0, verify.stderr)

        decompiled = subprocess.run(
            ["/usr/bin/osadecompile", self.app()],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(decompiled.returncode, 0, decompiled.stderr)
        # Kein absoluter CLI-Pfad des Build-Macs mehr im Droplet: die App loest
        # ihre eingebettete Kopie ueber das eigene Bundle auf.
        self.assertNotIn(os.path.realpath(os.path.join(self.copy, "ncpin")),
                         decompiled.stdout)
        self.assertIn("path to me", decompiled.stdout)
        self.assertIn("/Contents/Resources/ncpin", decompiled.stdout)
        self.assertIn("/usr/bin/python3 ", decompiled.stdout)
        self.assertNotIn("/tmp/ncpin-app.log", decompiled.stdout)
        self.assertNotIn("logmsg", decompiled.stdout)
        self.assertIn("Finder-Zugriff wurde verweigert", decompiled.stdout)
        self.assertIn("errNum is -1743", decompiled.stdout)
        self.assertIn("errNum is -1712", decompiled.stdout)
        self.assertIn("errNum is -1728", decompiled.stdout)
        self.assertIn('selectionStatus is "empty"', decompiled.stdout)

    def test_quick_actions_call_the_cli_inside_the_installed_app(self):
        """Die Quick Actions duerfen nicht den Repo-Pfad des Build-Macs rufen.

        Ein .workflow-Bundle kann seinen eigenen Ort zur Laufzeit nicht
        ermitteln — Automator fuehrt nur ein Shell-Skript aus —, es braucht also
        einen absoluten Pfad. Bis 2026-08-04 stand dort der Pfad ins Repo:
        Repo verschoben oder geloescht, Quick Action tot. Jetzt zeigt er in die
        installierte App, deren Ort feststeht.
        """
        self.install_ok()
        im_repo = os.path.realpath(os.path.join(self.copy, "ncpin"))

        for workflow, app in (
            ("Lokal halten (Nextcloud).workflow", "Lokal halten.app"),
            ("Speicher freigeben (Nextcloud).workflow", "Speicher freigeben.app"),
        ):
            with self.subTest(workflow=workflow):
                dokument = os.path.join(self.workflow(workflow), "Contents",
                                        "document.wflow")
                with open(dokument, "rb") as fh:
                    inhalt = plistlib.load(fh)
                text = str(inhalt)

                erwartet = os.path.join(self.app(app), "Contents", "Resources",
                                        "ncpin")
                self.assertIn(erwartet, text)
                self.assertNotIn(im_repo, text)
                # Und die verstaendliche Meldung, falls die App fehlt.
                self.assertIn("Die zugehoerige App fehlt", text)

                # Der Pfad muss auch wirklich auf eine ausfuehrbare Datei
                # zeigen — sonst waere der Test gruen und die Aktion trotzdem
                # tot.
                self.assertTrue(os.access(erwartet, os.X_OK), erwartet)

    def test_app_bundle_carries_runnable_relocatable_cli(self):
        # Die CLI liegt als Kopie im Bundle und wird bundle-relativ aufgerufen.
        # Beweis: Bundle an einen anderen Ort verschieben und die Kopie dort
        # ausfuehren — genau das macht ein aus dem DMG gezogenes Droplet.
        self.install_ok()
        with open(os.path.join(self.copy, "ncpin"), "rb") as fh:
            original = fh.read()

        for name in ("Lokal halten.app", "Speicher freigeben.app"):
            with self.subTest(app=name):
                embedded = os.path.join(self.app(name), "Contents",
                                        "Resources", "ncpin")
                self.assertTrue(os.path.isfile(embedded))
                self.assertFalse(os.path.islink(embedded))
                self.assertTrue(os.stat(embedded).st_mode & 0o111)
                with open(embedded, "rb") as fh:
                    self.assertEqual(fh.read(), original)

        moved = os.path.join(self.root, "woanders", "Lokal halten.app")
        os.makedirs(os.path.dirname(moved))
        shutil.move(self.app(), moved)
        version = subprocess.run(
            ["/usr/bin/python3",
             os.path.join(moved, "Contents", "Resources", "ncpin"),
             "--version"],
            env={"PATH": "/usr/bin:/bin"},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(version.returncode, 0, version.stderr)
        self.assertTrue(version.stdout.startswith("ncpin "), version.stdout)

    def test_embedded_cli_is_sealed_by_the_signature(self):
        # Die CLI-Kopie muss VOR dem Signieren im Bundle liegen, sonst waere sie
        # nicht versiegelt und liesse sich nachtraeglich austauschen.
        self.install_ok()
        embedded = os.path.join(self.app(), "Contents", "Resources", "ncpin")
        with open(embedded, "a", encoding="utf-8") as fh:
            fh.write("\n# nachtraeglich veraendert\n")

        verify = subprocess.run(
            ["/usr/bin/codesign", "--verify", "--strict", "--deep", self.app()],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertNotEqual(verify.returncode, 0, verify.stdout)

    def test_foreign_collisions_abort_without_force_and_force_replaces(self):
        foreign = self.app()
        os.makedirs(foreign)
        marker = os.path.join(foreign, "foreign-data")
        with open(marker, "w", encoding="utf-8") as handle:
            handle.write("behalten")

        refused = self.run_installer()
        self.assertNotEqual(refused.returncode, 0)
        self.assertTrue(os.path.exists(marker))
        self.assertIn("Kollision", refused.stderr)

        forced = self.run_installer("--force")
        self.assertEqual(forced.returncode, 0, forced.stderr + forced.stdout)
        self.assertFalse(os.path.exists(marker))
        self.assertEqual(
            self.plist(foreign)["NCPINOwnerIdentifier"],
            "com.ethermac.ncpin.local",
        )

    def test_collision_created_during_build_is_rechecked_and_preserved(self):
        fakebin = os.path.join(self.root, "fake-race")
        os.makedirs(fakebin)
        wrapper = os.path.join(fakebin, "codesign")
        with open(wrapper, "w", encoding="utf-8") as handle:
            handle.write(
                "#!/bin/sh\n"
                "if [ ! -e \"$NCPIN_TEST_RACE_TARGET\" ]; then\n"
                "  mkdir -p \"$NCPIN_TEST_RACE_TARGET\"\n"
                "  printf fremd > \"$NCPIN_TEST_RACE_TARGET/foreign-data\"\n"
                "fi\n"
                "exec /usr/bin/codesign \"$@\"\n"
            )
        os.chmod(wrapper, 0o755)
        env = self.env.copy()
        env["PATH"] = fakebin + ":/usr/bin:/bin"
        env["NCPIN_TEST_RACE_TARGET"] = self.app()

        result = self.run_installer(env=env)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Kollision", result.stderr)
        self.assertTrue(os.path.exists(
            os.path.join(self.app(), "foreign-data")))

    def test_uninstall_removes_owned_but_preserves_foreign_artifacts(self):
        self.install_ok()
        own_online = self.app("Speicher freigeben.app")
        own_workflow = self.workflow("Speicher freigeben (Nextcloud).workflow")
        foreign_app = self.app()
        foreign_workflow = self.workflow()
        foreign_link = os.path.join(self.links, "ncpin")

        shutil.rmtree(foreign_app)
        shutil.rmtree(foreign_workflow)
        os.unlink(foreign_link)
        os.makedirs(foreign_app)
        os.makedirs(foreign_workflow)
        os.symlink("/bin/echo", foreign_link)

        result = self.run_installer("--uninstall")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertTrue(os.path.isdir(foreign_app))
        self.assertTrue(os.path.isdir(foreign_workflow))
        self.assertEqual(os.readlink(foreign_link), "/bin/echo")
        self.assertFalse(os.path.exists(own_online))
        self.assertFalse(os.path.exists(own_workflow))

    def test_signature_failure_is_fatal_and_preserves_installed_app(self):
        self.install_ok()
        open(os.path.join(self.app(), "sentinel"), "w").close()
        fakebin = os.path.join(self.root, "fake-sign")
        self.fake_command(fakebin, "codesign", 71)

        env = self.release_env(fakebin)
        env["NCPIN_NOTARIZE"] = "0"
        result = self.run_installer(env=env)
        self.assertEqual(result.returncode, 71, result.stderr + result.stdout)
        self.assert_old_app_preserved()

    def test_notary_failure_is_fatal_and_preserves_installed_app(self):
        self.install_ok()
        open(os.path.join(self.app(), "sentinel"), "w").close()
        fakebin = os.path.join(self.root, "fake-notary")
        self.fake_command(fakebin, "codesign", 0)
        self.fake_command(fakebin, "xcrun", 72)
        self.fake_command(fakebin, "spctl", 0)

        result = self.run_installer(env=self.release_env(fakebin))
        self.assertEqual(result.returncode, 72, result.stderr + result.stdout)
        self.assert_old_app_preserved()

    def test_gatekeeper_failure_is_fatal_and_preserves_installed_app(self):
        self.install_ok()
        open(os.path.join(self.app(), "sentinel"), "w").close()
        fakebin = os.path.join(self.root, "fake-gatekeeper")
        self.fake_command(fakebin, "codesign", 0)
        self.fake_command(fakebin, "xcrun", 0)
        self.fake_command(fakebin, "spctl", 73)

        result = self.run_installer(env=self.release_env(fakebin))
        self.assertEqual(result.returncode, 73, result.stderr + result.stdout)
        self.assert_old_app_preserved()

    def test_protected_target_refuses_adhoc_build_before_building(self):
        # Ohne Developer-ID darf nichts ins geschuetzte Ziel — und zwar bevor
        # ueberhaupt gebaut wird. Der vorher installierte Stand bleibt liegen.
        self.install_ok()
        open(os.path.join(self.app(), "sentinel"), "w").close()

        result = self.run_patched_installer(self.env)

        self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
        self.assertIn("Kein Developer-ID-Zertifikat", result.stderr)
        self.assertIn("ausdruecklich gewaehltes anderes Ziel", result.stderr)
        self.assert_old_app_preserved()

    def test_protected_target_refuses_signed_but_unnotarized_build(self):
        # NCPIN_NOTARIZE=0 ist eine bewusste Entscheidung gegen das Ticket —
        # damit ist das geschuetzte Ziel ebenfalls gesperrt.
        self.install_ok()
        open(os.path.join(self.app(), "sentinel"), "w").close()
        env = self.env.copy()
        env["NCPIN_SIGN_ID"] = "Developer ID Application: Test"
        env["NCPIN_NOTARIZE"] = "0"

        result = self.run_patched_installer(env)

        self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
        self.assertIn("NCPIN_NOTARIZE=0", result.stderr)
        self.assert_old_app_preserved()

    def test_protected_target_refuses_bundle_without_stapled_ticket(self):
        # Zweite Schranke: Die Absicht stimmt (Zertifikat da, Notarisierung
        # gewollt), aber am fertigen Bundle haengt kein Ticket. Auch dann wird
        # nicht installiert.
        self.install_ok()
        open(os.path.join(self.app(), "sentinel"), "w").close()
        fakebin = os.path.join(self.root, "fake-staple")
        self.fake_command(fakebin, "codesign", 0)
        self.fake_command(fakebin, "spctl", 0)
        counter = os.path.join(self.root, "staple-calls")
        wrapper = os.path.join(fakebin, "xcrun")
        with open(wrapper, "w", encoding="utf-8") as handle:
            # Die beiden Pruefungen innerhalb der Notarisierung sollen gelingen;
            # erst die Schranke unmittelbar vor der Installation (dritter Aufruf)
            # findet kein angeheftetes Ticket.
            handle.write(
                "#!/bin/sh\n"
                'if [ "$1" = "stapler" ] && [ "$2" = "validate" ]; then\n'
                '  calls=$(cat "%s" 2>/dev/null || echo 0)\n'
                '  calls=$((calls + 1))\n'
                '  echo "$calls" > "%s"\n'
                '  [ "$calls" -le 2 ] || exit 1\n'
                "fi\n"
                "exit 0\n" % (counter, counter))
        os.chmod(wrapper, 0o755)

        result = self.run_patched_installer(self.release_env(fakebin))

        self.assertEqual(result.returncode, 1, result.stderr + result.stdout)
        self.assertIn("kein angeheftetes Notary-Ticket", result.stderr)
        self.assert_old_app_preserved()

    def test_unprotected_target_still_accepts_adhoc_build(self):
        # Gegenprobe: Ausserhalb des geschuetzten Ziels bleibt der Ad-hoc-Build
        # die normale Arbeitsweise — die Regel sperrt nur /Applications.
        self.install_ok()
        self.assertTrue(os.path.isdir(self.app()))

    def test_stage_only_and_uninstall_are_mutually_exclusive(self):
        # "Nur bauen" darf niemals nebenbei deinstallieren: die Kombination
        # ist ein Aufruffehler (Exit 2), BEVOR irgendein Ziel angefasst wird.
        self.install_ok()
        stage = os.path.join(self.root, "stage")

        for arguments in (["--stage-only", stage, "--uninstall"],
                          ["--uninstall", "--stage-only", stage]):
            with self.subTest(arguments=arguments):
                result = self.run_installer(*arguments)
                self.assertEqual(result.returncode, 2,
                                 result.stderr + result.stdout)
                self.assertIn("schliessen sich aus", result.stderr)
                # Installierte Artefakte blieben unangetastet.
                self.assertTrue(os.path.isdir(self.app()))
                self.assertTrue(os.path.isdir(self.workflow()))
                self.assertTrue(os.path.lexists(
                    os.path.join(self.links, "ncpin")))

    def test_atomic_replace_missing_target_branch_refuses_late_collision(self):
        # Simuliert das Rennen im "Ziel existiert noch nicht"-Zweig: Die
        # Existenzpruefung sieht kein Ziel, aber beim Rename ist ein fremdes
        # aufgetaucht. Der exklusive Rename muss es schuetzen (EEXIST) statt
        # es still zu ersetzen.
        spec = importlib.util.spec_from_file_location(
            "atomic_replace_test_module",
            os.path.join(self.copy, "tools", "atomic_replace.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        parent = os.path.join(self.root, "atomic-excl")
        os.makedirs(parent)
        source = os.path.join(parent, "quelle")
        destination = os.path.join(parent, "ziel")
        for path, content in ((source, "neu"), (destination, "fremd")):
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(content)

        with mock.patch.object(module.os.path, "lexists",
                               side_effect=lambda p: p == source):
            with self.assertRaises(OSError):
                module.atomic_replace(source, destination)

        with open(destination, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "fremd")
        self.assertTrue(os.path.exists(source))

    def test_atomic_replace_swaps_without_intermediate_missing_target(self):
        parent = os.path.join(self.root, "atomic")
        source = os.path.join(parent, "stage")
        destination = os.path.join(parent, "target")
        os.makedirs(source)
        os.makedirs(destination)
        with open(os.path.join(source, "value"), "w", encoding="utf-8") as fh:
            fh.write("neu")
        with open(os.path.join(destination, "value"), "w", encoding="utf-8") as fh:
            fh.write("alt")

        result = subprocess.run(
            ["/usr/bin/python3", os.path.join(self.copy, "tools",
                                               "atomic_replace.py"),
             source, destination],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        with open(os.path.join(destination, "value"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "neu")
        with open(os.path.join(source, "value"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "alt")


if __name__ == "__main__":
    unittest.main()
