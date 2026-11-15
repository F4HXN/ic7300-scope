"""Configuration de premier lancement ; aucune connexion n'est ouverte avant validation."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

from PyQt5 import QtWidgets
from platform_support import config_root

APP_ID = 'ic7300-scope-desktop' if Path(__file__).resolve().parent.name == 'app' else 'ic7300-scope'
CONFIG_DIR = Path(config_root()) / APP_ID
CONNECTION_FILE = CONFIG_DIR / 'connection.json'
STATION_FILE = CONFIG_DIR / 'indicatif.json'


def read_json(path):
    try:
        value=json.loads(path.read_text())
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError): return {}


def save_json(path, value):
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    descriptor, name=tempfile.mkstemp(prefix='.'+path.name,dir=path.parent)
    try:
        with os.fdopen(descriptor,'w',encoding='utf-8') as stream:
            json.dump(value,stream,ensure_ascii=False,indent=2)
            stream.write('\n')
        os.replace(name,path)
    finally:
        if os.path.exists(name): os.unlink(name)


def sound_devices(kind):
    try:
        result=subprocess.run(['pactl','-f','json','list',kind],capture_output=True,
                              text=True,timeout=3,check=True)
        return [(x['name'],x.get('description',x['name'])) for x in json.loads(result.stdout)]
    except (OSError,ValueError,KeyError,subprocess.SubprocessError): return []


def resolve(args, saved, default_port):
    """Les options explicites priment ; un port/source explicite implique USB local."""
    explicit_local=any(getattr(args,key,None) is not None for key in ('port','source','sink'))
    remote=args.remote if args.remote is not None else (None if explicit_local else saved.get('remote'))
    return {'remote':remote or None,
            'source':args.source if args.source is not None else saved.get('source',''),
            'sink':args.sink if args.sink is not None else saved.get('sink',''),
            'port':args.port if args.port is not None else saved.get('port',default_port),
            'baud':args.baud if args.baud is not None else saved.get('baud',115200)}


class SetupDialog(QtWidgets.QDialog):
    def __init__(self, connection, station, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Configurer IC-7300 Scope Desktop')
        self.resize(620,480)
        form=QtWidgets.QFormLayout(self)
        intro=QtWidgets.QLabel('Choisissez votre radio et votre station. Les informations restent sur ce PC.\n'
                              'Les comptes QRZ et Wavelog sont facultatifs, dans « Ma station… ».')
        intro.setWordWrap(True)
        form.addRow(intro)
        self.mode=QtWidgets.QComboBox()
        self.mode.addItems(['Câble USB sur ce PC','Radio sur un PC distant'])
        self.mode.setCurrentIndex(1 if connection.get('remote') else 0)
        form.addRow('Connexion',self.mode)
        self.host=QtWidgets.QLineEdit(connection.get('remote') or '')
        self.host.setPlaceholderText('Nom ou adresse du PC relié à la radio')
        form.addRow('PC distant',self.host)
        self.port=QtWidgets.QComboBox();self.port.setEditable(True)
        try:
            from serial.tools.list_ports import comports
            for port in comports(): self.port.addItem(port.device)
        except OSError: pass
        self.port.setCurrentText(connection.get('port') or '/dev/ttyUSB0')
        form.addRow('Port CI-V',self.port)
        self.baud=QtWidgets.QComboBox()
        self.baud.addItems(['9600','19200','38400','57600','115200'])
        self.baud.setCurrentText(str(connection.get('baud',115200)))
        form.addRow('Vitesse CI-V',self.baud)
        self.source,self.sink=QtWidgets.QComboBox(),QtWidgets.QComboBox()
        for widget,kind,key in [(self.source,'sources','source'),(self.sink,'sinks','sink')]:
            widget.setEditable(True)
            for name,label in sound_devices(kind): widget.addItem(name)
            widget.setCurrentText(connection.get(key) or '')
        self.source.setToolTip('Entrée USB Audio CODEC de la radio, pas le microphone du PC.')
        self.sink.setToolTip('Sortie USB Audio CODEC de la radio, pas les haut-parleurs du PC.')
        form.addRow('Entrée audio radio',self.source)
        form.addRow('Sortie audio radio',self.sink)
        self.fields={}
        for key,label in [('callsign','Indicatif (facultatif)'),('name','Prénom'),('qth','QTH'),('loc','Locator')]:
            widget=QtWidgets.QLineEdit(station.get(key,''))
            form.addRow(label,widget);self.fields[key]=widget
        self.error=QtWidgets.QLabel();self.error.setStyleSheet('color: #ed7777')
        self.error.setWordWrap(True);form.addRow(self.error)
        buttons=QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Save|QtWidgets.QDialogButtonBox.Cancel)
        buttons.button(QtWidgets.QDialogButtonBox.Save).setText('Enregistrer')
        buttons.button(QtWidgets.QDialogButtonBox.Cancel).setText('Annuler')
        buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.mode.currentIndexChanged.connect(self.update_mode)
        self.update_mode()

    def update_mode(self):
        remote=self.mode.currentIndex()==1
        self.host.setEnabled(remote)
        for widget in (self.port,self.baud,self.source,self.sink):widget.setEnabled(not remote)

    def values(self):
        remote=self.mode.currentIndex()==1
        return {'remote':self.host.text().strip() if remote else None,
                'port':self.port.currentText().strip(), 'baud':int(self.baud.currentText()),
                'source':self.source.currentText().strip(), 'sink':self.sink.currentText().strip()}

    def accept(self):
        values=self.values()
        if self.mode.currentIndex()==1:
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*',values['remote'] or ''):
                self.error.setText('Indiquez un nom de machine ou une adresse IPv4, sans protocole ni port.')
                return
        elif not values['port'] or not values['source'] or not values['sink']:
            self.error.setText('Choisissez le port CI-V et les deux périphériques audio de la radio.')
            return
        super().accept()


def configure(args, default_port):
    saved=read_json(CONNECTION_FILE)
    values=resolve(args,saved,default_port)
    if not saved or args.configure:
        station=read_json(STATION_FILE)
        dialog=SetupDialog(values,station)
        if dialog.exec_()!=QtWidgets.QDialog.Accepted:return False
        values=dialog.values()
        station.update({key:field.text().strip().upper() for key,field in dialog.fields.items()})
        try:
            save_json(STATION_FILE,station)
            save_json(CONNECTION_FILE,values)
        except OSError as exc:
            QtWidgets.QMessageBox.critical(None,'Configuration non enregistrée',str(exc))
            return False
    for key,value in values.items():setattr(args,key,value)
    return True


def current_connection(args, default_port):
    saved=read_json(CONNECTION_FILE)
    remote=getattr(args,'remote',None)
    return {'remote':remote or None,
            'port':saved.get('port',default_port) if remote else args.port,
            'source':saved.get('source','') if remote else args.source,
            'sink':saved.get('sink','') if remote else args.sink,
            'baud':args.baud}


def edit_settings(connection, station, parent=None):
    dialog=SetupDialog(connection,station,parent)
    if dialog.exec_()!=QtWidgets.QDialog.Accepted:return None
    values=dialog.values()
    updated=dict(station)
    updated.update({key:field.text().strip().upper() for key,field in dialog.fields.items()})
    try:
        save_json(STATION_FILE,updated)
        save_json(CONNECTION_FILE,values)
    except OSError as exc:
        QtWidgets.QMessageBox.critical(parent,'Configuration non enregistrée',str(exc))
        return None
    return values,updated


def restore_connection(args, default_port, default_source, default_sink):
    values=resolve(args,read_json(CONNECTION_FILE),default_port)
    values['port']=values['port'] or default_port
    values['source']=values['source'] or default_source
    values['sink']=values['sink'] or default_sink
    for key,value in values.items():setattr(args,key,value)
