# ncpin — offene Arbeit

0. Beobachten, ob kommende Client-Versionen den suffix-VFS-Modus oder die
   Rename-Mechanik in `discovery.cpp` entfernen — der Rename-Transport
   (ncpin 1.2.0, Antwort auf die in v34 entfernte Socket-API) hängt an beidem.
   Die Alternativen von damals (Client-Downgrade auf 4.0.11, Upstream-Issue,
   File-Provider-Backend) sind nur dann wieder relevant.
   Verifiziert am 2026-10-07 gegen den stabilen Client 34.0.5
   ([Quellstand](https://github.com/nextcloud/desktop/tree/62ebad6043b1e7c8e319f41be25d41f5a2c733e5))
   und den aktuellen Entwicklungsstand
   ([Quellstand](https://github.com/nextcloud/desktop/tree/edf26f2b1b98e1bd5c25b25f47808479236040cd)):
   Suffix-Backend, 1-Byte-Platzhalter und beide Rename-Aktionen bestehen weiter.
   Hydrierung verlangt weiterhin virtuellen Journaltyp, gleiche Inode und mtime;
   Dehydrierung verlangt gleiche Größe und mtime. Die ncpin-Vorprüfungen passen
   dazu. Erneut prüfen bei einem Client-Upgrade mit Änderungen an diesen
   Mechanismen oder bei einem reproduzierbaren Kompatibilitätsfehler.

Die vorhandenen flachen Icons bleiben bestehen. Das optionale Foto-Pin-Experiment
ist derzeit nicht geplant.

Erledigt mit ncpin 1.3.10:

- Syncwurzeln bleiben während einer Operation an ursprünglichen Kernelpfad und
  Device/Inode gebunden. APFS-Schreibungsvarianten werden über dieselbe Identität
  erkannt. `list` liest über einen geprüften Verzeichnisdeskriptor, lässt
  Zeilenumbrüche in Namen aus und meldet unlesbare Ordner als Laufzeitfehler.
- Stage-only lehnt Überlappungen mit konfigurierten Installationsartefakten ab,
  einschließlich Alias-Pfaden und eingebetteten CLI-Links. Uninstall isoliert
  Ziele vor der erneuten Identitäts-/Besitzprüfung und erhält fremde Ersetzungen.
- Apps und Quick Actions bestätigen rekursive Ordnerfreigaben vor der ersten
  Aktion. Die CLI zeigt einen Hinweis; Quick Actions erhalten den CLI-Exitcode.
- Ein fehlgeschlagener Release-Build erhält das letzte geprüfte DMG.
- 147 automatisierte Tests mit minimaler Umgebung und macOS-System-Python,
  Python-/Shell-Syntax und reales, nicht mutierendes Latenzgate bestanden.
  Apps und DMG signiert, notarisiert, gestapelt und von Gatekeeper akzeptiert.

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
