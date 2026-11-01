#!/usr/bin/env python3
"""Relais du son reçu de l'IC-7300 pour l'appli Android Lite.

Android ne parle pas le protocole de PipeWire : ce service lit le son du PC de
la radio (TCP 4713) avec parec et le renvoie en PCM brut, s16le mono 16 kHz, à
chaque client connecté. parec ne tourne que pendant une écoute. Réception
seule : rien de ce que le client envoie n'atteint la radio. Plusieurs clients
sont possibles, et le serveur CAT reste libre pour l'appli du PC.
"""
import argparse
import ipaddress
import os
import socket
import subprocess
import threading

SOURCE = "alsa_input.usb-Burr-Brown_from_TI_USB_Audio_CODEC-00.iec958-stereo"
RESEAUX = "127.0.0.0/8,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"
BLOC = 1600                     # 50 ms
CLIENTS_MAX = 3


def servir(conn, hote, args, places):
    cmd = ["parec", "--raw", f"--server={args.serveur}", f"--device={args.source}",
           "--format=s16le", "--rate=16000", "--channels=1", "--latency-msec=50",
           "--client-name=ic7300-son-android"]
    parec = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    print(f"écoute : {hote}", flush=True)
    try:
        conn.settimeout(5)      # client parti sans le dire : on n'attend pas
        while bloc := parec.stdout.read(BLOC):
            conn.sendall(bloc)
    except OSError:
        pass
    finally:
        parec.kill()
        parec.wait()
        parec.stdout.close()
        conn.close()
        places.release()
        print(f"fin d'écoute : {hote}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--serveur", default=os.environ.get("IC7300_SON", "192.168.1.37:4713"),
                        help="PipeWire du PC de la radio")
    parser.add_argument("--source", default=SOURCE)
    parser.add_argument("--port", type=int, default=7301)
    args = parser.parse_args()
    reseaux = [ipaddress.ip_network(n) for n in RESEAUX.split(",")]
    places = threading.BoundedSemaphore(CLIENTS_MAX)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", args.port))
    srv.listen(4)
    print(f"en écoute sur le port {args.port}, son de {args.serveur}", flush=True)
    while True:
        conn, (hote, _) = srv.accept()
        if not any(ipaddress.ip_address(hote) in n for n in reseaux) or not places.acquire(blocking=False):
            print(f"refusé : {hote}", flush=True)
            conn.close()
            continue
        threading.Thread(target=servir, args=(conn, hote, args, places), daemon=True).start()


if __name__ == "__main__":
    main()
