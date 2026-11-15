import argparse
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
PROFILE=tempfile.TemporaryDirectory(prefix='scope-test-profile-')
os.environ.update({'QT_QPA_PLATFORM':'offscreen','HOME':PROFILE.name,
                   'XDG_CONFIG_HOME':str(Path(PROFILE.name)/'config'),
                   'XDG_CACHE_HOME':str(Path(PROFILE.name)/'cache')})
sys.path.insert(0,str(ROOT/'app'))
from PyQt5 import QtWidgets
import desktop_setup as setup
import ic7300_scope as scope

spec=importlib.util.spec_from_file_location('privacy_audit',ROOT/'packaging/audit.py')
audit=importlib.util.module_from_spec(spec);spec.loader.exec_module(audit)
QT_APP=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def arguments(**kw):
    values=dict(remote=None,source=None,sink=None,port=None,baud=None,configure=False)
    values.update(kw)
    return argparse.Namespace(**values)


class SetupTests(unittest.TestCase):
    def test_main_window_starts_with_blank_station_without_radio(self):
        args=arguments(port='/dev/ttyUSB0',source='test-rx',sink='test-tx',baud=115200)
        args.no_cat=True;args.fmax=3500
        with patch.object(scope.AudioReader,'start'), \
             patch.object(scope,'find_loopback_stream',return_value=None), \
             patch.object(scope,'list_microphones',return_value=[]), \
             patch.object(scope,'sink_volume',return_value=0), \
             patch.object(scope,'sink_hw_volume',return_value=None):
            window=scope.MainWindow(args)
            try:
                window.show();QT_APP.processEvents()
                self.assertEqual(window.callsign.text(),'')
                self.assertEqual(window.station['qth'],'')
                self.assertEqual(window.windowTitle(),'IC-7300 Scope Desktop')
                folder=ROOT/'build';folder.mkdir(exist_ok=True)
                window.grab().save(str(folder/'main-window.png'))
            finally:window.close()

    def test_flags_override_saved_connection(self):
        saved=dict(remote='radio.example',port='/dev/ttyUSB7',source='rx',sink='tx',baud=9600)
        result=setup.resolve(arguments(source='new-rx'),saved,'/dev/ttyUSB0')
        self.assertIsNone(result['remote'])
        self.assertEqual(result['source'],'new-rx')
        self.assertEqual(result['baud'],9600)
        self.assertEqual(setup.resolve(arguments(remote='other.example'),saved,'x')['remote'],'other.example')

    def test_profiles_are_separate(self):
        self.assertEqual(Path(scope.CALLSIGN_FILE).parent.name,'ic7300-scope-desktop')
        self.assertEqual(Path(scope.LOG_FILE).parent.name,'ic7300-scope-desktop')
        for key,_,value in scope.STATION_FIELDS:
            if key in {'name','qth','loc'}:self.assertEqual(value,'')

    def test_atomic_save_is_private_and_preserves_other_fields(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'profile'/'station.json'
            setup.save_json(path,{'callsign':'N0CALL','name':''})
            self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o600)
            self.assertEqual(setup.read_json(path)['callsign'],'N0CALL')
            self.assertEqual(list(path.parent.glob('.*')),[])

    def test_invalid_or_non_dict_configuration(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'bad.json';p.write_text('[]')
            self.assertEqual(setup.read_json(p),{})
            p.write_text('{')
            self.assertEqual(setup.read_json(p),{})

    def test_cancel_does_not_save_or_connect(self):
        with patch.object(setup,'read_json',return_value={}), \
             patch.object(setup,'SetupDialog') as dialog, \
             patch.object(setup,'save_json') as save:
            dialog.return_value.exec_.return_value=QtWidgets.QDialog.Rejected
            self.assertFalse(setup.configure(arguments(),'/dev/ttyUSB0'))
            save.assert_not_called()
        with patch.object(scope,'configure',return_value=False), \
             patch.object(scope,'remote_audio') as remote, \
             patch.object(scope,'local_listen') as local, \
             patch.object(scope.QtWidgets,'QApplication',return_value=QT_APP), \
             patch.object(scope,'MainWindow') as main, patch.object(sys,'argv',['scope']):
            scope.main()
            main.assert_not_called();local.assert_not_called();remote.assert_not_called()

    def test_dialog_validates_remote_and_usb(self):
        with patch.object(setup,'sound_devices',return_value=[]):
            d=setup.SetupDialog({'port':'/dev/ttyUSB0'}, {})
            d.accept();self.assertEqual(d.result(),0)
            self.assertTrue(d.error.text())
            d.mode.setCurrentIndex(1)
            d.host.setText('https://radio.example:3000')
            d.accept();self.assertEqual(d.result(),0)
            d.host.setText('radio.example')
            d.accept();self.assertEqual(d.result(),QtWidgets.QDialog.Accepted)
            self.assertIsNone(d.fields['callsign'].text() or None)

    def test_save_station_retains_optional_accounts(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(setup,'CONNECTION_FILE',Path(temp)/'connection.json'), \
             patch.object(setup,'STATION_FILE',Path(temp)/'indicatif.json'), \
             patch.object(setup,'SetupDialog') as dialog:
            setup.save_json(setup.STATION_FILE,{'qrz_user':'demo','callsign':'N0CALL'})
            dialog.return_value.exec_.return_value=QtWidgets.QDialog.Accepted
            dialog.return_value.values.return_value=dict(remote='radio.example',port='/dev/ttyUSB0',source='',sink='',baud=115200)
            field=unittest.mock.Mock();field.text.return_value='N0TEST'
            dialog.return_value.fields={'callsign':field}
            self.assertTrue(setup.configure(arguments(configure=True),'/dev/ttyUSB0'))
            self.assertEqual(setup.read_json(setup.STATION_FILE)['qrz_user'],'demo')
            self.assertEqual(setup.read_json(setup.STATION_FILE)['callsign'],'N0TEST')


class AuditTests(unittest.TestCase):
    def test_clean_application_sources(self):
        with tempfile.TemporaryDirectory() as temp:
            for source in (ROOT/'app').rglob('*'):
                if source.is_file() and '__pycache__' not in source.parts:
                    dest=Path(temp)/source.relative_to(ROOT/'app')
                    dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(source.read_bytes())
            self.assertEqual(audit.audit(Path(temp)),[])

    def test_private_data_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/'profile.py').write_text('path = "/home/demo/profile"\n')
            (root/'carnet.adi').write_text('test')
            (root/'profile.json').write_text('{}')
            self.assertEqual(len(audit.audit(root)),3)

    def test_desktop_entry_is_valid(self):
        subprocess.run(['desktop-file-validate',str(ROOT/'packaging/ic7300-scope-desktop.desktop')],check=True)

if __name__=='__main__':unittest.main()
