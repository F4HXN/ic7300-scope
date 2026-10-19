#!/bin/sh
# Ajoute IC-7300 Scope au menu des applications de l'utilisateur.
set -e
DIR=$(cd "$(dirname "$0")" && pwd)
DATA=${XDG_DATA_HOME:-$HOME/.local/share}
install -Dm644 "$DIR/icon.svg" "$DATA/icons/hicolor/scalable/apps/ic7300-scope.svg"
sed "s|@DIR@|$DIR|" "$DIR/ic7300-scope.desktop" > "$DATA/applications/ic7300-scope.desktop"
update-desktop-database "$DATA/applications" 2>/dev/null || true
gtk-update-icon-cache -q "$DATA/icons/hicolor" 2>/dev/null || true
echo "IC-7300 Scope ajouté au menu des applications."
