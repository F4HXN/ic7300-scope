# IC-7300 État (Android)

Affiche l'état du serveur IC-7300 (PC de la radio, 192.168.1.37) : qui pilote la radio
(PC distant, appli locale ou personne), serveur CAT, radio USB, son réseau, fréquence,
mode, S-mètre, émission et dernières commandes CI-V. Rafraîchi toutes les 2 s.

Lit seulement `etat_serveur.py` (port 7301) sur le PC de la radio : l'appli ne se
connecte jamais au port 7300 et ne touche pas à la radio.

Sur le PC de la radio :

```
cp reseau/ic7300-etat.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now ic7300-etat
```

Construire et installer :

```
JAVA_HOME=~/Android/jdk ./gradlew assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

L'adresse du service se change dans l'appli (⚙).
