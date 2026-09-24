"""
tests/test_live_recording_and_waveform.py - Tests unitaires complets pour :
1. Le calcul et la mise en cache de la vraie forme d'onde (waveform) dans AudioClip.
2. L'armement intelligent des pistes audio lors du démarrage de l'enregistrement.
3. La création et l'évolution temporelle du bloc en direct pendant l'enregistrement.
4. Le rendu visuel de la forme d'onde et du bloc live sur la timeline.
"""
import os
import sys
from pathlib import Path
import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QPainter, QImage
from PySide6.QtCore import QRectF

from core.project import Project, Track, AudioClip, MidiClip, MidiNote
from core.audio_engine import AudioEngine
from ui.main_window import MainWindow
from ui.timeline_view import TimelineGrid


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if not app:
        app = QApplication([])
    return app


def test_waveform_peaks_calculation_and_caching():
    """Vérifie le calcul fidèle des crêtes (min/max) et la mise en cache sur AudioClip"""
    # 1. Échantillon audio synthétisé (sinusoïde d'amplitude 0.8)
    sr = 44100
    duration = 1.0
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    sine = (0.8 * np.sin(2.0 * np.pi * 440.0 * t)).astype(np.float32)
    stereo = np.column_stack((sine, sine))

    clip = AudioClip(name="Test Sine", audio_data=stereo, sample_rate=sr)

    # Calcul des crêtes pour 100 pixels
    mins, maxs = clip.get_waveform_peaks(num_bins=100)
    assert len(mins) == 100
    assert len(maxs) == 100
    assert np.all(mins <= maxs)
    assert np.max(maxs) <= 0.81
    assert np.min(mins) >= -0.81
    assert np.max(maxs) > 0.6  # Signal fort détecté

    # Vérification que le cache est réutilisé
    mins2, maxs2 = clip.get_waveform_peaks(num_bins=100)
    assert mins2 is mins
    assert maxs2 is maxs

    # Changement du nombre de bins -> recalcule
    mins3, maxs3 = clip.get_waveform_peaks(num_bins=200)
    assert len(mins3) == 200

    # Test avec données vides
    empty_clip = AudioClip(name="Empty")
    e_mins, e_maxs = empty_clip.get_waveform_peaks(num_bins=50)
    assert len(e_mins) == 0
    assert len(e_maxs) == 0


def test_waveform_peaks_gain_scaling():
    """Vérifie que le gain du clip ajuste proportionnellement les crêtes de l'onde"""
    raw = np.array([[0.2, 0.2], [-0.2, -0.2], [0.4, 0.4], [-0.4, -0.4]], dtype=np.float32)
    clip = AudioClip(name="Gain Test", audio_data=raw, gain=1.0)

    mins1, maxs1 = clip.get_waveform_peaks(num_bins=2)
    assert np.isclose(np.max(maxs1), 0.4)

    # Doubler le gain
    clip.gain = 2.0
    mins2, maxs2 = clip.get_waveform_peaks(num_bins=2)
    assert np.isclose(np.max(maxs2), 0.8)


def test_record_with_zero_tracks_does_not_create_any_track(qapp):
    """Vérifie que cliquer sur Record sans aucune piste ne crée AUCUNE piste et annule l'enregistrement."""
    window = MainWindow()
    window.project = Project.create_empty()
    assert len(window.project.tracks) == 0

    # Déclencher le bouton d'enregistrement
    window.transport_bar.btn_record.click()

    # Règle stricte : 0 piste créée, enregistrement non démarré
    assert len(window.project.tracks) == 0
    assert window.audio_engine.is_recording is False
    assert window.transport_bar.btn_record.isChecked() is False
    window.close()


def test_record_on_existing_track_without_creating_new_track(qapp):
    """Vérifie que l'enregistrement sur une piste existante n'en crée jamais d'autre et enregistre bien dessus."""
    window = MainWindow()
    window.project = Project.create_empty()
    t = Track(name="Ma Piste Existante", track_type="midi", armed=False)
    window.project.add_track(t)
    window.refresh_project_ui()
    assert len(window.project.tracks) == 1

    # Déclencher le bouton d'enregistrement
    window.transport_bar.btn_record.click()

    # Vérification : toujours exactement 1 piste, et elle a été armée
    assert len(window.project.tracks) == 1
    assert t.armed is True
    assert window.audio_engine.is_recording is True

    # Fournir un bloc audio capturé
    sample_block = np.zeros((512, 2), dtype=np.float32)
    sample_block[:, 0] = 0.5
    window.audio_engine._recorded_audio_blocks.append(sample_block)

    # Arrêt de l'enregistrement
    window.transport_bar.btn_stop.click()
    assert len(window.project.tracks) == 1
    assert len(t.clips) == 1
    assert isinstance(t.clips[0], AudioClip)
    assert t.track_type == "audio"
    window.close()


def test_live_clip_created_immediately_on_record(qapp):
    """Vérifie qu'un clip d'enregistrement en direct apparaît sur la timeline dès le début de la prise"""
    window = MainWindow()
    audio_t = Track(name="Chant", track_type="audio", armed=True)
    window.project.add_track(audio_t)
    window.refresh_project_ui()

    initial_clips_count = len(audio_t.clips)

    # Début d'enregistrement à beat 4.0
    window.audio_engine.current_beat = 4.0
    window.transport_bar.btn_record.click()

    # Le clip temporaire doit être créé et présent dans track.clips immédiatement
    assert len(audio_t.clips) == initial_clips_count + 1
    live_clip = audio_t.clips[-1]
    assert getattr(live_clip, "_is_recording", False) is True
    assert live_clip.start_beat == 4.0

    # Avancement de la tête de lecture (simulation playback timer)
    window.audio_engine.current_beat = 8.0
    window._update_playback_ui()

    # La durée du clip s'est allongée en temps réel
    assert live_clip.length_beats >= 3.9

    # Arrêt
    window.transport_bar.btn_stop.click()
    assert getattr(live_clip, "_is_recording", False) is False
    assert live_clip.length_beats >= 3.9
    window.close()


def test_live_waveform_updates_with_captured_audio(qapp):
    """Vérifie que les échantillons audio capturés en direct alimentent le clip et sa waveform"""
    window = MainWindow()
    audio_t = Track(name="Micro Direct", track_type="audio", armed=True)
    window.project.add_track(audio_t)
    window.refresh_project_ui()

    window.audio_engine.current_beat = 0.0
    window.transport_bar.btn_record.click()
    live_clip = audio_t.clips[-1]
    assert getattr(live_clip, "_is_recording", False) is True

    # Injection directe de blocs capturés dans l'AudioEngine
    block = np.full((512, 2), 0.35, dtype=np.float32)
    with window.audio_engine._record_lock:
        window.audio_engine._recorded_audio_blocks.append(block)

    # Déclenchement de la boucle de mise à jour UI
    window.audio_engine.current_beat = 2.0
    window._update_playback_ui()

    # Le clip en direct possède maintenant les données audio et calcule ses crêtes
    assert live_clip.audio_data is not None
    mins, maxs = live_clip.get_waveform_peaks(num_bins=50)
    assert len(mins) == 50
    assert np.isclose(np.max(maxs), 0.35, atol=1e-3)

    window.transport_bar.btn_stop.click()
    window.close()


def test_timeline_grid_renders_real_waveform_without_crash(qapp):
    """Vérifie que TimelineGrid.paintEvent dessine la forme d'onde réelle et le bloc live sans erreur"""
    proj = Project(name="Render Test")
    t = Track(name="Audio Track", track_type="audio")

    # Données audio réelles
    data = (0.5 * np.sin(np.linspace(0, 10, 2048))).astype(np.float32)
    stereo = np.column_stack((data, data))
    clip = AudioClip(name="Real Audio Clip", audio_data=stereo, start_beat=0.0, length_beats=4.0)
    t.clips.append(clip)
    proj.add_track(t)

    grid = TimelineGrid(proj)
    grid.resize(800, 300)

    # Rendu hors-écran dans une QImage pour tester le QPainter
    img = QImage(800, 300, QImage.Format_ARGB32)
    grid.render(img)

    # Test avec un clip en cours d'enregistrement (_is_recording=True)
    clip._is_recording = True
    grid.playhead_beat = 3.0
    img2 = QImage(800, 300, QImage.Format_ARGB32)
    grid.render(img2)


def test_recording_on_selected_track_among_multiple(qapp):
    """Vérifie que parmi plusieurs pistes, l'enregistrement se fait précisément sur la piste sélectionnée sans en créer de nouvelle."""
    window = MainWindow()
    window.project = Project.create_empty()
    t1 = Track(name="Guitare", track_type="audio")
    t2 = Track(name="Voix", track_type="audio")
    t3 = Track(name="Synth", track_type="midi")
    window.project.add_track(t1)
    window.project.add_track(t2)
    window.project.add_track(t3)
    window.refresh_project_ui()
    assert len(window.project.tracks) == 3

    # Sélectionner spécifiquement la piste 2 (Voix)
    window._on_track_selected(t2.id)
    assert window.selected_track_id == t2.id

    # Déclencher Record
    window.transport_bar.btn_record.click()

    # Règle : Toujours exactement 3 pistes, et t2 est armée
    assert len(window.project.tracks) == 3
    assert t2.armed is True
    assert t1.armed is False
    assert t3.armed is False
    assert window.audio_engine.is_recording is True

    # Injection audio
    sample_block = np.zeros((256, 2), dtype=np.float32)
    sample_block[:, 0] = 0.4
    window.audio_engine._recorded_audio_blocks.append(sample_block)

    # Arrêt
    window.transport_bar.btn_stop.click()

    assert len(window.project.tracks) == 3
    assert len(t2.clips) == 1
    assert len(t1.clips) == 0
    assert len(t3.clips) == 0
    assert isinstance(t2.clips[0], AudioClip)
    window.close()


def test_universal_audio_playback_on_any_track(qapp):
    """Vérifie que le moteur audio lit bien les AudioClips quel que soit le type formel de la piste."""
    engine = AudioEngine()
    proj = Project.create_empty()
    engine.set_project(proj)

    # Piste MIDI qui contient un AudioClip (ex: enregistrement fait sur cette piste)
    track = Track(name="Piste Hybride", track_type="midi")
    audio = np.ones((1000, 2), dtype=np.float32) * 0.7
    clip = AudioClip(name="Test Audio", audio_data=audio, start_beat=0.0, length_beats=4.0)
    track.clips.append(clip)
    proj.add_track(track)

    # Rendre une tranche
    out = engine._render_track_slice(track, start_b=0.0, end_b=1.0, frames=256, bpm=120.0)
    assert out is not None
    assert np.any(np.abs(out) > 0.0)


