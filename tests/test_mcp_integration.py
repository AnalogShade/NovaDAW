"""
tests/test_mcp_integration.py - Suite de tests automatisés pour l'intégration MCP et IPC de NovaDAW
"""
import sys
import os
import time
import threading
import unittest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from PySide6.QtWidgets import QApplication
from core.project import Project, Track, MidiClip, MidiNote
from core.mcp.midi_converters import note_name_to_pitch, pitch_to_note_name
from core.action_registry import action_registry
import core.actions
from core.ipc.ipc_server import NovaIpcServer
from core.ipc.ipc_client import NovaIpcClient
from core.mcp.server import mcp


class MockApp:
    """Mock léger de l'application NovaDAW pour tester les actions sans ouvrir de fenêtre réelle."""
    def __init__(self):
        self.project = Project.create_default()
        self.audio_engine = type("MockEngine", (), {
            "is_playing": False,
            "current_beat": 0.0,
            "play": lambda self: None,
            "pause": lambda self: None,
            "stop": lambda self: None,
            "seek_beat": lambda self, b: None,
            "export_wav": lambda self, p, end_bar: True,
        })()
        self.ruler = type("MockRuler", (), {"set_loop": lambda self, *a: None})()
        self.transport_bar = type("MockTransport", (), {
            "btn_loop": type("MockBtn", (), {"setChecked": lambda self, v: None})(),
            "spin_bpm": type("MockSpin", (), {"setValue": lambda self, v: None})(),
            "set_playing_state": lambda self, s: None,
        })()
        self.timeline_grid = type("MockGrid", (), {
            "update": lambda self, *a: None,
            "update_dimensions": lambda self, *a: None,
        })()
        self.piano_roll = type("MockPianoRoll", (), {
            "current_track": None,
            "current_clip": None,
            "open_clip": lambda self, t, c: None,
            "note_grid": type("MockNoteGrid", (), {
                "update": lambda self, *a: None,
                "update_dimensions": lambda self, *a: None
            })(),
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


class TestMcpIntegration(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # Créer une instance QApplication pour les tests IPC avec Qt
        cls.qt_app = QApplication.instance() or QApplication([])

    def test_01_midi_converters(self):
        """Vérifie la conversion bidirectionnelle noms de notes <-> numéros MIDI."""
        self.assertEqual(note_name_to_pitch("C4"), 60)
        self.assertEqual(note_name_to_pitch("Do4"), 60)
        self.assertEqual(note_name_to_pitch("A4"), 69)
        self.assertEqual(note_name_to_pitch("F#3"), 54)
        self.assertEqual(note_name_to_pitch("Bb2"), 46)
        self.assertEqual(note_name_to_pitch(72), 72)

        self.assertEqual(pitch_to_note_name(60), "C4")
        self.assertEqual(pitch_to_note_name(69), "A4")
        self.assertEqual(pitch_to_note_name(36), "C2")

    def test_02_action_registry_population(self):
        """Vérifie que toutes les actions attendues sont auto-enregistrées."""
        actions = action_registry.get_all()
        expected = [
            "novadaw_set_loop_region",
            "novadaw_adjust_loop_bounds",
            "novadaw_get_transport_state",
            "novadaw_control_transport",
            "novadaw_set_bpm",
            "novadaw_list_midi_clips",
            "novadaw_get_midi_notes",
            "novadaw_add_midi_notes",
            "novadaw_clear_midi_notes",
            "novadaw_remove_midi_notes",
            "novadaw_create_midi_clip",
            "novadaw_transpose_clip",
            "novadaw_quantize_notes",
            "novadaw_get_project_summary",
            "novadaw_create_track",
            "novadaw_set_track_controls",
            "novadaw_save_project",
            "novadaw_export_wav",
        ]
        for name in expected:
            self.assertIn(name, actions, f"L'action {name} devrait être enregistrée dans le registre.")

    def test_03_loop_actions(self):
        """Vérifie le positionnement et l'ajustement de la boucle."""
        app = MockApp()
        
        # 1. Régler la boucle sur 4 temps (mesures 2 à 3 : 4.0 à 8.0)
        res = action_registry.execute("novadaw_set_loop_region", app, {
            "start_beat": 4.0,
            "end_beat": 8.0,
            "enabled": True
        })
        self.assertEqual(res["status"], "success")
        self.assertEqual(app.project.loop_start_beat, 4.0)
        self.assertEqual(app.project.loop_end_beat, 8.0)
        self.assertTrue(app.project.loop_enabled)

        # 2. Décaler la boucle de 4 temps et l'étendre de 4 temps (devient 8.0 à 16.0)
        res2 = action_registry.execute("novadaw_adjust_loop_bounds", app, {
            "shift_beats": 4.0,
            "length_delta_beats": 4.0
        })
        self.assertEqual(app.project.loop_start_beat, 8.0)
        self.assertEqual(app.project.loop_end_beat, 16.0)
        self.assertEqual(res2["length_beats"], 8.0)

    def test_04_midi_notes_actions(self):
        """Vérifie l'écriture, la lecture et la manipulation de notes MIDI."""
        app = MockApp()

        # 1. Lister les clips
        clips = action_registry.execute("novadaw_list_midi_clips", app, {})
        self.assertGreater(len(clips), 0)
        clip_id = clips[0]["clip_id"]

        # 2. Vider le clip pour démarrer propre
        action_registry.execute("novadaw_clear_midi_notes", app, {"clip_id_or_name": clip_id})
        notes_res = action_registry.execute("novadaw_get_midi_notes", app, {"clip_id_or_name": clip_id})
        self.assertEqual(notes_res["total_notes"], 0)

        # 3. Insérer des notes avec notation musicale ("C4", "E4", "G4", "B4")
        new_notes = [
            {"pitch": "C4", "start_beat": 0.0, "duration": 1.0, "velocity": 100},
            {"pitch": "E4", "start_beat": 1.0, "duration": 1.0, "velocity": 95},
            {"pitch": "G4", "start_beat": 2.0, "duration": 1.0, "velocity": 110},
            {"pitch": "B4", "start_beat": 3.0, "duration": 1.0, "velocity": 90},
        ]
        add_res = action_registry.execute("novadaw_add_midi_notes", app, {
            "clip_id_or_name": clip_id,
            "notes": new_notes
        })
        self.assertEqual(add_res["added_count"], 4)
        self.assertEqual(add_res["total_notes"], 4)

        # 4. Transposition de +2 demi-tons (Do devient Ré, etc.)
        trans_res = action_registry.execute("novadaw_transpose_clip", app, {
            "clip_id_or_name": clip_id,
            "semitones": 2
        })
        self.assertEqual(trans_res["semitones"], 2)

        # 5. Vérifier que la première note est maintenant D4 (62)
        read_res = action_registry.execute("novadaw_get_midi_notes", app, {"clip_id_or_name": clip_id})
        self.assertEqual(read_res["notes"][0]["pitch"], 62)
        self.assertEqual(read_res["notes"][0]["note_name"], "D4")

    def test_05_ipc_server_client_roundtrip(self):
        """Vérifie la communication complète IPC (Socket TCP local) entre serveur et client."""
        app = MockApp()
        server = NovaIpcServer(app, host="127.0.0.1", port=0)  # Port dynamique libre
        self.assertTrue(server.start())

        try:
            client_result = None
            client_error = None

            def run_client_call():
                nonlocal client_result, client_error
                try:
                    client = NovaIpcClient()
                    if not client.is_server_running():
                        raise ConnectionError("Serveur non joignable")

                    # Appel distant de réglage de boucle via IPC
                    res1 = client.send_action("novadaw_set_loop_region", {
                        "start_beat": 12.0,
                        "end_beat": 24.0,
                        "enabled": True
                    })
                    # Appel distant de récupération de résumé
                    res2 = client.send_action("novadaw_get_project_summary")
                    client_result = (res1, res2)
                except Exception as e:
                    client_error = e

            t = threading.Thread(target=run_client_call)
            t.start()

            start_t = time.time()
            while t.is_alive() and (time.time() - start_t < 4.0):
                self.qt_app.processEvents()
                time.sleep(0.01)

            t.join()

            if client_error:
                raise client_error

            self.assertIsNotNone(client_result)
            res1, res2 = client_result
            self.assertEqual(res1["status"], "success")
            self.assertEqual(app.project.loop_start_beat, 12.0)
            self.assertEqual(app.project.loop_end_beat, 24.0)

            self.assertEqual(res2["bpm"], 120.0)
            self.assertEqual(res2["loop_start_beat"], 12.0)

        finally:
            server.stop()

    def test_06_fastmcp_server_tools_registered(self):
        """Vérifie que le serveur FastMCP a correctement découvert et exposé tous les outils."""
        registered_tools = [t.name for t in mcp._tool_manager.list_tools()]
        self.assertIn("novadaw_set_loop_region", registered_tools)
        self.assertIn("novadaw_add_midi_notes", registered_tools)
        self.assertIn("novadaw_get_midi_notes", registered_tools)
        self.assertIn("novadaw_control_transport", registered_tools)
        self.assertIn("novadaw_create_track", registered_tools)


if __name__ == "__main__":
    unittest.main()
