# Installer IC-7300 Scope sur Ubuntu

## Ce qu'il faut

Un seul câble USB entre l'IC-7300 et le PC suffit : il porte la commande CI-V et la carte son de la radio. Ce guide installe l'ensemble sur Ubuntu 26.04 (testé) ou 24.04.

| Élément | Détail |
| --- | --- |
| Radio | Icom IC-7300, adresse CI-V par défaut `94h` |
| Câble | USB A vers B, branché sur la prise USB arrière de la radio |
| PC de la radio | Ubuntu 26.04 ou 24.04, bureau GNOME, son PipeWire |
| Réseau (pilotage à distance) | Les deux PC sur le même réseau local `192.168.1.0/24` |

Deux montages sont possibles : tout sur un seul PC, ou la radio sur un PC et le pilotage depuis un autre. Les sections 2 à 6 valent pour les deux ; les sections 7 et 8 ne concernent que le pilotage à distance.

## Paquets Ubuntu

Tout vient des dépôts Ubuntu, en une seule commande :

```bash
sudo apt install git python3-pyqt5 python3-pyqtgraph python3-numpy python3-scipy \
                 python3-serial pulseaudio-utils wsjtx
```

| Paquet | Sert à |
| --- | --- |
| `python3-pyqt5`, `python3-pyqtgraph` | Fenêtres, spectre et waterfall |
| `python3-numpy`, `python3-scipy` | Calculs du spectre et des décodeurs (SSTV, fax, RTTY) |
| `python3-serial` | Commande CI-V de la radio |
| `pulseaudio-utils` | `pactl` et `parec` : son de la radio et partage réseau |
| `wsjtx` | Fournit `jt9`, le décodeur FT8 / FT4 |
| `git` | Récupérer le code |

`pulseaudio-utils` est indispensable même avec PipeWire : sans lui, l'appli n'a pas de son. La mise à niveau de 24.04 vers 26.04 le désinstalle ; il faut alors le réinstaller.

## Récupérer le code

Le code est sur le Gitea du réseau local. On le place dans `~/git`, le dossier des projets :

```bash
mkdir -p ~/git && cd ~/git
git clone http://192.168.1.54:3000/polo/ic7300-scope.git
cd ic7300-scope
```

Pour mettre à jour plus tard : `cd ~/git/ic7300-scope && git pull`. Les entrées du menu pointent vers ce dossier, donc une mise à jour ne demande rien d'autre.

## Brancher la radio

Le PC voit la radio comme un port série (puce CP2102) ; l'utilisateur doit être dans le groupe `dialout` pour l'ouvrir.

1. Régler l'IC-7300 (MENU → SET → Connectors) :

   | Menu de l'IC-7300 | Valeur |
   | --- | --- |
   | `CI-V USB Port` | `Unlink from [REMOTE]` |
   | `CI-V USB Baud Rate` | `115200` |
   | `DATA MOD` | `USB` |
   | `USB MOD Level` | À ajuster avec l'ALC affiché pendant l'émission |

2. Brancher le câble USB et allumer la radio.
3. Donner l'accès au port série, puis fermer la session et la rouvrir :

   ```bash
   sudo usermod -aG dialout $USER
   ```

4. Vérifier que la radio est vue :

   ```bash
   ls /dev/serial/by-id/ | grep IC-7300
   ```

L'appli cherche par défaut le port de la radio actuelle, dont le nom contient son numéro de série (`IC-7300_03009829`). Avec une autre IC-7300, il faut lui donner le chemin affiché à l'étape 4 : `--port /dev/serial/by-id/<nom>`, sur `ic7300_scope.py` comme sur `cat_server.py`.

## Son

La radio apparaît dans PipeWire comme une carte son « PCM2901 Audio Codec » ; rien n'est à installer de plus.

1. Vérifier que son entrée et sa sortie sont là :

   ```bash
   pactl list short sources | grep Burr-Brown
   pactl list short sinks   | grep Burr-Brown
   ```

2. Les noms attendus par l'appli sont `alsa_input.usb-Burr-Brown_from_TI_USB_Audio_CODEC-00.iec958-stereo` (son reçu) et `alsa_output.usb-Burr-Brown_from_TI_USB_Audio_CODEC-00.iec958-stereo` (son émis). S'ils diffèrent, passer les bons à l'appli avec `--source` et `--sink`.
3. Laisser la sortie par défaut du PC sur les haut-parleurs ou le casque, pas sur la radio. L'écoute se choisit ensuite dans l'appli, avec le sélecteur de sortie son.

Ne pas mettre la radio en entrée micro par défaut : un logiciel de visio prendrait alors le son de la radio à la place de la voix.

## Installer IC-7300 Scope

`install.sh` ajoute « IC-7300 Scope » au menu des applications, avec son icône ; il ne demande pas sudo.

```bash
cd ~/git/ic7300-scope
./install.sh
```

Premier lancement : ouvrir « IC-7300 Scope » depuis le menu. La fréquence, le mode, le S-mètre et le scope de la radio doivent s'afficher en quelques secondes.

Pour FT8 et FT4, l'horloge du PC doit être juste à moins d'une seconde : `timedatectl` doit indiquer `System clock synchronized: yes`.

## PC de la radio : IC-7300 Serveur

```
 ┌──────────── PC de la radio (192.168.1.37) ────────────┐          ┌──── PC distant ────────┐
 │  IC-7300 ⇄ cat_server.py    commande CI-V, TCP 7300   │ ◀──────▶ │  ic7300_scope.py       │
 │   (USB)  ⇄ PipeWire-Pulse   son reçu/émis, TCP 4713   │  réseau  │  --remote 192.168.1.37 │
 └───────────────────────────────────────────────────────┘          └────────────────────────┘
```

Sur le PC branché à la radio, `install-serveur.sh` partage la commande CI-V (port TCP 7300) et le son (port TCP 4713) sur le réseau local. Il ne demande pas sudo.

```bash
cd ~/git/ic7300-scope
./install-serveur.sh
```

Le script :

- installe le service utilisateur `ic7300-cat-server`, démarré à chaque ouverture de session ;
- ouvre le son de PipeWire sur le port 4713 (le son coupe une seconde) ;
- ajoute « IC-7300 Serveur » au menu et affiche l'adresse à donner à l'autre PC.

La fenêtre « IC-7300 Serveur » démarre ou arrête le serveur d'un clic, montre le PC distant connecté et liste en direct les commandes transmises à la radio.

Sécurité : seules les adresses `127.0.0.0/8` et `192.168.1.0/24` peuvent piloter la radio. Si la liaison se coupe, le serveur remet la radio en réception en moins de 10 secondes. Si le pare-feu est actif (`sudo ufw status`), ouvrir les deux ports :

```bash
sudo ufw allow from 192.168.1.0/24 to any port 7300,4713 proto tcp
```

## PC distant

Le PC distant n'a besoin ni de radio ni de serveur : les sections 2, 3 et 6 suffisent (paquets, code, `install.sh`).

Pour que l'entrée du menu se connecte toujours à la radio, on passe l'adresse du PC de la radio à `install.sh` :

```bash
cd ~/git/ic7300-scope
./install.sh --remote 192.168.1.37
```

Ou, en ligne de commande, pour une seule fois :

```bash
python3 ic7300_scope.py --remote 192.168.1.37
```

`192.168.1.37` est l'adresse actuelle du PC de la radio ; la fenêtre « IC-7300 Serveur » affiche celle à utiliser. L'appli crée elle-même l'entrée et la sortie son de la radio distante, et les retire à la fermeture.

## Dépannage

| Symptôme | Cause probable | Solution |
| --- | --- | --- |
| « CAT indisponible », pas de fréquence | Pas dans le groupe `dialout`, ou port pris | `sudo usermod -aG dialout $USER` puis nouvelle session ; fermer les autres logiciels de la radio (WSJT-X, QLog…) |
| « CAT indisponible » alors que les droits sont bons | Le serveur réseau tient le port série | Un PC distant est connecté : le déconnecter, ou arrêter le serveur dans « IC-7300 Serveur » |
| Spectre audio plat, pas de son | `pactl` / `parec` absents | `sudo apt install pulseaudio-utils` |
| Pas de décodage FT8 / FT4 | `jt9` absent, ou horloge fausse | `sudo apt install wsjtx` ; vérifier `timedatectl` |
| `--remote` : « Son du PC distant inaccessible » | Port 4713 fermé sur le PC de la radio | Relancer `./install-serveur.sh` ; ouvrir le pare-feu (section 7) |
| `--remote` : pas de commande | Serveur arrêté, ou port 7300 fermé | Démarrer le serveur dans « IC-7300 Serveur » ; `ss -ltn \| grep 7300` sur le PC de la radio |

Journal du serveur, sur le PC de la radio : `journalctl --user -u ic7300-cat-server -f`.
