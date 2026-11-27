"""Tier 2 Boundary & Corner Case Tests (F1–F6) for NovaDAW."""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

import numpy as np
import pytest

import core.hardware_manager as hm_mod
from core.audio_engine import AudioEngine
from core.project import AutomationLane, MidiClip, MidiNote, Project, Track
from plugins.registry import ensure_plugins_loaded, plugin_registry
from tests.e2e_playback.harness import (
    REPO_ROOT,
    compute_rmse,
    export_wav_array,
    inspect_mit_license_compliance,
    inspect_native_dsp_support,
    measure_callback_timing,
)

ensure_plugins_loaded()


# =============================================================================
# F1 Boundaries: Diagnostic Project Edge Cases
# =============================================================================


def test_f1_boundary_cold_uncached_seek_mid_song_chorus(
    silent_engine_factory, sous_la_cendre_project
):
    proj, _ = sous_la_cendre_project
    eng = silent_engine_factory(sample_rate=44100, block_size=256, asio_guard=False)
    eng.set_project(proj)
    eng.play()
    eng.seek_beat(132.0)

    out = np.zeros((256, 2), dtype=np.float32)
    eng._audio_callback(out, 256, None, None)
    expected_step = (256 / 44100.0) * (148.0 / 60.0)
    assert np.all(np.isfinite(out))
    assert eng.current_beat == pytest.approx(132.0 + expected_step, abs=1e-6)
    eng.pause()


def test_f1_boundary_seek_near_and_past_song_end(
    silent_engine_factory, sous_la_cendre_project
):
    proj, _ = sous_la_cendre_project
    eng = silent_engine_factory(sample_rate=44100, block_size=512, asio_guard=False)
    eng.set_project(proj)
    stats = measure_callback_timing(
        eng, block_size=512, start_beat=351.8, num_blocks=20, warmup_asio_guard=False
    )
    assert stats.non_finite_count == 0
    assert eng.current_beat > 352.0


def test_f1_boundary_extreme_buffer_sizes_64_to_2048(
    silent_engine_factory, sous_la_cendre_project
):
    proj, _ = sous_la_cendre_project
    for buf_size in (64, 128, 1024, 2048):
        eng = silent_engine_factory(sample_rate=44100, block_size=buf_size, asio_guard=False)
        eng.set_project(proj)
        eng.play()
        eng.seek_beat(132.0)
        out = np.zeros((buf_size, 2), dtype=np.float32)
        eng._audio_callback(out, buf_size, None, None)
        eng.pause()
        assert out.shape == (buf_size, 2)
        assert out.dtype == np.float32
        assert np.all(np.isfinite(out))


def test_f1_boundary_ndaw_file_untouched_on_disk(
    silent_engine_factory, sous_la_cendre_path, sous_la_cendre_project
):
    before_stat = sous_la_cendre_path.stat()
    before_sha = hashlib.sha256(sous_la_cendre_path.read_bytes()).hexdigest()

    proj, _ = sous_la_cendre_project
    eng = silent_engine_factory(sample_rate=44100, block_size=256, asio_guard=False)
    eng.set_project(proj)
    measure_callback_timing(eng, block_size=256, start_beat=132.0, num_blocks=5)

    after_stat = sous_la_cendre_path.stat()
    after_sha = hashlib.sha256(sous_la_cendre_path.read_bytes()).hexdigest()
    assert after_stat.st_mtime_ns == before_stat.st_mtime_ns
    assert after_sha == before_sha


def test_f1_boundary_beat_zero_cold_first_block_no_clipping_or_nan(
    silent_engine_factory, sous_la_cendre_project
):
    proj, _ = sous_la_cendre_project
    eng = silent_engine_factory(sample_rate=44100, block_size=128, asio_guard=False)
    eng.set_project(proj)
    eng.play()
    eng.seek_beat(0.0)
    out = np.zeros((128, 2), dtype=np.float32)
    eng._audio_callback(out, 128, None, None)
    eng.pause()
    assert np.all(np.isfinite(out))
    assert float(np.max(np.abs(out))) <= 1.0001


# =============================================================================
# F2 Boundaries: Native Fallback & License Scanner Edge Cases
# =============================================================================


def _disable_scipy_and_native(monkeypatch):
    import plugins.guitar_amp.amp_dsp as amp_dsp_mod
    import plugins.equalizer.equalizer_plugin as eq_mod
    import plugins.compressor.compressor_plugin as comp_mod
    import plugins.guitar.guitar_dsp as guitar_dsp_mod
    import plugins.drum_machine.drum_reverb as drum_rev_mod
    import plugins.synth.synth_dsp as synth_dsp_mod

    monkeypatch.setattr(amp_dsp_mod, "HAS_SCIPY", False)
    monkeypatch.setattr(eq_mod, "HAS_SCIPY", False)
    monkeypatch.setattr(comp_mod, "HAS_SCIPY", False)
    monkeypatch.setattr(guitar_dsp_mod, "HAS_SCIPY", False)
    monkeypatch.setattr(drum_rev_mod, "HAS_SCIPY", False)
    monkeypatch.setattr(synth_dsp_mod, "HAS_SCIPY_SIG", False)

    native_info = inspect_native_dsp_support()
    if native_info["module"] is not None:
        for attr in ("HAS_NATIVE_DSP", "NATIVE_AVAILABLE", "AVAILABLE"):
            if hasattr(native_info["module"], attr):
                monkeypatch.setattr(native_info["module"], attr, False)


def test_f2_boundary_missing_native_dll_and_scipy_fallback_all_dsp(monkeypatch):
    _disable_scipy_and_native(monkeypatch)
    sr = 44100
    t = np.arange(256, dtype=np.float32) / sr
    stereo = np.column_stack(
        [0.3 * np.sin(2.0 * np.pi * 220.0 * t), 0.3 * np.sin(2.0 * np.pi * 220.0 * t)]
    ).astype(np.float32)

    for pid in ("novadaw.guitar_amp", "novadaw.equalizer", "novadaw.compressor", "novadaw.mixer"):
        plug = plugin_registry.create_plugin(pid)
        if pid == "novadaw.equalizer":
            plug.apply_preset("Bass Boost")
        out = plug.process(stereo.copy(), sr)
        assert out.shape == (256, 2)
        assert np.all(np.isfinite(out))

    notes = [MidiNote(pitch=55, start_beat=0.0, duration=1.0, velocity=100)]
    for pid in ("novadaw.guitar", "novadaw.drum_machine", "novadaw.synth"):
        inst = plugin_registry.create_plugin(pid)
        out = inst.render_slice(notes, 0.0, (256 / sr) * 2.0, 2.0, 256, sr)
        assert out.shape == (256, 2)
        assert np.all(np.isfinite(out))


def test_f2_boundary_fallback_vs_reference_rmse_on_impulse_and_sine(monkeypatch):
    import plugins.guitar_amp.amp_dsp as amp_dsp_mod
    import plugins.equalizer.equalizer_plugin as eq_mod

    sr = 44100
    n = 1024
    t = np.arange(n, dtype=np.float32) / sr
    mono = (0.3 * np.sin(2.0 * np.pi * 150.0 * t) + 0.2 * np.sin(2.0 * np.pi * 2400.0 * t)).astype(np.float32)
    mono[1] += 0.5
    stereo = np.column_stack([mono, mono]).astype(np.float32)

    eq_ref = plugin_registry.create_plugin("novadaw.equalizer")
    eq_ref.apply_preset("Bright Air")
    amp_ref = plugin_registry.create_plugin("novadaw.guitar_amp")
    amp_ref.set_state({"distortion": 0.45, "cab_enabled": True})

    ref_eq = eq_ref.process(stereo.copy(), sr)
    ref_amp = amp_ref.process(stereo.copy(), sr)

    monkeypatch.setattr(eq_mod, "HAS_SCIPY", False)
    monkeypatch.setattr(amp_dsp_mod, "HAS_SCIPY", False)
    native_info = inspect_native_dsp_support()
    if native_info["module"] is not None:
        for attr in ("HAS_NATIVE_DSP", "NATIVE_AVAILABLE", "AVAILABLE"):
            if hasattr(native_info["module"], attr):
                monkeypatch.setattr(native_info["module"], attr, False)

    eq_fb = plugin_registry.create_plugin("novadaw.equalizer")
    eq_fb.apply_preset("Bright Air")
    amp_fb = plugin_registry.create_plugin("novadaw.guitar_amp")
    amp_fb.set_state({"distortion": 0.45, "cab_enabled": True})

    fb_eq = eq_fb.process(stereo.copy(), sr)
    fb_amp = amp_fb.process(stereo.copy(), sr)

    assert compute_rmse(ref_eq, fb_eq) < 1e-4
    assert compute_rmse(ref_amp, fb_amp) < 1e-3


def test_f2_boundary_equalizer_fallback_all_five_filter_types(monkeypatch):
    _disable_scipy_and_native(monkeypatch)
    sr = 44100
    t = np.arange(256, dtype=np.float32) / sr
    stereo = np.column_stack(
        [0.4 * np.sin(2.0 * np.pi * 440.0 * t), 0.4 * np.cos(2.0 * np.pi * 440.0 * t)]
    ).astype(np.float32)

    eq = plugin_registry.create_plugin("novadaw.equalizer")
    assert eq._native is None
    eq.set_band_count(5)
    filter_types = ["peaking", "low_shelf", "high_shelf", "low_pass", "high_pass"]
    freqs = [1000.0, 120.0, 8000.0, 6000.0, 100.0]
    for band, ftype, freq in zip(eq.bands, filter_types, freqs):
        band.filter_type = ftype
        band.frequency = freq
        band.gain_db = 4.5
        band.q = 1.2
        band.enabled = True

    out = eq.process(stereo.copy(), sr)
    assert out.shape == (256, 2)
    assert out.dtype == np.float32
    assert np.all(np.isfinite(out))


def test_f2_boundary_compressor_fallback_peak_and_rms_with_sidechain_hpf(monkeypatch):
    _disable_scipy_and_native(monkeypatch)
    sr = 44100
    t = np.arange(512, dtype=np.float32) / sr
    stereo = np.column_stack(
        [0.7 * np.sin(2.0 * np.pi * 110.0 * t), 0.7 * np.sin(2.0 * np.pi * 110.0 * t)]
    ).astype(np.float32)

    for mode in ("peak", "rms"):
        comp = plugin_registry.create_plugin("novadaw.compressor")
        assert comp._c_state is None
        comp.set_state(
            {
                "threshold_db": -24.0,
                "ratio": 6.0,
                "detection_mode": mode,
                "sidechain_hpf_hz": 80.0,
            }
        )
        out = comp.process(stereo.copy(), sr)
        assert out.shape == (512, 2)
        assert out.dtype == np.float32
        assert np.all(np.isfinite(out))


def test_f2_boundary_license_scanner_rejects_simulated_pedalboard_or_non_mit(tmp_path):
    assert inspect_mit_license_compliance(REPO_ROOT)["is_compliant"] is True

    sim_pedal = tmp_path / "sim_pedalboard"
    sim_pedal.mkdir()
    (sim_pedal / "LICENSE").write_text("MIT License\nPermission is hereby granted, free of charge...", encoding="utf-8")
    (sim_pedal / "requirements.txt").write_text("numpy>=1.24.0\npedalboard>=0.9.0\n", encoding="utf-8")
    (sim_pedal / "THIRD_PARTY_LICENSES.md").write_text("Aucune dépendance GPLv3 ou AGPL", encoding="utf-8")
    res_pedal = inspect_mit_license_compliance(sim_pedal)
    assert res_pedal["is_compliant"] is False
    assert "pedalboard" in res_pedal["forbidden_packages"]

    sim_vcs = tmp_path / "sim_vcs_editable"
    sim_vcs.mkdir()
    (sim_vcs / "LICENSE").write_text("MIT License\nPermission is hereby granted, free of charge...", encoding="utf-8")
    (sim_vcs / "requirements.txt").write_text(
        "-e git+https://github.com/spotify/pedalboard.git#egg=pedalboard\n", encoding="utf-8"
    )
    (sim_vcs / "THIRD_PARTY_LICENSES.md").write_text("Aucune dépendance GPLv3 ou AGPL", encoding="utf-8")
    res_vcs = inspect_mit_license_compliance(sim_vcs)
    assert res_vcs["is_compliant"] is False
    assert "pedalboard" in res_vcs["forbidden_packages"]

    sim_ed_eq = tmp_path / "sim_editable_eq"
    sim_ed_eq.mkdir()
    (sim_ed_eq / "LICENSE").write_text("MIT License\nPermission is hereby granted, free of charge...", encoding="utf-8")
    (sim_ed_eq / "requirements.txt").write_text(
        "--editable=https://example.com/juce.git\n", encoding="utf-8"
    )
    (sim_ed_eq / "THIRD_PARTY_LICENSES.md").write_text("Aucune dépendance GPLv3 ou AGPL", encoding="utf-8")
    res_ed_eq = inspect_mit_license_compliance(sim_ed_eq)
    assert res_ed_eq["is_compliant"] is False
    assert "juce" in res_ed_eq["forbidden_packages"]

    sim_gpl = tmp_path / "sim_gpl_license"
    sim_gpl.mkdir()
    (sim_gpl / "LICENSE").write_text("GNU GENERAL PUBLIC LICENSE Version 3", encoding="utf-8")
    (sim_gpl / "requirements.txt").write_text("numpy>=1.24.0\n", encoding="utf-8")
    (sim_gpl / "THIRD_PARTY_LICENSES.md").write_text("No GPLv3", encoding="utf-8")
    res_gpl = inspect_mit_license_compliance(sim_gpl)
    assert res_gpl["is_compliant"] is False
    assert res_gpl["mit_license_present"] is False


# =============================================================================
# F3 Boundaries: Plugin Clamping, Empty/Mono Buffers, Polyphony, Extreme DSP
# =============================================================================


def test_f3_boundary_clamped_out_of_range_plugin_parameters():
    sr = 44100
    stereo = np.full((128, 2), 0.25, dtype=np.float32)

    amp = plugin_registry.create_plugin("novadaw.guitar_amp")
    amp.set_state({"distortion": -5.0, "volume": 10.0})
    assert amp.distortion == pytest.approx(0.0)
    assert amp.volume == pytest.approx(2.0)
    amp.set_state({"distortion": 99.0, "low_db": -99.0, "treble_db": 99.0})
    assert amp.distortion == pytest.approx(1.0)
    assert amp.low_db == pytest.approx(-12.0)
    assert amp.treble_db == pytest.approx(12.0)
    assert np.all(np.isfinite(amp.process(stereo.copy(), sr)))

    eq = plugin_registry.create_plugin("novadaw.equalizer")
    eq.set_state(
        {
            "bands": [
                {"band_id": 0, "frequency": 1.0, "q": 0.01, "gain_db": 100.0, "filter_type": "peaking", "enabled": True},
                {"band_id": 1, "frequency": 99999.0, "q": 100.0, "gain_db": -100.0, "filter_type": "peaking", "enabled": True},
            ]
        }
    )
    b0, a0 = eq.bands[0].compute_coefficients(sr)
    b1, a1 = eq.bands[1].compute_coefficients(sr)
    assert np.all(np.isfinite(b0)) and np.all(np.isfinite(a0))
    assert np.all(np.isfinite(b1)) and np.all(np.isfinite(a1))
    assert np.all(np.isfinite(eq.process(stereo.copy(), sr)))

    guitar = plugin_registry.create_plugin("novadaw.guitar")
    guitar.set_state({"sustain_sec": 999.0})
    assert guitar.sustain_sec == pytest.approx(12.0)

    piano = plugin_registry.create_plugin("novadaw.piano")
    piano.set_state({"master_volume": 50.0})
    assert piano.get_state()["master_volume"] == pytest.approx(2.0)


def test_f3_boundary_empty_and_mono_buffers_on_all_effects():
    sr = 44100
    empty = np.zeros((0, 2), dtype=np.float32)
    mono = np.full((128,), 0.25, dtype=np.float32)

    for pid in ("novadaw.guitar_amp", "novadaw.equalizer", "novadaw.compressor", "novadaw.mixer"):
        plug = plugin_registry.create_plugin(pid)
        if pid == "novadaw.equalizer":
            plug.apply_preset("Vocal Clarity")

        out_empty = plug.process(empty.copy(), sr)
        assert len(out_empty) == 0
        assert np.all(np.isfinite(out_empty))

        out_mono = plug.process(mono.copy(), sr)
        assert out_mono.shape == (128, 2)
        assert np.all(np.isfinite(out_mono))


def test_f3_boundary_heavy_polyphony_saturation_piano_guitar_synth():
    sr = 44100
    bps = 2.0
    frames = 1024
    end_b = (frames / sr) * bps

    guitar = plugin_registry.create_plugin("novadaw.guitar")
    guitar_notes = [MidiNote(pitch=40 + i * 2, start_beat=0.0, duration=1.0, velocity=127) for i in range(12)]
    out_g = guitar.render_slice(guitar_notes, 0.0, end_b, bps, frames, sr)
    assert out_g.shape == (frames, 2)
    assert np.all(np.isfinite(out_g))
    assert float(np.sqrt(np.mean(out_g**2))) > 1e-4
    assert float(np.max(np.abs(out_g))) <= 1.2

    piano = plugin_registry.create_plugin("novadaw.piano")
    piano_notes = [MidiNote(pitch=48 + i * 3, start_beat=0.0, duration=1.0, velocity=127) for i in range(10)]
    out_p = piano.render_slice(piano_notes, 0.0, end_b, bps, frames, sr)
    assert out_p.shape == (frames, 2)
    assert np.all(np.isfinite(out_p))
    assert float(np.sqrt(np.mean(out_p**2))) > 1e-4
    assert float(np.max(np.abs(out_p))) <= 2.0

    synth = plugin_registry.create_plugin("novadaw.synth")
    synth_notes = [MidiNote(pitch=48 + i * 3, start_beat=0.0, duration=1.0, velocity=127) for i in range(8)]
    out_s = synth.render_slice(synth_notes, 0.0, end_b, bps, frames, sr)
    assert out_s.shape == (frames, 2)
    assert np.all(np.isfinite(out_s))
    assert float(np.sqrt(np.mean(out_s**2))) > 1e-4
    assert float(np.max(np.abs(out_s))) <= 1.2


def test_f3_boundary_bypass_and_disabled_instruments():
    sr = 44100
    bps = 2.0
    frames = 256
    end_b = (frames / sr) * bps
    stereo = np.full((128, 2), 0.33, dtype=np.float32)

    for pid in ("novadaw.guitar_amp", "novadaw.equalizer", "novadaw.compressor", "novadaw.mixer"):
        plug = plugin_registry.create_plugin(pid)
        plug.enabled = False
        inp = stereo.copy()
        out = plug.process(inp, sr)
        assert np.array_equal(out, inp)

    notes = [MidiNote(pitch=60, start_beat=0.0, duration=1.0, velocity=110)]
    for pid in ("novadaw.piano", "novadaw.guitar", "novadaw.synth"):
        inst = plugin_registry.create_plugin(pid)
        inst.enabled = False
        out = inst.render_slice(notes, 0.0, end_b, bps, frames, sr)
        assert out.shape == (frames, 2)
        assert float(np.max(np.abs(out))) == pytest.approx(0.0, abs=1e-7)

    drum = plugin_registry.create_plugin("novadaw.drum_machine")
    drum.enabled = False
    drum_in = stereo.copy()
    assert np.array_equal(drum.process(drum_in, sr), drum_in)


def test_f3_boundary_extreme_compressor_settings_no_div_zero():
    sr = 44100
    t = np.arange(256, dtype=np.float32) / sr
    stereo = np.column_stack(
        [0.6 * np.sin(2.0 * np.pi * 440.0 * t), 0.6 * np.sin(2.0 * np.pi * 440.0 * t)]
    ).astype(np.float32)

    comp_wet = plugin_registry.create_plugin("novadaw.compressor")
    comp_wet.set_state(
        {"ratio": 100.0, "attack_ms": 0.0, "release_ms": 0.0, "threshold_db": -80.0, "mix": 1.0}
    )
    out_wet = comp_wet.process(stereo.copy(), sr)
    assert out_wet.shape == (256, 2)
    assert np.all(np.isfinite(out_wet))

    comp_dry = plugin_registry.create_plugin("novadaw.compressor")
    comp_dry.set_state(
        {"ratio": 100.0, "attack_ms": 0.0, "release_ms": 0.0, "threshold_db": -80.0, "mix": 0.0}
    )
    out_dry = comp_dry.process(stereo.copy(), sr)
    assert np.all(np.isfinite(out_dry))
    assert compute_rmse(stereo, out_dry) < 1e-6


# =============================================================================
# F4 Boundaries: Concurrency, Seek Races, Empty Regions, Shutdown, Tiny Slices
# =============================================================================


def test_f4_boundary_sudden_seek_during_active_asio_guard_worker(
    silent_engine_factory, sous_la_cendre_project
):
    proj, _ = sous_la_cendre_project
    eng = silent_engine_factory(sample_rate=44100, block_size=256, asio_guard=True)
    eng.set_project(proj)
    eng.play()

    out = np.zeros((256, 2), dtype=np.float32)
    for beat in (0.0, 200.0, 40.0, 132.0, 12.0):
        eng.seek_beat(beat)
        eng._audio_callback(out, 256, None, None)
        assert np.all(np.isfinite(out))
    eng.pause()


def test_f4_boundary_rapid_invalidation_bursts_thread_safe(
    silent_engine_factory, sous_la_cendre_project
):
    proj, _ = sous_la_cendre_project
    eng = silent_engine_factory(sample_rate=44100, block_size=256, asio_guard=True)
    eng.set_project(proj)
    eng.play()
    eng.seek_beat(132.0)

    out = np.zeros((256, 2), dtype=np.float32)
    amp = proj.tracks[2].plugins[1]
    for i in range(6):
        amp.set_parameter("distortion", 0.2 + 0.1 * i)
        eng.notify_plugin_modified(amp)
        eng.invalidate_track_cache(proj.tracks[3].id, trigger_async=True)
        eng._audio_callback(out, 256, None, None)
        assert np.all(np.isfinite(out))
    eng.pause()


def test_f4_boundary_far_past_song_end_and_all_muted_silence(
    silent_engine_factory, sous_la_cendre_project
):
    proj, _ = sous_la_cendre_project
    eng = silent_engine_factory(sample_rate=44100, block_size=256, asio_guard=False)
    eng.set_project(proj)

    stats_tail = measure_callback_timing(eng, block_size=256, start_beat=1000.0, num_blocks=10)
    assert stats_tail.non_finite_count == 0
    assert stats_tail.peak_abs == pytest.approx(0.0, abs=1e-6)

    for trk in proj.tracks:
        trk.muted = True
    stats_muted = measure_callback_timing(eng, block_size=256, start_beat=132.0, num_blocks=10)
    assert stats_muted.non_finite_count == 0
    assert stats_muted.peak_abs == pytest.approx(0.0, abs=1e-7)


def test_f4_boundary_close_during_active_asio_guard_warmup(
    silent_engine_factory, sous_la_cendre_project
):
    proj, _ = sous_la_cendre_project
    eng = silent_engine_factory(sample_rate=44100, block_size=256, asio_guard=True)
    eng.set_project(proj)
    worker = eng._cache_worker
    time.sleep(0.05)

    t0 = time.perf_counter()
    eng.close()
    if worker is not None and worker.is_alive():
        worker.join(timeout=5.0)
        assert not worker.is_alive()
    assert (time.perf_counter() - t0) < 5.0


def test_f4_boundary_tiny_callback_slices_1_and_16_frames(
    silent_engine_factory, sous_la_cendre_project
):
    proj, _ = sous_la_cendre_project
    for frames in (1, 16):
        eng = silent_engine_factory(sample_rate=44100, block_size=frames, asio_guard=False)
        eng.set_project(proj)
        eng.play()
        eng.seek_beat(132.0)
        out = np.zeros((frames, 2), dtype=np.float32)
        eng._audio_callback(out, frames, None, None)
        eng.pause()
        assert out.shape == (frames, 2)
        assert out.dtype == np.float32
        assert np.all(np.isfinite(out))


# =============================================================================
# F5 Boundaries: HardwareManager & Zero-Config Scheduling Edge Cases
# =============================================================================


def test_f5_boundary_get_optimal_buffer_size_none_and_negative():
    assert hm_mod.hardware_manager.get_optimal_buffer_size(None) == 512
    assert hm_mod.hardware_manager.get_optimal_buffer_size(-1) == 512


def test_f5_boundary_get_optimal_buffer_size_out_of_range_index():
    assert hm_mod.hardware_manager.get_optimal_buffer_size(999999) == 512


def test_f5_boundary_engine_with_none_output_device_preserves_repo_settings(
    isolated_hardware_settings,
):
    repo_settings = REPO_ROOT / "settings.json"
    before_sha = hashlib.sha256(repo_settings.read_bytes()).hexdigest() if repo_settings.is_file() else None

    eng = AudioEngine(sample_rate=44100, block_size=256, output_device=None, input_device=None)
    try:
        assert eng.block_size == 256
    finally:
        eng.close()

    if before_sha is not None:
        after_sha = hashlib.sha256(repo_settings.read_bytes()).hexdigest()
        assert after_sha == before_sha


def test_f5_boundary_configure_device_extreme_block_sizes(silent_engine_factory):
    eng = silent_engine_factory(sample_rate=44100, block_size=256, asio_guard=False)
    assert eng.configure_device(block_size=64) is True
    assert eng.block_size == 64
    assert eng.configure_device(block_size=2048) is True
    assert eng.block_size == 2048


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


def test_f5_boundary_get_optimal_buffer_size_sample_rate_variations():
    default_out = hm_mod.hardware_manager.get_default_output_device_index()
    low_latency = _is_low_latency_driver(default_out)
    for sr in (44100, 48000, 96000):
        buf = hm_mod.hardware_manager.get_optimal_buffer_size(default_out, sr)
        if low_latency:
            assert buf <= 256
        else:
            assert buf in (128, 256, 512)


# =============================================================================
# F6 Boundaries: Empty Projects, Rapid Knobs, Zero Notes, Automation & Export
# =============================================================================


def test_f6_boundary_empty_project_and_zero_clip_track_silence(silent_engine_factory):
    proj = Project.create_empty("EmptySilence")
    proj.bpm = 120.0
    proj.add_track(Track(name="NoClips", track_type="midi"))

    eng = silent_engine_factory(sample_rate=44100, block_size=256, asio_guard=False)
    eng.set_project(proj)
    stats = measure_callback_timing(eng, block_size=256, start_beat=0.0, num_blocks=10)
    assert stats.non_finite_count == 0
    assert stats.peak_abs == pytest.approx(0.0, abs=1e-7)
    assert eng.current_beat > 0.0


def test_f6_boundary_rapid_knob_tweaks_interleaved_with_callbacks(silent_engine_factory):
    proj = Project.create_empty("RapidKnobs")
    proj.bpm = 120.0
    trk = Track(name="Tone", track_type="midi")
    guitar = plugin_registry.create_plugin("novadaw.guitar")
    amp = plugin_registry.create_plugin("novadaw.guitar_amp")
    eq = plugin_registry.create_plugin("novadaw.equalizer")
    trk.add_plugin(guitar)
    trk.add_plugin(amp)
    trk.add_plugin(eq)
    trk.plugin_path = "novadaw.guitar"
    trk.clips.append(
        MidiClip(
            name="Note",
            start_beat=0.0,
            length_beats=8.0,
            notes=[MidiNote(pitch=55, start_beat=0.0, duration=8.0, velocity=115)],
        )
    )
    proj.add_track(trk)

    eng = silent_engine_factory(sample_rate=44100, block_size=256, asio_guard=False)
    eng.set_project(proj)
    eng.play()
    out = np.zeros((256, 2), dtype=np.float32)

    for step in range(20):
        amp.set_parameter("distortion", (step % 10) / 10.0)
        amp.set_parameter("volume", 0.5 + (step % 5) * 0.25)
        eq.bands[0].gain_db = -6.0 + (step % 7) * 2.0
        eng.notify_plugin_modified(amp)
        eng._audio_callback(out, 256, None, None)
        assert np.all(np.isfinite(out))
        assert float(np.max(np.abs(out))) <= 1.0001
    eng.pause()


def test_f6_boundary_zero_note_clip_and_zero_duration_velocity_note(
    silent_engine_factory, tmp_path
):
    proj = Project.create_empty("ZeroNotes")
    proj.bpm = 120.0
    trk = Track(name="EdgeNotes", track_type="midi")
    trk.add_plugin(plugin_registry.create_plugin("novadaw.piano"))
    trk.plugin_path = "novadaw.piano"
    trk.clips.append(MidiClip(name="EmptyClip", start_beat=0.0, length_beats=2.0, notes=[]))
    trk.clips.append(
        MidiClip(
            name="ZeroNote",
            start_beat=2.0,
            length_beats=2.0,
            notes=[MidiNote(pitch=60, start_beat=0.0, duration=0.0, velocity=0)],
        )
    )
    proj.add_track(trk)

    eng = silent_engine_factory(sample_rate=44100, block_size=256, asio_guard=False)
    eng.set_project(proj)
    stats = measure_callback_timing(eng, block_size=256, start_beat=0.0, num_blocks=16)
    assert stats.non_finite_count == 0

    audio, sr = export_wav_array(eng, tmp_path / "zero_notes.wav", end_bar=1)
    assert sr == 44100
    assert np.all(np.isfinite(audio))


def test_f6_boundary_automation_lane_curve_types_clamping_and_disabled(silent_engine_factory):
    lane = AutomationLane(target_param="volume", target_scope="track", enabled=True)
    lane.add_point(0.0, -5.0, curve_type="step")
    lane.add_point(2.0, 99.0, curve_type="linear")
    lane.add_point(4.0, 0.5, curve_type="curve", curve_tension=0.5)

    assert lane.evaluate(0.0) == pytest.approx(0.0)
    assert lane.evaluate(1.0) == pytest.approx(0.0)  # step holds 0.0 until beat 2.0
    assert lane.evaluate(2.0) == pytest.approx(2.0)  # clamped max for volume is 2.0
    mid_curve = lane.evaluate(3.0)
    assert 0.5 <= mid_curve <= 2.0

    # Disabled lane must be ignored by engine automation evaluation
    proj = Project.create_empty("DisabledAuto")
    trk = Track(name="T", track_type="midi", volume=0.77, pan=-0.25)
    lane.enabled = False
    trk.automation_lanes.append(lane)
    proj.add_track(trk)

    eng = silent_engine_factory(sample_rate=44100, block_size=128, asio_guard=False)
    eng.set_project(proj)
    eng.play()
    eng.seek_beat(0.5)
    out = np.zeros((128, 2), dtype=np.float32)
    eng._audio_callback(out, 128, None, None)
    eng.pause()
    live_vals = eng.get_track_live_automation_values(trk.id)
    assert live_vals is not None
    assert live_vals["volume"] == pytest.approx(0.77)
    assert live_vals["pan"] == pytest.approx(-0.25)


def test_f6_boundary_export_wav_proportional_lengths_1_vs_2_bars(
    silent_engine_factory, tmp_path
):
    proj = Project.create_empty("ExportLength")
    proj.bpm = 120.0
    trk = Track(name="Synth", track_type="midi")
    trk.add_plugin(plugin_registry.create_plugin("novadaw.synth"))
    trk.plugin_path = "novadaw.synth"
    trk.clips.append(
        MidiClip(
            name="Bass",
            start_beat=0.0,
            length_beats=8.0,
            notes=[MidiNote(pitch=48, start_beat=0.0, duration=8.0, velocity=100)],
        )
    )
    proj.add_track(trk)

    eng = silent_engine_factory(sample_rate=44100, block_size=256, asio_guard=False)
    eng.set_project(proj)

    a1, sr1 = export_wav_array(eng, tmp_path / "bar1.wav", end_bar=1)
    a2, sr2 = export_wav_array(eng, tmp_path / "bar2.wav", end_bar=2)
    assert sr1 == sr2 == 44100
    assert a1.shape == (88200, 2)
    assert a2.shape == (176400, 2)
    assert len(a2) == 2 * len(a1)
    assert float(np.sqrt(np.mean(a1**2))) > 1e-3
