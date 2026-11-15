#!/usr/bin/python3 -s
"""Entrée installée ; chemins calculés pour tester aussi un paquet extrait."""
import os
from pathlib import Path
import runpy
import sys

prefix=Path(__file__).resolve().parents[1]
root=prefix/'lib/ic7300-scope-desktop'
sys.path[:0]=[str(root/'app'),str(root/'runtime')]
os.environ.setdefault('PYTHONDONTWRITEBYTECODE','1')
sys.dont_write_bytecode=True
if '--version' in sys.argv[1:]:
    print('IC-7300 Scope Desktop '+(root/'VERSION').read_text().strip())
elif '--self-test' in sys.argv[1:]:
    runpy.run_module('desktop_selftest',run_name='__main__')
else:
    runpy.run_path(str(root/'app/ic7300_scope.py'),run_name='__main__')
