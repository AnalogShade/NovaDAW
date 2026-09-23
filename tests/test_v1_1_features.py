"""
tests/test_v1_1_features.py - Suite de tests automatisés pour les fonctionnalités v1.1 :
- Importateur audio universel (tous formats)
- Périphériques Audio & Cartes Graphiques (GPU, pilotes, latence)
- Pilotabilité MCP étendue pour chaque plugin (Égaliseur, Compresseur, Mixeur, Import, Hardware)
- Pont IPC complet pour orchestration IA
"""
import sys
import os
import unittest
import numpy as np
import soundfile as sf

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from PySide6.QtWidgets import QApplication
from core.project import Project, Track
from core.action_registry import action_registry
import core.actions
from core.hardware_manager import hardware_manager
from core.audio_importer import load_audio_file, SUPPORTED_EXTENSIONS
from core.audio_engine import AudioEngine
from core.ipc.ipc_server import NovaIpcServer
from core.ipc.ipc_client import NovaIpcClient


class MockAppFull:
    """Mock complet de l'application NovaDAW pour tester l'orchestration des plugins et de l'audio"""
    def __init__(self):
        self.project = Project.create_default()
        self.audio_engine = AudioEngine()
        self.audio_engine.set_project(self.project)
        self.selected_track_id = self.project.tracks[0].id if self.project.tracks else None
        self.ruler = type("MockRuler", (), {"set_loop": lambda self, *a: None})()
        self.transport_bar = type("MockTransport", (), {
            "btn_loop": type("MockBtn", (), {"setChecked": lambda self, v: None})(),
            "spin_bpm": type("MockSpin", (), {"setValue": lambda self, v: None})(),
            "slider_vol": type("MockSlider", (), {"setValue": lambda self, v: None})(),
            "set_playing_state": lambda self, s: None,
        })()
        self.timeline_grid = type("MockGrid", (), {
            "update": lambda self, *a: None,
            "update_dimensions": lambda self, *a: None,
        })()
        self.audio_editor = type("MockAudioEditor", (), {
            "open_clip": lambda self, t, c: None,
        })()
        self.lower_zone = type("MockLowerZone", (), {
            "setCurrentWidget": lambda self, w: None,
        })()
        self.mixer_widget = type("MockMixerWidget", (), {
            "refresh_tracks": lambda self: None,
        })()

    def _on_stop(self):
        pass

    def _on_seek(self, b):
        self.audio_engine.current_beat = b

    def _on_goto_start(self):
        self._on_seek(0.0)

    def _on_goto_end(self):
        self._on_seek(16.0)

    def refresh_project_ui(self):
        pass


class TestV11Features(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])
        cls.test_dir = os.path.join(PROJECT_ROOT, "scratch", "test_v11")
        os.makedirs(cls.test_dir, exist_ok=True)

        # Générer un fichier audio de test WAV
        cls.test_wav = os.path.join(cls.test_dir, "sample_test.wav")
        sr = 44100
        t = np.linspace(0, 1.5, int(sr * 1.5), endpoint=False)
        sig = 0.4 * np.sin(2.0 * np.pi * 523.25 * t)  # C5
        sf.write(cls.test_wav, np.column_stack((sig, sig)), sr)

    def test_01_audio_importer(self):
        """Vérifie le chargement et la normalisation universelle d'un fichier audio."""
        audio, sr, dur = load_audio_file(self.test_wav, target_sr=44100)
        self.assertEqual(sr, 44100)
        self.assertAlmostEqual(dur, 1.5, places=2)
        self.assertEqual(audio.ndim, 2)
        self.assertEqual(audio.shape[1], 2)
        self.assertGreater(len(audio), 0)

    def test_02_hardware_manager_gpu(self):
        """Vérifie la détection du GPU et des fonctionnalités d'affichage."""
        gpus = hardware_manager.get_gpu_devices()
        self.assertIsInstance(gpus, list)
        self.assertGreater(len(gpus), 0)
        gpu0 = gpus[0]
        self.assertIn("name", gpu0)
        self.assertIn("status", gpu0)
        self.assertIn("driver_version", gpu0)

    def test_03_hardware_manager_audio(self):
        """Vérifie la détection des pilotes audio et le calcul de latence."""
        apis = hardware_manager.get_audio_host_apis()
        self.assertIsInstance(apis, list)
        self.assertGreater(len(apis), 0)

        devices = hardware_manager.get_audio_devices()
        self.assertIn("outputs", devices)
        self.assertIn("inputs", devices)

        # Calcul de latence : 512 samples @ 44100 Hz ≈ 11.61 ms
        lat = hardware_manager.calculate_latency_ms(512, 44100)
        self.assertAlmostEqual(lat, 11.61, places=1)

    def test_04_mcp_import_audio_action(self):
        """Vérifie l'action MCP novadaw_import_audio_file."""
        app = MockAppFull()
        res = action_registry.execute("novadaw_import_audio_file", app, {
            "file_path": self.test_wav,
            "start_beat": 4.0
        })
        self.assertEqual(res["status"], "success")
        self.assertIn("track_name", res)
        self.assertIn("clip_id", res)
        self.assertAlmostEqual(res["duration_seconds"], 1.5, places=1)
        app.audio_engine.close()

    def test_05_mcp_configure_equalizer_presets(self):
        """Vérifie le pilotage de l'égaliseur par un agent IA avec presets et réglages."""
        app = MockAppFull()

        # 1. Preset bass_boost
        res_eq = action_registry.execute("novadaw_configure_equalizer", app, {
            "track_id_or_name": "master",
            "preset": "bass_boost"
        })
        self.assertEqual(res_eq["status"], "success")
        self.assertEqual(res_eq["preset_applied"], "bass_boost")
        self.assertGreater(res_eq["bands"][0]["gain_db"], 0.0)

        # 2. Preset vocal_clarity
        res_voc = action_registry.execute("novadaw_configure_equalizer", app, {
            "track_id_or_name": "master",
            "preset": "vocal_clarity"
        })
        self.assertEqual(res_voc["preset_applied"], "vocal_clarity")

        # 3. Réglage précis de bande individuelle (Bande 0 -> 80Hz, +3dB)
        res_band = action_registry.execute("novadaw_configure_equalizer", app, {
            "track_id_or_name": "master",
            "band_index": 0,
            "frequency": 80.0,
            "gain_db": 3.0,
            "q": 1.2
        })
        self.assertEqual(res_band["bands"][0]["frequency"], 80.0)
        self.assertEqual(res_band["bands"][0]["gain_db"], 3.0)
        app.audio_engine.close()

    def test_06_mcp_configure_compressor_presets(self):
        """Vérifie le pilotage du compresseur dynamique par un agent IA."""
        app = MockAppFull()

        # 1. Preset punchy_drums
        res_comp = action_registry.execute("novadaw_configure_compressor", app, {
            "track_id_or_name": "master",
            "preset": "punchy_drums"
        })
        self.assertEqual(res_comp["status"], "success")
        self.assertEqual(res_comp["state"]["threshold_db"], -16.0)
        self.assertEqual(res_comp["state"]["ratio"], 4.0)

        # 2. Réglage manuel précis
        res_custom = action_registry.execute("novadaw_configure_compressor", app, {
            "track_id_or_name": "master",
            "threshold_db": -22.5,
            "ratio": 6.0,
            "makeup_gain_db": 3.5
        })
        self.assertEqual(res_custom["state"]["threshold_db"], -22.5)
        self.assertEqual(res_custom["state"]["ratio"], 6.0)
        self.assertEqual(res_custom["state"]["makeup_gain_db"], 3.5)
        app.audio_engine.close()

    def test_07_mcp_configure_mixer_and_levels(self):
        """Vérifie le pilotage du mixeur et la lecture des VU-mètres par l'IA."""
        app = MockAppFull()

        res_mix = action_registry.execute("novadaw_configure_mixer", app, {
            "master_volume": 0.85,
            "master_pan": -0.2,
            "mono": True,
            "dim": False
        })
        self.assertEqual(res_mix["status"], "success")
        self.assertAlmostEqual(res_mix["master_volume"], 0.85, places=2)
        self.assertAlmostEqual(res_mix["master_pan"], -0.2, places=2)
        self.assertTrue(res_mix["mono"])

        # Mesure des niveaux
        res_levels = action_registry.execute("novadaw_get_mixer_levels", app, {})
        self.assertIn("master", res_levels)
        self.assertIn("peak_left_db", res_levels["master"])
        self.assertIn("tracks", res_levels)
        app.audio_engine.close()

    def test_08_mcp_hardware_actions(self):
        """Vérifie les actions novadaw_get_hardware_devices et novadaw_set_audio_device."""
        app = MockAppFull()

        res_hw = action_registry.execute("novadaw_get_hardware_devices", app, {})
        self.assertIn("gpus", res_hw)
        self.assertIn("audio_host_apis", res_hw)
        self.assertIn("current_audio_settings", res_hw)

        res_set = action_registry.execute("novadaw_set_audio_device", app, {
            "sample_rate": 48000,
            "buffer_size": 256
        })
        self.assertEqual(res_set["status"], "success")
        self.assertEqual(res_set["sample_rate"], 48000)
        self.assertEqual(res_set["buffer_size"], 256)
        app.audio_engine.close()

    def test_09_mcp_get_track_plugins(self):
        """Vérifie l'inspection de la pile de plugins d'une piste."""
        app = MockAppFull()
        res = action_registry.execute("novadaw_get_track_plugins", app, {
            "track_id_or_name": "master"
        })
        self.assertEqual(res["track_name"], "Master")
        self.assertGreater(res["plugins_count"], 0)
        app.audio_engine.close()


if __name__ == "__main__":
    unittest.main()
