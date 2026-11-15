import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import numpy as np
sys.path.insert(0, os.environ.get('IC7300_TEST_APP', str(Path(__file__).resolve().parents[1] / 'app')))
from cw_decoder import CwDecoder, MORSE
from cw_neural import NeuralCwDecoder, make_cw_decoder, ROOT


def audio(text, rate=48000, wpm=20, pitch=600, noise=0):
    reverse = {v: k for k, v in MORSE.items()}
    unit = round(rate * 1.2 / wpm)
    chunks = [np.zeros(rate)]
    for word in text.split():
        for letter in word:
            for mark in reverse[letter]:
                n = unit * (3 if mark == '-' else 1)
                chunks += [np.sin(2*np.pi*pitch*np.arange(n)/rate), np.zeros(unit)]
            chunks.append(np.zeros(2*unit))
        chunks.append(np.zeros(4*unit))
    chunks.append(np.zeros(6*rate))
    result = np.concatenate(chunks)
    return (result + np.random.default_rng(42).normal(0,noise,len(result))).astype(np.float32)


class ClassicTests(unittest.TestCase):
    def test_digital_silence_does_not_prevent_decoding(self):
        d = CwDecoder(8000)
        samples = audio('CQ CQ DE N0CALL K', rate=8000, wpm=12)
        result = ''.join(d.feed(samples[i:i+1024]) for i in range(0,len(samples),1024))
        self.assertEqual(result.strip(), 'CQ CQ DE N0CALL K')

    def test_silence_is_not_text(self):
        self.assertEqual(CwDecoder(8000).feed(np.zeros(80000)), '')

    def test_force_classic(self):
        with patch.dict(os.environ, {'IC7300_CW_ENGINE':'classic'}):
            self.assertIs(type(make_cw_decoder(48000)), CwDecoder)

    def test_missing_model_falls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            d = NeuralCwDecoder(48000, directory)
            try:
                deadline=time.monotonic()+5
                while not d.error and time.monotonic()<deadline:
                    time.sleep(.01)
                self.assertTrue(d.error)
                self.assertIn('classique', d.status())
                self.assertEqual(d.feed(np.zeros(1024)), '')
            finally:
                d.close()


@unittest.skipUnless(importlib.util.find_spec('onnxruntime') and
                    (ROOT/'models/deepcw/model.onnx').is_file(), 'DeepCW non installé')
class NeuralTests(unittest.TestCase):
    def decode(self, text, pitch=600, noise=.3):
        d = NeuralCwDecoder(48000)
        d.auto_pitch=False
        d.set_pitch(pitch)
        result=''
        try:
            samples=audio(text, pitch=pitch,noise=noise)
            for i in range(0,len(samples),48000):
                result += d.feed(samples[i:i+48000])
                deadline=time.monotonic()+10
                while not d._input.empty() and not d.error and time.monotonic()<deadline:
                    time.sleep(.01)
                time.sleep(.05)
            # Récupérer la dernière sortie sans finaliser artificiellement le flux.
            time.sleep(.2)
            while not d._output.empty():
                gen, chunk=d._output.get_nowait()
                if gen == d._generation: result += chunk
            self.assertFalse(d.error, d.error)
            self.assertLess(d._buffer_samples, 48000)  # à 3200 Hz : moins de 15 s
            return result.strip()
        finally:
            d.close()

    def test_streaming_and_bounded_history(self):
        text='CQ CQ DE N0CALL N0CALL K CQ CQ DE N0CALL N0CALL K'
        self.assertEqual(self.decode(text), text)

    def test_selected_pitch_outside_model_band(self):
        text='CQ CQ DE N0CALL K'
        self.assertEqual(self.decode(text,pitch=350),text)

    def test_silence_produces_no_text(self):
        self.assertEqual(self.decode('', noise=.3), '')
        self.assertEqual(self.decode('', noise=0), '')

    def test_noisy_signal(self):
        text='CQ CQ DE N0CALL K'
        self.assertEqual(self.decode(text, noise=2), text)

    def test_reset_rejects_stale_output(self):
        d=NeuralCwDecoder(48000)
        try:
            d._output.put((d._generation,'OLD'))
            d.reset()
            self.assertEqual(d.feed(np.zeros(1024)), '')
        finally:
            d.close()

if __name__=='__main__': unittest.main()
