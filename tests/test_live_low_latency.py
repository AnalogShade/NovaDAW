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
    dlg.chk_streaming_mode.setChecked(False)
    dlg.chk_wasapi_exclusive.setChecked(True)
    dlg._on_reset_engine()
    assert engine._wasapi_exclusive is True

    dlg.chk_wasapi_exclusive.setChecked(False)
    dlg._on_reset_engine()
    assert engine._wasapi_exclusive is False
    dlg.close()
    engine.close()


def test_streaming_mode_wasapi_shared_alignment_and_studio_restore(monkeypatch):
    """
    Vérifie que le Mode Streaming ouvre un flux WASAPI Partagé auto_convert aligné sur le quantum 10 ms
    sans modifier les réglages studio, et restaure exactement la latence studio à la désactivation.
    """
    import core.audio_engine as ae_mod

    opened_kwargs = []

    class FakeWasapiSettings:
        def __init__(self, exclusive=False, auto_convert=False):
            self.exclusive = exclusive
            self.auto_convert = auto_convert

    class FakeOutputStream:
        def __init__(self, **kwargs):
            opened_kwargs.append(dict(kwargs))
            self.samplerate = kwargs.get("samplerate", 44100)
            self.blocksize = kwargs.get("blocksize", 256)
            self.latency = 0.050 if kwargs.get("latency") != "low" else 0.0058
            self.active = False

        def start(self):
            self.active = True

        def stop(self):
            self.active = False

        def close(self):
            self.active = False

    fake_sd = SimpleNamespace(
        WasapiSettings=FakeWasapiSettings,
        OutputStream=FakeOutputStream,
        check_output_settings=lambda **kw: None,
        query_devices=lambda idx=None, kind=None: (
            [{"name": "Haut-parleurs (Avid Eleven Rack)", "hostapi": 0, "max_output_channels": 2, "max_input_channels": 0, "default_samplerate": 44100.0, "default_low_output_latency": 0.003}]
            if idx is None and kind is None
            else {"name": "Haut-parleurs (Avid Eleven Rack)", "hostapi": 0, "max_output_channels": 2, "max_input_channels": 0, "default_samplerate": 44100.0, "default_low_output_latency": 0.003}
        ),
        query_hostapis=lambda idx=None: (
            [{"name": "Windows WASAPI", "devices": [0], "default_output_device": 0, "default_input_device": -1}]
            if idx is None
            else {"name": "Windows WASAPI", "devices": [0], "default_output_device": 0, "default_input_device": -1}
        ),
    )

    monkeypatch.setenv("NOVADAW_SILENT_TESTS", "1")
    engine = AudioEngine(sample_rate=44100, block_size=128, output_device=0)
    monkeypatch.setattr(ae_mod, "HAS_SOUNDDEVICE", True)
    monkeypatch.setattr(ae_mod, "sd", fake_sd)
    monkeypatch.setattr(hardware_manager, "is_wasapi_device", lambda idx: True)
    monkeypatch.setattr(hardware_manager, "resolve_wasapi_output_device", lambda idx: 0)
    monkeypatch.setattr(hardware_manager, "get_optimal_sample_rate", lambda *a, **kw: 44100)
    monkeypatch.setenv("NOVADAW_SILENT_TESTS", "0")

    try:
        # 1. Activer le Mode Streaming alors que wasapi_exclusive=True et block_size=128
        ok = engine.configure_device(
            output_device=0,
            sample_rate=44100,
            block_size=128,
            wasapi_exclusive=True,
            streaming_mode=True,
        )
        assert ok is True
        assert engine.streaming_mode is True
        assert engine.active_stream_mode == "streaming"
        # Les préférences studio doivent rester strictement intactes
        assert engine.block_size == 128
        assert engine._wasapi_exclusive is True
        # Le flux réel ouvert utilise le quantum 10 ms (441 éch. @ 44.1 kHz) + auto_convert sans mode exclusif
        assert engine.effective_block_size == 441
        stream_call = opened_kwargs[-1]
        assert stream_call["blocksize"] == 441
        assert stream_call["latency"] == pytest.approx(hardware_manager.STREAMING_HOST_LATENCY_SEC)
        assert isinstance(stream_call.get("extra_settings"), FakeWasapiSettings)
        assert stream_call["extra_settings"].exclusive is False
        assert stream_call["extra_settings"].auto_convert is True

        # En mode Streaming, le passage en arrière-plan ne coupe jamais le flux
        assert engine.suspend_for_background() is False

        # 2. Désactiver le Mode Streaming -> restauration immédiate du mode WASAPI Exclusif ultra-basse latence (128 éch.)
        ok2 = engine.configure_device(streaming_mode=False)
        assert ok2 is True
        assert engine.streaming_mode is False
        assert engine.active_stream_mode == "wasapi_exclusive"
        assert engine.block_size == 128
        assert engine.effective_block_size == 128
        studio_call = opened_kwargs[-1]
        assert studio_call["blocksize"] == 128
        assert studio_call["latency"] == "low"
        assert isinstance(studio_call.get("extra_settings"), FakeWasapiSettings)
        assert studio_call["extra_settings"].exclusive is True
    finally:
        monkeypatch.setenv("NOVADAW_SILENT_TESTS", "1")
        engine.close()


def test_streaming_mode_dialog_toggle_and_bit_identical_audio_quality(qapp, monkeypatch, tmp_path):
    """
    Vérifie l'ergonomie de chk_streaming_mode dans DeviceSettingsDialog et garantit
    une qualité audio DSP 100 % bit-identique entre le Mode Studio (128 éch.) et le Mode Streaming (441 éch.).
    """
    import core.hardware_manager as hm_mod
    from core.project import MidiClip, MidiNote

    temp_settings = str(tmp_path / "settings.json")
    monkeypatch.setattr(hm_mod, "SETTINGS_FILE", temp_settings)
    saved_backup = dict(hardware_manager.settings.get("audio", {}))

    engine = AudioEngine(sample_rate=44100, block_size=128)
    dlg = DeviceSettingsDialog(engine)
    try:
        assert hasattr(dlg, "chk_streaming_mode")
        dlg.chk_streaming_mode.setChecked(False)
        dlg.chk_wasapi_exclusive.setChecked(True)
        idx_128 = dlg.combo_buffer_size.findData(128)
        if idx_128 >= 0:
            dlg.combo_buffer_size.setCurrentIndex(idx_128)
        dlg._update_latency_label()
        studio_out_txt = dlg.lbl_out_latency.text()

        # Cocher Mode Streaming : suspend visuellement WASAPI Exclusif et Buffer sans perdre leurs valeurs
        dlg.chk_streaming_mode.setChecked(True)
        assert dlg.chk_wasapi_exclusive.isEnabled() is False
        assert dlg.chk_wasapi_exclusive.isChecked() is True
        assert dlg.combo_buffer_size.isEnabled() is False
        assert dlg.lbl_streaming_info.isHidden() is False
        streaming_out_txt = dlg.lbl_out_latency.text()
        assert streaming_out_txt != studio_out_txt
        assert "50.0" in streaming_out_txt

        dlg._on_apply_and_save()
        assert engine.streaming_mode is True
        assert engine._wasapi_exclusive is True
        assert engine.block_size == 128
        assert hardware_manager.settings["audio"]["streaming_mode"] is True
        assert hardware_manager.settings["audio"]["wasapi_exclusive"] is True

        # Décocher Mode Streaming : réactive immédiatement les contrôles et retrouve la latence studio exacte
        dlg.chk_streaming_mode.setChecked(False)
        assert dlg.chk_wasapi_exclusive.isEnabled() is True
        assert dlg.chk_wasapi_exclusive.isChecked() is True
        assert dlg.combo_buffer_size.isEnabled() is True
        assert dlg.lbl_out_latency.text() == studio_out_txt

        # Preuve DSP : rendu d'une piste MIDI identique au bit près entre blocs de 128 (Studio) et 441 (Streaming)
        total_frames = 128 * 441  # Multiple commun exact (56 448 échantillons)
        proj_a = Project(name="QualityA")
        trk_a = Track(name="Lead", track_type="midi")
        clip_a = MidiClip(name="Clip", start_beat=0.0, length_beats=4.0)
        clip_a.notes.append(MidiNote(pitch=60, start_beat=0.0, duration=1.5, velocity=100))
        trk_a.clips.append(clip_a)
        proj_a.add_track(trk_a)

        proj_b = Project(name="QualityB")
        trk_b = Track(name="Lead", track_type="midi")
        clip_b = MidiClip(name="Clip", start_beat=0.0, length_beats=4.0)
        clip_b.notes.append(MidiNote(pitch=60, start_beat=0.0, duration=1.5, velocity=100))
        trk_b.clips.append(clip_b)
        proj_b.add_track(trk_b)

        eng_studio = AudioEngine(sample_rate=44100, block_size=128)
        eng_studio._asio_guard_enabled = False
        eng_studio.set_project(proj_a)
        eng_studio.is_playing = True

        eng_stream = AudioEngine(sample_rate=44100, block_size=128)
        eng_stream.configure_device(streaming_mode=True)
        eng_stream._asio_guard_enabled = False
        eng_stream.set_project(proj_b)
        eng_stream.is_playing = True

        render_studio = np.zeros((total_frames, 2), dtype=np.float32)
        for pos in range(0, total_frames, 128):
            eng_studio._audio_callback(render_studio[pos:pos + 128], 128, None, None)

        render_stream = np.zeros((total_frames, 2), dtype=np.float32)
        for pos in range(0, total_frames, 441):
            eng_stream._audio_callback(render_stream[pos:pos + 441], 441, None, None)

        eng_studio.close()
        eng_stream.close()
        assert float(np.max(np.abs(render_studio))) > 0.05
        assert np.allclose(render_studio, render_stream, atol=1e-5)
    finally:
        hardware_manager.settings["audio"].update(saved_backup)
        dlg.close()
        engine.close()


def test_keyboard_hook_dispatches_asynchronously():
    """Vérifie que LowLevelKeyboardHook exécute les callbacks hors du hook Win32 critique."""
    from core.keyboard_hook import LowLevelKeyboardHook

    hook = LowLevelKeyboardHook()
    received = []
    done = threading.Event()

    def slow_cb(qt_key, is_down):
        received.append((qt_key, is_down, threading.current_thread().name))
        if len(received) >= 2:
            done.set()

    hook._key_callback = slow_cb
    t = threading.Thread(target=hook._dispatch_loop, daemon=True, name="NovaDAW-KeyHook-Dispatcher")
    t.start()
    try:
        hook._event_queue.put(("key", (Qt.Key_A, True)))
        hook._event_queue.put(("key", (Qt.Key_D, True)))
        assert done.wait(timeout=1.5) is True
        assert [(r[0], r[1]) for r in received] == [(Qt.Key_A, True), (Qt.Key_D, True)]
        assert all(r[2] == "NovaDAW-KeyHook-Dispatcher" for r in received)
    finally:
        hook._event_queue.put(None)
        t.join(timeout=1.0)
