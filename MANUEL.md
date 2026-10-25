# Manuel d'IC-7300 Scope

Ce manuel décrit le fonctionnement complet du logiciel, écran par écran. Pour
installer les paquets, régler la radio et le son, voir
[INSTALLATION.md](INSTALLATION.md).

## Sommaire

1. [Principe](#1-principe)
2. [Lancement et options](#2-lancement-et-options)
3. [La fenêtre principale](#3-la-fenêtre-principale)
4. [Fréquence, mode et filtres](#4-fréquence-mode-et-filtres)
5. [Mesures](#5-mesures)
6. [VFO, accord d'antenne et marche/arrêt](#6-vfo-accord-dantenne-et-marchearrêt)
7. [Bandes, mémoires et presets](#7-bandes-mémoires-et-presets)
8. [Spectre et waterfall](#8-spectre-et-waterfall)
9. [Son : écoute et émission](#9-son--écoute-et-émission)
10. [Décodeurs](#10-décodeurs)
11. [Émission CW](#11-émission-cw)
12. [Émission RTTY, FT8 et FT4](#12-émission-rtty-ft8-et-ft4)
13. [Messages pré-enregistrés, station et correspondant](#13-messages-pré-enregistrés-station-et-correspondant)
14. [Carnet de trafic](#14-carnet-de-trafic)
15. [SSTV et fax météo](#15-sstv-et-fax-météo)
16. [Enregistrement](#16-enregistrement)
17. [Pilotage à distance](#17-pilotage-à-distance)
18. [Sécurités](#18-sécurités)
19. [Fichiers du logiciel](#19-fichiers-du-logiciel)
20. [Raccourcis et gestes](#20-raccourcis-et-gestes)

---

## 1. Principe

L'IC-7300 se branche au PC par un seul câble USB, qui porte deux appareils :

| Appareil vu par le PC | Rôle |
|---|---|
| Port série (puce CP2102) | Commande **CI-V** : fréquence, mode, filtres, réglages, mesures, scope RF, émission, CW |
| Carte son « USB Audio CODEC » | **Son reçu** (spectre audio, décodeurs, écoute) et **son émis** (FT8, FT4, SSTV, RTTY, micro, fichier) |

Le logiciel fait tourner deux tâches de fond :

- **la lecture du son** (`parec`, 48 kHz) : 30 spectres par seconde, et le décodeur choisi reçoit le son en continu ;
- **le dialogue CI-V** : la radio est interrogée sans arrêt (S-mètre, fréquence, mode, filtre, réglages…). Chaque commande de l'interface part au tour suivant, et seule la dernière valeur d'un curseur est envoyée.

Les valeurs affichées viennent toujours de la radio. Un réglage fait sur la façade de l'IC-7300 se voit donc aussitôt dans le logiciel.

---

## 2. Lancement et options

Depuis le menu des applications : **IC-7300 Scope**. En ligne de commande :

```bash
python3 ic7300_scope.py                 # radio branchée sur ce PC
python3 ic7300_scope.py --no-cat        # sans CI-V (son seulement)
python3 ic7300_scope.py --remote <IP>   # radio branchée sur un autre PC
```

| Option | Rôle | Par défaut |
|---|---|---|
| `--port` | Port série CI-V | L'IC-7300 trouvé dans `/dev/serial/by-id` |
| `--baud` | Vitesse CI-V | `115200` |
| `--source` | Entrée son (PipeWire/PulseAudio) | Carte son de la radio |
| `--sink` | Sortie son vers la radio (émission) | Carte son de la radio |
| `--fmax` | Fréquence audio maximale affichée (Hz) | `3500` |
| `--no-cat` | N'ouvre pas le port CI-V (pris par un autre logiciel) | — |
| `--remote HÔTE` | Radio sur un autre PC (voir [§ 17](#17-pilotage-à-distance)) | — |

Au démarrage, le logiciel :

1. ouvre le port CI-V (s'il échoue, il réessaie toutes les 2 secondes et l'affiche dans la barre d'état) ;
2. règle le scope de la radio (mode centré, balayage rapide, envoi des données en CI-V) en notant les réglages d'origine pour les remettre à la fermeture ;
3. lit une fois les 99 canaux mémoire de la radio, dès qu'elle répond ;
4. rend l'écoute du PC non muette, au cas où une session précédente l'aurait laissée coupée.

---

## 3. La fenêtre principale

De haut en bas :

```
┌───────────────────────────────────────────────────────────┬────────┬──────────┐
│ Fréquence  MHz  [pas]  MODE  FIL1 FIL2 FIL3 ═largeur═      │  VFO   │ Bandes   │
│ Aller à [_____]                      UTC hh:mm:ss  Locale  │ bouton │ 160m 80m │
│ S-mètre ▮▮▮▮▮▮▮▮▮▯▯▯                                       │ A B    │  …       │
│ Audio dBFS ▮▮▮▮▯▯▯            ALC % ▮▮▯▯▯                  │
│ Entrée TX ▮▮▯▯  Sortie TX ▮▮▮▯                             │ A/B A=B│ Mémoires…│
│ ROS ▮▯▯  COMP ▮▯▯   Vd ▮▮▮▯  Id ▮▯▯                        │ TUNE   │ SSTV…    │
│                                                            │ ATU ⏻  │ Presets… │
│                                                            │        │ Enreg.   │
│                                                            │        │ ÉMETTRE  │
├───────────────────────────────────────────────────────────┴────────┴──────────┤
│ Spectre (scope RF de la radio ou spectre audio)                                │
│ Waterfall                                                                      │
├────────────────────────────────────────────────────────────────────────────────┤
│ Vue  Largeur  Niveaux auto  Plancher  Plage                                    │
│ RX : Sortie · Muet · Volume PC · AF radio · RF · SQL                           │
│ TX : Entrée · Niveau PC · Puissance · Réglage ALC · COMP · Po                         │
├────────────────────────────────────────────────────────────────────────────────┤
│ Décodeur [▼]  Calage auto  Inverser  ●  infos  rappel du mode  Effacer  Grande │
│ Texte décodé (vert) et texte émis (rouge)                                      │
│ CW [texte à émettre_____________] wpm  Pioche  Direct  Envoyer  Stop           │
│ Indicatif [__] Ma station… Correspondant [__] 🔎 [prénom] CQ QRZ? … ✎ Fin…     │
│                                                        Noter le QSO  Carnet…   │
├────────────────────────────────────────────────────────────────────────────────┤
│ Barre d'état : messages du logiciel                                            │
└────────────────────────────────────────────────────────────────────────────────┘
```

Les curseurs qui dépendent de la radio (AF, RF, SQL, puissance, USB MOD, largeur, vitesse CW) restent grisés tant que la radio n'a pas répondu.

---

## 4. Fréquence, mode et filtres

### Fréquence

| Moyen | Effet |
|---|---|
| **Molette** sur l'affichage de la fréquence | Un pas par cran |
| **Bouton rond** (VFO), tourné à la souris ou à la molette | Un pas par cran |
| **Pas** (liste à droite de la fréquence) | 10 Hz, 100 Hz, 1 kHz (par défaut), 5 kHz, 10 kHz, 100 kHz |
| **Aller à** | Taper la fréquence puis Entrée |
| **Clic** sur le spectre ou le waterfall (vue RF) | Fréquence exacte sous la souris |
| **Glisser** sur le spectre ou le waterfall (vue RF) | Comme un bouton de VFO (voir [§ 8](#8-spectre-et-waterfall)) |
| **Molette** sur le spectre ou le waterfall | Un pas par cran |

Avec la molette et le bouton rond, la fréquence se cale d'abord sur un multiple du pas, puis avance d'un pas à la fois.

Le champ **Aller à** comprend :

| Saisie | Compris comme |
|---|---|
| `14.074` (avec un point ou une virgule) | MHz |
| `14074` (nombre moyen) | kHz |
| `14074000` (grand nombre) | Hz |

La fréquence doit rester dans la couverture de l'IC-7300 (30 kHz à 74,8 MHz). Sinon, un message l'indique dans la barre d'état.

### Mode

Le menu **Mode** propose : LSB, LSB-D, USB, USB-D, AM, CW, FM, CW-R.

- **-D** est le mode data de la radio. Le son d'émission vient alors de l'USB (réglage `DATA MOD = USB`). Il sert pour FT8, FT4, RTTY et SSTV.
- Les modes RTTY et RTTY-R de la radio (FSK) ne sont pas dans le menu : le RTTY s'émet en AFSK, en USB-D ou LSB-D. Si la radio est mise en RTTY depuis sa façade, le mode s'affiche quand même.

### Filtres

- **FIL1, FIL2, FIL3** choisissent le filtre de la radio. La bulle de chaque bouton donne sa largeur d'usine pour le mode en cours.
- Le **curseur de largeur** règle la largeur du filtre en cours, comme le menu FILTER › BW de la radio, qui la garde pour ce filtre et ce mode. Les pas sont de 50 Hz jusqu'à 500 Hz puis de 100 Hz en BLU, CW et RTTY, et de 200 Hz en AM. En FM la largeur est fixe : le curseur affiche « fixe ».
- La bande passante s'affiche en jaune clair sur le spectre RF, à la largeur réelle lue sur la radio.

### Horloge

L'heure **UTC** (pour FT8 et le carnet) et l'heure **locale** s'affichent à droite de la ligne de fréquence. L'affichage change pile à chaque seconde.

---

## 5. Mesures

| Barre | Contenu |
|---|---|
| **S-mètre** | De S0 à S9+60 dB, rouge au-dessus de S9 |
| **Audio dBFS** | Niveau du son reçu par le PC, rouge au-dessus de −6 dBFS |
| **Entrée TX** | Niveau envoyé à la radio, Niveau PC compris, en dBFS, rouge au-dessus de −6 : le micro en continu (pour le régler avant d'émettre), ou le fichier pendant l'émission |
| **Sortie TX** | À droite de l'Entrée TX : puissance HF émise par la radio, en W (Po), rouge au-delà de 100 W |
| **ROS** | Rapport d'ondes stationnaires, en émission |
| **COMP dB** | Compression du processeur de parole, en émission BLU à la voix, quand le bouton **COMP** est en service ; sinon il reste à zéro |
| **ALC %** | À droite d'Audio dBFS : ALC en pourcentage de sa zone, en émission. Rester sous 100 |
| **Vd** | Tension d'alimentation de la radio, en V, lue en permanence (13,8 V nominal). Une forte chute en émission signale une alimentation ou des câbles trop justes |
| **Id** | Courant de l'amplificateur, en ampères |

En émission, la puissance de sortie s'affiche aussi en % (**Po**) au bout de la ligne TX. Les barres d'émission se remettent à zéro à la fin de chaque émission.

---

## 6. VFO, accord d'antenne et marche/arrêt

| Commande | Effet |
|---|---|
| **A**, **B** | Passe sur le VFO A ou B |
| **A/B** | Bascule entre les deux |
| **A=B** | Copie le VFO actif dans l'autre |
| Ligne grise sous les boutons | Fréquence et mode de l'autre VFO |
| **TUNE** | Lance un accord de la boîte interne. La radio émet une porteuse ; le bouton passe en rouge « TUNE… » pendant l'accord |
| **ATU** | Met la boîte d'accord interne en service (bouton vert) ou hors service |
| **⏻ ON / OFF** | Met la radio en marche ou l'arrête. L'arrêt demande confirmation et coupe une émission en cours |

La radio ne dit pas quel VFO est actif : le logiciel affiche « VFO ? » jusqu'au premier clic sur A, B ou A/B.

La mise en marche par CI-V demande que le menu `CI-V USB Baud Rate` ne soit **pas** sur Auto.

---

## 7. Bandes, mémoires et presets

### Boutons de bande

Un bouton par bande, de 160 m à 4 m. Le bouton de la bande en cours est surligné en jaune.

Chaque bande retient la **dernière fréquence et le dernier mode** utilisés (fichier `bandes.json`). Un clic sur une bande y ramène la radio. La première fois, la radio va sur une fréquence de départ :

| Bande | Départ | Bande | Départ |
|---|---|---|---|
| 160 m | 1,850 MHz LSB | 17 m | 18,130 MHz USB |
| 80 m | 3,700 MHz LSB | 15 m | 21,250 MHz USB |
| 60 m | 5,360 MHz USB | 12 m | 24,950 MHz USB |
| 40 m | 7,100 MHz LSB | 10 m | 28,500 MHz USB |
| 30 m | 10,120 MHz CW | 6 m | 50,150 MHz USB |
| 20 m | 14,200 MHz USB | 4 m | 70,200 MHz USB |

Les boutons de bande ne font rien pendant une émission.

### Mémoires…

La fenêtre a deux onglets. Dans les deux, un **double-clic** ou **Aller** règle la radio sur la mémoire, mode compris.

**Mémoires du PC** (fichier `memoires.json`) :

- Les cases se modifient directement dans le tableau (nom, fréquence, mode, note).
- **Mémoriser la fréquence actuelle** ajoute la fréquence et le mode en cours.
- **▲ / ▼** changent l'ordre, **Supprimer** retire la mémoire.
- **Envoyer dans la radio** écrit la mémoire choisie dans un canal de l'IC-7300 (1 à 99) ; **Tout envoyer (canaux libres)** range toutes les mémoires dans les canaux vides.
- Au départ, la liste contient les fréquences FT8, FT4, balises NCDXF, QRP CW, SSTV, RTTY et fax météo DWD.

**Mémoires de la radio** (canaux 1 à 99, lus au démarrage) :

- **Copier dans le PC** ajoute le canal aux mémoires du PC.
- **Vider** efface les canaux choisis ; **Tout vider** efface tous les canaux.
- **Montrer les canaux vides** permet d'en remplir un : nom, fréquence (en MHz ou en kHz), mode, puis **Écrire dans la radio**.
- **Relire la radio** relit les 99 canaux.
- Un canal rempli mais illisible n'est jamais écrasé.

### Presets

La liste **Presets…** règle la radio d'un clic. Un preset contient un ou plusieurs de ces réglages :

| Réglage | Détail |
|---|---|
| Mode | LSB, USB-D, CW… |
| Filtre | FIL1, FIL2 ou FIL3 |
| Largeur | Largeur du filtre |
| Puissance | En W |
| USB MOD Level | Gain de l'entrée USB |
| Décodeur | CW, RTTY, FT8… ou aucun |

Un réglage absent du preset n'est pas modifié. La bulle de chaque preset décrit ce qu'il règle.

| Preset de départ | Réglage |
|---|---|
| FT8 | USB-D, FIL1, décodeur FT8 |
| FT4 | USB-D, FIL1, décodeur FT4 |
| RTTY | USB-D, FIL2, décodeur RTTY 45 |
| CW | CW, FIL2, décodeur CW |

- **Enregistrer le réglage actuel…** demande un nom. Le preset enregistré garde le mode, le filtre, la largeur, la puissance, l'USB MOD Level et le décodeur en cours. Un nom déjà pris est remplacé.
- **Supprimer un preset…** en retire un.
- Les presets sont dans `presets.json`. Un preset ne s'applique pas pendant une émission.

---

## 8. Spectre et waterfall

### Les deux vues

| Vue | Contenu | Axe horizontal |
|---|---|---|
| **RF (scope radio)** | Le scope de l'IC-7300 lui-même, lu en CI-V, centré sur la fréquence écoutée | kHz |
| **Audio** | La FFT du son reçu (4096 points, 30 par seconde) | Hz, de 0 à la fréquence max |

En vue RF :

- la **ligne jaune** marque la fréquence écoutée et la **zone jaune claire** la bande passante du filtre ;
- **Largeur** règle la demi-largeur du scope : ±2,5 ; 5 ; 10 ; 25 ; 50 ; 100 ; 250 ; 500 kHz ;
- quand la fréquence change, l'historique du waterfall se décale d'autant au lieu d'être effacé.

En vue audio, le champ en Hz règle la fréquence maximale affichée (500 Hz à 24 kHz, 3500 Hz par défaut).

### Souris

| Geste | Vue RF | Vue audio |
|---|---|---|
| Survol | Ligne blanche et fréquence (au Hz près) en haut à gauche du spectre | Ligne blanche et fréquence audio |
| **Clic** | Règle la radio sur la fréquence exacte sous la souris (le pas est ignoré). En RTTY, un clic dans la bande écoutée choisit le signal à décoder sans changer la fréquence | Choisit la note du décodeur |
| **Glisser** | Comme un bouton de VFO : vers la droite, la fréquence monte. Un pixel vaut un quart de l'échelle de l'écran ; **Maj** rend le geste 10 fois plus fin. Pas de 10 Hz | — |
| **Molette** | Un pas d'accord par cran. Les petits gestes du pavé tactile s'additionnent | Idem |
| Tirer la **limite** entre spectre et waterfall | Change leur hauteur (le curseur devient ↕) | Idem |

### Niveaux du waterfall

- **Niveaux auto** (coché par défaut) : le plancher suit le bruit, un peu sous le 20ᵉ centile du spectre.
- **Plancher** : niveau affiché en noir, à régler à la main quand Niveaux auto est décoché.
- **Plage** : écart entre le noir et le blanc (90 en RF, 60 dB en audio).

Le waterfall garde les 10 dernières secondes (300 lignes), avec les couleurs « inferno ».

---

## 9. Son : écoute et émission

La grille du son a deux lignes : **RX** (réception) en haut, **TX** (émission) en bas.

### RX

| Réglage | Effet |
|---|---|
| **Sortie** | Sortie du PC où l'on entend la radio (haut-parleurs, casque…) |
| **Muet** | Coupe l'écoute sur le PC |
| **Volume PC** | Volume de l'écoute sur le PC (0 à 150 %) |
| **AF radio** | Volume du haut-parleur de la radio. Il ne change pas le son envoyé au PC |
| **RF** | RF GAIN de la radio : à baisser sur une bande bruyante ou un signal très fort |
| **SQL** | Squelch de la radio. À zéro, toujours ouvert |

L'écoute sur le PC passe par un flux PipeWire permanent (`ic7300_loopback_out`). S'il n'existe pas, Sortie, Muet et Volume PC sont grisés.

La sortie USB de la radio ignore le squelch : le logiciel coupe lui-même l'écoute du PC quand le squelch est fermé. Les décodeurs reçoivent toujours le son.

### TX

| Réglage | Effet |
|---|---|
| **Entrée** | Ce qui part à l'émission : un micro du PC, ou **Fichier audio** (bouton **Choisir…** : WAV, FLAC, OGG) |
| **Entrée TX** (bargraph, en haut) | Niveau qui arrive à la radio, **Niveau PC compris** : avec un micro, en continu, même hors émission (parler et régler pour que les crêtes, trait jaune, restent sous le rouge) ; avec un fichier, pendant l'émission |
| **Niveau PC** | Volume du son envoyé à la radio. Le régler pour que l'ALC bouge sans dépasser |
| **Puissance** | Puissance d'émission de la radio, en W |
| **Réglage ALC** | Gain de l'entrée USB de la radio (menu USB MOD Level). En émission, le monter jusqu'à ce que l'ALC bouge, sans rester au maximum |
| **COMP** (bouton, vert en service) et curseur | Compresseur de parole de la radio, niveau de 0 à 10 comme dans son menu. Il ne joue qu'en BLU, à la voix : plus de puissance moyenne. Viser 10 à 20 dB sur le bargraph COMP en parlant |
| **Po** | Puissance de sortie mesurée, pendant l'émission |

### Bouton ÉMETTRE

- **ÉMETTRE** (liseré rouge) met la radio en émission. Il devient **■ ARRÊTER** (fond rouge) pendant l'émission.
- Avec un **micro**, le son du micro est envoyé à la radio jusqu'au clic sur ARRÊTER.
- Avec un **fichier**, le fichier part 400 ms après le passage en émission. L'émission s'arrête toute seule à la fin du fichier.
- Hors mode data, le logiciel met le temps de l'émission l'entrée de la radio sur USB (menu DATA OFF MOD), puis remet le réglage d'avant.

---

## 10. Décodeurs

La ligne **Décodeur** choisit le décodeur. Le texte décodé s'affiche en vert dessous ; le texte émis s'y écrit en rouge.

| Élément | Rôle |
|---|---|
| **Calage auto** | CW et RTTY : la note du décodeur suit le signal |
| **Inverser** | RTTY : échange mark et space. À essayer si le texte reste incohérent |
| **●** (voyant) | Vert vif : décodage actif ; vert sombre : signal présent ; gris : rien |
| Infos | Note, vitesse ou qualité, selon le décodeur |
| Rappel du mode | En italique, le mode qu'il faut pour émettre avec ce décodeur. **Un clic sur le mode y met la radio** |
| **Effacer** | Vide le texte |
| **Grande fenêtre** | Le texte décodé en grand dans une fenêtre à part (F11 : plein écran, Échap pour en sortir) |

La **ligne verte pointillée** sur le spectre marque la note du décodeur (CW, RTTY). En vue RF, elle se place sur la fréquence radio correspondante.

### CW

- Démodulation à la note, seuil adaptatif qui suit le bruit et le signal, vitesse suivie toute seule (4 à 60 mots/min).
- En **Calage auto**, la note la plus forte entre 300 et 1200 Hz est cherchée. Un **clic** sur le spectre audio fixe la note et décoche Calage auto.
- Infos : note et vitesse en mots/min. Le voyant s'allume quand le signal est manipulé.
- Rappel : « passer en CW ou CW-R ».

### RTTY 45 et RTTY 50 météo

| | RTTY 45 | RTTY 50 météo |
|---|---|---|
| Vitesse | 45,45 bauds | 50 bauds |
| Écart | 170 Hz | 450 Hz |
| Usage | Trafic radioamateur | Bulletins de la météo allemande (DWD) |
| Calage auto | ± 60 Hz autour du clic | ± 150 Hz autour du clic |

- **Cliquer entre les deux traces** (mark et space), sur le spectre audio ou, en vue RF, dans la bande écoutée.
- Code Baudot ITA2, retour en lettres après une espace.
- Infos : fréquence du milieu et qualité (part de caractères bien encadrés).
- Rappel : « passer en USB-D ou LSB-D » pour émettre.

### FT8 et FT4

- Le son est découpé en créneaux calés sur l'heure UTC : 15 s en FT8, 7,5 s en FT4.
- Le son est confié à `jt9` (le moteur de WSJT-X, paquet `wsjtx`) deux fois par créneau : tôt (12,4 s en FT8, 5,7 s en FT4) pour avoir les messages avant la fin du créneau, puis au complet à la fin, pour les signaux faibles.
- Une ligne par message : `UTC  dB  DT  Freq  Message`.
- **L'horloge du PC doit être juste à moins d'une seconde** (`timedatectl` : `System clock synchronized: yes`).
- Le voyant s'allume pendant le décodage d'un créneau. Il n'y a pas de ligne de note : toute la bande est décodée.
- Rappel : « passer en USB-D ».
- Fréquences usuelles : 14,074 MHz (FT8), 14,080 MHz (FT4), les autres dans les mémoires du PC.
- Émission : voir [§ 12](#12-émission-rtty-ft8-et-ft4).

### Indicatif dans le texte décodé

Un **double-clic sur un indicatif** du texte décodé (fenêtre principale ou grande fenêtre) le met en **correspondant**. Son prénom est alors cherché sur qrz.com (voir [§ 13](#13-messages-pré-enregistrés-station-et-correspondant)). Le sien n'est pas pris.

---

## 11. Émission CW

La ligne **CW** se trouve juste sous le texte reçu : on lit, puis on répond. Elle est active en mode CW ou CW-R.

| Élément | Rôle |
|---|---|
| Champ de texte | Texte à émettre, **Entrée** ou **Envoyer** pour l'émettre |
| **wpm** | Vitesse du manipulateur de la radio (6 à 48 mots/min) |
| **Pioche** | Frappe humaine, comme au manipulateur droit (voir plus bas) |
| **Direct** | Chaque lettre tapée part aussitôt |
| **Stop** ou **Échap** | Arrête le message en cours |

- Le texte part au **manipulateur interne de la radio** par CI-V, 30 caractères par trame. Le logiciel met la radio en semi BK-IN si le BK-IN est coupé, sinon elle ne passerait pas en émission.
- Caractères acceptés : lettres, chiffres, espace et `/ ? . - , : ' ( ) = + " @`.
- **Signes liés** : `^` lie les deux lettres qui suivent (`^AR`, `^SK`, `^BT`…).
- Le texte émis s'écrit **en rouge, lettre par lettre**, au rythme de la manipulation, parmi le texte décodé. Le texte reçu pendant l'émission s'affiche après.
- Un message envoyé pendant le précédent part à sa suite.

### Direct

- Chaque lettre part dès qu'elle est tapée ; il n'y a pas de retour en arrière possible une fois la lettre partie.
- Entrée envoie le reste du champ (par exemple un message pré-enregistré).
- La ligne rouge se termine après 3 secondes sans frappe.

### Pioche

- La radio est manipulée par la **ligne DTR** du port USB, avec un rythme irrégulier : traits un peu longs, points appuyés, espaces étirées, petites hésitations.
- Menu de la radio : SET › Connectors › USB SEND/Keying › **USB Keying (CW) = DTR**.
- En direct avec la pioche, le mot part à l'espace.
- Impossible avec `--remote` : la ligne DTR ne passe pas par le réseau.

---

## 12. Émission RTTY, FT8 et FT4

### RTTY

Avec le décodeur **RTTY 45** ou **RTTY 50 météo** et la radio en **USB-D ou LSB-D**, la ligne d'émission passe en **RTTY**.

- Le texte est mis en audio AFSK sur **les tonalités du décodeur** (mark et space là où l'on a cliqué), puis émis comme un fichier. On répond donc pile sur la fréquence du correspondant.
- Le message est encadré de retours à la ligne, avec 0,4 s de mark avant.
- Le texte émis s'écrit en rouge au rythme de l'émission.
- **Stop** ou **Échap** arrête l'émission.
- Les messages pré-enregistrés passent à la série RTTY (sans signes liés, indicatif répété trois fois).

### FT8 et FT4

Avec le décodeur **FT8** ou **FT4** et la radio en **USB-D** (ou USB), la ligne d'émission passe en **FT8** ou **FT4**. Le message est codé par `ft8code` / `ft4code` (fournis avec WSJT-X), mis en audio par le logiciel, puis émis en mode data au début d'un créneau UTC.

| Élément | Rôle |
|---|---|
| Champ de texte | Le message ; **Entrée** l'arme : il part au prochain créneau choisi |
| **Pair / Impair** | Créneau d'émission. FT8 : pair = 00 et 30 s, impair = 15 et 45 s ; FT4 : créneaux de 7,5 s. Toujours l'inverse du correspondant |
| **Répéter** (coché au départ) | Le CQ ou l'appel repart à chaque cycle, sans limite de temps, jusqu'à **Stop**, **Échap** ou un autre message. `RR73` et `73` ne partent qu'une fois |
| **TX … Hz** | Fréquence audio d'émission, marquée par la **ligne rouge** pointillée. Un **clic** sur le spectre ou le waterfall la change (en vue RF, dans la bande écoutée) |
| **Stop** ou **Échap** | Annule le message armé et coupe l'émission en cours |

- Le son commence 0,5 s après le début du créneau, comme dans WSJT-X. Un message armé en retard part quand même s'il reste moins de 2 s de retard en FT8 (1 s en FT4), sinon au créneau suivant de même parité.
- Le message émis s'écrit **en rouge** parmi les messages décodés : `heure  TX  fréquence  message`.
- Messages standard : deux indicatifs plus un locator, un report (`-12`), `R-12`, `RR73`, `RRR` ou `73`. Le texte libre est limité à 13 caractères ; un message qui serait reçu autrement est refusé, avec ce qu'il donnerait.
- Comme WSJT-X, chaque créneau est décodé **avant sa fin** (à 12,4 s en FT8, 5,7 s en FT4, décodage rapide) : les messages arrivent vers 14,5 s et la réponse part **dès le créneau suivant**. Un second décodage, complet, à la fin du créneau, ajoute les signaux faibles manqués, sans doublons.

**Messages pré-enregistrés FT** : **CQ** (`CQ {call} {loc4}`), **Répondre** (`{dest} {call} {loc4}`), **Report** (`{dest} {call} {rpt}`), **R+report** (`{dest} {call} R{rpt}`), **RR73**, **73**.

**QSO automatique** : dès qu'un message est armé (CQ ou appel), le logiciel répond tout seul à chaque message qui lui est adressé, avec l'étape suivante du QSO (tableau plus bas), au créneau opposé.

- **Depuis ton CQ** : une station répond, le QSO se déroule tout seul ; à la fin (`RR73` émis, ou `73` reçu), **le CQ reprend** au cycle suivant. La barre d'état rappelle de noter le QSO.
- **Quand tu appelles une station** : le QSO se déroule tout seul jusqu'au `73`, puis l'émission s'arrête.
- **La station appelée répond à une autre** : l'appel s'arrête (« F1ABC répond à F5XYZ : appel arrêté »).
- Une autre station qui t'appelle pendant un QSO est surlignée, mais ne l'interrompt pas.
- **Stop** ou **Échap** arrête tout à n'importe quel moment.

**Double-clic sur une ligne décodée** (sur un CQ ou sur une réponse) : le logiciel prépare le message suivant du QSO, règle le correspondant (et son prénom via qrz.com), le report à envoyer (SNR de la ligne) et le créneau opposé au sien, puis **arme aussitôt le message** : il part tout seul au prochain créneau, sans Entrée. **Échap** l'annule.

| Ligne double-cliquée | Message émis |
|---|---|
| `CQ F1ABC JN23` | `F1ABC F4HXN JN33` |
| `F4HXN F1ABC JN23` (réponse à mon CQ) | `F1ABC F4HXN -07` |
| `F4HXN F1ABC -09` | `F1ABC F4HXN R-15` |
| `F4HXN F1ABC R-11` | `F1ABC F4HXN RR73` |
| `F4HXN F1ABC RR73` | `F1ABC F4HXN 73` |
| `F4HXN F1ABC 73` | Rien : le QSO est fini, il reste à le noter |
| `F5XYZ DL1AA R+02` (QSO d'autres) | `DL1AA F4HXN JN33` (appel de la station qui émet) |

**Réponses isolées** : une ligne décodée qui contient ton indicatif est **surlignée en jaune**, et la barre d'état indique qui te répond. Le panneau **Réponses**, à droite du texte décodé, ne garde que le fil de tes QSO : ces lignes et tes émissions en rouge. Un double-clic y émet la réponse comme dans la zone principale. **Effacer** vide les deux.

**Noter le QSO** reprend le report envoyé, le report reçu et le locator relevés pendant le QSO.

---

## 13. Messages pré-enregistrés, station et correspondant

### Indicatif et Ma station…

- **Indicatif** : le sien, gardé d'une session à l'autre.
- **Ma station…** enregistre :

| Champ | Utilisé dans |
|---|---|
| Prénom, QTH, Locator | Messages, carnet |
| Transceiver, Puissance (W), Antenne | Message « Station 73 », carnet |
| Compte et mot de passe qrz.com | Prénom du correspondant trouvé tout seul |
| Clé API carnet qrz.com | Envoi des QSO au carnet qrz.com |
| Adresse, clé API et profil de station Wavelog | Envoi des QSO à Wavelog |

Ces réglages sont dans `indicatif.json`, lisible par l'utilisateur seul (mots de passe et clés dedans).

### Correspondant

- **Correspondant** et **Son prénom** changent à chaque QSO et ne sont pas enregistrés.
- À la sortie du champ Correspondant (ou après un double-clic sur un indicatif), le prénom est cherché sur **qrz.com** si le compte est renseigné. La barre d'état affiche le nom et le pays.
- Le bouton **🔎** ouvre la fiche du correspondant sur qrz.com dans le navigateur.

### Messages pré-enregistrés

Un bouton par message ; le message rempli est mis dans le champ d'émission, à relire avant **Entrée**.

| Bouton | Message CW de départ |
|---|---|
| **CQ** | `CQ CQ CQ DE {call} {call} {call} PSE K` |
| **QRZ?** | `QRZ? DE {call} K` |
| **Répondre** | `{dest} {dest} DE {call} {call} K` |
| **Rapport** | Salutation selon l'heure, RST 599, prénom, QTH, locator |
| **Station 73** | Matériel, puissance, antenne, QSL, 73 et `SK` |
| **Fin** | Remerciements et `SK` en réponse à la fin du correspondant |

Champs utilisables :

| Champ | Valeur |
|---|---|
| `{call}` | Mon indicatif |
| `{dest}` | Correspondant |
| `{dname}` | Son prénom |
| `{gm}` | GM avant midi, GA l'après-midi, GE après 18 h |
| `{name}`, `{qth}`, `{loc}`, `{rig}`, `{pwr}`, `{ant}` | Ma station… |
| `{loc4}` | Locator sur 4 caractères (FT8 / FT4) |
| `{rpt}` | Report FT à envoyer (SNR du correspondant) |

Si un champ nécessaire est vide (sauf le prénom du correspondant), le logiciel le dit et place le curseur sur le champ à remplir.

Le bouton **✎** ouvre l'éditeur des messages du mode en cours (CW, RTTY ou FT) : nom du bouton, description et texte de chaque message, **Ajouter**, **Supprimer**, **▲ Monter**, **▼ Descendre**, boutons pour **insérer un champ**, **aperçu** rempli, et **Messages d'origine** pour revenir aux messages livrés. Les messages sont dans `messages-cw.json`, `messages-rtty.json` et `messages-ft.json`.

### Fin de message…

La liste ajoute une abréviation à la fin du message, à la place de celle qui y est déjà : K, KN, PSE K, BK, AR, AR K, SK, 73 SK, TU 73 SK, 73 ES GD DX SK, TU EE.

---

## 14. Carnet de trafic

### Noter le QSO

La fiche est pré-remplie avec :

- l'indicatif et le prénom du correspondant ;
- la date et l'heure UTC ;
- la fréquence de la radio ;
- le mode et le sous-mode ADIF, déduits du mode de la radio et du décodeur ;
- les reports par défaut : 599 en CW et RTTY, 59 en BLU, AM et FM, vides en FT8 et FT4.

Restent à compléter : reports (si besoin), locator et commentaire.

À la validation :

1. le QSO est ajouté au fichier ADIF local `carnet.adi` (rien n'est perdu si un service manque) ;
2. il est envoyé à **Wavelog** et au **carnet qrz.com**, si les cases sont cochées (elles le sont quand Ma station… est renseigné).

La barre d'état donne le résultat : `copie locale ✓, Wavelog ✓, qrz.com ✗ <raison>`.

### Carnet…

- La liste des QSO notés, le plus récent en haut.
- **Modifier…** et **Effacer** ne touchent que la copie locale (pas Wavelog ni qrz.com). L'ancien carnet est gardé en `carnet.adi.bak` à chaque changement.
- **Actualiser**, **Ouvrir le fichier**, **Ouvrir le dossier**.

---

## 15. SSTV et fax météo

Le bouton **SSTV…** ouvre une fenêtre à part : réception à gauche, émission à droite.

### Réception SSTV

- Le décodeur reconnaît l'en-tête VIS et choisit le mode tout seul. Il se recale à chaque top de synchro, ce qui corrige la pente.
- Modes : Martin M1 et M2, Scottie S1, S2 et DX, Robot 36 et 72, PD 50, 90, 120, 160, 180, 240 et 290.
- Chaque image reçue est enregistrée toute seule dans `~/Images/SSTV`.
- **Réinitialiser** abandonne l'image en cours et attend la suivante ; **Enregistrer l'image** enregistre l'image affichée ; **Dossier** ouvre le dossier.
- **Grande fenêtre** : la réception dans une fenêtre à part.
- La liste des fréquences d'activité règle la radio : 3735 et 7165/7171 kHz en LSB, 14230 (la plus active), 14233, 21340 et 28680 kHz en USB.

### Émission SSTV

1. Choisir le **mode** (la durée de chaque mode est indiquée).
2. **Choisir une image…**
3. Remplir jusqu'à **4 lignes de texte**, chacune avec sa couleur : une ligne en grand en haut (indicatif) et trois en bas (report, prénom et QTH, locator…).
4. **Émettre l'image** : l'image est mise en audio et émise en mode data, comme un fichier.

### Fax météo

- Choisir **Fax météo** dans la liste de réception.
- Cartes WEFAX 120 lignes/min, IOC 576.
- Le décodeur attend le signal de départ, cale le bord gauche et la pente sur les lignes de phasage, puis trace la carte jusqu'au signal d'arrêt.
- **Démarrer maintenant** commence la carte sans attendre le signal de départ.
- **Inverser** donne le négatif (radio en LSB).
- **Pente** redresse une carte penchée ; elle se cale toute seule.
- La liste des **stations** règle la radio en USB, 1,9 kHz sous la fréquence publiée : DWD Pinneberg (3855, 7880, 13882,5 kHz), Northwood, Athènes, Boston.

---

## 16. Enregistrement

- **● Enreg.** enregistre le son de la radio tel quel (WAV mono 16 bits, 48 kHz) dans `~/Musique/IC-7300`. Pendant l'enregistrement, le bouton affiche la durée (■ mm:ss) et sa bulle la taille ; un clic l'arrête.
- **Dossier** ouvre le dossier des enregistrements.
- Un enregistrement peut être réémis avec l'entrée **Fichier audio**.

---

## 17. Pilotage à distance

```
 ┌───────────── PC de la radio ─────────────┐          ┌──── PC distant ────┐
 │  IC-7300 ⇄ cat_server.py  (TCP 7300)     │ ◀──────▶ │  ic7300_scope.py   │
 │          ⇄ PipeWire-Pulse (TCP 4713)     │  réseau  │     --remote <IP>  │
 └──────────────────────────────────────────┘          └────────────────────┘
```

### Sur le PC de la radio

`./install-serveur.sh` installe le service `ic7300-cat-server` et ouvre le son de PipeWire sur le réseau local.

La fenêtre **IC-7300 Serveur** :

- démarre ou arrête le serveur d'un clic ;
- affiche l'adresse à donner à l'autre PC et le PC distant connecté ;
- liste en direct les commandes transmises à la radio (fréquence, mode, PTT, texte CW…) avec leur trame CI-V. Les interrogations automatiques (S-mètre, scope…) sont masquées par défaut.

Un seul client à la fois. Le port série n'est ouvert que pendant la connexion : le reste du temps, le logiciel local peut s'en servir.

### Sur le PC distant

```bash
python3 ic7300_scope.py --remote <IP-du-PC-de-la-radio>
```

Le logiciel crée lui-même l'entrée et la sortie son de la radio distante, et les retire à la fermeture. Tout fonctionne comme en local, sauf la **pioche** (la ligne DTR ne passe pas par le réseau).

---

## 18. Sécurités

| Situation | Ce que fait le logiciel |
|---|---|
| Toute émission (bouton, PTT de la radio, pédale, VOX) | L'écoute sur le PC est coupée, pour éviter le larsen, puis remise comme on l'a laissée |
| Émission trop longue | Coupure au bout de **3 minutes**, ou de la durée du fichier + 15 s s'il est plus long |
| La radio refuse d'émettre (hors bande amateur…) | L'émission est arrêtée après 2 s, avec un message |
| Arrêt de la radio pendant une émission | L'émission est coupée d'abord |
| Plantage pendant une émission | Au lancement suivant, la radio repasse en réception et le mode data d'avant est remis |
| Fermeture du logiciel (fenêtre, Ctrl+C, `kill`, fin de session) | Émission coupée, scope de la radio remis comme avant, écoute PC rendue non muette, fréquence de la bande retenue |
| Liaison réseau coupée (`--remote`) | Le serveur remet la radio en réception en moins de 10 s |
| Connexion depuis Internet | Refusée : seuls les réseaux locaux privés peuvent piloter la radio |

Les réglages de la radio à remettre sont notés dans `etat-origine.json`. Ce fichier n'existe que tant qu'il reste quelque chose à remettre, y compris après un plantage.

---

## 19. Fichiers du logiciel

Réglages, dans `~/.config/ic7300-scope/` :

| Fichier | Contenu |
|---|---|
| `indicatif.json` | Indicatif, Ma station…, comptes qrz.com et Wavelog, choix de la pioche (droits 600) |
| `bandes.json` | Dernière fréquence et dernier mode de chaque bande |
| `memoires.json` | Mémoires du PC |
| `presets.json` | Presets |
| `messages-cw.json`, `messages-rtty.json` | Messages pré-enregistrés modifiés |
| `carnet.adi` | Carnet local (ADIF 3.1.4) |
| `etat-origine.json` | Réglages de la radio à remettre (temporaire) |
| `memoires-radio-brut.txt` | Réponses brutes des canaux de la radio, pour le diagnostic |

Autres :

| Emplacement | Contenu |
|---|---|
| `~/Musique/IC-7300/` | Enregistrements WAV |
| `~/Images/SSTV/` | Images SSTV et cartes fax reçues |
| `~/.cache/ic7300-scope/rtty-emis.wav` | Dernier message RTTY mis en audio |

Code :

| Fichier | Rôle |
|---|---|
| `ic7300_scope.py` | Fenêtre principale, CI-V, son, spectre, waterfall, émission, CW, carnet |
| `cw_decoder.py` | Décodeur CW |
| `cw_pioche.py` | Rythme de la CW à la pioche et de l'écho rouge |
| `rtty_decoder.py` | Décodeur et modulateur RTTY |
| `ft8_decoder.py` | FT8 / FT4 via `jt9` |
| `ft8_encoder.py` | Émission FT8 / FT4 : message en son (GFSK) |
| `sstv.py` · `sstv_window.py` | SSTV : codage, décodage et fenêtre |
| `wefax.py` | Fax météo |
| `memory_window.py` | Fenêtre des mémoires |
| `carnet.py` | ADIF, Wavelog, carnet qrz.com |
| `qrz.py` | Recherche d'un indicatif sur qrz.com |
| `cat_server.py` | Serveur CI-V réseau |
| `serveur_gui.py` | Fenêtre IC-7300 Serveur |

---

## 20. Raccourcis et gestes

| Où | Geste | Effet |
|---|---|---|
| Fréquence, bouton rond | Molette | Un pas |
| Spectre / waterfall | Clic | Fréquence exacte (RF) ou note du décodeur (audio) |
| Spectre / waterfall | Glisser (RF) | Accord comme un VFO ; **Maj** : 10 fois plus fin |
| Spectre / waterfall | Molette | Un pas |
| Limite spectre / waterfall | Tirer | Change leur hauteur |
| Texte décodé | Double-clic sur un indicatif | Le met en correspondant ; en FT8 / FT4, émet aussi le message suivant au prochain créneau |
| Champ CW / RTTY | Entrée | Émet |
| Champ CW / RTTY / FT | Échap | Arrête l'émission (FT : annule le message armé) |
| Grande fenêtre du décodeur | F11 / Échap | Plein écran / sortie du plein écran |
| Rappel du mode | Clic sur un mode | La radio y passe |

---

Historique des modifications : [CHANGELOG.md](CHANGELOG.md)
