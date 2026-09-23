"""
tests/test_empty_project.py - Tests unitaires validant l'ouverture sur projet vide et la réinitialisation par 'Nouveau Projet'
"""
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest
from PySide6.QtWidgets import QApplication
from core.project import Project, Track
from core.audio_engine import AudioEngine
from core.action_registry import action_registry
from ui.main_window import MainWindow


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if not app:
        app = QApplication(["--platform", "offscreen"])
    return app


def test_01_project_create_empty():
    """Vérifie que Project.create_empty() crée un projet à 0 pistes avec un Master fonctionnel."""
    proj = Project.create_empty()
    assert len(proj.tracks) == 0
    assert proj.name == "Nouveau Projet"
    assert proj.bpm == 120.0
    assert proj.master_track is not None
    assert len(proj.master_track.plugins) >= 1
    assert proj.master_track.plugins[0].plugin_type_id == "novadaw.mixer"


def test_02_project_create_demo():
    """Vérifie que Project.create_demo() et create_default() conservent les 4 pistes de test."""
    demo = Project.create_demo()
    assert len(demo.tracks) == 4
    default = Project.create_default()
    assert len(default.tracks) == 4


def test_03_audio_engine_stop():
    """Vérifie que AudioEngine dispose bien d'une méthode stop() fonctionnelle sans crash."""
    engine = AudioEngine()
    proj = Project.create_empty()
    engine.set_project(proj)
    engine.play()
    assert engine.is_playing is True
    engine.stop()
    assert engine.is_playing is False
    assert engine.current_beat == 0.0
    engine.close()


def test_04_mainwindow_starts_with_empty_project(qapp):
    """Vérifie qu'à l'ouverture de l'application MainWindow, le projet est TOUJOURS vide."""
    win = MainWindow()
    try:
        assert len(win.project.tracks) == 0
        assert win.selected_track_id is None
        assert win.inspector.current_track is None
        assert win.piano_roll.current_track is None
        assert win.audio_editor.current_track is None
    finally:
        win.close()


def test_05_mainwindow_new_project_resets_dirty_state(qapp):
    """Vérifie que faire 'Nouveau Projet' lorsqu'un projet est déjà en cours réinitialise à un projet vide."""
    win = MainWindow()
    try:
        # 1. Simuler un projet en cours avec des pistes
        track1 = Track(name="Synth Test", track_type="midi")
        win.project.add_track(track1)
        track2 = Track(name="Audio Test", track_type="audio")
        win.project.add_track(track2)
        win.refresh_project_ui()
        win._on_track_selected(track1.id)
        win._on_seek(8.0)

        assert len(win.project.tracks) == 2
        assert win.selected_track_id == track1.id
        assert win.inspector.current_track is not None
        assert win.audio_engine.current_beat == 8.0

        # 2. Déclencher Nouveau Projet
        win.new_project()

        # 3. Vérifier que tout est remis à zéro dans un état propre et vide
        assert len(win.project.tracks) == 0
        assert win.selected_track_id is None
        assert win.inspector.current_track is None
        assert win.piano_roll.current_track is None
        assert win.audio_editor.current_track is None
        assert win.audio_engine.current_beat == 0.0
        assert win.timeline_grid.selected_clip is None
    finally:
        win.close()


def test_06_mainwindow_load_demo_then_new_project(qapp):
    """Vérifie le chargement du projet démo puis le retour à un projet vide via new_project()."""
    win = MainWindow()
    try:
        win.load_demo_project()
        assert len(win.project.tracks) == 4

        # Retour au projet vide
        win.new_project()
        assert len(win.project.tracks) == 0
        assert win.selected_track_id is None
    finally:
        win.close()


def test_07_action_registry_new_project(qapp):
    """Vérifie que l'action MCP/IPC novadaw_new_project réinitialise bien le projet."""
    win = MainWindow()
    try:
        win.load_demo_project()
        assert len(win.project.tracks) == 4

        res = action_registry.execute("novadaw_new_project", win)
        assert res["status"] == "success"
        assert len(win.project.tracks) == 0
    finally:
        win.close()
