#!/bin/zsh
# build.sh — ncpin bauen, ohne irgendetwas zu installieren.
#
# Die drei Einstiegspunkte des Projekts trennen bewusst:
#   ./build.sh     baut Droplets und Quick Actions nach build/, mehr nicht
#   ./install.sh   baut und installiert (Apps, Quick Actions, CLI-Symlink)
#   ./release.sh   baut und packt ein DMG — installiert nie
#
# Die Arbeit macht install.sh --stage-only; dieses Skript ist nur der
# einheitliche Einstiegspunkt nach dem projektübergreifenden Schema.
#
# Ist ein Developer-ID-Zertifikat da, wird signiert und notarisiert; sonst
# fällt install.sh auf eine lokale Ad-hoc-Signatur zurück. Das ist hier richtig:
# Ein reiner Build soll auch ohne Zertifikat durchlaufen. Für /Applications und
# fürs DMG gilt das nicht — dort verlangen install.sh und release.sh ein Ticket.
#
# Aufruf:  ./build.sh [zielverzeichnis]
# Letzte Zeile bei Erfolg: BUILD OK: <verzeichnis> (nicht installiert)
set -euo pipefail
cd "${0:A:h}"

exec ./install.sh --stage-only "${1:-build}"
