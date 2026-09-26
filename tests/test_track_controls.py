"""
tests/test_track_controls.py - Tests unitaires pour les fonctionnalités de contrôle de piste :
- Basculement de lecture en boucle (TransportBar.btn_loop)
- Saisie manuelle du volume et du panoramique
- Réinitialisation par double-clic (ResetableSlider)
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
from ui.track_header import TrackHeaderWidget, ResetableSlider, CompactNumEdit
from ui.transport_bar import TransportBar


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_transport_bar_loop_toggle(qapp):
    """Vérifie que le bouton boucle émet le signal loop_toggled sans TypeError."""
    bar = TransportBar()
    emitted_values = []
    bar.loop_toggled.connect(lambda val: emitted_values.append(val))

    assert bar.btn_loop.isChecked() is False

    # Clic pour activer
    bar.btn_loop.click()
    assert len(emitted_values) == 1
    assert emitted_values[-1] is True
    assert bar.btn_loop.isChecked() is True

    # Clic pour désactiver
    bar.btn_loop.click()
    assert len(emitted_values) == 2
    assert emitted_values[-1] is False
    assert bar.btn_loop.isChecked() is False


def test_resetable_slider_double_click(qapp):
    """Vérifie que le double-clic sur ResetableSlider rétablit la valeur par défaut."""
    pan_slider = ResetableSlider(Qt.Horizontal, default_value=0)
    pan_slider.setRange(-100, 100)
    pan_slider.setValue(45)
    assert pan_slider.value() == 45

    # Simuler double-clic gauche
    ev = QMouseEvent(QMouseEvent.Type.MouseButtonDblClick, QPoint(5, 5), Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    pan_slider.mouseDoubleClickEvent(ev)
    assert pan_slider.value() == 0

    vol_slider = ResetableSlider(Qt.Horizontal, default_value=80)
    vol_slider.setRange(0, 150)
    vol_slider.setValue(125)
    assert vol_slider.value() == 125

    vol_slider.mouseDoubleClickEvent(ev)
    assert vol_slider.value() == 80


def test_track_header_volume_manual_edit(qapp):
    """Vérifie la saisie manuelle dans le champ texte du volume."""
    track = Track(name="Audio 1", volume=0.8, pan=0.0)
    header = TrackHeaderWidget(track)

    assert header.txt_vol.text() == "80%"
    assert header.slider_vol.value() == 80

    # 1. Saisie "50%"
    header.txt_vol.setText("50%")
    header._on_vol_text_edited()
    assert header.slider_vol.value() == 50
    assert track.volume == 0.5
    assert header.txt_vol.text() == "50%"

    # 2. Saisie "1.2" (format float)
    header.txt_vol.setText("1.2")
    header._on_vol_text_edited()
    assert header.slider_vol.value() == 120
    assert track.volume == 1.2
    assert header.txt_vol.text() == "120%"

    # 3. Saisie invalide -> restaure la valeur actuelle
    header.txt_vol.setText("invalide")
    header._on_vol_text_edited()
    assert header.slider_vol.value() == 120
    assert header.txt_vol.text() == "120%"


def test_track_header_pan_manual_edit(qapp):
    """Vérifie la saisie manuelle dans le champ texte du panoramique."""
    track = Track(name="Synth", volume=0.8, pan=0.0)
    header = TrackHeaderWidget(track)

    assert header.txt_pan.text() == "C"
    assert header.slider_pan.value() == 0

    # 1. Saisie "L30" (Gauche 30%)
    header.txt_pan.setText("L30")
    header._on_pan_text_edited()
    assert header.slider_pan.value() == -30
    assert track.pan == -0.3
    assert header.txt_pan.text() == "L30"

    # 2. Saisie "R45" (Droite 45%)
    header.txt_pan.setText("R45")
    header._on_pan_text_edited()
    assert header.slider_pan.value() == 45
    assert track.pan == 0.45
    assert header.txt_pan.text() == "R45"

    # 3. Saisie "C" (Centre)
    header.txt_pan.setText("C")
    header._on_pan_text_edited()
    assert header.slider_pan.value() == 0
    assert track.pan == 0.0
    assert header.txt_pan.text() == "C"

    # 4. Saisie "-25" (négatif = gauche)
    header.txt_pan.setText("-25")
    header._on_pan_text_edited()
    assert header.slider_pan.value() == -25
    assert track.pan == -0.25
    assert header.txt_pan.text() == "L25"


def test_track_header_double_click_resets(qapp):
    """Vérifie que le double-clic sur les curseurs de l'en-tête de piste réinitialise pan et volume."""
    track = Track(name="Guitare", volume=1.4, pan=-0.75)
    header = TrackHeaderWidget(track)

    assert header.slider_pan.value() == -75
    assert header.slider_vol.value() == 140

    ev = QMouseEvent(QMouseEvent.Type.MouseButtonDblClick, QPoint(5, 5), Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)

    # Double-clic sur pan -> retourne à 0 (Centre)
    header.slider_pan.mouseDoubleClickEvent(ev)
    assert header.slider_pan.value() == 0
    assert track.pan == 0.0
    assert header.txt_pan.text() == "C"

    # Double-clic sur vol -> retourne à 80 (80%)
    header.slider_vol.mouseDoubleClickEvent(ev)
    assert header.slider_vol.value() == 80
    assert track.volume == 0.8
    assert header.txt_vol.text() == "80%"
