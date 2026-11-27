"""Tier 1 Feature Tests (F1–F6): Opaque-box requirements verification for NovaDAW."""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

import core.hardware_manager as hm_mod
from core.audio_engine import AudioEngine
from core.project import AutomationLane, MidiClip, MidiNote, Project, Track
from plugins.base import BasePlugin
from plugins.registry import ensure_plugins_loaded, plugin_registry
from tests.e2e_playback.harness import (
    REPO_ROOT,
    compute_rmse,
    export_wav_array,
    inspect_mit_license_compliance,
    inspect_native_dsp_support,
    measure_asio_guard_warmup,
    measure_callback_timing,
)

ensure_plugins_loaded()


# =============================================================================
# F1: Reproducible Diagnostic Reproduction (R1)
# =============================================================================


def test_f1_sous_la_cendre_loads_exact_project_shape(sous_la_cendre_project):
    proj, meta = sous_la_cendre_project
    assert meta["name"] == "Sous la cendre"
    assert meta["bpm"] == pytest.approx(148.0)
    assert meta["time_sig_num"] == 4
    assert meta["time_sig_den"] == 4
    assert meta["total_beats"] == pytest.approx(352.0)
    assert meta["track_count"] == 6
    assert meta["clip_count"] == 46
    assert meta["external_vst_count"] == 0

    for trk in proj.tracks:
        assert trk.muted is False
        assert trk.soloed is False

    expected_plugins = [
        ["novadaw.drum_machine"],
        ["novadaw.guitar", "novadaw.compressor"],
        ["novadaw.guitar", "novadaw.guitar_amp", "novadaw.equalizer"],
        ["novadaw.guitar", "novadaw.guitar_amp", "novadaw.equalizer"],
        ["novadaw.guitar", "novadaw.guitar_amp"],
        ["novadaw.piano", "novadaw.equalizer"],
    ]
    actual_plugins = [[p.plugin_type_id for p in t.plugins] for t in proj.tracks]
    assert actual_plugins == expected_plugins


def test_f1_chorus_multi_buffer_sweep_produces_finite_audio_and_budgets(
    silent_engine_factory, sous_la_cendre_project
):
    proj, _ = sous_la_cendre_project
    results = {}
    for buf_size in (128, 256, 512, 1024):
        eng = silent_engine_factory(sample_rate=44100, block_size=buf_size, asio_guard=False)
        eng.set_project(proj)
        stats = measure_callback_timing(
            eng, block_size=buf_size, start_beat=132.0, num_blocks=16, warmup_asio_guard=False
        )
        results[buf_size] = stats
        assert stats.non_finite_count == 0
        assert stats.rms > 1e-4
        assert stats.peak_abs > 1e-3
        assert stats.budget_ms == pytest.approx((buf_size / 44100.0) * 1000.0, rel=1e-4)

    assert results[128].budget_ms == pytest.approx(2.90249, abs=0.01)
    assert results[1024].budget_ms == pytest.approx(23.21995, abs=0.01)


def test_f1_startup_from_beat_zero_with_asio_guard_produces_valid_audio(
    silent_engine_factory, sous_la_cendre_project
):
    proj, _ = sous_la_cendre_project
    eng = silent_engine_factory(sample_rate=44100, block_size=256, asio_guard=True)
    eng.set_project(proj)
    stats = measure_callback_timing(
        eng, block_size=256, start_beat=0.0, num_blocks=40, warmup_asio_guard=False
    )
    assert stats.non_finite_count == 0
    assert stats.rms > 1e-4
    assert stats.median_ms > 0.0
    assert stats.p95_ms >= stats.median_ms
    assert stats.max_ms >= stats.p95_ms
    assert stats.total_blocks == 40


def test_f1_larger_buffer_amortizes_per_block_cost(
    silent_engine_factory, sous_la_cendre_project
):
    proj, _ = sous_la_cendre_project
    eng_small = silent_engine_factory(sample_rate=44100, block_size=128, asio_guard=False)
    eng_small.set_project(proj)
    # Warm drum samples via one throwaway slice so disk WAV loading doesn't skew 128ms stats.
    measure_callback_timing(eng_small, block_size=128, start_beat=132.0, num_blocks=2, warmup_asio_guard=False)
    stats_128 = measure_callback_timing(
        eng_small, block_size=128, start_beat=132.0, num_blocks=20, warmup_asio_guard=False
    )

    eng_large = silent_engine_factory(sample_rate=44100, block_size=1024, asio_guard=False)
    eng_large.set_project(proj)
    measure_callback_timing(eng_large, block_size=1024, start_beat=132.0, num_blocks=2, warmup_asio_guard=False)
    stats_1024 = measure_callback_timing(
        eng_large, block_size=1024, start_beat=132.0, num_blocks=20, warmup_asio_guard=False
    )

    assert stats_128.non_finite_count == 0
    assert stats_1024.non_finite_count == 0
    assert stats_1024.cpu_percent < stats_128.cpu_percent


def test_f1_uncached_vs_asio_guard_warm_speedup_at_256(
    silent_engine_factory, sous_la_cendre_project
):
    proj, _ = sous_la_cendre_project
    eng_cold = silent_engine_factory(sample_rate=44100, block_size=256, asio_guard=False)
    eng_cold.set_project(proj)
    measure_callback_timing(eng_cold, block_size=256, start_beat=132.0, num_blocks=2, warmup_asio_guard=False)
    uncached = measure_callback_timing(
        eng_cold, block_size=256, start_beat=132.0, num_blocks=25, warmup_asio_guard=False
    )

    eng_warm = silent_engine_factory(sample_rate=44100, block_size=256, asio_guard=True)
    eng_warm._asio_guard_enabled = False
    eng_warm.set_project(proj)
    warm = measure_callback_timing(
        eng_warm,
        block_size=256,
        start_beat=132.0,
        num_blocks=25,
        warmup_asio_guard=True,
        warmup_timeout_sec=90.0,
    )
    assert warm.non_finite_count == 0
    assert warm.rms > 1e-4
    assert warm.median_ms < uncached.median_ms


# =============================================================================
# F2: MIT License Compliance & Native DLL / Pure-Python Fallback
# =============================================================================


def test_f2_root_license_is_mit():
    info = inspect_mit_license_compliance(REPO_ROOT)
    assert info["mit_license_present"] is True
    lic_text = (REPO_ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "MIT License" in lic_text


def test_f2_requirements_and_third_party_zero_gplv3_or_pedalboard():
    info = inspect_mit_license_compliance(REPO_ROOT)
    assert info["forbidden_packages"] == []
    assert info["third_party_no_gplv3"] is True
    assert info["is_compliant"] is True


def test_f2_native_dsp_module_and_dll_available():
    """Verify M1 native C99 DSP loader & nova_dsp.dll are built and active (fails honestly pre-M1)."""
    info = inspect_native_dsp_support()
    assert info["module_file_exists"] is True, "core/dsp_native.py missing (M1 pending)"
    assert info["dll_exists"] is True, f"Native DLL missing at {info['dll_path']} (M1 pending)"
    assert info["native_active"] is True, f"Native DSP not active: {info['import_error']}"


def test_f2_pure_python_fallback_effects_parity(monkeypatch):
    import plugins.guitar_amp.amp_dsp as amp_dsp_mod
    import plugins.equalizer.equalizer_plugin as eq_mod
    import plugins.compressor.compressor_plugin as comp_mod

    sr = 44100
    t = np.arange(512, dtype=np.float32) / sr
    sig = (0.35 * np.sin(2.0 * np.pi * 220.0 * t) + 0.15 * np.sin(2.0 * np.pi * 1800.0 * t)).astype(np.float32)
    stereo_in = np.column_stack([sig, sig]).astype(np.float32)

    amp_ref = plugin_registry.create_plugin("novadaw.guitar_amp")
    eq_ref = plugin_registry.create_plugin("novadaw.equalizer")
    eq_ref.apply_preset("Vocal Clarity")
    comp_ref = plugin_registry.create_plugin("novadaw.compressor")
    comp_ref.set_state({"threshold_db": -20.0, "ratio": 4.0, "sidechain_hpf_hz": 0.0, "detection_mode": "peak"})
    mix_ref = plugin_registry.create_plugin("novadaw.mixer")
    mix_ref.set_state({"master_volume": 0.85, "master_pan": -0.2})

    ref_amp_out = amp_ref.process(stereo_in.copy(), sr)
    ref_eq_out = eq_ref.process(stereo_in.copy(), sr)
    ref_comp_out = comp_ref.process(stereo_in.copy(), sr)
    ref_mix_out = mix_ref.process(stereo_in.copy(), sr)

    monkeypatch.setattr(amp_dsp_mod, "HAS_SCIPY", False)
    monkeypatch.setattr(eq_mod, "HAS_SCIPY", False)
    monkeypatch.setattr(comp_mod, "HAS_SCIPY", False)

    native_info = inspect_native_dsp_support()
    if native_info["module"] is not None:
        for attr in ("HAS_NATIVE_DSP", "NATIVE_AVAILABLE", "AVAILABLE"):
            if hasattr(native_info["module"], attr):
                monkeypatch.setattr(native_info["module"], attr, False)

    amp_fb = plugin_registry.create_plugin("novadaw.guitar_amp")
    eq_fb = plugin_registry.create_plugin("novadaw.equalizer")
    eq_fb.apply_preset("Vocal Clarity")
    comp_fb = plugin_registry.create_plugin("novadaw.compressor")
    comp_fb.set_state({"threshold_db": -20.0, "ratio": 4.0, "sidechain_hpf_hz": 0.0, "detection_mode": "peak"})
    mix_fb = plugin_registry.create_plugin("novadaw.mixer")
    mix_fb.set_state({"master_volume": 0.85, "master_pan": -0.2})

    assert amp_fb._dsp._native is None
    assert eq_fb._native is None
    assert comp_fb._c_state is None

    fb_amp_out = amp_fb.process(stereo_in.copy(), sr)
    fb_eq_out = eq_fb.process(stereo_in.copy(), sr)
    fb_comp_out = comp_fb.process(stereo_in.copy(), sr)
    fb_mix_out = mix_fb.process(stereo_in.copy(), sr)

    for out in (fb_amp_out, fb_eq_out, fb_comp_out, fb_mix_out):
        assert out.shape == stereo_in.shape
        assert np.all(np.isfinite(out))

    assert compute_rmse(ref_amp_out, fb_amp_out) < 1e-3
    assert compute_rmse(ref_eq_out, fb_eq_out) < 1e-4
    assert compute_rmse(ref_comp_out, fb_comp_out) < 1e-4
    assert compute_rmse(ref_mix_out, fb_mix_out) < 1e-4


def test_f2_pure_python_fallback_instruments(monkeypatch):
    import plugins.guitar.guitar_dsp as guitar_dsp_mod
    import plugins.drum_machine.drum_reverb as drum_rev_mod
    import plugins.synth.synth_dsp as synth_dsp_mod

    monkeypatch.setattr(guitar_dsp_mod, "HAS_SCIPY", False)
    monkeypatch.setattr(drum_rev_mod, "HAS_SCIPY", False)
    monkeypatch.setattr(synth_dsp_mod, "HAS_SCIPY_SIG", False)

    native_info = inspect_native_dsp_support()
    if native_info["module"] is not None:
        for attr in ("HAS_NATIVE_DSP", "NATIVE_AVAILABLE", "AVAILABLE"):
            if hasattr(native_info["module"], attr):
                monkeypatch.setattr(native_info["module"], attr, False)

    sr = 44100
    bps = 2.0
    notes = [MidiNote(pitch=60, start_beat=0.0, duration=1.0, velocity=110)]
    drum_notes = [MidiNote(pitch=36, start_beat=0.0, duration=0.5, velocity=110)]

    for pid, n_list in (
        ("novadaw.piano", notes),
        ("novadaw.drum_machine", drum_notes),
        ("novadaw.synth", notes),
        ("novadaw.guitar", notes),
    ):
        inst = plugin_registry.create_plugin(pid)
        out = inst.render_slice(n_list, 0.0, (1024 / sr) * bps, bps, 1024, sr)
        assert out.shape == (1024, 2)
        assert out.dtype == np.float32
        assert np.all(np.isfinite(out))
        assert float(np.sqrt(np.mean(out**2))) > 1e-4


# =============================================================================
# F3: Individual & Multi-Track Playback Budget & Public API/State Parity
# =============================================================================


TARGET_PLUGIN_IDS = [
    "novadaw.guitar_amp",
    "novadaw.equalizer",
    "novadaw.compressor",
    "novadaw.mixer",
    "novadaw.piano",
    "novadaw.guitar",
    "novadaw.drum_machine",
    "novadaw.synth",
]


def test_f3_all_eight_plugins_instantiate_and_roundtrip_state():
    for pid in TARGET_PLUGIN_IDS:
        plug = plugin_registry.create_plugin(pid)
        assert isinstance(plug, BasePlugin)
        assert plug.plugin_type_id == pid

        state = plug.get_state()
        assert isinstance(state, dict)
        plug.set_state(state)

        serialized = plug.to_dict()
        restored = plugin_registry.create_from_dict(serialized)
        assert restored is not None
        assert restored.plugin_type_id == pid
        assert restored.enabled == plug.enabled
        assert set(restored.get_state().keys()) == set(state.keys())


def test_f3_effect_plugins_process_stereo_and_bypass_bit_identical():
    sr = 44100
    t = np.arange(128, dtype=np.float32) / sr
    mono = (0.4 * np.sin(2.0 * np.pi * 440.0 * t)).astype(np.float32)
    stereo = np.column_stack([mono, mono * 0.8]).astype(np.float32)

    for pid in ("novadaw.guitar_amp", "novadaw.equalizer", "novadaw.compressor", "novadaw.mixer"):
        plug = plugin_registry.create_plugin(pid)
        if pid == "novadaw.equalizer":
            plug.apply_preset("Warm Master")
        out_active = plug.process(stereo.copy(), sr)
        assert out_active.shape == (128, 2)
        assert out_active.dtype == np.float32
        assert np.all(np.isfinite(out_active))

        plug.enabled = False
        inp = stereo.copy()
        out_bypass = plug.process(inp, sr)
        assert np.array_equal(out_bypass, inp)


def test_f3_instrument_plugins_render_slice_finite_nonsilent():
    sr = 44100
    bps = 2.0
    frames = 512
    end_b = (frames / sr) * bps
    for pid, pitch in (
        ("novadaw.piano", 60),
        ("novadaw.guitar", 52),
        ("novadaw.drum_machine", 36),
        ("novadaw.synth", 64),
    ):
        inst = plugin_registry.create_plugin(pid)
        assert getattr(inst, "is_instrument", False) is True
        notes = [MidiNote(pitch=pitch, start_beat=0.0, duration=1.0, velocity=110)]
        out = inst.render_slice(notes, 0.0, end_b, bps, frames, sr)
        assert out.shape == (frames, 2)
        assert out.dtype == np.float32
        assert np.all(np.isfinite(out))
        assert float(np.sqrt(np.mean(out**2))) > 1e-4


def test_f3_individual_effect_block_budget_128_samples():
    import time

    sr = 44100
    t = np.arange(128, dtype=np.float32) / sr
    stereo = np.column_stack(
        [0.4 * np.sin(2.0 * np.pi * 330.0 * t), 0.4 * np.cos(2.0 * np.pi * 330.0 * t)]
    ).astype(np.float32)

    for pid in ("novadaw.guitar_amp", "novadaw.equalizer", "novadaw.compressor", "novadaw.mixer"):
        plug = plugin_registry.create_plugin(pid)
        if pid == "novadaw.equalizer":
            plug.apply_preset("Warm Master")
        if pid == "novadaw.guitar_amp":
            plug.set_state({"preset_name": "High Gain Metal", "distortion": 0.85, "cab_enabled": True})

        # One warmup call outside timing
        _ = plug.process(stereo.copy(), sr)
        times_ms = []
        for _ in range(100):
            t0 = time.perf_counter()
            out = plug.process(stereo, sr)
            times_ms.append((time.perf_counter() - t0) * 1000.0)
            assert np.all(np.isfinite(out))

        p95 = float(np.percentile(times_ms, 95))
        assert p95 < 1.0, f"{pid} p95={p95:.3f} ms exceeds 1.0 ms individual block sub-budget"


def test_f3_sous_la_cendre_128_sample_realtime_budget(
    silent_engine_factory, sous_la_cendre_project
):
    proj, _ = sous_la_cendre_project
    eng = silent_engine_factory(sample_rate=44100, block_size=128, asio_guard=True)
    eng._asio_guard_enabled = False
    eng.set_project(proj)

    stats = measure_callback_timing(
        eng,
        block_size=128,
        start_beat=132.0,
        num_blocks=60,
        warmup_asio_guard=True,
        warmup_timeout_sec=90.0,
    )
    assert stats.non_finite_count == 0
    assert stats.rms > 1e-4
    assert stats.p95_ms < stats.budget_ms, (
        f"128-sample p95={stats.p95_ms:.3f} ms >= budget={stats.budget_ms:.3f} ms"
    )
    assert stats.overruns == 0, f"128-sample had {stats.overruns}/{stats.total_blocks} overruns"


# =============================================================================
# F4: Zero-Alloc Callback, ASIO-Guard Warmup, Seek Prioritization & Auto-Bypass
# =============================================================================


def test_f4_asio_guard_full_warmup_under_3_seconds(
    silent_engine_factory, sous_la_cendre_project
):
    proj, _ = sous_la_cendre_project
    eng = silent_engine_factory(sample_rate=44100, block_size=256, asio_guard=True)
    eng._asio_guard_enabled = False
    eng.set_project(proj)

    warmup = measure_asio_guard_warmup(eng, timeout_sec=60.0)
    assert warmup["timed_out"] is False
    assert all(st["fully_rendered"] for st in warmup["track_status"].values())
    assert warmup["elapsed_sec"] < 3.0, (
        f"ASIO-Guard full warmup took {warmup['elapsed_sec']:.2f}s (target < 3.0s)"
    )


def test_f4_startup_256_samples_with_asio_guard_realtime_budget(
    silent_engine_factory, sous_la_cendre_project
):
    proj, _ = sous_la_cendre_project
    eng = silent_engine_factory(sample_rate=44100, block_size=256, asio_guard=True)
    eng.set_project(proj)
    stats = measure_callback_timing(
        eng, block_size=256, start_beat=0.0, num_blocks=60, warmup_asio_guard=False
    )
    assert stats.non_finite_count == 0
    assert stats.rms > 1e-4
    assert stats.p95_ms < stats.budget_ms, (
        f"Startup 256-smp p95={stats.p95_ms:.3f} ms >= budget={stats.budget_ms:.3f} ms"
    )
    assert stats.overruns == 0, f"Startup 256-smp had {stats.overruns}/{stats.total_blocks} overruns"


def test_f4_playhead_prioritized_seek_warmup(
    silent_engine_factory, sous_la_cendre_project
):
    import time

    proj, _ = sous_la_cendre_project
    eng = silent_engine_factory(sample_rate=44100, block_size=256, asio_guard=True)
    eng._asio_guard_enabled = False
    eng.set_project(proj)
    eng.seek_beat(132.0)
    eng._asio_guard_enabled = True
    eng._ensure_tracks_cached_async(force=True)
    # Allow brief background render around playhead without multi-second stall.
    time.sleep(1.5)

    stats = measure_callback_timing(
        eng, block_size=256, start_beat=132.0, num_blocks=30, warmup_asio_guard=False
    )
    assert stats.non_finite_count == 0
    assert stats.rms > 1e-4
    assert stats.p95_ms < stats.budget_ms, (
        f"Seek-prioritized p95={stats.p95_ms:.3f} ms >= budget={stats.budget_ms:.3f} ms"
    )


def test_f4_plugin_modification_invalidates_track_cache_immediately(silent_engine_factory):
    proj = Project.create_empty("CacheInvalidationTest")
    proj.bpm = 120.0
    trk = Track(name="Guitar", track_type="midi")
    guitar = plugin_registry.create_plugin("novadaw.guitar")
    amp = plugin_registry.create_plugin("novadaw.guitar_amp")
    amp.set_state({"distortion": 0.0, "volume": 1.0, "cab_enabled": False})
    trk.add_plugin(guitar)
    trk.add_plugin(amp)
    trk.plugin_path = "novadaw.guitar"
    trk.clips.append(
        MidiClip(
            name="Chord",
            start_beat=0.0,
            length_beats=4.0,
            notes=[MidiNote(pitch=52, start_beat=0.0, duration=4.0, velocity=115)],
        )
    )
    proj.add_track(trk)

    eng = silent_engine_factory(sample_rate=44100, block_size=256, asio_guard=True)
    eng._asio_guard_enabled = False
    eng.set_project(proj)
    warmup = measure_asio_guard_warmup(eng, timeout_sec=30.0)
    assert warmup["timed_out"] is False
    with eng._track_cache_lock:
        pre_cache = eng._track_audio_caches.get(trk.id)
        assert pre_cache is not None and pre_cache.get("fully_rendered") is True

    eng.play()
    eng.seek_beat(0.0)
    buf_clean = np.zeros((256, 2), dtype=np.float32)
    eng._audio_callback(buf_clean, 256, None, None)

    # 1. Insert FX edit: cache must invalidate + generation bump IMMEDIATELY before callback.
    amp.set_state({"distortion": 0.95, "volume": 1.5, "cab_enabled": True})
    gen_before = eng._cache_generation
    sgen_before = getattr(eng, "_state_generation", 0)
    eng.notify_plugin_modified(amp)
    with eng._track_cache_lock:
        post_cache = eng._track_audio_caches.get(trk.id)
        assert (
            post_cache is None
            or not post_cache.get("is_valid", False)
            or not post_cache.get("fully_rendered", False)
        )
    assert (eng._cache_generation > gen_before) or (
        getattr(eng, "_state_generation", 0) > sgen_before
    )

    eng.seek_beat(0.0)
    buf_dirty = np.zeros((256, 2), dtype=np.float32)
    eng._audio_callback(buf_dirty, 256, None, None)
    eng.pause()

    assert np.all(np.isfinite(buf_clean))
    assert np.all(np.isfinite(buf_dirty))
    assert compute_rmse(buf_clean, buf_dirty) > 1e-3

    # 2. Generator edit: re-warm ASIO-Guard, edit guitar, verify immediate invalidation + gen bump.
    warmup_gen = measure_asio_guard_warmup(eng, timeout_sec=30.0)
    assert warmup_gen["timed_out"] is False
    with eng._track_cache_lock:
        pre_gen_cache = eng._track_audio_caches.get(trk.id)
        assert pre_gen_cache is not None and pre_gen_cache.get("fully_rendered") is True

    eng.play()
    eng.seek_beat(0.0)
    buf_gen_before = np.zeros((256, 2), dtype=np.float32)
    eng._audio_callback(buf_gen_before, 256, None, None)

    guitar.set_state({"brightness": 0.15, "palm_mute": 0.85, "master_volume": 0.3})
    gen_before_inst = eng._cache_generation
    sgen_before_inst = getattr(eng, "_state_generation", 0)
    eng.notify_plugin_modified(guitar)
    with eng._track_cache_lock:
        post_gen_cache = eng._track_audio_caches.get(trk.id)
        assert (
            post_gen_cache is None
            or not post_gen_cache.get("is_valid", False)
            or not post_gen_cache.get("fully_rendered", False)
        )
    assert (eng._cache_generation > gen_before_inst) or (
        getattr(eng, "_state_generation", 0) > sgen_before_inst
    )

    eng.seek_beat(0.0)
    buf_gen_after = np.zeros((256, 2), dtype=np.float32)
    eng._audio_callback(buf_gen_after, 256, None, None)
    eng.pause()

    assert np.all(np.isfinite(buf_gen_before))
    assert np.all(np.isfinite(buf_gen_after))
    assert compute_rmse(buf_gen_before, buf_gen_after) > 1e-3


def test_f4_silent_and_muted_tracks_negligible_callback_cost(silent_engine_factory):
    proj = Project.create_empty("SilentIdleTracks")
    for idx in range(6):
        t = Track(name=f"Idle_{idx}", track_type="midi", muted=(idx % 2 == 0))
        t.add_plugin(plugin_registry.create_plugin("novadaw.guitar"))
        t.add_plugin(plugin_registry.create_plugin("novadaw.guitar_amp"))
        t.plugin_path = "novadaw.guitar"
        proj.add_track(t)

    eng = silent_engine_factory(sample_rate=44100, block_size=128, asio_guard=False)
    eng.set_project(proj)
    stats = measure_callback_timing(eng, block_size=128, start_beat=0.0, num_blocks=40)
    assert stats.non_finite_count == 0
    assert stats.peak_abs == pytest.approx(0.0, abs=1e-7)
    assert stats.p95_ms < 0.15, f"Idle/muted tracks p95={stats.p95_ms:.3f} ms >= 0.15 ms"


# =============================================================================
# F5: Zero-Config Optimal Scheduling (R2)
# =============================================================================


def _is_low_latency_driver(device_index) -> bool:
    if hm_mod.hardware_manager.is_wasapi_device(device_index):
        return True
    if hm_mod.HAS_SOUNDDEVICE and device_index is not None and device_index >= 0:
        try:
            d = hm_mod.sd.query_devices(device_index)
            api_info = hm_mod.sd.query_hostapis(d.get("hostapi", 0))
            api_name = api_info.get("name", "").lower()
            return "asio" in api_name or "wasapi" in api_name
        except Exception:
            return False
    return False


def test_f5_hardware_manager_optimal_buffer_low_latency(monkeypatch):
    default_out = hm_mod.hardware_manager.get_default_output_device_index()
    if default_out is not None:
        buf = hm_mod.hardware_manager.get_optimal_buffer_size(default_out, 44100)
        if _is_low_latency_driver(default_out):
            assert buf <= 256
        else:
            assert buf in (128, 256, 512)

    # Also verify WASAPI/ASIO device classification directly via sounddevice query stub
    monkeypatch.setattr(
        hm_mod.sd,
        "query_devices",
        lambda idx: {"hostapi": 0, "default_low_output_latency": 0.005},
    )
    monkeypatch.setattr(hm_mod.sd, "query_hostapis", lambda idx: {"name": "Windows WASAPI"})
    assert hm_mod.hardware_manager.get_optimal_buffer_size(0, 44100) <= 256

    monkeypatch.setattr(hm_mod.sd, "query_hostapis", lambda idx: {"name": "ASIO"})
    assert hm_mod.hardware_manager.get_optimal_buffer_size(0, 44100) <= 256


def test_f5_auto_configure_audio_selects_low_latency_without_manual_inflation(isolated_hardware_settings):
    cfg = hm_mod.hardware_manager.auto_configure_audio(force=True)
    assert isinstance(cfg, dict)
    assert cfg["buffer_size"] <= 256
    assert cfg["sample_rate"] >= 44100
    assert isolated_hardware_settings.is_file()


def test_f5_default_autoconfigured_engine_plays_chorus_without_overruns(
    silent_engine_factory, sous_la_cendre_project
):
    cfg = hm_mod.hardware_manager.auto_configure_audio(force=True)
    buf_size = int(cfg["buffer_size"])
    assert buf_size <= 256

    proj, _ = sous_la_cendre_project
    eng = silent_engine_factory(sample_rate=44100, block_size=buf_size, asio_guard=True)
    eng._asio_guard_enabled = False
    eng.set_project(proj)

    stats = measure_callback_timing(
        eng,
        block_size=buf_size,
        start_beat=132.0,
        num_blocks=50,
        warmup_asio_guard=True,
        warmup_timeout_sec=90.0,
    )
    assert stats.non_finite_count == 0
    assert stats.rms > 1e-4
    assert stats.p95_ms < stats.budget_ms
    assert stats.overruns == 0


def test_f5_repo_settings_json_unmodified_on_disk(isolated_hardware_settings):
    repo_settings = REPO_ROOT / "settings.json"
    existed = repo_settings.is_file()
    before_bytes = repo_settings.read_bytes() if existed else None
    before_sha = hashlib.sha256(before_bytes).hexdigest() if before_bytes is not None else None

    hm_mod.hardware_manager.auto_configure_audio(force=True)
    eng = AudioEngine(sample_rate=44100, block_size=128, output_device=None, input_device=None)
    try:
        eng.close()
    finally:
        pass

    assert repo_settings.is_file() == existed
    if existed:
        after_bytes = repo_settings.read_bytes()
        assert hashlib.sha256(after_bytes).hexdigest() == before_sha


def test_f5_both_128_and_256_meet_realtime_budgets(
    silent_engine_factory, sous_la_cendre_project
):
    proj, _ = sous_la_cendre_project
    for buf_size in (128, 256):
        eng = silent_engine_factory(sample_rate=44100, block_size=buf_size, asio_guard=True)
        eng._asio_guard_enabled = False
        eng.set_project(proj)
        stats = measure_callback_timing(
            eng,
            block_size=buf_size,
            start_beat=132.0,
            num_blocks=40,
            warmup_asio_guard=True,
            warmup_timeout_sec=90.0,
        )
        assert stats.non_finite_count == 0
        assert stats.p95_ms < stats.budget_ms, (
            f"buf={buf_size} p95={stats.p95_ms:.3f} ms >= budget={stats.budget_ms:.3f} ms"
        )
        assert stats.overruns == 0, f"buf={buf_size} overruns={stats.overruns}"


# =============================================================================
# F6: Functional & UX Preservation (R3)
# =============================================================================


def test_f6_transport_lifecycle_play_pause_stop_seek(
    silent_engine_factory, sous_la_cendre_project
):
    proj, _ = sous_la_cendre_project
    eng = silent_engine_factory(sample_rate=44100, block_size=256, asio_guard=False)
    eng.set_project(proj)

    playhead_events = []
    eng.playhead_callback = playhead_events.append

    eng.seek_beat(16.0)
    assert eng.current_beat == pytest.approx(16.0)

    eng.play()
    assert eng.is_playing is True
    out = np.zeros((256, 2), dtype=np.float32)
    eng._audio_callback(out, 256, None, None)
    assert eng.current_beat > 16.0
    beat_after_block = eng.current_beat

    eng.pause()
    assert eng.is_playing is False
    assert eng.current_beat == pytest.approx(beat_after_block)
    eng._audio_callback(out, 256, None, None)
    assert eng.current_beat == pytest.approx(beat_after_block)

    eng.seek_beat(132.0)
    assert eng.current_beat == pytest.approx(132.0)

    eng.stop()
    assert eng.is_playing is False
    assert eng.current_beat == pytest.approx(0.0)
    assert len(playhead_events) >= 3


def test_f6_loop_playback_wraps_current_beat_seamlessly(
    silent_engine_factory, sous_la_cendre_project
):
    proj, _ = sous_la_cendre_project
    proj.loop_enabled = True
    proj.loop_start_beat = 8.0
    proj.loop_end_beat = 10.0

    eng = silent_engine_factory(sample_rate=44100, block_size=512, asio_guard=False)
    eng.set_project(proj)
    eng.play()
    eng.seek_beat(9.95)

    out = np.zeros((512, 2), dtype=np.float32)
    for _ in range(12):
        eng._audio_callback(out, 512, None, None)
        assert np.all(np.isfinite(out))
        assert 8.0 <= eng.current_beat < 10.0
    eng.pause()


def test_f6_solo_and_mute_semantics(silent_engine_factory, sous_la_cendre_project):
    proj, _ = sous_la_cendre_project
    eng = silent_engine_factory(sample_rate=44100, block_size=256, asio_guard=False)
    eng.set_project(proj)

    # Full un-soloed mix at chorus
    eng.play()
    eng.seek_beat(132.0)
    full_mix = np.zeros((256, 2), dtype=np.float32)
    eng._audio_callback(full_mix, 256, None, None)

    # Solo only bass track
    bass = proj.get_track("Basse • Médiator")
    assert bass is not None
    bass.soloed = True
    eng.seek_beat(132.0)
    bass_only = np.zeros((256, 2), dtype=np.float32)
    eng._audio_callback(bass_only, 256, None, None)
    assert float(np.sqrt(np.mean(bass_only**2))) > 1e-4
    assert compute_rmse(full_mix, bass_only) > 1e-3

    # Mute all tracks -> silence
    bass.soloed = False
    for t in proj.tracks:
        t.muted = True
    eng.seek_beat(132.0)
    muted_out = np.zeros((256, 2), dtype=np.float32)
    eng._audio_callback(muted_out, 256, None, None)
    eng.pause()
    assert float(np.max(np.abs(muted_out))) == pytest.approx(0.0, abs=1e-7)


def test_f6_live_knob_editing_updates_audio_immediately(silent_engine_factory):
    proj = Project.create_empty("LiveKnobEdit")
    proj.bpm = 120.0
    trk = Track(name="LeadGuitar", track_type="midi")
    guitar = plugin_registry.create_plugin("novadaw.guitar")
    amp = plugin_registry.create_plugin("novadaw.guitar_amp")
    eq = plugin_registry.create_plugin("novadaw.equalizer")
    amp.set_state({"distortion": 0.0, "volume": 0.6, "cab_enabled": True})
    trk.add_plugin(guitar)
    trk.add_plugin(amp)
    trk.add_plugin(eq)
    trk.plugin_path = "novadaw.guitar"
    trk.clips.append(
        MidiClip(
            name="Riff",
            start_beat=0.0,
            length_beats=4.0,
            notes=[MidiNote(pitch=57, start_beat=0.0, duration=4.0, velocity=115)],
        )
    )
    proj.add_track(trk)

    eng = silent_engine_factory(sample_rate=44100, block_size=256, asio_guard=False)
    eng.set_project(proj)
    eng.play()
    eng.seek_beat(0.0)
    out_before = np.zeros((256, 2), dtype=np.float32)
    eng._audio_callback(out_before, 256, None, None)

    amp.set_parameter("distortion", 0.95)
    amp.set_parameter("volume", 1.8)
    eq.bands[2].gain_db = 12.0
    eng.notify_plugin_modified(amp)
    eng.notify_plugin_modified(eq)

    eng.seek_beat(0.0)
    out_after = np.zeros((256, 2), dtype=np.float32)
    eng._audio_callback(out_after, 256, None, None)
    eng.pause()

    assert np.all(np.isfinite(out_before))
    assert np.all(np.isfinite(out_after))
    assert float(np.max(np.abs(out_after))) <= 1.0001
    assert compute_rmse(out_before, out_after) > 1e-3


def test_f6_automation_lane_and_export_wav_24bit_stereo(silent_engine_factory, tmp_path):
    proj = Project.create_empty("AutoAndExport")
    proj.bpm = 120.0
    trk = Track(name="SynthAuto", track_type="midi", volume=0.8)
    synth = plugin_registry.create_plugin("novadaw.synth")
    trk.add_plugin(synth)
    trk.plugin_path = "novadaw.synth"
    trk.clips.append(
        MidiClip(
            name="Pad",
            start_beat=0.0,
            length_beats=8.0,
            notes=[MidiNote(pitch=60, start_beat=0.0, duration=8.0, velocity=110)],
        )
    )
    lane = AutomationLane(target_param="volume", target_scope="track", enabled=True)
    lane.add_point(0.0, 0.2, curve_type="linear")
    lane.add_point(4.0, 1.0, curve_type="linear")
    trk.automation_lanes.append(lane)
    proj.add_track(trk)

    assert lane.evaluate(0.0) == pytest.approx(0.2)
    assert lane.evaluate(2.0) == pytest.approx(0.6)
    assert lane.evaluate(4.0) == pytest.approx(1.0)

    eng = silent_engine_factory(sample_rate=44100, block_size=256, asio_guard=False)
    eng.set_project(proj)

    eng.play()
    eng.seek_beat(2.0)
    block = np.zeros((256, 2), dtype=np.float32)
    eng._audio_callback(block, 256, None, None)
    eng.pause()
    live_vals = eng.get_track_live_automation_values(trk.id)
    assert live_vals is not None
    assert 0.5 <= live_vals["volume"] <= 0.7

    wav_path = tmp_path / "mixdown_2bars.wav"
    audio, sr = export_wav_array(eng, wav_path, end_bar=2)
    info = sf.info(str(wav_path))
    expected_frames = int((2 * proj.beats_per_bar() / (proj.bpm / 60.0)) * 44100)

    assert sr == 44100
    assert info.subtype == "PCM_24"
    assert audio.shape == (expected_frames, 2)
    assert np.all(np.isfinite(audio))
    assert float(np.sqrt(np.mean(audio**2))) > 1e-3
