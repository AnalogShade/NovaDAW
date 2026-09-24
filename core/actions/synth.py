"""
core/actions/synth.py - Actions de gestion et de contrôle du synthétiseur NovaSynth pour NovaDAW et le protocole MCP.
"""
from typing import Dict, Any, List, Optional
from core.action_registry import action_registry
from plugins.registry import plugin_registry, ensure_plugins_loaded


def _get_or_create_synth_plugin(app, track_id_or_name: str):
    """Trouve ou instancie l'instrument NovaSynth sur la piste demandée"""
    ensure_plugins_loaded()
    track = app.project.get_track(track_id_or_name)
    if not track:
        raise ValueError(f"Piste '{track_id_or_name}' introuvable.")

    synth_plugin = None
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
    description="Configure les paramètres généraux ou d'une couche sonore du synthétiseur NovaSynth (presets, volume master, glide, delay, reverb, ou par couche: waveform, octave, demi-tons, detune, volume, pan, sortie stéréo 0-9, enveloppe ADSR, filtre multimode cutoff/res/drive, LFO).",
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
    delay_mix: Optional[float] = None,
    reverb_enabled: Optional[bool] = None,
    reverb_room_size: Optional[float] = None,
    reverb_mix: Optional[float] = None,
    layer_index: Optional[int] = None,
    layer_name: Optional[str] = None,
    waveform: Optional[str] = None,
    octave: Optional[int] = None,
    semitone: Optional[int] = None,
    fine_tune: Optional[float] = None,
    volume: Optional[float] = None,
    pan: Optional[float] = None,
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
    lfo_enabled: Optional[bool] = None,
    lfo_rate: Optional[float] = None,
    lfo_depth: Optional[float] = None,
    lfo_target: Optional[str] = None
) -> Dict[str, Any]:
    track, synth = _get_or_create_synth_plugin(app, track_id_or_name)

    # 1. Preset global
    if preset:
        synth.apply_preset(preset)

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
    if delay_mix is not None:
        synth.delay.mix = max(0.0, min(1.0, float(delay_mix)))

    # 4. Master Reverb
    if reverb_enabled is not None:
        synth.reverb.enabled = bool(reverb_enabled)
    if reverb_room_size is not None:
        synth.reverb.room_size = max(0.0, min(1.0, float(reverb_room_size)))
    if reverb_mix is not None:
        synth.reverb.mix = max(0.0, min(1.0, float(reverb_mix)))

    # 5. Réglages par Couche Sonore (Layer)
    target_layer = None
    if layer_index is not None:
        target_layer = synth.get_layer(layer_index)
    elif layer_name is not None:
        target_layer = synth.get_layer(layer_name)
    elif any(v is not None for v in [waveform, octave, semitone, fine_tune, volume, pan, output_bus, attack, decay, sustain, release, filter_type, cutoff, resonance, drive, filter_env_amount, lfo_enabled, lfo_rate, lfo_depth, lfo_target]):
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
        if volume is not None:
            target_layer.volume = max(0.0, min(2.0, float(volume)))
        if pan is not None:
            target_layer.pan = max(-1.0, min(1.0, float(pan)))
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
            target_layer.release = max(0.001, min(5.0, float(release)))

        # Filtre
        if filter_type:
            target_layer.filter_type = filter_type.lower().strip()
        if cutoff is not None:
            target_layer.cutoff = max(20.0, min(20000.0, float(cutoff)))
        if resonance is not None:
            target_layer.resonance = max(0.2, min(10.0, float(resonance)))
        if drive is not None:
            target_layer.drive = max(0.0, min(3.0, float(drive)))
        if filter_env_amount is not None:
            target_layer.filter_env_amount = max(-1.0, min(1.0, float(filter_env_amount)))

        # LFO
        if lfo_enabled is not None:
            target_layer.lfo_enabled = bool(lfo_enabled)
        if lfo_rate is not None:
            target_layer.lfo_rate = max(0.1, min(30.0, float(lfo_rate)))
        if lfo_depth is not None:
            target_layer.lfo_depth = max(0.0, min(1.0, float(lfo_depth)))
        if lfo_target:
            target_layer.lfo_target = lfo_target.lower().strip()

    if hasattr(app, "refresh_project_ui"):
        app.refresh_project_ui()

    return {
        "status": "success",
        "track_id": track.id,
        "track_name": track.name,
        "synth_name": synth.name,
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
    output_bus: int = 0
) -> Dict[str, Any]:
    track, synth = _get_or_create_synth_plugin(app, track_id_or_name)
    layer = synth.add_layer(
        name=name,
        waveform=waveform,
        octave=octave,
        volume=volume,
        output_bus=output_bus
    )
    if hasattr(app, "refresh_project_ui"):
        app.refresh_project_ui()

    return {
        "status": "success",
        "track_id": track.id,
        "layer_id": layer.layer_id,
        "layer_name": layer.name,
        "waveform": layer.waveform,
        "octave": layer.octave,
        "output_bus": layer.output_bus,
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

    if hasattr(app, "refresh_project_ui"):
        app.refresh_project_ui()

    return {
        "status": "success",
        "track_id": track.id,
        "removed": str(layer_index_or_id),
        "total_layers": len(synth.layers)
    }


@action_registry.register(
    name="novadaw_set_synth_preset",
    description="Applique un preset d'usine sur NovaSynth ('Cyberpunk Acid Lead', 'Neon Horizon SuperSaw', 'Deep Sub & Punch Bass', 'Ethereal Dream Pad', '80s Synthwave Pluck', 'Sci-Fi FM Resonator').",
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
        return {
            "status": "error",
            "message": f"Preset '{preset_name}' introuvable. Presets disponibles : {list(synth.get_factory_presets().keys())}"
        }

    if hasattr(app, "refresh_project_ui"):
        app.refresh_project_ui()

    return {
        "status": "success",
        "track_id": track.id,
        "preset_name": synth.preset_name,
        "total_layers": len(synth.layers),
        "state": synth.get_state()
    }


@action_registry.register(
    name="novadaw_get_synth_state",
    description="Retourne l'état complet du synthétiseur NovaSynth d'une piste (couches empilées, formes d'ondes, filtres, enveloppes, LFO, sorties stéréo assignées et effets).",
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
        "preset_name": synth.preset_name,
        "master_volume": synth.master_volume,
        "glide_time": synth.glide_time,
        "total_layers": len(synth.layers),
        "available_presets": list(synth.get_factory_presets().keys()),
        "state": synth.get_state()
    }
