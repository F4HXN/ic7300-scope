"""Décodage FT8 avec jt9, le moteur de WSJT-X.

Le son est découpé en créneaux de 15 s calés sur l'heure UTC. À la fin de
chaque créneau, il est ramené à 12 kHz, écrit en WAV et confié à jt9 dans un
fil à part ; les messages décodés reviennent au prochain appel de feed().
L'horloge du PC doit être à l'heure à moins d'une seconde près.
"""
import datetime
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
import wave

import numpy as np
from scipy.signal import resample_poly

SLOT = 15                       # secondes
JT9_RATE = 12000
DEPTH = 3                       # profondeur de décodage de jt9 (1 à 3)
# 123015 -12  0.3 1200 ~  CQ F4HXN JN18
LINE = re.compile(r"^(\d{6})\s+(-?\d+)\s+(-?[\d.]+)\s+(\d+)\s+~\s+(.*?)\s*$")


def clock_synchronized():
    try:
        out = subprocess.run(["timedatectl", "show", "-p", "NTPSynchronized",
                              "--value"], capture_output=True, text=True,
                             timeout=2).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return {"yes": True, "no": False}.get(out)


class Ft8Decoder:
    pitch = None                    # décode toute la bande, pas une note

    def __init__(self, rate):
        self.rate = rate
        self.auto_pitch = True
        self.available = shutil.which("jt9") is not None
        self.synchronized = clock_synchronized()
        self._dir = tempfile.mkdtemp(prefix="ic7300-ft8-")
        self._buffer = np.zeros(0, np.float32)
        self._buffer_end = None     # heure du dernier échantillon reçu
        self._slot = None           # début du créneau en cours (s depuis 1970)
        self._results = queue.Queue()
        self._running = 0
        self._last_count = None
        self._last_slot = None
        self._lock = threading.Lock()
        # un seul jt9 à la fois : les créneaux sortent dans l'ordre
        self._jobs = queue.Queue()
        threading.Thread(target=self._worker, daemon=True).start()

    @property
    def has_signal(self):
        return bool(self._last_count)

    @property
    def key_down(self):
        return self._running > 0

    def status(self):
        if not self.available:
            return "jt9 introuvable (installer WSJT-X)"
        parts = []
        if self._running:
            parts.append("décodage…")
        if self._last_slot is not None:
            hhmmss = time.strftime("%H:%M:%S", time.gmtime(self._last_slot))
            parts.append(f"{hhmmss} UTC : {self._last_count} message(s)")
        elif self._slot is not None:
            left = self._slot + SLOT - time.time()
            parts.append(f"premier créneau dans {max(left, 0):.0f} s")
        if self.synchronized is False:
            parts.append("horloge non synchronisée !")
        return " · ".join(parts)

    def track_pitch(self, db, bin_hz):
        pass

    def set_pitch(self, hz):
        pass

    def feed(self, samples):
        """Ajoute des échantillons ; renvoie les messages décodés entre-temps."""
        if not self.available:
            return ""
        now = time.time()
        if self._buffer_end is not None and now - self._buffer_end > 1.0:
            # trou dans le son (décodeur changé, coupure) : on repart à zéro
            self._buffer = np.zeros(0, np.float32)
            self._slot = None
        self._buffer = np.concatenate([self._buffer, samples])
        self._buffer_end = now
        slot = now // SLOT * SLOT
        if self._slot is None:
            self._slot = slot       # créneau entamé : on attend le suivant
        elif slot > self._slot:
            self._finish_slot(self._slot)
            self._slot = slot
        # on ne garde que le créneau en cours et un peu avant
        keep = int((now - self._slot + 1) * self.rate)
        if len(self._buffer) > keep:
            self._buffer = self._buffer[-keep:]

        out = []
        while not self._results.empty():
            out.append(self._results.get())
        return "".join(out)

    def _finish_slot(self, start):
        """Extrait [start, start + 15 s) du tampon et lance jt9 dessus."""
        first_time = self._buffer_end - len(self._buffer) / self.rate
        begin = int(round((start - first_time) * self.rate))
        end = begin + SLOT * self.rate
        if begin < 0 or end > len(self._buffer):
            return                  # créneau incomplet (démarrage, coupure)
        audio = self._buffer[begin:end]
        with self._lock:
            self._running += 1
        self._jobs.put((audio, start))

    def _worker(self):
        while True:
            job = self._jobs.get()
            if job is None:
                return
            self._decode(*job)

    def _decode(self, audio, start):
        try:
            name = time.strftime("%y%m%d_%H%M%S", time.gmtime(start))
            path = os.path.join(self._dir, name + ".wav")
            low = resample_poly(audio, 1, self.rate // JT9_RATE)
            pcm = (np.clip(low, -1, 1) * 32767).astype("<i2")
            with wave.open(path, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(JT9_RATE)
                w.writeframes(pcm.tobytes())
            result = subprocess.run(
                ["jt9", "-8", "-d", str(DEPTH), "-p", str(SLOT),
                 "-a", self._dir, "-t", self._dir, path],
                capture_output=True, text=True, timeout=SLOT * 2, cwd=self._dir)
            os.remove(path)
            lines = []
            for line in result.stdout.splitlines():
                m = LINE.match(line)
                if m:
                    hhmmss, snr, dt, freq, message = m.groups()
                    when = f"{hhmmss[:2]}:{hhmmss[2:4]}:{hhmmss[4:]}"
                    lines.append((int(freq), f"{when} {int(snr):+3d} "
                                  f"{float(dt):+4.1f} {int(freq):4d}  {message}\n"))
            self._last_slot = start
            self._last_count = len(lines)
            for _, line in sorted(lines):
                self._results.put(line)
        except (OSError, subprocess.SubprocessError) as e:
            self._results.put(f"[FT8] erreur jt9 : {e}\n")
        finally:
            with self._lock:
                self._running -= 1

    def close(self):
        self._jobs.put(None)
        shutil.rmtree(self._dir, ignore_errors=True)
