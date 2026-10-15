"""Cache scheduling, immutable snapshots and safe partial reads (no audio device)."""
import threading
import numpy as np
import pytest

from core.audio_engine import AudioEngine
from core.project import Project, Track, MidiClip, MidiNote, AudioClip


@pytest.fixture
def engine():
    e = AudioEngine(sample_rate=8000, block_size=64)
    e._asio_guard_enabled = False
    e.set_project(Project(name="Cache", bpm=120))
    yield e
    e.close()


def add_track(engine, name="Track", native=False):
    track = Track(name=name, track_type="midi")
    track.clips.append(MidiClip(start_beat=0, length_beats=4))
    track.clips[0].notes.append(MidiNote(pitch=60, start_beat=0, duration=2, velocity=90))
    if native:
        track.plugin_path = "novadaw.synth"
        engine.get_native_instrument(track)
    engine.project.add_track(track)
    return track


def install_prefix(engine, track, ready=128, total=256):
    audio = np.arange(total * 2, dtype=np.float32).reshape(total, 2)
    engine._track_audio_caches[track.id] = dict(
        audio=audio, sample_rate=8000, bpm=120, is_valid=True,
        fully_rendered=ready == total, rendered_frames=ready,
    )
    return audio


def test_partial_cache_returns_only_written_frames(engine):
    track = add_track(engine)
    audio = install_prefix(engine, track)
    cached = engine._get_cached_track_slice(track, 0, .016, 64, 120)
    np.testing.assert_array_equal(cached, audio[:64])
    cached[:] = -1
    assert audio[0, 0] == 0  # Mixing may change the copy, never the producer buffer.
    assert engine._get_cached_track_slice(track, .024, .040, 64, 120) is None
    assert engine._get_cached_track_slice(track, 0, .016, 64, 121) is None
    engine.sample_rate = 16000
    assert engine._get_cached_track_slice(track, 0, .016, 64, 120) is None


def test_partial_loop_requires_both_sides_ready(engine):
    track = add_track(engine)
    engine.project.loop_enabled = True
    engine.project.loop_start_beat = .008  # frame 32
    engine.project.loop_end_beat = .032  # frame 128
    audio = install_prefix(engine, track, ready=120)
    assert engine._get_cached_track_slice(track, .028, .044, 64, 120) is None
    engine._track_audio_caches[track.id]["rendered_frames"] = 128
    cached = engine._get_cached_track_slice(track, .028, .044, 64, 120)
    np.testing.assert_array_equal(cached, np.concatenate((audio[112:128], audio[32:80])))


def test_round_robin_publishes_every_track_before_completing_first(engine, monkeypatch):
    first, second = add_track(engine, "A"), add_track(engine, "B")
    order = []

    def blocks(track, bpm, sr, end):
        total = int(end / (bpm / 60) * sr)
        buf = np.ones((total, 2), dtype=np.float32)
        order.append(track.name + " prefix")
        yield buf, 64
        assert all(t.id in engine._track_audio_caches for t in (first, second))
        order.append(track.name + " complete")
        yield buf, total

    monkeypatch.setattr(engine, "_iter_track_offline", blocks)
    engine._ensure_tracks_cached()
    assert order == ["A prefix", "B prefix", "A complete", "B complete"]
    assert all(c["fully_rendered"] for c in engine._track_audio_caches.values())


def test_invalidation_cancels_obsolete_publication(engine, monkeypatch):
    track = add_track(engine)

    def blocks(*args):
        buf = np.ones((80000, 2), dtype=np.float32)
        yield buf, 64
        engine.invalidate_track_cache(track.id, trigger_async=False)
        yield buf, len(buf)

    monkeypatch.setattr(engine, "_iter_track_offline", blocks)
    engine._ensure_tracks_cached()
    assert track.id not in engine._track_audio_caches


def test_project_switch_during_render_does_not_publish_old_track(engine, monkeypatch):
    track = add_track(engine)

    def blocks(*args):
        engine.set_project(Project(name="Other"))
        yield np.ones((80000, 2), dtype=np.float32), 64

    monkeypatch.setattr(engine, "_iter_track_offline", blocks)
    engine._ensure_tracks_cached()
    assert track.id not in engine._track_audio_caches


def test_requests_share_one_worker(engine, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def render(force=False):
        calls.append(force)
        entered.set()
        assert release.wait(3)

    monkeypatch.setattr(engine, "_ensure_tracks_cached", render)
    engine._asio_guard_enabled = True
    engine._ensure_tracks_cached_async()
    assert entered.wait(3)
    worker = engine._cache_worker
    try:
        for _ in range(50):
            engine._ensure_tracks_cached_async()
            assert engine._cache_worker is worker
    finally:
        release.set()
        worker.join(3)
    assert not worker.is_alive()
    assert len(calls) == 2  # One running job plus one coalesced request.
    assert engine._cache_worker is None


def test_close_cancels_worker_and_debounce(engine, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    track = add_track(engine)

    def blocks(*args):
        entered.set()
        assert release.wait(3)
        yield np.ones((80000, 2), dtype=np.float32), 64

    monkeypatch.setattr(engine, "_iter_track_offline", blocks)
    engine._asio_guard_enabled = True
    engine._ensure_tracks_cached_async()
    assert entered.wait(3)
    worker = engine._cache_worker
    engine.close()
    release.set()
    worker.join(3)
    assert track.id not in engine._track_audio_caches
    engine._ensure_tracks_cached_async()
    assert engine._cache_worker is None


def test_native_snapshot_preserves_live_instrument_and_notes(engine):
    track = add_track(engine, native=True)
    native = engine.get_native_instrument(track)
    snapshot = engine._snapshot_cache_track(track)
    isolated = engine.get_native_instrument(snapshot)
    assert isolated is not native
    assert isolated.get_state() == native.get_state()
    snapshot.clips[0].notes[0].pitch = 72
    isolated.layers[0].cutoff = 123
    assert track.clips[0].notes[0].pitch == 60
    assert native.layers[0].cutoff != 123


def test_routed_snapshot_and_invalidation(engine):
    instrument = add_track(engine, "Instrument", native=True)
    source = add_track(engine, "Source")
    source.synth_route_track_id = instrument.id
    source.synth_output_bus = 2
    snapshot = engine._snapshot_cache_track(source)
    assert snapshot.synth_route_track_id is None
    assert snapshot.synth_output_bus == 2
    assert engine.get_native_instrument(snapshot).get_state() == engine.get_native_instrument(instrument).get_state()
    install_prefix(engine, instrument)
    install_prefix(engine, source)
    engine.invalidate_track_cache(instrument.id, trigger_async=False)
    assert not engine._track_audio_caches


def test_external_instrument_never_gets_silent_native_cache(engine):
    track = add_track(engine)
    track.plugin_path = "external.vst3"
    engine._ensure_tracks_cached()
    assert track.id not in engine._track_audio_caches


def test_deleted_track_cannot_return_in_cache(engine, monkeypatch):
    track = add_track(engine)

    def blocks(*args):
        engine.project.tracks.remove(track)
        yield np.ones((80000, 2), dtype=np.float32), 64

    monkeypatch.setattr(engine, "_iter_track_offline", blocks)
    engine._ensure_tracks_cached()
    assert track.id not in engine._track_audio_caches


def test_armed_tracks_stay_live(engine):
    track = add_track(engine)
    track.armed = True
    engine._ensure_tracks_cached()
    assert track.id not in engine._track_audio_caches


def test_restarted_job_preserves_unchanged_playable_prefix(engine, monkeypatch):
    track = add_track(engine)
    install_prefix(engine, track)
    engine._track_audio_caches[track.id]["version_hash"] = engine._compute_track_hash(track, 120, 8000)

    def blocks(_track, bpm, sr, end):
        buf = np.ones((int(end / (bpm / 60) * sr), 2), dtype=np.float32)
        yield buf, 64
        assert engine._track_audio_caches[track.id]["rendered_frames"] == 128
        yield buf, len(buf)

    monkeypatch.setattr(engine, "_iter_track_offline", blocks)
    engine._ensure_tracks_cached()
    assert engine._track_audio_caches[track.id]["fully_rendered"]


def test_mixed_audio_midi_is_not_published_before_audio_is_added(engine):
    engine.sample_rate = 44100
    track = add_track(engine, native=True)
    clip = AudioClip(start_beat=0, length_beats=1)
    clip.audio_data = np.full((22050, 2), .1, dtype=np.float32)
    clip.sample_rate = 44100
    track.clips.append(clip)
    snapshot = engine._snapshot_cache_track(track)
    assert snapshot.clips[-1].audio_data is clip.audio_data
    blocks = list(engine._iter_track_offline(snapshot, 120, 44100, 4))
    assert len(blocks) == 1
    assert blocks[0][1] == 88200
    track.clips.pop()
    midi = engine._render_track_offline(engine._snapshot_cache_track(track), 120, 44100, 4)
    np.testing.assert_allclose(blocks[0][0][:22050] - midi[:22050], clip.audio_data * clip.gain, atol=1e-6)


def test_progressive_native_matches_live_synth_render(engine):
    engine.sample_rate = 44100
    track = add_track(engine, native=True)
    native = engine.get_native_instrument(track)
    native.layers[0].waveform = "sine"
    snapshot = engine._snapshot_cache_track(track)
    blocks = list(engine._iter_track_offline(snapshot, 120, 44100, 4))
    assert blocks[0][1] == 4096
    assert blocks[-1][1] == 88200
    live = engine.get_native_instrument(engine._snapshot_cache_track(track))
    expected = np.concatenate([
        live.render_slice(track.clips[0].notes, offset / 44100 * 2,
                          (offset + min(4096, 88200 - offset)) / 44100 * 2,
                          2, min(4096, 88200 - offset), 44100)
        for offset in range(0, 88200, 4096)
    ])
    np.testing.assert_allclose(blocks[-1][0], expected, atol=1e-6)
