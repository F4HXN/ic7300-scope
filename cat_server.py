#!/usr/bin/env python3
"""Serveur CAT : met le port CI-V de l'IC-7300 à disposition sur le réseau.

Tourne sur le PC relié à la radio. L'appli d'un autre PC s'y connecte avec
--remote (ou --port socket://<ce PC>:7300). Un seul client à la fois ; le port
série n'est ouvert que pendant la connexion, l'appli locale peut s'en servir
le reste du temps.

Sécurité : à la déconnexion, même brutale (câble, Wi-Fi, PC planté), la radio
est remise en réception. Une connexion morte est détectée en ~10 s.

Surveillance : le trafic CI-V est recopié sur un socket local
($XDG_RUNTIME_DIR/ic7300-cat-monitor.sock), une ligne JSON par trame, pour la
fenêtre de serveur_gui.py.
"""
import argparse
import glob
import ipaddress
import json
import os
import select
import socket
import sys
import time

import serial

LOCAL_NETWORKS = "127.0.0.0/8,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"
PTT_OFF = bytes([0xFE, 0xFE, 0x94, 0xE0, 0x1C, 0x00, 0x00, 0xFD])
MONITOR_PATH = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"),
                            "ic7300-cat-monitor.sock")


class Monitor:
    """Socket local où les fenêtres de surveillance lisent le trafic.

    Une fenêtre trop lente est déconnectée plutôt que de ralentir la radio.
    """

    def __init__(self, path):
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(path)
        self.sock.listen(4)
        self.sock.setblocking(False)
        self.watchers = []
        self.client = None
        self.pending = {"tx": b"", "rx": b""}

    def accept(self):
        conn, _ = self.sock.accept()
        conn.setblocking(False)
        self.watchers.append(conn)
        self._send(conn, {"ev": "client", "host": self.client})

    def emit(self, **event):
        event["t"] = time.time()
        for conn in list(self.watchers):
            self._send(conn, event)

    def _send(self, conn, event):
        line = json.dumps(event).encode() + b"\n"
        try:
            if conn.send(line) == len(line):
                return
        except OSError:
            pass
        self.watchers.remove(conn)
        conn.close()

    def set_client(self, host):
        self.client = host
        self.pending = {"tx": b"", "rx": b""}
        self.emit(ev="client", host=host)

    def traffic(self, direction, data):
        """direction : "tx" vers la radio, "rx" venant de la radio."""
        if not self.watchers:
            return
        *frames, rest = (self.pending[direction] + data).split(b"\xfd")
        self.pending[direction] = rest[-512:]
        for frame in frames:
            start = frame.find(b"\xfe\xfe")
            if start >= 0:          # le préambule de réveil (FE x 175) est raccourci
                body = frame[start:].lstrip(b"\xfe")
                self.emit(ev="frame", dir=direction,
                          hex=(b"\xfe\xfe" + body + b"\xfd").hex(" "))


monitor = None


def radio_port():
    """Port CI-V de l'IC-7300 branchée, quel que soit son numéro de série."""
    ports = sorted(glob.glob("/dev/serial/by-id/*IC-7300*-if00-port0"))
    return ports[0] if ports else "/dev/ttyUSB0"


def log(message):
    print(time.strftime("%H:%M:%S"), message, flush=True)
    if monitor:
        monitor.emit(ev="log", msg=message)


def keepalive(conn):
    conn.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 4)
    conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 2)
    conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
    conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)


def serve(conn, args):
    try:
        ser = serial.Serial(None, args.baud, timeout=0, exclusive=True)
        ser.port = args.serial or radio_port()   # radio branchée après le démarrage
        ser.dtr = False             # au repos : USB Keying (CW) sur DTR manipulerait la radio
        ser.open()
    except serial.SerialException as e:
        log(f"port série indisponible ({e}) : l'appli tourne-t-elle sur ce PC ?")
        return
    try:
        while True:
            ready, _, _ = select.select([conn, ser, monitor.sock], [], [], 1.0)
            if monitor.sock in ready:
                monitor.accept()
            if conn in ready:
                data = conn.recv(4096)
                if not data:
                    break
                ser.write(data)
                monitor.traffic("tx", data)
            if ser in ready:
                data = ser.read(4096)
                if data:
                    conn.sendall(data)
                    monitor.traffic("rx", data)
    except OSError as e:
        log(f"connexion perdue : {e}")
    finally:
        try:
            ser.write(PTT_OFF)      # jamais d'émission sans client
            ser.flush()
            monitor.traffic("tx", PTT_OFF)
            time.sleep(0.1)
        except (serial.SerialException, OSError):
            pass
        ser.close()


def main():
    global monitor
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--serial", help="port CI-V de la radio (par défaut : "
                        "l'IC-7300 trouvée dans /dev/serial/by-id)")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--listen", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=7300)
    parser.add_argument("--allow", default=LOCAL_NETWORKS,
                        help="réseaux autorisés, séparés par des virgules "
                             "(par défaut : les réseaux locaux privés)")
    args = parser.parse_args()
    allowed = [ipaddress.ip_network(n.strip()) for n in args.allow.split(",")]

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((args.listen, args.port))
    server.listen(1)
    monitor = Monitor(MONITOR_PATH)
    log(f"en écoute sur {args.listen}:{args.port}, réseaux autorisés : {args.allow}")
    while True:
        ready, _, _ = select.select([server, monitor.sock], [], [])
        if monitor.sock in ready:
            monitor.accept()
        if server not in ready:
            continue
        conn, (host, _) = server.accept()
        if not any(ipaddress.ip_address(host) in net for net in allowed):
            log(f"refusé : {host}")
            conn.close()
            continue
        log(f"connecté : {host}")
        monitor.set_client(host)
        keepalive(conn)
        with conn:
            serve(conn, args)
        monitor.set_client(None)
        log(f"déconnecté : {host}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
