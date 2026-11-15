#!/usr/bin/env python3
"""Construction hors ligne à partir d'une liste de sources et d'artefacts vérifiés."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT=Path(__file__).resolve().parents[1]
APP_FILES=json.loads((ROOT/'SOURCE.json').read_text())['source_sha256']
lock=json.loads((ROOT/'packaging/assets.lock.json').read_text())
spec=importlib.util.spec_from_file_location('privacy_audit',ROOT/'packaging/audit.py')
audit=importlib.util.module_from_spec(spec);spec.loader.exec_module(audit)


def copy_file(source,dest):
    dest.parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(source,dest)


def build():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'dist')
    args=parser.parse_args()
    if sys.platform!='linux' or subprocess.check_output(['dpkg','--print-architecture'],text=True).strip()!=lock['architecture']:
        sys.exit('Construire sous Linux '+lock['architecture'])
    if f'{sys.version_info.major}.{sys.version_info.minor}'!=lock['python_minor']:
        sys.exit('Cette cible exige Python '+lock['python_minor'])
    version=(ROOT/'VERSION').read_text().strip()
    if not version or any(c not in '0123456789.+~-' for c in version):
        sys.exit('Version invalide')
    for name,checksum in lock['sha256'].items():
        path=ROOT/'assets'/name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=checksum:
            sys.exit('Artefact absent ou modifié : '+name+' ; lancer packaging/prepare-assets.py')
    args.output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='scope-deb-') as temp:
        stage=Path(temp)
        lib=stage/'usr/lib/ic7300-scope-desktop'
        names=[*APP_FILES,*[str(p.relative_to(ROOT/'distribution/files')) for p in (ROOT/'distribution/files').rglob('*') if p.is_file()]]
        for name in names:
            source=ROOT/'app'/name
            if source.is_symlink():sys.exit('Source symbolique interdite : '+name)
            copy_file(source,lib/'app'/name)
        for name in ('model.onnx','model.onnx.json'):
            copy_file(ROOT/'assets/models/deepcw'/name,lib/'app/models/deepcw'/name)
        runtime=lib/'runtime'
        for name in lock['sha256']:
            if not name.endswith('.whl'):continue
            with zipfile.ZipFile(ROOT/'assets'/name) as wheel:
                for item in wheel.infolist():
                    rel=PurePosixPath(item.filename)
                    if item.is_dir():continue
                    if rel.is_absolute() or '..' in rel.parts:sys.exit('Chemin wheel interdit')
                    if '__pycache__' in rel.parts or rel.suffix in {'.pyc','.pyo'}:continue
                    # Runtime d'inférence uniquement, sans exemples, datasets ou outils ONNX.
                    if rel.parts[0]=='onnxruntime' and len(rel.parts)>2 and rel.parts[1] not in {'capi'}:continue
                    destination=runtime/str(rel)
                    destination.parent.mkdir(parents=True,exist_ok=True)
                    destination.write_bytes(wheel.read(item))
        copy_file(ROOT/'VERSION',lib/'VERSION')
        copy_file(ROOT/'AUTHORS',lib/'AUTHORS')
        copy_file(ROOT/'PROJECT_LICENSE',lib/'PROJECT_LICENSE')
        metainfo=(ROOT/'packaging/io.github.f4hxn.IC7300Scope.metainfo.xml.in').read_text()
        metainfo=metainfo.replace('@PROJECT_LICENSE@',(ROOT/'PROJECT_LICENSE').read_text().strip()).replace('@VERSION@',version)
        metadata=stage/'usr/share/metainfo/io.github.f4hxn.IC7300Scope.metainfo.xml'
        metadata.parent.mkdir(parents=True,exist_ok=True)
        metadata.write_text(metainfo)
        subprocess.run(['appstreamcli','validate','--no-net',str(metadata)],check=True)
        launcher=stage/'usr/bin/ic7300-scope-desktop'
        copy_file(ROOT/'packaging/launcher.py',launcher)
        copy_file(ROOT/'packaging/ic7300-scope-desktop.desktop',stage/'usr/share/applications/ic7300-scope-desktop.desktop')
        copy_file(ROOT/'app/icon.svg',stage/'usr/share/icons/hicolor/scalable/apps/ic7300-scope-desktop.svg')
        doc=stage/'usr/share/doc/ic7300-scope-desktop'
        copy_file(ROOT/'README.md',doc/'README.md')
        copy_file(ROOT/'packaging/copyright',doc/'copyright')
        copy_file(ROOT/'AUTHORS',doc/'AUTHORS')
        copy_file(ROOT/'packaging/licenses/FSFAP',doc/'FSFAP')
        if (ROOT/'LICENSE').is_file():
            copy_file(ROOT/'LICENSE',doc/'LICENSE')
        copy_file(ROOT/'packaging/licenses/flatbuffers.LICENSE',
                  runtime/'flatbuffers-25.12.19.dist-info/LICENSE.txt')
        # Audit du contenu embarqué, y compris les sources des bibliothèques tierces.
        errors=audit.audit(stage)
        if errors:sys.exit('\n'.join(errors))
        debian=stage/'DEBIAN';debian.mkdir()
        installed_size=sum(p.stat().st_size for p in stage.rglob('*') if p.is_file())//1024
        upper_minor='.'.join([lock['python_minor'].split('.')[0],str(int(lock['python_minor'].split('.')[1])+1)])
        control=f'''Package: ic7300-scope-desktop
Version: {version}-1ubuntu{lock['ubuntu']}
Section: hamradio
Priority: optional
Architecture: {lock['architecture']}
Maintainer: F4HXN <F4HXN@users.noreply.github.com>
Homepage: https://github.com/F4HXN/ic7300-scope
Installed-Size: {installed_size}
Depends: python3 (>= {lock['python_minor']}), python3 (<< {upper_minor}), python3-pyqt5, python3-pyqtgraph, python3-numpy (>= 1.24), python3-scipy, python3-serial, python3-packaging, pulseaudio-utils, xdg-user-dirs, libc6 (>= 2.28), libstdc++6, libgcc-s1
Recommends: wsjtx
Description: Interface IC-7300 avec spectre et decodage CW local
 Spectre audio et RF, waterfall, CW DeepCW, RTTY, FT8/FT4 via WSJT-X,
 SSTV et carnet de trafic. Configuration de station vierge au premier
 lancement. Modele DeepCW et moteur ONNX Runtime CPU inclus.
 Construction pour Ubuntu {lock['ubuntu']} {lock['architecture']} et Python {lock['python_minor']}.
'''
        (debian/'control').write_text(control)
        sums=[]
        for path in sorted(stage.rglob('*')):
            if path.is_file() and 'DEBIAN' not in path.relative_to(stage).parts:
                sums.append(hashlib.md5(path.read_bytes()).hexdigest()+'  '+str(path.relative_to(stage)))
        (debian/'md5sums').write_text('\n'.join(sums)+'\n')
        epoch=int(os.environ.get('SOURCE_DATE_EPOCH','1794700800'))
        for path in stage.rglob('*'):
            path.chmod(0o755 if path.is_dir() or path==launcher else 0o644)
            os.utime(path,(epoch,epoch))
        os.utime(stage,(epoch,epoch))
        artifact=args.output/f"ic7300-scope-desktop_{version}-1ubuntu{lock['ubuntu']}_{lock['architecture']}.deb"
        subprocess.run(['dpkg-deb','--root-owner-group','-Zxz','--build',str(stage),str(artifact)],check=True,
                       env={**os.environ,'SOURCE_DATE_EPOCH':str(epoch)})
        checksum=hashlib.sha256(artifact.read_bytes()).hexdigest()
        artifact.with_suffix('.deb.sha256').write_text(checksum+'  '+artifact.name+'\n')
        print(artifact)
        print(f'{artifact.stat().st_size/1024/1024:.1f} Mio ; SHA-256 {checksum}')

if __name__=='__main__':build()
