"""Audio local Windows via PortAudio (WASAPI partagé). Aucun appel PulseAudio.

Les objets Capture et Job exposent les opérations utilisées par les lecteurs
Qt : read/terminate/poll/wait. Les erreurs des fils audio sont conservées et
remontées à l'interface ; aucun périphérique TX n'est choisi implicitement.
"""
import io
import math
import queue
import subprocess
import threading
import time
import wave

import numpy as np
import sounddevice as sd
import soundfile as sf

RATE = 48000


def devices(kind):
    key = 'max_input_channels' if kind == 'input' else 'max_output_channels'
    apis = sd.query_hostapis()
    available = [(i, d) for i, d in enumerate(sd.query_devices()) if d[key] > 0]
    wasapi = [(i, d) for i, d in available if 'WASAPI' in apis[d['hostapi']]['name']]
    return [(str(i), d['name']) for i, d in (wasapi or available)]


def stream_options(device, kind):
    if device is None or str(device) == '':
        raise ValueError('Périphérique audio non sélectionné')
    device = int(device)
    info = sd.query_devices(device)
    channels = min(2, info['max_input_channels' if kind == 'input' else 'max_output_channels'])
    if channels < 1:
        raise ValueError('Périphérique audio incompatible')
    options = dict(device=device, channels=channels, samplerate=RATE, dtype='float32')
    if 'WASAPI' in sd.query_hostapis(info['hostapi'])['name']:
        options['extra_settings'] = sd.WasapiSettings(auto_convert=True)
    return options


class Capture:
    def __init__(self, source, rate=RATE):
        if rate != RATE:
            raise ValueError('La capture Windows utilise 48000 Hz')
        self.closed = False
        self._disposed = False
        self._close_lock = threading.Lock()
        self.error = ''
        self.pending = bytearray()
        self.chunks = queue.Queue(maxsize=100)
        self.stdout = self
        self.stream = sd.InputStream(**stream_options(source, 'input'),
                                     blocksize=960, callback=self._callback)
        try:
            self.stream.start()
        except Exception:
            self.stream.close()
            raise

    def _callback(self, data, frames, timing, status):
        if status.input_overflow:
            self.error = 'Perte de données sur l’entrée audio USB'
            self.closed = True
            return
        mono = np.mean(data, axis=1)
        pcm = (np.clip(mono, -1, 1) * 32767).astype('<i2').tobytes()
        try:
            self.chunks.put_nowait(pcm)
        except queue.Full:
            self.error = 'Le traitement audio ne suit plus la capture USB'
            self.closed = True

    @property
    def stderr(self):
        return io.BytesIO(self.error.encode())

    def read(self, count):
        deadline = time.monotonic() + 2
        while len(self.pending) < count and not self.closed:
            try:
                self.pending.extend(self.chunks.get(timeout=.1))
            except queue.Empty:
                if not self.stream.active or time.monotonic() > deadline:
                    self.error = 'Entrée audio déconnectée'
                    self.closed = True
        if self.closed:
            return b''
        data = bytes(self.pending[:count])
        del self.pending[:count]
        return data

    def terminate(self):
        self.closed = True
        with self._close_lock:
            if not self._disposed:
                self._disposed = True
                try:
                    self.stream.abort()
                finally:
                    self.stream.close()


def scaled(data, volume):
    # Même courbe que le réglage PulseAudio existant : 50 % = environ -18 dB.
    return np.clip(data * (volume / 100) ** 3, -1, 1).astype(np.float32)


class Output:
    def __init__(self, device):
        self.stream = sd.OutputStream(**stream_options(device, 'output'), blocksize=960)
        try:
            self.stream.start()
        except Exception:
            self.stream.close()
            raise

    def write(self, mono):
        data = np.repeat(np.asarray(mono, dtype=np.float32)[:, None], self.stream.channels, axis=1)
        self.stream.write(data)

    def close(self, abort=True):
        try:
            self.stream.abort() if abort else self.stream.stop()
        finally:
            self.stream.close()


class Job:
    """Travail audio annulable ; poll()!=None dès qu'il finit ou échoue."""
    def __init__(self, action):
        self.cancel = threading.Event()
        self.done = threading.Event()
        self.error = ''
        self.output = None
        self.thread = threading.Thread(target=self._run, args=(action,), daemon=True)
        self.thread.start()

    def _run(self, action):
        try:
            action(self)
        except Exception as exc:
            if not self.cancel.is_set():
                self.error = str(exc)
        finally:
            self.done.set()

    def poll(self):
        return (1 if self.error else 0) if self.done.is_set() else None

    def terminate(self):
        self.cancel.set()
        if not self.done.wait(2):
            if self.output is not None:
                self.output.stream.abort()
            self.done.wait(1)

    def send_signal(self, _):
        self.terminate()

    kill = terminate

    def wait(self, timeout=None):
        if not self.done.wait(timeout):
            raise subprocess.TimeoutExpired('audio Windows', timeout)
        return self.poll()


class Engine:
    def __init__(self, source, sink, speaker):
        self.source, self.sink, self.speaker = str(source), str(sink), str(speaker)
        self.listen_volume, self.tx_volume = 100, 50
        self.muted = False
        self.tx_db = -100.0
        self.rx_queue = queue.Queue(maxsize=10)
        self.listener = None
        self._start_listener()

    def _start_listener(self):
        output = Output(self.speaker)  # échoue immédiatement si la sortie est inutilisable
        def listen(job):
            job.output = output
            try:
                while not job.cancel.is_set():
                    try:
                        data = self.rx_queue.get(timeout=.1)
                    except queue.Empty:
                        continue
                    output.write(np.zeros_like(data) if self.muted else scaled(data, self.listen_volume))
            finally:
                output.close()
        self.listener = Job(listen)

    def feed(self, pcm):
        if self.listener.poll() is not None:
            return
        data = np.frombuffer(pcm, '<i2').astype(np.float32) / 32768
        try:
            self.rx_queue.put_nowait(data)
        except queue.Full:
            # L'écoute ne doit jamais bloquer le décodeur ; abandon du bloc le plus ancien.
            try:
                self.rx_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self.rx_queue.put_nowait(data)
            except queue.Full:
                pass

    def move_speaker(self, device):
        device = str(device)
        if device == self.sink:
            raise ValueError('La sortie d’écoute ne peut pas être la sortie USB du poste')
        old = self.speaker
        self.listener.terminate()
        self.speaker = device
        try:
            self._start_listener()
        except Exception:
            self.speaker = old
            self._start_listener()
            raise

    def meter(self, data):
        self.tx_db = float(20 * np.log10(np.sqrt(np.mean(data * data)) + 1e-10))

    def play(self, path, device, transmit=False):
        # Valider le fichier et ouvrir la sortie avant de lancer le fil.
        info = sf.info(path)
        output = Output(device)
        def action(job):
            job.output = output
            from scipy.signal import resample_poly
            divisor = math.gcd(RATE, info.samplerate)
            try:
                with sf.SoundFile(path) as sound:
                    for block in sound.blocks(blocksize=info.samplerate // 10, dtype='float32', always_2d=True):
                        if job.cancel.is_set():
                            break
                        mono = block.mean(axis=1)
                        if info.samplerate != RATE:
                            mono = resample_poly(mono, RATE // divisor, info.samplerate // divisor)
                        if transmit:
                            mono = scaled(mono, self.tx_volume)
                            self.meter(mono)
                        output.write(mono)
            finally:
                output.close(abort=job.cancel.is_set())
                if transmit:
                    self.tx_db = -100.0
        return Job(action)

    def microphone(self, source):
        capture = Capture(source)
        try:
            output = Output(self.sink)
        except Exception:
            capture.terminate()
            raise
        def action(job):
            job.output = output
            try:
                while not job.cancel.is_set():
                    data = capture.read(1920)
                    if not data:
                        raise RuntimeError(capture.error or 'Microphone interrompu')
                    mono = scaled(np.frombuffer(data, '<i2').astype(np.float32) / 32768, self.tx_volume)
                    self.meter(mono)
                    output.write(mono)
            finally:
                capture.terminate()
                output.close()
                self.tx_db = -100.0
        return Job(action)

    def record(self, source, path):
        capture = Capture(source)
        try:
            record = wave.open(path, 'wb')
            record.setparams((1, 2, RATE, 0, 'NONE', 'not compressed'))
        except Exception:
            capture.terminate()
            raise
        def action(job):
            try:
                while not job.cancel.is_set():
                    data = capture.read(1920)
                    if not data:
                        raise RuntimeError(capture.error or 'Microphone interrompu')
                    record.writeframes(data)
            finally:
                capture.terminate()
                record.close()
        return Job(action)

    def close(self):
        if self.listener:
            self.listener.terminate()


engine = None
