"""
core/mcp/midi_converters.py - Utilitaires de conversion musicale pour le protocole MCP
Supporte les conversions entre noms de notes (ex: 'C4', 'F#3', 'Bb2', 'Do4') et numéros MIDI (0-127).
"""
import re
from typing import Union, List, Optional

NOTE_NAMES_SHARP = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

NOTE_SEMITONES = {
    # Notation anglo-saxonne
    "c": 0, "c#": 1, "db": 1,
    "d": 2, "d#": 3, "eb": 3,
    "e": 4, "fb": 4, "e#": 5,
    "f": 5, "f#": 6, "gb": 6,
    "g": 7, "g#": 8, "ab": 8,
    "a": 9, "a#": 10, "bb": 10,
    "b": 11, "cb": 11, "b#": 0,
    # Notation française (Do, Ré, Mi, Fa, Sol, La, Si)
    "do": 0, "do#": 1, "reb": 1,
    "re": 2, "re#": 3, "mib": 3,
    "mi": 4,
    "fa": 5, "fa#": 6, "solb": 6,
    "sol": 7, "sol#": 8, "lab": 8,
    "la": 9, "la#": 10, "sib": 10,
    "si": 11,
}


def note_name_to_pitch(note: Union[str, int]) -> int:
    """
    Convertit un nom de note musical (ex: 'C4', 'F#3', 'Bb2', 'Sol3') ou un entier en numéro MIDI (0 - 127).
    Convention standard : C4 (Do central) = 60.
    """
    if isinstance(note, int):
        return max(0, min(127, note))

    if isinstance(note, float):
        return max(0, min(127, int(round(note))))

    s = str(note).strip()
    if s.isdigit() or (s.startswith("-") and s[1:].isdigit()):
        return max(0, min(127, int(s)))

    # Regex pour extraire la note et l'octave: ex: C#4, Bb-1, Sol3, Do#4
    match = re.match(r"^([a-zA-Z#]+)\s*(-?\d+)?$", s)
    if not match:
        raise ValueError(f"Format de note MIDI invalide : '{note}' (exemples attendus: 'C4', 'F#3', 'Bb2', 60)")

    name_part = match.group(1).lower()
    octave_part = match.group(2)
    octave = int(octave_part) if octave_part is not None else 4

    if name_part not in NOTE_SEMITONES:
        raise ValueError(f"Nom de note inconnu : '{name_part}' dans '{note}'")

    semitone = NOTE_SEMITONES[name_part]
    # C4 = (4 + 1) * 12 + semitone = 60
    pitch = (octave + 1) * 12 + semitone
    return max(0, min(127, pitch))


def pitch_to_note_name(pitch: int) -> str:
    """
    Convertit un pitch MIDI (0 - 127) en nom de note (ex: 60 -> 'C4', 69 -> 'A4').
    """
    p = max(0, min(127, int(pitch)))
    octave = (p // 12) - 1
    name = NOTE_NAMES_SHARP[p % 12]
    return f"{name}{octave}"
