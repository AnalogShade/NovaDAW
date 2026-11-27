"""Dedicated unit & stress tests for Milestone M2 AudioEngine optimizations."""
import math
import numpy as np
import pytest

from core.audio_engine import AudioEngine
from core.project import Project, Track, MidiClip, MidiNote
from plugins.base import BasePlugin
from plugins.registry import ensure_plugins_loaded, plugin_registry


@pytest.fixture
def silent_engine():
    ensure_plugins_loaded()
    eng = AudioEngine(sample_rate=44100, block_size=128, output_device=0, input_device=0)
    eng._asio_guard_enabled = False
    yield eng
    eng.close()


def test_warm_callback_zero_fast_inst_signature_and_zero_generator_render(silent_engine, monkeypatch):
    proj = Project.create_empty("M2ZeroPolling")
    proj.bpm = 120.0
    trk = Track(name="GuitarAmpEQ", track_type="midi")
    gtr = plugin_registry.create_plugin("novadaw.guitar")
    amp = plugin_registry.create_plugin("novadaw.guitar_amp")
    eq = plugin_registry.create_plugin("novadaw.equalizer")
    trk.add_plugin(gtr)
    trk.add_plugin(amp)
    trk.add_plugin(eq)
    trk.plugin_path = "novadaw.guitar"
    trk.clips.append(
        MidiClip(
            name="Riff",
            start_beat=0.0,
            length_beats=4.0,
            notes=[MidiNote(pitch=52, start_beat=0.0, duration=3.5, velocity=110)],
        )
    )
    proj.add_track(trk)

    silent_engine.set_project(proj)
    silent_engine._asio_guard_enabled = True
    silent_engine._ensure_tracks_cached_async(force=True)
    assert silent_engine.wait_for_track_cache(timeout=15.0) is True
    assert silent_engine._track_audio_caches[trk.id]["fully_rendered"] is True

    sig_calls = 0
    orig_sig = silent_engine._fast_inst_signature

    def counted_sig(t):
        nonlocal sig_calls
        sig_calls += 1
        return orig_sig(t)

    monkeypatch.setattr(silent_engine, "_fast_inst_signature", counted_sig)

    gen_calls = 0
    orig_gen = silent_engine._render_track_generator_slice

    def counted_gen(*args, **kwargs):
        nonlocal gen_calls
        gen_calls += 1
        return orig_gen(*args, **kwargs)

    monkeypatch.setattr(silent_engine, "_render_track_generator_slice", counted_gen)

    silent_engine.play()
    silent_engine.seek_beat(0.0)
    out = np.zeros((128, 2), dtype=np.float32)
    for _ in range(20):
        silent_engine._audio_callback(out, 128, None, None)
    silent_engine.pause()

    assert sig_calls == 0, "Warm _audio_callback must make 0 calls to _fast_inst_signature"
    assert gen_calls == 0, "Warm _audio_callback must read 100% from pre-allocated ASIO-Guard cache"
    assert float(np.sqrt(np.mean(out.astype(np.float64) ** 2))) > 1e-4


def test_isolated_preview_does_not_mutate_live_native_fx_state(silent_engine):
    proj = Project.create_empty("M2IsolatedFX")
    trk = Track(name="LiveChain", track_type="midi")
    gtr = plugin_registry.create_plugin("novadaw.guitar")
    amp = plugin_registry.create_plugin("novadaw.guitar_amp")
    eq = plugin_registry.create_plugin("novadaw.equalizer")
    eq.bands[2].gain_db = 8.0
    comp = plugin_registry.create_plugin("novadaw.compressor")
    comp.set_state({"threshold_db": -24.0, "ratio": 6.0})
    trk.add_plugin(gtr)
    trk.add_plugin(amp)
    trk.add_plugin(eq)
    trk.add_plugin(comp)
    trk.plugin_path = "novadaw.guitar"
    proj.add_track(trk)
    silent_engine.set_project(proj)

    # Establish deterministic live FX filter state across two consecutive blocks
    rng = np.random.default_rng(42)
    b1 = (rng.standard_normal((256, 2)) * 0.25).astype(np.float32)
    b2 = (rng.standard_normal((256, 2)) * 0.25).astype(np.float32)

    silent_engine._reset_all_plugins()
    _ = silent_engine._process_track_effects_chain(trk, b1.copy(), 0.0, 0.02, 256, 120.0, native_inst=gtr)
    ref_b2 = silent_engine._process_track_effects_chain(trk, b2.copy(), 0.02, 0.04, 256, 120.0, native_inst=gtr)

    silent_engine._reset_all_plugins()
    silent_engine.is_playing = True
    _ = silent_engine._process_track_effects_chain(trk, b1.copy(), 0.0, 0.02, 256, 120.0, native_inst=gtr)
    # Trigger heavy isolated preview in the middle of live playback
    loud_preview = np.full((2048, 2), 0.9, dtype=np.float32)
    _ = silent_engine._process_track_effects_isolated(trk, loud_preview, gtr, reset_clones=True)
    actual_b2 = silent_engine._process_track_effects_chain(trk, b2.copy(), 0.02, 0.04, 256, 120.0, native_inst=gtr)
    silent_engine.is_playing = False

    np.testing.assert_allclose(actual_b2, ref_b2, atol=1e-6)


def test_master_soft_limiter_c1_continuity_and_nan_inf_guard(silent_engine):
    class PoisonPlugin(BasePlugin):
        def __init__(self):
            super().__init__("novadaw.poison_test", "Poison", "effect")
            self.mode = "ramp"

        def process(self, audio, sample_rate):
            n = len(audio)
            if self.mode == "nan":
                bad = audio.copy()
                bad[0, 0] = np.nan
                bad[1, 1] = np.inf
                bad[2, 0] = -np.inf
                return bad
            # Continuous ramp crossing 0.85 threshold from 0.80 -> 1.20
            ramp = np.linspace(0.80, 1.20, n, dtype=np.float32)
            return np.column_stack((ramp, -ramp))

        def get_state(self):
            return {}

        def set_state(self, state):
            pass

    proj = Project.create_empty("LimiterTest")
    master = proj.ensure_master_track()
    master.plugins.clear()
    poison = PoisonPlugin()
    master.plugins.append(poison)
    silent_engine.master_volume = 1.0
    silent_engine.set_project(proj)

    out = np.zeros((256, 2), dtype=np.float32)
    silent_engine._audio_callback(out, 256, None, None)
    assert np.all(np.isfinite(out))
    assert float(np.max(np.abs(out))) <= 1.00001
    # Verify C1 smoothness across 0.85 (max adjacent step on 256-pt ramp <= ~0.002)
    diffs = np.abs(np.diff(out[:, 0]))
    assert float(np.max(diffs)) < 0.005

    poison.mode = "nan"
    silent_engine._audio_callback(out, 256, None, None)
    assert np.all(np.isfinite(out))
    assert float(np.max(np.abs(out))) <= 1.00001
