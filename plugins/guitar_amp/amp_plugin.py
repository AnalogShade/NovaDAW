"""
plugins/guitar_amp/amp_plugin.py - Plugin d'amplificateur de guitare Nova Guitar Amp pour NovaDAW.

Licence MIT — Amplification virtuelle modulaire :
- Contrôles essentiels : Volume, Distorsion (Saturation lampe/diode oversamplée 4x),
  Tone Stack 3 bandes (Low, Mid, Treble) et Simulateur de Baffle (Cab Sim On/Off)
- Bouton Bypass transparent temps réel
- Gestion complète des presets (usine + utilisateur via PresetManager) et sérialisation .ndaw
"""
import copy
import math
import threading
from typing import Dict, Any, Optional
import numpy as np

from plugins.base import BasePlugin
from plugins.registry import register_plugin
from plugins.guitar_amp.amp_dsp import GuitarAmpDSP


@register_plugin(
    plugin_type_id="novadaw.guitar_amp",
    name="Nova Guitar Amp",
    category="effect",
    icon="🔊",
    description="Amplificateur de guitare avec Volume, Low/Mid/Treble, Distorsion oversamplée 4x et simulateur de baffle.",
    is_default=True,
)
class NovaGuitarAmpPlugin(BasePlugin):
    """
    Plugin d'amplificateur de guitare simple, musical et sans aliasing pour NovaDAW.
    Fonctionne après Nova Guitar (Électrique DI) ou sur une piste audio de vraie guitare enregistrée.
    """

    FACTORY_PRESETS: Dict[str, Dict[str, Any]] = {
        "Clean Studio (Neutre)": {
            "volume": 1.0,
            "distortion": 0.0,
            "low_db": 0.0,
            "mid_db": 0.0,
            "treble_db": 0.0,
            "cab_enabled": True,
        },
        "Warm Clean (Tube)": {
            "volume": 1.05,
            "distortion": 0.14,
            "low_db": 2.0,
            "mid_db": 1.0,
            "treble_db": 1.5,
            "cab_enabled": True,
        },
        "Blues Crunch": {
            "volume": 1.0,
            "distortion": 0.38,
            "low_db": 1.5,
            "mid_db": 3.0,
            "treble_db": 2.0,
            "cab_enabled": True,
        },
        "Classic Rock Overdrive": {
            "volume": 0.95,
            "distortion": 0.65,
            "low_db": 2.5,
            "mid_db": 2.0,
            "treble_db": 3.5,
            "cab_enabled": True,
        },
        "High Gain Metal": {
            "volume": 0.90,
            "distortion": 0.92,
            "low_db": 4.0,
            "mid_db": -3.0,
            "treble_db": 4.5,
            "cab_enabled": True,
        },
    }

    def __init__(
        self,
        instance_id: Optional[str] = None,
        volume: float = 1.0,
        distortion: float = 0.25,
        low_db: float = 1.0,
        mid_db: float = 1.5,
        treble_db: float = 1.5,
        cab_enabled: bool = True,
    ):
        super().__init__(
            plugin_type_id="novadaw.guitar_amp",
            name="Nova Guitar Amp",
            category="effect",
            icon="🔊",
            instance_id=instance_id,
        )
        self.description = "Ampli guitare avec Volume, Low/Mid/Treble, Distorsion oversamplée 4x et Cab Sim."
        self.preset_name: str = "Warm Clean (Tube)"

        self.volume: float = float(volume)
        self.distortion: float = float(distortion)
        self.low_db: float = float(low_db)
        self.mid_db: float = float(mid_db)
        self.treble_db: float = float(treble_db)
        self.cab_enabled: bool = bool(cab_enabled)

        self._dsp_lock = threading.RLock()
        self._dsp = GuitarAmpDSP(44100)

        # Indicateurs temps réel lus par la GUI
        self.current_input_peak_db: float = -60.0
        self.current_output_peak_db: float = -60.0

    def get_factory_presets(self) -> Dict[str, Dict[str, Any]]:
        return copy.deepcopy(self.FACTORY_PRESETS)

    def apply_preset(self, preset_name: str) -> bool:
        with self._dsp_lock:
            target = None
            for k, v in self.FACTORY_PRESETS.items():
                if k.lower() == preset_name.lower():
                    target = v
                    self.preset_name = k
                    break
            if target is None:
                try:
                    from core.preset_manager import global_preset_manager
                    if global_preset_manager.load_preset(self, preset_name):
                        return True
                except Exception:
                    pass
                return False
            self.set_state({**target, "preset_name": self.preset_name, "enabled": self.enabled})
            return True

    def reset(self) -> None:
        with self._dsp_lock:
            self._dsp.reset()
            self.current_input_peak_db = -60.0
            self.current_output_peak_db = -60.0

    def decay_meters(self, rate_db: float = 2.0) -> None:
        if self.current_input_peak_db > -60.0:
            self.current_input_peak_db = max(-60.0, self.current_input_peak_db - rate_db)
        if self.current_output_peak_db > -60.0:
            self.current_output_peak_db = max(-60.0, self.current_output_peak_db - rate_db)

    def process(self, audio: np.ndarray, sample_rate: int) -> np.ndarray:
        """Traite un buffer audio stéréo (N, 2) en float32 avec gestion du Bypass."""
        if audio is None or len(audio) == 0:
            return audio

        if audio.ndim == 1:
            in_buf = np.column_stack((audio, audio)).astype(np.float32)
        else:
            in_buf = np.ascontiguousarray(audio, dtype=np.float32)

        max_in = float(np.max(np.abs(in_buf)))
        in_pk_db = 20.0 * math.log10(max(1e-5, max_in))
        self.current_input_peak_db = in_pk_db

        if not self.enabled:
            self.current_output_peak_db = in_pk_db
            return in_buf

        with self._dsp_lock:
            out_buf = self._dsp.process_block(
                in_buf=in_buf,
                sample_rate=sample_rate,
                distortion=self.distortion,
                low_db=self.low_db,
                mid_db=self.mid_db,
                treble_db=self.treble_db,
                volume=self.volume,
                cab_enabled=self.cab_enabled,
            )

        max_out = float(np.max(np.abs(out_buf))) if len(out_buf) > 0 else 0.0
        self.current_output_peak_db = 20.0 * math.log10(max(1e-5, max_out))
        return out_buf

    def get_state(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "preset_name": self.preset_name,
            "volume": self.volume,
            "distortion": self.distortion,
            "low_db": self.low_db,
            "mid_db": self.mid_db,
            "treble_db": self.treble_db,
            "cab_enabled": self.cab_enabled,
        }

    def set_state(self, state: Dict[str, Any]) -> None:
        with self._dsp_lock:
            if "enabled" in state:
                self.enabled = bool(state["enabled"])
            self.preset_name = str(state.get("preset_name", self.preset_name))
            self.volume = max(0.0, min(2.0, float(state.get("volume", self.volume))))
            self.distortion = max(0.0, min(1.0, float(state.get("distortion", self.distortion))))
            self.low_db = max(-12.0, min(12.0, float(state.get("low_db", self.low_db))))
            self.mid_db = max(-12.0, min(12.0, float(state.get("mid_db", self.mid_db))))
            self.treble_db = max(-12.0, min(12.0, float(state.get("treble_db", self.treble_db))))
            self.cab_enabled = bool(state.get("cab_enabled", self.cab_enabled))
            self._dsp.reset()

    def create_editor(self, parent=None):
        from plugins.guitar_amp.amp_gui import NovaGuitarAmpEditorWidget
        return NovaGuitarAmpEditorWidget(self, parent)
