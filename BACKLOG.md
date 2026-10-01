# ncpin — offene Arbeit

0. Beobachten, ob kommende Client-Versionen den suffix-VFS-Modus oder die
   Rename-Mechanik in `discovery.cpp` entfernen — der Rename-Transport
   (ncpin 1.2.0, Antwort auf die in v34 entfernte Socket-API) hängt an beidem.
   Die Alternativen von damals (Client-Downgrade auf 4.0.11, Upstream-Issue,
   File-Provider-Backend) sind nur dann wieder relevant.
1. Optional: Foto-Pin-Iconvariante als kuratiertes Asset-Experiment; vorhandene
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
- Beide Dropletaktionen und Auswahl-Klickstart beider Apps über LaunchServices
  an einem aktuellen Developer-ID-signierten, notarisierten und gestapelten
  Build. Isolierter Socket und Testdatei mit Leerzeichen/Apostroph; jeder MAKE-
  Befehl wurde anschließend bis zum Zielzustand gepollt. Gatekeeper akzeptiert
  beide Bundles; strikte Signaturprüfung auch nach den App-Läufen erfolgreich.

Erledigt mit ncpin 1.3.7:

- Sichtbare App-, Quick-Action- und CLI-Texte verwenden korrekte Umlaute;
  Picker-Beschriftung und Rückmeldungen sind sprachlich korrigiert.
- Ordner-Picker beider aktueller notarisierter Apps mit leerer Finder-Auswahl
  geprüft: Testdatei korrekt angezeigt, OK erst nach Auswahl aktiv, Aktion
  bestätigt und Zielzustand gepollt. Abbrechen sendet keine Aktion.
  Strikte Signaturprüfung nach Lauf erfolgreich. Der zuvor beobachtete
  Timeout der GUI-Erfassung ist kein reproduzierbarer Picker-Fehler.
- Verweigerte Finder-Automation und ein echter Finder-Hang wurden nicht
  provoziert. Die bestehende Kompilierungs-/Codeprüfung deckt die Zuordnung
  von -1743 und -1712 zu den erklärenden Fehlerdialogen ab; eine echte
  Geräteabnahme dieser Fehlerzustände bleibt ereignisabhängig.

Nicht offen: der sichere Roundtrip mit explizitem Fixture, die frühere
Drei-Sekunden-Grundlatenz, fehlende TCC-Identität und Asset-Katalog-Überdeckung.
Das sind implementierte beziehungsweise verifizierte Dauerfallen im
AGENTS-Vertrag.
