"""
core/actions/synth.py - Actions de gestion et de contrôle du synthétiseur NovaSynth pour NovaDAW et le protocole MCP.
"""
from typing import Dict, Any, List, Optional
from core.action_registry import action_registry
from plugins.registry import plugin_registry, ensure_plugins_loaded


def _notify_synth_change(app, track, synth) -> None:
    """Invalide le cache ASIO-Guard (y compris pistes routées), marque le projet modifié et rafraîchit l'UI + éditeur."""
    engine = getattr(app, "audio_engine", None)
    if engine is not None and track is not None:
        if hasattr(engine, "invalidate_track_cache"):
            engine.invalidate_track_cache(track.id, trigger_async=True)
            for t in getattr(getattr(app, "project", None), "tracks", []):
                if getattr(t, "synth_route_track_id", None) == track.id:
                    engine.invalidate_track_cache(t.id, trigger_async=True)
    if hasattr(app, "set_dirty"):
        app.set_dirty(True)
    if hasattr(app, "refresh_project_ui"):
        app.refresh_project_ui()
    try:
        from ui.plugin_dialogs import sync_open_native_editor
        sync_open_native_editor(synth)
    except Exception:
        pass


def _list_all_synth_preset_names(synth) -> List[str]:
    names = list(synth.get_factory_presets().keys()) if hasattr(synth, "get_factory_presets") else []
    seen = {n.lower() for n in names}
    try:
        from core.preset_manager import global_preset_manager
        for p in global_preset_manager.list_presets("novadaw.synth", synth):
            p_name = p.get("name")
            if p_name and p_name.lower() not in seen:
                names.append(p_name)
                seen.add(p_name.lower())
    except Exception:
        pass
    return names


def _get_or_create_synth_plugin(app, track_id_or_name: str, plugin_instance_id: Optional[str] = None):
    """Trouve ou instancie l'instrument NovaSynth sur la piste demandée"""
    ensure_plugins_loaded()
    track = app.project.get_track(track_id_or_name)
    if not track:
        raise ValueError(f"Piste '{track_id_or_name}' introuvable.")

    synth_plugin = None
    if plugin_instance_id:
        cand = track.get_plugin(plugin_instance_id)
        if cand and getattr(cand, "plugin_type_id", None) == "novadaw.synth":
            synth_plugin = cand

    if not synth_plugin:
        for p in track.plugins:
            if getattr(p, "plugin_type_id", None) == "novadaw.synth":
                synth_plugin = p
                break

    if not synth_plugin:
        synth_plugin = plugin_registry.create_plugin("novadaw.synth")
        if synth_plugin:
            track.plugins.insert(0, synth_plugin)

    if not synth_plugin:
        raise RuntimeError("Impossible d'instancier l'instrument NovaSynth.")

    if track.track_type == "midi":
        track.plugin_path = "novadaw.synth"
        track.plugin_name = "NovaSynth"
        if hasattr(app.project, "add_rack_plugin"):
            app.project.add_rack_plugin("novadaw.synth", "NovaSynth", "instrument")

    return track, synth_plugin


@action_registry.register(
    name="novadaw_configure_synth",
    description="Configure les paramètres généraux ou d'une couche sonore du synthétiseur NovaSynth (presets, volume master, glide, delay avec ping_pong, reverb avec damping/width, ou par couche: waveform, octave, demi-tons, fine_tune, pulse_width, fm_ratio/fm_depth, unison_detune/spread, volume, pan, mute/solo/enabled, sortie stéréo 0-9, ADSR amp et ADSR filtre, filtre multimode cutoff/res/drive, LFO complet).",
    tags=["plugins", "synth", "instrument"]
)
def configure_synth(
    app,
    track_id_or_name: str,
    preset: Optional[str] = None,
    master_volume: Optional[float] = None,
    glide_time: Optional[float] = None,
    delay_enabled: Optional[bool] = None,
    delay_time: Optional[float] = None,
    delay_feedback: Optional[float] = None,
    delay_ping_pong: Optional[bool] = None,
    delay_mix: Optional[float] = None,
    reverb_enabled: Optional[bool] = None,
    reverb_room_size: Optional[float] = None,
    reverb_damping: Optional[float] = None,
    reverb_width: Optional[float] = None,
    reverb_mix: Optional[float] = None,
    layer_index: Optional[int] = None,
    layer_name: Optional[str] = None,
    waveform: Optional[str] = None,
    octave: Optional[int] = None,
    semitone: Optional[int] = None,
    fine_tune: Optional[float] = None,
    pulse_width: Optional[float] = None,
    fm_ratio: Optional[float] = None,
    fm_depth: Optional[float] = None,
    unison_detune: Optional[float] = None,
    unison_spread: Optional[float] = None,
    volume: Optional[float] = None,
    pan: Optional[float] = None,
    layer_enabled: Optional[bool] = None,
    layer_muted: Optional[bool] = None,
    layer_soloed: Optional[bool] = None,
    output_bus: Optional[int] = None,
    attack: Optional[float] = None,
    decay: Optional[float] = None,
    sustain: Optional[float] = None,
    release: Optional[float] = None,
    filter_type: Optional[str] = None,
    cutoff: Optional[float] = None,
    resonance: Optional[float] = None,
    drive: Optional[float] = None,
    filter_env_amount: Optional[float] = None,
    filter_attack: Optional[float] = None,
    filter_decay: Optional[float] = None,
    filter_sustain: Optional[float] = None,
    filter_release: Optional[float] = None,
    lfo_enabled: Optional[bool] = None,
    lfo_waveform: Optional[str] = None,
    lfo_rate: Optional[float] = None,
    lfo_depth: Optional[float] = None,
    lfo_target: Optional[str] = None,
    enabled: Optional[bool] = None,
    plugin_instance_id: Optional[str] = None,
) -> Dict[str, Any]:
    track, synth = _get_or_create_synth_plugin(app, track_id_or_name, plugin_instance_id=plugin_instance_id)

    # 1. Preset global
    if preset:
        if not synth.apply_preset(preset):
            avail = _list_all_synth_preset_names(synth)
            return {
                "status": "error",
                "message": f"Preset '{preset}' introuvable. Presets disponibles : {avail}",
                "available_presets": avail,
            }

    if enabled is not None:
        synth.enabled = bool(enabled)

    # 2. Master section
    if master_volume is not None:
        synth.master_volume = max(0.0, min(2.0, float(master_volume)))
    if glide_time is not None:
        synth.glide_time = max(0.0, min(1.0, float(glide_time)))

    # 3. Master Delay
    if delay_enabled is not None:
        synth.delay.enabled = bool(delay_enabled)
    if delay_time is not None:
        synth.delay.time_sec = max(0.01, min(2.0, float(delay_time)))
    if delay_feedback is not None:
        synth.delay.feedback = max(0.0, min(0.95, float(delay_feedback)))
    if delay_ping_pong is not None:
        synth.delay.ping_pong = bool(delay_ping_pong)
    if delay_mix is not None:
        synth.delay.mix = max(0.0, min(1.0, float(delay_mix)))

    # 4. Master Reverb
    if reverb_enabled is not None:
        synth.reverb.enabled = bool(reverb_enabled)
    if reverb_room_size is not None:
        synth.reverb.room_size = max(0.0, min(1.0, float(reverb_room_size)))
    if reverb_damping is not None:
        synth.reverb.damping = max(0.0, min(1.0, float(reverb_damping)))
    if reverb_width is not None:
        synth.reverb.width = max(0.0, min(1.0, float(reverb_width)))
    if reverb_mix is not None:
        synth.reverb.mix = max(0.0, min(1.0, float(reverb_mix)))

    # 5. Réglages par Couche Sonore (Layer)
    layer_param_values = [
        waveform, octave, semitone, fine_tune, pulse_width, fm_ratio, fm_depth,
        unison_detune, unison_spread, volume, pan, layer_enabled, layer_muted,
        layer_soloed, output_bus, attack, decay, sustain, release, filter_type,
        cutoff, resonance, drive, filter_env_amount, filter_attack, filter_decay,
        filter_sustain, filter_release, lfo_enabled, lfo_waveform, lfo_rate,
        lfo_depth, lfo_target,
    ]
    target_layer = None
    if layer_index is not None:
        target_layer = synth.get_layer(layer_index)
    elif layer_name is not None:
        target_layer = synth.get_layer(layer_name)
    elif any(v is not None for v in layer_param_values):
        target_layer = synth.layers[0] if synth.layers else None

    if target_layer:
        if waveform:
            target_layer.waveform = waveform.lower().strip()
        if octave is not None:
            target_layer.octave = max(-3, min(3, int(octave)))
        if semitone is not None:
            target_layer.semitone = max(-12, min(12, int(semitone)))
        if fine_tune is not None:
            target_layer.fine_tune = max(-100.0, min(100.0, float(fine_tune)))
        if pulse_width is not None:
            target_layer.pulse_width = max(0.05, min(0.95, float(pulse_width)))
        if fm_ratio is not None:
            target_layer.fm_ratio = max(0.25, min(16.0, float(fm_ratio)))
        if fm_depth is not None:
            target_layer.fm_depth = max(0.0, min(10.0, float(fm_depth)))
        if unison_detune is not None:
            target_layer.unison_detune = max(0.0, min(1.0, float(unison_detune)))
        if unison_spread is not None:
            target_layer.unison_spread = max(0.0, min(1.0, float(unison_spread)))
        if volume is not None:
            target_layer.volume = max(0.0, min(2.0, float(volume)))
        if pan is not None:
            target_layer.pan = max(-1.0, min(1.0, float(pan)))
        if layer_enabled is not None:
            target_layer.enabled = bool(layer_enabled)
        if layer_muted is not None:
            target_layer.muted = bool(layer_muted)
        if layer_soloed is not None:
            target_layer.soloed = bool(layer_soloed)
        if output_bus is not None:
            target_layer.output_bus = max(0, min(9, int(output_bus)))

        # ADSR Amplitude
        if attack is not None:
            target_layer.attack = max(0.001, min(5.0, float(attack)))
        if decay is not None:
            target_layer.decay = max(0.001, min(5.0, float(decay)))
        if sustain is not None:
            target_layer.sustain = max(0.0, min(1.0, float(sustain)))
        if release is not None:
            target_layer.release = max(0.001, min(8.0, float(release)))

        # Filtre & ADSR Filtre
        if filter_type:
            target_layer.filter_type = filter_type.lower().strip()
        if cutoff is not None:
            target_layer.cutoff = max(20.0, min(20000.0, float(cutoff)))
        if resonance is not None:
            target_layer.resonance = max(0.1, min(10.0, float(resonance)))
        if drive is not None:
            target_layer.drive = max(0.0, min(3.0, float(drive)))
        if filter_env_amount is not None:
            target_layer.filter_env_amount = max(-1.0, min(1.0, float(filter_env_amount)))
        if filter_attack is not None:
            target_layer.filter_attack = max(0.001, min(5.0, float(filter_attack)))
        if filter_decay is not None:
            target_layer.filter_decay = max(0.001, min(5.0, float(filter_decay)))
        if filter_sustain is not None:
            target_layer.filter_sustain = max(0.0, min(1.0, float(filter_sustain)))
        if filter_release is not None:
            target_layer.filter_release = max(0.001, min(8.0, float(filter_release)))

        # LFO
        if lfo_enabled is not None:
            target_layer.lfo_enabled = bool(lfo_enabled)
        if lfo_waveform:
            target_layer.lfo_waveform = lfo_waveform.lower().strip()
        if lfo_rate is not None:
            target_layer.lfo_rate = max(0.05, min(30.0, float(lfo_rate)))
        if lfo_depth is not None:
            target_layer.lfo_depth = max(0.0, min(1.0, float(lfo_depth)))
        if lfo_target:
            target_layer.lfo_target = lfo_target.lower().strip()

    _notify_synth_change(app, track, synth)

    return {
        "status": "success",
        "track_id": track.id,
        "track_name": track.name,
        "synth_name": synth.name,
        "enabled": synth.enabled,
        "preset_name": synth.preset_name,
        "total_layers": len(synth.layers),
        "state": synth.get_state()
    }


@action_registry.register(
    name="novadaw_add_synth_layer",
    description="Ajoute une nouvelle couche sonore à empiler sur le synthétiseur NovaSynth (waveform: saw, square, sine, triangle, noise, fm, supersaw, octave, volume, pan, output_bus: 0-9).",
    tags=["plugins", "synth", "layer"]
)
def add_synth_layer(
    app,
    track_id_or_name: str,
    name: str = "New Layer",
    waveform: str = "saw",
    octave: int = 0,
    volume: float = 0.8,
    output_bus: int = 0,
    pan: float = 0.0,
) -> Dict[str, Any]:
    track, synth = _get_or_create_synth_plugin(app, track_id_or_name)
    layer = synth.add_layer(
        name=name,
        waveform=waveform,
        octave=octave,
        volume=volume,
        output_bus=output_bus
    )
    if pan:
        layer.pan = max(-1.0, min(1.0, float(pan)))
    _notify_synth_change(app, track, synth)

    return {
        "status": "success",
        "track_id": track.id,
        "layer_id": layer.layer_id,
        "layer_name": layer.name,
        "waveform": layer.waveform,
        "octave": layer.octave,
        "pan": layer.pan,
        "output_bus": layer.output_bus,
        "total_layers": len(synth.layers)
    }


@action_registry.register(
    name="novadaw_duplicate_synth_layer",
    description="Duplique une couche sonore existante de NovaSynth par son index (0, 1...) ou son identifiant.",
    tags=["plugins", "synth", "layer"]
)
def duplicate_synth_layer(
    app,
    track_id_or_name: str,
    layer_index_or_id: Any = 0
) -> Dict[str, Any]:
    track, synth = _get_or_create_synth_plugin(app, track_id_or_name)
    src = synth.get_layer(layer_index_or_id)
    if not src:
        return {"status": "error", "message": f"Couche '{layer_index_or_id}' introuvable."}
    dup = synth.duplicate_layer(src.layer_id)
    if not dup:
        return {"status": "error", "message": "Impossible de dupliquer la couche (limite atteinte)."}

    _notify_synth_change(app, track, synth)
    return {
        "status": "success",
        "track_id": track.id,
        "layer_id": dup.layer_id,
        "layer_name": dup.name,
        "total_layers": len(synth.layers)
    }


@action_registry.register(
    name="novadaw_remove_synth_layer",
    description="Supprime une couche sonore de NovaSynth par son index (0, 1...) ou son identifiant.",
    tags=["plugins", "synth", "layer"]
)
def remove_synth_layer(
    app,
    track_id_or_name: str,
    layer_index_or_id: Any
) -> Dict[str, Any]:
    track, synth = _get_or_create_synth_plugin(app, track_id_or_name)
    success = synth.remove_layer(layer_index_or_id)
    if not success:
        return {
            "status": "error",
            "message": f"Impossible de supprimer la couche '{layer_index_or_id}'. Le synthé doit conserver au moins une couche active."
        }

    _notify_synth_change(app, track, synth)

    return {
        "status": "success",
        "track_id": track.id,
        "removed": str(layer_index_or_id),
        "total_layers": len(synth.layers)
    }


@action_registry.register(
    name="novadaw_set_synth_preset",
    description="Applique un preset d'usine ou utilisateur sur NovaSynth parmi les 34 presets d'usine (Leads, Pads, Harpes & Plucks, Violons & Cordes, Percussions & Impacts, Basses & Reeses, Claviers) et presets utilisateur.",
    tags=["plugins", "synth", "preset"]
)
def set_synth_preset(
    app,
    track_id_or_name: str,
    preset_name: str
) -> Dict[str, Any]:
    track, synth = _get_or_create_synth_plugin(app, track_id_or_name)
    success = synth.apply_preset(preset_name)
    if not success:
        avail = _list_all_synth_preset_names(synth)
        return {
            "status": "error",
            "message": f"Preset '{preset_name}' introuvable. Presets disponibles : {avail}",
            "available_presets": avail,
        }

    _notify_synth_change(app, track, synth)

    return {
        "status": "success",
        "track_id": track.id,
        "preset_name": synth.preset_name,
        "total_layers": len(synth.layers),
        "state": synth.get_state()
    }


@action_registry.register(
    name="novadaw_get_synth_state",
    description="Retourne l'état complet du synthétiseur NovaSynth d'une piste (couches empilées, formes d'ondes, filtres, enveloppes, LFO, sorties stéréo assignées, presets disponibles et effets).",
    tags=["plugins", "synth", "inspect"]
)
def get_synth_state(
    app,
    track_id_or_name: str
) -> Dict[str, Any]:
    track, synth = _get_or_create_synth_plugin(app, track_id_or_name)
    return {
        "status": "success",
        "track_id": track.id,
        "track_name": track.name,
        "synth_name": synth.name,
        "enabled": synth.enabled,
        "preset_name": synth.preset_name,
        "master_volume": synth.master_volume,
        "glide_time": synth.glide_time,
        "total_layers": len(synth.layers),
        "available_presets": _list_all_synth_preset_names(synth),
        "state": synth.get_state()
    }
