#!/usr/bin/env python3
"""Vérification du paquet extrait et installation dpkg dans une racine temporaire."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('deb',type=Path)
args=parser.parse_args()
deb=args.deb.resolve()


def run(command,**kwargs):
    result=subprocess.run([str(x) for x in command],capture_output=True,text=True,**kwargs)
    if result.returncode:
        sys.exit(result.stdout+'\n'+result.stderr)
    return result.stdout.strip()

with tempfile.TemporaryDirectory(prefix='scope-package-test-') as temp:
    root=Path(temp); extracted=root/'extracted';control=root/'control'
    run(['dpkg-deb','--extract',deb,extracted])
    run(['dpkg-deb','--control',deb,control])
    if set(p.name for p in control.iterdir())!={'control','md5sums'}:
        sys.exit('Métadonnées inattendues : aucun script système autorisé dans ce paquet.')
    for line in (control/'md5sums').read_text().splitlines():
        checksum,relative=line.split('  ',1)
        if hashlib.md5((extracted/relative).read_bytes()).hexdigest()!=checksum:
            sys.exit('Contenu modifié : '+relative)
    profile=root/'profile';profile.mkdir()
    env={**os.environ,'HOME':str(profile),'XDG_CONFIG_HOME':str(profile/'config'),
         'XDG_CACHE_HOME':str(profile/'cache'),'QT_QPA_PLATFORM':'offscreen',
         'PYTHONNOUSERSITE':'1','PYTHONDONTWRITEBYTECODE':'1'}
    launcher=extracted/'usr/bin/ic7300-scope-desktop'
    print(run([launcher,'--version'],env=env))
    print(run([launcher,'--self-test'],env=env))
    packaged=extracted/'usr/lib/ic7300-scope-desktop'
    env['PYTHONPATH']=str(packaged/'app')+os.pathsep+str(packaged/'runtime')
    env['IC7300_TEST_APP']=str(packaged/'app')
    print(run([sys.executable,'-B','-m','unittest','discover','-s',ROOT/'tests','-p','test_cw.py','-v'],env=env))
    print('Tests CW du moteur embarqué : OK (y compris bruit, silence et indicatifs).')
    # Dpkg reste strictement sous cette racine ; les dépendances système ont
    # été vérifiées séparément par APT et sont ignorées pour cette base vierge.
    install_root=root/'install';install_root.mkdir()
    log=root/'dpkg.log'
    command=['fakeroot','--','dpkg',f'--root={install_root}',f'--log={log}','--force-not-root','--force-depends']
    marker=install_root/'home/demo/.config/ic7300-scope-desktop/carnet.adi'
    marker.parent.mkdir(parents=True);marker.write_text('PROFILE-TO-PRESERVE\n')
    run([*command,'--install',deb])
    run([*command,'--install',deb])
    if marker.read_text()!='PROFILE-TO-PRESERVE\n':sys.exit('Profil modifié par la réinstallation')
    run([*command,'--remove','ic7300-scope-desktop'])
    if marker.read_text()!='PROFILE-TO-PRESERVE\n':sys.exit('Profil supprimé par la désinstallation')
    if (install_root/'usr/bin/ic7300-scope-desktop').exists():sys.exit('Lanceur non supprimé')
    print('Installation, réinstallation et suppression dpkg isolées : OK ; profil conservé.')
print('Paquet vérifié. Aucune installation effectuée sur le système hôte.')
