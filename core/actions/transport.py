"""
core/actions/transport.py - Actions de contrôle de transport et de boucle pour NovaDAW
"""
from typing import Dict, Any, Optional
from core.action_registry import action_registry


@action_registry.register(
    name="novadaw_set_loop_region",
    description="Définit l'intervalle et l'emplacement de la région de boucle (début et fin en temps/beats, 4 temps = 1 mesure en 4/4) et active/désactive la lecture en boucle.",
    tags=["transport", "loop"]
)
def set_loop_region(app, start_beat: float, end_beat: float, enabled: bool = True) -> Dict[str, Any]:
    start = max(0.0, float(start_beat))
    end = max(start + 0.25, float(end_beat))

    app.project.loop_start_beat = start
    app.project.loop_end_beat = end
    app.project.loop_enabled = enabled

    # Mise à jour visuelle sur l'interface Qt
    if hasattr(app, "ruler"):
        app.ruler.set_loop(enabled, start, end)
    if hasattr(app, "transport_bar"):
        app.transport_bar.btn_loop.setChecked(enabled)
    if hasattr(app, "timeline_grid"):
        app.timeline_grid.update()

    return {
        "status": "success",
        "loop_enabled": enabled,
        "loop_start_beat": start,
        "loop_end_beat": end,
        "length_beats": end - start,
        "bars": (end - start) / app.project.beats_per_bar(),
    }


@action_registry.register(
    name="novadaw_adjust_loop_bounds",
    description="Ajuste la position ou la taille de la boucle actuelle en déplaçant ou redimensionnant les marqueurs (valeurs positives ou négatives en temps).",
    tags=["transport", "loop"]
)
def adjust_loop_bounds(app, shift_beats: float = 0.0, length_delta_beats: float = 0.0) -> Dict[str, Any]:
    current_start = app.project.loop_start_beat
    current_end = app.project.loop_end_beat
    current_len = max(0.25, current_end - current_start)

    new_len = max(0.25, current_len + float(length_delta_beats))
    new_start = max(0.0, current_start + float(shift_beats))
    new_end = new_start + new_len

    return set_loop_region(app, start_beat=new_start, end_beat=new_end, enabled=app.project.loop_enabled)


@action_registry.register(
    name="novadaw_get_transport_state",
    description="Récupère l'état courant de lecture, la position de la tête de lecture (en temps et mesures), le tempo (BPM) et les marqueurs de boucle.",
    tags=["transport"]
)
def get_transport_state(app) -> Dict[str, Any]:
    current_beat = app.audio_engine.current_beat if hasattr(app, "audio_engine") else 0.0
    is_playing = app.audio_engine.is_playing if hasattr(app, "audio_engine") else False
    beats_per_bar = app.project.beats_per_bar()

    return {
        "is_playing": is_playing,
        "current_beat": round(current_beat, 3),
        "current_bar": int(current_beat // beats_per_bar) + 1,
        "current_beat_in_bar": round((current_beat % beats_per_bar) + 1, 2),
        "bpm": app.project.bpm,
        "loop_enabled": app.project.loop_enabled,
        "loop_start_beat": app.project.loop_start_beat,
        "loop_end_beat": app.project.loop_end_beat,
        "loop_length_beats": app.project.loop_end_beat - app.project.loop_start_beat,
    }


@action_registry.register(
    name="novadaw_control_transport",
    description="Pilote la barre de transport : 'play' (lecture), 'pause', 'stop' (retour début boucle ou 0), 'seek' (déplacer curseur à target_beat), 'goto_start', 'goto_end'.",
    tags=["transport"]
)
def control_transport(app, action: str, target_beat: Optional[float] = None) -> Dict[str, Any]:
    act = action.strip().lower()

    if act == "play":
        app.audio_engine.play()
        if hasattr(app, "transport_bar"):
            app.transport_bar.set_playing_state(True)
    elif act == "pause":
        app.audio_engine.pause()
        if hasattr(app, "transport_bar"):
            app.transport_bar.set_playing_state(False)
    elif act == "stop":
        app._on_stop()
    elif act == "seek":
        if target_beat is not None:
            app._on_seek(max(0.0, float(target_beat)))
    elif act == "goto_start":
        app._on_goto_start()
    elif act == "goto_end":
        app._on_goto_end()
    else:
        raise ValueError(f"Action de transport non reconnue : '{action}' (attendues: play, pause, stop, seek, goto_start, goto_end)")

    return get_transport_state(app)


@action_registry.register(
    name="novadaw_set_bpm",
    description="Modifie le tempo (BPM) du projet en direct.",
    tags=["transport"]
)
def set_bpm(app, bpm: float) -> Dict[str, Any]:
    val = max(20.0, min(300.0, float(bpm)))
    app.project.bpm = val
    if hasattr(app, "transport_bar"):
        app.transport_bar.spin_bpm.setValue(val)
    return {"status": "success", "bpm": val}
