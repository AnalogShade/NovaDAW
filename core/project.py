"""
core/project.py - Modèle de données pour le DAW
"""
import os
import math
from dataclasses import dataclass, field
from typing import List, Optional, Union, Any, Tuple, Dict
import uuid
import numpy as np



@dataclass
class MidiNote:
    pitch: int  # 0 - 127 (60 = C4 / Do4)
    start_beat: float  # Relatif au début du clip
    duration: float  # En temps (ex: 1.0 = 1 noire)
    velocity: int = 100  # 1 - 127

    def to_dict(self) -> dict:
        return {
            "pitch": int(self.pitch),
            "start_beat": float(self.start_beat),
            "duration": float(self.duration),
            "velocity": int(self.velocity),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MidiNote":
        return cls(
            pitch=data.get("pitch", 60),
            start_beat=data.get("start_beat", 0.0),
            duration=data.get("duration", 1.0),
            velocity=data.get("velocity", 100),
        )


@dataclass
class MidiClip:
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str = "MIDI Pattern"
    start_beat: float = 0.0
    length_beats: float = 4.0  # Par défaut 1 mesure en 4/4
    notes: List[MidiNote] = field(default_factory=list)
    color: str = "#3b82f6"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "type": "midi",
            "start_beat": float(self.start_beat),
            "length_beats": float(self.length_beats),
            "color": self.color,
            "notes": [n.to_dict() for n in self.notes],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MidiClip":
        clip = cls(
            id=data.get("id", str(uuid.uuid4())[:8]),
            name=data.get("name", "MIDI Pattern"),
            start_beat=data.get("start_beat", 0.0),
            length_beats=data.get("length_beats", 4.0),
            color=data.get("color", "#3b82f6"),
        )
        clip.notes = [MidiNote.from_dict(n) for n in data.get("notes", [])]
        return clip


    def split(self, split_beat: float) -> Tuple["MidiClip", "MidiClip"]:
        """Scinde le clip MIDI en deux parties à la position split_beat (en temps absolus du projet)"""
        rel_split = max(0.0, min(self.length_beats, split_beat - self.start_beat))
        notes1 = []
        notes2 = []
        for n in self.notes:
            n_start = n.start_beat
            n_end = n.start_beat + n.duration
            if n_start < rel_split:
                d1 = min(n.duration, rel_split - n_start)
                if d1 > 0.01:
                    notes1.append(MidiNote(n.pitch, n_start, d1, n.velocity))
            if n_end > rel_split:
                d2 = n_end - max(rel_split, n_start)
                s2 = max(0.0, n_start - rel_split)
                if d2 > 0.01:
                    notes2.append(MidiNote(n.pitch, s2, d2, n.velocity))

        part1 = MidiClip(
            id=str(uuid.uuid4())[:8],
            name=f"{self.name} (Part 1)",
            start_beat=self.start_beat,
            length_beats=rel_split,
            notes=notes1,
            color=self.color
        )
        part2 = MidiClip(
            id=str(uuid.uuid4())[:8],
            name=f"{self.name} (Part 2)",
            start_beat=self.start_beat + rel_split,
            length_beats=max(0.1, self.length_beats - rel_split),
            notes=notes2,
            color=self.color
        )
        return part1, part2


@dataclass
class AudioClip:
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str = "Audio Clip"
    start_beat: float = 0.0
    length_beats: float = 4.0
    file_path: Optional[str] = None
    gain: float = 1.0
    color: str = "#10b981"
    source_offset_beats: float = 0.0  # Décalage de départ dans le fichier audio source (en temps)
    # Données audio non sérialisées en JSON (chargées à la volée)
    audio_data: Optional[np.ndarray] = None
    sample_rate: int = 44100

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "type": "audio",
            "start_beat": float(self.start_beat),
            "length_beats": float(self.length_beats),
            "file_path": self.file_path,
            "gain": float(self.gain),
            "color": self.color,
            "source_offset_beats": float(getattr(self, "source_offset_beats", 0.0)),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AudioClip":
        clip = cls(
            id=data.get("id", str(uuid.uuid4())[:8]),
            name=data.get("name", "Audio Clip"),
            start_beat=data.get("start_beat", 0.0),
            length_beats=data.get("length_beats", 4.0),
            file_path=data.get("file_path"),
            gain=data.get("gain", 1.0),
            color=data.get("color", "#10b981"),
            source_offset_beats=data.get("source_offset_beats", 0.0),
        )
        return clip

    def _ensure_audio_loaded(self):
        """Assure que les données audio sont chargées depuis file_path si nécessaire"""
        if self.audio_data is None and self.file_path and os.path.exists(self.file_path):
            try:
                import soundfile as sf
                data, sr = sf.read(self.file_path, dtype="float32")
                self.audio_data = data
                self.sample_rate = sr
            except Exception:
                pass

    def get_total_duration_seconds(self) -> float:
        """Retourne la durée totale du fichier audio source en secondes"""
        self._ensure_audio_loaded()
        if self.audio_data is not None and self.sample_rate > 0:
            return len(self.audio_data) / float(self.sample_rate)
        return 0.0

    def get_total_duration_beats(self, bpm: float = 120.0) -> float:
        """Retourne la durée totale du fichier audio source en temps (beats) selon le BPM"""
        dur_sec = self.get_total_duration_seconds()
        eff_bpm = max(20.0, float(bpm))
        return (dur_sec * eff_bpm) / 60.0

    def get_max_allowed_length_beats(self, bpm: float = 120.0) -> float:
        """Retourne la longueur maximale autorisée pour ce clip afin de ne pas dépasser la fin de l'audio"""
        total_b = self.get_total_duration_beats(bpm)
        if total_b <= 0:
            return 9999.0
        offset_b = max(0.0, float(getattr(self, "source_offset_beats", 0.0)))
        return max(0.25, total_b - offset_b)

    def get_waveform_peaks(self, num_bins: int = 1000) -> Tuple[np.ndarray, np.ndarray]:
        """
        Calcule et met en cache les crêtes (min, max) de l'ensemble de l'onde pour un affichage global.
        Retourne (mins, maxs) avec valeurs entre -1.0 et 1.0.
        """
        self._ensure_audio_loaded()
        if self.audio_data is None or len(self.audio_data) == 0:
            return np.zeros(0, dtype=np.float32), np.zeros(0, dtype=np.float32)

        data = self.audio_data
        if data.ndim > 1:
            data = data[:, 0]  # Canal gauche / mono pour vue globale

        total_samples = len(data)
        bins = max(1, min(int(num_bins), total_samples))

        cache = getattr(self, "_waveform_cache", None)
        if cache is not None:
            c_len, c_bins, c_gain, c_mins, c_maxs = cache
            if c_len == total_samples and c_bins == bins and c_gain == self.gain:
                return c_mins, c_maxs

        samples_per_bin = total_samples // bins
        usable_samples = bins * samples_per_bin

        if samples_per_bin > 1:
            reshaped = data[:usable_samples].reshape(bins, samples_per_bin)
            mins = np.min(reshaped, axis=1).astype(np.float32)
            maxs = np.max(reshaped, axis=1).astype(np.float32)
        else:
            mins = data[:bins].astype(np.float32)
            maxs = data[:bins].astype(np.float32)

        eff_gain = float(getattr(self, "gain", 1.0))
        if eff_gain != 1.0:
            mins = np.clip(mins * eff_gain, -1.0, 1.0)
            maxs = np.clip(maxs * eff_gain, -1.0, 1.0)

        self._waveform_cache = (total_samples, bins, eff_gain, mins, maxs)
        return mins, maxs

    def get_slice_waveform_peaks(self, start_sample: int, end_sample: int, num_bins: int) -> Tuple[np.ndarray, np.ndarray]:
        """
        Calcule les crêtes (min, max) pour une tranche précise [start_sample, end_sample].
        Garantit que la forme d'onde ne s'étire pas lors du rognage de bloc.
        """
        self._ensure_audio_loaded()
        if self.audio_data is None or len(self.audio_data) == 0:
            return np.zeros(0, dtype=np.float32), np.zeros(0, dtype=np.float32)

        data = self.audio_data
        if data.ndim > 1:
            data = data[:, 0]

        total_samples = len(data)
        s_start = max(0, min(total_samples, int(start_sample)))
        s_end = max(s_start, min(total_samples, int(end_sample)))

        slice_len = s_end - s_start
        if slice_len <= 0 or num_bins <= 0:
            return np.zeros(0, dtype=np.float32), np.zeros(0, dtype=np.float32)

        bins = max(1, min(int(num_bins), slice_len))
        slice_data = data[s_start:s_end]

        samples_per_bin = slice_len // bins
        usable = bins * samples_per_bin

        if samples_per_bin > 1:
            reshaped = slice_data[:usable].reshape(bins, samples_per_bin)
            mins = np.min(reshaped, axis=1).astype(np.float32)
            maxs = np.max(reshaped, axis=1).astype(np.float32)
        else:
            mins = slice_data[:bins].astype(np.float32)
            maxs = slice_data[:bins].astype(np.float32)

        eff_gain = float(getattr(self, "gain", 1.0))
        if eff_gain != 1.0:
            mins = np.clip(mins * eff_gain, -1.0, 1.0)
            maxs = np.clip(maxs * eff_gain, -1.0, 1.0)

        return mins, maxs

    def split(self, split_beat: float, bpm: float = 120.0) -> Tuple["AudioClip", "AudioClip"]:
        """Scinde le clip audio en deux parties à split_beat (temps absolu) sans altérer l'alignement sonore"""
        rel_split = max(0.0, min(self.length_beats, split_beat - self.start_beat))
        cur_offset = float(getattr(self, "source_offset_beats", 0.0))

        part1 = AudioClip(
            id=str(uuid.uuid4())[:8],
            name=f"{self.name} (Part 1)",
            start_beat=self.start_beat,
            length_beats=rel_split,
            file_path=self.file_path,
            gain=self.gain,
            color=self.color,
            source_offset_beats=cur_offset,
            audio_data=self.audio_data,
            sample_rate=self.sample_rate
        )

        part2 = AudioClip(
            id=str(uuid.uuid4())[:8],
            name=f"{self.name} (Part 2)",
            start_beat=self.start_beat + rel_split,
            length_beats=max(0.1, self.length_beats - rel_split),
            file_path=self.file_path,
            gain=self.gain,
            color=self.color,
            source_offset_beats=cur_offset + rel_split,
            audio_data=self.audio_data,
            sample_rate=self.sample_rate
        )
        return part1, part2



ClipType = Union[MidiClip, AudioClip]


AUTOMATION_PARAM_SPECS: Dict[str, Dict[str, Any]] = {
    "volume": {
        "label": "Volume",
        "icon": "🔊",
        "min_val": 0.0,
        "max_val": 2.0,
        "default_val": 0.8,
        "center_val": None,
        "color": "#38bdf8",
    },
    "pan": {
        "label": "Panoramique",
        "icon": "↔",
        "min_val": -1.0,
        "max_val": 1.0,
        "default_val": 0.0,
        "center_val": 0.0,
        "color": "#a855f7",
    },
}


def get_automation_param_spec(target_param: str) -> Dict[str, Any]:
    """Retourne les métadonnées (plage, défaut, couleur) d'un paramètre automatisable."""
    if target_param in AUTOMATION_PARAM_SPECS:
        return AUTOMATION_PARAM_SPECS[target_param]
    return {
        "label": target_param.replace("_", " ").title(),
        "icon": "🎛️",
        "min_val": 0.0,
        "max_val": 1.0,
        "default_val": 0.5,
        "center_val": None,
        "color": "#10b981",
    }


def format_automation_value(target_param: str, value: float) -> str:
    """Formate lisiblement une valeur d'automatisation pour l'interface."""
    if target_param == "volume":
        return f"{int(round(value * 100.0))}%"
    if target_param == "pan":
        pct = int(round(value * 100.0))
        if pct == 0:
            return "C"
        return f"L{abs(pct)}" if pct < 0 else f"R{pct}"
    return f"{value:.2f}"


def _eval_segment_t(t: Union[float, np.ndarray], curve_type: str, curve_tension: float):
    """Calcule le facteur d'interpolation normalisé [0..1] selon le type de courbe."""
    if curve_type == "step":
        return 0.0 if isinstance(t, float) else np.zeros_like(t, dtype=np.float32)
    if curve_type == "curve":
        tension = max(-1.0, min(1.0, float(curve_tension)))
        if abs(tension) < 1e-4:
            # S-curve douce (cosinus) par défaut quand on choisit 'courbe' sans tension asymétrique
            if isinstance(t, float):
                return 0.5 * (1.0 - math.cos(math.pi * t))
            return (0.5 * (1.0 - np.cos(np.pi * t))).astype(np.float32)
        exp = 1.0 + 3.0 * abs(tension)
        if tension > 0.0:
            return t ** exp
        return 1.0 - (1.0 - t) ** exp
    return t


@dataclass
class AutomationPoint:
    """Point de contrôle d'une courbe d'automatisation."""
    beat: float = 0.0
    value: float = 0.8
    curve_type: str = "linear"  # "linear", "step" (angle droit), "curve" (courbe)
    curve_tension: float = 0.0  # -1.0 .. +1.0 (0.0 = S-curve douce en mode "curve")
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "beat": float(self.beat),
            "value": float(self.value),
            "curve_type": str(self.curve_type),
            "curve_tension": float(self.curve_tension),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AutomationPoint":
        ctype = data.get("curve_type", "linear")
        if ctype not in ("linear", "step", "curve"):
            ctype = "linear"
        return cls(
            id=data.get("id", str(uuid.uuid4())[:8]),
            beat=max(0.0, float(data.get("beat", 0.0))),
            value=float(data.get("value", 0.8)),
            curve_type=ctype,
            curve_tension=max(-1.0, min(1.0, float(data.get("curve_tension", 0.0)))),
        )


@dataclass
class AutomationLane:
    """Sous-piste d'automatisation liée à un paramètre de piste ou de plugin."""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    target_param: str = "volume"  # "volume", "pan", ou paramètre de plugin
    target_scope: str = "track"   # "track" | "plugin" (préparé pour Nova Vocal Glide, etc.)
    target_plugin_id: Optional[str] = None
    name: str = ""
    enabled: bool = True
    visible: bool = True
    height: int = 56
    mode: str = "read"  # "read", "write", "touch", "latch"
    color: str = ""
    points: List[AutomationPoint] = field(default_factory=list)

    def __post_init__(self):
        spec = get_automation_param_spec(self.target_param)
        if not self.name:
            self.name = spec["label"]
        if not self.color:
            self.color = spec["color"]
        if self.mode not in ("read", "write", "touch", "latch"):
            self.mode = "read"
        self.height = max(40, min(220, int(self.height)))
        self.sort_points()

    def get_bounds(self) -> Tuple[float, float]:
        spec = get_automation_param_spec(self.target_param)
        return float(spec["min_val"]), float(spec["max_val"])

    def clamp_value(self, value: float) -> float:
        min_v, max_v = self.get_bounds()
        return max(min_v, min(max_v, float(value)))

    def sort_points(self) -> None:
        self.points.sort(key=lambda p: p.beat)

    def add_point(
        self,
        beat: float,
        value: float,
        curve_type: str = "linear",
        curve_tension: float = 0.0,
        merge_tol: float = 1e-4,
    ) -> AutomationPoint:
        """Ajoute ou met à jour un point à `beat` avec la valeur bornée."""
        b = max(0.0, float(beat))
        v = self.clamp_value(value)
        for pt in self.points:
            if abs(pt.beat - b) <= merge_tol:
                pt.beat = b
                pt.value = v
                pt.curve_type = curve_type if curve_type in ("linear", "step", "curve") else pt.curve_type
                pt.curve_tension = max(-1.0, min(1.0, float(curve_tension)))
                self.sort_points()
                return pt
        pt = AutomationPoint(
            beat=b,
            value=v,
            curve_type=curve_type if curve_type in ("linear", "step", "curve") else "linear",
            curve_tension=max(-1.0, min(1.0, float(curve_tension))),
        )
        self.points.append(pt)
        self.sort_points()
        return pt

    def remove_point(self, point_id: str) -> bool:
        before = len(self.points)
        self.points = [p for p in self.points if p.id != point_id]
        return len(self.points) < before

    def evaluate(self, beat: float, fallback_value: Optional[float] = None) -> float:
        """Évalue la valeur de la courbe à un temps donné (en beats)."""
        if not self.points:
            if fallback_value is not None:
                return self.clamp_value(fallback_value)
            return float(get_automation_param_spec(self.target_param)["default_val"])

        b = float(beat)
        if b <= self.points[0].beat:
            return self.clamp_value(self.points[0].value)
        if b >= self.points[-1].beat:
            return self.clamp_value(self.points[-1].value)

        for i in range(len(self.points) - 1):
            p0 = self.points[i]
            p1 = self.points[i + 1]
            if p0.beat <= b < p1.beat:
                span = p1.beat - p0.beat
                if span <= 1e-9:
                    return self.clamp_value(p1.value)
                if p0.curve_type == "step":
                    return self.clamp_value(p0.value)
                t = (b - p0.beat) / span
                t_eff = _eval_segment_t(t, p0.curve_type, p0.curve_tension)
                return self.clamp_value(p0.value + t_eff * (p1.value - p0.value))

        return self.clamp_value(self.points[-1].value)

    def evaluate_range(
        self,
        start_beat: float,
        end_beat: float,
        num_frames: int,
        fallback_value: Optional[float] = None,
    ) -> np.ndarray:
        """Évalue la courbe échantillon par échantillon sur [start_beat, end_beat) en NumPy."""
        n = max(0, int(num_frames))
        if n == 0:
            return np.zeros(0, dtype=np.float32)

        if not self.points:
            val = self.clamp_value(
                fallback_value
                if fallback_value is not None
                else get_automation_param_spec(self.target_param)["default_val"]
            )
            return np.full(n, val, dtype=np.float32)

        if len(self.points) == 1 or end_beat <= self.points[0].beat:
            return np.full(n, self.clamp_value(self.points[0].value), dtype=np.float32)

        if start_beat >= self.points[-1].beat:
            return np.full(n, self.clamp_value(self.points[-1].value), dtype=np.float32)

        beats = np.linspace(float(start_beat), float(end_beat), n, endpoint=False, dtype=np.float64)
        out = np.empty(n, dtype=np.float32)

        first_b = self.points[0].beat
        last_b = self.points[-1].beat
        out[beats <= first_b] = self.points[0].value
        out[beats >= last_b] = self.points[-1].value

        for i in range(len(self.points) - 1):
            p0 = self.points[i]
            p1 = self.points[i + 1]
            if p1.beat <= start_beat or p0.beat >= end_beat:
                continue
            mask = (beats >= p0.beat) & (beats < p1.beat)
            if not np.any(mask):
                continue
            span = p1.beat - p0.beat
            if span <= 1e-9 or p0.curve_type == "step":
                out[mask] = p0.value
            else:
                t = (beats[mask] - p0.beat) / span
                t_eff = _eval_segment_t(t, p0.curve_type, p0.curve_tension)
                out[mask] = p0.value + t_eff * (p1.value - p0.value)

        min_v, max_v = self.get_bounds()
        return np.clip(out, min_v, max_v).astype(np.float32)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "target_param": self.target_param,
            "target_scope": self.target_scope,
            "target_plugin_id": self.target_plugin_id,
            "name": self.name,
            "enabled": bool(self.enabled),
            "visible": bool(self.visible),
            "height": int(self.height),
            "mode": self.mode,
            "color": self.color,
            "points": [p.to_dict() for p in self.points],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AutomationLane":
        lane = cls(
            id=data.get("id", str(uuid.uuid4())[:8]),
            target_param=data.get("target_param", "volume"),
            target_scope=data.get("target_scope", "track"),
            target_plugin_id=data.get("target_plugin_id"),
            name=data.get("name", ""),
            enabled=bool(data.get("enabled", True)),
            visible=bool(data.get("visible", True)),
            height=int(data.get("height", 56)),
            mode=data.get("mode", "read"),
            color=data.get("color", ""),
        )
        lane.points = [AutomationPoint.from_dict(p) for p in data.get("points", [])]
        lane.sort_points()
        return lane


@dataclass
class Track:
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str = "Track"
    track_type: str = "midi"  # "midi", "audio" ou "master"
    color: str = "#3b82f6"
    volume: float = 0.8  # 0.0 à 1.5
    pan: float = 0.0  # -1.0 (gauche) à +1.0 (droite)
    muted: bool = False
    soloed: bool = False
    armed: bool = False
    monitoring: bool = False
    height: int = 76  # Hauteur personnalisable de la piste (48 à 300px)
    clips: List[ClipType] = field(default_factory=list)
    plugin_path: Optional[str] = None
    plugin_name: Optional[str] = None
    insert_effects: List[str] = field(default_factory=list)
    plugins: List[Any] = field(default_factory=list)
    midi_channel: int = 1  # Canal MIDI de sortie, 1 à 16
    synth_output_bus: Optional[int] = None  # Sortie stéréo assignée de 0 à 9 (Out 1 à Out 10)
    synth_route_track_id: Optional[str] = None  # Piste hébergeant le synthétiseur partagé
    automation_lanes: List[AutomationLane] = field(default_factory=list)
    automation_expanded: bool = True

    def get_automation_lane(
        self,
        target_param: str,
        target_plugin_id: Optional[str] = None,
    ) -> Optional[AutomationLane]:
        """Retourne la sous-piste d'automatisation correspondant au paramètre ciblé."""
        for lane in self.automation_lanes:
            if lane.target_param == target_param and lane.target_plugin_id == target_plugin_id:
                return lane
        return None

    def get_automation_lane_by_id(self, lane_id: str) -> Optional[AutomationLane]:
        for lane in self.automation_lanes:
            if lane.id == lane_id:
                return lane
        return None

    def add_automation_lane(
        self,
        target_param: str,
        target_plugin_id: Optional[str] = None,
        target_scope: str = "track",
        seed_initial_point: bool = True,
    ) -> AutomationLane:
        """Ajoute une piste d'automatisation liée à cette piste (ou réaffiche si existante)."""
        existing = self.get_automation_lane(target_param, target_plugin_id)
        if existing is not None:
            existing.visible = True
            self.automation_expanded = True
            return existing

        lane = AutomationLane(
            target_param=target_param,
            target_scope=target_scope,
            target_plugin_id=target_plugin_id,
        )
        if seed_initial_point:
            if target_param == "volume":
                init_val = self.volume
            elif target_param == "pan":
                init_val = self.pan
            else:
                init_val = get_automation_param_spec(target_param)["default_val"]
            lane.add_point(0.0, init_val, curve_type="linear")
        self.automation_lanes.append(lane)
        self.automation_expanded = True
        return lane

    def remove_automation_lane(self, lane_id: str) -> bool:
        before = len(self.automation_lanes)
        self.automation_lanes = [l for l in self.automation_lanes if l.id != lane_id]
        return len(self.automation_lanes) < before

    def add_plugin(self, plugin: Any, index: Optional[int] = None) -> Any:
        """Ajoute un plugin à la pile d'effets de la piste"""
        if index is not None and 0 <= index <= len(self.plugins):
            self.plugins.insert(index, plugin)
        else:
            self.plugins.append(plugin)
        return plugin

    def remove_plugin(self, instance_id: str) -> bool:
        """Retire un plugin de la pile d'effets par son instance_id"""
        removed = self.get_plugin(instance_id)
        initial_len = len(self.plugins)
        self.plugins = [p for p in self.plugins if getattr(p, "instance_id", None) != instance_id]
        if (
            self.plugin_path == "novadaw.piano"
            and getattr(removed, "plugin_type_id", None) == self.plugin_path
            and not any(p.plugin_type_id == self.plugin_path for p in self.plugins)
        ):
            self.plugin_path = self.plugin_name = None
        return len(self.plugins) < initial_len

    def move_plugin(self, from_index: int, to_index: int) -> bool:
        """Déplace un plugin dans l'ordre de la pile d'effets"""
        if 0 <= from_index < len(self.plugins) and 0 <= to_index < len(self.plugins):
            item = self.plugins.pop(from_index)
            self.plugins.insert(to_index, item)
            return True
        return False

    def get_plugin(self, instance_id: str) -> Optional[Any]:
        """Récupère un plugin par son instance_id"""
        for p in self.plugins:
            if getattr(p, "instance_id", None) == instance_id:
                return p
        return None

    def to_dict(self) -> dict:
        plugin_path = self.plugin_path
        plugin_name = self.plugin_name
        if not plugin_path and self.track_type == "midi":
            for p in self.plugins:
                if getattr(p, "is_instrument", False) or getattr(p, "category", "") == "instrument":
                    plugin_path = getattr(p, "plugin_type_id", None)
                    plugin_name = getattr(p, "name", None)
                    break

        return {
            "id": self.id,
            "name": self.name,
            "track_type": self.track_type,
            "color": self.color,
            "volume": float(self.volume),
            "pan": float(self.pan),
            "muted": self.muted,
            "soloed": self.soloed,
            "armed": self.armed,
            "monitoring": self.monitoring,
            "height": int(self.height),
            "plugin_path": plugin_path,
            "plugin_name": plugin_name,
            "insert_effects": list(self.insert_effects),
            "plugins": [p.to_dict() for p in self.plugins if hasattr(p, "to_dict")],
            "midi_channel": self.midi_channel,
            "synth_output_bus": self.synth_output_bus,
            "synth_route_track_id": self.synth_route_track_id,
            "automation_expanded": bool(self.automation_expanded),
            "automation_lanes": [l.to_dict() for l in self.automation_lanes],
            "clips": [c.to_dict() for c in self.clips],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Track":
        t = cls(
            id=data.get("id", str(uuid.uuid4())[:8]),
            name=data.get("name", "Track"),
            track_type=data.get("track_type", "midi"),
            color=data.get("color", "#3b82f6"),
            volume=data.get("volume", 0.8),
            pan=data.get("pan", 0.0),
            muted=data.get("muted", False),
            soloed=data.get("soloed", False),
            armed=data.get("armed", False),
            monitoring=data.get("monitoring", False),
            height=int(data.get("height", 76)),
            plugin_path=data.get("plugin_path"),
            plugin_name=data.get("plugin_name"),
            insert_effects=data.get("insert_effects", []),
            midi_channel=max(1, min(16, int(data.get("midi_channel", 1) or 1))),
            synth_output_bus=data.get("synth_output_bus"),
            synth_route_track_id=data.get("synth_route_track_id"),
            automation_expanded=bool(data.get("automation_expanded", True)),
        )
        t.automation_lanes = [
            AutomationLane.from_dict(l) for l in data.get("automation_lanes", [])
        ]
        t.clips = []
        for c in data.get("clips", []):
            if c.get("type") == "audio":
                t.clips.append(AudioClip.from_dict(c))
            else:
                t.clips.append(MidiClip.from_dict(c))

        # Restauration des plugins natifs
        t.plugins = []
        raw_plugins = data.get("plugins", [])
        if raw_plugins:
            try:
                from plugins.registry import plugin_registry, ensure_plugins_loaded
                ensure_plugins_loaded()
                for p_data in raw_plugins:
                    p = plugin_registry.create_from_dict(p_data)
                    if p:
                        t.plugins.append(p)
            except Exception as e:
                print(f"[Track.from_dict] Erreur chargement plugins: {e}")

        # Si plugin_path n'est pas renseigné mais qu'un instrument natif est présent dans la pile
        if not t.plugin_path and t.track_type == "midi":
            for p in t.plugins:
                if getattr(p, "is_instrument", False) or getattr(p, "category", "") == "instrument":
                    t.plugin_path = getattr(p, "plugin_type_id", None)
                    t.plugin_name = getattr(p, "name", None)
                    break

        # Si plugin_path est un plugin natif mais n'est pas encore dans plugins
        if t.plugin_path and str(t.plugin_path).startswith("novadaw.") and t.track_type == "midi":
            has_p = any(getattr(p, "plugin_type_id", None) == t.plugin_path for p in t.plugins)
            if not has_p:
                try:
                    from plugins.registry import plugin_registry, ensure_plugins_loaded
                    ensure_plugins_loaded()
                    p = plugin_registry.create_plugin(t.plugin_path)
                    if p:
                        t.plugins.insert(0, p)
                except Exception:
                    pass

        return t


@dataclass
class Project:
    name: str = "Nouveau Projet"
    bpm: float = 120.0
    time_sig_num: int = 4
    time_sig_den: int = 4
    loop_enabled: bool = False
    loop_start_beat: float = 0.0
    loop_end_beat: float = 16.0  # 4 mesures par défaut
    tracks: List[Track] = field(default_factory=list)
    master_track: Optional[Track] = None
    plugin_states: dict = field(default_factory=dict)
    plugin_rack: List[dict] = field(default_factory=list)  # VST Instrument / Effect Stack du projet
    file_path: Optional[str] = None

    def ensure_master_track(self) -> Track:
        """Garantit l'existence de la piste Master avec son plugin Mixeur par défaut"""
        if self.master_track is None:
            self.master_track = Track(
                name="Master",
                track_type="master",
                color="#f59e0b",
                volume=1.0
            )
            try:
                from plugins.registry import plugin_registry, ensure_plugins_loaded
                ensure_plugins_loaded()
                mixer = plugin_registry.create_plugin("novadaw.mixer")
                if mixer and hasattr(mixer, "set_project"):
                    mixer.set_project(self)
                if mixer:
                    self.master_track.add_plugin(mixer)
            except Exception as e:
                print(f"[Project] Erreur création mixeur master: {e}")
        else:
            # S'assurer que les plugins du master ont la référence au projet et que le mixeur existe
            has_mixer = any(getattr(p, "plugin_type_id", None) == "novadaw.mixer" for p in self.master_track.plugins)
            if not has_mixer:
                try:
                    from plugins.registry import plugin_registry, ensure_plugins_loaded
                    ensure_plugins_loaded()
                    mixer = plugin_registry.create_plugin("novadaw.mixer")
                    if mixer and hasattr(mixer, "set_project"):
                        mixer.set_project(self)
                    if mixer:
                        self.master_track.add_plugin(mixer)
                except Exception as e:
                    print(f"[Project] Erreur création mixeur master: {e}")
            for p in self.master_track.plugins:
                if hasattr(p, "set_project"):
                    p.set_project(self)
        return self.master_track

    def add_rack_plugin(self, file_path: str, name: str, plugin_type: str = "instrument") -> dict:
        for existing in self.plugin_rack:
            if existing.get("file_path") == file_path:
                return existing
        item = {
            "id": str(uuid.uuid4())[:8],
            "name": name,
            "file_path": file_path,
            "plugin_type": plugin_type,
            "enabled": True,
        }
        self.plugin_rack.append(item)
        return item

    def sync_rack_plugins(self):
        """Synchronise le rack global du projet avec tous les instruments et effets présents sur les pistes."""
        all_tracks = list(self.tracks)
        if self.master_track:
            all_tracks.append(self.master_track)

        for track in all_tracks:
            # 1. Instrument de sortie MIDI
            if getattr(track, "plugin_path", None):
                p_path = track.plugin_path
                p_name = getattr(track, "plugin_name", None) or (
                    "NovaSynth" if p_path == "novadaw.synth"
                    else "Nova Drums VSTi" if p_path == "novadaw.drum_machine"
                    else "Nova Guitar" if p_path == "novadaw.guitar"
                    else "Nova Piano" if p_path == "novadaw.piano"
                    else os.path.splitext(os.path.basename(p_path))[0]
                )
                self.add_rack_plugin(p_path, p_name, "instrument")

            # 2. Plugins natifs dans la pile de la piste (ignorer le mixeur interne automatique du master)
            for p in getattr(track, "plugins", []):
                p_type = getattr(p, "plugin_type_id", None)
                if p_type:
                    if p_type == "novadaw.mixer" and getattr(track, "track_type", "") == "master":
                        continue
                    p_name = getattr(p, "name", p_type)
                    is_inst = getattr(p, "is_instrument", False) or getattr(p, "category", "") == "instrument"
                    self.add_rack_plugin(p_type, p_name, "instrument" if is_inst else "effect")

            # 3. Effets VST3 externes insérés
            for fx_path in getattr(track, "insert_effects", []):
                fx_name = os.path.splitext(os.path.basename(fx_path))[0]
                self.add_rack_plugin(fx_path, fx_name, "effect")

    def get_rack_native_plugin(self, path):
        from plugins.registry import plugin_registry, ensure_plugins_loaded
        ensure_plugins_loaded()
        cache = getattr(self, "_rack_native_plugins", None)
        if cache is None:
            cache = self._rack_native_plugins = {}
        if path not in cache:
            item = next((p for p in self.plugin_rack if p["file_path"] == path), {})
            plugin = plugin_registry.create_plugin(path)
            if plugin and item.get("native_state"):
                plugin.set_state(item["native_state"])
            cache[path] = plugin
        return cache[path]

    def remove_rack_plugin(self, rack_id: str):
        self.plugin_rack = [p for p in self.plugin_rack if p["id"] != rack_id]

    def add_track(self, track: Track) -> None:
        self.tracks.append(track)

    def remove_track(self, track_id: str) -> Optional[Track]:
        removed = next((t for t in self.tracks if t.id == track_id), None)
        self.tracks = [t for t in self.tracks if t.id != track_id]
        for t in self.tracks:
            if getattr(t, "synth_route_track_id", None) == track_id:
                t.synth_route_track_id = None
                t.synth_output_bus = None
        return removed

    def move_track(self, from_index: int, to_index: int) -> bool:
        """Déplace une piste de from_index vers to_index en conservant toutes ses données."""
        n = len(self.tracks)
        if not (0 <= from_index < n and 0 <= to_index < n):
            return False
        if from_index == to_index:
            return False
        track = self.tracks.pop(from_index)
        self.tracks.insert(to_index, track)
        return True

    def move_track_by_id(self, track_id: str, to_index: int) -> bool:
        """Déplace une piste identifiée par son ID vers la position to_index."""
        if not self.tracks:
            return False
        from_index = next((i for i, t in enumerate(self.tracks) if t.id == track_id), -1)
        if from_index < 0:
            return False
        clamped_to = max(0, min(len(self.tracks) - 1, int(to_index)))
        return self.move_track(from_index, clamped_to)

    def duplicate_track(self, track_id: str) -> Optional[Track]:
        """Duplique intégralement une piste (clips, notes, audio, effets, réglages) juste en dessous."""
        idx = next((i for i, t in enumerate(self.tracks) if t.id == track_id), -1)
        if idx < 0:
            return None
        orig = self.tracks[idx]
        dup = Track.from_dict(orig.to_dict())
        dup.id = str(uuid.uuid4())[:8]
        dup.name = f"{orig.name} (Copie)"
        dup.armed = False
        dup.monitoring = False
        for orig_clip, dup_clip in zip(orig.clips, dup.clips):
            dup_clip.id = str(uuid.uuid4())[:8]
            if isinstance(orig_clip, AudioClip) and isinstance(dup_clip, AudioClip):
                if orig_clip.audio_data is not None:
                    dup_clip.audio_data = orig_clip.audio_data.copy()
                dup_clip.sample_rate = orig_clip.sample_rate
        for p in getattr(dup, "plugins", []):
            if hasattr(p, "instance_id"):
                p.instance_id = str(uuid.uuid4())[:8]
        for lane in getattr(dup, "automation_lanes", []):
            lane.id = str(uuid.uuid4())[:8]
            for pt in lane.points:
                pt.id = str(uuid.uuid4())[:8]
        self.tracks.insert(idx + 1, dup)
        return dup

    def get_track(self, track_id: str) -> Optional[Track]:
        if not track_id:
            return None
        needle = str(track_id).strip().lower()
        if self.master_track and (self.master_track.id.lower() == needle or needle in ("master", "piste master")):
            return self.master_track
        for t in self.tracks:
            if t.id.lower() == needle or t.name.lower() == needle:
                return t
        return None

    def find_clip(self, clip_id: str) -> tuple[Optional[Track], Optional[ClipType]]:
        for track in self.tracks:
            for clip in track.clips:
                if clip.id == clip_id:
                    return track, clip
        return None, None

    def beats_per_bar(self) -> float:
        return float(self.time_sig_num) * (4.0 / self.time_sig_den)

    @classmethod
    def create_empty(cls, name: str = "Nouveau Projet") -> "Project":
        """Crée un projet complètement vide (0 pistes) avec la piste Master et son mixeur initialisés."""
        proj = cls(
            name=name,
            bpm=120.0,
            time_sig_num=4,
            time_sig_den=4,
            loop_enabled=False,
            loop_start_beat=0.0,
            loop_end_beat=16.0,
            tracks=[],
            plugin_rack=[],
            file_path=None,
        )
        proj.ensure_master_track()
        return proj

    @classmethod
    def create_demo(cls, name: str = "Projet Démo") -> "Project":
        """Crée un projet de démonstration avec Synth Lead, Bass Synth, Batterie Nova Drums et piste Audio."""
        proj = cls(name=name, bpm=120.0)
        # Ajout d'une piste MIDI avec motif musical pour tester tout de suite
        synth_track = Track(
            name="Synth Lead",
            track_type="midi",
            color="#38bdf8",
            volume=0.85
        )
        
        # Bloc d'intro sur 2 mesures (8 temps)
        clip = MidiClip(
            name="Lead Pattern",
            start_beat=0.0,
            length_beats=8.0,
            color="#38bdf8",
            notes=[
                MidiNote(pitch=60, start_beat=0.0, duration=1.0),   # C4
                MidiNote(pitch=64, start_beat=1.0, duration=1.0),   # E4
                MidiNote(pitch=67, start_beat=2.0, duration=1.0),   # G4
                MidiNote(pitch=71, start_beat=3.0, duration=1.0),   # B4
                MidiNote(pitch=72, start_beat=4.0, duration=2.0),   # C5
                MidiNote(pitch=67, start_beat=6.0, duration=2.0),   # G4
            ]
        )
        synth_track.clips.append(clip)
        proj.add_track(synth_track)

        # Ajout d'une piste Basse
        bass_track = Track(
            name="Bass Synth",
            track_type="midi",
            color="#a855f7",
            volume=0.80
        )
        bass_clip = MidiClip(
            name="Bass Groove",
            start_beat=0.0,
            length_beats=8.0,
            color="#a855f7",
            notes=[
                MidiNote(pitch=36, start_beat=0.0, duration=2.0),   # C2
                MidiNote(pitch=36, start_beat=2.0, duration=2.0),   # C2
                MidiNote(pitch=41, start_beat=4.0, duration=2.0),   # F2
                MidiNote(pitch=43, start_beat=6.0, duration=2.0),   # G2
            ]
        )
        bass_track.clips.append(bass_clip)
        proj.add_track(bass_track)

        # Ajout d'une piste Batterie (Nova Drums VSTi) avec motif rythmique complet
        drum_track = Track(
            name="Batterie (Nova Drums)",
            track_type="midi",
            color="#f59e0b",
            volume=0.90,
            plugin_path="novadaw.drum_machine",
            plugin_name="Nova Drums VSTi"
        )
        try:
            from plugins.registry import plugin_registry, ensure_plugins_loaded
            ensure_plugins_loaded()
            drum_plugin = plugin_registry.create_plugin("novadaw.drum_machine")
            if drum_plugin:
                drum_track.add_plugin(drum_plugin)
        except Exception:
            pass

        # Motif de batterie rock/pop 8 temps (2 mesures)
        drum_notes = []
        # Cymbale Crash au temps 0
        drum_notes.append(MidiNote(pitch=49, start_beat=0.0, duration=2.0, velocity=110))
        # Grosses caisses (Kick 36)
        for b in [0.0, 2.0, 4.0, 5.5]:
            drum_notes.append(MidiNote(pitch=36, start_beat=b, duration=0.5, velocity=115))
        # Caisses claires (Snare 38)
        for b in [1.0, 3.0, 5.0]:
            drum_notes.append(MidiNote(pitch=38, start_beat=b, duration=0.5, velocity=110))
        # Charleston fermé (42) et ouvert (46)
        for b in [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0]:
            drum_notes.append(MidiNote(pitch=42, start_beat=b, duration=0.25, velocity=95))
        drum_notes.append(MidiNote(pitch=46, start_beat=5.5, duration=0.5, velocity=105))
        # Fill de toms en fin de mesure (50: High Tom, 47: Mid Tom, 41: Low Tom, 38: Snare)
        drum_notes.append(MidiNote(pitch=50, start_beat=6.0, duration=0.5, velocity=105))
        drum_notes.append(MidiNote(pitch=47, start_beat=6.5, duration=0.5, velocity=105))
        drum_notes.append(MidiNote(pitch=41, start_beat=7.0, duration=0.5, velocity=110))
        drum_notes.append(MidiNote(pitch=38, start_beat=7.5, duration=0.5, velocity=115))

        drum_clip = MidiClip(
            name="Drum Groove 4/4",
            start_beat=0.0,
            length_beats=8.0,
            color="#f59e0b",
            notes=drum_notes
        )
        drum_track.clips.append(drum_clip)
        proj.add_track(drum_track)

        # Ajout d'une piste Audio prête à accueillir un enregistrement ou échantillon
        audio_track = Track(
            name="Audio Guitare / Voix",
            track_type="audio",
            color="#10b981",
            volume=0.9
        )
        proj.add_track(audio_track)

        # Initialisation de la piste Master avec le plugin Mixeur par défaut
        proj.ensure_master_track()

        return proj

    @classmethod
    def create_default(cls) -> "Project":
        """Rétro-compatibilité : retourne le projet de démonstration pour les tests existants."""
        return cls.create_demo()
