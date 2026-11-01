# IC-7300 Scope pour Windows — version USB expérimentale

Cette version utilise un IC-7300 relié directement au PC par son câble USB :
port COM pour les commandes CI-V, carte son USB pour la réception et l'émission.
Le mode distant `--remote` est réservé à Linux.

Le portage est préparé et les tests logiciels utilisent des périphériques simulés.
La validation sur un PC Windows avec un IC-7300 réel reste à effectuer, notamment
la latence audio, le FT8/FT4 et le retour RX après émission ou déconnexion USB.

## Installation

1. Utiliser un PC Windows 10/11 x64, puis installer **Python 3.12 64 bits**
   depuis [python.org](https://www.python.org/downloads/windows/), avec le lanceur `py`.
2. Installer le pilote USB correspondant à l'**IC-7300** en suivant le guide Icom
   disponible dans les téléchargements de la
   [page officielle du poste](https://www.icomjapan.com/lineup/products/IC-7300EUR/?open=1).
   Respecter l'ordre d'installation du pilote et de branchement indiqué par Icom.
3. Décompresser tout le dossier de l'application dans un répertoire accessible
   en écriture, par exemple `Documents\IC7300-Scope`.
4. Double-cliquer sur **`installer-windows.cmd`**. Une connexion Internet est
   nécessaire pour télécharger les dépendances. Elles sont installées dans
   `.venv-windows`, à côté du logiciel.
5. Brancher et allumer le poste, puis ouvrir **`lancer-windows.cmd`**.

Il s'agit d'un paquet Python avec lanceur, pas encore d'un exécutable autonome.

## Première connexion

Une fenêtre de sélection apparaît au lancement :

| Réglage | Choix |
|---|---|
| Port COM | Port du poste dans le Gestionnaire de périphériques Windows |
| Entrée USB du poste | Entrée « USB Audio CODEC » : son reçu par la radio |
| Sortie USB du poste | Sortie « USB Audio CODEC » : son envoyé à la radio |
| Haut-parleurs / casque | Sortie du PC utilisée pour écouter, différente de la sortie du poste |
| Débit CI-V | Identique au réglage `CI-V USB Baud Rate` du poste, normalement 115200 |
| Dossier WSJT-X | Facultatif ; dossier contenant `jt9.exe`, `ft8code.exe`, `ft4code.exe` |

La fenêtre conserve les choix par nom ; confirmer à chaque lancement permet
de vérifier les périphériques après un changement de prise USB.
Avec plusieurs cartes « USB Audio CODEC », vérifier leur correspondance dans
Windows avant de confirmer.

Dans l'IC-7300, reprendre les réglages USB du [guide d'installation](INSTALLATION.md#brancher-la-radio) :
CI-V USB séparé de REMOTE, vitesse identique à celle de l'application, DATA MOD
sur USB et niveau USB MOD ajusté avec l'ALC.

L'écoute PC utilise le son reçu par l'application ; il n'est pas nécessaire
d'activer « Écouter ce périphérique » dans Windows. Le bouton Muet, le volume
PC et le squelch agissent sur l'écoute, pas sur le son envoyé aux décodeurs.
Le niveau TX de l'application est un gain logiciel ; le volume de la carte
USB réglé dans Windows s'y ajoute.

## FT8 / FT4

Installer [WSJT-X pour Windows](https://wsjtx.github.io/wsjtx/downloads.html).
Indiquer son dossier d'exécutables dans la fenêtre de connexion si la détection
automatique ne les trouve pas. Les programmes sont appelés avec leur chemin
complet ; les chemins contenant des espaces sont acceptés.

Les trois outils `jt9.exe`, `ft8code.exe` et `ft4code.exe` sont nécessaires pour
toutes les fonctions FT8/FT4. Si l'un manque dans l'installation WSJT-X,
l'application signale son absence ; elle ne le télécharge pas elle-même.
Activer et vérifier la synchronisation de l'heure dans Windows : l'application
ne sait pas confirmer l'état de cette synchronisation sous Windows.

## Essai avec le poste

1. Vérifier la fréquence, le mode, le S-mètre et le spectre RF.
2. Vérifier le waterfall audio, l'écoute sur le casque, Muet et le volume PC.
3. Choisir le microphone dans Entrée TX et vérifier le bargraphe avant d'émettre.
4. Faire un essai d'émission court à faible puissance sur une charge adaptée :
   vérifier le son envoyé, l'ALC, puis le retour en réception avec ARRÊTER.
5. Vérifier ensuite un fichier audio, CW, FT8/FT4, l'enregistrement RX et l'appel CQ.

## Dépannage

- **Port COM occupé** : fermer le logiciel qui pilote déjà le poste.
- **Pas d'entrée audio** : vérifier le câble USB et l'autorisation d'accès au
  microphone pour les applications de bureau dans les paramètres Windows.
- **Format audio refusé** : régler les périphériques USB et PC sur 48 kHz dans
  les paramètres audio Windows, puis relancer. Le moteur privilégie WASAPI
  partagé et demande la conversion automatique de format.
- **Écoute PC interrompue** : sélectionner une sortie disponible ou relancer.
- **Périphérique retiré pendant l'émission** : le logiciel demande le retour RX
  lorsqu'il détecte l'erreur audio. Si le câble CI-V est lui-même débranché,
  aucune commande ne peut atteindre le poste : vérifier son état en façade.

Les réglages et le carnet sont dans `%APPDATA%\ic7300-scope`, les fichiers
temporaires dans `%LOCALAPPDATA%\ic7300-scope`. Les enregistrements sont dans
le dossier Musique de Windows, sous `IC-7300`, et les images SSTV dans Images/SSTV.

## Vérification logicielle

Les tests nécessitent `pytest` en plus des dépendances du logiciel :

```bat
.venv-windows\Scripts\python.exe -m pip install pytest
set PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
.venv-windows\Scripts\python.exe -m pytest -q tests
```

Ils couvrent le choix des périphériques, le format PCM, le volume et Muet,
la fin de lecture et les erreurs audio, les chemins WSJT-X, l'interface
Windows sans commande Linux et le relâchement du PTT sur panne simulée.
Ils ne remplacent pas les essais matériels ci-dessus.
