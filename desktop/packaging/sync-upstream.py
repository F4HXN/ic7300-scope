#!/usr/bin/env python3
"""Importer une révision explicite, normaliser, puis appliquer l'adaptation desktop."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
FILES = ['ic7300_scope.py', 'platform_support.py', 'cw_decoder.py', 'cw_neural.py',
         'cw_pioche.py', 'rtty_decoder.py', 'ft8_decoder.py', 'ft8_encoder.py',
         'carnet.py', 'qrz.py', 'cat_server.py', 'memory_window.py', 'sstv_window.py',
         'sstv.py', 'wefax.py', 'look_radio.py', 'icon.svg',
         'vendor/deepcw/engine.py', 'vendor/deepcw/LICENSE', 'vendor/deepcw/README.md']


PERSONAL_DEFAULTS = {}

def normalize(text):
    if 'STATION_FIELDS =' in text:
        for node in ast.parse(text).body:
            if isinstance(node, ast.Assign) and any(isinstance(x, ast.Name) and x.id == 'STATION_FIELDS' for x in node.targets):
                for key, _, value in ast.literal_eval(node.value):
                    if key in {'name','qth','loc'} and value:
                        PERSONAL_DEFAULTS[value] = {'name':'PRENOM','qth':'VILLE','loc':'AA00AA'}[key]
    for value, generic in PERSONAL_DEFAULTS.items():
        text = text.replace(value, generic)
    # Aucun nom propre ni ancienne valeur personnelle n'est stocké dans ces règles.
    text = re.sub(r'(\("(?:name|qth|loc)",\s*"[^"]*",\s*)"[^"]*"', r'\1""', text)
    text = re.sub(r'IC-7300 Scope by [A-Z0-9/]+', 'IC-7300 Scope Desktop', text)
    text = re.sub(r'\b[A-Z]{1,2}[0-9][A-Z]{2,3}\b', 'N0CALL', text)
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path, help='dépôt de développement local')
    parser.add_argument('--ref', required=True, help='commit ou tag à importer')
    args = parser.parse_args()
    def git(*parts):
        return subprocess.check_output(['git', '-C', str(args.source), *parts])
    commit = git('rev-parse', args.ref + '^{commit}').decode().strip()
    with tempfile.TemporaryDirectory(prefix='scope-import-') as temp:
        stage = Path(temp)
        hashes = {}
        for name in FILES:
            raw = git('show', f'{commit}:{name}')
            hashes[name] = hashlib.sha256(raw).hexdigest()
            path = stage / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(normalize(raw.decode()), encoding='utf-8')
        patch = ROOT / 'distribution/desktop.patch'
        if patch.is_file():
            for mode in ('--check', '--apply'):
                command = ['git', 'apply', '--no-index']
                if mode == '--check': command.append('--check')
                subprocess.run([*command, str(patch)], cwd=stage, check=True)
        for path in (ROOT / 'distribution/files').rglob('*'):
            if path.is_file():
                dest = stage / path.relative_to(ROOT / 'distribution/files')
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, dest)
        # Audit avant remplacement : un import non neutre ne remplace pas l'app.
        subprocess.run(['python3', str(ROOT/'packaging/audit.py'), str(stage)], check=True)
        app = ROOT / 'app'
        app.mkdir(exist_ok=True)
        # Remplacer seulement les fichiers gérés, jamais une configuration utilisateur.
        for path in stage.rglob('*'):
            if path.is_file():
                dest = app / path.relative_to(stage)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, dest)
        manifest = {'source_commit':commit, 'source_sha256':hashes}
        (ROOT/'SOURCE.json').write_text(json.dumps(manifest, indent=2)+'\n')
    print('Sources importées ; revue, tests et construction requis avant publication.')

if __name__ == '__main__': main()
