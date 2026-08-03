**🌐 Sprache / Language:** [English](README.md) · [Deutsch](README.de.md)

# ncpin

Nextcloud-VFS-Pin per Kommandozeile setzen — **am kaputten Finder-Menü vorbei.**

> **ℹ️ Client v34 hat den Unterbau ausgetauscht — ncpin 1.2 passt sich automatisch an.**
> Client v34.0.0 (erschienen 2026-07-28) hat die lokale Socket-API auf macOS entfernt
> ([PR #9463](https://github.com/nextcloud/desktop/pull/9463)): Die Finder-Integration läuft
> seitdem über XPC, und der Client akzeptiert dort nur Prozesse mit Nextclouds eigener Team-ID —
> ncpins ursprünglicher Transport war damit weg, ohne Einstellung, die ihn zurückbringt.
> Seit **ncpin 1.2** nutzt das Werkzeug auf v34+ deshalb einen zweiten Transport: Die Sync-Engine
> selbst nimmt Pin-Wünsche im suffix-Modus als **Datei-Umbenennungen** entgegen
> ([discovery.cpp](https://github.com/nextcloud/desktop/blob/stable-34.0/src/libsync/discovery.cpp):
> *„A suffix vfs file can be downloaded by renaming it to remove the suffix"* — und die
> Gegenrichtung löst die Dehydrierung aus). Clients bis v33 laufen weiter über den klassischen
> Socket; `ncpin doctor` zeigt den aktiven Transport an.

Setzt für Dateien/Ordner im Nextcloud-Sync-Ordner den Pin-Zustand:

- **lokal halten** (hydrieren / „Immer lokal verfügbar") — lädt die echte Datei herunter
- **Speicher freigeben** (dehydrieren / „Nur online verfügbar") — ersetzt sie durch einen winzigen Platzhalter

## Warum

Der Nextcloud-Client bietet diese Aktionen normalerweise im Finder-Rechtsklick über seine
**FinderSync-Extension** an. Auf manchen macOS-Versionen (beobachtet: macOS 26.x mit Nextcloud
33.0.5) lässt sich diese Extension nicht dauerhaft aktivieren — das Menü fehlt dann komplett.

ncpin umgeht das: Die Extension ist nur ein Bote. Je nach Client-Version nutzt ncpin einen von
zwei Transporten (automatisch gewählt):

- **Socket-Transport (Client bis v33):** Der laufende Client bietet einen lokalen Unix-Socket an;
  ncpin schickt exakt dieselben Befehle (`MAKE_AVAILABLE_LOCALLY` / `MAKE_ONLINE_ONLY`), die auch
  die Extension schicken würde.
- **Rename-Transport (Client v34+):** v34 hat diesen Socket entfernt. Die Sync-Engine liest
  Pin-Wünsche im suffix-Modus aber weiterhin aus dem Dateisystem: `datei.pdf.nextcloud` →
  `datei.pdf` umbenennen lässt den Client die Datei herunterladen; die Gegenrichtung dehydriert
  sie. ncpin führt genau diese Umbenennungen aus, liest den Zustand direkt vom Dateisystem, holt
  die Syncwurzeln aus der `nextcloud.cfg` des Clients und prüft vor einer Dehydrierung gegen das
  Sync-Journal, dass die Datei fertig gesynct ist (sonst würde die Engine den Wunsch still
  ignorieren).

Solange der Nextcloud-Client läuft, funktioniert ncpin — Extension hin oder her.

## Installation

```sh
git clone <repo> ~/git/ncpin     # oder schon vorhanden
cd ~/git/ncpin
./install.sh
```

Der Installer ist idempotent und pro-Mac:

1. verlinkt die CLI `ncpin` in einen PATH-Ordner,
2. baut zwei Apps nach `/Applications` (`Lokal halten`, `Speicher freigeben`),
3. installiert zwei Finder-**Quick Actions** (Rechtsklick → Schnellaktionen).

Apps und Workflows werden zunächst vollständig in einem temporären Verzeichnis gebaut und geprüft
und erst danach atomar eingesetzt. Gleichnamige fremde Apps, Workflows oder CLI-Ziele werden nicht
überschrieben. Eine bewusste Übernahme ist mit `./install.sh --force` möglich. Der Aufruf
`./install.sh --uninstall` entfernt ausschließlich Artefakte mit passendem ncpin-Besitzmarker
beziehungsweise den exakten ncpin-Symlink.

### Signierung, Notarisierung & Berechtigungen

Liegt ein **Developer-ID**-Zertifikat im Schlüsselbund, signiert `install.sh` die zwei Apps
automatisch (Developer ID + Hardened Runtime), **notarisiert und stapelt** sie und prüft sie danach
mit Gatekeeper. Jeder Signatur-, Notary-, Staple- oder Gatekeeper-Fehler bricht die Installation ab;
der vorher installierte Stand bleibt erhalten. Ohne Zertifikat fällt der Installer auf lokale
Ad-hoc-Signierung zurück; die Apps laufen lokal trotzdem (beim ersten Start ggf. einmal Rechtsklick
→ Öffnen).

Daneben gibt es zwei weitere Einstiegspunkte, die **nichts** installieren:

```sh
./build.sh [zielordner]           # nur bauen → build/ (Default)
./release.sh                      # DMG mit den zwei Droplets, notarisiert
./release.sh --no-finder-layout   # ohne Finder-Fensterlayout (headless)
```

> Das DMG enthält nur die beiden Apps. Die Quick Actions fürs Finder-Rechtsklick-Menü und der CLI-Befehl kommen ausschließlich über `./install.sh` — ein Image kann `~/Library/Services` nicht befüllen. Ein LIESMICH im Image sagt das auch dem, der es öffnet.

Jede App trägt eine eigene Kopie der CLI `ncpin` im Bundle
(`Contents/Resources/ncpin`) und löst sie zur Laufzeit bundle-relativ auf. In einem Droplet steht
damit kein absoluter Pfad des Build-Macs; die Apps funktionieren auch, wenn man sie aus dem DMG auf
einen anderen Mac zieht oder später verschiebt. Eine neu gebaute CLI erreicht eine installierte App
nur über einen erneuten `./install.sh`-Lauf.

- Auto-erkannt; per Env steuerbar: `NCPIN_SIGN_ID`, `NOTARY_PROFILE` bzw. das ältere `NCPIN_NOTARY_PROFILE` (ein
  `xcrun notarytool store-credentials`-Profil), `NCPIN_NOTARIZE=0` (bewusst nur signieren, z.B.
  offline). Ein explizit leeres `NCPIN_SIGN_ID` erzwingt den lokalen Ad-hoc-Modus ohne
  Schlüsselbundsuche.
- Toolbar- / Schnellaktions-Weg schickt Apple Events an den Finder. Beim ersten Mal fragt macOS
  *„… möchte den Finder steuern"* → **Erlauben** (Systemeinstellungen → Datenschutz & Sicherheit →
  Automatisierung). Die Apps tragen das Entitlement `com.apple.security.automation.apple-events`,
  damit der Hardened Runtime das zulässt; ohne dieses blockt macOS das Event noch vor dem Prompt
  (Fehler `-1743`).

## Nutzung über die Kommandozeile (auch für AI-Agenten / Skripte)

**Das ist der primäre, headless-taugliche Weg.** Maschinenlesbarer Output via `--json`,
aussagekräftige Exit-Codes — direkt aus Skripten/Agenten steuerbar.

```sh
ncpin local  ~/Nextcloud/Film.mp4       # lokal holen (hydrieren)
ncpin online ~/Nextcloud/Film.mp4       # Speicher freigeben (dehydrieren)
ncpin toggle ~/Nextcloud/Ordner         # umschalten (Ordner = rekursiv)
ncpin status ~/Nextcloud/Film.mp4       # aktuellen Zustand zeigen
ncpin doctor                            # Client/Transport/Sync-Ordner prüfen

ncpin local --wait ~/Nextcloud/Film.mp4 # blockiert, bis Zustand erreicht
ncpin status --json ~/Nextcloud/*.pdf   # JSON je Pfad
```

`local`, `online` und `toggle` verlangen mindestens einen Pfad und erlauben `--wait`/`--timeout`;
`status` verlangt mindestens einen Pfad, `list` genau einen und `doctor` keinen. Falsche Arity oder
Optionen enden mit Exit 2, bevor irgendein Transport angefasst wird. Optionen dürfen auch zwischen
mehreren Pfaden stehen (macOS-System-Python eingeschlossen).

Der Datei-Suffix wird automatisch aufgelöst: egal ob du `Film.mp4` oder den Platzhalter
`Film.mp4.nextcloud` angibst.

### Exit-Codes

| Code | Bedeutung |
|------|-----------|
| 0 | ok |
| 1 | Laufzeitfehler (Client läuft nicht / kein Transport / noch nicht gesynct / `--wait`-Timeout) |
| 2 | Aufruf-Fehler (falsche Argumente) |
| 3 | ein/mehrere Pfade nicht gefunden oder nicht in einem Nextcloud-Ordner |

### JSON-Beispiel

```sh
$ ncpin status --json ~/Nextcloud/Beispiele/Nextcloud\ intro.mp4
[
  {
    "path": "/Users/.../Nextcloud/Beispiele/Nextcloud intro.mp4.nextcloud",
    "op": "status",
    "state": "online"
  }
]
```

`state` ist `local`, `online` oder `unknown`.

### Konfiguration über Umgebungsvariablen

- `NCPIN_SUFFIX` — Platzhalter-Suffix (Standard `.nextcloud`; ownCloud: `.owncloud`)
- `NCPIN_SOCKET` — Socket-Pfad strikt vorgeben. Ein stale Override erzeugt einen Fehler und fällt
  weder auf einen anderen Socket **noch auf den Rename-Transport** zurück. Die automatische Suche
  akzeptiert nur Unix-Sockets, die mit mindestens einem `REGISTER_PATH` antworten.
- `NCPIN_CONFIG` — Pfad der Client-Konfiguration (`nextcloud.cfg`) strikt vorgeben, aus der der
  Rename-Transport seine Syncwurzeln liest (vor allem für Tests).

## Nutzung im Finder

**Am robustesten (funktioniert immer, auch bei kaputtem Finder-Subsystem): Droplet.**

- Die zwei Apps aus `/Applications` ins **Dock** legen. Datei(en) im Finder auswählen und auf das
  Dock-Icon **ziehen** → hydrieren / Speicher freigeben. Braucht weder Finder-Erweiterungen noch
  AppleEvents.
- Alternativ Dateien direkt auf die App ziehen (Dock, Finder, oder die App als Drop-Ziel in der
  Finder-Toolbar).

**Wenn das Finder-Subsystem gesund ist, zusätzlich:**

- **Rechtsklick → Schnellaktionen → „Lokal halten (Nextcloud)" / „Speicher freigeben (Nextcloud)"**.
- **Toolbar-Klick:** App mit ⌘ in die Finder-Toolbar ziehen, dann in einem Nextcloud-Ordner
  anklicken. Ist etwas markiert, wirkt der Klick auf die Auswahl; sonst öffnet sich ein
  **Auswahlmenü** mit dem Ordnerinhalt (Mehrfachauswahl, OK/Abbrechen, Esc bricht ab) — ohne
  Drag&Drop, also ohne Risiko, beim Verfehlen des Icons versehentlich einen Ordner zu verschieben.
  (Braucht funktionierende Finder-AppleEvents.)

> **Bekannte Einschränkung (macOS 26.x auf manchen Macs):** Das Finder-Erweiterungs-/Services-
> System kann defekt sein — dann erscheinen die Schnellaktionen nicht, und Finder-AppleEvents
> laufen in Timeout (`-1712`), wodurch der Toolbar-**Klick** nicht funktioniert (die App bricht
> dann nach 5 s mit passendem Hinweis ab statt zu hängen). Eine verweigerte Automation-Berechtigung
> (`-1743`) wird davon unterschieden; nur eine wirklich leere Auswahl öffnet den Ordner-Picker.
> In beiden Fehlerfällen nennt die App **Droplet** und **CLI** als Ausweichweg und schreibt keine
> ausgewählten Pfade in ein dauerhaftes Debuglog.
> Symptome desselben Defekts: FinderSync-Extension lässt sich nicht aktivieren, fremde
> Quick Actions (z.B. PeaZip) verschwinden zeitweise. Ein **Neustart** kann das Subsystem wieder
> beleben; ein reiner `killall Finder` reicht oft nicht.

Tauchen die Schnellaktionen (bei gesundem Finder) nicht auf: Systemeinstellungen → Anmeldeobjekte &
Erweiterungen → Erweiterungen → Finder → Häkchen prüfen, danach `killall Finder`.

## Wie es funktioniert

### Socket-Transport (Client bis v33)

Der Client hört auf einem Unix-Socket im App-Group-Container:

```
~/Library/Group Containers/<TeamID>.com.nextcloud.desktopclient/s
```

Protokoll (zeilenbasiert, UTF-8, je Zeile mit `\n`):

```
REGISTER_PATH:/Users/.../Nextcloud         # schickt der Server beim Verbinden
GET_MENU_ITEMS:<pfad>                       # liefert u.a. den Ist-Pin-Zustand
MAKE_AVAILABLE_LOCALLY:<pfad>               # hydrieren / pin „immer lokal"
MAKE_ONLINE_ONLY:<pfad>                     # dehydrieren / Speicher freigeben
```

Den Ist-Zustand liest ncpin lokalisierungs-unabhängig daran ab, welche Menü-Aktion gerade
aktiv (nicht ausgegraut) ist.

### Rename-Transport (Client v34+)

Client v34 hat die Socket-API auf macOS entfernt; der XPC-Ersatz akzeptiert nur Nextclouds eigene
signierte Prozesse. ncpin steuert die Sync-Engine deshalb über die Mechanik, die sie für den
suffix-Modus eingebaut hat ([discovery.cpp](https://github.com/nextcloud/desktop/blob/stable-34.0/src/libsync/discovery.cpp)):

- **hydrieren:** `datei.pdf.nextcloud` → `datei.pdf` umbenennen (ein einfaches `os.rename` erhält
  Inode und mtime — genau das prüft die Engine); der Dateiwächter des Clients bemerkt es und lädt
  den Inhalt herunter;
- **dehydrieren:** `datei.pdf` → `datei.pdf.nextcloud` umbenennen — der Client ersetzt den Inhalt
  durch einen 1-Byte-Platzhalter. Die Engine akzeptiert das nur, wenn Größe und mtime noch zum
  Sync-Journal passen; ncpin prüft das deshalb **vorher** (gegen einen APFS-Klon des Journals, da
  der laufende Client die SQLite-Datei exklusiv sperrt) und lehnt mit klarem Fehler ab, statt eine
  still ignorierte Umbenennung zu hinterlassen;
- **Zustand:** direkt aus dem Dateisystem — ein Platzhalter ist `<name>.nextcloud` mit exakt
  1 Byte (dieselbe Heuristik verwendet die Engine selbst);
- **Syncwurzeln:** aus der `nextcloud.cfg` des Clients (Ordner mit `virtualFilesMode=suffix`);
  es gelten dieselben Pfadgrenzen-Prüfungen wie beim Socket-Transport;
- **Ordner:** rekursiert ncpin selbst, Datei für Datei.

Von der Engine geerbter Grenzfall: Eine echte 1-Byte-Datei ist von einem frischen Platzhalter
nicht unterscheidbar und wird als `online` gemeldet.

## Tests / Latenz-Benchmark

`tests/latency_bench.py` misst den **Overhead** pro Aufruf (Verbinden + Handshake + eine
Zustands-Abfrage, ohne Netzwerk) und dient als Regressions-Gate — damit künftige Änderungen die
Drop→fertig-Latenz nicht heimlich wieder hochziehen. Gemessen wird gegen denselben Interpreter,
den die Droplet-Apps nutzen (macOS-System-Python `/usr/bin/python3`), nicht gegen das Python der
Login-Shell.

```bash
python3 tests/latency_bench.py            # Overhead messen, Gate prüfen (Exit 0/1/2)
python3 tests/latency_bench.py --json      # maschinenlesbar (für CI / Agenten)
python3 tests/latency_bench.py --roundtrip --sample /pfad/zum/entbehrlichen-fixture
```

`--roundtrip` ist mutierend und verlangt deshalb immer ein ausdrücklich gewähltes, entbehrliches
Datei-Fixture innerhalb einer registrierten Syncwurzel. Der Benchmark liest den Anfangszustand,
prüft jeden Einzellauf und stellt den verifizierten Zustand auch nach einem Fehler im `finally`
wieder her. Ohne sicher ermittelbaren Zustand führt er keine blinde Gegenoperation aus.

Die deterministische Suite (Fake-Socket, Rename-Transport, Parser, Benchmark und isolierter
Installer) läuft mit:

```bash
PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m unittest discover -s tests -p 'test_*.py' -v
```

Exit-Codes: `0` Gate grün · `1` Overhead über Schwelle (Standard 1,0 s) oder falscher Zustand ·
`2` Voraussetzung fehlt (kein Client/Transport — `ncpin doctor` muss zuerst grün sein).

## Voraussetzungen

- macOS, laufender Nextcloud-Desktop-Client mit Sync-Ordner im **Virtual-Files-Modus** (`suffix`).
  Clients bis v33 steuert ncpin über die Socket-API, v34+ über den Rename-Transport.
- python3 ≥ 3.7 — keine externen Abhängigkeiten. Das mit macOS gelieferte System-Python
  (`/usr/bin/python3`) reicht; die Droplet-Apps rufen genau dieses auf.
- Sicherheits-Guard: ncpin fasst nur reale, symlink-aufgelöste Pfade **innerhalb** eines
  registrierten Nextcloud-Ordners an und prüft diese Grenze unmittelbar vor Datei-/Transport-
  Zugriffen.

## Multi-Mac

Keine fest verdrahteten Benutzer- oder Repo-Pfade: Die Apps enthalten den tatsächlichen Pfad der
installierten CLI sicher gequotet. Pro Mac einmal `./install.sh`; der aktive Transport wird
dynamisch erkannt.

## Lizenz

MIT — siehe [LICENSE](LICENSE).
