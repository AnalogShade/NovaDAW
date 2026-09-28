import os
import tempfile
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QCloseEvent

from core.project import Project, Track, MidiClip, AudioClip, MidiNote
from ui.main_window import MainWindow


def test_unsaved_changes_detection_and_window_title(qapp):
    """
    Vérifie la détection des modifications non enregistrées et l'indicateur '*' dans le titre :
    1. Un nouveau projet vide initial n'a pas de modifications non enregistrées (pas de '*').
    2. L'ajout d'une piste ou modification marque le projet 'dirty' et ajoute '*' au titre.
    3. L'enregistrement réinitialise l'état 'dirty' et retire le '*'.
    4. Une nouvelle modification réactive le statut 'dirty'.
    """
    window = MainWindow()
    window.resize(1024, 768)

    # 1. État initial : propre
    assert window.has_unsaved_changes() is False
    assert "*" not in window.windowTitle()

    # 2. Modification : ajout d'une piste
    t = Track(name="Synth Pad", track_type="midi")
    window.project.add_track(t)
    window.set_dirty(True)

    assert window.has_unsaved_changes() is True
    assert "*" in window.windowTitle()

    # 3. Sauvegarde dans un fichier temporaire
    with tempfile.NamedTemporaryFile(suffix=".ndaw", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        window.project.file_path = tmp_path
        save_success = window.save_project_action()
        assert save_success is True

        # Après sauvegarde : propre
        assert window.has_unsaved_changes() is False
        assert "*" not in window.windowTitle()

        # 4. Modification secondaire : changement de tempo (BPM)
        window._on_bpm_changed(130.0)
        assert window.has_unsaved_changes() is True
        assert "*" in window.windowTitle()

    finally:
        window.close()
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


def test_maybe_save_changes_choices(qapp):
    """
    Vérifie les trois choix possibles lors de la demande de confirmation de sauvegarde :
    - 'discard' (Ne pas enregistrer) -> autorise l'action (retourne True)
    - 'cancel' (Annuler) -> annule l'action (retourne False)
    - 'save' (Enregistrer) -> enregistre et autorise l'action (retourne True)
    """
    window = MainWindow()
    with tempfile.NamedTemporaryFile(suffix=".ndaw", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        window.project.file_path = tmp_path

        # Si le projet n'a aucune modification, pas de prompt et retourne True
        assert window.has_unsaved_changes() is False
        assert window.maybe_save_changes("close") is True

        # Rendre le projet modifié
        window.set_dirty(True)
        assert window.has_unsaved_changes() is True

        # Choix 1 : Annuler ('cancel')
        window._test_save_prompt_response = "cancel"
        assert window.maybe_save_changes("close") is False

        # Choix 2 : Ne pas enregistrer ('discard')
        window._test_save_prompt_response = "discard"
        assert window.maybe_save_changes("close") is True

        # Choix 3 : Enregistrer ('save')
        window._test_save_prompt_response = "save"
        assert window.maybe_save_changes("close") is True
        # La sauvegarde doit avoir nettoyé l'état dirty
        assert window.has_unsaved_changes() is False

    finally:
        window.close()
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


def test_close_event_cancellation_when_unsaved(qapp):
    """
    Vérifie que la fermeture de la fenêtre est ignorée (annulée) si l'utilisateur choisit 'Annuler'.
    """
    window = MainWindow()
    window.set_dirty(True)

    # Simuler le clic sur 'Annuler'
    window._test_save_prompt_response = "cancel"

    close_ev = QCloseEvent()
    window.closeEvent(close_ev)

    # L'événement de fermeture doit avoir été rejeté (ignoré)
    assert close_ev.isAccepted() is False

    # Simuler le clic sur 'Ne pas enregistrer'
    window._test_save_prompt_response = "discard"
    close_ev2 = QCloseEvent()
    window.closeEvent(close_ev2)

    # L'événement de fermeture doit avoir été accepté
    assert close_ev2.isAccepted() is True


def test_new_and_open_project_protection_against_unsaved_changes(qapp):
    """
    Vérifie que 'Nouveau Projet' refuse d'écraser le projet en cours si l'utilisateur annule.
    """
    window = MainWindow()
    t = Track(name="Projet Existant", track_type="audio")
    window.project.add_track(t)
    window.set_dirty(True)

    # L'utilisateur tente de faire Nouveau Projet mais clique 'Annuler'
    window._test_save_prompt_response = "cancel"
    res = window.new_project()
    assert res is False
    # La piste existante est toujours là !
    assert len(window.project.tracks) == 1
    assert window.project.tracks[0].name == "Projet Existant"

    # L'utilisateur choisit 'Ne pas enregistrer'
    window._test_save_prompt_response = "discard"
    res = window.new_project()
    assert res is True
    # Nouveau projet vide créé
    assert len(window.project.tracks) == 0
    assert window.has_unsaved_changes() is False

    window.close()
