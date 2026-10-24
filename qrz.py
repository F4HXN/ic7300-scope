"""Fiche d'un indicatif dans la base XML de qrz.com (compte qrz.com).

Le prénom et la ville sont donnés à tous les comptes ; la fiche complète
demande un abonnement XML, inutile ici.
"""
import threading
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

URL = "https://xmldata.qrz.com/xml/current/"
AGENT = "ic7300-scope"
NS = {"q": "http://xmldata.qrz.com"}


class QrzError(Exception):
    pass


def base_call(call):
    """Indicatif sans /préfixe ni /suffixe : EA8/F5ABC et F5ABC/P → F5ABC."""
    parts = [p for p in call.upper().split("/") if any(c.isdigit() for c in p)
             and any(c.isalpha() for c in p)]
    return max(parts, key=len) if parts else call.upper()


def cw_name(info):
    """Premier prénom en majuscules sans accents, émissible en CW."""
    first = (info.get("nickname") or info.get("fname") or "").split()
    if not first:
        return ""
    plain = unicodedata.normalize("NFKD", first[0]).encode("ascii", "ignore").decode()
    return "".join(c for c in plain.upper() if c.isalnum() or c == "-")


class Qrz:
    def __init__(self, user, password):
        self.user, self.password = user, password
        self.key = None
        self.lock = threading.Lock()     # une session pour tous les fils

    def _get(self, params):
        request = urllib.request.Request(
            URL + "?" + urllib.parse.urlencode(params),
            headers={"User-Agent": AGENT})
        with urllib.request.urlopen(request, timeout=8) as reply:
            return ET.fromstring(reply.read())

    @staticmethod
    def _field(root, path):
        node = root.find(path, NS)
        return node.text.strip() if node is not None and node.text else ""

    def _login(self):
        root = self._get({"username": self.user, "password": self.password,
                          "agent": AGENT})
        self.key = self._field(root, "q:Session/q:Key") or None
        if not self.key:
            raise QrzError(self._field(root, "q:Session/q:Error") or "connexion refusée")

    def lookup(self, call):
        """Champs de la fiche (fname, name, addr2, country…), None si inconnu."""
        with self.lock:
            for attempt in range(2):
                if not self.key:
                    self._login()
                root = self._get({"s": self.key, "callsign": base_call(call)})
                error = self._field(root, "q:Session/q:Error")
                if not error:
                    break
                if error.startswith("Not found"):
                    return None
                if attempt or not ("Session" in error or "session key" in error):
                    raise QrzError(error)
                self.key = None             # session expirée : on se reconnecte
            record = root.find("q:Callsign", NS)
            if record is None:
                return None
            return {child.tag.split("}")[-1]: (child.text or "").strip()
                    for child in record}
