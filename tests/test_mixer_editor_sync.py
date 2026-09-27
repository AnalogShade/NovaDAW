"""
tests/test_mixer_editor_sync.py - Tests de synchronisation bidirectionnelle entre l'éditeur (en-têtes de piste, inspecteur)
et la console de mixage (MixerConsoleWidget / MixerChannelStrip) de NovaDAW.
"""
import sys
import os
import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QMouseEvent

from core.project import Track, Project
from plugins.mixer.mixer_plugin import MixerPlugin
from plugins.mixer.mixer_gui import MixerConsoleWidget, MixerChannelStrip
from ui.track_header import TrackHeaderWidget
from ui.inspector import TrackInspector
from ui.main_window import MainWindow


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_mixer_channel_strip_controls(qapp):
    """Vérifie que la tranche de mixeur modifie la piste et émet track_modified."""
    track = Track(name="Synth Lead", track_type="midi", volume=0.8, pan=0.0, muted=False, soloed=False, armed=False)
    strip = MixerChannelStrip(track)

    modified_count = [0]
    strip.track_modified.connect(lambda: modified_count.__setitem__(0, modified_count[0] + 1))

    # 1. Mute
    strip.btn_m.click()
    assert track.muted is True
    assert strip.btn_m.isChecked() is True
    assert modified_count[0] == 1

    # 2. Solo
    strip.btn_s.click()
    assert track.soloed is True
    assert strip.btn_s.isChecked() is True
    assert modified_count[0] == 2

    # 3. Rec Arm (R)
    strip.btn_r.click()
    assert track.armed is True
    assert strip.btn_r.isChecked() is True
    assert modified_count[0] == 3

    # 4. Volume fader
    strip.slider_vol.setValue(110)
    assert abs(track.volume - 1.1) < 1e-4
    assert strip.lbl_vol_db.text() == "110%"
    assert modified_count[0] == 4

    # 5. Pan slider
    strip.slider_pan.setValue(-40)
    assert abs(track.pan - (-0.4)) < 1e-4
    assert modified_count[0] == 5


def test_mixer_channel_strip_double_click_reset(qapp):
    """Vérifie que le double-clic sur les faders du mixeur réinitialise le volume à 80% et le pan à 0."""
    track = Track(name="Basse", volume=1.3, pan=0.6)
    strip = MixerChannelStrip(track)

    assert strip.slider_vol.value() == 130
    assert strip.slider_pan.value() == 60

    ev = QMouseEvent(QMouseEvent.Type.MouseButtonDblClick, QPoint(5, 5), Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)

    # Réinitialisation Volume
    strip.slider_vol.mouseDoubleClickEvent(ev)
    assert strip.slider_vol.value() == 80
    assert abs(track.volume - 0.8) < 1e-4
    assert strip.lbl_vol_db.text() == "80%"

    # Réinitialisation Pan
    strip.slider_pan.mouseDoubleClickEvent(ev)
    assert strip.slider_pan.value() == 0
    assert abs(track.pan - 0.0) < 1e-4


def test_mixer_strip_sync_from_track(qapp):
    """Vérifie que sync_controls_from_track met à jour les boutons sans déclencher de signaux en boucle."""
    track = Track(name="Pad", volume=0.5, pan=-0.3, muted=False, soloed=False, armed=False)
    strip = MixerChannelStrip(track)

    signals_emitted = [0]
    strip.track_modified.connect(lambda: signals_emitted.__setitem__(0, signals_emitted[0] + 1))

    # Modifier l'objet Track en arrière-plan
    track.muted = True
    track.soloed = True
    track.armed = True
    track.volume = 1.25
    track.pan = 0.5
    track.name = "Pad Stéréo"

    # Synchroniser
    strip.sync_controls_from_track()

    # Vérifier l'état de l'interface
    assert strip.btn_m.isChecked() is True
    assert strip.btn_s.isChecked() is True
    assert strip.btn_r.isChecked() is True
    assert strip.slider_vol.value() == 125
    assert strip.lbl_vol_db.text() == "125%"
    assert strip.slider_pan.value() == 50
    assert "Pad Stéréo" in strip.lbl_name.toolTip()

    # Aucun signal émis grâce à blockSignals
    assert signals_emitted[0] == 0


def test_bidirectional_sync_in_main_window(qapp):
    """Vérifie la synchronisation complète bidirectionnelle entre l'éditeur et le mixeur dans MainWindow."""
    project = Project(name="Test Sync Project")
    t1 = Track(name="Piste 1", volume=0.8, pan=0.0, muted=False, soloed=False, armed=False)
    t2 = Track(name="Piste 2", volume=0.8, pan=0.0, muted=False, soloed=False, armed=False)
    project.add_track(t1)
    project.add_track(t2)

    win = MainWindow()
    win.project = project
    win.refresh_project_ui()

    assert win.mixer_widget is not None
    assert len(win.mixer_widget.strips) == 2

    header1 = win.headers_layout.itemAt(0).widget()
    header2 = win.headers_layout.itemAt(1).widget()
    strip1 = win.mixer_widget.strips[t1.id]
    strip2 = win.mixer_widget.strips[t2.id]

    # --- 1. SOLO depuis l'éditeur vers le mixeur ---
    header1.btn_solo.click()
    assert t1.soloed is True
    assert header1.btn_solo.isChecked() is True
    assert strip1.btn_s.isChecked() is True
    assert strip2.btn_s.isChecked() is False

    # --- 2. SOLO depuis le mixeur vers l'éditeur ---
    strip1.btn_s.click()
    assert t1.soloed is False
    assert strip1.btn_s.isChecked() is False
    assert header1.btn_solo.isChecked() is False

    # Solo sur piste 2 depuis le mixeur
    strip2.btn_s.click()
    assert t2.soloed is True
    assert strip2.btn_s.isChecked() is True
    assert header2.btn_solo.isChecked() is True

    # Désactiver Solo piste 2 depuis le header
    header2.btn_solo.click()
    assert t2.soloed is False
    assert strip2.btn_s.isChecked() is False
    assert header2.btn_solo.isChecked() is False

    # --- 3. MUTE bidirectionnel ---
    # Depuis l'éditeur
    header1.btn_mute.click()
    assert t1.muted is True
    assert header1.btn_mute.isChecked() is True
    assert strip1.btn_m.isChecked() is True

    # Depuis le mixeur (désactiver)
    strip1.btn_m.click()
    assert t1.muted is False
    assert strip1.btn_m.isChecked() is False
    assert header1.btn_mute.isChecked() is False

    # --- 4. RECORD ARM bidirectionnel ---
    # Depuis l'éditeur
    header1.btn_rec.click()
    assert t1.armed is True
    assert header1.btn_rec.isChecked() is True
    assert strip1.btn_r.isChecked() is True

    # Depuis le mixeur (désactiver)
    strip1.btn_r.click()
    assert t1.armed is False
    assert strip1.btn_r.isChecked() is False
    assert header1.btn_rec.isChecked() is False

    # --- 5. VOLUME bidirectionnel ---
    # Depuis l'éditeur
    header1.slider_vol.setValue(135)
    assert abs(t1.volume - 1.35) < 1e-4
    assert strip1.slider_vol.value() == 135
    assert strip1.lbl_vol_db.text() == "135%"

    # Depuis le mixeur
    strip1.slider_vol.setValue(60)
    assert abs(t1.volume - 0.6) < 1e-4
    assert header1.slider_vol.value() == 60
    assert header1.txt_vol.text() == "60%"

    # --- 6. PANORAMIQUE bidirectionnel ---
    # Depuis l'éditeur
    header1.slider_pan.setValue(45)
    assert abs(t1.pan - 0.45) < 1e-4
    assert strip1.slider_pan.value() == 45

    # Depuis le mixeur
    strip1.slider_pan.setValue(-70)
    assert abs(t1.pan - (-0.7)) < 1e-4
    assert header1.slider_pan.value() == -70
    assert header1.txt_pan.text() == "L70"

    # --- 7. SÉLECTION DE PISTE bidirectionnelle ---
    # Clic sur le strip 2 du mixeur
    strip2.mousePressEvent(QMouseEvent(QMouseEvent.Type.MouseButtonPress, QPoint(5, 5), Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    assert win.selected_track_id == t2.id
    assert header2.is_selected is True
    assert header1.is_selected is False
    assert strip2.is_selected is True
    assert strip1.is_selected is False

    win.close()
