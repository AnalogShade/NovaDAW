"""
plugins/guitar_amp/amp_dsp.py - Chaîne DSP d'amplificateur de guitare (Licence MIT).

Architecture studio temps réel sans clic inter-buffers :
1. Filtre d'entrée coupe-bas (~70 Hz) pour garder des graves propres avant saturation
2. Étage de saturation lampe multi-étages (3 triodes en cascade avec bias dynamique,
   couplage inter-étages et Morsure haute-saturation) avec Oversampling 4x continu
3. Auto-Gain RMS continu à prior analytique : maintient le volume perçu constant
   quel que soit le réglage de distorsion et le niveau d'entrée
4. Blocage DC continu
5. Tone Stack 3 bandes (Low 110 Hz, Mid 720 Hz, Treble 3.2 kHz) par biquads continus
6. Simulateur de Baffle (Cabinet Sim 1x12 / 4x12) commutable
7. Volume Master avec protection douce anti-clipping
"""
import ctypes
import math
from typing import Optional, Tuple, Any
import numpy as np

from core import dsp_native

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


def _onepole_lowpass(freq: float, sr: int) -> Tuple[np.ndarray, np.ndarray]:
    """Coefficients 1er ordre passe-bas (b, a) compatibles SOS."""
    fc = max(5.0, min(float(freq), sr * 0.45))
    p = math.exp(-2.0 * math.pi * fc / float(sr))
    return (
        np.array([1.0 - p, 0.0, 0.0], dtype=np.float64),
        np.array([1.0, -p, 0.0], dtype=np.float64),
    )


def _onepole_highpass(freq: float, sr: int) -> Tuple[np.ndarray, np.ndarray]:
    """Coefficients 1er ordre passe-haut (b, a) compatibles SOS."""
    fc = max(5.0, min(float(freq), sr * 0.45))
    p = math.exp(-2.0 * math.pi * fc / float(sr))
    g = 0.5 * (1.0 + p)
    return (
        np.array([g, -g, 0.0], dtype=np.float64),
        np.array([1.0, -p, 0.0], dtype=np.float64),
    )


def _biquads_to_sos(sections) -> np.ndarray:
    """Convertit une liste de paires (b, a) d'ordre <= 2 en matrice SOS (N_sec, 6)."""
    rows = []
    for b, a in sections:
        b0 = float(b[0]) if len(b) > 0 else 0.0
        b1 = float(b[1]) if len(b) > 1 else 0.0
        b2 = float(b[2]) if len(b) > 2 else 0.0
        a0 = float(a[0]) if len(a) > 0 else 1.0
        a1 = float(a[1]) if len(a) > 1 else 0.0
        a2 = float(a[2]) if len(a) > 2 else 0.0
        rows.append([b0, b1, b2, a0, a1, a2])
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


def _onepole_env_1d(alpha: float, x: np.ndarray, zi: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Suiveur d'enveloppe exponentiel 1er ordre 1D continu (y[n] = alpha*x[n] + (1-alpha)*y[n-1])."""
    if HAS_SCIPY:
        return sig.lfilter(
            np.array([alpha], dtype=np.float64),
            np.array([1.0, -(1.0 - alpha)], dtype=np.float64),
            x,
            zi=zi,
        )
    y = np.empty_like(x, dtype=np.float64)
    state = float(zi[0])
    decay = 1.0 - alpha
    for i in range(len(x)):
        state = alpha * float(x[i]) + decay * state
        y[i] = state
    return y, np.array([state], dtype=np.float64)


def _triode_asym(u: np.ndarray, pos_lim: float, neg_lim: float) -> np.ndarray:
    """
    Courbe de saturation asymétrique de triode (12AX7) C2-continue en 0.
    Compression douce par conduction de grille sur l'alternance positive (pos_lim)
    et coupure plaque asymétrique sur l'alternance négative (neg_lim), générant
    des harmoniques paires et impaires riches sans discontinuité.
    """
    lim = np.where(u >= 0.0, pos_lim, neg_lim)
    s = u / lim
    return u / np.sqrt(1.0 + s * s)


class GuitarAmpDSP:
    """Moteur DSP continu stéréo d'amplificateur guitare."""

    def __init__(self, sample_rate: int = 44100):
        self.sample_rate = max(8000, int(sample_rate))
        self._prev_sample = np.zeros(2, dtype=np.float64)

        # États persistants des filtres
        self._pre_sos: Optional[np.ndarray] = None
        self._pre_zi: Optional[np.ndarray] = None

        self._tight_sos: Optional[np.ndarray] = None
        self._tight_zi: Optional[np.ndarray] = None
        self._tight_key: Optional[Tuple[Any, ...]] = None

        self._inter_sos: Optional[np.ndarray] = None
        self._inter1_zi: Optional[np.ndarray] = None
        self._inter2_zi: Optional[np.ndarray] = None

        self._sag_sos: Optional[np.ndarray] = None
        self._sag1_zi: Optional[np.ndarray] = None
        self._sag2_zi: Optional[np.ndarray] = None
        self._sag3_zi: Optional[np.ndarray] = None

        self._aa_sos: Optional[np.ndarray] = None
        self._aa_zi: Optional[np.ndarray] = None

        self._dc_sos: Optional[np.ndarray] = None
        self._dc_zi: Optional[np.ndarray] = None

        # Suiveurs d'enveloppe RMS pour compensation automatique du volume (Auto-Gain)
        self._env_alpha: float = 0.0003
        self._env_in2_zi = np.zeros(1, dtype=np.float64)
        self._env_out2_zi = np.zeros(1, dtype=np.float64)
        self._dist_active: bool = False

        self._tone_sos: Optional[np.ndarray] = None
        self._tone_zi: Optional[np.ndarray] = None
        self._tone_key: Optional[Tuple[Any, ...]] = None

        self._cab_sos: Optional[np.ndarray] = None
        self._cab_zi: Optional[np.ndarray] = None

        self._cached_sr: Optional[int] = None
        self._c_in_pk = ctypes.c_float(-60.0)
        self._c_out_pk = ctypes.c_float(-60.0)
        self._c_in_pk_p = ctypes.byref(self._c_in_pk)
        self._c_out_pk_p = ctypes.byref(self._c_out_pk)
        self._native = (
            dsp_native.nova_amp_create(self.sample_rate)
            if dsp_native.NATIVE_AVAILABLE and dsp_native.nova_amp_create is not None
            else None
        )

    def __del__(self):
        native = getattr(self, "_native", None)
        if native is not None and dsp_native is not None and dsp_native.nova_amp_destroy is not None:
            try:
                dsp_native.nova_amp_destroy(native)
            except Exception:
                pass
            self._native = None

    def _reset_distortion_states(self) -> None:
        if self._tight_zi is not None:
            self._tight_zi.fill(0.0)
        if self._inter1_zi is not None:
            self._inter1_zi.fill(0.0)
        if self._inter2_zi is not None:
            self._inter2_zi.fill(0.0)
        if self._sag1_zi is not None:
            self._sag1_zi.fill(0.0)
        if self._sag2_zi is not None:
            self._sag2_zi.fill(0.0)
        if self._sag3_zi is not None:
            self._sag3_zi.fill(0.0)
        if self._aa_zi is not None:
            self._aa_zi.fill(0.0)
        if self._dc_zi is not None:
            self._dc_zi.fill(0.0)
        self._env_in2_zi.fill(0.0)
        self._env_out2_zi.fill(0.0)
        self._dist_active = False

    def reset(self) -> None:
        if self._native is not None and dsp_native.nova_amp_reset is not None:
            dsp_native.nova_amp_reset(self._native)
        self._prev_sample.fill(0.0)
        if self._pre_zi is not None:
            self._pre_zi.fill(0.0)
        self._reset_distortion_states()
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

        # 2. Filtres inter-étages dans le domaine 4x oversamplé :
        #    couplage passe-haut 55 Hz (bloque le DC entre triodes) + passe-bas Miller 7.2 kHz
        sr4 = sr * 4
        self._inter_sos = _biquads_to_sos([
            _onepole_highpass(55.0, sr4),
            _onepole_lowpass(7200.0, sr4),
        ])
        self._inter1_zi = np.zeros((2, 2, 2), dtype=np.float64)
        self._inter2_zi = np.zeros((2, 2, 2), dtype=np.float64)

        # Suiveur de bias dynamique (grille/cathode) ~25 ms dans le domaine 4x
        p_sag = math.exp(-1.0 / (0.025 * float(sr4)))
        self._sag_sos = _biquads_to_sos([(
            np.array([1.0 - p_sag, 0.0, 0.0], dtype=np.float64),
            np.array([1.0, -p_sag, 0.0], dtype=np.float64),
        )])
        self._sag1_zi = np.zeros((1, 2, 2), dtype=np.float64)
        self._sag2_zi = np.zeros((1, 2, 2), dtype=np.float64)
        self._sag3_zi = np.zeros((1, 2, 2), dtype=np.float64)

        # 3. Filtre anti-aliasing 4x oversampling (4e ordre à ~0.42 * sr dans le domaine 4 * sr)
        fc_aa = min(18000.0, 0.42 * sr)
        if HAS_SCIPY:
            self._aa_sos = sig.butter(4, fc_aa / (0.5 * sr4), btype="low", output="sos")
        else:
            b1, a1 = _biquad_lowpass(fc_aa, 0.541, sr4)
            b2, a2 = _biquad_lowpass(fc_aa, 1.307, sr4)
            self._aa_sos = _biquads_to_sos([(b1, a1), (b2, a2)])
        self._aa_zi = np.zeros((self._aa_sos.shape[0], 2, 2), dtype=np.float64)

        # 4. DC Blocker continu (après décimation)
        self._dc_sos = _biquads_to_sos([(
            np.array([1.0, -1.0, 0.0], dtype=np.float64),
            np.array([1.0, -0.996, 0.0], dtype=np.float64),
        )])
        self._dc_zi = np.zeros((1, 2, 2), dtype=np.float64)

        # Coefficient du suiveur d'enveloppe RMS (~80 ms) pour Auto-Gain
        self._env_alpha = 1.0 - math.exp(-1.0 / (0.080 * float(sr)))

        # 5. Simulateur de baffle guitare (1x12 / 4x12 combo/cab)
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

    def _ensure_dist_filters(self, dist: float, sr: int) -> None:
        key = (round(dist, 3), sr)
        if key == self._tight_key and self._tight_sos is not None:
            return
        self._tight_key = key
        # Pré-mise en forme avant saturation : resserre les graves (70 -> 170 Hz)
        # et pousse les hauts-médiums (1.6 kHz, 0 -> +6 dB) pour du mordant et du sustain
        fc_tight = 70.0 + 100.0 * (dist ** 1.3)
        bite_db = 6.0 * (dist ** 1.2)
        self._tight_sos = _biquads_to_sos([
            _onepole_highpass(fc_tight, sr),
            _biquad_peaking(1600.0, bite_db, 0.85, sr),
        ])
        if self._tight_zi is None or self._tight_zi.shape[0] != 2:
            self._tight_zi = np.zeros((2, 2, 2), dtype=np.float64)

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

        if self._native is not None and dsp_native.nova_amp_process is not None:
            if in_buf.ndim == 1:
                in_c = np.column_stack((in_buf, in_buf)).astype(np.float32)
            elif in_buf.ndim == 2 and in_buf.shape[1] == 1:
                in_c = np.column_stack((in_buf[:, 0], in_buf[:, 0])).astype(np.float32)
            elif in_buf.ndim == 2 and in_buf.shape[1] > 2:
                in_c = np.ascontiguousarray(in_buf[:, :2], dtype=np.float32)
            else:
                in_c = np.ascontiguousarray(in_buf, dtype=np.float32)
            out = np.empty((n_samples, 2), dtype=np.float32)
            dsp_native.nova_amp_process(
                self._native,
                in_c.ctypes.data_as(dsp_native.c_float_p),
                out.ctypes.data_as(dsp_native.c_float_p),
                int(n_samples),
                max(8000, int(sample_rate)),
                float(distortion),
                float(low_db),
                float(mid_db),
                float(treble_db),
                float(volume),
                1 if cab_enabled else 0,
                None,
                None,
            )
            return out

        if in_buf.ndim == 1:
            in_buf = np.column_stack((in_buf, in_buf))
        elif in_buf.ndim == 2 and in_buf.shape[1] == 1:
            in_buf = np.column_stack((in_buf[:, 0], in_buf[:, 0]))
        elif in_buf.ndim == 2 and in_buf.shape[1] > 2:
            in_buf = in_buf[:, :2]

        sr = max(8000, int(sample_rate))
        self._ensure_static_filters(sr)
        self._ensure_tone_stack(low_db, mid_db, treble_db, sr)

        x = in_buf.astype(np.float64, copy=False)

        # 1. Pré-filtrage coupe-bas
        x, self._pre_zi = _sosfilt_stereo(self._pre_sos, x, self._pre_zi)

        # 2. Saturation lampe multi-étages avec Oversampling 4x et Auto-Gain RMS
        dist = max(0.0, min(1.0, float(distortion)))
        if dist > 0.002:
            self._dist_active = True
            self._ensure_dist_filters(dist, sr)
            x_drive, self._tight_zi = _sosfilt_stereo(self._tight_sos, x, self._tight_zi)

            ext = np.vstack([self._prev_sample[None, :], x_drive])
            diff = ext[1:] - ext[:-1]
            x4 = np.empty((n_samples * 4, 2), dtype=np.float64)
            x4[0::4] = ext[:-1] + 0.25 * diff
            x4[1::4] = ext[:-1] + 0.50 * diff
            x4[2::4] = ext[:-1] + 0.75 * diff
            x4[3::4] = ext[1:]
            self._prev_sample = x_drive[-1].copy()

            # Loi logarithmique en dB sur 3 étages en cascade (jusqu'à +64 dB à 100 %)
            total_db = 64.0 * (dist ** 0.85)
            g1 = 10.0 ** ((0.40 * total_db) / 20.0)
            g2 = 10.0 ** ((0.36 * total_db) / 20.0)
            g3 = 10.0 ** ((0.24 * total_db) / 20.0)
            g_total = g1 * g2 * g3

            # Étage 1 : Triode d'entrée V1A (bias dynamique de grille + coupure asymétrique)
            env1, self._sag1_zi = _sosfilt_stereo(self._sag_sos, np.maximum(x4, 0.0), self._sag1_zi)
            u1 = x4 * g1 - (0.35 * dist) * env1 * g1 + 0.05 * dist
            y1 = _triode_asym(u1, pos_lim=0.95, neg_lim=0.58)
            y1, self._inter1_zi = _sosfilt_stereo(self._inter_sos, y1, self._inter1_zi)

            # Étage 2 : Triode de gain V1B (inversion de phase + saturation asymétrique)
            env2, self._sag2_zi = _sosfilt_stereo(self._sag_sos, np.maximum(-y1, 0.0), self._sag2_zi)
            u2 = -y1 * g2 - (0.32 * dist) * env2 * g2 - 0.07 * dist
            y2 = _triode_asym(u2, pos_lim=0.88, neg_lim=0.54)
            y2, self._inter2_zi = _sosfilt_stereo(self._inter_sos, y2, self._inter2_zi)

            # Étage 3 : Étage de puissance / écrêtage haute-saturation asymétrique
            env3, self._sag3_zi = _sosfilt_stereo(self._sag_sos, np.maximum(y2, 0.0), self._sag3_zi)
            u3 = -y2 * g3 + (0.28 * dist) * env3 * g3 + 0.08 * dist
            diode_mix = max(0.0, (dist - 0.35) / 0.65) * 0.55
            y3_tube = np.tanh(u3)
            if diode_mix > 0.0:
                y3_hard = np.clip(u3 * 0.92, -0.76, 1.04)
                y3 = (1.0 - diode_mix) * y3_tube + diode_mix * y3_hard
            else:
                y3 = y3_tube

            # Filtrage anti-aliasing 4x puis décimation
            y4_filt, self._aa_zi = _sosfilt_stereo(self._aa_sos, y3, self._aa_zi)
            y_dec = y4_filt[3::4]

            # DC Blocker continu
            y_dec, self._dc_zi = _sosfilt_stereo(self._dc_sos, y_dec, self._dc_zi)

            # Auto-Gain RMS continu : maintient la sonie égale au signal d'entrée propre
            p_in = 0.5 * (x[:, 0] ** 2 + x[:, 1] ** 2)
            p_out = 0.5 * (y_dec[:, 0] ** 2 + y_dec[:, 1] ** 2)
            env_in2, self._env_in2_zi = _onepole_env_1d(self._env_alpha, p_in, self._env_in2_zi)
            env_out2, self._env_out2_zi = _onepole_env_1d(self._env_alpha, p_out, self._env_out2_zi)
            floor2 = 1e-8
            g_comp = np.sqrt((env_in2 + floor2 / (g_total * g_total)) / (env_out2 + floor2))
            x = y_dec * g_comp[:, None]
        else:
            if self._dist_active:
                self._reset_distortion_states()
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

    def process_block_with_peaks(
        self,
        in_buf: np.ndarray,
        sample_rate: int,
        distortion: float,
        low_db: float,
        mid_db: float,
        treble_db: float,
        volume: float,
        cab_enabled: bool = True,
    ) -> Tuple[np.ndarray, float, float]:
        """Fast path returning (out_buf, in_peak_db, out_peak_db) in a single C pass."""
        n_samples = len(in_buf)
        if n_samples == 0:
            return in_buf, -60.0, -60.0

        if self._native is not None and dsp_native.nova_amp_process is not None:
            if in_buf.dtype == np.float32 and in_buf.ndim == 2 and in_buf.shape[1] == 2 and in_buf.flags.c_contiguous:
                in_c = in_buf
            elif in_buf.ndim == 1:
                in_c = np.column_stack((in_buf, in_buf)).astype(np.float32)
            elif in_buf.ndim == 2 and in_buf.shape[1] == 1:
                in_c = np.column_stack((in_buf[:, 0], in_buf[:, 0])).astype(np.float32)
            elif in_buf.ndim == 2 and in_buf.shape[1] > 2:
                in_c = np.ascontiguousarray(in_buf[:, :2], dtype=np.float32)
            else:
                in_c = np.ascontiguousarray(in_buf, dtype=np.float32)
            out = np.empty((n_samples, 2), dtype=np.float32)
            dsp_native.nova_amp_process(
                self._native,
                in_c.ctypes.data_as(dsp_native.c_float_p),
                out.ctypes.data_as(dsp_native.c_float_p),
                int(n_samples),
                max(8000, int(sample_rate)),
                float(distortion),
                float(low_db),
                float(mid_db),
                float(treble_db),
                float(volume),
                1 if cab_enabled else 0,
                self._c_in_pk_p,
                self._c_out_pk_p,
            )
            return out, float(self._c_in_pk.value), float(self._c_out_pk.value)

        max_in = float(np.max(np.abs(in_buf)))
        in_pk_db = 20.0 * math.log10(max(1e-5, max_in))
        out = self.process_block(
            in_buf=in_buf,
            sample_rate=sample_rate,
            distortion=distortion,
            low_db=low_db,
            mid_db=mid_db,
            treble_db=treble_db,
            volume=volume,
            cab_enabled=cab_enabled,
        )
        max_out = float(np.max(np.abs(out))) if len(out) > 0 else 0.0
        out_pk_db = 20.0 * math.log10(max(1e-5, max_out))
        return out, in_pk_db, out_pk_db
