# ncpin — offene Arbeit

0. **Nextcloud-Desktop-Client v34 (2026-07-28) hat die lokale Socket-API auf
   macOS entfernt** (nur noch XPC mit Team-ID-Prüfung); ncpin ist damit ab
   Client v34 funktionsunfähig, `doctor` erkennt und meldet den Fall seit
   ncpin 1.1.0. Offen ist die Grundsatzentscheidung: Client-Downgrade auf
   4.0.11 dokumentiert lassen, Upstream-Issue bei nextcloud/desktop stellen
   und/oder ncpin auf ein File-Provider-Backend umbauen (setzt Umstieg des
   Sync-Ordners auf File-Provider-VFS voraus).
1. Fake-Socket-Tests noch um fragmentierte `GET_MENU_ITEMS`-Antworten, einen
   unbekannten `toggle`-Zustand und einen echten `--wait`-Timeout ergänzen.
   REGISTER-Fragmentierung, mehrere Roots, Suffixauflösung und Path-Traversal
   sind bereits abgedeckt.
2. LaunchServices-Registrierung der generierten Apps automatisiert prüfen;
   System-Python, `osacompile`, Pfadquotierung und Ad-hoc-Signatur sind bereits
   im isolierten Installer-Harness abgedeckt.
3. Foto-Pin-Iconvariante als kuratiertes Asset-Experiment; vorhandene flache
   Icons bleiben bis zu einer bewussten Auswahl gültig.
4. Vor einer öffentlichen Veröffentlichung vollständigen Privacy-/Signing-
   Preflight und Tests auf mindestens einem gesunden Finder-System ausführen.

Nicht offen: der sichere Roundtrip mit explizitem Fixture, die frühere
Drei-Sekunden-Grundlatenz, fehlende TCC-Identität und Asset-Katalog-Überdeckung.
Das sind implementierte beziehungsweise verifizierte Dauerfallen im
AGENTS-Vertrag.
