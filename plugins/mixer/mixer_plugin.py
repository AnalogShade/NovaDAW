"""
plugins/mixer/mixer_plugin.py - DSP et modèle pour le plugin Mixeur de NovaDAW.

Plugin de mixage placé par défaut sur la piste Master :
- Permet de contrôler les volumes, panoramiques, mutes et solos de toutes les pistes
- Mesure les crêtes stéréo (VU-mètres L/R temps réel)
- Gère le fader Master et le niveau de sortie global
- Totalement synchronisé avec le projet NovaDAW
"""
from typing import Dict, Any, Optional, List
import numpy as np

from plugins.base import BasePlugin
from plugins.registry import register_plugin


@register_plugin(
    plugin_type_id="novadaw.mixer",
    name="Mixeur de Pistes",
    category="mixer",
    icon="🎛️",
    description="Console de mixage complète multi-pistes avec faders, panoramique, VU-mètres et bus Master.",
    is_default=True
)
class MixerPlugin(BasePlugin):
    """
    Plugin Mixeur pour NovaDAW.
    Gère la balance globale du projet, les faders individuels et le bus Master.
    """

    def __init__(
        self,
        instance_id: Optional[str] = None,
        master_volume: float = 1.0,
        master_pan: float = 0.0
    ):
        super().__init__(
            plugin_type_id="novadaw.mixer",
            name="Mixeur de Pistes",
            category="mixer",
            icon="🎛️",
            instance_id=instance_id
        )
        self.master_volume: float = float(master_volume)
        self.master_pan: float = float(master_pan)
        self.mono_switch: bool = False
        self.dim_switch: bool = False

        # Référence au projet actif
        self.project: Optional[Any] = None

        # Niveaux de crête en temps réel (pour l'affichage VU-mètre)
        self.peak_left_db: float = -60.0
        self.peak_right_db: float = -60.0
        self.rms_left_db: float = -60.0
        self.rms_right_db: float = -60.0

        # Niveaux crête par piste (track_id -> (peak_l_db, peak_r_db))
        self.track_peaks: Dict[str, tuple[float, float]] = {}

    def set_project(self, project: Any):
        self.project = project

    def reset(self) -> None:
        self.peak_left_db = -60.0
        self.peak_right_db = -60.0
        self.rms_left_db = -60.0
        self.rms_right_db = -60.0
        self.track_peaks.clear()

    def process(self, audio: np.ndarray, sample_rate: int) -> np.ndarray:
        """Traite le bus stéréo Master et calcule les métriques VU-mètres"""
        if not self.enabled or audio is None or len(audio) == 0:
            self.peak_left_db = -60.0
            self.peak_right_db = -60.0
            return audio

        if audio.ndim == 1:
            out = np.column_stack((audio, audio)).astype(np.float32)
        else:
            out = audio.copy().astype(np.float32)

        # 1. Mode Mono (sommation L+R / 2 sur les deux canaux)
        if self.mono_switch:
            mono = 0.5 * (out[:, 0] + out[:, 1])
            out[:, 0] = mono
            out[:, 1] = mono

        # 2. Application du volume et pan Master du plugin
        vol = self.master_volume * (0.25 if self.dim_switch else 1.0)
        pan = max(-1.0, min(1.0, self.master_pan))
        gain_l = vol * (1.0 - max(0.0, pan))
        gain_r = vol * (1.0 + min(0.0, pan))

        out[:, 0] *= gain_l
        out[:, 1] *= gain_r

        # 3. Calcul des niveaux de crête (Peak Meters)
        max_l = float(np.max(np.abs(out[:, 0]))) if len(out) > 0 else 0.0
        max_r = float(np.max(np.abs(out[:, 1]))) if len(out) > 0 else 0.0

        self.peak_left_db = 20.0 * np.log10(max(1e-5, max_l))
        self.peak_right_db = 20.0 * np.log10(max(1e-5, max_r))

        # Calcul RMS
        rms_l = float(np.sqrt(np.mean(out[:, 0] ** 2))) if len(out) > 0 else 0.0
        rms_r = float(np.sqrt(np.mean(out[:, 1] ** 2))) if len(out) > 0 else 0.0
        self.rms_left_db = 20.0 * np.log10(max(1e-5, rms_l))
        self.rms_right_db = 20.0 * np.log10(max(1e-5, rms_r))

        return out

    def update_track_peak(self, track_id: str, peak_l: float, peak_r: float):
        """Appelé par le moteur audio pour mettre à jour les niveaux d'une piste individuelle"""
        self.track_peaks[track_id] = (
            20.0 * np.log10(max(1e-5, peak_l)),
            20.0 * np.log10(max(1e-5, peak_r))
        )

    def get_state(self) -> Dict[str, Any]:
        return {
            "master_volume": self.master_volume,
            "master_pan": self.master_pan,
            "mono_switch": self.mono_switch,
            "dim_switch": self.dim_switch,
        }

    def set_state(self, state: Dict[str, Any]) -> None:
        self.master_volume = float(state.get("master_volume", 1.0))
        self.master_pan = float(state.get("master_pan", 0.0))
        self.mono_switch = bool(state.get("mono_switch", False))
        self.dim_switch = bool(state.get("dim_switch", False))

    def create_editor(self, parent=None):
        from plugins.mixer.mixer_gui import MixerConsoleWidget
        return MixerConsoleWidget(self, parent)
