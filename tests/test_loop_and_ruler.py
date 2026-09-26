"""
tests/test_loop_and_ruler.py - Tests unitaires pour :
1. Désactivation par défaut de la lecture en boucle au démarrage (Project, TransportBar, TimelineRuler).
2. Détection de survol sur les poignées de boucle (curseur SizeHorCursor, infobulle et badge avec raccourci Maj+Clic).
3. Redimensionnement interactif par glisser-déposer de la poignée gauche (début de boucle).
4. Redimensionnement interactif par glisser-déposer de la poignée droite (fin de boucle).
5. Création d'une nouvelle boucle via Maj + Clic-Glisser.
6. Prise en compte du décalage de défilement horizontal (scroll_offset) dans la règle.
"""
import sys
from pathlib import Path
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QMouseEvent, QPaintEvent
from PySide6.QtCore import Qt, QPointF, QRect

from core.project import Project
from core.serializer import load_project
from ui.transport_bar import TransportBar
from ui.timeline_view import TimelineRuler


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if not app:
        app = QApplication([])
    return app


def test_default_loop_state_disabled(qapp):
    """Vérifie que la lecture en boucle est désactivée par défaut."""
    # 1. Dataclass Project par défaut
    p1 = Project()
    assert p1.loop_enabled is False

    # 2. Project.create_empty
    p2 = Project.create_empty()
    assert p2.loop_enabled is False

    # 3. Project.create_demo
    p3 = Project.create_demo()
    assert p3.loop_enabled is False

    # 4. TimelineRuler par défaut
    ruler = TimelineRuler()
    assert ruler.loop_enabled is False


def test_transport_bar_default_loop_unchecked(qapp):
    """Vérifie que le bouton de boucle dans la barre de transport est désactivé au démarrage."""
    bar = TransportBar()
    assert bar.btn_loop.isChecked() is False


def test_ruler_hover_cursor_and_tooltips(qapp):
    """Vérifie que le survol des poignées gauche/droite change le curseur en SizeHorCursor et affiche les raccourcis."""
    ruler = TimelineRuler()
    ruler.resize(800, 30)
    ruler.set_zoom(40.0)
    ruler.set_loop(False, 4.0, 12.0)  # Start à x = 160, End à x = 480

    # 1. Survol poignée gauche (x = 160)
    move_ev_start = QMouseEvent(
        QMouseEvent.Type.MouseMove,
        QPointF(160, 10),
        Qt.NoButton,
        Qt.NoButton,
        Qt.NoModifier
    )
    ruler.mouseMoveEvent(move_ev_start)
    assert ruler.cursor().shape() == Qt.SizeHorCursor
    tip_start = ruler.toolTip()
    assert "Début" in tip_start
    assert "Maj" in tip_start or "Shift" in tip_start

    # 2. Survol en dehors des poignées (ex: x = 50)
    move_ev_outside = QMouseEvent(
        QMouseEvent.Type.MouseMove,
        QPointF(50, 10),
        Qt.NoButton,
        Qt.NoButton,
        Qt.NoModifier
    )
    ruler.mouseMoveEvent(move_ev_outside)
    assert ruler.cursor().shape() == Qt.ArrowCursor

    # 3. Survol poignée droite (x = 480)
    move_ev_end = QMouseEvent(
        QMouseEvent.Type.MouseMove,
        QPointF(480, 10),
        Qt.NoButton,
        Qt.NoButton,
        Qt.NoModifier
    )
    ruler.mouseMoveEvent(move_ev_end)
    assert ruler.cursor().shape() == Qt.SizeHorCursor
    tip_end = ruler.toolTip()
    assert "Fin" in tip_end
    assert "Maj" in tip_end or "Shift" in tip_end


def test_ruler_drag_left_handle_adjusts_start(qapp):
    """Vérifie que glisser la poignée gauche ajuste loop_start_beat et active la boucle."""
    ruler = TimelineRuler()
    ruler.resize(800, 30)
    ruler.set_zoom(40.0)
    ruler.set_loop(False, 4.0, 16.0)

    loop_changes = []
    ruler.loop_changed.connect(lambda s, e: loop_changes.append((s, e)))

    # Clic sur la poignée gauche (x = 160)
    press_ev = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        QPointF(160, 10),
        Qt.LeftButton,
        Qt.LeftButton,
        Qt.NoModifier
    )
    ruler.mousePressEvent(press_ev)
    assert ruler._active_drag == "start"
    assert ruler.loop_enabled is True

    # Glisser vers x = 80 (temps 2.0)
    move_ev = QMouseEvent(
        QMouseEvent.Type.MouseMove,
        QPointF(80, 10),
        Qt.LeftButton,
        Qt.LeftButton,
        Qt.NoModifier
    )
    ruler.mouseMoveEvent(move_ev)
    assert ruler.loop_start_beat == 2.0
    assert ruler.loop_end_beat == 16.0
    assert (2.0, 16.0) in loop_changes

    # Relâcher
    release_ev = QMouseEvent(
        QMouseEvent.Type.MouseButtonRelease,
        QPointF(80, 10),
        Qt.LeftButton,
        Qt.NoButton,
        Qt.NoModifier
    )
    ruler.mouseReleaseEvent(release_ev)
    assert ruler._active_drag is None
    assert ruler.loop_start_beat == 2.0


def test_ruler_drag_right_handle_adjusts_end(qapp):
    """Vérifie que glisser la poignée droite ajuste loop_end_beat et active la boucle."""
    ruler = TimelineRuler()
    ruler.resize(800, 30)
    ruler.set_zoom(40.0)
    ruler.set_loop(False, 4.0, 16.0)

    loop_changes = []
    ruler.loop_changed.connect(lambda s, e: loop_changes.append((s, e)))

    # Clic sur la poignée droite (x = 640 pour temps 16)
    press_ev = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        QPointF(640, 10),
        Qt.LeftButton,
        Qt.LeftButton,
        Qt.NoModifier
    )
    ruler.mousePressEvent(press_ev)
    assert ruler._active_drag == "end"
    assert ruler.loop_enabled is True

    # Glisser vers x = 800 (temps 20.0)
    move_ev = QMouseEvent(
        QMouseEvent.Type.MouseMove,
        QPointF(800, 10),
        Qt.LeftButton,
        Qt.LeftButton,
        Qt.NoModifier
    )
    ruler.mouseMoveEvent(move_ev)
    assert ruler.loop_start_beat == 4.0
    assert ruler.loop_end_beat == 20.0
    assert (4.0, 20.0) in loop_changes

    # Relâcher
    release_ev = QMouseEvent(
        QMouseEvent.Type.MouseButtonRelease,
        QPointF(800, 10),
        Qt.LeftButton,
        Qt.NoButton,
        Qt.NoModifier
    )
    ruler.mouseReleaseEvent(release_ev)
    assert ruler._active_drag is None
    assert ruler.loop_end_beat == 20.0


def test_ruler_shift_drag_defines_new_loop(qapp):
    """Vérifie que Maj + Clic-Glisser redéfinit la boucle complète."""
    ruler = TimelineRuler()
    ruler.resize(800, 30)
    ruler.set_zoom(40.0)
    ruler.set_loop(False, 0.0, 16.0)

    # Maj + Clic à x = 200 (temps 5.0)
    press_ev = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        QPointF(200, 10),
        Qt.LeftButton,
        Qt.LeftButton,
        Qt.ShiftModifier
    )
    ruler.mousePressEvent(press_ev)
    assert ruler._active_drag == "new"
    assert ruler.loop_enabled is True

    # Glisser jusqu'à x = 400 (temps 10.0)
    move_ev = QMouseEvent(
        QMouseEvent.Type.MouseMove,
        QPointF(400, 10),
        Qt.LeftButton,
        Qt.LeftButton,
        Qt.ShiftModifier
    )
    ruler.mouseMoveEvent(move_ev)
    assert ruler.loop_start_beat == 5.0
    assert ruler.loop_end_beat == 10.0


def test_ruler_scroll_offset_sync(qapp):
    """Vérifie que set_scroll_offset décale correctement les poignées de boucle."""
    ruler = TimelineRuler()
    ruler.resize(800, 30)
    ruler.set_zoom(40.0)
    ruler.set_loop(True, 10.0, 20.0)  # Sans scroll: 400 et 800

    # Défilement horizontal de 200 pixels
    ruler.set_scroll_offset(200.0)

    # Après scroll, la poignée de début (temps 10 = 400px) doit être à 400 - 200 = 200px
    handle = ruler._get_handle_at(200.0)
    assert handle == "start"

    # La poignée de fin (temps 20 = 800px) doit être à 800 - 200 = 600px
    handle_end = ruler._get_handle_at(600.0)
    assert handle_end == "end"


def test_ruler_paint_event_no_crash(qapp):
    """Vérifie que paintEvent s'exécute sans exception avec et sans survol."""
    ruler = TimelineRuler()
    ruler.resize(800, 30)
    ruler.set_zoom(40.0)
    ruler.set_loop(False, 0.0, 16.0)

    # Test rendu sans survol
    ruler.repaint()

    # Test rendu avec survol poignée début
    ruler._hovered_handle = "start"
    ruler.repaint()

    # Test rendu avec survol poignée fin
    ruler._hovered_handle = "end"
    ruler.repaint()
