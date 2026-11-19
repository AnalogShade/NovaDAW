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
import math
from typing import Optional, Dict, Tuple, Any
import numpy as np

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


def _lfilter_fast(b: np.ndarray, a: np.ndarray, x: np.ndarray, zi: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Filtre IIR rapide utilisant SciPy en C avec repli NumPy pur."""
    if HAS_SCIPY:
        return sig.lfilter(b, a, x, zi=zi)
    # Repli Direct Form II Transposed en Python si SciPy absent
    n = len(x)
    order = len(a) - 1
    out = np.empty(n, dtype=np.float64)
    z = zi.copy()
    a1 = a[1:]
    b0 = b[0]
    b_rest = b[1:]
    for i in range(n):
        xi = x[i]
        yi = b0 * xi + z[0]
        out[i] = yi
        for k in range(order - 1):
            z[k] = b_rest[k] * xi - a1[k] * yi + z[k + 1]
        if order > 0:
            z[order - 1] = b_rest[order - 1] * xi - a1[order - 1] * yi
    return out, z


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
        self.sample_rate = max(8000, int(sample_rate))
        self.freq = max(25.0, min(float(freq), self.sample_rate * 0.38))
        self.w0 = 2.0 * math.pi * self.freq / float(self.sample_rate)
        self.period_total = float(self.sample_rate) / self.freq

        # 1. Paramètres du filtre de pertes (dépendant de la fréquence & de la brillance)
        bright = max(0.05, min(0.98, float(brightness)))
        mute = max(0.0, min(1.0, float(palm_mute)))
        # Pôle passe-bas : plus bright est bas ou mute est élevé, plus les aigus s'amortissent vite
        p_sustain = 0.04 + (1.0 - bright) * 0.42 + mute * 0.36
        # Légère adaptation selon la tessiture pour garder des cordes graves claires
        freq_scale = min(1.0, max(0.35, 220.0 / self.freq))
        self.p_sustain = max(0.02, min(0.84, p_sustain * (0.65 + 0.35 * freq_scale)))

        # Gain par période calculé depuis le temps T60 visé
        eff_sustain = max(0.15, float(sustain_sec) * (1.0 - 0.85 * mute))
        # Les notes aiguës s'éteignent naturellement un peu plus vite
        eff_sustain *= (180.0 / max(80.0, self.freq)) ** 0.32
        eff_sustain = max(0.12, min(14.0, eff_sustain))
        self.g_sustain = min(0.9996, max(0.90, 10.0 ** (-3.0 / (eff_sustain * self.freq))))

        # Paramètres en phase de relâchement (Note-Off / étouffement doigt)
        # On calibre -30 dB à rel_t (soit T60 = 2.0 * rel_t) afin que la corde reste
        # clairement audible pendant toute la durée choisie en ms après le Note-Off.
        rel_t = max(0.04, float(release_sec))
        self.g_release = min(0.9994, max(0.32, 10.0 ** (-1.5 / (rel_t * self.freq))))
        # Plus le relâchement est court, plus l'étouffement des aigus est marqué (muting) ;
        # sur un relâchement long, la corde continue de vibrer librement avec ses harmoniques.
        rel_norm = max(0.0, min(1.0, (rel_t - 0.04) / 1.16))
        extra_damp = (1.0 - rel_norm) ** 1.5
        self.p_release = min(
            0.84,
            max(self.p_sustain, self.p_sustain + extra_damp * (0.68 - self.p_sustain) + 0.03 * (1.0 - self.p_sustain)),
        )

        # 2. Filtre de dispersion (Tension / Rigidité de la corde)
        # Tension faible ou corde rigide -> dispersion négative -> partiels aigus légèrement plus hauts
        tension = max(0.0, min(1.0, float(string_tension)))
        # Coefficient all-pass négatif borné pour garantie de stabilité absolue
        self.a_disp = -0.34 * (1.0 - tension) * min(1.0, 350.0 / max(80.0, self.freq))

        # 3. Calcul des retards de phase et ajustement exact du délai fractionnaire Thiran
        tau_loss = _phase_delay_at(
            np.array([1.0 - self.p_sustain]), np.array([1.0, -self.p_sustain]), self.w0
        )
        tau_disp = _phase_delay_at(
            np.array([self.a_disp, 1.0]), np.array([1.0, self.a_disp]), self.w0
        )

        rem = self.period_total - tau_loss - tau_disp
        n_int = max(4, int(math.floor(rem)))
        delta = rem - n_int
        if delta < 0.20 and n_int > 4:
            n_int -= 1
            delta += 1.0

        # Raffinement itératif du coefficient Thiran pour une justesse parfaite même dans l'aigu
        eta = (1.0 - delta) / (1.0 + delta)
        for _ in range(3):
            tau_frac = _phase_delay_at(np.array([eta, 1.0]), np.array([1.0, eta]), self.w0)
            err = delta - tau_frac
            delta_corr = max(0.08, min(1.6, delta + err))
            eta = (1.0 - delta_corr) / (1.0 + delta_corr)
        eta = max(-0.78, min(0.78, eta))

        self.n_int = n_int
        self.eta = eta

        # Précalcul des coefficients combinés (Sustain & Release) : ordre 3
        self.b_sustain, self.a_sustain = self._build_loop_coeffs(self.g_sustain, self.p_sustain)
        self.b_release, self.a_release = self._build_loop_coeffs(self.g_release, self.p_release)

        # État interne persistant (ligne à retard circulaire + mémoire IIR + DC blocker)
        self.ring = np.zeros(self.n_int, dtype=np.float64)
        self.ring_pos = 0
        self.zi_loop = np.zeros(len(self.a_sustain) - 1, dtype=np.float64)
        self.zi_dc = np.zeros(1, dtype=np.float64)
        self.in_release = False

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
        out, self.zi_dc = _lfilter_fast(
            np.array([1.0, -1.0], dtype=np.float64),
            np.array([1.0, -0.995], dtype=np.float64),
            out,
            self.zi_dc,
        )
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

    def reset(self):
        for f in self._filters:
            f["zi"] = np.zeros(len(f["a"]) - 1, dtype=np.float64)
        self._pickup_delay_buf.fill(0.0)

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

        rng = np.random.default_rng(seed if seed is not None else (self.pitch * 131 + self.velocity * 17))
        exc_len = min(int(0.045 * self.sample_rate), max(64, int(self.plane_a.period_total * 2.5)))
        self.excitation = generate_pluck_excitation(
            length_samples=exc_len,
            period_samples=self.plane_a.period_total,
            sample_rate=self.sample_rate,
            velocity=self.velocity,
            pluck_position=pluck_position,
            pick_attack=pick_attack,
            guitar_mode=guitar_mode,
            rng=rng,
        )
        self.rendered_samples = 0

    def render_samples(self, count: int) -> np.ndarray:
        """Rend `count` échantillons mono en continu à partir de `self.rendered_samples`."""
        if count <= 0:
            return np.empty(0, dtype=np.float64)

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
