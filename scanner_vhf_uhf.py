#!/usr/bin/env python3
"""Scanner des bandes amateur VHF/UHF avec Gqrx et une clé RTL-SDR.

Gqrx fait la réception et le son ; ce programme le pilote par son contrôle
à distance (TCP 7356, bouton « Remote control » de Gqrx à activer) : il
balaie les canaux, s'arrête sur ceux où le niveau dépasse le bruit de fond
d'un seuil réglable, puis repart quand le signal a disparu.
"""
import argparse
import socket
import sys
import time

import numpy as np
from PyQt5 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

GQRX_HOST = "127.0.0.1"
GQRX_PORT = 7356

# (nom, début MHz, fin MHz, pas kHz) — plan de bande IARU Région 1
BANDS = [
    ("2 m : relais (145,5875 – 145,7875)", 145.5875, 145.7875, 12.5),
    ("2 m : FM simplex (145,2125 – 145,5875)", 145.2125, 145.5875, 12.5),
    ("2 m : toute la bande", 144.0, 146.0, 12.5),
    ("70 cm : relais (438,650 – 439,425)", 438.65, 439.425, 12.5),
    ("70 cm : FM simplex (433,400 – 434,600)", 433.4, 434.6, 12.5),
    ("70 cm : toute la bande", 430.0, 440.0, 25.0),
    ("6 m : FM (51,410 – 51,590)", 51.41, 51.59, 10.0),
]
FLOOR = -120.0  # bas de l'échelle des niveaux (dBFS)


class Gqrx:
    """Liaison texte avec le contrôle à distance de Gqrx (commandes façon rigctl)."""

    def __init__(self, host, port):
        self.sock = socket.create_connection((host, port), timeout=2)
        self.buf = b""

    def cmd(self, text):
        self.sock.sendall(text.encode() + b"\n")
        while b"\n" not in self.buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("Gqrx a fermé la connexion")
            self.buf += chunk
        line, self.buf = self.buf.split(b"\n", 1)
        return line.decode().strip()

    def tune(self, hz):
        self.cmd(f"F {hz}")

    def strength(self):
        return float(self.cmd("l STRENGTH"))

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class Scanner(QtWidgets.QMainWindow):
    def __init__(self, host, port):
        super().__init__()
        self.host, self.port = host, port
        self.rig = None
        self.chans = []
        self.levels = np.array([])
        self.idx = 0
        self.holding = False
        self.quiet_since = None
        self.noise = None
        self.lockout = set()
        self.rows = {}  # fréquence -> ligne du tableau d'activité
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.setWindowTitle("Scanner VHF/UHF (Gqrx)")
        self.resize(1000, 720)
        self._build()
        self.on_band()

    # --- Interface ---------------------------------------------------------

    def _build(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)

        row = QtWidgets.QHBoxLayout()
        self.band = QtWidgets.QComboBox()
        for name, *_ in BANDS:
            self.band.addItem(name)
        self.band.addItem("Personnalisée")
        self.band.currentIndexChanged.connect(self.on_band)
        row.addWidget(QtWidgets.QLabel("Bande"))
        row.addWidget(self.band, 1)
        self.f_start = self._spin(30, 1800, 4, " MHz")
        self.f_stop = self._spin(30, 1800, 4, " MHz")
        self.step = self._spin(5, 100, 2, " kHz")
        for label, w in (("de", self.f_start), ("à", self.f_stop), ("pas", self.step)):
            row.addWidget(QtWidgets.QLabel(label))
            row.addWidget(w)
            w.editingFinished.connect(self.on_custom)
        layout.addLayout(row)

        row = QtWidgets.QHBoxLayout()
        self.offset = QtWidgets.QSpinBox()
        self.offset.setRange(3, 40)
        self.offset.setValue(10)
        self.offset.setSuffix(" dB")
        self.dwell = QtWidgets.QSpinBox()
        self.dwell.setRange(50, 1000)
        self.dwell.setSingleStep(10)
        self.dwell.setValue(120)
        self.dwell.setSuffix(" ms")
        self.hold = QtWidgets.QDoubleSpinBox()
        self.hold.setRange(0, 30)
        self.hold.setValue(3)
        self.hold.setSuffix(" s")
        for label, w, tip in (
                ("Seuil : bruit +", self.offset,
                 "le scanner s'arrête quand le niveau dépasse le bruit de fond de tant"),
                ("Écoute par canal", self.dwell,
                 "temps passé sur chaque canal avant de lire le niveau"),
                ("Attente après signal", self.hold,
                 "temps d'attente en fin d'émission avant de repartir (réponse du correspondant)")):
            lab = QtWidgets.QLabel(label)
            lab.setToolTip(tip)
            w.setToolTip(tip)
            row.addWidget(lab)
            row.addWidget(w)
        row.addStretch(1)
        self.start_btn = QtWidgets.QPushButton("▶ Démarrer")
        self.start_btn.setCheckable(True)
        self.start_btn.toggled.connect(self.on_start)
        self.skip_btn = QtWidgets.QPushButton("⏭ Passer")
        self.skip_btn.setToolTip("quitter ce canal et continuer le balayage")
        self.skip_btn.clicked.connect(self.on_skip)
        self.lock_btn = QtWidgets.QPushButton("✖ Ignorer")
        self.lock_btn.setToolTip("ne plus s'arrêter sur ce canal (porteuse, parasite…)")
        self.lock_btn.clicked.connect(self.on_lock)
        for b in (self.start_btn, self.skip_btn, self.lock_btn):
            row.addWidget(b)
        layout.addLayout(row)

        row = QtWidgets.QHBoxLayout()
        self.freq_label = QtWidgets.QLabel("---.---.---")
        font = QtGui.QFont("Monospace", 30, QtGui.QFont.Bold)
        font.setStyleHint(QtGui.QFont.TypeWriter)
        self.freq_label.setFont(font)
        row.addWidget(self.freq_label)
        row.addSpacing(20)
        self.level_bar = QtWidgets.QProgressBar()
        self.level_bar.setRange(int(FLOOR), 0)
        self.level_bar.setFormat("%v dBFS")
        row.addWidget(self.level_bar, 1)
        self.state_label = QtWidgets.QLabel("Arrêté")
        self.state_label.setMinimumWidth(160)
        self.state_label.setAlignment(QtCore.Qt.AlignCenter)
        row.addWidget(self.state_label)
        layout.addLayout(row)

        self.plot = pg.PlotWidget()
        self.plot.setLabel("bottom", "Fréquence", "MHz")
        self.plot.setLabel("left", "Niveau", "dBFS")
        self.plot.showGrid(x=True, y=True, alpha=0.3)
        self.plot.setYRange(FLOOR, 0)
        self.plot.setMouseEnabled(x=True, y=False)
        self.bars = pg.BarGraphItem(x=[0], height=[0], width=0.01, y0=FLOOR,
                                    brush="#4fc3f7", pen=None)
        self.plot.addItem(self.bars)
        self.cursor = pg.InfiniteLine(angle=90, pen=pg.mkPen("#ffb300", width=2))
        self.plot.addItem(self.cursor)
        self.thr_line = pg.InfiniteLine(angle=0, pen=pg.mkPen("#ef5350", style=QtCore.Qt.DashLine))
        self.plot.addItem(self.thr_line)
        self.plot.scene().sigMouseClicked.connect(self.on_plot_click)
        layout.addWidget(self.plot, 2)

        self.table = QtWidgets.QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ["Fréquence (MHz)", "Dernière activité", "Passages", "Niveau max (dBFS)"])
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSortingEnabled(True)
        self.table.cellDoubleClicked.connect(self.on_table_click)
        self.table.setToolTip("double-clic : arrêter le scanner et écouter cette fréquence")
        layout.addWidget(self.table, 1)

        self.statusBar().showMessage(
            "Lancer Gqrx, choisir la clé RTL-SDR, démarrer la réception (▶) "
            "et activer « Remote control » dans sa barre d'outils.")

    def _spin(self, lo, hi, decimals, suffix):
        w = QtWidgets.QDoubleSpinBox()
        w.setRange(lo, hi)
        w.setDecimals(decimals)
        w.setSuffix(suffix)
        return w

    # --- Canaux ------------------------------------------------------------

    def on_band(self):
        i = self.band.currentIndex()
        if i >= len(BANDS):
            return
        _, start, stop, step = BANDS[i]
        for w, v in ((self.f_start, start), (self.f_stop, stop), (self.step, step)):
            w.blockSignals(True)
            w.setValue(v)
            w.blockSignals(False)
        self.set_channels()

    def on_custom(self):
        self.band.blockSignals(True)
        self.band.setCurrentIndex(len(BANDS))
        self.band.blockSignals(False)
        self.set_channels()

    def set_channels(self):
        start = round(self.f_start.value() * 1e6)
        stop = round(self.f_stop.value() * 1e6)
        step = round(self.step.value() * 1e3)
        if stop < start:
            start, stop = stop, start
        self.chans = list(range(start, stop + 1, step))
        self.levels = np.full(len(self.chans), np.nan)
        self.idx = 0
        self.holding = False
        self.noise = None
        x = np.array(self.chans) / 1e6
        self.bars.setOpts(x=x, height=np.zeros(len(x)), width=step / 1e6 * 0.8)
        self.plot.setXRange(x[0] - step / 1e6, x[-1] + step / 1e6, padding=0)
        self.thr_line.hide()
        if self.rig:
            self.rig.tune(self.chans[0])
        self.show_freq(self.chans[0])

    # --- Gqrx --------------------------------------------------------------

    def connect_gqrx(self):
        try:
            self.rig = Gqrx(self.host, self.port)
            self.rig.cmd("M FM 12500")
        except (OSError, ValueError) as e:
            self.rig = None
            QtWidgets.QMessageBox.warning(
                self, "Gqrx injoignable",
                f"Pas de contrôle à distance sur {self.host}:{self.port} ({e}).\n\n"
                "Dans Gqrx : démarrer la réception (▶) et activer le bouton "
                "« Remote control » (icône d'antenne réseau dans la barre d'outils).")
            return False
        return True

    def lost(self, e):
        self.start_btn.setChecked(False)
        if self.rig:
            self.rig.close()
            self.rig = None
        self.statusBar().showMessage(f"Liaison avec Gqrx perdue : {e}")

    def on_start(self, on):
        if on:
            if not self.rig and not self.connect_gqrx():
                self.start_btn.setChecked(False)
                return
            self.start_btn.setText("⏸ Arrêter")
            self.holding = False
            self.rig.tune(self.chans[self.idx])
            self.show_freq(self.chans[self.idx])
            self.timer.start(self.dwell.value())
            self.state_label.setText("Balayage…")
            self.statusBar().showMessage(f"{len(self.chans)} canaux, {len(self.lockout)} ignorés")
        else:
            self.timer.stop()
            self.start_btn.setText("▶ Démarrer")
            self.state_label.setText("Arrêté")
            self.state_label.setStyleSheet("")

    # --- Balayage ----------------------------------------------------------

    def threshold(self):
        return None if self.noise is None else self.noise + self.offset.value()

    def tick(self):
        try:
            lvl = self.rig.strength()
        except (OSError, ValueError, ConnectionError) as e:
            self.lost(e)
            return
        f = self.chans[self.idx]
        self.levels[self.idx] = lvl
        self.level_bar.setValue(int(max(FLOOR, min(0, lvl))))
        self.bars.setOpts(height=np.nan_to_num(self.levels, nan=FLOOR) - FLOOR)
        thr = self.threshold()

        if thr is not None and lvl >= thr and f not in self.lockout:
            if not self.holding:
                self.holding = True
                self.log_activity(f, lvl)
                self.state_label.setText("● Signal")
                self.state_label.setStyleSheet("background:#2e7d32; color:white; font-weight:bold")
            else:
                self.update_max(f, lvl)
            self.quiet_since = None
            self.timer.setInterval(200)
            return
        if self.holding:
            if self.quiet_since is None:
                self.quiet_since = time.monotonic()
                self.state_label.setText("Attente…")
                self.state_label.setStyleSheet("background:#f9a825; color:black")
            if time.monotonic() - self.quiet_since < self.hold.value():
                return
        self.next_channel()

    def next_channel(self):
        self.holding = False
        self.quiet_since = None
        self.idx += 1
        if self.idx >= len(self.chans):
            self.idx = 0
            self.update_noise()
        f = self.chans[self.idx]
        try:
            self.rig.tune(f)
        except (OSError, ConnectionError) as e:
            self.lost(e)
            return
        self.show_freq(f)
        self.state_label.setText("Balayage…")
        self.state_label.setStyleSheet("")
        self.timer.setInterval(self.dwell.value())

    def update_noise(self):
        # Bruit de fond = médiane du dernier balayage : quelques canaux
        # occupés ne la déplacent pas.
        known = self.levels[~np.isnan(self.levels)]
        if len(known) < 3:
            return
        self.noise = float(np.median(known))
        thr = self.threshold()
        self.thr_line.setValue(thr)
        self.thr_line.show()
        try:
            self.rig.cmd(f"L SQL {thr:.1f}")  # squelch de Gqrx calé sur le seuil
        except (OSError, ConnectionError):
            pass
        self.statusBar().showMessage(
            f"Bruit de fond {self.noise:.1f} dBFS, seuil {thr:.1f} dBFS — "
            f"{len(self.chans)} canaux, {len(self.lockout)} ignorés")

    def show_freq(self, hz):
        s = f"{hz:011,d}".replace(",", ".")
        self.freq_label.setText(s.lstrip("0").lstrip("."))
        self.cursor.setValue(hz / 1e6)

    # --- Activité ----------------------------------------------------------

    def log_activity(self, f, lvl):
        now = time.strftime("%H:%M:%S")
        self.table.setSortingEnabled(False)
        row = self.find_row(f)
        if row is None:
            row = self.table.rowCount()
            self.table.insertRow(row)
            item = QtWidgets.QTableWidgetItem(f"{f / 1e6:.4f}")
            item.setData(QtCore.Qt.UserRole, f)
            self.table.setItem(row, 0, item)
            self.table.setItem(row, 2, self._num(0))
            self.table.setItem(row, 3, self._num(lvl))
        self.table.setItem(row, 1, QtWidgets.QTableWidgetItem(now))
        count = self.table.item(row, 2)
        count.setData(QtCore.Qt.DisplayRole, count.data(QtCore.Qt.DisplayRole) + 1)
        self.table.setSortingEnabled(True)
        self.update_max(f, lvl)

    def update_max(self, f, lvl):
        row = self.find_row(f)
        if row is None:
            return
        item = self.table.item(row, 3)
        if lvl > item.data(QtCore.Qt.DisplayRole):
            item.setData(QtCore.Qt.DisplayRole, round(lvl, 1))

    def find_row(self, f):
        for row in range(self.table.rowCount()):
            if self.table.item(row, 0).data(QtCore.Qt.UserRole) == f:
                return row
        return None

    def _num(self, v):
        item = QtWidgets.QTableWidgetItem()
        item.setData(QtCore.Qt.DisplayRole, round(v, 1) if isinstance(v, float) else v)
        return item

    # --- Commandes ---------------------------------------------------------

    def on_skip(self):
        if self.start_btn.isChecked():
            self.next_channel()

    def on_lock(self):
        f = self.chans[self.idx]
        self.lockout.add(f)
        self.statusBar().showMessage(f"{f / 1e6:.4f} MHz ignoré ({len(self.lockout)} au total)")
        self.on_skip()

    def listen(self, f):
        """Arrête le balayage et cale Gqrx sur f."""
        self.start_btn.setChecked(False)
        if not self.rig and not self.connect_gqrx():
            return
        try:
            self.rig.tune(f)
        except (OSError, ConnectionError) as e:
            self.lost(e)
            return
        if f in self.chans:
            self.idx = self.chans.index(f)
        self.show_freq(f)
        self.state_label.setText("Écoute")

    def on_table_click(self, row, _col):
        self.listen(self.table.item(row, 0).data(QtCore.Qt.UserRole))

    def on_plot_click(self, ev):
        if not ev.double() or not self.chans:
            return
        x = self.plot.plotItem.vb.mapSceneToView(ev.scenePos()).x() * 1e6
        self.listen(min(self.chans, key=lambda c: abs(c - x)))

    def closeEvent(self, ev):
        self.timer.stop()
        if self.rig:
            self.rig.close()
        super().closeEvent(ev)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=GQRX_HOST, help="machine où tourne Gqrx")
    parser.add_argument("--port", type=int, default=GQRX_PORT,
                        help="port du contrôle à distance de Gqrx")
    args = parser.parse_args()
    app = QtWidgets.QApplication(sys.argv)
    pg.setConfigOptions(antialias=True)
    win = Scanner(args.host, args.port)
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
