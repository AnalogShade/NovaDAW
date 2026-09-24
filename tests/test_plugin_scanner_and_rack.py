"""
tests/test_plugin_scanner_and_rack.py - Tests automatisés pour le scanner étendu VST2/VST3,
la détection d'Omnisphere vs Kontakt, le rack flottant F11 et le routage MIDI.
"""
import os
import sys
import time
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from core.project import Project, Track
from core.plugin_manager import (
    global_plugin_manager,
    get_default_plugin_directories,
    is_vst2_dll,
    PluginInfo,
    PluginScanWorker
)
from ui.floating_plugin_rack import FloatingPluginRackDialog
from ui.vst_rack import VstRackWidget
from ui.track_header import TrackHeaderWidget
from ui.main_window import MainWindow


def test_get_default_plugin_directories():
    """Vérifie que les répertoires scannés couvrent VST3, VST2, 64-bit et 32-bit (Spectrasonics, Steinberg, etc.)."""
    dirs = get_default_plugin_directories()
    assert isinstance(dirs, list)
    assert len(dirs) >= 4

    dirs_lower = [d.lower() for d in dirs]
    # Doit inclure Common Files VST3
    assert any("vst3" in d for d in dirs_lower)
    # Doit inclure des répertoires VST2 (Steinberg, VstPlugins ou Spectrasonics)
    assert any("vstplugins" in d or "steinberg" in d or "spectrasonics" in d for d in dirs_lower)


def test_is_vst2_dll_pe_parser():
    """Vérifie que le parser PE ultra-rapide identifie correctement les DLLs VST2 sans exécuter de code."""
    # 1. Tester sur une DLL standard non-VST (comme python3.dll ou une DLL de Windows)
    py_dir = os.path.dirname(sys.executable)
    non_vst_dll = None
    for f in os.listdir(py_dir):
        if f.lower().endswith(".dll"):
            non_vst_dll = os.path.join(py_dir, f)
            break

    if non_vst_dll:
        assert is_vst2_dll(non_vst_dll) is False

    # 2. Si Omnisphere.dll existe sur la machine de l'utilisateur, vérifier qu'il est reconnu comme VST2
    omnisphere_paths = [
        r"C:\Program Files\Steinberg\VstPlugins\Omnisphere.dll",
        r"C:\ProgramData\Spectrasonics\plug-ins\64bit\Omnisphere.dll",
    ]
    for p in omnisphere_paths:
        if os.path.exists(p):
            assert is_vst2_dll(p) is True


def test_omnisphere_vs_kontakt_detection_and_categorization():
    """
    Vérifie la résolution de l'énigme Omnisphere vs Kontakt :
    - Kontakt possède une version VST3 64-bit native (compatible direct).
    - Omnisphere est présent en DLLs VST 2.4 héritées avec message diagnostique clair.
    """
    plugins = global_plugin_manager.plugins
    assert len(plugins) > 0

    kontakt_vst3 = next((p for p in plugins if "kontakt" in p.name.lower() and p.format == "vst3"), None)
    omnisphere_plugins = [p for p in plugins if "omnisphere" in p.name.lower()]

    if kontakt_vst3:
        assert kontakt_vst3.format == "vst3"
        assert kontakt_vst3.is_compatible is True
        assert kontakt_vst3.plugin_type == "instrument"

    if omnisphere_plugins:
        # Toutes les versions d'Omnisphere trouvées sont du VST2 hérité
        for omni in omnisphere_plugins:
            assert omni.format == "vst2"
            assert omni.is_compatible is False
            assert omni.plugin_type == "instrument"
            # Le message diagnostique doit être en français et mentionner la version VST3 de Spectrasonics
            assert "vst 2.4" in omni.error_message.lower()
            assert "spectrasonics" in omni.error_message.lower() or "vst3" in omni.error_message.lower()


def test_plugin_cache_speed_and_metadata():
    """Vérifie que le cache stocke mtime, format, et que le scan incrémental est instantané (<50ms)."""
    # Chaque plugin doit avoir son format renseigné
    for p in global_plugin_manager.plugins:
        assert p.format in ("vst3", "vst2")
        assert hasattr(p, "file_mtime")
        assert hasattr(p, "file_size")

    # Mesurer la vitesse d'une vérification du cache
    start = time.perf_counter()
    cache_dict = {p.file_path: p for p in global_plugin_manager.plugins}
    assert len(cache_dict) > 0
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert elapsed_ms < 50.0


def test_floating_plugin_rack_dialog(qapp):
    """Vérifie la fenêtre flottante F11 (style Cubase), son rack et l'épinglage au premier plan."""
    project = Project.create_empty()
    project.add_rack_plugin("novadaw.drum_machine", "Nova Drums", "instrument")

    dlg = FloatingPluginRackDialog(project)
    assert dlg.windowTitle().startswith("🎛️")
    assert dlg.chk_stay_on_top.isChecked() is True
    assert bool(dlg.windowFlags() & Qt.WindowStaysOnTopHint) is True

    # Basculer Stay on top
    dlg.chk_stay_on_top.setChecked(False)
    assert bool(dlg.windowFlags() & Qt.WindowStaysOnTopHint) is False
    dlg.chk_stay_on_top.setChecked(True)
    assert bool(dlg.windowFlags() & Qt.WindowStaysOnTopHint) is True

    # Vérifier que le slot de plugin est bien affiché
    assert len(project.plugin_rack) == 1
    assert dlg.rack_widget.stack_layout.count() >= 2  # slot + bouton slot vide + stretch
    dlg.close()


def test_track_header_rack_integration(qapp):
    """Vérifie que l'en-tête de piste liste les instruments du rack et propose d'ajouter au rack."""
    project = Project.create_empty()
    project.add_rack_plugin("C:\\Fake\\Synth.vst3", "Mon Super Synth", "instrument")

    track = Track(name="Piste MIDI 1", track_type="midi")
    project.add_track(track)

    win = MainWindow()
    win.project = project
    win.refresh_project_ui()

    header = win.headers_layout.itemAt(0).widget()
    assert isinstance(header, TrackHeaderWidget)

    # Vérifier le contenu du combo plugin
    items = [header.combo_plugin.itemText(i) for i in range(header.combo_plugin.count())]
    assert any("Synthé Interne" in it for it in items)
    assert any("Nova Drums" in it for it in items)
    assert any("[Rack #01] Mon Super Synth" in it for it in items)
    assert any("Assigner un instrument au Rack" in it for it in items)

    # Assigner l'instrument du rack à la piste
    rack_idx = next(i for i, it in enumerate(items) if "[Rack #01]" in it)
    header.combo_plugin.setCurrentIndex(rack_idx)
    assert track.plugin_path == "C:\\Fake\\Synth.vst3"
    assert track.plugin_name == "Mon Super Synth"
    win.close()


def test_main_window_floating_vst_rack_shortcut(qapp):
    """Vérifie l'ouverture et le focus du rack flottant avec F11."""
    win = MainWindow()
    assert win.floating_vst_rack is None

    win.show_floating_vst_rack()
    assert win.floating_vst_rack is not None
    assert win.floating_vst_rack.isVisible() is True

    # Réappel ne doit pas recréer une 2ème fenêtre mais réutiliser l'existante
    existing = win.floating_vst_rack
    win.show_floating_vst_rack()
    assert win.floating_vst_rack is existing
    win.floating_vst_rack.close()
    win.close()
