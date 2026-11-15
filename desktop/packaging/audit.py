#!/usr/bin/env python3
"""Contrôle des sources exportées et du contenu du paquet, sans lire le profil utilisateur."""
import ast
from pathlib import Path
import re
import sys

PATTERNS = [
    (r'/home/[^/\s"\']+', 'chemin utilisateur absolu'),
    (r'\b[A-Za-z0-9._%+-]+@(?:gmail|hotmail|outlook|orange|free)\.[A-Za-z]+', 'adresse personnelle'),
    (r'\b192\.168\.(?!0\.0/16)[0-9]+\.[0-9]+\b', 'adresse réseau privée spécifique'),
    (r'\b(?:10\.(?:[0-9]+\.){2}[0-9]+|172\.(?:1[6-9]|2[0-9]|3[01])\.[0-9]+\.[0-9]+)\b(?!/)', 'adresse réseau privée spécifique'),
    (r'-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----', 'clé privée'),
    (r'(?i)(?:password|api_key|qrz_password|wavelog_key|qrz_logbook_key)\s*[=:]\s*["\'][^"\'\s]{8,}["\']', 'secret probable'),
]
FORBIDDEN_SUFFIXES = {'.adi','.adif','.wav','.mp3','.sqlite','.sqlite3','.log','.pcap','.pem'}
ALLOWED_JSON = {'model.onnx.json'}


def audit(root):
    errors=[]
    for path in root.rglob('*'):
        relative=path.relative_to(root)
        if path.is_symlink():
            errors.append(f'{relative}: lien symbolique inattendu')
            continue
        if not path.is_file(): continue
        if any(part in {'.git','__pycache__'} for part in relative.parts):
            errors.append(f'{relative}: fichier local interdit')
        if path.suffix.lower() in FORBIDDEN_SUFFIXES:
            errors.append(f'{relative}: données utilisateur interdites')
        if path.suffix=='.json' and path.name not in ALLOWED_JSON:
            errors.append(f'{relative}: JSON utilisateur ou inconnu')
        if path.suffix not in {'.py','.svg','.md','.txt','.desktop','.sh','.json'} and path.name not in {'LICENSE','copyright','control'}:
            continue
        try: text=path.read_text(encoding='utf-8')
        except UnicodeError: continue
        for expression,label in PATTERNS:
            # Les notices tierces conservent les coordonnées de leurs ayants droit.
            if path.name == 'ThirdPartyNotices.txt' and label == 'adresse personnelle':
                continue
            if re.search(expression,text): errors.append(f'{relative}: {label}')
        # Les valeurs personnelles de départ doivent être vides.
        if path.name=='ic7300_scope.py':
            tree=ast.parse(text)
            for node in tree.body:
                if isinstance(node,ast.Assign) and any(isinstance(x,ast.Name) and x.id=='STATION_FIELDS' for x in node.targets):
                    for key,_,value in ast.literal_eval(node.value):
                        if key in {'name','qth','loc'} and value:
                            errors.append(f'{relative}: valeur personnelle par défaut ({key})')
    return errors

if __name__=='__main__':
    errors=audit(Path(sys.argv[1]))
    if errors:
        print('\n'.join(errors),file=sys.stderr)
        sys.exit(1)
    print('Audit : aucun profil, journal, secret détecté ou valeur personnelle par défaut.')
