"""
tests/test_project_path_and_save_as.py - Tests unitaires pour :
1. L'affichage en tout temps du chemin complet du projet (titre, barre supérieure, barre d'état).
2. L'amélioration de 'Enregistrer sous...' :
   - Répertoire initial et nom pré-rempli dans l'explorateur.
   - Boîte de confirmation avec Oui/Non si le fichier existe déjà.
   - Annulation/renommage sans perte si l'utilisateur choisit 'Non'.
"""
import os
import tempfile
import pytest
from PySide6.QtWidgets import QApplication

from core.project import Project, Track
from core.serializer import save_project
from ui.main_window import MainWindow


def test_full_project_path_display_always_visible(qapp):
    """
    Vérifie que le chemin complet du projet est affiché en tout temps :
    - [Non enregistré] pour un nouveau projet vide
    - Le chemin absolu complet dans le titre de fenêtre, la barre supérieure (brand bar)
      et la barre d'état permanente lorsqu'un fichier est associé.
    """
    window = MainWindow()
    try:
        # 1. Projet neuf non enregistré
        assert "Non enregistré" in window.windowTitle()
        assert "Non enregistré" in window.lbl_project_path_top.text()
        assert "Non enregistré" in window.lbl_project_path_status.text()

        # 2. Association d'un fichier et sauvegarde
        with tempfile.NamedTemporaryFile(suffix=".ndaw", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            window.project.file_path = tmp_path
            window.project.name = "ProjetTestComplet"
            window.save_project_action()

            full_path = os.path.abspath(tmp_path)
            # Vérification du chemin complet dans les trois zones d'affichage
            assert full_path in window.windowTitle()
            assert full_path in window.lbl_project_path_top.text()
            assert full_path in window.lbl_project_path_status.text()
            assert full_path in window.lbl_project_path_top.toolTip()
            assert full_path in window.lbl_project_path_status.toolTip()

            # 3. Modification (dirty) : l'étoile '*' s'ajoute tout en conservant le chemin complet
            window.set_dirty(True)
            assert "*" in window.windowTitle()
            assert full_path in window.windowTitle()

            # 4. Nouveau projet : réinitialisation à "Non enregistré"
            window._test_save_prompt_response = "discard"
            window.new_project()
            assert "Non enregistré" in window.windowTitle()
            assert "Non enregistré" in window.lbl_project_path_top.text()
            assert "Non enregistré" in window.lbl_project_path_status.text()

        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    finally:
        window.close()


def test_save_as_initial_path_and_prefilled_name(qapp, tmp_path):
    """
    Vérifie que 'Enregistrer sous...' pré-remplit le dossier et le nom du fichier actuel
    si le projet a déjà un emplacement enregistré sur le disque.
    """
    window = MainWindow()
    try:
        existing_dir = str(tmp_path / "mes_compositions")
        os.makedirs(existing_dir, exist_ok=True)
        file_path = os.path.join(existing_dir, "MorceauElectro.ndaw")
        save_project(window.project, file_path)

        window.project.file_path = file_path
        window.project.name = "MorceauElectro"
        window._update_project_path_display()

        # Simuler Save As vers un nouveau fichier
        new_dest = os.path.join(existing_dir, "MorceauElectro_v2.ndaw")
        window._test_save_as_path = new_dest

        success = window.save_project_as_dialog()
        assert success is True
        assert window.project.file_path == new_dest
        assert window.project.name == "MorceauElectro_v2"
        assert os.path.exists(new_dest)
        assert os.path.abspath(new_dest) in window.windowTitle()
        assert os.path.abspath(new_dest) in window.lbl_project_path_top.text()

    finally:
        window.close()


def test_save_as_overwrite_refusal_and_rename_loop(qapp, tmp_path):
    """
    Vérifie le comportement lorsque l'utilisateur choisit d'écraser ou refuse d'écraser :
    1. L'utilisateur tente d'enregistrer sous un nom de fichier existant.
    2. La confirmation apparaît. L'utilisateur répond 'Non' (refus d'écrasement).
    3. Rien n'est écrasé et le dialogue permet de renommer vers un nouveau nom.
    4. La sauvegarde vers le nouveau nom s'effectue avec succès.
    """
    window = MainWindow()
    try:
        file1 = str(tmp_path / "ProjetOriginal.ndaw")
        file2 = str(tmp_path / "ProjetOriginal_Renomme.ndaw")
        save_project(window.project, file1)
        original_content = open(file1, "r", encoding="utf-8").read()

        window.project.file_path = file1
        window.project.name = "ProjetOriginal"
        # Ajout d'une piste pour marquer une différence
        window.project.add_track(Track(name="Basse", track_type="midi"))

        # 1er choix : on tente d'écraser file1, mais l'utilisateur dit 'Non' (False)
        # 2e choix : on fournit le nouveau chemin file2
        window._test_save_as_path = [file1, file2]
        window._test_overwrite_prompt_response = False  # Refuse d'écraser

        success = window.save_project_as_dialog()
        assert success is True

        # file1 ne doit pas avoir été écrasé (conserver son contenu original)
        with open(file1, "r", encoding="utf-8") as f:
            assert f.read() == original_content

        # file2 a bien été créé avec la nouvelle piste
        assert os.path.exists(file2)
        assert window.project.file_path == file2
        assert window.project.name == "ProjetOriginal_Renomme"
        assert os.path.abspath(file2) in window.windowTitle()

    finally:
        window.close()


def test_save_as_overwrite_confirmed(qapp, tmp_path):
    """
    Vérifie que si l'utilisateur confirme 'Oui' à la question d'écrasement,
    le fichier existant est correctement écrasé et mis à jour.
    """
    window = MainWindow()
    try:
        file1 = str(tmp_path / "MonMorceau.ndaw")
        save_project(window.project, file1)

        window.project.file_path = file1
        window.project.name = "MonMorceau"
        window.project.add_track(Track(name="Solo Guitare", track_type="audio"))

        window._test_save_as_path = file1
        window._test_overwrite_prompt_response = True  # Confirme l'écrasement (Oui)

        success = window.save_project_as_dialog()
        assert success is True
        assert window.project.file_path == file1
        assert "Solo Guitare" in [t.name for t in window.project.tracks]

    finally:
        window.close()


def test_save_as_cancelled_dialog(qapp):
    """
    Vérifie que l'annulation pure et simple du dialogue (Annuler ou fermeture)
    retourne False sans modifier le projet en cours.
    """
    window = MainWindow()
    try:
        window.project.name = "ProjetIntact"
        window._test_save_as_path = ""  # Simule le clic sur Annuler

        success = window.save_project_as_dialog()
        assert success is False
        assert window.project.name == "ProjetIntact"
    finally:
        window.close()
