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

import numpy as np
from PySide6.QtWidgets import QApplication
from core.project import Project, Track, MidiClip, MidiNote, AudioClip
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
        self.assertIn("novadaw_configure_vocal_glide", registered_tools)
        self.assertIn("novadaw_crystallize_vocal_glide", registered_tools)
        self.assertIn("novadaw_configure_guitar", registered_tools)
        self.assertIn("novadaw_configure_guitar_amp", registered_tools)

    def test_07_vocal_glide_mcp_tools(self):
        """Vérifie la configuration et la cristallisation de Nova Vocal Glide via MCP."""
        app = MockApp()
        avail = action_registry.execute("novadaw_get_available_plugins", app, {})
        type_ids = {p["type_id"] for p in avail["plugins"]}
        self.assertIn("novadaw.vocal_glide", type_ids)

        # Créer une piste audio avec un signal faible puis fort
        created = action_registry.execute("novadaw_create_track", app, {
            "name": "Chant MCP",
            "track_type": "audio"
        })
        track_id = created["track_id"]
        track = app.project.get_track(track_id)

        sr = 44100
        t1 = np.linspace(0.0, 0.8, int(sr * 0.8), endpoint=False, dtype=np.float32)
        q = (0.05 * np.sin(2.0 * np.pi * 260.0 * t1)).astype(np.float32)
        l = (0.65 * np.sin(2.0 * np.pi * 260.0 * t1)).astype(np.float32)
        stereo = np.column_stack((np.concatenate((q, l)), np.concatenate((q, l))))
        track.clips.append(AudioClip(name="Take 1", start_beat=0.0, length_beats=3.2, audio_data=stereo, sample_rate=sr))

        # Configurer Vocal Glide (auto-ajout + preset + surcharge paramètre)
        cfg = action_registry.execute("novadaw_configure_vocal_glide", app, {
            "track_id_or_name": track_id,
            "preset": "Rap / Spoken Word Punchy",
            "target_db": -16.5,
            "aggressiveness": 90.0,
        })
        self.assertEqual(cfg["status"], "success")
        self.assertEqual(cfg["preset_name"], "Rap / Spoken Word Punchy")
        self.assertAlmostEqual(cfg["state"]["target_db"], -16.5)
        self.assertAlmostEqual(cfg["state"]["aggressiveness"], 90.0)
        self.assertAlmostEqual(cfg["state"]["speed"], 78.0)

        # Cristalliser en automatisation Volume
        cryst = action_registry.execute("novadaw_crystallize_vocal_glide", app, {
            "track_id_or_name": track_id,
            "detail_mode": "musical",
            "auto_bypass": True,
        })
        self.assertEqual(cryst["status"], "success")
        self.assertGreaterEqual(cryst["point_count"], 4)
        self.assertFalse(cryst["plugin_enabled"])
        lane = track.get_automation_lane("volume")
        self.assertIsNotNone(lane)
        self.assertGreaterEqual(len(lane.points), 4)

    def test_08_guitar_and_amp_mcp_tools(self):
        """Vérifie Nova Guitar et Nova Guitar Amp via leurs outils MCP dédiés."""
        app = MockApp()
        avail = action_registry.execute("novadaw_get_available_plugins", app, {})
        type_ids = {p["type_id"] for p in avail["plugins"]}
        self.assertIn("novadaw.guitar", type_ids)
        self.assertIn("novadaw.guitar_amp", type_ids)

        created = action_registry.execute("novadaw_create_track", app, {
            "name": "Guitare Lead",
            "track_type": "midi"
        })
        track_id = created["track_id"]
        track = app.project.get_track(track_id)

        # Ajouter d'abord l'ampli, puis l'instrument guitare : l'instrument doit être placé en position 0
        amp_res = action_registry.execute("novadaw_configure_guitar_amp", app, {
            "track_id_or_name": track_id,
            "preset": "Classic Rock Overdrive",
            "treble_db": 4.0,
        })
        self.assertEqual(amp_res["status"], "success")
        self.assertEqual(amp_res["preset_name"], "Classic Rock Overdrive")
        self.assertAlmostEqual(amp_res["state"]["distortion"], 0.65)
        self.assertAlmostEqual(amp_res["state"]["treble_db"], 4.0)

        gtr_res = action_registry.execute("novadaw_configure_guitar", app, {
            "track_id_or_name": track_id,
            "preset": "Électrique Tele Twang (Chevalet)",
            "palm_mute": 0.45,
        })
        self.assertEqual(gtr_res["status"], "success")
        self.assertEqual(gtr_res["preset_name"], "Électrique Tele Twang (Chevalet)")
        self.assertEqual(gtr_res["state"]["guitar_mode"], "electric_clean")
        self.assertAlmostEqual(gtr_res["state"]["palm_mute"], 0.45)
        self.assertEqual(track.plugin_path, "novadaw.guitar")
        self.assertEqual(getattr(track.plugins[0], "plugin_type_id", None), "novadaw.guitar")
        self.assertEqual(getattr(track.plugins[1], "plugin_type_id", None), "novadaw.guitar_amp")

        # Mode invalide -> erreur structurée
        bad_mode = action_registry.execute("novadaw_configure_guitar", app, {
            "track_id_or_name": track_id,
            "guitar_mode": "invalid_mode",
        })
        self.assertEqual(bad_mode["status"], "error")

    def test_09_generic_configure_plugin_presets_and_order(self):
        """Vérifie que les outils génériques (add_plugin_to_track / configure_plugin) gèrent presets et ordre."""
        app = MockApp()
        created = action_registry.execute("novadaw_create_track", app, {
            "name": "Guitare Générique",
            "track_type": "midi"
        })
        track_id = created["track_id"]

        amp_add = action_registry.execute("novadaw_add_plugin_to_track", app, {
            "track_id": track_id,
            "plugin_type_id": "novadaw.guitar_amp",
        })
        self.assertTrue(amp_add["success"])

        gtr_add = action_registry.execute("novadaw_add_plugin_to_track", app, {
            "track_id": track_id,
            "plugin_type_id": "novadaw.guitar",
        })
        self.assertTrue(gtr_add["success"])
        self.assertEqual(gtr_add["stack_position"], 0)

        # Application d'un preset via l'outil générique novadaw_configure_plugin
        amp_cfg = action_registry.execute("novadaw_configure_plugin", app, {
            "track_id": track_id,
            "plugin_instance_id": amp_add["plugin_instance_id"],
            "parameters": {"preset": "High Gain Metal", "volume": 0.85},
        })
        self.assertTrue(amp_cfg["success"])
        self.assertAlmostEqual(amp_cfg["state"]["distortion"], 0.92)
        self.assertAlmostEqual(amp_cfg["state"]["volume"], 0.85)

        gtr_cfg = action_registry.execute("novadaw_configure_plugin", app, {
            "track_id": track_id,
            "plugin_instance_id": gtr_add["plugin_instance_id"],
            "parameters": {"preset": "Acoustique Nylon Classique", "reverb_mix": 0.25},
        })
        self.assertTrue(gtr_cfg["success"])
        self.assertEqual(gtr_cfg["state"]["guitar_mode"], "acoustic_nylon")
        self.assertAlmostEqual(gtr_cfg["state"]["reverb_mix"], 0.25)

        # Ajout et preset Vocal Glide via les outils génériques
        vg_add = action_registry.execute("novadaw_add_plugin_to_track", app, {
            "track_id": track_id,
            "plugin_type_id": "novadaw.vocal_glide",
        })
        self.assertTrue(vg_add["success"])
        vg_cfg = action_registry.execute("novadaw_configure_plugin", app, {
            "track_id": track_id,
            "plugin_instance_id": vg_add["plugin_instance_id"],
            "parameters": {"preset": "Voix Douce / Ballade", "enabled": False},
        })
        self.assertTrue(vg_cfg["success"])
        self.assertAlmostEqual(vg_cfg["state"]["target_db"], -20.0)
        self.assertAlmostEqual(vg_cfg["state"]["max_boost_db"], 12.0)

    def test_10_duration_beats_and_param_aliases(self):
        """Vérifie la compatibilité de duration_beats et des alias track_id/clip_id."""
        app = MockApp()
        created = action_registry.execute("novadaw_create_track", app, {
            "name": "Alias Track",
            "track_type": "midi",
        })
        tid = created["track_id"]

        clip_res = action_registry.execute("novadaw_create_midi_clip", app, {
            "track_id": tid,
            "name": "Alias Clip",
            "start_beat": 0.0,
            "length_beats": 4.0,
        })
        cid = clip_res["clip_id"]

        add_res = action_registry.execute("novadaw_add_midi_notes", app, {
            "clip_id": cid,
            "notes": [
                {"pitch": "C4", "start_beat": 0.0, "duration_beats": 0.25, "velocity": 110},
                {"pitch": "G4", "start_beat": 0.5, "duration": 0.75, "velocity": 95},
            ],
        })
        self.assertEqual(add_res["added_count"], 2)
        notes_out = action_registry.execute("novadaw_get_midi_notes", app, {"clip_id": cid})["notes"]
        self.assertAlmostEqual(notes_out[0]["duration"], 0.25)
        self.assertAlmostEqual(notes_out[1]["duration"], 0.75)

    def test_11_all_nova_plugins_presets_and_extended_mcp_params(self):
        """Vérifie les paramètres étendus et presets unifiés sur Drums, EQ, Comp, Piano, Synth et Vocal Glide."""
        app = MockApp()
        midi_trk = action_registry.execute("novadaw_create_track", app, {"name": "Full MIDI", "track_type": "midi"})["track_id"]
        audio_trk = action_registry.execute("novadaw_create_track", app, {"name": "Full Audio", "track_type": "audio"})["track_id"]

        # 1. Nova Drums via configure_drum_machine + configure_plugin(preset=...)
        drm = action_registry.execute("novadaw_configure_drum_machine", app, {
            "track_id_or_name": midi_trk,
            "preset": "Punchy Rock",
            "reverb_width": 0.75,
            "pad_id": "snare",
            "pad_decay": 1.4,
            "pad_muted": True,
        })
        self.assertEqual(drm["status"], "success")
        self.assertEqual(drm["preset_name"], "Punchy Rock")
        self.assertAlmostEqual(drm["state"]["reverb"]["width"], 0.75)
        snare_state = [p for p in drm["state"]["pads"] if p["pad_id"] == "snare"][0]
        self.assertAlmostEqual(snare_state["decay"], 1.4)
        self.assertTrue(snare_state["muted"])

        drm_gen = action_registry.execute("novadaw_configure_plugin", app, {
            "track_id": midi_trk,
            "plugin_instance_id": drm["plugin_instance_id"],
            "parameters": {"preset": "Tight & Dry"},
        })
        self.assertTrue(drm_gen["success"])
        self.assertEqual(drm_gen["state"]["preset_name"], "Tight & Dry")

        # 2. NovaEQ (band_count + preset + bass_cut)
        eq_res = action_registry.execute("novadaw_configure_equalizer", app, {
            "track_id_or_name": audio_trk,
            "band_count": 24,
            "preset": "Vocal Clarity",
            "band_index": 0,
            "band_enabled": False,
        })
        self.assertEqual(eq_res["status"], "success")
        self.assertEqual(eq_res["bands_count"], 24)
        self.assertFalse(eq_res["bands"][0]["enabled"])

        eq_gen = action_registry.execute("novadaw_configure_plugin", app, {
            "track_id": audio_trk,
            "plugin_instance_id": eq_res["plugin_instance_id"],
            "parameters": {"band_count": 3},
        })
        self.assertTrue(eq_gen["success"])
        self.assertEqual(eq_gen["state"]["band_count"], 3)

        # 3. NovaCompressor (FACTORY_PRESETS français + RMS + SC HPF + Auto-Makeup)
        comp_res = action_registry.execute("novadaw_configure_compressor", app, {
            "track_id_or_name": audio_trk,
            "preset": "Voix Douce (Opto)",
            "detection_mode": "rms",
            "sidechain_hpf_hz": 90.0,
            "auto_makeup": True,
        })
        self.assertEqual(comp_res["status"], "success")
        self.assertEqual(comp_res["state"]["detection_mode"], "rms")
        self.assertAlmostEqual(comp_res["state"]["sidechain_hpf_hz"], 90.0)
        self.assertTrue(comp_res["state"]["auto_makeup"])

        # 4. Nova Piano avec preset direct
        pno_trk = action_registry.execute("novadaw_create_track", app, {"name": "Piano MCP", "track_type": "midi"})["track_id"]
        pno_res = action_registry.execute("novadaw_configure_piano", app, {
            "track_id_or_name": pno_trk,
            "preset": "Piano à queue",
            "reverb_room": "cathedral",
            "hammer_hardness": 0.7,
        })
        self.assertEqual(pno_res["status"], "success")
        self.assertEqual(pno_res["state"]["reverb_room"], "cathedral")

        # 5. NovaSynth paramètres avancés (FM, Unison, Filter ADSR, duplication de couche, 34 presets listés)
        syn_trk = action_registry.execute("novadaw_create_track", app, {"name": "Synth MCP", "track_type": "midi"})["track_id"]
        syn_state = action_registry.execute("novadaw_get_synth_state", app, {"track_id_or_name": syn_trk})
        self.assertGreaterEqual(len(syn_state["available_presets"]), 34)

        syn_cfg = action_registry.execute("novadaw_configure_synth", app, {
            "track_id_or_name": syn_trk,
            "waveform": "fm",
            "fm_ratio": 3.5,
            "fm_depth": 2.4,
            "filter_attack": 0.12,
            "lfo_enabled": True,
            "lfo_waveform": "triangle",
            "delay_ping_pong": False,
        })
        self.assertEqual(syn_cfg["status"], "success")
        l0 = syn_cfg["state"]["layers"][0]
        self.assertAlmostEqual(l0["fm_ratio"], 3.5)
        self.assertAlmostEqual(l0["filter_attack"], 0.12)
        self.assertEqual(l0["lfo_waveform"], "triangle")
        self.assertFalse(syn_cfg["state"]["delay"]["ping_pong"])

        dup_layer = action_registry.execute("novadaw_duplicate_synth_layer", app, {
            "track_id_or_name": syn_trk,
            "layer_index_or_id": 0,
        })
        self.assertEqual(dup_layer["status"], "success")
        self.assertEqual(dup_layer["total_layers"], len(syn_cfg["state"]["layers"]) + 1)

        # 6. Vocal Glide write_automation_live crée et active la lane Volume en mode 'write'
        vg_live = action_registry.execute("novadaw_configure_vocal_glide", app, {
            "track_id_or_name": audio_trk,
            "write_automation_live": True,
        })
        self.assertEqual(vg_live["status"], "success")
        lane = app.project.get_track(audio_trk).get_automation_lane("volume")
        self.assertIsNotNone(lane)
        self.assertEqual(lane.mode, "write")

    def test_12_daw_tracks_clips_automation_presets_and_project_io_mcp(self):
        """Vérifie les nouveaux outils MCP couvrant pistes, clips, automatisation, presets et ouverture .ndaw."""
        import tempfile
        app = MockApp()

        # Outil 'draw' accepté
        tool_res = action_registry.execute("novadaw_set_editing_tool", app, {"tool_name": "draw"})
        self.assertEqual(tool_res["active_tool"], "draw")

        # Création + contrôles étendus + routage multi-out + duplication + réordonnancement + suppression de piste
        t1 = action_registry.execute("novadaw_create_track", app, {"name": "Synth Host", "track_type": "midi"})["track_id"]
        t2 = action_registry.execute("novadaw_create_track", app, {"name": "Synth Aux", "track_type": "midi"})["track_id"]
        ctrl = action_registry.execute("novadaw_set_track_controls", app, {
            "track_id_or_name": t2,
            "armed": True,
            "synth_output_bus": 2,
            "synth_route_track_id": t1,
            "name": "Synth Out 3",
        })
        self.assertEqual(ctrl["track_name"], "Synth Out 3")
        self.assertEqual(ctrl["synth_output_bus"], 2)
        self.assertEqual(ctrl["synth_route_track_id"], t1)
        self.assertTrue(ctrl["armed"])

        dup_t = action_registry.execute("novadaw_duplicate_track", app, {"track_id_or_name": t1, "new_name": "Synth Copy"})
        self.assertEqual(dup_t["new_track_name"], "Synth Copy")
        reord = action_registry.execute("novadaw_reorder_track", app, {"track_id_or_name": dup_t["new_track_id"], "new_index": 0})
        self.assertEqual(reord["new_index"], 0)

        # Clips CRUD (list, update, duplicate, delete)
        c_new = action_registry.execute("novadaw_create_midi_clip", app, {
            "track_id_or_name": t1,
            "name": "Hook",
            "start_beat": 4.0,
            "length_beats": 4.0,
        })["clip_id"]
        upd_c = action_registry.execute("novadaw_update_clip", app, {
            "clip_id_or_name": c_new,
            "name": "Hook Edited",
            "start_beat": 8.0,
            "length_beats": 8.0,
        })
        self.assertEqual(upd_c["clip_name"], "Hook Edited")
        self.assertAlmostEqual(upd_c["start_beat"], 8.0)
        dup_c = action_registry.execute("novadaw_duplicate_clip", app, {"clip_id_or_name": c_new})
        self.assertAlmostEqual(dup_c["start_beat"], 16.0)
        listed = action_registry.execute("novadaw_list_clips", app, {"track_id_or_name": t1})
        self.assertGreaterEqual(listed["count"], 2)
        action_registry.execute("novadaw_delete_clip", app, {"clip_id_or_name": dup_c["new_clip_id"]})

        # Automatisation CRUD
        auto_set = action_registry.execute("novadaw_set_automation_lane", app, {
            "track_id_or_name": t1,
            "param_name": "pan",
            "clear_existing": True,
            "points": [
                {"beat": 0.0, "value": -0.5, "curve_type": "curve", "curve_tension": 0.3},
                {"beat": 4.0, "value": 0.5, "curve_type": "linear"},
            ],
        })
        self.assertEqual(auto_set["points_count"], 2)
        auto_get = action_registry.execute("novadaw_get_track_automation", app, {"track_id_or_name": t1, "param_name": "pan"})
        self.assertEqual(len(auto_get["lanes"]), 1)
        auto_clr = action_registry.execute("novadaw_clear_automation_lane", app, {
            "track_id_or_name": t1,
            "param_name": "pan",
            "remove_lane": True,
        })
        self.assertTrue(auto_clr["removed_lane"])

        # Pile de plugins : reorder + preset save/list/delete isolés
        eq_a = action_registry.execute("novadaw_add_plugin_to_track", app, {"track_id": t1, "plugin_type_id": "novadaw.equalizer"})
        cp_b = action_registry.execute("novadaw_add_plugin_to_track", app, {"track_id": t1, "plugin_type_id": "novadaw.compressor"})
        mv_res = action_registry.execute("novadaw_reorder_track_plugin", app, {
            "track_id_or_name": t1,
            "plugin_instance_id": cp_b["plugin_instance_id"],
            "new_index": 0,
        })
        self.assertEqual(mv_res["new_index"], 0)

        temp_preset_name = "__mcp_unit_test_temp_preset__"
        try:
            sv_p = action_registry.execute("novadaw_save_plugin_preset", app, {
                "track_id_or_name": t1,
                "plugin_instance_id": cp_b["plugin_instance_id"],
                "preset_name": temp_preset_name,
            })
            self.assertEqual(sv_p["status"], "success")
            ls_p = action_registry.execute("novadaw_list_plugin_presets", app, {"plugin_type_id": "novadaw.compressor"})
            self.assertTrue(any(p["name"] == temp_preset_name for p in ls_p["presets"]))
        finally:
            action_registry.execute("novadaw_delete_plugin_preset", app, {
                "plugin_type_id": "novadaw.compressor",
                "preset_name": temp_preset_name,
            })

        # Sauvegarde + réouverture .ndaw via MCP dans un dossier temporaire
        action_registry.execute("novadaw_set_project_info", app, {"name": "Projet MCP Test", "time_sig_num": 3, "time_sig_den": 4})
        with tempfile.TemporaryDirectory() as tmpdir:
            ndaw_path = os.path.join(tmpdir, "mcp_roundtrip.ndaw")
            action_registry.execute("novadaw_save_project", app, {"file_path": ndaw_path})
            action_registry.execute("novadaw_new_project", app, {})
            self.assertEqual(len(app.project.tracks), 0)
            opened = action_registry.execute("novadaw_open_project", app, {"file_path": ndaw_path})
            self.assertEqual(opened["project_name"], "Projet MCP Test")
            self.assertEqual(app.project.time_sig_num, 3)

        del_res = action_registry.execute("novadaw_delete_track", app, {"track_id_or_name": dup_t["new_track_id"]})
        self.assertEqual(del_res["status"], "success")


if __name__ == "__main__":
    unittest.main()
