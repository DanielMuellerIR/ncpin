#!/bin/zsh
# png2icns.sh — wandelt ein quadratisches PNG in ein macOS-.icns (alle Größen).
# Aufruf: png2icns.sh <quelle.png> <ziel.icns>
set -e
SRC="$1"; OUT="$2"
[ -f "$SRC" ] || { echo "Quelle fehlt: $SRC" >&2; exit 1; }

WORK="$(mktemp -d)/icon.iconset"
mkdir -p "$WORK"
# macOS erwartet genau diese Dateinamen/Größen im .iconset-Ordner.
for spec in 16:16x16 32:16x16@2x 32:32x32 64:32x32@2x \
            128:128x128 256:128x128@2x 256:256x256 512:256x256@2x \
            512:512x512 1024:512x512@2x; do
	px="${spec%%:*}"; name="${spec##*:}"
	sips -z "$px" "$px" "$SRC" --out "$WORK/icon_${name}.png" >/dev/null
done
iconutil -c icns "$WORK" -o "$OUT"
rm -rf "$(dirname "$WORK")"
echo "icns: $OUT"
