"""Fenêtre SSTV : réception à gauche, émission d'une image à droite."""
import datetime
import os
import tempfile
import wave

import numpy as np
from PyQt5 import QtCore, QtGui, QtWidgets

import sstv
import wefax


def pictures_dir():
    import subprocess
    try:
        out = subprocess.run(["xdg-user-dir", "PICTURES"], capture_output=True,
                             text=True, timeout=2).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        out = ""
    return out or os.path.expanduser("~/Pictures")


SSTV_DIR = os.path.join(pictures_dir(), "SSTV")

# stations de fax météo (120 lignes/min, IOC 576) : nom, fréquence annoncée (kHz)
FAX_STATIONS = [
    ("DWD Pinneberg (Allemagne)", 3855.0),
    ("DWD Pinneberg (Allemagne)", 7880.0),
    ("DWD Pinneberg (Allemagne)", 13882.5),
    ("Northwood (Royaume-Uni)", 2618.5),
    ("Northwood (Royaume-Uni)", 4610.0),
    ("Northwood (Royaume-Uni)", 8040.0),
    ("Northwood (Royaume-Uni)", 11086.5),
    ("Athènes (Grèce)", 4481.0),
    ("Athènes (Grèce)", 8105.0),
    ("Boston NMF (États-Unis)", 4235.0),
    ("Boston NMF (États-Unis)", 6340.5),
    ("Boston NMF (États-Unis)", 9110.0),
    ("Boston NMF (États-Unis)", 12750.0),
]
FAX_OFFSET_HZ = 1900            # en USB, la radio se règle 1,9 kHz plus bas
# fréquences d'activité SSTV : fréquence (kHz), mode de la radio, note
SSTV_FREQUENCIES = [
    (3735.0, "LSB", "80 m"),
    (7165.0, "LSB", "40 m"),
    (7171.0, "LSB", "40 m"),
    (14230.0, "USB", "20 m, la plus active"),
    (14233.0, "USB", "20 m"),
    (21340.0, "USB", "15 m"),
    (28680.0, "USB", "10 m"),
]


def bring_to_front(window):
    """Montre la fenêtre devant les autres, même si elle était passée derrière.

    GNOME refuse à une appli de remonter une fenêtre déjà ouverte (raise) :
    on la referme et la rouvre, ce qui la remet au premier plan."""
    if window.isVisible():
        geometry = window.saveGeometry()
        window.hide()
        window.restoreGeometry(geometry)
    window.showNormal() if window.isMinimized() else window.show()
    window.raise_()
    window.activateWindow()


def to_qimage(rgb):
    h, w, _ = rgb.shape
    rgb = np.ascontiguousarray(rgb)
    return QtGui.QImage(rgb.data, w, h, 3 * w, QtGui.QImage.Format_RGB888).copy()


def to_array(image):
    image = image.convertToFormat(QtGui.QImage.Format_RGB888)
    w, h, line = image.width(), image.height(), image.bytesPerLine()
    bits = image.constBits()
    bits.setsize(h * line)
    return np.frombuffer(bits, np.uint8).reshape(h, line)[:, :w * 3].reshape(h, w, 3).copy()


class ImageView(QtWidgets.QLabel):
    """Image à l'échelle de la place disponible, proportions gardées."""
    clicked = QtCore.pyqtSignal(float)     # abscisse cliquée, de 0 à 1

    def __init__(self, minimum):
        super().__init__()
        self.setMinimumSize(*minimum)
        self.setAlignment(QtCore.Qt.AlignCenter)
        self.setStyleSheet("background: #0b0f13; border: 1px solid #263238;")
        self._image = None

    def set_image(self, image):
        self._image = image
        self._update()

    def resizeEvent(self, event):
        self._update()
        super().resizeEvent(event)

    def mousePressEvent(self, event):
        pixmap = self.pixmap()
        if pixmap is not None and not pixmap.isNull():
            left = (self.width() - pixmap.width()) / 2
            x = (event.pos().x() - left) / pixmap.width()
            if 0 <= x <= 1:
                self.clicked.emit(x)
        super().mousePressEvent(event)

    def _update(self):
        if self._image is None:
            return
        self.setPixmap(QtGui.QPixmap.fromImage(self._image).scaled(
            self.size() - QtCore.QSize(2, 2), QtCore.Qt.KeepAspectRatio,
            QtCore.Qt.SmoothTransformation))


class BigView(QtWidgets.QWidget):
    """La réception seule, dans une fenêtre à part (F11 : plein écran)."""

    def __init__(self, owner):
        # fenêtre fille : elle reste devant la fenêtre SSTV
        super().__init__(owner, QtCore.Qt.Window)
        self.setWindowTitle("Réception SSTV / fax — IC-7300 Scope")
        self.resize(900, 720)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        self.view = ImageView((320, 256))
        self.view.clicked.connect(owner.on_rx_click)
        self.status = QtWidgets.QLabel("")
        self.status.setStyleSheet("font: 11pt monospace;")
        self.status.setToolTip("F11 : plein écran")
        layout.addWidget(self.view, stretch=1)
        layout.addWidget(self.status)
        if owner.decoder.image is not None and len(owner.decoder.image):
            self.view.set_image(to_qimage(owner.decoder.image))

    def keyPressEvent(self, event):
        # F11 : plein écran ou non ; Échap en sort
        if event.key() == QtCore.Qt.Key_F11:
            self.showNormal() if self.isFullScreen() else self.showFullScreen()
        elif event.key() == QtCore.Qt.Key_Escape and self.isFullScreen():
            self.showNormal()
        else:
            super().keyPressEvent(event)


class SstvWindow(QtWidgets.QWidget):
    def __init__(self, main):
        # fenêtre fille : elle reste devant la fenêtre principale
        super().__init__(main, QtCore.Qt.Window)
        self.main = main
        self.setWindowTitle("SSTV — IC-7300 Scope")
        self.resize(1100, 560)
        self.decoders = {"sstv": sstv.SstvDecoder(main.audio_rate),
                         "fax": wefax.WefaxDecoder(main.audio_rate)}
        self.decoder = self.decoders["sstv"]
        self.tx_source = None           # image choisie (QImage)
        self.tx_wav = None
        layout = QtWidgets.QHBoxLayout(self)

        # Réception
        rx = QtWidgets.QGroupBox("Réception")
        rx_layout = QtWidgets.QVBoxLayout(rx)
        rx_kind = QtWidgets.QHBoxLayout()
        self.rx_kind = QtWidgets.QComboBox()
        self.rx_kind.addItem("SSTV", "sstv")
        self.rx_kind.addItem("Fax météo (120 lignes/min, IOC 576)", "fax")
        self.rx_kind.setToolTip("Fax météo : DWD Pinneberg 3855, 7880 et 13882,5 kHz "
                                "(radio en USB, 1,9 kHz plus bas)")
        self.rx_kind.currentIndexChanged.connect(self.on_rx_kind)
        # réglages du fax
        self.fax_start = QtWidgets.QPushButton("Démarrer maintenant")
        self.fax_start.setToolTip("Commencer la carte sans attendre le signal de "
                                  "départ. Cliquer ensuite sur le bord gauche "
                                  "de la carte pour la recaler.")
        self.fax_start.clicked.connect(lambda: self.decoders["fax"].start_now())
        self.fax_invert = QtWidgets.QCheckBox("Inverser")
        self.fax_invert.setToolTip("Négatif : radio en LSB")
        self.fax_invert.toggled.connect(
            lambda on: setattr(self.decoders["fax"], "inverted", on))
        self.fax_slant = QtWidgets.QSpinBox()
        self.fax_slant.setRange(-2000, 2000)
        self.fax_slant.setSingleStep(10)
        self.fax_slant.setSuffix(" ppm")
        self.fax_slant.setPrefix("Pente ")
        self.fax_slant.setToolTip("Redresse une carte penchée. Calée toute seule "
                                  "sur le phasage en début de carte.")
        self.fax_slant.valueChanged.connect(
            lambda ppm: self.decoders["fax"].set_slant(ppm))
        # fréquences préprogrammées, selon SSTV ou fax
        self.station = QtWidgets.QComboBox()
        self.station.activated.connect(self.on_station)
        self.fax_widgets = (self.fax_start, self.fax_invert, self.fax_slant)
        rx_kind.addWidget(self.rx_kind)
        rx_kind.addWidget(self.station)
        for widget in self.fax_widgets:
            rx_kind.addWidget(widget)
        rx_kind.addStretch()
        rx_layout.addLayout(rx_kind)
        self.rx_view = ImageView((480, 384))
        self.rx_view.clicked.connect(self.on_rx_click)
        self.rx_status = QtWidgets.QLabel("")
        self.rx_status.setStyleSheet("font: 11pt monospace;")
        self.rx_saved = QtWidgets.QLabel("")
        rx_buttons = QtWidgets.QHBoxLayout()
        reset = QtWidgets.QPushButton("Réinitialiser")
        reset.setToolTip("Abandonner l'image en cours et attendre la suivante")
        reset.clicked.connect(lambda: self.decoder.reset())
        save = QtWidgets.QPushButton("Enregistrer l'image")
        save.clicked.connect(self.save_current)
        folder = QtWidgets.QPushButton("Dossier")
        folder.clicked.connect(lambda: self._open_folder(SSTV_DIR))
        detach = QtWidgets.QPushButton("Grande fenêtre")
        detach.setToolTip("Afficher la réception dans une fenêtre à part, "
                          "à agrandir ou mettre en plein écran (F11)")
        detach.clicked.connect(self.open_big_view)
        self.big_view = None
        for button in (reset, save, folder, detach):
            rx_buttons.addWidget(button)
        rx_buttons.addStretch()
        rx_layout.addWidget(self.rx_view, stretch=1)
        rx_layout.addWidget(self.rx_status)
        rx_layout.addWidget(self.rx_saved)
        rx_layout.addLayout(rx_buttons)

        # Émission
        tx = QtWidgets.QGroupBox("Émission")
        tx_layout = QtWidgets.QVBoxLayout(tx)
        form = QtWidgets.QFormLayout()
        self.mode_combo = QtWidgets.QComboBox()
        for mode in sstv.MODES:
            self.mode_combo.addItem(
                f"{mode.name}  ({mode.width}×{mode.height}, "
                f"{mode.duration // 60:.0f} min {mode.duration % 60:02.0f} s)", mode.name)
        self.mode_combo.setCurrentIndex(self.mode_combo.findData("Martin M1"))
        self.mode_combo.currentIndexChanged.connect(self.update_preview)
        self.text_edit = QtWidgets.QLineEdit()
        self.text_edit.setPlaceholderText("ex. F4HXN")
        self.text_edit.setToolTip("Texte écrit en haut de l'image (indicatif…)")
        self.text_edit.textChanged.connect(self.update_preview)
        self.text_color = QtGui.QColor("white")
        self.color_button = QtWidgets.QPushButton()
        self.color_button.setFixedWidth(40)
        self.color_button.setToolTip("Couleur du texte")
        self.color_button.clicked.connect(self.choose_text_color)
        self._show_text_color()
        text_row = QtWidgets.QHBoxLayout()
        text_row.addWidget(self.text_edit)
        text_row.addWidget(self.color_button)
        choose = QtWidgets.QPushButton("Choisir une image…")
        choose.clicked.connect(self.choose_image)
        form.addRow("Mode", self.mode_combo)
        form.addRow("Texte", text_row)
        form.addRow("", choose)
        self.tx_view = ImageView((320, 256))
        self.tx_info = QtWidgets.QLabel("")
        self.tx_info.setWordWrap(True)
        self.send_button = QtWidgets.QPushButton("Émettre l'image")
        self.send_button.setMinimumHeight(34)
        self.send_button.clicked.connect(self.send)
        tx_layout.addLayout(form)
        tx_layout.addWidget(self.tx_view, stretch=1)
        tx_layout.addWidget(self.tx_info)
        tx_layout.addWidget(self.send_button)

        layout.addWidget(rx, stretch=3)
        layout.addWidget(tx, stretch=2)

        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.refresh)
        self.update_preview()
        self.on_rx_kind()

    # -- marche / arrêt du décodeur avec la fenêtre

    def showEvent(self, event):
        self.main.audio.sstv = self.decoder
        self.timer.start()
        super().showEvent(event)

    def closeEvent(self, event):
        if self.big_view is not None:
            self.big_view.close()
        self.main.audio.sstv = None
        self.timer.stop()
        super().closeEvent(event)

    # -- réception

    def on_rx_kind(self):
        self.decoder = self.decoders[self.rx_kind.currentData()]
        if self.isVisible():
            self.main.audio.sstv = self.decoder
        fax = self.decoder is self.decoders["fax"]
        for widget in self.fax_widgets:
            widget.setVisible(fax)
        self._fill_stations(fax)
        for view in (self.rx_view, self.big_view and self.big_view.view):
            if view:
                view.set_image(None)
                view.clear()

    def _fill_stations(self, fax):
        self.station.clear()
        if fax:
            self.station.addItem("Station…", None)
            for name, khz in FAX_STATIONS:
                self.station.addItem(f"{khz:g} kHz · {name}",
                                     (round(khz * 1000) - FAX_OFFSET_HZ, "USB"))
            self.station.setToolTip("Règle la radio sur la station, en USB "
                                    "1,9 kHz sous la fréquence annoncée")
        else:
            self.station.addItem("Fréquence…", None)
            for khz, mode, note in SSTV_FREQUENCIES:
                self.station.addItem(f"{khz:g} kHz {mode} · {note}",
                                     (round(khz * 1000), mode))
            self.station.setToolTip("Règle la radio sur une fréquence d'activité SSTV")

    def on_station(self, index):
        choice = self.station.itemData(index)
        if choice is None:
            return
        hz, mode = choice
        main = self.main
        if not main.civ or main.tx_active:
            self.rx_status.setText("Radio non pilotée ou en émission : "
                                   "réglage impossible")
            return
        if main.mode_combo.currentText() != mode:
            main.mode_combo.setCurrentText(mode)
            main.civ.set_mode(mode)
        main.tune_to(hz)
        if self.decoder is self.decoders["fax"]:
            self.fax_invert.setChecked(False)   # en USB, la carte est à l'endroit

    def on_rx_click(self, x):
        """Fax : le point cliqué devient le bord gauche de la carte."""
        if self.decoder is self.decoders["fax"]:
            self.decoder.set_left_edge(x)

    def open_big_view(self):
        if self.big_view is None:
            self.big_view = BigView(self)
        bring_to_front(self.big_view)

    def refresh(self):
        image = self.decoder.image
        if image is not None and len(image):
            qimage = to_qimage(image)
            self.rx_view.set_image(qimage)
            if self.big_view is not None and self.big_view.isVisible():
                self.big_view.view.set_image(qimage)
        if self.big_view is not None and self.big_view.isVisible():
            self.big_view.status.setText(self.decoder.status())
        self.rx_status.setText(self.decoder.status())
        if self.decoder is self.decoders["fax"] and not self.fax_slant.hasFocus():
            # pente calée par le phasage
            self.fax_slant.blockSignals(True)
            self.fax_slant.setValue(round(self.decoder.slant_ppm))
            self.fax_slant.blockSignals(False)
        while self.decoder.finished:
            mode, rgb = self.decoder.finished.pop(0)
            path = self._save(rgb, mode.name)
            self.rx_saved.setText(f"Enregistrée : {path}")
        self.send_button.setEnabled(not self.main.tx_active)

    def _save(self, rgb, name):
        os.makedirs(SSTV_DIR, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y-%m-%d_%Hh%Mm%S")
        path = os.path.join(SSTV_DIR, f"{stamp}_{name.replace(' ', '')}.png")
        to_qimage(rgb).save(path)
        return path

    def save_current(self):
        image = self.decoder.image
        if image is None:
            return
        mode = self.decoder.mode
        path = self._save(image, mode.name if mode else "sstv")
        self.rx_saved.setText(f"Enregistrée : {path}")

    def _open_folder(self, path):
        import subprocess
        os.makedirs(path, exist_ok=True)
        subprocess.Popen(["xdg-open", path])

    # -- émission

    def choose_image(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Image à émettre", pictures_dir(),
            "Images (*.png *.jpg *.jpeg *.bmp *.gif *.webp)")
        if path:
            image = QtGui.QImage(path)
            if image.isNull():
                self.tx_info.setText("Image illisible")
                return
            self.tx_source = image
            self.update_preview()

    def _mode(self):
        return sstv.BY_NAME[self.mode_combo.currentData()]

    def prepared_image(self):
        """L'image choisie, recadrée à la taille du mode, avec le texte."""
        mode = self._mode()
        size = QtCore.QSize(mode.width, mode.height)
        if self.tx_source is None:
            image = QtGui.QImage(size, QtGui.QImage.Format_RGB888)
            image.fill(QtGui.QColor("#1a2027"))
        else:
            scaled = self.tx_source.scaled(size, QtCore.Qt.KeepAspectRatioByExpanding,
                                           QtCore.Qt.SmoothTransformation)
            x = (scaled.width() - mode.width) // 2
            y = (scaled.height() - mode.height) // 2
            image = scaled.copy(x, y, mode.width, mode.height)
        image = image.convertToFormat(QtGui.QImage.Format_RGB888)
        text = self.text_edit.text().strip()
        if text:
            painter = QtGui.QPainter(image)
            font = QtGui.QFont("Sans", max(mode.height // 9, 12), QtGui.QFont.Bold)
            painter.setFont(font)
            rect = QtCore.QRect(0, 4, mode.width, mode.height // 6)
            path = QtGui.QPainterPath()
            metrics = QtGui.QFontMetrics(font)
            x = (mode.width - metrics.horizontalAdvance(text)) // 2
            path.addText(x, 4 + metrics.ascent(), font, text)
            painter.setRenderHint(QtGui.QPainter.Antialiasing)
            # contour noir autour d'un texte clair, blanc autour d'un texte foncé
            outline = "black" if self.text_color.lightness() > 100 else "white"
            painter.strokePath(path, QtGui.QPen(QtGui.QColor(outline), 4))
            painter.fillPath(path, self.text_color)
            painter.end()
            del rect
        return image

    def choose_text_color(self):
        color = QtWidgets.QColorDialog.getColor(self.text_color, self, "Couleur du texte")
        if color.isValid():
            self.text_color = color
            self._show_text_color()
            self.update_preview()

    def _show_text_color(self):
        self.color_button.setStyleSheet(
            f"background: {self.text_color.name()}; border: 1px solid #546e7a;")

    def update_preview(self):
        mode = self._mode()
        self.tx_view.set_image(self.prepared_image())
        minutes, seconds = divmod(round(mode.duration), 60)
        if self.tx_source is None:
            self.tx_info.setText("Choisis une image à émettre.")
        else:
            self.tx_info.setText(f"{mode.name} : la radio émettra pendant "
                                 f"{minutes} min {seconds:02d} s.")

    def send(self):
        if self.tx_source is None:
            self.tx_info.setText("Choisis d'abord une image.")
            return
        mode = self._mode()
        audio = sstv.encode(mode, to_array(self.prepared_image()), rate=self.main.audio_rate)
        if self.tx_wav:
            try:
                os.remove(self.tx_wav)
            except OSError:
                pass
        handle, self.tx_wav = tempfile.mkstemp(prefix="sstv-", suffix=".wav")
        os.close(handle)
        with wave.open(self.tx_wav, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self.main.audio_rate)
            w.writeframes((audio * 32767).astype("<i2").tobytes())
        self.main.transmit_file(self.tx_wav)
