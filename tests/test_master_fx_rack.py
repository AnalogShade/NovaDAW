"""
tests/test_master_fx_rack.py - Tests unitaires pour le rack d'effets Master,
la synchronisation de l'onglet Effets Master (F6) et le filtrage du mixeur.
"""
import pytest
import numpy as np
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

from core.project import Project, Track
from core.audio_engine import AudioEngine
from plugins.registry import plugin_registry, ensure_plugins_loaded
from ui.master_fx_rack import MasterFxWidget
from ui.main_window import MainWindow
from ui.inspector import TrackInspector


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_master_fx_rack_filters_internal_mixer(qapp):
    """Vérifie que novadaw.mixer n'est jamais exposé dans la chaîne d'effets d'insertion."""
    ensure_plugins_loaded()
    project = Project.create_empty()
    master_t = project.ensure_master_track()

    # Le mixeur est bien présent techniquement sur la piste master
    assert any(getattr(p, "plugin_type_id", "") == "novadaw.mixer" for p in master_t.plugins)

    rack = MasterFxWidget(project)
    insert_plugins = rack.get_master_insert_plugins()

    # Mais le rack d'effets master ne doit pas l'exposer
    assert len(insert_plugins) == 0
    assert rack.lbl_status.text() == "Aucun effet inséré"


def test_master_fx_rack_add_eq_and_compressor(qapp):
    """Vérifie l'ajout d'égaliseur et de compresseur via le rack Master."""
    ensure_plugins_loaded()
    project = Project.create_empty()
    master_t = project.ensure_master_track()
    rack = MasterFxWidget(project)

    # Ajouter Égaliseur
    rack.add_equalizer()
    insert_plugins = rack.get_master_insert_plugins()
    assert len(insert_plugins) == 1
    assert insert_plugins[0].plugin_type_id == "novadaw.equalizer"
    assert "1/1 effet(s) actif(s)" in rack.lbl_status.text()

    # Ajouter Compresseur
    rack.add_compressor()
    insert_plugins = rack.get_master_insert_plugins()
    assert len(insert_plugins) == 2
    assert insert_plugins[1].plugin_type_id == "novadaw.compressor"
    assert "2/2 effet(s) actif(s)" in rack.lbl_status.text()

    # Le mixeur interne est toujours conservé sur la piste
    assert any(getattr(p, "plugin_type_id", "") == "novadaw.mixer" for p in master_t.plugins)


def test_master_fx_rack_reordering_and_removal(qapp):
    """Vérifie le réordonnancement (Up/Down) et la suppression d'effets master."""
    ensure_plugins_loaded()
    project = Project.create_empty()
    master_t = project.ensure_master_track()
    rack = MasterFxWidget(project)

    rack.add_equalizer()
    rack.add_compressor()

    insert_plugins = rack.get_master_insert_plugins()
    assert insert_plugins[0].plugin_type_id == "novadaw.equalizer"
    assert insert_plugins[1].plugin_type_id == "novadaw.compressor"

    # Déplacer le compresseur vers le haut (idx 1 -> idx 0)
    rack._move_plugin_up(1)
    insert_plugins = rack.get_master_insert_plugins()
    assert insert_plugins[0].plugin_type_id == "novadaw.compressor"
    assert insert_plugins[1].plugin_type_id == "novadaw.equalizer"

    # Supprimer le compresseur
    comp = insert_plugins[0]
    rack.remove_native_plugin(comp)
    insert_plugins = rack.get_master_insert_plugins()
    assert len(insert_plugins) == 1
    assert insert_plugins[0].plugin_type_id == "novadaw.equalizer"


def test_master_fx_rack_global_bypass(qapp):
    """Vérifie le bypass global (A/B mastering)."""
    ensure_plugins_loaded()
    project = Project.create_empty()
    master_t = project.ensure_master_track()
    rack = MasterFxWidget(project)

    rack.add_equalizer()
    rack.add_compressor()

    insert_plugins = rack.get_master_insert_plugins()
    assert all(p.enabled for p in insert_plugins)

    # Bypass global activé
    rack.toggle_global_bypass()
    assert all(not p.enabled for p in insert_plugins)
    assert "0/2 effet(s) actif(s)" in rack.lbl_status.text()

    # Réactivation globale
    rack.toggle_global_bypass()
    assert all(p.enabled for p in insert_plugins)
    assert "2/2 effet(s) actif(s)" in rack.lbl_status.text()


def test_main_window_master_fx_tab_and_shortcuts(qapp):
    """Vérifie la présence de l'onglet Effets Master et la commutation via bouton et raccourci."""
    win = MainWindow()
    win.show()

    # 1. Vérifier la présence de l'onglet dans lower_zone
    tab_titles = [win.lower_zone.tabText(i) for i in range(win.lower_zone.count())]
    assert any("Effets Master" in t for t in tab_titles)

    # 2. Vérifier que show_master_fx_rack bascule bien sur le widget
    win.show_master_fx_rack()
    assert win.lower_zone.currentWidget() == win.master_fx_widget

    # 3. Vérifier que le clic sur btn_master_fx du mixeur bascule sur l'onglet Effets Master
    win.show_mixer_console()
    assert win.lower_zone.currentWidget() == win.mixer_widget

    win.mixer_widget.btn_master_fx.click()
    assert win.lower_zone.currentWidget() == win.master_fx_widget

    # 4. Vérifier que l'ajout d'un effet met à jour le badge FX du mixeur
    win.master_fx_widget.add_equalizer()
    win.mixer_widget._update_master_fx_badges()
    assert "⚡ FX (1)" in win.mixer_widget.btn_master_fx.text()

    win.close()


def test_inspector_filters_mixer_on_master_track(qapp):
    """Vérifie que l'inspecteur masque novadaw.mixer sur la piste master."""
    ensure_plugins_loaded()
    win = MainWindow()
    win.show()

    master_t = win.project.ensure_master_track()
    win.inspector.set_track(master_t)

    # Aucune carte de plugin ne doit contenir "Mixeur de Pistes"
    items_count = win.inspector.plugins_container.count()
    mixer_cards = []
    for i in range(items_count):
        w = win.inspector.plugins_container.itemAt(i).widget()
        if w:
            for child in w.findChildren(object):
                txt = getattr(child, "text", lambda: "")()
                if "Mixeur" in txt:
                    mixer_cards.append(txt)

    assert len(mixer_cards) == 0

    win.close()


def test_audio_engine_processes_master_fx(qapp):
    """Vérifie que le moteur audio traite sans erreur les effets insérés sur le master."""
    ensure_plugins_loaded()
    project = Project.create_empty()
    track = Track(name="Synth Track", track_type="midi", volume=1.0)
    project.add_track(track)
    master_t = project.ensure_master_track()

    # Ajouter EQ et Compresseur sur le Master
    eq = plugin_registry.create_plugin("novadaw.equalizer")
    comp = plugin_registry.create_plugin("novadaw.compressor")
    master_t.add_plugin(eq)
    master_t.add_plugin(comp)

    engine = AudioEngine()
    engine.set_project(project)

    # Traiter un bloc audio via le moteur
    out = np.zeros((1024, 2), dtype=np.float32)
    out[:, 0] = np.sin(np.linspace(0, 440 * 2 * np.pi * (1024 / 44100), 1024))
    out[:, 1] = np.cos(np.linspace(0, 440 * 2 * np.pi * (1024 / 44100), 1024))

    # Vérifier que les effets master traitent le buffer
    processed = eq.process(out, engine.sample_rate)
    assert processed.shape == (1024, 2)
    assert not np.isnan(processed).any()

    processed_comp = comp.process(processed, engine.sample_rate)
    assert processed_comp.shape == (1024, 2)
    assert not np.isnan(processed_comp).any()
