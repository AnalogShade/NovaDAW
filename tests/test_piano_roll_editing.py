"""Editing behavior through real Qt input, without opening audio hardware."""
from unittest.mock import Mock
import pytest
from PySide6.QtCore import Qt, QPoint, QPointF, QEvent
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from core.project import MidiClip, MidiNote, Track
from ui.piano_roll import NoteGridWidget, PianoRoll


@pytest.fixture
def grid(qapp):
    widget = NoteGridWidget(start_pitch=0, num_pitches=128)
    widget.set_clip(MidiClip(notes=[MidiNote(60, 0.12, 0.5, 64), MidiNote(64, 1.12, 1, 90)]))
    yield widget
    widget.close()


def press(grid, x, pitch, button=Qt.LeftButton, modifiers=Qt.NoModifier):
    QTest.mousePress(grid, button, modifiers, QPoint(int(x), int(grid.y_for_pitch(pitch) + 8)))


def release(grid, x, pitch):
    QTest.mouseRelease(grid, Qt.LeftButton, Qt.NoModifier,
                       QPoint(int(x), int(grid.y_for_pitch(pitch) + 8)))


def move(grid, x, pitch):
    point = QPointF(x, grid.y_for_pitch(pitch) + 8)
    event = QMouseEvent(QEvent.MouseMove, point, point, Qt.NoButton, Qt.LeftButton, Qt.NoModifier)
    grid.mouseMoveEvent(event)


def test_select_quantize_transpose_velocity_and_undo(grid):
    original = grid.clip.to_dict()
    grid.select_all()
    QTest.keyClick(grid, Qt.Key_Q)
    assert [n.start_beat for n in grid.clip.notes] == [0, 1]
    QTest.keyClick(grid, Qt.Key_Up, Qt.ShiftModifier)
    assert [n.pitch for n in grid.clip.notes] == [72, 76]
    grid.set_velocity(42)
    assert [n.velocity for n in grid.clip.notes] == [42, 42]
    for _ in range(3):
        QTest.keyClick(grid, Qt.Key_Z, Qt.ControlModifier)
    assert grid.clip.to_dict() == original
    for _ in range(3):
        QTest.keyClick(grid, Qt.Key_Y, Qt.ControlModifier)
    assert [n.velocity for n in grid.clip.notes] == [42, 42]
    assert [n.pitch for n in grid.clip.notes] == [72, 76]


def test_ctrl_click_and_group_drag_keep_intervals(grid):
    a, b = grid.clip.notes
    press(grid, 15, 60)
    release(grid, 15, 60)
    press(grid, 75, 64, modifiers=Qt.ControlModifier)
    release(grid, 75, 64)
    assert len(grid.selected_notes) == 2
    press(grid, 15, 60)
    move(grid, 75, 62)
    move(grid, 135, 62)
    release(grid, 135, 62)
    assert b.start_beat - a.start_beat == pytest.approx(1)
    assert b.pitch - a.pitch == 4
    assert a.pitch == 62
    assert len(grid._undo) == 1  # A gesture, not each mouse move.
    grid.undo()
    assert [n.pitch for n in grid.clip.notes] == [60, 64]
    assert grid.clip.notes[0].start_beat == .12


def test_rectangle_selection_and_group_resize(grid):
    press(grid, 0, 66, modifiers=Qt.ControlModifier)
    move(grid, 140, 58)
    release(grid, 140, 58)
    assert len(grid.selected_notes) == 2
    a, b = grid.clip.notes
    press(grid, (a.start_beat + a.duration) * 60 - 1, 60)
    move(grid, (a.start_beat + a.duration) * 60 + 29, 60)
    release(grid, 70, 60)
    assert a.duration == 1
    assert b.duration == 1.5
    grid.undo()
    assert [n.duration for n in grid.clip.notes] == [.5, 1]


def test_identical_notes_can_be_deleted_individually(grid):
    grid.clip.notes = [MidiNote(60, 0, 1), MidiNote(60, 0, 1)]
    press(grid, 20, 60, Qt.RightButton)
    assert len(grid.clip.notes) == 1
    grid.undo()
    assert len(grid.clip.notes) == 2


def test_duplicate_clipboard_and_auto_extend(grid):
    grid.select_all()
    grid.copy_selected()
    grid.set_playhead(12)
    grid.paste_notes()
    assert len(grid.clip.notes) == 4
    assert grid.clip.notes[2].start_beat == 12
    assert grid.clip.length_beats == 14
    assert grid.clip.notes[2] is not grid.clip.notes[0]
    grid.undo()
    assert grid.clip.length_beats == 4
    assert len(grid.clip.notes) == 2
    grid.select_all()
    grid.duplicate_selected()
    assert len(grid.clip.notes) == 4
    assert grid.clip.notes[2].start_beat == 2.12


def test_creation_grows_clip_and_clear_is_reversible(grid):
    press(grid, 600, 20)
    release(grid, 600, 20)
    assert grid.clip.length_beats == 11
    assert grid.clip.notes[-1].pitch == 20
    grid.clear_notes()
    assert grid.clip.notes == []
    grid.undo()
    assert len(grid.clip.notes) == 3
    grid.undo()
    assert len(grid.clip.notes) == 2
    assert grid.clip.length_beats == 4


def test_pitch_and_time_group_boundaries(grid):
    grid.clip.notes = [MidiNote(1, .1, 1), MidiNote(126, 1.1, 1)]
    grid.select_all()
    grid.move_selected(semitones=12, beats=-5)
    assert [n.pitch for n in grid.clip.notes] == [2, 127]
    assert [n.start_beat for n in grid.clip.notes] == [0, 1]
    grid.move_selected(semitones=-12)
    assert [n.pitch for n in grid.clip.notes] == [0, 125]
    assert grid.pitch_at_y(-1000) == 127
    assert grid.pitch_at_y(10000) == 0


def test_external_edits_and_clip_switch_invalidate_history(grid):
    grid.select_all()
    grid.move_selected(semitones=1)
    grid.clip.notes.append(MidiNote(80, 0, 1))
    grid.undo()
    assert len(grid.clip.notes) == 3
    assert grid.clip.notes[0].pitch == 61
    grid.select_all()
    grid.move_selected(semitones=1)
    grid.set_clip(MidiClip())
    grid.undo()
    assert grid.clip.notes == []


def test_same_clip_refresh_preserves_selection_and_history(grid):
    grid.select_all()
    grid.move_selected(semitones=1)
    grid.set_clip(grid.clip)
    assert len(grid.selected_notes) == 2
    grid.undo()
    assert grid.clip.notes[0].pitch == 60


def test_no_op_does_not_dirty_clip_and_redo_cleared_by_edit(grid):
    changes = []
    grid.notes_changed.connect(lambda: changes.append(1))
    grid.quantize_selected()
    grid.undo()
    assert not changes
    grid.select_all()
    grid.move_selected(semitones=1)
    grid.undo()
    grid.select_all()
    grid.set_velocity(1)
    grid.redo()
    assert grid.clip.notes[0].pitch == 60
    assert grid.clip.notes[0].velocity == 1


def test_switch_track_releases_preview_on_original_track(qapp):
    engine = Mock()
    roll = PianoRoll(engine)
    a, b = Track(), Track()
    a.clips = [MidiClip(notes=[MidiNote(60, 0, 1)])]
    roll.set_active_track(a)
    press(roll.note_grid, 15, 60)
    roll.set_active_track(b)
    engine.stop_live_note.assert_called_with(60, track=a)
    assert roll.note_grid.num_pitches == 128
    roll.close()


def test_offset_playhead_updates_relative_pixels(grid, monkeypatch):
    grid.clip.start_beat = 100
    grid.playhead_project_beat = 101
    updates = []
    monkeypatch.setattr(grid, 'update', lambda *args: updates.append(args))
    grid.set_playhead(101.1)
    assert updates[0][0] == 56


def test_window_shortcuts_edit_notes_and_invalidate_correct_track(qapp, monkeypatch):
    from ui.main_window import MainWindow
    window = MainWindow()
    try:
        track = Track(name='Edited')
        clip = MidiClip(notes=[MidiNote(60, 0, 1)])
        track.clips = [clip]
        window.project.add_track(track)
        window.refresh_project_ui()
        window.piano_roll.open_clip(track, clip)
        window.lower_zone.setCurrentWidget(window.piano_roll)
        window.show()
        window.activateWindow()
        window.piano_roll.note_grid.setFocus()
        qapp.processEvents()
        grid = window.piano_roll.note_grid
        grid.select_all()
        calls = []
        monkeypatch.setattr(window.audio_engine, 'invalidate_track_cache',
                            lambda track_id, **kw: calls.append(track_id))
        QTest.keyClick(grid, Qt.Key_D, Qt.ControlModifier)
        assert len(clip.notes) == 2
        assert len(track.clips) == 1  # Global QAction must not duplicate the clip.
        QTest.keyClick(grid, Qt.Key_Delete)
        assert len(clip.notes) == 1
        assert len(track.clips) == 1
        assert window.has_unsaved_changes()
        assert calls and set(calls) == {track.id}
        QTest.keyClick(grid, Qt.Key_Z, Qt.ControlModifier)
        assert len(clip.notes) == 2
    finally:
        window.set_dirty(False)
        window.close()


def test_external_edit_cancels_pending_drag(grid):
    press(grid, 15, 60)
    grid.clip.notes.append(MidiNote(80, 0, 1))
    grid.set_clip(grid.clip)
    move(grid, 135, 62)
    release(grid, 135, 62)
    assert grid.clip.notes[0].pitch == 60
    grid.undo()
    assert len(grid.clip.notes) == 3


def test_modified_navigation_shortcuts_do_not_edit_notes(grid):
    grid.select_all()
    before = grid.clip.to_dict()
    QTest.keyClick(grid, Qt.Key_Up, Qt.AltModifier)
    QTest.keyClick(grid, Qt.Key_Left, Qt.ControlModifier)
    assert grid.clip.to_dict() == before


def test_clear_button_then_keyboard_undo(qapp):
    roll = PianoRoll(Mock())
    track = Track()
    clip = MidiClip(notes=[MidiNote(60, 0, 1)])
    roll.open_clip(track, clip)
    roll.show()
    roll.activateWindow()
    qapp.processEvents()
    QTest.mouseClick(roll.btn_clear, Qt.LeftButton)
    assert not clip.notes
    assert roll.note_grid.hasFocus()
    QTest.keyClick(roll.note_grid, Qt.Key_Z, Qt.ControlModifier)
    assert len(clip.notes) == 1
    roll.close()
