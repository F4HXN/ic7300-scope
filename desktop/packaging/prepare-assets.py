#!/usr/bin/env python3
"""Télécharger les artefacts figés, puis vérifier toutes les empreintes."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
lock=json.loads((ROOT/'packaging/assets.lock.json').read_text())
assets=ROOT/'assets'
if f'{sys.version_info.major}.{sys.version_info.minor}' != lock['python_minor']:
    sys.exit('Utiliser Python '+lock['python_minor']+' pour cette cible.')
(assets/'wheels').mkdir(parents=True,exist_ok=True)
subprocess.run([sys.executable,'-m','pip','download','--dest',str(assets/'wheels'),
                '--only-binary=:all:','--no-deps',*lock['packages']],check=True)
for name,checksum in lock['sha256'].items():
    path=assets/name
    if not path.is_file() and name.startswith('models/'):
        path.parent.mkdir(parents=True,exist_ok=True)
        url=f"https://raw.githubusercontent.com/e04/deepcw-engine/{lock['deepcw_revision']}/{path.name}"
        with urllib.request.urlopen(url,timeout=90) as response:
            data=response.read()
        if hashlib.sha256(data).hexdigest()!=checksum:
            sys.exit('Empreinte incorrecte : '+name)
        path.write_bytes(data)
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=checksum:
        sys.exit('Artefact manquant ou modifié : '+name)
print('Artefacts prêts et empreintes vérifiées. La construction sera hors ligne.')
