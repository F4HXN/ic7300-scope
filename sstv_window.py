"""Fenêtre SSTV : réception à gauche, émission d'une image à droite."""
import datetime
import os
import tempfile
import wave

import numpy as np
from PyQt5 import QtCore, QtGui, QtWidgets

import sstv


def pictures_dir():
    import subprocess
    try:
        out = subprocess.run(["xdg-user-dir", "PICTURES"], capture_output=True,
                             text=True, timeout=2).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        out = ""
    return out or os.path.expanduser("~/Pictures")


SSTV_DIR = os.path.join(pictures_dir(), "SSTV")


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

    def _update(self):
        if self._image is None:
            return
        self.setPixmap(QtGui.QPixmap.fromImage(self._image).scaled(
            self.size() - QtCore.QSize(2, 2), QtCore.Qt.KeepAspectRatio,
            QtCore.Qt.SmoothTransformation))


class SstvWindow(QtWidgets.QWidget):
    def __init__(self, main):
        super().__init__(None, QtCore.Qt.Window)
        self.main = main
        self.setWindowTitle("SSTV — IC-7300 Scope")
        self.resize(1100, 560)
        self.decoder = sstv.SstvDecoder(main.audio_rate)
        self.tx_source = None           # image choisie (QImage)
        self.tx_wav = None
        layout = QtWidgets.QHBoxLayout(self)

        # Réception
        rx = QtWidgets.QGroupBox("Réception")
        rx_layout = QtWidgets.QVBoxLayout(rx)
        self.rx_view = ImageView((480, 384))
        self.rx_status = QtWidgets.QLabel("")
        self.rx_status.setStyleSheet("font: 11pt monospace;")
        self.rx_saved = QtWidgets.QLabel("")
        rx_buttons = QtWidgets.QHBoxLayout()
        reset = QtWidgets.QPushButton("Réinitialiser")
        reset.setToolTip("Abandonner l'image en cours et attendre la suivante")
        reset.clicked.connect(self.decoder.reset)
        save = QtWidgets.QPushButton("Enregistrer l'image")
        save.clicked.connect(self.save_current)
        folder = QtWidgets.QPushButton("Dossier")
        folder.clicked.connect(lambda: self._open_folder(SSTV_DIR))
        for button in (reset, save, folder):
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
        choose = QtWidgets.QPushButton("Choisir une image…")
        choose.clicked.connect(self.choose_image)
        form.addRow("Mode", self.mode_combo)
        form.addRow("Texte", self.text_edit)
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

    # -- marche / arrêt du décodeur avec la fenêtre

    def showEvent(self, event):
        self.main.audio.sstv = self.decoder
        self.timer.start()
        super().showEvent(event)

    def closeEvent(self, event):
        self.main.audio.sstv = None
        self.timer.stop()
        super().closeEvent(event)

    # -- réception

    def refresh(self):
        image = self.decoder.image
        if image is not None:
            self.rx_view.set_image(to_qimage(image))
        self.rx_status.setText(self.decoder.status())
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
            painter.strokePath(path, QtGui.QPen(QtGui.QColor("black"), 4))
            painter.fillPath(path, QtGui.QColor("white"))
            painter.end()
            del rect
        return image

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
