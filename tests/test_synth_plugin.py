"""
tests/test_synth_plugin.py - Suite de tests unitaires et d'intégration pour le synthétiseur NovaSynth.
Vérifie la synthèse DSP, l'empilement de couches, le routage 10 sorties stéréo, les presets et le contrôle MCP.
"""
import sys
import os
import unittest
import numpy as np

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.project import Project, Track, MidiClip, MidiNote
from plugins.registry import plugin_registry, ensure_plugins_loaded
from plugins.synth.synth_plugin import NovaSynthPlugin, SynthLayer
from plugins.synth.synth_dsp import (
    pitch_to_freq,
    generate_oscillator,
    compute_adsr_envelope,
    ChamberlinSVF,
    StereoPingPongDelay,
    StereoStudioReverb
)
from core.action_registry import action_registry
import core.actions


class MockSynthApp:
    """Mock léger de l'application NovaDAW pour tester les actions sans interface graphique active."""
    def __init__(self):
        self.project = Project.create_default()

    def refresh_project_ui(self):
        pass


class TestNovaSynthPlugin(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        ensure_plugins_loaded()

    def test_01_plugin_registration(self):
        """Vérifie que NovaSynth est correctement enregistré dans le registre des plugins par défaut"""
        info = plugin_registry.get_plugin_info("novadaw.synth")
        self.assertIsNotNone(info, "novadaw.synth doit être enregistré.")
        self.assertEqual(info["name"], "NovaSynth")
        self.assertEqual(info["category"], "instrument")
        self.assertTrue(info["is_default"])

        # Instanciation via fabrique
        inst = plugin_registry.create_plugin("novadaw.synth")
        self.assertIsInstance(inst, NovaSynthPlugin)
        self.assertTrue(inst.is_instrument)

    def test_02_sound_layer_stacking(self):
        """Vérifie l'empilement dynamique de couches sonores (Sound Stacking)"""
        synth = NovaSynthPlugin()
        self.assertEqual(len(synth.layers), 2, "Doit comporter 2 couches par défaut.")

        # Ajout d'une 3ème couche (ex: SuperSaw arp)
        layer3 = synth.add_layer(name="Arp SuperSaw", waveform="supersaw", octave=1, volume=0.75, output_bus=2)
        self.assertEqual(len(synth.layers), 3)
        self.assertEqual(layer3.name, "Arp SuperSaw")
        self.assertEqual(layer3.waveform, "supersaw")
        self.assertEqual(layer3.output_bus, 2)

        # Duplication de couche
        dup = synth.duplicate_layer(layer3.layer_id)
        self.assertIsNotNone(dup)
        self.assertEqual(len(synth.layers), 4)
        self.assertIn("Copy", dup.name)

        # Suppression de couche
        removed = synth.remove_layer(dup.layer_id)
        self.assertTrue(removed)
        self.assertEqual(len(synth.layers), 3)

        # Protection : impossible de supprimer toutes les couches
        synth.remove_layer(synth.layers[0].layer_id)
        synth.remove_layer(synth.layers[0].layer_id)
        self.assertEqual(len(synth.layers), 1)
        res = synth.remove_layer(synth.layers[0].layer_id)
        self.assertFalse(res, "Le synthétiseur doit conserver au moins 1 couche active.")
        self.assertEqual(len(synth.layers), 1)

    def test_03_dsp_oscillators(self):
        """Vérifie la génération des 7 formes d'ondes d'oscillateurs en FP32"""
        sr = 44100
        n_samples = 1024
        waves = ["sine", "saw", "square", "triangle", "noise", "fm", "supersaw"]

        for w in waves:
            buf = generate_oscillator(
                wave_type=w,
                freq=440.0,
                num_samples=n_samples,
                sample_rate=sr,
                pulse_width=0.45,
                fm_ratio=2.5,
                fm_depth=1.5,
                unison_detune=0.3
            )
            self.assertEqual(buf.shape, (n_samples, 2), f"Forme d'onde {w} doit être stéréo (N, 2)")
            self.assertEqual(buf.dtype, np.float32)
            self.assertFalse(np.any(np.isnan(buf)), f"Pas de NaN pour {w}")
            self.assertFalse(np.any(np.isinf(buf)), f"Pas d'Inf pour {w}")
            max_amp = np.max(np.abs(buf))
            self.assertGreater(max_amp, 0.05, f"L'oscillateur {w} doit émettre du signal.")
            self.assertLessEqual(max_amp, 2.5, f"L'oscillateur {w} ne doit pas saturer de manière anormale.")

    def test_04_svf_multimode_filter(self):
        """Vérifie la stabilité et les 4 modes du filtre d'état variable (Chamberlin SVF)"""
        sr = 44100
        n = 2048
        white_noise = np.random.uniform(-0.8, 0.8, (n, 2)).astype(np.float32)
        svf = ChamberlinSVF()

        for ftype in ["lowpass", "highpass", "bandpass", "notch"]:
            out = svf.process(
                audio=white_noise,
                sample_rate=sr,
                filter_type=ftype,
                cutoff=1500.0,
                resonance=2.5,
                drive=0.5
            )
            self.assertEqual(out.shape, (n, 2))
            self.assertFalse(np.any(np.isnan(out)))
            self.assertFalse(np.any(np.isinf(out)))
            self.assertGreater(np.max(np.abs(out)), 0.01)

    def test_05_adsr_envelope(self):
        """Vérifie le calcul vectorisé des enveloppes d'amplitude ADSR"""
        env = compute_adsr_envelope(
            t_start=0.0,
            t_end=1.0,
            num_samples=1000,
            note_off_time=0.6,
            attack=0.1,
            decay=0.2,
            sustain=0.5,
            release=0.3
        )
        self.assertEqual(len(env), 1000)
        # Début à 0
        self.assertAlmostEqual(env[0], 0.0, places=2)
        # Pic d'attaque vers 100 échantillons (0.1s)
        self.assertAlmostEqual(env[100], 1.0, places=1)
        # Sustain à 0.5 vers 400 échantillons (0.4s)
        self.assertAlmostEqual(env[400], 0.5, places=1)
        # Note-off à 0.6s (600 échantillons), puis extinction vers 0.9s (900 échantillons)
        self.assertLess(env[700], 0.5)
        self.assertAlmostEqual(env[950], 0.0, places=2)

    def test_06_ten_output_buses_routing(self):
        """Vérifie la matrice des 10 sorties stéréo indépendantes (Bus 0 à Bus 9)"""
        synth = NovaSynthPlugin()
        synth.layers = []

        # Layer 1 assigné à la Sortie 1 (Bus 0)
        l1 = synth.add_layer(name="Kick/Bass Layer", waveform="sine", octave=-1, volume=1.0, output_bus=0)
        # Layer 2 assigné à la Sortie 2 (Bus 1)
        l2 = synth.add_layer(name="Lead Layer", waveform="saw", octave=1, volume=1.0, output_bus=1)

        note = MidiNote(pitch=60, start_beat=0.0, duration=1.0, velocity=100)
        bpm = 120.0
        bps = bpm / 60.0
        frames = 2048
        sr = 44100

        # Rendu spécifique Bus 0 (doit contenir l1 et être exempt de l2)
        out_bus0 = synth.render_slice([note], 0.0, 1.0, bps, frames, sr, bus_index=0)
        # Rendu spécifique Bus 1 (doit contenir l2)
        out_bus1 = synth.render_slice([note], 0.0, 1.0, bps, frames, sr, bus_index=1)
        # Rendu spécifique Bus 5 (inutilisé, doit être silencieux)
        out_bus5 = synth.render_slice([note], 0.0, 1.0, bps, frames, sr, bus_index=5)

        self.assertGreater(np.max(np.abs(out_bus0)), 0.05, "Bus 0 doit avoir du signal sonore.")
        self.assertGreater(np.max(np.abs(out_bus1)), 0.05, "Bus 1 doit avoir du signal sonore.")
        self.assertAlmostEqual(float(np.max(np.abs(out_bus5))), 0.0, places=5, msg="Bus 5 inutilisé doit être 100% silencieux.")

        # Rendu général master (bus_index=None : somme de tous les bus)
        out_master = synth.render_slice([note], 0.0, 1.0, bps, frames, sr, bus_index=None)
        self.assertGreater(np.max(np.abs(out_master)), 0.05)

        # Rendu simultané des 10 bus
        all_buses = synth.render_slice_all_buses([note], 0.0, 1.0, bps, frames, sr)
        self.assertEqual(len(all_buses), 10, "Doit retourner exactement 10 buffers stéréo.")

    def test_07_presets_and_state_roundtrip(self):
        """Vérifie le chargement de tous les presets d'usine et la sérialisation bidirectionnelle de l'état"""
        synth = NovaSynthPlugin()
        presets = synth.get_factory_presets()
        self.assertGreaterEqual(len(presets), 5)

        for pname in presets.keys():
            ok = synth.apply_preset(pname)
            self.assertTrue(ok, f"Le preset {pname} doit s'appliquer sans erreur.")
            self.assertEqual(synth.preset_name, pname)
            self.assertGreater(len(synth.layers), 0)

        # Test sérialisation get_state / set_state
        state = synth.get_state()
        self.assertIn("layers", state)
        self.assertIn("preset_name", state)
        self.assertIn("delay", state)
        self.assertIn("reverb", state)

        new_synth = NovaSynthPlugin()
        new_synth.set_state(state)
        self.assertEqual(new_synth.preset_name, synth.preset_name)
        self.assertEqual(len(new_synth.layers), len(synth.layers))

    def test_08_mcp_actions(self):
        """Vérifie les actions MCP de configuration, ajout/suppression de layers et presets"""
        app = MockSynthApp()
        # Créer une piste dédiée au synthé
        track = Track(name="Synth Track", track_type="midi")
        app.project.add_track(track)

        # 1. novadaw_configure_synth
        res_cfg = action_registry.execute("novadaw_configure_synth", app, {
            "track_id_or_name": track.id,
            "master_volume": 1.25,
            "glide_time": 0.05,
            "delay_enabled": True,
            "delay_mix": 0.35,
            "layer_index": 0,
            "waveform": "supersaw",
            "octave": 1,
            "cutoff": 4200.0,
            "attack": 0.02
        })
        self.assertEqual(res_cfg["status"], "success")
        self.assertEqual(track.plugin_path, "novadaw.synth")

        # 2. novadaw_add_synth_layer (Sound Stacking via MCP)
        res_add = action_registry.execute("novadaw_add_synth_layer", app, {
            "track_id_or_name": track.id,
            "name": "Sub Layer MCP",
            "waveform": "sine",
            "octave": -1,
            "output_bus": 3
        })
        self.assertEqual(res_add["status"], "success")
        self.assertEqual(res_add["output_bus"], 3)
        self.assertGreaterEqual(res_add["total_layers"], 3)

        # 3. novadaw_set_synth_preset
        res_pre = action_registry.execute("novadaw_set_synth_preset", app, {
            "track_id_or_name": track.id,
            "preset_name": "Ethereal Dream Pad"
        })
        self.assertEqual(res_pre["status"], "success")
        self.assertEqual(res_pre["preset_name"], "Ethereal Dream Pad")

        # 4. novadaw_get_synth_state
        res_state = action_registry.execute("novadaw_get_synth_state", app, {
            "track_id_or_name": track.id
        })
        self.assertEqual(res_state["status"], "success")
        self.assertIn("Cyberpunk Acid Lead", res_state["available_presets"])
        self.assertGreater(res_state["total_layers"], 0)

        # 5. novadaw_remove_synth_layer
        first_layer_id = res_state["state"]["layers"][0]["layer_id"]
        res_del = action_registry.execute("novadaw_remove_synth_layer", app, {
            "track_id_or_name": track.id,
            "layer_index_or_id": first_layer_id
        })
        self.assertEqual(res_del["status"], "success")

    def test_09_inspector_and_dialog_integration(self):
        """Vérifie l'intégration GUI de NovaSynth : Inspector combo, bouton d'ouverture et état activé"""
        from PySide6.QtWidgets import QApplication
        from ui.inspector import TrackInspector
        from ui.dialogs import AddTrackDialog
        app = QApplication.instance() or QApplication([])

        proj = Project(name="Synth GUI Test")
        track = Track(name="Synth Track", track_type="midi")
        proj.add_track(track)

        inspector = TrackInspector(proj)
        inspector.set_track(track)

        # Vérifier que NovaSynth est disponible dans le combo de l'inspecteur
        idx = inspector.combo_instrument.findData("novadaw.synth")
        self.assertGreater(idx, 0, "NovaSynth doit être présent dans le sélecteur d'instrument.")

        # Sélectionner NovaSynth dans l'inspecteur
        inspector.combo_instrument.setCurrentIndex(idx)
        self.assertEqual(track.plugin_path, "novadaw.synth")
        self.assertEqual(track.plugin_name, "NovaSynth")
        self.assertTrue(inspector.btn_edit_instrument.isEnabled(), "Le bouton Ouvrir l'interface doit être activé !")

        # Vérifier que le plugin a bien été instancié sur la piste
        has_synth = any(getattr(p, "plugin_type_id", None) == "novadaw.synth" for p in track.plugins)
        self.assertTrue(has_synth, "NovaSynth doit être ajouté à la pile de plugins de la piste.")

        # Vérifier AddTrackDialog
        add_dlg = AddTrackDialog()
        synth_dlg_idx = add_dlg.combo_inst.findData("novadaw.synth")
        self.assertGreater(synth_dlg_idx, 0, "NovaSynth doit être disponible dans la création de piste.")
        add_dlg.combo_inst.setCurrentIndex(synth_dlg_idx)
        data = add_dlg.get_track_data()
        self.assertEqual(data["plugin_path"], "novadaw.synth")

        inspector.close()
        add_dlg.close()

    def test_10_serialization_and_phase_continuity(self):
        """Vérifie la sérialisation avec auto-récupération de plugin_path et la continuité audio"""
        # Test sérialisation sans plugin_path explicite
        track = Track(name="Lead", track_type="midi", plugin_path=None)
        synth = plugin_registry.create_plugin("novadaw.synth")
        track.add_plugin(synth)

        data = track.to_dict()
        self.assertEqual(data["plugin_path"], "novadaw.synth")

        # Restauration
        restored = Track.from_dict(data)
        self.assertEqual(restored.plugin_path, "novadaw.synth")
        self.assertEqual(restored.plugin_name, "NovaSynth")

        # Continuité de phase à travers 2 tranches audio consécutives
        sr = 44100
        frames = 512
        notes = [MidiNote(pitch=60, start_beat=0.0, duration=4.0)]
        bpm = 120.0
        bps = bpm / 60.0

        # Tranche 1 (0.0 -> 0.25 beat)
        b1 = synth.render_slice(notes, 0.0, 0.25, bps, frames, sr)
        # Tranche 2 (0.25 -> 0.50 beat)
        b2 = synth.render_slice(notes, 0.25, 0.50, bps, frames, sr)

        self.assertEqual(len(b1), frames)
        self.assertEqual(len(b2), frames)
        delta_l = abs(b2[0, 0] - b1[-1, 0])
        delta_r = abs(b2[0, 1] - b1[-1, 1])
        self.assertLess(delta_l, 0.5, "La transition entre tranches ne doit pas comporter de saut de phase brusque.")
        self.assertLess(delta_r, 0.5)


if __name__ == "__main__":
    unittest.main()
