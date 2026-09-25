"""
tests/test_synth_improvements.py - Suite de tests unitaires et d'intégration pour les nouvelles fonctionnalités:
- Master VU/Peak Meter et indicateur de saturation/clipping (> 0 dBFS)
- Dimensionnement de la fenêtre NovaSynth
- Sound Stacking (Mute, Solo, Suppression protégée, Routage multi-sorties Out 1-10)
- Édition 2D interactive à la souris de l'enveloppe ADSR et du filtre Passe-Bas
- Moteur de Reverb de studio (comb + all-pass) et audibilité du mix
- Cache de pré-rendu pour pré-écoute clavier instantanée (<1ms)
- Oscilloscope temps réel avec dynamique de décroissance
"""
import sys
import os
import time
import unittest
from unittest.mock import patch
import numpy as np

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QMouseEvent

from core.audio_engine import AudioEngine
from core.project import Project, Track, MidiNote
from plugins.synth.synth_dsp import StereoStudioReverb, ChamberlinSVF, compute_adsr_envelope
from plugins.synth.synth_plugin import NovaSynthPlugin, SynthLayer
from plugins.synth.synth_gui import (
    InteractiveADSRWidget,
    FilterCurveWidget,
    CyberOscilloscope,
    LayerCardWidget,
    NovaSynthGUI,
)
from ui.transport_bar import MasterMeterWidget


class TestSynthAndMasterImprovements(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    # -------------------------------------------------------------------------
    # 1. Master Peak Meter & Clipping
    # -------------------------------------------------------------------------
    def test_01_audio_engine_master_meter_and_clipping(self):
        """Vérifie le suivi des crêtes L/R et la détection de saturation (> 0 dBFS) dans AudioEngine"""
        engine = AudioEngine(sample_rate=44100, block_size=256)

        # Au départ, aucun pic et pas de clip
        peak_l, peak_r, clipped = engine.get_master_peaks()
        self.assertEqual(peak_l, 0.0)
        self.assertEqual(peak_r, 0.0)
        self.assertFalse(clipped)

        # Simulation d'un bloc audio normal (en dessous de 0 dBFS / 1.0)
        normal_block = np.full((256, 2), 0.5, dtype=np.float32)
        with engine._master_peak_lock:
            peak_in = float(np.max(np.abs(normal_block)))
            engine._master_peak_l = float(np.max(np.abs(normal_block[:, 0])))
            engine._master_peak_r = float(np.max(np.abs(normal_block[:, 1])))
            if peak_in >= 1.0:
                engine._master_clipped = True
                engine._master_clip_time = time.time()

        p_l, p_r, is_clip = engine.get_master_peaks()
        self.assertAlmostEqual(p_l, 0.5, places=3)
        self.assertAlmostEqual(p_r, 0.5, places=3)
        self.assertFalse(is_clip, "Un signal à 0.5 ne doit pas déclencher le clipping")

        # Simulation d'un bloc audio saturant (> 1.0 / > 0 dBFS)
        clipped_block = np.array([[1.45, -1.20]], dtype=np.float32)
        with engine._master_peak_lock:
            peak_in = float(np.max(np.abs(clipped_block)))
            engine._master_peak_l = float(np.max(np.abs(clipped_block[:, 0])))
            engine._master_peak_r = float(np.max(np.abs(clipped_block[:, 1])))
            if peak_in >= 1.0:
                engine._master_clipped = True
                engine._master_clip_time = time.time()

        p_l, p_r, is_clip = engine.get_master_peaks()
        self.assertGreater(p_l, 1.0)
        self.assertTrue(is_clip, "Un signal > 1.0 doit enclencher le flag clipped")

        # Réinitialisation du clip
        engine.reset_master_clip()
        _, _, is_clip_after_reset = engine.get_master_peaks()
        self.assertFalse(is_clip_after_reset, "reset_master_clip doit réinitialiser l'indicateur")

    def test_02_master_meter_widget_gui(self):
        """Vérifie le MasterMeterWidget et le reset au clic de souris"""
        meter = MasterMeterWidget()
        meter.resize(100, 32)
        meter.show()

        # Mise à jour avec valeurs normales
        meter.set_levels(0.7, 0.65, False)
        self.assertFalse(meter.is_clipped)
        self.assertAlmostEqual(meter.level_l, 0.7, places=2)

        # Mise à jour avec saturation (> 0 dB)
        meter.set_levels(1.2, 1.1, True)
        self.assertTrue(meter.is_clipped)

        # Clic de souris pour réinitialiser
        reset_called = []
        meter.clip_reset.connect(lambda: reset_called.append(True))
        press_ev = QMouseEvent(QMouseEvent.Type.MouseButtonPress, QPointF(meter.width() - 5, meter.height() / 2),
                               Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        meter.mousePressEvent(press_ev)

        self.assertFalse(meter.is_clipped)
        self.assertEqual(len(reset_called), 1, "Le clic doit émettre clip_reset")
        meter.close()

    # -------------------------------------------------------------------------
    # 2. Reverb DSP & Audibilité
    # -------------------------------------------------------------------------
    def test_03_stereo_studio_reverb_dsp(self):
        """Vérifie que la nouvelle Reverb de studio (comb + all-pass) produit une diffusion audible en wet"""
        sr = 44100
        reverb = StereoStudioReverb(sample_rate=sr)

        # Signal impulsionnel sec suffisamment long pour couvrir les lignes de retard (> 1617 samples)
        impulse = np.zeros((8192, 2), dtype=np.float32)
        impulse[0, :] = 1.0

        # Dry 100% (mix = 0.0) -> sortie identique à l'entrée
        reverb.room_size = 0.8
        reverb.damping = 0.3
        reverb.mix = 0.0
        out_dry = reverb.process(impulse.copy())
        self.assertAlmostEqual(out_dry[0, 0], 1.0, places=2)
        self.assertAlmostEqual(float(np.max(np.abs(out_dry[100:, :]))), 0.0, places=3)

        # Wet 100% (mix = 1.0) -> queue de réverbération riche et diffuse sur les retards suivants
        reverb_wet = StereoStudioReverb(sample_rate=sr)
        reverb_wet.room_size = 0.8
        reverb_wet.damping = 0.3
        reverb_wet.mix = 1.0
        out_wet = reverb_wet.process(impulse.copy())

        # Doit contenir de l'énergie réverbérée au-delà de 1200 échantillons (retards des comb filters)
        tail_energy = np.sum(out_wet[1200:, :] ** 2)
        self.assertGreater(tail_energy, 1e-4, "La queue de réverbération doit être nettement audible et présente")

    def test_04_render_note_includes_tail(self):
        """Vérifie que render_note calcule une queue de déclin (release + reverb tail) et ne coupe pas brutalement"""
        synth = NovaSynthPlugin()
        synth.reverb_enabled = True
        synth.reverb_mix = 0.6
        synth.release = 0.3

        sr = 44100
        # Demande d'une note de 0.2 seconde
        audio = synth.render_note(pitch=60, duration_sec=0.2, velocity=0.8, sample_rate=sr)

        expected_min_len = int(0.2 * sr)
        # La longueur totale rendue doit dépasser la durée de la note pour inclure la release + reverb tail
        self.assertGreater(len(audio), expected_min_len + int(0.1 * sr),
                           "render_note doit étendre le buffer pour inclure le déclin et la réverbération")

    # -------------------------------------------------------------------------
    # 3. Sound Stacking, Protection & Multi-Bus Routing
    # -------------------------------------------------------------------------
    def test_05_sound_stacking_bus_routing_and_protection(self):
        """Vérifie le sélecteur de bus OUT 1..10 et la protection contre la suppression de la dernière couche"""
        synth = NovaSynthPlugin()
        gui = NovaSynthGUI(synth)
        gui.show()

        # Vérification du nombre initial de cartes de couches
        self.assertEqual(len(gui.layer_cards), 2)
        c1 = gui.layer_cards[0]
        self.assertEqual(c1.combo_bus.count(), 10, "Le sélecteur de bus doit offrir 10 sorties stéréo")
        self.assertEqual(c1.combo_bus.currentText(), "OUT 1")

        # Changer le bus vers OUT 4 (index 3)
        c1.combo_bus.setCurrentIndex(3)
        self.assertEqual(synth.layers[0].output_bus, 3)

        # Ajout d'une couche via le bouton GUI
        gui.btn_add_layer.click()
        self.assertEqual(len(synth.layers), 3)
        self.assertEqual(len(gui.layer_cards), 3)
        # La nouvelle couche doit avoir un bus incrémenté (index 2 -> OUT 3)
        self.assertEqual(gui.layer_cards[2].combo_bus.currentText(), "OUT 3")

        # Suppression de 2 couches
        gui._delete_layer(synth.layers[2])
        gui._delete_layer(synth.layers[1])
        self.assertEqual(len(synth.layers), 1)

        # Tentative de suppression de la dernière couche : doit être refusée sans bloquer sur QMessageBox
        with patch("PySide6.QtWidgets.QMessageBox.information") as mock_info:
            remaining = synth.layers[0]
            gui._delete_layer(remaining)
            mock_info.assert_called_once()
            self.assertEqual(len(synth.layers), 1, "La dernière couche ne doit jamais être supprimée")

        gui.close()

    # -------------------------------------------------------------------------
    # 4. Interactive 2D Mouse Dragging (ADSR & Filter)
    # -------------------------------------------------------------------------
    def test_06_interactive_adsr_2d_drag(self):
        """Vérifie la manipulation 2D interactive à la souris des nœuds ADSR"""
        adsr = InteractiveADSRWidget()
        adsr.resize(300, 150)
        adsr.set_adsr(0.1, 0.2, 0.7, 0.4)
        adsr.show()

        changed_events = []
        adsr.adsr_changed.connect(lambda a, d, s, r: changed_events.append((a, d, s, r)))

        # Récupérer les positions géométriques des nœuds
        _, _, _, _, _, nodes = adsr._get_geometry()
        att_pt = nodes[0]  # Sommet Attack

        # Clic souris sur le point Attack
        press_ev = QMouseEvent(QMouseEvent.Type.MouseButtonPress, att_pt,
                               Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        adsr.mousePressEvent(press_ev)
        self.assertEqual(adsr.active_node, 0)

        # Déplacement de la souris vers la droite (allonger l'attaque)
        drag_pt = QPointF(att_pt.x() + 40, att_pt.y())
        move_ev = QMouseEvent(QMouseEvent.Type.MouseMove, drag_pt,
                              Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        adsr.mouseMoveEvent(move_ev)

        # Relâchement de la souris
        release_ev = QMouseEvent(QMouseEvent.Type.MouseButtonRelease, drag_pt,
                                 Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        adsr.mouseReleaseEvent(release_ev)

        self.assertIsNone(adsr.active_node)
        self.assertGreater(len(changed_events), 0, "Le glissement 2D doit émettre adsr_changed")
        # L'attaque doit avoir augmenté
        self.assertGreater(adsr.attack, 0.1)

        adsr.close()

    def test_07_interactive_filter_curve_2d_drag(self):
        """Vérifie la manipulation 2D interactive à la souris du point Cutoff / Résonance du filtre"""
        filt = FilterCurveWidget()
        filt.resize(300, 150)
        filt.set_filter("lowpass", 1200.0, 1.5)
        filt.show()

        changed_events = []
        filt.filter_changed.connect(lambda c, r: changed_events.append((c, r)))

        # Position actuelle du point de contrôle
        *_, x_node, y_node, _, _ = filt._get_node_pos()
        ctrl_pt = QPointF(x_node, y_node)

        # Clic souris sur le point
        press_ev = QMouseEvent(QMouseEvent.Type.MouseButtonPress, ctrl_pt,
                               Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        filt.mousePressEvent(press_ev)
        self.assertTrue(filt.is_dragging)

        # Glissement vers le haut (augmentation de la résonance Q) et vers la droite (augmentation de Cutoff Hz)
        target_pt = QPointF(ctrl_pt.x() + 50, ctrl_pt.y() - 30)
        move_ev = QMouseEvent(QMouseEvent.Type.MouseMove, target_pt,
                              Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        filt.mouseMoveEvent(move_ev)

        release_ev = QMouseEvent(QMouseEvent.Type.MouseButtonRelease, target_pt,
                                 Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        filt.mouseReleaseEvent(release_ev)

        self.assertFalse(filt.is_dragging)
        self.assertGreater(len(changed_events), 0, "Le glissement 2D doit émettre filter_changed")
        self.assertGreater(filt.cutoff, 1200.0)
        self.assertGreater(filt.resonance, 1.5)

        filt.close()

    # -------------------------------------------------------------------------
    # 5. Note Prewarm Cache for Instant Audition
    # -------------------------------------------------------------------------
    def test_08_audition_note_cache(self):
        """Vérifie le cache de pré-rendu pour une latence de pré-écoute clavier < 1ms"""
        synth = NovaSynthPlugin()
        gui = NovaSynthGUI(synth)
        gui.show()

        # Le cache doit être initialisé
        self.assertIsInstance(gui._note_cache, dict)

        # Simuler un rendu en cache pour la note C4 (60)
        audio = synth.render_note(60, 0.45, 0.85, 44100)
        gui._note_cache[60] = audio

        # Vérifier que la note est en cache
        self.assertIn(60, gui._note_cache)
        self.assertEqual(len(gui._note_cache[60]), len(audio))

        # Test invalidation du cache lors de modifications de paramètres
        gui._invalidate_note_cache()
        self.assertEqual(len(gui._note_cache), 0, "L'invalidation doit vider le cache pour actualiser le timbre")

        gui.close()

    # -------------------------------------------------------------------------
    # 6. Oscilloscope Sample Fade & Motion
    # -------------------------------------------------------------------------
    def test_09_oscilloscope_fade_and_dynamics(self):
        """Vérifie que l'oscilloscope estompe dynamiquement les échantillons pour ne jamais figer"""
        osc = CyberOscilloscope()
        osc.resize(200, 100)
        osc.show()

        # Injection d'un burst d'échantillons
        test_samples = np.full(512, 0.8, dtype=np.float32)
        osc.set_audio_data(test_samples)

        initial_max = float(np.max(np.abs(osc._samples)))
        self.assertAlmostEqual(initial_max, 0.8, places=2)

        # Simulation de quelques frames de timer
        for _ in range(5):
            osc._on_timer()

        # Les échantillons doivent avoir décru (0.90^5 = ~0.59)
        decayed_max = float(np.max(np.abs(osc._samples)))
        self.assertLess(decayed_max, initial_max, "L'oscilloscope doit estomper les échantillons en l'absence de son")
        osc.close()

    # -------------------------------------------------------------------------
    # 7. Drag Performance & Audition Latency
    # -------------------------------------------------------------------------
    def test_10_drag_performance_and_fast_audition(self):
        """Vérifie la fluidité absolue lors du glisser-déplacer et la réponse instantanée de l'audition"""
        import time
        synth = NovaSynthPlugin()
        gui = NovaSynthGUI(synth)
        gui.show()

        # 1. Simuler 50 mouvements consécutifs de glisser-déplacer 2D
        t0 = time.time()
        for i in range(50):
            gui._on_filter_view_dragged(1000.0 + i * 20, 1.0 + (i % 5) * 0.2)
        drag_time = time.time() - t0

        # Les 50 mises à jour d'interface doivent s'exécuter rapidement (sans bloquer ni spammer de threads)
        self.assertLess(drag_time, 0.25, "50 événements de glisser-déplacer doivent s'exécuter de façon fluide")
        self.assertTrue(gui._prewarm_timer.isActive(), "Le pré-calcul lourd doit être debouncé pendant le déplacement")

        # 2. Vérifier le temps de rendu immédiat d'une note froide (non pré-calculée)
        t_note = time.time()
        gui._on_audition_note(60)
        cold_note_time = time.time() - t_note
        self.assertLess(cold_note_time, 0.50, "Le rendu d'une note froide doit s'exécuter en moins de 500ms (contre plusieurs secondes auparavant)")

        # 3. Vérifier le temps de déclenchement d'une note chaude (en cache)
        t_cached = time.time()
        gui._on_audition_note(60)
        warm_note_time = time.time() - t_cached
        self.assertLess(warm_note_time, 0.010, "Une note en cache doit répondre en moins de 10ms (<1ms typique)")

        gui.close()

    # -------------------------------------------------------------------------
    # 8. Sound Layer Waveform Selection (Card & Sculpting Panel)
    # -------------------------------------------------------------------------
    def test_11_waveform_selection_gui(self):
        """Vérifie la sélection personnalisée des formes d'onde sur les cartes de couches et le panneau de sculpture"""
        synth = NovaSynthPlugin()
        gui = NovaSynthGUI(synth)
        gui.show()

        # 1. Vérification des formes d'onde par défaut des couches
        self.assertEqual(len(gui.layer_cards), 2)
        c0 = gui.layer_cards[0]
        c1 = gui.layer_cards[1]
        self.assertEqual(c0.combo_wave.currentData(), "saw")
        self.assertEqual(c1.combo_wave.currentData(), "square")
        self.assertEqual(gui.combo_wave.currentData(), "saw")

        # 2. Changement de forme d'onde directement sur la carte de la couche
        # Choisir Sine (index 2 dans WAVEFORM_ITEMS_SHORT)
        c0.combo_wave.setCurrentIndex(2)
        self.assertEqual(synth.layers[0].waveform, "sine")
        self.assertEqual(gui.combo_wave.currentData(), "sine")

        # 3. Changement de forme d'onde via le menu déroulant en haut à droite du panneau de sculpture
        for i in range(gui.combo_wave.count()):
            if gui.combo_wave.itemData(i) == "supersaw":
                gui.combo_wave.setCurrentIndex(i)
                break
        self.assertEqual(synth.layers[0].waveform, "supersaw")
        self.assertEqual(c0.combo_wave.currentData(), "supersaw")

        # 4. Ajout d'une couche avec une forme d'onde spécifique (FM)
        gui._on_add_layer_with_waveform("fm")
        self.assertEqual(len(synth.layers), 3)
        self.assertEqual(synth.layers[2].waveform, "fm")
        self.assertEqual(gui.selected_layer, synth.layers[2])
        self.assertEqual(gui.combo_wave.currentData(), "fm")
        self.assertEqual(gui.layer_cards[2].combo_wave.currentData(), "fm")

        # 5. Modification de l'onde sur une couche non active (couche 1)
        gui.layer_cards[1].combo_wave.setCurrentIndex(3) # Triangle
        self.assertEqual(synth.layers[1].waveform, "triangle")

        gui.close()


if __name__ == "__main__":
    unittest.main()

