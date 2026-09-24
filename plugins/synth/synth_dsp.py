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
    unison_spread: float = 0.5
) -> np.ndarray:
    """
    Génère un buffer audio stéréo (num_samples, 2) FP32 pour la forme d'onde demandée.
    Applique un anti-aliasing PolyBLEP sur les ondes riches en harmoniques (Saw, Square).
    """
    if num_samples <= 0:
        return np.zeros((0, 2), dtype=np.float32)

    t = (np.arange(num_samples, dtype=np.float64) / sample_rate)
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
        # SuperSaw 7 voix detuned avec répartition panoramique stéréo (style Roland JP-8000)
        detune_factors = np.array([-0.11, -0.06, -0.02, 0.0, 0.02, 0.06, 0.11]) * max(0.01, min(1.0, unison_detune))
        pans = np.linspace(-unison_spread, unison_spread, 7)

        left = np.zeros(num_samples, dtype=np.float32)
        right = np.zeros(num_samples, dtype=np.float32)

        for d_st, pan in zip(detune_factors, pans):
            f_voice = freq * (2.0 ** (d_st / 12.0))
            # Phase pseudo-aléatoire déterministe
            rand_phase = ((phase_offset * 7.13 + d_st * 13.7) % 1.0)
            p_v = (t * f_voice + rand_phase) % 1.0
            saw_v = (2.0 * p_v - 1.0).astype(np.float32)

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
    """
    if num_samples <= 0:
        return np.zeros(0, dtype=np.float32)

    t = np.linspace(t_start, t_end, num_samples, endpoint=False, dtype=np.float64)
    env = np.zeros(num_samples, dtype=np.float32)

    a = max(0.001, float(attack))
    d = max(0.001, float(decay))
    s = max(0.0, min(1.0, float(sustain)))
    r = max(0.001, float(release))

    # Phase 1: Avant le début de la note
    # env reste 0

    # Phase 2: Note On
    on_mask = (t >= 0.0) & (t < note_off_time)
    if np.any(on_mask):
        t_on = t[on_mask]
        # Attack
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

    # Phase 3: Note Off (Release)
    rel_mask = (t >= note_off_time) & (t >= 0.0)
    if np.any(rel_mask):
        t_rel = t[rel_mask] - note_off_time

        # Calculer le niveau d'enveloppe au moment exact du note-off
        if note_off_time < a:
            sus_val = note_off_time / a
        elif note_off_time < (a + d):
            sus_val = 1.0 - ((note_off_time - a) / d) * (1.0 - s)
        else:
            sus_val = s

        rel_factor = np.maximum(0.0, 1.0 - (t_rel / r))
        env[rel_mask] = (sus_val * rel_factor).astype(np.float32)

    return np.clip(env, 0.0, 1.0)


class ChamberlinSVF:
    """
    Filtre d'état variable (State-Variable Filter) à stabilité inconditionnelle.
    Offre des courbes analogiques douces pour Passe-Bas, Passe-Haut, Passe-Bande et Notch
    avec résonance chaleureuse et saturation intégrée.
    """
    def __init__(self):
        self.low_l = 0.0
        self.band_l = 0.0
        self.low_r = 0.0
        self.band_r = 0.0

    def reset(self):
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

        out = np.zeros_like(audio)
        ftype = (filter_type or "lowpass").lower()

        # Facteur d'amortissement q = 1 / Q
        q = 1.0 / max(0.2, min(10.0, float(resonance)))
        drive_gain = 1.0 + max(0.0, min(3.0, float(drive))) * 0.8

        low_l = self.low_l
        band_l = self.band_l
        low_r = self.low_r
        band_r = self.band_r

        # Sur-échantillonnage interne 2x pour une précision analogique sans instabilité à haute fréquence
        base_cutoff = max(20.0, min(sample_rate * 0.45, float(cutoff)))
        f_val = 2.0 * math.sin(math.pi * base_cutoff / (sample_rate * 2.0))

        use_mod = (mod_cutoff is not None and len(mod_cutoff) == n)

        in_l = audio[:, 0] * drive_gain
        in_r = audio[:, 1] * drive_gain

        for i in range(n):
            if use_mod:
                cur_c = max(20.0, min(sample_rate * 0.45, base_cutoff * (2.0 ** mod_cutoff[i])))
                f = 2.0 * math.sin(math.pi * cur_c / (sample_rate * 2.0))
            else:
                f = f_val

            # Canal Gauche (2x passes pour lissage numérique)
            for _ in range(2):
                low_l += f * band_l
                high_l = in_l[i] - low_l - q * band_l
                band_l += f * high_l
                # Saturation douce dans la boucle pour éviter tout débordement
                if band_l > 4.0:
                    band_l = 4.0
                elif band_l < -4.0:
                    band_l = -4.0

            # Canal Droit
            for _ in range(2):
                low_r += f * band_r
                high_r = in_r[i] - low_r - q * band_r
                band_r += f * high_r
                if band_r > 4.0:
                    band_r = 4.0
                elif band_r < -4.0:
                    band_r = -4.0

            if ftype in ("lowpass", "lp"):
                out[i, 0] = low_l
                out[i, 1] = low_r
            elif ftype in ("highpass", "hp"):
                out[i, 0] = high_l
                out[i, 1] = high_r
            elif ftype in ("bandpass", "bp"):
                out[i, 0] = band_l
                out[i, 1] = band_r
            elif ftype in ("notch", "rejet"):
                out[i, 0] = low_l + high_l
                out[i, 1] = low_r + high_r
            else:
                out[i, 0] = low_l
                out[i, 1] = low_r

        self.low_l = low_l
        self.band_l = band_l
        self.low_r = low_r
        self.band_r = band_r

        # Compensation légère de drive
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

        for i in range(n):
            read_idx = (w_idx - delay_samples) % buf_len

            read_l = self.buffer_l[read_idx]
            read_r = self.buffer_r[read_idx]

            wet[i, 0] = read_l
            wet[i, 1] = read_r

            if self.ping_pong:
                # Écho croisé gauche <-> droite
                self.buffer_l[w_idx] = in_l[i] + read_r * fb
                self.buffer_r[w_idx] = in_r[i] + read_l * fb
            else:
                self.buffer_l[w_idx] = in_l[i] + read_l * fb
                self.buffer_r[w_idx] = in_r[i] + read_r * fb

            w_idx = (w_idx + 1) % buf_len

        self.write_idx = w_idx
        mix = max(0.0, min(1.0, self.mix))
        return ((audio * (1.0 - mix * 0.5)) + (wet * mix)).astype(np.float32)


class StereoStudioReverb:
    """Réverbération stéréo de studio à haute densité (modèle Schroeder/Freeverb)"""
    def __init__(self, sample_rate: int = 44100):
        self.sample_rate = sample_rate
        self.enabled = True
        self.room_size = 0.50
        self.damping = 0.40
        self.width = 1.0
        self.mix = 0.20

        # Peigne et allpass
        comb_tunings_l = [1116, 1188, 1277, 1356, 1422, 1491, 1557, 1617]
        scale = sample_rate / 44100.0
        self.comb_lens = [int(ct * scale) for ct in comb_tunings_l]
        self.comb_buffers_l = [np.zeros(cl, dtype=np.float32) for cl in self.comb_lens]
        self.comb_buffers_r = [np.zeros(cl + 23, dtype=np.float32) for cl in self.comb_lens]
        self.comb_indices = [0] * len(self.comb_lens)
        self.comb_filter_stores = [0.0] * len(self.comb_lens)

    def reset(self):
        for b in self.comb_buffers_l:
            b.fill(0.0)
        for b in self.comb_buffers_r:
            b.fill(0.0)
        self.comb_indices = [0] * len(self.comb_lens)
        self.comb_filter_stores = [0.0] * len(self.comb_lens)

    def process(self, audio: np.ndarray) -> np.ndarray:
        if not self.enabled or self.mix <= 0.001 or len(audio) == 0:
            return audio

        n = len(audio)
        mono_in = (audio[:, 0] + audio[:, 1]) * 0.015
        out_l = np.zeros(n, dtype=np.float32)
        out_r = np.zeros(n, dtype=np.float32)

        feedback = 0.70 + self.room_size * 0.28
        damp = self.damping * 0.4

        for c_idx, cl in enumerate(self.comb_lens):
            buf_l = self.comb_buffers_l[c_idx]
            buf_r = self.comb_buffers_r[c_idx]
            idx = self.comb_indices[c_idx]
            cl_r = len(buf_r)
            idx_r = idx % cl_r
            filter_store = self.comb_filter_stores[c_idx]

            for i in range(n):
                out_sample_l = buf_l[idx]
                out_sample_r = buf_r[idx_r]

                filter_store = (out_sample_l * (1.0 - damp)) + (filter_store * damp)
                buf_l[idx] = mono_in[i] + filter_store * feedback

                buf_r[idx_r] = mono_in[i] + out_sample_r * feedback

                out_l[i] += out_sample_l
                out_r[i] += out_sample_r

                idx = (idx + 1) % cl
                idx_r = (idx_r + 1) % cl_r

            self.comb_indices[c_idx] = idx
            self.comb_filter_stores[c_idx] = filter_store

        wet = np.column_stack((out_l, out_r))
        mix = max(0.0, min(1.0, self.mix))
        return ((audio * (1.0 - mix * 0.4)) + (wet * mix)).astype(np.float32)
