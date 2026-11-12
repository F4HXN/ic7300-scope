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

Commandes : une fenêtre de surveillance peut écrire sur ce socket une ligne
JSON {"id": …, "cmd": "marche" | "arret" | "deconnecter"} ; la réponse
{"ev": "reponse", "id": …, "ok": …, "msg": …} ne part qu'à elle. Le PC
déconnecté est refusé pendant KICK_S secondes (son appli se reconnecte seule).
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

import subprocess

import serial

LOCAL_NETWORKS = "127.0.0.0/8,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"
PTT_OFF = bytes([0xFE, 0xFE, 0x94, 0xE0, 0x1C, 0x00, 0x00, 0xFD])
POWER_ON = bytes([0xFE] * 175 + [0xFE, 0xFE, 0x94, 0xE0, 0x18, 0x01, 0xFD])   # réveil + marche
POWER_OFF = bytes([0xFE, 0xFE, 0x94, 0xE0, 0x18, 0x00, 0xFD])
KICK_S = 600
PROBE_S = 10                    # radio libre : marche / arrêt relu toutes les 10 s
READ_FREQ = bytes([0xFE, 0xFE, 0x94, 0xE0, 0x03, 0xFD])
BANS_PATH = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "ic7300-cat-refus.json")
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
        self.inbox = {}             # fenêtre → début de ligne de commande reçu
        self.client = None
        self.pending = {"tx": b"", "rx": b""}

    def accept(self):
        conn, _ = self.sock.accept()
        conn.setblocking(False)
        self.watchers.append(conn)
        self.inbox[conn] = b""
        self._send(conn, {"ev": "client", "host": self.client})

    def commands(self, ready):
        """Commandes JSON lues sur les fenêtres prêtes : [(fenêtre, dict)]."""
        found = []
        for conn in [c for c in self.watchers if c in ready]:
            try:
                data = conn.recv(4096)
            except OSError:
                data = b""
            if not data:
                self._drop(conn)
                continue
            *lines, self.inbox[conn] = (self.inbox[conn] + data).split(b"\n")
            self.inbox[conn] = self.inbox[conn][-4096:]
            for line in lines:
                try:
                    command = json.loads(line)
                    if isinstance(command, dict):
                        found.append((conn, command))
                except ValueError:
                    pass
        return found

    def reply(self, conn, command, ok, msg):
        if conn in self.watchers:
            self._send(conn, {"ev": "reponse", "id": command.get("id"), "ok": ok,
                              "msg": msg, "t": time.time()})

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
        self._drop(conn)

    def _drop(self, conn):
        if conn in self.watchers:
            self.watchers.remove(conn)
        self.inbox.pop(conn, None)
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
banned = {}                     # hôte → fin du refus (heure murale) après « deconnecter »


def load_bans():
    """Refus en cours, gardés si le service redémarre."""
    try:
        with open(BANS_PATH) as f:
            banned.update({h: t for h, t in json.load(f).items() if t > time.time()})
    except (OSError, ValueError, AttributeError):
        pass


def ban(host):
    banned[host] = time.time() + KICK_S
    try:
        with open(BANS_PATH + ".tmp", "w") as f:
            json.dump({h: t for h, t in banned.items() if t > time.time()}, f)
        os.replace(BANS_PATH + ".tmp", BANS_PATH)
    except OSError as e:
        log(f"refus non enregistré : {e}")
busy_logged = set()             # postes refusés pendant la liaison en cours


def radio_port():
    """Port CI-V de l'IC-7300 branchée, quel que soit son numéro de série."""
    if sys.platform == "win32":
        from serial.tools import list_ports
        ports = [p.device for p in list_ports.comports()
                 if "IC-7300" in (p.description or "").upper()]
        return ports[0] if len(ports) == 1 else ""
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


def open_radio(args):
    ser = serial.Serial(None, args.baud, timeout=0, exclusive=True)
    ser.port = args.serial or radio_port()   # radio branchée après le démarrage
    ser.dtr = False             # au repos : USB Keying (CW) sur DTR manipulerait la radio
    ser.open()
    return ser


def power(ser, on):
    """Marche / arrêt (CI-V 18) ; vrai si la radio répond OK (FB)."""
    for _ in range(3 if on else 1):
        ser.reset_input_buffer()
        ser.write(POWER_ON if on else POWER_OFF)
        ser.flush()
        monitor.traffic("tx", POWER_ON[-7:] if on else POWER_OFF)
        buffer, deadline = b"", time.monotonic() + (1.0 if on else 0.5)
        while time.monotonic() < deadline:
            buffer += ser.read(64)
            # écho de la commande puis réponse de la radio vers le PC (E0 94)
            if b"\xfe\xfe\xe0\x94\xfb\xfd" in buffer:
                monitor.traffic("rx", b"\xfe\xfe\xe0\x94\xfb\xfd")
                return True
            time.sleep(0.02)
        time.sleep(0.5)
    return False


def probe(args):
    """Radio libre : la radio est-elle en marche ? (marche, fréquence en Hz).

    Arrêtée, l'IC-7300 répond encore NG (FA) ; sans réponse, câble ou radio
    hors tension. None si le port est pris (appli IC-7300 Scope ouverte ici)."""
    try:
        ser = open_radio(args)
    except serial.SerialException:
        return None, None
    try:
        ser.reset_input_buffer()
        ser.write(READ_FREQ)
        ser.flush()
        buffer, deadline = b"", time.monotonic() + 0.4
        while time.monotonic() < deadline:
            buffer += ser.read(64)
            for frame in buffer.split(b"\xfd"):
                body = frame.lstrip(b"\xfe")
                if body[:3] == b"\xe0\x94\x03" and len(body) == 8:
                    digits = "".join(f"{b >> 4}{b & 15}" for b in reversed(body[3:8]))
                    return True, int(digits)
                if body[:3] == b"\xe0\x94\xfa":
                    return False, None
            time.sleep(0.02)
        return False, None
    except (serial.SerialException, OSError):
        return None, None
    finally:
        ser.close()


def command_idle(conn, command, args):
    """Commande reçue sans PC distant : le port série est ouvert le temps de l'envoyer."""
    cmd = command.get("cmd")
    if cmd == "deconnecter":
        return monitor.reply(conn, command, False, "aucun PC distant connecté")
    if cmd not in ("marche", "arret"):
        return monitor.reply(conn, command, False, f"commande inconnue : {cmd}")
    try:
        ser = open_radio(args)
    except serial.SerialException as e:
        return monitor.reply(conn, command, False,
                             f"port série indisponible ({e}) : l'appli tourne-t-elle sur ce PC ?")
    try:
        ok = power(ser, cmd == "marche")
    finally:
        ser.close()
    if ok:
        monitor.emit(ev="alimentation", marche=cmd == "marche")
    action = "mise en marche" if cmd == "marche" else "arrêt"
    log(f"{action} demandée par la surveillance : {'OK' if ok else 'pas de réponse'}")
    monitor.reply(conn, command, ok, f"{action} : " + (
        "OK" if ok else "la radio ne répond pas (CI-V USB Baud Rate sur 115200, pas Auto ?)"))


def cut_audio(host):
    """Coupe les liaisons son (PipeWire TCP 4713) ouvertes par ce poste ; nombre coupé.

    Le tunnel son de l'appli distante ne se recrée pas seul : il faudra la relancer."""
    try:
        out = subprocess.run(["pactl", "list", "clients"], capture_output=True,
                             text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return 0
    # pactl n'a pas de kill-client : on détruit l'objet PipeWire du client
    cut = 0
    for block in out.split("\nClient ")[0:]:
        fields = dict(line.strip().split(" = ", 1) for line in block.splitlines() if " = " in line)
        peer = fields.get("pulse.server.peer", "").strip('"')
        ident = fields.get("object.id", "").strip('"')
        if ident.isdigit() and peer.rsplit(":", 1)[0].strip("[]") == host:
            if subprocess.run(["pw-cli", "destroy", ident], capture_output=True,
                              timeout=5).returncode == 0:
                cut += 1
    return cut


def refuse_busy(server, host):
    """Un autre poste se présente pendant une liaison : refusé tout de suite.

    Sans cela, il resterait dans la file du noyau en se croyant connecté, puis
    serait servi (et aussitôt perdu) à la fin de la liaison en cours."""
    try:
        other, (who, _) = server.accept()
    except OSError:
        return
    other.close()
    if who not in busy_logged and banned.get(who, 0) <= time.time():
        busy_logged.add(who)            # son appli réessaie toutes les 2 s
        log(f"refusé : {who}, la radio est pilotée par {host}")


def serve(conn, args, host, server):
    try:
        ser = open_radio(args)
    except serial.SerialException as e:
        log(f"port série indisponible ({e}) : l'appli tourne-t-elle sur ce PC ?")
        return
    try:
        while True:
            ready, _, _ = select.select([conn, ser, server, monitor.sock, *monitor.watchers], [], [], 1.0)
            if monitor.sock in ready:
                monitor.accept()
            if server in ready:
                refuse_busy(server, host)
            kicked = False
            for watcher, command in monitor.commands(ready):
                cmd = command.get("cmd")
                if cmd == "deconnecter":
                    ban(host)
                    sound = cut_audio(host)
                    log(f"déconnexion demandée : {host} refusé pendant {KICK_S // 60} min, "
                        f"{sound} liaison(s) son coupée(s)")
                    monitor.reply(watcher, command, True, f"{host} déconnecté (CAT"
                                  + (f" et {sound} liaison(s) son)" if sound else ")"))
                    kicked = True
                elif cmd in ("marche", "arret"):
                    # le PC distant reçoit aussi la réponse de la radio : sans gravité
                    ser.write(POWER_ON if cmd == "marche" else POWER_OFF)
                    monitor.traffic("tx", POWER_ON[-7:] if cmd == "marche" else POWER_OFF)
                    log(f"{'mise en marche' if cmd == 'marche' else 'arrêt'} envoyé(e), {host} connecté")
                    monitor.reply(watcher, command, True,
                                  ("mise en marche" if cmd == "marche" else "arrêt") + f" envoyé ({host} connecté)")
                else:
                    monitor.reply(watcher, command, False, f"commande inconnue : {cmd}")
            if kicked:
                break
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
    server.listen(8)
    monitor = Monitor(MONITOR_PATH)
    load_bans()
    log(f"en écoute sur {args.listen}:{args.port}, réseaux autorisés : {args.allow}")
    while True:
        ready, _, _ = select.select([server, monitor.sock, *monitor.watchers], [], [], PROBE_S)
        if not ready:
            on, hz = probe(args)
            monitor.emit(ev="alimentation", marche=on, frequence=hz)
            continue
        if monitor.sock in ready:
            monitor.accept()
        for watcher, command in monitor.commands(ready):
            command_idle(watcher, command, args)
        if server not in ready:
            continue
        conn, (host, _) = server.accept()
        if banned.get(host, 0) > time.time():
            conn.close()            # silencieux : son appli réessaie toutes les 2 s
            continue
        if not any(ipaddress.ip_address(host) in net for net in allowed):
            log(f"refusé : {host}")
            conn.close()
            continue
        log(f"connecté : {host}")
        monitor.set_client(host)
        keepalive(conn)
        with conn:
            busy_logged.clear()
            serve(conn, args, host, server)
        monitor.set_client(None)
        log(f"déconnecté : {host}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
