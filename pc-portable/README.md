# IC-7300 PC portable

Poste : Lenovo V15 (polo-lenovo-v15), Ubuntu.

L'IC-7300 est branché en USB sur le .37 (192.168.1.37), qui fait tourner
`cat_server.py` (CI-V, port 7300) et partage le son de la radio (PipeWire, port 4713).
Ce poste pilote la radio par le réseau local (`--remote`).

```sh
./pc-portable/install.sh            # entrée « IC-7300 PC portable » dans le menu
./pc-portable/lancer.sh             # ou lancement direct
./pc-portable/install.sh 192.168.1.x   # si la radio est branchée sur un autre PC
```

Une seule appli à la fois peut tenir la radio : fermer l'appli lancée sur le .37
(ou sur l'autre poste) avant d'ouvrir celle-ci, sinon le CAT répond « connection reset ».
