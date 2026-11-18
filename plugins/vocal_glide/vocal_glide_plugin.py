"""
plugins/vocal_glide/vocal_glide_plugin.py - DSP et modèle de Nova Vocal Glide.

Plugin 100 % original (licence MIT) pour le nivellement automatique de la voix :
- Mesure l'enveloppe RMS de la voix (avec filtre sidechain vocal optionnel 100 Hz - 6.5 kHz)
- Pilote un fader virtuel bidirectionnel pour rapprocher la voix d'un niveau cible (Target dBFS)
- Intègre un seuil d'activité (Noise Floor / Gate) avec hystérésis et maintien pour ne jamais
  remonter le bruit de fond ou les respirations entre les phrases
- Contrôles d'agressivité (sensibilité / zone morte), vitesse (Slow-Fast), plage Boost/Cut et comportement en pause
- Supporte le mode Live (micro en direct), l'écriture temps réel via AutomationProcessContext,
  et la cristallisation instantanée vers une AutomationLane Volume réutilisant le moteur d'automatisation NovaDAW.
"""
from collections import deque
from typing import Dict, Any, Optional, List, Tuple
import math
import threading
import numpy as np

try:
    import scipy.signal
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False

from plugins.base import BasePlugin, AutomationProcessContext
from plugins.registry import register_plugin


@register_plugin(
    plugin_type_id="novadaw.vocal_glide",
    name="Nova Vocal Glide",
    category="dynamics",
    icon="🎤",
    description="Automatise le volume vocal en temps réel vers un niveau cible et cristallise la courbe en piste d'automatisation.",
    is_default=True,
)
class VocalGlidePlugin(BasePlugin):
    """
    Plugin Nova Vocal Glide modulaire pour NovaDAW.
    Suit dynamiquement le niveau moyen de la voix et fait glisser un fader virtuel
    pour maintenir une présence vocale homogène avant ou à la place d'une automatisation manuelle.
    """

    FACTORY_PRESETS: Dict[str, Dict[str, Any]] = {
        "Voix Lead (Équilibré)": {
            "target_db": -18.0,
            "activity_threshold_db": -46.0,
            "aggressiveness": 75.0,
            "speed": 55.0,
            "max_boost_db": 9.0,
            "max_cut_db": 9.0,
            "idle_behavior": "return_zero",
            "sidechain_vocal_filter": True,
            "output_trim_db": 0.0,
        },
        "Voix Douce / Ballade": {
            "target_db": -20.0,
            "activity_threshold_db": -52.0,
            "aggressiveness": 68.0,
            "speed": 38.0,
            "max_boost_db": 12.0,
            "max_cut_db": 6.0,
            "idle_behavior": "return_zero",
            "sidechain_vocal_filter": True,
            "output_trim_db": 0.0,
        },
        "Rap / Spoken Word Punchy": {
            "target_db": -14.0,
            "activity_threshold_db": -42.0,
            "aggressiveness": 88.0,
            "speed": 78.0,
            "max_boost_db": 8.0,
            "max_cut_db": 10.0,
            "idle_behavior": "return_zero",
            "sidechain_vocal_filter": True,
            "output_trim_db": 0.0,
        },
        "Chœurs / Backing Vocals": {
            "target_db": -22.0,
            "activity_threshold_db": -44.0,
            "aggressiveness": 82.0,
            "speed": 50.0,
            "max_boost_db": 6.0,
            "max_cut_db": 10.0,
            "idle_behavior": "hold",
            "sidechain_vocal_filter": True,
            "output_trim_db": 0.0,
        },
        "Podcast / Voix Off Broadcast": {
            "target_db": -16.0,
            "activity_threshold_db": -48.0,
            "aggressiveness": 90.0,
            "speed": 65.0,
            "max_boost_db": 10.0,
            "max_cut_db": 10.0,
            "idle_behavior": "return_zero",
            "sidechain_vocal_filter": True,
            "output_trim_db": 0.0,
        },
        "Transparent (Subtil)": {
            "target_db": -18.0,
            "activity_threshold_db": -45.0,
            "aggressiveness": 45.0,
            "speed": 40.0,
            "max_boost_db": 6.0,
            "max_cut_db": 6.0,
            "idle_behavior": "return_zero",
            "sidechain_vocal_filter": True,
            "output_trim_db": 0.0,
        },
    }

    def __init__(
        self,
        instance_id: Optional[str] = None,
        target_db: float = -18.0,
        activity_threshold_db: float = -46.0,
        aggressiveness: float = 75.0,
        speed: float = 55.0,
        max_boost_db: float = 9.0,
        max_cut_db: float = 9.0,
        idle_behavior: str = "return_zero",
        sidechain_vocal_filter: bool = True,
        output_trim_db: float = 0.0,
        write_automation_live: bool = False,
        crystallize_auto_bypass: bool = True,
        crystallize_detail: str = "musical",
    ):
        super().__init__(
            plugin_type_id="novadaw.vocal_glide",
            name="Nova Vocal Glide",
            category="dynamics",
            icon="🎤",
            instance_id=instance_id,
        )
        self.description = (
            "Automatise le volume vocal vers un niveau cible et génère une piste d'automatisation."
        )
        self.supports_automation_write = True
        self.automatable_outputs = ["volume"]

        # Paramètres utilisateur
        self.target_db: float = self._clamp(float(target_db), -42.0, -3.0)
        self.activity_threshold_db: float = self._clamp(float(activity_threshold_db), -72.0, -12.0)
        self.aggressiveness: float = self._clamp(float(aggressiveness), 0.0, 100.0)
        self.speed: float = self._clamp(float(speed), 0.0, 100.0)
        self.max_boost_db: float = self._clamp(float(max_boost_db), 0.0, 15.0)
        self.max_cut_db: float = self._clamp(float(max_cut_db), 0.0, 15.0)
        self.idle_behavior: str = idle_behavior if idle_behavior in ("return_zero", "hold") else "return_zero"
        self.sidechain_vocal_filter: bool = bool(sidechain_vocal_filter)
        self.output_trim_db: float = self._clamp(float(output_trim_db), -12.0, 12.0)
        self.write_automation_live: bool = bool(write_automation_live)
        self.crystallize_auto_bypass: bool = bool(crystallize_auto_bypass)
        self.crystallize_detail: str = crystallize_detail if crystallize_detail in ("precise", "musical", "smooth") else "musical"

        # État DSP temps réel
        self._env_rms_sq: float = 0.0
        self._glide_gain_db: float = 0.0
        self._prev_lin_gain: float = 1.0
        self._vocal_active: bool = False
        self._hold_timer_sec: float = 0.0
        self._last_auto_write_beat: float = -1e9

        # Cache filtres sidechain
        self._sc_sr: Optional[int] = None
        self._sc_hpf_b: Optional[np.ndarray] = None
        self._sc_hpf_a: Optional[np.ndarray] = None
        self._sc_hpf_zi: Optional[np.ndarray] = None
        self._sc_lpf_b: Optional[np.ndarray] = None
        self._sc_lpf_a: Optional[np.ndarray] = None
        self._sc_lpf_zi: Optional[np.ndarray] = None

        # Télémétrie temps réel lue par l'UI (30 FPS)
        self.current_input_db: float = -60.0
        self.current_output_db: float = -60.0
        self.current_glide_gain_db: float = 0.0
        self.is_vocal_active: bool = False
        self._history_lock = threading.Lock()
        self.history: deque = deque(maxlen=220)

    @staticmethod
    def _clamp(val: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, float(val)))

    def get_factory_presets(self) -> Dict[str, Dict[str, Any]]:
        return self.FACTORY_PRESETS

    def reset(self) -> None:
        """Réinitialise les enveloppes, filtres et historiques internes."""
        self._env_rms_sq = 0.0
        self._glide_gain_db = 0.0
        self._prev_lin_gain = 10.0 ** (self.output_trim_db / 20.0)
        self._vocal_active = False
        self._hold_timer_sec = 0.0
        self._last_auto_write_beat = -1e9
        self._sc_hpf_zi = None
        self._sc_lpf_zi = None
        self.current_input_db = -60.0
        self.current_output_db = -60.0
        self.current_glide_gain_db = 0.0
        self.is_vocal_active = False
        with self._history_lock:
            self.history.clear()

    def decay_meters(self, rate_db: float = 1.6) -> None:
        """Décroissance douce des indicateurs lorsque l'audio s'arrête."""
        if self.current_input_db > -60.0:
            self.current_input_db = max(-60.0, self.current_input_db - rate_db)
        if self.current_output_db > -60.0:
            self.current_output_db = max(-60.0, self.current_output_db - rate_db)

    def get_history_snapshot(self) -> List[Tuple[float, float, float, bool]]:
        """Retourne une copie thread-safe de l'historique récent (in_db, out_db, gain_db, active)."""
        with self._history_lock:
            return list(self.history)

    def _ensure_sidechain_filters(self, sr: int) -> None:
        """Prépare les filtres biquad Butterworth 100 Hz HPF + 6.5 kHz LPF pour isoler la bande vocale."""
        if self._sc_sr == sr and self._sc_hpf_b is not None:
            return
        sr_f = float(max(8000, sr))
        self._sc_hpf_b, self._sc_hpf_a = self._biquad_butterworth(100.0, sr_f, highpass=True)
        lpf_freq = min(6500.0, sr_f * 0.42)
        self._sc_lpf_b, self._sc_lpf_a = self._biquad_butterworth(lpf_freq, sr_f, highpass=False)
        self._sc_sr = sr
        self._sc_hpf_zi = None
        self._sc_lpf_zi = None

    @staticmethod
    def _biquad_butterworth(freq: float, sr: float, highpass: bool) -> Tuple[np.ndarray, np.ndarray]:
        w0 = 2.0 * math.pi * max(10.0, min(freq, sr * 0.45)) / sr
        cos_w0 = math.cos(w0)
        sin_w0 = math.sin(w0)
        alpha = sin_w0 / (2.0 * 0.70710678)
        if highpass:
            b0 = (1.0 + cos_w0) * 0.5
            b1 = -(1.0 + cos_w0)
            b2 = (1.0 + cos_w0) * 0.5
        else:
            b0 = (1.0 - cos_w0) * 0.5
            b1 = 1.0 - cos_w0
            b2 = (1.0 - cos_w0) * 0.5
        a0 = 1.0 + alpha
        a1 = -2.0 * cos_w0
        a2 = 1.0 - alpha
        b = np.array([b0 / a0, b1 / a0, b2 / a0], dtype=np.float32)
        a = np.array([1.0, a1 / a0, a2 / a0], dtype=np.float32)
        return b, a

    def _compute_sidechain(self, in_buf: np.ndarray, sample_rate: int) -> np.ndarray:
        """Extrait le signal mono de détection sidechain (filtré bande vocale si activé)."""
        sc = 0.5 * (in_buf[:, 0] + in_buf[:, 1])
        if self.sidechain_vocal_filter and HAS_SCIPY and len(sc) > 0:
            self._ensure_sidechain_filters(sample_rate)
            if self._sc_hpf_zi is None:
                self._sc_hpf_zi = scipy.signal.lfilter_zi(self._sc_hpf_b, self._sc_hpf_a) * float(sc[0])
            sc, self._sc_hpf_zi = scipy.signal.lfilter(self._sc_hpf_b, self._sc_hpf_a, sc, zi=self._sc_hpf_zi)
            if self._sc_lpf_zi is None:
                self._sc_lpf_zi = scipy.signal.lfilter_zi(self._sc_lpf_b, self._sc_lpf_a) * float(sc[0])
            sc, self._sc_lpf_zi = scipy.signal.lfilter(self._sc_lpf_b, self._sc_lpf_a, sc, zi=self._sc_lpf_zi)
        return sc

    def _step_glide_envelope(self, sub_rms_sq: float, dt: float) -> Tuple[float, float, bool]:
        """
        Met à jour d'un pas `dt` l'enveloppe RMS, le détecteur d'activité et le fader virtuel.
        Retourne (level_db, glide_gain_db, vocal_active).
        """
        sr_rms_tau = 0.045
        alpha_rms = 1.0 - math.exp(-dt / sr_rms_tau)
        self._env_rms_sq += alpha_rms * (sub_rms_sq - self._env_rms_sq)
        rms_amp = math.sqrt(max(1e-12, self._env_rms_sq))
        level_db = 20.0 * math.log10(max(1e-5, rms_amp))

        # Détecteur d'activité vocale avec hystérésis (1.5 dB) et maintien (Hold 180 ms)
        thresh = self.activity_threshold_db
        hyst = 1.5
        hold_sec = 0.18
        if not self._vocal_active:
            if level_db >= thresh + hyst:
                self._vocal_active = True
                self._hold_timer_sec = hold_sec
        else:
            if level_db >= thresh:
                self._hold_timer_sec = hold_sec
            else:
                self._hold_timer_sec -= dt
                if self._hold_timer_sec <= 0.0:
                    self._vocal_active = False

        # Calcul du gain cible du fader
        aggr_ratio = self._clamp(self.aggressiveness / 100.0, 0.0, 1.0)
        deadzone_db = (1.0 - aggr_ratio) * 6.0  # 0 dB à 100%, 3 dB à 50%, 6 dB à 0%

        if self._vocal_active and aggr_ratio > 0.0:
            err = self.target_db - level_db
            if abs(err) <= deadzone_db:
                eff_err = 0.0
            else:
                eff_err = math.copysign(abs(err) - deadzone_db, err)
            desired_db = self._clamp(eff_err * aggr_ratio, -self.max_cut_db, self.max_boost_db)
        else:
            if self.idle_behavior == "hold":
                desired_db = self._clamp(self._glide_gain_db, -self.max_cut_db, self.max_boost_db)
            else:
                desired_db = 0.0

        # Constante de temps ballistique du fader selon Speed (0..100)
        sp_norm = self._clamp(self.speed / 100.0, 0.0, 1.0)
        tau_active = 0.45 * ((0.035 / 0.45) ** sp_norm)
        tau = tau_active if self._vocal_active else max(0.35, tau_active * 2.5)
        alpha_fader = 1.0 - math.exp(-dt / max(0.005, tau))

        self._glide_gain_db += alpha_fader * (desired_db - self._glide_gain_db)
        self._glide_gain_db = self._clamp(self._glide_gain_db, -self.max_cut_db, self.max_boost_db)
        return level_db, self._glide_gain_db, self._vocal_active

    def process(self, audio: np.ndarray, sample_rate: int) -> np.ndarray:
        """Traite un buffer audio stéréo en ajustant dynamiquement le volume vocal."""
        return self._process_internal(audio, sample_rate, context=None)

    def process_with_context(
        self,
        audio: np.ndarray,
        sample_rate: int,
        context: Optional[AutomationProcessContext] = None,
    ) -> np.ndarray:
        """Point d'entrée temps réel avec contexte temporel et écriture d'automatisation optionnelle."""
        return self._process_internal(audio, sample_rate, context=context)

    def _process_internal(
        self,
        audio: np.ndarray,
        sample_rate: int,
        context: Optional[AutomationProcessContext] = None,
    ) -> np.ndarray:
        if audio is None or len(audio) == 0:
            return audio

        if audio.ndim == 1:
            in_buf = np.column_stack((audio, audio)).astype(np.float32)
        else:
            in_buf = np.ascontiguousarray(audio[:, :2], dtype=np.float32)

        n_samples = len(in_buf)
        sr = max(8000, int(sample_rate))

        raw_rms = float(np.sqrt(np.mean(in_buf * in_buf))) if n_samples > 0 else 0.0
        in_db = 20.0 * math.log10(max(1e-5, raw_rms))
        self.current_input_db = in_db

        if not self.enabled:
            self._glide_gain_db *= 0.85
            self.current_glide_gain_db = self._glide_gain_db
            self.current_output_db = in_db
            self.is_vocal_active = False
            self._prev_lin_gain = 1.0
            with self._history_lock:
                self.history.append((in_db, in_db, 0.0, False))
            return in_buf

        sc = self._compute_sidechain(in_buf, sr)
        out_buf = in_buf.copy()

        sub_size = 32
        gain_curve = np.empty(n_samples, dtype=np.float32)
        prev_g = self._prev_lin_gain
        last_level_db = in_db
        last_glide_db = self._glide_gain_db
        last_active = self._vocal_active

        for start in range(0, n_samples, sub_size):
            end = min(n_samples, start + sub_size)
            chunk = sc[start:end]
            chunk_len = end - start
            dt = float(chunk_len) / float(sr)
            sub_rms_sq = float(np.mean(chunk * chunk)) if chunk_len > 0 else 0.0

            last_level_db, last_glide_db, last_active = self._step_glide_envelope(sub_rms_sq, dt)
            target_g = 10.0 ** ((last_glide_db + self.output_trim_db) / 20.0)
            if chunk_len == 1:
                gain_curve[start] = target_g
            else:
                gain_curve[start:end] = np.linspace(prev_g, target_g, chunk_len, dtype=np.float32)
            prev_g = target_g

        self._prev_lin_gain = prev_g
        out_buf[:, 0] *= gain_curve
        out_buf[:, 1] *= gain_curve

        out_rms = float(np.sqrt(np.mean(out_buf * out_buf))) if n_samples > 0 else 0.0
        out_db = 20.0 * math.log10(max(1e-5, out_rms))

        self.current_input_db = last_level_db if in_db > -59.5 else in_db
        self.current_output_db = out_db
        self.current_glide_gain_db = last_glide_db
        self.is_vocal_active = last_active

        with self._history_lock:
            self.history.append((self.current_input_db, out_db, last_glide_db, last_active))

        # Écriture temps réel optionnelle vers une piste d'automatisation (mode Write)
        if (
            context is not None
            and context.is_playing
            and self.write_automation_live
            and context.automation_sink is not None
        ):
            mid_beat = 0.5 * (context.start_beat + context.end_beat)
            if abs(mid_beat - self._last_auto_write_beat) >= 0.08:
                self._last_auto_write_beat = mid_beat
                base_vol = 0.8
                if context.read_automation is not None:
                    v = context.read_automation("volume", mid_beat)
                    if v is not None:
                        base_vol = float(v)
                lin = 10.0 ** ((last_glide_db + self.output_trim_db) / 20.0)
                auto_val = self._clamp(base_vol * lin, 0.0, 2.0)
                context.emit_automation("volume", mid_beat, auto_val, self.instance_id)

        return out_buf

    def analyze_track_to_automation_points(
        self,
        track,
        bpm: float,
        sample_rate: int = 44100,
        start_beat: Optional[float] = None,
        end_beat: Optional[float] = None,
        detail_mode: Optional[str] = None,
    ) -> Tuple[List[Tuple[float, float]], Dict[str, Any]]:
        """
        Analyse hors-ligne des clips audio d'une piste avec un clone DSP isolé
        pour générer une liste propre de points (beat, volume) prête pour AutomationLane.
        Inclut un léger lookahead (35 ms) afin d'anticiper les débuts de syllabes sans à-coup.
        """
        from core.project import AudioClip, get_automation_param_spec
        from ui.automation_lane_renderer import simplify_freehand_points

        audio_clips = [
            c for c in getattr(track, "clips", [])
            if isinstance(c, AudioClip) and c.audio_data is not None and len(c.audio_data) > 0
        ]
        if not audio_clips:
            return [], {
                "point_count": 0,
                "min_gain_db": 0.0,
                "max_gain_db": 0.0,
                "was_clamped": False,
                "analyzed_beats": 0.0,
            }

        sr = max(8000, int(sample_rate))
        bpm_safe = max(20.0, float(bpm))
        beats_per_sec = bpm_safe / 60.0

        clips_min_b = min(c.start_beat for c in audio_clips)
        clips_max_b = max(c.start_beat + c.length_beats for c in audio_clips)
        win_start = max(0.0, float(start_beat)) if start_beat is not None else max(0.0, clips_min_b)
        win_end = float(end_beat) if end_beat is not None else clips_max_b
        if win_end <= win_start:
            return [], {
                "point_count": 0,
                "min_gain_db": 0.0,
                "max_gain_db": 0.0,
                "was_clamped": False,
                "analyzed_beats": 0.0,
            }

        total_sec = (win_end - win_start) / beats_per_sec
        total_samples = max(1, int(round(total_sec * sr)))
        mix = np.zeros((total_samples, 2), dtype=np.float32)

        for clip in audio_clips:
            c_start = clip.start_beat
            c_end = c_start + clip.length_beats
            if c_end <= win_start or c_start >= win_end:
                continue

            c_sr = max(8000, int(getattr(clip, "sample_rate", sr) or sr))
            data = clip.audio_data
            if data.ndim == 1:
                stereo = np.column_stack((data, data)).astype(np.float32)
            else:
                stereo = np.ascontiguousarray(data[:, :2], dtype=np.float32)

            if c_sr != sr and HAS_SCIPY and len(stereo) > 1:
                new_len = max(1, int(round(len(stereo) * (sr / float(c_sr)))))
                x_old = np.linspace(0.0, 1.0, len(stereo), endpoint=False)
                x_new = np.linspace(0.0, 1.0, new_len, endpoint=False)
                stereo = np.column_stack((
                    np.interp(x_new, x_old, stereo[:, 0]),
                    np.interp(x_new, x_old, stereo[:, 1]),
                )).astype(np.float32)

            offset_beats = float(getattr(clip, "source_offset_beats", 0.0))
            dst_start = int(round(((c_start - win_start) / beats_per_sec) * sr))
            src_start = int(round((offset_beats / beats_per_sec) * sr))
            max_len_samples = int(round((clip.length_beats / beats_per_sec) * sr))
            readable_end = min(len(stereo), src_start + max_len_samples)

            if dst_start < 0:
                src_start += -dst_start
                dst_start = 0

            avail_src = max(0, readable_end - src_start)
            avail_dst = max(0, total_samples - dst_start)
            to_copy = min(avail_src, avail_dst)
            if to_copy > 0:
                mix[dst_start:dst_start + to_copy] += stereo[src_start:src_start + to_copy] * float(clip.gain)

        sim = VocalGlidePlugin(instance_id="sim")
        sim.set_state(self.get_state())
        sim.enabled = True
        sim.reset()
        sc = sim._compute_sidechain(mix, sr)

        lookahead_samples = int(0.035 * sr)
        block_samples = max(128, int(0.025 * sr))
        base_vol = float(getattr(track, "volume", 0.8))
        vol_spec = get_automation_param_spec("volume")
        min_v = float(vol_spec["min_val"])
        max_v = float(vol_spec["max_val"])

        raw_points: List[Tuple[float, float]] = [(win_start, max(min_v, min(max_v, base_vol)))]
        min_gain_db = 0.0
        max_gain_db = 0.0
        was_clamped = False

        for start in range(0, total_samples, block_samples):
            end = min(total_samples, start + block_samples)
            la_start = min(total_samples - 1, start + lookahead_samples)
            la_end = min(total_samples, end + lookahead_samples)
            chunk = sc[la_start:la_end] if la_end > la_start else sc[start:end]
            dt = float(end - start) / float(sr)
            sub_rms_sq = float(np.mean(chunk * chunk)) if len(chunk) > 0 else 0.0

            _, glide_db, _ = sim._step_glide_envelope(sub_rms_sq, dt)
            total_db = glide_db + sim.output_trim_db
            min_gain_db = min(min_gain_db, total_db)
            max_gain_db = max(max_gain_db, total_db)

            raw_val = base_vol * (10.0 ** (total_db / 20.0))
            if raw_val > max_v or raw_val < min_v:
                was_clamped = True
            clamped_val = max(min_v, min(max_v, raw_val))

            mid_sample = 0.5 * (start + end)
            beat = win_start + (mid_sample / float(sr)) * beats_per_sec
            raw_points.append((beat, clamped_val))

        raw_points.append((win_end, max(min_v, min(max_v, base_vol))))

        mode = detail_mode or self.crystallize_detail
        span_scale = {"precise": 0.45, "musical": 1.1, "smooth": 2.4}.get(mode, 1.1)
        simplified = simplify_freehand_points(
            raw_points,
            snap_enabled=False,
            value_span=(max_v - min_v) * span_scale,
        )

        return simplified, {
            "point_count": len(simplified),
            "min_gain_db": min_gain_db,
            "max_gain_db": max_gain_db,
            "was_clamped": was_clamped,
            "analyzed_beats": win_end - win_start,
        }

    def get_state(self) -> Dict[str, Any]:
        return {
            "target_db": self.target_db,
            "activity_threshold_db": self.activity_threshold_db,
            "aggressiveness": self.aggressiveness,
            "speed": self.speed,
            "max_boost_db": self.max_boost_db,
            "max_cut_db": self.max_cut_db,
            "idle_behavior": self.idle_behavior,
            "sidechain_vocal_filter": self.sidechain_vocal_filter,
            "output_trim_db": self.output_trim_db,
            "write_automation_live": self.write_automation_live,
            "crystallize_auto_bypass": self.crystallize_auto_bypass,
            "crystallize_detail": self.crystallize_detail,
        }

    def set_state(self, state: Dict[str, Any]) -> None:
        if "target_db" in state:
            self.target_db = self._clamp(float(state["target_db"]), -42.0, -3.0)
        if "activity_threshold_db" in state:
            self.activity_threshold_db = self._clamp(float(state["activity_threshold_db"]), -72.0, -12.0)
        if "aggressiveness" in state:
            self.aggressiveness = self._clamp(float(state["aggressiveness"]), 0.0, 100.0)
        if "speed" in state:
            self.speed = self._clamp(float(state["speed"]), 0.0, 100.0)
        if "max_boost_db" in state:
            self.max_boost_db = self._clamp(float(state["max_boost_db"]), 0.0, 15.0)
        if "max_cut_db" in state:
            self.max_cut_db = self._clamp(float(state["max_cut_db"]), 0.0, 15.0)
        if "idle_behavior" in state and state["idle_behavior"] in ("return_zero", "hold"):
            self.idle_behavior = state["idle_behavior"]
        if "sidechain_vocal_filter" in state:
            self.sidechain_vocal_filter = bool(state["sidechain_vocal_filter"])
            self._sc_hpf_zi = None
            self._sc_lpf_zi = None
        if "output_trim_db" in state:
            self.output_trim_db = self._clamp(float(state["output_trim_db"]), -12.0, 12.0)
        if "write_automation_live" in state:
            self.write_automation_live = bool(state["write_automation_live"])
        if "crystallize_auto_bypass" in state:
            self.crystallize_auto_bypass = bool(state["crystallize_auto_bypass"])
        if "crystallize_detail" in state and state["crystallize_detail"] in ("precise", "musical", "smooth"):
            self.crystallize_detail = state["crystallize_detail"]

    def create_editor(self, parent=None):
        """Instancie l'interface graphique PySide6 dédiée à Nova Vocal Glide."""
        from plugins.vocal_glide.vocal_glide_gui import VocalGlideWidget
        return VocalGlideWidget(self, parent)
