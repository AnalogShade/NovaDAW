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


def test_editor_tracking_and_close_helpers():
    """Vérifie le suivi des fenêtres d'éditeurs et la fermeture programmée"""
    import threading
    fake_path = "C:\\Fake\\Plugin.vst3"
    assert not global_plugin_manager.is_editor_open(fake_path)

    evt = threading.Event()
    global_plugin_manager.open_editors[fake_path] = evt
    assert global_plugin_manager.is_editor_open(fake_path)

    # Fermeture de l'éditeur ciblé
    global_plugin_manager.close_editor(fake_path)
    assert evt.is_set()

    # Fermeture globale
    evt2 = threading.Event()
    global_plugin_manager.open_editors["C:\\Fake2.vst3"] = evt2
    global_plugin_manager.close_all_editors()
    assert evt2.is_set()
    assert len(global_plugin_manager.open_editors) == 0


def test_audio_engine_offline_midi_protection():
    """Vérifie que l'AudioEngine protège contre les crashs natifs pour les plugins multi-bus complexes"""
    engine = AudioEngine()

    class FakePlugin:
        def __init__(self, name, is_instrument=True):
            self.name = name
            self.is_instrument = is_instrument

    kontakt = FakePlugin("Kontakt")
    sampletank = FakePlugin("SampleTank 4")
    synth = FakePlugin("Syntronik")
    fx = FakePlugin("TR5 Black 76", is_instrument=False)

    assert engine._can_render_vst_offline(kontakt) is False
    assert engine._can_render_vst_offline(sampletank) is False
    assert engine._can_render_vst_offline(synth) is True
    assert engine._can_render_vst_offline(fx) is False
    assert engine._can_render_vst_offline(None) is False

    engine.close()


def test_piano_roll_track_binding():
    """Vérifie que la sélection d'une piste met à jour le Piano Roll et son routage instrument"""
    from PySide6.QtWidgets import QApplication
    from ui.piano_roll import PianoRoll

    app = QApplication.instance() or QApplication([])

    engine = AudioEngine()
    roll = PianoRoll(engine)

    track_midi = Track(name="Synth Piste", track_type="midi", plugin_name="Syntronik", plugin_path="C:\\Fake\\Syntronik.vst3")
    roll.set_active_track(track_midi)

    assert roll.current_track == track_midi
    assert "Synth Piste" in roll.lbl_title.text()
    assert "Syntronik" in roll.lbl_title.text()

    # Changement vers piste audio
    track_audio = Track(name="Voix", track_type="audio")
    roll.set_active_track(track_audio)
    assert "Piste audio" in roll.lbl_title.text()

    # Désélection
    roll.set_active_track(None)
    assert roll.current_track is None
    assert "Aucune piste sélectionnée" in roll.lbl_title.text()

    engine.close()
