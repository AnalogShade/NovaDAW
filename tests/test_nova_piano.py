"""Nova Piano sound, native editor, project persistence and MCP integration."""
import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import soundfile as sf
from scipy.integrate import quad

from core.action_registry import action_registry
from core.audio_engine import AudioEngine
from core.mcp.server import mcp
from core.project import MidiClip, MidiNote, Project, Track
from core.serializer import load_project, save_project
from plugins.piano.piano_dsp import HammerDrivenModes, hammer_contact_seconds
from plugins.piano.piano_plugin import NovaPianoPlugin
from ui.plugin_dialogs import NativePluginDialog


def test_hammer_force_sets_displacement_velocity_and_phase():
    duration = hammer_contact_seconds(0.5, 0.7, 0.6)
    assert hammer_contact_seconds(0.5, 0.3, 0.6) > duration
    assert hammer_contact_seconds(0.5, 0.7, 0.2) > duration
    assert hammer_contact_seconds(0.8, 0.7, 0.6) < duration

    omega = 2 * np.pi * np.array([262.0, 1157.0])
    loss = np.array([[1.9, 17.0]])
    modes = HammerDrivenModes(omega, loss, duration, (1,))
    times = duration * np.array([0, 0.0001, 0.31, 0.9999, 1, 1.0001, 2.8, 4.1])
    response = modes.response(times)
    np.testing.assert_array_equal(response[0], 0)
    assert np.max(np.abs(response[1] / times[1])) < 1e-8

    # Independently integrate the applied force against the real oscillator
    # kernel, with probes on both sides of hammer separation.
    def force(t):
        return np.exp(-2 * t / duration) * (1 - np.cos(2 * np.pi * t / duration))

    area = quad(force, 0, duration)[0]
    expected = [
        [
            quad(
                lambda s: force(s) * np.exp(-damping * (t - s)) * np.sin(w * (t - s)) / w,
                0, min(t, duration),
            )[0] / area
            for w, damping in zip(omega, loss[0])
        ]
        for t in times
    ]
    np.testing.assert_allclose(response, expected, rtol=2e-6, atol=1e-10)


def test_editor_audition_strikes_all_notes_at_the_same_time(qapp, monkeypatch):
    piano = NovaPianoPlugin()
    render = Mock(return_value=np.zeros((0, 2), dtype=np.float32))
    monkeypatch.setattr(piano, "render_in_context", render)
    monkeypatch.setattr("plugins.piano.piano_gui.play_plugin_audition", Mock())
    editor = piano.create_editor()
    try:
        editor.audition.click()
        notes = render.call_args.args[0]
        assert len(notes) == 4
        assert {note.start_beat for note in notes} == {0.0}
    finally:
        editor.close()


def test_piano_is_tuned_velocity_sensitive_and_block_invariant():
    piano = NovaPianoPlugin()
    piano.set_state({"reverb_mix": 0})
    sr = 44100
    hard = piano.render_note(69, 0.6, sr, 114)
    soft = piano.render_note(69, 0.6, sr, 40)
    assert hard.dtype == np.float32
    assert np.isfinite(hard).all()
    assert np.max(np.abs(hard)) > 2 * np.max(np.abs(soft))
    segment = hard[sr // 10:sr // 2, 0]
    spectrum = np.abs(np.fft.rfft(segment * np.hanning(len(segment))))
    hz = np.fft.rfftfreq(len(segment), 1 / sr)
    peak = hz[np.argmax(spectrum[(hz >= 425) & (hz < 460)]) + np.searchsorted(hz, 425)]
    assert abs(peak - 440) <= 2.5
    assert np.max(np.abs(hard[int(0.8 * sr):])) < np.max(np.abs(hard[:sr // 10])) * 0.01
    assert not np.allclose(hard[:, 0], hard[:, 1])

    piano.set_state({"reverb_room": "cathedral", "reverb_mix": 0.6})
    notes = [MidiNote(pitch=60, start_beat=0.013, duration=0.58, velocity=105)]
    frames = 27600
    reference = piano.render_in_context(notes, 0, 1, frames, sr, piano.create_isolated_context())
    context = piano.create_isolated_context()
    streamed = np.concatenate([
        piano.render_in_context(notes, start / sr, 1, min(671, frames - start), sr, context)
        for start in range(0, frames, 671)
    ])
    np.testing.assert_allclose(streamed, reference, atol=2e-6)


def test_reverb_rooms_have_distinct_responses_and_cathedral_has_late_tail():
    piano = NovaPianoPlugin()
    sr = 22050
    waves = {}
    for room in ("small_room", "large_room", "concert_hall", "cathedral"):
        piano.set_state({"reverb_room": room, "reverb_mix": 0.7})
        waves[room] = piano.render_note(60, 0.45, sr)
    assert all(np.isfinite(wave).all() for wave in waves.values())
    assert len(waves["cathedral"]) > len(waves["small_room"])
    assert np.max(np.abs(waves["cathedral"][2 * sr:3 * sr])) > 0.001
    for first, second in zip(waves, list(waves)[1:]):
        assert not np.allclose(waves[first][:sr], waves[second][:sr])


def test_hammer_changes_attack_and_table_extends_dry_aftersound():
    piano = NovaPianoPlugin()
    sr = 44100
    piano.set_state({"reverb_mix": 0, "hammer_hardness": 0, "soundboard_resonance": 0})
    soft = piano.render_note(60, 1.3, sr, 108)
    piano.set_state({"hammer_hardness": 1})
    hard = piano.render_note(60, 1.3, sr, 108)

    def attack_centroid(wave):
        attack = wave[110:2100, 0]
        power = np.abs(np.fft.rfft(attack * np.hanning(len(attack)))) ** 2
        return np.average(np.fft.rfftfreq(len(attack), 1 / sr), weights=power)

    assert attack_centroid(hard) > 1.1 * attack_centroid(soft)
    piano.set_state({"hammer_hardness": 0, "soundboard_resonance": 1})
    resonant = piano.render_note(60, 1.3, sr, 108)
    assert np.linalg.norm(resonant[30000:48000]) > 1.25 * np.linalg.norm(soft[30000:48000])


def test_piano_mcp_editor_save_and_audio_routes(qapp, tmp_path, monkeypatch):
    api_tools = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}
    properties = api_tools["novadaw_configure_piano"].inputSchema["properties"]
    assert {"reverb_room", "hammer_hardness", "soundboard_resonance"} <= properties.keys()

    project = Project(bpm=120)
    track = Track(name="Grand", track_type="midi")
    clip = MidiClip(start_beat=0, length_beats=4)
    clip.notes = [MidiNote(pitch=60, start_beat=0, duration=1, velocity=100)]
    track.clips.append(clip)
    project.add_track(track)
    app = SimpleNamespace(project=project, audio_engine=Mock(), set_dirty=Mock())
    configure = action_registry.get("novadaw_configure_piano").func
    assert configure(app, "Grand", reverb_room="unknown")["status"] == "error"
    assert configure(app, "Grand", hammer_hardness=1.5)["status"] == "error"
    assert track.plugins == []
    result = configure(
        app, "Grand", reverb_room="cathedral", reverb_mix=0.6,
        hammer_hardness=0.42, soundboard_resonance=0.71,
    )
    assert result["status"] == "success"
    piano = track.get_plugin(result["plugin_instance_id"])
    assert configure(app, track.id, master_volume=0.9)["plugin_instance_id"] == piano.instance_id
    app.audio_engine.invalidate_track_cache.assert_called_with(track.id, trigger_async=True)
    app.set_dirty.assert_called_with(True)

    dialog = NativePluginDialog(piano, track=track)
    assert dialog.editor.room.currentText() == "Cathédrale"
    hammer_slider, _ = dialog.editor.sliders["hammer_hardness"]
    board_slider, _ = dialog.editor.sliders["soundboard_resonance"]
    assert (hammer_slider.value(), board_slider.value()) == (42, 71)
    hammer_slider.setValue(57)
    board_slider.setValue(62)
    assert piano.get_parameter("hammer_hardness") == 0.57
    assert piano.get_parameter("soundboard_resonance") == 0.62
    dialog.editor.room.setCurrentIndex(dialog.editor.room.findData("concert_hall"))
    assert piano.get_parameter("reverb_room") == "concert_hall"
    dialog.editor.room.setCurrentIndex(dialog.editor.room.findData("cathedral"))
    dialog.close()

    project_file = tmp_path / "piano.ndaw"
    save_project(project, str(project_file))
    restored = load_project(str(project_file))
    restored_track = restored.get_track(track.id)
    restored_piano = restored_track.get_plugin(piano.instance_id)
    assert restored_track.plugin_path == "novadaw.piano"
    assert restored_piano.get_state() == piano.get_state()

    engine = AudioEngine(sample_rate=22050, output_device=0, input_device=0)
    try:
        engine._asio_guard_enabled = False
        engine.set_project(restored)
        # Use the silent stream supplied by conftest, while exercising the live path.
        monkeypatch.delenv("NOVADAW_SILENT_TESTS")
        monkeypatch.setattr(engine, "_stream", SimpleNamespace(active=True, stop=lambda: None, close=lambda: None))
        engine.start_live_note(64, velocity=82, track=restored_track)
        live_step = engine._flush_and_advance_live_native(512, 0.0)
        start, end, groups = live_step
        live, _ = engine._render_track_live_native_audio(
            track.id, restored_track, groups[track.id], start, end, 512, use_isolated_ctx=False
        )
        assert np.max(np.abs(live)) > 0.02
        live_context = engine._live_native_contexts[("novadaw.piano", track.id)][1]
        assert restored_piano._timeline_room.next_sample is None
        cached = engine._render_track_offline(restored_track, 120, 22050, 8)
        assert np.max(np.abs(cached[44100:])) > 0.001
        assert restored_piano._timeline_room.next_sample is None
        wav_path = tmp_path / "piano.wav"
        assert engine.export_wav(str(wav_path), end_bar=2)
        wav, _ = sf.read(wav_path)
        assert np.max(np.abs(wav[44100:])) > 0.001
        assert restored_piano._timeline_room.next_sample is None
        assert live_context.next_sample == 512
        assert engine._live_native_contexts[("novadaw.piano", track.id)][1] is live_context
    finally:
        engine.close()
