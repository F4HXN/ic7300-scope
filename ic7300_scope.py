#!/usr/bin/env python3
"""IC-7300 Scope : S-mètre en bargraph, spectre audio et waterfall.

Le son est lu sur la carte « USB Audio CODEC » de la radio via parec,
le S-mètre, la fréquence et le mode sont lus en CI-V sur le port USB.
"""
import argparse
import datetime
import json
import math
import os
import queue
import subprocess
import sys
import threading
import time
import wave

import numpy as np
import serial
from PyQt5 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

from cw_decoder import CwDecoder
from psk31_decoder import Psk31Decoder

DEFAULT_SOURCE = "alsa_input.usb-Burr-Brown_from_TI_USB_Audio_CODEC-00.iec958-stereo"
DEFAULT_PORT = ("/dev/serial/by-id/usb-Silicon_Labs_CP2102_USB_to_UART_Bridge_"
                "Controller_IC-7300_03009829-if00-port0")

# Flux de l'écoute permanente (~/.config/pipewire/pipewire.conf.d/50-ic7300-loopback.conf)
LOOPBACK_STREAM = "ic7300_loopback_out"


def music_dir():
    try:
        out = subprocess.run(["xdg-user-dir", "MUSIC"], capture_output=True,
                             text=True, timeout=2).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        out = ""
    return out or os.path.expanduser("~/Music")


RECORD_DIR = os.path.join(music_dir(), "IC-7300")

ICON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.svg")

RATE = 48000
FFT_SIZE = 4096
HOP = 1600                      # 30 spectres par seconde
BIN_HZ = RATE / FFT_SIZE
HISTORY = 300                   # lignes du waterfall (10 s)

RIG_ADDR = 0x94                 # adresse CI-V par défaut de l'IC-7300
CTRL_ADDR = 0xE0
MODES = {0x00: "LSB", 0x01: "USB", 0x02: "AM", 0x03: "CW", 0x04: "RTTY",
         0x05: "FM", 0x07: "CW-R", 0x08: "RTTY-R"}
MIN_FREQ = 30_000               # couverture de réception de l'IC-7300
MAX_FREQ = 74_800_000
SPANS = [2500, 5000, 10_000, 25_000, 50_000, 100_000, 250_000, 500_000]
# zone écoutée autour de la fréquence affichée, en kHz (approximative)
PASSBANDS = {"LSB": (-3.0, -0.1), "USB": (0.1, 3.0), "CW": (-0.25, 0.25),
             "CW-R": (-0.25, 0.25), "RTTY": (-0.25, 0.25),
             "RTTY-R": (-0.25, 0.25), "AM": (-3.0, 3.0), "FM": (-7.5, 7.5)}
STEPS = [("10 Hz", 10), ("100 Hz", 100), ("1 kHz", 1000), ("5 kHz", 5000),
         ("10 kHz", 10_000), ("100 kHz", 100_000)]

# Échelle du S-mètre Icom : 0 = S0, 120 = S9, 241 = S9+60 dB
S9 = 120
S9_PLUS_60 = 241


def bcd_le(data):
    """BCD poids faible en premier (fréquence CI-V)."""
    value = 0
    for i, b in enumerate(data):
        value += ((b >> 4) * 10 + (b & 0x0F)) * 100 ** i
    return value


def to_bcd_le(value, size=5):
    return bytes((value // 10 ** (2 * i + 1) % 10) << 4
                 | value // 10 ** (2 * i) % 10 for i in range(size))


def bcd_be(data):
    """BCD poids fort en premier (niveaux CI-V)."""
    value = 0
    for b in data:
        value = value * 100 + (b >> 4) * 10 + (b & 0x0F)
    return value


def s_meter_raw(db_over_s9):
    return S9 + (S9_PLUS_60 - S9) * db_over_s9 / 60


def s_meter_text(raw):
    if raw <= S9:
        return f"S{round(raw * 9 / S9)}"
    return f"S9+{round((raw - S9) * 60 / (S9_PLUS_60 - S9))}"


def format_frequency(hz):
    return f"{hz // 1_000_000}.{(hz // 1000) % 1000:03d}.{hz % 1000:03d}"


def find_loopback_stream():
    """Flux d'écoute radio → PC : (index, volume en %, muet) ou None."""
    try:
        out = subprocess.run(["pactl", "-f", "json", "list", "sink-inputs"],
                             capture_output=True, text=True, timeout=2).stdout
        streams = json.loads(out)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    for stream in streams:
        if stream["properties"].get("node.name") == LOOPBACK_STREAM:
            channel = next(iter(stream["volume"].values()))
            volume = int(channel["value_percent"].rstrip("%"))
            return stream["index"], volume, stream["mute"]
    return None


def parse_frequency(text):
    """« 14.074 » ou « 14,074 » → MHz, « 7074 » → kHz, « 7074000 » → Hz."""
    text = text.strip().replace(",", ".")
    try:
        value = float(text)
    except ValueError:
        return None
    if "." in text or value < 100:
        return round(value * 1_000_000)
    if value < 100_000:
        return round(value * 1000)
    return round(value)


class AudioReader(QtCore.QThread):
    """Lit le son de la radio et calcule un spectre en dBFS."""
    spectrum = QtCore.pyqtSignal(object, float)
    failed = QtCore.pyqtSignal(str)
    decoded = QtCore.pyqtSignal(str)
    decoder_state = QtCore.pyqtSignal(str, float, bool, bool)  # infos, note, signal, actif

    def __init__(self, source):
        super().__init__()
        self.source = source
        self.proc = None
        self._running = True
        self.decoders = {"cw": CwDecoder(RATE), "psk31": Psk31Decoder(RATE)}
        self.decoder = None             # décodeur choisi, ou None
        self._record_lock = threading.Lock()
        self._record = None
        self.recorded_frames = 0

    def run(self):
        cmd = ["parec", "--raw", f"--device={self.source}", "--format=s16le",
               f"--rate={RATE}", "--channels=1", "--latency-msec=20",
               "--client-name=IC-7300 Scope"]
        try:
            self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE)
        except FileNotFoundError:
            self.failed.emit("parec introuvable (paquet pulseaudio-utils)")
            return
        window = np.hanning(FFT_SIZE).astype(np.float32)
        norm = window.sum() / 2
        buf = np.zeros(FFT_SIZE, np.float32)
        nbytes = HOP * 2
        while self._running:
            data = self.proc.stdout.read(nbytes)
            if len(data) < nbytes:
                if self._running:
                    err = self.proc.stderr.read().decode(errors="replace").strip()
                    self.failed.emit(f"Flux audio interrompu {err}".strip())
                break
            with self._record_lock:
                if self._record:
                    self._record.writeframes(data)
                    self.recorded_frames += HOP
            x = np.frombuffer(data, "<i2").astype(np.float32) / 32768
            buf = np.roll(buf, -HOP)
            buf[-HOP:] = x
            mag = np.abs(np.fft.rfft(buf * window)) / norm
            db = 20 * np.log10(mag + 1e-10)
            rms = 20 * np.log10(np.sqrt(np.mean(x * x)) + 1e-10)
            self.spectrum.emit(db.astype(np.float32), float(rms))
            decoder = self.decoder
            if decoder:
                decoder.track_pitch(db, BIN_HZ)
                text = decoder.feed(x)
                if text:
                    self.decoded.emit(text)
                self.decoder_state.emit(decoder.status(), decoder.pitch,
                                        decoder.has_signal, decoder.key_down)

    def start_recording(self, path):
        """Enregistre le son de la radio tel quel : WAV mono 16 bits."""
        record = wave.open(path, "wb")
        record.setnchannels(1)
        record.setsampwidth(2)
        record.setframerate(RATE)
        with self._record_lock:
            self._record = record
            self.recorded_frames = 0

    def stop_recording(self):
        with self._record_lock:
            record, self._record = self._record, None
        if record:
            record.close()

    def stop(self):
        self.stop_recording()
        self._running = False
        if self.proc:
            self.proc.terminate()
        self.wait(2000)


class CivReader(QtCore.QThread):
    """Interroge la radio en CI-V : S-mètre, fréquence et mode."""
    meter = QtCore.pyqtSignal(int)
    frequency = QtCore.pyqtSignal(int)
    mode = QtCore.pyqtSignal(str)
    other_frequency = QtCore.pyqtSignal(int)     # VFO non sélectionné
    other_mode = QtCore.pyqtSignal(str)
    af_gain = QtCore.pyqtSignal(int)            # volume de la radio, 0 à 255
    scope = QtCore.pyqtSignal(object, int, int) # balayage, bords bas/haut (Hz)
    span = QtCore.pyqtSignal(int)               # demi-largeur du scope (Hz)
    status = QtCore.pyqtSignal(str)

    def __init__(self, port, baud):
        super().__init__()
        self.port = port
        self.baud = baud
        self._running = True
        self._pending_freq = None
        self._pending_mode = None
        self._pending_af = None
        self._vfo_ops = queue.Queue()
        self._filter = 0x01                 # FIL1 tant que la radio n'a rien dit
        self._pending_span = None
        self._wave = None
        self._wave_edges = None
        # réglages du scope de la radio, remis à la fermeture
        self._scope_mode = None
        self._scope_speed = None

    def run(self):
        while self._running:
            try:
                with serial.Serial(self.port, self.baud, timeout=0.05) as ser:
                    self.status.emit("CAT connecté")
                    self._buf = bytearray()
                    self._scope_setup(ser)
                    self._poll(ser)
                    self._scope_restore(ser)
            except (serial.SerialException, OSError) as e:
                self.status.emit(f"CAT indisponible : {e}")
                for _ in range(20):
                    if not self._running:
                        return
                    time.sleep(0.1)

    def _send(self, ser, *payload):
        ser.write(bytes([0xFE, 0xFE, RIG_ADDR, CTRL_ADDR, *payload, 0xFD]))

    def set_frequency(self, hz):
        """Appelable depuis l'interface : seule la dernière valeur est envoyée."""
        self._pending_freq = hz

    def set_mode(self, name):
        self._pending_mode = name

    def set_af_gain(self, level):
        self._pending_af = level

    def set_span(self, hz):
        self._pending_span = hz

    def vfo_operation(self, code):
        """0x00 VFO A, 0x01 VFO B, 0xA0 A=B, 0xB0 échange A/B."""
        self._vfo_ops.put(code)

    def _poll(self, ser):
        # La radio ignore une commande reçue pendant qu'elle répond à la
        # précédente : on attend chaque réponse avant d'envoyer la suivante.
        n = 0
        while self._running:
            changed = False
            hz, self._pending_freq = self._pending_freq, None
            if hz is not None:
                self._command(ser, 0x05, *to_bcd_le(hz))
                changed = True
            mode, self._pending_mode = self._pending_mode, None
            if mode is not None:
                code = next(c for c, m in MODES.items() if m == mode)
                # sans le filtre, la radio repasse sur son filtre par défaut
                self._command(ser, 0x06, code, self._filter)
                changed = True
            while not self._vfo_ops.empty():
                self._command(ser, 0x07, self._vfo_ops.get())
                changed = True
            level, self._pending_af = self._pending_af, None
            if level is not None:
                self._command(ser, 0x14, 0x01, level // 100,
                              (level // 10 % 10) << 4 | level % 10)
            span, self._pending_span = self._pending_span, None
            if span is not None:
                self._command(ser, 0x27, 0x15, 0x00, *to_bcd_le(span))
                self._transact(ser, (0x27, 0x15, 0x00), (0x27,))
            if changed:
                n = 0
            if n % 10 == 0:
                queries = [(0x03,), (0x04,), (0x25, 0x01), (0x26, 0x01),
                           (0x14, 0x01), (0x15, 0x02)]
            else:
                queries = [(0x15, 0x02)]
            n += 1
            for query in queries:
                self._transact(ser, query, (query[0],))
            time.sleep(0.05)

    def _scope_setup(self, ser):
        """Scope de la radio centré sur la fréquence, données envoyées en CI-V."""
        if self._scope_mode is None:
            self._transact(ser, (0x27, 0x14, 0x00), (0x27,))
            self._transact(ser, (0x27, 0x1A, 0x00), (0x27,))
        self._command(ser, 0x27, 0x14, 0x00, 0x00)      # mode centré
        self._command(ser, 0x27, 0x1A, 0x00, 0x00)      # balayage rapide
        self._command(ser, 0x27, 0x11, 0x01)            # données vers le CI-V
        self._transact(ser, (0x27, 0x15, 0x00), (0x27,))

    def _scope_restore(self, ser):
        self._command(ser, 0x27, 0x11, 0x00)
        if self._scope_mode is not None:
            self._command(ser, 0x27, 0x14, 0x00, self._scope_mode)
        if self._scope_speed is not None:
            self._command(ser, 0x27, 0x1A, 0x00, self._scope_speed)

    def _command(self, ser, *payload):
        reply = self._transact(ser, payload, (0xFB, 0xFA))
        if reply != 0xFB:
            self.status.emit("La radio a refusé la commande")

    def _transact(self, ser, payload, expected):
        """Envoie une trame et attend une réponse parmi `expected`."""
        self._send(ser, *payload)
        deadline = time.monotonic() + 0.15
        while time.monotonic() < deadline:
            self._buf += ser.read(64)
            while 0xFD in self._buf:
                end = self._buf.index(0xFD)
                cmd = self._handle(bytes(self._buf[:end]))
                del self._buf[:end + 1]
                if cmd in expected:
                    return cmd
        return None

    def _handle(self, frame):
        """Traite une trame CI-V et renvoie sa commande."""
        start = frame.find(b"\xfe\xfe")
        if start < 0:
            return None
        frame = frame[start:].lstrip(b"\xfe")
        if len(frame) < 3 or frame[1] != RIG_ADDR:
            return None                     # écho de nos propres commandes
        cmd, data = frame[2], frame[3:]
        if cmd == 0x27 and len(data) >= 4 and data[0] == 0x00:
            self._wave_part(bcd_be(data[2:3]), bcd_be(data[3:4]), data[4:])
            return None                     # données du scope, pas une réponse
        if cmd in (0x00, 0x03) and len(data) == 5:
            self.frequency.emit(bcd_le(data))
        elif cmd in (0x01, 0x04) and data:
            self.mode.emit(MODES.get(data[0], f"mode {data[0]:02X}"))
            if len(data) >= 2:
                self._filter = data[1]
        elif cmd == 0x25 and len(data) == 6 and data[0] == 0x01:
            self.other_frequency.emit(bcd_le(data[1:]))
        elif cmd == 0x26 and len(data) >= 2 and data[0] == 0x01:
            self.other_mode.emit(MODES.get(data[1], f"mode {data[1]:02X}"))
        elif cmd == 0x14 and len(data) == 3 and data[0] == 0x01:
            self.af_gain.emit(bcd_be(data[1:]))
        elif cmd == 0x15 and len(data) == 3 and data[0] == 0x02:
            self.meter.emit(bcd_be(data[1:]))
        elif cmd == 0x27 and len(data) == 3 and data[0] == 0x14:
            if self._scope_mode is None:
                self._scope_mode = data[2]
        elif cmd == 0x27 and len(data) == 3 and data[0] == 0x1A:
            if self._scope_speed is None:
                self._scope_speed = data[2]
        elif cmd == 0x27 and len(data) == 7 and data[0] == 0x15:
            self.span.emit(bcd_le(data[2:]))
        return cmd

    def _wave_part(self, seq, total, payload):
        """Un balayage arrive en `total` trames ; la première donne les bords."""
        if seq == 1:
            self._wave = []
            self._wave_edges = None
            if len(payload) >= 11:
                a, b = bcd_le(payload[1:6]), bcd_le(payload[6:11])
                # mode centré : fréquence centrale et demi-largeur
                self._wave_edges = (a - b, a + b) if payload[0] == 0 else (a, b)
        elif self._wave is not None and seq == len(self._wave) + 2:
            self._wave.append(payload)
            if seq == total and self._wave_edges:
                points = np.frombuffer(b"".join(self._wave), np.uint8)
                self.scope.emit(points.astype(np.float32), *self._wave_edges)
                self._wave = None
        else:
            self._wave = None               # trame perdue : on attend le suivant

    def stop(self):
        self._running = False
        self.wait(2000)


class FrequencyDisplay(QtWidgets.QLabel):
    """Affichage de la fréquence, réglable à la molette."""
    tuned = QtCore.pyqtSignal(int)

    def __init__(self):
        super().__init__("--.---.---")
        self.setStyleSheet("font: bold 30pt monospace; color: #4fc3f7;")
        self.setToolTip("Molette : changer la fréquence")
        self.setCursor(QtCore.Qt.SizeVerCursor)
        self._angle = 0

    def wheelEvent(self, event):
        self._angle += event.angleDelta().y()
        steps = int(self._angle / 120)
        if steps:
            self._angle -= steps * 120
            self.tuned.emit(steps)
        event.accept()


class TuningKnob(QtWidgets.QWidget):
    """Bouton de VFO : on le tourne à la souris (glisser) ou à la molette."""
    tuned = QtCore.pyqtSignal(int)
    DETENTS = 40                    # crans par tour

    def __init__(self):
        super().__init__()
        self.setFixedSize(150, 150)
        self.setCursor(QtCore.Qt.OpenHandCursor)
        self.setToolTip("Glisser en tournant ou molette : changer la fréquence")
        self.angle = 0.0
        self._accum = 0.0
        self._last = None
        self._wheel = 0

    def _angle_at(self, pos):
        c = self.rect().center()
        return math.degrees(math.atan2(pos.y() - c.y(), pos.x() - c.x()))

    def _rotate(self, degrees):
        self.angle = (self.angle + degrees) % 360
        self._accum += degrees
        per_detent = 360 / self.DETENTS
        steps = int(self._accum / per_detent)
        if steps:
            self._accum -= steps * per_detent
            self.tuned.emit(steps)
        self.update()

    def mousePressEvent(self, event):
        self._last = self._angle_at(event.pos())
        self.setCursor(QtCore.Qt.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        if self._last is None:
            return
        a = self._angle_at(event.pos())
        delta = (a - self._last + 180) % 360 - 180
        self._last = a
        self._rotate(delta)

    def mouseReleaseEvent(self, event):
        self._last = None
        self.setCursor(QtCore.Qt.OpenHandCursor)

    def wheelEvent(self, event):
        self._wheel += event.angleDelta().y()
        steps = int(self._wheel / 120)
        if steps:
            self._wheel -= steps * 120
            self._rotate(steps * 360 / self.DETENTS)
        event.accept()

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        c = QtCore.QPointF(self.rect().center())
        r = min(self.width(), self.height()) / 2 - 2
        # graduations
        p.setPen(QtGui.QPen(QtGui.QColor("#455a64"), 2))
        for i in range(self.DETENTS):
            a = math.radians(i * 360 / self.DETENTS)
            p.drawLine(c + QtCore.QPointF(math.cos(a), math.sin(a)) * (r - 1),
                       c + QtCore.QPointF(math.cos(a), math.sin(a)) * (r - 8))
        # corps du bouton
        body = r - 12
        grad = QtGui.QRadialGradient(c - QtCore.QPointF(body / 3, body / 3), body * 1.6)
        grad.setColorAt(0, QtGui.QColor("#5f6b75"))
        grad.setColorAt(1, QtGui.QColor("#15191d"))
        p.setPen(QtGui.QPen(QtGui.QColor("#0b0f13"), 2))
        p.setBrush(grad)
        p.drawEllipse(c, body, body)
        # trou pour le doigt
        a = math.radians(self.angle - 90)
        dimple = c + QtCore.QPointF(math.cos(a), math.sin(a)) * body * 0.62
        p.setPen(QtGui.QPen(QtGui.QColor("#0b0f13"), 1))
        p.setBrush(QtGui.QColor("#22292f"))
        p.drawEllipse(dimple, body * 0.17, body * 0.17)
        # repère
        p.setPen(QtGui.QPen(QtGui.QColor("#4fc3f7"), 3,
                            cap=QtCore.Qt.RoundCap))
        p.drawLine(c + QtCore.QPointF(math.cos(a), math.sin(a)) * body * 0.86,
                   c + QtCore.QPointF(math.cos(a), math.sin(a)) * body * 0.97)


class BarGraph(QtWidgets.QWidget):
    """Bargraph à segments avec mémoire de crête."""

    def __init__(self, minimum, maximum, ticks, red_from, segments=48):
        super().__init__()
        self.minimum = minimum
        self.maximum = maximum
        self.ticks = ticks
        self.red_from = red_from
        self.segments = segments
        self.value = minimum
        self.peak = minimum
        self.peak_time = 0.0
        self.setMinimumHeight(44)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                           QtWidgets.QSizePolicy.Fixed)

    def setValue(self, value):
        value = min(max(value, self.minimum), self.maximum)
        self.value = value
        now = time.monotonic()
        if value >= self.peak:
            self.peak = value
            self.peak_time = now
        elif now - self.peak_time > 1.0:
            step = (self.maximum - self.minimum) * 0.015
            self.peak = max(value, self.peak - step)
        self.update()

    def _fraction(self, value):
        return (value - self.minimum) / (self.maximum - self.minimum)

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        w, h = self.width(), self.height()
        margin = 6
        bar_h = h - 18
        inner_w = w - 2 * margin
        p.fillRect(self.rect(), QtGui.QColor("#0b0f13"))
        seg_w = inner_w / self.segments
        lit_fraction = self._fraction(self.value)
        green, red = QtGui.QColor("#3ddc84"), QtGui.QColor("#ff5252")
        for i in range(self.segments):
            centre = (i + 0.5) / self.segments
            seg_value = self.minimum + centre * (self.maximum - self.minimum)
            color = red if seg_value >= self.red_from else green
            if centre > lit_fraction:
                color = color.darker(500)
            p.fillRect(QtCore.QRectF(margin + i * seg_w + 1, 4, seg_w - 2,
                                     bar_h - 4), color)
        if self.peak > self.minimum:
            x = margin + self._fraction(self.peak) * inner_w
            p.fillRect(QtCore.QRectF(x - 1.5, 2, 3, bar_h), QtGui.QColor("#ffd54a"))
        font = p.font()
        font.setPointSizeF(8)
        p.setFont(font)
        p.setPen(QtGui.QColor("#b0bec5"))
        for value, label in self.ticks:
            x = margin + self._fraction(value) * inner_w
            x = min(max(x, 24), w - 24)
            p.drawText(QtCore.QRectF(x - 24, bar_h + 1, 48, 16),
                       QtCore.Qt.AlignCenter, label)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, args):
        super().__init__()
        self.setWindowTitle("IC-7300 Scope")
        self.audio = AudioReader(args.source)   # démarré une fois l'écran prêt
        self.resize(1100, 760)
        self.fmax = args.fmax
        self.avg = None
        self.waterfall = None
        self.wf_low = -100.0
        self.freq_hz = None
        self.other_hz = None
        self.other_mode = ""

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)

        # Fréquence, mode et état
        top = QtWidgets.QHBoxLayout()
        self.freq_label = FrequencyDisplay()
        self.freq_label.tuned.connect(self.on_tuned)
        self.step_combo = QtWidgets.QComboBox()
        for label, hz in STEPS:
            self.step_combo.addItem(label, hz)
        self.step_combo.setCurrentIndex(2)
        self.step_combo.setToolTip("Pas de la molette sur la fréquence")
        self.freq_entry = QtWidgets.QLineEdit()
        self.freq_entry.setPlaceholderText("7074 ou 14.074")
        self.freq_entry.setToolTip("kHz, MHz (avec un point) ou Hz, puis Entrée")
        self.freq_entry.setMaximumWidth(130)
        self.freq_entry.returnPressed.connect(self.on_freq_entered)
        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.addItems(MODES.values())
        self.mode_combo.setStyleSheet("font: bold 16pt; color: #ffd54a;")
        self.mode_combo.activated[str].connect(self.on_mode_selected)
        self.cursor_label = QtWidgets.QLabel("")
        self.cursor_label.setStyleSheet("font: 12pt monospace;")
        top.addWidget(self.freq_label)
        top.addWidget(QtWidgets.QLabel("MHz"))
        top.addSpacing(10)
        top.addWidget(self.step_combo)
        top.addSpacing(20)
        top.addWidget(self.mode_combo)
        top.addSpacing(20)
        top.addWidget(QtWidgets.QLabel("Aller à"))
        top.addWidget(self.freq_entry)
        top.addStretch()
        top.addWidget(self.cursor_label)
        left = QtWidgets.QVBoxLayout()
        left.addLayout(top)

        # Bargraphs
        bars = QtWidgets.QGridLayout()
        s_ticks = [(i * S9 / 9, str(i)) for i in range(1, 10, 2)]
        s_ticks += [(s_meter_raw(d), f"+{d}") for d in (20, 40, 60)]
        self.s_meter = BarGraph(0, S9_PLUS_60, s_ticks, red_from=S9)
        self.s_value = QtWidgets.QLabel("S0")
        level_ticks = [(v, f"{v}") for v in range(-60, 1, 10)]
        self.level_meter = BarGraph(-60, 0, level_ticks, red_from=-6)
        self.level_value = QtWidgets.QLabel("")
        for label in (self.s_value, self.level_value):
            label.setMinimumWidth(90)
            label.setStyleSheet("font: bold 14pt monospace;")
        bars.addWidget(QtWidgets.QLabel("S-mètre"), 0, 0)
        bars.addWidget(self.s_meter, 0, 1)
        bars.addWidget(self.s_value, 0, 2)
        bars.addWidget(QtWidgets.QLabel("Audio dBFS"), 1, 0)
        bars.addWidget(self.level_meter, 1, 1)
        bars.addWidget(self.level_value, 1, 2)
        left.addLayout(bars)
        left.addStretch()

        # VFO
        self.active_vfo = None              # la radio ne dit pas lequel est actif
        vfo = QtWidgets.QGridLayout()
        self.vfo_label = QtWidgets.QLabel("VFO ?")
        self.vfo_label.setAlignment(QtCore.Qt.AlignCenter)
        self.vfo_label.setStyleSheet("font: bold 14pt; color: #4fc3f7;")
        self.knob = TuningKnob()
        self.knob.tuned.connect(self.on_tuned)
        self.other_label = QtWidgets.QLabel("")
        self.other_label.setAlignment(QtCore.Qt.AlignCenter)
        self.other_label.setStyleSheet("font: 11pt monospace; color: #90a4ae;")
        self.other_label.setToolTip("L'autre VFO")
        vfo.addWidget(self.vfo_label, 0, 0, 1, 4)
        vfo.addWidget(self.knob, 1, 0, 1, 4, QtCore.Qt.AlignCenter)
        buttons = [("A", "Passer sur le VFO A", 0x00),
                   ("B", "Passer sur le VFO B", 0x01),
                   ("A/B", "Basculer entre VFO A et B", 0xB0),
                   ("A=B", "Copier le VFO actif dans l'autre", 0xA0)]
        for col, (text, tip, code) in enumerate(buttons):
            button = QtWidgets.QPushButton(text)
            button.setToolTip(tip)
            button.setMinimumWidth(30)
            button.clicked.connect(lambda _, c=code: self.on_vfo_operation(c))
            vfo.addWidget(button, 2, col)
        vfo.addWidget(self.other_label, 3, 0, 1, 4)
        header = QtWidgets.QHBoxLayout()
        header.addLayout(left, stretch=1)
        header.addSpacing(10)
        header.addLayout(vfo)
        layout.addLayout(header)

        # Spectre et waterfall
        graphics = pg.GraphicsLayoutWidget()
        self.spec_plot = graphics.addPlot(row=0, col=0)
        self.spec_plot.setYRange(-130, 0, padding=0)
        self.spec_plot.setMouseEnabled(x=False, y=False)
        self.spec_plot.showGrid(x=True, y=True, alpha=0.25)
        self.spec_plot.setLabel("left", "dBFS")
        self.spec_plot.hideButtons()
        self.spec_curve = self.spec_plot.plot(
            pen=pg.mkPen("#4fc3f7", width=1), fillLevel=-140,
            brush=(79, 195, 247, 60))
        self.wf_plot = graphics.addPlot(row=1, col=0)
        self.wf_plot.setXLink(self.spec_plot)
        self.wf_plot.setMouseEnabled(x=False, y=False)
        self.wf_plot.hideAxis("left")
        self.wf_plot.invertY(True)
        self.wf_plot.hideButtons()
        self.wf_image = pg.ImageItem()
        self.wf_image.setLookupTable(
            pg.colormap.get("inferno").getLookupTable(nPts=256))
        self.wf_plot.addItem(self.wf_image)
        graphics.ci.layout.setRowStretchFactor(0, 1)
        graphics.ci.layout.setRowStretchFactor(1, 2)
        self.cursor_lines = []
        self.tuned_lines = []
        self.passbands = []
        for plot in (self.spec_plot, self.wf_plot):
            line = pg.InfiniteLine(angle=90, pen=pg.mkPen("#ffffff80"))
            line.setVisible(False)
            plot.addItem(line, ignoreBounds=True)
            self.cursor_lines.append(line)
            band = pg.LinearRegionItem(movable=False, brush=(255, 213, 74, 40),
                                       pen=pg.mkPen(None))
            plot.addItem(band, ignoreBounds=True)
            self.passbands.append(band)
            tuned = pg.InfiniteLine(angle=90, pen=pg.mkPen("#ffd54a", width=1))
            plot.addItem(tuned, ignoreBounds=True)
            self.tuned_lines.append(tuned)
        self.pitch_lines = []
        for plot in (self.spec_plot, self.wf_plot):
            line = pg.InfiniteLine(angle=90, pen=pg.mkPen("#3ddc84", width=1,
                                                          style=QtCore.Qt.DashLine))
            line.setVisible(False)
            plot.addItem(line, ignoreBounds=True)
            self.pitch_lines.append(line)
        graphics.scene().sigMouseMoved.connect(self.on_mouse_moved)
        graphics.scene().sigMouseClicked.connect(self.on_mouse_clicked)
        layout.addWidget(graphics, stretch=1)

        # Réglages
        controls = QtWidgets.QHBoxLayout()
        self.view_combo = QtWidgets.QComboBox()
        self.view_combo.addItem("RF (scope radio)", "rf")
        self.view_combo.addItem("Audio", "audio")
        self.view_combo.setToolTip("RF : centré sur la fréquence écoutée, "
                                   "clic pour s'y caler. Audio : le son reçu.")
        self.span_combo = QtWidgets.QComboBox()
        for hz in SPANS:
            label = f"±{hz / 1000:g} kHz".replace(".", ",")
            self.span_combo.addItem(label, hz)
        self.span_combo.setToolTip("Largeur du scope de la radio")
        self.span_combo.activated.connect(self.on_span_selected)
        self.fmax_spin = QtWidgets.QSpinBox()
        self.fmax_spin.setRange(500, RATE // 2)
        self.fmax_spin.setSingleStep(500)
        self.fmax_spin.setSuffix(" Hz")
        self.fmax_spin.setValue(self.fmax)
        self.fmax_spin.valueChanged.connect(self.set_fmax)
        self.auto_check = QtWidgets.QCheckBox("Niveaux auto")
        self.auto_check.setChecked(True)
        self.floor_spin = QtWidgets.QDoubleSpinBox()
        self.floor_spin.setRange(-160, 160)
        self.floor_spin.setValue(self.wf_low)
        self.floor_spin.valueChanged.connect(self.on_floor_changed)
        self.range_spin = QtWidgets.QDoubleSpinBox()
        self.range_spin.setRange(10, 160)
        controls.addWidget(self.view_combo)
        controls.addWidget(QtWidgets.QLabel("Largeur"))
        controls.addWidget(self.span_combo)
        controls.addWidget(self.fmax_spin)
        controls.addSpacing(20)
        controls.addWidget(self.auto_check)
        controls.addWidget(QtWidgets.QLabel("Plancher"))
        controls.addWidget(self.floor_spin)
        controls.addWidget(QtWidgets.QLabel("Plage"))
        controls.addWidget(self.range_spin)
        controls.addStretch()

        # Volumes : le son de la radio sur le PC, et le volume de la radio
        self.pc_volume = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.pc_volume.setRange(0, 150)
        self.pc_volume.setFixedWidth(130)
        self.pc_volume.setToolTip("Volume du son de la radio sur le PC")
        self.pc_volume_label = QtWidgets.QLabel()
        self.pc_volume_label.setMinimumWidth(50)
        self.mute_check = QtWidgets.QCheckBox("Muet")
        self.af_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.af_slider.setRange(0, 255)
        self.af_slider.setFixedWidth(130)
        self.af_slider.setToolTip("Volume du haut-parleur de la radio (AF). "
                                  "Ne change pas le son envoyé au PC.")
        self.af_label = QtWidgets.QLabel()
        self.af_label.setMinimumWidth(50)
        controls.addWidget(QtWidgets.QLabel("Volume PC"))
        controls.addWidget(self.pc_volume)
        controls.addWidget(self.pc_volume_label)
        controls.addWidget(self.mute_check)
        controls.addSpacing(20)
        controls.addWidget(QtWidgets.QLabel("AF radio"))
        controls.addWidget(self.af_slider)
        controls.addWidget(self.af_label)

        self.loopback = find_loopback_stream()
        if self.loopback:
            _, volume, muted = self.loopback
            self.pc_volume.setValue(volume)
            self.mute_check.setChecked(muted)
        else:
            for widget in (self.pc_volume, self.mute_check):
                widget.setEnabled(False)
                widget.setToolTip("Écoute radio → PC introuvable dans PipeWire")
        self.pc_volume_label.setText(f"{self.pc_volume.value()} %")
        self.pc_volume.valueChanged.connect(self.on_pc_volume)
        self.mute_check.toggled.connect(self.on_mute)
        self.af_slider.setEnabled(False)    # activé quand la radio répond
        self.af_slider.valueChanged.connect(self.on_af_slider)
        layout.addLayout(controls)

        # Décodeurs CW et PSK31
        decoder_row = QtWidgets.QHBoxLayout()
        self.decoder_combo = QtWidgets.QComboBox()
        self.decoder_combo.addItem("Aucun", None)
        self.decoder_combo.addItem("CW", "cw")
        self.decoder_combo.addItem("PSK31", "psk31")
        self.decoder_auto = QtWidgets.QCheckBox("Calage auto")
        self.decoder_auto.setChecked(True)
        self.decoder_led = QtWidgets.QLabel("●")
        self.decoder_info = QtWidgets.QLabel("")
        self.decoder_info.setStyleSheet("font: 11pt monospace;")
        decoder_clear = QtWidgets.QPushButton("Effacer")
        decoder_row.addWidget(QtWidgets.QLabel("Décodeur"))
        decoder_row.addWidget(self.decoder_combo)
        decoder_row.addWidget(self.decoder_auto)
        decoder_row.addWidget(self.decoder_led)
        decoder_row.addWidget(self.decoder_info)
        decoder_row.addStretch()
        decoder_row.addWidget(decoder_clear)
        decoder_row.addSpacing(20)
        self.record_button = QtWidgets.QPushButton("● Enregistrer")
        self.record_button.setCheckable(True)
        self.record_button.setToolTip(f"Enregistre le son de la radio dans {RECORD_DIR}")
        self.record_button.toggled.connect(self.on_record)
        self.record_label = QtWidgets.QLabel("")
        self.record_label.setStyleSheet("font: 11pt monospace;")
        record_folder = QtWidgets.QPushButton("Dossier")
        record_folder.setToolTip("Ouvrir le dossier des enregistrements")
        record_folder.clicked.connect(self.open_record_folder)
        decoder_row.addWidget(self.record_button)
        decoder_row.addWidget(self.record_label)
        decoder_row.addWidget(record_folder)
        self.record_timer = QtCore.QTimer(self)
        self.record_timer.setInterval(500)
        self.record_timer.timeout.connect(self.update_record_label)
        self.record_path = None
        self.decoder_text = QtWidgets.QPlainTextEdit()
        self.decoder_text.setReadOnly(True)
        self.decoder_text.setMaximumBlockCount(500)
        self.decoder_text.setFixedHeight(70)
        self.decoder_text.setStyleSheet("font: bold 14pt monospace; color: #3ddc84;")
        self.decoder_text.setVisible(False)
        layout.addLayout(decoder_row)
        layout.addWidget(self.decoder_text)
        self.decoder_combo.currentIndexChanged.connect(self.on_decoder_changed)
        self.decoder_auto.toggled.connect(self.on_decoder_auto)
        decoder_clear.clicked.connect(self.decoder_text.clear)
        self._set_decoder_led(False, False)

        self.view = None
        self.scope_edges = None
        self.view_combo.currentIndexChanged.connect(self.on_view_changed)
        self.on_view_changed()

        self.audio.spectrum.connect(self.on_spectrum)
        self.audio.failed.connect(self.statusBar().showMessage)
        self.audio.decoded.connect(self.on_decoded)
        self.audio.decoder_state.connect(self.on_decoder_state)
        self.on_decoder_changed()
        self.audio.start()

        self.civ = None
        if args.no_cat:
            self.statusBar().showMessage("CAT désactivé")
        else:
            self.civ = CivReader(args.port, args.baud)
            self.civ.meter.connect(self.on_meter)
            self.civ.frequency.connect(self.on_frequency)
            self.civ.mode.connect(self.on_mode)
            self.civ.other_frequency.connect(self.on_other_frequency)
            self.civ.other_mode.connect(self.on_other_mode)
            self.civ.af_gain.connect(self.on_af_gain)
            self.civ.scope.connect(self.on_scope)
            self.civ.span.connect(self.on_span)
            self.civ.status.connect(self.statusBar().showMessage)
            self.civ.start()

    def visible_bins(self):
        return int(self.fmax / BIN_HZ) + 1

    def on_view_changed(self):
        self.view = self.view_combo.currentData()
        rf = self.view == "rf"
        self.span_combo.setVisible(rf)
        self.fmax_spin.setVisible(not rf)
        for item in self.tuned_lines + self.passbands:
            item.setVisible(rf)
        self._show_pitch_lines()
        self.avg = None
        if rf:
            # niveaux du scope de la radio : 0 à 160
            self.spec_plot.setYRange(0, 160, padding=0)
            self.spec_plot.setLabel("left", "Niveau")
            self.spec_curve.setFillLevel(0)
            self.wf_plot.setLabel("bottom", "Fréquence (kHz)")
            self.floor_spin.setSuffix("")
            self.range_spin.setSuffix("")
            self.wf_low = 20.0
            self.range_spin.setValue(90)
            self.scope_edges = None
            self._reset_waterfall(1, 0, 1)
            self._update_markers()
        else:
            self.spec_plot.setYRange(-130, 0, padding=0)
            self.spec_plot.setLabel("left", "dBFS")
            self.spec_curve.setFillLevel(-140)
            self.wf_plot.setLabel("bottom", "Fréquence audio", units="Hz")
            self.floor_spin.setSuffix(" dB")
            self.range_spin.setSuffix(" dB")
            self.wf_low = -100.0
            self.range_spin.setValue(60)
            self.set_fmax(self.fmax)
        self.floor_spin.setValue(self.wf_low)

    def _reset_waterfall(self, width, x0, x1):
        self.waterfall = np.full((HISTORY, width), -160, np.float32)
        self.wf_image.setImage(self.waterfall, autoLevels=False,
                               levels=(-160, -100))
        self.wf_image.setRect(QtCore.QRectF(x0, 0, x1 - x0, HISTORY))
        self.spec_plot.setXRange(x0, x1, padding=0)
        self.wf_plot.setYRange(0, HISTORY, padding=0)

    def set_fmax(self, fmax):
        self.fmax = fmax
        if self.view == "audio":
            self._reset_waterfall(self.visible_bins(), 0,
                                  self.visible_bins() * BIN_HZ)
            self.spec_plot.setXRange(0, fmax, padding=0)

    def _add_waterfall_row(self, row, visible):
        if self.auto_check.isChecked():
            target = float(np.percentile(visible, 20)) - 5
            self.wf_low += 0.05 * (target - self.wf_low)
            self.floor_spin.blockSignals(True)
            self.floor_spin.setValue(self.wf_low)
            self.floor_spin.blockSignals(False)
        self.waterfall = np.roll(self.waterfall, 1, axis=0)
        self.waterfall[0] = row
        self.wf_image.setImage(self.waterfall, autoLevels=False,
                               levels=(self.wf_low,
                                       self.wf_low + self.range_spin.value()))

    def on_scope(self, points, low, high):
        if self.view != "rf":
            return
        n = len(points)
        if self.scope_edges != (low, high) or self.waterfall.shape[1] != n:
            old = self.scope_edges
            if old and old[1] - old[0] == high - low and self.waterfall.shape[1] == n:
                # la fréquence a bougé : on décale l'historique d'autant
                shift = round((old[0] - low) / ((high - low) / n))
                self.waterfall = np.roll(self.waterfall, shift, axis=1)
                if shift > 0:
                    self.waterfall[:, :shift] = -160
                elif shift < 0:
                    self.waterfall[:, shift:] = -160
                self.wf_image.setRect(QtCore.QRectF(low / 1000, 0,
                                                    (high - low) / 1000, HISTORY))
                self.spec_plot.setXRange(low / 1000, high / 1000, padding=0)
            else:
                self._reset_waterfall(n, low / 1000, high / 1000)
            self.scope_edges = (low, high)
            self.avg = None
        self.avg = points if self.avg is None else 0.5 * self.avg + 0.5 * points
        step = (high - low) / n
        self.spec_curve.setData((low + (np.arange(n) + 0.5) * step) / 1000, self.avg)
        self._add_waterfall_row(points, self.avg)

    def on_span(self, hz):
        index = self.span_combo.findData(hz)
        if index >= 0:
            self.span_combo.setCurrentIndex(index)

    def on_span_selected(self):
        if self.civ:
            self.civ.set_span(self.span_combo.currentData())

    def _update_markers(self):
        if self.freq_hz is None:
            return
        f = self.freq_hz / 1000
        low, high = PASSBANDS.get(self.mode_combo.currentText(), (-1.5, 1.5))
        for line in self.tuned_lines:
            line.setPos(f)
        for band in self.passbands:
            band.setRegion((f + low, f + high))

    def on_floor_changed(self, value):
        if not self.auto_check.isChecked():
            self.wf_low = value

    def on_spectrum(self, db, rms):
        self.level_meter.setValue(rms)
        self.level_value.setText(f"{rms:5.1f}")
        if self.view != "audio":
            return
        self.avg = db if self.avg is None else 0.6 * self.avg + 0.4 * db
        k = self.visible_bins()
        visible = self.avg[:k]
        self.spec_curve.setData(np.arange(k) * BIN_HZ, visible)
        self._add_waterfall_row(db[:k], visible)

    def on_frequency(self, hz):
        self.freq_hz = hz
        self.freq_label.setText(format_frequency(hz))
        self._update_markers()

    def on_mode(self, name):
        self.mode_combo.setCurrentText(name)
        self._update_markers()

    def tune_to(self, hz):
        if not self.civ:
            return
        if not MIN_FREQ <= hz <= MAX_FREQ:
            self.statusBar().showMessage(
                f"Hors de la plage de la radio ({MIN_FREQ // 1000}–"
                f"{MAX_FREQ // 1_000_000} MHz)")
            return
        self.on_frequency(hz)
        self.civ.set_frequency(hz)

    def on_tuned(self, steps):
        if self.freq_hz is None:
            return
        step = self.step_combo.currentData()
        base = self.freq_hz // step * step
        if steps < 0 and base != self.freq_hz:
            steps += 1                      # on s'aligne d'abord sur le pas
        self.tune_to(base + steps * step)

    def on_freq_entered(self):
        hz = parse_frequency(self.freq_entry.text())
        if hz is None:
            self.statusBar().showMessage("Fréquence non comprise")
            return
        self.freq_entry.clear()
        self.tune_to(hz)

    def on_mode_selected(self, name):
        self._update_markers()
        if self.civ:
            self.civ.set_mode(name)

    def on_other_frequency(self, hz):
        self.other_hz = hz
        self._show_other()

    def on_other_mode(self, name):
        self.other_mode = name
        self._show_other()

    def _show_other(self):
        name = {"A": "B", "B": "A"}.get(self.active_vfo, "autre")
        text = "--.---.---"
        if self.other_hz is not None:
            text = format_frequency(self.other_hz)
        self.other_label.setText(f"{name} : {text} {self.other_mode}".rstrip())

    def on_vfo_operation(self, code):
        if not self.civ:
            return
        if code == 0x00:
            self.active_vfo = "A"
        elif code == 0x01:
            self.active_vfo = "B"
        elif code == 0xB0 and self.active_vfo:
            self.active_vfo = "B" if self.active_vfo == "A" else "A"
        self.vfo_label.setText(f"VFO {self.active_vfo or '?'}")
        self._show_other()
        self.civ.vfo_operation(code)

    def _pactl_stream(self, *args):
        """Commande pactl sur le flux d'écoute ; le retrouve s'il a changé."""
        for attempt in range(2):
            if attempt or not self.loopback:
                self.loopback = find_loopback_stream()
            if not self.loopback:
                break
            cmd = ["pactl", args[0], str(self.loopback[0]), *args[1:]]
            if subprocess.run(cmd, capture_output=True).returncode == 0:
                return
        self.statusBar().showMessage("Écoute radio → PC introuvable dans PipeWire")

    def on_pc_volume(self, value):
        self.pc_volume_label.setText(f"{value} %")
        self._pactl_stream("set-sink-input-volume", f"{value}%")

    def on_mute(self, muted):
        self._pactl_stream("set-sink-input-mute", "1" if muted else "0")

    def on_af_gain(self, level):
        self.af_slider.setEnabled(True)
        if not self.af_slider.isSliderDown():
            self.af_slider.blockSignals(True)
            self.af_slider.setValue(level)
            self.af_slider.blockSignals(False)
        self.af_label.setText(f"{round(self.af_slider.value() * 100 / 255)} %")

    def on_af_slider(self, level):
        self.af_label.setText(f"{round(level * 100 / 255)} %")
        if self.civ:
            self.civ.set_af_gain(level)

    def on_meter(self, raw):
        self.s_meter.setValue(raw)
        self.s_value.setText(s_meter_text(raw))

    def _x_at(self, pos):
        """Abscisse sous la souris (kHz en RF, Hz en audio), ou None."""
        for plot in (self.spec_plot, self.wf_plot):
            if plot.sceneBoundingRect().contains(pos):
                x = plot.vb.mapSceneToView(pos).x()
                if self.view == "audio" and 0 <= x <= self.fmax:
                    return x
                if (self.view == "rf" and self.scope_edges
                        and self.scope_edges[0] <= x * 1000 <= self.scope_edges[1]):
                    return x
        return None

    def on_mouse_moved(self, pos):
        x = self._x_at(pos)
        for line in self.cursor_lines:
            line.setVisible(x is not None)
            if x is not None:
                line.setPos(x)
        if x is None:
            self.cursor_label.setText("")
        elif self.view == "rf":
            self.cursor_label.setText(format_frequency(round(x * 1000)))
        else:
            self.cursor_label.setText(f"{x:6.0f} Hz")

    def on_mouse_clicked(self, event):
        x = self._x_at(event.scenePos())
        if x is None:
            return
        decoder = self.decoder_combo.currentData()
        if self.view == "audio":
            if decoder:
                self._set_decoder_pitch(x)
            return
        # vue RF : en PSK31, un clic dans la bande écoutée choisit le signal
        audio = self._audio_offset(x * 1000)
        if decoder == "psk31" and audio is not None:
            self._set_decoder_pitch(audio)
            return
        step = self.step_combo.currentData()
        self.tune_to(round(x * 1000 / step) * step)

    def _audio_offset(self, rf_hz):
        """Fréquence audio d'un signal RF, s'il tombe dans la bande en BLU."""
        if self.freq_hz is None:
            return None
        mode = self.mode_combo.currentText()
        audio = {"USB": rf_hz - self.freq_hz, "LSB": self.freq_hz - rf_hz}.get(mode)
        if audio is not None and 100 <= audio <= 3000:
            return audio
        return None

    def _set_decoder_pitch(self, hz):
        decoder = self.audio.decoder
        if decoder is None:
            return
        if isinstance(decoder, CwDecoder):
            self.decoder_auto.setChecked(False)     # la note ne bouge plus
        decoder.set_pitch(hz)
        self._move_pitch_lines(hz)

    def on_decoder_changed(self):
        name = self.decoder_combo.currentData()
        decoder = self.audio.decoders.get(name)
        if decoder:
            decoder.auto_pitch = self.decoder_auto.isChecked()
        self.audio.decoder = decoder
        self.decoder_text.setVisible(decoder is not None)
        self.decoder_auto.setToolTip({
            "cw": "Cherche la note CW la plus forte entre 300 et 1200 Hz. "
                  "Clic sur le spectre audio pour la choisir.",
            "psk31": "Se recale sur le signal à ± 25 Hz du clic, puis le suit. "
                     "Clic sur le spectre audio, ou en RF dans la bande écoutée.",
        }.get(name, ""))
        self.decoder_led.setToolTip("Allumé quand le signal est manipulé"
                                    if name == "cw" else "Allumé quand le signal est décodable")
        if decoder is None:
            self.decoder_info.setText("")
            self._set_decoder_led(False, False)
        else:
            self._move_pitch_lines(decoder.pitch)
        self._show_pitch_lines()

    def on_decoder_auto(self, auto):
        if self.audio.decoder:
            self.audio.decoder.auto_pitch = auto

    def _show_pitch_lines(self):
        visible = self.audio.decoder is not None
        for line in self.pitch_lines:
            line.setVisible(visible)

    def _move_pitch_lines(self, audio_hz):
        """Repère du décodeur : en Hz audio, ou en kHz RF dans la vue RF."""
        x = audio_hz
        if self.view == "rf":
            mode = self.mode_combo.currentText()
            sign = {"USB": 1, "LSB": -1}.get(mode)
            if self.freq_hz is None or sign is None:
                for line in self.pitch_lines:
                    line.setVisible(False)
                return
            x = (self.freq_hz + sign * audio_hz) / 1000
        for line in self.pitch_lines:
            line.setVisible(self.audio.decoder is not None)
            line.setPos(x)

    def _set_decoder_led(self, signal, active):
        color = "#3ddc84" if active else ("#2e5d41" if signal else "#37474f")
        self.decoder_led.setStyleSheet(f"font: 16pt; color: {color};")

    def on_decoded(self, text):
        self.decoder_text.moveCursor(QtGui.QTextCursor.End)
        self.decoder_text.insertPlainText(text)
        self.decoder_text.ensureCursorVisible()

    def on_decoder_state(self, info, pitch, signal, active):
        if self.audio.decoder is None:
            return
        self._set_decoder_led(signal, active)
        self.decoder_info.setText(info)
        self._move_pitch_lines(pitch)

    def on_record(self, recording):
        if recording:
            os.makedirs(RECORD_DIR, exist_ok=True)
            name = datetime.datetime.now().strftime("%Y-%m-%d_%Hh%Mm%S")
            if self.freq_hz is not None:
                name += f"_{format_frequency(self.freq_hz)}_{self.mode_combo.currentText()}"
            self.record_path = os.path.join(RECORD_DIR, name + ".wav")
            try:
                self.audio.start_recording(self.record_path)
            except OSError as e:
                self.statusBar().showMessage(f"Enregistrement impossible : {e}")
                self.record_button.setChecked(False)
                return
            self.record_button.setText("■ Arrêter")
            self.record_button.setStyleSheet("background: #c62828; color: white;")
            self.record_timer.start()
            self.update_record_label()
        else:
            self.audio.stop_recording()
            self.record_timer.stop()
            self.record_button.setText("● Enregistrer")
            self.record_button.setStyleSheet("")
            self.record_label.setText("")
            if self.record_path:
                self.statusBar().showMessage(f"Enregistré : {self.record_path}")

    def update_record_label(self):
        seconds = self.audio.recorded_frames // RATE
        size = self.audio.recorded_frames * 2 / 1e6
        self.record_label.setText(f"{seconds // 60:02d}:{seconds % 60:02d} · {size:.1f} Mo")

    def open_record_folder(self):
        os.makedirs(RECORD_DIR, exist_ok=True)
        subprocess.Popen(["xdg-open", RECORD_DIR])

    def closeEvent(self, event):
        self.audio.stop()
        if self.civ:
            self.civ.stop()
        super().closeEvent(event)


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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=DEFAULT_SOURCE,
                        help="source PulseAudio/PipeWire du son de la radio")
    parser.add_argument("--port", default=DEFAULT_PORT, help="port série CI-V")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--fmax", type=int, default=3500,
                        help="fréquence audio maximale affichée (Hz)")
    parser.add_argument("--no-cat", action="store_true",
                        help="ne pas ouvrir le port CI-V (s'il est pris par "
                             "un autre logiciel)")
    args = parser.parse_args()

    pg.setConfigOptions(imageAxisOrder="row-major", antialias=True,
                        background="#101418")
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("IC-7300 Scope")
    app.setDesktopFileName("ic7300-scope")      # lien avec le menu des applications
    app.setWindowIcon(QtGui.QIcon(ICON))
    app.setStyle("Fusion")
    app.setPalette(dark_palette())
    window = MainWindow(args)
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
