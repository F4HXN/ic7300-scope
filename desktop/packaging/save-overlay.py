#!/usr/bin/env python3
"""Enregistrer les adaptations des fichiers importés, sans identité amont."""
import argparse
import difflib
import importlib.util
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('upstream_import',ROOT/'packaging/sync-upstream.py')
sync=importlib.util.module_from_spec(spec);spec.loader.exec_module(sync)
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('source',type=Path)
args=parser.parse_args()
commit=json.loads((ROOT/'SOURCE.json').read_text())['source_commit']
patch=''
for name in sync.FILES:
    before=subprocess.check_output(['git','-C',str(args.source),'show',f'{commit}:{name}']).decode()
    before=sync.normalize(before)
    after=(ROOT/'app'/name).read_text()
    patch+=''.join(difflib.unified_diff(before.splitlines(True),after.splitlines(True),
                                      fromfile='a/'+name,tofile='b/'+name))
(ROOT/'distribution/desktop.patch').write_text(patch)
for source in (ROOT/'distribution/files').rglob('*'):
    if source.is_file():source.write_bytes((ROOT/'app'/source.relative_to(ROOT/'distribution/files')).read_bytes())
print('Adaptations sauvegardées. Vérifier le diff avant commit.')
