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
import signal
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
from rtty_decoder import RttyDecoder
from ft8_decoder import Ft4Decoder, Ft8Decoder
from sstv_window import SstvWindow

DEFAULT_SOURCE = "alsa_input.usb-Burr-Brown_from_TI_USB_Audio_CODEC-00.iec958-stereo"
RADIO_SINK = "alsa_output.usb-Burr-Brown_from_TI_USB_Audio_CODEC-00.iec958-stereo"
DEFAULT_PORT = ("/dev/serial/by-id/usb-Silicon_Labs_CP2102_USB_to_UART_Bridge_"
                "Controller_IC-7300_03009829-if00-port0")

# Flux de l'écoute permanente (~/.config/pipewire/pipewire.conf.d/50-ic7300-loopback.conf)
LOOPBACK_STREAM = "ic7300_loopback_out"
# Radio sur un autre PC (--remote) : serveur CAT (cat_server.py) et son
# PipeWire en réseau (pipewire-pulse.conf.d/50-reseau.conf) sur ce PC-là
CAT_TCP_PORT = 7300
PULSE_TCP_PORT = 4713
REMOTE_RX = "ic7300_distant_rx"
REMOTE_TX = "ic7300_distant_tx"


def music_dir():
    try:
        out = subprocess.run(["xdg-user-dir", "MUSIC"], capture_output=True,
                             text=True, timeout=2).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        out = ""
    return out or os.path.expanduser("~/Music")


RECORD_DIR = os.path.join(music_dir(), "IC-7300")

# réglages de la radio avant que l'appli les change (scope, mode data pendant
# l'émission) ; le fichier n'existe que tant qu'il reste quelque chose à
# remettre, y compris si l'appli a planté
STATE_FILE = os.path.join(os.environ.get("XDG_CONFIG_HOME")
                          or os.path.expanduser("~/.config"),
                          "ic7300-scope", "etat-origine.json")
# dernière fréquence et dernier mode utilisés sur chaque bande
BANDS_FILE = os.path.join(os.path.dirname(STATE_FILE), "bandes.json")
# bandes émission de l'IC-7300 : nom, limites (Hz), fréquence et mode de départ
BANDS = [("160m", 1_810_000, 2_000_000, 1_850_000, "LSB"),
         ("80m", 3_500_000, 3_800_000, 3_700_000, "LSB"),
         ("60m", 5_351_500, 5_366_500, 5_360_000, "USB"),
         ("40m", 7_000_000, 7_200_000, 7_100_000, "LSB"),
         ("30m", 10_100_000, 10_150_000, 10_120_000, "CW"),
         ("20m", 14_000_000, 14_350_000, 14_200_000, "USB"),
         ("17m", 18_068_000, 18_168_000, 18_130_000, "USB"),
         ("15m", 21_000_000, 21_450_000, 21_250_000, "USB"),
         ("12m", 24_890_000, 24_990_000, 24_950_000, "USB"),
         ("10m", 28_000_000, 29_700_000, 28_500_000, "USB"),
         ("6m", 50_000_000, 52_000_000, 50_150_000, "USB"),
         ("4m", 70_000_000, 70_500_000, 70_200_000, "USB")]


def band_of(hz):
    for name, low, high, _, _ in BANDS:
        if low <= hz <= high:
            return name
    return None


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def update_state(**values):
    state = load_state()
    state.update(values)
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def remove_state(*keys):
    state = load_state()
    for key in keys:
        state.pop(key, None)
    if state:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    else:
        try:
            os.remove(STATE_FILE)
        except OSError:
            pass


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
DATA_MODES = ("LSB", "USB")     # proposés aussi en mode data (LSB-D, USB-D)
MODE_NAMES = ["LSB", "LSB-D", "USB", "USB-D", "AM", "CW", "RTTY", "FM",
              "CW-R", "RTTY-R"]
TX_TIMEOUT = 180                # s : l'émission est coupée au-delà
# repères de l'IC-7300 pour les mesures en émission
PO_POINTS = [(0, 0), (143, 50), (213, 100), (255, 120)]           # % de puissance
SWR_POINTS = [(0, 1.0), (48, 1.5), (80, 2.0), (120, 3.0), (255, 6.0)]
COMP_POINTS = [(0, 0.0), (130, 15.0), (241, 30.0), (255, 32.0)]     # dB
ID_POINTS = [(0, 0.0), (97, 10.0), (146, 15.0), (241, 25.0)]      # A
ALC_ZONE = 120                  # au-delà, l'ALC sort de sa zone sur la radio
MIN_FREQ = 30_000               # couverture de réception de l'IC-7300
MAX_FREQ = 74_800_000
SPANS = [2500, 5000, 10_000, 25_000, 50_000, 100_000, 250_000, 500_000]
# largeur de FIL1, FIL2, FIL3 en kHz (réglages d'usine de l'IC-7300 ; chaque
# mode a les siens : FIL3 vaut 1,8 kHz en BLU mais 3 kHz en AM)
FILTER_WIDTHS = {"LSB": (3.0, 2.4, 1.8), "USB": (3.0, 2.4, 1.8),
                 "AM": (9.0, 6.0, 3.0), "FM": (15.0, 10.0, 7.0),
                 "CW": (1.2, 0.5, 0.25), "CW-R": (1.2, 0.5, 0.25),
                 "RTTY": (2.4, 0.5, 0.25), "RTTY-R": (2.4, 0.5, 0.25)}


def passband(mode, filt):
    """Zone écoutée autour de la fréquence affichée, en kHz (approximative)."""
    width = FILTER_WIDTHS.get(mode, (3.0, 3.0, 3.0))[(filt or 1) - 1]
    if mode == "USB":
        return 0.1, 0.1 + width
    if mode == "LSB":
        return -0.1 - width, -0.1
    return -width / 2, width / 2


def base_mode(name):
    """Le mode sans le mode data : USB-D → USB."""
    return name.removesuffix("-D")


def mode_name(code, data):
    name = MODES.get(code, f"mode {code:02X}")
    return name + "-D" if data and name in DATA_MODES else name
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


def remote_audio(host):
    """Crée ici l'entrée et la sortie son de la radio branchée sur `host`,
    plus l'écoute sur la sortie par défaut. Renvoie les modules à décharger."""
    unload_remote_audio(None)       # restes d'une session plantée
    server = f"server=tcp:{host}:{PULSE_TCP_PORT}"
    commands = [
        ["module-tunnel-source", server, f"source={DEFAULT_SOURCE}",
         f"source_name={REMOTE_RX}"],
        ["module-tunnel-sink", server, f"sink={RADIO_SINK}", f"sink_name={REMOTE_TX}"],
        ["module-loopback", f"source={REMOTE_RX}", "latency_msec=60",
         "source_dont_move=true", f"sink_input_properties=node.name={LOOPBACK_STREAM}"],
    ]
    modules = []
    for command in commands:
        try:
            out = subprocess.run(["pactl", "load-module", *command],
                                 capture_output=True, text=True, timeout=10)
        except subprocess.TimeoutExpired:
            out = None
        if out is None or out.returncode != 0:
            unload_remote_audio(modules)
            error = out.stderr.strip() if out else "pas de réponse"
            raise RuntimeError(f"{command[0]} : {error or 'échec'}")
        modules.append(out.stdout.strip())
    return modules


def unload_remote_audio(modules):
    """Décharge les modules donnés, ou tous ceux de --remote (None)."""
    if modules is None:
        out = subprocess.run(["pactl", "list", "modules", "short"],
                             capture_output=True, text=True).stdout
        modules = [line.split()[0] for line in out.splitlines()
                   if REMOTE_RX in line or REMOTE_TX in line]
    for module in reversed(modules):
        subprocess.run(["pactl", "unload-module", module], capture_output=True)


def list_microphones(exclude):
    """Entrées son du PC (nom, description), sans les retours et sans la radio."""
    try:
        out = subprocess.run(["pactl", "-f", "json", "list", "sources"],
                             capture_output=True, text=True, timeout=2).stdout
        sources = json.loads(out)
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    # « description » vaut « (null) » quand elle contient des accents
    return [(src["name"], src["properties"].get("device.description") or src["name"])
            for src in sources
            if not src["name"].endswith(".monitor") and src["name"] != exclude]


def sink_volume(sink):
    try:
        out = subprocess.run(["pactl", "get-sink-volume", sink],
                             capture_output=True, text=True, timeout=2).stdout
        return int(out.split("%")[0].split()[-1])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None


def piecewise(raw, points):
    """Conversion d'un niveau CI-V (0 à 255) par interpolation entre repères."""
    xs, ys = zip(*points)
    return float(np.interp(raw, xs, ys))


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
        self.decoders = {"cw": CwDecoder(RATE), "psk31": Psk31Decoder(RATE),
                         "rtty": RttyDecoder(RATE), "ft8": Ft8Decoder(RATE),
                         "ft4": Ft4Decoder(RATE)}
        self.decoder = None             # décodeur choisi, ou None
        self.sstv = None                # décodeur SSTV quand sa fenêtre est ouverte
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
            sstv = self.sstv
            if sstv:
                sstv.feed(x)
            decoder = self.decoder
            if decoder:
                decoder.track_pitch(db, BIN_HZ)
                text = decoder.feed(x)
                if text:
                    self.decoded.emit(text)
                # types Python purs : un numpy.bool fait planter emit() et
                # arrête ce fil, donc tous les décodeurs
                self.decoder_state.emit(decoder.status(), float(decoder.pitch or 0.0),
                                        bool(decoder.has_signal), bool(decoder.key_down))

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
        self.decoders["ft8"].close()
        self.decoders["ft4"].close()
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
    level = QtCore.pyqtSignal(int, int)         # réglage 0x14 (RF 0x02, SQL 0x03), 0 à 255
    tuner = QtCore.pyqtSignal(int)              # boîte d'accord : 0 arrêt, 1 marche, 2 accord
    monitor = QtCore.pyqtSignal(bool)           # MONI : écoute de sa propre émission
    filter = QtCore.pyqtSignal(int)             # filtre FIL1, FIL2 ou FIL3 (1 à 3)
    power = QtCore.pyqtSignal(int)              # puissance réglée, 0 à 255
    key_speed = QtCore.pyqtSignal(int)          # vitesse du manipulateur, 0 à 255
    transmitting = QtCore.pyqtSignal(bool)      # la radio émet
    tx_meters = QtCore.pyqtSignal(int, int, int)  # puissance, ROS, ALC (0 à 255)
    usb_mod = QtCore.pyqtSignal(int)            # menu USB MOD Level, 0 à 255
    comp = QtCore.pyqtSignal(int)               # compression, 0 à 255
    drain = QtCore.pyqtSignal(int)              # courant de l'ampli (Id), 0 à 255
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
        self._pending_levels = {}       # réglages 0x14 : sous-commande → valeur
        self._commands = queue.Queue()
        self._tuner = None
        self._filter = 0x01                 # FIL1 tant que la radio n'a rien dit
        self._pending_span = None
        self._pending_power = None
        self._pending_key_speed = None
        self._break_in = None           # BK-IN : 0 arrêt, 1 semi, 2 full
        self._pending_usb_mod = None
        self._pending_ptt = None
        self._ptt = False               # émission demandée par l'appli
        self._tx = False                # la radio dit qu'elle émet
        self._data_state = None         # (data, filtre) lu sur la radio
        self._mode_code = None          # dernier mode lu (code CI-V)
        self._data_off_mod = None       # menu DATA OFF MOD lu sur la radio
        self._tx_data = False           # émission en mode data (numérique)
        self._tx_meters = [0, 0, 0]
        self._wave = None
        self._wave_edges = None
        # réglages du scope de la radio, remis à la fermeture
        self._scope_mode = None
        self._scope_speed = None

    def run(self):
        while self._running:
            try:
                # chemin du port, ou socket://hôte:port (cat_server.py)
                with serial.serial_for_url(self.port, self.baud, timeout=0.05,
                                           exclusive=True) as ser:
                    self.status.emit("CAT connecté")
                    self._buf = bytearray()
                    self._recover(ser)
                    self._scope_setup(ser)
                    self._poll(ser)
                    self._release(ser)
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

    def set_level(self, sub, level):
        """Réglage 0x14 de la radio : 0x02 gain RF, 0x03 squelch."""
        self._pending_levels[sub] = level

    def set_span(self, hz):
        self._pending_span = hz

    def vfo_operation(self, code):
        """0x00 VFO A, 0x01 VFO B, 0xA0 A=B, 0xB0 échange A/B."""
        self._commands.put((0x07, code))

    def tune(self):
        """Lance l'accord de la boîte d'accord interne (la radio émet)."""
        self._commands.put((0x1C, 0x01, 0x02))

    def set_atu(self, on):
        self._commands.put((0x1C, 0x01, 0x01 if on else 0x00))

    def set_monitor(self, on):
        self._commands.put((0x16, 0x45, 0x01 if on else 0x00))

    def set_filter(self, number):
        """FIL1 à FIL3 : même mode, autre filtre (en mode data, celui du data)."""
        self._filter = number
        if self._data_state is not None and self._data_state[0]:
            self._commands.put((0x1A, 0x06, 0x01, number))
        elif self._mode_code is not None:
            self._commands.put((0x06, self._mode_code, number))

    def set_power(self, level):
        self._pending_power = level

    def set_key_speed(self, level):
        self._pending_key_speed = level

    def send_cw(self, text):
        """Texte émis en CW par le manipulateur de la radio (mode CW).

        Sans BK-IN la radio ne passe pas en émission : on met le semi BK-IN."""
        if self._break_in == 0x00:
            self._commands.put((0x16, 0x47, 0x01))
            self._break_in = 0x01
            self.status.emit("BK-IN mis en semi pour émettre la CW")
        for i in range(0, len(text), 30):       # 30 caractères par trame
            self._commands.put((0x17, *text[i:i + 30].encode("ascii")))

    def stop_cw(self):
        self._commands.put((0x17, 0xFF))

    def set_usb_mod(self, level):
        """Gain de l'entrée USB à l'émission (menu USB MOD Level), 0 à 255."""
        self._pending_usb_mod = level

    def set_ptt(self, on, data=False):
        """Émission (True) ou réception (False).

        data=True passe la radio en mode data (SSTV…) ; sinon le mode reste
        tel quel (phonie) et l'entrée USB est prise hors mode data."""
        if on:
            self._tx_data = data
        self._pending_ptt = on

    def _key(self, ser):
        if self._tx_data:
            self._key_data(ser)
        else:
            self._key_voice(ser)
        self._command(ser, 0x1C, 0x00, 0x01)
        self._ptt = True

    def _key_voice(self, ser):
        # hors mode data, la radio prend le son sur l'entrée du menu
        # DATA OFF MOD (MIC,ACC ici) : on la met sur USB le temps de l'émission
        self._data_off_mod = None
        self._transact(ser, (0x1A, 0x05, 0x00, 0x66), (0x1A,))
        saved = self._data_off_mod
        if saved is not None and saved != 0x03:
            update_state(tx_menu=saved)
            self._command(ser, 0x1A, 0x05, 0x00, 0x66, 0x03)

    def _key_data(self, ser):
        # en mode data, le son vient de l'USB (réglage DATA MOD = USB) :
        # on y passe le temps de l'émission et on note l'état d'avant
        self._data_state = None
        self._transact(ser, (0x1A, 0x06), (0x1A,))
        saved = self._data_state or (0x00, 0x00)
        update_state(tx_data=list(saved))
        if saved[0] == 0x00:
            self._command(ser, 0x1A, 0x06, 0x01, self._filter)

    def _release(self, ser):
        """Repasse en réception et remet les réglages d'avant l'émission."""
        if self._ptt:
            self._command(ser, 0x1C, 0x00, 0x00)
            self._ptt = False
        state = load_state()
        saved = state.get("tx_data")
        if saved:
            if saved[0] == 0x00:
                self._command(ser, 0x1A, 0x06, *saved)
            remove_state("tx_data")
        if state.get("tx_menu") is not None:
            self._command(ser, 0x1A, 0x05, 0x00, 0x66, state["tx_menu"])
            remove_state("tx_menu")

    def _recover(self, ser):
        """Si l'appli a planté en émission, on coupe et on remet le mode data."""
        state = load_state()
        if state.get("tx_data") or state.get("tx_menu") is not None:
            self._command(ser, 0x1C, 0x00, 0x00)
            self._release(ser)
            self.status.emit("Émission coupée : l'appli s'était arrêtée en émission")

    def _poll(self, ser):
        # La radio ignore une commande reçue pendant qu'elle répond à la
        # précédente : on attend chaque réponse avant d'envoyer la suivante.
        n = 0
        while self._running:
            changed = False
            ptt, self._pending_ptt = self._pending_ptt, None
            if ptt is True and not self._ptt:
                self._key(ser)
                changed = True
            elif ptt is False:
                self._release(ser)
                changed = True
            hz, self._pending_freq = self._pending_freq, None
            if hz is not None:
                self._command(ser, 0x05, *to_bcd_le(hz))
                changed = True
            mode, self._pending_mode = self._pending_mode, None
            if mode is not None:
                base = base_mode(mode)
                code = next(c for c, m in MODES.items() if m == base)
                # sans le filtre, la radio repasse sur son filtre par défaut
                self._command(ser, 0x06, code, self._filter)
                if base in DATA_MODES or base in ("AM", "FM"):
                    data = 0x01 if mode.endswith("-D") else 0x00
                    self._command(ser, 0x1A, 0x06, data,
                                  self._filter if data else 0x00)
                self._transact(ser, (0x1A, 0x06), (0x1A,))
                changed = True
            while not self._commands.empty():
                self._command(ser, *self._commands.get())
                changed = True
            level, self._pending_af = self._pending_af, None
            if level is not None:
                self._command(ser, 0x14, 0x01, level // 100,
                              (level // 10 % 10) << 4 | level % 10)
            for sub in list(self._pending_levels):
                level = self._pending_levels.pop(sub)
                self._command(ser, 0x14, sub, level // 100,
                              (level // 10 % 10) << 4 | level % 10)
            power, self._pending_power = self._pending_power, None
            if power is not None:
                self._command(ser, 0x14, 0x0A, power // 100,
                              (power // 10 % 10) << 4 | power % 10)
            speed, self._pending_key_speed = self._pending_key_speed, None
            if speed is not None:
                self._command(ser, 0x14, 0x0C, speed // 100,
                              (speed // 10 % 10) << 4 | speed % 10)
            level, self._pending_usb_mod = self._pending_usb_mod, None
            if level is not None:
                self._command(ser, 0x1A, 0x05, 0x00, 0x65, level // 100,
                              (level // 10 % 10) << 4 | level % 10)
            span, self._pending_span = self._pending_span, None
            if span is not None:
                self._command(ser, 0x27, 0x15, 0x00, *to_bcd_le(span))
                self._transact(ser, (0x27, 0x15, 0x00), (0x27,))
            if changed:
                n = 0
            if self._ptt or self._tx:       # en émission : puissance, ROS, ALC
                queries = [(0x1C, 0x00), (0x15, 0x11), (0x15, 0x12), (0x15, 0x13),
                           (0x15, 0x14), (0x15, 0x16)]
                if n % 10 == 0:
                    queries += [(0x03,), (0x04,), (0x1A, 0x06)]
            elif n % 10 == 0:
                queries = [(0x03,), (0x04,), (0x1A, 0x06), (0x25, 0x01), (0x26, 0x01),
                           (0x14, 0x01), (0x14, 0x02), (0x14, 0x03), (0x14, 0x0A), (0x1C, 0x00),
                           (0x1C, 0x01), (0x1A, 0x05, 0x00, 0x65), (0x16, 0x45),
                           (0x15, 0x02), (0x14, 0x0C), (0x16, 0x47)]
            elif self._tuner == 0x02:       # accord en cours : on suit de près
                queries = [(0x1C, 0x00), (0x1C, 0x01), (0x15, 0x11), (0x15, 0x12)]
            else:
                # état d'émission à chaque tour : PTT de la radio, pédale ou VOX
                # détectés vite pour couper l'écoute PC (larsen)
                queries = [(0x15, 0x02), (0x1C, 0x00)]
            n += 1
            for query in queries:
                if not self._running:
                    break               # fermeture : on remet la radio sans attendre
                self._transact(ser, query, (query[0],))
            time.sleep(0.05)

    def _scope_setup(self, ser):
        """Scope de la radio centré sur la fréquence, données envoyées en CI-V."""
        if self._scope_mode is None:
            saved = load_state().get("scope")
            if saved:                   # la dernière session n'a pas pu les remettre
                self._scope_mode, self._scope_speed = saved
            else:
                self._transact(ser, (0x27, 0x14, 0x00), (0x27,))
                self._transact(ser, (0x27, 0x1A, 0x00), (0x27,))
                if self._scope_mode is not None and self._scope_speed is not None:
                    update_state(scope=[self._scope_mode, self._scope_speed])
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
        remove_state("scope")

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

    def _emit_mode(self):
        if self._mode_code is not None:
            data = self._data_state is not None and self._data_state[0]
            self.mode.emit(mode_name(self._mode_code, data))

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
            self._mode_code = data[0]
            if len(data) >= 2:
                self._filter = data[1]
                self.filter.emit(data[1])
            self._emit_mode()
        elif cmd == 0x25 and len(data) == 6 and data[0] == 0x01:
            self.other_frequency.emit(bcd_le(data[1:]))
        elif cmd == 0x26 and len(data) >= 2 and data[0] == 0x01:
            self.other_mode.emit(mode_name(data[1], len(data) >= 3 and data[2]))
        elif cmd == 0x1C and len(data) == 2 and data[0] == 0x01:
            self._tuner = data[1]
            self.tuner.emit(data[1])
        elif cmd == 0x16 and len(data) == 2 and data[0] == 0x47:
            self._break_in = data[1]
        elif cmd == 0x14 and len(data) == 3 and data[0] == 0x0C:
            self.key_speed.emit(bcd_be(data[1:]))
        elif cmd == 0x16 and len(data) == 2 and data[0] == 0x45:
            self.monitor.emit(data[1] == 0x01)
        elif cmd == 0x1C and len(data) == 2 and data[0] == 0x00:
            self._tx = data[1] == 0x01
            self.transmitting.emit(self._tx)
        elif cmd == 0x1A and len(data) == 3 and data[0] == 0x06:
            self._data_state = (data[1], data[2])
            self._emit_mode()
        elif cmd == 0x1A and len(data) == 5 and data[:3] == b"\x05\x00\x65":
            self.usb_mod.emit(bcd_be(data[3:]))
        elif cmd == 0x1A and len(data) == 4 and data[:3] == b"\x05\x00\x66":
            self._data_off_mod = data[3]
        elif cmd == 0x14 and len(data) == 3 and data[0] == 0x0A:
            self.power.emit(bcd_be(data[1:]))
        elif cmd == 0x15 and len(data) == 3 and data[0] in (0x11, 0x12, 0x13):
            self._tx_meters[data[0] - 0x11] = bcd_be(data[1:])
            if data[0] == 0x13:
                self.tx_meters.emit(*self._tx_meters)
        elif cmd == 0x15 and len(data) == 3 and data[0] == 0x14:
            self.comp.emit(bcd_be(data[1:]))
        elif cmd == 0x15 and len(data) == 3 and data[0] == 0x16:
            self.drain.emit(bcd_be(data[1:]))
        elif cmd == 0x14 and len(data) == 3 and data[0] == 0x01:
            self.af_gain.emit(bcd_be(data[1:]))
        elif cmd == 0x14 and len(data) == 3 and data[0] in (0x02, 0x03):
            self.level.emit(data[0], bcd_be(data[1:]))
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
        # laisser le temps de couper l'émission et de remettre le scope
        self._running = False
        self.wait(6000)


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
        self.audio_rate = RATE
        self.sstv_window = None
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
        self.mode_combo.addItems(MODE_NAMES)
        self.mode_combo.setStyleSheet("font: bold 16pt; color: #ffd54a;")
        self.mode_combo.activated[str].connect(self.on_mode_selected)
        self.filter_num = None
        self.filter_buttons = QtWidgets.QButtonGroup(self)
        filters = QtWidgets.QHBoxLayout()
        filters.setSpacing(2)
        for number in (1, 2, 3):
            button = QtWidgets.QPushButton(f"FIL{number}")
            button.setCheckable(True)
            button.setMinimumWidth(44)
            self.filter_buttons.addButton(button, number)
            filters.addWidget(button)
        self.filter_buttons.idClicked.connect(self.on_filter_selected)
        self._filter_tooltips()
        self.cursor_label = QtWidgets.QLabel("")
        self.cursor_label.setStyleSheet("font: 12pt monospace;")
        top.addWidget(self.freq_label)
        top.addWidget(QtWidgets.QLabel("MHz"))
        top.addSpacing(10)
        top.addWidget(self.step_combo)
        top.addSpacing(20)
        top.addWidget(self.mode_combo)
        top.addLayout(filters)
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
        # Bargraphs d'émission
        tx_bars = QtWidgets.QGridLayout()
        swr_ticks = [(raw, f"{swr:g}") for raw, swr in SWR_POINTS[:-1]] + [(255, "∞")]
        self.swr_meter = BarGraph(0, 255, swr_ticks, red_from=80, segments=24)
        comp_ticks = [(v, f"{v}") for v in (0, 10, 20, 30)]
        self.comp_meter = BarGraph(0, 30, comp_ticks, red_from=20, segments=24)
        alc_ticks = [(v, f"{v}") for v in (0, 50, 100, 150)]
        self.alc_meter = BarGraph(0, 150, alc_ticks, red_from=100, segments=24)
        self.alc_meter.setToolTip("ALC en % de sa zone : rester sous 100")
        id_ticks = [(v, f"{v}") for v in (0, 5, 10, 15, 20, 25)]
        self.id_meter = BarGraph(0, 25, id_ticks, red_from=22, segments=24)
        self.id_meter.setToolTip("Courant consommé par l'ampli (Id)")
        self.swr_value = QtWidgets.QLabel("")
        self.comp_value = QtWidgets.QLabel("")
        self.alc_value = QtWidgets.QLabel("")
        self.id_value = QtWidgets.QLabel("")
        for i, (name, meter, value) in enumerate(
                (("ROS", self.swr_meter, self.swr_value),
                 ("COMP dB", self.comp_meter, self.comp_value),
                 ("ALC %", self.alc_meter, self.alc_value),
                 ("Id", self.id_meter, self.id_value))):
            value.setMinimumWidth(60)
            value.setStyleSheet("font: bold 12pt monospace;")
            row, col = divmod(i, 2)
            tx_bars.addWidget(QtWidgets.QLabel(name), row, 3 * col)
            tx_bars.addWidget(meter, row, 3 * col + 1)
            tx_bars.addWidget(value, row, 3 * col + 2)
        left.addLayout(tx_bars)
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
        self.tune_button = QtWidgets.QPushButton("TUNE")
        self.tune_button.setToolTip("Accorde l'antenne avec la boîte d'accord "
                                    "interne. La radio émet une porteuse.")
        self.tune_button.clicked.connect(self.on_tune)
        self.atu_button = QtWidgets.QPushButton("ATU")
        self.atu_button.setCheckable(True)
        self.atu_button.setToolTip("Boîte d'accord interne en service")
        self.atu_button.clicked.connect(self.on_atu)
        self.monitor_button = QtWidgets.QPushButton("MONI")
        self.monitor_button.setCheckable(True)
        self.monitor_button.setToolTip("Moniteur de la radio : on entend sa propre "
                                       "émission (niveau réglé dans la radio, MONI)")
        self.monitor_button.clicked.connect(self.on_monitor)
        vfo.addWidget(self.tune_button, 4, 0, 1, 2)
        vfo.addWidget(self.atu_button, 4, 2)
        vfo.addWidget(self.monitor_button, 4, 3)

        # Bandes : la radio retrouve la dernière fréquence utilisée sur la bande
        try:
            with open(BANDS_FILE) as f:
                self.band_memory = json.load(f)
        except (OSError, ValueError):
            self.band_memory = {}
        bands = QtWidgets.QGridLayout()
        bands.setSpacing(4)
        self.band_buttons = {}
        for i, (name, low, high, _, _) in enumerate(BANDS):
            button = QtWidgets.QPushButton(name)
            button.setCheckable(True)
            button.setMinimumWidth(52)
            button.setStyleSheet("QPushButton:checked { background: #ffd54a; "
                                 "color: #101418; font-weight: bold; }")
            button.setToolTip(f"{low / 1e6:g} – {high / 1e6:g} MHz")
            button.clicked.connect(lambda _, n=name: self.on_band(n))
            bands.addWidget(button, i // 2, i % 2)
            self.band_buttons[name] = button
        bands.setRowStretch(len(BANDS) // 2, 1)

        header = QtWidgets.QHBoxLayout()
        header.addLayout(left, stretch=1)
        header.addSpacing(10)
        header.addLayout(vfo)
        header.addSpacing(6)
        header.addLayout(bands)
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
        # molette : change la fréquence ; glisser en vue RF : déplace la bande
        self.drag = None                # (x de départ, kHz par pixel, fréquence)
        self.dragged = False
        graphics.viewport().installEventFilter(self)
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
        # gain RF et squelch de la radio
        self.level_sliders = {}
        for sub, name, tip in ((0x02, "RF", "Gain HF de la radio (RF GAIN). Le baisser "
                                "sur une bande bruyante ou un signal très fort."),
                               (0x03, "SQL", "Squelch de la radio : coupe le son sous "
                                "ce niveau. À zéro, toujours ouvert.")):
            slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
            slider.setRange(0, 255)
            slider.setFixedWidth(100)
            slider.setToolTip(tip)
            slider.setEnabled(False)        # activé quand la radio répond
            label = QtWidgets.QLabel()
            label.setMinimumWidth(45)
            slider.valueChanged.connect(
                lambda value, sub=sub: self.on_level_slider(sub, value))
            controls.addSpacing(10)
            controls.addWidget(QtWidgets.QLabel(name))
            controls.addWidget(slider)
            controls.addWidget(label)
            self.level_sliders[sub] = (slider, label)

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
        self.decoder_combo.addItem("RTTY 45", "rtty")
        self.decoder_combo.addItem("FT8", "ft8")
        self.decoder_combo.addItem("FT4", "ft4")
        self.decoder_auto = QtWidgets.QCheckBox("Calage auto")
        self.decoder_auto.setChecked(True)
        self.rtty_invert = QtWidgets.QCheckBox("Inverser")
        self.rtty_invert.setToolTip("Mark sur la tonalité haute. À essayer si "
                                    "le texte reste incohérent.")
        self.rtty_invert.toggled.connect(self.on_rtty_invert)
        self.decoder_led = QtWidgets.QLabel("●")
        self.decoder_info = QtWidgets.QLabel("")
        self.decoder_info.setStyleSheet("font: 11pt monospace;")
        decoder_clear = QtWidgets.QPushButton("Effacer")
        decoder_row.addWidget(QtWidgets.QLabel("Décodeur"))
        decoder_row.addWidget(self.decoder_combo)
        decoder_row.addWidget(self.decoder_auto)
        decoder_row.addWidget(self.rtty_invert)
        decoder_row.addWidget(self.decoder_led)
        decoder_row.addWidget(self.decoder_info)
        self.decoder_led.setMinimumWidth(16)
        decoder_row.addStretch()
        decoder_row.addWidget(decoder_clear)
        sstv_button = QtWidgets.QPushButton("SSTV…")
        sstv_button.setToolTip("Réception et émission d'images SSTV, "
                               "dans une fenêtre à part")
        sstv_button.clicked.connect(self.open_sstv)
        decoder_row.addWidget(sstv_button)
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

        # Émission : micro du PC ou fichier, envoyés à la radio par l'USB
        self.radio_sink = args.sink
        tx_row = QtWidgets.QHBoxLayout()
        self.power_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.power_slider.setRange(0, 100)
        self.power_slider.setFixedWidth(120)
        self.power_slider.setToolTip("Puissance d'émission de la radio")
        self.power_slider.setEnabled(False)     # activé quand la radio répond
        self.power_label = QtWidgets.QLabel("")
        self.power_label.setMinimumWidth(55)
        self.tx_source = QtWidgets.QComboBox()
        for name, description in list_microphones(args.source):
            self.tx_source.addItem(f"Micro : {description}", name)
        self.tx_source.addItem("Fichier audio", "file")
        self.tx_source.setToolTip("Ce qui part à l'émission")
        self.tx_file = None
        self.tx_file_button = QtWidgets.QPushButton("Choisir…")
        self.tx_file_button.clicked.connect(self.choose_tx_file)
        self.tx_level = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.tx_level.setRange(0, 100)
        self.tx_level.setFixedWidth(100)
        self.tx_level.setToolTip("Niveau du son envoyé à la radio. Le régler "
                                 "pour que l'ALC bouge sans dépasser.")
        volume = sink_volume(self.radio_sink)
        self.tx_level.setValue(volume if volume is not None else 50)
        self.tx_level_label = QtWidgets.QLabel(f"{self.tx_level.value()} %")
        self.tx_level_label.setMinimumWidth(40)
        self.tx_level.valueChanged.connect(self.on_tx_level)
        # gain USB de la radio : c'est lui qui fait monter l'ALC
        self.alc_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.alc_slider.setRange(0, 100)
        self.alc_slider.setFixedWidth(100)
        self.alc_slider.setToolTip("Gain de l'entrée USB de la radio (menu USB MOD "
                                   "Level). En émission, le monter jusqu'à ce que "
                                   "l'ALC bouge, sans rester au maximum.")
        self.alc_slider.setEnabled(False)       # activé quand la radio répond
        self.alc_slider_label = QtWidgets.QLabel("")
        self.alc_slider_label.setMinimumWidth(40)
        self.tx_meter_label = QtWidgets.QLabel("")
        self.tx_meter_label.setStyleSheet("font: 11pt monospace;")
        self.ptt_button = QtWidgets.QPushButton("ÉMETTRE")
        self.ptt_button.setCheckable(True)
        self.ptt_button.setMinimumWidth(120)
        self.ptt_button.setToolTip(f"La radio émet. Coupure automatique après "
                                   f"{TX_TIMEOUT // 60} min (ou la durée du "
                                   f"fichier s'il est plus long).")
        self.ptt_button.toggled.connect(self.on_ptt)
        tx_row.addWidget(QtWidgets.QLabel("Puissance"))
        tx_row.addWidget(self.power_slider)
        tx_row.addWidget(self.power_label)
        tx_row.addSpacing(10)
        tx_row.addWidget(self.tx_source)
        tx_row.addWidget(self.tx_file_button)
        tx_row.addSpacing(10)
        tx_row.addWidget(QtWidgets.QLabel("Niveau"))
        tx_row.addWidget(self.tx_level)
        tx_row.addWidget(self.tx_level_label)
        tx_row.addSpacing(10)
        tx_row.addWidget(QtWidgets.QLabel("Réglage ALC"))
        tx_row.addWidget(self.alc_slider)
        tx_row.addWidget(self.alc_slider_label)
        tx_row.addWidget(self.tx_meter_label)
        tx_row.addStretch()
        tx_row.addWidget(self.ptt_button)
        layout.addLayout(tx_row)

        # Émission CW : texte envoyé au manipulateur de la radio (CI-V)
        cw_row = QtWidgets.QHBoxLayout()
        self.cw_text = QtWidgets.QLineEdit()
        self.cw_text.setPlaceholderText("Texte à émettre en CW, Entrée pour envoyer "
                                        "(^AR, ^SK… pour les signes liés)")
        self.cw_text.setStyleSheet("font: 12pt monospace;")
        self.cw_text.returnPressed.connect(self.on_cw_send)
        self.cw_speed = QtWidgets.QSpinBox()
        self.cw_speed.setRange(6, 48)
        self.cw_speed.setSuffix(" wpm")
        self.cw_speed.setToolTip("Vitesse du manipulateur de la radio")
        self.cw_speed.setEnabled(False)         # activé quand la radio répond
        self.cw_speed.valueChanged.connect(self.on_cw_speed)
        self.cw_send_button = QtWidgets.QPushButton("Envoyer")
        self.cw_send_button.clicked.connect(self.on_cw_send)
        self.cw_stop_button = QtWidgets.QPushButton("Stop")
        self.cw_stop_button.setToolTip("Arrête le message en cours")
        self.cw_stop_button.clicked.connect(self.on_cw_stop)
        cw_row.addWidget(QtWidgets.QLabel("CW"))
        cw_row.addWidget(self.cw_text, stretch=1)
        cw_row.addWidget(self.cw_speed)
        cw_row.addWidget(self.cw_send_button)
        cw_row.addWidget(self.cw_stop_button)
        layout.addLayout(cw_row)
        self._cw_enable(False)
        self.tx_source.currentIndexChanged.connect(self.on_tx_source)
        self.on_tx_source()
        self.power_slider.valueChanged.connect(self.on_power_slider)
        self.alc_slider.valueChanged.connect(self.on_alc_slider)
        self.tx_active = False
        self.tx_digital = False         # émission numérique (SSTV) : mode data
        self.tx_started = 0.0
        self.tx_limit = TX_TIMEOUT          # s ; plus pour un fichier plus long
        self.tx_loopback = None             # module PipeWire micro → radio
        self.tx_muted_listen = False        # écoute coupée le temps d'une émission
        self.radio_tx = False               # la radio émet (lu en CI-V)
        self.tx_player = None               # paplay du fichier
        self.tx_timer = QtCore.QTimer(self)
        self.tx_timer.setInterval(250)
        self.tx_timer.timeout.connect(self.check_tx)
        self.decoder_combo.currentIndexChanged.connect(self.on_decoder_changed)
        self.decoder_auto.toggled.connect(self.on_decoder_auto)
        decoder_clear.clicked.connect(self.decoder_text.clear)
        self._set_decoder_led(False, False)

        self.view = None
        self.scope_edges = None
        self.scope_points = 0
        self.audio_db = None
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
            self.civ.filter.connect(self.on_filter)
            self.civ.other_frequency.connect(self.on_other_frequency)
            self.civ.other_mode.connect(self.on_other_mode)
            self.civ.af_gain.connect(self.on_af_gain)
            self.civ.level.connect(self.on_level)
            self.civ.tuner.connect(self.on_tuner)
            self.civ.monitor.connect(self.monitor_button.setChecked)
            self.civ.power.connect(self.on_power)
            self.civ.key_speed.connect(self.on_key_speed)
            self.civ.transmitting.connect(self.on_transmitting)
            self.civ.tx_meters.connect(self.on_tx_meters)
            self.civ.usb_mod.connect(self.on_usb_mod)
            self.civ.comp.connect(self.on_comp)
            self.civ.drain.connect(self.on_drain)
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
            self.scope_points = n
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
        low, high = passband(base_mode(self.mode_combo.currentText()),
                             self.filter_num)
        for line in self.tuned_lines:
            line.setPos(f)
        for band in self.passbands:
            band.setRegion((f + low, f + high))

    def on_floor_changed(self, value):
        if not self.auto_check.isChecked():
            self.wf_low = value

    def on_spectrum(self, db, rms):
        # spectre audio gardé aussi en vue RF : sert à caler le décodeur au clic
        self.audio_db = db if self.audio_db is None else 0.8 * self.audio_db + 0.2 * db
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
        band = band_of(hz)
        for name, button in self.band_buttons.items():
            button.setChecked(name == band)

    def _remember_band(self):
        """Note la fréquence et le mode actuels pour la bande en cours."""
        band = self.freq_hz and band_of(self.freq_hz)
        if band:
            self.band_memory[band] = [self.freq_hz, self.mode_combo.currentText()]
            try:
                os.makedirs(os.path.dirname(BANDS_FILE), exist_ok=True)
                with open(BANDS_FILE, "w") as f:
                    json.dump(self.band_memory, f)
            except OSError:
                pass

    def on_band(self, name):
        if not self.civ or self.tx_active:
            self.band_buttons[name].setChecked(name == band_of(self.freq_hz or 0))
            return
        self._remember_band()
        default = next(b for b in BANDS if b[0] == name)
        hz, mode = self.band_memory.get(name, (default[3], default[4]))
        if mode in MODE_NAMES and mode != self.mode_combo.currentText():
            self.mode_combo.setCurrentText(mode)
            self.civ.set_mode(mode)
        self.tune_to(hz)

    def on_mode(self, name):
        self.mode_combo.setCurrentText(name)
        self._cw_enable(self.civ is not None and name in ("CW", "CW-R"))
        self._filter_tooltips()
        self._update_markers()

    def on_filter(self, number):
        self.filter_num = number
        button = self.filter_buttons.button(number)
        if button:
            button.setChecked(True)
        self._update_markers()

    def on_filter_selected(self, number):
        if self.civ:
            self.civ.set_filter(number)
        self.on_filter(number)

    def _filter_tooltips(self):
        mode = base_mode(self.mode_combo.currentText())
        widths = FILTER_WIDTHS.get(mode)
        for number in (1, 2, 3):
            text = f"Filtre {number} de la radio"
            if widths:
                width = widths[number - 1]
                text += (f" : {width:g} kHz en {mode}" if width >= 1
                         else f" : {width * 1000:g} Hz en {mode}")
                text += " (réglage d'usine)"
            self.filter_buttons.button(number).setToolTip(text)

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
        self._filter_tooltips()
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

    def on_power(self, raw):
        self.power_slider.setEnabled(True)
        if not self.power_slider.isSliderDown():
            self.power_slider.blockSignals(True)
            self.power_slider.setValue(round(raw * 100 / 255))
            self.power_slider.blockSignals(False)
        self.power_label.setText(f"≈ {self.power_slider.value()} W")

    def on_power_slider(self, percent):
        self.power_label.setText(f"≈ {percent} W")
        if self.civ:
            self.civ.set_power(round(percent * 255 / 100))

    def _cw_enable(self, on):
        for widget in (self.cw_text, self.cw_send_button, self.cw_stop_button):
            widget.setEnabled(on)
        self.cw_text.setToolTip("" if on else "Passer la radio en CW pour émettre")

    def on_key_speed(self, raw):
        self.cw_speed.setEnabled(True)
        if not self.cw_speed.hasFocus():
            self.cw_speed.blockSignals(True)
            self.cw_speed.setValue(round(6 + raw * 42 / 255))
            self.cw_speed.blockSignals(False)

    def on_cw_speed(self, wpm):
        if self.civ:
            self.civ.set_key_speed(round((wpm - 6) * 255 / 42))

    def on_cw_send(self):
        # caractères que la radio sait émettre ; ^ lie deux lettres (^AR)
        text = "".join(c for c in self.cw_text.text().upper()
                       if c.isascii() and (c.isalnum() or c in " /?.-,:'()=+\"@^"))
        text = " ".join(text.split())
        if not text or not self.civ:
            return
        self.civ.send_cw(text)
        self.cw_text.clear()
        self.statusBar().showMessage(f"CW : {text}")

    def on_cw_stop(self):
        if self.civ:
            self.civ.stop_cw()

    def on_usb_mod(self, raw):
        self.alc_slider.setEnabled(True)
        if not self.alc_slider.isSliderDown():
            self.alc_slider.blockSignals(True)
            self.alc_slider.setValue(round(raw * 100 / 255))
            self.alc_slider.blockSignals(False)
        self.alc_slider_label.setText(f"{self.alc_slider.value()} %")

    def on_alc_slider(self, percent):
        self.alc_slider_label.setText(f"{percent} %")
        if self.civ:
            self.civ.set_usb_mod(round(percent * 255 / 100))

    def on_tx_source(self):
        is_file = self.tx_source.currentData() == "file"
        self.tx_file_button.setVisible(is_file)
        if is_file and self.tx_file:
            self.tx_file_button.setText(os.path.basename(self.tx_file))

    def choose_tx_file(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Fichier à émettre", RECORD_DIR,
            "Audio (*.wav *.flac *.ogg);;Tous les fichiers (*)")
        if path:
            self.tx_file = path
            self.tx_file_button.setText(os.path.basename(path))

    def on_tx_level(self, value):
        self.tx_level_label.setText(f"{value} %")
        subprocess.run(["pactl", "set-sink-volume", self.radio_sink, f"{value}%"],
                       capture_output=True)

    def on_ptt(self, on):
        if on:
            self.start_tx()
        else:
            self.stop_tx()

    def start_tx(self):
        source = self.tx_source.currentData()
        problem = None
        if not self.civ:
            problem = "Émission impossible sans CAT"
        elif source == "file" and not self.tx_file:
            problem = "Choisis d'abord un fichier à émettre"
        if problem:
            self.statusBar().showMessage(problem)
            self._set_ptt_button(False)
            return
        self.civ.set_ptt(True, data=self.tx_digital)
        if source == "file":
            # un temps pour que la radio passe en émission avant le son
            QtCore.QTimer.singleShot(400, self._play_tx_file)
        else:
            out = subprocess.run(
                ["pactl", "load-module", "module-loopback", f"source={source}",
                 f"sink={self.radio_sink}", "latency_msec=40",
                 "source_dont_move=true", "sink_dont_move=true"],
                capture_output=True, text=True)
            self.tx_loopback = out.stdout.strip() or None
        self.tx_limit = TX_TIMEOUT
        if source == "file":
            try:
                with wave.open(self.tx_file) as w:
                    length = w.getnframes() / w.getframerate()
                self.tx_limit = max(TX_TIMEOUT, length + 15)
            except (OSError, wave.Error, EOFError):
                pass
        self.tx_active = True
        self._mute_listen_for_tx(True)
        self.tx_started = time.monotonic()
        self.tx_timer.start()
        self._set_ptt_button(True)

    def open_sstv(self):
        if self.sstv_window is None:
            self.sstv_window = SstvWindow(self)
        self.sstv_window.show()
        self.sstv_window.raise_()
        self.sstv_window.activateWindow()

    def transmit_file(self, path):
        """Émet un fichier (utilisé par la fenêtre SSTV)."""
        if self.tx_active:
            return
        self.tx_file = path
        self.tx_digital = True
        self.tx_source.setCurrentIndex(self.tx_source.findData("file"))
        self.on_tx_source()
        self.ptt_button.setChecked(True)

    def _play_tx_file(self):
        if self.tx_active:
            self.tx_player = subprocess.Popen(
                ["paplay", f"--device={self.radio_sink}", self.tx_file])

    def stop_tx(self, reason=None):
        if self.tx_player:
            self.tx_player.terminate()
            self.tx_player = None
        if self.tx_loopback:
            subprocess.run(["pactl", "unload-module", self.tx_loopback],
                           capture_output=True)
            self.tx_loopback = None
        if self.civ:
            self.civ.set_ptt(False)
        self.tx_active = False
        self.tx_digital = False
        if not self.radio_tx:
            self._mute_listen_for_tx(False)
        self.tx_timer.stop()
        self._set_ptt_button(False)
        self.tx_meter_label.setText("")
        for meter, value in ((self.swr_meter, self.swr_value),
                             (self.comp_meter, self.comp_value),
                             (self.alc_meter, self.alc_value),
                             (self.id_meter, self.id_value)):
            meter.setValue(meter.minimum)
            value.setText("")
        if reason:
            self.statusBar().showMessage(reason)

    def _set_ptt_button(self, on):
        self.ptt_button.blockSignals(True)
        self.ptt_button.setChecked(on)
        self.ptt_button.blockSignals(False)
        self.ptt_button.setText("■ ARRÊTER" if on else "ÉMETTRE")
        self.ptt_button.setStyleSheet(
            "background: #c62828; color: white; font-weight: bold;" if on else "")

    def check_tx(self):
        elapsed = time.monotonic() - self.tx_started
        if elapsed > self.tx_limit:
            self.stop_tx(f"Émission coupée au bout de {elapsed / 60:.0f} min")
        elif self.tx_player and self.tx_player.poll() is not None:
            self.stop_tx("Fichier émis en entier")

    def on_transmitting(self, tx):
        # émission lancée aussi par le PTT de la radio, une pédale ou le VOX
        self.radio_tx = tx
        if tx or not self.tx_active:
            self._mute_listen_for_tx(tx)
        if self.tx_active and not tx and time.monotonic() - self.tx_started > 2:
            self.stop_tx("La radio n'émet pas (hors bande amateur ?)")

    def _mute_listen_for_tx(self, tx):
        """Écoute radio → PC coupée pendant toute émission : la radio renvoie
        ce qu'elle émet (surtout avec MONI) et les haut-parleurs repasseraient
        dans le micro, du PC ou de la radio = larsen. Remise ensuite comme
        l'utilisateur l'a laissée."""
        if tx == self.tx_muted_listen:
            return
        self.tx_muted_listen = tx
        self.on_mute(True if tx else self.mute_check.isChecked())

    def on_tx_meters(self, po, swr, alc):
        self.swr_meter.setValue(swr)
        self.swr_value.setText(f"{piecewise(swr, SWR_POINTS):.1f}")
        percent = alc * 100 / ALC_ZONE
        self.alc_meter.setValue(percent)
        self.alc_value.setText(f"{percent:.0f}")
        self.tx_meter_label.setText(f"Po {piecewise(po, PO_POINTS):3.0f} %")

    def on_drain(self, raw):
        amps = piecewise(raw, ID_POINTS)
        self.id_meter.setValue(amps)
        self.id_value.setText(f"{amps:.1f} A")

    def on_comp(self, raw):
        db = piecewise(raw, COMP_POINTS)
        self.comp_meter.setValue(db)
        self.comp_value.setText(f"{db:.0f}")

    def on_tune(self):
        if self.civ:
            self.civ.tune()
            self.on_tuner(0x02)

    def on_atu(self, on):
        if self.civ:
            self.civ.set_atu(on)

    def on_monitor(self, on):
        if self.civ:
            self.civ.set_monitor(on)

    def on_tuner(self, state):
        self.atu_button.setChecked(state in (0x01, 0x02))
        tuning = state == 0x02
        self.tune_button.setText("TUNE…" if tuning else "TUNE")
        self.tune_button.setStyleSheet(
            "background: #c62828; color: white; font-weight: bold;" if tuning else "")

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

    def on_level(self, sub, level):
        slider, label = self.level_sliders[sub]
        slider.setEnabled(True)
        if not slider.isSliderDown():
            slider.blockSignals(True)
            slider.setValue(level)
            slider.blockSignals(False)
        label.setText(f"{round(slider.value() * 100 / 255)} %")

    def on_level_slider(self, sub, level):
        self.level_sliders[sub][1].setText(f"{round(level * 100 / 255)} %")
        if self.civ:
            self.civ.set_level(sub, level)

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

    def eventFilter(self, obj, event):
        kind = event.type()
        if kind == QtCore.QEvent.Wheel:
            pos = self.spec_plot.scene().views()[0].mapToScene(event.pos())
            if self._x_at(pos) is not None and self.freq_hz is not None:
                notches = event.angleDelta().y() / 120
                if notches:
                    self.on_tuned(round(notches) or (1 if notches > 0 else -1))
                return True
        elif (kind == QtCore.QEvent.MouseButtonPress
              and event.button() == QtCore.Qt.LeftButton):
            self.dragged = False
            pos = self.spec_plot.scene().views()[0].mapToScene(event.pos())
            if self.view == "rf" and self._x_at(pos) is not None and self.freq_hz:
                low, high = self.spec_plot.vb.viewRange()[0]
                width = self.spec_plot.vb.width()
                if width > 0:
                    self.drag = (event.pos().x(), (high - low) / width, self.freq_hz)
        elif kind == QtCore.QEvent.MouseMove and self.drag:
            x0, khz_per_px, start_hz = self.drag
            dx = event.pos().x() - x0
            if abs(dx) > 3:
                self.dragged = True
            if self.dragged:
                # on tire la bande : vers la gauche, la fréquence monte
                hz = start_hz - dx * khz_per_px * 1000
                step = min(self.step_combo.currentData(), 100)
                self.tune_to(int(round(hz / step) * step))
                self.setCursor(QtCore.Qt.SizeHorCursor)
            return False
        elif kind == QtCore.QEvent.MouseButtonRelease and self.drag:
            self.drag = None
            self.unsetCursor()
        return super().eventFilter(obj, event)

    def on_mouse_clicked(self, event):
        if self.dragged:
            self.dragged = False
            return                      # fin d'un glisser, pas un clic
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
        if decoder in ("psk31", "rtty") and audio is not None:
            if decoder == "psk31" and self.scope_points:
                # un point du scope fait jusqu'à 100 Hz (±25 kHz) : on cherche
                # le signal le plus fort autour du clic dans le spectre audio
                point = (self.scope_edges[1] - self.scope_edges[0]) / self.scope_points
                audio = self._strongest_near(audio, max(25, 1.5 * point))
            self._set_decoder_pitch(audio)
            return
        step = self.step_combo.currentData()
        self.tune_to(round(x * 1000 / step) * step)

    def _audio_offset(self, rf_hz):
        """Fréquence audio d'un signal RF, s'il tombe dans la bande en BLU."""
        if self.freq_hz is None:
            return None
        mode = base_mode(self.mode_combo.currentText())
        audio = {"USB": rf_hz - self.freq_hz, "LSB": self.freq_hz - rf_hz}.get(mode)
        if audio is not None and 100 <= audio <= 3000:
            return audio
        return None

    def _strongest_near(self, hz, tolerance):
        """Centre du signal le plus fort à ± tolerance de hz (spectre audio).

        La puissance est lissée sur ± 20 Hz : les deux raies d'un signal
        PSK31 au repos ne font qu'un pic, centré sur sa fréquence."""
        if self.audio_db is None:
            return hz
        power = 10 ** (self.audio_db / 10)
        half = max(int(20 / BIN_HZ), 1)
        smooth = np.convolve(power, np.ones(2 * half + 1), mode="same")
        lo = max(int((hz - tolerance) / BIN_HZ), 1)
        hi = min(int((hz + tolerance) / BIN_HZ) + 2, len(smooth))
        if hi <= lo:
            return hz
        best = lo + int(np.argmax(smooth[lo:hi]))
        floor = np.median(smooth[int(200 / BIN_HZ):int(3000 / BIN_HZ)])
        if smooth[best] < 4 * floor:        # rien qui dépasse le bruit
            return hz
        return best * BIN_HZ

    def _set_decoder_pitch(self, hz):
        decoder = self.audio.decoder
        if decoder is None or decoder.pitch is None:
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
            "rtty": "Se recale sur la paire de tonalités à ± 60 Hz du clic. "
                    "Cliquer entre les deux traces.",
        }.get(name, ""))
        self.rtty_invert.setVisible(name == "rtty")
        self.decoder_auto.setVisible(name in ("cw", "psk31", "rtty"))
        # FT8/FT4 : plusieurs lignes par créneau, il faut de la place
        self.decoder_text.setFixedHeight(150 if name in ("ft8", "ft4") else 70)
        if name == "ft4" and self.decoder_text.document().isEmpty():
            self.decoder_text.setPlainText(
                "UTC      dB   DT Freq  Message  (14.080, 7.0475… en USB)\n")
        if name == "ft8" and self.decoder_text.document().isEmpty():
            self.decoder_text.setPlainText(
                "UTC      dB   DT Freq  Message  (14.074, 7.074… en USB)\n")
        self.decoder_led.setToolTip({
            "cw": "Allumé quand le signal est manipulé",
            "ft8": "Allumé pendant le décodage d'un créneau",
            "ft4": "Allumé pendant le décodage d'un créneau",
        }.get(name, "Allumé quand le signal est décodable"))
        if decoder is None:
            self.decoder_info.setText("")
            self._set_decoder_led(False, False)
        else:
            self._move_pitch_lines(decoder.pitch)
        self._show_pitch_lines()

    def on_rtty_invert(self, inverted):
        self.audio.decoders["rtty"].inverted = inverted

    def on_decoder_auto(self, auto):
        if self.audio.decoder:
            self.audio.decoder.auto_pitch = auto

    def _show_pitch_lines(self):
        decoder = self.audio.decoder
        visible = decoder is not None and decoder.pitch is not None
        for line in self.pitch_lines:
            line.setVisible(visible)

    def _move_pitch_lines(self, audio_hz):
        """Repère du décodeur : en Hz audio, ou en kHz RF dans la vue RF."""
        if audio_hz is None:            # FT8/FT4 : toute la bande, pas de repère
            self._show_pitch_lines()
            return
        x = audio_hz
        if self.view == "rf":
            mode = base_mode(self.mode_combo.currentText())
            sign = {"USB": 1, "LSB": -1}.get(mode)
            if self.freq_hz is None or sign is None:
                for line in self.pitch_lines:
                    line.setVisible(False)
                return
            x = (self.freq_hz + sign * audio_hz) / 1000
        decoder = self.audio.decoder
        for line in self.pitch_lines:
            line.setVisible(decoder is not None and decoder.pitch is not None)
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
        self._remember_band()
        if self.tx_active:
            self.stop_tx()
        if self.sstv_window:
            self.sstv_window.close()
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
    parser.add_argument("--sink", default=RADIO_SINK,
                        help="sortie PulseAudio/PipeWire vers la radio (émission)")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--fmax", type=int, default=3500,
                        help="fréquence audio maximale affichée (Hz)")
    parser.add_argument("--no-cat", action="store_true",
                        help="ne pas ouvrir le port CI-V (s'il est pris par "
                             "un autre logiciel)")
    parser.add_argument("--remote", metavar="HÔTE",
                        help="radio branchée sur un autre PC du réseau, qui fait "
                             "tourner cat_server.py et partage son son")
    args = parser.parse_args()
    remote_modules = []
    if args.remote:
        try:
            remote_modules = remote_audio(args.remote)
        except RuntimeError as e:
            sys.exit(f"Son du PC distant {args.remote} inaccessible : {e}")
        args.source, args.sink = REMOTE_RX, REMOTE_TX
        args.port = f"socket://{args.remote}:{CAT_TCP_PORT}"

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
    # kill, Ctrl+C ou fin de session : fermer la fenêtre pour remettre la
    # radio comme on l'a trouvée ; le minuteur laisse Python voir le signal
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, lambda *_: window.close())
    wake = QtCore.QTimer()
    wake.timeout.connect(lambda: None)
    wake.start(200)
    code = app.exec_()
    unload_remote_audio(remote_modules)
    sys.exit(code)


if __name__ == "__main__":
    main()
