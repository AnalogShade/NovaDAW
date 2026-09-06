"""
core/actions/midi.py - Actions de manipulation des notes et clips MIDI pour NovaDAW
"""
from typing import Dict, Any, List, Optional, Union
from core.action_registry import action_registry
from core.project import Track, MidiClip, MidiNote
from core.mcp.midi_converters import note_name_to_pitch, pitch_to_note_name


def _resolve_clip(app, clip_id_or_name: Optional[str] = None, track_id_or_name: Optional[str] = None) -> tuple[Optional[Track], Optional[MidiClip]]:
    """Trouve un clip MIDI et sa piste dans le projet."""
    # 1. Si clip_id_or_name est spécifié, chercher par ID ou par nom
    if clip_id_or_name:
        needle = str(clip_id_or_name).strip().lower()
        for track in app.project.tracks:
            if track_id_or_name:
                t_needle = str(track_id_or_name).strip().lower()
                if track.id.lower() != t_needle and track.name.lower() != t_needle:
                    continue
            for clip in track.clips:
                if isinstance(clip, MidiClip):
                    if clip.id.lower() == needle or clip.name.lower() == needle:
                        return track, clip

    # 2. Si non trouvé ou non spécifié, vérifier le clip actuellement ouvert dans le Piano Roll
    if hasattr(app, "piano_roll") and app.piano_roll.current_clip:
        return app.piano_roll.current_track, app.piano_roll.current_clip

    # 3. Fallback : prendre le premier clip MIDI disponible
    for track in app.project.tracks:
        if track_id_or_name:
            t_needle = str(track_id_or_name).strip().lower()
            if track.id.lower() != t_needle and track.name.lower() != t_needle:
                continue
        for clip in track.clips:
            if isinstance(clip, MidiClip):
                return track, clip

    return None, None


@action_registry.register(
    name="novadaw_list_midi_clips",
    description="Liste tous les clips MIDI existants dans le projet avec leurs pistes, IDs, positions temporelles et nombre de notes.",
    tags=["midi"]
)
def list_midi_clips(app, track_id_or_name: Optional[str] = None) -> List[Dict[str, Any]]:
    clips_info = []
    for track in app.project.tracks:
        if track_id_or_name:
            t_needle = str(track_id_or_name).strip().lower()
            if track.id.lower() != t_needle and track.name.lower() != t_needle:
                continue
        for clip in track.clips:
            if isinstance(clip, MidiClip):
                clips_info.append({
                    "track_id": track.id,
                    "track_name": track.name,
                    "clip_id": clip.id,
                    "clip_name": clip.name,
                    "start_beat": clip.start_beat,
                    "length_beats": clip.length_beats,
                    "notes_count": len(clip.notes),
                    "color": clip.color
                })
    return clips_info


@action_registry.register(
    name="novadaw_get_midi_notes",
    description="Récupère la liste de toutes les notes d'un clip MIDI avec leurs noms (ex: C4, G#3), temps de départ, durée et vélocité.",
    tags=["midi"]
)
def get_midi_notes(app, clip_id_or_name: Optional[str] = None, track_id_or_name: Optional[str] = None) -> Dict[str, Any]:
    track, clip = _resolve_clip(app, clip_id_or_name, track_id_or_name)
    if not clip:
        raise ValueError("Aucun clip MIDI trouvé correspondant aux critères.")

    notes_data = []
    for n in clip.notes:
        notes_data.append({
            "pitch": n.pitch,
            "note_name": pitch_to_note_name(n.pitch),
            "start_beat": round(n.start_beat, 3),
            "duration": round(n.duration, 3),
            "velocity": n.velocity
        })

    # Tri par temps croissant puis par hauteur de note
    notes_data.sort(key=lambda x: (x["start_beat"], x["pitch"]))

    return {
        "track_name": track.name if track else "Inconnu",
        "clip_id": clip.id,
        "clip_name": clip.name,
        "clip_start_beat": clip.start_beat,
        "clip_length_beats": clip.length_beats,
        "total_notes": len(notes_data),
        "notes": notes_data
    }


@action_registry.register(
    name="novadaw_add_midi_notes",
    description="Insère des notes dans un clip MIDI. Accepte les noms musicaux ('C4', 'Eb3', 'Sol3') ou numéros MIDI (0-127). Met à jour immédiatement le Piano Roll.",
    tags=["midi"]
)
def add_midi_notes(
    app,
    notes: List[Dict[str, Any]],
    clip_id_or_name: Optional[str] = None,
    track_id_or_name: Optional[str] = None,
    clear_existing: bool = False
) -> Dict[str, Any]:
    track, clip = _resolve_clip(app, clip_id_or_name, track_id_or_name)
    if not clip:
        raise ValueError("Aucun clip MIDI trouvé. Spécifiez un clip existant ou créez-en un avec 'novadaw_create_midi_clip'.")

    if clear_existing:
        clip.notes.clear()

    added = []
    for n_data in notes:
        raw_pitch = n_data.get("pitch", 60)
        pitch = note_name_to_pitch(raw_pitch)
        start_beat = max(0.0, float(n_data.get("start_beat", 0.0)))
        duration = max(0.0625, float(n_data.get("duration", 1.0)))
        velocity = max(1, min(127, int(n_data.get("velocity", 100))))

        midi_note = MidiNote(
            pitch=pitch,
            start_beat=start_beat,
            duration=duration,
            velocity=velocity
        )
        clip.notes.append(midi_note)
        added.append({
            "pitch": pitch,
            "note_name": pitch_to_note_name(pitch),
            "start_beat": start_beat,
            "duration": duration,
            "velocity": velocity
        })

    # Mise à jour de la longueur du clip si les notes dépassent
    max_beat = max((n.start_beat + n.duration for n in clip.notes), default=clip.length_beats)
    if max_beat > clip.length_beats:
        clip.length_beats = float(max_beat)

    # Rafraîchissement visuel direct du Piano Roll et de la Timeline
    if hasattr(app, "piano_roll") and track:
        # Charger et afficher directement ce clip dans le Piano Roll pour que l'utilisateur le voie
        app.piano_roll.open_clip(track, clip)
        if hasattr(app, "lower_zone"):
            app.lower_zone.setCurrentWidget(app.piano_roll)
            if getattr(app, "is_lower_zone_minimized", False):
                app._expand_lower_zone()

    if hasattr(app, "timeline_grid"):
        app.timeline_grid.update_dimensions()
        app.timeline_grid.update()

    return {
        "status": "success",
        "clip_id": clip.id,
        "clip_name": clip.name,
        "added_count": len(added),
        "total_notes": len(clip.notes),
        "notes_added": added
    }


@action_registry.register(
    name="novadaw_clear_midi_notes",
    description="Supprime toutes les notes d'un clip MIDI tout en conservant le clip.",
    tags=["midi"]
)
def clear_midi_notes(app, clip_id_or_name: Optional[str] = None, track_id_or_name: Optional[str] = None) -> Dict[str, Any]:
    track, clip = _resolve_clip(app, clip_id_or_name, track_id_or_name)
    if not clip:
        raise ValueError("Aucun clip MIDI trouvé.")

    count = len(clip.notes)
    clip.notes.clear()

    if hasattr(app, "piano_roll") and app.piano_roll.current_clip == clip:
        app.piano_roll.note_grid.update()
    if hasattr(app, "timeline_grid"):
        app.timeline_grid.update()

    return {"status": "success", "clip_id": clip.id, "cleared_count": count}


@action_registry.register(
    name="novadaw_remove_midi_notes",
    description="Supprime des notes spécifiques d'un clip selon leur hauteur ou une plage temporelle.",
    tags=["midi"]
)
def remove_midi_notes(
    app,
    clip_id_or_name: Optional[str] = None,
    pitch: Optional[Union[str, int]] = None,
    start_beat_min: Optional[float] = None,
    start_beat_max: Optional[float] = None
) -> Dict[str, Any]:
    track, clip = _resolve_clip(app, clip_id_or_name)
    if not clip:
        raise ValueError("Aucun clip MIDI trouvé.")

    target_pitch = note_name_to_pitch(pitch) if pitch is not None else None
    remaining = []
    removed_count = 0

    for n in clip.notes:
        match_pitch = (target_pitch is None) or (n.pitch == target_pitch)
        match_time = True
        if start_beat_min is not None and n.start_beat < float(start_beat_min):
            match_time = False
        if start_beat_max is not None and n.start_beat > float(start_beat_max):
            match_time = False

        if match_pitch and match_time:
            removed_count += 1
        else:
            remaining.append(n)

    clip.notes = remaining

    if hasattr(app, "piano_roll") and app.piano_roll.current_clip == clip:
        app.piano_roll.note_grid.update()
    if hasattr(app, "timeline_grid"):
        app.timeline_grid.update()

    return {"status": "success", "clip_id": clip.id, "removed_count": removed_count, "remaining_count": len(remaining)}


@action_registry.register(
    name="novadaw_create_midi_clip",
    description="Crée un nouveau bloc / clip MIDI sur une piste à une position temporelle donnée.",
    tags=["midi"]
)
def create_midi_clip(
    app,
    track_id_or_name: str,
    name: str = "MIDI Pattern",
    start_beat: float = 0.0,
    length_beats: float = 4.0,
    color: Optional[str] = None
) -> Dict[str, Any]:
    target_track = None
    needle = str(track_id_or_name).strip().lower()
    for t in app.project.tracks:
        if t.id.lower() == needle or t.name.lower() == needle:
            target_track = t
            break

    if not target_track:
        raise ValueError(f"Piste '{track_id_or_name}' introuvable.")

    new_clip = MidiClip(
        name=name,
        start_beat=max(0.0, float(start_beat)),
        length_beats=max(1.0, float(length_beats)),
        color=color or target_track.color,
    )
    target_track.clips.append(new_clip)

    # Ouvrir le clip dans le Piano Roll pour édition immédiate
    if hasattr(app, "piano_roll"):
        app.piano_roll.open_clip(target_track, new_clip)
        if hasattr(app, "lower_zone"):
            app.lower_zone.setCurrentWidget(app.piano_roll)

    if hasattr(app, "timeline_grid"):
        app.timeline_grid.update_dimensions()
        app.timeline_grid.update()

    return {
        "status": "success",
        "clip_id": new_clip.id,
        "clip_name": new_clip.name,
        "track_name": target_track.name,
        "start_beat": new_clip.start_beat,
        "length_beats": new_clip.length_beats
    }


@action_registry.register(
    name="novadaw_transpose_clip",
    description="Transpose toutes les notes d'un clip d'un intervalle en demi-tons (ex: +2 pour monter d'un ton, -12 pour descendre d'une octave).",
    tags=["midi"]
)
def transpose_clip(app, semitones: int, clip_id_or_name: Optional[str] = None) -> Dict[str, Any]:
    track, clip = _resolve_clip(app, clip_id_or_name)
    if not clip:
        raise ValueError("Aucun clip MIDI trouvé.")

    shift = int(semitones)
    for n in clip.notes:
        n.pitch = max(0, min(127, n.pitch + shift))

    if hasattr(app, "piano_roll") and app.piano_roll.current_clip == clip:
        app.piano_roll.note_grid.update()
    if hasattr(app, "timeline_grid"):
        app.timeline_grid.update()

    return {"status": "success", "clip_id": clip.id, "semitones": shift, "notes_count": len(clip.notes)}


@action_registry.register(
    name="novadaw_quantize_notes",
    description="Recale les temps de départ des notes sur la grille temporelle la plus proche (ex: 0.25 pour 1/16, 0.5 pour 1/8, 1.0 pour une noire).",
    tags=["midi"]
)
def quantize_notes(app, grid_division: float = 0.25, clip_id_or_name: Optional[str] = None) -> Dict[str, Any]:
    track, clip = _resolve_clip(app, clip_id_or_name)
    if not clip:
        raise ValueError("Aucun clip MIDI trouvé.")

    div = max(0.0625, float(grid_division))
    for n in clip.notes:
        n.start_beat = round(n.start_beat / div) * div

    if hasattr(app, "piano_roll") and app.piano_roll.current_clip == clip:
        app.piano_roll.note_grid.update()
    if hasattr(app, "timeline_grid"):
        app.timeline_grid.update()

    return {"status": "success", "clip_id": clip.id, "grid_division": div, "notes_count": len(clip.notes)}
