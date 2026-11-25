"""
core/actions/automation.py - Actions de gestion des pistes et courbes d'automatisation pour NovaDAW et MCP.
"""
import math
from typing import Dict, Any, List, Optional
from core.action_registry import action_registry
from core.project import AUTOMATION_PARAM_SPECS


def _notify_automation_change(app, track) -> None:
    if hasattr(app, "set_dirty"):
        app.set_dirty(True)
    if hasattr(app, "refresh_project_ui"):
        app.refresh_project_ui()
    if hasattr(app, "timeline_grid"):
        app.timeline_grid.update_dimensions()
        app.timeline_grid.update()


@action_registry.register(
    name="novadaw_get_track_automation",
    description="Inspecte les pistes d'automatisation ('volume', 'pan') d'une piste avec tous leurs points (beat, value, curve_type, curve_tension) et leur mode ('read', 'write').",
    tags=["automation", "track"]
)
def get_track_automation(
    app,
    track_id_or_name: str,
    param_name: Optional[str] = None,
) -> Dict[str, Any]:
    track = app.project.get_track(track_id_or_name)
    if not track:
        raise ValueError(f"Piste '{track_id_or_name}' introuvable.")

    filter_param = param_name.strip().lower() if param_name else None
    lanes_out = []
    for lane in getattr(track, "automation_lanes", []):
        if filter_param and lane.target_param.lower() != filter_param:
            continue
        min_v, max_v = lane.get_bounds()
        lanes_out.append({
            "lane_id": lane.id,
            "target_param": lane.target_param,
            "name": lane.name,
            "enabled": lane.enabled,
            "visible": lane.visible,
            "mode": lane.mode,
            "min_val": min_v,
            "max_val": max_v,
            "points_count": len(lane.points),
            "points": [p.to_dict() for p in lane.points],
        })

    return {
        "status": "success",
        "track_id": track.id,
        "track_name": track.name,
        "automation_expanded": bool(getattr(track, "automation_expanded", True)),
        "supported_params": list(AUTOMATION_PARAM_SPECS.keys()),
        "lanes": lanes_out,
    }


@action_registry.register(
    name="novadaw_set_automation_lane",
    description="Crée ou configure une piste d'automatisation ('volume' 0.0..1.5 ou 'pan' -1.0..1.0) sur une piste et y écrit des points [{'beat': 0.0, 'value': 0.8, 'curve_type': 'linear'|'curve'|'step', 'curve_tension': 0.0}].",
    tags=["automation", "track"]
)
def set_automation_lane(
    app,
    track_id_or_name: str,
    param_name: str = "volume",
    points: Optional[List[Dict[str, Any]]] = None,
    mode: Optional[str] = None,
    enabled: Optional[bool] = None,
    visible: Optional[bool] = None,
    clear_existing: bool = False,
) -> Dict[str, Any]:
    track = app.project.get_track(track_id_or_name)
    if not track or track.track_type == "master":
        raise ValueError("Sélectionnez une piste Audio ou MIDI existante (hors Master) pour l'automatisation.")

    p_clean = param_name.strip().lower()
    if p_clean not in AUTOMATION_PARAM_SPECS:
        raise ValueError(
            f"Paramètre d'automatisation non supporté : '{param_name}'. Paramètres valides : {list(AUTOMATION_PARAM_SPECS.keys())}"
        )

    seed = points is None and not clear_existing
    lane = track.add_automation_lane(p_clean, seed_initial_point=seed)

    if mode is not None:
        m_clean = mode.strip().lower()
        if m_clean not in ("read", "write", "touch", "latch"):
            raise ValueError(f"Mode d'automatisation invalide : '{mode}' (attendus: read, write, touch, latch).")
        lane.mode = m_clean

    if enabled is not None:
        lane.enabled = bool(enabled)
    if visible is not None:
        lane.visible = bool(visible)
        if lane.visible:
            track.automation_expanded = True

    if clear_existing:
        lane.points.clear()

    if points:
        for pt in points:
            beat = float(pt.get("beat", 0.0))
            val = float(pt.get("value", AUTOMATION_PARAM_SPECS[p_clean]["default_val"]))
            if not math.isfinite(beat) or not math.isfinite(val):
                raise ValueError("Les coordonnées 'beat' et 'value' d'un point d'automatisation doivent être finies.")
            if p_clean == "volume":
                val = max(0.0, min(1.5, val))
            c_type = str(pt.get("curve_type", "linear")).strip().lower()
            if c_type not in ("linear", "curve", "step"):
                c_type = "linear"
            c_tension = max(-1.0, min(1.0, float(pt.get("curve_tension", 0.0))))
            lane.add_point(beat=max(0.0, beat), value=val, curve_type=c_type, curve_tension=c_tension)

    _notify_automation_change(app, track)

    return {
        "status": "success",
        "track_id": track.id,
        "track_name": track.name,
        "lane_id": lane.id,
        "target_param": lane.target_param,
        "enabled": lane.enabled,
        "visible": lane.visible,
        "mode": lane.mode,
        "points_count": len(lane.points),
        "points": [p.to_dict() for p in lane.points],
    }


@action_registry.register(
    name="novadaw_clear_automation_lane",
    description="Vide tous les points d'une piste d'automatisation ('volume' ou 'pan') ou supprime complètement la piste d'automatisation si remove_lane=True.",
    tags=["automation", "track"]
)
def clear_automation_lane(
    app,
    track_id_or_name: str,
    param_name: str = "volume",
    remove_lane: bool = False,
) -> Dict[str, Any]:
    track = app.project.get_track(track_id_or_name)
    if not track:
        raise ValueError(f"Piste '{track_id_or_name}' introuvable.")

    p_clean = param_name.strip().lower()
    lane = track.get_automation_lane(p_clean)
    if not lane:
        return {
            "status": "success",
            "track_id": track.id,
            "cleared_points": 0,
            "removed_lane": False,
            "message": f"Aucune piste d'automatisation '{p_clean}' présente sur '{track.name}'.",
        }

    cleared_count = len(lane.points)
    if remove_lane:
        track.remove_automation_lane(lane.id)
    else:
        lane.points.clear()

    _notify_automation_change(app, track)

    return {
        "status": "success",
        "track_id": track.id,
        "target_param": p_clean,
        "cleared_points": cleared_count,
        "removed_lane": bool(remove_lane),
    }
