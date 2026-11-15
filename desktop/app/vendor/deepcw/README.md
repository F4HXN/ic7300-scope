# DeepCW embarqué

`engine.py` est une copie sans modification de `examples/python/decode_morse.py`
du projet https://github.com/e04/deepcw-engine, commit
`79104fbe1ea0abd09eb27c6ebf9c6bab364d0d7f`, sous licence MIT (voir LICENSE).

Le modèle et ses métadonnées sont téléchargés séparément par install-cw.sh,
avec vérification SHA-256 et version figée. Ils ne sont pas dans Git.
L'adaptateur cw_neural.py ajoute l'exécution asynchrone, la sélection de la note,
une mémoire bornée et le repli sur le décodeur classique.
