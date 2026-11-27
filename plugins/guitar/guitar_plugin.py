"""
plugins/guitar/guitar_plugin.py - Plugin d'instrument virtuel Nova Guitar pour NovaDAW.

Modélisation physique par guides d'ondes numériques (Digital Waveguide Synthesis) sous licence MIT :
- Prend en compte la tension / rigidité de la corde (inharmonicité physique),
  la position d'attaque du médiator/doigt, la brillance, le palm mute et la double polarisation.
- Deux familles sonores dédiées :
  1. Guitare Acoustique (caisse de résonance modale : Nylon Classique, Folk Acier)
  2. Guitare Électrique DI Clean (micro magnétique : Strat Manche, Tele Chevalet, Jazz Hollowbody, Basse)
- Entièrement compatible avec le séquenceur MIDI, le Piano Roll, l'ASIO-Guard,
  le Gestionnaire de Presets universel et le bouton Bypass de NovaDAW.
"""
import copy
import math
import threading
from typing import Dict, Any, Optional, List, Tuple
import numpy as np

from plugins.base import BasePlugin
from plugins.registry import register_plugin
from plugins.synth.synth_dsp import StereoStudioReverb
from plugins.guitar.guitar_dsp import (
    GuitarStringVoice,
    GuitarBodyOrPickupProcessor,
    GuitarStereoWidener,
)


@register_plugin(
    plugin_type_id="novadaw.guitar",
    name="Nova Guitar",
    category="instrument",
    icon="🎸",
    description="Guitare à modélisation physique Digital Waveguide (Acoustique Nylon/Acier & Électrique DI).",
    is_default=True,
)
class NovaGuitarPlugin(BasePlugin):
    """
    Instrument virtuel Nova Guitar à modélisation physique pour NovaDAW.
    Transforme les notes MIDI du séquenceur en vibrations réalistes de cordes de guitare.
    """

    FACTORY_PRESETS: Dict[str, Dict[str, Any]] = {
        "Acoustique Folk Acier (Dreadnought)": {
            "guitar_mode": "acoustic_steel",
            "string_tension": 0.62,
            "pluck_position": 0.22,
            "brightness": 0.72,
            "sustain_sec": 4.8,
            "release_sec": 0.24,
            "pick_attack": 0.60,
            "palm_mute": 0.0,
            "body_resonance": 0.75,
            "pickup_position": 0.22,
            "stereo_width": 0.35,
            "reverb_mix": 0.16,
            "reverb_room": 0.45,
            "dual_polarization": True,
            "master_volume": 0.95,
        },
        "Acoustique Nylon Classique": {
            "guitar_mode": "acoustic_nylon",
            "string_tension": 0.80,
            "pluck_position": 0.27,
            "brightness": 0.44,
            "sustain_sec": 3.8,
            "release_sec": 0.28,
            "pick_attack": 0.28,
            "palm_mute": 0.0,
            "body_resonance": 0.82,
            "pickup_position": 0.25,
            "stereo_width": 0.30,
            "reverb_mix": 0.20,
            "reverb_room": 0.52,
            "dual_polarization": True,
            "master_volume": 1.0,
        },
        "Électrique Strat Clean (Manche)": {
            "guitar_mode": "electric_clean",
            "string_tension": 0.70,
            "pluck_position": 0.26,
            "brightness": 0.78,
            "sustain_sec": 5.5,
            "release_sec": 0.20,
            "pick_attack": 0.58,
            "palm_mute": 0.0,
            "body_resonance": 0.55,
            "pickup_position": 0.32,
            "stereo_width": 0.15,
            "reverb_mix": 0.0,
            "reverb_room": 0.35,
            "dual_polarization": True,
            "master_volume": 0.95,
        },
        "Électrique Tele Twang (Chevalet)": {
            "guitar_mode": "electric_clean",
            "string_tension": 0.66,
            "pluck_position": 0.13,
            "brightness": 0.88,
            "sustain_sec": 5.2,
            "release_sec": 0.18,
            "pick_attack": 0.78,
            "palm_mute": 0.0,
            "body_resonance": 0.70,
            "pickup_position": 0.10,
            "stereo_width": 0.12,
            "reverb_mix": 0.0,
            "reverb_room": 0.30,
            "dual_polarization": True,
            "master_volume": 0.95,
        },
        "Électrique Jazz Hollowbody": {
            "guitar_mode": "electric_clean",
            "string_tension": 0.75,
            "pluck_position": 0.34,
            "brightness": 0.38,
            "sustain_sec": 4.2,
            "release_sec": 0.25,
            "pick_attack": 0.32,
            "palm_mute": 0.05,
            "body_resonance": 0.45,
            "pickup_position": 0.38,
            "stereo_width": 0.22,
            "reverb_mix": 0.10,
            "reverb_room": 0.42,
            "dual_polarization": True,
            "master_volume": 1.0,
        },
        "Électrique Palm Mute Rythmique": {
            "guitar_mode": "electric_clean",
            "string_tension": 0.58,
            "pluck_position": 0.14,
            "brightness": 0.76,
            "sustain_sec": 3.5,
            "release_sec": 0.12,
            "pick_attack": 0.82,
            "palm_mute": 0.62,
            "body_resonance": 0.60,
            "pickup_position": 0.11,
            "stereo_width": 0.10,
            "reverb_mix": 0.0,
            "reverb_room": 0.25,
            "dual_polarization": True,
            "master_volume": 1.0,
        },
        "Basse Électrique Clean (Pick)": {
            "guitar_mode": "electric_bass",
            "string_tension": 0.52,
            "pluck_position": 0.19,
            "brightness": 0.68,
            "sustain_sec": 5.0,
            "release_sec": 0.22,
            "pick_attack": 0.72,
            "palm_mute": 0.08,
            "body_resonance": 0.55,
            "pickup_position": 0.20,
            "stereo_width": 0.05,
            "reverb_mix": 0.0,
            "reverb_room": 0.20,
            "dual_polarization": True,
            "master_volume": 1.05,
        },
    }

    def __init__(self, instance_id: Optional[str] = None):
        super().__init__(
            plugin_type_id="novadaw.guitar",
            name="Nova Guitar",
            category="instrument",
            icon="🎸",
            instance_id=instance_id,
        )
        self.is_instrument: bool = True
        self.description = "Guitare à modélisation physique Digital Waveguide (Acoustique & Électrique DI)."

        self.preset_name: str = "Acoustique Folk Acier (Dreadnought)"

        # Paramètres physiques de la corde et de l'instrument
        self.guitar_mode: str = "acoustic_steel"  # "acoustic_steel", "acoustic_nylon", "electric_clean", "electric_bass"
        self.string_tension: float = 0.62         # 0.0 (souple/inharmonique) à 1.0 (tendue/pure)
        self.pluck_position: float = 0.22         # 0.05 (chevalet) à 0.50 (manche)
        self.brightness: float = 0.72             # 0.05 à 0.98
        self.sustain_sec: float = 4.8             # 0.5 à 10.0 sec
        self.release_sec: float = 0.24            # 0.05 à 1.5 sec
        self.pick_attack: float = 0.60            # 0.0 (doigt doux) à 1.0 (médiator dur)
        self.palm_mute: float = 0.0               # 0.0 à 1.0
        self.body_resonance: float = 0.75         # 0.0 à 1.2 (caisse acoustique ou résonance RLC micro)
        self.pickup_position: float = 0.22        # 0.08 (chevalet) à 0.40 (manche)
        self.stereo_width: float = 0.35           # 0.0 à 1.0
        self.reverb_mix: float = 0.16             # 0.0 à 0.6
        self.reverb_room: float = 0.45            # 0.1 à 0.9
        self.dual_polarization: bool = True
        self.master_volume: float = 0.95          # 0.0 à 2.0

        # État DSP temps réel
        self._dsp_lock = threading.RLock()
        self._live_voices: Dict[Tuple[Any, ...], GuitarStringVoice] = {}
        self._body_proc = GuitarBodyOrPickupProcessor(44100)
        self._body_proc_side = GuitarBodyOrPickupProcessor(44100)
        self._stereo_widener = GuitarStereoWidener()
        self._reverb = StereoStudioReverb(44100)
        self._sync_reverb()

    def _sync_reverb(self) -> None:
        self._reverb.enabled = self.reverb_mix > 0.005
        self._reverb.room_size = max(0.1, min(0.95, float(self.reverb_room)))
        self._reverb.damping = 0.45
        self._reverb.width = max(0.2, min(1.0, float(self.stereo_width) + 0.35))
        self._reverb.mix = max(0.0, min(0.65, float(self.reverb_mix)))

    def get_factory_presets(self) -> Dict[str, Dict[str, Any]]:
        return copy.deepcopy(self.FACTORY_PRESETS)

    def apply_preset(self, preset_name: str) -> bool:
        """Applique un preset d'usine ou utilisateur par son nom."""
        with self._dsp_lock:
            target = None
            for k, v in self.FACTORY_PRESETS.items():
                if k.lower() == preset_name.lower():
                    target = v
                    self.preset_name = k
                    break

            if target is None:
                try:
                    from core.preset_manager import global_preset_manager
                    if global_preset_manager.load_preset(self, preset_name):
                        return True
                except Exception:
                    pass
                return False

            self.set_state({**target, "preset_name": self.preset_name, "enabled": self.enabled})
            return True

    def notify_modified(self) -> None:
        """Notifie le moteur audio global pour rafraîchir le cache ASIO-Guard."""
        try:
            from core.audio_engine import get_global_audio_engine
            engine = get_global_audio_engine()
            if engine is not None and hasattr(engine, "notify_plugin_modified"):
                engine.notify_plugin_modified(self)
        except Exception:
            pass

    def reset(self) -> None:
        with self._dsp_lock:
            self._live_voices.clear()
            self._body_proc.reset()
            self._body_proc_side.reset()
            self._stereo_widener.reset()
            self._reverb.reset()

    def process(self, audio: np.ndarray, sample_rate: int) -> np.ndarray:
        """Instrument générateur MIDI : passe le signal existant de manière transparente."""
        if audio is None or len(audio) == 0:
            return audio
        if audio.ndim == 1:
            return np.column_stack((audio, audio)).astype(np.float32)
        return np.ascontiguousarray(audio, dtype=np.float32)

    def _voice_params_sig(self) -> Tuple[Any, ...]:
        return (
            self.guitar_mode,
            round(self.string_tension, 3),
            round(self.pluck_position, 3),
            round(self.brightness, 3),
            round(self.sustain_sec, 3),
            round(self.release_sec, 3),
            round(self.pick_attack, 3),
            round(self.palm_mute, 3),
            bool(self.dual_polarization),
        )

    def _create_voice(self, pitch: int, velocity: int, note_dur_samples: int, sample_rate: int) -> GuitarStringVoice:
        return GuitarStringVoice(
            pitch=pitch,
            velocity=velocity,
            note_dur_samples=note_dur_samples,
            sample_rate=sample_rate,
            guitar_mode=self.guitar_mode,
            string_tension=self.string_tension,
            pluck_position=self.pluck_position,
            brightness=self.brightness,
            sustain_sec=self.sustain_sec,
            release_sec=self.release_sec,
            pick_attack=self.pick_attack,
            palm_mute=self.palm_mute,
            dual_polarization=self.dual_polarization,
        )

    def get_release_tail_sec(self) -> float:
        """Retourne la durée de queue (en secondes) requise après un Note-Off."""
        rev_pad = 0.35 if self.reverb_mix > 0.005 else 0.08
        return max(0.18, min(4.2, float(self.release_sec) * 2.1 + rev_pad))

    def create_isolated_context(self, sample_rate: int = 44100) -> Dict[str, Any]:
        """Crée un contexte DSP isolé pour les rendus offline ASIO-Guard sans perturber la lecture live."""
        rev = StereoStudioReverb(sample_rate)
        rev.enabled = self.reverb_mix > 0.005
        rev.room_size = max(0.1, min(0.95, float(self.reverb_room)))
        rev.damping = 0.45
        rev.width = max(0.2, min(1.0, float(self.stereo_width) + 0.35))
        rev.mix = max(0.0, min(0.65, float(self.reverb_mix)))
        return {
            "voices": {},
            "body_proc": GuitarBodyOrPickupProcessor(sample_rate),
            "body_proc_side": GuitarBodyOrPickupProcessor(sample_rate),
            "stereo_widener": GuitarStereoWidener(),
            "reverb": rev,
        }

    def render_slice(
        self,
        notes: List[Any],
        start_b: float,
        end_b: float,
        beats_per_sec: float,
        frames: int,
        sample_rate: int,
        bus_index: Optional[int] = None,
    ) -> np.ndarray:
        """Rend une tranche temporelle [start_b, end_b) depuis les notes MIDI du séquenceur."""
        with self._dsp_lock:
            return self._render_slice_internal(
                notes=notes,
                start_b=start_b,
                end_b=end_b,
                beats_per_sec=beats_per_sec,
                frames=frames,
                sample_rate=sample_rate,
                use_live_state=True,
            )

    def _render_slice_internal(
        self,
        notes: List[Any],
        start_b: float,
        end_b: float,
        beats_per_sec: float,
        frames: int,
        sample_rate: int,
        isolated_ctx: Optional[Dict[str, Any]] = None,
        use_live_state: bool = True,
    ) -> np.ndarray:
        if not self.enabled or frames <= 0:
            return np.zeros((max(0, frames), 2), dtype=np.float32)

        if not notes or beats_per_sec <= 0.0:
            return np.zeros((frames, 2), dtype=np.float32)

        if use_live_state or isolated_ctx is None:
            voices_dict = self._live_voices
            body_proc = self._body_proc
            body_proc_side = self._body_proc_side
            stereo_widener = self._stereo_widener
            reverb = self._reverb
            self._sync_reverb()
        else:
            voices_dict = isolated_ctx["voices"]
            body_proc = isolated_ctx["body_proc"]
            body_proc_side = isolated_ctx["body_proc_side"]
            stereo_widener = isolated_ctx["stereo_widener"]
            reverb = isolated_ctx["reverb"]

        slice_start_sec = start_b / beats_per_sec
        slice_end_sec = end_b / beats_per_sec
        tail_margin_sec = max(0.40, self.get_release_tail_sec() + 0.15)

        candidates = []
        for note in notes:
            n_start_b = float(getattr(note, "start_beat", 0.0))
            n_dur_b = max(0.02, float(getattr(note, "duration", 1.0)))
            n_start_sec = n_start_b / beats_per_sec
            n_off_sec = (n_start_b + n_dur_b) / beats_per_sec
            if (n_off_sec + tail_margin_sec) >= slice_start_sec and n_start_sec < slice_end_sec:
                candidates.append((note, n_start_b, n_dur_b, n_start_sec, n_off_sec))

        if not candidates:
            return np.zeros((frames, 2), dtype=np.float32)

        # Limiter la polyphonie maximale à 12 cordes simultanées (évite les surcharges)
        if len(candidates) > 12:
            candidates.sort(key=lambda item: item[3], reverse=True)
            candidates = candidates[:12]

        param_sig = self._voice_params_sig()
        slice_start_sample_abs = int(round(slice_start_sec * sample_rate))

        mono_mix = np.zeros(frames, dtype=np.float64)
        side_mix = np.zeros(frames, dtype=np.float64)
        active_keys = set()

        for note, n_start_b, n_dur_b, n_start_sec, n_off_sec in candidates:
            pitch = int(getattr(note, "pitch", 60))
            vel = int(getattr(note, "velocity", 100))
            note_start_sample_abs = int(round(n_start_sec * sample_rate))
            note_dur_samples = max(1, int(round((n_off_sec - n_start_sec) * sample_rate)))

            # Fenêtre d'intersection dans le buffer courant [0..frames)
            local_start = max(0, note_start_sample_abs - slice_start_sample_abs)
            if local_start >= frames:
                continue
            render_count = frames - local_start
            if render_count <= 0:
                continue

            # Position relative désirée depuis le début de la note
            desired_voice_sample = max(0, slice_start_sample_abs + local_start - note_start_sample_abs)

            live_id = getattr(note, "_live_id", None)
            if live_id is not None:
                v_key = ("live", live_id, sample_rate, param_sig)
            else:
                v_key = (pitch, round(n_start_b, 4), round(n_dur_b, 4), vel, sample_rate, param_sig)
            active_keys.add(v_key)

            voice = voices_dict.get(v_key)
            if voice is None or desired_voice_sample < voice.rendered_samples:
                voice = self._create_voice(pitch, vel, note_dur_samples, sample_rate)
                voices_dict[v_key] = voice
            elif live_id is not None:
                # Synchronise la frontière Note-Off lorsque la note live est relâchée
                voice.note_dur_samples = max(voice.rendered_samples, note_dur_samples)

            # Avance rapide si on démarre en milieu de note (seek)
            if desired_voice_sample > voice.rendered_samples:
                skip = desired_voice_sample - voice.rendered_samples
                voice.skip_samples(skip)

            voice_mono = voice.render_samples(render_count)

            # Légère spatialisation naturelle du manche selon la hauteur (grave léger gauche, aigu léger droite)
            # Limitée à ±0.16 pour préserver le centrage d'une note seule tout en aérant les accords
            pitch_norm = max(-1.0, min(1.0, (pitch - 58.0) / 18.0))
            pan = pitch_norm * max(0.0, min(1.0, self.stereo_width)) * 0.16
            g_l = math.sqrt(0.5 * (1.0 - pan))
            g_r = math.sqrt(0.5 * (1.0 + pan))

            mono_mix[local_start:local_start + render_count] += voice_mono
            side_mix[local_start:local_start + render_count] += voice_mono * ((g_l - g_r) * 0.5)

        # Nettoyage des voix terminées
        if len(voices_dict) > len(active_keys):
            for k in list(voices_dict.keys()):
                if k not in active_keys:
                    voices_dict.pop(k, None)

        # Traitement Caisse acoustique ou Micro électrique sur Mid et Side pour garder le même timbre
        body_m = body_proc.process_mono(
            mono_mix,
            mode=self.guitar_mode,
            body_resonance=self.body_resonance,
            pickup_position=self.pickup_position,
            sample_rate=sample_rate,
        )
        widened_l, widened_r = stereo_widener.process(body_m, self.stereo_width)

        if self.stereo_width > 0.001 and float(np.max(np.abs(side_mix))) > 1e-7:
            body_s = body_proc_side.process_mono(
                side_mix,
                mode=self.guitar_mode,
                body_resonance=self.body_resonance,
                pickup_position=self.pickup_position,
                sample_rate=sample_rate,
            )
            out_l = widened_l + body_s
            out_r = widened_r - body_s
        else:
            out_l = widened_l
            out_r = widened_r

        stereo = np.column_stack((out_l, out_r)).astype(np.float32)

        # Compensation douce si accord dense
        if len(candidates) > 1:
            headroom = 1.0 / math.sqrt(1.0 + (len(candidates) - 1) * 0.35)
            stereo *= headroom

        # Réverbération d'ambiance optionnelle (principalement pour acoustique / jazz)
        if reverb.enabled and float(np.max(np.abs(stereo))) > 1e-6:
            reverb.sample_rate = sample_rate
            stereo = reverb.process(stereo)

        out = stereo * float(self.master_volume)
        # Limiteur doux de protection contre la saturation numérique
        peak = float(np.max(np.abs(out))) if len(out) > 0 else 0.0
        if peak > 0.98:
            out = np.tanh(out * 0.92) * 1.04

        return out.astype(np.float32)

    def render_note(
        self,
        pitch: int,
        duration_sec: float = 0.6,
        sample_rate: Any = 44100,
        velocity: Any = 100,
        bus_index: Optional[int] = None,
    ) -> np.ndarray:
        """Rendu isolé immédiat d'une note (pour Piano Roll, Clavier virtuel ou audition dans la GUI)."""
        if isinstance(sample_rate, (int, float)) and isinstance(velocity, (int, float)):
            if velocity > 1000 and sample_rate <= 127:
                actual_sr = int(velocity)
                actual_vel = int(sample_rate * 127 if sample_rate <= 1.0 else sample_rate)
                sample_rate = actual_sr
                velocity = actual_vel
            elif velocity <= 1.0 and isinstance(velocity, float):
                velocity = int(velocity * 127)

        sample_rate = max(8000, int(sample_rate))
        velocity = max(1, min(127, int(velocity)))
        duration_sec = max(0.05, float(duration_sec))

        if not self.enabled:
            frames = max(64, int(duration_sec * sample_rate))
            return np.zeros((frames, 2), dtype=np.float32)

        with self._dsp_lock:
            tail_sec = self.get_release_tail_sec()
            total_sec = duration_sec + tail_sec
            frames = max(128, int(total_sec * sample_rate))

            bpm = 120.0
            bps = bpm / 60.0
            dummy_note = type(
                "DummyNote",
                (),
                {
                    "pitch": int(pitch),
                    "start_beat": 0.0,
                    "duration": max(0.05, duration_sec * bps),
                    "velocity": velocity,
                },
            )()
            iso_ctx = self.create_isolated_context(sample_rate)
            wave = self._render_slice_internal(
                notes=[dummy_note],
                start_b=0.0,
                end_b=total_sec * bps,
                beats_per_sec=bps,
                frames=frames,
                sample_rate=sample_rate,
                isolated_ctx=iso_ctx,
                use_live_state=False,
            )
            fade_n = min(len(wave), max(1, int(0.012 * sample_rate)))
            if fade_n > 1:
                wave[-fade_n:] *= np.linspace(1.0, 0.0, fade_n, dtype=np.float32)[:, None]
            return wave

    def get_state(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "preset_name": self.preset_name,
            "guitar_mode": self.guitar_mode,
            "string_tension": self.string_tension,
            "pluck_position": self.pluck_position,
            "brightness": self.brightness,
            "sustain_sec": self.sustain_sec,
            "release_sec": self.release_sec,
            "pick_attack": self.pick_attack,
            "palm_mute": self.palm_mute,
            "body_resonance": self.body_resonance,
            "pickup_position": self.pickup_position,
            "stereo_width": self.stereo_width,
            "reverb_mix": self.reverb_mix,
            "reverb_room": self.reverb_room,
            "dual_polarization": self.dual_polarization,
            "master_volume": self.master_volume,
        }

    def set_state(self, state: Dict[str, Any]) -> None:
        with self._dsp_lock:
            if "enabled" in state:
                self.enabled = bool(state["enabled"])
            self.preset_name = str(state.get("preset_name", self.preset_name))
            self.guitar_mode = str(state.get("guitar_mode", self.guitar_mode))
            self.string_tension = max(0.0, min(1.0, float(state.get("string_tension", self.string_tension))))
            self.pluck_position = max(0.04, min(0.50, float(state.get("pluck_position", self.pluck_position))))
            self.brightness = max(0.05, min(0.98, float(state.get("brightness", self.brightness))))
            self.sustain_sec = max(0.4, min(12.0, float(state.get("sustain_sec", self.sustain_sec))))
            self.release_sec = max(0.04, min(2.0, float(state.get("release_sec", self.release_sec))))
            self.pick_attack = max(0.0, min(1.0, float(state.get("pick_attack", self.pick_attack))))
            self.palm_mute = max(0.0, min(1.0, float(state.get("palm_mute", self.palm_mute))))
            self.body_resonance = max(0.0, min(1.5, float(state.get("body_resonance", self.body_resonance))))
            self.pickup_position = max(0.06, min(0.45, float(state.get("pickup_position", self.pickup_position))))
            self.stereo_width = max(0.0, min(1.0, float(state.get("stereo_width", self.stereo_width))))
            self.reverb_mix = max(0.0, min(0.65, float(state.get("reverb_mix", self.reverb_mix))))
            self.reverb_room = max(0.1, min(0.95, float(state.get("reverb_room", self.reverb_room))))
            self.dual_polarization = bool(state.get("dual_polarization", self.dual_polarization))
            self.master_volume = max(0.0, min(2.0, float(state.get("master_volume", self.master_volume))))
            self._sync_reverb()
            self._live_voices.clear()
            self._body_proc.reset()
            self._body_proc_side.reset()
            self._stereo_widener.reset()
        self.notify_modified()

    def create_editor(self, parent=None):
        from plugins.guitar.guitar_gui import NovaGuitarEditorWidget
        return NovaGuitarEditorWidget(self, parent)
