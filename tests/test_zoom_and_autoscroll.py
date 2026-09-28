import pytest
import numpy as np
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QPoint, QPointF
from PySide6.QtGui import QWheelEvent, QKeySequence

from core.project import Project, Track, MidiClip, AudioClip, MidiNote
from ui.main_window import MainWindow
from ui.timeline_view import TimelineGrid, TimelineRuler
from ui.piano_roll import PianoRoll, NoteGridWidget


def test_timeline_total_project_beats_and_dynamic_extension(qapp):
    """
    Vérifie que la timeline s'adapte dynamiquement à la longueur du projet :
    - Même pour un projet de 52 mesures (208 temps), la timeline s'étend au-delà (208 + 64 = 272 temps)
    - La scrollbar permet donc de scroller bien au-delà de la 32e mesure (128 temps)
    """
    proj = Project.create_empty()
    track = Track(name="Synth", track_type="midi")
    # Clip se terminant à la mesure 52 (temps 208)
    clip = MidiClip(name="Outro", start_beat=192.0, length_beats=16.0)
    track.clips.append(clip)
    proj.add_track(track)

    grid = TimelineGrid(proj)
    grid.pixels_per_beat = 40.0
    grid.update_dimensions()

    total_beats = grid.get_total_project_beats()
    # Doit dépasser 208 temps + marge de 64 temps = 272 temps
    assert total_beats >= 272.0
    # La largeur du widget doit être d'au moins 272 * 40 = 10880 px
    assert grid.width() >= int(272.0 * 40.0)


def test_ctrl_wheel_events_on_timeline_and_ruler(qapp):
    """
    Vérifie que Ctrl + Molette émet correctement les signaux de zoom
    sur la Timeline et la Ruler.
    """
    proj = Project.create_empty()
    grid = TimelineGrid(proj)
    ruler = TimelineRuler()

    zoom_in_grid_called = []
    zoom_out_grid_called = []
    zoom_in_ruler_called = []
    zoom_out_ruler_called = []

    grid.zoom_in_requested.connect(lambda: zoom_in_grid_called.append(True))
    grid.zoom_out_requested.connect(lambda: zoom_out_grid_called.append(True))
    ruler.zoom_in_requested.connect(lambda: zoom_in_ruler_called.append(True))
    ruler.zoom_out_requested.connect(lambda: zoom_out_ruler_called.append(True))

    # Molette vers le haut avec Ctrl -> Zoom In
    ev_wheel_up = QWheelEvent(
        QPointF(100, 100), QPointF(100, 100),
        QPoint(0, 0), QPoint(0, 120),
        Qt.NoButton, Qt.ControlModifier, Qt.NoScrollPhase, False
    )
    grid.wheelEvent(ev_wheel_up)
    assert len(zoom_in_grid_called) == 1

    ruler.wheelEvent(ev_wheel_up)
    assert len(zoom_in_ruler_called) == 1

    # Molette vers le bas avec Ctrl -> Zoom Out
    ev_wheel_down = QWheelEvent(
        QPointF(100, 100), QPointF(100, 100),
        QPoint(0, 0), QPoint(0, -120),
        Qt.NoButton, Qt.ControlModifier, Qt.NoScrollPhase, False
    )
    grid.wheelEvent(ev_wheel_down)
    assert len(zoom_out_grid_called) == 1

    ruler.wheelEvent(ev_wheel_down)
    assert len(zoom_out_ruler_called) == 1


def test_piano_roll_zoom_controls_and_ctrl_wheel(qapp):
    """
    Vérifie les contrôles de zoom du Piano Roll (boutons et Ctrl + Molette).
    """
    from core.audio_engine import AudioEngine
    engine = AudioEngine()
    pr = PianoRoll(engine)
    pr.resize(800, 500)
    pr.show()
    QApplication.processEvents()

    initial_ppb = pr.note_grid.pixels_per_beat  # 60.0 par défaut
    assert initial_ppb == 60.0

    # Zoom in via bouton
    pr.btn_zoom_in.click()
    assert pr.note_grid.pixels_per_beat > initial_ppb

    # Zoom reset via bouton (100% = 60.0)
    pr.btn_zoom_reset.click()
    assert pr.note_grid.pixels_per_beat == 60.0

    # Zoom out via méthode
    pr.zoom_out()
    assert pr.note_grid.pixels_per_beat < initial_ppb

    # Reset
    pr.zoom_reset()
    assert pr.note_grid.pixels_per_beat == 60.0

    # Test de l'eventFilter sur la scroll_area du Piano Roll avec Ctrl + Molette
    ev_wheel_up = QWheelEvent(
        QPointF(100, 100), QPointF(100, 100),
        QPoint(0, 0), QPoint(0, 120),
        Qt.NoButton, Qt.ControlModifier, Qt.NoScrollPhase, False
    )
    handled = pr.eventFilter(pr.scroll_area, ev_wheel_up)
    assert handled is True
    assert pr.note_grid.pixels_per_beat > 60.0

    pr.close()


def test_main_window_global_zoom_reset_and_autoscroll(qapp):
    """
    Vérifie dans MainWindow :
    1. Le raccourci et la méthode de réinitialisation du zoom à 100% (Ctrl+0).
    2. L'activation / désactivation du défilement automatique (Follow Playhead).
    3. Le scroll automatique de la timeline quand la tête de lecture avance pendant la lecture.
    """
    window = MainWindow()
    window.resize(1024, 768)
    window.show()
    QApplication.processEvents()

    # 1. Test du Zoom global
    window.timeline_grid.set_zoom(80.0)
    window.piano_roll.note_grid.set_zoom(100.0)
    assert window.timeline_grid.pixels_per_beat == 80.0
    assert window.piano_roll.note_grid.pixels_per_beat == 100.0

    # Réinitialisation globale à 100% (Ctrl + 0)
    window._on_global_zoom_reset()
    assert window.timeline_grid.pixels_per_beat == 40.0
    assert window.piano_roll.note_grid.pixels_per_beat == 60.0

    # 2. Test du basculement de l'autoscroll
    assert window.autoscroll_enabled is True
    assert window.transport_bar.btn_autoscroll.isChecked() is True
    assert window.act_autoscroll.isChecked() is True

    window._toggle_autoscroll()
    assert window.autoscroll_enabled is False
    assert window.transport_bar.btn_autoscroll.isChecked() is False
    assert window.act_autoscroll.isChecked() is False

    window._toggle_autoscroll()
    assert window.autoscroll_enabled is True
    assert window.transport_bar.btn_autoscroll.isChecked() is True

    # 3. Test du défilement automatique pendant la lecture
    # Création d'une piste longue pour permettre le scroll
    t = Track(name="Test Long", track_type="audio")
    clip = AudioClip(name="LongAudio", start_beat=0.0, length_beats=200.0)
    t.clips.append(clip)
    window.project.tracks = [t]
    window.refresh_project_ui()
    QApplication.processEvents()

    h_bar = window.timeline_scroll.horizontalScrollBar()
    h_bar.setValue(0)
    assert h_bar.value() == 0

    # Simuler la lecture à 80 temps (80 * 40 = 3200 px, bien au-delà d'un viewport de ~800px)
    window.audio_engine.is_playing = True
    window.audio_engine.current_beat = 80.0
    window._update_playback_ui()

    # Le scrollbar horizontal doit avoir défilé automatiquement pour suivre la lecture
    assert h_bar.value() > 0

    # Si autoscroll est désactivé, le scrollbar ne doit pas bouger automatiquement
    window._on_autoscroll_toggled(False)
    h_bar.setValue(50)
    window.audio_engine.current_beat = 120.0
    window._update_playback_ui()
    assert h_bar.value() == 50

    window.audio_engine.is_playing = False
    window.close()
