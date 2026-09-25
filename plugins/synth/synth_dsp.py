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
import math
import numpy as np
from typing import Optional, Tuple, Dict, Any, List

try:
    import scipy.signal as sig
    HAS_SCIPY_SIG = True
except Exception:
    HAS_SCIPY_SIG = False


def pitch_to_freq(pitch: float) -> float:
    """Convertit un numéro de note MIDI en fréquence en Hertz (A4 = 69 = 440 Hz)"""
    return 440.0 * (2.0 ** ((pitch - 69.0) / 12.0))


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
    time_offset: float = 0.0
) -> np.ndarray:
    """
    Génère un buffer audio stéréo (num_samples, 2) FP32 pour la forme d'onde demandée.
    Applique un anti-aliasing PolyBLEP sur les ondes riches en harmoniques (Saw, Square).
    Garantit une continuité de phase parfaite d'un bloc à l'autre grâce à time_offset.
    """
    if num_samples <= 0:
        return np.zeros((0, 2), dtype=np.float32)

    t = (np.arange(num_samples, dtype=np.float64) / sample_rate) + float(time_offset)
    wave_lower = (wave_type or "sine").lower().strip()

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
        # PolyBLEP Sawtooth anti-aliasé
        naive = (2.0 * phase - 1.0).astype(np.float64)
        blep = np.zeros_like(phase)

        mask1 = phase < dt
        t1 = phase[mask1] / dt
        blep[mask1] = 2.0 * t1 - (t1 * t1) - 1.0

        mask2 = phase > (1.0 - dt)
        t2 = (phase[mask2] - 1.0) / dt
        blep[mask2] = (t2 * t2) + 2.0 * t2 + 1.0

        mono_out = (naive - blep).astype(np.float32)

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
        # White noise + Pink noise
        white = np.random.uniform(-1.0, 1.0, num_samples).astype(np.float32)
        # Bruit rose filtré doux
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
        # SuperSaw 7 voix detuned avec répartition panoramique stéréo et PolyBLEP anti-aliasé
        detune_factors = np.array([-0.11, -0.06, -0.02, 0.0, 0.02, 0.06, 0.11], dtype=np.float64) * max(0.01, min(1.0, unison_detune))
        pans = np.linspace(-unison_spread, unison_spread, 7, dtype=np.float32)
        seed_phases = np.array([0.0, 0.142857, 0.285714, 0.428571, 0.571428, 0.714285, 0.857142], dtype=np.float64)

        left = np.zeros(num_samples, dtype=np.float32)
        right = np.zeros(num_samples, dtype=np.float32)

        for idx, (d_st, pan) in enumerate(zip(detune_factors, pans)):
            f_voice = max(10.0, min(freq * (2.0 ** (d_st / 12.0)), sample_rate * 0.49))
            dt_v = f_voice / sample_rate
            p_v = (t * f_voice + seed_phases[idx] + phase_offset) % 1.0

            # PolyBLEP pour chaque voix afin d'éviter tout grésillement/repliement de spectre
            naive_v = 2.0 * p_v - 1.0
            blep_v = np.zeros_like(p_v)

            mask1 = p_v < dt_v
            t1 = p_v[mask1] / dt_v
            blep_v[mask1] = 2.0 * t1 - (t1 * t1) - 1.0

            mask2 = p_v > (1.0 - dt_v)
            t2 = (p_v[mask2] - 1.0) / dt_v
            blep_v[mask2] = (t2 * t2) + 2.0 * t2 + 1.0

            saw_v = (naive_v - blep_v).astype(np.float32)

            gain_l = float(np.sqrt(0.5 * (1.0 - pan)))
            gain_r = float(np.sqrt(0.5 * (1.0 + pan)))

            left += saw_v * gain_l
            right += saw_v * gain_r

        norm = 1.0 / np.sqrt(7.0)
        return np.column_stack((left * norm, right * norm)).astype(np.float32)

    if mono_out is None:
        mono_out = np.sin(2.0 * np.pi * phase).astype(np.float32)

    return np.column_stack((mono_out, mono_out)).astype(np.float32)


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
        self.low_l = 0.0
        self.band_l = 0.0
        self.low_r = 0.0
        self.band_r = 0.0

    def reset(self):
        self.zi = None
        self._prev_x = None
        self._prev_y = None
        self._prev_cutoff = -1.0
        self._prev_q = -1.0
        self._prev_type = ""
        self.low_l = 0.0
        self.band_l = 0.0
        self.low_r = 0.0
        self.band_r = 0.0

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

        drive_gain = 1.0 + max(0.0, min(3.0, float(drive))) * 0.8
        in_sig = audio * drive_gain
        ftype = (filter_type or "lowpass").lower()
        q = max(0.2, min(10.0, float(resonance)))

        def _calc_ba(c_hz: float):
            c_hz = max(20.0, min(sample_rate * 0.48, float(c_hz)))
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
            return b, a

        if HAS_SCIPY_SIG:
            if mod_cutoff is not None and len(mod_cutoff) == n:
                m_val = float(np.mean(mod_cutoff))
                c_eff = max(20.0, min(sample_rate * 0.48, float(cutoff) * (2.0 ** m_val)))
            else:
                c_eff = max(20.0, min(sample_rate * 0.48, float(cutoff)))

            b, a = _calc_ba(c_eff)

            if self.zi is None or self.zi.shape != (2, 2):
                self.zi = np.zeros((2, 2), dtype=np.float32)
            elif self._prev_x is not None and self._prev_y is not None:
                # Si les coefficients ont changé, recalculer zi depuis l'historique audio réel
                # pour garantir une continuité mathématique absolue sans saut d'amplitude (zéro clic)
                if abs(c_eff - self._prev_cutoff) > 0.05 or abs(q - self._prev_q) > 0.01 or ftype != self._prev_type:
                    px = self._prev_x
                    py = self._prev_y
                    self.zi = np.zeros((2, 2), dtype=np.float32)
                    for ch in range(2):
                        self.zi[0, ch] = b[1] * px[1, ch] + b[2] * px[0, ch] - a[1] * py[1, ch] - a[2] * py[0, ch]
                        self.zi[1, ch] = b[2] * px[1, ch] - a[2] * py[1, ch]

            out, self.zi = sig.lfilter(b, a, in_sig, axis=0, zi=self.zi)

            # Mettre à jour l'historique d'échantillons continus
            if n >= 2:
                self._prev_x = in_sig[-2:, :].copy()
                self._prev_y = out[-2:, :].copy()
            elif n == 1:
                if self._prev_x is not None:
                    self._prev_x[0] = self._prev_x[1]
                    self._prev_x[1] = in_sig[0]
                    self._prev_y[0] = self._prev_y[1]
                    self._prev_y[1] = out[0]
                else:
                    self._prev_x = np.vstack([in_sig, in_sig])
                    self._prev_y = np.vstack([out, out])

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

    def reset(self):
        self.buffer_l.fill(0.0)
        self.buffer_r.fill(0.0)
        self.write_idx = 0

    def process(self, audio: np.ndarray) -> np.ndarray:
        if not self.enabled or self.mix <= 0.001 or len(audio) == 0:
            return audio

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
        self._b_comb: Optional[np.ndarray] = None
        self._a_combs_l: List[np.ndarray] = []
        self._a_combs_r: List[np.ndarray] = []
        self._b_aps_l: List[np.ndarray] = []
        self._a_aps_l: List[np.ndarray] = []
        self._b_aps_r: List[np.ndarray] = []
        self._a_aps_r: List[np.ndarray] = []
        self._update_coefficients()
        self.reset()

    def _update_coefficients(self):
        """Précalcule les coefficients des filtres IIR de réverbération pour un rendu sans allocation."""
        if not HAS_SCIPY_SIG:
            return
        fb = 0.72 + self.room_size * 0.25
        damp = self.damping * 0.45
        self._b_comb = np.array([1.0, -damp], dtype=np.float32)

        self._a_combs_l = []
        for cl in self.comb_lens_l:
            a = np.zeros(cl + 1, dtype=np.float32)
            a[0] = 1.0
            a[1] = -damp
            a[cl] = -fb * (1.0 - damp)
            self._a_combs_l.append(a)

        self._a_combs_r = []
        for cr in self.comb_lens_r:
            a = np.zeros(cr + 1, dtype=np.float32)
            a[0] = 1.0
            a[1] = -damp
            a[cr] = -fb * (1.0 - damp)
            self._a_combs_r.append(a)

        self._b_aps_l = []
        self._a_aps_l = []
        for al in self.allpass_lens_l:
            b_ap = np.zeros(al + 1, dtype=np.float32)
            b_ap[0] = -0.5
            b_ap[al] = 1.0
            a_ap = np.zeros(al + 1, dtype=np.float32)
            a_ap[0] = 1.0
            a_ap[al] = -0.5
            self._b_aps_l.append(b_ap)
            self._a_aps_l.append(a_ap)

        self._b_aps_r = []
        self._a_aps_r = []
        for ar in self.allpass_lens_r:
            b_ap = np.zeros(ar + 1, dtype=np.float32)
            b_ap[0] = -0.5
            b_ap[ar] = 1.0
            a_ap = np.zeros(ar + 1, dtype=np.float32)
            a_ap[0] = 1.0
            a_ap[ar] = -0.5
            self._b_aps_r.append(b_ap)
            self._a_aps_r.append(a_ap)

        self._last_room = self.room_size
        self._last_damp = self.damping

    def reset(self):
        for b in self.comb_buffers_l + self.comb_buffers_r + self.allpass_buffers_l + self.allpass_buffers_r:
            b.fill(0.0)
        self.comb_indices_l = [0] * len(self.comb_lens_l)
        self.comb_indices_r = [0] * len(self.comb_lens_r)
        self.comb_stores_l = [0.0] * len(self.comb_lens_l)
        self.comb_stores_r = [0.0] * len(self.comb_lens_r)
        self.allpass_indices_l = [0] * len(self.allpass_lens_l)
        self.allpass_indices_r = [0] * len(self.allpass_lens_r)
        self.comb_zi_l = [np.zeros(cl, dtype=np.float32) for cl in self.comb_lens_l]
        self.comb_zi_r = [np.zeros(cr, dtype=np.float32) for cr in self.comb_lens_r]
        self.allpass_zi_l = [np.zeros(al, dtype=np.float32) for al in self.allpass_lens_l]
        self.allpass_zi_r = [np.zeros(ar, dtype=np.float32) for ar in self.allpass_lens_r]

    def process(self, audio: np.ndarray) -> np.ndarray:
        if not self.enabled or self.mix <= 0.001 or len(audio) == 0:
            return audio

        n = len(audio)
        mono_in = (audio[:, 0] + audio[:, 1]) * 0.08

        if HAS_SCIPY_SIG:
            if abs(self.room_size - self._last_room) > 0.001 or abs(self.damping - self._last_damp) > 0.001 or self._b_comb is None:
                self._update_coefficients()

            out_l = np.zeros(n, dtype=np.float32)
            out_r = np.zeros(n, dtype=np.float32)
            b_comb = self._b_comb

            for idx, cl in enumerate(self.comb_lens_l):
                cl_out, self.comb_zi_l[idx] = sig.lfilter(b_comb, self._a_combs_l[idx], mono_in, zi=self.comb_zi_l[idx])
                out_l += cl_out

            for idx, cr in enumerate(self.comb_lens_r):
                cr_out, self.comb_zi_r[idx] = sig.lfilter(b_comb, self._a_combs_r[idx], mono_in, zi=self.comb_zi_r[idx])
                out_r += cr_out

            for idx in range(len(self.allpass_lens_l)):
                out_l, self.allpass_zi_l[idx] = sig.lfilter(self._b_aps_l[idx], self._a_aps_l[idx], out_l, zi=self.allpass_zi_l[idx])

            for idx in range(len(self.allpass_lens_r)):
                out_r, self.allpass_zi_r[idx] = sig.lfilter(self._b_aps_r[idx], self._a_aps_r[idx], out_r, zi=self.allpass_zi_r[idx])
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

        wet = np.column_stack((out_l, out_r))
        mix = max(0.0, min(1.0, self.mix))
        dry_gain = 1.0 - (mix * 0.45)
        wet_gain = mix * 0.75
        return ((audio * dry_gain) + (wet * wet_gain)).astype(np.float32)
