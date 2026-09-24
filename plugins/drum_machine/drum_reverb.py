"""
plugins/drum_machine/drum_reverb.py - Processeur de réverbération stéréo de studio (Freeverb / Schroeder)
Conçu pour les percussions et la batterie : ajoute de la profondeur spatiale, de la chaleur et du punch.
"""
import numpy as np


class CombFilter:
    """Filtre en peigne à rétroaction avec amortissement passe-bas (Low-pass feedback comb filter)"""
    def __init__(self, size: int):
        self.buffer = np.zeros(size, dtype=np.float32)
        self.size = size
        self.idx = 0
        self.filter_store = 0.0

    def reset(self):
        self.buffer.fill(0.0)
        self.idx = 0
        self.filter_store = 0.0

    def process_block(self, input_signal: np.ndarray, feedback: float, damp: float) -> np.ndarray:
        output = np.zeros_like(input_signal)
        n = len(input_signal)
        buf = self.buffer
        buf_len = self.size
        idx = self.idx
        fstore = self.filter_store
        damp1 = 1.0 - damp

        for i in range(n):
            out_val = buf[idx]
            # Filtre d'amortissement passe-bas simple
            fstore = out_val * damp1 + fstore * damp
            buf[idx] = input_signal[i] + fstore * feedback
            output[i] = out_val
            idx += 1
            if idx >= buf_len:
                idx = 0

        self.idx = idx
        self.filter_store = fstore
        return output


class AllpassFilter:
    """Filtre diffuseur passe-tout (All-pass filter) pour augmenter la densité des réflexions"""
    def __init__(self, size: int, feedback: float = 0.5):
        self.buffer = np.zeros(size, dtype=np.float32)
        self.size = size
        self.idx = 0
        self.feedback = feedback

    def reset(self):
        self.buffer.fill(0.0)
        self.idx = 0

    def process_block(self, input_signal: np.ndarray) -> np.ndarray:
        output = np.zeros_like(input_signal)
        n = len(input_signal)
        buf = self.buffer
        buf_len = self.size
        idx = self.idx
        fb = self.feedback

        for i in range(n):
            buf_out = buf[idx]
            in_val = input_signal[i]
            output[i] = -in_val + buf_out
            buf[idx] = in_val + (buf_out * fb)
            idx += 1
            if idx >= buf_len:
                idx = 0

        self.idx = idx
        return output


class DrumReverb:
    """
    Réverbération stéréo de studio complète.
    Intègre 8 peignes en parallèle et 4 passe-tout en série par canal stéréo.
    """
    COMB_TUNINGS_L = [1116, 1188, 1277, 1356, 1422, 1491, 1557, 1617]
    COMB_TUNINGS_R = [1139, 1211, 1300, 1379, 1445, 1514, 1580, 1640]

    ALLPASS_TUNINGS_L = [556, 441, 341, 225]
    ALLPASS_TUNINGS_R = [579, 464, 364, 248]

    def __init__(
        self,
        room_size: float = 0.5,
        damping: float = 0.5,
        width: float = 1.0,
        wet_mix: float = 0.3,
        dry_mix: float = 1.0,
        sample_rate: int = 44100
    ):
        self.sample_rate = sample_rate
        self.room_size = float(room_size)
        self.damping = float(damping)
        self.width = float(width)
        self.wet_mix = float(wet_mix)
        self.dry_mix = float(dry_mix)
        self.enabled = True

        scale = sample_rate / 44100.0

        # Peignes Gauche / Droite
        self.combs_l = [CombFilter(max(1, int(t * scale))) for t in self.COMB_TUNINGS_L]
        self.combs_r = [CombFilter(max(1, int(t * scale))) for t in self.COMB_TUNINGS_R]

        # Passe-tout Gauche / Droite
        self.allpass_l = [AllpassFilter(max(1, int(t * scale))) for t in self.ALLPASS_TUNINGS_L]
        self.allpass_r = [AllpassFilter(max(1, int(t * scale))) for t in self.ALLPASS_TUNINGS_R]

    def reset(self):
        """Réinitialise les lignes à retard lors de l'arrêt ou saut de lecture"""
        for c in self.combs_l:
            c.reset()
        for c in self.combs_r:
            c.reset()
        for a in self.allpass_l:
            a.reset()
        for a in self.allpass_r:
            a.reset()

    def process(self, input_audio: np.ndarray) -> np.ndarray:
        """
        Traite un buffer audio (N, 2) ou (N,).
        Retourne un buffer audio (N, 2) en float32.
        """
        if not self.enabled or (self.wet_mix <= 0.001 and self.dry_mix >= 0.999):
            if input_audio.ndim == 1:
                return np.column_stack((input_audio, input_audio)).astype(np.float32)
            return input_audio.astype(np.float32)

        if input_audio.ndim == 1:
            in_l = input_audio
            in_r = input_audio
        else:
            in_l = input_audio[:, 0]
            in_r = input_audio[:, 1]

        # Entrée mono sommée pour la réverbération spatiale
        in_mix = (in_l + in_r) * 0.015

        # Calcul des coefficients de rétroaction et d'amortissement
        # room_size 0.0 -> 0.7, 1.0 -> 0.98
        feedback = 0.70 + (self.room_size * 0.28)
        damp = np.clip(self.damping * 0.4, 0.0, 0.4)

        # 1. Traitement des 8 peignes en parallèle
        out_comb_l = np.zeros_like(in_mix)
        for c in self.combs_l:
            out_comb_l += c.process_block(in_mix, feedback, damp)

        out_comb_r = np.zeros_like(in_mix)
        for c in self.combs_r:
            out_comb_r += c.process_block(in_mix, feedback, damp)

        # 2. Traitement des 4 diffuseurs passe-tout en cascade
        out_ap_l = out_comb_l
        for a in self.allpass_l:
            out_ap_l = a.process_block(out_ap_l)

        out_ap_r = out_comb_r
        for a in self.allpass_r:
            out_ap_r = a.process_block(out_ap_r)

        # 3. Matrice de largeur stéréo (Mid/Side)
        wet1 = self.wet_mix * (self.width / 2.0 + 0.5)
        wet2 = self.wet_mix * ((1.0 - self.width) / 2.0)

        out_wet_l = out_ap_l * wet1 + out_ap_r * wet2
        out_wet_r = out_ap_r * wet1 + out_ap_l * wet2

        # 4. Mélange Wet / Dry
        out_l = (in_l * self.dry_mix) + out_wet_l
        out_r = (in_r * self.dry_mix) + out_wet_r

        return np.column_stack((out_l, out_r)).astype(np.float32)

    def process_burst(self, input_audio: np.ndarray) -> np.ndarray:
        """
        Version ultra-rapide vectorisée (via scipy.signal.lfilter) spécialement optimisée
        pour le pré-rendu et la mise en cache RAM instantanée des pads de batterie.
        Ne perturbe pas l'état des registres temps réel.
        """
        if not self.enabled or (self.wet_mix <= 0.001 and self.dry_mix >= 0.999):
            if input_audio.ndim == 1:
                return np.column_stack((input_audio, input_audio)).astype(np.float32)
            return input_audio.astype(np.float32)

        try:
            import scipy.signal as sig
        except ImportError:
            # Fallback direct si scipy non disponible
            return self.process(input_audio)

        if input_audio.ndim == 1:
            in_l = input_audio
            in_r = input_audio
        else:
            in_l = input_audio[:, 0]
            in_r = input_audio[:, 1]

        sr = self.sample_rate
        # Pour une réverbération de percussion vive, limiter le signal d'excitation de réverb à 1.5s
        max_rev_len = min(len(input_audio), int(1.5 * sr))
        in_mix = (in_l[:max_rev_len] + in_r[:max_rev_len]) * 0.015

        feedback = float(0.70 + (self.room_size * 0.28))
        damp = float(np.clip(self.damping * 0.4, 0.0, 0.4))

        scale = sr / 44100.0
        combs_l = [max(1, int(t * scale)) for t in self.COMB_TUNINGS_L]
        combs_r = [max(1, int(t * scale)) for t in self.COMB_TUNINGS_R]
        ap_l = [max(1, int(t * scale)) for t in self.ALLPASS_TUNINGS_L]
        ap_r = [max(1, int(t * scale)) for t in self.ALLPASS_TUNINGS_R]

        def _fast_comb(x: np.ndarray, M: int) -> np.ndarray:
            b = np.zeros(M + 2, dtype=np.float64)
            b[M] = 1.0
            b[M + 1] = -damp
            a = np.zeros(M + 1, dtype=np.float64)
            a[0] = 1.0
            a[1] = -damp
            a[M] = -feedback * (1.0 - damp)
            return sig.lfilter(b, a, x).astype(np.float32)

        def _fast_allpass(x: np.ndarray, M: int, fb: float = 0.5) -> np.ndarray:
            b = np.zeros(M + 1, dtype=np.float64)
            b[0] = -1.0
            b[M] = 1.0 + fb
            a = np.zeros(M + 1, dtype=np.float64)
            a[0] = 1.0
            a[M] = -fb
            return sig.lfilter(b, a, x).astype(np.float32)

        out_comb_l = np.zeros(max_rev_len, dtype=np.float32)
        for m in combs_l:
            out_comb_l += _fast_comb(in_mix, m)

        out_comb_r = np.zeros(max_rev_len, dtype=np.float32)
        for m in combs_r:
            out_comb_r += _fast_comb(in_mix, m)

        out_ap_l = out_comb_l
        for m in ap_l:
            out_ap_l = _fast_allpass(out_ap_l, m)

        out_ap_r = out_comb_r
        for m in ap_r:
            out_ap_r = _fast_allpass(out_ap_r, m)

        wet1 = self.wet_mix * (self.width / 2.0 + 0.5)
        wet2 = self.wet_mix * ((1.0 - self.width) / 2.0)

        out_wet_l = out_ap_l * wet1 + out_ap_r * wet2
        out_wet_r = out_ap_r * wet1 + out_ap_l * wet2

        full_len = len(input_audio)
        if full_len > max_rev_len:
            out_wet_l = np.pad(out_wet_l, (0, full_len - max_rev_len))
            out_wet_r = np.pad(out_wet_r, (0, full_len - max_rev_len))

        out_l = (in_l * self.dry_mix) + out_wet_l
        out_r = (in_r * self.dry_mix) + out_wet_r

        return np.column_stack((out_l, out_r)).astype(np.float32)

