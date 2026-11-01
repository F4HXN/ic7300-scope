# IC-7300 Scope Lite for Android

Commande sur le réseau local via le serveur CAT IC-7300 Scope (TCP 7300). VFO A/B, fréquence, mode, marche/arrêt, S-mètre et lecture des mémoires radio 1–99. Le serveur doit tourner sur le PC relié à la radio.

Son : `son_reseau.py` (service `reseau/ic7300-son.service`, port 7301) relaie le son reçu en PCM 16 kHz. Il tourne sur la VM 192.168.1.31 et lit PipeWire du PC de la radio. Réception seule, et il reste disponible quand l'appli du PC occupe le serveur CAT.
