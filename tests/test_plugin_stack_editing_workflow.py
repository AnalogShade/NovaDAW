"""
tests/test_plugin_stack_editing_workflow.py
Test vérifiant la correction du bug des effets :
1. Chaque plugin dans la pile de la piste possède son propre bouton 'Ouvrir l'interface' / 'Fermer l'interface'
2. Possibilité d'ouvrir indépendamment l'instrument (NovaSynth) et chaque effet (Égaliseur, Compresseur)
3. Synchronisation automatique des effets dans la vue 'Plugins du projet' (Rack VST / F11)
4. Capacité d'ouvrir et éditer l'effet de la piste depuis 'Plugins du projet'
"""
import pytest
from PySide6.QtWidgets import QApplication, QPushButton
from PySide6.QtCore import Qt

from core.project import Project, Track
from plugins.registry import plugin_registry, ensure_plugins_loaded
from ui.inspector import TrackInspector
from ui.vst_rack import VstRackWidget
from ui.plugin_dialogs import _open_native_editors, is_native_plugin_editor_open


@pytest.fixture(autouse=True)
def clean_editors():
    ensure_plugins_loaded()
    for dlg in list(_open_native_editors.values()):
        dlg.close()
    _open_native_editors.clear()
    yield
    for dlg in list(_open_native_editors.values()):
        dlg.close()
    _open_native_editors.clear()


def test_plugin_stack_individual_open_interface_buttons(qapp):
    """Vérifie que chaque plugin/effet de la pile possède son propre bouton Ouvrir l'interface."""
    project = Project.create_empty()
    track = Track(name="Lead Synth", track_type="midi")
    project.add_track(track)

    inspector = TrackInspector(project)
    inspector.set_track(track)

    # 1. Ajouter le synthétiseur
    synth = plugin_registry.create_plugin("novadaw.synth")
    assert synth is not None
    track.add_plugin(synth)
    track.plugin_path = "novadaw.synth"
    track.plugin_name = "NovaSynth"

    # 2. Ajouter l'égaliseur paramétrique
    eq = plugin_registry.create_plugin("novadaw.equalizer")
    assert eq is not None
    track.add_plugin(eq)

    inspector._rebuild_plugin_stack()

    # Vérifier que les 2 cartes de plugins sont bien créées dans plugins_container
    assert inspector.plugins_container.count() == 2

    card_synth = inspector.plugins_container.itemAt(0).widget()
    card_eq = inspector.plugins_container.itemAt(1).widget()

    # Vérifier que les deux cartes possèdent leur bouton "Ouvrir l'interface"
    btn_open_synth = getattr(card_synth, "btn_open", None)
    btn_open_eq = getattr(card_eq, "btn_open", None)

    assert btn_open_synth is not None
    assert btn_open_eq is not None
    assert "Ouvrir" in btn_open_synth.text()
    assert "Ouvrir" in btn_open_eq.text()

    # 3. Ouvrir l'égaliseur via son bouton dédié sur sa propre ligne
    btn_open_eq.click()

    # Vérifier que c'est bien l'égaliseur qui est ouvert et NON le synthétiseur
    assert is_native_plugin_editor_open(eq) is True
    assert is_native_plugin_editor_open(synth) is False
    assert eq.instance_id in _open_native_editors
    assert synth.instance_id not in _open_native_editors

    # Le bouton de l'égaliseur doit maintenant indiquer "Fermer l'interface"
    card_eq = inspector.plugins_container.itemAt(1).widget()
    card_synth = inspector.plugins_container.itemAt(0).widget()
    assert "Fermer" in card_eq.btn_open.text()
    assert "Ouvrir" in card_synth.btn_open.text()

    # 4. Ouvrir aussi le synthétiseur
    card_synth.btn_open.click()
    assert is_native_plugin_editor_open(synth) is True
    assert is_native_plugin_editor_open(eq) is True

    # Fermer l'égaliseur
    card_eq = inspector.plugins_container.itemAt(1).widget()
    card_eq.btn_open.click()
    assert is_native_plugin_editor_open(eq) is False
    assert is_native_plugin_editor_open(synth) is True

    inspector.close()


def test_effects_reflected_in_project_plugins_rack(qapp):
    """Vérifie que les effets ajoutés à la piste apparaissent bien dans Plugins du projet et sont éditables."""
    project = Project.create_empty()
    track = Track(name="Lead Track", track_type="midi")
    project.add_track(track)

    inspector = TrackInspector(project)
    inspector.set_track(track)

    # Ajouter synthétiseur et égaliseur via les méthodes de l'inspecteur
    inspector._add_synth_plugin()
    inspector._add_equalizer_plugin()

    # Créer le widget Plugins du projet (Rack VST / F11)
    rack_widget = VstRackWidget(project)
    rack_widget.refresh_rack()

    # Vérifier que les deux plugins sont bien présents dans le rack du projet
    rack_paths = [p["file_path"] for p in project.plugin_rack]
    assert "novadaw.synth" in rack_paths
    assert "novadaw.equalizer" in rack_paths

    # Trouver l'item de l'égaliseur dans le rack
    eq_item = next(p for p in project.plugin_rack if p["file_path"] == "novadaw.equalizer")
    assert eq_item["plugin_type"] == "effect"

    track_eq = next(p for p in track.plugins if getattr(p, "plugin_type_id", None) == "novadaw.equalizer")

    # Fermer l'éditeur pour tester l'ouverture depuis le rack du projet
    for dlg in list(_open_native_editors.values()):
        dlg.close()
    assert is_native_plugin_editor_open(track_eq) is False

    # Vérifier que l'ouverture depuis le rack ouvre bien l'instance d'égaliseur de la piste
    rack_widget.open_editor("novadaw.equalizer")

    # L'éditeur de l'instance de la piste doit être ouvert
    assert is_native_plugin_editor_open(track_eq) is True

    # Cliquer à nouveau sur open_editor (bascule Fermer)
    rack_widget.open_editor("novadaw.equalizer")
    assert is_native_plugin_editor_open(track_eq) is False

    inspector.close()
    rack_widget.close()
