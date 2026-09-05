"""
core/project.py - Modèle de données pour le DAW
"""
from dataclasses import dataclass, field
from typing import List, Optional, Union
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


@dataclass
class AudioClip:
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str = "Audio Clip"
    start_beat: float = 0.0
    length_beats: float = 4.0
    file_path: Optional[str] = None
    gain: float = 1.0
    color: str = "#10b981"
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
        )
        return clip


ClipType = Union[MidiClip, AudioClip]


@dataclass
class Track:
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str = "Track"
    track_type: str = "midi"  # "midi" ou "audio"
    color: str = "#3b82f6"
    volume: float = 0.8  # 0.0 à 1.5
    pan: float = 0.0  # -1.0 (gauche) à +1.0 (droite)
    muted: bool = False
    soloed: bool = False
    armed: bool = False
    clips: List[ClipType] = field(default_factory=list)

    def to_dict(self) -> dict:
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
        )
        t.clips = []
        for c in data.get("clips", []):
            if c.get("type") == "audio":
                t.clips.append(AudioClip.from_dict(c))
            else:
                t.clips.append(MidiClip.from_dict(c))
        return t


@dataclass
class Project:
    name: str = "Nouveau Projet"
    bpm: float = 120.0
    time_sig_num: int = 4
    time_sig_den: int = 4
    loop_enabled: bool = True
    loop_start_beat: float = 0.0
    loop_end_beat: float = 16.0  # 4 mesures par défaut
    tracks: List[Track] = field(default_factory=list)
    file_path: Optional[str] = None

    def add_track(self, track: Track) -> None:
        self.tracks.append(track)

    def remove_track(self, track_id: str) -> None:
        self.tracks = [t for t in self.tracks if t.id != track_id]

    def get_track(self, track_id: str) -> Optional[Track]:
        for t in self.tracks:
            if t.id == track_id:
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
    def create_default(cls) -> "Project":
        proj = cls(name="Nouveau Projet", bpm=120.0)
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

        # Ajout d'une piste Audio prête à accueillir un enregistrement ou échantillon
        audio_track = Track(
            name="Audio Guitare / Voix",
            track_type="audio",
            color="#10b981",
            volume=0.9
        )
        proj.add_track(audio_track)

        return proj
