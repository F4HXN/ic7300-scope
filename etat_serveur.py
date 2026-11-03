#!/usr/bin/env python3
"""État du serveur IC-7300 en JSON, pour l'appli Android « IC-7300 État ».

Tourne sur le PC relié à la radio, à côté de cat_server.py. Ne touche jamais
à la radio : lit seulement le socket de surveillance de cat_server.py (comme
la fenêtre serveur_gui.py) et quelques indicateurs du PC.

    GET http://<ce PC>:7301/etat

Fréquence, mode, S-mètre et émission ne sont connus que pendant qu'un PC
distant pilote la radio par cat_server.py : l'appli lancée sur ce PC passe
directement par l'USB, sans copie de son trafic.
"""
import argparse
import ipaddress
import json
import os
import socket
import subprocess
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from cat_server import LOCAL_NETWORKS, MONITOR_PATH, radio_port
from serveur_gui import MODES, SERVICE, bcd_frequency, bcd_level, describe

CAT_TCP_PORT = 7300
PULSE_TCP_PORT = 4713
MAX_COMMANDS = 40


def s_meter(level):
    """Valeur 0–255 du S-mètre de l'IC-7300 → « S5 », « S9+20 »…"""
    if level <= 120:                # S0 à S9 : 120 pas
        return f"S{round(level * 9 / 120)}"
    return f"S9+{round((level - 120) * 60 / 121)}"


class Radio:
    """Ce que le trafic CI-V copié par cat_server.py apprend sur la radio."""

    def __init__(self):
        self.lock = threading.Lock()
        self.connected = False      # socket de surveillance ouvert
        self.client = None          # PC distant connecté à cat_server.py
        self.client_since = None
        self.values = {}            # frequence, mode, smetre, emission, marche…
        self.updated = None
        self.commands = deque(maxlen=MAX_COMMANDS)

    def event(self, event):
        with self.lock:
            if event["ev"] == "client":
                if event["host"] != self.client:
                    self.client_since = time.time() if event["host"] else None
                self.client = event["host"]
                if not self.client:
                    self.values.pop("emission", None)
            elif event["ev"] == "log":
                self.commands.appendleft({"t": event["t"], "sens": "serveur",
                                          "nom": event["msg"], "valeur": ""})
            elif event["ev"] == "frame":
                self.frame(event)

    def frame(self, event):
        frame = bytes.fromhex(event["hex"])
        if len(frame) < 6:
            return
        name, value, automatic = describe(frame)
        to_radio = event["dir"] == "tx"
        cmd, rest = frame[4], frame[5:-1]
        v = self.values
        if not to_radio or cmd in (0x05, 0x06):     # réponses, ou réglages imposés
            if cmd in (0x00, 0x03, 0x05) and len(rest) == 5:
                v["frequence"] = bcd_frequency(rest)
            elif cmd == 0x25 and len(rest) == 6 and rest[0] == 0:
                v["frequence"] = bcd_frequency(rest[1:])
            elif cmd in (0x01, 0x04, 0x06) and rest:
                v["mode"] = MODES.get(rest[0], f"{rest[0]:02X}")
            elif cmd == 0x15 and len(rest) == 3:
                level = bcd_level(rest[1:])
                if rest[0] == 0x02:
                    v["smetre"], v["smetre_brut"] = s_meter(level), level
                elif rest[0] == 0x11:
                    v["puissance"] = level * 100 // 255
                elif rest[0] == 0x12:
                    v["ros_brut"] = level
            elif cmd == 0x1C and len(rest) == 2 and rest[0] == 0:
                v["emission"] = bool(rest[1])
            elif cmd == 0x18 and rest and not to_radio:
                v["marche"] = bool(rest[0])
        if to_radio and cmd == 0x1C and len(rest) == 2 and rest[0] == 0:
            v["emission"] = bool(rest[1])           # PTT demandé (PTT_OFF compris)
        self.updated = event["t"]
        if not automatic:
            self.commands.appendleft({"t": event["t"],
                                      "sens": "→ radio" if to_radio else "← radio",
                                      "nom": name, "valeur": value})

    def snapshot(self):
        with self.lock:
            return {"surveillance": self.connected, "client": self.client,
                    "client_depuis": self.client_since, "radio": dict(self.values),
                    "maj_radio": self.updated, "commandes": list(self.commands)}


def follow_monitor(radio):
    """Lit le socket de surveillance, et s'y reconnecte si cat_server redémarre."""
    while True:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.connect(MONITOR_PATH)
                radio.connected = True
                buffer = b""
                while data := sock.recv(65536):
                    buffer += data
                    *lines, buffer = buffer.split(b"\n")
                    for line in lines:
                        try:
                            radio.event(json.loads(line))
                        except (ValueError, KeyError):
                            pass
        except OSError:
            pass
        radio.connected = False
        with radio.lock:
            radio.client = radio.client_since = None
        time.sleep(3)


def service_active():
    return subprocess.run(["systemctl", "--user", "is-active", "--quiet", SERVICE]
                          ).returncode == 0


def listening():
    """Ports TCP en écoute sur ce PC, lus dans /proc : se connecter au port
    7300 ferait de cette sonde un client de cat_server.py (radio ouverte, PTT
    remis à zéro)."""
    ports = set()
    for name in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(name) as f:
                for line in f.readlines()[1:]:
                    fields = line.split()
                    if fields[3] == "0A":           # LISTEN
                        ports.add(int(fields[1].rsplit(":", 1)[1], 16))
        except OSError:
            pass
    return ports


def local_app():
    """Vrai si ic7300_scope.py tourne sur ce PC sans --remote (il tient l'USB)."""
    out = subprocess.run(["pgrep", "-af", "ic7300_scope.py"],
                         capture_output=True, text=True).stdout
    return any("python" in line and "--remote" not in line for line in out.splitlines())


def status(radio):
    try:
        usb = bool(radio_port())
    except Exception:
        usb = False
    ports = listening()
    return {"hote": socket.gethostname(), "heure": time.time(),
            "service_cat": service_active(), "port_cat": CAT_TCP_PORT in ports,
            "son_reseau": PULSE_TCP_PORT in ports, "radio_usb": usb,
            "appli_locale": local_app(), **radio.snapshot()}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=7301)
    parser.add_argument("--allow", default=LOCAL_NETWORKS,
                        help="réseaux autorisés, séparés par des virgules")
    args = parser.parse_args()
    allowed = [ipaddress.ip_network(n.strip()) for n in args.allow.split(",")]
    radio = Radio()
    threading.Thread(target=follow_monitor, args=(radio,), daemon=True).start()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            host = ipaddress.ip_address(self.client_address[0])
            if not any(host in net for net in allowed):
                self.send_error(403)
                return
            if self.path.split("?")[0] not in ("/", "/etat"):
                self.send_error(404)
                return
            body = json.dumps(status(radio), ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    print(f"état IC-7300 sur le port {args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
