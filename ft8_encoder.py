"""Émission FT8 et FT4 : du message au son.

Le message est codé par ft8code / ft4code (fournis avec WSJT-X) : 77 bits,
CRC, code correcteur, puis les tonalités (79 en FT8, 105 en FT4 avec les deux
symboles de rampe). Le son est fait comme dans WSJT-X : modulation de
fréquence à phase continue, adoucie par un filtre gaussien (GFSK), avec une
rampe au début et à la fin pour ne pas éclabousser à côté.

FT8 : 8 tonalités espacées de 6,25 Hz, 0,16 s par symbole (12,64 s en tout).
FT4 : 4 tonalités espacées de 20,833 Hz, 0,048 s par symbole (5,04 s).
"""
import re
import shutil
import subprocess

import numpy as np
from scipy.special import erf

# mode : (programme, durée d'un symbole en s, produit BT du filtre gaussien)
MODES = {"ft8": ("ft8code", 0.16, 2.0), "ft4": ("ft4code", 0.048, 1.0)}


class Ft8EncodeError(Exception):
    pass


def encode(message, mode="ft8"):
    """Tonalités du message (liste d'entiers) ; Ft8EncodeError si le message
    ne passe pas tel quel (texte libre de plus de 13 caractères…)."""
    program = MODES[mode][0]
    if not shutil.which(program):
        raise Ft8EncodeError(f"{program} introuvable (installer WSJT-X)")
    message = " ".join(message.upper().split())
    if not message:
        raise Ft8EncodeError("message vide")
    try:
        out = subprocess.run([program, message], capture_output=True, text=True,
                             timeout=5).stdout
    except (OSError, subprocess.SubprocessError) as e:
        raise Ft8EncodeError(f"{program} : {e}") from e
    # ligne « 1. message  décodé  ... » : ce que la station d'en face lira
    decoded = None
    for line in out.splitlines():
        m = re.match(r"\s*1\.\s+(.*)", line)
        if m:
            parts = re.split(r"\s{2,}", m.group(1).strip())
            if len(parts) >= 2:
                decoded = parts[1]
            break
    if decoded != message:
        raise Ft8EncodeError(f"« {message} » serait reçu « {decoded or '?'} » "
                             "(texte libre : 13 caractères au plus)")
    m = re.search(r"Channel symbols \((\d+) tones\)", out)
    if not m:
        raise Ft8EncodeError(f"sortie de {program} non comprise")
    count = int(m.group(1))
    for line in out[m.end():].splitlines():
        digits = line.replace(" ", "")
        if digits.isdigit() and len(digits) == count:
            return [int(d) for d in digits]
    raise Ft8EncodeError(f"tonalités de {program} non trouvées")


def _pulse(bt, t):
    """Impulsion gaussienne de la GFSK (aire d'un symbole)."""
    c = np.pi * np.sqrt(2 / np.log(2))
    return 0.5 * (erf(c * bt * (t + 0.5)) - erf(c * bt * (t - 0.5)))


def waveform(tones, f0, mode="ft8", rate=48000, amplitude=0.5):
    """Le son des tonalités, la plus basse à f0 (Hz)."""
    _, symbol, bt = MODES[mode]
    nsps = round(rate * symbol)
    n = len(tones)
    pulse = _pulse(bt, (np.arange(3 * nsps) - 1.5 * nsps) / nsps)
    peak = 2 * np.pi / symbol / rate        # écart d'une tonalité, par échantillon
    # un symbole fictif de part et d'autre : la fréquence y est déjà établie
    dphi = np.zeros((n + 2) * nsps)
    for j, tone in enumerate(tones):
        dphi[j * nsps:(j + 3) * nsps] += peak * pulse * tone
    dphi[:2 * nsps] += peak * tones[0] * pulse[nsps:]
    dphi[n * nsps:] += peak * tones[-1] * pulse[:2 * nsps]
    dphi += 2 * np.pi * f0 / rate
    wave = np.sin(np.cumsum(dphi[nsps:(n + 1) * nsps]))
    # rampe : 1/8 de symbole en FT8, un symbole entier en FT4
    ramp = nsps // 8 if mode == "ft8" else nsps
    edge = (1 - np.cos(np.pi * np.arange(ramp) / ramp)) / 2
    wave[:ramp] *= edge
    wave[-ramp:] *= edge[::-1]
    return (amplitude * wave).astype(np.float32)
