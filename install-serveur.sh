#!/bin/sh
# À lancer sur le PC branché à la radio : partage son son et son port CAT
# sur le réseau local pour un IC-7300 Scope lancé avec --remote ailleurs.
set -e
cd "$(dirname "$0")"
mkdir -p ~/.config/pipewire/pipewire-pulse.conf.d ~/.config/systemd/user
cp reseau/50-reseau.conf ~/.config/pipewire/pipewire-pulse.conf.d/
cp reseau/ic7300-cat-server.service ~/.config/systemd/user/
systemctl --user restart pipewire-pulse
systemctl --user daemon-reload
systemctl --user enable --now ic7300-cat-server.service
sleep 1
echo "Ports ouverts :"
ss -ltn | grep -E ':(4713|7300) ' || echo "  aucun ?"
echo "Sur l'autre PC : python3 ic7300_scope.py --remote $(hostname -I | cut -d' ' -f1)"
