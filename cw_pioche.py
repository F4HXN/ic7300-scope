"""CW « à la pioche » : le rythme d'une main sur un manipulateur droit.

Le manipulateur de la radio tape des points et des traits parfaits. Une main,
elle, garde son style d'un bout à l'autre du message (traits un peu longs,
points appuyés) mais varie à chaque élément, ralentit ou accélère un peu,
étire les espaces entre les lettres et hésite parfois avant l'une d'elles.

hand_timing() traduit le texte en une suite (manipulateur baissé, durée en s),
à la vitesse moyenne demandée.
"""
import math
import random

from cw_decoder import MORSE

CODE = {char: code for code, char in MORSE.items() if len(char) == 1}


def _symbols(word):
    """Codes Morse d'un mot ; ^ lie les deux lettres qui suivent (^SK)."""
    out, i = [], 0
    while i < len(word):
        pair = word[i + 1:i + 3]
        if word[i] == "^" and len(pair) == 2 and all(c in CODE for c in pair):
            out.append(CODE[pair[0]] + CODE[pair[1]])
            i += 3
            continue
        if word[i] in CODE:
            out.append(CODE[word[i]])
        i += 1
    return out


def hand_timing(text, wpm, rng=random):
    unit = 1.2 / wpm                        # durée d'un point (norme PARIS)
    # style de l'opérateur, le même pour tout le message
    dash = rng.uniform(3.1, 3.6)            # traits un peu plus longs que 3
    dot = rng.uniform(1.0, 1.15)            # points appuyés
    letter_gap = rng.uniform(3.3, 4.2)      # espaces entre lettres étirés
    word_gap = rng.uniform(7.5, 9.5)
    speed = 1.0

    def vary(units, spread):
        return unit * units * min(1.4, max(0.6, rng.gauss(1.0, spread))) / speed

    steps = []
    nominal = 0                             # durée du texte au manipulateur, en points
    for word in text.split():
        for s, code in enumerate(_symbols(word)):
            # la vitesse dérive doucement d'une lettre à l'autre
            speed = min(1.12, max(0.88, speed * math.exp(rng.gauss(0, 0.025))))
            if steps:
                gap = vary(word_gap, 0.15) if s == 0 else vary(letter_gap, 0.15)
                if s and rng.random() < 0.04:       # hésitation
                    gap += unit * rng.uniform(1.0, 3.0)
                steps.append((False, gap))
                nominal += 3 if s else 7
            for e, element in enumerate(code):
                if e:
                    steps.append((False, vary(1.0, 0.12)))
                steps.append((True, vary(dot if element == "." else dash, 0.09)))
                nominal += (1 if element == "." else 3) + (1 if e else 0)
    # même durée totale qu'au manipulateur : la vitesse affichée reste juste
    total = sum(duration for _, duration in steps)
    scale = nominal * unit / total if total else 1.0
    return [(down, duration * scale) for down, duration in steps]
