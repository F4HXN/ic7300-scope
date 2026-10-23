<div align="center">

# 📻 IC-7300 Scope

**Le poste de trafic complet pour l'Icom IC-7300, sous Linux.**

Spectre, waterfall, décodeurs numériques, émission CW et pilotage à distance,
dans une seule fenêtre.

![Python](https://img.shields.io/badge/Python-3-3776AB?logo=python&logoColor=white)
![PyQt5](https://img.shields.io/badge/PyQt5-pyqtgraph-41CD52?logo=qt&logoColor=white)
![Linux](https://img.shields.io/badge/Linux-PipeWire%20%2F%20PulseAudio-FCC624?logo=linux&logoColor=black)
![Indicatif](https://img.shields.io/badge/Indicatif-F4HXN-c62828)

</div>

---

## Sommaire

- [Aperçu](#aperçu)
- [Fonctions](#fonctions)
- [Installation](#installation)
- [Utilisation](#utilisation)
- [Trafic en CW](#trafic-en-cw)
- [Piloter la radio depuis un autre PC](#piloter-la-radio-depuis-un-autre-pc)
- [Scanner VHF/UHF](#scanner-vhfuhf)
- [Organisation du code](#organisation-du-code)

---

## Aperçu

IC-7300 Scope se branche sur l'IC-7300 par **un seul câble USB**, qui porte à
la fois la commande **CI-V** et la **carte son** intégrée à la radio. Il n'y a
ni interface externe ni logiciel de pilotage à installer.

```
 IC-7300 ──USB──┬── CI-V (CP2102) ───── fréquence, mode, S-mètre, scope RF, PTT, CW
                └── USB Audio CODEC ─── son reçu → spectre, waterfall, décodeurs
                                        son émis ← FT8, SSTV, fichier audio
```

---

## Fonctions

### 🔭 Affichage

| | |
|---|---|
| **Scope RF** | Le spectre de la radio elle-même, lu en CI-V, avec clic pour se caler sur un signal |
| **Spectre audio** | FFT du son reçu, jusqu'à 3,5 kHz par défaut (`--fmax`) |
| **Waterfall** | Hauteur réglable **à la souris** en tirant la séparation avec le spectre |
| **S-mètre** | En bargraph, lu en continu |
| **Mesures TX** | Puissance, **ROS** et **ALC** pendant l'émission |

### 🎛️ Commandes de la radio

| | |
|---|---|
| **Bandes** | De 160 m à 4 m, fréquence et mode de départ pour chacune |
| **Réglages** | Volume, AF, **RF GAIN**, **SQL**, puissance, niveau TX |
| **Boutons** | `TUNE`, `ATU`, `MONI`, filtres `FIL1-3`, marche/arrêt `⏻` |
| **Sécurité** | L'écoute sur le PC se coupe pendant toute émission (pas de larsen) |

### 🔤 Décodeurs

| Mode | Principe |
|---|---|
| **CW** | Démodulation à la note, seuil adaptatif, vitesse suivie automatiquement |
| **PSK31** | BPSK 31,25 bauds, Varicode, AFC qui suit la dérive |
| **RTTY 45** | 45,45 bauds, écart 170 Hz, Baudot ITA2 |
| **FT8 / FT4** | Créneaux calés sur l'heure UTC, décodage par `jt9` (moteur de WSJT-X) |
| **SSTV** | Réception et émission : Martin, Scottie, Robot, PD |

### 📡 Émission

- **CW** tapée au clavier, envoyée par le manipulateur interne de la radio (CI-V),
  avec vitesse réglable et arrêt immédiat
- **FT8 / FT4** et **SSTV** par la carte son de la radio
- **Fichier audio** au choix, pour passer un message enregistré
- **Enregistrement** de la réception en WAV

---

## Installation

### Dépendances

```bash
sudo apt install python3-pyqt5 python3-pyqtgraph python3-numpy python3-serial \
                 pulseaudio-utils wsjtx
```

> `wsjtx` n'est nécessaire que pour le décodage FT8 / FT4 (il fournit `jt9`).

### Récupérer le projet

```bash
git clone https://github.com/F4HXN/ic7300-scope.git
cd ic7300-scope
./install.sh          # ajoute « IC-7300 Scope » au menu des applications
```

### Réglages de la radio

| Menu de l'IC-7300 | Valeur |
|---|---|
| `CI-V USB Port` | `Unlink from [REMOTE]` |
| `CI-V USB Baud Rate` | `115200` |
| `USB MOD Level` | à ajuster avec l'ALC affiché pendant l'émission |
| `DATA MOD` | `USB` |

---

## Utilisation

```bash
python3 ic7300_scope.py                 # radio branchée sur ce PC
python3 ic7300_scope.py --no-cat        # sans CI-V (port pris par un autre logiciel)
python3 ic7300_scope.py --remote <IP>   # radio branchée sur un autre PC
```

| Option | Rôle |
|---|---|
| `--port` | Port série CI-V (par défaut le CP2102 de la radio dans `/dev/serial/by-id`) |
| `--source` | Entrée son de la radio (PipeWire / PulseAudio) |
| `--sink` | Sortie son vers la radio, pour l'émission |
| `--baud` | Vitesse CI-V, `115200` par défaut |
| `--fmax` | Fréquence audio maximale affichée, en Hz |

> ⏱️ Pour **FT8 / FT4**, l'horloge du PC doit être juste à moins d'une seconde
> près (`timedatectl` doit indiquer `System clock synchronized: yes`).

---

## Trafic en CW

La ligne d'émission CW se trouve **juste sous le texte reçu** : on lit, puis on
répond. Les boutons de macros préparent un message complet dans le champ CW,
qu'on relit avant de l'envoyer avec **Entrée**.

| Bouton | Message préparé |
|---|---|
| **CQ** | `CQ CQ CQ DE F4HXN F4HXN F4HXN PSE K` |
| **QRZ?** | `QRZ? DE F4HXN K` |
| **Répondre** | `F1ABC F1ABC DE F4HXN F4HXN K` |
| **Rapport** | Salutation selon l'heure, RST 599, prénom, QTH, locator |
| **Station 73** | Matériel, puissance, antenne, QSL, 73 et `SK` |
| **Fin** | Remerciements et `SK` en réponse à la fin du correspondant |

- **Mon indicatif** est mémorisé ; **Ma station…** enregistre prénom, QTH,
  locator, radio, puissance et antenne (`~/.config/ic7300-scope/indicatif.json`).
- Le **correspondant** (indicatif et prénom) change à chaque QSO.
- Le bouton **🔎** ouvre sa fiche sur [qrz.com](https://www.qrz.com).

---

## Piloter la radio depuis un autre PC

Le PC relié à la radio partage son **son** et son **port CI-V** sur le réseau
local. Un autre PC, sur le canapé ou dans la maison, pilote la radio comme s'il
y était branché.

```
 ┌───────────── PC de la radio ─────────────┐          ┌──── PC distant ────┐
 │  IC-7300 ⇄ cat_server.py  (TCP 7300)     │ ◀──────▶ │  ic7300_scope.py   │
 │          ⇄ PipeWire-Pulse (TCP 4713)     │  réseau  │     --remote <IP>  │
 └──────────────────────────────────────────┘          └────────────────────┘
```

**Sur le PC de la radio :**

```bash
./install-serveur.sh
```

Ce script installe le service `ic7300-cat-server` et ajoute **IC-7300 Serveur**
au menu, pour le démarrer ou l'arrêter d'un clic.

**Sur le PC distant :**

```bash
python3 ic7300_scope.py --remote 192.168.1.37
```

> 🛡️ **Sécurité** : si la liaison se coupe (Wi-Fi, câble, PC planté), le
> serveur remet la radio **en réception** en moins de 10 secondes. Seules les
> adresses du réseau local sont acceptées.

---

## Scanner VHF/UHF

`scanner_vhf_uhf.py` balaie les bandes amateur **2 m, 70 cm et 6 m** avec une
clé **RTL-SDR**, en pilotant [Gqrx](https://gqrx.dk) par son contrôle à
distance.

| | |
|---|---|
| **Bandes prêtes** | Relais et FM simplex 2 m / 70 cm, bandes entières, 6 m FM (plan IARU Région 1) |
| **Arrêt automatique** | Le seuil suit le **bruit de fond**, mesuré à chaque balayage |
| **Squelch** | Celui de Gqrx suit le seuil : on n'entend que les canaux actifs |
| **Attente** | Reste quelques secondes après une émission, le temps de la réponse |
| **Historique** | Fréquence, heure, nombre de passages et niveau maximum |
| **Passer / Ignorer** | Sauter un canal, ou l'écarter pour de bon (porteuse, parasite) |
| **Double-clic** | Sur le graphique ou l'historique : écoute de cette fréquence |

**Mise en route :**

1. Brancher la clé RTL-SDR et lancer **Gqrx**
2. Démarrer la réception (▶) et activer **Remote control** dans la barre d'outils
3. Lancer le scanner :

```bash
python3 scanner_vhf_uhf.py
```

---

## Organisation du code

| Fichier | Rôle |
|---|---|
| `ic7300_scope.py` | Fenêtre principale, CI-V, son, spectre, waterfall, émission |
| `cw_decoder.py` | Décodeur CW |
| `psk31_decoder.py` | Décodeur PSK31 |
| `rtty_decoder.py` | Décodeur RTTY |
| `ft8_decoder.py` | FT8 / FT4 via `jt9` |
| `sstv.py` · `sstv_window.py` | SSTV : codage, décodage et fenêtre |
| `cat_server.py` | Serveur CI-V réseau pour `--remote` |
| `scanner_vhf_uhf.py` | Scanner VHF/UHF piloté par Gqrx |
| `install.sh` · `install-serveur.sh` | Entrées du menu des applications |
| `reseau/` | Service systemd et partage du son en réseau |

---

<div align="center">

**73 de F4HXN** · JN33KW · Puget-Théniers

</div>
