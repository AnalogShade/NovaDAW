"""Headless tests for the Nova Piano modal core and its host contract."""

from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

from plugins.piano.piano_dsp import HammerDrivenModes, _hammer_at_bridge, string_modes
from plugins.piano.piano_plugin import NovaPianoPlugin
from plugins.piano.piano_soundboard import BRIDGE_CHANNELS, PianoSoundboard


def test_bridge_contact_projection_preserves_the_modal_phase():
    rate = 44100
    modes = string_modes(36, rate, .67)
    duration = .00176
    drive = .1 * modes.bridge_force
    pressure, separation = _hammer_at_bridge(modes, drive, duration, .36, rate)
    frames = len(pressure)
    broad = HammerDrivenModes(modes.poles.imag, -modes.poles.real[None, :], duration, (.64,))
    core = HammerDrivenModes(modes.poles.imag, -modes.poles.real[None, :], duration * .29, (.36,))
    times = np.arange(frames) / rate
    expected = (broad.response(times) + core.response(times)) @ drive
    np.testing.assert_allclose(pressure, expected, atol=2e-6, rtol=1e-5)
    terminal = broad.displacement_state(frames / rate) + core.displacement_state(frames / rate)
    np.testing.assert_allclose(
        separation, terminal[0] * drive, atol=1e-12
    )


def test_bass_modes_include_stiffness_and_longitudinal_sum_frequencies():
    modes = string_modes(36, 44100, .67)
    frequency = modes.poles.imag / (2 * np.pi)
    assert frequency[modes.primary_count - 1] > 9000
    assert frequency[9] > 1.004 * 10 * frequency[0]
    assert modes.longitudinal is not None
    phantoms = modes.longitudinal
    even_odd = phantoms.bank.poles[:len(phantoms.left)].imag / (2 * np.pi)
    np.testing.assert_allclose(even_odd, frequency[phantoms.left] + frequency[phantoms.right])


def test_soundboard_has_shared_position_dependent_and_continuous_state():
    board = PianoSoundboard(44100)
    force = np.zeros((1337, BRIDGE_CHANNELS), dtype=np.float32)
    force[0, 0] = 1.0
    force[531, -1] = 0.8
    expected = board.process(force, .67)
    board.reset()
    blocked = np.concatenate([
        board.process(force[:93], .67),
        board.process(force[93:712], .67),
        board.process(force[712:], .67),
    ])
    np.testing.assert_allclose(blocked, expected, atol=1e-7)
    assert np.max(np.abs(blocked[150:445])) > 1e-4
    board.reset()
    changed_position = board.process(force[:, ::-1].copy(), .67)
    assert not np.allclose(expected, changed_position)
    assert not np.allclose(expected[:, 0], expected[:, 1])


def test_soundboard_disperses_impulse_and_bass_has_piano_envelope():
    rate = 44100
    board = PianoSoundboard(rate)
    impulse = np.zeros((rate // 4, BRIDGE_CHANNELS), dtype=np.float32)
    impulse[0, 1] = 1.0
    ir = board.process(impulse, 0.67).mean(axis=1)
    # Soundboard must disperse significant energy past the first millisecond
    # instead of passing a raw piezo-like impulse straight through.
    energy_after_1ms = float(np.sum(ir[round(0.001 * rate):] ** 2) / np.sum(ir ** 2))
    assert energy_after_1ms > 0.30

    piano = NovaPianoPlugin()
    piano.set_state({"reverb_mix": 0.0})
    bass = piano.render_note(36, 1.2, rate, 100)[:, 0]
    early_rms = float(np.sqrt(np.mean(bass[round(0.02 * rate):round(0.10 * rate)] ** 2)))
    mid_rms = float(np.sqrt(np.mean(bass[round(0.15 * rate):round(0.45 * rate)] ** 2)))
    # Two-stage piano decay: prompt sound clearly drops into the aftersound.
    assert early_rms > 1.45 * mid_rms
    # No single-millisecond plectrum spike dominating the attack window.
    assert np.argmax(np.abs(bass[:round(0.05 * rate)])) > round(0.003 * rate)


@pytest.mark.parametrize(("pitch", "threshold", "energy_range"), [
    (36, 500, (.11, .36)),
    (60, 1000, (.023, .11)),
    (84, 2000, (.015, .075)),
])
def test_pressure_voicing_is_register_specific(pitch, threshold, energy_range):
    piano = NovaPianoPlugin()
    piano.set_state({"reverb_mix": 0})
    rate = 44100
    segment = piano.render_note(pitch, .45, rate, 80)[round(.02 * rate):round(.12 * rate)]
    bins = np.fft.rfftfreq(len(segment), 1 / rate)
    band = (30 < bins) & (bins < 8000)
    power = np.sum(np.abs(np.fft.rfft(segment * np.hanning(len(segment))[:, None], axis=0))**2, axis=1)
    fraction = power[band & (bins >= threshold)].sum() / power[band].sum()
    assert energy_range[0] < fraction < energy_range[1]


def test_keyboard_has_even_response_without_guitar_wolf_notes_or_dead_keys():
    """Ensure passive soundboard & trichord voicing eliminate wolf peaks and muted guitar plucks."""
    rate = 44100
    board = PianoSoundboard(rate)
    impulse = np.zeros((rate // 2, BRIDGE_CHANNELS), dtype=np.float32)
    impulse[0, 2] = 1.0
    mag_db = 20.0 * np.log10(np.abs(np.fft.rfft(board.process(impulse, 0.67)[:, 0])) + 1e-12)
    freqs = np.fft.rfftfreq(len(impulse), 1.0 / rate)
    mid_band = (freqs >= 65.0) & (freqs <= 4000.0)
    # Passive spruce mobility avoids deep >30 dB comb notches and sharp wolf spikes.
    assert float(mag_db[mid_band].max() - mag_db[mid_band].min()) < 14.0

    piano = NovaPianoPlugin()
    piano.set_state({"reverb_mix": 0.0})
    rms_values = []
    for pitch in range(40, 81):
        wave = piano.render_note(pitch, 0.35, rate, 90).mean(axis=1)
        seg = wave[round(0.02 * rate):round(0.20 * rate)]
        rms_values.append(float(np.sqrt(np.mean(seg ** 2))))
    steps_db = np.abs(20.0 * np.log10(np.array(rms_values[1:]) / np.array(rms_values[:-1])))
    assert float(np.max(steps_db)) < 3.5


def test_host_live_cache_and_export_keep_independent_modal_state(monkeypatch, tmp_path):
    # Exercise the host entry points in-process with no stream or application.
    from core import audio_engine, hardware_manager
    from core.actions.plugins import configure_piano
    from core.project import MidiClip, MidiNote, Project, Track
    from core.serializer import load_project, save_project

    monkeypatch.setenv("NOVADAW_SILENT_TESTS", "1")
    monkeypatch.setattr(audio_engine, "HAS_SOUNDDEVICE", False)
    monkeypatch.setattr(hardware_manager, "HAS_SOUNDDEVICE", False)

    project = Project(bpm=120)
    track = Track(name="Piano", track_type="midi")
    clip = MidiClip(start_beat=0, length_beats=4)
    clip.notes = [MidiNote(pitch=60, start_beat=0, duration=1.2, velocity=99)]
    track.clips.append(clip)
    project.add_track(track)
    result = configure_piano(
        SimpleNamespace(project=project), track.id, reverb_room="cathedral",
        reverb_mix=.48, hammer_hardness=.72, soundboard_resonance=.55,
    )
    assert result["status"] == "success"
    project_path = tmp_path / "piano.ndaw"
    save_project(project, str(project_path))
    restored = load_project(str(project_path))
    track = restored.get_track(track.id)
    piano = track.get_plugin(result["plugin_instance_id"])
    assert piano.get_state() == result["state"]

    rate = 22050
    engine = audio_engine.AudioEngine(sample_rate=rate, block_size=256, output_device=-1, input_device=-1)
    try:
        assert engine._stream is None
        engine._asio_guard_enabled = False
        engine.set_project(restored)
        note = dict(pitch=60, start_beat=0., duration=1.2, velocity=99, live_id=1, native_inst=piano)
        live, owner = engine._render_track_live_native_audio(track.id, track, [note], 0., 512 / rate, 256)
        assert owner is piano
        assert np.max(np.abs(live)) > .025
        context = engine._live_native_contexts[("novadaw.piano", track.id)][1]
        cached = engine._render_track_offline(track, 120, rate, 8)
        np.testing.assert_allclose(live, cached[:256], atol=3e-6)
        assert np.max(np.abs(cached[rate:])) > .001
        output = tmp_path / "piano.wav"
        assert engine.export_wav(str(output), end_bar=2)
        recorded, written_rate = sf.read(output)
        assert written_rate == rate and np.max(np.abs(recorded)) > .04
        assert context.next_sample == 256
        assert piano._timeline_room.next_sample is None
    finally:
        engine.close()
