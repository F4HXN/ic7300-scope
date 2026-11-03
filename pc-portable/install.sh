#!/bin/sh
# Ajoute « IC-7300 PC portable » au menu des applications (Lenovo V15, polo-lenovo-v15).
# La radio est branchée en USB sur le .37 : l'appli la pilote par le réseau
# (cat_server.py port 7300, son PipeWire port 4713).
#   ./install.sh [ADRESSE_DU_PC_RADIO]   (192.168.1.37 par défaut)
set -e
ID=ic7300-pc-portable
APP=ic7300-scope         # = setDesktopFileName de l'appli : le dock relie la fenêtre à cette entrée
RADIO=${1:-192.168.1.37}
HERE=$(cd "$(dirname "$0")" && pwd)
DIR=$(dirname "$HERE")
DATA=${XDG_DATA_HOME:-$HOME/.local/share}
install -Dm644 "$DIR/icon.svg" "$DATA/icons/hicolor/scalable/apps/$ID.svg"
sed -e "s|@DIR@/ic7300_scope.py|$HERE/lancer.sh $RADIO|" \
    -e "s|^Name=.*|Name=IC-7300 PC portable|" \
    -e "s|^Icon=.*|Icon=$ID|" \
    "$DIR/ic7300-scope.desktop" > "$DATA/applications/$APP.desktop"
update-desktop-database "$DATA/applications" 2>/dev/null || true
gtk-update-icon-cache -q "$DATA/icons/hicolor" 2>/dev/null || true
echo "IC-7300 PC portable ajouté au menu des applications (radio sur $RADIO)."
