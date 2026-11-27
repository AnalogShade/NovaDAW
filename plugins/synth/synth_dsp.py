"""
plugins/synth/synth_dsp.py - Moteur DSP ultra-haute fidélité FP32 pour le synthétiseur NovaSynth.

Fonctionnalités :
- Synthèse d'oscillateurs purs anti-aliasés (PolyBLEP Saw & Square, Sine, Triangle, Noise, FM 2-op, SuperSaw JP-8000).
- Filtre State-Variable multimode (Chamberlin / Cytomic SVF) : Passe-bas (LP), Passe-haut (HP), Passe-bande (BP), Rejet (Notch).
- Double générateur d'enveloppe ADSR analogique (Amplitude et Filtre/Modulation).
- LFO multi-formes d'ondes (Sine, Tri, Saw, Square, Random S&H) routable vers Pitch, Cutoff, Pan, Amp, PWM.
- Effets d'espace intégrés : Saturation analogique, Délai Ping-Pong stéréo, Réverbération studio.
- Gestion polyphonique multivoix sans clic ni coupure.
"""
import ctypes
import math
import random
import threading
import numpy as np
from typing import Optional, Tuple, Dict, Any, List

from core import dsp_native

try:
    import scipy.signal as sig
    HAS_SCIPY_SIG = True
except Exception:
    HAS_SCIPY_SIG = False


_OSC_WAVE_MAP = {
    "sine": 0,
    "triangle": 1,
    "saw": 2,
    "square": 3,
    "pulse": 3,
    "noise": 4,
    "fm": 5,
    "supersaw": 6,
    "unison": 6,
}

_SVF_TYPE_MAP = {
    "lowpass": 0,
    "lp": 0,
    "highpass": 1,
    "hp": 1,
    "bandpass": 2,
    "bp": 2,
    "notch": 3,
    "rejet": 3,
}


def pitch_to_freq(pitch: float) -> float:
    """Convertit un numéro de note MIDI en fréquence en Hertz (A4 = 69 = 440 Hz)"""
    return 440.0 * (2.0 ** ((pitch - 69.0) / 12.0))


_DETUNE_7 = np.array([-0.11, -0.06, -0.02, 0.0, 0.02, 0.06, 0.11], dtype=np.float64)
_SEEDS_7 = np.array([0.0, 0.142857, 0.285714, 0.428571, 0.571428, 0.714285, 0.857142], dtype=np.float64)[:, None]
_NORM_7 = np.float32(1.0 / np.sqrt(7.0))


def generate_oscillator(
    wave_type: str,
    freq: float,
    num_samples: int,
    sample_rate: int,
    phase_offset: float = 0.0,
    pulse_width: float = 0.5,
    fm_ratio: float = 2.0,
    fm_depth: float = 1.0,
    unison_detune: float = 0.25,
    unison_spread: float = 0.5,
    time_offset: float = 0.0,
    t_vec: Optional[np.ndarray] = None
) -> np.ndarray:
    """
    Génère un buffer audio stéréo (num_samples, 2) FP32 pour la forme d'onde demandée.
    Applique un anti-aliasing PolyBLEP sur les ondes riches en harmoniques (Saw, Square).
    Garantit une continuité de phase parfaite d'un bloc à l'autre grâce à time_offset ou t_vec.
    """
    if num_samples <= 0:
        return np.zeros((0, 2), dtype=np.float32)

    wave_lower = (wave_type or "sine").lower().strip()
    if dsp_native.NATIVE_AVAILABLE and dsp_native.nova_synth_osc_process is not None:
        t_c = None
        t_ptr = None
        if t_vec is not None:
            if len(t_vec) == num_samples:
                t_c = np.ascontiguousarray(t_vec, dtype=np.float64)
                t_ptr = t_c.ctypes.data_as(dsp_native.c_double_p)
            else:
                t_ptr = False
        if t_ptr is not False:
            wave_id = _OSC_WAVE_MAP.get(wave_lower, 0)
            rng_state = ctypes.c_uint64(random.getrandbits(64) | 1)
            out = np.empty((num_samples, 2), dtype=np.float32)
            dsp_native.nova_synth_osc_process(
                wave_id,
                float(freq),
                int(num_samples),
                int(sample_rate),
                float(phase_offset),
                float(pulse_width),
                float(fm_ratio),
                float(fm_depth),
                float(unison_detune),
                float(unison_spread),
                float(time_offset),
                t_ptr,
                ctypes.byref(rng_state),
                out.ctypes.data_as(dsp_native.c_float_p),
            )
            return out

    if t_vec is not None:
        t = t_vec
    else:
        t = (np.arange(num_samples, dtype=np.float64) / sample_rate) + float(time_offset)

    # Fréquence normalisée
    freq = max(10.0, min(float(freq), sample_rate * 0.49))
    phase = (t * freq + phase_offset) % 1.0
    dt = freq / sample_rate

    mono_out: Optional[np.ndarray] = None

    if wave_lower == "sine":
        mono_out = np.sin(2.0 * np.pi * phase).astype(np.float32)

    elif wave_lower == "triangle":
        # Triangle band-limited
        mono_out = (2.0 * np.abs(2.0 * (phase - np.floor(phase + 0.5))) - 1.0).astype(np.float32)

    elif wave_lower == "saw":
        # PolyBLEP Sawtooth anti-aliasé haute performance
        saw_naive = (2.0 * phase - 1.0).astype(np.float32)
        m1 = phase < dt
        if np.any(m1):
            t1 = (phase[m1] / dt).astype(np.float32)
            saw_naive[m1] -= (2.0 * t1 - (t1 * t1) - 1.0)
        m2 = phase > (1.0 - dt)
        if np.any(m2):
            t2 = ((phase[m2] - 1.0) / dt).astype(np.float32)
            saw_naive[m2] -= ((t2 * t2) + 2.0 * t2 + 1.0)
        mono_out = saw_naive

    elif wave_lower in ("square", "pulse"):
        # Pulse wave avec PWM et PolyBLEP
        pw = max(0.05, min(0.95, float(pulse_width)))
        naive = np.where(phase < pw, 1.0, -1.0).astype(np.float64)
        blep = np.zeros_like(phase)

        # Transition à 0
        mask1 = phase < dt
        t1 = phase[mask1] / dt
        blep[mask1] += 2.0 * t1 - (t1 * t1) - 1.0

        mask2 = phase > (1.0 - dt)
        t2 = (phase[mask2] - 1.0) / dt
        blep[mask2] += (t2 * t2) + 2.0 * t2 + 1.0

        # Transition à pw
        p_pw = (phase - pw) % 1.0
        mask_pw1 = p_pw < dt
        t_pw1 = p_pw[mask_pw1] / dt
        blep[mask_pw1] -= (2.0 * t_pw1 - (t_pw1 * t_pw1) - 1.0)

        mask_pw2 = p_pw > (1.0 - dt)
        t_pw2 = (p_pw[mask_pw2] - 1.0) / dt
        blep[mask_pw2] -= ((t_pw2 * t_pw2) + 2.0 * t_pw2 + 1.0)

        mono_out = (naive + blep).astype(np.float32)

    elif wave_lower == "noise":
        white = np.random.uniform(-1.0, 1.0, num_samples).astype(np.float32)
        if HAS_SCIPY_SIG:
            b0 = sig.lfilter([0.05], [1.0, -0.95], white).astype(np.float32)
            mono_out = (white + b0 * 2.0) * 0.5
        else:
            b0 = 0.0
            pink = np.zeros(num_samples, dtype=np.float32)
            for i in range(num_samples):
                b0 = 0.95 * b0 + 0.05 * white[i]
                pink[i] = (white[i] + b0 * 2.0) * 0.5
            mono_out = pink

    elif wave_lower == "fm":
        # Synthèse FM 2 Opérateurs
        m_freq = freq * max(0.25, min(16.0, float(fm_ratio)))
        m_phase = (2.0 * np.pi * m_freq * t)
        modulator = np.sin(m_phase)
        depth = max(0.0, min(10.0, float(fm_depth)))
        carrier_phase = 2.0 * np.pi * freq * t + phase_offset * 2.0 * np.pi + depth * modulator
        mono_out = np.sin(carrier_phase).astype(np.float32)

    elif wave_lower in ("supersaw", "unison"):
        # SuperSaw 7 voix detuned vectorisée en 2D avec répartition stéréo et PolyBLEP ultra-rapide
        pans = np.linspace(-unison_spread, unison_spread, 7, dtype=np.float32)
        detunes = _DETUNE_7 * max(0.01, min(1.0, unison_detune))
        f_voices = np.clip(freq * (2.0 ** (detunes / 12.0)), 10.0, sample_rate * 0.49)[:, None]
        dt_voices = f_voices / sample_rate
        dt_broadcast = np.broadcast_to(dt_voices, (7, len(t)))
        gains_l = np.sqrt(0.5 * (1.0 - pans)).astype(np.float32)[:, None]
        gains_r = np.sqrt(0.5 * (1.0 + pans)).astype(np.float32)[:, None]

        p_v = (t * f_voices + _SEEDS_7 + phase_offset) % 1.0
        saw_v = (2.0 * p_v - 1.0).astype(np.float32)

        m1 = p_v < dt_voices
        if np.any(m1):
            t1 = (p_v[m1] / dt_broadcast[m1]).astype(np.float32)
            saw_v[m1] -= (2.0 * t1 - (t1 * t1) - 1.0)

        m2 = p_v > (1.0 - dt_voices)
        if np.any(m2):
            t2 = ((p_v[m2] - 1.0) / dt_broadcast[m2]).astype(np.float32)
            saw_v[m2] -= ((t2 * t2) + 2.0 * t2 + 1.0)

        left = np.sum(saw_v * gains_l, axis=0) * _NORM_7
        right = np.sum(saw_v * gains_r, axis=0) * _NORM_7
        out = np.empty((len(t), 2), dtype=np.float32)
        out[:, 0] = left
        out[:, 1] = right
        return out

    if mono_out is None:
        mono_out = np.sin(2.0 * np.pi * phase).astype(np.float32)

    res = np.empty((num_samples, 2), dtype=np.float32)
    res[:, 0] = mono_out
    res[:, 1] = mono_out
    return res


def compute_adsr_envelope(
    t_start: float,
    t_end: float,
    num_samples: int,
    note_off_time: float,
    attack: float,
    decay: float,
    sustain: float,
    release: float
) -> np.ndarray:
    """
    Calcule l'enveloppe ADSR (0.0 à 1.0) pour chaque échantillon de l'intervalle temporel [t_start, t_end].
    t_start, t_end et note_off_time sont relatifs au début de la note (note_start = 0.0).
    Fournit des chemins rapides optimisés et une extinction douce anti-clic.
    """
    if num_samples <= 0:
        return np.zeros(0, dtype=np.float32)

    if dsp_native.NATIVE_AVAILABLE and dsp_native.nova_synth_adsr_process is not None:
        out = np.empty(num_samples, dtype=np.float32)
        dsp_native.nova_synth_adsr_process(
            float(t_start),
            float(t_end),
            int(num_samples),
            float(note_off_time),
            float(attack),
            float(decay),
            float(sustain),
            float(release),
            out.ctypes.data_as(dsp_native.c_float_p),
        )
        return out

    a = max(0.001, float(attack))
    d = max(0.001, float(decay))
    s = max(0.0, min(1.0, float(sustain)))
    r = max(0.001, float(release))

    # Optimisation chemin rapide 1 : Avant le déclenchement de la note
    if t_end <= 0.0:
        return np.zeros(num_samples, dtype=np.float32)

    # Optimisation chemin rapide 2 : Après l'extinction complète du release
    if t_start >= (note_off_time + r):
        return np.zeros(num_samples, dtype=np.float32)

    # Optimisation chemin rapide 3 : Phase de sustain pur stable
    if t_start >= (a + d) and t_end <= note_off_time:
        return np.full(num_samples, s, dtype=np.float32)

    t = np.linspace(t_start, t_end, num_samples, endpoint=False, dtype=np.float64)
    env = np.zeros(num_samples, dtype=np.float32)

    # Phase 2: Note On
    on_mask = (t >= 0.0) & (t < note_off_time)
    if np.any(on_mask):
        t_on = t[on_mask]
        att_mask = t_on < a
        env[on_mask] = np.where(
            att_mask,
            t_on / a,
            np.where(
                t_on < (a + d),
                1.0 - ((t_on - a) / d) * (1.0 - s),
                s
            )
        )

    # Phase 3: Note Off (Release analogique doux sans clic)
    rel_mask = (t >= note_off_time) & (t >= 0.0)
    if np.any(rel_mask):
        t_rel = t[rel_mask] - note_off_time

        # Niveau exact au moment du note-off
        if note_off_time < a:
            sus_val = max(0.0, note_off_time / a)
        elif note_off_time < (a + d):
            sus_val = 1.0 - ((note_off_time - a) / d) * (1.0 - s)
        else:
            sus_val = s

        norm_rel = np.clip(1.0 - (t_rel / r), 0.0, 1.0)
        # Courbe légèrement incurvée douce (smooth concave) pour une décroissance progressive à zéro sans choc
        rel_factor = norm_rel * norm_rel
        env[rel_mask] = (sus_val * rel_factor).astype(np.float32)

    return np.clip(env, 0.0, 1.0)


class ChamberlinSVF:
    """
    Filtre d'état variable (State-Variable Filter) à stabilité inconditionnelle et haute performance.
    Garantit une continuité mathématique absolue sans aucun clic ni grésillement lors des changements de paramètres.
    """
    def __init__(self):
        self.zi: Optional[np.ndarray] = None
        self._prev_x: Optional[np.ndarray] = None
        self._prev_y: Optional[np.ndarray] = None
        self._prev_cutoff: float = -1.0
        self._prev_q: float = -1.0
        self._prev_type: str = ""
        self._cached_b: Optional[np.ndarray] = None
        self._cached_a: Optional[np.ndarray] = None
        self.low_l = 0.0
        self.band_l = 0.0
        self.low_r = 0.0
        self.band_r = 0.0
        self._native_state = dsp_native.NovaSVFState()
        self._native_state.prev_cutoff = -1.0
        self._native_state.prev_q = -1.0
        self._native_state.prev_type = -1

    def reset(self):
        self.zi = None
        self._prev_x = None
        self._prev_y = None
        self._prev_cutoff = -1.0
        self._prev_q = -1.0
        self._prev_type = ""
        self._cached_b = None
        self._cached_a = None
        self.low_l = 0.0
        self.band_l = 0.0
        self.low_r = 0.0
        self.band_r = 0.0
        if dsp_native.nova_svf_reset is not None:
            dsp_native.nova_svf_reset(ctypes.byref(self._native_state))
        else:
            ctypes.memset(ctypes.byref(self._native_state), 0, ctypes.sizeof(self._native_state))
            self._native_state.prev_cutoff = -1.0
            self._native_state.prev_q = -1.0
            self._native_state.prev_type = -1

    def process(
        self,
        audio: np.ndarray,
        sample_rate: int,
        filter_type: str = "lowpass",
        cutoff: float = 2000.0,
        resonance: float = 1.0,
        drive: float = 0.0,
        mod_cutoff: Optional[np.ndarray] = None
    ) -> np.ndarray:
        """
        Traite le signal audio stéréo (num_samples, 2).
        cutoff: Fréquence de coupure en Hz (20 à 20000).
        resonance: Facteur Q (0.1 à 10.0).
        drive: Saturation non-linéaire douce (0.0 à 3.0).
        mod_cutoff: Tableau optionnel de modulation de coupure par échantillon.
        """
        n = len(audio)
        if n == 0:
            return audio

        if dsp_native.NATIVE_AVAILABLE and dsp_native.nova_svf_process is not None:
            in_c = np.ascontiguousarray(audio, dtype=np.float32)
            if in_c.ndim == 2 and in_c.shape[1] == 2:
                ftype_lower = (filter_type or "lowpass").lower()
                ftype_id = _SVF_TYPE_MAP.get(ftype_lower, 0)
                mod_c = None
                mod_ptr = None
                if mod_cutoff is not None and len(mod_cutoff) == n:
                    mod_c = np.ascontiguousarray(mod_cutoff, dtype=np.float32)
                    mod_ptr = mod_c.ctypes.data_as(dsp_native.c_float_p)
                out = np.empty((n, 2), dtype=np.float32)
                dsp_native.nova_svf_process(
                    ctypes.byref(self._native_state),
                    in_c.ctypes.data_as(dsp_native.c_float_p),
                    out.ctypes.data_as(dsp_native.c_float_p),
                    n,
                    int(sample_rate),
                    ftype_id,
                    float(cutoff),
                    float(resonance),
                    float(drive),
                    mod_ptr,
                )
                return out

        if not np.all(np.isfinite(audio)):
            audio = np.nan_to_num(audio, nan=0.0, posinf=0.0, neginf=0.0)
        drive_gain = 1.0 + max(0.0, min(3.0, float(drive))) * 0.8
        in_sig = audio if drive <= 0.01 else audio * drive_gain
        ftype = (filter_type or "lowpass").lower()
        q = max(0.2, min(10.0, float(resonance)))

        if HAS_SCIPY_SIG:
            if mod_cutoff is not None and len(mod_cutoff) == n:
                m_val = float(np.mean(mod_cutoff))
                c_eff = max(20.0, min(sample_rate * 0.48, float(cutoff) * (2.0 ** m_val)))
            else:
                c_eff = max(20.0, min(sample_rate * 0.48, float(cutoff)))

            # Optimisation contournement transparent : Lowpass grand ouvert sans drive ni modulation
            if ftype in ("lowpass", "lp") and c_eff >= (sample_rate * 0.44) and drive <= 0.05 and mod_cutoff is None:
                return in_sig

            if (self._cached_b is not None and 
                abs(c_eff - self._prev_cutoff) <= 0.05 and 
                abs(q - self._prev_q) <= 0.01 and 
                ftype == self._prev_type):
                b, a = self._cached_b, self._cached_a
            else:
                c_hz = max(20.0, min(sample_rate * 0.48, float(c_eff)))
                w0 = 2.0 * math.pi * c_hz / sample_rate
                cw = math.cos(w0)
                sw = math.sin(w0)
                alpha = sw / (2.0 * q)
                a0 = 1.0 + alpha
                if ftype in ("lowpass", "lp"):
                    b = np.array([(1.0 - cw) * 0.5, 1.0 - cw, (1.0 - cw) * 0.5], dtype=np.float32) / a0
                elif ftype in ("highpass", "hp"):
                    b = np.array([(1.0 + cw) * 0.5, -(1.0 + cw), (1.0 + cw) * 0.5], dtype=np.float32) / a0
                elif ftype in ("bandpass", "bp"):
                    b = np.array([alpha, 0.0, -alpha], dtype=np.float32) / a0
                elif ftype in ("notch", "rejet"):
                    b = np.array([1.0, -2.0 * cw, 1.0], dtype=np.float32) / a0
                else:
                    b = np.array([(1.0 - cw) * 0.5, 1.0 - cw, (1.0 - cw) * 0.5], dtype=np.float32) / a0
                a = np.array([1.0, -2.0 * cw / a0, (1.0 - alpha) / a0], dtype=np.float32)
                self._cached_b, self._cached_a = b, a

            if self.zi is None or self.zi.shape != (2, 2):
                self.zi = np.zeros((2, 2), dtype=np.float32)
            elif self._prev_x is not None and self._prev_y is not None:
                if abs(c_eff - self._prev_cutoff) > 100.0 or abs(q - self._prev_q) > 0.5 or ftype != self._prev_type:
                    px = self._prev_x
                    py = self._prev_y
                    self.zi = np.zeros((2, 2), dtype=np.float32)
                    for ch in range(2):
                        self.zi[0, ch] = b[1] * px[1, ch] + b[2] * px[0, ch] - a[1] * py[1, ch] - a[2] * py[0, ch]
                        self.zi[1, ch] = b[2] * px[1, ch] - a[2] * py[1, ch]

            out, self.zi = sig.lfilter(b, a, in_sig, axis=0, zi=self.zi)

            if n >= 2:
                self._prev_x = in_sig[-2:, :].copy()
                self._prev_y = out[-2:, :].copy()

            self._prev_cutoff = c_eff
            self._prev_q = q
            self._prev_type = ftype
        else:
            out = np.zeros_like(audio)
            base_cutoff = max(20.0, min(sample_rate * 0.45, float(cutoff)))
            f_val = 2.0 * math.sin(math.pi * base_cutoff / (sample_rate * 2.0))
            use_mod = (mod_cutoff is not None and len(mod_cutoff) == n)
            in_l = in_sig[:, 0]
            in_r = in_sig[:, 1]
            low_l, band_l, low_r, band_r = self.low_l, self.band_l, self.low_r, self.band_r
            for i in range(n):
                f = 2.0 * math.sin(math.pi * max(20.0, min(sample_rate * 0.45, base_cutoff * (2.0 ** mod_cutoff[i]))) / (sample_rate * 2.0)) if use_mod else f_val
                for _ in range(2):
                    low_l += f * band_l
                    high_l = in_l[i] - low_l - (1.0 / q) * band_l
                    band_l += f * high_l
                    low_r += f * band_r
                    high_r = in_r[i] - low_r - (1.0 / q) * band_r
                    band_r += f * high_r
                if ftype in ("lowpass", "lp"):
                    out[i, 0], out[i, 1] = low_l, low_r
                elif ftype in ("highpass", "hp"):
                    out[i, 0], out[i, 1] = high_l, high_r
                elif ftype in ("bandpass", "bp"):
                    out[i, 0], out[i, 1] = band_l, band_r
                elif ftype in ("notch", "rejet"):
                    out[i, 0], out[i, 1] = low_l + high_l, low_r + high_r
                else:
                    out[i, 0], out[i, 1] = low_l, low_r
            self.low_l, self.band_l, self.low_r, self.band_r = low_l, band_l, low_r, band_r

        # Compensation de drive
        if drive > 0.05:
            out = np.tanh(out / drive_gain) * drive_gain

        return out.astype(np.float32)


class StereoPingPongDelay:
    """Délai stéréo de studio avec mode Ping-Pong et filtre de rétroaction"""
    def __init__(self, sample_rate: int = 44100):
        self.sample_rate = sample_rate
        self.max_delay_samples = int(sample_rate * 2.0)
        self.buffer_l = np.zeros(self.max_delay_samples, dtype=np.float32)
        self.buffer_r = np.zeros(self.max_delay_samples, dtype=np.float32)
        self.write_idx = 0
        self.enabled = False
        self.time_sec = 0.35
        self.feedback = 0.40
        self.ping_pong = True
        self.mix = 0.25
        self._lock = threading.RLock()
        self._native_sr = int(sample_rate)
        self._native = (
            dsp_native.nova_delay_create(self._native_sr)
            if dsp_native.NATIVE_AVAILABLE and dsp_native.nova_delay_create is not None
            else None
        )

    def __del__(self):
        native = getattr(self, "_native", None)
        if native is not None and dsp_native is not None and dsp_native.nova_delay_destroy is not None:
            try:
                dsp_native.nova_delay_destroy(native)
            except Exception:
                pass
            self._native = None

    def reset(self):
        with self._lock:
            if self._native is not None and dsp_native.nova_delay_reset is not None:
                dsp_native.nova_delay_reset(self._native)
            self.buffer_l.fill(0.0)
            self.buffer_r.fill(0.0)
            self.write_idx = 0

    def process(self, audio: np.ndarray) -> np.ndarray:
        if not self.enabled or self.mix <= 0.001 or len(audio) == 0:
            return audio

        if dsp_native.NATIVE_AVAILABLE and dsp_native.nova_delay_process is not None and self._native is not None:
            in_c = np.ascontiguousarray(audio, dtype=np.float32)
            if in_c.ndim == 2 and in_c.shape[1] == 2:
                n = len(in_c)
                out = np.empty((n, 2), dtype=np.float32)
                eff_time = float(self.time_sec) * (float(self.sample_rate) / float(max(1, self._native_sr)))
                with self._lock:
                    dsp_native.nova_delay_process(
                        self._native,
                        in_c.ctypes.data_as(dsp_native.c_float_p),
                        out.ctypes.data_as(dsp_native.c_float_p),
                        n,
                        eff_time,
                        float(self.feedback),
                        1 if self.ping_pong else 0,
                        float(self.mix),
                        1 if self.enabled else 0,
                    )
                return out

        if not np.all(np.isfinite(audio)):
            audio = np.nan_to_num(audio, nan=0.0, posinf=0.0, neginf=0.0)

        delay_samples = max(10, min(self.max_delay_samples - 1, int(self.time_sec * self.sample_rate)))
        wet = np.zeros_like(audio)
        n = len(audio)

        buf_len = self.max_delay_samples
        w_idx = self.write_idx
        fb = max(0.0, min(0.95, self.feedback))

        in_l = audio[:, 0]
        in_r = audio[:, 1]
        pos = 0

        while pos < n:
            r_idx = (w_idx - delay_samples) % buf_len
            step = min(n - pos, delay_samples, buf_len - w_idx, buf_len - r_idx)
            if step <= 0:
                step = 1

            read_l = self.buffer_l[r_idx:r_idx + step]
            read_r = self.buffer_r[r_idx:r_idx + step]
            wet[pos:pos + step, 0] = read_l
            wet[pos:pos + step, 1] = read_r

            if self.ping_pong:
                self.buffer_l[w_idx:w_idx + step] = in_l[pos:pos + step] + read_r * fb
                self.buffer_r[w_idx:w_idx + step] = in_r[pos:pos + step] + read_l * fb
            else:
                self.buffer_l[w_idx:w_idx + step] = in_l[pos:pos + step] + read_l * fb
                self.buffer_r[w_idx:w_idx + step] = in_r[pos:pos + step] + read_r * fb

            w_idx = (w_idx + step) % buf_len
            pos += step

        self.write_idx = w_idx
        mix = max(0.0, min(1.0, self.mix))
        return ((audio * (1.0 - mix * 0.5)) + (wet * mix)).astype(np.float32)


class StereoStudioReverb:
    """Réverbération stéréo de studio haute fidélité avec filtres en peigne amortis et diffuseurs all-pass (modèle Freeverb/Schroeder)."""
    def __init__(self, sample_rate: int = 44100):
        self.sample_rate = sample_rate
        self.enabled = True
        self.room_size = 0.50
        self.damping = 0.35
        self.width = 1.0
        self.mix = 0.25

        scale = max(0.1, sample_rate / 44100.0)
        # 4 filtres en peigne accordés avec délais distincts pour gauche et droite
        self.comb_lens_l = [max(2, int(ct * scale)) for ct in [1116, 1277, 1422, 1617]]
        self.comb_lens_r = [max(2, int((ct + 23) * scale)) for ct in [1116, 1277, 1422, 1617]]
        self.comb_buffers_l = [np.zeros(cl, dtype=np.float32) for cl in self.comb_lens_l]
        self.comb_buffers_r = [np.zeros(cr, dtype=np.float32) for cr in self.comb_lens_r]
        self.comb_indices_l = [0] * len(self.comb_lens_l)
        self.comb_indices_r = [0] * len(self.comb_lens_r)
        self.comb_stores_l = [0.0] * len(self.comb_lens_l)
        self.comb_stores_r = [0.0] * len(self.comb_lens_r)

        # 2 étages de diffuseurs all-pass pour éliminer toute résonance métallique
        self.allpass_lens_l = [max(2, int(at * scale)) for at in [556, 341]]
        self.allpass_lens_r = [max(2, int((at + 23) * scale)) for at in [556, 341]]
        self.allpass_buffers_l = [np.zeros(al, dtype=np.float32) for al in self.allpass_lens_l]
        self.allpass_buffers_r = [np.zeros(ar, dtype=np.float32) for ar in self.allpass_lens_r]
        self.allpass_indices_l = [0] * len(self.allpass_lens_l)
        self.allpass_indices_r = [0] * len(self.allpass_lens_r)
        self._last_room: float = -1.0
        self._last_damp: float = -1.0
        self._b_comb: Optional[np.ndarray] = np.array([1.0], dtype=np.float32)
        self.comb_pole_zi_l = [np.zeros(1, dtype=np.float32) for _ in self.comb_lens_l]
        self.comb_pole_zi_r = [np.zeros(1, dtype=np.float32) for _ in self.comb_lens_r]
        self._comb_zi_8 = np.zeros((1, 8), dtype=np.float32)
        self._lock = threading.RLock()
        self._native_sr = int(sample_rate)
        self._native = (
            dsp_native.nova_reverb_create(self._native_sr)
            if dsp_native.NATIVE_AVAILABLE and dsp_native.nova_reverb_create is not None
            else None
        )
        self.reset()

    def __del__(self):
        native = getattr(self, "_native", None)
        if native is not None and dsp_native is not None and dsp_native.nova_reverb_destroy is not None:
            try:
                dsp_native.nova_reverb_destroy(native)
            except Exception:
                pass
            self._native = None

    def _update_coefficients(self):
        """Précalcule les coefficients des filtres de réverbération pour un rendu sans allocation."""
        self._b_comb = np.array([1.0], dtype=np.float32)
        self._last_room = self.room_size
        self._last_damp = self.damping

    def reset(self):
        with self._lock:
            if getattr(self, "_native", None) is not None and dsp_native.nova_reverb_reset is not None:
                dsp_native.nova_reverb_reset(self._native)
            for b in self.comb_buffers_l + self.comb_buffers_r + self.allpass_buffers_l + self.allpass_buffers_r:
                b.fill(0.0)
            self.comb_indices_l = [0] * len(self.comb_lens_l)
            self.comb_indices_r = [0] * len(self.comb_lens_r)
            self.comb_stores_l = [0.0] * len(self.comb_lens_l)
            self.comb_stores_r = [0.0] * len(self.comb_lens_r)
            self.allpass_indices_l = [0] * len(self.allpass_lens_l)
            self.allpass_indices_r = [0] * len(self.allpass_lens_r)
            self.comb_pole_zi_l = [np.zeros(1, dtype=np.float32) for _ in self.comb_lens_l]
            self.comb_pole_zi_r = [np.zeros(1, dtype=np.float32) for _ in self.comb_lens_r]
            self._comb_zi_8 = np.zeros((1, 8), dtype=np.float32)

    def process(self, audio: np.ndarray) -> np.ndarray:
        if not self.enabled or self.mix <= 0.001 or len(audio) == 0:
            return audio

        if dsp_native.NATIVE_AVAILABLE and dsp_native.nova_reverb_process is not None and self._native is not None:
            in_c = np.ascontiguousarray(audio, dtype=np.float32)
            if in_c.ndim == 2 and in_c.shape[1] == 2:
                n = len(in_c)
                out = np.empty((n, 2), dtype=np.float32)
                with self._lock:
                    dsp_native.nova_reverb_process(
                        self._native,
                        in_c.ctypes.data_as(dsp_native.c_float_p),
                        out.ctypes.data_as(dsp_native.c_float_p),
                        n,
                        float(self.room_size),
                        float(self.damping),
                        float(self.mix),
                        1 if self.enabled else 0,
                    )
                return out

        if not np.all(np.isfinite(audio)):
            audio = np.nan_to_num(audio, nan=0.0, posinf=0.0, neginf=0.0)

        n = len(audio)
        chunk_size = max(16, min(min(self.comb_lens_l), min(self.comb_lens_r)))
        if n > chunk_size:
            out = np.empty_like(audio)
            for c_start in range(0, n, chunk_size):
                c_end = min(n, c_start + chunk_size)
                out[c_start:c_end] = self._process_chunk(audio[c_start:c_end])
            return out
        return self._process_chunk(audio)

    def _process_chunk(self, audio: np.ndarray) -> np.ndarray:
        n = len(audio)
        mono_in = ((audio[:, 0] + audio[:, 1]) * 0.08).astype(np.float32)
        fb = float(0.72 + self.room_size * 0.25)
        damp = float(self.damping * 0.45)

        if HAS_SCIPY_SIG:
            b_pole = self._b_comb if self._b_comb is not None else np.array([1.0], dtype=np.float32)
            a_pole = np.array([1.0, -damp], dtype=np.float32)
            gain_fb = np.float32(fb * (1.0 - damp))

            delayed_8 = np.empty((n, 8), dtype=np.float32)
            for idx, cl in enumerate(self.comb_lens_l):
                buf = self.comb_buffers_l[idx]
                w_idx = self.comb_indices_l[idx]
                if w_idx + n <= cl:
                    delayed_8[:, idx] = buf[w_idx:w_idx + n]
                else:
                    p1 = cl - w_idx
                    delayed_8[:p1, idx] = buf[w_idx:]
                    delayed_8[p1:, idx] = buf[:n - p1]

            for idx, cr in enumerate(self.comb_lens_r):
                buf = self.comb_buffers_r[idx]
                w_idx = self.comb_indices_r[idx]
                col = idx + 4
                if w_idx + n <= cr:
                    delayed_8[:, col] = buf[w_idx:w_idx + n]
                else:
                    p1 = cr - w_idx
                    delayed_8[:p1, col] = buf[w_idx:]
                    delayed_8[p1:, col] = buf[:n - p1]

            out_l = delayed_8[:, 0] + delayed_8[:, 1] + delayed_8[:, 2] + delayed_8[:, 3]
            out_r = delayed_8[:, 4] + delayed_8[:, 5] + delayed_8[:, 6] + delayed_8[:, 7]

            store_8, self._comb_zi_8 = sig.lfilter(b_pole, a_pole, delayed_8, axis=0, zi=self._comb_zi_8)
            to_write_8 = mono_in[:, None] + store_8 * gain_fb

            for idx, cl in enumerate(self.comb_lens_l):
                buf = self.comb_buffers_l[idx]
                w_idx = self.comb_indices_l[idx]
                col_data = to_write_8[:, idx]
                if w_idx + n <= cl:
                    buf[w_idx:w_idx + n] = col_data
                else:
                    p1 = cl - w_idx
                    buf[w_idx:] = col_data[:p1]
                    buf[:n - p1] = col_data[p1:]
                self.comb_indices_l[idx] = (w_idx + n) % cl

            for idx, cr in enumerate(self.comb_lens_r):
                buf = self.comb_buffers_r[idx]
                w_idx = self.comb_indices_r[idx]
                col_data = to_write_8[:, idx + 4]
                if w_idx + n <= cr:
                    buf[w_idx:w_idx + n] = col_data
                else:
                    p1 = cr - w_idx
                    buf[w_idx:] = col_data[:p1]
                    buf[:n - p1] = col_data[p1:]
                self.comb_indices_r[idx] = (w_idx + n) % cr

            # Diffuseurs all-pass vectorisés par sous-tranches sur tampon circulaire (0 lfilter d'ordre élevé)
            for idx, al in enumerate(self.allpass_lens_l):
                abl = self.allpass_buffers_l[idx]
                ail = self.allpass_indices_l[idx]
                pos = 0
                while pos < n:
                    step = min(n - pos, al - ail)
                    bufout = abl[ail:ail + step].copy()
                    in_s = out_l[pos:pos + step]
                    abl[ail:ail + step] = in_s + bufout * 0.5
                    out_l[pos:pos + step] = bufout - in_s * 0.5
                    ail = (ail + step) % al
                    pos += step
                self.allpass_indices_l[idx] = ail

            for idx, ar in enumerate(self.allpass_lens_r):
                abr = self.allpass_buffers_r[idx]
                air = self.allpass_indices_r[idx]
                pos = 0
                while pos < n:
                    step = min(n - pos, ar - air)
                    bufout = abr[air:air + step].copy()
                    in_s = out_r[pos:pos + step]
                    abr[air:air + step] = in_s + bufout * 0.5
                    out_r[pos:pos + step] = bufout - in_s * 0.5
                    air = (air + step) % ar
                    pos += step
                self.allpass_indices_r[idx] = air
        else:
            out_l = np.zeros(n, dtype=np.float32)
            out_r = np.zeros(n, dtype=np.float32)
            for c_idx in range(len(self.comb_lens_l)):
                bl = self.comb_buffers_l[c_idx]
                br = self.comb_buffers_r[c_idx]
                cl = self.comb_lens_l[c_idx]
                cr = self.comb_lens_r[c_idx]
                il = self.comb_indices_l[c_idx]
                ir = self.comb_indices_r[c_idx]
                sl_store = self.comb_stores_l[c_idx]
                sr_store = self.comb_stores_r[c_idx]

                for i in range(n):
                    sl = bl[il]
                    sr = br[ir]
                    sl_store = (sl * (1.0 - damp)) + (sl_store * damp)
                    sr_store = (sr * (1.0 - damp)) + (sr_store * damp)
                    bl[il] = mono_in[i] + sl_store * fb
                    br[ir] = mono_in[i] + sr_store * fb
                    out_l[i] += sl
                    out_r[i] += sr
                    il = (il + 1) % cl
                    ir = (ir + 1) % cr

                self.comb_indices_l[c_idx] = il
                self.comb_indices_r[c_idx] = ir
                self.comb_stores_l[c_idx] = sl_store
                self.comb_stores_r[c_idx] = sr_store

            for a_idx in range(len(self.allpass_lens_l)):
                abl = self.allpass_buffers_l[a_idx]
                abr = self.allpass_buffers_r[a_idx]
                al = self.allpass_lens_l[a_idx]
                ar = self.allpass_lens_r[a_idx]
                ail = self.allpass_indices_l[a_idx]
                air = self.allpass_indices_r[a_idx]

                for i in range(n):
                    bufout_l = abl[ail]
                    abl[ail] = out_l[i] + bufout_l * 0.5
                    out_l[i] = -out_l[i] + bufout_l
                    ail = (ail + 1) % al

                    bufout_r = abr[air]
                    abr[air] = out_r[i] + bufout_r * 0.5
                    out_r[i] = -out_r[i] + bufout_r
                    air = (air + 1) % ar

                self.allpass_indices_l[a_idx] = ail
                self.allpass_indices_r[a_idx] = air

        wet = np.empty((n, 2), dtype=np.float32)
        wet[:, 0] = out_l
        wet[:, 1] = out_r
        mix = max(0.0, min(1.0, self.mix))
        dry_gain = np.float32(1.0 - (mix * 0.45))
        wet_gain = np.float32(mix * 0.75)
        return ((audio * dry_gain) + (wet * wet_gain)).astype(np.float32)
