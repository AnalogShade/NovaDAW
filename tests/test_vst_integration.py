"""
tests/test_vst_integration.py - Tests unitaires pour l'intégration VST3 et le routage de piste
"""
import os
import pytest
from core.project import Project, Track, MidiClip, MidiNote
from core.serializer import save_project, load_project
from core.plugin_manager import (
    global_plugin_manager,
    get_default_vst3_directories,
    PluginInfo
)
from core.audio_engine import AudioEngine


def test_default_vst3_directories_dynamic():
    """Vérifie que les répertoires standards sont découverts dynamiquement sans chemins en dur"""
    dirs = get_default_vst3_directories()
    assert isinstance(dirs, list)
    assert len(dirs) > 0
    # Doit contenir au moins le dossier Common Files sous Windows
    for d in dirs:
        assert os.path.isabs(d)


def test_plugin_cache_loaded():
    """Vérifie que le gestionnaire de plugins charge le cache correctement"""
    assert hasattr(global_plugin_manager, "plugins")
    assert isinstance(global_plugin_manager.plugins, list)
    assert len(global_plugin_manager.plugins) > 0

    instruments = global_plugin_manager.get_compatible_instruments()
    effects = global_plugin_manager.get_compatible_effects()
    assert len(instruments) > 0
    assert len(effects) > 0


def test_track_plugin_attributes_and_serialization(tmp_path):
    """Vérifie que les attributs plugin de Track sont sérialisés et désérialisés"""
    p = Project(name="Test VST Project")
    t = Track(
        name="MIDI VST Track",
        track_type="midi",
        plugin_path="C:\\Fake\\Path\\Synth.vst3",
        plugin_name="SuperSynth"
    )
    p.add_track(t)

    file_path = str(tmp_path / "project_vst.ndaw")
    save_project(p, file_path)

    loaded = load_project(file_path)
    assert len(loaded.tracks) == 1
    assert loaded.tracks[0].plugin_path == "C:\\Fake\\Path\\Synth.vst3"
    assert loaded.tracks[0].plugin_name == "SuperSynth"


def test_audio_engine_fallback_with_vst():
    """Vérifie que l'AudioEngine gère le fallback sans planter si un VST est inexistant"""
    engine = AudioEngine()
    proj = Project.create_default()
    # Assigner un plugin inexistant
    proj.tracks[0].plugin_path = "C:\\NonExistent\\Plugin.vst3"
    engine.set_project(proj)

    # Le rendu de tranche ne doit pas planter et doit fallback sur le synthé interne
    slice_buf = engine._render_track_slice(proj.tracks[0], 0.0, 1.0, 1024, 120.0)
    assert slice_buf is not None
    assert slice_buf.shape == (1024, 2)
    engine.close()
