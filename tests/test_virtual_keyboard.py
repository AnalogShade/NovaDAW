"""
tests/test_virtual_keyboard.py - Tests unitaires pour le Clavier Musical Virtuel (Musical Typing)
"""
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

from core.audio_engine import AudioEngine
from core.project import Project, Track
from ui.virtual_keyboard import VirtualKeyboardDialog, KEY_NOTE_MAP, pitch_to_name


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def test_pitch_to_name():
    assert pitch_to_name(48) == "C3"
    assert pitch_to_name(49) == "C#3"
    assert pitch_to_name(60) == "C4"
    assert pitch_to_name(72) == "C5"


def test_key_note_map_coverage():
    # Touches Do / C
    assert KEY_NOTE_MAP[Qt.Key_A][0] == 0
    assert KEY_NOTE_MAP[Qt.Key_Q][0] == 0
    # Touches Do# / C#
    assert KEY_NOTE_MAP[Qt.Key_W][0] == 1
    assert KEY_NOTE_MAP[Qt.Key_Z][0] == 1
    # Octave supérieure
    assert KEY_NOTE_MAP[Qt.Key_K][0] == 12
    assert KEY_NOTE_MAP[Qt.Key_O][0] == 13
    assert KEY_NOTE_MAP[Qt.Key_L][0] == 14


def test_virtual_keyboard_dialog_note_play(app):
    engine = AudioEngine()
    project = Project()
    track = Track(name="Synth", track_type="midi")
    project.tracks.append(track)
    engine.set_project(project)

    dlg = VirtualKeyboardDialog(engine)
    dlg.set_active_track(track)
    assert dlg.current_track == track

    # Appui sur la touche A (Do / C, base_pitch=48 -> pitch 48)
    handled = dlg.handle_key_press(Qt.Key_A)
    assert handled is True
    assert Qt.Key_A in dlg.held_keys_pitches
    assert dlg.held_keys_pitches[Qt.Key_A] == 48
    assert 48 in dlg.piano_widget.active_pitches

    # Relâchement de la touche A
    handled_rel = dlg.handle_key_release(Qt.Key_A)
    assert handled_rel is True
    assert Qt.Key_A not in dlg.held_keys_pitches
    assert 48 not in dlg.piano_widget.active_pitches

    engine.close()


def test_virtual_keyboard_octave_and_velocity_shift(app):
    engine = AudioEngine()
    dlg = VirtualKeyboardDialog(engine)

    initial_base = dlg.base_pitch
    # Octave + (Touche X)
    handled_x = dlg.handle_key_press(Qt.Key_X)
    assert handled_x is True
    assert dlg.base_pitch == initial_base + 12

    # Octave - (Touche Z)
    handled_z = dlg.handle_key_press(Qt.Key_Z)
    assert handled_z is True
    assert dlg.base_pitch == initial_base

    # Vélocité + (Touche V)
    initial_vel = dlg.velocity
    handled_v = dlg.handle_key_press(Qt.Key_V)
    assert handled_v is True
    assert dlg.velocity == min(127, initial_vel + 15)

    # Vélocité - (Touche C)
    handled_c = dlg.handle_key_press(Qt.Key_C)
    assert handled_c is True
    assert dlg.velocity == initial_vel

    engine.close()


def test_main_window_musical_typing_toggle(app):
    from ui.main_window import MainWindow

    win = MainWindow()
    assert hasattr(win, "virtual_keyboard_dialog")
    assert hasattr(win, "toggle_musical_typing")

    # Initialement masqué
    assert not win.virtual_keyboard_dialog.isVisible()
    assert not win.transport_bar.btn_musical_typing.isChecked()

    # Toggle avec Ctrl+K / fonction
    win.toggle_musical_typing()
    assert win.virtual_keyboard_dialog.isVisible()
    assert win.transport_bar.btn_musical_typing.isChecked()

    # Toggle à nouveau pour désactiver
    win.toggle_musical_typing()
    assert not win.virtual_keyboard_dialog.isVisible()
    assert not win.transport_bar.btn_musical_typing.isChecked()

    win.audio_engine.close()


def test_musical_typing_suspends_conflicting_single_key_shortcuts(app):
    from ui.main_window import MainWindow
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtCore import QEvent

    win = MainWindow()
    win.show()

    # Trouver l'action Aimantage / Snap (touche 'J')
    act_snap = None
    for a in win.findChildren(object):
        if hasattr(a, 'text') and 'Aimantage' in a.text():
            act_snap = a
            break

    assert act_snap is not None
    assert act_snap.shortcut().toString() == "J"
    initial_snap = win.editing_toolbar.btn_snap.isChecked()

    # 1. Activation du Clavier Musical
    win.toggle_musical_typing()
    app.processEvents()

    # Les raccourcis mono-touches (J, F, L, etc.) doivent être suspendus
    assert act_snap.shortcut().toString() == ""
    assert len(win._suspended_shortcuts) > 0

    # Événement ShortcutOverride sur la touche J doit être intercepté
    ev_override = QKeyEvent(QEvent.ShortcutOverride, Qt.Key_J, Qt.NoModifier, "j")
    app.sendEvent(win, ev_override)
    assert ev_override.isAccepted() is True

    # Appui sur J doit jouer la note Si/B (pitch 59 pour base C3=48) et ne PAS toucher au Snap
    ev_press = QKeyEvent(QEvent.KeyPress, Qt.Key_J, Qt.NoModifier, "j")
    app.sendEvent(win, ev_press)
    app.processEvents()

    assert Qt.Key_J in win.virtual_keyboard_dialog.held_keys_pitches
    assert win.virtual_keyboard_dialog.held_keys_pitches[Qt.Key_J] == 59
    assert win.editing_toolbar.btn_snap.isChecked() == initial_snap

    # Relâchement de la touche J
    ev_release = QKeyEvent(QEvent.KeyRelease, Qt.Key_J, Qt.NoModifier, "j")
    app.sendEvent(win, ev_release)
    app.processEvents()
    assert Qt.Key_J not in win.virtual_keyboard_dialog.held_keys_pitches

    # 2. Désactivation du Clavier Musical
    win.toggle_musical_typing()
    app.processEvents()

    # Les raccourcis doivent être immédiatement et intégralement rétablis
    assert act_snap.shortcut().toString() == "J"
    assert len(win._suspended_shortcuts) == 0

    win.audio_engine.close()

