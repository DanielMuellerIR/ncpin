#!/bin/zsh
# release.sh — DMG mit den beiden ncpin-Droplets packen und notarisieren.
#
# Die drei Einstiegspunkte des Projekts trennen bewusst:
#   ./build.sh     baut Droplets und Quick Actions nach build/, mehr nicht
#   ./install.sh   baut und installiert (Apps, Quick Actions, CLI-Symlink)
#   ./release.sh   baut und packt ein DMG — installiert NIE
#
# ⚠ Was das DMG NICHT kann: Es enthält nur die beiden Droplets. Die Quick
# Actions (Rechtsklick-Menü im Finder) und der CLI-Symlink kommen ausschließlich
# über ./install.sh — ein Ordner unter ~/Library/Services lässt sich nicht per
# Drag & Drop aus einem Image befüllen. Das DMG ist damit der bequeme Weg für die
# beiden Apps, kein vollständiger Installer. Der beiliegende LIESMICH-Text sagt
# das auch dem, der es öffnet.
#
# Zuerst bekommen die Droplets ihr eigenes Notary-Ticket (das erledigt
# install.sh --stage-only), dann das DMG. Ein Ticket allein am Image reicht
# nicht: Wer eine App herauszieht, hätte sonst ein Bundle ohne eigenes.
#
# Voraussetzungen:
#   - "Developer ID Application"-Zertifikat im Schlüsselbund
#   - notarytool-Profil: NOTARY_PROFILE oder `git config ncpin.notaryProfile`
#     (der eingebaute Default ncpin-notary ist nur ein Platzhalter)
#
# Aufruf:
#   ./release.sh                     # vollständiger Lauf
#   ./release.sh --no-finder-layout  # ohne Finder-Fensterlayout (headless)
#
# Letzte Zeile bei Erfolg: RELEASE OK: <pfad-zum-dmg>
set -euo pipefail
cd "${0:A:h}"

FINDER_LAYOUT=1
for arg in "$@"; do
	case "$arg" in
		--no-finder-layout) FINDER_LAYOUT=0 ;;
		-h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
		*) print -u2 -- "Unbekannte Option: $arg"
		   print -u2 -- "Aufruf: ./release.sh [--no-finder-layout]"; exit 2 ;;
	esac
done

NOTARY_PROFILE="${NOTARY_PROFILE:-}"
if [ -z "$NOTARY_PROFILE" ]; then
	NOTARY_PROFILE="$(git config --local --get ncpin.notaryProfile 2>/dev/null || true)"
fi
: "${NOTARY_PROFILE:=ncpin-notary}"
export NOTARY_PROFILE

# Fail-fast VOR dem Bauen: Ein unbrauchbares Profil erst nach der Notarisierung
# der Droplets zu bemerken, kostet nur Zeit.
#
# Fünf Versuche statt einem: `notarytool history` meldet gelegentlich fälschlich
# „No Keychain password item found", obwohl das Profil da ist (2026-07-26 real
# belegt — Versuch 1 fehlgeschlagen, Versuch 2 sofort ok). Ein einzelner
# Fehlversuch würde sonst einen ganzen Lauf grundlos abbrechen; ein wirklich
# fehlendes Profil scheitert auch nach fünf Versuchen.
notary_profile_works() {
	local attempt
	for attempt in 1 2 3 4 5; do
		xcrun notarytool history --keychain-profile "$NOTARY_PROFILE" >/dev/null 2>&1 && return 0
		sleep 3
	done
	return 1
}
if ! notary_profile_works; then
	print -u2 -- "FEHLER: Notary-Profil '$NOTARY_PROFILE' ist auf diesem Mac nicht verwendbar."
	print -u2 -- "Über SSH ist der Login-Schlüsselbund gesperrt — dann in einer lokalen"
	print -u2 -- "Terminalsitzung erneut versuchen."
	exit 2
fi

STAGE="build/release"
VOLNAME="ncpin"
DIST="build/dmg"
DMG="$DIST/ncpin.dmg"
RW_DMG="$DIST/ncpin-rw.dmg"
MOUNT_DIR="/Volumes/$VOLNAME"
# Geraeteknoten (/dev/diskN[sM]) des EIGENEN hdiutil attach. Nur dieses Geraet
# wird je getrennt — niemals blind der Mountpoint: Dort koennte ein fremdes
# Volume gleichen Namens haengen, das ein detach -force mitten im Schreiben
# zwangsweise auswerfen wuerde.
ATTACHED_DEV=""

cleanup() {
	if [ -n "$ATTACHED_DEV" ]; then
		hdiutil detach "$ATTACHED_DEV" >/dev/null 2>&1 \
			|| hdiutil detach "$ATTACHED_DEV" -force >/dev/null 2>&1 || true
		ATTACHED_DEV=""
	fi
	rm -f -- "$RW_DMG"
}
trap cleanup EXIT

echo "=== 1/3 Droplets bauen, signieren, notarisieren ==="
rm -rf -- "$STAGE"
./install.sh --stage-only "$STAGE"

for app in "$STAGE"/*.app(N); do
	xcrun stapler validate "$app"
done

echo "=== 2/3 DMG packen ==="
mkdir -p -- "$DIST"
rm -f -- "$DMG" "$RW_DMG"
# Fail-closed statt detach -force: Haengt unter dem Mountpoint bereits ein
# (moeglicherweise fremdes) Volume, wird es NICHT zwangsgetrennt — abbrechen
# und dem Nutzer das Auswerfen ueberlassen. mount(8) zeigt aufgeloeste Pfade,
# darum beide Schreibweisen pruefen.
if mount | grep -qF " on $MOUNT_DIR (" \
		|| mount | grep -qF " on ${MOUNT_DIR:A} ("; then
	print -u2 -- "FEHLER: $MOUNT_DIR ist bereits eingehaengt — bitte zuerst auswerfen."
	exit 2
fi

# Nur die Apps ins Image — die .workflow-Dateien gehören nach ~/Library/Services
# und wären hier eine Einladung, sie an die falsche Stelle zu ziehen.
PAYLOAD="$(mktemp -d)"
trap 'rm -rf -- "$PAYLOAD"; cleanup' EXIT
for app in "$STAGE"/*.app(N); do
	/usr/bin/ditto "$app" "$PAYLOAD/${app:t}"
done
cat > "$PAYLOAD/LIESMICH.txt" <<'EOF'
ncpin — Droplets für Nextcloud „Lokal halten" / „Speicher freigeben"

Die beiden Apps hier einfach nach /Programme (Applications) ziehen.

Nicht enthalten:
  - die Quick Actions fürs Finder-Rechtsklick-Menü
  - der CLI-Befehl `ncpin`

Beides installiert nur ./install.sh aus dem Repository — ein Image kann den
Ordner ~/Library/Services nicht befüllen.
EOF

SIZE=$(( $(du -sm "$PAYLOAD" | cut -f1) + 20 ))
hdiutil create -srcfolder "$PAYLOAD" -volname "$VOLNAME" -fs HFS+ \
	-fsargs "-c c=64,a=16,e=16" -format UDRW -size "${SIZE}m" "$RW_DMG"
# Attach-Ausgabe einfangen und daraus den Geraeteknoten der Zeile mit unserem
# Mountpoint ziehen — alle spaeteren detach-Aufrufe treffen nur dieses Geraet.
# hdiutil meldet den symlink-aufgeloesten Mountpoint (real belegt: /var/... in
# der Ausgabe als /private/var/...), darum gegen beide Schreibweisen matchen.
ATTACH_OUT="$(hdiutil attach "$RW_DMG" -mountpoint "$MOUNT_DIR" -nobrowse -noverify -noautoopen)"
print -r -- "$ATTACH_OUT"
ATTACHED_DEV="$(print -r -- "$ATTACH_OUT" \
	| awk -v m="$MOUNT_DIR" -v r="${MOUNT_DIR:A}" '$NF == m || $NF == r {print $1; exit}')"
if [ -z "$ATTACHED_DEV" ]; then
	print -u2 -- "FEHLER: Geraeteknoten des eigenen Attach nicht ermittelbar."
	exit 1
fi

ln -s /Applications "$MOUNT_DIR/Applications"

# Icon-Positionen setzen. --no-finder-layout überspringt das: Der Schritt öffnet
# ein echtes Finder-Fenster und reißt den Fokus an sich, was headless-Läufe (und
# Läufe neben laufender Arbeit) stört.
if [ "$FINDER_LAYOUT" -eq 1 ]; then
osascript <<APPLESCRIPT
tell application "Finder"
  tell disk "$VOLNAME"
    open
    set current view of container window to icon view
    set toolbar visible of container window to false
    set statusbar visible of container window to false
    set the bounds of container window to {200, 120, 820, 560}
    set theViewOptions to the icon view options of container window
    set arrangement of theViewOptions to not arranged
    set icon size of theViewOptions to 112
    set position of item "Lokal halten.app" of container window to {130, 170}
    set position of item "Speicher freigeben.app" of container window to {130, 330}
    set position of item "Applications" of container window to {450, 170}
    try
      set position of item "LIESMICH.txt" of container window to {450, 330}
    end try
    update without registering applications
    close
  end tell
end tell
APPLESCRIPT
else
	echo "    (Finder-Layout übersprungen: --no-finder-layout)"
fi

sync; sleep 2                       # Race: DS_Store-Schreibpuffer vs. detach
# Erst regulaer trennen; -force nur als zweiter Versuch und ausschliesslich
# auf den eigenen, oben ermittelten Geraeteknoten.
hdiutil detach "$ATTACHED_DEV" || { sleep 2; hdiutil detach "$ATTACHED_DEV" -force; }
ATTACHED_DEV=""

hdiutil convert "$RW_DMG" -format UDZO -imagekey zlib-level=9 -o "$DMG"
rm -f -- "$RW_DMG"

echo "=== 3/3 DMG signieren, notarisieren, stapeln ==="
SIGN_ID="${NCPIN_SIGN_ID-}"
if [ -z "${SIGN_ID:-}" ]; then
	SIGN_ID="$(security find-identity -v -p codesigning 2>/dev/null \
		| grep "Developer ID Application" | head -1 | sed -E 's/.*"(.*)".*/\1/' || true)"
fi
if [ -z "$SIGN_ID" ]; then
	print -u2 -- "FEHLER: Kein Developer-ID-Zertifikat — ein unsigniertes DMG hat keinen Zweck."
	exit 1
fi
codesign --force --timestamp --sign "$SIGN_ID" "$DMG"
xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" --wait
xcrun stapler staple "$DMG"
xcrun stapler validate "$DMG"
spctl --assess --type open --context context:primary-signature -v "$DMG" 2>&1 | tail -2

echo "RELEASE OK: $PWD/$DMG"
