"""Chemins et exécutables communs à Linux et Windows."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

WINDOWS = sys.platform == 'win32'


def config_root():
    return (os.environ.get('APPDATA') or str(Path.home() / 'AppData/Roaming')) if WINDOWS else (
        os.environ.get('XDG_CONFIG_HOME') or str(Path.home() / '.config'))


def cache_root():
    return (os.environ.get('LOCALAPPDATA') or str(Path.home() / 'AppData/Local')) if WINDOWS else (
        os.environ.get('XDG_CACHE_HOME') or str(Path.home() / '.cache'))


def user_folder(name):
    if WINDOWS:
        from PyQt5.QtCore import QStandardPaths
        kind = {'MUSIC': QStandardPaths.MusicLocation,
                'PICTURES': QStandardPaths.PicturesLocation}[name]
        return QStandardPaths.writableLocation(kind)
    try:
        result = subprocess.run(['xdg-user-dir', name], capture_output=True,
                                text=True, timeout=2).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        result = ''
    return result or str(Path.home() / {'MUSIC': 'Music', 'PICTURES': 'Pictures'}[name])


def open_folder(path):
    from PyQt5.QtCore import QUrl
    from PyQt5.QtGui import QDesktopServices
    return QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(path)))


def wsjtx_program(name):
    """PATH puis dossier WSJT-X sélectionné ou emplacements Windows habituels."""
    found = shutil.which(name)
    if found:
        return found
    if WINDOWS:
        folders = [os.environ.get('WSJTX_BIN'), r'C:\WSJT\wsjtx\bin',
                   r'C:\WSJT\bin', r'C:\WSJTX\bin',
                   str(Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'WSJT-X/bin')]
        for folder in folders:
            if folder and (Path(folder) / (name + '.exe')).is_file():
                return str(Path(folder) / (name + '.exe'))
    return None


def subprocess_options():
    return {'creationflags': subprocess.CREATE_NO_WINDOW} if WINDOWS else {}
