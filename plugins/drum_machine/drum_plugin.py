"""
plugins/drum_machine/drum_plugin.py - Moteur DSP et modèle pour l'Instrument Virtuel Nova Drums VSTi.

Caractéristiques :
- Échantillonnage polyphonique ultra-rapide en pleine précision FP32 (32-bit Float)
- Mapping General MIDI standard (Grosse caisse, Caisse claire, Toms, Charleston, Cymbales)
- Choke group pour le charleston (charleston fermé étouffe instantanément le charleston ouvert)
- Contrôles complets par pad : Volume, Panoramique, Pitch / Accordage (±12 demi-tons), Decay, Send Reverb
- Réverbération stéréo de studio intégrée (Freeverb) avec Room Size, Damping, Width, Mix
- Presets sonores d'usine (Studio Acoustic, Punchy Rock, Trap / Modern, Big Hall, Tight & Dry)
- Rendu temps réel pour Piano Roll et export audio sans perte
"""
import os
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
import numpy as np
import soundfile as sf

from plugins.base import BasePlugin
from plugins.registry import register_plugin
from plugins.drum_machine.drum_reverb import DrumReverb


class DrumPad:
    """Représente un pad de batterie individuel avec ses réglages acoustiques et son échantillon"""
    def __init__(
        self,
        pad_id: str,
        name: str,
        midi_pitches: List[int],
        sample_filename: str,
        volume: float = 1.0,
        pan: float = 0.0,
        tune: float = 0.0,
        decay: float = 1.0,
        reverb_send: float = 0.25,
        choke_group: int = 0
    ):
        self.pad_id = pad_id
        self.name = name
        self.midi_pitches = list(midi_pitches)
        self.sample_filename = sample_filename
        self.volume = float(volume)          # 0.0 à 2.0
        self.pan = float(pan)                # -1.0 (G) à +1.0 (D)
        self.tune = float(tune)              # -12.0 à +12.0 demi-tons
        self.decay = float(decay)            # 0.1 à 2.0 (multiplicateur de durée)
        self.reverb_send = float(reverb_send)# 0.0 à 1.0
        self.choke_group = int(choke_group)  # 1 pour Hi-Hat ouvert/fermé
        self.muted = False
        self.soloed = False

        # Données audio chargées en mémoire vive
        self.sample_data: Optional[np.ndarray] = None  # (samples, 2) en float32
        self.sample_rate: int = 44100

        # Données audio recalculées si tune != 0
        self._cached_tuned_data: Optional[np.ndarray] = None
        self._cached_tune_val: float = 0.0

    def load_sample(self, base_dirs: List[Path]) -> bool:
        """Cherche et charge le fichier audio WAV dans les dossiers spécifiés"""
        for b_dir in base_dirs:
            p = b_dir / self.sample_filename
            if p.exists():
                try:
                    data, sr = sf.read(str(p), dtype="float32")
                    if data.ndim == 1:
                        data = np.column_stack((data, data))
                    elif data.shape[1] > 2:
                        data = data[:, :2]
                    self.sample_data = data
                    self.sample_rate = sr
                    self._cached_tuned_data = None
                    return True
                except Exception as e:
                    print(f"[NovaDrums] Erreur lecture sample {p}: {e}")
        return False

    def get_audio_data(self) -> np.ndarray:
        """Retourne les données audio en tenant compte du paramètre tune (hauteur de note)"""
        if self.sample_data is None:
            # Silence par défaut
            return np.zeros((100, 2), dtype=np.float32)

        if abs(self.tune) < 0.05:
            return self.sample_data

        if self._cached_tuned_data is not None and abs(self._cached_tune_val - self.tune) < 0.01:
            return self._cached_tuned_data

        # Rééchantillonnage pour modifier la hauteur sans altérer le moteur
        # pitch_factor = 2^(tune / 12)
        pitch_factor = 2.0 ** (self.tune / 12.0)
        orig_len = len(self.sample_data)
        new_len = max(10, int(orig_len / pitch_factor))
        
        orig_indices = np.linspace(0, orig_len - 1, new_len)
        tuned = np.zeros((new_len, 2), dtype=np.float32)
        tuned[:, 0] = np.interp(orig_indices, np.arange(orig_len), self.sample_data[:, 0])
        tuned[:, 1] = np.interp(orig_indices, np.arange(orig_len), self.sample_data[:, 1])

        self._cached_tuned_data = tuned
        self._cached_tune_val = self.tune
        return tuned

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pad_id": self.pad_id,
            "name": self.name,
            "volume": self.volume,
            "pan": self.pan,
            "tune": self.tune,
            "decay": self.decay,
            "reverb_send": self.reverb_send,
            "muted": self.muted,
            "soloed": self.soloed,
        }

    def from_dict(self, d: Dict[str, Any]):
        self.volume = float(d.get("volume", self.volume))
        self.pan = float(d.get("pan", self.pan))
        self.tune = float(d.get("tune", self.tune))
        self.decay = float(d.get("decay", self.decay))
        self.reverb_send = float(d.get("reverb_send", self.reverb_send))
        self.muted = bool(d.get("muted", self.muted))
        self.soloed = bool(d.get("soloed", self.soloed))
        self._cached_tuned_data = None


@register_plugin(
    plugin_type_id="novadaw.drum_machine",
    name="Nova Drums VSTi",
    category="instrument",
    icon="🥁",
    description="Instrument virtuel de batterie échantillonné en FP32 avec réverbération stéréo de studio.",
    is_default=True
)
class DrumMachinePlugin(BasePlugin):
    """
    Plugin VSTi de batterie complet pour NovaDAW.
    Prend en charge la synthèse d'échantillons en FP32, les déclenchements MIDI polyphoniques,
    le choke group (charley) et la réverbération spatiale.
    """
    def __init__(self, instance_id: Optional[str] = None):
        super().__init__(
            plugin_type_id="novadaw.drum_machine",
            name="Nova Drums VSTi",
            category="instrument",
            icon="🥁",
            instance_id=instance_id
        )
        self.description = "Instrument virtuel de batterie échantillonné en FP32 avec réverbération."
        self.is_instrument = True
        self.master_volume = 1.0
        self.preset_name = "Studio Acoustic"

        # Module de réverbération stéréo
        self.reverb = DrumReverb(
            room_size=0.45,
            damping=0.50,
            width=1.0,
            wet_mix=0.25,
            dry_mix=1.0
        )

        # Définition des 9 pads du kit standard GM
        self.pads: List[DrumPad] = [
            DrumPad("kick", "Kick", [35, 36], "kick.wav", volume=1.0, pan=0.0, tune=0.0, decay=1.0, reverb_send=0.08, choke_group=0),
            DrumPad("snare", "Snare", [38, 40], "snare.wav", volume=0.95, pan=0.0, tune=0.0, decay=1.0, reverb_send=0.35, choke_group=0),
            DrumPad("hihat_c", "Closed HH", [42, 44], "hihat_closed.wav", volume=0.85, pan=-0.25, tune=0.0, decay=1.0, reverb_send=0.15, choke_group=1),
            DrumPad("hihat_o", "Open HH", [46], "hihat_open.wav", volume=0.85, pan=-0.30, tune=0.0, decay=1.0, reverb_send=0.25, choke_group=1),
            DrumPad("tom_l", "Low Tom", [41, 43, 45], "tom_low.wav", volume=0.90, pan=-0.40, tune=0.0, decay=1.0, reverb_send=0.28, choke_group=0),
            DrumPad("tom_m", "Mid Tom", [47, 48], "tom_mid.wav", volume=0.90, pan=0.20, tune=0.0, decay=1.0, reverb_send=0.28, choke_group=0),
            DrumPad("tom_h", "High Tom", [50], "tom_high.wav", volume=0.90, pan=0.45, tune=0.0, decay=1.0, reverb_send=0.28, choke_group=0),
            DrumPad("crash", "Crash", [49, 57], "crash.wav", volume=0.80, pan=-0.35, tune=0.0, decay=1.0, reverb_send=0.40, choke_group=0),
            DrumPad("ride", "Ride", [51, 59], "ride.wav", volume=0.80, pan=0.35, tune=0.0, decay=1.0, reverb_send=0.30, choke_group=0),
        ]

        # Voix actives en cours de lecture pour le rendu continu
        self._active_voices: List[Dict[str, Any]] = []

        # Chargement des samples audio
        self.reload_samples()

    def get_sample_dirs(self) -> List[Path]:
        """Retourne les chemins candidats où chercher les fichiers audio WAV"""
        here = Path(__file__).resolve().parent
        root = here.parent.parent
        return [
            here / "samples" / "default_kit",
            root / "assets" / "drum_kits" / "default_kit",
            root / "scratch",
        ]

    def reload_samples(self):
        """Recharge tous les samples audio des pads"""
        dirs = self.get_sample_dirs()
        for pad in self.pads:
            loaded = pad.load_sample(dirs)
            if not loaded:
                # Créer un son de remplacement synthétique propre en attendant
                self._generate_fallback_sample(pad)

    def _generate_fallback_sample(self, pad: DrumPad):
        """Génère un sample synthétique doux si le WAV n'a pas encore été généré"""
        sr = 44100
        if "kick" in pad.pad_id:
            t = np.linspace(0, 0.4, int(sr * 0.4), endpoint=False)
            freq = 140.0 * np.exp(-t * 24.0) + 45.0
            phase = 2.0 * np.pi * np.cumsum(freq) / sr
            sig = np.sin(phase) * np.exp(-t * 8.0)
        elif "snare" in pad.pad_id:
            t = np.linspace(0, 0.35, int(sr * 0.35), endpoint=False)
            tone = np.sin(2.0 * np.pi * 180.0 * t) * np.exp(-t * 18.0)
            noise = np.random.uniform(-1.0, 1.0, len(t)) * np.exp(-t * 12.0)
            sig = (tone * 0.4 + noise * 0.6)
        elif "hihat" in pad.pad_id:
            dur = 0.4 if "o" in pad.pad_id else 0.08
            t = np.linspace(0, dur, int(sr * dur), endpoint=False)
            sig = np.random.uniform(-1.0, 1.0, len(t)) * np.exp(-t * (12.0 if "o" in pad.pad_id else 45.0))
        elif "tom" in pad.pad_id:
            f0 = 80.0 if "l" in pad.pad_id else (115.0 if "m" in pad.pad_id else 150.0)
            t = np.linspace(0, 0.5, int(sr * 0.5), endpoint=False)
            freq = f0 * 1.5 * np.exp(-t * 18.0) + f0
            sig = np.sin(2.0 * np.pi * np.cumsum(freq) / sr) * np.exp(-t * 7.0)
        else:
            dur = 1.2
            t = np.linspace(0, dur, int(sr * dur), endpoint=False)
            sig = np.random.uniform(-1.0, 1.0, len(t)) * np.exp(-t * 3.5)

        sig = sig.astype(np.float32)
        pad.sample_data = np.column_stack((sig, sig))
        pad.sample_rate = sr

    def find_pad_by_pitch(self, pitch: int) -> Optional[DrumPad]:
        """Trouve le pad correspondant au numéro de note MIDI"""
        for pad in self.pads:
            if pitch in pad.midi_pitches:
                return pad
        # Fallback harmonique par modulo si pitch hors GM
        fallback_idx = pitch % len(self.pads)
        return self.pads[fallback_idx]

    def render_note(self, pitch: int, duration_sec: float = 0.5, sample_rate: int = 44100, velocity: int = 100) -> np.ndarray:
        """
        Rend immédiatement le son d'un pad (utilisé pour les aperçus Piano Roll ou les clics de pad).
        Retourne un buffer numpy float32 (samples, 2).
        """
        pad = self.find_pad_by_pitch(pitch)
        if not pad:
            return np.zeros((max(1, int(duration_sec * sample_rate)), 2), dtype=np.float32)

        data = pad.get_audio_data()
        vel_gain = (max(1, min(127, velocity)) / 127.0) * pad.volume * self.master_volume

        # Panoramique
        pan = max(-1.0, min(1.0, pad.pan))
        gain_l = vel_gain * (1.0 - max(0.0, pan))
        gain_r = vel_gain * (1.0 + min(0.0, pan))

        # Enveloppe de decay
        decay_factor = max(0.1, min(2.0, pad.decay))
        n_samples = int(len(data) * decay_factor)
        
        if abs(decay_factor - 1.0) > 0.05:
            # Interpolation de durée
            idx_orig = np.linspace(0, len(data) - 1, n_samples)
            dry_sound = np.zeros((n_samples, 2), dtype=np.float32)
            dry_sound[:, 0] = np.interp(idx_orig, np.arange(len(data)), data[:, 0]) * gain_l
            dry_sound[:, 1] = np.interp(idx_orig, np.arange(len(data)), data[:, 1]) * gain_r
        else:
            dry_sound = np.zeros_like(data)
            dry_sound[:, 0] = data[:, 0] * gain_l
            dry_sound[:, 1] = data[:, 1] * gain_r

        # Application réverbération selon le send du pad
        if self.reverb.enabled and pad.reverb_send > 0.01:
            wet_send = dry_sound * pad.reverb_send
            rev_out = self.reverb.process(wet_send)
            out = dry_sound + rev_out
        else:
            out = dry_sound

        return out.astype(np.float32)

    def render_slice(
        self,
        notes: List[Any],
        start_b: float,
        end_b: float,
        beats_per_sec: float,
        frames: int,
        sample_rate: int
    ) -> np.ndarray:
        """
        Rend de manière ultra-rapide et sans coupure les notes de batterie situées dans l'intervalle [start_b, end_b).
        Gère le choke group (charleston fermé coupe charleston ouvert) et la réverbération.
        """
        output = np.zeros((frames, 2), dtype=np.float32)
        reverb_in = np.zeros((frames, 2), dtype=np.float32)

        has_any_solo = any(p.soloed for p in self.pads)

        # 1. Vérifier chaque note du clip
        for note in notes:
            abs_start = getattr(note, "start_beat", 0.0)
            if not (start_b <= abs_start < end_b):
                continue

            pitch = getattr(note, "pitch", 36)
            vel = getattr(note, "velocity", 100)
            pad = self.find_pad_by_pitch(pitch)
            if not pad:
                continue

            if pad.muted:
                continue
            if has_any_solo and not pad.soloed:
                continue

            # Gestion Choke Group : Si charleston fermé (choke_group=1), étouffer les voix actives du choke_group 1
            if pad.choke_group > 0:
                for voice in self._active_voices:
                    if voice.get("choke_group") == pad.choke_group:
                        voice["choked"] = True

            # Calcul du décalage en samples dans le buffer actuel
            offset_sec = max(0.0, (abs_start - start_b) / beats_per_sec)
            start_frame = int(offset_sec * sample_rate)

            # Préparation des données audio du pad
            data = pad.get_audio_data()
            vel_gain = (max(1, min(127, vel)) / 127.0) * pad.volume * self.master_volume
            pan = max(-1.0, min(1.0, pad.pan))
            g_l = vel_gain * (1.0 - max(0.0, pan))
            g_r = vel_gain * (1.0 + min(0.0, pan))

            # Enregistrement de la voix active
            self._active_voices.append({
                "data": data,
                "cursor": 0,
                "start_offset": start_frame,
                "gain_l": g_l,
                "gain_r": g_r,
                "reverb_send": pad.reverb_send,
                "choke_group": pad.choke_group,
                "choked": False,
                "choke_fade": 1.0,
            })

        # 2. Rendu de toutes les voix actives dans le bloc audio
        surviving_voices = []
        for voice in self._active_voices:
            data = voice["data"]
            cur = voice["cursor"]
            st_off = voice["start_offset"]
            g_l = voice["gain_l"]
            g_r = voice["gain_r"]
            rev_send = voice["reverb_send"]
            is_choked = voice["choked"]

            # Déterminer la fenêtre d'écriture
            buf_start = max(0, st_off)
            if buf_start >= frames:
                # La voix commencera au prochain bloc
                voice["start_offset"] -= frames
                surviving_voices.append(voice)
                continue

            sample_start = cur
            avail_in_data = len(data) - sample_start
            to_render = min(frames - buf_start, avail_in_data)

            if to_render > 0:
                chunk = data[sample_start:sample_start + to_render]
                l_sig = chunk[:, 0] * g_l
                r_sig = chunk[:, 1] * g_r

                if is_choked:
                    # Fondu d'étouffement rapide (choke fade out)
                    fade_len = min(to_render, int(0.008 * sample_rate))
                    fade = np.linspace(voice["choke_fade"], 0.0, fade_len)
                    l_sig[:fade_len] *= fade
                    r_sig[:fade_len] *= fade
                    l_sig[fade_len:] = 0.0
                    r_sig[fade_len:] = 0.0
                    voice["choke_fade"] = 0.0

                output[buf_start:buf_start + to_render, 0] += l_sig
                output[buf_start:buf_start + to_render, 1] += r_sig

                if rev_send > 0.01:
                    reverb_in[buf_start:buf_start + to_render, 0] += l_sig * rev_send
                    reverb_in[buf_start:buf_start + to_render, 1] += r_sig * rev_send

                voice["cursor"] += to_render
                voice["start_offset"] = 0

            # Garder la voix si non terminée et non étouffée à zéro
            if voice["cursor"] < len(data) and (not is_choked or voice["choke_fade"] > 0.01):
                surviving_voices.append(voice)

        self._active_voices = surviving_voices

        # 3. Traitement de la réverbération stéréo
        if self.reverb.enabled and np.any(np.abs(reverb_in) > 1e-6):
            rev_out = self.reverb.process(reverb_in)
            output += rev_out

        return output

    def process(self, audio: np.ndarray, sample_rate: int) -> np.ndarray:
        """Méthode requise par BasePlugin si inséré sur une piste audio"""
        if not self.enabled:
            return audio
        # Si audio entrant, on lui applique la réverbération générale de la drum machine
        if self.reverb.enabled and audio is not None:
            return self.reverb.process(audio)
        return audio

    def reset(self) -> None:
        """Réinitialise les voix et les mémoires de réverbération lors d'un arrêt de lecture"""
        self._active_voices.clear()
        self.reverb.reset()

    def apply_preset(self, preset_name: str):
        """Applique un preset de configuration acoustique prédéfini"""
        self.preset_name = preset_name
        name = preset_name.lower()

        if "acoustic" in name or "studio" in name:
            self.master_volume = 1.0
            self.reverb.room_size = 0.45
            self.reverb.damping = 0.50
            self.reverb.wet_mix = 0.25
            for p in self.pads:
                p.tune = 0.0
                p.decay = 1.0
        elif "rock" in name or "punch" in name:
            self.master_volume = 1.15
            self.reverb.room_size = 0.55
            self.reverb.damping = 0.35
            self.reverb.wet_mix = 0.30
            for p in self.pads:
                if "kick" in p.pad_id:
                    p.tune = -1.5
                    p.volume = 1.2
                elif "snare" in p.pad_id:
                    p.tune = 0.5
                    p.volume = 1.15
                    p.reverb_send = 0.45
        elif "trap" in name or "modern" in name:
            self.master_volume = 1.10
            self.reverb.room_size = 0.30
            self.reverb.damping = 0.20
            self.reverb.wet_mix = 0.15
            for p in self.pads:
                if "hihat" in p.pad_id:
                    p.tune = 2.0
                elif "kick" in p.pad_id:
                    p.tune = -2.0
                    p.decay = 1.3
        elif "hall" in name or "ambient" in name:
            self.master_volume = 0.95
            self.reverb.room_size = 0.85
            self.reverb.damping = 0.30
            self.reverb.wet_mix = 0.55
            for p in self.pads:
                p.reverb_send = 0.50
        elif "dry" in name or "tight" in name:
            self.master_volume = 1.05
            self.reverb.room_size = 0.10
            self.reverb.wet_mix = 0.02
            for p in self.pads:
                p.decay = 0.80
                p.reverb_send = 0.02

    def get_state(self) -> Dict[str, Any]:
        return {
            "preset_name": self.preset_name,
            "master_volume": self.master_volume,
            "reverb": {
                "enabled": self.reverb.enabled,
                "room_size": self.reverb.room_size,
                "damping": self.reverb.damping,
                "width": self.reverb.width,
                "wet_mix": self.reverb.wet_mix,
                "dry_mix": self.reverb.dry_mix,
            },
            "pads": [p.to_dict() for p in self.pads],
        }

    def set_state(self, state: Dict[str, Any]) -> None:
        self.preset_name = state.get("preset_name", self.preset_name)
        self.master_volume = float(state.get("master_volume", 1.0))
        
        rev_data = state.get("reverb", {})
        if rev_data:
            self.reverb.enabled = bool(rev_data.get("enabled", True))
            self.reverb.room_size = float(rev_data.get("room_size", 0.45))
            self.reverb.damping = float(rev_data.get("damping", 0.50))
            self.reverb.width = float(rev_data.get("width", 1.0))
            self.reverb.wet_mix = float(rev_data.get("wet_mix", 0.25))
            self.reverb.dry_mix = float(rev_data.get("dry_mix", 1.0))

        pads_data = state.get("pads", [])
        pads_map = {d.get("pad_id"): d for d in pads_data if "pad_id" in d}
        for pad in self.pads:
            if pad.pad_id in pads_map:
                pad.from_dict(pads_map[pad.pad_id])

    def create_editor(self, parent=None):
        from plugins.drum_machine.drum_gui import DrumMachineWidget
        return DrumMachineWidget(self, parent)
