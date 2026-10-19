#!/bin/sh
# À lancer sur le PC branché à la radio : partage son son et son port CAT
# sur le réseau local pour un IC-7300 Scope lancé avec --remote ailleurs,
# et ajoute « IC-7300 Serveur » au menu des applications.
set -e
DIR=$(cd "$(dirname "$0")" && pwd)
cd "$DIR"
DATA=${XDG_DATA_HOME:-$HOME/.local/share}
PULSE_CONF=~/.config/pipewire/pipewire-pulse.conf.d/50-reseau.conf
mkdir -p "$(dirname "$PULSE_CONF")" ~/.config/systemd/user
if ! cmp -s reseau/50-reseau.conf "$PULSE_CONF"; then
    cp reseau/50-reseau.conf "$PULSE_CONF"
    systemctl --user restart pipewire-pulse     # le son coupe une seconde
fi
cp reseau/ic7300-cat-server.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now ic7300-cat-server.service
install -Dm644 icon-serveur.svg "$DATA/icons/hicolor/scalable/apps/ic7300-serveur.svg"
sed "s|@DIR@|$DIR|" ic7300-serveur.desktop > "$DATA/applications/ic7300-serveur.desktop"
update-desktop-database "$DATA/applications" 2>/dev/null || true
gtk-update-icon-cache -q "$DATA/icons/hicolor" 2>/dev/null || true
sleep 1
echo "Ports ouverts :"
ss -ltn | grep -E ':(4713|7300) ' || echo "  aucun ?"
echo "« IC-7300 Serveur » ajouté au menu des applications."
echo "Sur l'autre PC : python3 ic7300_scope.py --remote $(hostname -I | cut -d' ' -f1)"
