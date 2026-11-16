"""
tests/test_live_low_latency.py - Validation de l'architecture faible latence temps réel :
- Canal mémoire partagée (_SharedRenderChannel) + rendu bloc direct (render_live_block)
- Détection instantanée de crash du processus VST isolé via WaitForMultipleObjects
- Rendu synchrone sans FIFO 512x3 dans AudioEngine._audio_callback
- Contournement du cache ASIO-Guard pour le jeu live pendant la lecture
- Chemin direct clavier -> AudioEngine sans attente du thread UI Qt
- Support WASAPI Exclusif dans HardwareManager / DeviceSettingsDialog
"""
import multiprocessing
import os
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from core.audio_engine import AudioEngine
from core.hardware_manager import hardware_manager
from core.project import Project, Track
from core.vst_host import (
    IsolatedPlugin,
    _SHM_OP_QUIT,
    _SharedRenderChannel,
    _host,
)
from ui.device_settings_dialog import DeviceSettingsDialog
from ui.virtual_keyboard import VirtualKeyboardDialog


def _dummy_shm_worker(connection, path, probe_only=False, shm_tag=None):
    """Processus VST isolé synthétique supportant le protocole mémoire partagée."""
    import core.vst_host as vh

    class FakeSynth:
        name = "FakeLowLatencySynth"
        is_instrument = True
        is_effect = False
        parameters_count = 0

        def __init__(self):
            self.active = False

        def __call__(self, *args, **kwargs):
            first_arg = args[0] if args else kwargs.get("input_array", kwargs.get("midi_messages"))
            sr = float(kwargs.get("sample_rate", 44100.0))
            if isinstance(first_arg, list) or "duration" in kwargs:
                dur = float(kwargs.get("duration", 0.01))
                frames = max(1, int(round(dur * sr)))
                for msg, _ts in (first_arg or []):
                    if msg and (msg[0] & 0xF0) == 0x90 and msg[2] > 0:
                        self.active = True
                    elif msg and ((msg[0] & 0xF0) == 0x80 or ((msg[0] & 0xF0) == 0x90 and msg[2] == 0)):
                        self.active = False
                val = 0.25 if self.active else 0.0
                return np.full((2, frames), val, dtype=np.float32)
            arr = np.asarray(first_arg, dtype=np.float32)
            return arr * 0.5

        def reset(self):
            self.active = False

        @property
        def raw_state(self):
            return b""

        @raw_state.setter
        def raw_state(self, val):
            pass

        def show_editor(self, close_evt):
            close_evt.wait()

    # Réutiliser le vrai _host en injectant _load_any_plugin
    vh._load_any_plugin = lambda p, probe_only=False: FakeSynth()
    _host(connection, path, probe_only=probe_only, shm_tag=shm_tag)


_dummy_shm_worker._supports_shm = True


def test_shm_isolated_plugin_live_render_and_audio_fx():
    """Vérifie le rendu MIDI et audio via mémoire partagée sans pickle."""
    if sys.platform != "win32":
        pytest.skip("Test Win32 SharedMemory uniquement.")

    plugin = IsolatedPlugin("FakeSynth.vst3", worker=_dummy_shm_worker)
    try:
        assert plugin._shm is not None
        out_stereo = np.zeros((128, 2), dtype=np.float32)
        on_msg = (bytes([0x90, 60, 100]), 0.0)
        ok = plugin.render_live_block([on_msg], 128, 44100, out_stereo)
        assert ok is True
        assert np.allclose(out_stereo, 0.25, atol=1e-5)

        off_msg = (bytes([0x80, 60, 0]), 0.0)
        ok2 = plugin.render_live_block([off_msg], 128, 44100, out_stereo)
        assert ok2 is True
        assert np.allclose(out_stereo, 0.0, atol=1e-5)

        # Test rendu effet audio (op = RENDER_AUDIO via __call__)
        in_audio = np.ones((2, 128), dtype=np.float32) * 0.8
        fx_out = plugin(in_audio, sample_rate=44100, reset=False)
        assert fx_out.shape == (2, 128)
        assert np.allclose(fx_out, 0.4, atol=1e-5)
    finally:
        plugin.dispose()


def test_shm_isolated_plugin_crash_detection():
    """Vérifie que le crash du processus enfant est détecté immédiatement sans blocage."""
    if sys.platform != "win32":
        pytest.skip("Test Win32 SharedMemory uniquement.")

    plugin = IsolatedPlugin("FakeSynth.vst3", worker=_dummy_shm_worker)
    try:
        plugin._process.kill()
        plugin._process.join(timeout=2)
        out_stereo = np.zeros((128, 2), dtype=np.float32)
        with pytest.raises(RuntimeError):
            plugin.render_live_block([], 128, 44100, out_stereo)
    finally:
        plugin.dispose()


def test_audio_engine_direct_live_vst_zero_fifo_and_immediate_note_off(monkeypatch):
    """Vérifie le rendu live synchrone dès le premier bloc (128 éch.) sans FIFO 512x3."""
    if sys.platform != "win32":
        pytest.skip("Test Win32 SharedMemory uniquement.")

    monkeypatch.setenv("NOVADAW_SILENT_TESTS", "1")
    engine = AudioEngine(sample_rate=44100, block_size=128)
    monkeypatch.delenv("NOVADAW_SILENT_TESTS", raising=False)
    engine._stream = SimpleNamespace(active=True, stop=lambda: None, close=lambda: None)

    plugin = IsolatedPlugin("FakeSynth.vst3", worker=_dummy_shm_worker)
    try:
        project = Project(name="LiveDirectTest")
        track = Track(name="Omnisphere", track_type="midi", plugin_path="FakeSynth.vst3")
        project.add_track(track)
        engine.project = project
        monkeypatch.setattr(engine, "get_track_plugin", lambda t: plugin)

        # Démarrer une note live -> doit être audible dès le TOUT PREMIER bloc de 128 échantillons
        engine.start_live_note(60, velocity=100, track=track)
        assert engine._live_direct_active is True

        out = np.zeros((128, 2), dtype=np.float32)
        engine._audio_callback(out, 128, None, None)
        assert float(np.max(np.abs(out))) > 0.05

        # Relâcher immédiatement avec min_gate_sec=0.0 (mode clavier d'ordinateur)
        engine.stop_live_note(60, track=track, min_gate_sec=0.0)
        assert len(engine._live_scheduled_note_offs) == 0

        out2 = np.zeros((128, 2), dtype=np.float32)
        engine._audio_callback(out2, 128, None, None)
        assert np.allclose(out2, 0.0, atol=1e-5)
    finally:
        engine._stream = None
        engine.close()
        plugin.dispose()


def test_audio_engine_live_bypasses_asio_guard_cache_during_playback(monkeypatch):
    """Vérifie qu'une note live jouée pendant la lecture contourne le cache ASIO-Guard."""
    engine = AudioEngine(sample_rate=44100, block_size=128)
    project = Project(name="LiveOverPlay")
    track = Track(name="Synth", track_type="midi", armed=False)
    project.add_track(track)
    engine.project = project

    # Simuler un cache ASIO-Guard silencieux
    engine._track_audio_caches[track.id] = {
        "audio": np.zeros((44100, 2), dtype=np.float32),
        "version_hash": "dummy",
        "bpm": 120.0,
        "sample_rate": 44100,
    }
    monkeypatch.setattr(engine, "_get_cached_track_slice", lambda *a, **kw: np.zeros((128, 2), dtype=np.float32))

    # Sans note live -> cache hit
    assert engine._is_track_live_active(track.id) is False

    # Avec note live en file -> contournement du cache
    engine._live_midi_queue[track.id] = [(bytes([0x90, 60, 100]), 0.0)]
    assert engine._is_track_live_active(track.id) is True
    engine.close()


def test_virtual_keyboard_direct_fast_path_bypasses_qt_loop(qapp):
    """Vérifie que _dispatch_remote_key déclenche start_live_note immédiatement avant processEvents."""
    engine = MagicMock(spec=AudioEngine)
    dlg = VirtualKeyboardDialog(engine)
    track = Track(name="Omnisphere", track_type="midi")
    dlg.set_active_track(track)
    dlg._is_active = True

    # Appui sur 'A' (offset 0 -> pitch 48) depuis un thread externe sans pomper Qt
    dlg._dispatch_remote_key(Qt.Key_A, True)
    engine.start_live_note.assert_called_once_with(48, velocity=dlg.velocity, track=track)

    # Relâchement immédiat (min_gate_sec=0.0)
    dlg._dispatch_remote_key(Qt.Key_A, False)
    engine.stop_live_note.assert_called_once_with(48, track=track, min_gate_sec=0.0)

    # Pomper Qt met ensuite à jour l'état visuel sans double-déclencher l'audio
    qapp.processEvents()
    assert engine.start_live_note.call_count == 1
    assert engine.stop_live_note.call_count == 1
    assert 48 not in dlg.piano_widget.active_pitches
    dlg.close()


def test_wasapi_exclusive_setting_and_dialog(qapp):
    """Vérifie le réglage WASAPI Exclusif dans DeviceSettingsDialog et HardwareManager."""
    engine = AudioEngine()
    dlg = DeviceSettingsDialog(engine)
    assert hasattr(dlg, "chk_wasapi_exclusive")
    dlg.chk_wasapi_exclusive.setChecked(True)
    dlg._on_reset_engine()
    assert engine._wasapi_exclusive is True

    dlg.chk_wasapi_exclusive.setChecked(False)
    dlg._on_reset_engine()
    assert engine._wasapi_exclusive is False
    dlg.close()
    engine.close()
