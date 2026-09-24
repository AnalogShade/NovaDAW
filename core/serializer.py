"""
core/serializer.py - Sauvegarde et chargement de projet au format .ndaw (JSON structuré)
"""
import json
import os
import soundfile as sf
from core.project import Project, AudioClip


def save_project(project: Project, file_path: str) -> None:
    """Enregistre l'état complet du projet dans un fichier .ndaw"""
    from core.plugin_manager import global_plugin_manager
    if getattr(global_plugin_manager, "current_project", None) is project:
        project.plugin_states = global_plugin_manager.capture_states()
    for item in project.plugin_rack:
        native = getattr(project, "_rack_native_plugins", {}).get(item["file_path"])
        if native:
            item["native_state"] = native.get_state()
    project_dict = {
        "format_version": "1.1",
        "name": project.name,
        "bpm": project.bpm,
        "time_sig_num": project.time_sig_num,
        "time_sig_denom": project.time_sig_den,
        "loop_enabled": project.loop_enabled,
        "loop_start_beat": project.loop_start_beat,
        "loop_end_beat": project.loop_end_beat,
        "plugin_rack": list(project.plugin_rack),
        "plugin_states": project.plugin_states,
        "master_track": project.master_track.to_dict() if project.master_track else None,
        "tracks": [t.to_dict() for t in project.tracks],
    }

    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(project_dict, f, indent=2, ensure_ascii=False)

    project.file_path = file_path


def load_project(file_path: str) -> Project:
    """Charge un projet depuis un fichier .ndaw"""
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    proj = Project(
        name=data.get("name", "Projet Sans Titre"),
        bpm=float(data.get("bpm", 120.0)),
        time_sig_num=int(data.get("time_sig_num", 4)),
        time_sig_den=int(data.get("time_sig_denom", 4)),
        loop_enabled=bool(data.get("loop_enabled", True)),
        loop_start_beat=float(data.get("loop_start_beat", 0.0)),
        loop_end_beat=float(data.get("loop_end_beat", 16.0)),
        plugin_rack=data.get("plugin_rack", []),
        plugin_states=data.get("plugin_states", {}),
        file_path=file_path,
    )

    from core.project import Track
    for t_data in data.get("tracks", []):
        track = Track.from_dict(t_data)
        # Lier le projet aux plugins si nécessaire (ex: Mixeur)
        for p in track.plugins:
            if hasattr(p, "set_project"):
                p.set_project(proj)

        # Recharger les données audio si des fichiers audio sont référencés
        for clip in track.clips:
            if isinstance(clip, AudioClip) and clip.file_path and os.path.exists(clip.file_path):
                try:
                    data_samples, sr = sf.read(clip.file_path, dtype="float32")
                    clip.audio_data = data_samples
                    clip.sample_rate = sr
                except Exception as e:
                    print(f"Erreur chargement audio {clip.file_path}: {e}")
        proj.add_track(track)

    # Chargement ou création de la piste Master
    if "master_track" in data and data["master_track"]:
        proj.master_track = Track.from_dict(data["master_track"])
        for p in proj.master_track.plugins:
            if hasattr(p, "set_project"):
                p.set_project(proj)
    else:
        proj.ensure_master_track()

    return proj
