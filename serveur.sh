#!/bin/sh
# Lanceur du menu « IC-7300 Serveur » : ouvre la fenêtre de marche/arrêt et
# de surveillance du serveur qui laisse un autre PC piloter la radio.
DIR=$(cd "$(dirname "$0")" && pwd)
exec python3 "$DIR/serveur_gui.py" "$@"
