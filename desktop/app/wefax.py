"""Fax météo (WEFAX) : réception des cartes en ondes courtes.

Le standard des stations météo (DWD Pinneberg, Northwood…) : 120 lignes par
minute, IOC 576 (1809 points par ligne), modulation de fréquence 1500 Hz
noir, 2300 Hz blanc. Une carte commence par le signal de départ (noir et
blanc alternés à 300 Hz pendant 5 s), puis une trentaine de secondes de
phasage (une impulsion par ligne qui marque le bord gauche), et finit par le
signal d'arrêt (alternance à 450 Hz).

Le décodeur démodule la fréquence, attend le signal de départ, cale le bord
gauche et la pente (petit écart d'horloge avec l'émetteur) sur les lignes de
phasage, puis trace la carte ligne par ligne jusqu'au signal d'arrêt.
"""
import threading
from types import SimpleNamespace

import numpy as np

from sstv import lowpass

BLACK_HZ, WHITE_HZ = 1500.0, 2300.0
CENTER_HZ = (BLACK_HZ + WHITE_HZ) / 2
WORK_RATE = 12000               # fréquence de travail (Hz)
LPM = 120
IOC = 576
WIDTH = round(np.pi * IOC)      # 1809 points par ligne
LINE = WORK_RATE * 60 // LPM    # 6000 échantillons par ligne
MAX_LINES = 1600                # au-delà, on clôt la carte (13 min)
PHASING_LINES = 50              # lignes regardées pour caler le bord gauche
START_HZ, STOP_HZ = 300.0, 450.0
TONE_DB = 13                    # tonalité de départ/arrêt au-dessus du reste
MODE = SimpleNamespace(name="Fax meteo")


class WefaxDecoder:
    """Décode en continu ; la carte en cours est dans `image` (RVB uint8)."""

    def __init__(self, rate):
        self.rate = rate
        self._decim = rate // WORK_RATE
        self._fir = lowpass(1000, rate, 255)
        self._fir_state = np.zeros(len(self._fir) - 1, complex)
        self._phase = 0.0
        self._decim_count = 0
        self._prev = None
        self._lock = threading.Lock()
        self.inverted = False           # bande latérale inférieure
        self.finished = []              # (mode, image) terminées, à enregistrer
        self.reset()

    def reset(self):
        with self._lock:
            self._values = np.zeros(0, np.float32)  # 0 noir … 1 blanc
            self._checked = 0           # tonalités cherchées jusqu'ici
            self._tone_count = 0
            self._stop_count = 0
            self.mode = None
            self.image = None
            self._rows = []
            self._start = 0             # échantillon du début de la 1re ligne
            self._line = LINE           # durée réelle d'une ligne (pente)
            self._phased = False
            self._waiting_end_of_tone = False

    def status(self):
        if self._waiting_end_of_tone:
            return "Signal de départ reçu…"
        if self.mode is None:
            return "En attente d'une carte (signal de départ à 300 Hz)…"
        state = "phasage" if not self._phased and len(self._rows) < PHASING_LINES else (
            "calée" if self._phased else "sans phasage")
        return f"Fax météo · ligne {len(self._rows)} · {state}"

    def start_now(self):
        """Commencer une carte tout de suite (sans attendre le départ)."""
        with self._lock:
            self._begin(len(self._values), phase=False)

    def set_left_edge(self, fraction):
        """Le point cliqué (fraction de la largeur) devient le bord gauche."""
        with self._lock:
            if self.mode is None:
                return
            self._start += int(round(fraction * self._line)) % int(self._line)
            self._phased = True
            self._render_all()

    @property
    def slant_ppm(self):
        return (self._line / LINE - 1) * 1e6

    def set_slant(self, ppm):
        """Corrige la pente : +100 ppm allonge chaque ligne de 0,6 échantillon."""
        with self._lock:
            self._line = LINE * (1 + ppm * 1e-6)
            if self.mode is not None:
                self._render_all()

    # -- démodulation FM : de 0 (noir) à 1 (blanc), à 12 kHz

    def feed(self, samples):
        n = np.arange(len(samples))
        step = 2 * np.pi * CENTER_HZ / self.rate
        mixed = samples * np.exp(-1j * (self._phase + step * n))
        self._phase = (self._phase + step * len(samples)) % (2 * np.pi)
        padded = np.concatenate([self._fir_state, mixed])
        filtered = np.convolve(padded, self._fir, mode="valid")
        self._fir_state = padded[len(padded) - len(self._fir_state):]
        first = (-self._decim_count) % self._decim
        self._decim_count += len(filtered)
        z = filtered[first::self._decim]
        if not len(z):
            return
        z = np.concatenate([[z[0] if self._prev is None else self._prev], z])
        self._prev = z[-1]
        freq = CENTER_HZ + np.angle(z[1:] * np.conj(z[:-1])) * WORK_RATE / (2 * np.pi)
        value = np.clip((freq - BLACK_HZ) / (WHITE_HZ - BLACK_HZ), 0, 1)
        if self.inverted:
            value = 1 - value
        with self._lock:
            self._values = np.concatenate([self._values, value.astype(np.float32)])
            self._check_tones()
            if self.mode is not None:
                self._decode_lines()
            else:
                self._trim()

    def _trim(self):
        """Hors carte, on ne garde que les dernières secondes."""
        drop = len(self._values) - 3 * WORK_RATE
        if drop > 0:
            self._values = self._values[drop:]
            self._checked = max(0, self._checked - drop)

    # -- signaux de départ et d'arrêt

    def _tone_level(self, end, hz):
        """Force de l'alternance à `hz` dans la dernière seconde (dB)."""
        part = self._values[end - WORK_RATE:end]
        spectrum = np.abs(np.fft.rfft((part - part.mean()) * np.hanning(len(part))))
        k = int(round(hz))              # 1 Hz par case
        peak = spectrum[k - 3:k + 4].max()
        floor = np.median(spectrum[100:1500]) + 1e-9
        return 20 * np.log10(peak / floor + 1e-9)

    def _check_tones(self):
        step = WORK_RATE // 4
        while self._checked + step <= len(self._values):
            self._checked += step
            end = self._checked
            if end < WORK_RATE:
                continue
            if self.mode is None:
                start = self._tone_level(end, START_HZ) > TONE_DB + 10
                if start:
                    self._tone_count += 1
                    if self._tone_count >= 4:
                        self._waiting_end_of_tone = True
                elif self._waiting_end_of_tone:
                    # fin du départ : le phasage commence
                    self._waiting_end_of_tone = False
                    self._tone_count = 0
                    self._begin(end - step, phase=True)
                else:
                    self._tone_count = 0
            else:
                stop = self._tone_level(end, STOP_HZ) > TONE_DB + 10
                self._stop_count = self._stop_count + 1 if stop else 0
                if self._stop_count >= 6:
                    # la carte s'arrête avant l'alternance de fin
                    self._finish(drop_seconds=2.5)
                    return

    # -- carte

    def _begin(self, start, phase):
        self.mode = MODE
        self._rows = []
        self._start = start
        self._line = LINE
        self._phased = not phase        # sans phasage : on garde ce départ
        self._stop_count = 0
        self.image = np.zeros((0, WIDTH, 3), np.uint8)

    def _row(self, k):
        a = self._start + k * self._line
        idx = a + np.arange(WIDTH) * (self._line / WIDTH)
        idx = np.clip(np.round(idx).astype(int), 0, len(self._values) - 1)
        return (self._values[idx] * 255).astype(np.uint8)

    def _render_all(self):
        count = len(self._rows)
        self._rows = [self._row(k) for k in range(count)
                      if self._start + (k + 1) * self._line <= len(self._values)]
        self._update_image()

    def _update_image(self):
        if self._rows:
            gray = np.array(self._rows)
            self.image = np.repeat(gray[:, :, None], 3, axis=2)

    def _decode_lines(self):
        added = False
        while self._start + (len(self._rows) + 1) * self._line <= len(self._values):
            self._rows.append(self._row(len(self._rows)))
            added = True
            if not self._phased and len(self._rows) == PHASING_LINES:
                self._phasing()
            if len(self._rows) >= MAX_LINES:
                self._finish()
                return
        if added:
            self._update_image()

    def _phasing(self):
        """Bord gauche et pente d'après l'impulsion de phasage de chaque ligne."""
        line = int(LINE)
        lines = np.array([self._values[self._start + k * line:self._start + (k + 1) * line]
                          for k in range(4, PHASING_LINES)])
        profile = lines.mean(axis=0)
        width = line // 20              # l'impulsion fait 5 % de la ligne
        kernel = np.ones(width) / width
        wrap = lambda p: np.concatenate([p, p[:width]])
        smooth = np.convolve(wrap(profile), kernel, "valid")[:line]
        deviation = smooth - np.median(profile)
        sign = 1 if deviation.max() > -deviation.min() else -1
        contrast = sign * deviation
        centre = int(np.argmax(contrast))
        if contrast[centre] < 0.35:     # pas d'impulsion nette : pas de phasage
            self._phased = True
            return
        # position de l'impulsion ligne par ligne → pente
        positions = []
        for k, row in enumerate(lines):
            s = np.convolve(wrap(sign * (row - np.median(row))), kernel, "valid")[:line]
            near = (np.arange(line) - centre + line // 2) % line - line // 2
            window = np.abs(near) < line // 10
            positions.append(centre + near[window][np.argmax(s[window])])
        k = np.arange(len(positions))
        slope, first = np.polyfit(k, positions, 1)
        if abs(slope) > LINE * 0.002:   # pente invraisemblable (plus de 2000 ppm)
            slope, first = 0.0, float(np.median(positions))
        self._line = LINE + slope
        # le début de l'impulsion devient le bord gauche ; `first` est sa
        # position dans la 5e ligne, on remonte à la 1re
        self._start += int(round(first - 4 * slope)) % line
        self._phased = True
        self._render_all()

    def _finish(self, drop_seconds=0.0):
        drop = int(drop_seconds * LPM / 60)
        rows = self._rows[:len(self._rows) - drop] if drop else self._rows
        if len(rows) > PHASING_LINES:
            gray = np.array(rows)
            self.finished.append((MODE, np.repeat(gray[:, :, None], 3, axis=2)))
        self.mode = None
        self._values = self._values[-WORK_RATE:]
        self._checked = len(self._values)
        self._stop_count = 0
