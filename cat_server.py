#!/usr/bin/env python3
"""Serveur CAT : met le port CI-V de l'IC-7300 à disposition sur le réseau.

Tourne sur le PC relié à la radio. L'appli d'un autre PC s'y connecte avec
--remote (ou --port socket://<ce PC>:7300). Un seul client à la fois ; le port
série n'est ouvert que pendant la connexion, l'appli locale peut s'en servir
le reste du temps.

Sécurité : à la déconnexion, même brutale (câble, Wi-Fi, PC planté), la radio
est remise en réception. Une connexion morte est détectée en ~10 s.
"""
import argparse
import ipaddress
import select
import socket
import sys
import time

import serial

DEFAULT_PORT = ("/dev/serial/by-id/usb-Silicon_Labs_CP2102_USB_to_UART_Bridge_"
                "Controller_IC-7300_03009829-if00-port0")
PTT_OFF = bytes([0xFE, 0xFE, 0x94, 0xE0, 0x1C, 0x00, 0x00, 0xFD])


def log(message):
    print(time.strftime("%H:%M:%S"), message, flush=True)


def keepalive(conn):
    conn.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 4)
    conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 2)
    conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
    conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)


def serve(conn, args):
    try:
        ser = serial.Serial(args.serial, args.baud, timeout=0, exclusive=True)
    except serial.SerialException as e:
        log(f"port série indisponible ({e}) : l'appli tourne-t-elle sur ce PC ?")
        return
    try:
        while True:
            ready, _, _ = select.select([conn, ser], [], [], 1.0)
            if conn in ready:
                data = conn.recv(4096)
                if not data:
                    break
                ser.write(data)
            if ser in ready:
                data = ser.read(4096)
                if data:
                    conn.sendall(data)
    except OSError as e:
        log(f"connexion perdue : {e}")
    finally:
        try:
            ser.write(PTT_OFF)      # jamais d'émission sans client
            ser.flush()
            time.sleep(0.1)
        except (serial.SerialException, OSError):
            pass
        ser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--serial", default=DEFAULT_PORT, help="port CI-V de la radio")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--listen", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=7300)
    parser.add_argument("--allow", default="127.0.0.0/8,192.168.1.0/24",
                        help="réseaux autorisés, séparés par des virgules")
    args = parser.parse_args()
    allowed = [ipaddress.ip_network(n.strip()) for n in args.allow.split(",")]

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((args.listen, args.port))
    server.listen(1)
    log(f"en écoute sur {args.listen}:{args.port}, réseaux autorisés : {args.allow}")
    while True:
        conn, (host, _) = server.accept()
        if not any(ipaddress.ip_address(host) in net for net in allowed):
            log(f"refusé : {host}")
            conn.close()
            continue
        log(f"connecté : {host}")
        keepalive(conn)
        with conn:
            serve(conn, args)
        log(f"déconnecté : {host}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
