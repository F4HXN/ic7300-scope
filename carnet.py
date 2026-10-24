"""Carnet de trafic : un QSO en ADIF, envoyé à Wavelog et au carnet qrz.com,
et gardé dans un fichier ADIF local (rien n'est perdu si un service manque).
"""
import json
import os
import unicodedata
import urllib.parse
import urllib.error
import urllib.request

AGENT = "ic7300-scope"
QRZ_LOGBOOK_URL = "https://logbook.qrz.com/api"

# bandes ADIF (Hz)
ADIF_BANDS = [("160m", 1_800_000, 2_000_000), ("80m", 3_500_000, 4_000_000),
              ("60m", 5_060_000, 5_450_000), ("40m", 7_000_000, 7_300_000),
              ("30m", 10_100_000, 10_150_000), ("20m", 14_000_000, 14_350_000),
              ("17m", 18_068_000, 18_168_000), ("15m", 21_000_000, 21_450_000),
              ("12m", 24_890_000, 24_990_000), ("10m", 28_000_000, 29_700_000),
              ("6m", 50_000_000, 54_000_000), ("4m", 70_000_000, 71_000_000),
              ("2m", 144_000_000, 148_000_000)]


def adif_band(hz):
    for name, low, high in ADIF_BANDS:
        if low <= hz <= high:
            return name
    return ""


def adif_mode(radio_mode, decoder=None):
    """(MODE, SUBMODE) ADIF d'après le mode de la radio et le décodeur."""
    if decoder == "ft8":
        return "FT8", ""
    if decoder == "ft4":
        return "MFSK", "FT4"
    base = radio_mode.split("-")[0]
    if base in ("USB", "LSB"):
        return "SSB", base
    if base in ("CW", "RTTY", "AM", "FM"):
        return base, ""
    return base, ""


def default_report(mode):
    return "599" if mode in ("CW", "RTTY") else "59" if mode in ("SSB", "AM", "FM") else ""


def _ascii(text):
    """L'ADIF compte en octets : pas d'accents (Jérôme → Jerome)."""
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def adif_record(qso):
    """Un QSO (dict de champs ADIF en minuscules) en enregistrement ADIF."""
    out = []
    for key, value in qso.items():
        value = _ascii(str(value)).strip()
        if value:
            out.append(f"<{key.upper()}:{len(value)}>{value}")
    return " ".join(out) + " <EOR>"


HEADER = (f"Carnet IC-7300 Scope <ADIF_VER:5>3.1.4 "
          f"<PROGRAMID:{len(AGENT)}>{AGENT} <EOH>\n")


def save_adif(path, record):
    """Ajoute le QSO au fichier ADIF local (créé avec son en-tête)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    new = not os.path.exists(path)
    with open(path, "a") as f:
        if new:
            f.write(HEADER)
        f.write(record + "\n")


def write_adif(path, qsos):
    """Réécrit tout le carnet (QSO modifié ou effacé) ; l'ancien reste en .bak."""
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(HEADER)
        for qso in qsos:
            f.write(adif_record(qso) + "\n")
    if os.path.exists(path):
        os.replace(path, path + ".bak")
    os.replace(tmp, path)


def read_adif(path):
    """QSO d'un fichier ADIF : liste de dicts (champs en minuscules)."""
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    head = text.upper().find("<EOH>")
    if head >= 0:
        text = text[head + 5:]
    qsos, qso, pos = [], {}, 0
    while True:
        start = text.find("<", pos)
        end = text.find(">", start)
        if start < 0 or end < 0:
            break
        tag = text[start + 1:end].split(":")
        name = tag[0].strip().lower()
        if name == "eor":
            qsos.append(qso)
            qso = {}
            pos = end + 1
            continue
        try:
            size = int(tag[1])
        except (IndexError, ValueError):
            size = 0
        qso[name] = text[end + 1:end + 1 + size]
        pos = end + 1 + size
    return qsos


def _post(url, data, content_type):
    request = urllib.request.Request(url, data=data, headers={
        "User-Agent": AGENT, "Content-Type": content_type})
    try:
        with urllib.request.urlopen(request, timeout=10) as reply:
            return reply.status, reply.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:     # Wavelog répond 4xx avec un message
        return e.code, e.read().decode("utf-8", "replace")


def wavelog_station_id(url, key, station):
    """Numéro du profil de station : donné tel quel, ou retrouvé par son nom."""
    station = str(station).strip()
    if station.isdigit():
        return station
    request = urllib.request.Request(
        f"{url.rstrip('/')}/index.php/api/station_info/{urllib.parse.quote(key)}",
        headers={"User-Agent": AGENT})
    with urllib.request.urlopen(request, timeout=10) as reply:
        profiles = json.loads(reply.read().decode("utf-8", "replace"))
    for profile in profiles:
        if profile.get("station_profile_name", "").strip().lower() == station.lower():
            return profile["station_id"]
    names = ", ".join(p.get("station_profile_name", "?") for p in profiles)
    raise ValueError(f"profil de station « {station} » inconnu (profils : {names})")


def send_wavelog(url, key, station_id, record):
    """(réussi, message) ; API de Wavelog : POST /index.php/api/qso."""
    try:
        station_id = wavelog_station_id(url, key, station_id)
    except ValueError as e:
        return False, str(e)
    body = json.dumps({"key": key, "station_profile_id": station_id,
                       "type": "adif", "string": record}).encode()
    status, text = _post(url.rstrip("/") + "/index.php/api/qso", body,
                         "application/json")
    try:
        reply = json.loads(text)
    except ValueError:
        reply = {}
    if 200 <= status < 300 and reply.get("status", "created") in ("created", "success"):
        return True, "enregistré"
    reason = reply.get("reason") or reply.get("messages") or text[:120] or f"HTTP {status}"
    return False, str(reason)


def send_qrz(key, record):
    """(réussi, message) ; API du carnet qrz.com : ACTION=INSERT."""
    body = urllib.parse.urlencode({"KEY": key, "ACTION": "INSERT",
                                   "ADIF": record}).encode()
    status, text = _post(QRZ_LOGBOOK_URL, body, "application/x-www-form-urlencoded")
    reply = dict(urllib.parse.parse_qsl(text.strip()))
    if reply.get("RESULT") in ("OK", "REPLACE"):
        return True, "enregistré"
    return False, reply.get("REASON") or text[:120] or f"HTTP {status}"
