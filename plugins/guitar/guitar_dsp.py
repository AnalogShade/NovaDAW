"""
plugins/guitar/guitar_dsp.py - Moteur DSP de modélisation physique de corde de guitare (Digital Waveguide).

Licence MIT — Implémentation 100 % originale basée sur la physique des guides d'ondes numériques :
- Boucle de propagation bidirectionnelle avec délai fractionnaire all-pass de Thiran (< 0.1 cent de précision)
- Dispersion de rigidité / tension de corde (filtre all-pass d'inharmonicité)
- Pertes dépendant de la fréquence (nylon chaud vs acier brillant) et amortissement au relâchement (Note-Off / Palm Mute)
- Excitation réaliste avec position du médiator/doigt (comb filter) et dynamique liée à la vélocité
- Double polarisation couplée pour un battement et un sustain naturels
- Étage de sortie commutable : Caisse acoustique modale (Nylon / Folk) ou Micro magnétique électrique (DI)
"""
import ctypes
from functools import lru_cache
import math
from typing import Optional, Dict, Tuple, Any
import numpy as np

from core import dsp_native

try:
    import scipy.signal as sig
    HAS_SCIPY = True
except Exception:
    HAS_SCIPY = False

from plugins.synth.synth_dsp import pitch_to_freq


def _biquad_peaking(freq: float, gain_db: float, q: float, sr: int) -> Tuple[np.ndarray, np.ndarray]:
    """Calcule les coefficients biquad peaking (Audio EQ Cookbook)."""
    f = max(20.0, min(float(freq), sr * 0.45))
    a_lin = 10.0 ** (float(gain_db) / 40.0)
    w0 = 2.0 * math.pi * f / float(sr)
    cos_w0 = math.cos(w0)
    sin_w0 = math.sin(w0)
    alpha = sin_w0 / (2.0 * max(0.1, float(q)))

    b0 = 1.0 + alpha * a_lin
    b1 = -2.0 * cos_w0
    b2 = 1.0 - alpha * a_lin
    a0 = 1.0 + alpha / a_lin
    a1 = -2.0 * cos_w0
    a2 = 1.0 - alpha / a_lin

    b = np.array([b0 / a0, b1 / a0, b2 / a0], dtype=np.float64)
    a = np.array([1.0, a1 / a0, a2 / a0], dtype=np.float64)
    return b, a


def _biquad_bandpass(freq: float, q: float, sr: int) -> Tuple[np.ndarray, np.ndarray]:
    """Calcule les coefficients biquad bandpass (gain crête unitaire)."""
    f = max(20.0, min(float(freq), sr * 0.45))
    w0 = 2.0 * math.pi * f / float(sr)
    cos_w0 = math.cos(w0)
    sin_w0 = math.sin(w0)
    alpha = sin_w0 / (2.0 * max(0.2, float(q)))

    b0 = alpha
    b1 = 0.0
    b2 = -alpha
    a0 = 1.0 + alpha
    a1 = -2.0 * cos_w0
    a2 = 1.0 - alpha

    b = np.array([b0 / a0, b1 / a0, b2 / a0], dtype=np.float64)
    a = np.array([1.0, a1 / a0, a2 / a0], dtype=np.float64)
    return b, a


def _biquad_lowpass(freq: float, q: float, sr: int) -> Tuple[np.ndarray, np.ndarray]:
    """Calcule les coefficients biquad low-pass 2e ordre."""
    f = max(20.0, min(float(freq), sr * 0.45))
    w0 = 2.0 * math.pi * f / float(sr)
    cos_w0 = math.cos(w0)
    sin_w0 = math.sin(w0)
    alpha = sin_w0 / (2.0 * max(0.2, float(q)))

    b0 = (1.0 - cos_w0) * 0.5
    b1 = 1.0 - cos_w0
    b2 = (1.0 - cos_w0) * 0.5
    a0 = 1.0 + alpha
    a1 = -2.0 * cos_w0
    a2 = 1.0 - alpha

    b = np.array([b0 / a0, b1 / a0, b2 / a0], dtype=np.float64)
    a = np.array([1.0, a1 / a0, a2 / a0], dtype=np.float64)
    return b, a


def _biquad_highpass(freq: float, q: float, sr: int) -> Tuple[np.ndarray, np.ndarray]:
    """Calcule les coefficients biquad high-pass 2e ordre."""
    f = max(15.0, min(float(freq), sr * 0.45))
    w0 = 2.0 * math.pi * f / float(sr)
    cos_w0 = math.cos(w0)
    sin_w0 = math.sin(w0)
    alpha = sin_w0 / (2.0 * max(0.2, float(q)))

    b0 = (1.0 + cos_w0) * 0.5
    b1 = -(1.0 + cos_w0)
    b2 = (1.0 + cos_w0) * 0.5
    a0 = 1.0 + alpha
    a1 = -2.0 * cos_w0
    a2 = 1.0 - alpha

    b = np.array([b0 / a0, b1 / a0, b2 / a0], dtype=np.float64)
    a = np.array([1.0, a1 / a0, a2 / a0], dtype=np.float64)
    return b, a


def _phase_delay_at(b: np.ndarray, a: np.ndarray, w0: float) -> float:
    """Retourne le retard de phase exact en échantillons d'un filtre IIR à la pulsation w0."""
    if w0 <= 1e-9:
        return 0.0
    z_inv = complex(math.cos(w0), -math.sin(w0))
    num = 0.0j
    z_p = 1.0 + 0.0j
    for coef in b:
        num += float(coef) * z_p
        z_p *= z_inv
    den = 0.0j
    z_p = 1.0 + 0.0j
    for coef in a:
        den += float(coef) * z_p
        z_p *= z_inv
    h = num / den
    ang = math.atan2(h.imag, h.real)
    # Le retard de phase d'un filtre causal est positif (-angle / w0)
    if ang > 0.0:
        ang -= 2.0 * math.pi
    return -ang / w0


_DC_B = np.array([1.0, -1.0], dtype=np.float64)
_DC_A = np.array([1.0, -0.995], dtype=np.float64)


def _lfilter_fast(b: np.ndarray, a: np.ndarray, x: np.ndarray, zi: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Filtre IIR rapide utilisant SciPy en C avec repli NumPy pur."""
    if HAS_SCIPY:
        return sig.lfilter(b, a, x, zi=zi)
    # Repli Direct Form II Transposed en Python si SciPy absent
    b_arr = np.asarray(b, dtype=np.float64)
    a_arr = np.asarray(a, dtype=np.float64)
    a0 = float(a_arr[0])
    if a0 != 1.0:
        b_arr = b_arr / a0
        a_arr = a_arr / a0
    m = max(len(b_arr), len(a_arr))
    order = m - 1
    x_arr = np.asarray(x, dtype=np.float64)
    n = len(x_arr)
    if order == 0:
        b0 = float(b_arr[0]) if len(b_arr) > 0 else 0.0
        return b0 * x_arr, np.empty(0, dtype=np.float64)

    if len(b_arr) < m:
        b_pad = np.zeros(m, dtype=np.float64)
        b_pad[:len(b_arr)] = b_arr
    else:
        b_pad = b_arr
    if len(a_arr) < m:
        a_pad = np.zeros(m, dtype=np.float64)
        a_pad[:len(a_arr)] = a_arr
    else:
        a_pad = a_arr

    out = np.empty(n, dtype=np.float64)
    z = np.asarray(zi, dtype=np.float64).copy()
    if len(z) != order:
        raise ValueError(f"zi length {len(z)} does not match filter order {order}")
    b0 = float(b_pad[0])
    b_rest = b_pad[1:]
    a_rest = a_pad[1:]
    for i in range(n):
        xi = float(x_arr[i])
        yi = b0 * xi + z[0]
        out[i] = yi
        for k in range(order - 1):
            z[k] = b_rest[k] * xi - a_rest[k] * yi + z[k + 1]
        z[order - 1] = b_rest[order - 1] * xi - a_rest[order - 1] * yi
    return out, z


@lru_cache(maxsize=512)
def _cached_plane_coeffs(
    freq: float,
    sample_rate: int,
    brightness: float,
    string_tension: float,
    sustain_sec: float,
    release_sec: float,
    palm_mute: float,
) -> Tuple[int, float, float, float, float, float, float, float, float, int, float, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    sr = max(8000, int(sample_rate))
    f = max(25.0, min(float(freq), sr * 0.38))
    w0 = 2.0 * math.pi * f / float(sr)
    period_total = float(sr) / f

    bright = max(0.05, min(0.98, float(brightness)))
    mute = max(0.0, min(1.0, float(palm_mute)))
    p_sus_raw = 0.04 + (1.0 - bright) * 0.42 + mute * 0.36
    freq_scale = min(1.0, max(0.35, 220.0 / f))
    p_sustain = max(0.02, min(0.84, p_sus_raw * (0.65 + 0.35 * freq_scale)))

    eff_sustain = max(0.15, float(sustain_sec) * (1.0 - 0.85 * mute))
    eff_sustain *= (180.0 / max(80.0, f)) ** 0.32
    eff_sustain = max(0.12, min(14.0, eff_sustain))
    g_sustain = min(0.9996, max(0.90, 10.0 ** (-3.0 / (eff_sustain * f))))

    rel_t = max(0.04, float(release_sec))
    g_release = min(0.9994, max(0.32, 10.0 ** (-1.5 / (rel_t * f))))
    rel_norm = max(0.0, min(1.0, (rel_t - 0.04) / 1.16))
    extra_damp = (1.0 - rel_norm) ** 1.5
    p_release = min(
        0.84,
        max(p_sustain, p_sustain + extra_damp * (0.68 - p_sustain) + 0.03 * (1.0 - p_sustain)),
    )

    tension = max(0.0, min(1.0, float(string_tension)))
    a_disp = -0.34 * (1.0 - tension) * min(1.0, 350.0 / max(80.0, f))

    tau_loss = _phase_delay_at(
        np.array([1.0 - p_sustain]), np.array([1.0, -p_sustain]), w0
    )
    tau_disp = _phase_delay_at(
        np.array([a_disp, 1.0]), np.array([1.0, a_disp]), w0
    )

    rem = period_total - tau_loss - tau_disp
    n_int = max(4, int(math.floor(rem)))
    delta = rem - n_int
    if delta < 0.20 and n_int > 4:
        n_int -= 1
        delta += 1.0

    eta = (1.0 - delta) / (1.0 + delta)
    for _ in range(3):
        tau_frac = _phase_delay_at(np.array([eta, 1.0]), np.array([1.0, eta]), w0)
        err = delta - tau_frac
        delta_corr = max(0.08, min(1.6, delta + err))
        eta = (1.0 - delta_corr) / (1.0 + delta_corr)
    eta = max(-0.78, min(0.78, eta))

    def _coeffs(g_val: float, p_val: float) -> Tuple[np.ndarray, np.ndarray]:
        b_loss = np.array([g_val * (1.0 - p_val)], dtype=np.float64)
        a_loss = np.array([1.0, -p_val], dtype=np.float64)
        b_frac = np.array([eta, 1.0], dtype=np.float64)
        a_frac = np.array([1.0, eta], dtype=np.float64)
        b_disp = np.array([a_disp, 1.0], dtype=np.float64)
        a_disp_arr = np.array([1.0, a_disp], dtype=np.float64)
        b_out = np.ascontiguousarray(np.convolve(b_loss, np.convolve(b_frac, b_disp)), dtype=np.float64)
        a_out = np.ascontiguousarray(np.convolve(a_loss, np.convolve(a_frac, a_disp_arr)), dtype=np.float64)
        b_out.setflags(write=False)
        a_out.setflags(write=False)
        return b_out, a_out

    b_sus, a_sus = _coeffs(g_sustain, p_sustain)
    b_rel, a_rel = _coeffs(g_release, p_release)
    return (
        sr, f, w0, period_total, p_sustain, g_sustain, g_release, p_release,
        a_disp, n_int, eta, b_sus, a_sus, b_rel, a_rel,
    )


class WaveguideStringPlane:
    """
    Plan de vibration unique d'une corde dans un guide d'ondes numérique (Digital Waveguide).
    Supporte le streaming incrémental bloc par bloc sans clic ni dérive d'accordage.
    """

    def __init__(
        self,
        freq: float,
        sample_rate: int,
        brightness: float = 0.65,
        string_tension: float = 0.65,
        sustain_sec: float = 4.5,
        release_sec: float = 0.22,
        palm_mute: float = 0.0,
    ):
        (
            self.sample_rate,
            self.freq,
            self.w0,
            self.period_total,
            self.p_sustain,
            self.g_sustain,
            self.g_release,
            self.p_release,
            self.a_disp,
            self.n_int,
            self.eta,
            self.b_sustain,
            self.a_sustain,
            self.b_release,
            self.a_release,
        ) = _cached_plane_coeffs(
            float(freq),
            int(sample_rate),
            float(brightness),
            float(string_tension),
            float(sustain_sec),
            float(release_sec),
            float(palm_mute),
        )

        # État interne persistant (ligne à retard circulaire + mémoire IIR + DC blocker)
        self.in_release = False
        self._native = None
        if dsp_native.NATIVE_AVAILABLE and dsp_native.nova_guitar_plane_create is not None:
            self._native = dsp_native.nova_guitar_plane_create(
                int(self.n_int),
                self.b_sustain.ctypes.data_as(dsp_native.c_double_p),
                self.a_sustain.ctypes.data_as(dsp_native.c_double_p),
                self.b_release.ctypes.data_as(dsp_native.c_double_p),
                self.a_release.ctypes.data_as(dsp_native.c_double_p),
            )
        if self._native is None:
            self.ring = np.zeros(self.n_int, dtype=np.float64)
            self.ring_pos = 0
            self.zi_loop = np.zeros(len(self.a_sustain) - 1, dtype=np.float64)
            self.zi_dc = np.zeros(1, dtype=np.float64)
        else:
            self.ring = None
            self.ring_pos = 0
            self.zi_loop = None
            self.zi_dc = None

    def __del__(self):
        native = getattr(self, "_native", None)
        if native is not None and dsp_native is not None and dsp_native.nova_guitar_plane_destroy is not None:
            try:
                dsp_native.nova_guitar_plane_destroy(native)
            except Exception:
                pass
            self._native = None

    def _build_loop_coeffs(self, g: float, p: float) -> Tuple[np.ndarray, np.ndarray]:
        b_loss = np.array([g * (1.0 - p)], dtype=np.float64)
        a_loss = np.array([1.0, -p], dtype=np.float64)
        b_frac = np.array([self.eta, 1.0], dtype=np.float64)
        a_frac = np.array([1.0, self.eta], dtype=np.float64)
        b_disp = np.array([self.a_disp, 1.0], dtype=np.float64)
        a_disp = np.array([1.0, self.a_disp], dtype=np.float64)

        b = np.convolve(b_loss, np.convolve(b_frac, b_disp))
        a = np.convolve(a_loss, np.convolve(a_frac, a_disp))
        return b, a

    def step(self, excitation: np.ndarray, is_released: bool = False) -> np.ndarray:
        """
        Fait avancer le guide d'ondes de `len(excitation)` échantillons.
        Peut être appelé par blocs de taille quelconque avec continuité parfaite.
        """
        n_out = len(excitation)
        if n_out <= 0:
            return np.empty(0, dtype=np.float64)

        if is_released and not self.in_release:
            self.in_release = True

        if self._native is not None and dsp_native.nova_guitar_plane_step is not None:
            exc_c = np.ascontiguousarray(excitation, dtype=np.float64)
            out = np.empty(n_out, dtype=np.float64)
            dsp_native.nova_guitar_plane_step(
                self._native,
                exc_c.ctypes.data_as(dsp_native.c_double_p),
                out.ctypes.data_as(dsp_native.c_double_p),
                n_out,
                1 if is_released else 0,
            )
            return out

        if self.ring is None:
            self.ring = np.zeros(self.n_int, dtype=np.float64)
            self.ring_pos = 0
            self.zi_loop = np.zeros(len(self.a_sustain) - 1, dtype=np.float64)
            self.zi_dc = np.zeros(1, dtype=np.float64)
        b, a = (self.b_release, self.a_release) if self.in_release else (self.b_sustain, self.a_sustain)
        out = np.empty(n_out, dtype=np.float64)

        n_int = self.n_int
        ring = self.ring
        pos = self.ring_pos
        idx = 0

        while idx < n_out:
            # On peut traiter en bloc vectorisé jusqu'à min(n_int, espace avant fin du ring)
            step_len = min(n_out - idx, n_int - pos)
            delayed_seg = ring[pos:pos + step_len]
            filtered_seg, self.zi_loop = _lfilter_fast(b, a, delayed_seg, self.zi_loop)
            new_seg = excitation[idx:idx + step_len] + filtered_seg
            ring[pos:pos + step_len] = new_seg
            out[idx:idx + step_len] = new_seg
            pos += step_len
            if pos >= n_int:
                pos = 0
            idx += step_len

        self.ring_pos = pos
        # Suppression de toute composante continue
        out, self.zi_dc = _lfilter_fast(_DC_B, _DC_A, out, self.zi_dc)
        return out


def generate_pluck_excitation(
    length_samples: int,
    period_samples: float,
    sample_rate: int,
    velocity: int = 100,
    pluck_position: float = 0.24,
    pick_attack: float = 0.55,
    guitar_mode: str = "electric_clean",
    rng: Optional[np.random.Generator] = None,
) -> np.ndarray:
    """
    Génère l'excitation initiale réaliste de la corde (impulsion médiator/doigt + bruit filtré + peigne de position).
    """
    if length_samples <= 0:
        return np.empty(0, dtype=np.float64)

    if rng is None:
        rng = np.random.default_rng()

    vel_norm = max(0.05, min(1.0, float(velocity) / 127.0))
    vel_curve = vel_norm ** 1.15
    att = max(0.0, min(1.0, float(pick_attack)))

    # Durée du transitoire d'excitation (~1 période + attaque du médiator)
    pulse_ms = 2.6 - att * 1.4  # 1.2 ms (médiator sec) à 2.6 ms (doigt doux)
    if guitar_mode == "acoustic_nylon":
        pulse_ms += 1.1
    pulse_len = max(8, min(length_samples, int((pulse_ms / 1000.0) * sample_rate)))

    # 1. Impulsion lisse (déplacement de la corde relâchée)
    t_idx = np.arange(pulse_len, dtype=np.float64)
    # Impulsion asymétrique (montée rapide, descente douce)
    half = max(2, int(pulse_len * 0.25))
    pulse = np.zeros(pulse_len, dtype=np.float64)
    pulse[:half] = np.sin(0.5 * math.pi * (t_idx[:half] / float(half)))
    rem = pulse_len - half
    if rem > 0:
        pulse[half:] = 0.5 * (1.0 + np.cos(math.pi * (np.arange(rem, dtype=np.float64) / float(rem))))

    # 2. Bruit d'attaque filtré (frottement du médiator / ongle sur le filetage de corde)
    noise_len = min(length_samples, max(pulse_len, int(0.014 * sample_rate)))
    raw_noise = rng.uniform(-1.0, 1.0, size=noise_len)
    # Enveloppe exponentielle rapide sur le bruit
    env_noise = np.exp(-np.arange(noise_len, dtype=np.float64) / max(4.0, noise_len * 0.22))
    noise_burst = raw_noise * env_noise

    # Filtrage passe-bas du bruit selon la vélocité et le mode (nylon = doux, acier/élec = claquant)
    base_fc = 1400.0 if guitar_mode == "acoustic_nylon" else 3400.0
    fc = base_fc * (0.45 + 0.85 * vel_norm) * (0.55 + 0.90 * att)
    fc = max(400.0, min(sample_rate * 0.44, fc))
    b_lp, a_lp = _biquad_lowpass(fc, 0.707, sample_rate)
    if HAS_SCIPY:
        noise_burst = sig.lfilter(b_lp, a_lp, noise_burst)
    else:
        noise_burst, _ = _lfilter_fast(b_lp, a_lp, noise_burst, np.zeros(2, dtype=np.float64))

    # Mélange impulsion + bruit
    exc = np.zeros(length_samples, dtype=np.float64)
    exc[:pulse_len] += pulse * (0.52 - 0.14 * att)
    exc[:noise_len] += noise_burst * (0.36 + 0.42 * att)

    # 3. Filtre en peigne de position d'attaque (Pluck Position)
    # Une corde pincée à une fraction p de sa longueur annule les harmoniques multiples de 1/p
    p_pos = max(0.04, min(0.50, float(pluck_position)))
    comb_delay = max(1, min(length_samples - 1, int(round(p_pos * period_samples))))
    if comb_delay < length_samples:
        comb_exc = exc.copy()
        comb_exc[comb_delay:] -= exc[:-comb_delay] * 0.85
        # Légère compensation de niveau quand on pince très près du chevalet (comb très serré)
        norm = 1.0 / max(0.32, min(1.0, p_pos * 3.8))
        exc = comb_exc * norm

    # Normalisation énergétique maîtrisée + application de la vélocité
    peak = float(np.max(np.abs(exc))) if length_samples > 0 else 1.0
    if peak > 1e-6:
        exc = (exc / peak) * (0.48 * vel_curve)

    return exc


_GUITAR_MODE_MAP = {
    "acoustic_steel": 0,
    "acoustic_nylon": 1,
    "electric_bass": 2,
    "electric_clean": 3,
}


class GuitarBodyOrPickupProcessor:
    """
    Étage de coloration acoustique (caisse de résonance modale) ou électrique (micro magnétique).
    Maintient ses états de filtres entre blocs pour un rendu temps réel parfaitement continu.
    """

    def __init__(self, sample_rate: int):
        self.sample_rate = max(8000, int(sample_rate))
        self._filters: list = []
        self._cached_key: Optional[Tuple[Any, ...]] = None
        self._pickup_delay_buf = np.zeros(128, dtype=np.float64)
        self._native_state = dsp_native.NovaGuitarBodyState()

    def reset(self):
        for f in self._filters:
            f["zi"] = np.zeros(len(f["a"]) - 1, dtype=np.float64)
        self._pickup_delay_buf.fill(0.0)
        if dsp_native.nova_guitar_body_reset is not None:
            dsp_native.nova_guitar_body_reset(ctypes.byref(self._native_state))
        else:
            ctypes.memset(ctypes.byref(self._native_state), 0, ctypes.sizeof(self._native_state))

    def _ensure_filters(self, mode: str, body_res: float, pickup_pos: float, sr: int):
        key = (mode, round(body_res, 3), round(pickup_pos, 3), sr)
        if key == self._cached_key and self.sample_rate == sr:
            return
        self.sample_rate = sr
        self._cached_key = key
        self._filters = []

        if mode in ("acoustic_nylon", "acoustic_steel"):
            # Modes de caisse acoustique réalistes (Helmholtz + table d'harmonie + bois)
            is_nylon = (mode == "acoustic_nylon")
            air_f = 104.0 if is_nylon else 96.0
            top_f = 212.0 if is_nylon else 192.0
            dip_f = 415.0 if is_nylon else 390.0
            wood_f = 840.0 if is_nylon else 960.0

            modes = [
                (air_f, 5.2, 0.58 * body_res),
                (top_f, 6.0, 0.52 * body_res),
                (dip_f, 4.5, 0.30 * body_res),
                (wood_f, 3.8, 0.22 * body_res),
            ]
            for freq, q, gain in modes:
                b, a = _biquad_bandpass(freq, q, sr)
                self._filters.append({
                    "kind": "parallel_bp",
                    "b": b,
                    "a": a,
                    "gain": gain,
                    "zi": np.zeros(2, dtype=np.float64),
                })
            # Léger filtre coupe-bas subsonique pour la propreté
            b_hp, a_hp = _biquad_highpass(62.0, 0.707, sr)
            self._filters.append({
                "kind": "series",
                "b": b_hp,
                "a": a_hp,
                "gain": 1.0,
                "zi": np.zeros(2, dtype=np.float64),
            })
        elif mode == "electric_bass":
            # Micro Precision/Jazz basse : bosse grave-médium chaude + bobinage doux
            b_lp, a_lp = _biquad_lowpass(1850.0, 1.35, sr)
            b_pk, a_pk = _biquad_peaking(140.0, 3.0 * body_res, 1.1, sr)
            for b, a in ((b_pk, a_pk), (b_lp, a_lp)):
                self._filters.append({
                    "kind": "series",
                    "b": b,
                    "a": a,
                    "gain": 1.0,
                    "zi": np.zeros(2, dtype=np.float64),
                })
        else:
            # Guitare électrique (DI) : résonance RLC du bobinage du micro magnétique
            # pickup_pos faible = Bridge (plus aigu et mordant), élevé = Neck (plus rond)
            pu = max(0.05, min(0.45, float(pickup_pos)))
            fc = 3900.0 - (pu - 0.08) * 4200.0
            fc = max(1850.0, min(4300.0, fc))
            q_rlc = 1.25 + 0.85 * body_res
            b_lp, a_lp = _biquad_lowpass(fc, q_rlc, sr)
            b_hp, a_hp = _biquad_highpass(75.0, 0.707, sr)
            # Légère présence de micro simple/double
            b_pk, a_pk = _biquad_peaking(fc * 0.72, 2.5 * body_res, 1.4, sr)
            for b, a in ((b_hp, a_hp), (b_pk, a_pk), (b_lp, a_lp)):
                self._filters.append({
                    "kind": "series",
                    "b": b,
                    "a": a,
                    "gain": 1.0,
                    "zi": np.zeros(2, dtype=np.float64),
                })

    def process_mono(
        self,
        signal: np.ndarray,
        mode: str,
        body_resonance: float,
        pickup_position: float,
        sample_rate: int,
    ) -> np.ndarray:
        if len(signal) == 0:
            return signal
        if dsp_native.NATIVE_AVAILABLE and dsp_native.nova_guitar_body_process is not None:
            x_c = signal if (signal.dtype == np.float64 and signal.flags.c_contiguous) else np.ascontiguousarray(signal, dtype=np.float64)
            n_sig = len(x_c)
            out = np.empty(n_sig, dtype=np.float64)
            mode_id = _GUITAR_MODE_MAP.get(mode, 3)
            dsp_native.nova_guitar_body_process(
                ctypes.byref(self._native_state),
                ctypes.byref(ctypes.c_double.from_buffer(x_c)) if x_c.flags.writeable else x_c.ctypes.data_as(dsp_native.c_double_p),
                ctypes.byref(ctypes.c_double.from_buffer(out)),
                n_sig,
                mode_id,
                float(body_resonance),
                float(pickup_position),
                int(sample_rate),
            )
            return out
        self._ensure_filters(mode, body_resonance, pickup_position, sample_rate)
        x = signal.astype(np.float64, copy=False)

        if mode in ("acoustic_nylon", "acoustic_steel"):
            # Somme signal direct de chevalet + modes résonants de la caisse
            dry_mix = 0.65
            body_sum = np.zeros_like(x)
            for f in self._filters:
                if f["kind"] == "parallel_bp":
                    bp_out, f["zi"] = _lfilter_fast(f["b"], f["a"], x, f["zi"])
                    body_sum += bp_out * f["gain"]
            out = x * dry_mix + body_sum
            for f in self._filters:
                if f["kind"] == "series":
                    out, f["zi"] = _lfilter_fast(f["b"], f["a"], out, f["zi"])
            return out
        else:
            # Filtre spatial d'ouverture de micro magnétique + chaîne RLC en série
            # (simule la lecture de la corde sur une largeur finie ~1.5 ms à chevalet vs manche)
            pu_samples = max(2, min(64, int(round(pickup_position * 0.0045 * sample_rate))))
            buf = self._pickup_delay_buf
            if len(buf) < pu_samples:
                buf = np.zeros(pu_samples, dtype=np.float64)
                self._pickup_delay_buf = buf
            prev_tail = buf[-pu_samples:]
            combined = np.concatenate([prev_tail, x])
            buf[-pu_samples:] = combined[-pu_samples:]
            # Différence pondérée (lecture de vitesse magnétique douce sans tuer les graves)
            out = 0.72 * x + 0.28 * (x - combined[:len(x)])
            for f in self._filters:
                out, f["zi"] = _lfilter_fast(f["b"], f["a"], out, f["zi"])
            return out


@lru_cache(maxsize=512)
def _cached_pluck_excitation(
    length_samples: int,
    period_samples: float,
    sample_rate: int,
    velocity: int,
    pluck_position: float,
    pick_attack: float,
    guitar_mode: str,
    eff_seed: int,
    use_scipy: bool,
) -> np.ndarray:
    rng = np.random.default_rng(eff_seed)
    exc = generate_pluck_excitation(
        length_samples=length_samples,
        period_samples=period_samples,
        sample_rate=sample_rate,
        velocity=velocity,
        pluck_position=pluck_position,
        pick_attack=pick_attack,
        guitar_mode=guitar_mode,
        rng=rng,
    )
    exc_c = np.ascontiguousarray(exc, dtype=np.float64)
    exc_c.setflags(write=False)
    return exc_c


class GuitarStringVoice:
    """
    Voix polyphonique complète d'une corde de guitare (excitation pré-générée + 2 polarisations couplées).
    Permet un rendu continu bloc par bloc ou une avance rapide si on saute dans une note.
    """

    def __init__(
        self,
        pitch: int,
        velocity: int,
        note_dur_samples: int,
        sample_rate: int,
        guitar_mode: str = "electric_clean",
        string_tension: float = 0.65,
        pluck_position: float = 0.24,
        brightness: float = 0.65,
        sustain_sec: float = 4.5,
        release_sec: float = 0.22,
        pick_attack: float = 0.55,
        palm_mute: float = 0.0,
        dual_polarization: bool = True,
        seed: Optional[int] = None,
    ):
        self.pitch = int(pitch)
        self.velocity = int(velocity)
        self.note_dur_samples = max(1, int(note_dur_samples))
        self.sample_rate = max(8000, int(sample_rate))
        self.guitar_mode = guitar_mode
        self.dual_polarization = bool(dual_polarization)

        self.freq = pitch_to_freq(self.pitch)
        if guitar_mode == "electric_bass" and self.pitch > 55:
            # Garder une tessiture naturelle si l'utilisateur joue dans le registre médium
            pass

        self.plane_a = WaveguideStringPlane(
            freq=self.freq,
            sample_rate=self.sample_rate,
            brightness=brightness,
            string_tension=string_tension,
            sustain_sec=sustain_sec,
            release_sec=release_sec,
            palm_mute=palm_mute,
        )

        # Seconde polarisation légèrement désaccordée (~0.9 cent) et légèrement plus mate
        self.plane_b: Optional[WaveguideStringPlane] = None
        if self.dual_polarization:
            self.plane_b = WaveguideStringPlane(
                freq=self.freq * 1.00055,
                sample_rate=self.sample_rate,
                brightness=max(0.05, brightness * 0.92),
                string_tension=string_tension,
                sustain_sec=sustain_sec * 0.90,
                release_sec=release_sec,
                palm_mute=palm_mute,
            )

        eff_seed = int(seed) if seed is not None else (self.pitch * 131 + self.velocity * 17)
        exc_len = min(int(0.045 * self.sample_rate), max(64, int(self.plane_a.period_total * 2.5)))
        self._exc_c = _cached_pluck_excitation(
            int(exc_len),
            float(self.plane_a.period_total),
            int(self.sample_rate),
            int(self.velocity),
            float(pluck_position),
            float(pick_attack),
            str(guitar_mode),
            eff_seed,
            bool(HAS_SCIPY),
        )
        self._exc_ptr = (
            self._exc_c.ctypes.data_as(dsp_native.c_double_p)
            if dsp_native.c_double_p is not None
            else None
        )
        self._exc_len = len(self._exc_c)
        self.excitation = self._exc_c
        self.rendered_samples = 0

    def skip_samples(self, count: int) -> None:
        """Avance rapide sans allouer de tampon de sortie."""
        if count <= 0:
            return
        if (
            self.plane_a._native is not None
            and (self.plane_b is None or self.plane_b._native is not None)
            and dsp_native.nova_guitar_voice_render is not None
        ):
            dsp_native.nova_guitar_voice_render(
                self.plane_a._native,
                self.plane_b._native if self.plane_b is not None else None,
                self._exc_ptr,
                self._exc_len,
                self.rendered_samples,
                self.note_dur_samples,
                None,
                int(count),
            )
            self.rendered_samples += int(count)
            return
        skip = count
        while skip > 0:
            step = min(4096, skip)
            self.render_samples(step)
            skip -= step

    def render_samples(self, count: int) -> np.ndarray:
        """Rend `count` échantillons mono en continu à partir de `self.rendered_samples`."""
        if count <= 0:
            return np.empty(0, dtype=np.float64)

        if (
            self.plane_a._native is not None
            and (self.plane_b is None or self.plane_b._native is not None)
            and dsp_native.nova_guitar_voice_render is not None
        ):
            out = np.empty(count, dtype=np.float64)
            dsp_native.nova_guitar_voice_render(
                self.plane_a._native,
                self.plane_b._native if self.plane_b is not None else None,
                self._exc_ptr,
                self._exc_len,
                self.rendered_samples,
                self.note_dur_samples,
                ctypes.byref(ctypes.c_double.from_buffer(out)),
                count,
            )
            self.rendered_samples += count
            return out

        start = self.rendered_samples
        end = start + count
        exc_len = len(self.excitation)

        def _get_exc(s_idx: int, e_idx: int) -> np.ndarray:
            n = e_idx - s_idx
            if s_idx >= exc_len:
                return np.zeros(n, dtype=np.float64)
            buf = np.zeros(n, dtype=np.float64)
            copy_n = min(n, exc_len - s_idx)
            buf[:copy_n] = self.excitation[s_idx:s_idx + copy_n]
            return buf

        # Découper exactement à la frontière de Note-Off si elle tombe dans ce bloc
        if start < self.note_dur_samples < end:
            part1_n = self.note_dur_samples - start
            part2_n = end - self.note_dur_samples
            out1 = self._step_planes(_get_exc(start, self.note_dur_samples), is_released=False)
            out2 = self._step_planes(_get_exc(self.note_dur_samples, end), is_released=True)
            res = np.concatenate([out1, out2])
        else:
            released = start >= self.note_dur_samples
            res = self._step_planes(_get_exc(start, end), is_released=released)

        self.rendered_samples = end
        return res

    def _step_planes(self, exc: np.ndarray, is_released: bool) -> np.ndarray:
        out_a = self.plane_a.step(exc, is_released=is_released)
        if self.plane_b is not None:
            out_b = self.plane_b.step(exc * 0.85, is_released=is_released)
            return 0.72 * out_a + 0.28 * out_b
        return out_a


class GuitarStereoWidener:
    """
    Élargisseur stéréo Mid/Side par réseau all-pass en quadrature (90° sur 40 Hz – 16 kHz).
    - Compatibilité mono 100 % : (L + R) / 2 préserve exactement l'amplitude spectrale (0 filtrage en peigne).
    - Symétrie L/R parfaite même sur une note isolée (pas de bascule de panoramique sur E2/A3/E4).
    - Conservation de la sonie globale grâce à la normalisation énergétique 1 / sqrt(1 + k²).
    """

    _COEFFS_A = (0.6923878, 0.9360654322959, 0.9882295226860, 0.9987488452737)
    _COEFFS_B = (0.4021921162426, 0.8561710882420, 0.9722909545651, 0.9952884791278)

    def __init__(self) -> None:
        self._stages_a = self._build_stages(self._COEFFS_A, with_delay=True)
        self._stages_b = self._build_stages(self._COEFFS_B, with_delay=False)
        self._native_state = dsp_native.NovaGuitarWidenerState()

    @staticmethod
    def _build_stages(coeffs: Tuple[float, ...], with_delay: bool) -> list:
        stages = []
        if with_delay:
            stages.append({
                "b": np.array([0.0, 1.0], dtype=np.float64),
                "a": np.array([1.0, 0.0], dtype=np.float64),
                "zi": np.zeros(1, dtype=np.float64),
            })
        for a_val in coeffs:
            c = a_val * a_val
            stages.append({
                "b": np.array([-c, 0.0, 1.0], dtype=np.float64),
                "a": np.array([1.0, 0.0, -c], dtype=np.float64),
                "zi": np.zeros(2, dtype=np.float64),
            })
        return stages

    def reset(self) -> None:
        for st in self._stages_a:
            st["zi"].fill(0.0)
        for st in self._stages_b:
            st["zi"].fill(0.0)
        if dsp_native.nova_guitar_widener_reset is not None:
            dsp_native.nova_guitar_widener_reset(ctypes.byref(self._native_state))
        else:
            ctypes.memset(ctypes.byref(self._native_state), 0, ctypes.sizeof(self._native_state))

    def process(self, mono: np.ndarray, stereo_width: float) -> Tuple[np.ndarray, np.ndarray]:
        w = max(0.0, min(1.0, float(stereo_width)))
        if len(mono) == 0 or w <= 0.001:
            return mono, mono

        if dsp_native.NATIVE_AVAILABLE and dsp_native.nova_guitar_widener_process is not None:
            x_c = mono if (mono.dtype == np.float64 and mono.flags.c_contiguous) else np.ascontiguousarray(mono, dtype=np.float64)
            n = len(x_c)
            out_l = np.empty(n, dtype=np.float64)
            out_r = np.empty(n, dtype=np.float64)
            dsp_native.nova_guitar_widener_process(
                ctypes.byref(self._native_state),
                ctypes.byref(ctypes.c_double.from_buffer(x_c)) if x_c.flags.writeable else x_c.ctypes.data_as(dsp_native.c_double_p),
                ctypes.byref(ctypes.c_double.from_buffer(out_l)),
                ctypes.byref(ctypes.c_double.from_buffer(out_r)),
                n,
                w,
            )
            return out_l, out_r

        x = mono.astype(np.float64, copy=False)
        sig_i = x
        for st in self._stages_a:
            sig_i, st["zi"] = _lfilter_fast(st["b"], st["a"], sig_i, st["zi"])

        sig_q = x
        for st in self._stages_b:
            sig_q, st["zi"] = _lfilter_fast(st["b"], st["a"], sig_q, st["zi"])

        k = w * 0.78
        norm = 1.0 / math.sqrt(1.0 + k * k)
        out_l = (sig_i + k * sig_q) * norm
        out_r = (sig_i - k * sig_q) * norm
        return out_l, out_r
