"""DeepCW optionnel : inférence bornée en arrière-plan, sans correction lexicale."""
import importlib.util
import os
from pathlib import Path
import queue
import threading

import numpy as np
from cw_decoder import CwDecoder

ROOT = Path(__file__).resolve().parent


def load_engine():
    spec = importlib.util.spec_from_file_location(
        'ic7300_deepcw_engine', ROOT / 'vendor/deepcw/engine.py')
    # dataclasses consulte sys.modules pendant le chargement.
    import sys
    module = sys.modules.get(spec.name)
    if module is None:
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(spec.name, None)
            raise
    return module


class NeuralCwDecoder(CwDecoder):
    """Même interface que CW ; aucune inférence dans le fil de capture audio."""
    def __init__(self, rate, model_dir=None):
        super().__init__(rate)
        self.model_dir = Path(model_dir or ROOT / 'models/deepcw')
        self.error = ''
        self._buffer_samples = 0
        self._generation = 0
        self._input = queue.Queue(maxsize=100)  # moins de 3 s à 48 kHz / 1024
        self._output = queue.Queue(maxsize=100)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name='DeepCW')
        self._thread.start()

    def set_pitch(self, hz):
        if abs(hz - self.pitch) > 50:
            self.reset()
        super().set_pitch(hz)

    def reset(self):
        self._generation += 1

    def feed(self, samples):
        classic = super().feed(samples)
        if self.error:
            return classic
        try:
            self._input.put_nowait((self._generation, self.pitch,
                                   np.array(samples, dtype=np.float32, copy=True)))
        except queue.Full:
            # Ne jamais recoller des morceaux séparés par une perte audio.
            self.reset()
        chunks = []
        while True:
            try:
                generation, text = self._output.get_nowait()
            except queue.Empty:
                break
            if generation == self._generation:
                chunks.append(text)
        return ''.join(chunks)

    def status(self):
        if self.error:
            return f'CW classique · DeepCW indisponible : {self.error}'
        return 'DeepCW · délai ~3–5 s · ' + super().status()

    def close(self):
        self._stop.set()
        self._thread.join(timeout=2)

    def _run(self):
        try:
            engine = load_engine()
            meta = engine.load_metadata(self.model_dir / 'model.onnx.json')
            # Limite le CPU afin de préserver le scope et le son.
            opt = engine.ort.SessionOptions()
            opt.intra_op_num_threads = 2
            opt.inter_op_num_threads = 1
            session = engine.ort.InferenceSession(
                str(self.model_dir / 'model.onnx'), sess_options=opt,
                providers=['CPUExecutionProvider'])
            target = int(meta['sample_rate'])
            if self.rate % target:
                raise ValueError('fréquence audio non multiple de celle du modèle')
            factor = self.rate // target
            generation = None
            while not self._stop.is_set():
                try:
                    gen, pitch, samples = self._input.get(timeout=.1)
                except queue.Empty:
                    continue
                if gen != generation:
                    generation = gen
                    decoder = engine.Decoder.__new__(engine.Decoder)
                    decoder.metadata, decoder.session = meta, session
                    decoder.stitcher = engine.PosteriorStitcher(meta)
                    decoder.transcript = ''
                    audio = np.zeros(0, np.float32)
                    pending = np.zeros(0, np.float32)
                    next_step = 2 * target
                pending = np.concatenate((pending, samples))
                count = len(pending) // factor
                # Moyenne anti-repliement élémentaire, sans dépendance scipy.
                down = pending[:count * factor].reshape(-1, factor).mean(axis=1)
                pending = pending[count * factor:]
                audio = np.concatenate((audio, down))
                if len(audio) < next_step:
                    continue
                # Le clic/note automatique sélectionne un seul signal. On le
                # recentre à 600 Hz, dans la bande d'entrée du modèle.
                n = len(audio)
                bins = np.fft.rfftfreq(n, 1 / target)
                spectrum = np.fft.rfft(audio)
                mask = np.clip((150 - np.abs(bins - pitch)) / 50, 0, 1)
                selected = np.fft.irfft(spectrum * mask, n=n)
                analytic = np.fft.fft(selected)
                analytic[1:(n + 1)//2] *= 2
                analytic[n//2 + 1:] = 0
                selected = (np.fft.ifft(analytic) * np.exp(
                    2j*np.pi*(600-pitch)*np.arange(n)/target)).real.astype(np.float32)
                # Vérifier la tonalité AVANT le passe-bande : le bruit filtré
                # seul peut ressembler à des E/T au réseau neuronal.
                frames = np.lib.stride_tricks.sliding_window_view(audio, 256)[::128]
                power = np.mean(np.abs(np.fft.rfft(frames * np.hanning(256),
                                                  axis=1))**2, axis=0)
                frequencies = np.fft.rfftfreq(256, 1 / target)
                noise_band = power[(frequencies >= 250) & (frequencies <= 1400)]
                tone_band = power[np.abs(frequencies - pitch) <= 75]
                tonal = (len(tone_band) > 0 and float(tone_band.max()) >
                         4 * max(float(np.median(noise_band)), 1e-10))
                text = decoder.step(selected if tonal else np.zeros_like(selected),
                                    len(selected))
                if not tonal:
                    text = ''
                # L'exemple amont garde le transcript ; seule la nouvelle
                # sortie nous sert, garder juste le témoin d'espacement.
                decoder.transcript = 'x' if decoder.transcript else ''
                if text:
                    self._output.put_nowait((gen, text))
                self._buffer_samples = len(audio)
                next_step = len(audio) + target
                # Rebase des coordonnées : audio et postérieurs restent bornés.
                shift = decoder.stitcher.context_start_frame
                if shift:
                    removed = shift * decoder.stitcher.hop_length
                    audio = audio[removed:]
                    next_step -= removed
                    for name in ('row_base', 'covered_end', 'frozen_end', 'committed_end'):
                        setattr(decoder.stitcher, name,
                                getattr(decoder.stitcher, name) - shift)
        except Exception as exc:
            self.error = str(exc)


def make_cw_decoder(rate):
    if os.environ.get('IC7300_CW_ENGINE', '').lower() == 'classic':
        return CwDecoder(rate)
    model = ROOT / 'models/deepcw'
    if not (model / 'model.onnx').is_file():
        return CwDecoder(rate)
    return NeuralCwDecoder(rate, model)
