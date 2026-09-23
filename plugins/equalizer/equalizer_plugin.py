"""
plugins/equalizer/equalizer_plugin.py - Moteur DSP et modèle pour l'Égaliseur Paramétrique.

Permet de configurer 3, 10, 12 ou 24 bandes (ou nombre personnalisé) avec filtres
biquad standards (RBJ Audio EQ Cookbook) : Peaking, Low-Shelf, High-Shelf, High-Pass, Low-Pass.
Maintient l'état du filtre (Direct Form II Transposed) entre les blocs audio pour éviter
tout clic ou discontinuité sonore lors du traitement temps réel.
"""
from typing import List, Dict, Any, Optional, Tuple
import numpy as np

try:
    from scipy.signal import lfilter, lfilter_zi
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False

from plugins.base import BasePlugin
from plugins.registry import register_plugin


class EqualizerBand:
    """Représente une bande individuelle de l'égaliseur"""

    def __init__(
        self,
        band_id: int,
        frequency: float = 1000.0,
        gain_db: float = 0.0,
        q: float = 1.0,
        filter_type: str = "peaking",
        enabled: bool = True
    ):
        self.band_id = band_id
        self.frequency = float(frequency)  # 20 Hz à 20000 Hz
        self.gain_db = float(gain_db)      # -24 dB à +24 dB
        self.q = float(q)                  # 0.1 à 10.0
        self.filter_type = filter_type      # "peaking", "low_shelf", "high_shelf", "low_pass", "high_pass"
        self.enabled = enabled

        # Coefficients de biquad mis en cache
        self._cached_sr: Optional[int] = None
        self._cached_b: Optional[np.ndarray] = None
        self._cached_a: Optional[np.ndarray] = None

        # État des mémoires de délai pour le lissage des blocs audio (stéréo)
        self.zi_left: Optional[np.ndarray] = None
        self.zi_right: Optional[np.ndarray] = None

    def reset_state(self):
        self.zi_left = None
        self.zi_right = None

    def compute_coefficients(self, sample_rate: int) -> Tuple[np.ndarray, np.ndarray]:
        """
        Calcule les coefficients de filtre IIR biquad d'ordre 2 selon le Cookbook de RBJ.
        Retourne (b, a) normalisés avec a[0] = 1.0.
        """
        if (
            self._cached_sr == sample_rate
            and self._cached_b is not None
            and self._cached_a is not None
        ):
            return self._cached_b, self._cached_a

        # Limitation sécurisée de fréquence Nyquist
        nyquist = sample_rate * 0.499
        f0 = max(10.0, min(nyquist, self.frequency))
        q = max(0.1, min(15.0, self.q))
        gain = max(-36.0, min(36.0, self.gain_db))

        # Si le gain est nul pour les filtres d'amplification/atténuation, filtre neutre
        if abs(gain) < 0.01 and self.filter_type in ("peaking", "low_shelf", "high_shelf"):
            b = np.array([1.0, 0.0, 0.0], dtype=np.float64)
            a = np.array([1.0, 0.0, 0.0], dtype=np.float64)
            self._cached_sr = sample_rate
            self._cached_b, self._cached_a = b, a
            return b, a

        a_linear = 10.0 ** (gain / 40.0)
        w0 = 2.0 * np.pi * f0 / sample_rate
        cos_w0 = np.cos(w0)
        sin_w0 = np.sin(w0)
        alpha = sin_w0 / (2.0 * q)

        if self.filter_type == "peaking":
            b0 = 1.0 + alpha * a_linear
            b1 = -2.0 * cos_w0
            b2 = 1.0 - alpha * a_linear
            a0 = 1.0 + alpha / a_linear
            a1 = -2.0 * cos_w0
            a2 = 1.0 - alpha / a_linear

        elif self.filter_type == "low_shelf":
            two_sqrt_a_alpha = 2.0 * np.sqrt(a_linear) * alpha
            b0 = a_linear * ((a_linear + 1.0) - (a_linear - 1.0) * cos_w0 + two_sqrt_a_alpha)
            b1 = 2.0 * a_linear * ((a_linear - 1.0) - (a_linear + 1.0) * cos_w0)
            b2 = a_linear * ((a_linear + 1.0) - (a_linear - 1.0) * cos_w0 - two_sqrt_a_alpha)
            a0 = (a_linear + 1.0) + (a_linear - 1.0) * cos_w0 + two_sqrt_a_alpha
            a1 = -2.0 * ((a_linear - 1.0) + (a_linear + 1.0) * cos_w0)
            a2 = (a_linear + 1.0) + (a_linear - 1.0) * cos_w0 - two_sqrt_a_alpha

        elif self.filter_type == "high_shelf":
            two_sqrt_a_alpha = 2.0 * np.sqrt(a_linear) * alpha
            b0 = a_linear * ((a_linear + 1.0) + (a_linear - 1.0) * cos_w0 + two_sqrt_a_alpha)
            b1 = -2.0 * a_linear * ((a_linear - 1.0) + (a_linear + 1.0) * cos_w0)
            b2 = a_linear * ((a_linear + 1.0) + (a_linear - 1.0) * cos_w0 - two_sqrt_a_alpha)
            a0 = (a_linear + 1.0) - (a_linear - 1.0) * cos_w0 + two_sqrt_a_alpha
            a1 = 2.0 * ((a_linear - 1.0) - (a_linear + 1.0) * cos_w0)
            a2 = (a_linear + 1.0) - (a_linear - 1.0) * cos_w0 - two_sqrt_a_alpha

        elif self.filter_type == "low_pass":
            b0 = (1.0 - cos_w0) / 2.0
            b1 = 1.0 - cos_w0
            b2 = (1.0 - cos_w0) / 2.0
            a0 = 1.0 + alpha
            a1 = -2.0 * cos_w0
            a2 = 1.0 - alpha

        elif self.filter_type == "high_pass":
            b0 = (1.0 + cos_w0) / 2.0
            b1 = -(1.0 + cos_w0)
            b2 = (1.0 + cos_w0) / 2.0
            a0 = 1.0 + alpha
            a1 = -2.0 * cos_w0
            a2 = 1.0 - alpha

        else:
            # Type inconnu -> passe-tout neutre
            b0, b1, b2 = 1.0, 0.0, 0.0
            a0, a1, a2 = 1.0, 0.0, 0.0

        b = np.array([b0 / a0, b1 / a0, b2 / a0], dtype=np.float64)
        a = np.array([1.0, a1 / a0, a2 / a0], dtype=np.float64)

        self._cached_sr = sample_rate
        self._cached_b, self._cached_a = b, a
        return b, a

    def invalidate_cache(self):
        self._cached_sr = None
        self._cached_b = None
        self._cached_a = None

    def process(self, audio: np.ndarray, sample_rate: int) -> np.ndarray:
        """Applique le filtre biquad sur le buffer stéréo (N, 2)"""
        if not self.enabled:
            return audio

        b, a = self.compute_coefficients(sample_rate)

        # Si filtre neutre
        if np.allclose(b, [1.0, 0.0, 0.0]) and np.allclose(a, [1.0, 0.0, 0.0]):
            return audio

        if HAS_SCIPY:
            # Initialisation de l'état zi si absent
            if self.zi_left is None:
                self.zi_left = lfilter_zi(b, a) * audio[0, 0]
                self.zi_right = lfilter_zi(b, a) * audio[0, 1]

            out_left, self.zi_left = lfilter(b, a, audio[:, 0], zi=self.zi_left)
            out_right, self.zi_right = lfilter(b, a, audio[:, 1], zi=self.zi_right)
            return np.column_stack((out_left, out_right)).astype(np.float32)
        else:
            # Fallback pure NumPy si SciPy n'est pas présent
            return self._fallback_biquad(audio, b, a)

    def _fallback_biquad(self, audio: np.ndarray, b: np.ndarray, a: np.ndarray) -> np.ndarray:
        """Implémentation Direct Form II Transposed pure NumPy"""
        n_samples = len(audio)
        out = np.zeros_like(audio)

        # États mémoires d1, d2
        if self.zi_left is None:
            self.zi_left = np.zeros(2, dtype=np.float64)
            self.zi_right = np.zeros(2, dtype=np.float64)

        b0, b1, b2 = b[0], b[1], b[2]
        a1, a2 = a[1], a[2]

        for ch, zi in enumerate([self.zi_left, self.zi_right]):
            d1, d2 = zi[0], zi[1]
            x = audio[:, ch]
            y = np.zeros(n_samples, dtype=np.float32)
            for i in range(n_samples):
                xi = x[i]
                yi = b0 * xi + d1
                d1 = b1 * xi - a1 * yi + d2
                d2 = b2 * xi - a2 * yi
                y[i] = yi
            out[:, ch] = y
            zi[0], zi[1] = d1, d2

        return out

    def to_dict(self) -> Dict[str, Any]:
        return {
            "band_id": self.band_id,
            "frequency": self.frequency,
            "gain_db": self.gain_db,
            "q": self.q,
            "filter_type": self.filter_type,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EqualizerBand":
        return cls(
            band_id=data.get("band_id", 1),
            frequency=data.get("frequency", 1000.0),
            gain_db=data.get("gain_db", 0.0),
            q=data.get("q", 1.0),
            filter_type=data.get("filter_type", "peaking"),
            enabled=data.get("enabled", True),
        )


@register_plugin(
    plugin_type_id="novadaw.equalizer",
    name="Égaliseur Paramétrique",
    category="eq",
    icon="📊",
    description="Égaliseur paramétrique modulaire professionnel (3, 10, 12, 24 bandes).",
    is_default=True
)
class EqualizerPlugin(BasePlugin):
    """
    Plugin d'égaliseur paramétrique complet pour NovaDAW.
    Permet à l'utilisateur de choisir entre 3, 10, 12 ou 24 bandes ou
    de configurer librement chaque filtre.
    """

    def __init__(
        self,
        instance_id: Optional[str] = None,
        band_count: int = 10
    ):
        super().__init__(
            plugin_type_id="novadaw.equalizer",
            name="Égaliseur Paramétrique",
            category="eq",
            icon="📊",
            instance_id=instance_id
        )
        self.master_gain_db: float = 0.0
        self.bands: List[EqualizerBand] = []
        self.set_band_count(band_count)

    def set_band_count(self, count: int):
        """
        Configure l'égaliseur selon le nombre de bandes souhaité (3, 10, 12, 24 bandes).
        Préserve les réglages existants si compatibles, sinon génère les fréquences standards.
        """
        count = max(1, min(32, count))
        frequencies, types, default_qs = self._get_default_frequencies_for_count(count)

        new_bands = []
        for i in range(count):
            freq = frequencies[i]
            ftype = types[i]
            q_val = default_qs[i]
            gain = 0.0

            # Si on a déjà une bande existante à cet index, reprendre son gain si pertinent
            if i < len(self.bands):
                gain = self.bands[i].gain_db

            band = EqualizerBand(
                band_id=i + 1,
                frequency=freq,
                gain_db=gain,
                q=q_val,
                filter_type=ftype,
                enabled=True
            )
            new_bands.append(band)

        self.bands = new_bands
        self.reset()

    @staticmethod
    def _get_default_frequencies_for_count(count: int) -> Tuple[List[float], List[str], List[float]]:
        """Génère la répartition optimale des fréquences et types de filtres selon le nombre de bandes"""
        if count == 3:
            freqs = [100.0, 1000.0, 8000.0]
            types = ["low_shelf", "peaking", "high_shelf"]
            qs = [0.707, 1.0, 0.707]
        elif count == 10:
            # Norme ISO 10 bandes octave standard
            freqs = [31.25, 62.5, 125.0, 250.0, 500.0, 1000.0, 2000.0, 4000.0, 8000.0, 16000.0]
            types = ["low_shelf"] + ["peaking"] * 8 + ["high_shelf"]
            qs = [1.414] * 10
        elif count == 12:
            # 12 bandes studio semi-paramétrique
            freqs = [32.0, 64.0, 125.0, 250.0, 500.0, 1000.0, 2000.0, 4000.0, 6000.0, 8000.0, 12000.0, 16000.0]
            types = ["low_shelf"] + ["peaking"] * 10 + ["high_shelf"]
            qs = [1.414] * 12
        elif count == 24:
            # 24 bandes 1/3 octave mastering haute résolution
            freqs = [
                25.0, 31.5, 40.0, 50.0, 63.0, 80.0, 100.0, 125.0, 160.0, 200.0, 250.0, 315.0,
                400.0, 500.0, 630.0, 800.0, 1000.0, 1250.0, 1600.0, 2000.0, 2500.0, 3150.0,
                4000.0, 8000.0
            ]
            types = ["low_shelf"] + ["peaking"] * 22 + ["high_shelf"]
            qs = [2.0] * 24
        else:
            # Répartition logarithmique générique
            freqs = np.geomspace(30.0, 18000.0, count).tolist()
            types = ["low_shelf"] + ["peaking"] * (count - 2) + ["high_shelf"] if count >= 2 else ["peaking"]
            qs = [1.414] * count

        return freqs, types, qs

    def process(self, audio: np.ndarray, sample_rate: int) -> np.ndarray:
        """Traite le buffer audio au travers de toutes les bandes actives de l'égaliseur"""
        if not self.enabled or audio is None or len(audio) == 0:
            return audio

        # Assurer format float32 2D stéréo
        if audio.ndim == 1:
            out = np.column_stack((audio, audio)).astype(np.float32)
        else:
            out = audio.copy().astype(np.float32)

        # Passer le signal séquentiellement à travers chaque bande
        for band in self.bands:
            if band.enabled:
                out = band.process(out, sample_rate)

        # Gain global de sortie
        if abs(self.master_gain_db) > 0.01:
            gain_lin = 10.0 ** (self.master_gain_db / 20.0)
            out *= gain_lin

        return out

    def reset(self) -> None:
        """Réinitialise les mémoires de filtrage de toutes les bandes"""
        for band in self.bands:
            band.reset_state()

    def compute_magnitude_response(self, frequencies: np.ndarray, sample_rate: int = 44100) -> np.ndarray:
        """
        Calcule la réponse fréquentielle cumulée (en dB) pour tracer la courbe interactive.
        frequencies: tableau 1D de fréquences en Hz (ex: 20 à 20000)
        """
        total_mag_db = np.zeros_like(frequencies, dtype=np.float64)

        if not self.enabled:
            return total_mag_db

        w = 2.0 * np.pi * frequencies / sample_rate
        z = np.exp(1j * w)
        z_inv = 1.0 / z
        z_inv2 = z_inv * z_inv

        for band in self.bands:
            if not band.enabled:
                continue
            b, a = band.compute_coefficients(sample_rate)
            num = b[0] + b[1] * z_inv + b[2] * z_inv2
            den = a[0] + a[1] * z_inv + a[2] * z_inv2
            h = num / den
            mag = np.abs(h)
            # Éviter log(0)
            mag = np.maximum(mag, 1e-6)
            total_mag_db += 20.0 * np.log10(mag)

        total_mag_db += self.master_gain_db
        return total_mag_db

    def get_state(self) -> Dict[str, Any]:
        return {
            "band_count": len(self.bands),
            "master_gain_db": self.master_gain_db,
            "bands": [b.to_dict() for b in self.bands],
        }

    def set_state(self, state: Dict[str, Any]) -> None:
        self.master_gain_db = float(state.get("master_gain_db", 0.0))
        saved_bands = state.get("bands", [])
        if saved_bands:
            self.bands = [EqualizerBand.from_dict(d) for d in saved_bands]
        elif "band_count" in state:
            self.set_band_count(int(state["band_count"]))
        self.reset()

    def create_editor(self, parent=None):
        from plugins.equalizer.equalizer_gui import EqualizerPluginWidget
        return EqualizerPluginWidget(self, parent)
