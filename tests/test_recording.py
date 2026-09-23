"""
tests/test_recording.py - Tests unitaires et d'intégration pour l'enregistrement audio/MIDI
et les retours visuels haute intensité (auras néon).
"""
import sys
import os
import pytest
import numpy as np

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QColor

from core.project import Project, Track, AudioClip, MidiClip
from core.audio_engine import AudioEngine
from ui.main_window import MainWindow
from ui.track_header import TrackHeaderWidget
from ui.glow_effects import set_button_glow
from core.actions.transport import control_transport, get_transport_state


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if not app:
        app = QApplication([])
    return app


def test_button_glow_helper(qapp):
    from PySide6.QtWidgets import QPushButton
    btn = QPushButton("R")
    assert btn.graphicsEffect() is None

    # Activation de la lueur
    set_button_glow(btn, True, "#ff2244", blur_radius=16)
    eff = btn.graphicsEffect()
    assert eff is not None
    assert eff.blurRadius() == 16
    assert eff.color().red() > 200

    # Désactivation
    set_button_glow(btn, False, "#ff2244")
    assert btn.graphicsEffect() is None


def test_track_header_rec_arm_glow(qapp):
    track = Track(name="Guitare", track_type="audio", armed=False)
    header = TrackHeaderWidget(track)

    assert not header.btn_rec.isChecked()
    assert header.btn_rec.graphicsEffect() is None

    # Clic pour armer la piste (R)
    header.btn_rec.setChecked(True)
    assert track.armed is True
    assert header.btn_rec.graphicsEffect() is not None
    assert header.btn_rec.graphicsEffect().color().red() > 200

    # Désarmement
    header.btn_rec.setChecked(False)
    assert track.armed is False
    assert header.btn_rec.graphicsEffect() is None


def test_track_header_mute_solo_glow(qapp):
    track = Track(name="Basse", track_type="audio", muted=False, soloed=False)
    header = TrackHeaderWidget(track)

    # Solo
    header.btn_solo.setChecked(True)
    assert track.soloed is True
    assert header.btn_solo.graphicsEffect() is not None

    header.btn_solo.setChecked(False)
    assert track.soloed is False
    assert header.btn_solo.graphicsEffect() is None

    # Mute
    header.btn_mute.setChecked(True)
    assert track.muted is True
    assert header.btn_mute.graphicsEffect() is not None

    header.btn_mute.setChecked(False)
    assert track.muted is False
    assert header.btn_mute.graphicsEffect() is None


def test_record_launches_play_and_record_simultaneously(qapp):
    window = MainWindow()
    track = Track(name="Piste Audio Test", track_type="audio", armed=True)
    window.project.add_track(track)
    window.refresh_project_ui()

    # Déclencher le bouton d'enregistrement
    window.transport_bar.btn_record.click()

    # Vérification : la lecture ET l'enregistrement doivent être actifs ensemble !
    assert window.audio_engine.is_playing is True
    assert window.audio_engine.is_recording is True
    assert window.transport_bar.btn_play.isChecked() is True
    assert window.transport_bar.btn_record.isChecked() is True

    # Vérification des auras lumineuses sur Play et Record
    assert window.transport_bar.btn_play.graphicsEffect() is not None
    assert window.transport_bar.btn_play.graphicsEffect().color().green() > 150
    assert window.transport_bar.btn_record.graphicsEffect() is not None
    assert window.transport_bar.btn_record.graphicsEffect().color().red() > 200

    # Arrêt
    window.transport_bar.btn_stop.click()
    assert window.audio_engine.is_playing is False
    assert window.audio_engine.is_recording is False
    assert window.transport_bar.btn_play.isChecked() is False
    assert window.transport_bar.btn_record.isChecked() is False
    assert window.transport_bar.btn_play.graphicsEffect() is None
    assert window.transport_bar.btn_record.graphicsEffect() is None
    window.close()


def test_auto_arm_if_no_track_armed_on_record(qapp):
    window = MainWindow()
    track = Track(name="Piste 1", track_type="audio", armed=False)
    window.project.add_track(track)
    window.refresh_project_ui()

    # L'utilisateur clique sur Record sans avoir armé de piste à l'avance
    window.transport_bar.btn_record.click()

    # Le système doit avoir auto-armé la piste et démarré lecture + enregistrement
    armed_tracks = [t for t in window.project.tracks if t.armed]
    assert len(armed_tracks) >= 1
    assert window.audio_engine.is_playing is True
    assert window.audio_engine.is_recording is True

    window.transport_bar.btn_stop.click()
    window.close()


def test_finish_recording_creates_clip_on_timeline(qapp):
    window = MainWindow()
    # Créer une piste audio dédiée
    audio_t = Track(name="Voix Lead", track_type="audio", armed=True)
    window.project.add_track(audio_t)
    window.refresh_project_ui()
    initial_clips = len(audio_t.clips)

    window.audio_engine.current_beat = 2.0
    window.transport_bar.btn_record.click()

    # Simuler quelques blocs audio enregistrés
    sample_block = np.zeros((1024, 2), dtype=np.float32)
    sample_block[:, 0] = 0.25
    sample_block[:, 1] = 0.25
    window.audio_engine._recorded_audio_blocks.append(sample_block)

    # Avancer le curseur
    window.audio_engine.current_beat = 6.0

    # Arrêter l'enregistrement
    window.transport_bar.btn_stop.click()

    # Vérifier qu'un nouveau bloc Audio a été créé à la bonne position temporelle
    assert len(audio_t.clips) == initial_clips + 1
    new_clip = audio_t.clips[-1]
    assert isinstance(new_clip, AudioClip)
    assert new_clip.start_beat == 2.0
    assert abs(new_clip.length_beats - 4.0) < 0.6
    assert new_clip.audio_data is not None
    assert len(new_clip.audio_data) == 1024
    window.close()


def test_mcp_record_action(qapp):
    window = MainWindow()
    state = get_transport_state(window)
    assert state["is_recording"] is False

    # Déclencher 'record' via le contrôleur de transport MCP
    state2 = control_transport(window, "record")
    assert state2["is_recording"] is True
    assert state2["is_playing"] is True

    # Re-déclencher pour punch out
    state3 = control_transport(window, "record")
    assert state3["is_recording"] is False

    window.transport_bar.btn_stop.click()
    window.close()
