"""Sélection explicite des périphériques USB à chaque lancement Windows."""
import json
import os
from pathlib import Path

from PyQt5 import QtWidgets
from serial.tools import list_ports

from platform_support import config_root
import windows_audio


def configure(args):
    path = Path(config_root()) / 'ic7300-scope/windows-usb.json'
    try:
        saved = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        saved = {}
    dialog = QtWidgets.QDialog()
    dialog.setWindowTitle('IC-7300 Scope — connexion USB Windows')
    dialog.resize(720, 340)
    layout = QtWidgets.QFormLayout(dialog)
    layout.addRow(QtWidgets.QLabel('Choisissez le poste USB et les périphériques audio du PC.'))
    combos = {}
    try:
        inputs, outputs = windows_audio.devices('input'), windows_audio.devices('output')
    except Exception as exc:
        QtWidgets.QMessageBox.critical(None, 'Audio Windows', str(exc))
        return False
    ports = [(p.device, f'{p.device} — {p.description}') for p in list_ports.comports()]
    rows = [('port', 'Port COM du poste', ports),
            ('source', 'Entrée USB du poste (réception)', inputs),
            ('sink', 'Sortie USB du poste (émission)', outputs),
            ('speaker', 'Haut-parleurs / casque du PC', outputs)]
    for key, title, choices in rows:
        combo = QtWidgets.QComboBox()
        combo.addItem('— Sélectionner —', None)
        for value, description in choices:
            combo.addItem(description, value)
        # Le nom est conservé, pas l'index PortAudio qui peut changer au redémarrage.
        old = saved.get(key)
        index = combo.findText(old) if old else -1
        explicit = str(getattr(args, key, ''))
        if key == 'port' and explicit:
            index = combo.findData(explicit)
        elif key in ('source', 'sink') and explicit.isdecimal():
            index = combo.findData(explicit)
        if index < 1 and key in ('source', 'sink'):
            matches = [i for i in range(1, combo.count())
                       if 'USB AUDIO CODEC' in combo.itemText(i).upper()]
            index = matches[0] if len(matches) == 1 else -1
        if index > 0:
            combo.setCurrentIndex(index)
        combo.setMinimumContentsLength(45)
        combo.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        combos[key] = combo
        layout.addRow(title, combo)
    baud = QtWidgets.QComboBox()
    baud.addItems(['4800', '9600', '19200', '38400', '57600', '115200'])
    baud.setCurrentText(str(saved.get('baud', args.baud)))
    layout.addRow('Débit CI-V (identique au poste)', baud)
    wsjtx = QtWidgets.QLineEdit(saved.get('wsjtx', os.environ.get('WSJTX_BIN', '')))
    wsjtx.setPlaceholderText(r'Facultatif : C:\WSJT\wsjtx\bin')
    layout.addRow('Dossier bin de WSJT-X (FT8/FT4)', wsjtx)
    buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
    buttons.rejected.connect(dialog.reject)
    layout.addRow(buttons)

    def accept():
        values = {k: c.currentData() for k, c in combos.items()}
        required = ('source', 'sink', 'speaker') if args.no_cat else tuple(combos)
        if any(values[k] is None for k in required):
            QtWidgets.QMessageBox.warning(dialog, 'Périphériques', 'Sélectionnez chaque périphérique.')
            return
        if values['sink'] == values['speaker']:
            QtWidgets.QMessageBox.warning(dialog, 'Sortie PC',
                'Choisissez les haut-parleurs ou le casque du PC, distincts de la sortie USB du poste.')
            return
        try:
            # Valider l'entrée RX et la sortie TX sans émettre ni envoyer de son.
            capture = windows_audio.Capture(values['source'])
            capture.terminate()
            output = windows_audio.Output(values['sink'])
            output.close()
            engine = windows_audio.Engine(values['source'], values['sink'], values['speaker'])
        except Exception as exc:
            QtWidgets.QMessageBox.critical(dialog, 'Périphérique indisponible', str(exc))
            return
        args.source, args.sink = values['source'], values['sink']
        args.port, args.baud = values['port'] or '', int(baud.currentText())
        windows_audio.engine = engine
        if wsjtx.text().strip():
            os.environ['WSJTX_BIN'] = wsjtx.text().strip()
        else:
            os.environ.pop('WSJTX_BIN', None)
        settings = {k: c.currentText() for k, c in combos.items()}
        settings.update(baud=args.baud, wsjtx=wsjtx.text().strip())
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding='utf-8')
        except OSError as exc:
            QtWidgets.QMessageBox.warning(dialog, 'Réglages non enregistrés', str(exc))
        dialog.accept()

    buttons.accepted.connect(accept)
    return dialog.exec_() == QtWidgets.QDialog.Accepted
