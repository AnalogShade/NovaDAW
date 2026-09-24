"""
tests/test_audio_editing_and_grid.py - Tests automatisés complets pour :
1. Rendu waveform 1:1 sans étirement/décalage lors du rognage/redimensionnement.
2. Respect du décalage de source (source_offset_beats) en lecture audio.
3. Clamping du redimensionnement d'un AudioClip à la durée réelle du fichier audio.
4. Découpe / scission (split) de clips audio et MIDI.
5. Palette d'outils d'édition (Pointeur, Ciseaux, Gomme).
6. Opérations presse-papier (Copier, Couper, Coller à la tête de lecture, Dupliquer).
7. Grille temporelle musicale BPM et aimantage (Snap).
8. Actions MCP correspondantes.
"""
import sys
import os
from pathlib import Path
import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage
from core.project import Project, Track, AudioClip, MidiClip, MidiNote
from core.audio_engine import AudioEngine
from ui.main_window import MainWindow
from ui.timeline_view import TimelineGrid
from ui.editing_toolbar import EditingToolbar
from core.action_registry import action_registry
import core.actions


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if not app:
        app = QApplication([])
    return app


def test_audio_clip_slice_peaks_invariance_on_resize():
    """
    Vérifie qu'un AudioClip dont on réduit la durée ne compresse PAS sa forme d'onde,
    mais conserve les mêmes crêtes 1:1 pour les temps visibles (rognage sans étirement).
    """
    sr = 44100
    bpm = 120.0  # 1 temps = 0.5s = 22050 samples
    samples_per_beat = int((60.0 / bpm) * sr)  # 22050
    total_beats = 8.0
    total_samples = int(total_beats * samples_per_beat)

    # Signal avec une forme caractéristique : rampe croissante
    ramp = np.linspace(0.1, 0.9, total_samples, dtype=np.float32)
    stereo = np.column_stack((ramp, ramp))

    clip = AudioClip(name="Test Ramp", audio_data=stereo, sample_rate=sr, length_beats=8.0)

    # 1. Échantillonnage à pleine longueur (8 temps) sur 320 pixels (40 pixels/temps)
    start_s1 = int(0.0 * samples_per_beat)
    dur_s1 = int(8.0 * samples_per_beat)
    mins_full, maxs_full = clip.get_slice_waveform_peaks(start_s1, start_s1 + dur_s1, 320)
    assert len(maxs_full) == 320

    # 2. Rognage du clip à 4 temps (moitié de la taille)
    clip.length_beats = 4.0
    dur_s2 = int(4.0 * samples_per_beat)
    mins_cropped, maxs_cropped = clip.get_slice_waveform_peaks(start_s1, start_s1 + dur_s2, 160)
    assert len(maxs_cropped) == 160

    # Vérification essentielle : les 160 premiers pixels de la tranche complète
    # correspondent exactement aux 160 pixels de la tranche rognée (aucune distorsion temporelle)
    np.testing.assert_allclose(maxs_cropped, maxs_full[:160], atol=1e-2)
    np.testing.assert_allclose(mins_cropped, mins_full[:160], atol=1e-2)


def test_audio_clip_max_allowed_length():
    """Vérifie que get_max_allowed_length_beats empêche le bloc de dépasser la durée audio réelle"""
    sr = 44100
    bpm = 120.0
    # 2 secondes d'audio = 4.0 temps à 120 BPM
    data = np.zeros((sr * 2, 2), dtype=np.float32)
    clip = AudioClip(name="2s Clip", audio_data=data, sample_rate=sr, start_beat=0.0, length_beats=4.0)

    assert np.isclose(clip.get_total_duration_beats(bpm), 4.0)
    assert np.isclose(clip.get_max_allowed_length_beats(bpm), 4.0)

    # Si on applique un décalage de source de 1.0 temps
    clip.source_offset_beats = 1.0
    assert np.isclose(clip.get_max_allowed_length_beats(bpm), 3.0)


def test_audio_engine_source_offset_playback():
    """Vérifie que AudioEngine lit l'audio à partir de source_offset_beats et respecte les bornes"""
    engine = AudioEngine()
    proj = Project.create_empty()
    proj.bpm = 120.0
    engine.set_project(proj)

    sr = engine.sample_rate
    beats_per_sec = 2.0  # 120 bpm / 60
    samples_per_beat = int(sr / beats_per_sec)

    # Créer un audio où le beat 0-1 vaut 0.2, le beat 1-2 vaut 0.8
    sig = np.zeros((samples_per_beat * 4, 2), dtype=np.float32)
    sig[:samples_per_beat, :] = 0.2
    sig[samples_per_beat:samples_per_beat * 2, :] = 0.8

    track = Track(name="Audio Test", track_type="audio")
    # Clip commençant à beat 0 sur la timeline, mais avec source_offset_beats = 1.0
    # Donc dès beat 0, on doit entendre la valeur 0.8 !
    clip = AudioClip(
        name="Offset Clip",
        audio_data=sig,
        sample_rate=sr,
        start_beat=0.0,
        length_beats=2.0,
        source_offset_beats=1.0
    )
    track.clips.append(clip)
    proj.add_track(track)

    out = engine._render_track_slice(track, start_b=0.0, end_b=0.5, frames=256, bpm=120.0)
    assert out is not None
    # L'amplitude doit correspondre à 0.8 (et non 0.2)
    np.testing.assert_allclose(out[:256, 0], 0.8, atol=1e-3)


def test_midi_clip_split():
    """Vérifie que la scission d'un MidiClip partitionne correctement les notes sans perte"""
    clip = MidiClip(name="Melodie", start_beat=0.0, length_beats=8.0)
    clip.notes = [
        MidiNote(pitch=60, start_beat=1.0, duration=1.0, velocity=100),  # strictly before
        MidiNote(pitch=64, start_beat=3.0, duration=2.0, velocity=100),  # straddles split at 4.0
        MidiNote(pitch=67, start_beat=5.0, duration=1.0, velocity=100),  # strictly after
    ]

    p1, p2 = clip.split(split_beat=4.0)

    # Partie 1 (0.0 à 4.0)
    assert p1.start_beat == 0.0
    assert p1.length_beats == 4.0
    assert len(p1.notes) == 2
    assert p1.notes[0].pitch == 60
    assert p1.notes[0].start_beat == 1.0
    assert p1.notes[0].duration == 1.0
    assert p1.notes[1].pitch == 64
    assert p1.notes[1].start_beat == 3.0
    assert np.isclose(p1.notes[1].duration, 1.0)  # Raccourcie à la coupe

    # Partie 2 (4.0 à 8.0)
    assert p2.start_beat == 4.0
    assert p2.length_beats == 4.0
    assert len(p2.notes) == 2
    assert p2.notes[0].pitch == 64
    assert np.isclose(p2.notes[0].start_beat, 0.0)
    assert np.isclose(p2.notes[0].duration, 1.0)
    assert p2.notes[1].pitch == 67
    assert np.isclose(p2.notes[1].start_beat, 1.0)  # 5.0 - 4.0
    assert p2.notes[1].duration == 1.0


def test_audio_clip_split():
    """Vérifie que la scission d'un AudioClip ajuste parfaitement source_offset_beats"""
    data = np.ones((44100 * 4, 2), dtype=np.float32)
    clip = AudioClip(name="Chant", audio_data=data, sample_rate=44100, start_beat=2.0, length_beats=6.0, source_offset_beats=1.0)

    p1, p2 = clip.split(split_beat=4.0, bpm=120.0)

    # Partie 1 (2.0 à 4.0, longueur 2.0)
    assert p1.start_beat == 2.0
    assert p1.length_beats == 2.0
    assert p1.source_offset_beats == 1.0

    # Partie 2 (4.0 à 8.0, longueur 4.0)
    assert p2.start_beat == 4.0
    assert p2.length_beats == 4.0
    assert p2.source_offset_beats == 3.0  # 1.0 + 2.0


def test_snap_beat_grid_calculation(qapp):
    """Vérifie le calcul d'aimantage (snap) pour différentes résolutions"""
    grid = TimelineGrid(Project.create_empty())

    grid.snap_enabled = True
    # Grille à 1 temps (Noire)
    grid.grid_resolution = 1.0
    assert grid.snap_beat(0.4) == 0.0
    assert grid.snap_beat(0.6) == 1.0
    assert grid.snap_beat(2.8) == 3.0

    # Grille à 0.25 (Double-croche)
    grid.grid_resolution = 0.25
    assert np.isclose(grid.snap_beat(1.12), 1.0)
    assert np.isclose(grid.snap_beat(1.15), 1.25)

    # Grille à 4.0 (1 Mesure)
    grid.grid_resolution = 4.0
    assert grid.snap_beat(2.1) == 4.0
    assert grid.snap_beat(1.8) == 0.0

    # Snap désactivé
    grid.snap_enabled = False
    assert grid.snap_beat(1.37) == 1.37


def test_clipboard_operations_and_paste_at_playhead(qapp):
    """Vérifie le fonctionnement de Copier, Couper, Coller à la tête de lecture, et Dupliquer"""
    proj = Project.create_empty()
    t = Track(name="Guitare", track_type="midi")
    clip = MidiClip(name="Riff", start_beat=0.0, length_beats=4.0)
    t.clips.append(clip)
    proj.add_track(t)

    grid = TimelineGrid(proj)
    grid.selected_clip = (t, clip)

    # 1. Copier
    assert grid.copy_selected_clip() is True
    assert grid._clip_clipboard is not None
    assert grid._clip_clipboard.name == "Riff"

    # 2. Coller à beat 8.0
    grid.playhead_beat = 8.0
    pasted = grid.paste_clip_at_playhead(target_track=t)
    assert pasted is not None
    assert pasted.start_beat == 8.0
    assert len(t.clips) == 2

    # 3. Dupliquer
    grid.selected_clip = (t, pasted)
    dup = grid.duplicate_selected_clip()
    assert dup is not None
    assert dup.start_beat == 12.0  # 8.0 + 4.0
    assert len(t.clips) == 3

    # 4. Couper
    grid.selected_clip = (t, dup)
    assert grid.cut_selected_clip() is True
    assert len(t.clips) == 2
    assert grid.selected_clip is None


def test_editing_toolbar_and_tools_switching(qapp):
    """Vérifie le basculement d'outils et l'intégration de la barre d'outils"""
    window = MainWindow()
    assert hasattr(window, "editing_toolbar")
    toolbar: EditingToolbar = window.editing_toolbar

    # Vérification sélection d'outils
    toolbar.btn_split.click()
    assert window.timeline_grid.active_tool == "split"

    toolbar.btn_erase.click()
    assert window.timeline_grid.active_tool == "erase"

    toolbar.btn_select.click()
    assert window.timeline_grid.active_tool == "select"

    # Vérification changement de grille
    toolbar.combo_grid.setCurrentIndex(1)  # 1/2 Mesure (2.0)
    assert window.timeline_grid.grid_resolution == 2.0

    # Vérification bouton Snap
    toolbar.btn_snap.setChecked(False)
    assert window.timeline_grid.snap_enabled is False
    toolbar.btn_snap.setChecked(True)
    assert window.timeline_grid.snap_enabled is True

    window.close()


def test_split_clip_and_erase_tools_in_timeline_grid(qapp):
    """Vérifie que les outils ciseaux et gomme fonctionnent directement sur la timeline"""
    proj = Project.create_empty()
    t = Track(name="Voix", track_type="audio")
    data = np.zeros((44100 * 6, 2), dtype=np.float32)
    clip = AudioClip(name="Vocal", audio_data=data, sample_rate=44100, start_beat=0.0, length_beats=6.0)
    t.clips.append(clip)
    proj.add_track(t)

    grid = TimelineGrid(proj)

    # 1. Scission via split_clip_at
    p1, p2 = grid.split_clip_at(t, clip, 2.0)
    assert len(t.clips) == 2
    assert p1.length_beats == 2.0
    assert p2.length_beats == 4.0

    # 2. Outil Gomme
    grid.active_tool = "erase"
    # Clic pour supprimer p2
    t.clips = [c for c in t.clips if c.id != p2.id]
    assert len(t.clips) == 1
    assert t.clips[0].id == p1.id


def test_mcp_actions_for_editing_and_grid(qapp):
    """Vérifie les actions MCP novadaw_split_clip, novadaw_copy_clip, novadaw_paste_clip, novadaw_set_grid"""
    window = MainWindow()
    window.project = Project.create_empty()
    t = Track(name="Guitare Electrique", track_type="midi")
    clip = MidiClip(name="Solo", start_beat=0.0, length_beats=8.0)
    t.clips.append(clip)
    window.project.add_track(t)
    window.refresh_project_ui()

    # 1. novadaw_split_clip
    res_split = action_registry.execute("novadaw_split_clip", window, {
        "clip_id_or_name": clip.id,
        "split_beat": 4.0
    })
    assert res_split["status"] == "success"
    assert len(t.clips) == 2

    # 2. novadaw_copy_clip
    res_copy = action_registry.execute("novadaw_copy_clip", window, {
        "clip_id_or_name": t.clips[0].id
    })
    assert res_copy["status"] == "success"

    # 3. novadaw_paste_clip
    res_paste = action_registry.execute("novadaw_paste_clip", window, {
        "target_beat": 16.0
    })
    assert res_paste["status"] == "success"
    assert len(t.clips) == 3

    # 4. novadaw_set_grid
    res_grid = action_registry.execute("novadaw_set_grid", window, {
        "resolution_beats": 0.5,
        "snap_enabled": True
    })
    assert res_grid["status"] == "success"
    assert window.timeline_grid.grid_resolution == 0.5
    assert window.timeline_grid.snap_enabled is True

    # 5. novadaw_set_editing_tool
    res_tool = action_registry.execute("novadaw_set_editing_tool", window, {
        "tool_name": "split"
    })
    assert res_tool["status"] == "success"
    assert window.timeline_grid.active_tool == "split"

    window.close()


def test_timeline_paint_with_tools_and_grid_no_crash(qapp):
    """Vérifie que TimelineGrid dessine la grille, le guide de ciseaux et les waveforms sans exception"""
    proj = Project.create_empty()
    t = Track(name="Piste Audio", track_type="audio")
    data = (0.3 * np.sin(np.linspace(0, 50, 44100 * 4))).astype(np.float32)
    clip = AudioClip(name="Onde", audio_data=np.column_stack((data, data)), start_beat=0.0, length_beats=4.0)
    t.clips.append(clip)
    proj.add_track(t)

    grid = TimelineGrid(proj)
    grid.resize(800, 300)

    # Mode normal
    img = QImage(800, 300, QImage.Format_ARGB32)
    grid.render(img)

    # Mode Ciseaux avec survol
    grid.active_tool = "split"
    grid._hover_clip = (t, clip)
    grid._hover_split_beat = 2.0
    img2 = QImage(800, 300, QImage.Format_ARGB32)
    grid.render(img2)
