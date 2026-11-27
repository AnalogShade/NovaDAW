"""Tier 4 Real-World Application Scenarios (S1–S6, E2E-M2) on 'Sous la cendre'."""

from __future__ import annotations

import time
from typing import List

import numpy as np
import pytest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QScrollArea, QWidget

import core.hardware_manager as hm_mod
from plugins.mixer.mixer_gui import MixerConsoleWidget
from plugins.registry import ensure_plugins_loaded
from tests.e2e_playback.harness import (
    compute_rmse,
    export_wav_array,
    inspect_native_dsp_support,
    load_sous_la_cendre,
    measure_asio_guard_warmup,
    measure_callback_timing,
)
from ui.piano_roll import NoteGridWidget
from ui.timeline_view import TimelineGrid, TimelineRuler

ensure_plugins_loaded()


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


def _assert_rendered_image_has_content(img: QImage, min_distinct_colors: int = 3) -> None:
    assert not img.isNull()
    w = img.width()
    h = img.height()
    assert w > 0 and h > 0
    step_x = max(1, w // 24)
    step_y = max(1, h // 24)
    colors = set()
    for x in range(0, w, step_x):
        for y in range(0, h, step_y):
            colors.add(img.pixel(x, y))
            if len(colors) >= min_distinct_colors:
                return
    assert len(colors) >= min_distinct_colors, (
        f"Expected rendered widget image to have >= {min_distinct_colors} distinct colors, got {len(colors)}"
    )


def _autoscroll_widgets(
    beat: float,
    grid: TimelineGrid,
    tl_scroll: QScrollArea,
    ng: NoteGridWidget,
    pr_scroll: QScrollArea,
) -> None:
    playhead_x = int(beat * grid.pixels_per_beat)
    h_bar = tl_scroll.horizontalScrollBar()
    vp_w = tl_scroll.viewport().width()
    cur_scroll = h_bar.value()
    if playhead_x >= (cur_scroll + vp_w - 40) or playhead_x < cur_scroll:
        h_bar.setValue(max(0, playhead_x - int(vp_w * 0.12)))

    clip = getattr(ng, "clip", None)
    if clip is not None and (clip.start_beat <= beat <= (clip.start_beat + clip.length_beats)):
        pr_px = int((beat - clip.start_beat) * ng.pixels_per_beat)
        pr_hbar = pr_scroll.horizontalScrollBar()
        pr_vp_w = max(100, pr_scroll.viewport().width())
        pr_scroll_val = pr_hbar.value()
        if pr_px >= (pr_scroll_val + pr_vp_w - 40) or pr_px < pr_scroll_val:
            pr_hbar.setValue(max(0, pr_px - int(pr_vp_w * 0.12)))


def test_t4_s1_chorus_studio_default_128_samples(
    silent_engine_factory, sous_la_cendre_project
):
    """S1: Chorus Studio Default (128 smp, 44100 Hz, budget 2.902 ms)."""
    proj, _ = sous_la_cendre_project
    eng = silent_engine_factory(sample_rate=44100, block_size=128, asio_guard=True)
    eng._asio_guard_enabled = False
    eng.set_project(proj)

    stats = measure_callback_timing(
        eng,
        block_size=128,
        start_beat=132.0,
        num_blocks=80,
        warmup_asio_guard=True,
        warmup_timeout_sec=60.0,
    )
    assert stats.non_finite_count == 0
    assert stats.rms > 1e-4
    assert stats.peak_abs <= 1.0001
    assert stats.p95_ms < stats.budget_ms, (
        f"S1 128-smp p95={stats.p95_ms:.3f} ms >= budget={stats.budget_ms:.3f} ms"
    )
    assert stats.overruns == 0, f"S1 128-smp had {stats.overruns}/{stats.total_blocks} overruns"


def test_t4_s2_default_autoconfigured_playback_256_samples(
    silent_engine_factory, sous_la_cendre_project, isolated_hardware_settings
):
    """S2: Default Auto-Configured Playback (256 smp, 44100 Hz, budget 5.805 ms)."""
    cfg = hm_mod.hardware_manager.auto_configure_audio(force=True)
    assert int(cfg["sample_rate"]) >= 44100
    default_out = hm_mod.hardware_manager.get_default_output_device_index()
    if _is_low_latency_driver(default_out):
        assert int(cfg["buffer_size"]) <= 256
    else:
        assert int(cfg["buffer_size"]) in (128, 256, 512)

    proj, _ = sous_la_cendre_project
    eng = silent_engine_factory(sample_rate=44100, block_size=256, asio_guard=True)
    eng._asio_guard_enabled = False
    eng.set_project(proj)

    stats = measure_callback_timing(
        eng,
        block_size=256,
        start_beat=132.0,
        num_blocks=80,
        warmup_asio_guard=True,
        warmup_timeout_sec=60.0,
    )
    assert stats.non_finite_count == 0
    assert stats.rms > 1e-4
    assert stats.p95_ms < stats.budget_ms, (
        f"S2 256-smp p95={stats.p95_ms:.3f} ms >= budget={stats.budget_ms:.3f} ms"
    )
    assert stats.overruns == 0, f"S2 256-smp had {stats.overruns}/{stats.total_blocks} overruns"


def test_t4_s3_startup_to_chorus_with_active_qt_ui(
    qapp,
    qt_widget_cleanup,
    silent_engine_factory,
    sous_la_cendre_project,
    monkeypatch,
):
    """S3: Continuous startup-to-chorus (beat 0.0 -> 132.0+) with active Qt UI & 30/60 Hz ticks."""
    proj, _ = sous_la_cendre_project
    eng = silent_engine_factory(sample_rate=44100, block_size=256, asio_guard=True)
    eng.set_project(proj)

    ruler = qt_widget_cleanup(TimelineRuler())
    ruler.resize(1200, 30)
    ruler.set_loop(proj.loop_enabled, proj.loop_start_beat, proj.loop_end_beat)
    ruler.show()

    tl_scroll = qt_widget_cleanup(QScrollArea())
    tl_scroll.setWidgetResizable(True)
    grid = TimelineGrid(proj)
    tl_scroll.setWidget(grid)
    tl_scroll.resize(1200, 600)
    tl_scroll.horizontalScrollBar().valueChanged.connect(ruler.set_scroll_offset)
    tl_scroll.show()

    clip_intro = proj.tracks[5].clips[0]   # 0..32
    clip_riff = proj.tracks[0].clips[1]    # 32..96
    clip_montee = proj.tracks[5].clips[1]  # 96..128
    clip_chorus = proj.tracks[5].clips[2]  # 128..192

    pr_scroll = qt_widget_cleanup(QScrollArea())
    pr_scroll.setWidgetResizable(True)
    ng = NoteGridWidget(start_pitch=0, num_pitches=128, row_height=18)
    ng.set_clip(clip_intro)
    pr_scroll.setWidget(ng)
    pr_scroll.resize(1200, 360)
    pr_scroll.verticalScrollBar().setValue(ng.y_for_pitch(72))
    pr_scroll.show()

    mixer_p = next(
        p
        for p in proj.ensure_master_track().plugins
        if getattr(p, "plugin_type_id", None) == "novadaw.mixer"
    )
    console = qt_widget_cleanup(MixerConsoleWidget(mixer_p))
    console.meter_timer.stop()
    console.resize(1200, 360)
    console.show()
    qapp.processEvents()

    css_calls = 0
    orig_set_ss = QWidget.setStyleSheet

    def spy_ss(self, ss):
        nonlocal css_calls
        css_calls += 1
        return orig_set_ss(self, ss)

    monkeypatch.setattr(QWidget, "setStyleSheet", spy_ss)

    block_size = 256
    budget_ms = (block_size / 44100.0) * 1000.0
    out = np.zeros((block_size, 2), dtype=np.float32)
    audio_times_ms: List[float] = []
    ui_tick_times_ms: List[float] = []
    non_finite_count = 0
    sum_sq = 0.0
    total_scalars = 0
    current_section = 0

    eng.play()
    eng.seek_beat(0.0)
    blk_idx = 0
    try:
        while eng.current_beat < 132.5 and blk_idx < 10500:
            blk_idx += 1
            out.fill(0.0)
            t0 = time.perf_counter()
            eng._audio_callback(out, block_size, None, None)
            audio_times_ms.append((time.perf_counter() - t0) * 1000.0)

            if not np.all(np.isfinite(out)):
                non_finite_count += int(out.size - np.count_nonzero(np.isfinite(out)))
            else:
                sum_sq += float(np.sum(out.astype(np.float64) ** 2))
                total_scalars += int(out.size)

            beat = eng.current_beat
            if beat >= 128.0 and current_section != 3:
                ng.set_clip(clip_chorus)
                current_section = 3
            elif 96.0 <= beat < 128.0 and current_section != 2:
                ng.set_clip(clip_montee)
                current_section = 2
            elif 32.0 <= beat < 96.0 and current_section != 1:
                ng.set_clip(clip_riff)
                current_section = 1

            # ~60 Hz playhead tick (every 3 blocks at 256 smp) & ~30 Hz meters (every 6 blocks)
            if blk_idx % 3 == 0:
                ui_t0 = time.perf_counter()
                _autoscroll_widgets(beat, grid, tl_scroll, ng, pr_scroll)
                ruler.set_playhead(beat)
                grid.set_playhead(beat)
                ng.set_playhead(beat)
                if blk_idx % 6 == 0:
                    console._update_meters()
                qapp.processEvents()
                ui_tick_times_ms.append((time.perf_counter() - ui_t0) * 1000.0)
    finally:
        eng.pause()

    assert eng.current_beat >= 132.0
    assert current_section == 3
    assert non_finite_count == 0
    rms = float(np.sqrt(sum_sq / max(1, total_scalars)))
    assert rms > 1e-4

    audio_arr = np.asarray(audio_times_ms, dtype=np.float64)
    p95_ms = float(np.percentile(audio_arr, 95))
    overruns = int(np.sum(audio_arr > budget_ms))

    ui_arr = np.asarray(ui_tick_times_ms, dtype=np.float64)
    ui_p95_ms = float(np.percentile(ui_arr, 95))

    assert css_calls == 0, f"Expected 0 setStyleSheet calls during S3 ticks, got {css_calls}"
    _assert_rendered_image_has_content(tl_scroll.viewport().grab().toImage(), min_distinct_colors=3)
    _assert_rendered_image_has_content(pr_scroll.viewport().grab().toImage(), min_distinct_colors=3)
    _assert_rendered_image_has_content(console.master_vu.grab().toImage(), min_distinct_colors=3)

    assert ui_p95_ms < 16.67, f"S3 UI tick p95={ui_p95_ms:.3f} ms >= 16.67 ms"
    assert p95_ms < budget_ms, f"S3 audio p95={p95_ms:.3f} ms >= budget={budget_ms:.3f} ms"
    assert overruns == 0, f"S3 had {overruns}/{len(audio_times_ms)} audio overruns"


def test_t4_s4_asio_guard_full_project_warmup_under_3_seconds(
    silent_engine_factory, sous_la_cendre_project
):
    """S4: Fresh 'Sous la cendre' ASIO-Guard warmup completes all 6 tracks in < 3.0 s."""
    proj, _ = sous_la_cendre_project
    eng = silent_engine_factory(sample_rate=44100, block_size=256, asio_guard=True)
    eng._asio_guard_enabled = False
    eng.set_project(proj)

    warmup = measure_asio_guard_warmup(eng, timeout_sec=60.0)
    assert warmup["timed_out"] is False
    assert len(warmup["track_status"]) == 6
    for st in warmup["track_status"].values():
        assert st["fully_rendered"] is True
        assert st["rendered_frames"] > 0
    assert warmup["elapsed_sec"] < 3.0, (
        f"S4 ASIO-Guard warmup took {warmup['elapsed_sec']:.2f} s (target < 3.0 s)"
    )


def test_t4_s5_native_c_vs_pure_python_wav_mixdown_parity(
    silent_engine_factory, sous_la_cendre_path, tmp_path, monkeypatch
):
    """S5: 8-bar WAV mixdown parity on 'Sous la cendre' (Native C vs Pure-Python fallback)."""
    native_info = inspect_native_dsp_support()
    assert native_info["native_active"] is True, (
        f"Native DSP must be active for S5 WAV parity test: {native_info['import_error']}"
    )

    proj_nat, _ = load_sous_la_cendre(path=sous_la_cendre_path, unsolo=True)
    eng_nat = silent_engine_factory(sample_rate=44100, block_size=256, asio_guard=False)
    eng_nat.set_project(proj_nat)
    wav_nat, sr_nat = export_wav_array(eng_nat, tmp_path / "s5_native.wav", end_bar=8)

    mod = native_info["module"]
    assert mod is not None
    for attr in ("HAS_NATIVE_DSP", "NATIVE_AVAILABLE", "AVAILABLE"):
        if hasattr(mod, attr):
            monkeypatch.setattr(mod, attr, False)

    proj_py, _ = load_sous_la_cendre(path=sous_la_cendre_path, unsolo=True)
    eng_py = silent_engine_factory(sample_rate=44100, block_size=256, asio_guard=False)
    eng_py.set_project(proj_py)
    wav_py, sr_py = export_wav_array(eng_py, tmp_path / "s5_python.wav", end_bar=8)

    assert sr_nat == sr_py == 44100
    assert wav_nat.shape == wav_py.shape
    assert np.all(np.isfinite(wav_nat))
    assert np.all(np.isfinite(wav_py))
    assert float(np.sqrt(np.mean(wav_nat**2))) > 1e-3
    rmse = compute_rmse(wav_nat, wav_py)
    assert rmse < 1e-4, f"S5 Native C vs Pure-Python 8-bar WAV RMSE={rmse:.6f} >= 1e-4"


def test_t4_s6_live_chorus_solo_loop_and_knob_edits_128_samples(
    silent_engine_factory, sous_la_cendre_project
):
    """S6: Live chorus solo + loop wrap + knob edits at 128 samples within 2.9025 ms budget."""
    proj, _ = sous_la_cendre_project
    proj.loop_enabled = True
    proj.loop_start_beat = 132.0
    proj.loop_end_beat = 140.0

    eng = silent_engine_factory(sample_rate=44100, block_size=128, asio_guard=True)
    eng._asio_guard_enabled = False
    eng.set_project(proj)
    warmup = measure_asio_guard_warmup(eng, timeout_sec=60.0)
    assert warmup["timed_out"] is False

    lead = proj.get_track("Guitare mélodique")
    assert lead is not None
    lead_amp = lead.plugins[1]

    block_size = 128
    budget_ms = 2.9025
    out = np.zeros((block_size, 2), dtype=np.float32)
    times_ms: List[float] = []
    non_finite_count = 0
    sum_sq = 0.0
    total_scalars = 0

    def _run_phase(num_blocks: int) -> None:
        nonlocal non_finite_count, sum_sq, total_scalars
        for _ in range(num_blocks):
            out.fill(0.0)
            t0 = time.perf_counter()
            eng._audio_callback(out, block_size, None, None)
            times_ms.append((time.perf_counter() - t0) * 1000.0)
            assert 132.0 <= eng.current_beat < 140.0
            if not np.all(np.isfinite(out)):
                non_finite_count += int(out.size - np.count_nonzero(np.isfinite(out)))
            else:
                sum_sq += float(np.sum(out.astype(np.float64) ** 2))
                total_scalars += int(out.size)

    eng.play()
    eng.seek_beat(139.85)
    try:
        # Phase (a): full chorus across loop wrap
        _run_phase(30)

        # Phase (b): solo melodic guitar + live amp distortion edit
        lead.soloed = True
        lead_amp.set_parameter("distortion", 0.90)
        eng.notify_plugin_modified(lead_amp)
        _run_phase(30)

        # Phase (c): un-solo full chorus
        lead.soloed = False
        _run_phase(30)
    finally:
        eng.pause()

    arr = np.asarray(times_ms, dtype=np.float64)
    p95_ms = float(np.percentile(arr, 95))
    overruns = int(np.sum(arr > budget_ms))
    rms = float(np.sqrt(sum_sq / max(1, total_scalars)))

    assert non_finite_count == 0
    assert rms > 1e-4
    assert p95_ms < budget_ms, f"S6 128-smp live chorus p95={p95_ms:.3f} ms >= {budget_ms:.4f} ms"
    assert overruns == 0, f"S6 128-smp live chorus had {overruns}/{len(times_ms)} overruns"
