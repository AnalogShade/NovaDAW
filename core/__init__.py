"""
core - Package du noyau DAW
"""
from core.project import Project, Track, MidiClip, AudioClip, MidiNote
from core.serializer import save_project, load_project
from core.audio_engine import AudioEngine, midi_to_freq
from core.recent_projects import RecentProjectsManager, recent_projects_manager

__all__ = [
    "Project",
    "Track",
    "MidiClip",
    "AudioClip",
    "MidiNote",
    "save_project",
    "load_project",
    "AudioEngine",
    "midi_to_freq",
    "RecentProjectsManager",
    "recent_projects_manager",
]

