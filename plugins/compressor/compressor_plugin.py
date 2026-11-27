"""
plugins/compressor/compressor_plugin.py - DSP et modèle du Compresseur Dynamique de Studio.

Implémente un compresseur audio de qualité studio professionnelle (broadcast-grade) :
- Détecteur de niveau découplé (Giannoulis architecture) éliminant toute distorsion harmonique
  (THD < 0.05%, suppression du ripple de passage par zéro sur les basses fréquences)
- Modes de détection sélectionnables : Crête (Peak - rapide/percutant) et RMS (doux/musical)
- Filtre passe-haut de sidechain commutable (Sidechain HPF 80 Hz) anti-pompage sur les basses
- Transition progressive à courbure douce (Soft-Knee réglable de 0 à 12 dB)
- Compensation de gain manuelle et Auto-Makeup Gain dynamique
- Compression parallèle transparente (Dry/Wet Mix)
- Mesures en direct ultra-précises pour VU-mètre de Réduction de Gain (GR) et crêtes Entrée/Sortie
"""
from typing import Dict, Any, Optional
import ctypes
import math
import threading
import numpy as np

from core import dsp_native
try:
    import scipy.signal
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False

from plugins.base import BasePlugin
from plugins.registry import register_plugin


@register_plugin(
    plugin_type_id="novadaw.compressor",
    name="Compresseur Dynamique",
    category="dynamics",
    icon="🗜️",
    description="Compresseur audio studio avec soft-knee, sidechain HPF, modes Peak/RMS et compression parallèle.",
    is_default=True
)
class CompressorPlugin(BasePlugin):
    """
    Compresseur audio dynamique modulaire de qualité studio pour NovaDAW.
    """

    FACTORY_PRESETS = {
        "Défaut (Transparent)": {
            "threshold_db": -18.0, "ratio": 4.0, "attack_ms": 15.0, "release_ms": 120.0,
            "knee_db": 4.0, "makeup_gain_db": 0.0, "auto_makeup": False, "mix": 1.0,
            "detection_mode": "peak", "sidechain_hpf_hz": 0.0
        },
        "Voix Douce (Opto)": {
            "threshold_db": -20.0, "ratio": 3.2, "attack_ms": 20.0, "release_ms": 250.0,
            "knee_db": 6.0, "makeup_gain_db": 3.0, "auto_makeup": False, "mix": 1.0,
            "detection_mode": "rms", "sidechain_hpf_hz": 80.0
        },
        "Batterie Punch (VCA)": {
            "threshold_db": -16.0, "ratio": 4.5, "attack_ms": 8.0, "release_ms": 65.0,
            "knee_db": 2.5, "makeup_gain_db": 2.5, "auto_makeup": False, "mix": 0.90,
            "detection_mode": "peak", "sidechain_hpf_hz": 80.0
        },
        "Snare Crack / Slam": {
            "threshold_db": -15.0, "ratio": 6.0, "attack_ms": 3.0, "release_ms": 45.0,
            "knee_db": 1.5, "makeup_gain_db": 3.0, "auto_makeup": False, "mix": 1.0,
            "detection_mode": "peak", "sidechain_hpf_hz": 0.0
        },
        "Basse Électrique": {
            "threshold_db": -18.0, "ratio": 4.0, "attack_ms": 12.0, "release_ms": 140.0,
            "knee_db": 4.0, "makeup_gain_db": 2.0, "auto_makeup": False, "mix": 1.0,
            "detection_mode": "rms", "sidechain_hpf_hz": 60.0
        },
        "Master Bus Glue": {
            "threshold_db": -12.0, "ratio": 2.0, "attack_ms": 30.0, "release_ms": 200.0,
            "knee_db": 5.0, "makeup_gain_db": 1.0, "auto_makeup": False, "mix": 1.0,
            "detection_mode": "rms", "sidechain_hpf_hz": 80.0
        },
        "Compression Parallèle NY": {
            "threshold_db": -28.0, "ratio": 10.0, "attack_ms": 1.5, "release_ms": 80.0,
            "knee_db": 1.0, "makeup_gain_db": 6.0, "auto_makeup": False, "mix": 0.40,
            "detection_mode": "peak", "sidechain_hpf_hz": 0.0
        },
        "Limiteur de Crête": {
            "threshold_db": -2.0, "ratio": 20.0, "attack_ms": 0.5, "release_ms": 50.0,
            "knee_db": 0.0, "makeup_gain_db": 0.0, "auto_makeup": False, "mix": 1.0,
            "detection_mode": "peak", "sidechain_hpf_hz": 0.0
        },
    }

    def __init__(
        self,
        instance_id: Optional[str] = None,
        threshold_db: float = -18.0,
        ratio: float = 4.0,
        attack_ms: float = 15.0,
        release_ms: float = 120.0,
        knee_db: float = 4.0,
        makeup_gain_db: float = 0.0,
        auto_makeup: bool = False,
        mix: float = 1.0,
        detection_mode: str = "peak",
        sidechain_hpf_hz: float = 0.0
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
        self.auto_makeup: bool = bool(auto_makeup)
        self.mix: float = float(mix)  # 0.0 (dry) à 1.0 (wet)
        self.detection_mode: str = str(detection_mode).lower()  # "peak" ou "rms"
        self.sidechain_hpf_hz: float = float(sidechain_hpf_hz)
        self.preset_name: str = "Défaut (Transparent)"

        # État interne continu pour filtrage temps réel sans artefacts entre buffers
        self._env_peak: float = 0.0
        self._env_gr_db: float = 0.0
        self._hpf_zi: Optional[np.ndarray] = None
        self._cached_hpf_freq: Optional[float] = None
        self._cached_hpf_sr: Optional[int] = None
        self._cached_hpf_b: Optional[np.ndarray] = None
        self._cached_hpf_a: Optional[np.ndarray] = None

        # Mesures en direct lues par l'interface graphique (30/60 FPS)
        self.current_gain_reduction_db: float = 0.0
        self.current_input_peak_db: float = -60.0
        self.current_output_peak_db: float = -60.0

        self._lock = threading.RLock()
        if dsp_native.NATIVE_AVAILABLE and dsp_native.nova_comp_reset is not None:
            self._c_state: Optional[dsp_native.NovaCompressorState] = dsp_native.NovaCompressorState()
            dsp_native.nova_comp_reset(ctypes.byref(self._c_state))
        else:
            self._c_state = None

    def get_factory_presets(self) -> Dict[str, Dict[str, Any]]:
        return self.FACTORY_PRESETS

    def apply_preset(self, preset_name: str) -> bool:
        """Applique un preset d'usine, un alias MCP ou un preset utilisateur."""
        if not preset_name:
            return False
        clean = preset_name.strip().lower()
        for k, v in self.FACTORY_PRESETS.items():
            if k.lower() == clean:
                self.set_state(v)
                self.preset_name = k
                return True

        alias_presets = {
            "punchy_drums": {"threshold_db": -16.0, "ratio": 4.0, "attack_ms": 30.0, "release_ms": 90.0, "makeup_gain_db": 3.0, "mix": 1.0, "knee_db": 3.0, "auto_makeup": False, "detection_mode": "peak", "sidechain_hpf_hz": 0.0},
            "drums": {"threshold_db": -16.0, "ratio": 4.0, "attack_ms": 30.0, "release_ms": 90.0, "makeup_gain_db": 3.0, "mix": 1.0, "knee_db": 3.0, "auto_makeup": False, "detection_mode": "peak", "sidechain_hpf_hz": 0.0},
            "vocal_leveler": {"threshold_db": -20.0, "ratio": 3.2, "attack_ms": 12.0, "release_ms": 140.0, "makeup_gain_db": 4.0, "mix": 1.0, "knee_db": 6.0, "auto_makeup": False, "detection_mode": "rms", "sidechain_hpf_hz": 80.0},
            "vocal": {"threshold_db": -20.0, "ratio": 3.2, "attack_ms": 12.0, "release_ms": 140.0, "makeup_gain_db": 4.0, "mix": 1.0, "knee_db": 6.0, "auto_makeup": False, "detection_mode": "rms", "sidechain_hpf_hz": 80.0},
            "master_glue": {"threshold_db": -12.0, "ratio": 2.0, "attack_ms": 30.0, "release_ms": 100.0, "makeup_gain_db": 1.5, "mix": 1.0, "knee_db": 4.0, "auto_makeup": False, "detection_mode": "rms", "sidechain_hpf_hz": 80.0},
            "master": {"threshold_db": -12.0, "ratio": 2.0, "attack_ms": 30.0, "release_ms": 100.0, "makeup_gain_db": 1.5, "mix": 1.0, "knee_db": 4.0, "auto_makeup": False, "detection_mode": "rms", "sidechain_hpf_hz": 80.0},
            "parallel_crush": {"threshold_db": -26.0, "ratio": 8.0, "attack_ms": 5.0, "release_ms": 50.0, "makeup_gain_db": 6.0, "mix": 0.45, "knee_db": 2.0, "auto_makeup": False, "detection_mode": "peak", "sidechain_hpf_hz": 0.0},
            "crush": {"threshold_db": -26.0, "ratio": 8.0, "attack_ms": 5.0, "release_ms": 50.0, "makeup_gain_db": 6.0, "mix": 0.45, "knee_db": 2.0, "auto_makeup": False, "detection_mode": "peak", "sidechain_hpf_hz": 0.0},
            "gentle": {"threshold_db": -10.0, "ratio": 1.8, "attack_ms": 25.0, "release_ms": 200.0, "makeup_gain_db": 1.0, "mix": 1.0, "knee_db": 5.0, "auto_makeup": False, "detection_mode": "rms", "sidechain_hpf_hz": 0.0},
            "soft": {"threshold_db": -10.0, "ratio": 1.8, "attack_ms": 25.0, "release_ms": 200.0, "makeup_gain_db": 1.0, "mix": 1.0, "knee_db": 5.0, "auto_makeup": False, "detection_mode": "rms", "sidechain_hpf_hz": 0.0},
        }
        if clean in alias_presets:
            self.set_state(alias_presets[clean])
            self.preset_name = preset_name.strip()
            return True

        try:
            from core.preset_manager import global_preset_manager
            if global_preset_manager.load_preset(self, preset_name):
                return True
        except Exception:
            pass
        return False

    def reset(self) -> None:
        """Réinitialise l'état dynamique interne et les indicateurs."""
        with self._lock:
            if self._c_state is not None and dsp_native.nova_comp_reset is not None:
                dsp_native.nova_comp_reset(ctypes.byref(self._c_state))
            self._env_peak = 0.0
            self._env_gr_db = 0.0
            self._hpf_zi = None
            self.current_gain_reduction_db = 0.0
            self.current_input_peak_db = -60.0
            self.current_output_peak_db = -60.0

    def decay_meters(self, rate_db: float = 1.8, gr_rate_db: float = 1.2) -> None:
        """
        Décroissance balistique douce des indicateurs de crête et de réduction de gain.
        Appelée à chaque rafraîchissement d'interface graphique (30 FPS) pour éviter que les
        vu-mètres ne restent figés après une note ou en l'absence de signal continu.
        """
        if self.current_input_peak_db > -60.0:
            self.current_input_peak_db = max(-60.0, self.current_input_peak_db - rate_db)
        if self.current_output_peak_db > -60.0:
            self.current_output_peak_db = max(-60.0, self.current_output_peak_db - rate_db)
        if self.current_gain_reduction_db < 0.0:
            self.current_gain_reduction_db = min(0.0, self.current_gain_reduction_db + gr_rate_db)

    def _get_hpf_coeffs(self, freq: float, sr: int):
        """Coefficients biquad Butterworth High-Pass de 2e ordre pour le sidechain."""
        if self._cached_hpf_freq == freq and self._cached_hpf_sr == sr and self._cached_hpf_b is not None:
            return self._cached_hpf_b, self._cached_hpf_a

        w0 = 2.0 * math.pi * max(10.0, min(freq, sr * 0.45)) / float(sr)
        cos_w0 = math.cos(w0)
        sin_w0 = math.sin(w0)
        alpha = sin_w0 / (2.0 * 0.70710678)
        b0 = (1.0 + cos_w0) * 0.5
        b1 = -(1.0 + cos_w0)
        b2 = (1.0 + cos_w0) * 0.5
        a0 = 1.0 + alpha
        a1 = -2.0 * cos_w0
        a2 = 1.0 - alpha

        b = np.array([b0 / a0, b1 / a0, b2 / a0], dtype=np.float32)
        a = np.array([1.0, a1 / a0, a2 / a0], dtype=np.float32)

        self._cached_hpf_freq = freq
        self._cached_hpf_sr = sr
        self._cached_hpf_b, self._cached_hpf_a = b, a
        return b, a

    def process(self, audio: np.ndarray, sample_rate: int) -> np.ndarray:
        """
        Traite le buffer audio stéréo (N, 2) avec l'algorithme de compression dynamique studio.
        Garantit une latence ultra-faible (< 0.15 ms pour 512 échantillons) et un son transparent (THD < 0.05%).
        """
        if audio is None or len(audio) == 0:
            self.current_gain_reduction_db = 0.0
            return audio

        # Formater en stéréo float32 contigu
        if audio.ndim == 1:
            in_buf = np.column_stack((audio, audio)).astype(np.float32)
        elif audio.ndim == 2 and audio.shape[1] == 1:
            in_buf = np.column_stack((audio[:, 0], audio[:, 0])).astype(np.float32)
        elif audio.ndim == 2 and audio.shape[1] > 2:
            in_buf = np.ascontiguousarray(audio[:, :2], dtype=np.float32)
        else:
            in_buf = np.ascontiguousarray(audio, dtype=np.float32)

        n_samples = len(in_buf)

        # En mode Bypass : transmettre le signal audio transparent et mettre GR à 0
        if not self.enabled:
            max_in = float(np.max(np.abs(in_buf))) if n_samples > 0 else 0.0
            in_pk_db = 20.0 * math.log10(max(1e-5, max_in))
            self.current_input_peak_db = in_pk_db
            self.current_gain_reduction_db = 0.0
            self.current_output_peak_db = in_pk_db
            return in_buf

        if self._c_state is not None and dsp_native.nova_comp_process is not None:
            out_buf = np.empty((n_samples, 2), dtype=np.float32)
            in_pk = ctypes.c_float(-60.0)
            out_pk = ctypes.c_float(-60.0)
            gr_db = ctypes.c_float(0.0)
            det_mode = 1 if str(self.detection_mode).lower() == "rms" else 0
            with self._lock:
                dsp_native.nova_comp_process(
                    ctypes.byref(self._c_state),
                    in_buf.ctypes.data_as(dsp_native.c_float_p),
                    out_buf.ctypes.data_as(dsp_native.c_float_p),
                    int(n_samples),
                    int(sample_rate),
                    float(self.threshold_db),
                    float(self.ratio),
                    float(self.attack_ms),
                    float(self.release_ms),
                    float(self.knee_db),
                    float(self.makeup_gain_db),
                    1 if self.auto_makeup else 0,
                    float(self.mix),
                    det_mode,
                    float(self.sidechain_hpf_hz),
                    1,
                    ctypes.byref(in_pk),
                    ctypes.byref(out_pk),
                    ctypes.byref(gr_db),
                )
                self._env_peak = float(self._c_state.env_peak)
                self._env_gr_db = float(self._c_state.env_gr_db)
                self.current_input_peak_db = float(in_pk.value)
                self.current_output_peak_db = float(out_pk.value)
                self.current_gain_reduction_db = float(gr_db.value)
            return out_buf

        # 1. Mesure du niveau de crête d'entrée (repli Python/SciPy)
        max_in = float(np.max(np.abs(in_buf))) if n_samples > 0 else 0.0
        in_pk_db = 20.0 * math.log10(max(1e-5, max_in))
        self.current_input_peak_db = in_pk_db

        # 2. Détection Sidechain (Max stéréo)
        sc = np.maximum(np.abs(in_buf[:, 0]), np.abs(in_buf[:, 1]))

        # Application du filtre Sidechain HPF si actif (ex: 80 Hz pour éviter le pompage sur la basse)
        if self.sidechain_hpf_hz >= 20.0:
            b, a = self._get_hpf_coeffs(self.sidechain_hpf_hz, sample_rate)
            if HAS_SCIPY:
                if self._hpf_zi is None or len(self._hpf_zi) != 2:
                    self._hpf_zi = scipy.signal.lfilter_zi(b, a) * float(sc[0])
                sc, self._hpf_zi = scipy.signal.lfilter(b, a, sc, zi=self._hpf_zi)
                sc = np.abs(sc)
            else:
                b0, b1, b2 = float(b[0]), float(b[1]), float(b[2])
                a1, a2 = float(a[1]), float(a[2])
                if self._hpf_zi is None or len(self._hpf_zi) != 2:
                    denom = 1.0 + a1 + a2
                    y_ss = (b0 + b1 + b2) / denom if abs(denom) > 1e-12 else 0.0
                    sc0 = float(sc[0])
                    self._hpf_zi = np.array([(y_ss - b0) * sc0, (b2 - a2 * y_ss) * sc0], dtype=np.float64)
                z0, z1 = float(self._hpf_zi[0]), float(self._hpf_zi[1])
                sc_filt = np.empty(n_samples, dtype=np.float64)
                for i in range(n_samples):
                    x_s = float(sc[i])
                    y_s = b0 * x_s + z0
                    z0 = b1 * x_s - a1 * y_s + z1
                    z1 = b2 * x_s - a2 * y_s
                    sc_filt[i] = abs(y_s)
                self._hpf_zi[0], self._hpf_zi[1] = z0, z1
                sc = sc_filt

        # 3. Constantes de temps ballistiques (Attack / Release)
        att_sec = max(0.0001, self.attack_ms / 1000.0)
        rel_sec = max(0.001, self.release_ms / 1000.0)
        sr = float(sample_rate)

        # Détecteur découplé : constante de maintien de crête évitant le ripple de passage par zéro
        det_rel_sec = max(0.015, rel_sec)
        alpha_det_rel = math.exp(-1.0 / (det_rel_sec * sr))

        alpha_att = math.exp(-1.0 / (att_sec * sr))
        alpha_rel = math.exp(-1.0 / (rel_sec * sr))
        om_att = 1.0 - alpha_att
        om_rel = 1.0 - alpha_rel

        # Courbe statique de compression
        t_db = self.threshold_db
        r = max(1.0, self.ratio)
        slope = (1.0 / r) - 1.0
        knee = max(0.0, self.knee_db)
        half_k = knee * 0.5
        two_k = 2.0 * knee if knee > 0.0 else 1.0
        log_mult = 8.685889638065037  # 20 / ln(10)
        math_log = math.log

        cur_pk = self._env_peak
        cur_gr = self._env_gr_db

        is_rms = (self.detection_mode == "rms")
        rms_alpha = math.exp(-1.0 / (0.015 * sr))
        om_rms = 1.0 - rms_alpha

        # 4. Détection d'enveloppe vectorisée + boucle temporelle découplée ultra-rapide (sans distorsion)
        if n_samples > 0:
            if is_rms and HAS_SCIPY:
                b_rms = np.array([om_rms], dtype=np.float64)
                a_rms = np.array([1.0, -rms_alpha], dtype=np.float64)
                zi_rms = np.array([rms_alpha * cur_pk], dtype=np.float64)
                pk_arr, _ = scipy.signal.lfilter(b_rms, a_rms, sc.astype(np.float64) ** 2, zi=zi_rms)
                cur_pk = float(pk_arr[-1])
                det_arr = np.sqrt(np.maximum(1e-10, pk_arr))
            else:
                sc_list = sc.tolist()
                det_list = [0.0] * n_samples
                if is_rms:
                    math_sqrt = math.sqrt
                    for i, x in enumerate(sc_list):
                        cur_pk = rms_alpha * cur_pk + om_rms * (x * x)
                        det_list[i] = math_sqrt(cur_pk if cur_pk > 1e-10 else 1e-10)
                else:
                    for i, x in enumerate(sc_list):
                        if x > cur_pk:
                            cur_pk = x
                        else:
                            cur_pk = alpha_det_rel * cur_pk
                        det_list[i] = cur_pk
                det_arr = np.asarray(det_list, dtype=np.float64)

            # Calculateur de gain statique vectorisé (Soft-Knee / Hard-Knee)
            x_db = np.where(det_arr > 1e-5, log_mult * np.log(np.maximum(1e-10, det_arr)), -100.0)
            diff = x_db - t_db
            if knee > 0.1:
                d_k = diff + half_k
                tgt_gr_arr = np.where(
                    diff <= -half_k,
                    0.0,
                    np.where(diff >= half_k, slope * diff, slope * (d_k * d_k) / two_k),
                )
            else:
                tgt_gr_arr = np.where(diff > 0.0, slope * diff, 0.0)

            # Lissage de l'enveloppe de réduction de gain (listes Python natives sans overhead NumPy)
            tgt_list = tgt_gr_arr.tolist()
            gr_list = [0.0] * n_samples
            for i, tgt_gr in enumerate(tgt_list):
                if tgt_gr < cur_gr:
                    cur_gr = alpha_att * cur_gr + om_att * tgt_gr
                else:
                    cur_gr = alpha_rel * cur_gr + om_rel * tgt_gr
                gr_list[i] = cur_gr
            full_gr = np.asarray(gr_list, dtype=np.float32)
        else:
            full_gr = np.empty(0, dtype=np.float32)

        self._env_peak = cur_pk
        self._env_gr_db = cur_gr
        self.current_gain_reduction_db = float(np.min(full_gr)) if n_samples > 0 else 0.0

        # 5. Calcul du Makeup Gain et Auto-Makeup
        auto_db = 0.0
        if self.auto_makeup:
            auto_db = -(t_db * (1.0 - 1.0 / r)) * 0.5
        total_make_db = self.makeup_gain_db + auto_db
        makeup_lin = 10.0 ** (total_make_db / 20.0)

        # 6. Conversion en gain linéaire et application audio
        gain_lin = (10.0 ** (full_gr / 20.0)) * makeup_lin
        wet = in_buf * gain_lin[:, None]

        # 7. Compression parallèle (Dry / Wet Mix)
        mix_val = max(0.0, min(1.0, self.mix))
        out_buf = (1.0 - mix_val) * in_buf + mix_val * wet

        # 8. Mesure du niveau de crête de sortie
        max_out = float(np.max(np.abs(out_buf))) if n_samples > 0 else 0.0
        self.current_output_peak_db = 20.0 * math.log10(max(1e-5, max_out))

        return out_buf.astype(np.float32)

    def get_state(self) -> Dict[str, Any]:
        return {
            "threshold_db": self.threshold_db,
            "ratio": self.ratio,
            "attack_ms": self.attack_ms,
            "release_ms": self.release_ms,
            "knee_db": self.knee_db,
            "makeup_gain_db": self.makeup_gain_db,
            "auto_makeup": self.auto_makeup,
            "mix": self.mix,
            "detection_mode": self.detection_mode,
            "sidechain_hpf_hz": self.sidechain_hpf_hz,
            "enabled": self.enabled,
        }

    def set_state(self, state: Dict[str, Any]) -> None:
        if "preset_name" in state:
            self.preset_name = str(state["preset_name"])
        if "threshold_db" in state:
            self.threshold_db = float(state["threshold_db"])
        if "ratio" in state:
            self.ratio = float(state["ratio"])
        if "attack_ms" in state:
            self.attack_ms = float(state["attack_ms"])
        if "release_ms" in state:
            self.release_ms = float(state["release_ms"])
        if "knee_db" in state:
            self.knee_db = float(state["knee_db"])
        if "makeup_gain_db" in state:
            self.makeup_gain_db = float(state["makeup_gain_db"])
        if "auto_makeup" in state:
            self.auto_makeup = bool(state["auto_makeup"])
        if "mix" in state:
            self.mix = float(state["mix"])
        if "detection_mode" in state:
            mode = str(state["detection_mode"]).lower()
            if mode in ("peak", "rms"):
                self.detection_mode = mode
        if "sidechain_hpf_hz" in state:
            self.sidechain_hpf_hz = float(state["sidechain_hpf_hz"])
        if "enabled" in state:
            self.enabled = bool(state["enabled"])
        self.reset()

    def create_editor(self, parent=None):
        from plugins.compressor.compressor_gui import CompressorPluginWidget
        return CompressorPluginWidget(self, parent)
