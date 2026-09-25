"""
tests/test_preset_manager.py - Tests unitaires du gestionnaire de presets universel natif de NovaDAW.
"""
import os
import sys
import tempfile
import shutil
import unittest
from unittest.mock import MagicMock, patch
import numpy as np

from PySide6.QtWidgets import QApplication

from core.preset_manager import PresetManager, PresetInfo
from plugins.synth.synth_plugin import NovaSynthPlugin
from plugins.synth.synth_gui import NovaSynthGUI
from plugins.base import BasePlugin
from ui.plugin_dialogs import NativePluginDialog

# Initialiser QApplication pour les tests Qt
app = QApplication.instance()
if app is None:
    app = QApplication(sys.argv)


class DummyPlugin(BasePlugin):
    """Plugin factice pour tester l'universalité du gestionnaire de presets"""
    def __init__(self, plugin_type_id="test.dummy"):
        super().__init__(
            plugin_type_id=plugin_type_id,
            name="Dummy Plugin",
            category="effect",
            icon="🧪"
        )
        self.gain = 1.0
        self.preset_name = "Default"
        self._factory_presets = {
            "Factory Clean": {"gain": 0.5, "preset_name": "Factory Clean"},
            "Factory Boost": {"gain": 2.0, "preset_name": "Factory Boost"},
        }

    def get_factory_presets(self):
        return self._factory_presets

    def get_state(self):
        return {"gain": self.gain, "preset_name": self.preset_name}

    def set_state(self, state):
        self.gain = state.get("gain", self.gain)
        self.preset_name = state.get("preset_name", self.preset_name)

    def process(self, audio, sample_rate):
        return audio * self.gain


class TestPresetManager(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.pm = PresetManager(base_dir=self.temp_dir)
        self.plugin = DummyPlugin()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_01_save_and_load_preset(self):
        """Vérifie l'enregistrement et le chargement d'un preset utilisateur"""
        self.plugin.gain = 1.75
        self.plugin.preset_name = "My Custom Gain"

        path = self.pm.save_preset(
            plugin_type_id=self.plugin.plugin_type_id,
            preset_name="My Custom Gain",
            state=self.plugin.to_dict(),
            author="Tester"
        )
        self.assertTrue(os.path.isfile(path))

        # Modifier le plugin
        self.plugin.gain = 0.1
        self.plugin.preset_name = "Changed"

        # Recharger le preset
        success = self.pm.load_preset(self.plugin, "My Custom Gain")
        self.assertTrue(success)
        self.assertAlmostEqual(self.plugin.gain, 1.75)
        self.assertEqual(self.plugin.preset_name, "My Custom Gain")

    def test_02_list_presets_combines_factory_and_user(self):
        """Vérifie que la liste agrège les presets d'usine et les presets utilisateur"""
        self.pm.save_preset(
            plugin_type_id=self.plugin.plugin_type_id,
            preset_name="User Sound 1",
            state=self.plugin.to_dict()
        )

        presets = self.pm.list_presets(self.plugin.plugin_type_id, self.plugin)
        names = [p.name for p in presets]

        self.assertIn("Factory Clean", names)
        self.assertIn("Factory Boost", names)
        self.assertIn("User Sound 1", names)

        # Vérifier les drapeaux is_factory
        clean_p = next(p for p in presets if p.name == "Factory Clean")
        user_p = next(p for p in presets if p.name == "User Sound 1")
        self.assertTrue(clean_p.is_factory)
        self.assertFalse(user_p.is_factory)

    def test_03_delete_preset(self):
        """Vérifie la suppression d'un preset utilisateur et la protection des presets d'usine"""
        self.pm.save_preset(self.plugin.plugin_type_id, "Temp Preset", self.plugin.to_dict())
        self.assertTrue(self.pm.preset_exists(self.plugin.plugin_type_id, "Temp Preset"))

        deleted = self.pm.delete_preset(self.plugin.plugin_type_id, "Temp Preset")
        self.assertTrue(deleted)
        self.assertFalse(self.pm.preset_exists(self.plugin.plugin_type_id, "Temp Preset"))

        # Essayer de supprimer un preset d'usine ne doit pas lever d'exception
        del_factory = self.pm.delete_preset(self.plugin.plugin_type_id, "Factory Clean")
        self.assertFalse(del_factory)

    def test_04_export_and_import_preset(self):
        """Vérifie l'exportation vers un fichier externe et l'importation"""
        self.plugin.gain = 3.14
        self.pm.save_preset(self.plugin.plugin_type_id, "To Export", self.plugin.to_dict())

        export_target = os.path.join(self.temp_dir, "exported_preset.ndawpreset")
        exported_path = self.pm.export_preset(self.plugin.plugin_type_id, "To Export", export_target)
        self.assertTrue(os.path.isfile(exported_path))

        # Importer sous un autre nom
        imported_info = self.pm.import_preset(self.plugin.plugin_type_id, exported_path)
        self.assertIsNotNone(imported_info)
        self.assertEqual(imported_info.name, "To Export")

        # Vérifier le chargement du preset importé
        new_plugin = DummyPlugin()
        self.pm.load_preset(new_plugin, "To Export")
        self.assertAlmostEqual(new_plugin.gain, 3.14)

    def test_05_isolation_between_plugins(self):
        """Vérifie l'isolation stricte des presets entre types de plugins différents"""
        other_plugin = DummyPlugin(plugin_type_id="test.other")
        self.pm.save_preset("test.dummy", "Shared Name", {"gain": 1.0})
        self.pm.save_preset("test.other", "Shared Name", {"gain": 99.0})

        p1 = DummyPlugin("test.dummy")
        p2 = DummyPlugin("test.other")

        self.pm.load_preset(p1, "Shared Name")
        self.pm.load_preset(p2, "Shared Name")

        self.assertEqual(p1.gain, 1.0)
        self.assertEqual(p2.gain, 99.0)

    def test_06_novasynth_preset_roundtrip(self):
        """Vérifie l'intégration complète avec le synthétiseur NovaSynth"""
        synth = NovaSynthPlugin()
        synth.master_volume = 0.88
        synth.delay.enabled = True
        synth.delay.mix = 0.45
        synth.layers[0].semitone = 7

        path = self.pm.save_preset(
            plugin_type_id=synth.plugin_type_id,
            preset_name="Fifth Lead Custom",
            state=synth.to_dict()
        )
        self.assertTrue(os.path.exists(path))

        # Réinitialiser et recharger
        new_synth = NovaSynthPlugin()
        success = self.pm.load_preset(new_synth, "Fifth Lead Custom")
        self.assertTrue(success)
        self.assertAlmostEqual(new_synth.master_volume, 0.88)
        self.assertTrue(new_synth.delay.enabled)
        self.assertAlmostEqual(new_synth.delay.mix, 0.45)
        self.assertEqual(new_synth.layers[0].semitone, 7)

    def test_07_native_plugin_dialog_preset_toolbar(self):
        """Vérifie le fonctionnement de la barre de presets dans NativePluginDialog"""
        synth = NovaSynthPlugin()
        dialog = NativePluginDialog(synth)
        dialog.show()

        # Vérifier la présence des contrôles
        self.assertTrue(hasattr(dialog, "combo_presets"))
        self.assertTrue(hasattr(dialog, "btn_save"))
        self.assertTrue(hasattr(dialog, "btn_delete"))
        self.assertTrue(hasattr(dialog, "btn_import"))
        self.assertTrue(hasattr(dialog, "btn_export"))

        # Vérifier que les presets d'usine sont listés dans le combo
        items = [dialog.combo_presets.itemText(i) for i in range(dialog.combo_presets.count())]
        self.assertTrue(any("Cyberpunk Acid Lead" in item for item in items))

        dialog.close()

    def test_08_novasynth_gui_preset_bar(self):
        """Vérifie la barre de presets interne au GUI de NovaSynth"""
        synth = NovaSynthPlugin()
        gui = NovaSynthGUI(synth)
        gui.show()

        self.assertTrue(hasattr(gui, "combo_presets"))
        self.assertTrue(hasattr(gui, "btn_save_preset"))

        # Simuler la sélection d'un preset
        count = gui.combo_presets.count()
        self.assertGreater(count, 0)

        gui.combo_presets.setCurrentIndex(1)
        self.assertIsNotNone(synth.preset_name)

        gui.close()


if __name__ == "__main__":
    unittest.main()
