"""Décodeur CW : de l'audio reçu au texte.

La note est isolée par démodulation à sa fréquence (blocs de 5 ms), l'enveloppe
est comparée à un seuil qui suit le bruit et le signal, puis la durée des
traits et des silences donne points, traits, lettres et mots. La vitesse
s'adapte toute seule.
"""
from collections import deque

import numpy as np

MORSE = {
    ".-": "A", "-...": "B", "-.-.": "C", "-..": "D", ".": "E", "..-.": "F",
    "--.": "G", "....": "H", "..": "I", ".---": "J", "-.-": "K", ".-..": "L",
    "--": "M", "-.": "N", "---": "O", ".--.": "P", "--.-": "Q", ".-.": "R",
    "...": "S", "-": "T", "..-": "U", "...-": "V", ".--": "W", "-..-": "X",
    "-.--": "Y", "--..": "Z",
    "-----": "0", ".----": "1", "..---": "2", "...--": "3", "....-": "4",
    ".....": "5", "-....": "6", "--...": "7", "---..": "8", "----.": "9",
    ".-.-.-": ".", "--..--": ",", "..--..": "?", "-..-.": "/", "-....-": "-",
    "-.--.": "(", "-.--.-": ")", ".----.": "'", "---...": ":", ".-..-.": '"',
    ".--.-.": "@", "-...-": "=", ".-.-.": "+", "-.-.--": "!",
    "...-.-": "<SK>", ".-...": "<AS>", "-.-.-": "<KA>", "...-.": "<SN>",
    "........": "<HH>",
}

BLOCK_SECONDS = 0.005
MIN_DOT = 0.02                  # 60 mots/min
MAX_DOT = 0.3                   # 4 mots/min
PITCH_RANGE = (300, 1200)       # où chercher la note en mode auto (Hz)
HISTORY_SECONDS = 2.0           # fenêtre pour estimer bruit et signal
SQUELCH_RATIO = 4.0
NOISE_FLOOR = 1e-5              # pour un silence numérique parfait (-100 dBFS)   # signal / bruit minimal pour décoder


class CwDecoder:
    def __init__(self, rate):
        self.rate = rate
        self.block = round(rate * BLOCK_SECONDS)
        self.pitch = 600.0
        self.auto_pitch = True
        self.dot = 0.06                 # 20 mots/min au départ
        self._pending = np.zeros(0, np.float32)
        self._spectrum = None
        self._prev_env = 0.0
        self._history = np.zeros(round(HISTORY_SECONDS / BLOCK_SECONDS))
        self._filled = 0
        self._noise = 0.0
        self._signal = 0.0
        self._key = False
        self._candidate = False
        self._candidate_time = 0.0
        self._t = 0.0
        self._last_edge = 0.0
        self._code = ""
        self._word_done = True
        self._marks = deque(maxlen=24)
        self._last_signal = 0.0

    @property
    def wpm(self):
        return 1.2 / self.dot

    @property
    def has_signal(self):
        return self._signal > SQUELCH_RATIO * self._noise

    @property
    def key_down(self):
        return self._key

    def status(self):
        state = f"{self.wpm:4.1f} mots/min" if self.has_signal else "pas de signal"
        return f"note {self.pitch:4.0f} Hz · {state}"

    def set_pitch(self, hz):
        self.pitch = hz

    def track_pitch(self, db, bin_hz):
        """Mode auto : la note est le pic le plus fort du spectre moyenné."""
        if not self.auto_pitch:
            return
        self._spectrum = db if self._spectrum is None else (
            0.9 * self._spectrum + 0.1 * db)
        lo, hi = (int(f / bin_hz) for f in PITCH_RANGE)
        part = self._spectrum[lo:hi]
        k = int(np.argmax(part))
        if part[k] < np.median(part) + 12:
            return                      # rien qui ressorte du bruit
        if 0 < k < len(part) - 1:       # interpolation parabolique du pic
            a, b, c = part[k - 1], part[k], part[k + 1]
            k += 0.5 * (a - c) / (a - 2 * b + c)
        self.pitch = (lo + k) * bin_hz

    def feed(self, samples):
        """Ajoute des échantillons et renvoie le texte décodé entre-temps."""
        buf = np.concatenate([self._pending, samples])
        count = len(buf) // self.block
        self._pending = buf[count * self.block:]
        if not count:
            return ""
        seg = buf[:count * self.block]
        osc = np.exp(-2j * np.pi * self.pitch * np.arange(len(seg)) / self.rate)
        env = np.abs((seg * osc).reshape(count, self.block).mean(axis=1))
        return "".join(self._step(float(e)) for e in env)

    def _step(self, e):
        e, self._prev_env = 0.5 * (e + self._prev_env), e
        self._t += BLOCK_SECONDS
        # le CW est silencieux au moins la moitié du temps : le bas de
        # l'enveloppe récente donne le bruit, le haut donne le signal
        if e > NOISE_FLOOR:             # un silence numérique ne dit rien du bruit
            self._history[self._filled % len(self._history)] = e
            self._filled += 1
            if self._filled % 10 == 0:
                recent = self._history[:min(self._filled, len(self._history))]
                self._noise, self._signal = np.percentile(recent, (20, 95))
                self._noise = max(self._noise, NOISE_FLOOR)
        span = self._signal - self._noise
        threshold = self._noise + (0.4 if self._key else 0.55) * span
        wanted = self.has_signal and e > threshold
        if self.has_signal:
            self._last_signal = self._t
        elif self._marks and self._t - self._last_signal > 0.5:
            self._marks.clear()         # nouvelle station : vitesse à réapprendre

        out = ""
        # un changement d'état doit tenir 2 blocs (10 ms) pour compter
        if wanted != self._key:
            if wanted != self._candidate:
                self._candidate = wanted
                self._candidate_time = self._t - BLOCK_SECONDS
            elif self._t - self._candidate_time >= 2 * BLOCK_SECONDS:
                duration = self._candidate_time - self._last_edge
                if self._key:
                    self._mark(duration)
                self._last_edge = self._candidate_time
                self._key = wanted
        else:
            self._candidate = self._key

        if not self._key:
            gap = self._t - self._last_edge
            if self._code and gap > 2 * self.dot:
                out += MORSE.get(self._code, "*")
                self._code = ""
            if not self._word_done and gap > 5 * self.dot:
                out += " "
                self._word_done = True
        return out

    def _mark(self, duration):
        self._marks.append(duration)
        self._update_speed()
        self._code += "." if duration < 2 * self.dot else "-"
        self._word_done = False
        if len(self._code) > 8:         # ce n'est pas du morse : on repart
            self._code = ""

    def _update_speed(self):
        """Sépare les dernières durées en points et traits (2 groupes)."""
        d = np.array(self._marks)
        if len(d) < 4 or d.max() < 2 * d.min():
            return                      # que des points ou que des traits
        split = (d.min() + d.max()) / 2
        for _ in range(5):
            short, long = d[d < split].mean(), d[d >= split].mean()
            split = (short + long) / 2
        self.dot = min(max((short + long / 3) / 2, MIN_DOT), MAX_DOT)
