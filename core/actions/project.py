"""
core/actions/project.py - Actions globales de gestion de projet, pistes, mixage et export pour NovaDAW
"""
import os
from typing import Dict, Any, List, Optional
from core.action_registry import action_registry
from core.project import Track
from core.serializer import save_project


@action_registry.register(
    name="novadaw_get_project_summary",
    description="Récupère un résumé complet du projet : nom, BPM, boucle, pistes (types, volumes, panoramiques, clips).",
    tags=["project"]
)
def get_project_summary(app) -> Dict[str, Any]:
    tracks_info = []
    for t in app.project.tracks:
        tracks_info.append({
            "id": t.id,
            "name": t.name,
            "type": t.track_type,
            "volume": t.volume,
            "pan": t.pan,
            "muted": t.muted,
            "soloed": t.soloed,
            "clips_count": len(t.clips),
            "clips": [
                {
                    "id": c.id,
                    "name": c.name,
                    "start_beat": c.start_beat,
                    "length_beats": c.length_beats,
                    "type": "midi" if hasattr(c, "notes") else "audio"
                }
                for c in t.clips
            ]
        })

    return {
        "project_name": app.project.name,
        "bpm": app.project.bpm,
        "time_signature": f"{app.project.time_sig_num}/{app.project.time_sig_den}",
        "loop_enabled": app.project.loop_enabled,
        "loop_start_beat": app.project.loop_start_beat,
        "loop_end_beat": app.project.loop_end_beat,
        "tracks_count": len(app.project.tracks),
        "tracks": tracks_info
    }


@action_registry.register(
    name="novadaw_create_track",
    description="Ajoute une nouvelle piste MIDI ou Audio au projet et met à jour l'interface.",
    tags=["project", "track"]
)
def create_track(app, name: str, track_type: str = "midi", color: Optional[str] = None) -> Dict[str, Any]:
    t_type = "audio" if track_type.lower() == "audio" else "midi"
    default_color = "#10b981" if t_type == "audio" else "#38bdf8"

    new_track = Track(
        name=name,
        track_type=t_type,
        color=color or default_color
    )
    app.project.add_track(new_track)

    if hasattr(app, "refresh_project_ui"):
        app.refresh_project_ui()

    return {
        "status": "success",
        "track_id": new_track.id,
        "name": new_track.name,
        "type": new_track.track_type,
        "color": new_track.color
    }


@action_registry.register(
    name="novadaw_set_track_controls",
    description="Ajuste les réglages de mixage d'une piste : volume (0.0 à 1.5), panoramique (-1.0 à 1.0), sourdine (muted) ou solo.",
    tags=["project", "mix"]
)
def set_track_controls(
    app,
    track_id_or_name: str,
    volume: Optional[float] = None,
    pan: Optional[float] = None,
    muted: Optional[bool] = None,
    soloed: Optional[bool] = None
) -> Dict[str, Any]:
    target_track = None
    needle = str(track_id_or_name).strip().lower()
    for t in app.project.tracks:
        if t.id.lower() == needle or t.name.lower() == needle:
            target_track = t
            break

    if not target_track:
        raise ValueError(f"Piste '{track_id_or_name}' introuvable.")

    if volume is not None:
        target_track.volume = max(0.0, min(1.5, float(volume)))
    if pan is not None:
        target_track.pan = max(-1.0, min(1.0, float(pan)))
    if muted is not None:
        target_track.muted = bool(muted)
    if soloed is not None:
        target_track.soloed = bool(soloed)

    if hasattr(app, "refresh_project_ui"):
        app.refresh_project_ui()

    return {
        "status": "success",
        "track_name": target_track.name,
        "volume": target_track.volume,
        "pan": target_track.pan,
        "muted": target_track.muted,
        "soloed": target_track.soloed
    }


@action_registry.register(
    name="novadaw_save_project",
    description="Enregistre le projet actuel sur le disque au format .ndaw.",
    tags=["project", "file"]
)
def save_project_action(app, file_path: Optional[str] = None) -> Dict[str, Any]:
    target_path = file_path or app.project.file_path or f"{app.project.name}.ndaw"
    if not target_path.endswith(".ndaw"):
        target_path += ".ndaw"

    save_project(app.project, target_path)
    app.project.file_path = target_path
    if hasattr(app, "statusBar"):
        app.statusBar().showMessage(f"Projet sauvegardé : {os.path.basename(target_path)}", 3000)

    return {"status": "success", "file_path": os.path.abspath(target_path)}


@action_registry.register(
    name="novadaw_export_wav",
    description="Exécute le mixage audio du projet complet et l'exporte en fichier WAV haute qualité.",
    tags=["project", "export"]
)
def export_wav(app, output_file_path: str, end_bar: Optional[int] = None) -> Dict[str, Any]:
    out_path = output_file_path.strip()
    if not out_path.endswith(".wav"):
        out_path += ".wav"

    target_bar = end_bar or int(max(8, (app.project.loop_end_beat / 4.0)))
    success = app.audio_engine.export_wav(out_path, end_bar=target_bar)
    if not success:
        raise RuntimeError("Échec de la génération du fichier WAV.")

    return {
        "status": "success",
        "file_path": os.path.abspath(out_path),
        "exported_bars": target_bar
    }
