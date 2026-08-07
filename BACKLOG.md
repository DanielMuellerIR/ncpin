# ncpin — offene Arbeit

0. Beobachten, ob kommende Client-Versionen den suffix-VFS-Modus oder die
   Rename-Mechanik in `discovery.cpp` entfernen — der Rename-Transport
   (ncpin 1.2.0, Antwort auf die in v34 entfernte Socket-API) hängt an beidem.
   Die Alternativen von damals (Client-Downgrade auf 4.0.11, Upstream-Issue,
   File-Provider-Backend) sind nur dann wieder relevant.
1. `install.sh` als Ganzes zurückrollbar machen. Die fünf Ziele (zwei Apps, zwei
   Quick Actions, CLI-Symlink) werden nacheinander getauscht, und jeder Altstand
   wird sofort gelöscht. Scheitert ein späterer Schritt — etwa die
   Kollisionsprüfung vor der zweiten App —, bleiben frühere Ziele auf dem neuen
   und spätere auf dem alten Stand. Nötig: alle herausgetauschten Altstände bis
   zum erfolgreichen Abschluss behalten, bei jedem Folgefehler rückwärts
   zurückrollen und erst danach aufräumen; dazu Fehler ab jeder Zielposition
   injizieren. Gefunden im Code-Review vom 2026-08-06; die irreführende Meldung
   „nichts installiert" ist bereits korrigiert.
2. Fake-Socket-Tests noch um fragmentierte `GET_MENU_ITEMS`-Antworten, einen
   unbekannten `toggle`-Zustand und einen echten `--wait`-Timeout ergänzen.
   REGISTER-Fragmentierung, mehrere Roots, Suffixauflösung und Path-Traversal
   sind bereits abgedeckt.
3. LaunchServices-Registrierung der generierten Apps automatisiert prüfen;
   System-Python, `osacompile`, Pfadquotierung und Ad-hoc-Signatur sind bereits
   im isolierten Installer-Harness abgedeckt.
4. Foto-Pin-Iconvariante als kuratiertes Asset-Experiment; vorhandene flache
   Icons bleiben bis zu einer bewussten Auswahl gültig.
5. Vor einer öffentlichen Veröffentlichung vollständigen Privacy-/Signing-
   Preflight und Tests auf mindestens einem gesunden Finder-System ausführen.

Nicht offen: der sichere Roundtrip mit explizitem Fixture, die frühere
Drei-Sekunden-Grundlatenz, fehlende TCC-Identität und Asset-Katalog-Überdeckung.
Das sind implementierte beziehungsweise verifizierte Dauerfallen im
AGENTS-Vertrag.
