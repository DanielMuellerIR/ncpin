#!/bin/zsh
# install.sh — ncpin auf diesem Mac sicher und idempotent einrichten.
#
# Alle Artefakte werden zuerst in einem Temp-Verzeichnis vollstaendig gebaut,
# markiert, signiert und gegebenenfalls notarisiert. Erst danach ersetzt der
# Installer bereits als ncpin-eigen verifizierte Ziele atomar.

set -euo pipefail

REPO="${0:A:h}"
NCPIN="$REPO/ncpin"
# Ziel ist seit 2026-07-26 /Applications statt ~/Applications: ein Ort für alle
# eigenen Apps, und dort liegen ausschließlich Bundles mit Notary-Ticket. Das ist
# keine bloße Zusage: Ohne angeheftetes Ticket bricht der Installer ab, statt dort
# einen Ad-hoc-Build abzulegen (siehe target_needs_notary_ticket).
# ~/Applications wurde dabei geräumt — wer es dennoch braucht, setzt NCPIN_APPS_DIR.
APPS="${NCPIN_APPS_DIR:-/Applications}"
SVC="${NCPIN_SERVICES_DIR:-$HOME/Library/Services}"
LINKDIR_OVERRIDE="${NCPIN_LINK_DIR:-}"
APP1="$APPS/Lokal halten.app"
APP2="$APPS/Speicher freigeben.app"
QA1="$SVC/Lokal halten (Nextcloud).workflow"
QA2="$SVC/Speicher freigeben (Nextcloud).workflow"
BUNDLE_BASE="com.ethermac.ncpin"
QA_BASE="com.ethermac.ncpin.quickaction"
FORCE=0
UNINSTALL=0

STAGE_ONLY=0
STAGE_DIR=""

usage() {
	cat <<'EOF'
Aufruf: ./install.sh [--force] [--uninstall] [--stage-only <dir>] [--help]

  --force            fremde Kollisionen bei der Installation bewusst ersetzen
  --uninstall        nur nachweislich von ncpin installierte Artefakte entfernen
  --stage-only <dir> Droplets und Quick Actions nur bauen (signiert und, wenn
                     moeglich, notarisiert) und nach <dir> legen — nichts
                     installieren. Unterbau von build.sh und release.sh.
                     Nicht mit --uninstall kombinierbar.
EOF
}

while [ $# -gt 0 ]; do
	case "$1" in
		--force) FORCE=1 ;;
		--uninstall) UNINSTALL=1 ;;
		--stage-only)
			STAGE_ONLY=1
			shift
			STAGE_DIR="${1:-}"
			[ -n "$STAGE_DIR" ] || { print -u2 -- "--stage-only braucht ein Zielverzeichnis"; exit 2 ; }
			;;
		-h|--help) usage; exit 0 ;;
		*) print -u2 -- "Unbekannte Option: $1"; usage >&2; exit 2 ;;
	esac
	shift
done

# Betriebsarten schliessen sich aus: "nur bauen" darf niemals nebenbei eine
# Installation entfernen. Vor jedem Zielzugriff mit Aufruffehler ablehnen.
if [ "$UNINSTALL" -eq 1 ] && [ "$STAGE_ONLY" -eq 1 ]; then
	print -u2 -- "--uninstall und --stage-only schliessen sich aus."
	usage >&2
	exit 2
fi

path_exists() {
	[ -e "$1" ] || [ -L "$1" ]
}

plist_owner() {
	local artifact="$1"
	/usr/libexec/PlistBuddy -c "Print :NCPINOwnerIdentifier" \
		"$artifact/Contents/Info.plist" 2>/dev/null || true
}

is_owned_tree() {
	[ -d "$1" ] && [ "$(plist_owner "$1")" = "$2" ]
}

is_owned_link() {
	[ -L "$1" ] && [ "$(readlink "$1")" = "$NCPIN" ]
}

remove_owned_tree() {
	local target="$1" owner="$2"
	if ! path_exists "$target"; then return; fi
	if is_owned_tree "$target" "$owner"; then
		rm -rf -- "$target"
		echo "  entfernt: $target"
	else
		print -u2 -- "  uebersprungen (nicht als ncpin-eigen markiert): $target"
	fi
}

remove_owned_link() {
	local target="$1"
	if ! path_exists "$target"; then return; fi
	if is_owned_link "$target"; then
		unlink "$target"
		echo "  Symlink entfernt: $target"
	else
		print -u2 -- "  uebersprungen (fremdes CLI-Ziel): $target"
	fi
}

uninstall() {
	echo "Entferne ncpin-Integration…"
	remove_owned_tree "$APP1" "$BUNDLE_BASE.local"
	remove_owned_tree "$APP2" "$BUNDLE_BASE.online"
	remove_owned_tree "$QA1" "$QA_BASE.local"
	remove_owned_tree "$QA2" "$QA_BASE.online"
	if [ -n "$LINKDIR_OVERRIDE" ]; then
		remove_owned_link "$LINKDIR_OVERRIDE/ncpin"
	fi
	for directory in "$HOME/bin" "$HOME/.local/bin" "/usr/local/bin"; do
		remove_owned_link "$directory/ncpin"
	done
	if [ "${NCPIN_SKIP_REGISTRATION:-0}" != "1" ]; then
		tccutil reset AppleEvents "$BUNDLE_BASE.local" 2>/dev/null || true
		tccutil reset AppleEvents "$BUNDLE_BASE.online" 2>/dev/null || true
		/System/Library/CoreServices/pbs -update 2>/dev/null || true
	fi
	echo "Fertig. (Die CLI im Repo bleibt erhalten.)"
}

if [ "$UNINSTALL" -eq 1 ]; then
	uninstall
	exit 0
fi

[ -x "$NCPIN" ] || { print -u2 -- "ncpin ist nicht ausfuehrbar: $NCPIN"; exit 1; }

# Ersten beschreibbaren PATH-Ordner verwenden; Tests duerfen ein isoliertes Ziel
# explizit vorgeben, damit niemals /usr/local/bin beruehrt wird.
if [ -n "$LINKDIR_OVERRIDE" ]; then
	LINKDIR="$LINKDIR_OVERRIDE"
else
	LINKDIR=""
	for directory in "/usr/local/bin" "$HOME/.local/bin" "$HOME/bin"; do
		if [ -d "$directory" ] && [ -w "$directory" ]; then
			LINKDIR="$directory"
			break
		fi
	done
	[ -n "$LINKDIR" ] || LINKDIR="$HOME/bin"
fi
LINK="$LINKDIR/ncpin"

assert_replaceable_tree() {
	local target="$1" owner="$2"
	if ! path_exists "$target" || is_owned_tree "$target" "$owner"; then return; fi
	if [ "$FORCE" -eq 1 ]; then
		print -u2 -- "WARNUNG: --force ersetzt fremdes Ziel: $target"
		return
	fi
	print -u2 -- "Kollision: $target ist nicht als ncpin-eigen markiert."
	print -u2 -- "Mit --force bewusst ersetzen oder Ziel manuell umbenennen."
	exit 1
}

assert_replaceable_link() {
	local target="$1"
	if ! path_exists "$target" || is_owned_link "$target"; then return; fi
	if [ "$FORCE" -eq 1 ]; then
		print -u2 -- "WARNUNG: --force ersetzt fremdes CLI-Ziel: $target"
		return
	fi
	print -u2 -- "Kollision: $target gehoert nicht dieser ncpin-Installation."
	exit 1
}

# Explizit gesetztes leeres NCPIN_SIGN_ID bedeutet lokaler Ad-hoc-Fallback und
# verhindert auch in Tests jeden Keychain-Zugriff. Nur wenn die Variable fehlt,
# wird automatisch nach einer Developer ID gesucht.
if [ "${NCPIN_SIGN_ID+x}" = "x" ]; then
	SIGN_ID="$NCPIN_SIGN_ID"
else
	SIGN_ID="$(security find-identity -v -p codesigning 2>/dev/null \
		| grep "Developer ID Application" | head -1 | sed -E 's/.*"(.*)".*/\1/' || true)"
fi
# Profilname: Umgebung schlaegt clone-lokale Git-Konfiguration, dann ein
# generischer Platzhalter-Default. Der Default existiert typischerweise nicht
# als Keychain-Profil — der Profil-Vorabcheck bricht dann frueh und klar ab.
# Den eigenen Profilnamen deshalb per Umgebung setzen oder einmal pro Clone
# hinterlegen: git config ncpin.notaryProfile <profilname>.
# NCPIN_NOTARY_PROFILE bleibt als Alias bestehen.
NOTARY_PROFILE="${NCPIN_NOTARY_PROFILE:-${NOTARY_PROFILE:-}}"
if [ -z "$NOTARY_PROFILE" ]; then
	NOTARY_PROFILE="$(git -C "$REPO" config --local --get ncpin.notaryProfile 2>/dev/null || true)"
fi
: "${NOTARY_PROFILE:=ncpin-notary}"
NOTARIZE="${NCPIN_NOTARIZE:-1}"
case "$NOTARIZE" in 0|1) ;; *) print -u2 -- "NCPIN_NOTARIZE muss 0 oder 1 sein"; exit 2 ;; esac

# In /Applications liegen ausschliesslich Bundles mit angeheftetem
# Notary-Ticket. Ein ad-hoc oder nur signierter Build darf dort nie landen —
# lieber gar nicht installieren als unnotarisiert.
target_needs_notary_ticket() {
	# :A macht den Pfad absolut und loest Symlinks auf, damit weder ein
	# relatives NCPIN_APPS_DIR noch ein Umweg ueber einen Symlink die Regel
	# umgeht.
	case "${APPS:A}" in
		/Applications|/Applications/*) return 0 ;;
	esac
	return 1
}

# Fail-closed und bewusst VOR der Kollisionspruefung: Wer ohne Zertifikat nach
# /Applications installieren will, soll genau das erklaert bekommen und nicht
# zuerst eine Kollisionsmeldung sehen. Bis hier wurde kein Ziel angefasst.
if [ "$STAGE_ONLY" -eq 0 ] && target_needs_notary_ticket; then
	if [ -z "$SIGN_ID" ] || [ "$NOTARIZE" != "1" ]; then
		if [ -z "$SIGN_ID" ]; then
			print -u2 -- "FEHLER: Kein Developer-ID-Zertifikat — ein ad-hoc signiertes Bundle darf nicht nach $APPS."
		else
			print -u2 -- "FEHLER: NCPIN_NOTARIZE=0 — ein unnotarisiertes Bundle darf nicht nach $APPS."
		fi
		print -u2 -- "Dort liegen ausschliesslich Bundles mit angeheftetem Notary-Ticket."
		print -u2 -- "Stattdessen:"
		print -u2 -- "  ./build.sh                          # nur bauen, Apps bleiben im Projektordner build/"
		print -u2 -- "  NCPIN_APPS_DIR=<ziel> ./install.sh  # ausdruecklich gewaehltes anderes Ziel"
		exit 2
	fi
fi

# Kollisionen vor dem teuren Build erkennen. Bis hier wurde kein Ziel veraendert.
# Bei --stage-only entfaellt das: Dieser Weg fasst kein Installationsziel an, und
# ein fremdes ~/Applications/Lokal halten.app duerfte den Build nicht blockieren.
if [ "$STAGE_ONLY" -eq 0 ]; then
	assert_replaceable_tree "$APP1" "$BUNDLE_BASE.local"
	assert_replaceable_tree "$APP2" "$BUNDLE_BASE.online"
	assert_replaceable_tree "$QA1" "$QA_BASE.local"
	assert_replaceable_tree "$QA2" "$QA_BASE.online"
	assert_replaceable_link "$LINK"
fi

BUILD="$(mktemp -d "${TMPDIR:-/tmp}/ncpin-install.XXXXXX")"
trap 'rm -rf -- "$BUILD"' EXIT
BUILD_APPS="$BUILD/apps"
BUILD_SERVICES="$BUILD/services"
mkdir -p "$BUILD_APPS" "$BUILD_SERVICES"
BUILT_APP1="$BUILD_APPS/Lokal halten.app"
BUILT_APP2="$BUILD_APPS/Speicher freigeben.app"
BUILT_QA1="$BUILD_SERVICES/Lokal halten (Nextcloud).workflow"
BUILT_QA2="$BUILD_SERVICES/Speicher freigeben (Nextcloud).workflow"
TMPL="$REPO/apps/ncpin-app.applescript.tmpl"

build_app() {  # $1=action $2=label $3=app-pfad $4=bundle-id
	local action="$1" label="$2" app="$3" bundle_id="$4"
	local source="$BUILD/$action.applescript" app_exec icns
	/usr/bin/python3 "$REPO/apps/render_applescript.py" \
		"$TMPL" "$action" "$label" "$source"
	/usr/bin/osacompile -o "$app" "$source"
	/usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier $bundle_id" \
		"$app/Contents/Info.plist" 2>/dev/null \
		|| /usr/libexec/PlistBuddy -c "Add :CFBundleIdentifier string $bundle_id" \
			"$app/Contents/Info.plist"
	/usr/libexec/PlistBuddy -c "Add :NCPINOwnerIdentifier string $bundle_id" \
		"$app/Contents/Info.plist"

	if [ -f "$REPO/apps/icons/$action.png" ]; then
		icns="$BUILD/$action.icns"
		"$REPO/apps/png2icns.sh" "$REPO/apps/icons/$action.png" "$icns" >/dev/null
		cp -f "$icns" "$app/Contents/Resources/droplet.icns"
		rm -f "$app/Contents/Resources/Assets.car"
		/usr/libexec/PlistBuddy -c "Delete :CFBundleIconName" \
			"$app/Contents/Info.plist" 2>/dev/null || true
		touch "$app"
	fi

	app_exec="$(basename "$app" .app)"
	mv "$app/Contents/MacOS/droplet" "$app/Contents/MacOS/$app_exec"
	/usr/libexec/PlistBuddy -c "Set :CFBundleExecutable $app_exec" \
		"$app/Contents/Info.plist"

	# Die CLI wandert als Kopie MIT ins Bundle. Das Droplet loest sie zur Laufzeit
	# ueber das eigene Bundle auf (siehe ncpinPath in der AppleScript-Vorlage), es
	# steht also kein absoluter Pfad dieses Macs in der App. Nur so ist ein aus dem
	# DMG gezogenes Droplet auf einem fremden Mac ueberhaupt funktionsfaehig.
	# Zwingend VOR dem Signieren: Signieren bleibt der letzte inhaltsaendernde
	# Schritt, sonst zerbricht die Bundle-Versiegelung.
	/usr/bin/ditto "$NCPIN" "$app/Contents/Resources/ncpin"
	chmod 0755 "$app/Contents/Resources/ncpin"

	if [ -n "$SIGN_ID" ]; then
		codesign --force --options runtime --timestamp \
			--entitlements "$REPO/apps/ncpin.entitlements" --sign "$SIGN_ID" "$app"
		echo "   signiert (Developer ID): $(basename "$app")"
	else
		codesign --force --sign - "$app"
		echo "   lokal ad-hoc signiert: $(basename "$app")"
	fi
	codesign --verify --strict --deep "$app"
}

notarize_app() {
	local app="$1" name tmp zip
	name="$(basename "$app")"
	tmp="$BUILD/notary-${name:r}"
	mkdir -p "$tmp"
	zip="$tmp/$name.zip"
	/usr/bin/ditto -c -k --keepParent "$app" "$zip"
	echo "   notarisiere $name …"
	xcrun notarytool submit "$zip" --keychain-profile "$NOTARY_PROFILE" --wait
	xcrun stapler staple "$app"
	xcrun stapler validate "$app"
	spctl --assess --type execute -vv "$app"
	echo "   notarisiert, gestapelt und von Gatekeeper akzeptiert: $name"
}

build_app local "lokal halten" "$BUILT_APP1" "$BUNDLE_BASE.local"
build_app online "Speicher freigeben" "$BUILT_APP2" "$BUNDLE_BASE.online"

# Die Quick Actions rufen die CLI-Kopie in der INSTALLIERTEN App auf, nicht die
# im Repo. Ein .workflow-Bundle kann seinen eigenen Ort zur Laufzeit nicht
# ermitteln — Automator fuehrt nur ein Shell-Skript aus —, also braucht es einen
# absoluten Pfad. Der aus dem Repo waere der Pfad des Build-Macs: Repo
# verschoben oder geloescht, Quick Action tot (gefunden am 2026-08-04). Der Pfad
# in die App ist dagegen derselbe, den auch der Nutzer sieht, und ueberlebt
# jeden Umzug des Repos. Preis: Wer die App aus $APPS entfernt, verliert auch
# die Quick Action — das Skript sagt dann, woran es liegt.
/usr/bin/python3 "$REPO/quickactions/make_quickaction.py" \
	local "Lokal halten (Nextcloud)" "$APP1/Contents/Resources/ncpin" \
	"$BUILT_QA1" >/dev/null
/usr/bin/python3 "$REPO/quickactions/make_quickaction.py" \
	online "Speicher freigeben (Nextcloud)" "$APP2/Contents/Resources/ncpin" \
	"$BUILT_QA2" >/dev/null

if [ -n "$SIGN_ID" ] && [ "$NOTARIZE" = "1" ]; then
	notarize_app "$BUILT_APP1"
	notarize_app "$BUILT_APP2"
elif [ -n "$SIGN_ID" ]; then
	echo "   Notarisierung explizit uebersprungen (NCPIN_NOTARIZE=0)."
else
	echo "   Kein Developer-ID-Zertifikat: lokaler Ad-hoc-Build, kein Release."
fi

# Vor dem ersten Zielschreibzugriff alle gebauten Besitzmarker erneut pruefen.
is_owned_tree "$BUILT_APP1" "$BUNDLE_BASE.local" || { print -u2 -- "Besitzmarker fehlt: $BUILT_APP1"; exit 1; }
is_owned_tree "$BUILT_APP2" "$BUNDLE_BASE.online" || { print -u2 -- "Besitzmarker fehlt: $BUILT_APP2"; exit 1; }
is_owned_tree "$BUILT_QA1" "$QA_BASE.local" || { print -u2 -- "Besitzmarker fehlt: $BUILT_QA1"; exit 1; }
is_owned_tree "$BUILT_QA2" "$QA_BASE.online" || { print -u2 -- "Besitzmarker fehlt: $BUILT_QA2"; exit 1; }

# Ausstieg fuer build.sh und release.sh: fertige Artefakte herausreichen, ohne
# irgendetwas zu installieren. Bewusst NACH den Besitzmarker-Pruefungen, damit
# auch der Staging-Weg nur geprueftes Material weitergibt.
if [ "$STAGE_ONLY" -eq 1 ]; then
	mkdir -p -- "$STAGE_DIR"
	for artefakt in "$BUILT_APP1" "$BUILT_APP2" "$BUILT_QA1" "$BUILT_QA2"; do
		rm -rf -- "$STAGE_DIR/${artefakt:t}"
		/usr/bin/ditto "$artefakt" "$STAGE_DIR/${artefakt:t}"
	done
	echo "BUILD OK: $STAGE_DIR (nicht installiert)"
	exit 0
fi

# Zweite, harte Schranke unmittelbar vor dem Einsetzen. Der Vorabcheck oben
# prueft die Absicht (Zertifikat da, Notarisierung gewollt), diese Stelle das
# Ergebnis: Ohne angeheftetes Ticket am fertigen Bundle wird nach /Applications
# nichts installiert.
if target_needs_notary_ticket; then
	for artefakt in "$BUILT_APP1" "$BUILT_APP2"; do
		if ! xcrun stapler validate "$artefakt" >/dev/null 2>&1; then
			print -u2 -- "FEHLER: kein angeheftetes Notary-Ticket: ${artefakt:t}"
			print -u2 -- "Installation nach $APPS abgebrochen; nichts veraendert."
			exit 1
		fi
	done
fi

mkdir -p "$APPS" "$SVC" "$LINKDIR"

remove_stage() {
	local stage="$1"
	if path_exists "$stage"; then rm -rf -- "$stage"; fi
}

install_tree() {
	local source="$1" destination="$2" owner="$3" parent stage
	# Der Build kann dauern. Deshalb unmittelbar vor jedem Austausch erneut
	# pruefen, ob seit dem Preflight ein fremdes Ziel aufgetaucht ist.
	assert_replaceable_tree "$destination" "$owner"
	parent="$(dirname "$destination")"
	stage="$(mktemp -d "$parent/.ncpin-stage.XXXXXX")"
	/usr/bin/ditto "$source" "$stage"
	if ! /usr/bin/python3 "$REPO/tools/atomic_replace.py" "$stage" "$destination"; then
		remove_stage "$stage"
		return 1
	fi
	# Bei einem Swap liegt der verifizierte alte Stand jetzt am Stage-Pfad.
	remove_stage "$stage"
}

install_link() {
	local destination="$1" parent stage
	assert_replaceable_link "$destination"
	parent="$(dirname "$destination")"
	stage="$parent/.ncpin-stage-link-$$-$RANDOM"
	[ ! -e "$stage" ] && [ ! -L "$stage" ]
	ln -s "$NCPIN" "$stage"
	if ! /usr/bin/python3 "$REPO/tools/atomic_replace.py" "$stage" "$destination"; then
		remove_stage "$stage"
		return 1
	fi
	remove_stage "$stage"
}

install_tree "$BUILT_APP1" "$APP1" "$BUNDLE_BASE.local"
install_tree "$BUILT_APP2" "$APP2" "$BUNDLE_BASE.online"
install_tree "$BUILT_QA1" "$QA1" "$QA_BASE.local"
install_tree "$BUILT_QA2" "$QA2" "$QA_BASE.online"
install_link "$LINK"

echo "1) CLI verlinkt: $LINK -> $NCPIN"
echo "2) Apps installiert: $APP1"
echo "                    $APP2"
echo "3) Quick Actions installiert: $QA1"
echo "                             $QA2"

if [ "${NCPIN_SKIP_REGISTRATION:-0}" != "1" ]; then
	LSREG="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
	[ -x "$LSREG" ] && "$LSREG" -f "$APP1" "$APP2" 2>/dev/null
	/System/Library/CoreServices/pbs -update 2>/dev/null || true
	killall Dock 2>/dev/null || true
fi

case ":$PATH:" in
	*":$LINKDIR:"*) ;;
	*) echo "Hinweis: $LINKDIR ist nicht in PATH." ;;
esac

echo
echo "Test: ncpin doctor"
