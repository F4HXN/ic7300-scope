"""Décodeur RTTY (Baudot ITA2) : de l'audio au texte.

Radioamateurs : 45,45 bauds, écart 170 Hz. Météo allemande (DWD) : 50 bauds,
écart 450 Hz.

Les deux tonalités (mark et space) sont ramenées en bande de base, filtrées,
et la différence de leurs amplitudes donne le bit. Chaque caractère est un
bit de départ (space), 5 bits de données (poids faible d'abord) et un arrêt
(mark) : on lit chaque bit en son milieu à partir du front de départ.

À l'émission, afsk() fait l'inverse : le texte en Baudot, puis les deux
tonalités en audio (AFSK), à émettre en BLU sur l'entrée USB de la radio.
"""
import numpy as np

from sstv import lowpass

BAUD = 45.45
SHIFT = 170.0
SEARCH_HZ = 60                  # en mode auto, on cherche la paire à ± autour
MIN_SNR_DB = 8                  # tonalités au-dessus du bruit pour décoder

LETTERS = ["", "E", "\n", "A", " ", "S", "I", "U", "", "D", "R", "J", "N",
           "F", "C", "K", "T", "Z", "L", "W", "H", "Y", "P", "Q", "O", "B",
           "G", None, "M", "X", "V", None]
FIGURES = ["", "3", "\n", "-", " ", "'", "8", "7", "", "$", "4", "", ",",
           "!", ":", "(", "5", "+", ")", "2", "#", "6", "0", "1", "9", "?",
           "&", None, ".", "/", "=", None]
FIGS, LTRS = 0x1B, 0x1F
CR, LF = 0x08, 0x02
STOP_BITS = 1.5
LEAD_IN = 0.4                   # s de mark avant le texte, pour caler l'autre


class RttyDecoder:
    def __init__(self, rate, baud=BAUD, shift=SHIFT, inverted=False):
        self.rate = rate
        self.baud = baud
        self.shift = shift
        self.pitch = 2210.0             # milieu entre les deux tonalités
        self.auto_pitch = True
        self.inverted = inverted        # mark sur la tonalité haute
        self.quality = 0.5              # part de caractères bien encadrés
        self.snr = 0.0
        self._anchor = self.pitch
        self._stage = 16                # 48 kHz → 3 kHz
        self._mid_rate = rate / self._stage
        self._bit = self._mid_rate / baud
        self._fir = lowpass(baud, self._mid_rate, 129)
        self._phases = [0.0, 0.0]
        self._pending = np.zeros(0, np.float32)
        self._fir_state = np.zeros((2, len(self._fir) - 1), complex)
        self._spectrum = None
        self._n = 0
        self._prev = 1.0
        self._start = None              # échantillon du front de départ
        self._bits = []
        self._figures = False

    @property
    def has_signal(self):
        return self.snr > MIN_SNR_DB

    @property
    def key_down(self):
        return self.has_signal and self.quality > 0.5

    def status(self):
        if not self.has_signal:
            state = "pas de signal"
        else:
            state = f"qualité {self.quality * 100:3.0f} %"
        return f"{self.pitch:6.1f} Hz · {state}"

    def set_pitch(self, hz):
        self.pitch = self._anchor = hz
        self._start = None
        self._bits = []

    def tones(self):
        """Fréquences audio (mark, space)."""
        low, high = self.pitch - self.shift / 2, self.pitch + self.shift / 2
        return (high, low) if self.inverted else (low, high)

    def track_pitch(self, db, bin_hz):
        """Mesure le signal et, en auto, cale le milieu sur la paire de tonalités."""
        self._spectrum = db if self._spectrum is None else (
            0.8 * self._spectrum + 0.2 * db)
        floor = np.median(self._spectrum)

        def level(f):
            return np.interp(f / bin_hz, np.arange(len(self._spectrum)), self._spectrum)

        if self.auto_pitch:
            span = max(SEARCH_HZ, self.shift / 3)   # 150 Hz pour l'écart de 450
            centres = np.arange(self._anchor - span, self._anchor + span, 2.0)
            score = level(centres - self.shift / 2) + level(centres + self.shift / 2)
            best = centres[int(np.argmax(score))]
            if min(level(best - self.shift / 2), level(best + self.shift / 2)) > floor + MIN_SNR_DB:
                self.pitch += 0.3 * (best - self.pitch)
        self.snr = min(level(f) for f in self.tones()) - floor

    def feed(self, samples):
        """Ajoute des échantillons et renvoie le texte décodé entre-temps."""
        buf = np.concatenate([self._pending, samples])
        count = len(buf) // self._stage
        self._pending = buf[count * self._stage:]
        seg = buf[:count * self._stage]
        n = np.arange(len(seg))
        envelopes = []
        for i, freq in enumerate(self.tones()):
            step = 2 * np.pi * freq / self.rate
            mixed = seg * np.exp(-1j * (self._phases[i] + step * n))
            self._phases[i] = (self._phases[i] + step * len(seg)) % (2 * np.pi)
            mid = mixed.reshape(count, self._stage).mean(axis=1)
            padded = np.concatenate([self._fir_state[i], mid])
            envelopes.append(np.abs(np.convolve(padded, self._fir, mode="valid")))
            self._fir_state[i] = padded[len(padded) - self._fir_state.shape[1]:]
        mark, space = envelopes
        return "".join(self._sample(d) for d in mark - space)

    def _sample(self, d):
        self._n += 1
        out = ""
        if self._start is None:
            if self._prev > 0 >= d:     # front mark → space : bit de départ
                self._start = self._n
                self._bits = []
        else:
            elapsed = self._n - self._start
            index = len(self._bits)     # 0 : contrôle du départ, 1 à 5 : données
            if elapsed >= self._bit * (index + 0.5):
                if index == 0 and d > 0:
                    self._start = None  # parasite, pas un vrai départ
                elif index < 6:
                    self._bits.append(d > 0)
                else:                   # bit d'arrêt : doit être un mark
                    out = self._character(d > 0)
                    self._start = None
        self._prev = d
        return out

    def _character(self, stop_ok):
        self.quality += 0.1 * (stop_ok - self.quality)
        if not stop_ok or not self.has_signal:
            return ""
        code = sum(bit << i for i, bit in enumerate(self._bits[1:]))
        if code == FIGS:
            self._figures = True
            return ""
        if code == LTRS:
            self._figures = False
            return ""
        char = (FIGURES if self._figures else LETTERS)[code]
        if char == " ":
            self._figures = False       # retour en lettres après une espace
        return char


def baudot(text):
    """Texte → [(code ITA2, caractère affiché)], encadré de retours à la ligne.

    Les caractères sans code Baudot sont omis. Le décodeur d'en face repasse en
    lettres après une espace (USOS) : on renvoie FIGS avant le chiffre suivant."""
    out = [(LTRS, ""), (CR, ""), (LF, "")]
    figures = False
    for char in text.upper():
        if char in LETTERS:
            if figures:
                out.append((LTRS, ""))
                figures = False
            out.append((LETTERS.index(char), char))
        elif char in FIGURES:
            if not figures:
                out.append((FIGS, ""))
                figures = True
            out.append((FIGURES.index(char), char))
        if char == " ":
            figures = False
    return out + [(CR, ""), (LF, ""), (LTRS, "")]


def afsk(text, rate, mark, space, baud=BAUD):
    """Le texte en audio RTTY : (échantillons, [(instant, caractère)], durée).

    Chaque caractère : départ (space), 5 bits poids faible d'abord, arrêt
    (mark, 1,5 bit). La phase est continue d'une tonalité à l'autre."""
    bit = rate / baud
    freqs, schedule = [mark] * round(LEAD_IN * rate), []
    for code, char in baudot(text):
        if char:
            schedule.append((len(freqs) / rate, char))
        start = len(freqs)
        bits = [0] + [(code >> i) & 1 for i in range(5)]
        for i, b in enumerate(bits):
            freqs += [mark if b else space] * (round(start + (i + 1) * bit) - len(freqs))
        freqs += [mark] * (round(start + (6 + STOP_BITS) * bit) - len(freqs))
    freqs += [mark] * round(0.2 * rate)
    phase = 2 * np.pi * np.cumsum(np.array(freqs)) / rate
    return (0.5 * np.sin(phase)).astype(np.float32), schedule, len(freqs) / rate
