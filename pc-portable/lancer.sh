#!/bin/sh
# IC-7300 PC portable (Lenovo V15, polo-lenovo-v15) : lance l'appli en pilotant la radio du .37.
#   ./lancer.sh [ADRESSE_DU_PC_RADIO] [options de ic7300_scope.py]
RADIO=${1:-192.168.1.37}
[ $# -gt 0 ] && shift
DIR=$(cd "$(dirname "$0")/.." && pwd)
CONFIG=${XDG_CONFIG_HOME:-$HOME/.config}/ic7300-scope/connection.json
if [ -f "$CONFIG" ]; then
    exec "$DIR/ic7300_scope.py" "$@"
fi
exec "$DIR/ic7300_scope.py" --remote "$RADIO" "$@"
