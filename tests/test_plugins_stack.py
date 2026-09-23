"""
tests/test_plugins_stack.py - Tests unitaires complets pour l'architecture de plugins modulaires,
l'égaliseur paramétrique, le compresseur dynamique, le mixeur et la pile d'effets de piste.
"""
import os
import pytest
import numpy as np
from core.project import Project, Track, MidiClip, MidiNote
from core.serializer import save_project, load_project
from core.audio_engine import AudioEngine
from plugins.base import BasePlugin
from plugins.registry import plugin_registry, ensure_plugins_loaded
from plugins.equalizer.equalizer_plugin import EqualizerPlugin, EqualizerBand
from plugins.compressor.compressor_plugin import CompressorPlugin
from plugins.mixer.mixer_plugin import MixerPlugin
from ui.plugin_dialogs import open_native_plugin_editor


@pytest.fixture(autouse=True)
def setup_plugins():
    ensure_plugins_loaded()


def test_01_plugin_registry():
    """Vérifie que tous les plugins natifs sont correctement enregistrés"""
    available = plugin_registry.get_available_plugins()
    type_ids = [p["type_id"] for p in available]
    assert "novadaw.equalizer" in type_ids
    assert "novadaw.compressor" in type_ids
    assert "novadaw.mixer" in type_ids

    eq = plugin_registry.create_plugin("novadaw.equalizer")
    assert isinstance(eq, EqualizerPlugin)

    comp = plugin_registry.create_plugin("novadaw.compressor")
    assert isinstance(comp, CompressorPlugin)

    mixer = plugin_registry.create_plugin("novadaw.mixer")
    assert isinstance(mixer, MixerPlugin)


def test_02_equalizer_band_modes():
    """Vérifie les configurations 3, 10, 12 et 24 bandes de l'égaliseur"""
    eq = EqualizerPlugin()

    # 3 bandes
    eq.set_band_count(3)
    assert len(eq.bands) == 3
    assert eq.bands[0].frequency == 100.0
    assert eq.bands[1].frequency == 1000.0
    assert eq.bands[2].frequency == 8000.0

    # 10 bandes
    eq.set_band_count(10)
    assert len(eq.bands) == 10

    # 12 bandes
    eq.set_band_count(12)
    assert len(eq.bands) == 12

    # 24 bandes
    eq.set_band_count(24)
    assert len(eq.bands) == 24


def test_03_equalizer_dsp_processing():
    """Vérifie le traitement audio et la courbe de réponse de l'égaliseur"""
    sr = 44100
    eq = EqualizerPlugin(band_count=3)
    eq.bands[1].frequency = 1000.0
    eq.bands[1].gain_db = 12.0  # +12 dB boost à 1 kHz
    eq.bands[1].invalidate_cache()

    # Signal test sinus 1 kHz
    t = np.linspace(0, 0.05, int(sr * 0.05), endpoint=False)
    sig_1k = (0.2 * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)
    stereo_in = np.column_stack((sig_1k, sig_1k))

    out = eq.process(stereo_in, sr)
    assert out.shape == stereo_in.shape
    # Le signal doit être amplifié par le filtre peaking à 1 kHz
    assert np.max(np.abs(out)) > np.max(np.abs(stereo_in))

    # Test Bypass
    eq.enabled = False
    out_bypassed = eq.process(stereo_in, sr)
    assert np.allclose(out_bypassed, stereo_in)

    # Test courbe de magnitude
    freqs = np.array([100.0, 1000.0, 10000.0])
    eq.enabled = True
    mag = eq.compute_magnitude_response(freqs, sr)
    assert mag[1] > mag[0]  # Réponse plus forte à 1 kHz qu'à 100 Hz


def test_04_compressor_dsp_processing():
    """Vérifie la réduction de gain et la compression sur signal fort"""
    sr = 44100
    comp = CompressorPlugin(
        threshold_db=-20.0,
        ratio=8.0,
        attack_ms=5.0,
        release_ms=50.0,
        makeup_gain_db=0.0
    )

    # Signal fort (0 dBFS = amplitude 1.0)
    t = np.linspace(0, 0.1, int(sr * 0.1), endpoint=False)
    loud_sig = (0.9 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    loud_stereo = np.column_stack((loud_sig, loud_sig))

    out_loud = comp.process(loud_stereo, sr)
    # Vérifier que la réduction de gain est négative (compression active)
    assert comp.current_gain_reduction_db < -2.0
    # Le niveau de sortie crête doit être inférieur à l'entrée
    assert np.max(np.abs(out_loud)) < np.max(np.abs(loud_stereo))

    # Signal faible (-40 dBFS = amplitude 0.01)
    comp.reset()
    quiet_sig = (0.01 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    quiet_stereo = np.column_stack((quiet_sig, quiet_sig))
    out_quiet = comp.process(quiet_stereo, sr)
    # Aucune réduction de gain sous le seuil
    assert abs(comp.current_gain_reduction_db) < 0.1


def test_05_mixer_plugin_processing():
    """Vérifie le plugin Mixeur (attenuation, mono, VU-mètre)"""
    sr = 44100
    mixer = MixerPlugin(master_volume=0.5)

    t = np.linspace(0, 0.05, int(sr * 0.05), endpoint=False)
    sig = (0.8 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    stereo_in = np.column_stack((sig, sig))

    out = mixer.process(stereo_in, sr)
    assert np.allclose(out, stereo_in * 0.5)
    assert mixer.peak_left_db > -20.0

    # Mode Mono
    mixer.mono_switch = True
    stereo_diff = np.column_stack((sig, np.zeros_like(sig)))
    out_mono = mixer.process(stereo_diff, sr)
    # Les canaux gauche et droit doivent être identiques en mode mono
    assert np.allclose(out_mono[:, 0], out_mono[:, 1])


def test_06_track_plugin_stack_operations():
    """Vérifie l'empilement, l'ordre et la suppression de plugins sur une piste"""
    track = Track(name="Synth Track", track_type="midi")
    assert len(track.plugins) == 0

    eq = EqualizerPlugin(band_count=3)
    comp = CompressorPlugin(threshold_db=-15.0)

    # 1. Empilement
    track.add_plugin(eq)
    track.add_plugin(comp)
    assert len(track.plugins) == 2
    assert track.plugins[0] == eq
    assert track.plugins[1] == comp

    # 2. Réordonner (inversion de la pile : Compresseur avant EQ)
    ok = track.move_plugin(0, 1)
    assert ok is True
    assert track.plugins[0] == comp
    assert track.plugins[1] == eq

    # 3. Suppression
    track.remove_plugin(comp.instance_id)
    assert len(track.plugins) == 1
    assert track.plugins[0] == eq


def test_07_master_track_default_mixer():
    """Vérifie que la piste Master existe par défaut avec le plugin Mixeur"""
    proj = Project.create_default()
    master = proj.ensure_master_track()
    assert master is not None
    assert master.track_type == "master"
    assert len(master.plugins) >= 1
    assert master.plugins[0].plugin_type_id == "novadaw.mixer"
    assert master.plugins[0].project == proj


def test_08_audio_engine_with_plugin_stack():
    """Vérifie que l'AudioEngine traite la pile d'effets de piste et le Master"""
    engine = AudioEngine()
    proj = Project.create_default()
    engine.set_project(proj)

    # Ajouter un compresseur et un égaliseur sur la piste Synth Lead
    synth_track = proj.tracks[0]
    eq = EqualizerPlugin(band_count=3)
    eq.bands[0].gain_db = 6.0
    synth_track.add_plugin(eq)

    comp = CompressorPlugin(threshold_db=-24.0, ratio=4.0)
    synth_track.add_plugin(comp)

    # Rendu d'une tranche
    buf = engine._render_track_slice(synth_track, 0.0, 1.0, 1024, 120.0)
    assert buf is not None
    assert buf.shape == (1024, 2)

    # Le mixeur sur le master doit avoir reçu les métriques de crête
    mixer_p = engine._get_active_mixer_plugin()
    assert mixer_p is not None

    engine.close()


def test_09_project_serialization_with_plugins(tmp_path):
    """Vérifie que l'état des plugins sur les pistes et le master est sauvegardé et restauré fidèlement"""
    proj = Project(name="Project With Plugins Stack")
    track = Track(name="Guitar", track_type="audio")

    # EQ avec réglages personnalisés
    eq = EqualizerPlugin(band_count=10)
    eq.bands[0].gain_db = 8.5
    eq.bands[4].gain_db = -4.0
    track.add_plugin(eq)

    # Compresseur avec réglages personnalisés
    comp = CompressorPlugin(threshold_db=-22.5, ratio=6.0, attack_ms=8.0, release_ms=90.0)
    track.add_plugin(comp)

    proj.add_track(track)
    master = proj.ensure_master_track()

    # Sauvegarder
    save_file = str(tmp_path / "plugins_project.ndaw")
    save_project(proj, save_file)

    # Recharger
    loaded = load_project(save_file)
    assert len(loaded.tracks) == 1
    loaded_track = loaded.tracks[0]

    assert len(loaded_track.plugins) == 2
    loaded_eq = loaded_track.plugins[0]
    loaded_comp = loaded_track.plugins[1]

    assert isinstance(loaded_eq, EqualizerPlugin)
    assert len(loaded_eq.bands) == 10
    assert abs(loaded_eq.bands[0].gain_db - 8.5) < 0.01
    assert abs(loaded_eq.bands[4].gain_db - (-4.0)) < 0.01

    assert isinstance(loaded_comp, CompressorPlugin)
    assert abs(loaded_comp.threshold_db - (-22.5)) < 0.01
    assert abs(loaded_comp.ratio - 6.0) < 0.01

    # Master Track restauré
    assert loaded.master_track is not None
    assert len(loaded.master_track.plugins) >= 1
    assert loaded.master_track.plugins[0].plugin_type_id == "novadaw.mixer"


def test_10_plugin_gui_instantiation():
    """Vérifie que les widgets graphiques des 3 plugins et NativePluginDialog s'instancient sans erreur"""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])

    # 1. Égaliseur Widget
    eq = EqualizerPlugin(band_count=3)
    eq_widget = eq.create_editor()
    assert eq_widget is not None

    # 2. Compresseur Widget
    comp = CompressorPlugin()
    comp_widget = comp.create_editor()
    assert comp_widget is not None

    # 3. Mixeur Widget
    proj = Project.create_default()
    mixer = proj.ensure_master_track().plugins[0]
    mixer_widget = mixer.create_editor()
    assert mixer_widget is not None

    # 4. NativePluginDialog
    dlg = open_native_plugin_editor(eq)
    assert dlg is not None
    dlg.close()


def test_11_mcp_plugin_actions():
    """Vérifie que les actions MCP de plugins fonctionnent (ajout, bypass, suppression)"""
    from core.action_registry import action_registry
    import core.actions

    class MockApp:
        def __init__(self):
            self.project = Project.create_default()
        def refresh_project_ui(self):
            pass

    app = MockApp()

    # Liste des plugins
    res_avail = action_registry.execute("novadaw_get_available_plugins", app, {})
    assert res_avail["count"] >= 3

    # Ajout d'un compresseur sur la 1ère piste
    track_id = app.project.tracks[0].id
    res_add = action_registry.execute("novadaw_add_plugin_to_track", app, {
        "track_id": track_id,
        "plugin_type_id": "novadaw.compressor"
    })
    assert res_add["success"] is True
    p_inst_id = res_add["plugin_instance_id"]

    # Configuration du compresseur
    res_cfg = action_registry.execute("novadaw_configure_plugin", app, {
        "track_id": track_id,
        "plugin_instance_id": p_inst_id,
        "parameters": {"threshold_db": -25.0}
    })
    assert res_cfg["success"] is True
    assert res_cfg["state"]["threshold_db"] == -25.0

    # Bypass
    res_byp = action_registry.execute("novadaw_set_plugin_bypass", app, {
        "track_id": track_id,
        "plugin_instance_id": p_inst_id,
        "enabled": False
    })
    assert res_byp["success"] is True
    assert res_byp["enabled"] is False

    # Suppression
    res_rem = action_registry.execute("novadaw_remove_plugin_from_track", app, {
        "track_id": track_id,
        "plugin_instance_id": p_inst_id
    })
    assert res_rem["success"] is True
