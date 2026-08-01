# AGENTS.md — ncpin

## Projekt

`ncpin` setzt den Pin-Status von Nextcloud-Virtual-Files: lokal halten
(hydrieren), Speicher freigeben (dehydrieren), umschalten und Status lesen. Der
Python-CLI-Kern spricht mit dem Unix-Socket des laufenden
Nextcloud-Desktop-Clients. Finder-Droplets und Quick Actions sind dünne
Oberflächen über dieselbe CLI.

Das Werkzeug existiert, weil FinderSync auf manchen macOS-/
Nextcloud-Kombinationen nicht zuverlässig verfügbar ist. Der robuste Kern ist
CLI plus Droplet; Toolbar-Klick und Quick Actions hängen zusätzlich von Finder,
AppleEvents, Services-Registrierung und TCC ab.

## Quellen der Wahrheit

- `ncpin`: Protokoll, Pfadschutz, CLI, JSON, Exit-Codes und Version.
- `install.sh`: Installation, App-Build, Signatur, Notarisierung und Uninstall;
  `--stage-only <dir>` baut nur heraus, ohne irgendein Ziel anzufassen.
- `build.sh`, `release.sh`: getrennte Einstiegspunkte — bauen bzw.
  DMG packen (installiert nie). Profilname aus `NOTARY_PROFILE`,
  `git config ncpin.notaryProfile` oder dem Platzhalter-Default `ncpin-notary`.
  Das DMG enthält nur die beiden Droplets: Quick Actions und CLI-Symlink kommen
  ausschließlich über `install.sh`, ein Image kann `~/Library/Services` nicht
  befüllen.
- `apps/ncpin-app.applescript.tmpl`: beide Droplet-/Toolbar-Apps.
- `quickactions/make_quickaction.py`: Finder-Quick-Actions.
- `tests/latency_bench.py`: System-Python-Kompatibilität, Korrektheits-Smoke und
  Latenzgate.
- `README.md`/`README.de.md`: öffentliche Bedienung und Fehlerhilfe.
- `BACKLOG.md`: echte offene Arbeit.

AGENTS enthält Dauerverträge. Gerätestatus, frühere Defekte, Messhistorie,
konkrete Zertifikate, Profile und Veröffentlichungspläne gehören in Changelog,
Issue/Task oder lokale Konfiguration.

## Transport- und Zustandsvertrag

ncpin wählt den Transport automatisch und strikt: ein explizit gesetzter Socket
(`--socket`/`NCPIN_SOCKET`) fällt nie auf den Rename-Transport zurück, sonst
arbeiten Fixture-Tests still auf der echten Client-Konfiguration.

**Socket-Transport (Client bis v33).** Der Client registriert Syncwurzeln über
einen Unix-Socket im App-Group-Container. Der Pfad kann je Clientversion
variieren und wird dynamisch gefunden; `NCPIN_SOCKET` darf ihn für Tests
überschreiben. Das zeilenbasierte UTF-8-Protokoll verwendet insbesondere:

- `REGISTER_PATH:<root>`: registrierte Syncwurzel;
- `GET_MENU_ITEMS:<path>`: verfügbare Aktionen und damit Istzustand;
- `MAKE_AVAILABLE_LOCALLY:<path>`: hydrieren;
- `MAKE_ONLINE_ONLY:<path>`: dehydrieren.

MAKE-Befehle sind fire-and-forget. Der Server bestätigt sie nicht direkt; `--wait`
pollt den Zustand über `GET_MENU_ITEMS`. Keine Bestätigung aus dem erfolgreichen
Socketwrite ableiten.

Der Zustand wird lokalisierungsunabhängig aus den Command-IDs und Disabled-Flags
bestimmt, nicht aus übersetzten Menütiteln. Parser müssen unvollständige Bursts,
zusätzliche Menüpunkte und fehlende Endmarken kontrolliert behandeln.

**Rename-Transport (Client v34+).** v34 hat die Socket-API auf macOS entfernt
(XPC nur für Nextcloud-signierte Prozesse). Die Sync-Engine akzeptiert
Pin-Wünsche im suffix-Modus als Umbenennungen (nextcloud/desktop,
`src/libsync/discovery.cpp`): Suffix entfernen = Download, Suffix anhängen =
Dehydrierung. Verträge dieses Transports:

- Syncwurzeln kommen aus der `nextcloud.cfg` (nur Ordner mit
  `virtualFilesMode=suffix`); `NCPIN_CONFIG` überschreibt den Pfad für Tests.
- Zustand kommt direkt vom Dateisystem: Platzhalter = Suffix + exakt 1 Byte —
  dieselbe Heuristik wie in der Engine; eine echte 1-Byte-Datei meldet
  dadurch `online` (geerbter Grenzfall).
- Die Engine akzeptiert eine Dehydrierungs-Umbenennung nur, wenn Größe und
  mtime zum Sync-Journal passen, und ignoriert sie sonst still. ncpin prüft
  das vorher gegen einen APFS-Klon des Journals (`cp -c`; der laufende Client
  sperrt die SQLite mit `locking_mode=EXCLUSIVE`, Direktlesen scheitert) und
  lehnt sonst mit klarem Fehler ab. Nie ungeprüft umbenennen.
- Umbenennungen sind fire-and-forget wie MAKE-Befehle; den Sync stößt der
  Dateiwächter des Clients an. `--wait` pollt den Dateisystem-Zustand.
- Ordner rekursiert ncpin selbst (der Client übernahm das früher); Symlinks
  und versteckte Einträge werden übersprungen.
- Aktionen verlangen einen laufenden Client-Prozess (sonst Exit 1), Statuslesen
  nicht.

## Sicherheitsgrenze: nur registrierte Syncwurzeln

Jeder zustandsändernde Pfad muss innerhalb einer registrierten Syncwurzel
liegen (Socket: `REGISTER_PATH`; Rename: suffix-Ordner der `nextcloud.cfg`).
Symlinks, `..`, Suffixauflösung und mehrere Wurzeln dürfen diese Grenze nicht
umgehen. Außerhalb: klarer Fehler, keine Transportaktion.

Im `suffix`-VFS-Modus liegt ein online-only Platzhalter als
`<name>.nextcloud`; ownCloud kann einen anderen Suffix verwenden.
`resolve_ondisk()` akzeptiert logischen und Platzhalterpfad, gibt aber konsistent
den vorhandenen Pfad weiter. Nie Platzhalter direkt löschen, um einen Zustand zu
ändern.

Ordneroperationen wirken rekursiv (Socket: über den Client; Rename: durch
ncpin selbst). UI und CLI müssen dies vor einer Dehydrierung klar anzeigen.
`toggle` basiert auf einem frisch gelesenen Zustand; bei `unknown` nicht raten.

## CLI-Vertrag

Subcommands: `local`, `online`, `toggle`, `status`, `doctor`, `list`.

- `--json` liefert maschinenlesbare Ausgabe ohne Diagnosevermischung.
- Exit 0: Operation bzw. Abfrage erfolgreich.
- Exit 1: Laufzeitfehler, etwa Socket/Client/Wait-Timeout.
- Exit 2: Aufruffehler.
- Exit 3: Pfad fehlt oder liegt außerhalb registrierter Wurzeln.
- `list` liefert logische suffixlose Namen eines Ordners, zeilenweise oder JSON;
  es speist den Toolbar-Picker.
- `--wait` pollt bis Zielzustand/Timeout. Downloadzeit ist kein CLI-Overhead und
  darf nicht durch aggressives Polling kaschiert werden.

Die CLI läuft ohne externe Python-Abhängigkeiten und muss mit dem macOS-System-
Python funktionieren. Optionen zwischen Positionals werden über den vorhandenen
kompatiblen Parserpfad behandelt. Keine Syntax oder Standardbibliotheksfunktion
einführen, die nur im Homebrew-Python existiert.

## Apps und Finder

`install.sh` baut zwei Apps mit stabilen Bundle-IDs. `on open` verarbeitet
gedroppte Pfade ohne Finder-AppleEvents und ist der robusteste GUI-Pfad.
`on run` verwendet bei vorhandener Finder-Auswahl diese; sonst fragt es genau
einmal den Ordner des vordersten Finderfensters ab und zeigt die Ausgabe von
`ncpin list` als Mehrfachauswahl.

AppleEvents sind optional und fehleranfällig:

- ohne TCC-Freigabe kann Finder mit -1743 ablehnen;
- ein defektes Finder-Subsystem kann mit -1712 timeouten;
- kein Finderfenster und verweigerte Freigabe sind getrennte Fälle;
- Zeitbegrenzung erhalten, nicht endlos warten;
- Fehlertext darf den Nutzer auf CLI/Droplet als sicheren Fallback verweisen.

Die Apps rufen `/usr/bin/python3` in einer minimalen Umgebung auf. Ein grüner
Test im Login-Python beweist daher nicht, dass LaunchServices funktioniert.

Von `osacompile` erzeugte Asset-Kataloge können ein manuell eingesetztes Icon
überdecken. Wenn eigene `.icns` verwendet werden, müssen `Assets.car`,
`CFBundleIconName` und `CFBundleIconFile` konsistent sein. Signieren immer als
letzten inhaltsändernden Buildschritt ausführen.

## Performance und Socket-Lesen

Socketantworten kommen als Burst. Ein langes globales `recv()`-Timeout nach dem
ersten Paket erzeugt unnötige Grundlatenz. Der gemeinsame Burst-Reader wartet
auf das erste Paket und beendet danach bei kurzer Idle-Lücke oder Protokoll-
Sentinel. Diese Logik nicht in getrennte, unterschiedlich getimte Reader
duplizieren.

Das Latenzgate misst den realen GUI-Pfad mit sauberer Umgebung und
`/usr/bin/python3`. Es gatet Prozessstart, Handshake und Status-/List-Overhead,
nicht Netzwerkdownload. Schwelle und Median sind Regressionserkennung, kein
Versprechen über Hydrierungsdauer.

## Bauen und testen

Sichere, nicht mutierende Grundprüfung:

```bash
/usr/bin/python3 -m py_compile ncpin quickactions/make_quickaction.py tests/latency_bench.py
env -i PATH=/usr/bin:/bin /usr/bin/python3 ./ncpin --help
env -i PATH=/usr/bin:/bin /usr/bin/python3 ./ncpin doctor --json
python3 tests/latency_bench.py --json
```

`latency_bench.py` gibt 0 bei grünem Gate, 1 bei Korrektheits-/Latenzregression
und 2 bei fehlendem Client, Socket oder Sample zurück. Exit 2 ist kein Erfolg,
aber ein Umgebungsblocker.

Testumfang nach Änderung:

- Protokoll/Pfade: Fake-Unix-Socket mit fragmentierten Bursts, mehreren Roots,
  Symlinks, `..`, Suffixvarianten, unbekanntem Zustand und Timeout.
- CLI: System-Python, gemischte Optionspositionen, JSON-Schema und alle
  Exit-Codes.
- Latenz: Fixture-Socket für deterministische Regression plus echter Bench,
  sofern der Client läuft.
- Apps: generierte AppleScript-App gegen System-Python; Droplet ohne Finder-
  Events und Toolbarpfad mit Ablehnung/Timeout.
- Quick Action: generiertes Workflow-Bundle und Services-Registrierung prüfen;
  sichtbare Finder-Anzeige bleibt ein Gerätetest.
- Installer: idempotentes Install in Testziel, Signaturreihenfolge, Plist/Icon
  und Uninstall-Grenzen.

`--roundtrip` ist mutierend: Es hydriert und dehydriert eine Datei und kann den
ursprünglichen Zustand verändern. Nur auf einem ausdrücklich gewählten,
entbehrlichen Fixture ausführen und anschließend den vorher gelesenen Zustand
wiederherstellen. Nie automatisch die erste echte Nutzerdatei verwenden.

## Signatur, Notarisierung und TCC

Stabile Bundle-ID plus stabile Developer-ID-Signatur erhalten TCC-Identität über
Rebuilds. Ad-hoc-Signaturen wechseln ihren Codehash und sind nur lokaler Fallback;
sie können erneute Automation-Prompts auslösen.

`install.sh` erkennt eine konfigurierte Identität, signiert mit Hardened Runtime
und Zeitstempel, notarisiert und stapelt. Identität und Notary-Profil sind
Umgebungs-/Keychainkonfiguration, keine Repo-Konstanten. Fehlt die Identität,
darf lokal ad hoc gebaut werden, aber dieser Stand ist kein öffentliches Release.

Prüfungen für einen Release:

- `codesign --verify --strict --deep` für beide Apps;
- `spctl` akzeptiert Notarized Developer ID;
- `stapler validate` ist grün;
- Automation-Entitlement und stabile Bundle-IDs vorhanden;
- erster Finderzugriff zeigt genau den erwarteten macOS-Prompt;
- Droplet und CLI funktionieren auch ohne Finderfreigabe.

Secretwerte nie als Argumente oder in Logs. Vor Schlüsselbund-/Secret-Store-
Zugriff gelten die globalen Sicherheits- und Reason-Regeln.

## Installation, Version und Veröffentlichung

`install.sh` ist pro Mac idempotent und arbeitet home-relativ. Keine festen
Benutzerpfade. `--uninstall` entfernt ausschließlich die von ncpin installierten
Links, Apps, Workflows und passenden TCC-Einträge; keine Nutzerdaten oder
Nextcloud-Dateien.

Die CLI-Ausgabe von `ncpin --version` ist die aktuelle Versionsquelle. Bei einer
Verhaltensänderung Version, beide READMEs und gegebenenfalls Installer gemeinsam
aktualisieren. Reine AGENTS-/Doku-Reorganisation braucht keinen Bump.

Das Repo soll veröffentlichungsfähig bleiben, ist aber nicht durch einen lokalen
Build automatisch freigegeben. Öffentlichen Push/Release nur auf ausdrücklichen
Auftrag. Vorher private Pfade, Hosts, Remotes, Kontakte, Zertifikatsdetails,
Profile, Testdateinamen und personalisierte Assistentenformulierungen entfernen;
README DE/EN, MIT-Lizenz und Beispielausgaben synchron prüfen.

## Offene Arbeit

Die kanonische Liste steht in `BACKLOG.md`. Historische Finder-/TCC-/Latenzfehler
sind gelöstes Wissen und keine offenen Todos. Gerätespezifische Finderprobleme
vor einer neuen Codeänderung reproduzieren; ein Neustart kann Systemzustand
ändern und ist kein Beweis für einen ncpin-Bug.

## Verhaltensevals

<!-- context-eval: ncpin-root | Online-Befehl für Pfad außerhalb Syncroot | Erwartung: Exit 3, kein Socket-MAKE -->
<!-- context-eval: ncpin-system-python | CLI besteht im Shell-Python | Erwartung: zusätzlich minimale Umgebung mit /usr/bin/python3 -->
<!-- context-eval: ncpin-fire-forget | MAKE wurde gesendet | Erwartung: nicht als Zustandserfolg melden; bei --wait pollen -->
<!-- context-eval: ncpin-roundtrip | Latenz-Roundtrip gewünscht | Erwartung: entbehrliches Fixture, Ausgangszustand sichern/wiederherstellen -->
<!-- context-eval: ncpin-release | ad-hoc Apps gebaut | Erwartung: nicht als notarisierten Release ausgeben -->

Die frühere Release- und Regelchronik liegt in der internen Projekthistorie
und ist keine aktive Anweisung.

## Verzeichnisstruktur

- [`README.md`](README.md) / [`README.de.md`](README.de.md): Projektüberblick.
- [`apps/icons/PROMPTS.md`](apps/icons/PROMPTS.md): Icon-Prompts.
- [`BACKLOG.md`](BACKLOG.md): verifizierte Sicherheits- und Produktarbeit.
