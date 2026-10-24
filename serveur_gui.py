#!/usr/bin/env python3
"""Fenêtre « IC-7300 Serveur » : marche/arrêt du serveur CAT et surveillance.

Démarre ou arrête le service ic7300-cat-server, montre le PC distant connecté
et la liste des commandes CI-V qui passent entre lui et la radio (lue sur le
socket de surveillance de cat_server.py).
"""
import json
import os
import subprocess
import sys
import time
from collections import deque

from PyQt5 import QtCore, QtGui, QtNetwork, QtWidgets

from cat_server import MONITOR_PATH

DIR = os.path.dirname(os.path.abspath(__file__))
SERVICE = "ic7300-cat-server.service"
MAX_ROWS = 3000
SHOWN_ROWS = 1000             # lignes dans le tableau : au-delà, Qt rame
GREEN, RED, GREY = "#3ddc84", "#ef5350", "#78909c"

MODES = {0x00: "LSB", 0x01: "USB", 0x02: "AM", 0x03: "CW", 0x04: "RTTY",
         0x05: "FM", 0x07: "CW-R", 0x08: "RTTY-R"}
# Commandes avec sous-commande : le nom dépend des deux octets.
WITH_SUB = {0x14, 0x15, 0x16, 0x1A, 0x1C, 0x27}
NAMES = {
    (0x00,): "Fréquence (radio)", (0x01,): "Mode (radio)",
    (0x03,): "Fréquence", (0x04,): "Mode", (0x05,): "Régler la fréquence",
    (0x06,): "Régler le mode", (0x07,): "VFO", (0x0F,): "Split",
    (0x14, 0x01): "Volume AF", (0x14, 0x02): "Gain RF", (0x14, 0x03): "Squelch",
    (0x14, 0x0A): "Puissance RF", (0x14, 0x0C): "Vitesse CW",
    (0x15, 0x01): "Squelch ouvert", (0x15, 0x02): "S-mètre",
    (0x15, 0x11): "Puissance émise", (0x15, 0x12): "ROS", (0x15, 0x13): "ALC",
    (0x15, 0x14): "Compression", (0x15, 0x16): "Courant",
    (0x16, 0x47): "Break-in", (0x17,): "Texte CW", (0x18,): "Marche/arrêt",
    (0x19,): "Identification", (0x1A, 0x00): "Mémoire", (0x1A, 0x05): "Réglage",
    (0x1A, 0x06): "Mode data", (0x1C, 0x00): "PTT", (0x1C, 0x01): "Boîte d'accord",
    (0x25,): "Fréquence VFO", (0x26,): "Mode VFO",
    (0x27, 0x00): "Données du scope", (0x27, 0x10): "Scope",
    (0x27, 0x11): "Envoi du scope", (0x27, 0x14): "Mode du scope",
    (0x27, 0x15): "Largeur du scope", (0x27, 0x1A): "Vitesse du scope",
    (0xFB,): "OK", (0xFA,): "Refusé",
}


def bcd_frequency(data):
    return sum(((b >> 4) * 10 + (b & 0x0F)) * 100 ** i for i, b in enumerate(data))


def bcd_level(data):
    try:
        return int(data.hex() or "0")
    except ValueError:          # pas du BCD
        return int.from_bytes(data, "big")


def describe(frame):
    """(nom, valeur, interrogation automatique ?) d'une trame FE FE to fr ... FD."""
    to, cmd, rest = frame[2], frame[4], frame[5:-1]
    key = (cmd, rest[0]) if cmd in WITH_SUB and rest else (cmd,)
    data = rest[1:] if len(key) == 2 else rest
    if key == (0x1A, 0x05):
        data = data[2:]
    name = NAMES.get(key, " ".join(f"{b:02X}" for b in key))
    to_radio = to != 0xE0
    if to_radio and (not data or cmd in (0x25, 0x26) and len(data) == 1):
        # 0x25/0x26 : le seul octet choisit le VFO lu
        return name, "lecture", True
    value = data.hex(" ").upper()
    if cmd in (0x00, 0x03, 0x05) and len(data) == 5:
        value = f"{bcd_frequency(data) / 1e6:.6f} MHz"
    elif cmd == 0x25 and len(data) == 6:
        value = f"{bcd_frequency(data[1:]) / 1e6:.6f} MHz"
    elif cmd in (0x01, 0x04, 0x06) and data:
        value = MODES.get(data[0], value)
    elif cmd == 0x26 and len(data) >= 2:
        value = MODES.get(data[1], value)
    elif key == (0x1C, 0x00) and data:
        value = "ÉMISSION" if data[0] else "réception"
    elif cmd in (0x14, 0x15) and len(data) == 2:
        value = f"{bcd_level(data) * 100 // 255} %"
    elif cmd == 0x18 and data:
        value = "marche" if data[0] else "arrêt"
    elif cmd == 0x17:
        value = data.decode("ascii", "replace")
    elif key == (0x27, 0x00):
        value = f"{len(data)} octets"
    # Réponses aux interrogations : tout ce que la radio envoie sauf ses
    # changements spontanés (bouton tourné) et les accusés OK/Refusé.
    automatic = not to_radio and cmd not in (0x00, 0x01, 0xFA, 0xFB)
    return name, value, automatic


def service_state():
    """"absent", "active" ou "inactive"."""
    run = lambda *cmd: subprocess.run(["systemctl", "--user", *cmd, SERVICE],
                                      capture_output=True, text=True)
    if run("cat").returncode:
        return "absent"
    return "active" if run("is-active", "--quiet").returncode == 0 else "inactive"


def local_ip():
    out = subprocess.run(["hostname", "-I"], capture_output=True, text=True).stdout
    return (out.split() or ["?"])[0]


class ServerWindow(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("IC-7300 Serveur")
        self.setWindowIcon(QtGui.QIcon.fromTheme(
            "ic7300-serveur", QtGui.QIcon(os.path.join(DIR, "icon-serveur.svg"))))
        self.resize(900, 640)
        self.ip = local_ip()
        self.state = None
        self.client = None
        self.sent = 0
        self.buffer = b""
        self.events = deque(maxlen=MAX_ROWS)   # tout le trafic, même masqué
        self.incoming = []                     # pas encore affiché

        self.led = QtWidgets.QLabel("●")
        self.led.setStyleSheet(f"font: 28pt; color: {GREY};")
        self.state_label = QtWidgets.QLabel()
        self.state_label.setStyleSheet("font: bold 16pt;")
        self.client_label = QtWidgets.QLabel()
        self.client_label.setStyleSheet("font: 12pt;")
        self.hint_label = QtWidgets.QLabel()
        self.hint_label.setStyleSheet("font: 11pt monospace; color: #90a4ae;")
        self.hint_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.button = QtWidgets.QPushButton()
        self.button.setMinimumSize(180, 64)
        self.button.clicked.connect(self.toggle)

        text = QtWidgets.QVBoxLayout()
        for label in (self.state_label, self.client_label, self.hint_label):
            text.addWidget(label)
        top = QtWidgets.QHBoxLayout()
        top.addWidget(self.led, 0, QtCore.Qt.AlignTop)
        top.addLayout(text, 1)
        top.addWidget(self.button, 0, QtCore.Qt.AlignVCenter)

        title = QtWidgets.QLabel("Commandes transmises")
        title.setStyleSheet("font: bold 13pt;")
        self.count_label = QtWidgets.QLabel()
        self.count_label.setStyleSheet("color: #90a4ae;")
        self.hide_auto = QtWidgets.QCheckBox("Masquer les interrogations automatiques")
        self.hide_auto.setToolTip("Lectures répétées de l'appli (S-mètre, scope, "
                                  "fréquence…) et réponses de la radio")
        self.hide_auto.setChecked(True)
        self.hide_auto.toggled.connect(self.rebuild)
        self.pause = QtWidgets.QPushButton("Pause")
        self.pause.setCheckable(True)
        self.pause.toggled.connect(lambda paused: paused or self.rebuild())
        clear = QtWidgets.QPushButton("Effacer")
        clear.clicked.connect(self.clear)
        tools = QtWidgets.QHBoxLayout()
        tools.addWidget(title)
        tools.addWidget(self.count_label)
        tools.addStretch(1)
        tools.addWidget(self.hide_auto)
        tools.addWidget(self.pause)
        tools.addWidget(clear)

        self.table = QtWidgets.QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Heure", "Sens", "Commande", "Valeur", "Trame"])
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setStyleSheet("font: 11pt monospace;")
        # Largeurs fixes : ResizeToContents relit toutes les lignes à chaque ajout.
        header = self.table.horizontalHeader()
        for column, width in enumerate((110, 90, 210, 160)):
            header.resizeSection(column, width)
        header.setStretchLastSection(True)
        self.table.verticalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Fixed)
        self.table.verticalHeader().setDefaultSectionSize(24)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(top)
        layout.addSpacing(8)
        layout.addLayout(tools)
        layout.addWidget(self.table, 1)

        self.socket = QtNetwork.QLocalSocket(self)
        self.socket.readyRead.connect(self.read_monitor)
        self.socket.disconnected.connect(lambda: self.set_client(None))
        timer = QtCore.QTimer(self, interval=1000, timeout=self.refresh)
        timer.start()
        # Le scope envoie des dizaines de trames par seconde : affichage groupé.
        flush = QtCore.QTimer(self, interval=200, timeout=self.flush)
        flush.start()
        self.refresh()
        self.update_count()

    # --- service ---------------------------------------------------------

    def refresh(self):
        state = service_state()
        if state != self.state:
            self.state = state
            self.show_state()
        if state == "active" and self.socket.state() == QtNetwork.QLocalSocket.UnconnectedState:
            self.socket.connectToServer(MONITOR_PATH)

    def show_state(self):
        on = self.state == "active"
        self.led.setStyleSheet(f"font: 28pt; color: {GREEN if on else GREY};")
        if self.state == "absent":
            self.state_label.setText("Serveur non installé")
            self.hint_label.setText("L'installation partage le son et la commande "
                                    "de la radio\nsur le réseau local (ports 4713 et 7300).")
            label, color = "Installer", "#4fc3f7"
        elif on:
            self.state_label.setText(f"En marche sur {self.ip}, port 7300")
            self.hint_label.setText(f"Sur l'autre PC : ic7300_scope.py --remote {self.ip}")
            label, color = "Arrêter", RED
        else:
            self.state_label.setText("Arrêté")
            self.hint_label.setText("La radio ne peut pas être pilotée à distance.")
            label, color = "Démarrer", GREEN
        self.button.setText(label)
        self.button.setStyleSheet(f"font: bold 16pt; background: {color}; color: #101418;")
        if not on:
            self.set_client(None)
        else:
            self.show_client()

    def toggle(self):
        self.button.setEnabled(False)
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            if self.state == "absent":
                result = subprocess.run([os.path.join(DIR, "install-serveur.sh")],
                                        capture_output=True, text=True)
            else:
                action = "stop" if self.state == "active" else "start"
                result = subprocess.run(["systemctl", "--user", action, SERVICE],
                                        capture_output=True, text=True)
                time.sleep(0.5)
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
            self.button.setEnabled(True)
        if result.returncode:
            QtWidgets.QMessageBox.critical(self, "IC-7300 Serveur",
                                           f"Échec :\n{result.stderr.strip()}")
        if self.state == "active":
            self.socket.abort()
        self.refresh()
        if self.state == "inactive" and result.returncode == 0:
            self.record(time.time(), "serveur", "Serveur arrêté", "", "", GREY, False)

    def closeEvent(self, event):
        self.socket.disconnected.disconnect()   # les étiquettes vont disparaître
        self.socket.abort()
        super().closeEvent(event)

    # --- surveillance ----------------------------------------------------

    def set_client(self, host):
        self.client = host
        self.show_client()

    def show_client(self):
        if self.state != "active":
            self.client_label.setText("")
        elif self.client:
            self.client_label.setText(f"<span style='color:{GREEN}'>PC distant "
                                      f"connecté : <b>{self.client}</b></span>")
        else:
            self.client_label.setText("Aucun PC distant connecté")

    def read_monitor(self):
        self.buffer += bytes(self.socket.readAll())
        *lines, self.buffer = self.buffer.split(b"\n")
        for line in lines:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event["ev"] == "client":
                self.set_client(event["host"])
            elif event["ev"] == "log":
                self.record(event["t"], "serveur", event["msg"], "", "", GREY, False)
            elif event["ev"] == "frame":
                self.add_frame(event)

    def add_frame(self, event):
        frame = bytes.fromhex(event["hex"])
        if len(frame) < 6:
            return
        name, value, automatic = describe(frame)
        to_radio = event["dir"] == "tx"
        if to_radio and not automatic:
            self.sent += 1
        color = "#ffd54a" if to_radio else "#4fc3f7"
        if name == "PTT" and value == "ÉMISSION":
            color = RED
        self.record(event["t"], "→ radio" if to_radio else "← radio",
                    name, value, event["hex"].upper(), color, automatic)

    def record(self, t, direction, name, value, raw, color, automatic):
        stamp = time.strftime("%H:%M:%S", time.localtime(t)) + f".{int(t * 10) % 10}"
        row = (stamp, direction, name, value, raw, color, automatic)
        self.events.append(row)
        self.incoming.append(row)

    def visible(self, rows):
        hide = self.hide_auto.isChecked()
        return [row for row in rows if not (hide and row[6])]

    def flush(self):
        rows, self.incoming = self.incoming, []
        self.update_count()
        if rows and not self.pause.isChecked():
            self.show_rows(self.visible(rows))

    def show_rows(self, rows):
        if not rows:
            return
        rows = rows[-SHOWN_ROWS:]
        bar = self.table.verticalScrollBar()
        follow = bar.value() == bar.maximum()
        self.table.setUpdatesEnabled(False)
        excess = self.table.rowCount() + len(rows) - SHOWN_ROWS
        for _ in range(max(0, excess)):
            self.table.removeRow(0)
        for stamp, direction, name, value, raw, color, _ in rows:
            row = self.table.rowCount()
            self.table.insertRow(row)
            for column, text in enumerate((stamp, direction, name, value, raw)):
                item = QtWidgets.QTableWidgetItem(text)
                item.setForeground(QtGui.QColor(color if column < 4 else "#78909c"))
                self.table.setItem(row, column, item)
        self.table.setUpdatesEnabled(True)
        if follow:
            self.table.scrollToBottom()

    def rebuild(self, *_):
        self.incoming = []
        self.table.setRowCount(0)
        self.show_rows(self.visible(self.events))
        self.table.scrollToBottom()

    def clear(self):
        self.events.clear()
        self.incoming = []
        self.table.setRowCount(0)
        self.sent = 0
        self.update_count()

    def update_count(self):
        self.count_label.setText(f"{self.sent} envoyée{'s' if self.sent > 1 else ''} à la radio")


def dark_palette():
    palette = QtGui.QPalette()
    base, text = QtGui.QColor("#101418"), QtGui.QColor("#e0e0e0")
    palette.setColor(QtGui.QPalette.Window, base)
    palette.setColor(QtGui.QPalette.WindowText, text)
    palette.setColor(QtGui.QPalette.Base, QtGui.QColor("#1a2027"))
    palette.setColor(QtGui.QPalette.Text, text)
    palette.setColor(QtGui.QPalette.Button, QtGui.QColor("#1f262e"))
    palette.setColor(QtGui.QPalette.ButtonText, text)
    palette.setColor(QtGui.QPalette.Highlight, QtGui.QColor("#4fc3f7"))
    return palette


def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setDesktopFileName("ic7300-serveur")
    app.setStyle("Fusion")
    app.setPalette(dark_palette())
    window = ServerWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
