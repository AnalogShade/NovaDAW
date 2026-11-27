"""Tier 3 Pairwise Cross-Feature Combination Tests (E2E-M2) for NovaDAW."""

from __future__ import annotations

import hashlib
from typing import List

import numpy as np
import pytest
from PySide6.QtCore import QRect
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QScrollArea, QWidget

import core.hardware_manager as hm_mod
from core.project import AutomationLane, MidiClip, MidiNote, Project, Track
from plugins.mixer.mixer_gui import MixerConsoleWidget
from plugins.registry import ensure_plugins_loaded, plugin_registry
from tests.e2e_playback.harness import (
    REPO_ROOT,
    compute_rmse,
    export_wav_array,
    inspect_native_dsp_support,
    measure_asio_guard_warmup,
    measure_callback_timing,
)
from ui.piano_roll import NoteGridWidget
from ui.timeline_view import TimelineGrid, TimelineRuler

ensure_plugins_loaded()


def _assert_rendered_image_has_content(img: QImage, min_distinct_colors: int = 2) -> None:
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


def _drive_blocks(engine, start_beat: float, num_blocks: int, block_size: int) -> np.ndarray:
    engine.seek_beat(float(start_beat))
    blocks = np.zeros((num_blocks, block_size, 2), dtype=np.float32)
    out = np.zeros((block_size, 2), dtype=np.float32)
    for i in range(num_blocks):
        out.fill(0.0)
        engine._audio_callback(out, block_size, None, None)
        blocks[i] = out
    return blocks


def _build_all_fx_and_instruments_project() -> Project:
    proj = Project.create_empty("Tier3AllFxAndInstruments")
    proj.bpm = 120.0

    # Track 1: Guitar -> GuitarAmp -> Equalizer
    t_gtr = Track(name="GuitarChain", track_type="midi", volume=0.85, pan=-0.15)
    gtr = plugin_registry.create_plugin("novadaw.guitar")
    amp = plugin_registry.create_plugin("novadaw.guitar_amp")
    amp.set_state({"distortion": 0.45, "volume": 0.9, "cab_enabled": True})
    eq = plugin_registry.create_plugin("novadaw.equalizer")
    eq.apply_preset("Warm Master")
    t_gtr.add_plugin(gtr)
    t_gtr.add_plugin(amp)
    t_gtr.add_plugin(eq)
    t_gtr.plugin_path = "novadaw.guitar"
    t_gtr.clips.append(
        MidiClip(
            name="GtrRiff",
            start_beat=0.0,
            length_beats=8.0,
            notes=[
                MidiNote(pitch=52, start_beat=0.0, duration=2.0, velocity=110),
                MidiNote(pitch=55, start_beat=2.0, duration=2.0, velocity=105),
                MidiNote(pitch=57, start_beat=4.0, duration=4.0, velocity=115),
            ],
        )
    )
    proj.add_track(t_gtr)

    # Track 2: Synth ("saw", deterministic) -> Compressor (RMS + sidechain HPF)
    t_syn = Track(name="SynthComp", track_type="midi", volume=0.75, pan=0.2)
    syn = plugin_registry.create_plugin("novadaw.synth")
    syn.set_state({"wave_type": "saw", "cutoff": 2600.0, "master_volume": 0.7})
    comp = plugin_registry.create_plugin("novadaw.compressor")
    comp.set_state(
        {
            "threshold_db": -22.0,
            "ratio": 5.0,
            "detection_mode": "rms",
            "sidechain_hpf_hz": 80.0,
            "mix": 1.0,
        }
    )
    t_syn.add_plugin(syn)
    t_syn.add_plugin(comp)
    t_syn.plugin_path = "novadaw.synth"
    t_syn.clips.append(
        MidiClip(
            name="SynPad",
            start_beat=0.0,
            length_beats=8.0,
            notes=[
                MidiNote(pitch=60, start_beat=0.0, duration=4.0, velocity=100),
                MidiNote(pitch=64, start_beat=4.0, duration=4.0, velocity=105),
            ],
        )
    )
    proj.add_track(t_syn)

    # Track 3: Piano
    t_pno = Track(name="Piano", track_type="midi", volume=0.8, pan=0.0)
    pno = plugin_registry.create_plugin("novadaw.piano")
    t_pno.add_plugin(pno)
    t_pno.plugin_path = "novadaw.piano"
    t_pno.clips.append(
        MidiClip(
            name="PnoChord",
            start_beat=0.0,
            length_beats=8.0,
            notes=[
                MidiNote(pitch=67, start_beat=0.0, duration=4.0, velocity=95),
                MidiNote(pitch=72, start_beat=2.0, duration=4.0, velocity=95),
            ],
        )
    )
    proj.add_track(t_pno)

    # Track 4: Drum Machine
    t_drm = Track(name="Drums", track_type="midi", volume=0.85, pan=0.0)
    drm = plugin_registry.create_plugin("novadaw.drum_machine")
    t_drm.add_plugin(drm)
    t_drm.plugin_path = "novadaw.drum_machine"
    t_drm.clips.append(
        MidiClip(
            name="Beat",
            start_beat=0.0,
            length_beats=8.0,
            notes=[
                MidiNote(pitch=36, start_beat=float(b), duration=0.5, velocity=115)
                for b in range(8)
            ],
        )
    )
    proj.add_track(t_drm)

    # Master Mixer
    master_t = proj.ensure_master_track()
    mixer_p = next(
        p for p in master_t.plugins if getattr(p, "plugin_type_id", None) == "novadaw.mixer"
    )
    mixer_p.set_state({"master_volume": 0.9, "master_pan": -0.1})
    return proj


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


# =============================================================================
# Non-GUI Pairwise Combination Tests (5 tests)
# =============================================================================


def test_t3_playback_live_fx_edit_with_asio_guard_warm(
    silent_engine_factory, sous_la_cendre_project
):
    proj, _ = sous_la_cendre_project
    eng = silent_engine_factory(sample_rate=44100, block_size=256, asio_guard=True)
    eng._asio_guard_enabled = False
    eng.set_project(proj)
    warmup = measure_asio_guard_warmup(eng, timeout_sec=60.0)
    assert warmup["timed_out"] is False

    eng.play()
    try:
        buf_before = _drive_blocks(eng, start_beat=132.0, num_blocks=20, block_size=256)
        assert np.all(np.isfinite(buf_before))
        assert float(np.sqrt(np.mean(buf_before**2))) > 1e-4

        # Live FX edits mid-playback across multiple tracks (amp + eq + compressor)
        amp = proj.tracks[2].plugins[1]
        eq = proj.tracks[2].plugins[2]
        comp = proj.tracks[1].plugins[1]

        amp.set_parameter("distortion", 0.95)
        amp.set_parameter("volume", 1.6)
        eq.bands[2].gain_db = 9.0
        comp.set_state({"threshold_db": -32.0, "ratio": 8.0})

        eng.notify_plugin_modified(amp)
        eng.notify_plugin_modified(eq)
        eng.notify_plugin_modified(comp)

        buf_after = _drive_blocks(eng, start_beat=132.0, num_blocks=20, block_size=256)
    finally:
        eng.pause()

    assert np.all(np.isfinite(buf_after))
    assert float(np.max(np.abs(buf_after))) <= 1.0001
    assert float(np.sqrt(np.mean(buf_after**2))) > 1e-4
    assert compute_rmse(buf_before, buf_after) > 1e-3


def test_t3_seek_combined_with_solo_and_mute_under_asio_guard(
    silent_engine_factory, sous_la_cendre_project
):
    proj, _ = sous_la_cendre_project
    eng = silent_engine_factory(sample_rate=44100, block_size=256, asio_guard=True)
    eng._asio_guard_enabled = False
    eng.set_project(proj)
    warmup = measure_asio_guard_warmup(eng, timeout_sec=60.0)
    assert warmup["timed_out"] is False

    drums = proj.get_track("Batterie • Punch Rock")
    piano = proj.get_track("Piano • Atmosphère")
    assert drums is not None and piano is not None

    eng.play()
    try:
        # 1. Solo drums across intro -> chorus -> breakdown -> chorus
        drums.soloed = True
        _ = _drive_blocks(eng, start_beat=0.0, num_blocks=8, block_size=256)
        drums_chorus = _drive_blocks(eng, start_beat=132.0, num_blocks=8, block_size=256)
        _ = _drive_blocks(eng, start_beat=200.0, num_blocks=8, block_size=256)
        assert np.all(np.isfinite(drums_chorus))
        assert float(np.sqrt(np.mean(drums_chorus**2))) > 1e-4

        # 2. Switch solo to piano at chorus
        drums.soloed = False
        piano.soloed = True
        piano_chorus = _drive_blocks(eng, start_beat=132.0, num_blocks=8, block_size=256)
        assert np.all(np.isfinite(piano_chorus))
        assert float(np.sqrt(np.mean(piano_chorus**2))) > 1e-4
        assert compute_rmse(drums_chorus, piano_chorus) > 1e-3

        # 3. Soloed + Muted on the same track must yield silence
        piano.muted = True
        silent_mix = _drive_blocks(eng, start_beat=132.0, num_blocks=8, block_size=256)
        assert float(np.max(np.abs(silent_mix))) <= 1e-7

        # 4. Restore full un-soloed/un-muted chorus
        piano.muted = False
        piano.soloed = False
        full_chorus = _drive_blocks(eng, start_beat=132.0, num_blocks=8, block_size=256)
        assert np.all(np.isfinite(full_chorus))
        assert float(np.sqrt(np.mean(full_chorus**2))) > 1e-4
        assert compute_rmse(piano_chorus, full_chorus) > 1e-3
    finally:
        eng.pause()


def test_t3_plugin_edits_automation_and_loop_wrap_with_wav_export(
    silent_engine_factory, tmp_path
):
    proj = Project.create_empty("Tier3AutoLoopExport")
    proj.bpm = 120.0
    proj.loop_enabled = True
    proj.loop_start_beat = 0.0
    proj.loop_end_beat = 4.0

    t1 = Track(name="SynthAuto", track_type="midi", volume=0.8, pan=0.0)
    syn = plugin_registry.create_plugin("novadaw.synth")
    syn.set_state({"wave_type": "saw", "master_volume": 0.7})
    eq = plugin_registry.create_plugin("novadaw.equalizer")
    eq.apply_preset("Vocal Clarity")
    t1.add_plugin(syn)
    t1.add_plugin(eq)
    t1.plugin_path = "novadaw.synth"
    t1.clips.append(
        MidiClip(
            name="Pad",
            start_beat=0.0,
            length_beats=8.0,
            notes=[MidiNote(pitch=60, start_beat=0.0, duration=8.0, velocity=110)],
        )
    )
    vol_lane = AutomationLane(target_param="volume", target_scope="track", enabled=True)
    vol_lane.add_point(0.0, 0.2, curve_type="linear")
    vol_lane.add_point(4.0, 1.0, curve_type="linear")
    pan_lane = AutomationLane(target_param="pan", target_scope="track", enabled=True)
    pan_lane.add_point(0.0, -0.8, curve_type="linear")
    pan_lane.add_point(4.0, 0.8, curve_type="linear")
    t1.automation_lanes.extend([vol_lane, pan_lane])
    proj.add_track(t1)

    t2 = Track(name="GuitarFX", track_type="midi", volume=0.85, pan=0.0)
    gtr = plugin_registry.create_plugin("novadaw.guitar")
    amp = plugin_registry.create_plugin("novadaw.guitar_amp")
    amp.set_state({"distortion": 0.1, "volume": 0.9, "cab_enabled": True})
    comp = plugin_registry.create_plugin("novadaw.compressor")
    comp.set_state({"threshold_db": -20.0, "ratio": 4.0})
    t2.add_plugin(gtr)
    t2.add_plugin(amp)
    t2.add_plugin(comp)
    t2.plugin_path = "novadaw.guitar"
    t2.clips.append(
        MidiClip(
            name="Lead",
            start_beat=0.0,
            length_beats=8.0,
            notes=[MidiNote(pitch=52, start_beat=0.0, duration=8.0, velocity=115)],
        )
    )
    proj.add_track(t2)

    eng = silent_engine_factory(sample_rate=44100, block_size=256, asio_guard=True)
    eng._asio_guard_enabled = False
    eng.set_project(proj)
    warmup = measure_asio_guard_warmup(eng, timeout_sec=30.0)
    assert warmup["timed_out"] is False

    eng.play()
    eng.seek_beat(3.90)
    out = np.zeros((256, 2), dtype=np.float32)
    auto_samples = []
    wrapped = False
    try:
        for step in range(20):
            if step == 5:
                amp.set_parameter("distortion", 0.95)
                eng.notify_plugin_modified(amp)
            prev_beat = eng.current_beat
            out.fill(0.0)
            eng._audio_callback(out, 256, None, None)
            assert np.all(np.isfinite(out))
            assert float(np.max(np.abs(out))) <= 1.0001
            assert 0.0 <= eng.current_beat < 4.0
            if eng.current_beat < prev_beat:
                wrapped = True
            vals = eng.get_track_live_automation_values(t1.id)
            if vals is not None:
                auto_samples.append((eng.current_beat, vals["volume"], vals["pan"]))
    finally:
        eng.pause()

    assert wrapped is True
    assert len(auto_samples) >= 2
    vols = [s[1] for s in auto_samples]
    pans = [s[2] for s in auto_samples]
    assert max(vols) - min(vols) > 0.5
    assert max(pans) - min(pans) > 1.0

    wav_path = tmp_path / "t3_loop_auto.wav"
    audio, sr = export_wav_array(eng, wav_path, end_bar=2)
    expected_frames = int((2 * proj.beats_per_bar() / (proj.bpm / 60.0)) * 44100)
    assert sr == 44100
    assert audio.shape == (expected_frames, 2)
    assert np.all(np.isfinite(audio))
    assert float(np.sqrt(np.mean(audio**2))) > 1e-3


def test_t3_native_c_vs_python_wav_export_parity_all_fx_and_instruments(
    silent_engine_factory, tmp_path, monkeypatch
):
    native_info = inspect_native_dsp_support()
    assert native_info["native_active"] is True, (
        f"Native DSP must be active for C vs Python parity test: {native_info['import_error']}"
    )

    proj_nat = _build_all_fx_and_instruments_project()
    eng_nat = silent_engine_factory(sample_rate=44100, block_size=256, asio_guard=False)
    eng_nat.set_project(proj_nat)
    wav_nat, sr_nat = export_wav_array(eng_nat, tmp_path / "t3_nat.wav", end_bar=2)

    mod = native_info["module"]
    assert mod is not None
    for attr in ("HAS_NATIVE_DSP", "NATIVE_AVAILABLE", "AVAILABLE"):
        if hasattr(mod, attr):
            monkeypatch.setattr(mod, attr, False)

    # Verify fallback instantiation genuinely disables native states
    amp_fb = plugin_registry.create_plugin("novadaw.guitar_amp")
    eq_fb = plugin_registry.create_plugin("novadaw.equalizer")
    comp_fb = plugin_registry.create_plugin("novadaw.compressor")
    assert amp_fb._dsp._native is None
    assert eq_fb._native is None
    assert comp_fb._c_state is None

    proj_py = _build_all_fx_and_instruments_project()
    eng_py = silent_engine_factory(sample_rate=44100, block_size=256, asio_guard=False)
    eng_py.set_project(proj_py)
    wav_py, sr_py = export_wav_array(eng_py, tmp_path / "t3_py.wav", end_bar=2)

    assert sr_nat == sr_py == 44100
    assert wav_nat.shape == wav_py.shape
    assert np.all(np.isfinite(wav_nat))
    assert np.all(np.isfinite(wav_py))
    assert float(np.sqrt(np.mean(wav_nat**2))) > 1e-3
    rmse = compute_rmse(wav_nat, wav_py)
    assert rmse < 1e-4, f"Native C vs pure-Python 2-bar WAV RMSE={rmse:.6f} >= 1e-4"


def test_t3_multitrack_buffer_transitions_and_hardware_autoconfig(
    silent_engine_factory, sous_la_cendre_project, isolated_hardware_settings
):
    repo_settings = REPO_ROOT / "settings.json"
    before_sha = (
        hashlib.sha256(repo_settings.read_bytes()).hexdigest()
        if repo_settings.is_file()
        else None
    )

    cfg = hm_mod.hardware_manager.auto_configure_audio(force=True)
    proj, _ = sous_la_cendre_project
    eng = silent_engine_factory(
        sample_rate=int(cfg["sample_rate"]),
        block_size=int(cfg["buffer_size"]),
        asio_guard=True,
    )
    eng._asio_guard_enabled = False
    eng.set_project(proj)
    warmup = measure_asio_guard_warmup(eng, timeout_sec=60.0)
    assert warmup["timed_out"] is False

    # Phase 1: auto-configured buffer
    s1 = measure_callback_timing(
        eng, block_size=int(cfg["buffer_size"]), start_beat=132.0, num_blocks=16
    )
    assert s1.non_finite_count == 0 and s1.rms > 1e-4

    # Phase 2: 512 samples
    assert eng.configure_device(block_size=512) is True
    s2 = measure_callback_timing(eng, block_size=512, start_beat=132.0, num_blocks=16)
    assert s2.non_finite_count == 0 and s2.rms > 1e-4

    # Phase 3: 128 samples
    assert eng.configure_device(block_size=128) is True
    s3 = measure_callback_timing(eng, block_size=128, start_beat=132.0, num_blocks=16)
    assert s3.non_finite_count == 0 and s3.rms > 1e-4

    # Phase 4: sample_rate=48000, block_size=256 (invalidates/rebuilds caches)
    assert eng.configure_device(sample_rate=48000, block_size=256) is True
    s4 = measure_callback_timing(eng, block_size=256, start_beat=132.0, num_blocks=16)
    assert s4.non_finite_count == 0 and s4.rms > 1e-4

    # Phase 5: streaming_mode=True (effective_block_size)
    assert eng.configure_device(streaming_mode=True, block_size=128) is True
    eff_bs = int(eng.effective_block_size)
    assert eff_bs >= 128
    s5 = measure_callback_timing(eng, block_size=eff_bs, start_beat=132.0, num_blocks=16)
    assert s5.non_finite_count == 0 and s5.rms > 1e-4

    if before_sha is not None:
        assert hashlib.sha256(repo_settings.read_bytes()).hexdigest() == before_sha


# =============================================================================
# GUI + Playback + Metering Pairwise Tests (2 tests)
# =============================================================================


def test_t3_playback_concurrent_60hz_timeline_pianoroll_and_30hz_mixer_meters(
    qapp,
    qt_widget_cleanup,
    silent_engine_factory,
    sous_la_cendre_project,
    monkeypatch,
):
    proj, _ = sous_la_cendre_project
    eng = silent_engine_factory(sample_rate=44100, block_size=128, asio_guard=True)
    eng._asio_guard_enabled = False
    eng.set_project(proj)
    warmup = measure_asio_guard_warmup(eng, timeout_sec=60.0)
    assert warmup["timed_out"] is False

    grid_dirty_widths: List[int] = []
    ng_dirty_widths: List[int] = []
    orig_grid_paint = TimelineGrid.paintEvent
    orig_ng_paint = NoteGridWidget.paintEvent

    def spy_grid_paint(self, ev):
        grid_dirty_widths.append(ev.rect().width())
        return orig_grid_paint(self, ev)

    def spy_ng_paint(self, ev):
        ng_dirty_widths.append(ev.rect().width())
        return orig_ng_paint(self, ev)

    # Install paintEvent spies BEFORE widget instantiation (PySide6 binds vtable on __init__).
    monkeypatch.setattr(TimelineGrid, "paintEvent", spy_grid_paint)
    monkeypatch.setattr(NoteGridWidget, "paintEvent", spy_ng_paint)

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

    pr_scroll = qt_widget_cleanup(QScrollArea())
    pr_scroll.setWidgetResizable(True)
    ng = NoteGridWidget(start_pitch=0, num_pitches=128, row_height=18)
    ng.set_clip(proj.tracks[5].clips[2])  # '04 · Refrain' (128..192)
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

    # Let QScrollAreas compute scrollbar bounds, then scroll to chorus & clear startup paints.
    qapp.processEvents()
    _autoscroll_widgets(132.0, grid, tl_scroll, ng, pr_scroll)
    ruler.set_playhead(132.0)
    grid.set_playhead(132.0)
    ng.set_playhead(132.0)
    qapp.processEvents()
    grid_dirty_widths.clear()
    ng_dirty_widths.clear()

    css_calls = 0
    orig_set_ss = QWidget.setStyleSheet

    def spy_ss(self, ss):
        nonlocal css_calls
        css_calls += 1
        return orig_set_ss(self, ss)

    monkeypatch.setattr(QWidget, "setStyleSheet", spy_ss)

    import time

    block_size = 128
    num_blocks = 120
    budget_ms = (block_size / 44100.0) * 1000.0
    out = np.zeros((block_size, 2), dtype=np.float32)
    times_ms: List[float] = []
    non_finite_count = 0
    sum_sq = 0.0
    total_scalars = 0

    eng.play()
    eng.seek_beat(132.0)
    try:
        for blk_idx in range(1, num_blocks + 1):
            out.fill(0.0)
            t0 = time.perf_counter()
            eng._audio_callback(out, block_size, None, None)
            times_ms.append((time.perf_counter() - t0) * 1000.0)

            if not np.all(np.isfinite(out)):
                non_finite_count += int(out.size - np.count_nonzero(np.isfinite(out)))
            else:
                sum_sq += float(np.sum(out.astype(np.float64) ** 2))
                total_scalars += int(out.size)

            # ~60 Hz playhead update (every 6 blocks at 128 smp / 44.1 kHz)
            if blk_idx % 6 == 0:
                beat = eng.current_beat
                _autoscroll_widgets(beat, grid, tl_scroll, ng, pr_scroll)
                ruler.set_playhead(beat)
                grid.set_playhead(beat)
                ng.set_playhead(beat)
                qapp.processEvents()

            # ~30 Hz mixer meter update (every 12 blocks at 128 smp / 44.1 kHz)
            if blk_idx % 12 == 0:
                console._update_meters()
                qapp.processEvents()
    finally:
        eng.pause()

    rms = float(np.sqrt(sum_sq / max(1, total_scalars)))
    p95_ms = float(np.percentile(times_ms, 95))
    overruns = int(np.sum(np.asarray(times_ms) > budget_ms))

    assert css_calls == 0, f"Expected 0 setStyleSheet calls during ticks, got {css_calls}"
    assert grid_dirty_widths, "Expected TimelineGrid paintEvents on playhead ticks"
    assert ng_dirty_widths, "Expected NoteGridWidget paintEvents on playhead ticks"
    assert max(grid_dirty_widths) < 65, f"TimelineGrid dirty rect too wide: {max(grid_dirty_widths)}"
    assert max(ng_dirty_widths) < 65, f"NoteGridWidget dirty rect too wide: {max(ng_dirty_widths)}"

    assert non_finite_count == 0
    assert rms > 1e-4
    assert mixer_p.peak_left_db > -60.0
    assert any(
        strip.vu_meter.peak_left_db > -60.0 and strip.lbl_peak_db.text() != "-∞ dB"
        for strip in console.strips.values()
    ), "Expected real AudioEngine -> MixerPlugin -> strip VU/PeakDbLabel propagation"

    # Non-empty rendered pixel check
    _assert_rendered_image_has_content(tl_scroll.viewport().grab().toImage(), min_distinct_colors=3)
    _assert_rendered_image_has_content(pr_scroll.viewport().grab().toImage(), min_distinct_colors=3)
    _assert_rendered_image_has_content(console.master_vu.grab().toImage(), min_distinct_colors=3)

    # Honest real-time audio budget check
    assert p95_ms < budget_ms, f"128-smp concurrent UI+audio p95={p95_ms:.3f} ms >= {budget_ms:.3f} ms"
    assert overruns == 0, f"128-smp concurrent UI+audio had {overruns}/{num_blocks} overruns"


def test_t3_gui_zoom_scroll_and_clip_switch_during_active_playback(
    qapp,
    qt_widget_cleanup,
    silent_engine_factory,
    sous_la_cendre_project,
):
    proj, _ = sous_la_cendre_project
    proj.loop_enabled = True
    proj.loop_start_beat = 128.0
    proj.loop_end_beat = 136.0

    eng = silent_engine_factory(sample_rate=44100, block_size=256, asio_guard=True)
    eng._asio_guard_enabled = False
    eng.set_project(proj)
    warmup = measure_asio_guard_warmup(eng, timeout_sec=60.0)
    assert warmup["timed_out"] is False

    ruler = qt_widget_cleanup(TimelineRuler())
    ruler.resize(1200, 30)
    ruler.set_loop(True, 128.0, 136.0)
    ruler.show()

    tl_scroll = qt_widget_cleanup(QScrollArea())
    tl_scroll.setWidgetResizable(True)
    grid = TimelineGrid(proj)
    tl_scroll.setWidget(grid)
    tl_scroll.resize(1200, 600)
    tl_scroll.horizontalScrollBar().valueChanged.connect(ruler.set_scroll_offset)
    tl_scroll.show()

    pr_scroll = qt_widget_cleanup(QScrollArea())
    pr_scroll.setWidgetResizable(True)
    ng = NoteGridWidget(start_pitch=0, num_pitches=128, row_height=18)
    ng.set_clip(proj.tracks[5].clips[2])
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

    out = np.zeros((256, 2), dtype=np.float32)
    non_finite_count = 0
    sum_sq = 0.0
    total_scalars = 0
    wrapped = False

    eng.play()
    eng.seek_beat(135.7)
    try:
        for blk_idx in range(1, 37):
            prev_beat = eng.current_beat
            out.fill(0.0)
            eng._audio_callback(out, 256, None, None)
            if not np.all(np.isfinite(out)):
                non_finite_count += int(out.size - np.count_nonzero(np.isfinite(out)))
            else:
                sum_sq += float(np.sum(out.astype(np.float64) ** 2))
                total_scalars += int(out.size)

            assert 128.0 <= eng.current_beat < 136.0
            if eng.current_beat < prev_beat:
                wrapped = True

            if blk_idx == 6:
                grid.set_zoom(25.0)
                ruler.set_zoom(25.0)
                tl_scroll.horizontalScrollBar().setValue(int(128.0 * 25.0))
                ng.set_clip(proj.tracks[5].clips[0])
            elif blk_idx == 15:
                grid.set_zoom(80.0)
                ruler.set_zoom(80.0)
                ng.set_zoom(90.0)
                ng.set_clip(proj.tracks[0].clips[3])
            elif blk_idx == 24:
                ng.set_clip(proj.tracks[5].clips[2])

            if blk_idx % 3 == 0:
                beat = eng.current_beat
                _autoscroll_widgets(beat, grid, tl_scroll, ng, pr_scroll)
                ruler.set_playhead(beat)
                grid.set_playhead(beat)
                ng.set_playhead(beat)
                qapp.processEvents()

            if blk_idx % 6 == 0:
                console._update_meters()
                qapp.processEvents()
    finally:
        eng.pause()

    assert wrapped is True
    assert non_finite_count == 0
    assert float(np.sqrt(sum_sq / max(1, total_scalars))) > 1e-4

    _assert_rendered_image_has_content(tl_scroll.viewport().grab().toImage(), min_distinct_colors=3)
    _assert_rendered_image_has_content(pr_scroll.viewport().grab().toImage(), min_distinct_colors=3)
    _assert_rendered_image_has_content(
        grid.grab(QRect(0, 0, min(grid.width(), 800), min(grid.height(), 400))).toImage(),
        min_distinct_colors=3,
    )
