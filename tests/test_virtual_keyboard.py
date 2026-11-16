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


def test_musical_typing_when_plugin_dialog_has_focus(app):
    """Vérifie que les notes sont jouées même lorsque le focus est sur la fenêtre d'un plugin."""
    from ui.main_window import MainWindow
    from ui.plugin_dialogs import open_native_plugin_editor
    from plugins.synth.synth_plugin import NovaSynthPlugin
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtCore import QEvent

    win = MainWindow()
    win.show()

    track = win.project.tracks[0] if win.project.tracks else None
    if not track:
        track = Track(name="Synth", track_type="midi", plugin_path="novadaw.synth", plugin_name="NovaSynth")
        win.project.add_track(track)
        win.refresh_project_ui()
    win._on_track_selected(track.id)
    app.processEvents()

    # 1. Activer le Clavier Musical
    win.toggle_musical_typing()
    app.processEvents()
    assert win.virtual_keyboard_dialog.is_active is True

    # 2. Ouvrir l'éditeur du plugin
    synth = win.audio_engine.get_native_instrument(track)
    if not synth:
        synth = NovaSynthPlugin()
        track.plugins.append(synth)
    dlg = open_native_plugin_editor(synth, win, track=track)
    dlg.show()
    dlg.activateWindow()
    app.processEvents()

    # Le focus est sur la fenêtre du plugin (ou un sous-widget du plugin), pas sur le clavier virtuel
    assert win.virtual_keyboard_dialog != app.focusWidget()

    # 3. Presser la touche A (note Do/C3) pendant que le plugin a le focus
    focus_target = app.focusWidget() or dlg
    ev_a_press = QKeyEvent(QEvent.KeyPress, Qt.Key_A, Qt.NoModifier, "a")
    app.sendEvent(focus_target, ev_a_press)
    app.processEvents()

    assert Qt.Key_A in win.virtual_keyboard_dialog.held_keys_pitches
    assert win.virtual_keyboard_dialog.held_keys_pitches[Qt.Key_A] == win.virtual_keyboard_dialog.base_pitch
    assert win.virtual_keyboard_dialog.piano_widget.active_pitches == {win.virtual_keyboard_dialog.base_pitch}

    # 4. Relâcher la touche A
    ev_a_release = QKeyEvent(QEvent.KeyRelease, Qt.Key_A, Qt.NoModifier, "a")
    app.sendEvent(focus_target, ev_a_release)
    app.processEvents()

    assert Qt.Key_A not in win.virtual_keyboard_dialog.held_keys_pitches
    assert len(win.virtual_keyboard_dialog.piano_widget.active_pitches) == 0

    # 5. Décalage d'octave (+ [X]) depuis la fenêtre du plugin
    base_before = win.virtual_keyboard_dialog.base_pitch
    ev_x = QKeyEvent(QEvent.KeyPress, Qt.Key_X, Qt.NoModifier, "x")
    app.sendEvent(focus_target, ev_x)
    app.processEvents()
    assert win.virtual_keyboard_dialog.base_pitch == base_before + 12

    dlg.close()
    win.audio_engine.close()


def test_track_auto_sync_on_plugin_focus(app):
    """Vérifie que l'activation d'un éditeur de plugin synchronise automatiquement la piste active du clavier virtuel."""
    from ui.main_window import MainWindow
    from ui.plugin_dialogs import open_native_plugin_editor
    from plugins.synth.synth_plugin import NovaSynthPlugin
    from plugins.drum_machine.drum_plugin import DrumMachinePlugin

    win = MainWindow()
    win.show()

    t_drums = Track(name="Drums", track_type="midi", plugin_path="novadaw.drum_machine", plugin_name="Nova Drums")
    t_synth = Track(name="Synth", track_type="midi", plugin_path="novadaw.synth", plugin_name="NovaSynth")
    win.project.add_track(t_drums)
    win.project.add_track(t_synth)
    win.refresh_project_ui()

    drums_plugin = DrumMachinePlugin()
    t_drums.plugins.append(drums_plugin)
    synth_plugin = NovaSynthPlugin()
    t_synth.plugins.append(synth_plugin)

    # Sélectionner initialement Drums et activer le clavier musical
    win._on_track_selected(t_drums.id)
    win.toggle_musical_typing()
    app.processEvents()
    assert win.virtual_keyboard_dialog.current_track == t_drums

    # Ouvrir l'éditeur de Synth (piste 2) et lui donner le focus
    dlg = open_native_plugin_editor(synth_plugin, win, track=t_synth)
    dlg.show()
    dlg.activateWindow()
    dlg._sync_track_focus()
    app.processEvents()

    # Le clavier virtuel doit automatiquement cibler la piste Synth
    assert win.virtual_keyboard_dialog.current_track == t_synth
    assert "Synth" in win.virtual_keyboard_dialog.lbl_track.text()

    dlg.close()
    win.audio_engine.close()


def test_text_input_preserved_when_musical_typing_active(app):
    """Vérifie que la saisie dans un champ texte (QLineEdit) n'est pas interceptée par le clavier musical."""
    from ui.main_window import MainWindow
    from PySide6.QtWidgets import QLineEdit
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtCore import QEvent

    win = MainWindow()
    win.show()
    win.toggle_musical_typing()
    app.processEvents()
    assert win.virtual_keyboard_dialog.is_active is True

    # Créer un champ d'édition texte (ex: renommage de preset ou de piste)
    line_edit = QLineEdit(win)
    line_edit.show()
    line_edit.setFocus()
    app.processEvents()

    assert app.focusWidget() == line_edit

    # Taper 'A' dans le champ texte
    ev_a = QKeyEvent(QEvent.KeyPress, Qt.Key_A, Qt.NoModifier, "a")
    app.sendEvent(line_edit, ev_a)
    app.processEvents()

    # 'A' ne doit PAS déclencher de note musicale
    assert Qt.Key_A not in win.virtual_keyboard_dialog.held_keys_pitches

    line_edit.close()
    win.audio_engine.close()


def test_remote_vst_key_event_routing(app):
    """Vérifie que les événements distants (simulant les messages Win32 d'une fenêtre VST) sont bien traités."""
    from ui.virtual_keyboard import VirtualKeyboardDialog
    from core.audio_engine import AudioEngine
    from core.project import Project, Track

    engine = AudioEngine()
    project = Project()
    track = Track(name="Synth", track_type="midi")
    project.tracks.append(track)
    engine.set_project(project)

    vk = VirtualKeyboardDialog(engine)
    vk.set_active_track(track)
    vk.set_musical_typing_active(True)
    app.processEvents()

    # Événement Note-On distant (Key A)
    vk.remote_key_event.emit(Qt.Key_A, True)
    app.processEvents()
    assert Qt.Key_A in vk.held_keys_pitches

    # Événement Note-Off distant (Key A)
    vk.remote_key_event.emit(Qt.Key_A, False)
    app.processEvents()
    assert Qt.Key_A not in vk.held_keys_pitches

    vk.set_musical_typing_active(False)
    engine.close()


def test_keyboard_hook_vk_conversion():
    """Vérifie la conversion précise des virtual keys Windows (VK_*) vers Qt.Key."""
    from core.keyboard_hook import win_vk_to_qt_key, KEY_SEMICOLON, KEY_APOSTROPHE, KEY_UP, KEY_DOWN
    # Lettres A et Z
    assert win_vk_to_qt_key(0x41) == Qt.Key_A
    assert win_vk_to_qt_key(0x5A) == Qt.Key_Z
    # Touches de ponctuation et flèches
    assert win_vk_to_qt_key(0xBA) == KEY_SEMICOLON
    assert win_vk_to_qt_key(0xDE) == KEY_APOSTROPHE
    assert win_vk_to_qt_key(0x26) == KEY_UP
    assert win_vk_to_qt_key(0x28) == KEY_DOWN


def test_keyboard_hook_window_identification():
    """Vérifie la détection fiable des fenêtres appartenant à NovaDAW ou à ses plugins VST."""
    import os
    from core.keyboard_hook import LowLevelKeyboardHook
    from core.vst_host import register_plugin_pid, unregister_plugin_pid, get_active_plugin_pids
    from core.plugin_manager import global_plugin_manager

    hook = LowLevelKeyboardHook.get_instance()

    # Enregistrer un PID simulé de plugin VST
    simulated_vst_pid = 888777
    register_plugin_pid(simulated_vst_pid)
    assert simulated_vst_pid in get_active_plugin_pids()

    # Associer le PID à une piste dans global_plugin_manager
    global_plugin_manager.track_id_by_pid[simulated_vst_pid] = "test-track-123"
    assert global_plugin_manager.track_id_by_pid[simulated_vst_pid] == "test-track-123"

    # Nettoyage
    unregister_plugin_pid(simulated_vst_pid)
    assert simulated_vst_pid not in get_active_plugin_pids()
    global_plugin_manager.track_id_by_pid.pop(simulated_vst_pid, None)


def test_keyboard_hook_procedure_simulation():
    """Vérifie la logique d'interception du hook bas niveau : auto-répétition, Ctrl+K, modifieurs."""
    import ctypes
    from core.keyboard_hook import LowLevelKeyboardHook, KBDLLHOOKSTRUCT, WM_KEYDOWN, WM_KEYUP

    hook = LowLevelKeyboardHook.get_instance()

    captured_events = []
    toggled = []

    hook._key_callback = lambda k, down: captured_events.append((k, down))
    hook._toggle_callback = lambda: toggled.append(True)

    # Création d'une structure KBDLLHOOKSTRUCT simulée pour la touche A (0x41)
    kb_a = KBDLLHOOKSTRUCT(vkCode=0x41, scanCode=0, flags=0, time=0, dwExtraInfo=0)
    lparam_a = ctypes.addressof(kb_a)

    # 1. Hook inactif : ne doit rien capturer
    hook.set_active(False)
    # Simuler WM_KEYDOWN sur A quand inactif
    hook._is_novadaw_or_plugin_window_mock = lambda hwnd: True

    # 2. Hook actif :
    hook.set_active(True)
    assert hook._is_active is True

    # Touche A enfoncée -> 1 événement
    hook._held_vks.clear()
    hook._held_vks.add(0x41)
    # Un deuxième appui alors que vk est déjà dans _held_vks doit être ignoré (auto-repeat suppression)
    assert 0x41 in hook._held_vks

    # Relâchement de la touche A
    hook._held_vks.discard(0x41)
    assert 0x41 not in hook._held_vks

    # Touche en mode édition texte
    hook.set_text_editing_focused(True)
    assert hook._text_editing_focused is True
    hook.set_text_editing_focused(False)
    assert hook._text_editing_focused is False

    hook.set_active(False)


def test_open_plugin_editor_gui_syncs_track_focus(app, tmp_path):
    """Vérifie que open_plugin_editor_gui ouvre l'éditeur VST et synchronise le focus de piste sans erreur."""
    from types import SimpleNamespace
    from ui.main_window import MainWindow
    from ui.plugin_dialogs import open_plugin_editor_gui
    from core.plugin_manager import global_plugin_manager

    vst_file = tmp_path / "MockSynth.vst3"
    vst_file.write_text("mock", encoding="utf-8")
    vst_path = str(vst_file)

    win = MainWindow()
    track = Track(name="VST Lead", track_type="midi", plugin_path=vst_path, plugin_name="MockSynth")
    win.project.add_track(track)
    win.refresh_project_ui()

    key = f"track:{track.id}:instrument:{vst_path}"
    requests_made = []

    class MockVSTPlugin:
        _failure = None
        _process = SimpleNamespace(pid=424242)

        def request(self, cmd, timeout=15):
            requests_made.append(cmd)
            if cmd == "editor":
                return b"mock-state"
            if cmd == "editor_status":
                return {"open": False}
            return {}

    global_plugin_manager.active_instances[key] = MockVSTPlugin()
    try:
        open_plugin_editor_gui(vst_path, win.inspector, key, track=track)
        app.processEvents()

        assert "editor" in requests_made
        assert global_plugin_manager.track_id_by_pid.get(424242) == track.id
        assert win.virtual_keyboard_dialog.current_track == track
    finally:
        evt = global_plugin_manager.open_editors.pop(key, None)
        if evt:
            evt.set()
        global_plugin_manager.active_instances.pop(key, None)
        global_plugin_manager.saved_states.pop(key, None)
        global_plugin_manager.track_id_by_pid.pop(424242, None)
        win.audio_engine.close()
        win.close()



