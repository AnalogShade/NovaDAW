"""
core/actions/project.py - Actions globales de gestion de projet, pistes, mixage et export pour NovaDAW
"""
import os
from typing import Dict, Any, List, Optional
from core.action_registry import action_registry
from core.project import Project, Track
from core.serializer import save_project


@action_registry.register(
    name="novadaw_get_project_summary",
    description="Récupère un résumé complet du projet : nom, chemin, BPM, signature, boucle, bus Master et pistes (types, routages, volumes, panoramiques, armement, clips).",
    tags=["project"]
)
def get_project_summary(app) -> Dict[str, Any]:
    tracks_info = []
    for t in app.project.tracks:
        tracks_info.append({
            "id": t.id,
            "name": t.name,
            "type": t.track_type,
            "color": getattr(t, "color", "#38bdf8"),
            "midi_channel": t.midi_channel,
            "synth_output_bus": getattr(t, "synth_output_bus", None),
            "synth_route_track_id": getattr(t, "synth_route_track_id", None),
            "plugin_path": getattr(t, "plugin_path", None),
            "plugin_name": getattr(t, "plugin_name", None),
            "volume": t.volume,
            "pan": t.pan,
            "muted": t.muted,
            "soloed": t.soloed,
            "armed": getattr(t, "armed", False),
            "monitoring": getattr(t, "monitoring", False),
            "plugins_count": len(getattr(t, "plugins", [])),
            "automation_lanes_count": len(getattr(t, "automation_lanes", [])),
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

    master_t = app.project.ensure_master_track() if hasattr(app.project, "ensure_master_track") else getattr(app.project, "master_track", None)
    master_info = None
    if master_t is not None:
        master_info = {
            "id": master_t.id,
            "volume": master_t.volume,
            "pan": master_t.pan,
            "plugins_count": len(getattr(master_t, "plugins", [])),
            "insert_effects_count": len(getattr(master_t, "insert_effects", [])),
        }

    return {
        "project_name": app.project.name,
        "file_path": getattr(app.project, "file_path", None),
        "is_dirty": bool(getattr(app, "_is_dirty", False)),
        "bpm": app.project.bpm,
        "time_signature": f"{app.project.time_sig_num}/{app.project.time_sig_den}",
        "loop_enabled": app.project.loop_enabled,
        "loop_start_beat": app.project.loop_start_beat,
        "loop_end_beat": app.project.loop_end_beat,
        "master": master_info,
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

    if hasattr(app, "set_dirty"):
        app.set_dirty(True)

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
    description="Ajuste les réglages de mixage, d'enregistrement ou de routage d'une piste : volume (0.0 à 1.5), panoramique (-1.0 à 1.0), sourdine (muted), solo, armement Rec (armed), monitoring live (monitoring), nom, couleur, sortie stéréo NovaSynth (synth_output_bus 0..9 ou -1 pour toutes) et piste source partagée (synth_route_track_id).",
    tags=["project", "mix"]
)
def set_track_controls(
    app,
    track_id_or_name: str,
    volume: Optional[float] = None,
    pan: Optional[float] = None,
    muted: Optional[bool] = None,
    soloed: Optional[bool] = None,
    armed: Optional[bool] = None,
    monitoring: Optional[bool] = None,
    name: Optional[str] = None,
    color: Optional[str] = None,
    synth_output_bus: Optional[int] = None,
    synth_route_track_id: Optional[str] = None,
) -> Dict[str, Any]:
    target_track = app.project.get_track(track_id_or_name)
    if not target_track:
        raise ValueError(f"Piste '{track_id_or_name}' introuvable.")

    cache_invalidated = False
    if volume is not None:
        target_track.volume = max(0.0, min(1.5, float(volume)))
    if pan is not None:
        target_track.pan = max(-1.0, min(1.0, float(pan)))
    if muted is not None:
        target_track.muted = bool(muted)
    if soloed is not None:
        target_track.soloed = bool(soloed)
    if armed is not None and target_track.track_type != "master":
        target_track.armed = bool(armed)
    if monitoring is not None and target_track.track_type == "audio":
        target_track.monitoring = bool(monitoring)
    if name is not None and name.strip() and target_track.track_type != "master":
        target_track.name = name.strip()
    if color is not None and color.strip() and target_track.track_type != "master":
        old_color = target_track.color
        target_track.color = color.strip()
        for c in getattr(target_track, "clips", []):
            if getattr(c, "color", None) == old_color:
                c.color = target_track.color
    if synth_output_bus is not None and target_track.track_type == "midi":
        target_track.synth_output_bus = None if int(synth_output_bus) < 0 else max(0, min(9, int(synth_output_bus)))
        cache_invalidated = True
    if synth_route_track_id is not None and target_track.track_type == "midi":
        clean_route = synth_route_track_id.strip()
        if not clean_route:
            target_track.synth_route_track_id = None
        else:
            src_track = app.project.get_track(clean_route)
            if not src_track or src_track.id == target_track.id:
                raise ValueError(f"Piste source NovaSynth invalide : '{synth_route_track_id}'.")
            target_track.synth_route_track_id = src_track.id
        cache_invalidated = True

    if cache_invalidated and hasattr(app, "audio_engine") and hasattr(app.audio_engine, "invalidate_track_cache"):
        app.audio_engine.invalidate_track_cache(target_track.id, trigger_async=True)

    if hasattr(app, "set_dirty"):
        app.set_dirty(True)

    if hasattr(app, "refresh_project_ui"):
        app.refresh_project_ui()

    return {
        "status": "success",
        "track_id": target_track.id,
        "track_name": target_track.name,
        "volume": target_track.volume,
        "pan": target_track.pan,
        "muted": target_track.muted,
        "soloed": target_track.soloed,
        "armed": getattr(target_track, "armed", False),
        "monitoring": getattr(target_track, "monitoring", False),
        "color": getattr(target_track, "color", "#38bdf8"),
        "synth_output_bus": getattr(target_track, "synth_output_bus", None),
        "synth_route_track_id": getattr(target_track, "synth_route_track_id", None),
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
    if hasattr(app, "set_dirty"):
        app.set_dirty(False)
    if hasattr(app, "_saved_project_snapshot") and hasattr(app, "_capture_project_snapshot"):
        app._saved_project_snapshot = app._capture_project_snapshot()
    if hasattr(app, "recent_projects_manager"):
        app.recent_projects_manager.add_recent_project(target_path)
    if hasattr(app, "_update_recent_projects_menu"):
        app._update_recent_projects_menu()
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


@action_registry.register(
    name="novadaw_import_audio_file",
    description="Importe un fichier audio de n'importe quel format (WAV, MP3, FLAC, OGG, AIFF, M4A...) sur une piste Audio existante ou en créant une nouvelle piste.",
    tags=["project", "audio", "file"]
)
def import_audio_file_action(
    app,
    file_path: str,
    track_id_or_name: Optional[str] = None,
    start_beat: float = 0.0,
    track_name: Optional[str] = None
) -> Dict[str, Any]:
    from core.audio_importer import load_audio_file
    from core.project import AudioClip

    clean_path = os.path.abspath(os.path.expanduser(file_path.strip()))
    if not os.path.isfile(clean_path):
        raise FileNotFoundError(f"Fichier audio introuvable : '{file_path}'")

    sr = app.audio_engine.sample_rate if hasattr(app, "audio_engine") else 44100
    audio_data, target_sr, dur_sec = load_audio_file(clean_path, target_sr=sr)

    # Résolution ou création de la piste cible
    target_track = None
    if track_id_or_name:
        target_track = app.project.get_track(track_id_or_name)

    if not target_track:
        sel_id = getattr(app, "selected_track_id", None)
        if sel_id:
            cand = app.project.get_track(sel_id)
            if cand and cand.track_type == "audio":
                target_track = cand

    if not target_track:
        # Créer une nouvelle piste Audio
        base_name = track_name or os.path.splitext(os.path.basename(clean_path))[0]
        target_track = Track(
            name=base_name,
            track_type="audio",
            color="#10b981"
        )
        app.project.add_track(target_track)

    # Calcul de la longueur en temps selon le tempo du projet
    bpm = app.project.bpm
    beats = max(1.0, round((dur_sec / 60.0) * bpm, 2))

    clip_name = os.path.splitext(os.path.basename(clean_path))[0]
    new_clip = AudioClip(
        name=clip_name,
        start_beat=max(0.0, float(start_beat)),
        length_beats=beats,
        file_path=clean_path,
        gain=1.0,
        color=target_track.color
    )
    new_clip.audio_data = audio_data
    new_clip.sample_rate = target_sr
    target_track.clips.append(new_clip)

    # Mise à jour de l'interface utilisateur
    if hasattr(app, "set_dirty"):
        app.set_dirty(True)

    if hasattr(app, "refresh_project_ui"):
        app.refresh_project_ui()

    if hasattr(app, "audio_editor"):
        app.audio_editor.open_clip(target_track, new_clip)
        if hasattr(app, "lower_zone"):
            app.lower_zone.setCurrentWidget(app.audio_editor)
            if getattr(app, "is_lower_zone_minimized", False):
                app._expand_lower_zone()

    if hasattr(app, "timeline_grid"):
        app.timeline_grid.update_dimensions()
        app.timeline_grid.update()

    if hasattr(app, "statusBar"):
        app.statusBar().showMessage(f"Fichier audio importé : {clip_name} ({dur_sec:.2f}s)", 3500)

    return {
        "status": "success",
        "file_path": clean_path,
        "track_id": target_track.id,
        "track_name": target_track.name,
        "clip_id": new_clip.id,
        "clip_name": new_clip.name,
        "duration_seconds": round(dur_sec, 3),
        "length_beats": beats,
        "sample_rate": target_sr,
        "channels": 2
    }


@action_registry.register(
    name="novadaw_new_project",
    description="Réinitialise NovaDAW avec un nouveau projet vide sans pistes (sans boîte de dialogue bloquante).",
    tags=["project"]
)
def new_project_action(app) -> Dict[str, Any]:
    if hasattr(app, "new_project"):
        try:
            app.new_project(prompt_save=False)
        except TypeError:
            app.new_project()
    else:
        app.project = Project.create_empty()
        if hasattr(app, "audio_engine") and hasattr(app.audio_engine, "set_project"):
            app.audio_engine.set_project(app.project)
        if hasattr(app, "refresh_project_ui"):
            app.refresh_project_ui()

    return {"status": "success", "message": "Nouveau projet vide initialisé."}


@action_registry.register(
    name="novadaw_load_demo_project",
    description="Charge le projet de démonstration NovaDAW avec synthétiseurs, basse et batterie (sans boîte de dialogue bloquante).",
    tags=["project"]
)
def load_demo_project_action(app) -> Dict[str, Any]:
    if hasattr(app, "load_demo_project"):
        try:
            app.load_demo_project(prompt_save=False)
        except TypeError:
            app.load_demo_project()
    else:
        app.project = Project.create_demo()
        if hasattr(app, "audio_engine") and hasattr(app.audio_engine, "set_project"):
            app.audio_engine.set_project(app.project)
        if hasattr(app, "refresh_project_ui"):
            app.refresh_project_ui()

    return {"status": "success", "message": "Projet de démonstration chargé."}


def _find_clip_and_track(app, clip_id_or_name: Optional[str] = None, track_id_or_name: Optional[str] = None):
    if not getattr(app, "project", None):
        return None, None
    for track in app.project.tracks:
        if track_id_or_name:
            t_needle = str(track_id_or_name).strip().lower()
            if track.id.lower() != t_needle and track.name.lower() != t_needle:
                continue
        for clip in track.clips:
            if clip_id_or_name:
                c_needle = str(clip_id_or_name).strip().lower()
                if clip.id.lower() == c_needle or clip.name.lower() == c_needle:
                    return track, clip
            else:
                return track, clip
    return None, None


@action_registry.register(
    name="novadaw_split_clip",
    description="Scinde un clip audio ou MIDI en deux parties à la position temporelle indiquée (en temps absolus).",
    tags=["clip", "editing"]
)
def split_clip_action(app, track_id_or_name: Optional[str] = None, clip_id_or_name: Optional[str] = None, split_beat: Optional[float] = None) -> Dict[str, Any]:
    track, clip = _find_clip_and_track(app, clip_id_or_name, track_id_or_name)
    if not clip or not track:
        raise ValueError("Aucun clip correspondant trouvé pour la scission.")

    target_beat = float(split_beat) if split_beat is not None else float(getattr(app.audio_engine, "current_beat", 0.0))
    if not (clip.start_beat < target_beat < clip.start_beat + clip.length_beats):
        raise ValueError(f"Le temps de scission ({target_beat}) doit être strictement compris entre le début ({clip.start_beat}) et la fin ({clip.start_beat + clip.length_beats}) du bloc.")

    if hasattr(app, "timeline_grid"):
        p1, p2 = app.timeline_grid.split_clip_at(track, clip, target_beat)
    else:
        bpm = getattr(app.project, "bpm", 120.0)
        p1, p2 = clip.split(target_beat, bpm=bpm) if hasattr(clip, "split") else (None, None)
        if p1 and p2:
            idx = track.clips.index(clip)
            track.clips[idx] = p1
            track.clips.insert(idx + 1, p2)

    return {
        "status": "success",
        "message": f"Clip '{clip.name}' scindé à {target_beat:.2f} temps.",
        "track_id": track.id,
        "part1_id": p1.id if p1 else None,
        "part1_length": p1.length_beats if p1 else None,
        "part2_id": p2.id if p2 else None,
        "part2_length": p2.length_beats if p2 else None,
    }


@action_registry.register(
    name="novadaw_copy_clip",
    description="Copie un clip audio ou MIDI dans le presse-papier de NovaDAW.",
    tags=["clip", "clipboard"]
)
def copy_clip_action(app, clip_id_or_name: Optional[str] = None) -> Dict[str, Any]:
    track, clip = _find_clip_and_track(app, clip_id_or_name)
    if not clip:
        raise ValueError("Aucun clip sélectionné ou trouvé à copier.")

    import copy
    copied = copy.deepcopy(clip)
    if hasattr(app, "timeline_grid"):
        app.timeline_grid._clip_clipboard = copied
    setattr(app, "_global_clip_clipboard", copied)

    return {
        "status": "success",
        "clip_id": clip.id,
        "clip_name": clip.name,
        "message": f"Clip '{clip.name}' copié dans le presse-papier."
    }


@action_registry.register(
    name="novadaw_paste_clip",
    description="Colle le clip du presse-papier à la position temporelle indiquée (ou à la tête de lecture) sur la piste cible.",
    tags=["clip", "clipboard"]
)
def paste_clip_action(app, track_id_or_name: Optional[str] = None, target_beat: Optional[float] = None) -> Dict[str, Any]:
    target_track = None
    if track_id_or_name:
        target_track = app.project.get_track(track_id_or_name)
    if not target_track and hasattr(app, "selected_track_id"):
        target_track = app.project.get_track(app.selected_track_id)
    if not target_track and app.project.tracks:
        target_track = app.project.tracks[0]

    if not target_track:
        raise ValueError("Aucune piste valide trouvée pour coller le bloc.")

    if hasattr(app, "timeline_grid") and hasattr(app.timeline_grid, "paste_clip_at_playhead"):
        if target_beat is not None:
            old_beat = app.timeline_grid.playhead_beat
            app.timeline_grid.playhead_beat = float(target_beat)
            new_clip = app.timeline_grid.paste_clip_at_playhead(target_track=target_track)
            app.timeline_grid.playhead_beat = old_beat
        else:
            new_clip = app.timeline_grid.paste_clip_at_playhead(target_track=target_track)
    else:
        cb = getattr(app, "_global_clip_clipboard", None)
        if not cb:
            raise ValueError("Presse-papier vide.")
        import copy, uuid
        new_clip = copy.deepcopy(cb)
        new_clip.id = str(uuid.uuid4())[:8]
        new_clip.start_beat = float(target_beat) if target_beat is not None else float(getattr(app.audio_engine, "current_beat", 0.0))
        target_track.clips.append(new_clip)

    if not new_clip:
        raise RuntimeError("Échec du collage du clip.")

    return {
        "status": "success",
        "track_id": target_track.id,
        "track_name": target_track.name,
        "clip_id": new_clip.id,
        "clip_name": new_clip.name,
        "start_beat": new_clip.start_beat,
        "length_beats": new_clip.length_beats,
        "message": f"Clip '{new_clip.name}' collé avec succès."
    }


@action_registry.register(
    name="novadaw_set_grid",
    description="Configure la résolution temporelle de la grille musicale (en temps) et l'aimantage (snap).",
    tags=["grid", "editing"]
)
def set_grid_action(app, resolution_beats: float = 1.0, snap_enabled: bool = True) -> Dict[str, Any]:
    res = float(resolution_beats)
    snap = bool(snap_enabled)

    if hasattr(app, "timeline_grid"):
        app.timeline_grid.set_grid_resolution(res)
        app.timeline_grid.set_snap_enabled(snap)

    if hasattr(app, "editing_toolbar"):
        app.editing_toolbar.set_grid_resolution(res)
        app.editing_toolbar.set_snap_enabled(snap)

    if hasattr(app, "ruler"):
        app.ruler.set_grid_resolution(res)
        app.ruler.set_snap_enabled(snap)

    return {
        "status": "success",
        "grid_resolution_beats": res,
        "snap_enabled": snap,
        "message": f"Grille réglée à {res} temps, aimantage {'activé' if snap else 'désactivé'}."
    }


@action_registry.register(
    name="novadaw_set_editing_tool",
    description="Sélectionne l'outil d'édition actif dans la palette ('select', 'split', 'erase', 'draw').",
    tags=["tool", "editing"]
)
def set_editing_tool_action(app, tool_name: str = "select") -> Dict[str, Any]:
    tool = str(tool_name).strip().lower()
    if tool not in ("select", "split", "erase", "draw"):
        raise ValueError(f"Outil invalide '{tool_name}'. Les outils valides sont 'select', 'split', 'erase', 'draw'.")

    if hasattr(app, "editing_toolbar") and hasattr(app.editing_toolbar, "set_active_tool"):
        app.editing_toolbar.set_active_tool(tool)
    elif hasattr(app, "timeline_grid") and hasattr(app.timeline_grid, "set_active_tool"):
        app.timeline_grid.set_active_tool(tool)

    return {
        "status": "success",
        "active_tool": tool,
        "message": f"Outil d'édition '{tool}' activé."
    }


@action_registry.register(
    name="novadaw_set_midi_channel",
    description="Choisit le canal MIDI de sortie d'une piste (1 à 16), indépendamment des sorties audio.",
    tags=["track", "midi"]
)
def set_midi_channel(app, track_id_or_name: str, channel: int) -> Dict[str, Any]:
    track = app.project.get_track(track_id_or_name)
    if track is None or track.track_type != "midi":
        raise ValueError("Sélectionnez une piste MIDI existante.")
    if isinstance(channel, bool) or not isinstance(channel, int) or not 1 <= channel <= 16:
        raise ValueError("Le canal MIDI doit être un entier entre 1 et 16.")
    if track.midi_channel != channel:
        track.midi_channel = channel
        if hasattr(app, "audio_engine"):
            app.audio_engine.invalidate_track_cache(track.id)
        if hasattr(app, "set_dirty"):
            app.set_dirty(True)
        if hasattr(app, "refresh_project_ui"):
            app.refresh_project_ui()
    return {"status": "success", "track_id": track.id, "midi_channel": channel}


@action_registry.register(
    name="novadaw_open_project",
    description="Ouvre et charge un fichier de projet NovaDAW (.ndaw) depuis le disque sans boîte de dialogue bloquante.",
    tags=["project", "file"]
)
def open_project_action(app, file_path: str) -> Dict[str, Any]:
    from core.serializer import load_project

    clean_path = os.path.abspath(os.path.expanduser(file_path.strip()))
    if not os.path.isfile(clean_path):
        raise FileNotFoundError(f"Fichier projet introuvable : '{file_path}'")

    if hasattr(app, "open_project_file"):
        loaded_proj = load_project(clean_path)
        from ui.plugin_dialogs import close_all_native_editors
        from core.plugin_manager import global_plugin_manager
        close_all_native_editors()
        global_plugin_manager.close_all_editors()
        if hasattr(app, "audio_engine") and hasattr(app.audio_engine, "stop"):
            app.audio_engine.stop()
        app.project = loaded_proj
        if hasattr(app, "audio_engine") and hasattr(app.audio_engine, "set_project"):
            app.audio_engine.set_project(app.project)
        app.selected_track_id = None
        if hasattr(app, "_on_seek"):
            app._on_seek(0.0)
        if hasattr(app, "_capture_project_snapshot"):
            app._saved_project_snapshot = app._capture_project_snapshot()
        if hasattr(app, "set_dirty"):
            app.set_dirty(False)
        if hasattr(app, "refresh_project_ui"):
            app.refresh_project_ui()
        if hasattr(app, "recent_projects_manager"):
            app.recent_projects_manager.add_recent_project(clean_path)
        if hasattr(app, "_update_recent_projects_menu"):
            app._update_recent_projects_menu()
    else:
        app.project = load_project(clean_path)
        if hasattr(app, "audio_engine") and hasattr(app.audio_engine, "set_project"):
            app.audio_engine.set_project(app.project)
        if hasattr(app, "refresh_project_ui"):
            app.refresh_project_ui()

    return {
        "status": "success",
        "file_path": clean_path,
        "project_name": app.project.name,
        "bpm": app.project.bpm,
        "tracks_count": len(app.project.tracks),
    }


@action_registry.register(
    name="novadaw_get_recent_projects",
    description="Retourne la liste des projets récents (.ndaw) enregistrés dans NovaDAW.",
    tags=["project", "file"]
)
def get_recent_projects_action(app) -> Dict[str, Any]:
    from core.recent_projects import recent_projects_manager
    mgr = getattr(app, "recent_projects_manager", recent_projects_manager)
    recent = mgr.get_recent_projects() if hasattr(mgr, "get_recent_projects") else []
    return {"status": "success", "count": len(recent), "recent_projects": recent}


@action_registry.register(
    name="novadaw_set_project_info",
    description="Modifie les métadonnées du projet actif : nom du projet et/ou signature rythmique (ex: 4/4, 3/4, 6/8).",
    tags=["project"]
)
def set_project_info_action(
    app,
    name: Optional[str] = None,
    time_sig_num: Optional[int] = None,
    time_sig_den: Optional[int] = None,
) -> Dict[str, Any]:
    if name is not None and name.strip():
        app.project.name = name.strip()
    if time_sig_num is not None:
        num = int(time_sig_num)
        if not 1 <= num <= 32:
            raise ValueError("Le numérateur de signature rythmique doit être compris entre 1 et 32.")
        app.project.time_sig_num = num
    if time_sig_den is not None:
        den = int(time_sig_den)
        if den not in (2, 4, 8, 16):
            raise ValueError("Le dénominateur de signature rythmique doit être 2, 4, 8 ou 16.")
        app.project.time_sig_den = den

    if hasattr(app, "set_dirty"):
        app.set_dirty(True)
    if hasattr(app, "refresh_project_ui"):
        app.refresh_project_ui()

    return {
        "status": "success",
        "project_name": app.project.name,
        "time_signature": f"{app.project.time_sig_num}/{app.project.time_sig_den}",
        "beats_per_bar": app.project.beats_per_bar(),
    }


@action_registry.register(
    name="novadaw_delete_track",
    description="Supprime une piste MIDI ou Audio du projet par son identifiant ou son nom (la piste Master ne peut pas être supprimée).",
    tags=["project", "track"]
)
def delete_track_action(app, track_id_or_name: str) -> Dict[str, Any]:
    track = app.project.get_track(track_id_or_name)
    if not track:
        raise ValueError(f"Piste '{track_id_or_name}' introuvable.")
    if track.track_type == "master":
        raise ValueError("La piste Master ne peut pas être supprimée.")

    removed_id = track.id
    removed_name = track.name
    app.project.remove_track(removed_id)

    engine = getattr(app, "audio_engine", None)
    if engine is not None and hasattr(engine, "invalidate_track_cache"):
        engine.invalidate_track_cache(removed_id)

    if getattr(app, "selected_track_id", None) == removed_id:
        app.selected_track_id = app.project.tracks[0].id if app.project.tracks else None

    if hasattr(app, "set_dirty"):
        app.set_dirty(True)
    if hasattr(app, "refresh_project_ui"):
        app.refresh_project_ui()

    return {
        "status": "success",
        "deleted_track_id": removed_id,
        "deleted_track_name": removed_name,
        "remaining_tracks": len(app.project.tracks),
    }


@action_registry.register(
    name="novadaw_duplicate_track",
    description="Duplique intégralement une piste (clips, notes MIDI, données audio, plugins et courbes d'automatisation).",
    tags=["project", "track"]
)
def duplicate_track_action(
    app,
    track_id_or_name: str,
    new_name: Optional[str] = None,
) -> Dict[str, Any]:
    track = app.project.get_track(track_id_or_name)
    if not track or track.track_type == "master":
        raise ValueError(f"Piste '{track_id_or_name}' introuvable ou non duplicable.")

    dup = app.project.duplicate_track(track.id)
    if not dup:
        raise RuntimeError(f"Échec de la duplication de la piste '{track.name}'.")
    if new_name and new_name.strip():
        dup.name = new_name.strip()

    if hasattr(app, "set_dirty"):
        app.set_dirty(True)
    if hasattr(app, "refresh_project_ui"):
        app.refresh_project_ui()

    return {
        "status": "success",
        "source_track_id": track.id,
        "new_track_id": dup.id,
        "new_track_name": dup.name,
        "tracks_count": len(app.project.tracks),
    }


@action_registry.register(
    name="novadaw_reorder_track",
    description="Déplace une piste à une nouvelle position verticale (new_index >= 0) dans l'arrangement et la console.",
    tags=["project", "track"]
)
def reorder_track_action(app, track_id_or_name: str, new_index: int) -> Dict[str, Any]:
    track = app.project.get_track(track_id_or_name)
    if not track or track.track_type == "master":
        raise ValueError(f"Piste '{track_id_or_name}' introuvable ou non déplaçable.")

    old_idx = app.project.tracks.index(track)
    target_idx = max(0, min(len(app.project.tracks) - 1, int(new_index)))
    app.project.move_track(old_idx, target_idx)

    if hasattr(app, "set_dirty"):
        app.set_dirty(True)
    if hasattr(app, "refresh_project_ui"):
        app.refresh_project_ui()

    return {
        "status": "success",
        "track_id": track.id,
        "track_name": track.name,
        "old_index": old_idx,
        "new_index": app.project.tracks.index(track),
    }


@action_registry.register(
    name="novadaw_list_clips",
    description="Liste tous les clips Audio et/ou MIDI du projet (ou d'une piste précise) avec leurs métadonnées détaillées (gain, offset source, nombre de notes, chemin fichier).",
    tags=["clip", "project"]
)
def list_clips_action(
    app,
    track_id_or_name: Optional[str] = None,
    clip_type: Optional[str] = None,
) -> Dict[str, Any]:
    type_filter = clip_type.strip().lower() if clip_type else None
    items = []
    for track in app.project.tracks:
        if track_id_or_name:
            needle = track_id_or_name.strip().lower()
            if track.id.lower() != needle and track.name.lower() != needle:
                continue
        for clip in track.clips:
            c_type = "midi" if hasattr(clip, "notes") else "audio"
            if type_filter and c_type != type_filter:
                continue
            entry: Dict[str, Any] = {
                "track_id": track.id,
                "track_name": track.name,
                "clip_id": clip.id,
                "clip_name": clip.name,
                "type": c_type,
                "start_beat": clip.start_beat,
                "length_beats": clip.length_beats,
                "color": getattr(clip, "color", track.color),
            }
            if c_type == "midi":
                entry["notes_count"] = len(clip.notes)
            else:
                entry["file_path"] = getattr(clip, "file_path", None)
                entry["gain"] = getattr(clip, "gain", 1.0)
                entry["source_offset_beats"] = getattr(clip, "source_offset_beats", 0.0)
                entry["sample_rate"] = getattr(clip, "sample_rate", 44100)
            items.append(entry)
    return {"status": "success", "count": len(items), "clips": items}


@action_registry.register(
    name="novadaw_update_clip",
    description="Déplace, renomme, recolore, redimensionne/rogne (start_beat, length_beats, source_offset_beats) ou ajuste le gain (0.0..2.0) d'un clip Audio ou MIDI.",
    tags=["clip", "editing"]
)
def update_clip_action(
    app,
    clip_id_or_name: str,
    track_id_or_name: Optional[str] = None,
    name: Optional[str] = None,
    start_beat: Optional[float] = None,
    length_beats: Optional[float] = None,
    source_offset_beats: Optional[float] = None,
    gain: Optional[float] = None,
    color: Optional[str] = None,
) -> Dict[str, Any]:
    track, clip = _find_clip_and_track(app, clip_id_or_name, track_id_or_name)
    if not clip or not track:
        raise ValueError(f"Clip '{clip_id_or_name}' introuvable.")

    is_audio = not hasattr(clip, "notes")
    bpm = float(getattr(app.project, "bpm", 120.0))

    if name is not None and name.strip():
        clip.name = name.strip()
    if color is not None and color.strip():
        clip.color = color.strip()
    if start_beat is not None:
        clip.start_beat = max(0.0, float(start_beat))
    if is_audio and source_offset_beats is not None:
        clip.source_offset_beats = max(0.0, float(source_offset_beats))
    if length_beats is not None:
        req_len = max(0.0625, float(length_beats))
        if is_audio and hasattr(clip, "get_max_allowed_length_beats"):
            max_len = clip.get_max_allowed_length_beats(bpm)
            if max_len is not None and max_len > 0:
                req_len = min(req_len, float(max_len))
        clip.length_beats = req_len
    if is_audio and gain is not None:
        clip.gain = max(0.0, min(2.0, float(gain)))

    engine = getattr(app, "audio_engine", None)
    if engine is not None and hasattr(engine, "invalidate_track_cache"):
        engine.invalidate_track_cache(track.id, trigger_async=True)

    if hasattr(app, "set_dirty"):
        app.set_dirty(True)
    if hasattr(app, "timeline_grid"):
        app.timeline_grid.update_dimensions()
        app.timeline_grid.update()

    return {
        "status": "success",
        "track_id": track.id,
        "clip_id": clip.id,
        "clip_name": clip.name,
        "start_beat": clip.start_beat,
        "length_beats": clip.length_beats,
        "source_offset_beats": getattr(clip, "source_offset_beats", 0.0) if is_audio else None,
        "gain": getattr(clip, "gain", 1.0) if is_audio else None,
    }


@action_registry.register(
    name="novadaw_delete_clip",
    description="Supprime un clip Audio ou MIDI de sa piste par son identifiant ou son nom.",
    tags=["clip", "editing"]
)
def delete_clip_action(
    app,
    clip_id_or_name: str,
    track_id_or_name: Optional[str] = None,
) -> Dict[str, Any]:
    track, clip = _find_clip_and_track(app, clip_id_or_name, track_id_or_name)
    if not clip or not track:
        raise ValueError(f"Clip '{clip_id_or_name}' introuvable.")

    track.clips = [c for c in track.clips if c.id != clip.id]

    if hasattr(app, "piano_roll") and getattr(app.piano_roll, "current_clip", None) is clip:
        if hasattr(app.piano_roll, "clear"):
            app.piano_roll.clear()
    if hasattr(app, "audio_editor") and getattr(app.audio_editor, "current_clip", None) is clip:
        if hasattr(app.audio_editor, "clear"):
            app.audio_editor.clear()

    engine = getattr(app, "audio_engine", None)
    if engine is not None and hasattr(engine, "invalidate_track_cache"):
        engine.invalidate_track_cache(track.id, trigger_async=True)

    if hasattr(app, "set_dirty"):
        app.set_dirty(True)
    if hasattr(app, "refresh_project_ui"):
        app.refresh_project_ui()

    return {
        "status": "success",
        "track_id": track.id,
        "deleted_clip_id": clip.id,
        "deleted_clip_name": clip.name,
        "remaining_clips": len(track.clips),
    }


@action_registry.register(
    name="novadaw_duplicate_clip",
    description="Duplique un clip Audio ou MIDI immédiatement à sa suite (ou à la position temporelle target_beat).",
    tags=["clip", "editing"]
)
def duplicate_clip_action(
    app,
    clip_id_or_name: str,
    track_id_or_name: Optional[str] = None,
    target_beat: Optional[float] = None,
) -> Dict[str, Any]:
    import copy
    import uuid

    track, clip = _find_clip_and_track(app, clip_id_or_name, track_id_or_name)
    if not clip or not track:
        raise ValueError(f"Clip '{clip_id_or_name}' introuvable.")

    new_clip = copy.deepcopy(clip)
    new_clip.id = str(uuid.uuid4())[:8]
    new_clip.start_beat = (
        max(0.0, float(target_beat))
        if target_beat is not None
        else float(clip.start_beat + clip.length_beats)
    )
    track.clips.append(new_clip)

    engine = getattr(app, "audio_engine", None)
    if engine is not None and hasattr(engine, "invalidate_track_cache"):
        engine.invalidate_track_cache(track.id, trigger_async=True)

    if hasattr(app, "set_dirty"):
        app.set_dirty(True)
    if hasattr(app, "timeline_grid"):
        app.timeline_grid.update_dimensions()
        app.timeline_grid.update()

    return {
        "status": "success",
        "track_id": track.id,
        "source_clip_id": clip.id,
        "new_clip_id": new_clip.id,
        "new_clip_name": new_clip.name,
        "start_beat": new_clip.start_beat,
        "length_beats": new_clip.length_beats,
    }
