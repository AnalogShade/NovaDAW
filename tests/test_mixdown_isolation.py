"""Offline exports must not consume playback caches or mutate live native DSP."""
import json
from unittest.mock import patch
import numpy as np
import pytest
import soundfile as sf

from core.audio_engine import AudioEngine
from core.project import Project, Track, MidiClip, MidiNote, AudioClip
from core.ipc.ipc_client import NovaIpcClient


@pytest.fixture
def engine():
    e = AudioEngine(sample_rate=44100, output_device=0, input_device=0)
    e._asio_guard_enabled = False
    e.set_project(Project(name="Export", bpm=120))
    yield e
    e.close()


def add_synth(e):
    track = Track(name="Native", track_type="midi", plugin_path="novadaw.synth")
    clip = MidiClip(start_beat=0, length_beats=4)
    clip.notes = [MidiNote(pitch=60, start_beat=0, duration=2, velocity=90)]
    track.clips.append(clip)
    e.project.add_track(track)
    inst = e.get_native_instrument(track)
    for layer in inst.layers:
        layer.waveform = "sine"
        layer.lfo_enabled = False
    return track, inst


def test_export_ignores_cache_and_preserves_transport_and_dsp(engine, tmp_path):
    track, inst = add_synth(engine)
    cold = tmp_path / "cold.wav"
    engine.export_wav(str(cold), 1)
    inst.render_slice(track.clips[0].notes, 0, .25, 2, 5512, 44100)
    live_delay_index = inst.delay.write_idx
    live_voice_keys = list(inst._voice_svfs)
    live_settings = inst.get_state()
    engine._track_audio_caches[track.id] = dict(
        audio=np.full((88200, 2), .9, dtype=np.float32),
        sample_rate=44100, bpm=120, fully_rendered=True, is_valid=True,
    )
    engine._asio_guard_enabled = True
    engine.is_playing = True
    engine.current_beat = 7.5
    hot = tmp_path / "hot.wav"
    engine.export_wav(str(hot), 1)
    np.testing.assert_array_equal(sf.read(cold)[0], sf.read(hot)[0])
    assert engine.is_playing and engine.current_beat == 7.5
    assert inst.delay.write_idx == live_delay_index
    assert list(inst._voice_svfs) == live_voice_keys
    assert inst.get_state() == live_settings
    assert sf.info(hot).subtype == "PCM_24"


def test_export_is_linear_even_with_playback_loop_and_cache(engine, tmp_path):
    track = Track(name="Ramp", track_type="audio", volume=.8, pan=.25)
    clip = AudioClip(start_beat=0, length_beats=8)
    clip.sample_rate = 44100
    ramp = np.linspace(.01, .2, 176400, dtype=np.float32)
    clip.audio_data = np.column_stack((ramp, ramp))
    track.clips.append(clip)
    engine.project.add_track(track)
    engine.project.loop_enabled = True
    engine.project.loop_start_beat = 0
    engine.project.loop_end_beat = 2
    engine._asio_guard_enabled = True
    engine._track_audio_caches[track.id] = dict(
        audio=clip.audio_data.copy(), sample_rate=44100, bpm=120,
        fully_rendered=True, is_valid=True,
    )
    output = tmp_path / "linear.wav"
    engine.export_wav(str(output), 2)
    actual, sr = sf.read(output)
    expected = np.column_stack((ramp * .8 * .75, ramp * .8)) * engine.master_volume
    np.testing.assert_allclose(actual, np.tanh(expected), atol=2e-7)
    assert len(actual) == 176400 and sr == 44100
    assert engine.project.loop_enabled


def test_export_retains_solo_mute_and_native_master(engine, tmp_path):
    engine.project.ensure_master_track()
    mixer = engine.project.master_track.plugins[0]
    mixer.master_volume = .4
    for value, solo in [(.1, True), (.5, False)]:
        track = Track(track_type="audio", volume=1, soloed=solo)
        clip = AudioClip(start_beat=0, length_beats=4)
        clip.sample_rate = 44100
        clip.audio_data = np.full((88200, 2), value, dtype=np.float32)
        track.clips.append(clip)
        engine.project.add_track(track)
    output = tmp_path / "solo.wav"
    engine.export_wav(str(output), 1)
    actual = sf.read(output)[0]
    np.testing.assert_allclose(actual, np.tanh(.1 * .4), atol=2e-7)
    assert mixer.master_volume == .4


@pytest.mark.parametrize("action,explicit,expected", [
    ("novadaw_export_wav", None, 300.0),
    ("novadaw_get_project_summary", None, 5.0),
    ("novadaw_export_wav", 1.5, 1.5),
])
def test_export_ipc_timeout_does_not_change_fast_commands(action, explicit, expected):
    class Socket:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def settimeout(self, value): assert value == expected
        def sendall(self, value): pass
        def recv(self, size):
            return (json.dumps({"success": True, "result": {"ok": True}}) + "\n").encode()

    with patch("core.ipc.ipc_client.socket.create_connection", return_value=Socket()) as connect:
        assert NovaIpcClient().send_action(action, timeout=explicit) == {"ok": True}
        assert connect.call_args.kwargs["timeout"] == expected
