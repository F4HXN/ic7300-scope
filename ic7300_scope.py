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
import re
import signal
import subprocess
import sys
import threading
import time
import unicodedata
import wave
import xml.etree.ElementTree as ET

import numpy as np
import serial
from PyQt5 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

from cw_decoder import CwDecoder
from cw_pioche import char_schedule, hand_timing
import carnet
from qrz import Qrz, QrzError, cw_name
from rtty_decoder import RttyDecoder, afsk
from ft8_decoder import Ft4Decoder, Ft8Decoder
from ft8_encoder import Ft8EncodeError, encode as ft_encode, waveform as ft_waveform
from cat_server import radio_port
from memory_window import MemoryWindow
from sstv_window import SstvWindow, bring_to_front

DEFAULT_SOURCE = "alsa_input.usb-Burr-Brown_from_TI_USB_Audio_CODEC-00.iec958-stereo"
RADIO_SINK = "alsa_output.usb-Burr-Brown_from_TI_USB_Audio_CODEC-00.iec958-stereo"
DEFAULT_PORT = radio_port()

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
# indicatif et station de l'opérateur, pour les messages CW tout faits
CALLSIGN_FILE = os.path.join(os.path.dirname(STATE_FILE), "indicatif.json")
LOG_FILE = os.path.join(os.path.dirname(STATE_FILE), "carnet.adi")
# message RTTY mis en audio avant d'être émis
RTTY_TX_FILE = os.path.join(os.environ.get("XDG_CACHE_HOME")
                            or os.path.expanduser("~/.cache"),
                            "ic7300-scope", "rtty-emis.wav")
# message FT8 / FT4 mis en audio avant d'être émis
FT_TX_FILE = os.path.join(os.path.dirname(RTTY_TX_FILE), "ft-emis.wav")
# messages pré-enregistrés de chaque mode : fichier, d'origine plus bas
MACROS_FILES = {kind: os.path.join(os.path.dirname(STATE_FILE), f"messages-{kind}.json")
                for kind in ("cw", "rtty", "ft")}
MEMORY_DUMP_FILE = os.path.join(os.path.dirname(STATE_FILE), "memoires-radio-brut.txt")
# presets du PC : mode, filtre, largeur, puissance, USB MOD Level, décodeur
PRESETS_FILE = os.path.join(os.path.dirname(STATE_FILE), "presets.json")
# presets de départ : seuls le mode, le filtre et le décodeur changent
DEFAULT_PRESETS = [
    {"name": "FT8", "mode": "USB-D", "filter": 1, "decoder": "ft8"},
    {"name": "FT4", "mode": "USB-D", "filter": 1, "decoder": "ft4"},
    {"name": "RTTY", "mode": "USB-D", "filter": 2, "decoder": "rtty"},
    {"name": "CW", "mode": "CW", "filter": 2, "decoder": "cw"},
]
# champs de « Ma station… » : clé, libellé, valeur de départ
STATION_FIELDS = [("name", "Prénom", "JEAN-PAUL"),
                  ("qth", "QTH", "PUGET-THENIERS"),
                  ("loc", "Locator", "JN33KW"),
                  ("rig", "Transceiver", "IC7300"),
                  ("pwr", "Puissance (W)", ""),
                  ("ant", "Antenne", "")]
# comptes en ligne, dans « Ma station… » : (clé, libellé, masqué, aide)
ACCOUNT_FIELDS = [
    ("qrz_user", "Compte qrz.com", False,
     "Identifiant qrz.com : prénom du correspondant trouvé tout seul"),
    ("qrz_password", "Mot de passe qrz.com", True, "Mot de passe qrz.com"),
    ("qrz_logbook_key", "Clé API carnet qrz.com", True,
     "qrz.com > Logbook > Settings > API key : les QSO notés y sont envoyés"),
    ("wavelog_url", "Adresse Wavelog", False,
     "Adresse de ton Wavelog, ex. https://log.exemple.fr"),
    ("wavelog_key", "Clé API Wavelog", True,
     "Wavelog > compte > API Keys : clé en lecture-écriture"),
    ("wavelog_station", "Profil de station Wavelog", False,
     "Nom ou numéro du profil de station (Wavelog > Station Locations)")]
# messages CW d'un QSO classique, dans l'ordre : {call} mon indicatif, {dest}
# et {dname} indicatif et prénom du correspondant, {gm} GM/GA/GE selon l'heure,
# les autres viennent de « Ma station… » ; ^SK est le signe SK lié
CW_MACROS = [
    ("CQ", "Appel général", "CQ CQ CQ DE {call} {call} {call} PSE K"),
    ("QRZ?", "Qui m'appelle ? (indicatif mal reçu)", "QRZ? DE {call} K"),
    ("Répondre", "Répondre à un CQ", "{dest} {dest} DE {call} {call} K"),
    ("Rapport", "Premier passage : RST, prénom, QTH, locator",
     "{dest} DE {call} {gm} DR OM TNX FER CALL UR RST 599 599 "
     "NAME {name} {name} QTH {qth} {qth} LOC {loc} {loc} HW? {dest} DE {call} K"),
    ("Station 73", "Matériel, remerciements et fin du QSO",
     "{dest} DE {call} FB {dname} RIG HERE {rig} PWR {pwr} W ANT {ant} "
     "TNX FB QSO QSL VIA BUREAU OR EQSL 73 ES GD DX {dest} DE {call} ^SK"),
    ("Fin", "Réponse à la fin du correspondant",
     "{dest} DE {call} TNX {dname} FB QSO QSL OK 73 ES GD DX {dest} DE {call} ^SK"),
]
# messages RTTY : pas de signes liés, l'indicatif répété trois fois (QRM)
RTTY_MACROS = [
    ("CQ", "Appel général", "CQ CQ CQ DE {call} {call} {call} PSE K"),
    ("QRZ?", "Qui m'appelle ? (indicatif mal reçu)", "QRZ? DE {call} K"),
    ("Répondre", "Répondre à un CQ", "{dest} DE {call} {call} {call} K"),
    ("Rapport", "Premier passage : RST, prénom, QTH, locator",
     "{dest} DE {call} {gm} OM TNX FER CALL UR RST 599 599 "
     "NAME {name} {name} QTH {qth} {qth} LOC {loc} {loc} HW? {dest} DE {call} K"),
    ("Station 73", "Matériel, remerciements et fin du QSO",
     "{dest} DE {call} FB {dname} RIG HERE {rig} PWR {pwr} W ANT {ant} "
     "TNX FB QSO QSL VIA BUREAU OR EQSL 73 ES GD DX {dest} DE {call} SK"),
    ("Fin", "Réponse à la fin du correspondant",
     "{dest} DE {call} TNX {dname} FB QSO QSL OK 73 ES GD DX {dest} DE {call} SK"),
]
# messages FT8 / FT4 d'un QSO, dans l'ordre ; {loc4} locator sur 4 caractères,
# {rpt} report à envoyer (SNR du correspondant)
FT_MACROS = [
    ("CQ", "Appel général", "CQ {call} {loc4}"),
    ("Répondre", "Répondre à un CQ : mon locator", "{dest} {call} {loc4}"),
    ("Report", "Son report (réponse à mon CQ)", "{dest} {call} {rpt}"),
    ("R+report", "Report reçu, le sien en retour", "{dest} {call} R{rpt}"),
    ("RR73", "Reports reçus, fin", "{dest} {call} RR73"),
    ("73", "Fin du QSO", "{dest} {call} 73"),
]
DEFAULT_MACROS = {"cw": CW_MACROS, "rtty": RTTY_MACROS, "ft": FT_MACROS}
# champs utilisables dans les messages CW et RTTY : {clé}, libellé
MACRO_FIELDS = [("call", "Mon indicatif"), ("dest", "Correspondant"),
                ("dname", "Son prénom"), ("gm", "GM/GA/GE"), ("name", "Mon prénom"),
                ("qth", "QTH"), ("loc", "Locator"), ("rig", "Transceiver"),
                ("pwr", "Puissance"), ("ant", "Antenne"),
                ("loc4", "Locator (4)"), ("rpt", "Report FT")]
RTTY_DECODERS = ("rtty", "rtty50")
# interrupteurs de réception de la radio : code (commande CI-V sur 2 octets,
# 0x1100 pour l'atténuateur qui n'a pas de sous-commande)
SW_NB, SW_NR, SW_AN, SW_MN = 0x1622, 0x1640, 0x1641, 0x1648
SW_PREAMP, SW_ATT, SW_IPPLUS = 0x1602, 0x1100, 0x1A07
FT_DECODERS = ("ft8", "ft4")
FT_SLOTS = {"ft8": 15.0, "ft4": 7.5}       # durée d'un créneau (s)
FT_START = 0.5                  # le son commence 0,5 s après le début du créneau
FT_LATE = {"ft8": 2.0, "ft4": 1.0}         # départ en retard encore décodable (s)
# ligne décodée : heure, SNR, DT, fréquence audio, message
FT_LINE_RE = re.compile(r"^(\d\d):(\d\d):(\d\d)\s+([+-]?\d+)\s+[+-]?[\d.]+\s+(\d+)\s+(.+?)\s*$")
GRID_RE = re.compile(r"(?!RR73)[A-R]{2}[0-9]{2}")    # RR73 a la forme d'un locator
# abréviations de fin de message : texte, sens
CW_ENDINGS = [
    ("K", "À vous (invitation à émettre)"),
    ("KN", "À vous seul, les autres s'abstiennent"),
    ("PSE K", "S'il vous plaît, à vous"),
    ("BK", "Coupure, réponse rapide"),
    ("^AR", "Fin de message"),
    ("^AR K", "Fin de message, à vous"),
    ("^SK", "Fin de contact"),
    ("73 ^SK", "Amitiés, fin de contact"),
    ("TU 73 ^SK", "Merci, amitiés, fin de contact"),
    ("73 ES GD DX ^SK", "Amitiés et bons DX, fin de contact"),
    ("TU EE", "Merci (le « dit-dit » final)"),
]
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
# indicatif : préfixe, chiffre, suffixe finissant par une lettre (F4HXN, 9A1AA,
# 3DA0RU), avec /préfixe ou /suffixe éventuels (EA8/F4HXN, F5ABC/P)
CALLSIGN_RE = re.compile(r"(?:[A-Z0-9]{1,4}/)?[A-Z0-9]{1,3}[0-9][A-Z0-9]{0,3}[A-Z]"
                         r"(?:/[A-Z0-9]{1,4})?")
TX_TEXT_COLOR = "#ef5350"       # texte émis en CW, parmi le texte décodé
REPLY_BG, REPLY_FG = "#ffd54a", "#101418"     # FT : ligne qui contient mon indicatif
CW_DIRECT_IDLE = 3.0            # s sans frappe en direct : fin de la ligne émise
CW_ECHO_DELAY = 0.15            # s entre l'envoi CI-V et la première lettre manipulée
SLIDER_WIDTH = 150              # px : curseurs de la grille du son
SOUND_GAPS = (4, 8, 12, 16, 20)  # colonnes vides entre les groupes de la grille
SOUND_COLUMNS = 22              # colonne élastique, à droite de la grille
# repères de l'IC-7300 pour les mesures en émission
PO_POINTS = [(0, 0), (143, 50), (213, 100), (255, 120)]           # % de puissance
SWR_POINTS = [(0, 1.0), (48, 1.5), (80, 2.0), (120, 3.0), (255, 6.0)]
COMP_POINTS = [(0, 0.0), (130, 15.0), (241, 30.0), (255, 32.0)]     # dB
ID_POINTS = [(0, 0.0), (97, 10.0), (146, 15.0), (241, 25.0)]      # A
VD_POINTS = [(0, 0.0), (13, 10.0), (241, 16.0)]                   # V
ALC_ZONE = 120                  # au-delà, l'ALC sort de sa zone sur la radio
MIN_FREQ = 30_000               # couverture de réception de l'IC-7300
MAX_FREQ = 74_800_000
DRAG_GAIN = 0.25                # glisser sur le waterfall : part de l'échelle
SPANS = [2500, 5000, 10_000, 25_000, 50_000, 100_000, 250_000, 500_000]
# largeur de FIL1, FIL2, FIL3 en kHz (réglages d'usine de l'IC-7300 ; chaque
# mode a les siens : FIL3 vaut 1,8 kHz en BLU mais 3 kHz en AM)
FILTER_WIDTHS = {"LSB": (3.0, 2.4, 1.8), "USB": (3.0, 2.4, 1.8),
                 "AM": (9.0, 6.0, 3.0), "FM": (15.0, 10.0, 7.0),
                 "CW": (1.2, 0.5, 0.25), "CW-R": (1.2, 0.5, 0.25),
                 "RTTY": (2.4, 0.5, 0.25), "RTTY-R": (2.4, 0.5, 0.25)}


def width_hz(mode, index):
    """Largeur du filtre (Hz) pour la valeur CI-V 0x1A 0x03 : de 50 Hz en
    50 Hz jusqu'à 500 Hz puis de 100 Hz en 100 Hz (BLU, CW, RTTY), de 200 Hz
    en 200 Hz en AM ; None en FM (largeur fixe)."""
    if mode == "FM":
        return None
    if mode == "AM":
        return (index + 1) * 200
    return (index + 1) * 50 if index < 10 else 600 + (index - 10) * 100


def width_text(hz):
    return f"{hz / 1000:g} kHz".replace(".", ",") if hz >= 1000 else f"{hz} Hz"


def passband(mode, filt, width=None):
    """Zone écoutée autour de la fréquence affichée, en kHz (approximative).

    width : largeur lue sur la radio (Hz), sinon celle d'usine du filtre."""
    width = (width / 1000 if width else
             FILTER_WIDTHS.get(mode, (3.0, 3.0, 3.0))[(filt or 1) - 1])
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


def to_bcd_be(value):
    """Un octet BCD : 42 → 0x42."""
    return (value // 10 % 10) << 4 | value % 10


# canal mémoire (commande 1A 00), relevé sur l'IC-7300 : sélection, puis deux
# fois fréquence (5), mode, filtre, data (quartet haut) et subton (quartet
# bas), subton relais (3), subton TSQL (3) — VFO puis split — et le nom (10)
MEMORY_DATA_SIZE = 1 + 2 * 14 + 10


def memory_content(hz, mode, name, keep=None):
    """Contenu d'un canal ; avec `keep` (canal existant), seuls fréquence,
    mode et nom changent."""
    base = base_mode(mode)
    code = next(c for c, n in MODES.items() if n == base)
    data = 0x10 if mode.endswith("-D") else 0x00
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    label = text[:10].ljust(10).encode("ascii")
    if keep is not None:
        content = bytearray(keep)
        content[1:6] = to_bcd_le(hz)
        content[6] = code
        content[8] = data | (keep[8] & 0x0F)    # le subton est gardé
        content[-10:] = label
        return bytes(content)
    tone = bytes((0x00, 0x08, 0x85))           # 88,5 Hz, non utilisé
    vfo = to_bcd_le(hz) + bytes((code, 0x01, data)) + tone + tone
    return bytes((0x00,)) + vfo + vfo + label


def radio_memory(content):
    """Contenu d'un canal → (nom, Hz, mode), None s'il est vide ou illisible."""
    if not content or content == b"\xff" or len(content) != MEMORY_DATA_SIZE:
        return None
    code, data = content[6], content[8]
    if code not in MODES:
        return None
    name = content[-10:].decode("ascii", "replace").strip()
    return name, bcd_le(content[1:6]), mode_name(code, data >> 4 == 0x01)


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
    """Flux d'écoute radio → PC : (index, volume en %, muet, sortie) ou None."""
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
            return stream["index"], volume, stream["mute"], stream["sink"]
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


def list_outputs(exclude):
    """Sorties son du PC (index, nom, description), sans celles de la radio."""
    try:
        out = subprocess.run(["pactl", "-f", "json", "list", "sinks"],
                             capture_output=True, text=True, timeout=2).stdout
        sinks = json.loads(out)
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    outputs = []
    for sink in sinks:
        if sink["name"] in exclude:
            continue
        description = sink["properties"].get("device.description")
        if not description or description == "(null)":
            description = sink["name"]
        outputs.append((sink["index"], sink["name"], description))
    return outputs


def sink_hw_volume(sink):
    """Vrai si la sortie règle son volume dans la carte son : son retour
    (.monitor) est alors pris avant le volume, sinon après."""
    try:
        out = subprocess.run(["pactl", "list", "sinks"], capture_output=True, text=True,
                             timeout=2, env={**os.environ, "LC_ALL": "C"}).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    for block in out.split("\nSink #"):
        if re.search(rf"^\s*Name: {re.escape(sink)}$", block, re.M):
            return "HW_VOLUME_CTRL" in block
    return False


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
        self.decoders = {"cw": CwDecoder(RATE), "rtty": RttyDecoder(RATE),
                         "rtty50": RttyDecoder(RATE, baud=50, shift=450,
                                               inverted=True),
                         "ft8": Ft8Decoder(RATE),
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


class InputLevel(QtCore.QThread):
    """Niveau d'une entrée son (micro de l'émission), en dBFS, 10 fois par seconde."""
    level = QtCore.pyqtSignal(float)

    def __init__(self, source):
        super().__init__()
        self.source = source
        self.proc = None
        self._running = True

    def run(self):
        rate = 8000
        try:
            self.proc = subprocess.Popen(
                ["parec", "--raw", f"--device={self.source}", "--format=s16le",
                 f"--rate={rate}", "--channels=1", "--latency-msec=50",
                 "--client-name=IC-7300 Scope niveau TX"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except FileNotFoundError:
            return
        nbytes = rate // 10 * 2
        while self._running:
            data = self.proc.stdout.read(nbytes)
            if len(data) < nbytes:
                break
            x = np.frombuffer(data, "<i2").astype(np.float32) / 32768
            self.level.emit(float(20 * np.log10(np.sqrt(np.mean(x * x)) + 1e-10)))

    def stop(self):
        self._running = False
        if self.proc:
            self.proc.terminate()
        self.wait(1000)


class CivReader(QtCore.QThread):
    """Interroge la radio en CI-V : S-mètre, fréquence et mode."""
    meter = QtCore.pyqtSignal(int)
    frequency = QtCore.pyqtSignal(int)
    mode = QtCore.pyqtSignal(str)
    other_frequency = QtCore.pyqtSignal(int)     # VFO non sélectionné
    other_mode = QtCore.pyqtSignal(str)
    af_gain = QtCore.pyqtSignal(int)            # volume de la radio, 0 à 255
    level = QtCore.pyqtSignal(int, int)         # réglage 0x14 (RF 0x02, SQL 0x03, COMP 0x0E), 0 à 255
    compressor = QtCore.pyqtSignal(bool)        # compresseur de parole en service
    switch = QtCore.pyqtSignal(int, int)        # NB, NR, notch, préampli, ATT, IP+ : code, valeur
    tuner = QtCore.pyqtSignal(int)              # boîte d'accord : 0 arrêt, 1 marche, 2 accord
    filter = QtCore.pyqtSignal(int)             # filtre FIL1, FIL2 ou FIL3 (1 à 3)
    filter_width = QtCore.pyqtSignal(int)       # largeur du filtre, valeur 0x1A 0x03
    power = QtCore.pyqtSignal(int)              # puissance réglée, 0 à 255
    key_speed = QtCore.pyqtSignal(int)          # vitesse du manipulateur, 0 à 255
    transmitting = QtCore.pyqtSignal(bool)      # la radio émet
    tx_meters = QtCore.pyqtSignal(int, int, int)  # puissance, ROS, ALC (0 à 255)
    usb_mod = QtCore.pyqtSignal(int)            # menu USB MOD Level, 0 à 255
    comp = QtCore.pyqtSignal(int)               # compression, 0 à 255
    drain = QtCore.pyqtSignal(int)              # courant de l'ampli (Id), 0 à 255
    voltage = QtCore.pyqtSignal(int)            # tension d'alimentation (Vd), 0 à 255
    scope = QtCore.pyqtSignal(object, int, int) # balayage, bords bas/haut (Hz)
    span = QtCore.pyqtSignal(int)               # demi-largeur du scope (Hz)
    powered = QtCore.pyqtSignal(bool)           # la radio répond (en marche)
    squelch_open = QtCore.pyqtSignal(bool)      # squelch ouvert (signal présent)
    memory_written = QtCore.pyqtSignal(bool, str)  # écriture d'un canal : réussite, message
    memory_channel = QtCore.pyqtSignal(int, object)  # canal lu : numéro, contenu brut ou None
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
        self._memory_jobs = queue.Queue()
        self._memory_scan = []          # canaux de la radio qui restent à lire
        self._memory_reply = None
        self._tuner = None
        self._filter = 0x01                 # FIL1 tant que la radio n'a rien dit
        self._pending_span = None
        self._pending_width = None
        self._pending_power = None
        self._pending_key_speed = None
        self._break_in = None           # BK-IN : 0 arrêt, 1 semi, 2 full
        self._pending_usb_mod = None
        self._pending_ptt = None
        self._pending_onoff = None      # mise en marche (True) ou arrêt (False)
        self._powered = None            # la radio répond-elle ?
        self._last_reply = 0.0
        self._squelch = None
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
        # CW à la pioche : manipulation par la ligne DTR, dans son propre fil
        self._ser = None
        self._hand_queue = queue.Queue()
        self._hand_stop = threading.Event()
        self._hand_thread = None
        self._hand_end = 0.0            # fin du dernier message à la pioche

    def run(self):
        while self._running:
            try:
                # chemin du port, ou socket://hôte:port (cat_server.py)
                ser = serial.serial_for_url(self.port, self.baud, timeout=0.05,
                                            exclusive=True, do_not_open=True)
                # DTR au repos : avec le menu USB Keying (CW) sur DTR, la
                # ligne levée à l'ouverture manipulerait la radio
                ser.dtr = False
                ser.open()
                with ser:
                    self._ser = ser
                    self.status.emit("CAT connecté")
                    self._buf = bytearray()
                    self._last_reply = time.monotonic()
                    self._recover(ser)
                    self._scope_setup(ser)
                    self._poll(ser)
                    self._hand_stop.set()
                    self._release(ser)
                    self._scope_restore(ser)
                    self._ser = None
            except (serial.SerialException, OSError) as e:
                self._ser = None
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

    def set_mode(self, name, filt=None):
        """Mode, et filtre FIL1 à FIL3 envoyé avec lui s'il est donné."""
        if filt:
            self._filter = filt
        self._pending_mode = name

    def set_af_gain(self, level):
        self._pending_af = level

    def set_level(self, sub, level):
        """Réglage 0x14 de la radio : 0x02 gain RF, 0x03 squelch, 0x0E compresseur."""
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

    def set_filter(self, number):
        """FIL1 à FIL3 : même mode, autre filtre (en mode data, celui du data)."""
        self._filter = number
        if self._data_state is not None and self._data_state[0]:
            self._commands.put((0x1A, 0x06, 0x01, number))
        elif self._mode_code is not None:
            self._commands.put((0x06, self._mode_code, number))

    def set_filter_width(self, index):
        """Largeur du filtre en cours (0 à 40, 0 à 49 en AM)."""
        self._pending_width = index

    def set_power(self, level):
        self._pending_power = level

    def read_memories(self):
        """Relit les canaux 1 à 99 de la radio, un par tour de lecture."""
        self._memory_scan = list(range(1, 100))

    def write_memory(self, channel, hz, mode, name):
        """Écrit un canal mémoire de la radio (1 à 99), puis le relit."""
        self._memory_jobs.put((channel, hz, mode, name))

    def clear_memory(self, channel):
        """Vide un canal mémoire de la radio."""
        self._memory_jobs.put((channel, None, None, None))

    def set_key_speed(self, level):
        self._pending_key_speed = level

    def send_cw(self, text):
        """Texte émis en CW par le manipulateur de la radio (mode CW).

        Sans BK-IN la radio ne passe pas en émission : on met le semi BK-IN."""
        self._break_in_for_cw()
        for i in range(0, len(text), 30):       # 30 caractères par trame
            self._commands.put((0x17, *text[i:i + 30].encode("ascii")))

    def send_cw_hand(self, text, wpm):
        """Texte émis comme à la pioche : rythme irrégulier d'une main, la
        radio étant manipulée par la ligne DTR (menu USB Keying (CW) sur DTR)."""
        self._break_in_for_cw()
        if self._hand_thread is not None and self._hand_stop.is_set():
            self._hand_thread.join(0.5)     # message arrêté : le fil se termine
        self._hand_queue.put((text, wpm))
        if self._hand_thread is None or not self._hand_thread.is_alive():
            self._hand_stop.clear()
            self._hand_thread = threading.Thread(target=self._hand_key, daemon=True)
            self._hand_thread.start()

    def set_switch(self, code, value):
        """NB, NR, notch auto/manuel, préampli, atténuateur ou IP+ (codes SW_*)."""
        if code == SW_ATT:
            self._commands.put((0x11, value))
        else:
            self._commands.put((code >> 8, code & 0xFF, value))

    def set_compressor(self, on):
        """Compresseur de parole (BLU) en service ou non."""
        self._commands.put((0x16, 0x44, 0x01 if on else 0x00))

    def _break_in_for_cw(self):
        if self._break_in == 0x00:
            self._commands.put((0x16, 0x47, 0x01))
            self._break_in = 0x01
            self.status.emit("BK-IN mis en semi pour émettre la CW")

    def _hand_key(self):
        ser = self._ser
        try:
            while not self._hand_stop.is_set():
                try:
                    text, wpm = self._hand_queue.get_nowait()
                except queue.Empty:
                    return
                # échéances cumulées : les retards de sleep ne s'additionnent pas ;
                # un mot qui suit de près le précédent (direct) attend l'espace
                deadline = max(time.monotonic(), self._hand_end + 7 * 1.2 / wpm)
                for down, duration in hand_timing(text, wpm):
                    if self._hand_stop.is_set() or ser is not self._ser:
                        return
                    ser.dtr = down
                    deadline += duration
                    self._hand_stop.wait(max(0.0, deadline - time.monotonic()))
                ser.dtr = False
                self._hand_end = deadline
        except (serial.SerialException, OSError) as e:
            self.status.emit(f"Manipulation DTR impossible : {e}")
        finally:
            try:
                if ser is not None and ser.is_open:
                    ser.dtr = False
            except (serial.SerialException, OSError):
                pass

    def stop_cw(self):
        self._hand_stop.set()
        while not self._hand_queue.empty():
            self._hand_queue.get_nowait()
        self._commands.put((0x17, 0xFF))

    def set_onoff(self, on):
        """Met la radio en marche (True) ou l'arrête (False)."""
        self._pending_onoff = on

    def _power_on(self, ser):
        # radio arrêtée : une suite de 0xFE réveille son port avant la commande
        # (150 à 115200 bauds selon Icom ; un peu plus, comme Hamlib) ; la
        # radio ne l'accepte pas si son menu CI-V USB Baud Rate est sur Auto
        self.status.emit("Mise en marche de la radio…")
        for _ in range(3):
            ser.write(bytes([0xFE] * 175))
            ser.flush()
            if self._transact(ser, (0x18, 0x01), (0xFB, 0xFA)) == 0xFB:
                return
            time.sleep(0.5)
        self.status.emit("La radio ne répond pas à la mise en marche : menu "
                         "CI-V USB Baud Rate sur 115200 (pas Auto) ?")

    def _power_off(self, ser):
        if self._ptt or self._tx:
            self._command(ser, 0x1C, 0x00, 0x00)
            self._release(ser)
        self._transact(ser, (0x18, 0x00), (0xFB, 0xFA))
        self.status.emit("Radio arrêtée")

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
            onoff, self._pending_onoff = self._pending_onoff, None
            if onoff is True:
                self._power_on(ser)
                changed = True
            elif onoff is False:
                self._power_off(ser)
                self._last_reply = 0.0      # arrêtée tout de suite
                changed = True
            # sans réponse depuis 2 s, la radio est arrêtée ; à sa remise en
            # marche, elle a oublié l'envoi du scope en CI-V
            powered = time.monotonic() - self._last_reply < 2.0
            if powered != self._powered:
                if powered and self._powered is False:
                    self._scope_setup(ser)
                self._powered = powered
                self.powered.emit(powered)
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
                self._transact(ser, (0x1A, 0x03), (0x1A,))
                changed = True
            width, self._pending_width = self._pending_width, None
            if width is not None:
                self._command(ser, 0x1A, 0x03, to_bcd_be(width))
            while not self._commands.empty():
                command = self._commands.get()
                self._command(ser, *command)
                if command[0] == 0x06 or command[:2] == (0x1A, 0x06):
                    # autre filtre : sa largeur
                    self._transact(ser, (0x1A, 0x03), (0x1A,))
                changed = True
            if not self._memory_jobs.empty():
                # une écriture par tour : les mesures continuent
                self._write_memory(ser, *self._memory_jobs.get())
            if self._memory_scan:
                # un canal à la fois : les mesures continuent pendant la lecture
                channel = self._memory_scan.pop(0)
                self.memory_channel.emit(channel, self._read_memory(ser, channel))
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
            if not self._powered:           # radio arrêtée : on guette son réveil
                queries = [(0x03,)]
            elif self._ptt or self._tx:     # en émission : puissance, ROS, ALC
                queries = [(0x1C, 0x00), (0x15, 0x11), (0x15, 0x12), (0x15, 0x13),
                           (0x15, 0x14), (0x15, 0x15), (0x15, 0x16)]
                if n % 10 == 0:
                    queries += [(0x03,), (0x04,), (0x1A, 0x06)]
            elif n % 10 == 0:
                queries = [(0x03,), (0x04,), (0x1A, 0x06), (0x1A, 0x03), (0x25, 0x01), (0x26, 0x01),
                           (0x14, 0x01), (0x14, 0x02), (0x14, 0x03), (0x14, 0x0A), (0x1C, 0x00),
                           (0x1C, 0x01), (0x1A, 0x05, 0x00, 0x65),
                           (0x15, 0x02), (0x14, 0x0C), (0x16, 0x47), (0x15, 0x01),
                           (0x16, 0x44), (0x14, 0x0E), (0x15, 0x15),
                           (0x16, 0x22), (0x14, 0x12), (0x16, 0x40), (0x14, 0x06),
                           (0x16, 0x41), (0x16, 0x48), (0x16, 0x02), (0x11,),
                           (0x1A, 0x07)]
            elif self._tuner == 0x02:       # accord en cours : on suit de près
                queries = [(0x1C, 0x00), (0x1C, 0x01), (0x15, 0x11), (0x15, 0x12)]
            else:
                # état d'émission à chaque tour : PTT de la radio, pédale ou VOX
                # détectés vite pour couper l'écoute PC (larsen)
                # squelch aussi : l'écoute PC suit son ouverture
                queries = [(0x15, 0x02), (0x1C, 0x00), (0x15, 0x01)]
            n += 1
            for query in queries:
                if not self._running:
                    break               # fermeture : on remet la radio sans attendre
                if (self._pending_freq is not None or self._pending_ptt is not None
                        or self._pending_onoff is not None):
                    break               # clic ou PTT : on l'envoie sans finir la série
                self._transact(ser, query, (query[0],))
            if self._pending_freq is None:
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
                # arrêtée, la radio répond encore NG (0xFA) : pas en marche
                if cmd is not None and cmd != 0xFA:
                    self._last_reply = time.monotonic()
                if cmd in expected:
                    return cmd
        return None

    def _read_memory(self, ser, channel):
        """Contenu brut du canal (après son numéro), b"\xff" s'il est vide."""
        self._memory_reply = None
        for _ in range(3):
            self._transact(ser, (0x1A, 0x00, 0x00, to_bcd_be(channel)), (0x1A, 0xFA))
            if self._memory_reply is not None:
                return self._memory_reply
        return None

    def _write_memory(self, ser, channel, hz, mode, name):
        before = self._read_memory(ser, channel)
        if before is None:
            self.memory_written.emit(False, "La radio ne répond pas à la lecture des mémoires")
            return
        if before != b"\xff" and len(before) != MEMORY_DATA_SIZE:
            # format inattendu : on n'écrit rien plutôt que d'abîmer le canal
            self.memory_written.emit(False, f"Format de mémoire inconnu "
                                            f"({len(before)} octets), rien n'est écrit")
            return
        if hz is None:
            content = b"\xff"                       # canal vidé
        elif before != b"\xff":
            # canal déjà rempli : on ne change que fréquence, mode et nom, le
            # reste (split, subtons) est gardé
            content = memory_content(hz, mode, name, keep=before)
        else:
            content = memory_content(hz, mode, name)
        reply = None
        for _ in range(3):
            reply = self._transact(ser, (0x1A, 0x00, 0x00, to_bcd_be(channel), *content),
                                   (0xFB, 0xFA))
            if reply is not None:
                break
        after = self._read_memory(ser, channel)
        self.memory_channel.emit(channel, after)
        if reply == 0xFB and after == content == b"\xff":
            self.memory_written.emit(True, f"Canal {channel} de la radio vidé")
        elif reply == 0xFB and after == content:
            self.memory_written.emit(True, f"Canal {channel} de la radio écrit et vérifié")
        elif reply == 0xFB and after is not None and after[1:7] == content[1:7]:
            self.memory_written.emit(True, f"Canal {channel} écrit (fréquence et mode vérifiés)")
        else:
            self.memory_written.emit(False, f"La radio a refusé l'écriture du canal {channel}")

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
        if cmd == 0x1A and len(data) >= 4 and data[0] == 0x00:
            self._memory_reply = bytes(data[3:])   # contenu d'un canal mémoire
            return cmd
        if cmd == 0x27 and len(data) >= 4 and data[0] == 0x00:
            self._wave_part(bcd_be(data[2:3]), bcd_be(data[3:4]), data[4:])
            return None                     # données du scope, pas une réponse
        if cmd in (0x00, 0x03) and len(data) == 5:
            if self._pending_freq is None:  # sinon, ancienne fréquence : l'affichage reculerait
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
        elif cmd == 0x1C and len(data) == 2 and data[0] == 0x00:
            self._tx = data[1] == 0x01
            self.transmitting.emit(self._tx)
        elif cmd == 0x1A and len(data) == 2 and data[0] == 0x03:
            self.filter_width.emit(bcd_be(data[1:]))
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
        elif cmd == 0x15 and len(data) == 3 and data[0] == 0x15:
            self.voltage.emit(bcd_be(data[1:]))
        elif cmd == 0x14 and len(data) == 3 and data[0] == 0x01:
            self.af_gain.emit(bcd_be(data[1:]))
        elif cmd == 0x16 and len(data) == 2 and data[0] == 0x44:
            self.compressor.emit(data[1] == 0x01)
        elif cmd == 0x16 and len(data) == 2 and data[0] in (0x22, 0x40, 0x41, 0x48, 0x02):
            self.switch.emit(0x1600 | data[0], data[1])
        elif cmd == 0x11 and len(data) == 1:
            self.switch.emit(SW_ATT, data[0])
        elif cmd == 0x1A and len(data) == 2 and data[0] == 0x07:
            self.switch.emit(SW_IPPLUS, data[1])
        elif cmd == 0x14 and len(data) == 3 and data[0] in (0x02, 0x03, 0x0E, 0x06, 0x12):
            self.level.emit(data[0], bcd_be(data[1:]))
        elif cmd == 0x15 and len(data) == 3 and data[0] == 0x02:
            self.meter.emit(bcd_be(data[1:]))
        elif cmd == 0x15 and len(data) == 2 and data[0] == 0x01:
            if data[1] != self._squelch:
                self._squelch = data[1]
                self.squelch_open.emit(data[1] == 0x01)
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
    """Bargraph à segments avec mémoire de crête.

    Les segments ont la même taille dans tous les bargraphs : leur nombre
    dépend de la largeur."""
    SEGMENT_PX = 14

    def __init__(self, minimum, maximum, ticks, red_from):
        super().__init__()
        self.minimum = minimum
        self.maximum = maximum
        self.ticks = ticks
        self.red_from = red_from
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
        segments = max(4, round(inner_w / self.SEGMENT_PX))
        seg_w = inner_w / segments
        lit_fraction = self._fraction(self.value)
        green, red = QtGui.QColor("#3ddc84"), QtGui.QColor("#ff5252")
        for i in range(segments):
            centre = (i + 0.5) / segments
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


class DecodedTextWindow(QtWidgets.QWidget):
    """Le texte décodé en grand, dans une fenêtre à part (F11 : plein écran).

    Elle recopie le texte de la fenêtre principale à chaque changement : ce qui
    arrive, Effacer et le changement de décodeur s'y voient aussitôt."""

    def __init__(self, document, decoder_combo, parent):
        # fenêtre fille : elle reste devant la fenêtre principale
        super().__init__(parent, QtCore.Qt.Window)
        self.setWindowTitle("Décodeur — IC-7300 Scope")
        self.resize(1000, 600)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        self.title = QtWidgets.QLabel()
        self.title.setStyleSheet("font: bold 13pt;")
        self.text = QtWidgets.QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setMaximumBlockCount(500)
        self.source = document
        self.text.setStyleSheet("font: bold 14pt monospace; color: #3ddc84;")
        CallsignClick(self.text, parent.set_correspondent)
        layout.addWidget(self.title)
        layout.addWidget(self.text, stretch=1)
        self.combo = decoder_combo
        self.combo.currentIndexChanged.connect(self._show_title)
        self._show_title()
        document.contentsChanged.connect(self._copy)
        self._copy()

    def _show_title(self, *_):
        self.title.setText(f"Décodeur : {self.combo.currentText()}   (F11 : plein écran)")

    def _copy(self):
        """Recopie le texte ; reste en bas, sauf si on est remonté lire plus haut."""
        bar = self.text.verticalScrollBar()
        at_end = bar.value() >= bar.maximum() - 40 or not self.isVisible()
        position = bar.value()
        # copie avec les couleurs (texte émis en rouge)
        self.text.clear()
        self.text.textCursor().insertFragment(QtGui.QTextDocumentFragment(self.source))
        if at_end:
            self.text.moveCursor(QtGui.QTextCursor.End)
            self.text.ensureCursorVisible()
        else:
            bar.setValue(position)

    def keyPressEvent(self, event):
        if event.key() == QtCore.Qt.Key_F11:
            self.showNormal() if self.isFullScreen() else self.showFullScreen()
        elif event.key() == QtCore.Qt.Key_Escape and self.isFullScreen():
            self.showNormal()
        else:
            super().keyPressEvent(event)


class MainWindow(QtWidgets.QMainWindow):
    qrz_found = QtCore.pyqtSignal(str, object, str)   # indicatif, fiche, erreur
    log_sent = QtCore.pyqtSignal(str, str, bool, str)  # indicatif, service, réussi, message

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
        self.freq_entry.setPlaceholderText("14.074")
        self.freq_entry.setToolTip("kHz, MHz (avec un point) ou Hz, puis Entrée")
        self.freq_entry.setFixedWidth(175)
        self.freq_entry.setStyleSheet("font: 15pt monospace; padding: 2px 4px;")
        self.freq_entry.returnPressed.connect(self.on_freq_entered)
        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.addItems(MODE_NAMES)
        # RTTY et RTTY-R de la radio (FSK) retirés du menu : l'émission RTTY
        # se fait en LSB-D/USB-D ; gardés cachés pour afficher le mode quand
        # la radio y est mise depuis sa façade
        for i, name in enumerate(MODE_NAMES):
            if name.startswith("RTTY"):
                self.mode_combo.view().setRowHidden(i, True)
                self.mode_combo.model().item(i).setEnabled(False)   # ni molette ni clavier
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
            # filtre en cours en jaune, comme la bande en cours
            button.setStyleSheet("QPushButton:checked { background: #ffd54a; "
                                 "color: #101418; font-weight: bold; }")
            self.filter_buttons.addButton(button, number)
            filters.addWidget(button)
        self.filter_buttons.idClicked.connect(self.on_filter_selected)
        # largeur du filtre en cours, réglée sur la radio (menu FILTER > BW)
        self.width_index = None
        self.width_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.width_slider.setRange(0, 40)
        self.width_slider.setFixedWidth(110)
        self.width_slider.setEnabled(False)     # activé quand la radio répond
        self.width_slider.setToolTip("Largeur du filtre en cours (la radio la "
                                     "garde pour ce filtre et ce mode)")
        self.width_slider.valueChanged.connect(self.on_width_slider)
        self.width_label = QtWidgets.QLabel()
        self.width_label.setMinimumWidth(64)
        filters.addSpacing(6)
        filters.addWidget(self.width_slider)
        filters.addWidget(self.width_label)
        self._filter_tooltips()
        top.addWidget(self.freq_label)
        top.addWidget(QtWidgets.QLabel("MHz"))
        top.addSpacing(10)
        top.addWidget(self.step_combo)
        top.addSpacing(20)
        top.addWidget(self.mode_combo)
        top.addLayout(filters)
        top.addSpacing(20)
        goto_label = QtWidgets.QLabel("Aller à")
        goto_label.setStyleSheet("font-size: 14pt;")
        top.addWidget(goto_label)
        top.addWidget(self.freq_entry)
        # horloge : UTC pour le trafic (FT8, carnet), puis heure locale, sur la
        # ligne de base du reste
        clock = QtWidgets.QHBoxLayout()
        clock.setSpacing(6)
        self.utc_label = QtWidgets.QLabel()
        self.local_label = QtWidgets.QLabel()
        for name, label, color, weight in (("UTC", self.utc_label, "#4fc3f7", "bold"),
                                           ("Locale", self.local_label, "#b0bec5", "")):
            title = QtWidgets.QLabel(name)
            title.setStyleSheet(f"font-size: 14pt; color: {color};")
            label.setStyleSheet(f"font: {weight} 15pt monospace; color: {color};")
            if clock.count():
                clock.addSpacing(18)
            clock.addWidget(title)
            clock.addWidget(label)
        top.addSpacing(30)
        top.addLayout(clock)
        self.clock_timer = QtCore.QTimer(self)
        self.clock_timer.timeout.connect(self.update_clock)
        self.update_clock()
        top.addStretch()
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
        # entrée de l'émission : micro (en continu, pour le régler avant
        # d'émettre) ou, avec un fichier, le son qui part vers la radio
        self.tx_in_meter = BarGraph(-60, 0, level_ticks, red_from=-6)
        self.tx_in_meter.setToolTip("Niveau envoyé à la radio, Niveau PC compris (micro, "
                                    "ou fichier pendant l'émission). Rester sous le rouge.")
        self.tx_in_value = QtWidgets.QLabel("")
        self.tx_in_reader = None
        # à côté : la puissance HF qui sort de la radio en émission (Po)
        po_ticks = [(v, f"{v}") for v in (0, 25, 50, 75, 100)]
        self.po_meter = BarGraph(0, 120, po_ticks, red_from=100)
        self.po_meter.setToolTip("Puissance de sortie de la radio en émission, "
                                 "en W (100 W pour la puissance maximale)")
        self.po_value = QtWidgets.QLabel("")
        # ALC à côté de l'audio reçu, en émission
        alc_ticks = [(v, f"{v}") for v in (0, 50, 100, 150)]
        self.alc_meter = BarGraph(0, 150, alc_ticks, red_from=100)
        self.alc_meter.setToolTip("ALC en % de sa zone : rester sous 100")
        self.alc_value = QtWidgets.QLabel("")
        for label in (self.s_value, self.level_value, self.tx_in_value, self.po_value,
                      self.alc_value):
            label.setMinimumWidth(90)
            label.setStyleSheet("font: bold 14pt monospace;")
        swr_ticks = [(raw, f"{swr:g}") for raw, swr in SWR_POINTS[:-1]] + [(255, "∞")]
        self.swr_meter = BarGraph(0, 255, swr_ticks, red_from=80)
        comp_ticks = [(v, f"{v}") for v in (0, 10, 20, 30)]
        self.comp_meter = BarGraph(0, 30, comp_ticks, red_from=20)
        vd_ticks = [(v, f"{v}") for v in (10, 12, 14, 16)]
        self.vd_meter = BarGraph(10, 16, vd_ticks, red_from=15)
        self.vd_meter.setToolTip("Tension d'alimentation de la radio (Vd) : 13,8 V "
                                 "nominal ; si elle chute fort en émission, "
                                 "l'alimentation ou ses câbles sont trop justes")
        id_ticks = [(v, f"{v}") for v in (0, 5, 10, 15, 20, 25)]
        self.id_meter = BarGraph(0, 25, id_ticks, red_from=22)
        self.id_meter.setToolTip("Courant consommé par l'ampli (Id)")
        self.swr_value = QtWidgets.QLabel("")
        self.comp_value = QtWidgets.QLabel("")
        self.vd_value = QtWidgets.QLabel("")
        self.id_value = QtWidgets.QLabel("")
        # une seule grille, alignée : nom, barre, valeur, deux fois par ligne ;
        # le S-mètre prend toute la largeur
        bars.addWidget(QtWidgets.QLabel("S-mètre"), 0, 0)
        bars.addWidget(self.s_meter, 0, 1, 1, 4)
        bars.addWidget(self.s_value, 0, 5)
        for row, pair in enumerate((
                (("Audio dBFS", self.level_meter, self.level_value),
                 ("ALC %", self.alc_meter, self.alc_value)),
                (("Entrée TX", self.tx_in_meter, self.tx_in_value),
                 ("Sortie TX", self.po_meter, self.po_value)),
                (("ROS", self.swr_meter, self.swr_value),
                 ("COMP dB", self.comp_meter, self.comp_value)),
                (("Vd", self.vd_meter, self.vd_value),
                 ("Id", self.id_meter, self.id_value))), start=1):
            for col, (name, meter, value) in zip((0, 3), pair):
                # même largeur pour les deux barres de chaque ligne
                meter.setSizePolicy(QtWidgets.QSizePolicy.Ignored,
                                    QtWidgets.QSizePolicy.Fixed)
                value.setMinimumWidth(90)
                value.setStyleSheet("font: bold 14pt monospace;")
                bars.addWidget(QtWidgets.QLabel(name), row, col)
                bars.addWidget(meter, row, col + 1)
                bars.addWidget(value, row, col + 2)
        bars.setColumnStretch(1, 1)
        bars.setColumnStretch(4, 1)
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
        self.tune_button = QtWidgets.QPushButton("TUNE")
        self.tune_button.setToolTip("Accorde l'antenne avec la boîte d'accord "
                                    "interne. La radio émet une porteuse.")
        self.tune_button.clicked.connect(self.on_tune)
        self.atu_button = QtWidgets.QPushButton("ATU")
        self.atu_button.setCheckable(True)
        self.atu_button.setToolTip("Boîte d'accord interne en service")
        # en vert quand la boîte d'accord est en service, comme ⏻ ON
        self.atu_button.setStyleSheet("QPushButton:checked { background: #2e7d32; "
                                      "color: white; font-weight: bold; }")
        self.atu_button.clicked.connect(self.on_atu)
        vfo.addWidget(self.tune_button, 4, 0, 1, 2)
        vfo.addWidget(self.atu_button, 4, 2, 1, 2)
        self.power_button = QtWidgets.QPushButton("⏻ OFF")
        self.power_button.setCheckable(True)
        self.power_button.setToolTip("Met la radio en marche ou l'arrête")
        self.power_button.clicked.connect(self.on_power_button)
        self.radio_on = False
        vfo.addWidget(self.power_button, 5, 0, 1, 4)

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
        memory_row = (len(BANDS) + 1) // 2
        # mémoires et SSTV sous les bandes
        memory_button = QtWidgets.QPushButton("Mémoires…")
        memory_button.setToolTip("Les fréquences mémorisées : double-clic pour y aller")
        memory_button.clicked.connect(self.open_memories)
        self.memory_window = None
        self.radio_memories = {}        # canaux de la radio : canal → (nom, Hz, mode)
        self.radio_memory_status = "Mémoires de la radio pas encore lues"
        self.radio_memory_odd = set()   # canaux remplis mais illisibles : jamais écrasés
        self.radio_memories_ready = False
        self._memories_requested = False
        bands.addWidget(memory_button, memory_row, 0, 1, 2)
        sstv_button = QtWidgets.QPushButton("SSTV…")
        sstv_button.setToolTip("Réception et émission d'images SSTV, fax météo, "
                               "dans une fenêtre à part")
        sstv_button.clicked.connect(self.open_sstv)
        bands.addWidget(sstv_button, memory_row + 1, 0, 1, 2)
        # presets du PC : mode, filtre, puissance… d'un clic
        self.preset_combo = QtWidgets.QComboBox()
        self.preset_combo.setToolTip("Règle la radio d'un clic : mode, filtre, largeur, "
                                     "puissance, USB MOD Level et décodeur")
        self.preset_combo.activated.connect(self.on_preset)
        try:
            with open(PRESETS_FILE) as f:
                self.presets = [p for p in json.load(f) if p.get("name")]
        except (OSError, ValueError, TypeError, AttributeError):
            self.presets = [dict(p) for p in DEFAULT_PRESETS]
        bands.addWidget(self.preset_combo, memory_row + 2, 0, 1, 2)

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
        graphics.ci.layout.setRowStretchFactor(1, 1)
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
        self.ft_tx_lines = []
        for plot in (self.spec_plot, self.wf_plot):
            line = pg.InfiniteLine(angle=90, pen=pg.mkPen("#ef5350", width=1,
                                                          style=QtCore.Qt.DashLine))
            line.setVisible(False)
            plot.addItem(line, ignoreBounds=True)
            self.ft_tx_lines.append(line)
        # fréquence sous la souris, dans le coin haut gauche du spectre
        self.cursor_text = pg.TextItem("", color="#ffffff", anchor=(0, 0),
                                       fill=(16, 20, 24, 200))
        self.cursor_text.setFont(QtGui.QFont("monospace", 12, QtGui.QFont.Bold))
        self.cursor_text.setParentItem(self.spec_plot.vb)  # hors des coordonnées
        self.cursor_text.setPos(6, 6)
        graphics.scene().sigMouseMoved.connect(self.on_mouse_moved)
        graphics.scene().sigMouseClicked.connect(self.on_mouse_clicked)
        # molette : un pas par cran ; glisser en vue RF : vers la droite, la fréquence monte
        self.drag = None                # (x de départ, kHz par pixel, fréquence)
        self.wf_wheel = 0               # molette sur le waterfall, en 1/120 de cran
        self.dragged = False
        # tirer la limite entre spectre et waterfall : change leur hauteur
        self.split_drag = False
        self.graphics = graphics
        graphics.viewport().installEventFilter(self)
        layout.addWidget(graphics, stretch=2)

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

        # Son : réception sur la première ligne, émission sur la seconde, en
        # colonnes alignées : appareil du PC, niveau du PC, niveau de la
        # radio, gain de la radio
        sound = QtWidgets.QGridLayout()
        sound.setHorizontalSpacing(8)
        for col in SOUND_GAPS:
            sound.setColumnMinimumWidth(col, 14)
        sound.setColumnStretch(SOUND_COLUMNS, 1)
        self.pc_volume = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.pc_volume.setRange(0, 150)
        self.pc_volume.setFixedWidth(SLIDER_WIDTH)
        self.pc_volume.setToolTip("Volume du son de la radio sur le PC")
        self.pc_volume_label = QtWidgets.QLabel()
        self.pc_volume_label.setMinimumWidth(50)
        self.mute_check = QtWidgets.QCheckBox("Muet")
        self.af_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.af_slider.setRange(0, 255)
        self.af_slider.setFixedWidth(SLIDER_WIDTH)
        self.af_slider.setToolTip("Volume du haut-parleur de la radio (AF). "
                                  "Ne change pas le son envoyé au PC.")
        self.af_label = QtWidgets.QLabel()
        self.af_label.setMinimumWidth(50)
        # sortie du PC où l'on entend la radio (haut-parleurs, casque…)
        self.pc_output = QtWidgets.QComboBox()
        self.pc_output.setToolTip("Sortie du PC où l'on entend la radio")
        self.pc_output.setSizeAdjustPolicy(
            QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.pc_output.setMinimumContentsLength(22)
        sound.addWidget(sound_title("RX", "#3ddc84"), 0, 0)
        sound.addWidget(QtWidgets.QLabel("Sortie"), 0, 1)
        sound.addWidget(self.pc_output, 0, 2)
        sound.addWidget(self.mute_check, 0, 3)
        add_slider(sound, 0, 5, "Volume PC", self.pc_volume, self.pc_volume_label)
        add_slider(sound, 0, 9, "AF radio", self.af_slider, self.af_label)
        # gain RF et squelch de la radio
        self.level_sliders = {}
        for sub, name, tip in ((0x02, "RF", "Gain HF de la radio (RF GAIN). Le baisser "
                                "sur une bande bruyante ou un signal très fort."),
                               (0x03, "SQL", "Squelch de la radio : coupe le son sous "
                                "ce niveau. À zéro, toujours ouvert.")):
            slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
            slider.setRange(0, 255)
            slider.setFixedWidth(SLIDER_WIDTH)
            slider.setToolTip(tip)
            slider.setEnabled(False)        # activé quand la radio répond
            label = QtWidgets.QLabel()
            label.setMinimumWidth(45)
            slider.valueChanged.connect(
                lambda value, sub=sub: self.on_level_slider(sub, value))
            add_slider(sound, 0, 13 if sub == 0x02 else 17, name, slider, label)
            self.level_sliders[sub] = (slider, label)
        # filtres de réception de la radio, sur la ligne des réglages du scope
        green = ("QPushButton:checked { background: #2e7d32; color: white; "
                 "font-weight: bold; }")
        self.switch_buttons = {}

        def filter_button(code, text, tip):
            button = QtWidgets.QPushButton(text)
            button.setCheckable(True)
            button.setEnabled(False)            # activé quand la radio répond
            button.setStyleSheet(green)
            button.setToolTip(tip)
            button.setMinimumWidth(48)
            button.clicked.connect(lambda on, c=code: self.on_switch_button(c, on))
            self.switch_buttons[code] = button
            return button

        def filter_slider(sub, tip):
            slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
            slider.setRange(0, 255)
            slider.setFixedWidth(90)
            slider.setEnabled(False)
            slider.setToolTip(tip)
            label = QtWidgets.QLabel()
            label.setMinimumWidth(36)
            slider.valueChanged.connect(lambda value, s=sub: self.on_level_slider(s, value))
            self.level_sliders[sub] = (slider, label)
            return slider, label

        widgets = [QtWidgets.QLabel("  "),
                   filter_button(SW_NB, "NB", "Noise Blanker : contre les crépitements "
                                 "(allumages, clôtures, alimentations). À couper près "
                                 "d'une station forte."),
                   *filter_slider(0x12, "Niveau du NB (50 % pour commencer)"),
                   filter_button(SW_NR, "NR", "Réduction de bruit de la radio, contre "
                                 "le souffle. Pas en modes numériques."),
                   *filter_slider(0x06, "Niveau du NR, de 0 à 15 : 4 à 7 en BLU, "
                                  "au-delà le son devient « sous l'eau »")]
        self.notch_combo = QtWidgets.QComboBox()
        for text, value in (("Notch —", 0), ("Notch auto", SW_AN), ("Notch manuel", SW_MN)):
            self.notch_combo.addItem(text, value)
        self.notch_combo.setEnabled(False)
        self.notch_combo.setToolTip("Contre une porteuse ou un sifflement : auto en BLU, "
                                    "manuel en CW et en numérique (l'auto effacerait le "
                                    "signal). Position du manuel : sur la radio")
        self.notch_combo.activated.connect(self.on_notch)
        self.preamp_combo = QtWidgets.QComboBox()
        for text, value in (("P.AMP —", 0), ("P.AMP 1", 1), ("P.AMP 2", 2)):
            self.preamp_combo.addItem(text, value)
        self.preamp_combo.setEnabled(False)
        self.preamp_combo.setToolTip("Préampli : à couper sous 10 MHz, le bruit de bande "
                                     "y est déjà plus fort que celui de la radio")
        self.preamp_combo.activated.connect(
            lambda i: self.on_switch_button(SW_PREAMP, self.preamp_combo.itemData(i)))
        widgets += [self.notch_combo, self.preamp_combo,
                    filter_button(SW_ATT, "ATT", "Atténuateur de 20 dB : bande saturée "
                                  "par des signaux très forts"),
                    filter_button(SW_IPPLUS, "IP+", "IP+ : meilleure tenue aux signaux "
                                  "forts (80 m, 40 m le soir)")]
        self.notch_state = {SW_AN: 0, SW_MN: 0}
        for widget in widgets:
            controls.insertWidget(controls.count() - 1, widget)    # avant l'espace

        self.loopback = find_loopback_stream()
        if self.loopback:
            _, volume, _, current = self.loopback
            self.pc_volume.setValue(volume)
            # la coupure laissée par une session précédente (squelch fermé,
            # émission) est retenue par PipeWire : on repart toujours non muet
            self._pactl_stream("set-sink-input-mute", "0")
            for index, name, description in list_outputs(
                    {args.sink, RADIO_SINK, REMOTE_TX}):
                self.pc_output.addItem(description, name)
                if index == current:
                    self.pc_output.setCurrentIndex(self.pc_output.count() - 1)
        else:
            for widget in (self.pc_volume, self.mute_check, self.pc_output):
                widget.setEnabled(False)
                widget.setToolTip("Écoute radio → PC introuvable dans PipeWire")
        self.pc_volume_label.setText(f"{self.pc_volume.value()} %")
        self.pc_volume.valueChanged.connect(self.on_pc_volume)
        self.mute_check.toggled.connect(self.on_mute)
        self.pc_output.currentIndexChanged.connect(self.on_pc_output)
        self.af_slider.setEnabled(False)    # activé quand la radio répond
        self.af_slider.valueChanged.connect(self.on_af_slider)
        layout.addLayout(controls)
        layout.addLayout(sound)
        layout.addSpacing(32)           # espace avant le décodeur

        # Décodeurs CW, RTTY, FT8 et FT4
        decoder_row = QtWidgets.QHBoxLayout()
        self.decoder_combo = QtWidgets.QComboBox()
        self.decoder_combo.addItem("Aucun", None)
        self.decoder_combo.addItem("CW", "cw")
        self.decoder_combo.addItem("RTTY 45", "rtty")
        self.decoder_combo.addItem("RTTY 50 météo", "rtty50")
        self.decoder_combo.addItem("FT8", "ft8")
        self.decoder_combo.addItem("FT4", "ft4")
        self._build_presets()               # les bulles des presets nomment le décodeur
        self.decoder_auto = QtWidgets.QCheckBox("Calage auto")
        self.decoder_auto.setChecked(True)
        self.rtty_invert = QtWidgets.QCheckBox("Inverser")
        self.rtty_invert.setToolTip("Mark sur la tonalité haute. À essayer si "
                                    "le texte reste incohérent.")
        self.rtty_invert.toggled.connect(self.on_rtty_invert)
        self.decoder_led = QtWidgets.QLabel("●")
        self.decoder_info = QtWidgets.QLabel("")
        self.decoder_info.setStyleSheet("font: 11pt monospace;")
        # mode à prendre pour émettre : CW ou CW-R pour la CW, mode data
        # (AFSK) pour le RTTY 45, USB-D pour FT8 et FT4
        self.mode_hint = QtWidgets.QLabel()
        self.mode_hint.setStyleSheet("font: italic 11pt; color: #90a4ae;")
        self.mode_hint.setVisible(False)
        self.mode_hint.setToolTip("Clic sur un mode : la radio y passe")
        self.mode_hint.linkActivated.connect(self.on_mode_hint)
        decoder_clear = QtWidgets.QPushButton("Effacer")
        decoder_row.addWidget(QtWidgets.QLabel("Décodeur"))
        decoder_row.addWidget(self.decoder_combo)
        decoder_row.addWidget(self.decoder_auto)
        decoder_row.addWidget(self.rtty_invert)
        decoder_row.addWidget(self.decoder_led)
        decoder_row.addWidget(self.decoder_info)
        decoder_row.addWidget(self.mode_hint)
        self.decoder_led.setMinimumWidth(16)
        decoder_row.addStretch()
        decoder_row.addWidget(decoder_clear)
        text_window_button = QtWidgets.QPushButton("Grande fenêtre")
        text_window_button.setToolTip("Le texte décodé (CW, RTTY, FT8…) dans une "
                                      "fenêtre à part, en grand (F11 : plein écran)")
        text_window_button.clicked.connect(self.open_text_window)
        self.text_window = None
        decoder_row.addWidget(text_window_button)
        # sans décodeur, pas de texte : ni Effacer ni Grande fenêtre
        self.decoder_text_buttons = (decoder_clear, text_window_button)
        self.record_button = QtWidgets.QPushButton("● Enreg.")
        self.record_button.setCheckable(True)
        self.record_button.setToolTip(f"Enregistre le son de la radio dans {RECORD_DIR}")
        self.record_button.toggled.connect(self.on_record)
        record_folder = QtWidgets.QPushButton("Dossier")
        record_folder.setToolTip("Ouvrir le dossier des enregistrements")
        record_folder.clicked.connect(self.open_record_folder)
        # sous SSTV, dans la colonne des bandes
        bands.addWidget(self.record_button, memory_row + 3, 0)
        bands.addWidget(record_folder, memory_row + 3, 1)
        self.record_timer = QtCore.QTimer(self)
        self.record_timer.setInterval(500)
        self.record_timer.timeout.connect(self.update_record_label)
        self.record_path = None
        self.decoder_text = QtWidgets.QPlainTextEdit()
        self.decoder_text.setReadOnly(True)
        self.decoder_text.setMaximumBlockCount(500)
        self.decoder_text.setMinimumHeight(70)
        self.decoder_text.setStyleSheet("font: bold 14pt monospace; color: #3ddc84;")
        # FT8 / FT4 : à droite, seulement le fil de mes QSO (lignes avec mon
        # indicatif et mes émissions), comme le panneau de droite de WSJT-X
        self.reply_text = QtWidgets.QPlainTextEdit()
        self.reply_text.setReadOnly(True)
        self.reply_text.setMaximumBlockCount(300)
        self.reply_text.setStyleSheet("font: bold 14pt monospace; color: #3ddc84;")
        self.reply_text.setPlaceholderText("Réponses : les lignes avec mon indicatif "
                                           "et mes émissions")
        self.reply_text.setToolTip("Le fil de mes QSO. Double-clic sur une ligne : "
                                   "réponse préparée")
        self.reply_text.setVisible(False)
        self.decoder_box = QtWidgets.QWidget()
        box = QtWidgets.QHBoxLayout(self.decoder_box)
        box.setContentsMargins(0, 0, 0, 0)
        box.addWidget(self.decoder_text, stretch=3)
        box.addWidget(self.reply_text, stretch=2)
        self.decoder_box.setVisible(False)
        layout.addLayout(decoder_row)
        layout.addWidget(self.decoder_box, stretch=1)

        # Émission : micro du PC ou fichier, envoyés à la radio par l'USB
        self.radio_sink = args.sink
        self.power_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.power_slider.setRange(0, 100)
        self.power_slider.setFixedWidth(SLIDER_WIDTH)
        self.power_slider.setToolTip("Puissance d'émission de la radio")
        self.power_slider.setEnabled(False)     # activé quand la radio répond
        self.power_label = QtWidgets.QLabel("")
        self.power_label.setMinimumWidth(55)
        self.tx_source = QtWidgets.QComboBox()
        for name, description in list_microphones(args.source):
            self.tx_source.addItem(description, name)
        self.tx_source.addItem("Fichier audio", "file")
        self.tx_source.setToolTip("Ce qui part à l'émission")
        # les noms des micros peuvent être très longs : la liste déroulée les
        # montre en entier, la case reste raisonnable
        self.tx_source.setSizeAdjustPolicy(
            QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.tx_source.setMinimumContentsLength(22)
        self.tx_file = None
        self.tx_file_button = QtWidgets.QPushButton("Choisir…")
        self.tx_file_button.clicked.connect(self.choose_tx_file)
        self.tx_level = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.tx_level.setRange(0, 100)
        self.tx_level.setFixedWidth(SLIDER_WIDTH)
        self.tx_level.setToolTip("Niveau du son envoyé à la radio. Le régler "
                                 "pour que l'ALC bouge sans dépasser.")
        volume = sink_volume(self.radio_sink)
        self.tx_level.setValue(volume if volume is not None else 50)
        self.tx_level_label = QtWidgets.QLabel(f"{self.tx_level.value()} %")
        self.tx_level_label.setMinimumWidth(50)
        self.tx_level.valueChanged.connect(self.on_tx_level)
        # gain USB de la radio : c'est lui qui fait monter l'ALC
        self.alc_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.alc_slider.setRange(0, 100)
        self.alc_slider.setFixedWidth(SLIDER_WIDTH)
        self.alc_slider.setToolTip("Gain de l'entrée USB de la radio (menu USB MOD "
                                   "Level). En émission, le monter jusqu'à ce que "
                                   "l'ALC bouge, sans rester au maximum.")
        self.alc_slider.setEnabled(False)       # activé quand la radio répond
        self.alc_slider_label = QtWidgets.QLabel("")
        self.alc_slider_label.setMinimumWidth(45)
        self.tx_meter_label = QtWidgets.QLabel("")
        self.tx_meter_label.setStyleSheet("font: 11pt monospace;")
        self.ptt_button = QtWidgets.QPushButton("ÉMETTRE")
        self.ptt_button.setCheckable(True)
        self.ptt_button.setMinimumWidth(120)
        self.ptt_button.setToolTip(f"La radio émet. Coupure automatique après "
                                   f"{TX_TIMEOUT // 60} min (ou la durée du "
                                   f"fichier s'il est plus long).")
        self.ptt_button.toggled.connect(self.on_ptt)
        sound.addWidget(sound_title("TX", "#ef5350"), 1, 0)
        sound.addWidget(QtWidgets.QLabel("Entrée"), 1, 1)
        sound.addWidget(self.tx_source, 1, 2)
        sound.addWidget(self.tx_file_button, 1, 3)
        add_slider(sound, 1, 5, "Niveau PC", self.tx_level, self.tx_level_label)
        add_slider(sound, 1, 9, "Puissance", self.power_slider, self.power_label)
        add_slider(sound, 1, 13, "Réglage ALC", self.alc_slider, self.alc_slider_label)
        # compresseur de parole : bouton en service (vert) et niveau 0 à 10,
        # sous SQL ; il ne joue qu'en BLU, à la voix
        self.comp_button = QtWidgets.QPushButton("COMP")
        self.comp_button.setCheckable(True)
        self.comp_button.setEnabled(False)      # activé quand la radio répond
        self.comp_button.setToolTip("Compresseur de parole de la radio (BLU) : plus de "
                                    "puissance moyenne. Le bargraph COMP montre son effet "
                                    "en émission, viser 10 à 20 dB.")
        self.comp_button.setStyleSheet("QPushButton:checked { background: #2e7d32; "
                                       "color: white; font-weight: bold; }")
        self.comp_button.clicked.connect(self.on_comp_button)
        slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        slider.setRange(0, 255)
        slider.setFixedWidth(SLIDER_WIDTH)
        slider.setToolTip("Niveau du compresseur (menu COMP de la radio, 0 à 10)")
        slider.setEnabled(False)
        label = QtWidgets.QLabel()
        label.setMinimumWidth(45)
        slider.valueChanged.connect(lambda value: self.on_level_slider(0x0E, value))
        self.level_sliders[0x0E] = (slider, label)
        sound.addWidget(self.comp_button, 1, 17)
        sound.addWidget(slider, 1, 18)
        sound.addWidget(label, 1, 19)
        sound.addWidget(self.tx_meter_label, 1, 21)
        # sous Enreg./Dossier, dans la colonne des bandes
        bands.addWidget(self.ptt_button, memory_row + 4, 0, 1, 2)
        bands.setRowStretch(memory_row + 5, 1)
        self._set_ptt_button(False)

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
        self.cw_hand = QtWidgets.QCheckBox("Pioche")
        self.cw_hand.setToolTip(
            "Frappe humaine, comme au manipulateur droit : rythme irrégulier\n"
            "au lieu des points et traits parfaits du manipulateur de la radio.\n"
            "Menu de la radio : SET > Connectors > USB SEND/Keying >\n"
            "USB Keying (CW) sur DTR.")
        # direct : chaque touche part aussitôt, comme au manipulateur
        self.cw_direct = QtWidgets.QCheckBox("Direct")
        self.cw_direct.setToolTip(
            "Chaque lettre tapée part aussitôt (pas d'Entrée). Pas de retour\n"
            "en arrière possible une fois la lettre partie. Avec la pioche,\n"
            "le mot part à l'espace.")
        self.cw_direct.toggled.connect(self.on_cw_direct)
        self.cw_direct_sent = ""        # texte du champ déjà émis en direct
        self.cw_echo_space = False      # en direct : espace à écrire avant la lettre suivante
        self.cw_text.textEdited.connect(self.on_cw_typed)
        QtWidgets.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key_Escape), self.cw_text,
                            lambda: self.on_cw_stop(), context=QtCore.Qt.WidgetShortcut)
        self.cw_send_button = QtWidgets.QPushButton("Envoyer")
        self.cw_send_button.clicked.connect(self.on_cw_send)
        self.cw_stop_button = QtWidgets.QPushButton("Stop")
        self.cw_stop_button.setToolTip("Arrête le message en cours (Échap)")
        self.cw_stop_button.clicked.connect(self.on_cw_stop)
        self.cw_echo = []           # lettres émises à écrire : (instant, texte, début)
        self.cw_echo_end = 0.0      # fin prévue du dernier message
        self.cw_echo_held = ""      # texte décodé pendant l'émission, écrit après
        self.cw_echo_timer = QtCore.QTimer(self)
        self.cw_echo_timer.setInterval(20)
        self.cw_echo_timer.timeout.connect(self.show_cw_echo)
        self.tx_text_label = QtWidgets.QLabel("CW")
        # FT8 / FT4 : créneau, répétition et fréquence d'émission
        self.ft_parity = QtWidgets.QComboBox()
        self.ft_parity.addItem("Pair", 0)
        self.ft_parity.addItem("Impair", 1)
        self.ft_parity.setToolTip("Créneau d'émission : pair (FT8 : 00 et 30 s) ou impair "
                                  "(15 et 45 s). Toujours l'inverse du correspondant ; "
                                  "le double-clic sur une ligne décodée le règle")
        self.ft_repeat = QtWidgets.QCheckBox("Répéter")
        self.ft_repeat.setChecked(True)
        self.ft_repeat.setToolTip(
            "CQ ou appel répété à chaque cycle jusqu'à Stop (Échap). Les réponses\n"
            "partent toutes seules ; l'appel s'arrête si la station appelée répond\n"
            "à une autre. RR73 et 73 ne partent qu'une fois, puis le CQ reprend.")
        self.ft_tx_hz = 1500
        self.ft_tx_label = QtWidgets.QLabel()
        self.ft_tx_label.setToolTip("Fréquence audio d'émission : clic sur le spectre "
                                    "pour la changer (ligne rouge)")
        self.ft_armed = None            # message en attente : dict, ou None
        self.ft_last_slot = None        # créneau de la dernière émission
        self.ft_report = "-10"          # report à envoyer
        self.ft_qso = {}                # QSO en cours : call, grid, sent, rcvd, from_cq
        self.ft_cq = None               # (CQ, créneau) à relancer après un QSO
        self.ft_timer = QtCore.QTimer(self)
        self.ft_timer.setInterval(50)
        self.ft_timer.timeout.connect(self.check_ft)
        cw_row.addWidget(self.tx_text_label)
        cw_row.addWidget(self.cw_text, stretch=1)
        cw_row.addWidget(self.cw_speed)
        cw_row.addWidget(self.cw_hand)
        cw_row.addWidget(self.cw_direct)
        cw_row.addWidget(self.ft_parity)
        cw_row.addWidget(self.ft_repeat)
        cw_row.addWidget(self.ft_tx_label)
        cw_row.addWidget(self.cw_send_button)
        cw_row.addWidget(self.cw_stop_button)
        # indicatif et messages tout faits
        macro_row = QtWidgets.QHBoxLayout()
        self.callsign = QtWidgets.QLineEdit()
        self.callsign.setPlaceholderText("Indicatif")
        self.callsign.setMaximumWidth(120)
        self.callsign.setStyleSheet("font: 12pt monospace;")
        self.station = {key: value for key, _, value in STATION_FIELDS}
        try:
            with open(CALLSIGN_FILE) as f:
                self.station.update(json.load(f))
        except (OSError, ValueError):
            pass
        self.callsign.setText(self.station.get("callsign", ""))
        if args.port.startswith("socket://"):
            # la ligne DTR ne passe pas par le réseau
            self.cw_hand.setToolTip("Pioche impossible avec --remote : la ligne "
                                    "DTR de la radio ne passe pas par le réseau")
            self.station["pioche"] = False
            self.cw_hand.setEnabled(False)
        self.cw_hand.setChecked(bool(self.station.get("pioche")))
        self.cw_hand.toggled.connect(self.on_cw_hand)
        station_button = QtWidgets.QPushButton("Ma station…")
        station_button.setToolTip("Prénom, QTH, locator et matériel des messages")
        station_button.clicked.connect(self.on_station)
        self.callsign.textEdited.connect(
            lambda text: self._upper(self.callsign, text))
        self.callsign.editingFinished.connect(self.on_callsign)
        # correspondant : change à chaque QSO, pas enregistré
        self.dest_call = QtWidgets.QLineEdit()
        self.dest_call.setPlaceholderText("Correspondant")
        self.dest_call.setMaximumWidth(120)
        self.dest_name = QtWidgets.QLineEdit()
        self.dest_name.setPlaceholderText("Son prénom")
        self.dest_name.setMaximumWidth(120)
        CallsignClick(self.decoder_text, self.set_correspondent)
        CallsignClick(self.reply_text, self.set_correspondent)
        self.qrz = None                     # session qrz.com, ouverte au besoin
        self.qrz_asked = ""                 # dernier indicatif cherché
        self.qrz_found.connect(self.on_qrz_found)
        self.dest_call.editingFinished.connect(
            lambda: self.lookup_name(self.dest_call.text().strip()))
        for field in (self.dest_call, self.dest_name):
            field.setStyleSheet("font: 12pt monospace;")
            field.textEdited.connect(
                lambda text, field=field: self._upper(field, text))
        macro_row.addWidget(QtWidgets.QLabel("Indicatif"))
        macro_row.addWidget(self.callsign)
        macro_row.addWidget(station_button)
        macro_row.addSpacing(10)
        macro_row.addWidget(QtWidgets.QLabel("Correspondant"))
        macro_row.addWidget(self.dest_call)
        qrz_button = QtWidgets.QPushButton(magnifier_icon(), "")
        qrz_button.setToolTip("Fiche du correspondant sur qrz.com")
        qrz_button.setMaximumWidth(36)
        qrz_button.clicked.connect(self.on_qrz_lookup)
        macro_row.addWidget(qrz_button)
        macro_row.addWidget(self.dest_name)
        macro_row.addSpacing(10)
        self.cw_macro_buttons = []
        self.cw_macro_row = QtWidgets.QHBoxLayout()
        self.cw_macro_row.setContentsMargins(0, 0, 0, 0)
        macro_row.addLayout(self.cw_macro_row)
        # une série de messages par mode, celle du mode d'émission affichée
        self.macro_sets = {}
        for kind, path in MACROS_FILES.items():
            try:
                with open(path) as f:
                    self.macro_sets[kind] = [tuple(m) for m in json.load(f)]
            except (OSError, ValueError, TypeError):
                self.macro_sets[kind] = list(DEFAULT_MACROS[kind])
        self.macro_kind = "cw"
        self._build_cw_macros()
        self.edit_macros_button = QtWidgets.QPushButton("✎")
        self.edit_macros_button.setMaximumWidth(36)
        self.edit_macros_button.setToolTip("Modifier les messages CW pré-enregistrés")
        self.edit_macros_button.clicked.connect(self.on_edit_macros)
        macro_row.addWidget(self.edit_macros_button)
        # abréviation de fin : remplace celle qui termine déjà le message
        self.cw_ending = QtWidgets.QComboBox()
        self.cw_ending.addItem("Fin de message…", None)
        for text, meaning in CW_ENDINGS:
            self.cw_ending.addItem(f"{text.replace('^', '')}  ({meaning})", text)
        self.cw_ending.setToolTip("Ajoute une abréviation à la fin du message CW, "
                                  "à la place de celle qui y est déjà")
        self.cw_ending.setSizeAdjustPolicy(
            QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.cw_ending.setMinimumContentsLength(14)
        self.cw_ending.activated.connect(self.on_cw_ending)
        macro_row.addSpacing(10)
        macro_row.addWidget(self.cw_ending)
        self.cw_macro_buttons.append(self.cw_ending)
        macro_row.addStretch()
        log_button = QtWidgets.QPushButton("Noter le QSO")
        log_button.setToolTip("Fiche du QSO, envoyée à Wavelog et au carnet "
                              f"qrz.com, copie dans {LOG_FILE}")
        log_button.clicked.connect(self.on_log_qso)
        macro_row.addWidget(log_button)
        logbook_button = QtWidgets.QPushButton("Carnet…")
        logbook_button.setToolTip(f"Les QSO notés ({LOG_FILE})")
        logbook_button.clicked.connect(self.on_logbook)
        macro_row.addWidget(logbook_button)
        self.logbook_window = None
        self.log_results = {}           # indicatif → résultats des envois
        self.log_sent.connect(self.on_log_sent)
        # juste sous le texte décodé : réception puis réponse
        index = layout.indexOf(self.decoder_box) + 1
        layout.insertLayout(index, cw_row)
        layout.insertLayout(index + 1, macro_row)
        # en phonie (USB, LSB) ces deux lignes se cachent, sauf le carnet
        self.tx_rows = (cw_row, macro_row)
        self.voice_keep = (log_button, logbook_button)
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
        self.sql_closed = False             # écoute coupée, squelch de la radio fermé
        self.radio_tx = False               # la radio émet (lu en CI-V)
        self.tx_player = None               # paplay du fichier
        self.tx_once = None                 # fichier émis une fois (RTTY), sans
                                            # changer l'entrée choisie
        self.tx_timer = QtCore.QTimer(self)
        self.tx_timer.setInterval(250)
        self.tx_timer.timeout.connect(self.check_tx)
        self.decoder_combo.currentIndexChanged.connect(self.on_decoder_changed)
        self.decoder_auto.toggled.connect(self.on_decoder_auto)
        decoder_clear.clicked.connect(self.decoder_text.clear)
        decoder_clear.clicked.connect(self.reply_text.clear)
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
            self.civ.filter_width.connect(self.on_filter_width)
            self.civ.other_frequency.connect(self.on_other_frequency)
            self.civ.other_mode.connect(self.on_other_mode)
            self.civ.af_gain.connect(self.on_af_gain)
            self.civ.level.connect(self.on_level)
            self.civ.compressor.connect(self.on_compressor)
            self.civ.switch.connect(self.on_switch)
            self.civ.tuner.connect(self.on_tuner)
            self.civ.power.connect(self.on_power)
            self.civ.key_speed.connect(self.on_key_speed)
            self.civ.transmitting.connect(self.on_transmitting)
            self.civ.tx_meters.connect(self.on_tx_meters)
            self.civ.usb_mod.connect(self.on_usb_mod)
            self.civ.comp.connect(self.on_comp)
            self.civ.drain.connect(self.on_drain)
            self.civ.voltage.connect(self.on_voltage)
            self.civ.scope.connect(self.on_scope)
            self.civ.span.connect(self.on_span)
            self.civ.powered.connect(self.on_powered)
            self.civ.memory_channel.connect(self.on_memory_channel)
            self.civ.squelch_open.connect(self.on_squelch)
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
        self._show_ft_tx_line()
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
        mode = base_mode(self.mode_combo.currentText())
        width = None if self.width_index is None else width_hz(mode, self.width_index)
        low, high = passband(mode, self.filter_num, width)
        for line in self.tuned_lines:
            line.setPos(f)
        for band in self.passbands:
            band.setRegion((f + low, f + high))
        self._show_ft_tx_line()

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
        if base_mode(name) == "FM":        # largeur fixe en FM
            self.width_slider.setEnabled(False)
            self.width_index = None
            self.width_label.setText("fixe")
        self._update_tx_text()
        self._filter_tooltips()
        self._update_markers()

    def on_filter(self, number):
        self.filter_num = number
        button = self.filter_buttons.button(number)
        if button:
            button.setChecked(True)
        self._update_markers()

    def on_filter_width(self, index):
        """Largeur lue sur la radio."""
        mode = base_mode(self.mode_combo.currentText())
        self.width_slider.setEnabled(mode != "FM")
        self.width_slider.blockSignals(True)
        self.width_slider.setMaximum(49 if mode == "AM" else 40)
        if not self.width_slider.isSliderDown():
            self.width_slider.setValue(index)
        self.width_slider.blockSignals(False)
        if not self.width_slider.isSliderDown():
            self._show_width(index)

    def on_width_slider(self, index):
        if self.civ:
            self.civ.set_filter_width(index)
        self._show_width(index)

    def _show_width(self, index):
        self.width_index = index
        hz = width_hz(base_mode(self.mode_combo.currentText()), index)
        self.width_label.setText(width_text(hz) if hz else "fixe")
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

    def update_clock(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        self.utc_label.setText(f"{now:%H:%M:%S}")
        self.local_label.setText(f"{now.astimezone():%H:%M:%S}")
        # prochain tic juste après le changement de seconde
        self.clock_timer.start(1000 - now.microsecond // 1000 + 5)

    def on_freq_entered(self):
        hz = parse_frequency(self.freq_entry.text())
        if hz is None:
            self.statusBar().showMessage("Fréquence non comprise")
            return
        self.freq_entry.clear()
        self.tune_to(hz)

    def on_mode_selected(self, name):
        self._update_tx_text()
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
        for widget in (self.cw_text, self.cw_send_button, self.cw_stop_button,
                       *self.cw_macro_buttons):
            widget.setEnabled(on)
        self.cw_text.setToolTip("" if on else "Passer la radio en CW pour émettre, "
                                "ou en LSB-D/USB-D avec le décodeur RTTY, "
                                "ou en USB-D avec le décodeur FT8 ou FT4")

    def _rtty_tx(self):
        """Le texte part en RTTY : décodeur RTTY et radio en BLU (AFSK)."""
        return (self.decoder_combo.currentData() in RTTY_DECODERS
                and base_mode(self.mode_combo.currentText()) in ("LSB", "USB"))

    def _ft_tx(self):
        """Le texte part en FT8 / FT4 : décodeur FT et radio en USB."""
        return (self.decoder_combo.currentData() in FT_DECODERS
                and base_mode(self.mode_combo.currentText()) == "USB")

    def _update_tx_text(self):
        """Ligne d'émission en CW (manipulateur de la radio), en RTTY ou en FT."""
        rtty = self._rtty_tx()
        ft = self._ft_tx()
        cw = self.mode_combo.currentText() in ("CW", "CW-R")
        self._cw_enable(getattr(self, "civ", None) is not None and (cw or rtty or ft))
        self.tx_text_label.setText(
            "RTTY" if rtty else self.decoder_combo.currentText() if ft else "CW")
        # mode de la radio qu'il faut pour répondre avec ce décodeur
        # (FT8 et FT4 : toujours en USB, en mode data)
        ft_hint = (self.mode_combo.currentText() != "USB-D", ("USB-D",))
        hint = {"rtty": (not self.mode_combo.currentText().endswith("-D"), ("USB-D", "LSB-D")),
                "cw": (not cw, ("CW", "CW-R")),
                "ft8": ft_hint, "ft4": ft_hint}.get(self.decoder_combo.currentData())
        self.mode_hint.setVisible(bool(hint and hint[0]))
        if hint:
            # chaque mode est un lien : un clic y met la radio
            links = " ou ".join(f'<a href="{m}" style="color: #4fc3f7;">{m}</a>'
                                for m in hint[1])
            self.mode_hint.setText(f"passer en {links}")
        self.cw_text.setPlaceholderText(
            "Texte à émettre en RTTY sur les tonalités du décodeur, Entrée pour envoyer"
            if rtty else
            "Message à émettre au prochain créneau choisi, Entrée pour l'armer "
            "(double-clic sur une ligne décodée : réponse émise toute seule)"
            if ft else "Texte à émettre en CW, Entrée pour envoyer "
            "(^AR, ^SK… pour les signes liés)")
        # ce qui doit se voir, appliqué une seule fois plus bas : la radio
        # renvoie son mode chaque seconde, rien ne doit clignoter
        shown = {widget: not (rtty or ft)
                 for widget in (self.cw_speed, self.cw_hand, self.cw_direct)}
        shown.update({widget: ft for widget in (self.ft_parity, self.ft_repeat,
                                                 self.ft_tx_label)})
        shown[self.cw_ending] = not ft
        self._show_ft_tx_line()
        kind = "rtty" if rtty else "ft" if ft else "cw"
        if kind != self.macro_kind:
            self.macro_kind = kind
            self._build_cw_macros()
            self.edit_macros_button.setToolTip(
                f"Modifier les messages {kind.upper()} pré-enregistrés")
        # phonie, ou pas de décodeur : rien à émettre en texte, seuls Noter le
        # QSO et Carnet… restent
        hide_all = (self.mode_combo.currentText() in ("USB", "LSB")
                    or self.decoder_combo.currentData() is None)
        pending = list(getattr(self, "tx_rows", ()))
        while pending:
            layout = pending.pop()
            for i in range(layout.count()):
                item = layout.itemAt(i)
                if item.layout():
                    pending.append(item.layout())
                widget = item.widget()
                if widget:
                    # sans effet si déjà dans cet état : pas de saut d'affichage
                    widget.setVisible(widget in self.voice_keep
                                      or (not hide_all and shown.get(widget, True)))

    def _show_ft_tx_line(self):
        """Ligne rouge : fréquence d'émission FT, en Hz audio ou en kHz RF."""
        if not hasattr(self, "ft_tx_label"):
            return                      # pendant la construction de la fenêtre
        self.ft_tx_label.setText(f"TX {self.ft_tx_hz} Hz")
        x = self.ft_tx_hz
        visible = self._ft_tx()
        if self.view == "rf":
            sign = {"USB": 1, "LSB": -1}.get(base_mode(self.mode_combo.currentText()))
            if self.freq_hz is None or sign is None:
                visible = False
            else:
                x = (self.freq_hz + sign * self.ft_tx_hz) / 1000
        for line in self.ft_tx_lines:
            line.setVisible(visible)
            line.setPos(x)

    def set_ft_tx_hz(self, hz):
        """Fréquence audio d'émission FT, choisie au clic sur le spectre."""
        self.ft_tx_hz = int(min(max(round(hz), 200), 2900))
        self._show_ft_tx_line()
        if self.ft_armed:
            self._ft_make_audio()       # le message armé part sur la nouvelle fréquence
        self.statusBar().showMessage(f"Émission FT sur {self.ft_tx_hz} Hz")

    def arm_ft(self, text):
        """Message FT8 / FT4 armé : il part au prochain créneau choisi."""
        message = " ".join(text.upper().split())
        mode = self.decoder_combo.currentData()
        if not message or not self.civ:
            return
        try:
            tones = ft_encode(message, mode)
        except Ft8EncodeError as e:
            self.statusBar().showMessage(f"{mode.upper()} : {e}")
            return
        self.ft_armed = {"message": message, "mode": mode, "tones": tones,
                         "parity": self.ft_parity.currentData(), "since": time.time()}
        if message.startswith("CQ "):
            # relancé tout seul à la fin de chaque QSO venu de ce CQ
            self.ft_cq = (message, self.ft_parity.currentIndex())
        if not self._ft_make_audio():
            return
        # un message déjà émis dans ce créneau ne bloque pas le suivant
        self.ft_last_slot = None
        self.cw_text.clear()
        self.ft_timer.start()
        slot = FT_SLOTS[mode]
        now = time.time()
        index = int(now // slot)
        late = now - index * slot
        if index % 2 == self.ft_armed["parity"] and late < FT_LATE[mode]:
            when = "tout de suite"
        else:
            nxt = index + 1 if (index + 1) % 2 == self.ft_armed["parity"] else index + 2
            when = "à " + time.strftime("%H:%M:%S", time.gmtime(nxt * slot)) + " UTC"
        self.statusBar().showMessage(f"{mode.upper()} : « {message} » {when}, "
                                     f"{self.ft_tx_hz} Hz (Échap pour annuler)")

    def _ft_make_audio(self):
        armed = self.ft_armed
        samples = ft_waveform(armed["tones"], self.ft_tx_hz, armed["mode"], RATE)
        try:
            os.makedirs(os.path.dirname(FT_TX_FILE), exist_ok=True)
            with wave.open(FT_TX_FILE, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(RATE)
                w.writeframes((samples * 32767).astype(np.int16).tobytes())
        except OSError as e:
            self.disarm_ft(f"FT impossible : {e}")
            return False
        return True

    def disarm_ft(self, reason=None):
        self.ft_armed = None
        self.ft_timer.stop()
        if reason:
            self.statusBar().showMessage(reason)

    def check_ft(self):
        """Toutes les 50 ms : le message armé part au début de son créneau."""
        armed = self.ft_armed
        if not armed or self.tx_active:
            return
        if not self._ft_tx() or armed["mode"] != self.decoder_combo.currentData():
            self.disarm_ft("FT : émission annulée (décodeur ou mode changé)")
            return
        slot = FT_SLOTS[armed["mode"]]
        now = time.time()
        index = int(now // slot)
        late = now - index * slot
        if (index % 2 != armed["parity"] or late >= FT_LATE[armed["mode"]]
                or index == self.ft_last_slot):
            return
        self.ft_last_slot = index
        # la radio passe en émission puis le son part 400 ms après (start_tx) :
        # on la déclenche juste après le début du créneau pour un son à +0,5 s
        if late < FT_START - 0.4:
            QtCore.QTimer.singleShot(int((FT_START - 0.4 - late) * 1000), self._ft_go)
        else:
            self._ft_go()

    def _ft_go(self):
        armed = self.ft_armed
        if not armed or self.tx_active:
            return
        self.tx_once = FT_TX_FILE
        self.tx_digital = True
        self.ptt_button.setChecked(True)
        if not self.tx_active:
            self.disarm_ft()
            return
        # le message émis, en rouge parmi les messages décodés
        cursor = self.decoder_text.textCursor()
        cursor.movePosition(QtGui.QTextCursor.End)
        if not cursor.atBlockStart():
            cursor.insertText("\n", QtGui.QTextCharFormat())
        sent = QtGui.QTextCharFormat()
        sent.setForeground(QtGui.QColor(TX_TEXT_COLOR))
        now = time.strftime("%H:%M:%S", time.gmtime())
        tx_line = f"{now}  TX     {self.ft_tx_hz:4d}  {armed['message']}"
        cursor.insertText(tx_line, sent)
        self._add_reply(tx_line, TX_TEXT_COLOR)
        cursor.insertText("\n", QtGui.QTextCharFormat())
        self.decoder_text.setTextCursor(cursor)
        self.decoder_text.ensureCursorVisible()
        words = armed["message"].split()
        if len(words) == 3 and re.fullmatch(r"R?[+-]\d\d", words[2]):
            self.ft_qso["sent"] = words[2].lstrip("R")
        # RR73 et 73 closent le QSO : une seule fois, puis le CQ reprend
        if words[-1] in ("RR73", "73"):
            self.disarm_ft()
            self._ft_qso_done()
        elif not self.ft_repeat.isChecked():
            self.disarm_ft()

    def ft_prepare(self, call, line):
        """Double-clic sur une ligne FT décodée : correspondant, créneau et
        message suivant du QSO, armé aussitôt (il part au prochain créneau)."""
        m = FT_LINE_RE.match(line.strip())
        my = self.callsign.text().strip().upper()
        if not m or not my:
            return False
        _, _, sec, snr, _, message = m.groups()
        words = [w.strip("<>") for w in message.split()]     # <indicatif abrégé>
        mode = self.decoder_combo.currentData()
        # leur créneau, et le nôtre à l'opposé
        their = round(int(sec) / FT_SLOTS[mode]) % 2
        report = f"{min(max(int(snr), -30), 30):+03d}"
        if words[0] == "CQ":
            # CQ [DX|POTA…] indicatif [locator]
            calls = [w for w in words[1:] if CALLSIGN_RE.fullmatch(w)]
            if not calls:
                return False
            dest, step = calls[0], "{dest} {call} {loc4}"
        elif words[0] == my and len(words) >= 2:
            dest = words[1]
            last = words[2] if len(words) > 2 else ""
            if last in ("RR73", "RRR"):
                step = "{dest} {call} 73"
            elif last == "73":
                step = None             # QSO fini : plus rien à envoyer
            elif re.fullmatch(r"R[+-]\d\d", last):
                step = "{dest} {call} RR73"
                self.ft_qso["rcvd"] = last[1:]
            elif re.fullmatch(r"[+-]\d\d", last):
                step = "{dest} {call} R{rpt}"
                self.ft_qso["rcvd"] = last
            else:                       # il répond à mon CQ avec son locator
                step = "{dest} {call} {rpt}"
        else:
            # QSO d'autres stations : on appelle celle qui émet (2e indicatif)
            dest = call if call != my else words[1] if len(words) > 1 else call
            step = "{dest} {call} {loc4}"
        if not CALLSIGN_RE.fullmatch(dest) or dest == my:
            return False
        if dest != self.ft_qso.get("call"):
            self.ft_qso = {"call": dest}
            self.dest_name.clear()
        grid = words[-1] if GRID_RE.fullmatch(words[-1]) else None
        if grid:
            self.ft_qso["grid"] = grid
        self.ft_report = report
        self.dest_call.setText(dest)
        self.lookup_name(dest)
        self.ft_parity.setCurrentIndex(1 - their)
        if step is None:
            self.statusBar().showMessage(f"QSO avec {dest} terminé : Noter le QSO")
            self.disarm_ft()
            self._ft_qso_done()
            return True
        values = self.macro_values()
        if "{loc4}" in step and not values["loc4"]:
            self.statusBar().showMessage("Indiquer d'abord le champ Locator de Ma station…")
            self.on_station()
            return True
        # émission automatique : comme Entrée sur le message préparé
        self.arm_ft(fill_macro(step, values))
        self.cw_text.setFocus()
        return True

    def on_mode_hint(self, mode):
        """Lien de la ligne Décodeur : la radio passe dans le mode pour émettre."""
        if not self.civ or self.tx_active:
            return
        self.mode_combo.setCurrentText(mode)
        self.on_mode_selected(mode)

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
        if self._ft_tx():
            self.arm_ft(self.cw_text.text())
            return
        if self._rtty_tx():
            self.send_rtty(self.cw_text.text())
            return
        if self.cw_direct.isChecked():
            # Entrée en direct : ce qui reste (message tout fait, mot à la pioche)
            text = self.cw_text.text()
            sent = self.cw_direct_sent
            rest = text[len(sent):] if text.startswith(sent) else text
            self.cw_text.clear()
            self.cw_direct_sent = ""
            self._send_cw_text(rest)
            return
        self._send_cw_text(self.cw_text.text())
        self.cw_text.clear()

    def on_cw_direct(self, on):
        self.cw_direct_sent = self.cw_text.text() if on else ""
        self.cw_send_button.setEnabled(self.cw_text.isEnabled())
        self.statusBar().showMessage(
            "CW en direct : chaque lettre tapée part aussitôt" if on else "")

    def on_cw_typed(self, text):
        """En direct : envoie les lettres ajoutées depuis la dernière frappe."""
        if not self.cw_direct.isChecked() or self._rtty_tx():
            return
        sent = self.cw_direct_sent
        if not text.startswith(sent):
            # retour en arrière : la lettre est déjà partie, on suit le champ
            self.cw_direct_sent = text
            return
        added = text[len(sent):]
        if self.cw_hand.isChecked():
            # à la pioche, le rythme d'un mot se calcule d'un coup : il part à l'espace
            if " " not in added:
                return
            word = text[len(sent):text.rindex(" ")]       # sent finit par une espace
            self.cw_direct_sent = text[:text.rindex(" ") + 1]
            if word.strip():
                self._send_cw_text(word + " ", direct=True)
            else:
                self.cw_echo_space = True   # espace seule : le mot suivant s'en écarte
            return
        self.cw_direct_sent = text
        self._send_cw_text(added, direct=True)

    def _send_cw_text(self, raw, direct=False):
        """Émet le texte en CW et l'écrit en rouge au rythme de la manipulation."""
        # caractères que la radio sait émettre ; ^ lie deux lettres (^AR)
        text = "".join(c for c in raw.upper()
                       if c.isascii() and (c.isalnum() or c in " /?.-,:'()=+\"@^"))
        if direct:
            text = text.replace("^", "")
            if text.strip() == "" and text:
                text = " "              # espace entre deux mots
        else:
            text = " ".join(text.split())
        if not text or not self.civ:
            return
        if self.cw_hand.isChecked():
            self.civ.send_cw_hand(text, self.cw_speed.value())
        else:
            self.civ.send_cw(text)
        if not direct:
            self.statusBar().showMessage(f"CW : {text}")
        # le message émis s'écrit en rouge au rythme de la manipulation ; un
        # message envoyé pendant le précédent part à sa suite, sur la même
        # ligne en direct
        unit = 1.2 / self.cw_speed.value()
        start = max(time.monotonic() + CW_ECHO_DELAY, self.cw_echo_end)
        live = self.cw_direct.isChecked() and self.cw_echo_timer.isActive()
        if not live:
            self.cw_echo_space = False
        if not text.strip():
            # espace tapée seule : 7 points avec les 3 qui suivent la lettre
            self.cw_echo_space = True
            self.cw_echo_end = start + 4 * unit
        else:
            schedule, length = char_schedule(text.strip(), self.cw_speed.value())
            lead = " " if live and (self.cw_echo_space or text[0] == " ") else ""
            for i, (t, chunk) in enumerate(schedule):
                self.cw_echo.append((start + t, (lead if i == 0 else "") + chunk,
                                     not live and i == 0))
            self.cw_echo_space = text.endswith(" ")
            gap = 7 if self.cw_echo_space or not direct else 3
            self.cw_echo_end = start + length + gap * unit
        self.cw_echo_timer.start()

    def send_rtty(self, text):
        """Message mis en audio RTTY (mark et space du décodeur), émis en mode data."""
        text = " ".join(text.upper().replace("^", "").split())
        decoder = self.audio.decoder
        if not text or not self.civ or decoder is None:
            return
        if self.tx_active:
            self.statusBar().showMessage("RTTY : attendre la fin de l'émission en cours")
            return
        mark, space = decoder.tones()
        samples, schedule, length = afsk(text, RATE, mark, space, decoder.baud)
        try:
            os.makedirs(os.path.dirname(RTTY_TX_FILE), exist_ok=True)
            with wave.open(RTTY_TX_FILE, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(RATE)
                w.writeframes((samples * 32767).astype(np.int16).tobytes())
        except OSError as e:
            self.statusBar().showMessage(f"RTTY impossible : {e}")
            return
        self.cw_text.clear()
        self.tx_once = RTTY_TX_FILE
        self.tx_digital = True
        self.ptt_button.setChecked(True)
        if not self.tx_active:
            return
        self.statusBar().showMessage(
            f"RTTY : {text}  (mark {mark:.0f} Hz, space {space:.0f} Hz, {length:.0f} s)")
        # le son part 400 ms après le PTT (start_tx)
        start = time.monotonic() + 0.4 + CW_ECHO_DELAY
        for i, (t, char) in enumerate(schedule):
            self.cw_echo.append((start + t, char, i == 0))
        self.cw_echo_end = start + length
        self.cw_echo_timer.start()

    def show_cw_echo(self):
        """Écrit les lettres émises arrivées à échéance, en rouge sur leur ligne."""
        now = time.monotonic()
        cursor = self.decoder_text.textCursor()
        cursor.movePosition(QtGui.QTextCursor.End)
        sent = QtGui.QTextCharFormat()
        sent.setForeground(QtGui.QColor(TX_TEXT_COLOR))
        while self.cw_echo and self.cw_echo[0][0] <= now:
            _, chunk, first = self.cw_echo.pop(0)
            if first and not cursor.atBlockStart():
                cursor.insertText("\n", QtGui.QTextCharFormat())
            cursor.insertText(chunk, sent)
        idle = CW_DIRECT_IDLE if self.cw_direct.isChecked() else 0.0
        if not self.cw_echo and now >= self.cw_echo_end + idle:
            self._end_cw_echo(cursor)
        self.decoder_text.setTextCursor(cursor)
        self.decoder_text.ensureCursorVisible()

    def _end_cw_echo(self, cursor):
        """Fin du texte émis : le texte décodé reprend sur la ligne suivante."""
        self.cw_echo_timer.stop()
        self.cw_echo.clear()
        if not cursor.atBlockStart():
            cursor.insertText("\n", QtGui.QTextCharFormat())
        held, self.cw_echo_held = self.cw_echo_held.lstrip(), ""
        cursor.insertText(held, QtGui.QTextCharFormat())

    def set_correspondent(self, call, line=""):
        """Indicatif pris dans le texte décodé (double-clic)."""
        if self._ft_tx() and self.ft_prepare(call, line):
            return
        if call == self.callsign.text().strip().upper():
            return                          # le sien
        if call != self.dest_call.text().strip():
            self.dest_name.clear()          # prénom d'un autre QSO
        self.dest_call.setText(call)
        self.statusBar().showMessage(f"Correspondant : {call}")
        self.lookup_name(call)

    def lookup_name(self, call):
        """Prénom du correspondant cherché sur qrz.com, en arrière-plan."""
        user = self.station.get("qrz_user", "")
        password = self.station.get("qrz_password", "")
        if not call or call == self.qrz_asked:
            return
        if not (user and password):
            self.statusBar().showMessage(
                f"Correspondant : {call} — pour son prénom, renseigner le compte "
                f"qrz.com dans Ma station…")
            return
        self.qrz_asked = call
        if self.qrz is None or (self.qrz.user, self.qrz.password) != (user, password):
            self.qrz = Qrz(user, password)
        qrz = self.qrz
        self.statusBar().showMessage(f"Correspondant : {call} — recherche sur qrz.com…")

        def search():
            try:
                self.qrz_found.emit(call, qrz.lookup(call), "")
            except QrzError as e:
                self.qrz_found.emit(call, None, str(e))
            except (OSError, ET.ParseError) as e:
                self.qrz_found.emit(call, None, f"qrz.com injoignable ({e})")
        threading.Thread(target=search, daemon=True).start()

    def on_qrz_found(self, call, info, error):
        if call != self.dest_call.text().strip():
            return                          # un autre correspondant entre-temps
        if error:
            self.qrz_asked = ""             # nouvel essai au prochain clic
            self.statusBar().showMessage(f"qrz.com : {error}")
        elif info is None:
            self.statusBar().showMessage(f"qrz.com : {call} inconnu")
        else:
            name = cw_name(info)
            if name and not self.dest_name.text().strip():
                self.dest_name.setText(name)
            who = " ".join(filter(None, (info.get("fname"), info.get("name"))))
            where = ", ".join(filter(None, (info.get("addr2"), info.get("country"))))
            self.statusBar().showMessage(
                f"qrz.com : {call} — " + " — ".join(filter(None, (who, where))))

    def on_cw_hand(self, on):
        self.station["pioche"] = on
        self.on_callsign()                  # enregistre avec la station

    def _upper(self, field, text):
        position = field.cursorPosition()
        field.setText(text.upper())
        field.setCursorPosition(position)

    def on_callsign(self):
        self.station["callsign"] = self.callsign.text().strip()
        try:
            os.makedirs(os.path.dirname(CALLSIGN_FILE), exist_ok=True)
            with open(CALLSIGN_FILE, "w") as f:
                json.dump(self.station, f)
            os.chmod(CALLSIGN_FILE, 0o600)  # mots de passe et clés dedans
        except OSError:
            pass

    def on_station(self):
        """Prénom, QTH, locator et matériel repris dans les messages CW."""
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Ma station")
        form = QtWidgets.QFormLayout(dialog)
        fields = {}
        for key, label, _ in STATION_FIELDS:
            field = QtWidgets.QLineEdit(self.station.get(key, ""))
            field.textEdited.connect(lambda text, field=field: self._upper(field, text))
            form.addRow(label, field)
            fields[key] = field
        # comptes en ligne : prénom du correspondant, envoi des QSO
        for key, label, hidden, tip in ACCOUNT_FIELDS:
            field = QtWidgets.QLineEdit(self.station.get(key, ""))
            if hidden:
                field.setEchoMode(QtWidgets.QLineEdit.Password)
            field.setToolTip(tip + "\nEnregistré sur ce PC seulement.")
            form.addRow(label, field)
            fields[key] = field
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            for key, field in fields.items():
                self.station[key] = field.text().strip()
            self.on_callsign()

    def on_cw_macro(self, text):
        values = self.macro_values()
        # le prénom du correspondant peut manquer, pas le reste
        missing = {"call": ("ton indicatif", self.callsign),
                   "dest": ("l'indicatif du correspondant", self.dest_call)}
        for key, label, _ in STATION_FIELDS:
            missing[key] = (f"le champ {label} de Ma station…", None)
        missing["loc4"] = ("le champ Locator de Ma station…", None)
        for key, (label, field) in missing.items():
            if "{" + key + "}" in text and not values[key]:
                self.statusBar().showMessage(f"Indiquer d'abord {label}")
                if field:
                    field.setFocus()
                else:
                    self.on_station()
                return
        self.cw_text.setText(" ".join(fill_macro(text, values).split()))
        self.cw_text.setFocus()

    def _build_cw_macros(self):
        """Un bouton par message CW pré-enregistré."""
        enabled = self.cw_text.isEnabled()
        for button in self.cw_macro_buttons:
            if button is not self.cw_ending:
                self.cw_macro_row.removeWidget(button)
                button.deleteLater()
        self.cw_macro_buttons = [b for b in self.cw_macro_buttons if b is self.cw_ending]
        label = self.macro_kind.upper()
        for i, (name, tip, text) in enumerate(self.macro_sets[self.macro_kind]):
            button = QtWidgets.QPushButton(name)
            button.setToolTip(f"{tip} :\n{text}\nMis dans le champ {label}, Entrée pour envoyer")
            button.clicked.connect(lambda _, text=text: self.on_cw_macro(text))
            button.setEnabled(enabled)
            self.cw_macro_row.insertWidget(i, button)
            self.cw_macro_buttons.insert(i, button)

    def on_edit_macros(self):
        kind = self.macro_kind
        dialog = CwMacroDialog(self.macro_sets[kind], self.macro_values, self, kind)
        if dialog.exec_() != QtWidgets.QDialog.Accepted:
            return
        self.macro_sets[kind] = dialog.macros()
        try:
            os.makedirs(os.path.dirname(MACROS_FILES[kind]), exist_ok=True)
            with open(MACROS_FILES[kind], "w") as f:
                json.dump(self.macro_sets[kind], f, ensure_ascii=False, indent=1)
        except OSError as e:
            self.statusBar().showMessage(f"Messages {kind.upper()} non enregistrés : {e}")
        self._build_cw_macros()

    def macro_values(self):
        """Valeurs des champs {…} des messages CW, à cet instant."""
        hour = datetime.datetime.now().hour
        return {**self.station,
                "call": self.callsign.text().strip(),
                "dest": self.dest_call.text().strip(),
                "dname": self.dest_name.text().strip(),
                "gm": "GM" if hour < 12 else "GA" if hour < 18 else "GE",
                "loc4": self.station.get("loc", "")[:4].upper(),
                "rpt": self.ft_report}

    def on_cw_ending(self, index):
        ending = self.cw_ending.itemData(index)
        self.cw_ending.setCurrentIndex(0)
        if not ending:
            return
        words = self.cw_text.text().split()
        # retire l'abréviation de fin déjà présente, la plus longue d'abord
        for old, _ in sorted(CW_ENDINGS, key=lambda e: -len(e[0].split())):
            n = len(old.split())
            if words[-n:] == old.split():
                del words[-n:]
                break
        self.cw_text.setText(" ".join(words + [ending]))
        self.cw_text.setFocus()

    def on_log_qso(self):
        """Fiche du QSO pré-remplie, puis envoi à Wavelog et à qrz.com."""
        hz = self.freq_hz or 0
        mode, submode = carnet.adif_mode(self.mode_combo.currentText(),
                                         self.decoder_combo.currentData())
        # QSO FT8 / FT4 : reports et locator relevés pendant le QSO
        ft = (self.ft_qso if self.ft_qso.get("call") == self.dest_call.text().strip()
              else {})
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Noter le QSO")
        form = QtWidgets.QFormLayout(dialog)
        when = QtWidgets.QDateTimeEdit(QtCore.QDateTime.currentDateTimeUtc())
        when.setTimeSpec(QtCore.Qt.UTC)
        when.setDisplayFormat("dd/MM/yyyy HH:mm:ss 'UTC'")
        fields = {}
        for key, label, value in (
                ("call", "Indicatif", self.dest_call.text().strip()),
                ("name", "Prénom", self.dest_name.text().strip()),
                ("freq", "Fréquence (MHz)", f"{hz / 1e6:.6f}" if hz else ""),
                ("mode", "Mode", mode), ("submode", "Sous-mode", submode),
                ("rst_sent", "Report envoyé", ft.get("sent", carnet.default_report(mode))),
                ("rst_rcvd", "Report reçu", ft.get("rcvd", carnet.default_report(mode))),
                ("gridsquare", "Locator", ft.get("grid", "")),
                ("comment", "Commentaire", "")):
            field = QtWidgets.QLineEdit(value)
            if key != "comment":
                field.textEdited.connect(lambda text, field=field: self._upper(field, text))
            fields[key] = field
            form.addRow(label, field)
            if key == "name":
                form.addRow("Date et heure", when)
        wavelog_ok = all(self.station.get(k) for k in
                         ("wavelog_url", "wavelog_key", "wavelog_station"))
        qrz_ok = bool(self.station.get("qrz_logbook_key"))
        targets = {}
        for key, label, ok in (("wavelog", "Envoyer à Wavelog", wavelog_ok),
                               ("qrz", "Envoyer au carnet qrz.com", qrz_ok)):
            box = QtWidgets.QCheckBox(label)
            box.setChecked(ok)
            box.setEnabled(ok)
            if not ok:
                box.setToolTip("À régler dans Ma station…")
            targets[key] = box
            form.addRow(box)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        fields["call"].setFocus()
        if dialog.exec_() != QtWidgets.QDialog.Accepted:
            return
        values = {key: field.text().strip() for key, field in fields.items()}
        call = values["call"]
        if not call:
            self.statusBar().showMessage("QSO non noté : indicatif manquant")
            return
        utc = when.dateTime().toUTC()
        try:
            band = carnet.adif_band(float(values["freq"]) * 1e6)
        except ValueError:
            band = ""
        qso = {"call": call, "name": values["name"],
               "qso_date": utc.toString("yyyyMMdd"), "time_on": utc.toString("HHmmss"),
               "band": band, "freq": values["freq"], "mode": values["mode"],
               "submode": values["submode"], "rst_sent": values["rst_sent"],
               "rst_rcvd": values["rst_rcvd"], "gridsquare": values["gridsquare"],
               "comment": values["comment"],
               "station_callsign": self.callsign.text().strip(),
               "operator": self.callsign.text().strip(),
               "my_gridsquare": self.station.get("loc", ""),
               "tx_pwr": self.station.get("pwr", "")}
        record = carnet.adif_record(qso)
        try:
            carnet.save_adif(LOG_FILE, record)
        except OSError as e:
            self.statusBar().showMessage(f"Copie locale du QSO impossible : {e}")
        if self.logbook_window:
            self.logbook_window.reload()
        station = dict(self.station)
        sends = [name for name, box in targets.items() if box.isChecked()]
        self.log_results[call] = {name: None for name in sends}
        self._show_log_results(call)

        def send(name):
            try:
                if name == "wavelog":
                    ok, message = carnet.send_wavelog(
                        station["wavelog_url"], station["wavelog_key"],
                        station["wavelog_station"], record)
                else:
                    ok, message = carnet.send_qrz(station["qrz_logbook_key"], record)
            except (OSError, ValueError) as e:
                ok, message = False, f"injoignable ({e})"
            self.log_sent.emit(call, name, ok, message)
        for name in sends:
            threading.Thread(target=send, args=(name,), daemon=True).start()

    def on_logbook(self):
        if self.logbook_window is None:
            self.logbook_window = LogbookWindow(LOG_FILE, self)
        self.logbook_window.reload()
        bring_to_front(self.logbook_window)

    def on_log_sent(self, call, service, ok, message):
        self.log_results.setdefault(call, {})[service] = (ok, message)
        self._show_log_results(call)

    def _show_log_results(self, call):
        names = {"wavelog": "Wavelog", "qrz": "qrz.com"}
        parts = ["copie locale ✓"]
        for service, result in self.log_results.get(call, {}).items():
            if result is None:
                parts.append(f"{names[service]} …")
            elif result[0]:
                parts.append(f"{names[service]} ✓")
            else:
                parts.append(f"{names[service]} ✗ {result[1]}")
        self.statusBar().showMessage(f"QSO {call} : " + ", ".join(parts))

    def on_qrz_lookup(self):
        call = self.dest_call.text().strip()
        if not call:
            self.statusBar().showMessage("Indiquer d'abord l'indicatif du correspondant")
            self.dest_call.setFocus()
            return
        QtGui.QDesktopServices.openUrl(
            QtCore.QUrl(f"https://www.qrz.com/db/{QtCore.QUrl.toPercentEncoding(call).data().decode()}"))

    def on_cw_stop(self):
        if self.ft_armed:
            self.disarm_ft("FT : émission annulée")
        if self.tx_once and self.tx_active:
            self.stop_tx("Émission arrêtée")
        elif self.civ:
            self.civ.stop_cw()
        if self.cw_echo_timer.isActive():
            self.cw_echo_end = 0.0
            cursor = self.decoder_text.textCursor()
            cursor.movePosition(QtGui.QTextCursor.End)
            self._end_cw_echo(cursor)
            self.decoder_text.setTextCursor(cursor)

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
        # bargraph Entrée TX : le micro, ou ce qui part vers la radio
        if self.tx_in_reader:
            self.tx_in_reader.stop()
        self.tx_in_meter.setValue(self.tx_in_meter.minimum)
        self.tx_in_value.setText("")
        source = self.radio_sink + ".monitor" if is_file else self.tx_source.currentData()
        # le Niveau PC s'ajoute au micro ; au retour de la sortie, seulement
        # s'il est réglé dans la carte son (sinon le retour l'a déjà)
        self.tx_in_gain = not is_file or sink_hw_volume(self.radio_sink)
        self.tx_in_reader = InputLevel(source) if source else None
        if self.tx_in_reader:
            self.tx_in_reader.level.connect(self.on_tx_in_level)
            self.tx_in_reader.start()

    def on_tx_in_level(self, db):
        # ce qui arrive à la radio : après le Niveau PC, volume de PipeWire en
        # échelle cubique (50 % = -18 dB)
        percent = self.tx_level.value()
        if self.tx_in_gain:
            db += 60 * math.log10(percent / 100) if percent else -200
        self.tx_in_meter.setValue(db)
        self.tx_in_value.setText(f"{max(db, -99):5.1f}")    # silence : pas de -200

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
        source = "file" if self.tx_once else self.tx_source.currentData()
        self.tx_playing = self.tx_once or self.tx_file
        problem = None
        if not self.civ:
            problem = "Émission impossible sans CAT"
        elif source == "file" and not self.tx_playing:
            problem = "Choisis d'abord un fichier à émettre"
        if problem:
            self.statusBar().showMessage(problem)
            self._set_ptt_button(False)
            self.tx_once = None
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
                with wave.open(self.tx_playing) as w:
                    length = w.getnframes() / w.getframerate()
                self.tx_limit = max(TX_TIMEOUT, length + 15)
            except (OSError, wave.Error, EOFError):
                pass
        self.tx_active = True
        self._mute_listen_for_tx(True)
        self.tx_started = time.monotonic()
        self.tx_timer.start()
        self._set_ptt_button(True)

    def tune_with_mode(self, hz, mode):
        """Règle la radio sur hz dans ce mode ; False si c'est impossible."""
        if not self.civ or self.tx_active:
            return False
        if mode in MODE_NAMES and mode != self.mode_combo.currentText():
            self.mode_combo.setCurrentText(mode)
            self.civ.set_mode(mode)
        self.tune_to(hz)
        return True

    def _build_presets(self):
        combo = self.preset_combo
        combo.clear()
        combo.addItem("Presets…", None)
        for i, preset in enumerate(self.presets):
            combo.addItem(preset["name"], i)
            combo.setItemData(combo.count() - 1, self._preset_text(preset),
                              QtCore.Qt.ToolTipRole)
        combo.insertSeparator(combo.count())
        combo.addItem("Enregistrer le réglage actuel…", "save")
        combo.addItem("Supprimer un preset…", "delete")

    def _preset_text(self, preset):
        parts = [preset.get("mode", "")]
        if preset.get("filter"):
            parts.append(f"FIL{preset['filter']}")
        hz = preset.get("width") is not None and width_hz(
            base_mode(preset.get("mode", "")), preset["width"])
        if hz:
            parts.append(width_text(hz))
        if preset.get("power") is not None:
            parts.append(f"{preset['power']} W")
        if preset.get("usb_mod") is not None:
            parts.append(f"USB MOD {preset['usb_mod']} %")
        if "decoder" in preset:
            index = self.decoder_combo.findData(preset["decoder"]) if preset["decoder"] else 0
            parts.append(f"décodeur {self.decoder_combo.itemText(max(index, 0))}")
        return " · ".join(p for p in parts if p)

    def _save_presets(self):
        try:
            os.makedirs(os.path.dirname(PRESETS_FILE), exist_ok=True)
            with open(PRESETS_FILE, "w") as f:
                json.dump(self.presets, f, ensure_ascii=False, indent=1)
        except OSError as e:
            self.statusBar().showMessage(f"Presets non enregistrés : {e}")
        self._build_presets()

    def on_preset(self, index):
        data = self.preset_combo.itemData(index)
        self.preset_combo.setCurrentIndex(0)
        if data == "save":
            self.save_preset()
        elif data == "delete":
            self.delete_preset()
        elif data is not None:
            self.apply_preset(self.presets[data])

    def apply_preset(self, preset):
        """Règle la radio et le décodeur ; un réglage absent du preset ne change pas."""
        if self.tx_active:
            self.statusBar().showMessage("Preset : attendre la fin de l'émission")
            return
        mode = preset.get("mode")
        if mode not in MODE_NAMES:
            mode = self.mode_combo.currentText()
        filt = preset.get("filter")
        self.mode_combo.setCurrentText(mode)
        if filt in (1, 2, 3):
            self.on_filter(filt)
        else:
            filt = None
        if self.civ:
            self.civ.set_mode(mode, filt)   # le filtre part avec le mode
        width = preset.get("width")
        if width is not None and base_mode(mode) != "FM":
            self.width_slider.blockSignals(True)
            self.width_slider.setMaximum(49 if base_mode(mode) == "AM" else 40)
            self.width_slider.setValue(width)
            self.width_slider.blockSignals(False)
            self.on_width_slider(width)
        for key, slider, slot in (("power", self.power_slider, self.on_power_slider),
                                  ("usb_mod", self.alc_slider, self.on_alc_slider)):
            if preset.get(key) is not None:
                slider.blockSignals(True)
                slider.setValue(preset[key])
                slider.blockSignals(False)
                slot(slider.value())
        if "decoder" in preset:
            index = self.decoder_combo.findData(preset["decoder"]) if preset["decoder"] else 0
            self.decoder_combo.setCurrentIndex(max(index, 0))
        self._update_tx_text()
        self._filter_tooltips()
        self._update_markers()
        self.statusBar().showMessage(f"Preset {preset['name']} : {self._preset_text(preset)}")

    def save_preset(self):
        name, ok = QtWidgets.QInputDialog.getText(
            self, "Enregistrer un preset",
            "Nom du preset (un nom déjà pris est remplacé) :")
        name = name.strip()
        if not ok or not name:
            return
        preset = {"name": name, "mode": self.mode_combo.currentText()}
        if self.filter_num:
            preset["filter"] = self.filter_num
        if self.width_index is not None:
            preset["width"] = self.width_index
        # puissance et USB MOD seulement si la radio les a donnés
        if self.power_slider.isEnabled():
            preset["power"] = self.power_slider.value()
        if self.alc_slider.isEnabled():
            preset["usb_mod"] = self.alc_slider.value()
        preset["decoder"] = self.decoder_combo.currentData()
        for i, old in enumerate(self.presets):
            if old["name"] == name:
                self.presets[i] = preset
                break
        else:
            self.presets.append(preset)
        self._save_presets()
        self.statusBar().showMessage(f"Preset {name} enregistré : {self._preset_text(preset)}")

    def delete_preset(self):
        names = [p["name"] for p in self.presets]
        if not names:
            self.statusBar().showMessage("Aucun preset à supprimer")
            return
        name, ok = QtWidgets.QInputDialog.getItem(
            self, "Supprimer un preset", "Preset à supprimer :", names, 0, False)
        if ok:
            self.presets = [p for p in self.presets if p["name"] != name]
            self._save_presets()
            self.statusBar().showMessage(f"Preset {name} supprimé")

    def on_memory_channel(self, channel, content):
        """Un canal de la radio vient d'être lu (au démarrage ou après écriture)."""
        # réponses brutes gardées pour le diagnostic du format des mémoires
        try:
            with open(MEMORY_DUMP_FILE, "w" if channel == 1 else "a") as f:
                f.write(f"{channel:2d} {len(content) if content else '-':>3} "
                        f"{content.hex(' ') if content else 'pas de réponse'}\n")
        except OSError:
            pass
        entry = radio_memory(content)
        self.radio_memory_odd.discard(channel)
        if entry:
            self.radio_memories[channel] = entry
        else:
            self.radio_memories.pop(channel, None)
            if content is None or content != b"\xff":
                self.radio_memory_odd.add(channel)  # format inattendu : signalé
        odd = len(self.radio_memory_odd)
        if channel == 99 or self.radio_memories_ready:
            self.radio_memories_ready = True
            self.radio_memory_status = (
                f"{len(self.radio_memories)} canaux lus dans la radio"
                + (f", {odd} illisibles" if odd else ""))
        else:
            self.radio_memory_status = f"Lecture des mémoires de la radio… canal {channel}/99"
        if self.memory_window:
            self.memory_window.show_radio()

    def open_memories(self):
        if self.memory_window is None:
            self.memory_window = MemoryWindow(self, MODE_NAMES, parse_frequency)
            if self.civ:
                self.civ.memory_written.connect(self.memory_window.on_memory_written)
        bring_to_front(self.memory_window)

    def open_text_window(self):
        if self.text_window is None:
            self.text_window = DecodedTextWindow(self.decoder_text.document(),
                                                 self.decoder_combo, self)
        bring_to_front(self.text_window)

    def open_sstv(self):
        if self.sstv_window is None:
            self.sstv_window = SstvWindow(self)
        bring_to_front(self.sstv_window)

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
                ["paplay", f"--device={self.radio_sink}", self.tx_playing])

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
        self.tx_once = None
        if not self.radio_tx:
            self._mute_listen_for_tx(False)
        self.tx_timer.stop()
        self._set_ptt_button(False)
        self.tx_meter_label.setText("")
        for meter, value in ((self.po_meter, self.po_value),
                             (self.swr_meter, self.swr_value),
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
            "background: #c62828; color: white; font-weight: bold;" if on else
            "border: 2px solid #c62828; border-radius: 3px; font-weight: bold;")

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
        self.on_mute()

    def on_tx_meters(self, po, swr, alc):
        self.swr_meter.setValue(swr)
        self.swr_value.setText(f"{piecewise(swr, SWR_POINTS):.1f}")
        percent = alc * 100 / ALC_ZONE
        self.alc_meter.setValue(percent)
        self.alc_value.setText(f"{percent:.0f}")
        watts = piecewise(po, PO_POINTS)    # % de 100 W, donc des W
        self.tx_meter_label.setText(f"Po {watts:3.0f} %")
        self.po_meter.setValue(watts)
        self.po_value.setText(f"{watts:3.0f} W")

    def on_drain(self, raw):
        amps = piecewise(raw, ID_POINTS)
        self.id_meter.setValue(amps)
        self.id_value.setText(f"{amps:.1f} A")

    def on_voltage(self, raw):
        volts = piecewise(raw, VD_POINTS)
        self.vd_meter.setValue(volts)
        self.vd_value.setText(f"{volts:.1f} V")

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

    def on_power_button(self):
        on = not self.radio_on      # l'état lu sur la radio, pas celui du bouton
        self.on_powered(self.radio_on)
        if not self.civ:
            return
        if not on:
            answer = QtWidgets.QMessageBox.question(
                self, "Arrêt", "Arrêter la radio ?")
            if answer != QtWidgets.QMessageBox.Yes:
                return
            if self.ptt_button.isChecked():
                self.stop_tx("Émission coupée : arrêt de la radio")
        self.power_button.setText("⏻ …")
        self.civ.set_onoff(on)

    def on_powered(self, on):
        self.radio_on = on
        if on and self.civ and not self._memories_requested:
            # mémoires de la radio lues une fois, dès qu'elle répond
            self._memories_requested = True
            self.civ.read_memories()
        self.power_button.setChecked(on)
        self.power_button.setText("⏻ ON" if on else "⏻ OFF")
        self.power_button.setStyleSheet(
            "background: #2e7d32; color: white; font-weight: bold;" if on else "")

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

    def on_pc_output(self, *_):
        self._pactl_stream("move-sink-input", self.pc_output.currentData())

    def on_mute(self, *_):
        muted = self.mute_check.isChecked() or self.tx_muted_listen or self.sql_closed
        self._pactl_stream("set-sink-input-mute", "1" if muted else "0")

    def on_squelch(self, open_):
        """La sortie USB de la radio ignore le squelch : l'appli coupe
        l'écoute PC elle-même (les décodeurs reçoivent toujours le son)."""
        self.sql_closed = not open_
        self.on_mute()

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
        label.setText(level_text(sub, slider.value()))

    def on_level_slider(self, sub, level):
        self.level_sliders[sub][1].setText(level_text(sub, level))
        if self.civ:
            self.civ.set_level(sub, level)

    def on_switch(self, code, value):
        """État d'un filtre lu sur la radio."""
        if code in (SW_AN, SW_MN):
            self.notch_state[code] = value
            self.notch_combo.setEnabled(True)
            mode = SW_AN if self.notch_state[SW_AN] else SW_MN if self.notch_state[SW_MN] else 0
            self.notch_combo.setCurrentIndex(self.notch_combo.findData(mode))
        elif code == SW_PREAMP:
            self.preamp_combo.setEnabled(True)
            index = self.preamp_combo.findData(value)
            if index >= 0:
                self.preamp_combo.setCurrentIndex(index)
        elif code in self.switch_buttons:
            button = self.switch_buttons[code]
            button.setEnabled(True)
            button.setChecked(value != 0)

    def on_switch_button(self, code, value):
        if self.civ:
            # l'atténuateur se règle en dB (20), les autres en 0 / 1 / 2
            self.civ.set_switch(code, (0x20 if value else 0) if code == SW_ATT else int(value))

    def on_notch(self, index):
        """Notch auto ou manuel : l'un coupe l'autre."""
        mode = self.notch_combo.itemData(index)
        if self.civ:
            for code in (SW_AN, SW_MN):
                if code != mode:
                    self.civ.set_switch(code, 0)
            if mode:
                self.civ.set_switch(mode, 1)

    def on_compressor(self, on):
        self.comp_button.setEnabled(True)
        self.comp_button.setChecked(on)

    def on_comp_button(self, on):
        if self.civ:
            self.civ.set_compressor(on)

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
            self.cursor_text.setText("")
        else:
            self.cursor_text.setText(format_frequency(round(x * 1000))
                                     if self.view == "rf" else f"{x:.0f} Hz")
        self.cursor_text.setVisible(x is not None)

    def _on_split(self, pos):
        """Vrai si la souris est sur la limite entre spectre et waterfall."""
        spec = self.spec_plot.sceneBoundingRect()
        wf = self.wf_plot.sceneBoundingRect()
        return (spec.left() <= pos.x() <= spec.right()
                and spec.bottom() - 4 <= pos.y() <= wf.top() + 4)

    def _resize_split(self, pos):
        spec = self.spec_plot.sceneBoundingRect()
        wf = self.wf_plot.sceneBoundingRect()
        height = min(max(pos.y() - spec.top(), 60), wf.bottom() - spec.top() - 60)
        self.spec_plot.setMinimumHeight(height)
        self.spec_plot.setMaximumHeight(height)

    def eventFilter(self, obj, event):
        kind = event.type()
        if kind in (QtCore.QEvent.MouseButtonPress, QtCore.QEvent.MouseMove,
                    QtCore.QEvent.MouseButtonRelease):
            pos = self.spec_plot.scene().views()[0].mapToScene(event.pos())
            if (kind == QtCore.QEvent.MouseButtonPress
                    and event.button() == QtCore.Qt.LeftButton
                    and self._on_split(pos)):
                self.split_drag = True
                return True
            if self.split_drag:
                if kind == QtCore.QEvent.MouseMove:
                    self._resize_split(pos)
                else:
                    self.split_drag = False
                return True
            if kind == QtCore.QEvent.MouseMove and not self.drag:
                if self._on_split(pos):
                    self.graphics.viewport().setCursor(QtCore.Qt.SizeVerCursor)
                else:
                    self.graphics.viewport().unsetCursor()
        if kind == QtCore.QEvent.Wheel:
            pos = self.spec_plot.scene().views()[0].mapToScene(event.pos())
            if self._x_at(pos) is not None and self.freq_hz is not None:
                # un cran = un pas ; le pavé tactile envoie des fractions de
                # cran, cumulées (sinon chaque petit geste ferait un pas)
                self.wf_wheel += event.angleDelta().y()
                steps = int(self.wf_wheel / 120)
                if steps:
                    self.wf_wheel -= steps * 120
                    self.on_tuned(steps)
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
                # comme un bouton de VFO : vers la droite, la fréquence monte,
                # au quart de l'échelle de l'écran pour la précision (Maj :
                # encore 10 fois plus fin) ; pas de 10 Hz
                gain = DRAG_GAIN / (10 if event.modifiers() & QtCore.Qt.ShiftModifier else 1)
                hz = start_hz + (dx - 3 * (1 if dx > 0 else -1)) * khz_per_px * 1000 * gain
                self.tune_to(int(round(hz / 10) * 10))
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
        if decoder in FT_DECODERS:
            # FT8 / FT4 : le clic choisit la fréquence audio d'émission
            audio = x if self.view == "audio" else self._audio_offset(x * 1000)
            if audio is not None:
                self.set_ft_tx_hz(audio)
                return
        if self.view == "audio":
            if decoder:
                self._set_decoder_pitch(x)
            return
        # vue RF : en RTTY, un clic dans la bande écoutée choisit le signal
        audio = self._audio_offset(x * 1000)
        if decoder in RTTY_DECODERS and audio is not None:
            self._set_decoder_pitch(audio)
            return
        # fréquence exacte sous la souris (celle affichée), sans le pas d'accord
        self.tune_to(round(x * 1000))

    def _audio_offset(self, rf_hz):
        """Fréquence audio d'un signal RF, s'il tombe dans la bande en BLU."""
        if self.freq_hz is None:
            return None
        mode = base_mode(self.mode_combo.currentText())
        audio = {"USB": rf_hz - self.freq_hz, "LSB": self.freq_hz - rf_hz}.get(mode)
        if audio is not None and 100 <= audio <= 3000:
            return audio
        return None

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
        # le mode de la radio suit le décodeur : RTTY, FT8 et FT4 en mode data
        # (le RTTY garde LSB-D s'il y est déjà), la CW en CW (ou CW-R)
        current = self.mode_combo.currentText()
        wanted = None
        if name in FT_DECODERS and current != "USB-D":
            wanted = "USB-D"
        elif name in RTTY_DECODERS and not current.endswith("-D"):
            wanted = "USB-D"
        elif name == "cw" and current not in ("CW", "CW-R"):
            wanted = "CW"
        if wanted and getattr(self, "civ", None) and not self.tx_active:
            self.mode_combo.setCurrentText(wanted)
            self.on_mode_selected(wanted)
            self.statusBar().showMessage(f"Radio en {wanted} pour le décodeur "
                                         f"{self.decoder_combo.currentText()}")
        decoder = self.audio.decoders.get(name)
        if decoder:
            decoder.auto_pitch = self.decoder_auto.isChecked()
        self.audio.decoder = decoder
        self.decoder_box.setVisible(decoder is not None)
        for button in self.decoder_text_buttons:
            button.setVisible(decoder is not None)
        self.reply_text.setVisible(name in FT_DECODERS)
        self.decoder_auto.setToolTip({
            "cw": "Cherche la note CW la plus forte entre 300 et 1200 Hz. "
                  "Clic sur le spectre audio pour la choisir.",
            "rtty": "Se recale sur la paire de tonalités à ± 60 Hz du clic. "
                    "Cliquer entre les deux traces.",
            "rtty50": "Météo DWD (50 bauds, écart 450 Hz). Se recale sur la "
                      "paire de tonalités à ± 150 Hz du clic. Cliquer entre "
                      "les deux traces.",
        }.get(name, ""))
        self._update_tx_text()
        self.rtty_invert.setVisible(name in RTTY_DECODERS)
        if name in RTTY_DECODERS:
            self.rtty_invert.setChecked(decoder.inverted)
        self.decoder_auto.setVisible(name in ("cw", *RTTY_DECODERS))
        # FT8/FT4 : plusieurs lignes par créneau, il faut de la place
        self.decoder_box.setFixedHeight(150 if name in FT_DECODERS else 70)
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
        if self.decoder_combo.currentData() in RTTY_DECODERS:
            self.audio.decoder.inverted = inverted

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
        if self.cw_echo_timer.isActive():
            self.cw_echo_held += text   # pas au milieu de la ligne émise
            return
        cursor = self.decoder_text.textCursor()
        cursor.movePosition(QtGui.QTextCursor.End)
        my = self.callsign.text().strip().upper()
        if self.decoder_combo.currentData() in FT_DECODERS and my:
            mine = QtGui.QTextCharFormat()
            mine.setBackground(QtGui.QColor(REPLY_BG))
            mine.setForeground(QtGui.QColor(REPLY_FG))
            for line in text.splitlines(keepends=True):
                # indicatif seul, ou entre <> quand il est abrégé (hachage)
                if my in (w.strip("<>") for w in line.upper().split()):
                    cursor.insertText(line.rstrip("\n"), mine)
                    cursor.insertText(line[len(line.rstrip("\n")):], QtGui.QTextCharFormat())
                    self._add_reply(line.rstrip("\n"))
                    words = [w.strip("<>") for w in line.split()]
                    if len(words) > 5:
                        self.statusBar().showMessage(f"Réponse de {words[5]} : "
                                                     + " ".join(words[4:]))
                else:
                    cursor.insertText(line, QtGui.QTextCharFormat())
                self._ft_watch(line, my)
        else:
            cursor.insertText(text, QtGui.QTextCharFormat())   # couleur du texte décodé
        self.decoder_text.setTextCursor(cursor)
        self.decoder_text.ensureCursorVisible()

    def _ft_watch(self, line, my):
        """Émission en cours (CQ ou QSO) : réponse automatique à qui me répond,
        arrêt si la station appelée répond à une autre."""
        armed = self.ft_armed
        m = FT_LINE_RE.match(line.strip())
        if not armed or not m:
            return
        words = [w.strip("<>") for w in m.group(6).split()]
        if len(words) < 2:
            return
        to, sender = words[0], words[1]
        target = armed["message"].split()[0]
        if target == "CQ":
            if to == my:
                # une station répond à mon CQ : le QSO commence tout seul
                self.ft_prepare(sender, line)
                self.ft_qso["from_cq"] = True
        elif sender == target:
            if to == my:
                from_cq = self.ft_qso.get("from_cq")
                self.ft_prepare(sender, line)       # étape suivante, émise toute seule
                if from_cq:
                    self.ft_qso["from_cq"] = True
            elif to != "CQ":
                self.disarm_ft(f"{target} répond à {to} : appel arrêté")

    def _ft_qso_done(self):
        """QSO fini : le CQ qui l'a amené reprend au cycle suivant."""
        if self.ft_qso.get("from_cq") and self.ft_cq and self._ft_tx():
            message, parity = self.ft_cq
            self.ft_parity.setCurrentIndex(parity)
            call = self.ft_qso.get("call", "")
            self.arm_ft(message)
            self.statusBar().showMessage(f"QSO avec {call} terminé (Noter le QSO) : "
                                         f"« {message} » relancé")

    def _add_reply(self, line, color=None):
        """Une ligne au fil de mes QSO (panneau de droite) ; color : texte émis."""
        cursor = self.reply_text.textCursor()
        cursor.movePosition(QtGui.QTextCursor.End)
        if not cursor.atBlockStart():
            cursor.insertText("\n", QtGui.QTextCharFormat())
        fmt = QtGui.QTextCharFormat()
        if color:
            fmt.setForeground(QtGui.QColor(color))
        cursor.insertText(line, fmt)
        self.reply_text.setTextCursor(cursor)
        self.reply_text.ensureCursorVisible()

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
            self.record_button.setStyleSheet("background: #c62828; color: white;")
            self.record_timer.start()
            self.update_record_label()
        else:
            self.audio.stop_recording()
            self.record_timer.stop()
            self.record_button.setText("● Enreg.")
            self.record_button.setStyleSheet("")
            self.record_button.setToolTip(f"Enregistre le son de la radio dans {RECORD_DIR}")
            if self.record_path:
                self.statusBar().showMessage(f"Enregistré : {self.record_path}")

    def update_record_label(self):
        seconds = self.audio.recorded_frames // RATE
        size = self.audio.recorded_frames * 2 / 1e6
        # durée dans le bouton (clic pour arrêter), taille en bulle d'aide
        self.record_button.setText(f"■ {seconds // 60:02d}:{seconds % 60:02d}")
        self.record_button.setToolTip(f"Enregistrement en cours · {size:.1f} Mo\n"
                                      "Clic pour arrêter")

    def open_record_folder(self):
        os.makedirs(RECORD_DIR, exist_ok=True)
        subprocess.Popen(["xdg-open", RECORD_DIR])

    def closeEvent(self, event):
        self._remember_band()
        if self.tx_active:
            self.stop_tx()
        if self.sstv_window:
            self.sstv_window.close()
        if self.text_window:
            self.text_window.close()
        self.audio.stop()
        if self.tx_in_reader:
            self.tx_in_reader.stop()
        if self.civ:
            self.civ.stop()
        # écoute rendue non muette, sinon PipeWire la garde coupée au lancement suivant
        self._pactl_stream("set-sink-input-mute", "0")
        super().closeEvent(event)


def fill_macro(text, values):
    """Remplace les {champs} connus ; un champ inconnu ou une accolade seule
    restent tels quels (message écrit à la main)."""
    return re.sub(r"\{(\w+)\}",
                  lambda m: str(values.get(m.group(1), m.group(0))), text)


class LogbookWindow(QtWidgets.QWidget):
    """Les QSO du carnet local, le plus récent en haut.

    Double-clic sur une ligne : l'indicatif devient le correspondant.
    Modifier et Effacer ne touchent que la copie locale."""

    COLUMNS = (("Date", "qso_date"), ("UTC", "time_on"), ("Indicatif", "call"),
               ("Prénom", "name"), ("Bande", "band"), ("MHz", "freq"),
               ("Mode", "mode"), ("Envoyé", "rst_sent"), ("Reçu", "rst_rcvd"),
               ("Locator", "gridsquare"), ("Commentaire", "comment"))

    def __init__(self, path, parent):
        super().__init__(parent, QtCore.Qt.Window)
        self.setWindowTitle("Carnet de trafic — IC-7300 Scope")
        self.resize(900, 450)
        self.path = path
        layout = QtWidgets.QVBoxLayout(self)
        self.table = QtWidgets.QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels([c[0] for c in self.COLUMNS])
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.cellDoubleClicked.connect(
            lambda row, col: parent.set_correspondent(self.table.item(row, 2).text()))
        self.qsos = []                  # dans l'ordre du fichier
        self.rows = []                  # ligne du tableau → indice dans qsos
        QtWidgets.QShortcut(QtGui.QKeySequence.Delete, self.table, self.delete)
        layout.addWidget(self.table, stretch=1)
        row = QtWidgets.QHBoxLayout()
        self.count = QtWidgets.QLabel()
        row.addWidget(self.count)
        row.addStretch()
        for label, slot in (("Modifier…", self.edit), ("Effacer", self.delete),
                            ("Actualiser", self.reload),
                            ("Ouvrir le fichier", lambda: self._open(self.path)),
                            ("Ouvrir le dossier",
                             lambda: self._open(os.path.dirname(self.path)))):
            button = QtWidgets.QPushButton(label)
            button.clicked.connect(slot)
            row.addWidget(button)
        layout.addLayout(row)

    @staticmethod
    def _open(path):
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(path))

    @staticmethod
    def _show(key, value):
        if key == "qso_date" and len(value) == 8:
            return f"{value[6:]}/{value[4:6]}/{value[:4]}"
        if key == "time_on" and len(value) >= 4:
            return f"{value[:2]}:{value[2:4]}"
        return value

    def reload(self):
        try:
            qsos = carnet.read_adif(self.path)
        except FileNotFoundError:
            qsos = []
        except OSError as e:
            self.count.setText(f"Carnet illisible : {e}")
            return
        self.qsos = qsos
        self.rows = sorted(range(len(qsos)), reverse=True, key=lambda i: (
            qsos[i].get("qso_date", ""), qsos[i].get("time_on", "")))
        self.table.setRowCount(len(qsos))
        for r, qso in enumerate(qsos[i] for i in self.rows):
            for c, (_, key) in enumerate(self.COLUMNS):
                self.table.setItem(r, c, QtWidgets.QTableWidgetItem(
                    self._show(key, qso.get(key, ""))))
        self.table.resizeColumnsToContents()
        self.count.setText(f"{len(qsos)} QSO — {self.path}")

    def _selected(self):
        row = self.table.currentRow()
        if 0 <= row < len(self.rows):
            return self.rows[row]
        self.count.setText("Choisir d'abord un QSO dans la liste")
        return None

    def _save(self):
        try:
            carnet.write_adif(self.path, self.qsos)
        except OSError as e:
            QtWidgets.QMessageBox.warning(self, "Carnet", f"Carnet non enregistré : {e}")
        self.reload()

    def delete(self):
        index = self._selected()
        if index is None:
            return
        qso = self.qsos[index]
        when = f"{self._show('qso_date', qso.get('qso_date', ''))} " \
               f"{self._show('time_on', qso.get('time_on', ''))}"
        if QtWidgets.QMessageBox.question(
                self, "Effacer le QSO",
                f"Effacer le QSO avec {qso.get('call', '?')} du {when} UTC ?\n\n"
                "Seule la copie locale est effacée : Wavelog et qrz.com "
                "se corrigent sur leur site.") != QtWidgets.QMessageBox.Yes:
            return
        del self.qsos[index]
        self._save()

    def edit(self):
        index = self._selected()
        if index is None:
            return
        qso = self.qsos[index]
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle(f"Modifier le QSO — {qso.get('call', '')}")
        form = QtWidgets.QFormLayout(dialog)
        utc = QtCore.QDateTime.fromString(
            qso.get("qso_date", "") + qso.get("time_on", "").ljust(6, "0"),
            "yyyyMMddHHmmss")
        utc.setTimeSpec(QtCore.Qt.UTC)      # l'heure du carnet est déjà en UTC
        when = QtWidgets.QDateTimeEdit(utc)
        when.setTimeSpec(QtCore.Qt.UTC)
        when.setDisplayFormat("dd/MM/yyyy HH:mm:ss 'UTC'")
        form.addRow("Date et heure", when)
        fields = {}
        for label, key in self.COLUMNS:
            if key in ("qso_date", "time_on", "band"):
                continue
            fields[key] = QtWidgets.QLineEdit(qso.get(key, ""))
            form.addRow(label, fields[key])
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec_() != QtWidgets.QDialog.Accepted:
            return
        for key, field in fields.items():
            value = field.text().strip()
            qso[key] = value if key == "comment" else value.upper()
        qso["qso_date"] = when.dateTime().toString("yyyyMMdd")
        qso["time_on"] = when.dateTime().toString("HHmmss")
        try:
            qso["band"] = carnet.adif_band(float(qso["freq"]) * 1e6)
        except ValueError:
            pass
        self._save()


class CwMacroDialog(QtWidgets.QDialog):
    """Messages CW ou RTTY pré-enregistrés : nom du bouton, description, texte."""

    def __init__(self, macros, values, parent, kind="cw"):
        super().__init__(parent)
        self.kind = kind
        self.setWindowTitle(f"Messages {kind.upper()}")
        self.resize(900, 520)
        self.values = values            # fonction : valeurs pour l'aperçu
        self.items = [list(m) for m in macros]
        layout = QtWidgets.QHBoxLayout(self)
        left = QtWidgets.QVBoxLayout()
        self.list = QtWidgets.QListWidget()
        self.list.setDragDropMode(QtWidgets.QAbstractItemView.InternalMove)
        self.list.model().rowsMoved.connect(self._reordered)
        self.list.currentRowChanged.connect(self._show)
        left.addWidget(self.list)
        buttons = QtWidgets.QGridLayout()
        for i, (label, slot) in enumerate((("Ajouter", self._add), ("Supprimer", self._remove),
                                           ("▲ Monter", lambda: self._move(-1)),
                                           ("▼ Descendre", lambda: self._move(1)))):
            button = QtWidgets.QPushButton(label)
            button.clicked.connect(slot)
            buttons.addWidget(button, i // 2, i % 2)
        left.addLayout(buttons)
        layout.addLayout(left, 1)

        right = QtWidgets.QFormLayout()
        self.name = QtWidgets.QLineEdit()
        self.name.setMaxLength(14)
        self.tip = QtWidgets.QLineEdit()
        self.text = QtWidgets.QPlainTextEdit()
        self.text.setStyleSheet("font: 12pt monospace;")
        self.text.setMinimumHeight(120)
        fields = QtWidgets.QGridLayout()
        fields.setSpacing(4)
        for i, (key, label) in enumerate(MACRO_FIELDS):
            button = QtWidgets.QPushButton(label)
            button.setToolTip(f"Insère {{{key}}}")
            button.clicked.connect(lambda _, key=key: self._insert(key))
            fields.addWidget(button, i // 5, i % 5)
        self.preview = QtWidgets.QLabel()
        self.preview.setWordWrap(True)
        self.preview.setStyleSheet(f"font: 12pt monospace; color: {TX_TEXT_COLOR};")
        right.addRow("Bouton", self.name)
        right.addRow("Description", self.tip)
        right.addRow("Texte", self.text)
        right.addRow("Insérer", fields)
        right.addRow("Aperçu", self.preview)
        reset = QtWidgets.QPushButton("Messages d'origine")
        reset.setToolTip("Remet les messages livrés avec l'appli")
        reset.clicked.connect(self._reset)
        box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        box.addButton(reset, QtWidgets.QDialogButtonBox.ResetRole)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        right.addRow(box)
        layout.addLayout(right, 2)
        self.name.textEdited.connect(self._edited)
        self.tip.textEdited.connect(self._edited)
        self.text.textChanged.connect(self._edited)
        self._fill(0)

    def _fill(self, row):
        self.list.blockSignals(True)
        self.list.clear()
        self.list.addItems([m[0] for m in self.items])
        self.list.blockSignals(False)
        self.list.setCurrentRow(min(row, len(self.items) - 1))
        self._show(self.list.currentRow())

    def _show(self, row):
        on = 0 <= row < len(self.items)
        for widget in (self.name, self.tip, self.text):
            widget.blockSignals(True)
            widget.setEnabled(on)
        name, tip, text = self.items[row] if on else ("", "", "")
        self.name.setText(name)
        self.tip.setText(tip)
        self.text.setPlainText(text)
        for widget in (self.name, self.tip, self.text):
            widget.blockSignals(False)
        self._preview()

    def _edited(self):
        row = self.list.currentRow()
        if not 0 <= row < len(self.items):
            return
        cursor = self.text.textCursor().position()
        upper = self.text.toPlainText().upper()
        # majuscules, sauf dans les {champs}
        upper = re.sub(r"\{(\w+)\}", lambda m: m.group(0).lower(), upper)
        if upper != self.text.toPlainText():
            self.text.blockSignals(True)
            self.text.setPlainText(upper)
            c = self.text.textCursor()
            c.setPosition(min(cursor, len(upper)))
            self.text.setTextCursor(c)
            self.text.blockSignals(False)
        self.items[row] = [self.name.text(), self.tip.text(), upper]
        self.list.item(row).setText(self.name.text())
        self._preview()

    def _preview(self):
        text = " ".join(self.text.toPlainText().split())
        self.preview.setText(fill_macro(text, self.values()) if text else "")

    def _insert(self, key):
        self.text.insertPlainText(f"{{{key}}} ")
        self.text.setFocus()

    def _add(self):
        self.items.append(["Nouveau", "", "{dest} DE {call} K"])
        self._fill(len(self.items) - 1)
        self.name.selectAll()
        self.name.setFocus()

    def _remove(self):
        row = self.list.currentRow()
        if 0 <= row < len(self.items):
            del self.items[row]
            self._fill(row)

    def _move(self, step):
        row = self.list.currentRow()
        if 0 <= row < len(self.items) and 0 <= row + step < len(self.items):
            self.items[row], self.items[row + step] = self.items[row + step], self.items[row]
            self._fill(row + step)

    def _reordered(self):
        # glisser-déposer dans la liste : on suit le nouvel ordre des noms
        order = [self.list.item(i).text() for i in range(self.list.count())]
        remaining = list(self.items)
        self.items = []
        for name in order:
            for m in remaining:
                if m[0] == name:
                    self.items.append(m)
                    remaining.remove(m)
                    break

    def _reset(self):
        self.items = [list(m) for m in DEFAULT_MACROS[self.kind]]
        self._fill(0)

    def macros(self):
        """Messages gardés : ceux qui ont un nom et un texte."""
        return [tuple(m) for m in self.items if m[0].strip() and m[2].strip()]


def callsign_at(edit, pos):
    """(indicatif sous le point pos d'une zone de texte ou None, sa ligne)."""
    cursor = edit.cursorForPosition(pos)
    line, i = cursor.block().text().upper(), cursor.positionInBlock()
    start, end = i, i
    while start > 0 and (line[start - 1].isalnum() or line[start - 1] == "/"):
        start -= 1
    while end < len(line) and (line[end].isalnum() or line[end] == "/"):
        end += 1
    word = line[start:end].strip("/")
    return (word if CALLSIGN_RE.fullmatch(word) else None), line


class CallsignClick(QtCore.QObject):
    """Double-clic sur un indicatif d'une zone de texte : callback(indicatif, ligne)."""

    def __init__(self, edit, callback):
        super().__init__(edit)
        self.edit, self.callback = edit, callback
        edit.viewport().installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.MouseButtonDblClick:
            call, line = callsign_at(self.edit, event.pos())
            if call:
                self.callback(call, line)
                return True
        return False


def level_text(sub, level):
    """Réglage 0x14 affiché : compresseur de 0 à 10 et NR de 0 à 15 comme sur la
    radio, le reste en %."""
    if sub == 0x0E:
        return f"{round(level * 10 / 255)}"
    if sub == 0x06:                         # NR : de 0 à 15 comme sur la radio
        return f"{round(level * 15 / 255)}"
    return f"{round(level * 100 / 255)} %"


def add_slider(grid, row, col, name, slider, value):
    """Nom, curseur et valeur dans trois colonnes de la grille du son, le
    texte un peu plus gros."""
    for column, widget in ((col, QtWidgets.QLabel(name)), (col + 1, slider),
                           (col + 2, value)):
        if isinstance(widget, QtWidgets.QLabel):
            font = widget.font()
            font.setPointSizeF(font.pointSizeF() * 1.25)
            widget.setFont(font)
        grid.addWidget(widget, row, column)


def sound_title(text, color):
    """En-tête de ligne de la grille du son (RX, TX)."""
    label = QtWidgets.QLabel(text)
    label.setStyleSheet(f"color: {color}; font-weight: bold; font-size: 13pt;")
    return label


def magnifier_icon(size=22, color="#e0e0e0"):
    """Loupe dessinée : l'emoji 🔎 reste vide avec Qt5 et Noto Color Emoji."""
    pixmap = QtGui.QPixmap(size, size)
    pixmap.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.Antialiasing)
    pen = QtGui.QPen(QtGui.QColor(color), size / 9, cap=QtCore.Qt.RoundCap)
    painter.setPen(pen)
    radius = size * 0.3
    center = QtCore.QPointF(size * 0.42, size * 0.42)
    painter.drawEllipse(center, radius, radius)
    pen.setWidthF(size / 6)
    painter.setPen(pen)
    painter.drawLine(QtCore.QPointF(size * 0.65, size * 0.65), QtCore.QPointF(size * 0.88, size * 0.88))
    painter.end()
    return QtGui.QIcon(pixmap)


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
    # curseurs plus gros : rainure épaisse et poignée large, faciles à attraper
    app.setStyleSheet("""
        QSlider:horizontal { min-height: 28px; }
        QSlider::groove:horizontal { height: 8px; border-radius: 4px;
                                     background: #37474f; }
        QSlider::sub-page:horizontal { border-radius: 4px; background: #4fc3f7; }
        QSlider::handle:horizontal { width: 22px; margin: -8px 0;
                                     border-radius: 11px; background: #eceff1; }
        QSlider::handle:horizontal:hover { background: #ffffff; }
        QSlider::sub-page:horizontal:disabled { background: #546e7a; }
        QSlider::handle:horizontal:disabled { background: #78909c; }
    """)
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
