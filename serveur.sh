#!/bin/sh
# Lanceur du serveur réseau (menu « IC-7300 Serveur ») : montre son état et
# le démarre ou l'arrête. Le serveur laisse un autre PC piloter la radio.
DIR=$(cd "$(dirname "$0")" && pwd)
SERVICE=ic7300-cat-server.service
IP=$(hostname -I | cut -d' ' -f1)
TITLE="IC-7300 Serveur"

if ! systemctl --user cat "$SERVICE" >/dev/null 2>&1; then
    zenity --question --title="$TITLE" --width=380 \
        --text="Le serveur n'est pas installé.\n\nL'installer ? Le son et la commande de la radio seront partagés sur le réseau local (ports 4713 et 7300)." \
        || exit 0
    "$DIR/install-serveur.sh" >/dev/null 2>&1 || {
        zenity --error --title="$TITLE" --text="Échec de l'installation."; exit 1; }
    notify-send -i ic7300-serveur "$TITLE" "Installé et démarré sur $IP"
    exit 0
fi

if systemctl --user is-active --quiet "$SERVICE"; then
    zenity --question --title="$TITLE" --width=380 \
        --ok-label="Arrêter" --cancel-label="Laisser tourner" \
        --text="Le serveur tourne sur <b>$IP</b>.\n\nSur l'autre PC :\n<tt>ic7300_scope.py --remote $IP</tt>" \
        || exit 0
    systemctl --user stop "$SERVICE"
    notify-send -i ic7300-serveur "$TITLE" "Arrêté : la radio ne peut plus être pilotée à distance"
else
    systemctl --user start "$SERVICE"
    sleep 1
    if systemctl --user is-active --quiet "$SERVICE"; then
        notify-send -i ic7300-serveur "$TITLE" "Démarré sur $IP (port 7300)"
    else
        zenity --error --title="$TITLE" --text="Le serveur n'a pas démarré."
    fi
fi
