"""
plugins/guitar_amp/amp_dsp.py - Chaîne DSP d'amplificateur de guitare (Licence MIT).

Architecture studio temps réel sans clic inter-buffers :
1. Filtre d'entrée coupe-bas (~70 Hz) pour garder des graves propres avant saturation
2. Étage de saturation asymétrique lampe/diode avec Oversampling 4x continu (anti-aliasing)
3. Blocage DC continu
4. Tone Stack 3 bandes (Low 110 Hz, Mid 720 Hz, Treble 3.2 kHz) par biquads continus
5. Simulateur de Baffle (Cabinet Sim 1x12 / 4x12) commutable
6. Volume Master avec protection douce anti-clipping
"""
import math
from typing import Optional, Tuple, Any
import numpy as np

try:
    import scipy.signal as sig
    HAS_SCIPY = True
except Exception:
    HAS_SCIPY = False

from plugins.guitar.guitar_dsp import (
    _biquad_peaking,
    _biquad_lowpass,
    _biquad_highpass,
)


def _biquad_lowshelf(freq: float, gain_db: float, q: float, sr: int) -> Tuple[np.ndarray, np.ndarray]:
    """Coefficients biquad Low-Shelf (Audio EQ Cookbook)."""
    f = max(20.0, min(float(freq), sr * 0.45))
    a_lin = 10.0 ** (float(gain_db) / 40.0)
    w0 = 2.0 * math.pi * f / float(sr)
    cos_w0 = math.cos(w0)
    sin_w0 = math.sin(w0)
    alpha = sin_w0 / (2.0 * max(0.2, float(q)))
    sqrt_a = math.sqrt(a_lin)
    two_sqrt_a_alpha = 2.0 * sqrt_a * alpha

    b0 = a_lin * ((a_lin + 1.0) - (a_lin - 1.0) * cos_w0 + two_sqrt_a_alpha)
    b1 = 2.0 * a_lin * ((a_lin - 1.0) - (a_lin + 1.0) * cos_w0)
    b2 = a_lin * ((a_lin + 1.0) - (a_lin - 1.0) * cos_w0 - two_sqrt_a_alpha)
    a0 = (a_lin + 1.0) + (a_lin - 1.0) * cos_w0 + two_sqrt_a_alpha
    a1 = -2.0 * ((a_lin - 1.0) + (a_lin + 1.0) * cos_w0)
    a2 = (a_lin + 1.0) + (a_lin - 1.0) * cos_w0 - two_sqrt_a_alpha

    return (
        np.array([b0 / a0, b1 / a0, b2 / a0], dtype=np.float64),
        np.array([1.0, a1 / a0, a2 / a0], dtype=np.float64),
    )


def _biquad_highshelf(freq: float, gain_db: float, q: float, sr: int) -> Tuple[np.ndarray, np.ndarray]:
    """Coefficients biquad High-Shelf (Audio EQ Cookbook)."""
    f = max(20.0, min(float(freq), sr * 0.45))
    a_lin = 10.0 ** (float(gain_db) / 40.0)
    w0 = 2.0 * math.pi * f / float(sr)
    cos_w0 = math.cos(w0)
    sin_w0 = math.sin(w0)
    alpha = sin_w0 / (2.0 * max(0.2, float(q)))
    sqrt_a = math.sqrt(a_lin)
    two_sqrt_a_alpha = 2.0 * sqrt_a * alpha

    b0 = a_lin * ((a_lin + 1.0) + (a_lin - 1.0) * cos_w0 + two_sqrt_a_alpha)
    b1 = -2.0 * a_lin * ((a_lin - 1.0) + (a_lin + 1.0) * cos_w0)
    b2 = a_lin * ((a_lin + 1.0) + (a_lin - 1.0) * cos_w0 - two_sqrt_a_alpha)
    a0 = (a_lin + 1.0) - (a_lin - 1.0) * cos_w0 + two_sqrt_a_alpha
    a1 = 2.0 * ((a_lin - 1.0) - (a_lin + 1.0) * cos_w0)
    a2 = (a_lin + 1.0) - (a_lin - 1.0) * cos_w0 - two_sqrt_a_alpha

    return (
        np.array([b0 / a0, b1 / a0, b2 / a0], dtype=np.float64),
        np.array([1.0, a1 / a0, a2 / a0], dtype=np.float64),
    )


def _biquads_to_sos(sections) -> np.ndarray:
    """Convertit une liste de paires (b, a) d'ordre 2 en matrice SOS (N_sec, 6)."""
    rows = []
    for b, a in sections:
        rows.append([b[0], b[1], b[2], a[0], a[1], a[2]])
    return np.asarray(rows, dtype=np.float64)


def _sosfilt_stereo(sos: np.ndarray, x: np.ndarray, zi: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Applique une chaîne SOS sur un signal stéréo (N, 2) avec état continu."""
    if HAS_SCIPY:
        return sig.sosfilt(sos, x, axis=0, zi=zi)
    # Repli NumPy pur si SciPy indisponible
    n_sec = sos.shape[0]
    y = x.copy()
    new_zi = zi.copy()
    for s in range(n_sec):
        b0, b1, b2, _a0, a1, a2 = sos[s]
        for ch in range(2):
            z0 = new_zi[s, 0, ch]
            z1 = new_zi[s, 1, ch]
            col = y[:, ch]
            for i in range(len(col)):
                xi = col[i]
                yi = b0 * xi + z0
                z0 = b1 * xi - a1 * yi + z1
                z1 = b2 * xi - a2 * yi
                col[i] = yi
            new_zi[s, 0, ch] = z0
            new_zi[s, 1, ch] = z1
    return y, new_zi


class GuitarAmpDSP:
    """Moteur DSP continu stéréo d'amplificateur guitare."""

    def __init__(self, sample_rate: int = 44100):
        self.sample_rate = max(8000, int(sample_rate))
        self._prev_sample = np.zeros(2, dtype=np.float64)

        # États persistants des filtres
        self._pre_sos: Optional[np.ndarray] = None
        self._pre_zi: Optional[np.ndarray] = None

        self._aa_sos: Optional[np.ndarray] = None
        self._aa_zi: Optional[np.ndarray] = None

        self._dc_zi = np.zeros((1, 2), dtype=np.float64)

        self._tone_sos: Optional[np.ndarray] = None
        self._tone_zi: Optional[np.ndarray] = None
        self._tone_key: Optional[Tuple[Any, ...]] = None

        self._cab_sos: Optional[np.ndarray] = None
        self._cab_zi: Optional[np.ndarray] = None

        self._cached_sr: Optional[int] = None

    def reset(self) -> None:
        self._prev_sample.fill(0.0)
        if self._pre_zi is not None:
            self._pre_zi.fill(0.0)
        if self._aa_zi is not None:
            self._aa_zi.fill(0.0)
        self._dc_zi.fill(0.0)
        if self._tone_zi is not None:
            self._tone_zi.fill(0.0)
        if self._cab_zi is not None:
            self._cab_zi.fill(0.0)

    def _ensure_static_filters(self, sr: int) -> None:
        if self._cached_sr == sr and self._pre_sos is not None:
            return
        self._cached_sr = sr
        self.sample_rate = sr

        # 1. Pré-filtre d'entrée : coupe-bas 70 Hz pour propreté avant saturation
        b_hp, a_hp = _biquad_highpass(70.0, 0.707, sr)
        self._pre_sos = _biquads_to_sos([(b_hp, a_hp)])
        self._pre_zi = np.zeros((1, 2, 2), dtype=np.float64)

        # 2. Filtre anti-aliasing 4x oversampling (4e ordre à ~0.42 * sr dans le domaine 4 * sr)
        sr4 = sr * 4
        fc_aa = min(18000.0, 0.42 * sr)
        if HAS_SCIPY:
            self._aa_sos = sig.butter(4, fc_aa / (0.5 * sr4), btype="low", output="sos")
        else:
            b1, a1 = _biquad_lowpass(fc_aa, 0.541, sr4)
            b2, a2 = _biquad_lowpass(fc_aa, 1.307, sr4)
            self._aa_sos = _biquads_to_sos([(b1, a1), (b2, a2)])
        self._aa_zi = np.zeros((self._aa_sos.shape[0], 2, 2), dtype=np.float64)

        # 3. Simulateur de baffle guitare (1x12 / 4x12 combo/cab)
        cab_sections = [
            _biquad_highpass(85.0, 0.707, sr),
            _biquad_peaking(115.0, 3.0, 1.6, sr),
            _biquad_peaking(380.0, -3.5, 1.1, sr),
            _biquad_peaking(2350.0, 2.5, 1.4, sr),
            _biquad_lowpass(4800.0, 0.541, sr),
            _biquad_lowpass(4800.0, 1.307, sr),
        ]
        self._cab_sos = _biquads_to_sos(cab_sections)
        self._cab_zi = np.zeros((self._cab_sos.shape[0], 2, 2), dtype=np.float64)

    def _ensure_tone_stack(self, low_db: float, mid_db: float, treble_db: float, sr: int) -> None:
        key = (round(low_db, 2), round(mid_db, 2), round(treble_db, 2), sr)
        if key == self._tone_key and self._tone_sos is not None:
            return
        self._tone_key = key
        sections = [
            _biquad_lowshelf(110.0, low_db, 0.707, sr),
            _biquad_peaking(720.0, mid_db, 0.85, sr),
            _biquad_highshelf(3200.0, treble_db, 0.707, sr),
        ]
        self._tone_sos = _biquads_to_sos(sections)
        if self._tone_zi is None or self._tone_zi.shape[0] != 3:
            self._tone_zi = np.zeros((3, 2, 2), dtype=np.float64)

    def process_block(
        self,
        in_buf: np.ndarray,
        sample_rate: int,
        distortion: float,
        low_db: float,
        mid_db: float,
        treble_db: float,
        volume: float,
        cab_enabled: bool = True,
    ) -> np.ndarray:
        n_samples = len(in_buf)
        if n_samples == 0:
            return in_buf

        sr = max(8000, int(sample_rate))
        self._ensure_static_filters(sr)
        self._ensure_tone_stack(low_db, mid_db, treble_db, sr)

        x = in_buf.astype(np.float64, copy=False)

        # 1. Pré-filtrage coupe-bas
        x, self._pre_zi = _sosfilt_stereo(self._pre_sos, x, self._pre_zi)

        # 2. Saturation / Distorsion avec Oversampling 4x continu
        dist = max(0.0, min(1.0, float(distortion)))
        if dist > 0.004:
            ext = np.vstack([self._prev_sample[None, :], x])
            diff = ext[1:] - ext[:-1]
            x4 = np.empty((n_samples * 4, 2), dtype=np.float64)
            x4[0::4] = ext[:-1] + 0.25 * diff
            x4[1::4] = ext[:-1] + 0.50 * diff
            x4[2::4] = ext[:-1] + 0.75 * diff
            x4[3::4] = ext[1:]
            self._prev_sample = x[-1].copy()

            # Courbe tube asymétrique à 2 étages doux
            drive = 1.0 + (dist ** 1.65) * 30.0
            bias = 0.09 * dist
            # Étage préamp
            y4 = np.tanh(x4 * drive + bias) - math.tanh(bias)
            # Légère compression harmonique de puissance
            y4 = np.tanh(y4 * (1.0 + 0.65 * dist))
            # Compensation automatique du niveau pour garder un volume perçu équilibré
            comp = 1.0 / (1.0 + 0.52 * ((drive - 1.0) ** 0.36))
            y4 *= comp

            # Filtrage anti-aliasing 4x puis décimation
            y4_filt, self._aa_zi = _sosfilt_stereo(self._aa_sos, y4, self._aa_zi)
            x = y4_filt[3::4]

            # DC Blocker continu
            if HAS_SCIPY:
                x, self._dc_zi = sig.lfilter(
                    np.array([1.0, -1.0], dtype=np.float64),
                    np.array([1.0, -0.996], dtype=np.float64),
                    x,
                    axis=0,
                    zi=self._dc_zi,
                )
        else:
            self._prev_sample = x[-1].copy()

        # 3. Tone Stack (Low / Mid / Treble)
        x, self._tone_zi = _sosfilt_stereo(self._tone_sos, x, self._tone_zi)

        # 4. Simulateur de Baffle (Cabinet Sim)
        if cab_enabled:
            x, self._cab_zi = _sosfilt_stereo(self._cab_sos, x, self._cab_zi)

        # 5. Master Volume & protection douce anti-clipping
        out = x * max(0.0, float(volume))
        peak = float(np.max(np.abs(out)))
        if peak > 0.98:
            out = np.tanh(out * 0.90) * 1.05

        return out.astype(np.float32)
