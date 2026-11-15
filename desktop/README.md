# IC-7300 Scope Desktop

Auteur : **F4HXN**. Code de l’application sous **[licence MIT](LICENSE)**.
Les composants tiers conservent leurs licences et leurs crédits.

Version de distribution Linux pour l'Icom IC-7300 : spectre RF/audio, waterfall,
CW DeepCW, RTTY, FT8/FT4 via WSJT-X, SSTV et carnet de trafic.

Le paquet fourni cible **Ubuntu 26.04 amd64, Python 3.14**. Il contient le modèle
DeepCW et ONNX Runtime CPU. Les bibliothèques Qt, NumPy, SciPy, pyserial et audio
sont des dépendances Ubuntu ; APT peut devoir les télécharger à l'installation.
WSJT-X est recommandé pour le FT8/FT4. Aucun téléchargement de modèle n'est
nécessaire au lancement. Le décodage CW fonctionne localement.

## Installer et lancer

```sh
sudo apt install ./ic7300-scope-desktop_1.0.1-1ubuntu26.04_amd64.deb
ic7300-scope-desktop
```

Une entrée « IC-7300 Scope Desktop » est ajoutée au menu. Le paquet contient
les métadonnées AppStream (auteur, licence, description et lanceur) utilisées
par les logithèques. L’auteur et la licence sont aussi dans **Aide → À propos…**.
Avec **Discover 6.6.6**, la fiche d’un `.deb` local a deux limites vérifiées :

- Le libellé « Installer depuis… » contient le chemin complet du fichier. À la
  largeur par défaut (936 px dans le test), le bouton est masqué par manque de
  place ; il devient visible et actif en élargissant ou maximisant la fenêtre.
- Cette fiche utilise les détails génériques de PackageKit et ne lit pas le
  fichier AppStream embarqué : l’auteur reste vide et la licence peut être
  « inconnue ». Le paquet contient bien F4HXN, la licence MIT et les métadonnées
  AppStream validées. Ils sont aussi consultables dans **Aide → À propos…**.

La simulation d’installation PackageKit réussit. Ces limites d’affichage ne
signifient pas un rejet du paquet. La commande APT ci-dessus permet également
l’installation directe. Une fiche complète en logithèque avant installation
nécessite une publication dans un catalogue de dépôt avec métadonnées AppStream.

Au premier lancement,
choisir le port CI-V et les périphériques USB Audio CODEC, ou le nom/l'adresse
du PC distant déjà configuré pour partager la radio. La station est vierge ;
l'indicatif, le prénom, le QTH et le locator sont à renseigner. Annuler ferme
l'application sans ouvrir la radio.

La vitesse CI-V doit correspondre à celle réglée sur le poste. Pour l'accès au
port série, l'utilisateur doit appartenir au groupe approprié (souvent dialout).
Ce paquet ne change pas les groupes, le pare-feu ou les services du système.
Il n'installe pas automatiquement de serveur CAT ou de partage audio distant.

Pour refaire la configuration, fermer la fenêtre en cours puis utiliser
l'action « Configurer la connexion » du menu, ou :

```sh
ic7300-scope-desktop --configure
```

Les réglages, comptes et le carnet sont conservés dans
`~/.config/ic7300-scope-desktop/`, séparément de la version de développement.
Les enregistrements et images sont placés dans les dossiers utilisateur Musique
et Images, sous « IC-7300 Scope Desktop ». Les mises à jour et désinstallations
ne suppriment pas ces données. Aucune migration automatique du profil existant
n'est réalisée. Ne pas lancer deux applications qui pilotent la même radio.

QRZ et Wavelog ne sont utilisés que si l'utilisateur configure ces services.
Leurs informations restent dans son profil et ne sont jamais incluses dans
la construction. Les licences et crédits des bibliothèques tierces sont conservés.

## Mettre à jour une installation

Installer un paquet de version supérieure avec `sudo apt install ./nouvelle-version.deb`.
Il n'y a pas de dépôt APT ni de mise à jour automatique configurés. Pour revenir
en arrière, fermer l'application et réinstaller le paquet précédent avec
`sudo apt install --allow-downgrades ./ancienne-version.deb`.

## Construire le paquet

Sur la cible Ubuntu/Python indiquée dans `packaging/assets.lock.json` :

```sh
python3 packaging/prepare-assets.py  # une fois : téléchargements figés et vérifiés
./packaging/build-deb.sh            # ensuite : construction hors ligne, sans sudo
```

Le `.deb` et son empreinte SHA-256 se trouvent dans `dist/`. `VERSION` contient
la version de l'application. Pour modifier le programme, éditer `app/`, augmenter
`VERSION`, tester et relancer la construction. Le paquet n'embarque ni Git,
profil utilisateur, carnet, capture d'écran, enregistrement, fichier temporaire,
ni ancien historique du dépôt de développement. La construction utilise une
liste explicite de fichiers et un audit ; le contrôle automatique ne remplace
pas la revue des modifications avant publication.

## Importer une amélioration du programme de développement

`SOURCE.json` indique le commit importé et les empreintes des fichiers sources.
Le dépôt de distribution a son propre historique neuf. Les modifications propres
à la distribution sont gardées dans `distribution/desktop.patch` et les nouveaux
modules dans `distribution/files/`.

```sh
# Enregistrer les adaptations faites à app/ avant de réimporter :
python3 packaging/save-overlay.py /chemin/vers/le/depot-developpement
# Importer ensuite un commit ou un tag explicite :
python3 packaging/sync-upstream.py /chemin/vers/le/depot-developpement --ref COMMIT_OU_TAG
```

L'import lit seulement les fichiers suivis à la révision demandée. Les valeurs
de station sont neutralisées avant application du patch. Un conflit ou un audit
en échec arrête l'import avant de remplacer les sources. Après import, relire
les différences, mettre à jour `VERSION`, tester, construire et distribuer le
nouveau paquet. Pour un nouveau modèle CW, mettre à jour les versions/empreintes
dans `packaging/assets.lock.json` après vérification et refaire les tests.

## Vérifications

```sh
python3 -B -m unittest discover -s tests -v
ic7300-scope-desktop --version
ic7300-scope-desktop --self-test
python3 packaging/verify-deb.py dist/ic7300-scope-desktop_1.0.1-1ubuntu26.04_amd64.deb
```

La vérification complète du paquet exige `fakeroot` et `dpkg`. Elle installe,
réinstalle et supprime le paquet uniquement dans une racine temporaire, sans
modifier l'installation hôte, et vérifie la conservation d'un profil témoin.

Les tests d'interface utilisent Qt en mode offscreen et un profil temporaire.
Le self-test charge réellement le modèle sans ouvrir de port série ni lire
le profil utilisateur. La validation sur une radio et une installation système
réelle reste distincte des tests d'un paquet extrait.
