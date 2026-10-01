"""
tests/test_recent_projects.py - Tests unitaires pour la gestion des projets récents
dans NovaDAW (menu Fichier -> Projets récents, persistance, détection de fichiers déplacés).
"""
import os
import json
import tempfile
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QAction

from core.project import Project, Track
from core.serializer import save_project
from core.recent_projects import RecentProjectsManager
from ui.main_window import MainWindow


def test_recent_projects_manager_crud(tmp_path):
    """Vérifie l'ajout, la déduplication, l'ordonnancement, la suppression et le vidage."""
    settings_file = str(tmp_path / "settings.json")
    manager = RecentProjectsManager(settings_file=settings_file, max_items=3)

    # État initial vide
    assert manager.get_recent_projects() == []

    p1 = str(tmp_path / "project1.ndaw")
    p2 = str(tmp_path / "project2.ndaw")
    p3 = str(tmp_path / "project3.ndaw")
    p4 = str(tmp_path / "project4.ndaw")

    # Ajout de projets
    manager.add_recent_project(p1)
    assert manager.get_recent_projects() == [os.path.abspath(p1)]

    manager.add_recent_project(p2)
    assert manager.get_recent_projects() == [os.path.abspath(p2), os.path.abspath(p1)]

    # Ré-ajouter p1 doit le remonter en première position sans doublon
    manager.add_recent_project(p1)
    assert manager.get_recent_projects() == [os.path.abspath(p1), os.path.abspath(p2)]

    # Dépassement de la limite max_items (3)
    manager.add_recent_project(p3)
    manager.add_recent_project(p4)
    recents = manager.get_recent_projects()
    assert len(recents) == 3
    assert recents[0] == os.path.abspath(p4)
    assert recents[1] == os.path.abspath(p3)
    assert recents[2] == os.path.abspath(p1)

    # Suppression d'un élément
    res_del = manager.remove_recent_project(p3)
    assert res_del is True
    assert os.path.abspath(p3) not in manager.get_recent_projects()
    assert len(manager.get_recent_projects()) == 2

    # Effacer tout
    manager.clear_recent_projects()
    assert manager.get_recent_projects() == []


def test_recent_projects_preserves_hardware_settings(tmp_path):
    """Vérifie que la sauvegarde des récents préserve les paramètres audio/graphiques existants."""
    settings_file = str(tmp_path / "settings.json")
    initial_data = {
        "audio": {"buffer_size": 512, "sample_rate": 48000},
        "graphics": {"fps_limit": 120}
    }
    with open(settings_file, "w", encoding="utf-8") as f:
        json.dump(initial_data, f)

    manager = RecentProjectsManager(settings_file=settings_file)
    p = str(tmp_path / "mon_morceau.ndaw")
    manager.add_recent_project(p)

    with open(settings_file, "r", encoding="utf-8") as f:
        saved_data = json.load(f)

    assert saved_data["audio"]["buffer_size"] == 512
    assert saved_data["graphics"]["fps_limit"] == 120
    assert saved_data["recent_projects"] == [os.path.abspath(p)]


def test_main_window_recent_projects_menu_empty_and_populated(qapp, tmp_path):
    """Vérifie la création du menu Projets récents et son actualisation dans MainWindow."""
    settings_file = str(tmp_path / "settings.json")
    test_manager = RecentProjectsManager(settings_file=settings_file)

    window = MainWindow()
    window.recent_projects_manager = test_manager
    window._update_recent_projects_menu()

    try:
        assert hasattr(window, "menu_recent_projects")
        actions = window.menu_recent_projects.actions()
        assert len(actions) == 1
        assert "(Aucun projet récent)" in actions[0].text()
        assert actions[0].isEnabled() is False

        # Sauvegarder un projet
        proj_file = str(tmp_path / "MyProject.ndaw")
        window.project.name = "MyProject"
        window.project.file_path = proj_file
        window.save_project_action()

        # Le menu doit contenir le projet + séparateur + effacer
        window._update_recent_projects_menu()
        updated_actions = window.menu_recent_projects.actions()
        texts = [a.text() for a in updated_actions]

        assert any("MyProject" in t for t in texts)
        assert any("Effacer la liste" in t for t in texts)

    finally:
        window.close()


def test_main_window_open_recent_project_flow(qapp, tmp_path):
    """Vérifie l'ouverture d'un projet depuis la liste des récents."""
    settings_file = str(tmp_path / "settings.json")
    test_manager = RecentProjectsManager(settings_file=settings_file)

    proj_file = str(tmp_path / "CoolBeat.ndaw")
    proj = Project(name="CoolBeat", bpm=128.0)
    proj.add_track(Track(name="Synth Lead", track_type="midi"))
    save_project(proj, proj_file)

    test_manager.add_recent_project(proj_file)

    window = MainWindow()
    window.recent_projects_manager = test_manager
    window._update_recent_projects_menu()

    try:
        assert window.project.name != "CoolBeat"

        # Ouvrir via open_recent_project
        success = window.open_recent_project(proj_file)
        assert success is True
        assert window.project.name == "CoolBeat"
        assert window.project.bpm == 128.0
        assert len(window.project.tracks) == 1
        assert window.project.tracks[0].name == "Synth Lead"

        # Le projet doit être en tête de liste
        assert test_manager.get_recent_projects()[0] == os.path.abspath(proj_file)

    finally:
        window.close()


def test_main_window_open_recent_missing_file_handling(qapp, tmp_path):
    """Vérifie la gestion d'un fichier récent introuvable (déplacé ou supprimé)."""
    settings_file = str(tmp_path / "settings.json")
    test_manager = RecentProjectsManager(settings_file=settings_file)

    missing_file = str(tmp_path / "deleted_project.ndaw")
    test_manager.add_recent_project(missing_file)
    assert os.path.abspath(missing_file) in test_manager.get_recent_projects()

    window = MainWindow()
    window.recent_projects_manager = test_manager
    window._test_recent_not_found_response = "yes"  # Simuler la confirmation de retrait

    try:
        success = window.open_recent_project(missing_file)
        assert success is False

        # Le fichier introuvable doit avoir été retiré des récents
        assert os.path.abspath(missing_file) not in test_manager.get_recent_projects()

    finally:
        window.close()


def test_main_window_clear_recent_projects(qapp, tmp_path):
    """Vérifie l'action d'effacement de l'historique des projets récents."""
    settings_file = str(tmp_path / "settings.json")
    test_manager = RecentProjectsManager(settings_file=settings_file)

    p1 = str(tmp_path / "p1.ndaw")
    test_manager.add_recent_project(p1)
    assert len(test_manager.get_recent_projects()) == 1

    window = MainWindow()
    window.recent_projects_manager = test_manager
    window._update_recent_projects_menu()

    try:
        window.clear_recent_projects()
        assert test_manager.get_recent_projects() == []

        actions = window.menu_recent_projects.actions()
        assert len(actions) == 1
        assert "(Aucun projet récent)" in actions[0].text()

    finally:
        window.close()
