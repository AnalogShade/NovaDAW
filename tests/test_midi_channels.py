"""Output channel persistence, VST messages and UI/MCP integration (silent)."""
from types import SimpleNamespace
from unittest.mock import Mock
import time
import numpy as np
import pytest
from core.project import Track, Project, MidiClip, MidiNote
from core.serializer import save_project, load_project
from core.audio_engine import AudioEngine
from core.actions.project import set_midi_channel, get_project_summary


@pytest.fixture
def engine():
    e = AudioEngine(sample_rate=8000, block_size=64)
    e._asio_guard_enabled = False
    yield e
    e.close()


def test_roundtrip_legacy_and_duplicate(tmp_path):
    assert Track.from_dict({"name": "Old"}).midi_channel == 1
    p = Project.create_empty()
    t = Track(midi_channel=16)
    p.add_track(t)
    assert p.duplicate_track(t.id).midi_channel == 16
    path = str(tmp_path / "channels.ndaw")
    save_project(p, path)
    assert [t.midi_channel for t in load_project(path).tracks] == [16, 16]


@pytest.mark.parametrize("channel", [1, 2, 10, 16])
def test_vst_playback_preview_and_export(engine, monkeypatch, tmp_path, channel):
    captured = []
    def plugin(messages, **kw):
        captured.extend(m[0] for m in messages)
        return np.zeros((2, round(kw["duration"] * kw["sample_rate"])), dtype=np.float32)
    monkeypatch.setattr(engine, "get_track_plugin", lambda t: plugin)
    monkeypatch.setattr(engine, "_can_render_vst_offline", lambda p: True)
    t = Track(plugin_path="fake.vst3", midi_channel=channel)
    t.clips.append(MidiClip(start_beat=0, length_beats=4, notes=[MidiNote(60, 0, .25, 80)]))
    p = Project.create_empty()
    p.add_track(t)
    engine.set_project(p)
    engine._render_track_slice(t, 0, 1, 4000, 120)
    assert bytes([0x90 | (channel-1), 60, 80]) in captured
    assert bytes([0x80 | (channel-1), 60, 0]) in captured
    captured.clear()
    engine.preview_note(62, track=t)
    assert [m[0] for m in captured] == [0x90 | (channel-1), 0x80 | (channel-1)]
    captured.clear()
    assert engine.export_wav(str(tmp_path / "mix.wav"), end_bar=1)
    assert any(m[0] == 0x90 | (channel-1) for m in captured)
    assert any(m[0] == 0x80 | (channel-1) for m in captured)


def test_live_release_keeps_original_channel(engine, monkeypatch):
    monkeypatch.delenv("NOVADAW_SILENT_TESTS")
    engine._stream = SimpleNamespace(active=True, stop=lambda: None, close=lambda: None)
    monkeypatch.setattr(engine, "get_track_plugin", lambda t: object())
    monkeypatch.setattr(engine, "_can_render_vst_offline", lambda p: True)
    monkeypatch.setattr(engine, "_ensure_live_vst_streamer", lambda: None)
    t = Track(plugin_path="fake.vst3", midi_channel=2)
    engine.start_live_note(60, track=t)
    t.midi_channel = 16
    engine._live_active_notes[(t.id, 60)]["press_time"] = time.time() - 1
    engine.stop_live_note(60, track=t)
    assert [m[0][0] for m in engine._live_midi_queue[t.id]] == [0x91, 0x81]


def test_mcp_validation_cache_and_summary(engine):
    p = Project.create_empty()
    t = Track()
    p.add_track(t)
    app = SimpleNamespace(project=p, audio_engine=SimpleNamespace(invalidate_track_cache=Mock()), set_dirty=Mock(), refresh_project_ui=Mock())
    before = engine._compute_track_hash(t, 120, 8000)
    set_midi_channel(app, t.id, 2)
    assert engine._compute_track_hash(t, 120, 8000) != before
    app.audio_engine.invalidate_track_cache.assert_called_once_with(t.id)
    assert get_project_summary(app)["tracks"][0]["midi_channel"] == 2
    for bad in [0, 17, 2.5, True]:
        with pytest.raises(ValueError):
            set_midi_channel(app, t.id, bad)
    a = Track(track_type="audio")
    p.add_track(a)
    with pytest.raises(ValueError):
        set_midi_channel(app, a.id, 2)


def test_inspector_and_header_channel_sync(qapp):
    from ui.inspector import TrackInspector
    from ui.track_header import TrackHeaderWidget
    p = Project.create_empty()
    t = Track()
    p.add_track(t)
    inspector = TrackInspector(p)
    header = TrackHeaderWidget(t)
    inspector.set_track(t)
    modified = Mock()
    inspector.track_modified.connect(modified)
    inspector.combo_midi_channel.setCurrentIndex(1)
    assert t.midi_channel == 2
    modified.assert_called_once()
    header.sync_controls_from_track()
    assert header.combo_midi_channel.currentData() == 2
    header.combo_midi_channel.setCurrentIndex(15)
    inspector.sync_controls_from_track()
    assert inspector.combo_midi_channel.currentData() == 16
    inspector.set_track(Track(track_type="audio"))
    assert inspector.frame_midi.isHidden()
    inspector.close()
    header.close()


def test_channel_change_releases_previous_sequencer_part(engine, monkeypatch):
    captured = []
    def plugin(messages, **kw):
        captured.extend(m[0] for m in messages)
        return np.zeros((2, round(kw["duration"] * kw["sample_rate"])), dtype=np.float32)
    monkeypatch.setattr(engine, "get_track_plugin", lambda t: plugin)
    monkeypatch.setattr(engine, "_can_render_vst_offline", lambda p: True)
    t = Track(plugin_path="fake.vst3", midi_channel=2)
    engine._render_track_slice(t, 0, 1, 4000, 120)
    t.midi_channel = 16
    engine._render_track_slice(t, 1, 2, 4000, 120)
    assert bytes([0xB1, 123, 0]) in captured
