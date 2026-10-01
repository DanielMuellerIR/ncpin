# ncpin — offene Arbeit

0. Beobachten, ob kommende Client-Versionen den suffix-VFS-Modus oder die
   Rename-Mechanik in `discovery.cpp` entfernen — der Rename-Transport
   (ncpin 1.2.0, Antwort auf die in v34 entfernte Socket-API) hängt an beidem.
   Die Alternativen von damals (Client-Downgrade auf 4.0.11, Upstream-Issue,
   File-Provider-Backend) sind nur dann wieder relevant.
1. Echten Droplet- und Toolbarstart über LaunchServices an einem aktuellen,
   notarisierten Build prüfen. Die automatisierte Registrierung ist abgedeckt;
   sie beweist noch keinen App-Start oder Finder-/TCC-Zugriff. Diese Abnahme
   braucht eine freigegebene GUI-Testsitzung und Signierung/Notarisierung.
2. Optional: Foto-Pin-Iconvariante als kuratiertes Asset-Experiment; vorhandene
   flache Icons bleiben bis zu einer bewussten Auswahl gültig.

Erledigt mit ncpin 1.3.6:

- Installer-Gesamtrollback für beide Apps, beide Quick Actions und CLI-Symlink.
  Fehler an jeder Zielposition bei Erstinstallation und Update, eine späte
  Kollision sowie Registrierungsfehler nach allen fünf Austauschen sind geprüft.
  Auch `--force` stellt fremde Altstände wieder her; blockierte Rückabwicklung
  erhält die Sicherung und meldet ihren Pfad.
- Socket-Fixtures für fragmentierte `GET_MENU_ITEMS`-Antworten einschließlich
  UTF-8 und Endmarke, unbekannten `toggle` ohne MAKE und echten `--wait`-Timeout
  bei unverändertem Zustand. Weiter relevant für den Socket-Transport älterer
  Clients und explizite Test-Sockets.
- LaunchServices-Registrierung beider erzeugten Apps mit Nachweis im Registry-Dump
  und anschließender Deregistrierung der temporären Testpfade, ohne App-Start.

Nicht offen: der sichere Roundtrip mit explizitem Fixture, die frühere
Drei-Sekunden-Grundlatenz, fehlende TCC-Identität und Asset-Katalog-Überdeckung.
Das sind implementierte beziehungsweise verifizierte Dauerfallen im
AGENTS-Vertrag.
