"""Décodeur PSK31 (BPSK, 31,25 bauds) : de l'audio reçu au texte.

Le signal est ramené en bande de base autour de sa fréquence audio, filtré et
réduit à 16 échantillons par symbole. On lit chaque symbole là où l'amplitude
est la plus forte, un changement de phase vaut 0, pas de changement vaut 1,
et les caractères Varicode sont séparés par « 00 ». Une AFC suit la dérive.
"""
import numpy as np

BAUD = 31.25
SAMPLES_PER_SYMBOL = 16
SQUELCH = 0.35                  # qualité minimale pour afficher du texte
SEARCH_HZ = 25                  # en mode auto, on cherche le signal à ± autour

VARICODE = [
    "1010101011", "1011011011", "1011101101", "1101110111", "1011101011",
    "1101011111", "1011101111", "1011111101", "1011111111", "11101111",
    "11101", "1101101111", "1011011101", "11111", "1101110101",
    "1110101011", "1011110111", "1011110101", "1110101101", "1110101111",
    "1101011011", "1101101011", "1101101101", "1101010111", "1101111011",
    "1101111101", "1110110111", "1101010101", "1101011101", "1110111011",
    "1011111011", "1101111111", "1", "111111111", "101011111",
    "111110101", "111011011", "1011010101", "1010111011", "101111111",
    "11111011", "11110111", "101101111", "111011111", "1110101",
    "110101", "1010111", "110101111", "10110111", "10111101",
    "11101101", "11111111", "101110111", "101011011", "101101011",
    "110101101", "110101011", "110110111", "11110101", "110111101",
    "111101101", "1010101", "111010111", "1010101111", "1010111101",
    "1111101", "11101011", "10101101", "10110101", "1110111",
    "11011011", "11111101", "101010101", "1111111", "111111101",
    "101111101", "11010111", "10111011", "11011101", "10101011",
    "11010101", "111011101", "10101111", "1101111", "1101101",
    "101010111", "110110101", "101011101", "101110101", "101111011",
    "1010101101", "111110111", "111101111", "111111011", "1010111111",
    "101101101", "1011011111", "1011", "1011111", "101111",
    "101101", "11", "111101", "1011011", "101011",
    "1101", "111101011", "10111111", "11011", "111011",
    "1111", "111", "111111", "110111111", "10101",
    "10111", "101", "110111", "1111011", "1101011",
    "11011111", "1011101", "111010101", "1010110111", "110111011",
    "1010110101", "1011010111", "1110110101",
]
DECODE = {code: chr(i) for i, code in enumerate(VARICODE)}


def lowpass(cutoff, rate, taps):
    """Filtre passe-bas RIF (sinus cardinal fenêtré), gain 1."""
    n = np.arange(taps) - (taps - 1) / 2
    h = np.sinc(2 * cutoff / rate * n) * np.hamming(taps)
    return h / h.sum()


class Psk31Decoder:
    def __init__(self, rate):
        self.rate = rate
        self.pitch = 1000.0             # fréquence audio du signal
        self._anchor = self.pitch       # fréquence choisie (clic)
        self.auto_pitch = True          # recaler sur le pic voisin
        self.quality = 0.0
        self._phase = 0.0
        self._stage1 = 16               # 48 kHz → 3 kHz par moyenne
        self._mid_rate = rate / self._stage1
        self._stage2 = round(self._mid_rate / (BAUD * SAMPLES_PER_SYMBOL))
        self._fir = lowpass(50, self._mid_rate, 129)
        self._fir_state = np.zeros(len(self._fir) - 1, complex)
        self._pending = np.zeros(0, complex)
        self._mid_count = 0
        self._n = 0
        self._energy = np.zeros(SAMPLES_PER_SYMBOL)
        self._countdown = SAMPLES_PER_SYMBOL
        self._prev = None
        self._bits = ""
        self._spectrum = None

    @property
    def has_signal(self):
        return self.quality > SQUELCH

    @property
    def key_down(self):
        return self.has_signal

    def status(self):
        state = f"qualité {self.quality * 100:3.0f} %" if self.quality > 0 else ""
        return f"{self.pitch:6.1f} Hz · {state}".rstrip(" ·")

    def track_pitch(self, db, bin_hz):
        """Mode auto : se recaler sur le centre du signal voisin (± SEARCH_HZ).

        Au repos, un signal PSK31 montre deux pics à ±15,6 Hz de sa
        fréquence : on prend le barycentre de la puissance, pas le plus haut pic.
        """
        if not self.auto_pitch or self.quality > 0.5:
            return                      # l'AFC suit déjà le signal
        self._spectrum = db if self._spectrum is None else (
            0.8 * self._spectrum + 0.2 * db)
        lo = max(int((self.pitch - SEARCH_HZ) / bin_hz), 1)
        hi = int((self.pitch + SEARCH_HZ) / bin_hz) + 2
        power = 10 ** (self._spectrum[lo:hi] / 10)
        floor = 10 ** (np.median(self._spectrum) / 10)
        excess = np.clip(power - 4 * floor, 0, None)
        if excess.sum() > 0:
            centre = (excess * np.arange(lo, hi)).sum() / excess.sum() * bin_hz
            self.pitch += 0.3 * (centre - self.pitch)
            self._keep_near_anchor()

    def set_pitch(self, hz):
        self.pitch = self._anchor = hz
        self._prev = None
        self._bits = ""
        self.quality = 0.0

    def _keep_near_anchor(self):
        self.pitch = min(max(self.pitch, self._anchor - SEARCH_HZ),
                         self._anchor + SEARCH_HZ)

    def feed(self, samples):
        """Ajoute des échantillons et renvoie le texte décodé entre-temps."""
        n = np.arange(len(samples))
        step = 2 * np.pi * self.pitch / self.rate
        mixed = samples * np.exp(-1j * (self._phase + step * n))
        self._phase = (self._phase + step * len(samples)) % (2 * np.pi)

        buf = np.concatenate([self._pending, mixed])
        count = len(buf) // self._stage1
        self._pending = buf[count * self._stage1:]
        mid = buf[:count * self._stage1].reshape(count, self._stage1).mean(axis=1)

        padded = np.concatenate([self._fir_state, mid])
        filtered = np.convolve(padded, self._fir, mode="valid")
        self._fir_state = padded[len(padded) - len(self._fir_state):]

        # on garde un échantillon sur _stage2, sans perdre le fil entre appels
        first = (-self._mid_count) % self._stage2
        self._mid_count += len(filtered)
        out = ""
        for z in filtered[first::self._stage2]:
            out += self._sample(z)
        return out

    def _sample(self, z):
        p = self._n % SAMPLES_PER_SYMBOL
        self._n += 1
        self._energy[p] = 0.97 * self._energy[p] + 0.03 * abs(z) ** 2
        self._countdown -= 1
        if self._countdown:
            return ""
        # lecture au maximum d'amplitude : le prochain symbole est lu 16
        # échantillons plus tard, à un cran près pour s'en rapprocher
        best = int(np.argmax(self._energy))
        diff = (best - p + 8) % SAMPLES_PER_SYMBOL - 8
        self._countdown = SAMPLES_PER_SYMBOL + int(np.sign(diff))
        return self._symbol(z)

    def _symbol(self, z):
        prev, self._prev = self._prev, z
        if prev is None or abs(prev) == 0:
            return ""
        d = z * np.conj(prev)
        # 1 pour une BPSK propre (0 ou 180°), autour de 0 pour du bruit
        self.quality += 0.1 * (np.cos(2 * np.angle(d)) - self.quality)
        if self.quality > 0.3:          # AFC : l'écart de phase résiduel
            error = np.angle(d * d) / 2
            self.pitch += 0.05 * error * BAUD / (2 * np.pi)
            self._keep_near_anchor()
        if not self.has_signal:
            self._bits = ""
            return ""
        self._bits += "1" if d.real > 0 else "0"
        if self._bits.endswith("00"):
            code, self._bits = self._bits[:-2].lstrip("0"), ""
            char = DECODE.get(code, "")
            if char == "\n" or char.isprintable():
                return char
            return ""
        if len(self._bits) > 14:        # trop long : ce n'est pas du Varicode
            self._bits = ""
        return ""
