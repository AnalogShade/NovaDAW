"""
tests/test_gui_paint_performance.py - Regression & performance tests for M3 GUI & Metering optimizations.

Verifies:
1. Pixel-exact equivalence (0 differing pixels) between full unclipped paintEvent and
   incremental dirty-rect / multi-rect QRegion repaints on TimelineGrid, TimelineRuler,
   and NoteGridWidget (including >= 60 px loop jumps and triangle wing invalidation).
2. Dirty-rect width clipping on playhead ticks, recording clip invalidation, ruler scroll
   offset handling, and full-viewport invalidation on zoom/edit.
3. Offscreen playhead paint performance (< 2.0 ms/tick avg) on dense 6-track / 46-clip /
   ~4000-note + audio + curved automation fixtures.
4. MixerConsoleWidget: 0 setStyleSheet calls during 30 Hz meter ticks, dirty-gate skip on
   steady/silent levels, VuMeterBar pixel colors & 0 dB marker accuracy, PeakDbLabel text
   updates across bands, and active/silent throughput budgets.
"""
import math
import os
import sys
import time
from pathlib import Path
from typing import List

import numpy as np
import pytest

os.environ.setdefault("NOVADAW_SILENT_TESTS", "1")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPoint, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QRegion
from PySide6.QtWidgets import QApplication, QScrollArea, QWidget

from core.project import (
    AudioClip,
    AutomationLane,
    AutomationPoint,
    MidiClip,
    MidiNote,
    Project,
    Track,
)
from plugins.mixer.mixer_gui import MixerConsoleWidget, PeakDbLabel, VuMeterBar
from ui.piano_roll import NoteGridWidget
from ui.timeline_view import TimelineGrid, TimelineRuler


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _count_differing_pixels(img_a: QImage, img_b: QImage) -> int:
    if img_a.size() != img_b.size():
        return -1
    if img_a == img_b:
        return 0
    w, h = img_a.width(), img_a.height()
    diff = 0
    for y in range(h):
        for x in range(w):
            if img_a.pixel(x, y) != img_b.pixel(x, y):
                diff += 1
    return diff


def _build_rich_timeline_project(
    num_tracks: int = 6,
    clips_per_track: int = 8,
    notes_per_clip: int = 100,
    with_audio: bool = True,
    with_automation: bool = True,
) -> Project:
    proj = Project(name="M3 Benchmark", bpm=148.0, loop_enabled=True, loop_start_beat=4.0, loop_end_beat=20.0)
    sr = 44100
    t_sec = 4.0
    t_arr = np.linspace(0.0, t_sec, int(sr * t_sec), endpoint=False, dtype=np.float32)
    sine = (0.7 * np.sin(2.0 * math.pi * 220.0 * t_arr)).astype(np.float32)

    for ti in range(num_tracks):
        track = Track(
            name=f"Track {ti + 1}",
            track_type="audio" if (with_audio and ti == num_tracks - 1) else "midi",
            color=["#38bdf8", "#a855f7", "#f59e0b", "#10b981", "#ec4899", "#ef4444"][ti % 6],
            volume=0.85,
        )
        for ci in range(clips_per_track):
            start_b = ci * 8.0 + 1.0
            if track.track_type == "audio":
                clip = AudioClip(
                    name=f"Audio {ci}",
                    start_beat=start_b,
                    length_beats=6.0,
                    color=track.color,
                    audio_data=sine.copy(),
                    sample_rate=sr,
                    source_offset_beats=0.25,
                )
            else:
                notes = [
                    MidiNote(
                        pitch=44 + ((ni * 3 + ti) % 36),
                        start_beat=(ni % 24) * 0.25,
                        duration=0.25,
                        velocity=100,
                    )
                    for ni in range(notes_per_clip)
                ]
                clip = MidiClip(
                    name=f"Clip {ti}-{ci}",
                    start_beat=start_b,
                    length_beats=6.0,
                    color=track.color,
                    notes=notes,
                )
            track.clips.append(clip)

        if with_automation and ti == 0:
            lane = AutomationLane(target_param="volume", enabled=True, visible=True, height=56)
            for pi in range(60):
                lane.points.append(
                    AutomationPoint(
                        beat=pi * 1.25,
                        value=0.35 + 0.55 * (0.5 + 0.5 * math.sin(pi * 0.45)),
                        curve_type="curve",
                        curve_tension=0.0,
                    )
                )
            lane.sort_points()
            track.automation_expanded = True
            track.automation_lanes.append(lane)

        proj.add_track(track)

    proj.ensure_master_track()
    return proj


def _simulate_dirty_playhead_renders(widget: QWidget, beats: List[float], monkeypatch) -> QImage:
    """Renders widget at beats[0] full, then replays set_playhead(b) QRegion updates via QWidget.render."""
    w, h = widget.width(), widget.height()
    img = QImage(w, h, QImage.Format_ARGB32)
    img.fill(0)
    widget.render(img, QPoint(0, 0), QRegion(0, 0, w, h))

    pending_rects: List[QRect] = []
    orig_update = widget.update

    def capture_update(*args):
        if len(args) == 4:
            pending_rects.append(QRect(int(args[0]), int(args[1]), int(args[2]), int(args[3])))
        elif len(args) == 1 and isinstance(args[0], QRect):
            pending_rects.append(args[0])
        elif len(args) == 0:
            pending_rects.append(widget.rect())
        return orig_update(*args)

    monkeypatch.setattr(widget, "update", capture_update)
    bounds = QRect(0, 0, w, h)
    for b in beats[1:]:
        pending_rects.clear()
        widget.set_playhead(b)
        if not pending_rects:
            continue
        reg = QRegion()
        for r in pending_rects:
            clipped = r.intersected(bounds)
            if not clipped.isEmpty():
                reg = reg.united(clipped)
        if not reg.isEmpty():
            widget.render(img, reg.boundingRect().topLeft(), reg)
    return img


# ---------------------------------------------------------------------------
# 1. Pixel-Equivalence Tests (TimelineGrid, TimelineRuler, NoteGridWidget)
# ---------------------------------------------------------------------------

def test_timeline_grid_dirty_rect_pixel_equivalence(qapp, monkeypatch):
    proj = _build_rich_timeline_project(
        num_tracks=3, clips_per_track=4, notes_per_clip=24, with_audio=True, with_automation=True
    )
    grid = TimelineGrid(proj)
    grid.setFixedSize(1200, 360)
    grid.selected_track_id = proj.tracks[0].id
    grid.selected_clip = (proj.tracks[0], proj.tracks[0].clips[1])
    grid.time_selection = (5.0, 14.0)
    grid.playhead_beat = 2.0

    # Short ticks (< 60 px) + large loop wrap jump (>= 60 px, multi-rect QRegion) + return step
    beats = [2.0, 2.2, 2.5, 6.0, 11.25, 19.8, 4.0, 4.35]
    img_dirty = _simulate_dirty_playhead_renders(grid, beats, monkeypatch)

    img_full = QImage(grid.size(), QImage.Format_ARGB32)
    img_full.fill(0)
    grid.render(img_full, QPoint(0, 0), QRegion(grid.rect()))

    assert _count_differing_pixels(img_dirty, img_full) == 0


def test_timeline_ruler_scrolled_playhead_pixel_equivalence(qapp, monkeypatch):
    ruler = TimelineRuler()
    ruler.setFixedSize(900, 30)
    ruler.set_zoom(40.0)
    ruler.set_loop(True, 10.0, 22.0)
    ruler.set_scroll_offset(320.0)  # beat 8.0 at x=0
    ruler.playhead_beat = 9.0

    # Includes short moves, >=60px loop wrap, and moves across loop handles
    beats = [9.0, 9.2, 10.0, 10.25, 21.8, 10.1]
    img_dirty = _simulate_dirty_playhead_renders(ruler, beats, monkeypatch)

    img_full = QImage(ruler.size(), QImage.Format_ARGB32)
    img_full.fill(0)
    ruler.render(img_full, QPoint(0, 0), QRegion(ruler.rect()))

    assert _count_differing_pixels(img_dirty, img_full) == 0


def test_note_grid_dirty_rect_and_triangle_pixel_equivalence(qapp, monkeypatch):
    clip = MidiClip(
        name="Equiv Clip",
        start_beat=10.0,
        length_beats=12.0,
        notes=[
            MidiNote(pitch=60, start_beat=0.0, duration=1.0),
            MidiNote(pitch=64, start_beat=1.0, duration=1.5),
            MidiNote(pitch=67, start_beat=2.5, duration=2.0),
            MidiNote(pitch=72, start_beat=5.0, duration=1.0),
        ],
    )
    ng = NoteGridWidget(start_pitch=48, num_pitches=36, row_height=18)
    ng.set_clip(clip)
    ng.setFixedSize(960, 36 * 18)
    ng.selected_notes = [clip.notes[1]]
    ng.selected_note = clip.notes[1]
    ng._marquee = QRectF(40.0, 60.0, 180.0, 140.0)
    ng.playhead_project_beat = 10.5

    # Short moves + >=60px jump so top-14px +-6px triangle wings must clear cleanly
    beats = [10.5, 10.65, 10.8, 16.5, 11.1]
    img_dirty = _simulate_dirty_playhead_renders(ng, beats, monkeypatch)

    img_full = QImage(ng.size(), QImage.Format_ARGB32)
    img_full.fill(0)
    ng.render(img_full, QPoint(0, 0), QRegion(ng.rect()))

    assert _count_differing_pixels(img_dirty, img_full) == 0


# ---------------------------------------------------------------------------
# 2. Dirty-Rect Clipping, Triangle Wings, Recording & Full-Invalidate Tests
# ---------------------------------------------------------------------------

def test_timeline_grid_playhead_dirty_rect_clipping(qapp, monkeypatch):
    painted_widths: List[int] = []
    orig_paint = TimelineGrid.paintEvent

    def spy_paint(self, event):
        painted_widths.append(event.rect().width())
        return orig_paint(self, event)

    monkeypatch.setattr(TimelineGrid, "paintEvent", spy_paint)

    proj = _build_rich_timeline_project(num_tracks=3, clips_per_track=4, notes_per_clip=20)
    scroll = QScrollArea()
    grid = TimelineGrid(proj)
    scroll.setWidget(grid)
    scroll.resize(1200, 600)
    scroll.show()
    qapp.processEvents()

    painted_widths.clear()
    for i in range(1, 31):
        grid.set_playhead(1.0 + i * 0.05)  # 2 px/tick at ppb=40
        qapp.processEvents()

    scroll.close()
    assert painted_widths, "Expected paintEvent calls during playhead ticks"
    assert max(painted_widths) < 65, f"Expected narrow dirty rect, got max width {max(painted_widths)}"


def test_timeline_grid_recording_clip_also_invalidated_on_playhead(qapp, monkeypatch):
    proj = Project(bpm=120.0)
    tr = Track(name="Rec Track", track_type="audio")
    rec_clip = AudioClip(name="Rec", start_beat=2.0, length_beats=1.0)
    rec_clip._is_recording = True
    tr.clips.append(rec_clip)
    proj.add_track(tr)

    grid = TimelineGrid(proj)
    updates = []
    monkeypatch.setattr(grid, "update", lambda *args: updates.append(args))
    grid.playhead_beat = 3.0
    grid.set_playhead(3.2)

    assert len(updates) >= 2
    # Recording clip starts at x = 2 * 40 = 80; its update rect covers from cx-4 = 76
    assert any(u[0] <= 80 and u[2] >= 48 for u in updates if len(u) == 4)


def test_timeline_grid_full_invalidate_on_zoom_scroll_edit(qapp, monkeypatch):
    painted_widths: List[int] = []
    orig_paint = TimelineGrid.paintEvent

    def spy_paint(self, event):
        painted_widths.append(event.rect().width())
        return orig_paint(self, event)

    monkeypatch.setattr(TimelineGrid, "paintEvent", spy_paint)

    proj = _build_rich_timeline_project(num_tracks=2, clips_per_track=2, notes_per_clip=10)
    scroll = QScrollArea()
    grid = TimelineGrid(proj)
    scroll.setWidget(grid)
    scroll.resize(1000, 400)
    scroll.show()
    qapp.processEvents()

    painted_widths.clear()
    grid.set_zoom(80.0)
    qapp.processEvents()
    assert painted_widths and max(painted_widths) >= 900

    painted_widths.clear()
    grid.selected_clip = (proj.tracks[0], proj.tracks[0].clips[0])
    grid.duplicate_selected_clip()
    qapp.processEvents()
    scroll.close()
    assert painted_widths and max(painted_widths) >= 900


def test_timeline_ruler_skips_offscreen_playhead_and_invalidates_scrolled(qapp, monkeypatch):
    ruler = TimelineRuler()
    ruler.resize(800, 30)
    ruler.set_zoom(40.0)
    ruler.set_scroll_offset(400.0)  # visible beats: 10..30

    updates = []
    monkeypatch.setattr(ruler, "update", lambda *args: updates.append(args))

    # Offscreen to the left (beats 1.0 -> 1.1)
    ruler.playhead_beat = 1.0
    ruler.set_playhead(1.1)
    assert updates == []

    # Visible (beats 15.0 -> 15.1 => px 200..204 after subtracting scroll_offset=400)
    ruler.playhead_beat = 15.0
    ruler.set_playhead(15.1)
    assert len(updates) == 1
    assert updates[0][0] == 192  # 200 - 8


def test_note_grid_dirty_rect_and_triangle_band_invalidation(qapp, monkeypatch):
    painted_widths = []
    orig_paint = NoteGridWidget.paintEvent
    monkeypatch.setattr(
        NoteGridWidget,
        "paintEvent",
        lambda self, ev: (painted_widths.append(ev.rect().width()), orig_paint(self, ev)),
    )

    clip = MidiClip(
        name="Test",
        start_beat=10.0,
        length_beats=16.0,
        notes=[MidiNote(pitch=60 + (i % 12), start_beat=i * 0.25, duration=0.25) for i in range(60)],
    )
    scroll = QScrollArea()
    ng = NoteGridWidget(start_pitch=0, num_pitches=128, row_height=18)
    ng.set_clip(clip)
    scroll.setWidget(ng)
    scroll.resize(1200, 500)
    scroll.show()
    qapp.processEvents()

    # Verify update calls include body (±4px) first and top triangle band (height=14) second
    updates = []
    orig_update = ng.update
    monkeypatch.setattr(ng, "update", lambda *args: (updates.append(args), orig_update(*args)))
    ng.playhead_project_beat = 11.0
    ng.set_playhead(11.1)
    assert updates[0][0] == 56  # (11-10)*60 - 4
    assert any(len(u) == 4 and u[1] == 0 and u[3] == 14 and u[0] <= 52 for u in updates)

    # Verify paintEvent clips to narrow dirty rect on playhead tick
    painted_widths.clear()
    for i in range(1, 21):
        ng.set_playhead(11.1 + i * 0.05)
        qapp.processEvents()
    assert painted_widths and max(painted_widths) < 65

    # Verify zoom triggers full viewport repaint
    painted_widths.clear()
    ng.set_zoom(90.0)
    qapp.processEvents()
    scroll.close()
    assert painted_widths and max(painted_widths) >= 1000


# ---------------------------------------------------------------------------
# 3. Playhead Tick Paint Performance Benchmarks (< 2.0 ms/tick offscreen)
# ---------------------------------------------------------------------------

def test_timeline_grid_60tick_playhead_performance_budget(qapp):
    # 6 tracks, 46 clips (~3800 MIDI notes + audio clip + 60-point curved automation)
    proj = _build_rich_timeline_project(
        num_tracks=6, clips_per_track=8, notes_per_clip=95, with_audio=True, with_automation=True
    )
    scroll = QScrollArea()
    grid = TimelineGrid(proj)
    scroll.setWidget(grid)
    scroll.resize(1200, 600)
    scroll.show()
    scroll.horizontalScrollBar().setValue(400)
    qapp.processEvents()

    # Warm caches once
    grid.set_playhead(12.0)
    qapp.processEvents()

    ticks = 60
    t0 = time.perf_counter()
    for i in range(ticks):
        grid.set_playhead(12.05 + i * 0.05)
        qapp.processEvents()
    avg_ms = ((time.perf_counter() - t0) * 1000.0) / ticks
    assert avg_ms < 2.0, f"TimelineGrid playhead tick too slow: {avg_ms:.3f} ms/tick"


def test_note_grid_60tick_playhead_performance_budget(qapp):
    clip = MidiClip(
        name="Dense",
        start_beat=0.0,
        length_beats=64.0,
        notes=[
            MidiNote(pitch=36 + (i % 48), start_beat=i * 0.2, duration=0.18)
            for i in range(300)
        ],
    )
    scroll = QScrollArea()
    ng = NoteGridWidget(start_pitch=0, num_pitches=128, row_height=18)
    ng.set_clip(clip)
    scroll.setWidget(ng)
    scroll.resize(1200, 500)
    scroll.show()
    qapp.processEvents()

    ng.set_playhead(1.0)
    qapp.processEvents()

    ticks = 60
    t0 = time.perf_counter()
    for i in range(ticks):
        ng.set_playhead(1.05 + i * 0.05)
        qapp.processEvents()
    avg_ms = ((time.perf_counter() - t0) * 1000.0) / ticks
    assert avg_ms < 2.0, f"NoteGridWidget playhead tick too slow: {avg_ms:.3f} ms/tick"


# ---------------------------------------------------------------------------
# 4. MixerConsoleWidget Metering Tests (Zero setStyleSheet, Dirty Gate, Colors)
# ---------------------------------------------------------------------------

def _build_mixer_console(num_tracks: int = 16) -> MixerConsoleWidget:
    proj = Project(name="Mixer Bench")
    for i in range(num_tracks):
        proj.add_track(Track(name=f"Tr {i + 1}", track_type="midi", color="#38bdf8"))
    master_t = proj.ensure_master_track()
    mixer_p = next(p for p in master_t.plugins if getattr(p, "plugin_type_id", "") == "novadaw.mixer")
    console = MixerConsoleWidget(mixer_p)
    console.meter_timer.stop()
    console.resize(1600, 360)
    console.show()
    return console


def test_mixer_meter_zero_stylesheet_calls_during_30hz_ticks(qapp, monkeypatch):
    console = _build_mixer_console(16)
    qapp.processEvents()

    css_calls = 0
    orig_set_ss = QWidget.setStyleSheet

    def spy_ss(self, ss):
        nonlocal css_calls
        css_calls += 1
        return orig_set_ss(self, ss)

    monkeypatch.setattr(QWidget, "setStyleSheet", spy_ss)

    mixer_p = console.mixer
    first_tid = next(iter(console.strips.keys()))

    # 150 active ticks sweeping across -45 dB, -4 dB, +1.5 dB
    for i in range(150):
        band = (-45.0, -4.0, +1.5)[i % 3]
        mixer_p.peak_left_db = band
        mixer_p.peak_right_db = band - 1.0
        for tid in console.strips:
            mixer_p.track_peaks[tid] = (band, band - 0.5)
        console._update_meters()
        qapp.processEvents()

    # Check exact PeakDbLabel formatting across bands before decay
    mixer_p.peak_left_db = +1.5
    mixer_p.peak_right_db = +1.5
    mixer_p.track_peaks[first_tid] = (-14.2, -16.0)
    console._update_meters()
    assert console.lbl_master_peak_db.text() == "+1.5 dB"
    assert console.strips[first_tid].lbl_peak_db.text() == "-14.2 dB"

    # 50 silent ticks (-60 dB)
    for _ in range(50):
        mixer_p.peak_left_db = -60.0
        mixer_p.peak_right_db = -60.0
        for tid in console.strips:
            mixer_p.track_peaks[tid] = (-60.0, -60.0)
        console._update_meters()
        qapp.processEvents()

    assert console.lbl_master_peak_db.text() == "-∞ dB"
    assert console.strips[first_tid].lbl_peak_db.text() == "-∞ dB"
    assert css_calls == 0, f"Expected 0 setStyleSheet calls during meter ticks, got {css_calls}"
    console.close()


def test_mixer_meter_dirty_gate_skips_redundant_repaints(qapp, monkeypatch):
    vu_paints = 0
    lbl_paints = 0
    orig_vu_paint = VuMeterBar.paintEvent
    orig_lbl_paint = PeakDbLabel.paintEvent

    def spy_vu(self, ev):
        nonlocal vu_paints
        vu_paints += 1
        return orig_vu_paint(self, ev)

    def spy_lbl(self, ev):
        nonlocal lbl_paints
        lbl_paints += 1
        return orig_lbl_paint(self, ev)

    monkeypatch.setattr(VuMeterBar, "paintEvent", spy_vu)
    monkeypatch.setattr(PeakDbLabel, "paintEvent", spy_lbl)

    console = _build_mixer_console(8)
    qapp.processEvents()

    mixer_p = console.mixer

    # Settle at steady -12.0 dB (must actually trigger paints on the transition!)
    vu_paints = 0
    lbl_paints = 0
    mixer_p.peak_left_db = -12.0
    mixer_p.peak_right_db = -12.0
    for tid in console.strips:
        mixer_p.track_peaks[tid] = (-12.0, -12.0)
    console._update_meters()
    qapp.processEvents()
    assert vu_paints >= 9 and lbl_paints >= 9

    vu_paints = 0
    lbl_paints = 0
    t0 = time.perf_counter()
    for _ in range(60):
        mixer_p.peak_left_db = -12.0
        mixer_p.peak_right_db = -12.0
        for tid in console.strips:
            mixer_p.track_peaks[tid] = (-12.0, -12.0)
        console._update_meters()
        qapp.processEvents()
    steady_ms = (time.perf_counter() - t0) * 1000.0
    assert vu_paints == 0
    assert lbl_paints == 0
    assert steady_ms < 25.0

    # Settle at silent -60.0 dB
    mixer_p.peak_left_db = -60.0
    mixer_p.peak_right_db = -60.0
    for tid in console.strips:
        mixer_p.track_peaks[tid] = (-60.0, -60.0)
    console._update_meters()
    qapp.processEvents()

    vu_paints = 0
    lbl_paints = 0
    t0 = time.perf_counter()
    for _ in range(60):
        console._update_meters()
        qapp.processEvents()
    silent_ms = (time.perf_counter() - t0) * 1000.0
    assert vu_paints == 0
    assert lbl_paints == 0
    assert silent_ms < 25.0
    console.close()


def test_mixer_meter_pixel_colors_and_0db_marker(qapp):
    vu = VuMeterBar()
    vu.setFixedWidth(14)
    vu.setFixedHeight(130)
    vu.show()

    # 1. Silent (-60 dB): background #0b0d12, 1px border #1f2430, 0 dB line #f87171
    vu.set_levels(-60.0, -60.0)
    qapp.processEvents()
    img_silent = vu.grab().toImage()

    assert QColor(img_silent.pixel(0, 0)) == QColor("#1f2430")
    assert QColor(img_silent.pixel(4, 65)) == QColor("#0b0d12")
    y_zero = int(130 - 2 - (130 - 4) * (48.0 / 51.0))
    assert QColor(img_silent.pixel(4, y_zero)) == QColor(248, 113, 113)

    # 2. Hot (+2.0 dB): green near bottom, yellow around 70%, red near top
    vu.set_levels(2.0, 2.0)
    qapp.processEvents()
    img_hot = vu.grab().toImage()

    c_bot = QColor(img_hot.pixel(4, 126))
    c_mid = QColor(img_hot.pixel(4, 42))
    c_top = QColor(img_hot.pixel(4, 5))
    assert c_bot.green() > c_bot.red() and c_bot.green() > 150
    assert c_mid.red() > 150 and c_mid.green() > 140
    assert c_top.red() > 200 and c_top.green() < 100

    # 3. Mid (-18.0 dB): pixels well above bar height remain #0b0d12
    vu.set_levels(-18.0, -18.0)
    qapp.processEvents()
    img_mid = vu.grab().toImage()
    assert QColor(img_mid.pixel(4, 20)) == QColor("#0b0d12")
    vu.close()


def test_mixer_meter_active_throughput_budget(qapp, monkeypatch):
    console = _build_mixer_console(16)
    qapp.processEvents()

    css_calls = 0
    orig_set_ss = QWidget.setStyleSheet

    def spy_ss(self, ss):
        nonlocal css_calls
        css_calls += 1
        return orig_set_ss(self, ss)

    monkeypatch.setattr(QWidget, "setStyleSheet", spy_ss)

    mixer_p = console.mixer
    ticks = 300
    t0 = time.perf_counter()
    for i in range(ticks):
        lvl = -22.0 + (i % 20) * 1.1
        mixer_p.peak_left_db = lvl
        mixer_p.peak_right_db = lvl - 1.5
        for idx, tid in enumerate(console.strips):
            t_lvl = -24.0 + ((i + idx) % 22) * 1.1
            mixer_p.track_peaks[tid] = (t_lvl, t_lvl - 0.8)
        console._update_meters()
        qapp.processEvents()
    active_avg_ms = ((time.perf_counter() - t0) * 1000.0) / ticks

    # Silent throughput (after settling)
    mixer_p.peak_left_db = -60.0
    mixer_p.peak_right_db = -60.0
    for tid in console.strips:
        mixer_p.track_peaks[tid] = (-60.0, -60.0)
    console._update_meters()
    qapp.processEvents()

    t1 = time.perf_counter()
    for _ in range(ticks):
        console._update_meters()
        qapp.processEvents()
    silent_avg_ms = ((time.perf_counter() - t1) * 1000.0) / ticks

    assert css_calls == 0
    assert active_avg_ms < 3.5, f"Active 16-strip metering too slow: {active_avg_ms:.3f} ms/tick"
    assert silent_avg_ms < 0.2, f"Silent 16-strip metering too slow: {silent_avg_ms:.3f} ms/tick"
    console.close()


# ---------------------------------------------------------------------------
# 5. M3 Iteration 2 Edge-Case Regression Tests
# ---------------------------------------------------------------------------

def test_vu_meter_and_peak_label_nan_inf_safety(qapp):
    vu = VuMeterBar()
    vu.setFixedSize(14, 130)
    vu.show()

    vu.set_levels(float("nan"), -10.0)
    qapp.processEvents()
    img_nan = vu.grab().toImage()
    # Left bar (x=3..6) must remain background #0b0d12, not full bar
    assert QColor(img_nan.pixel(4, 20)) == QColor("#0b0d12")

    vu.set_levels(float("-inf"), float("+inf"))
    qapp.processEvents()
    img_inf = vu.grab().toImage()
    assert QColor(img_inf.pixel(4, 120)) == QColor("#0b0d12")  # -inf -> 0 height
    assert QColor(img_inf.pixel(9, 6)) != QColor("#0b0d12")    # +inf -> clamped to top

    # Even if peak_left_db is mutated directly to NaN before paintEvent
    vu.peak_left_db = float("nan")
    vu.peak_right_db = float("nan")
    vu.repaint()
    qapp.processEvents()
    vu.close()

    lbl = PeakDbLabel()
    lbl.set_level_db(-3.0)
    assert lbl.text() == "-3.0 dB"
    lbl.set_level_db(float("nan"))
    assert lbl.text() == "-∞ dB"
    assert lbl._color == PeakDbLabel._C_SILENT

    console = _build_mixer_console(2)
    first_tid = next(iter(console.strips.keys()))
    console.mixer.peak_left_db = float("nan")
    console.mixer.peak_right_db = float("nan")
    console.mixer.track_peaks[first_tid] = (float("nan"), -12.0)
    console._update_meters()
    qapp.processEvents()
    assert console.lbl_master_peak_db.text() == "-∞ dB"
    assert console.strips[first_tid].lbl_peak_db.text() == "-12.0 dB"
    console.close()


def test_timeline_grid_inplace_audio_mutation_invalidates_waveform_cache(qapp):
    proj = Project(bpm=120.0)
    track = Track(name="Audio", track_type="audio")
    clip = AudioClip(
        name="C",
        start_beat=1.0,
        length_beats=8.0,
        audio_data=np.full(88200, 0.8, np.float32),
        sample_rate=44100,
    )
    track.clips.append(clip)
    proj.add_track(track)

    g1 = TimelineGrid(proj)
    g1.setFixedSize(600, 120)
    g1.show()
    qapp.processEvents()

    # In-place mutation keeping id(clip.audio_data) and shape identical
    clip.audio_data[:] = 0.01
    g1.update()
    qapp.processEvents()
    img1 = g1.grab().toImage().convertToFormat(QImage.Format_ARGB32)

    g2 = TimelineGrid(proj)
    g2.setFixedSize(600, 120)
    g2.show()
    qapp.processEvents()
    img2 = g2.grab().toImage().convertToFormat(QImage.Format_ARGB32)

    assert _count_differing_pixels(img1, img2) == 0
    g1.close()
    g2.close()


def test_note_grid_narrow_note_label_overflow_dirty_rect_equivalence(qapp):
    ng = NoteGridWidget(start_pitch=0, num_pitches=36, row_height=18)
    ng.set_clip(MidiClip(notes=[MidiNote(pitch=1, start_beat=1.0, duration=0.45)]))
    ng.pixels_per_beat = 60.0
    ng.setFixedSize(960, 648)

    full = QImage(ng.size(), QImage.Format_ARGB32)
    full.fill(0)
    ng.render(full, QPoint(0, 0), QRegion(ng.rect()))

    dirty_img = full.copy()
    ng.render(dirty_img, QPoint(99, 0), QRegion(99, 0, 17, 648))

    assert _count_differing_pixels(full, dirty_img) == 0


def test_subpixel_playhead_updates_on_ruler_and_note_grid(qapp, monkeypatch):
    ruler = TimelineRuler()
    ruler.setFixedSize(400, 30)
    ruler.set_zoom(40.0)
    ruler.playhead_beat = 100.1 / 40.0
    img_ruler_dirty = _simulate_dirty_playhead_renders(
        ruler, [100.1 / 40.0, 100.9 / 40.0], monkeypatch
    )
    img_ruler_full = QImage(ruler.size(), QImage.Format_ARGB32)
    img_ruler_full.fill(0)
    ruler.render(img_ruler_full, QPoint(0, 0), QRegion(ruler.rect()))
    assert _count_differing_pixels(img_ruler_dirty, img_ruler_full) == 0

    ng = NoteGridWidget(start_pitch=48, num_pitches=24, row_height=18)
    ng.set_clip(MidiClip(start_beat=0.0, length_beats=10.0, notes=[]))
    ng.pixels_per_beat = 60.0
    ng.setFixedSize(400, 24 * 18)
    ng.playhead_project_beat = 100.1 / 60.0
    img_ng_dirty = _simulate_dirty_playhead_renders(
        ng, [100.1 / 60.0, 100.9 / 60.0], monkeypatch
    )
    img_ng_full = QImage(ng.size(), QImage.Format_ARGB32)
    img_ng_full.fill(0)
    ng.render(img_ng_full, QPoint(0, 0), QRegion(ng.rect()))
    assert _count_differing_pixels(img_ng_dirty, img_ng_full) == 0


# ---------------------------------------------------------------------------
# 6. M3 Iteration 3 Regression Tests (Sub-Pixel Accumulation & Cache Hook)
# ---------------------------------------------------------------------------

def test_gradual_subpixel_playhead_accumulation_no_freeze(qapp, monkeypatch):
    # 75 ticks of +0.02 px from 100.0 -> 101.5 px (ends on exact multiple of 0.06 px)
    ruler = TimelineRuler()
    ruler.setFixedSize(400, 30)
    ruler.set_zoom(40.0)
    ruler.playhead_beat = 100.0 / 40.0

    ruler_update_calls = 0
    orig_ruler_update = ruler.update

    def count_ruler_update(*args):
        nonlocal ruler_update_calls
        ruler_update_calls += 1
        return orig_ruler_update(*args)

    monkeypatch.setattr(ruler, "update", count_ruler_update)
    ruler_beats = [(100.0 + i * 0.02) / 40.0 for i in range(76)]
    img_ruler_dirty = _simulate_dirty_playhead_renders(ruler, ruler_beats, monkeypatch)
    img_ruler_full = QImage(ruler.size(), QImage.Format_ARGB32)
    img_ruler_full.fill(0)
    ruler.render(img_ruler_full, QPoint(0, 0), QRegion(ruler.rect()))
    assert ruler_update_calls > 0
    assert _count_differing_pixels(img_ruler_dirty, img_ruler_full) == 0

    ng = NoteGridWidget(start_pitch=48, num_pitches=24, row_height=18)
    ng.set_clip(MidiClip(start_beat=0.0, length_beats=10.0, notes=[]))
    ng.pixels_per_beat = 60.0
    ng.setFixedSize(400, 24 * 18)
    ng.playhead_project_beat = 100.0 / 60.0

    ng_update_calls = 0
    orig_ng_update = ng.update

    def count_ng_update(*args):
        nonlocal ng_update_calls
        ng_update_calls += 1
        return orig_ng_update(*args)

    monkeypatch.setattr(ng, "update", count_ng_update)
    ng_beats = [(100.0 + i * 0.02) / 60.0 for i in range(76)]
    img_ng_dirty = _simulate_dirty_playhead_renders(ng, ng_beats, monkeypatch)
    img_ng_full = QImage(ng.size(), QImage.Format_ARGB32)
    img_ng_full.fill(0)
    ng.render(img_ng_full, QPoint(0, 0), QRegion(ng.rect()))
    assert ng_update_calls > 0
    assert _count_differing_pixels(img_ng_dirty, img_ng_full) == 0


def test_zoomed_out_60bpm_60hz_playhead_no_freeze(qapp, monkeypatch):
    ppb = 2.5
    dt = 1.0 / 60.0

    # 1. Mocked update() without paintEvent (60 BPM @ 60 Hz, 180 frames = +7.5 px)
    proj = Project(bpm=60.0)
    proj.add_track(Track(name="T1", track_type="midi"))
    tg = TimelineGrid(proj)
    tg.setFixedSize(600, 120)
    tg.pixels_per_beat = ppb
    tg.playhead_beat = 10.0

    tr = TimelineRuler()
    tr.setFixedSize(600, 32)
    tr.pixels_per_beat = ppb
    tr.playhead_beat = 10.0

    ng = NoteGridWidget(start_pitch=36, num_pitches=36, row_height=18)
    ng.set_clip(MidiClip(start_beat=0.0, length_beats=20.0, notes=[]))
    ng.setFixedSize(600, 36 * 18)
    ng.pixels_per_beat = ppb
    ng.playhead_project_beat = 10.0

    counts = [0, 0, 0]
    monkeypatch.setattr(tg, "update", lambda *a: counts.__setitem__(0, counts[0] + 1))
    monkeypatch.setattr(tr, "update", lambda *a: counts.__setitem__(1, counts[1] + 1))
    monkeypatch.setattr(ng, "update", lambda *a: counts.__setitem__(2, counts[2] + 1))
    for f in range(1, 181):
        b = 10.0 + f * dt
        tg.set_playhead(b)
        tr.set_playhead(b)
        ng.set_playhead(b)
    assert counts[0] > 0 and counts[1] > 0 and counts[2] > 0

    # Non-finite beats are ignored safely without corrupting state
    tr.set_playhead(float("nan"))
    ng.set_playhead(float("inf"))
    assert math.isfinite(tr.playhead_beat)
    assert math.isfinite(ng.playhead_project_beat)

    # 2. Rendered pixel-equivalence over the same 180 frames (+7.5 px)
    monkeypatch.undo()
    beats = [10.0 + f * dt for f in range(181)]
    for widget, reset_attr in ((tg, "playhead_beat"), (tr, "playhead_beat"), (ng, "playhead_project_beat")):
        setattr(widget, reset_attr, 10.0)
        if hasattr(widget, "_drawn_playhead_px"):
            widget._drawn_playhead_px = None
            widget._expected_playhead_beat = None
            widget._drawn_playhead_xform = None
        img_dirty = _simulate_dirty_playhead_renders(widget, beats, monkeypatch)
        img_full = QImage(widget.size(), QImage.Format_ARGB32)
        img_full.fill(0)
        widget.render(img_full, QPoint(0, 0), QRegion(widget.rect()))
        assert _count_differing_pixels(img_dirty, img_full) == 0


def test_subpixel_jitter_and_post_paint_direct_mutation(qapp, monkeypatch):
    # Pre-show < 0.05 px jitter emits 0 updates, then crossing >= 0.05 px emits update
    ruler = TimelineRuler()
    ruler.setFixedSize(400, 30)
    ruler.set_zoom(40.0)
    ruler.playhead_beat = 100.0 / 40.0
    tr_updates = []
    monkeypatch.setattr(ruler, "update", lambda *args: tr_updates.append(args))
    ruler.set_playhead(100.02 / 40.0)
    assert len(tr_updates) == 0
    assert math.isclose(ruler.playhead_beat, 100.02 / 40.0)
    ruler.set_playhead(100.06 / 40.0)
    assert len(tr_updates) == 1

    ng_pre = NoteGridWidget(start_pitch=48, num_pitches=24, row_height=18)
    ng_pre.set_clip(MidiClip(start_beat=0.0, length_beats=10.0, notes=[]))
    ng_pre.pixels_per_beat = 60.0
    ng_pre.setFixedSize(400, 24 * 18)
    ng_pre.playhead_project_beat = 100.0 / 60.0
    ng_pre_updates = []
    monkeypatch.setattr(ng_pre, "update", lambda *args: ng_pre_updates.append(args))
    ng_pre.set_playhead(100.02 / 60.0)
    assert len(ng_pre_updates) == 0
    assert math.isclose(ng_pre.playhead_project_beat, 100.02 / 60.0)
    ng_pre.set_playhead(100.06 / 60.0)
    assert len(ng_pre_updates) == 2  # body + top 14px triangle band

    # Post-paint direct mutation on NoteGridWidget preserves exact old_px dirty rect
    ng = NoteGridWidget(start_pitch=48, num_pitches=24, row_height=18)
    ng.set_clip(MidiClip(start_beat=10.0, length_beats=16.0, notes=[]))
    ng.pixels_per_beat = 60.0
    ng.setFixedSize(800, 24 * 18)
    ng.show()
    qapp.processEvents()

    ng_post_updates = []
    monkeypatch.setattr(ng, "update", lambda *args: ng_post_updates.append(args))
    ng.playhead_project_beat = 11.0
    ng.set_playhead(11.1)
    assert ng_post_updates[0][0] == 56  # (11.0 - 10.0) * 60 - 4
    ng.close()


def test_timeline_grid_invalidate_waveform_cache_localized_edit(qapp):
    proj = Project(bpm=120.0)
    track = Track(name="Audio", track_type="audio")
    clip = AudioClip(
        name="LocalEdit",
        start_beat=1.0,
        length_beats=8.0,
        audio_data=np.full(88200, 0.2, np.float32),
        sample_rate=44100,
    )
    track.clips.append(clip)
    proj.add_track(track)

    g1 = TimelineGrid(proj)
    g1.setFixedSize(600, 120)
    g1.show()
    qapp.processEvents()
    assert clip.id in g1._waveform_peak_cache

    # Localized sub-stride edit (between 64-stride sample indices 0 and 1378)
    clip.audio_data[1:200] = 0.99
    g1.invalidate_waveform_cache(clip)
    assert clip.id not in g1._waveform_peak_cache

    g1.update()
    qapp.processEvents()
    img1 = g1.grab().toImage().convertToFormat(QImage.Format_ARGB32)

    g2 = TimelineGrid(proj)
    g2.setFixedSize(600, 120)
    g2.show()
    qapp.processEvents()
    img2 = g2.grab().toImage().convertToFormat(QImage.Format_ARGB32)

    assert _count_differing_pixels(img1, img2) == 0

    # Also verify int(id(audio_data)), str(clip.id), and None clear variants
    assert clip.id in g2._waveform_peak_cache
    g2.invalidate_waveform_cache(id(clip.audio_data))
    assert clip.id not in g2._waveform_peak_cache
    g2.update()
    qapp.processEvents()
    assert clip.id in g2._waveform_peak_cache
    g2.invalidate_waveform_cache(clip.id)
    assert clip.id not in g2._waveform_peak_cache
    g2.update()
    qapp.processEvents()
    assert clip.id in g2._waveform_peak_cache
    g2.invalidate_waveform_cache()
    assert len(g2._waveform_peak_cache) == 0

    g1.close()
    g2.close()
