"""Contrats du portage USB, sans radio ni périphérique audio réel."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import time

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import windows_audio as audio
import platform_support


class FakeStream:
    created = []
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.channels = kwargs['channels']
        self.active = False
        self.closed = False
        self.written = []
        self.created.append(self)

    def start(self): self.active = True
    def abort(self): self.active = False
    def stop(self): self.active = False
    def close(self): self.closed = True
    def write(self, data): self.written.append(data.copy())


@pytest.fixture
def hardware(monkeypatch):
    FakeStream.created = []
    devices = [
        dict(name='USB Audio CODEC RX', hostapi=0, max_input_channels=2, max_output_channels=0),
        dict(name='USB Audio CODEC TX', hostapi=0, max_input_channels=0, max_output_channels=2),
        dict(name='Haut-parleurs', hostapi=0, max_input_channels=0, max_output_channels=2),
        dict(name='Microphone', hostapi=0, max_input_channels=1, max_output_channels=0),
        dict(name='Duplicata MME', hostapi=1, max_input_channels=2, max_output_channels=0),
    ]
    monkeypatch.setattr(audio.sd, 'query_devices', lambda index=None: devices if index is None else devices[int(index)])
    monkeypatch.setattr(audio.sd, 'query_hostapis', lambda index=None: [dict(name='Windows WASAPI'), dict(name='MME')] if index is None else dict(name='Windows WASAPI' if index == 0 else 'MME'))
    monkeypatch.setattr(audio.sd, 'InputStream', FakeStream)
    monkeypatch.setattr(audio.sd, 'OutputStream', FakeStream)
    monkeypatch.setattr(audio.sd, 'WasapiSettings', lambda **kwargs: kwargs)
    return devices


def test_device_selection_and_no_implicit_tx(hardware):
    assert audio.devices('input') == [('0', 'USB Audio CODEC RX'), ('3', 'Microphone')]
    with pytest.raises(ValueError):
        audio.Output(None)
    with pytest.raises(ValueError):
        audio.Output('0')
    options = audio.stream_options('1', 'output')
    assert options['extra_settings'] == {'auto_convert': True}
    assert options['samplerate'] == 48000


def test_capture_converts_stereo_and_preserves_partial_blocks(hardware):
    c = audio.Capture('0')
    c._callback(np.array([[.5, -.5], [.5, .5], [-.5, -.5]], dtype=np.float32), 3, None,
                SimpleNamespace(input_overflow=False))
    assert np.frombuffer(c.read(4), '<i2').tolist() == [0, 16383]
    assert np.frombuffer(c.read(2), '<i2').tolist() == [-16383]
    c.terminate()
    c.terminate()
    assert c.stream.closed and c.read(2) == b''


def test_capture_overflow_reports_error_and_closes(hardware):
    c = audio.Capture('0')
    c._callback(np.zeros((5,2)), 5, None, SimpleNamespace(input_overflow=True))
    assert c.read(2) == b'' and 'Perte' in c.error
    c.terminate()
    assert c.stream.closed


def wait_for(predicate):
    deadline = time.monotonic() + 2
    while not predicate() and time.monotonic() < deadline:
        time.sleep(.01)
    assert predicate()


def test_rx_mute_volume_and_output_protection(hardware):
    engine = audio.Engine('0', '1', '2')
    output = FakeStream.created[-1]
    try:
        engine.muted = True
        engine.feed(np.full(960, 16000, dtype='<i2').tobytes())
        wait_for(lambda: len(output.written) == 1)
        assert not output.written[0].any()
        engine.muted = False
        engine.listen_volume = 50
        engine.feed(np.full(960, 16000, dtype='<i2').tobytes())
        wait_for(lambda: len(output.written) == 2)
        np.testing.assert_allclose(output.written[1], 16000 / 32768 * .125)
        with pytest.raises(ValueError): engine.move_speaker('1')
    finally:
        engine.close()
    assert output.closed


def test_file_playback_gain_completion_and_invalid_file(hardware, tmp_path):
    path = tmp_path / 'tone.wav'
    audio.sf.write(path, np.full(4800, .5), 48000, subtype='PCM_16')
    engine = audio.Engine('0', '1', '2')
    try:
        job = engine.play(str(path), '1', transmit=True)
        assert job.wait(2) == 0
        output = FakeStream.created[-1]
        assert output.closed
        np.testing.assert_allclose(np.concatenate(output.written), .0625)
        with pytest.raises(Exception): engine.play(str(tmp_path / 'absent.wav'), '1', True)
    finally:
        engine.close()


def test_job_failure_is_observable():
    def fail(job): raise RuntimeError('USB débranché')
    job = audio.Job(fail)
    assert job.wait(2) == 1
    assert job.error == 'USB débranché'


def test_wsjtx_custom_path_with_spaces(monkeypatch, tmp_path):
    folder = tmp_path / 'WSJT X' / 'bin'
    folder.mkdir(parents=True)
    (folder / 'jt9.exe').touch()
    monkeypatch.setattr(platform_support, 'WINDOWS', True)
    monkeypatch.setattr(platform_support.shutil, 'which', lambda _: None)
    monkeypatch.setenv('WSJTX_BIN', str(folder))
    assert platform_support.wsjtx_program('jt9') == str(folder / 'jt9.exe')
    assert platform_support.wsjtx_program('ft8code') is None


@pytest.fixture(scope='module')
def qtapp():
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    return app


@pytest.fixture
def window(hardware, monkeypatch, qtapp, tmp_path):
    import ic7300_scope as scope
    import ft8_decoder
    monkeypatch.setattr(scope, 'WINDOWS', True)
    monkeypatch.setattr(scope, 'win_audio', audio, raising=False)
    monkeypatch.setattr(ft8_decoder, 'WINDOWS', True)
    monkeypatch.setattr(scope.QtCore.QThread, 'start', lambda _: None)
    monkeypatch.setattr(scope, 'BANDS_FILE', str(tmp_path/'bands.json'))
    monkeypatch.setattr(scope, 'OPTIONS_FILE', str(tmp_path/'options.json'))
    def forbidden(*args, **kwargs):
        raise AssertionError(f'Commande externe inattendue : {args}')
    monkeypatch.setattr(scope.subprocess, 'run', forbidden)
    monkeypatch.setattr(scope.subprocess, 'Popen', forbidden)
    engine = audio.Engine('0', '1', '2')
    monkeypatch.setattr(audio, 'engine', engine)
    args = SimpleNamespace(source='0', sink='1', port='COM3', baud=115200, fmax=3500, no_cat=True)
    w = scope.MainWindow(args)
    w.show()
    qtapp.processEvents()
    yield w
    w.close()
    qtapp.processEvents()


def test_windows_ui_without_linux_commands(window):
    assert window.view_combo.isVisible()
    assert window.pc_output.currentData() == '2'
    assert window.tx_source.currentData() == '3'
    window.pc_volume.setValue(70)
    assert audio.engine.listen_volume == 70
    window.mute_check.setChecked(True)
    assert audio.engine.muted


def test_microphone_failure_does_not_key_radio(window, monkeypatch):
    window.civ = Mock()
    monkeypatch.setattr(audio.engine, 'microphone', Mock(side_effect=RuntimeError('Micro indisponible')))
    window.start_tx()
    window.civ.set_ptt.assert_not_called()
    assert not window.tx_active
    assert 'Micro indisponible' in window.statusBar().currentMessage()


def test_playback_failure_releases_ptt(window, monkeypatch):
    window.civ = Mock()
    window.tx_active = True
    window.tx_playing = 'test.wav'
    monkeypatch.setattr(audio.engine, 'play', Mock(side_effect=RuntimeError('USB débranché')))
    window._play_tx_file()
    window.civ.set_ptt.assert_called_with(False)
    assert not window.tx_active and not audio.engine.muted
    assert 'USB débranché' in window.statusBar().currentMessage()


def test_microphone_loss_releases_ptt(window):
    window.civ = Mock()
    window.tx_active = True
    window.tx_started = time.monotonic()
    window.tx_limit = 180
    window.tx_loopback = Mock(error='Micro débranché')
    window.tx_loopback.poll.return_value = 1
    window.check_tx()
    window.civ.set_ptt.assert_called_with(False)
    assert not window.tx_active


def test_linux_ui_still_initializes(monkeypatch, qtapp):
    import ic7300_scope as scope
    monkeypatch.setattr(scope, 'WINDOWS', False)
    monkeypatch.setattr(scope.QtCore.QThread, 'start', lambda _: None)
    monkeypatch.setattr(scope, 'find_loopback_stream', lambda: None)
    monkeypatch.setattr(scope, 'list_microphones', lambda _: [])
    monkeypatch.setattr(scope, 'sink_volume', lambda _: 50)
    monkeypatch.setattr(scope, 'sink_hw_volume', lambda _: False)
    monkeypatch.setattr(scope, 'STATE_FILE', '/tmp/ic7300-ui-test-state')
    args = SimpleNamespace(source='test', sink='test', port='/dev/null', baud=115200, fmax=3500, no_cat=True)
    w = scope.MainWindow(args)
    w.show()
    qtapp.processEvents()
    assert w.view_combo.isVisible()
    w.audio.stop()
    if w.tx_in_reader: w.tx_in_reader.stop()
    w.hide()
