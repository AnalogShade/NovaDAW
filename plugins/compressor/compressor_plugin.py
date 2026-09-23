"""
plugins/compressor/compressor_plugin.py - DSP et modèle du Compresseur Dynamique.

Implémente un compresseur audio de qualité studio avec :
- Détection de crêtes et enveloppe ballistique temps réel (Attack / Release)
- Transition douce Soft-Knee
- Réduction de gain calculée par échantillon
- Compensation de gain (Makeup Gain)
- Compression parallèle (Dry/Wet Mix)
- Mesure en direct de la réduction de gain pour le VU-mètre
"""
from typing import Dict, Any, Optional
import numpy as np

from plugins.base import BasePlugin
from plugins.registry import register_plugin


@register_plugin(
    plugin_type_id="novadaw.compressor",
    name="Compresseur Dynamique",
    category="dynamics",
    icon="🗜️",
    description="Compresseur audio studio avec soft-knee, réduction de gain et compression parallèle.",
    is_default=True
)
class CompressorPlugin(BasePlugin):
    """
    Compresseur audio dynamique modulaire pour NovaDAW.
    """

    def __init__(
        self,
        instance_id: Optional[str] = None,
        threshold_db: float = -18.0,
        ratio: float = 4.0,
        attack_ms: float = 15.0,
        release_ms: float = 120.0,
        knee_db: float = 4.0,
        makeup_gain_db: float = 0.0,
        mix: float = 1.0
    ):
        super().__init__(
            plugin_type_id="novadaw.compressor",
            name="Compresseur Dynamique",
            category="dynamics",
            icon="🗜️",
            instance_id=instance_id
        )
        self.threshold_db: float = float(threshold_db)
        self.ratio: float = float(ratio)
        self.attack_ms: float = float(attack_ms)
        self.release_ms: float = float(release_ms)
        self.knee_db: float = float(knee_db)
        self.makeup_gain_db: float = float(makeup_gain_db)
        self.mix: float = float(mix)  # 0.0 (dry) à 1.0 (wet)

        # État interne de l'enveloppe de compression
        self._env_gain_db: float = 0.0
        self.current_gain_reduction_db: float = 0.0
        self.current_input_peak_db: float = -60.0
        self.current_output_peak_db: float = -60.0

    def reset(self) -> None:
        self._env_gain_db = 0.0
        self.current_gain_reduction_db = 0.0
        self.current_input_peak_db = -60.0
        self.current_output_peak_db = -60.0

    def process(self, audio: np.ndarray, sample_rate: int) -> np.ndarray:
        """Traite le buffer audio (N, 2) avec l'algorithme de compression dynamique"""
        if not self.enabled or audio is None or len(audio) == 0:
            self.current_gain_reduction_db = 0.0
            return audio

        # Formater en stéréo float32
        if audio.ndim == 1:
            in_buf = np.column_stack((audio, audio)).astype(np.float32)
        else:
            in_buf = audio.copy().astype(np.float32)

        n_samples = len(in_buf)

        # 1. Détection de crête (Sidechain interne : max des 2 canaux)
        peak_input = np.maximum(np.abs(in_buf[:, 0]), np.abs(in_buf[:, 1]))
        max_in = float(np.max(peak_input)) if n_samples > 0 else 0.0
        self.current_input_peak_db = 20.0 * np.log10(max(1e-5, max_in))

        # Conversion en dB avec seuil plancher (-96 dB)
        input_db = 20.0 * np.log10(np.maximum(peak_input, 1e-5))

        # 2. Courbe caractéristique statique de compression (avec Soft Knee)
        t = self.threshold_db
        r = max(1.0, self.ratio)
        w = max(0.0, self.knee_db)

        # Calcul vectorisé du niveau de sortie cible y_db
        # Région 1 : Sous le seuil et hors knee -> pas de compression
        target_gr_db = np.zeros(n_samples, dtype=np.float32)

        diff = input_db - t

        if w > 0.1:
            # Masque zone linéaire inférieure (2 * (x - T) < -W)
            # Masque zone soft-knee (-W <= 2 * (x - T) <= W)
            knee_mask = (2.0 * diff >= -w) & (2.0 * diff <= w)
            # Masque zone de compression franche (2 * (x - T) > W)
            comp_mask = 2.0 * diff > w

            # Calcul soft-knee : y = x + (1/R - 1) * (x - T + W/2)^2 / (2W)
            target_gr_db[knee_mask] = (1.0 / r - 1.0) * ((diff[knee_mask] + w / 2.0) ** 2) / (2.0 * w)
            # Calcul compression franche : y = T + (x - T) / R -> GR = (1/R - 1) * (x - T)
            target_gr_db[comp_mask] = (1.0 / r - 1.0) * diff[comp_mask]
        else:
            # Hard knee pur
            comp_mask = diff > 0.0
            target_gr_db[comp_mask] = (1.0 / r - 1.0) * diff[comp_mask]

        # 3. Lissage de l'enveloppe ballistique (Attack / Release)
        att_sec = max(0.0001, self.attack_ms / 1000.0)
        rel_sec = max(0.001, self.release_ms / 1000.0)
        alpha_att = float(np.exp(-1.0 / (att_sec * sample_rate)))
        alpha_rel = float(np.exp(-1.0 / (rel_sec * sample_rate)))

        env_gr = np.zeros(n_samples, dtype=np.float32)
        cur_env = self._env_gain_db

        for i in range(n_samples):
            tgt = target_gr_db[i]
            if tgt < cur_env:
                # Attack phase (compression en cours d'augmentation, GR devient plus négatif)
                cur_env = alpha_att * cur_env + (1.0 - alpha_att) * tgt
            else:
                # Release phase (relâchement, GR remonte vers 0)
                cur_env = alpha_rel * cur_env + (1.0 - alpha_rel) * tgt
            env_gr[i] = cur_env

        self._env_gain_db = cur_env
        self.current_gain_reduction_db = float(np.min(env_gr)) if n_samples > 0 else 0.0

        # 4. Conversion en gain linéaire et application du Makeup Gain
        makeup_lin = 10.0 ** (self.makeup_gain_db / 20.0)
        gain_lin = (10.0 ** (env_gr / 20.0)) * makeup_lin

        # Application au signal stéréo
        wet_out = np.empty_like(in_buf)
        wet_out[:, 0] = in_buf[:, 0] * gain_lin
        wet_out[:, 1] = in_buf[:, 1] * gain_lin

        # 5. Compression parallèle (Dry / Wet Mix)
        mix_val = max(0.0, min(1.0, self.mix))
        out_buf = (1.0 - mix_val) * in_buf + mix_val * wet_out

        max_out = float(np.max(np.abs(out_buf))) if n_samples > 0 else 0.0
        self.current_output_peak_db = 20.0 * np.log10(max(1e-5, max_out))

        return out_buf.astype(np.float32)

    def get_state(self) -> Dict[str, Any]:
        return {
            "threshold_db": self.threshold_db,
            "ratio": self.ratio,
            "attack_ms": self.attack_ms,
            "release_ms": self.release_ms,
            "knee_db": self.knee_db,
            "makeup_gain_db": self.makeup_gain_db,
            "mix": self.mix,
        }

    def set_state(self, state: Dict[str, Any]) -> None:
        self.threshold_db = float(state.get("threshold_db", -18.0))
        self.ratio = float(state.get("ratio", 4.0))
        self.attack_ms = float(state.get("attack_ms", 15.0))
        self.release_ms = float(state.get("release_ms", 120.0))
        self.knee_db = float(state.get("knee_db", 4.0))
        self.makeup_gain_db = float(state.get("makeup_gain_db", 0.0))
        self.mix = float(state.get("mix", 1.0))
        self.reset()

    def create_editor(self, parent=None):
        from plugins.compressor.compressor_gui import CompressorPluginWidget
        return CompressorPluginWidget(self, parent)
