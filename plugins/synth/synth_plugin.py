"""
plugins/synth/synth_plugin.py - Modèle et architecture du plugin d'instrument virtuel NovaSynth.

Caractéristiques :
- Synthétiseur 100% électronique polyphonique pur (synthèse DSP sans samples obligatoires).
- Empilement illimité de couches sonores (Sound Layers) : jusqu'à N couches simultanées par note.
- Matrice de routage multi-sorties : 10 sorties stéréo indépendantes (Bus 0 à Bus 9 / Out 1 à Out 10).
- Multi-modulations : Oscillateurs PolyBLEP, FM 2-op, SuperSaw JP-8000, SVF analogique, Double ADSR, LFO flexible.
- Effets d'espace : Ping-Pong Delay, Reverb Studio, Saturation analogique.
- Contrôlable à 100% par le protocole MCP et entièrement sérialisable dans les sessions .ndaw.
"""
import uuid
import copy
from typing import Dict, Any, Optional, List, Tuple
import numpy as np

from plugins.base import BasePlugin
from plugins.registry import register_plugin
from plugins.synth.synth_dsp import (
    pitch_to_freq,
    generate_oscillator,
    compute_adsr_envelope,
    ChamberlinSVF,
    StereoPingPongDelay,
    StereoStudioReverb
)


class SynthLayer:
    """Représente une couche sonore individuelle (Oscillateur + Filtre + ADSR + LFO + Bus)"""
    def __init__(
        self,
        layer_id: Optional[str] = None,
        name: str = "Layer 1",
        waveform: str = "saw",
        octave: int = 0,
        semitone: int = 0,
        fine_tune: float = 0.0,
        volume: float = 0.8,
        pan: float = 0.0,
        output_bus: int = 0,
        color: str = "#00f0ff"
    ):
        self.layer_id = layer_id or str(uuid.uuid4())[:8]
        self.name = name
        self.enabled = True
        self.muted = False
        self.soloed = False
        self.color = color

        # Oscillateur
        self.waveform = waveform  # "sine", "saw", "square", "triangle", "noise", "fm", "supersaw"
        self.octave = int(octave)           # -3 à +3
        self.semitone = int(semitone)       # -12 à +12
        self.fine_tune = float(fine_tune)   # -100 à +100 cents
        self.volume = float(volume)         # 0.0 à 1.5
        self.pan = float(pan)               # -1.0 (G) à +1.0 (D)
        self.output_bus = max(0, min(9, int(output_bus)))  # 0 à 9 (10 sorties stéréo)

        # Paramètres étendus d'oscillateur
        self.unison_voices = 7
        self.unison_detune = 0.25
        self.unison_spread = 0.50
        self.pulse_width = 0.50
        self.fm_ratio = 2.0
        self.fm_depth = 1.0

        # Enveloppe d'amplitude (Amp ADSR)
        self.attack = 0.010    # sec
        self.decay = 0.250     # sec
        self.sustain = 0.70    # 0.0 à 1.0
        self.release = 0.350   # sec

        # Filtre multimode
        self.filter_type = "lowpass"  # "lowpass", "highpass", "bandpass", "notch"
        self.cutoff = 3500.0          # Hz (20 à 20000)
        self.resonance = 1.2          # 0.2 à 10.0
        self.drive = 0.0              # 0.0 à 3.0
        self.key_tracking = 0.0       # 0.0 à 1.0

        # Enveloppe de filtre (Filter ADSR)
        self.filter_env_amount = 0.0  # -1.0 à 1.0
        self.filter_attack = 0.015
        self.filter_decay = 0.300
        self.filter_sustain = 0.20
        self.filter_release = 0.400

        # LFO
        self.lfo_enabled = False
        self.lfo_waveform = "sine"
        self.lfo_rate = 2.0           # Hz
        self.lfo_depth = 0.0          # 0.0 à 1.0
        self.lfo_target = "cutoff"    # "pitch", "cutoff", "pan", "amp", "pwm"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "layer_id": self.layer_id,
            "name": self.name,
            "enabled": self.enabled,
            "muted": self.muted,
            "soloed": self.soloed,
            "color": self.color,
            "waveform": self.waveform,
            "octave": self.octave,
            "semitone": self.semitone,
            "fine_tune": self.fine_tune,
            "volume": self.volume,
            "pan": self.pan,
            "output_bus": self.output_bus,
            "unison_voices": self.unison_voices,
            "unison_detune": self.unison_detune,
            "unison_spread": self.unison_spread,
            "pulse_width": self.pulse_width,
            "fm_ratio": self.fm_ratio,
            "fm_depth": self.fm_depth,
            "attack": self.attack,
            "decay": self.decay,
            "sustain": self.sustain,
            "release": self.release,
            "filter_type": self.filter_type,
            "cutoff": self.cutoff,
            "resonance": self.resonance,
            "drive": self.drive,
            "key_tracking": self.key_tracking,
            "filter_env_amount": self.filter_env_amount,
            "filter_attack": self.filter_attack,
            "filter_decay": self.filter_decay,
            "filter_sustain": self.filter_sustain,
            "filter_release": self.filter_release,
            "lfo_enabled": self.lfo_enabled,
            "lfo_waveform": self.lfo_waveform,
            "lfo_rate": self.lfo_rate,
            "lfo_depth": self.lfo_depth,
            "lfo_target": self.lfo_target,
        }

    def from_dict(self, d: Dict[str, Any]):
        self.layer_id = d.get("layer_id", self.layer_id)
        self.name = d.get("name", self.name)
        self.enabled = bool(d.get("enabled", self.enabled))
        self.muted = bool(d.get("muted", self.muted))
        self.soloed = bool(d.get("soloed", self.soloed))
        self.color = d.get("color", self.color)
        self.waveform = d.get("waveform", self.waveform)
        self.octave = int(d.get("octave", self.octave))
        self.semitone = int(d.get("semitone", self.semitone))
        self.fine_tune = float(d.get("fine_tune", self.fine_tune))
        self.volume = float(d.get("volume", self.volume))
        self.pan = float(d.get("pan", self.pan))
        self.output_bus = max(0, min(9, int(d.get("output_bus", self.output_bus))))
        self.unison_voices = int(d.get("unison_voices", self.unison_voices))
        self.unison_detune = float(d.get("unison_detune", self.unison_detune))
        self.unison_spread = float(d.get("unison_spread", self.unison_spread))
        self.pulse_width = float(d.get("pulse_width", self.pulse_width))
        self.fm_ratio = float(d.get("fm_ratio", self.fm_ratio))
        self.fm_depth = float(d.get("fm_depth", self.fm_depth))
        self.attack = float(d.get("attack", self.attack))
        self.decay = float(d.get("decay", self.decay))
        self.sustain = float(d.get("sustain", self.sustain))
        self.release = float(d.get("release", self.release))
        self.filter_type = d.get("filter_type", self.filter_type)
        self.cutoff = float(d.get("cutoff", self.cutoff))
        self.resonance = float(d.get("resonance", self.resonance))
        self.drive = float(d.get("drive", self.drive))
        self.key_tracking = float(d.get("key_tracking", self.key_tracking))
        self.filter_env_amount = float(d.get("filter_env_amount", self.filter_env_amount))
        self.filter_attack = float(d.get("filter_attack", self.filter_attack))
        self.filter_decay = float(d.get("filter_decay", self.filter_decay))
        self.filter_sustain = float(d.get("filter_sustain", self.filter_sustain))
        self.filter_release = float(d.get("filter_release", self.filter_release))
        self.lfo_enabled = bool(d.get("lfo_enabled", self.lfo_enabled))
        self.lfo_waveform = d.get("lfo_waveform", self.lfo_waveform)
        self.lfo_rate = float(d.get("lfo_rate", self.lfo_rate))
        self.lfo_depth = float(d.get("lfo_depth", self.lfo_depth))
        self.lfo_target = d.get("lfo_target", self.lfo_target)


@register_plugin(
    plugin_type_id="novadaw.synth",
    name="NovaSynth",
    category="instrument",
    icon="⚡",
    description="Synthétiseur polyphonique modulaire électronique multi-couches avec 10 sorties stéréo assignables.",
    is_default=True
)
class NovaSynthPlugin(BasePlugin):
    """
    Instrument Virtuel de Synthèse Électronique Avancée pour NovaDAW.
    Prend en charge le stacking de couches sonores, 10 sorties stéréo indépendantes,
    synthèse FM / SuperSaw / PolyBLEP, et contrôle MCP temps réel.
    """
    NUM_OUTPUT_BUSES = 10

    def __init__(self, instance_id: Optional[str] = None):
        super().__init__(
            plugin_type_id="novadaw.synth",
            name="NovaSynth",
            category="instrument",
            icon="⚡",
            instance_id=instance_id
        )
        self.is_instrument = True
        self.preset_name = "Cyberpunk Acid Lead"
        self.master_volume = 1.0
        self.glide_time = 0.0

        # Effets d'espace intégrés
        self.delay = StereoPingPongDelay()
        self.reverb = StereoStudioReverb()
        self.saturation_drive = 0.0

        # Filtre interne de secours pour traitement audio entrant
        self._input_svf = ChamberlinSVF()

        # Liste des couches sonores empilées (Sound Layers)
        self.layers: List[SynthLayer] = []
        self._init_default_layers()

    def _init_default_layers(self):
        """Initialise la configuration de base avec deux couches empilées complémentaires"""
        self.layers = [
            SynthLayer(
                layer_id="layer_lead",
                name="Main Lead (Saw)",
                waveform="saw",
                octave=0,
                semitone=0,
                fine_tune=0.0,
                volume=0.85,
                pan=-0.15,
                output_bus=0,
                color="#00f0ff"
            ),
            SynthLayer(
                layer_id="layer_sub",
                name="Sub Body (Square)",
                waveform="square",
                octave=-1,
                semitone=0,
                fine_tune=0.0,
                volume=0.65,
                pan=0.15,
                output_bus=0,
                color="#ff007f"
            )
        ]
        # Réglages Acid Lead par défaut
        self.layers[0].cutoff = 2800.0
        self.layers[0].resonance = 2.5
        self.layers[0].drive = 0.4
        self.layers[0].filter_env_amount = 0.60
        self.layers[0].filter_decay = 0.25

        self.layers[1].cutoff = 1200.0
        self.layers[1].resonance = 1.2
        self.layers[1].pulse_width = 0.50

        self.delay.enabled = True
        self.delay.time_sec = 0.25
        self.delay.feedback = 0.35
        self.delay.mix = 0.20

        self.reverb.enabled = True
        self.reverb.room_size = 0.40
        self.reverb.mix = 0.15

    # -------------------------------------------------------------------------
    # Gestion des Couches Sonores (Sound Stacking)
    # -------------------------------------------------------------------------
    def add_layer(
        self,
        name: str = "New Layer",
        waveform: str = "saw",
        octave: int = 0,
        volume: float = 0.8,
        output_bus: int = 0
    ) -> SynthLayer:
        """Ajoute une nouvelle couche sonore à empiler sur le synthétiseur"""
        colors = ["#00f0ff", "#ff007f", "#ffaa00", "#00ff88", "#a855f7", "#38bdf8", "#ec4899", "#f97316"]
        c_idx = len(self.layers) % len(colors)
        layer = SynthLayer(
            name=name,
            waveform=waveform,
            octave=octave,
            volume=volume,
            output_bus=output_bus,
            color=colors[c_idx]
        )
        self.layers.append(layer)
        return layer

    def remove_layer(self, index_or_id: Any) -> bool:
        """Supprime une couche sonore par index ou par ID (au moins une couche doit subsister)"""
        if len(self.layers) <= 1:
            return False

        if isinstance(index_or_id, int):
            if 0 <= index_or_id < len(self.layers):
                self.layers.pop(index_or_id)
                return True
        else:
            for i, l in enumerate(self.layers):
                if l.layer_id == str(index_or_id) or l.name.lower() == str(index_or_id).lower():
                    self.layers.pop(i)
                    return True
        return False

    def duplicate_layer(self, index_or_id: Any) -> Optional[SynthLayer]:
        """Duplique une couche sonore pour enrichir instantanément le son empilé"""
        target = self.get_layer(index_or_id)
        if not target:
            return None
        new_l = copy.deepcopy(target)
        new_l.layer_id = str(uuid.uuid4())[:8]
        new_l.name = f"{target.name} (Copy)"
        # Léger detune par défaut pour grossir le son
        new_l.fine_tune += 7.0
        self.layers.append(new_l)
        return new_l

    def get_layer(self, index_or_id: Any) -> Optional[SynthLayer]:
        """Recherche une couche sonore par index ou identifiant"""
        if isinstance(index_or_id, int):
            if 0 <= index_or_id < len(self.layers):
                return self.layers[index_or_id]
            return None
        s = str(index_or_id).lower().strip()
        for l in self.layers:
            if l.layer_id.lower() == s or l.name.lower() == s:
                return l
        return None

    # -------------------------------------------------------------------------
    # Presets Sonores d'Usine
    # -------------------------------------------------------------------------
    def get_factory_presets(self) -> Dict[str, Dict[str, Any]]:
        """Banque de presets de synthèse riche et variée"""
        return {
            "Cyberpunk Acid Lead": {
                "master_volume": 1.0,
                "glide_time": 0.04,
                "delay": {"enabled": True, "time_sec": 0.25, "feedback": 0.35, "mix": 0.22},
                "reverb": {"enabled": True, "room_size": 0.45, "mix": 0.18},
                "layers": [
                    {
                        "name": "Acid Saw", "waveform": "saw", "octave": 0, "volume": 0.85, "pan": -0.1, "output_bus": 0,
                        "cutoff": 2600.0, "resonance": 3.2, "drive": 0.8, "filter_env_amount": 0.65, "filter_decay": 0.22,
                        "attack": 0.005, "decay": 0.3, "sustain": 0.6, "release": 0.25
                    },
                    {
                        "name": "Sub Punch", "waveform": "square", "octave": -1, "volume": 0.70, "pan": 0.1, "output_bus": 0,
                        "cutoff": 1200.0, "resonance": 1.0, "pulse_width": 0.45,
                        "attack": 0.005, "decay": 0.2, "sustain": 0.7, "release": 0.25
                    }
                ]
            },
            "Neon Horizon SuperSaw": {
                "master_volume": 0.95,
                "glide_time": 0.0,
                "delay": {"enabled": True, "time_sec": 0.375, "feedback": 0.40, "mix": 0.25},
                "reverb": {"enabled": True, "room_size": 0.70, "mix": 0.30},
                "layers": [
                    {
                        "name": "SuperSaw 7-Voices", "waveform": "supersaw", "octave": 0, "volume": 0.85, "pan": 0.0, "output_bus": 0,
                        "unison_detune": 0.35, "unison_spread": 0.8, "cutoff": 12000.0, "resonance": 0.8,
                        "attack": 0.040, "decay": 0.5, "sustain": 0.8, "release": 0.6
                    },
                    {
                        "name": "Warm Octave Down", "waveform": "saw", "octave": -1, "volume": 0.60, "pan": 0.0, "output_bus": 0,
                        "cutoff": 3500.0, "resonance": 1.0,
                        "attack": 0.020, "decay": 0.4, "sustain": 0.75, "release": 0.5
                    },
                    {
                        "name": "Air Noise Shimmer", "waveform": "noise", "octave": 1, "volume": 0.25, "pan": 0.0, "output_bus": 1,
                        "cutoff": 6000.0, "resonance": 1.5, "filter_type": "highpass",
                        "attack": 0.100, "decay": 0.4, "sustain": 0.2, "release": 0.8
                    }
                ]
            },
            "Deep Sub & Punch Bass": {
                "master_volume": 1.05,
                "glide_time": 0.02,
                "delay": {"enabled": False, "mix": 0.0},
                "reverb": {"enabled": False, "mix": 0.0},
                "layers": [
                    {
                        "name": "Pure Sine Sub", "waveform": "sine", "octave": -2, "volume": 1.0, "pan": 0.0, "output_bus": 1,
                        "cutoff": 350.0, "resonance": 0.5,
                        "attack": 0.005, "decay": 0.2, "sustain": 0.9, "release": 0.2
                    },
                    {
                        "name": "Grit Transient", "waveform": "fm", "octave": -1, "volume": 0.75, "pan": 0.0, "output_bus": 0,
                        "fm_ratio": 3.0, "fm_depth": 2.5, "cutoff": 1800.0, "resonance": 1.8, "drive": 0.6,
                        "attack": 0.002, "decay": 0.15, "sustain": 0.3, "release": 0.15
                    }
                ]
            },
            "Ethereal Dream Pad": {
                "master_volume": 0.90,
                "glide_time": 0.08,
                "delay": {"enabled": True, "time_sec": 0.50, "feedback": 0.50, "mix": 0.35},
                "reverb": {"enabled": True, "room_size": 0.85, "mix": 0.45},
                "layers": [
                    {
                        "name": "Soft Tri Pad", "waveform": "triangle", "octave": 0, "volume": 0.75, "pan": -0.25, "output_bus": 0,
                        "cutoff": 2200.0, "resonance": 0.8,
                        "attack": 0.45, "decay": 0.8, "sustain": 0.85, "release": 1.2,
                        "lfo_enabled": True, "lfo_rate": 0.6, "lfo_depth": 0.35, "lfo_target": "cutoff"
                    },
                    {
                        "name": "Shimmer Saw Pad", "waveform": "saw", "octave": 1, "fine_tune": 8.0, "volume": 0.55, "pan": 0.25, "output_bus": 0,
                        "cutoff": 3200.0, "resonance": 1.0,
                        "attack": 0.60, "decay": 0.9, "sustain": 0.80, "release": 1.5,
                        "lfo_enabled": True, "lfo_rate": 0.8, "lfo_depth": 0.20, "lfo_target": "pan"
                    }
                ]
            },
            "80s Synthwave Pluck": {
                "master_volume": 1.0,
                "glide_time": 0.0,
                "delay": {"enabled": True, "time_sec": 0.25, "feedback": 0.30, "mix": 0.25},
                "reverb": {"enabled": True, "room_size": 0.50, "mix": 0.20},
                "layers": [
                    {
                        "name": "Pluck Saw", "waveform": "saw", "octave": 0, "volume": 0.85, "pan": -0.1, "output_bus": 0,
                        "cutoff": 4500.0, "resonance": 2.0, "filter_env_amount": 0.70, "filter_decay": 0.18,
                        "attack": 0.002, "decay": 0.22, "sustain": 0.15, "release": 0.25
                    },
                    {
                        "name": "Pluck Square Detune", "waveform": "square", "octave": 0, "fine_tune": -10.0, "volume": 0.65, "pan": 0.1, "output_bus": 0,
                        "pulse_width": 0.35, "cutoff": 3800.0, "resonance": 1.5, "filter_env_amount": 0.50, "filter_decay": 0.16,
                        "attack": 0.002, "decay": 0.20, "sustain": 0.10, "release": 0.20
                    }
                ]
            },
            "Sci-Fi FM Resonator": {
                "master_volume": 0.90,
                "glide_time": 0.05,
                "delay": {"enabled": True, "time_sec": 0.33, "feedback": 0.45, "mix": 0.30},
                "reverb": {"enabled": True, "room_size": 0.65, "mix": 0.25},
                "layers": [
                    {
                        "name": "FM Carrier/Mod", "waveform": "fm", "octave": 0, "volume": 0.85, "pan": 0.0, "output_bus": 0,
                        "fm_ratio": 3.5, "fm_depth": 3.0, "cutoff": 5000.0, "resonance": 2.5, "filter_type": "bandpass",
                        "attack": 0.02, "decay": 0.4, "sustain": 0.5, "release": 0.4,
                        "lfo_enabled": True, "lfo_rate": 4.5, "lfo_depth": 0.40, "lfo_target": "cutoff"
                    }
                ]
            }
        }

    def apply_preset(self, preset_name: str) -> bool:
        """Applique un preset d'usine ou personnalisé"""
        presets = self.get_factory_presets()
        target = None
        for k, v in presets.items():
            if k.lower() == preset_name.lower():
                target = v
                self.preset_name = k
                break
        if not target:
            return False

        self.master_volume = float(target.get("master_volume", 1.0))
        self.glide_time = float(target.get("glide_time", 0.0))

        del_data = target.get("delay", {})
        self.delay.enabled = bool(del_data.get("enabled", False))
        self.delay.time_sec = float(del_data.get("time_sec", 0.35))
        self.delay.feedback = float(del_data.get("feedback", 0.40))
        self.delay.mix = float(del_data.get("mix", 0.25))

        rev_data = target.get("reverb", {})
        self.reverb.enabled = bool(rev_data.get("enabled", True))
        self.reverb.room_size = float(rev_data.get("room_size", 0.50))
        self.reverb.mix = float(rev_data.get("mix", 0.20))

        layer_defs = target.get("layers", [])
        if layer_defs:
            self.layers = []
            for ld in layer_defs:
                l = SynthLayer()
                l.from_dict(ld)
                self.layers.append(l)

        return True

    # -------------------------------------------------------------------------
    # Rendu Audio Temps Réel & Multi-Sorties
    # -------------------------------------------------------------------------
    def render_slice(
        self,
        notes: List[Any],
        start_b: float,
        end_b: float,
        beats_per_sec: float,
        frames: int,
        sample_rate: int,
        bus_index: Optional[int] = None
    ) -> np.ndarray:
        """
        Rend les notes situées dans la tranche [start_b, end_b).
        Si bus_index est None : retourne le mixage stéréo sommateur de tous les bus avec master FX.
        Si 0 <= bus_index < 10 : retourne spécifiquement la sortie stéréo de ce bus !
        """
        if not self.enabled or frames <= 0:
            return np.zeros((frames, 2), dtype=np.float32)

        # Matrice des 10 sorties stéréo
        buses = [np.zeros((frames, 2), dtype=np.float32) for _ in range(self.NUM_OUTPUT_BUSES)]

        dur_sec = frames / sample_rate
        slice_start_sec = start_b / beats_per_sec
        slice_end_sec = end_b / beats_per_sec

        # Déterminer les couches actives
        active_layers = [l for l in self.layers if l.enabled and not l.muted]
        solos = [l for l in active_layers if l.soloed]
        if solos:
            active_layers = solos

        if not active_layers or not notes:
            return np.zeros((frames, 2), dtype=np.float32)

        # Rendu note par note
        for note in notes:
            pitch = getattr(note, "pitch", 60)
            note_start_b = getattr(note, "start_beat", 0.0)
            note_dur_b = getattr(note, "duration", 1.0)
            vel = getattr(note, "velocity", 100) / 127.0

            note_start_sec = note_start_b / beats_per_sec
            note_off_sec = (note_start_b + note_dur_b) / beats_per_sec

            # Vérifier si la note ou son release chevauche la tranche
            # Longueur max de release parmi les couches
            max_rel = max(l.release for l in active_layers)
            note_end_with_rel = note_off_sec + max_rel

            if note_end_with_rel < slice_start_sec or note_start_sec >= slice_end_sec:
                continue

            t_start_rel = slice_start_sec - note_start_sec
            t_end_rel = slice_end_sec - note_start_sec
            note_off_rel = note_off_sec - note_start_sec

            # Rendu pour chaque couche sonore
            for layer in active_layers:
                bus_dest = max(0, min(9, layer.output_bus))

                # Pitch final avec octave, demitons et fine tuning
                effective_pitch = pitch + (layer.octave * 12) + layer.semitone + (layer.fine_tune / 100.0)
                freq = pitch_to_freq(effective_pitch)

                # 1. Enveloppe d'amplitude
                amp_env = compute_adsr_envelope(
                    t_start_rel, t_end_rel, frames, note_off_rel,
                    layer.attack, layer.decay, layer.sustain, layer.release
                )
                if np.max(amp_env) <= 1e-5:
                    continue

                # 2. Enveloppe de filtre et modulation LFO
                mod_cutoff = None
                if abs(layer.filter_env_amount) > 0.01:
                    f_env = compute_adsr_envelope(
                        t_start_rel, t_end_rel, frames, note_off_rel,
                        layer.filter_attack, layer.filter_decay, layer.filter_sustain, layer.filter_release
                    )
                    mod_cutoff = (f_env * layer.filter_env_amount * 3.5).astype(np.float32)

                # Modulation LFO
                if layer.lfo_enabled and layer.lfo_depth > 0.01:
                    t_vec = np.linspace(slice_start_sec, slice_end_sec, frames, endpoint=False)
                    lfo_phase = (t_vec * layer.lfo_rate) % 1.0
                    if layer.lfo_waveform == "sine":
                        lfo_val = np.sin(2.0 * np.pi * lfo_phase)
                    elif layer.lfo_waveform == "triangle":
                        lfo_val = 2.0 * np.abs(2.0 * (lfo_phase - np.floor(lfo_phase + 0.5))) - 1.0
                    elif layer.lfo_waveform == "saw":
                        lfo_val = 2.0 * lfo_phase - 1.0
                    elif layer.lfo_waveform == "square":
                        lfo_val = np.where(lfo_phase < 0.5, 1.0, -1.0)
                    else:
                        lfo_val = np.random.uniform(-1.0, 1.0, frames)

                    lfo_mod = lfo_val * layer.lfo_depth

                    if layer.lfo_target == "cutoff":
                        if mod_cutoff is None:
                            mod_cutoff = (lfo_mod * 2.0).astype(np.float32)
                        else:
                            mod_cutoff += (lfo_mod * 2.0).astype(np.float32)
                    elif layer.lfo_target == "pitch":
                        freq *= float(2.0 ** (np.mean(lfo_mod) * 0.25))
                    elif layer.lfo_target == "amp":
                        amp_env *= np.clip(1.0 + lfo_mod * 0.5, 0.0, 1.5)

                # 3. Synthèse de l'oscillateur
                raw_osc = generate_oscillator(
                    wave_type=layer.waveform,
                    freq=freq,
                    num_samples=frames,
                    sample_rate=sample_rate,
                    phase_offset=0.0,
                    pulse_width=layer.pulse_width,
                    fm_ratio=layer.fm_ratio,
                    fm_depth=layer.fm_depth,
                    unison_detune=layer.unison_detune,
                    unison_spread=layer.unison_spread
                )

                # 4. Traitement par le filtre State-Variable
                svf = ChamberlinSVF()
                filtered = svf.process(
                    audio=raw_osc,
                    sample_rate=sample_rate,
                    filter_type=layer.filter_type,
                    cutoff=layer.cutoff,
                    resonance=layer.resonance,
                    drive=layer.drive,
                    mod_cutoff=mod_cutoff
                )

                # 5. Application de l'enveloppe, du volume et du panoramique
                vol = layer.volume * vel
                p = layer.pan
                gain_l = float(np.sqrt(0.5 * (1.0 - p))) * vol
                gain_r = float(np.sqrt(0.5 * (1.0 + p))) * vol

                voice_out_l = filtered[:, 0] * amp_env * gain_l
                voice_out_r = filtered[:, 1] * amp_env * gain_r

                buses[bus_dest][:, 0] += voice_out_l
                buses[bus_dest][:, 1] += voice_out_r

        # Multi-Sorties : si un bus précis est demandé
        if bus_index is not None and 0 <= bus_index < self.NUM_OUTPUT_BUSES:
            bus_audio = buses[bus_index]
            if np.max(np.abs(bus_audio)) > 1e-5:
                if self.delay.enabled:
                    bus_audio = self.delay.process(bus_audio)
                if self.reverb.enabled:
                    bus_audio = self.reverb.process(bus_audio)
            return (bus_audio * self.master_volume).astype(np.float32)

        # Mixeur général : Sommation de tous les bus actifs vers la sortie Principale
        master_mix = np.zeros((frames, 2), dtype=np.float32)
        for b in buses:
            master_mix += b

        # Master FX
        if self.delay.enabled:
            master_mix = self.delay.process(master_mix)
        if self.reverb.enabled:
            master_mix = self.reverb.process(master_mix)

        return (master_mix * self.master_volume).astype(np.float32)

    def render_slice_all_buses(
        self,
        notes: List[Any],
        start_b: float,
        end_b: float,
        beats_per_sec: float,
        frames: int,
        sample_rate: int
    ) -> List[np.ndarray]:
        """Retourne la liste des 10 buffers audio stéréo pour les 10 sorties simultanées"""
        return [
            self.render_slice(notes, start_b, end_b, beats_per_sec, frames, sample_rate, bus_index=i)
            for i in range(self.NUM_OUTPUT_BUSES)
        ]

    def render_note(
        self,
        pitch: int,
        duration_sec: float = 0.5,
        sample_rate: int = 44100,
        velocity: int = 100,
        bus_index: Optional[int] = None
    ) -> np.ndarray:
        """Rendu immédiat d'une note (pour Piano Roll ou clic clavier dans le GUI)"""
        frames = max(100, int(duration_sec * sample_rate))
        dummy_note = type("DummyNote", (), {
            "pitch": pitch,
            "start_beat": 0.0,
            "duration": 1.0,
            "velocity": velocity
        })()
        # 120 BPM => 2 beats par seconde => duration_sec = 1.0 beat pour 0.5s
        bpm = 60.0 / max(0.01, duration_sec)
        beats_per_sec = bpm / 60.0
        return self.render_slice(
            [dummy_note], 0.0, 2.0, beats_per_sec, frames, sample_rate, bus_index=bus_index
        )

    def process(self, audio: np.ndarray, sample_rate: int) -> np.ndarray:
        """Traitement audio FX si inséré sur une piste audio"""
        if not self.enabled or audio is None or len(audio) == 0:
            return audio
        out = self._input_svf.process(audio, sample_rate, filter_type="lowpass", cutoff=6000.0)
        if self.delay.enabled:
            out = self.delay.process(out)
        if self.reverb.enabled:
            out = self.reverb.process(out)
        return out * self.master_volume

    def reset(self) -> None:
        self.delay.reset()
        self.reverb.reset()
        self._input_svf.reset()

    # -------------------------------------------------------------------------
    # Sérialisation & État
    # -------------------------------------------------------------------------
    def get_state(self) -> Dict[str, Any]:
        return {
            "preset_name": self.preset_name,
            "master_volume": self.master_volume,
            "glide_time": self.glide_time,
            "saturation_drive": self.saturation_drive,
            "delay": {
                "enabled": self.delay.enabled,
                "time_sec": self.delay.time_sec,
                "feedback": self.delay.feedback,
                "ping_pong": self.delay.ping_pong,
                "mix": self.delay.mix,
            },
            "reverb": {
                "enabled": self.reverb.enabled,
                "room_size": self.reverb.room_size,
                "damping": self.reverb.damping,
                "width": self.reverb.width,
                "mix": self.reverb.mix,
            },
            "layers": [l.to_dict() for l in self.layers]
        }

    def set_state(self, state: Dict[str, Any]) -> None:
        self.preset_name = state.get("preset_name", self.preset_name)
        self.master_volume = float(state.get("master_volume", self.master_volume))
        self.glide_time = float(state.get("glide_time", self.glide_time))
        self.saturation_drive = float(state.get("saturation_drive", self.saturation_drive))

        del_data = state.get("delay", {})
        if del_data:
            self.delay.enabled = bool(del_data.get("enabled", self.delay.enabled))
            self.delay.time_sec = float(del_data.get("time_sec", self.delay.time_sec))
            self.delay.feedback = float(del_data.get("feedback", self.delay.feedback))
            self.delay.ping_pong = bool(del_data.get("ping_pong", self.delay.ping_pong))
            self.delay.mix = float(del_data.get("mix", self.delay.mix))

        rev_data = state.get("reverb", {})
        if rev_data:
            self.reverb.enabled = bool(rev_data.get("enabled", self.reverb.enabled))
            self.reverb.room_size = float(rev_data.get("room_size", self.reverb.room_size))
            self.reverb.damping = float(rev_data.get("damping", self.reverb.damping))
            self.reverb.width = float(rev_data.get("width", self.reverb.width))
            self.reverb.mix = float(rev_data.get("mix", self.reverb.mix))

        layer_data = state.get("layers", [])
        if layer_data:
            self.layers = []
            for ld in layer_data:
                l = SynthLayer()
                l.from_dict(ld)
                self.layers.append(l)

    def create_editor(self, parent=None):
        """Instancie l'interface graphique PySide6 du NovaSynth"""
        try:
            from plugins.synth.synth_gui import NovaSynthGUI
            return NovaSynthGUI(self, parent=parent)
        except Exception as e:
            print(f"[NovaSynth] Erreur instanciation interface graphique : {e}")
            return None
