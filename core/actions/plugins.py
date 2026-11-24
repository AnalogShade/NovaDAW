"""
core/actions/plugins.py - Actions de gestion de la pile de plugins pour NovaDAW et le protocole MCP.
"""
from typing import Dict, Any, List, Optional
from core.action_registry import action_registry
from plugins.registry import plugin_registry, ensure_plugins_loaded


def _notify_plugin_change(app, track=None) -> None:
    """Invalide le cache audio de la piste, marque le projet modifié et rafraîchit l'UI."""
    if track is not None:
        engine = getattr(app, "audio_engine", None)
        if engine is not None and hasattr(engine, "invalidate_track_cache"):
            engine.invalidate_track_cache(track.id, trigger_async=True)
    if hasattr(app, "set_dirty"):
        app.set_dirty(True)
    if hasattr(app, "refresh_project_ui"):
        app.refresh_project_ui()


def _get_or_create_plugin(
    app,
    track_id_or_name: str,
    plugin_type_id: str,
    plugin_instance_id: Optional[str] = None,
    insert_at_front: bool = False,
):
    """Trouve ou instancie un plugin natif donné sur la piste demandée."""
    ensure_plugins_loaded()
    track = app.project.get_track(track_id_or_name)
    if not track:
        raise ValueError(f"Piste '{track_id_or_name}' introuvable.")

    plugin = None
    if plugin_instance_id:
        plugin = track.get_plugin(plugin_instance_id)

    if not plugin:
        for p in track.plugins:
            if getattr(p, "plugin_type_id", None) == plugin_type_id:
                plugin = p
                break

    if not plugin:
        plugin = plugin_registry.create_plugin(plugin_type_id)
        if not plugin:
            raise RuntimeError(f"Impossible d'instancier le plugin '{plugin_type_id}'.")
        if hasattr(plugin, "set_project"):
            plugin.set_project(app.project)
        if insert_at_front:
            track.add_plugin(plugin, index=0)
        else:
            track.add_plugin(plugin)

    if getattr(plugin, "is_instrument", False) or getattr(plugin, "category", "") == "instrument":
        if track.track_type == "midi":
            track.plugin_path = plugin_type_id
            track.plugin_name = plugin.name
            if hasattr(app.project, "add_rack_plugin"):
                app.project.add_rack_plugin(plugin_type_id, plugin.name, "instrument")

    return track, plugin


def _apply_named_preset(plugin, preset_name: str) -> Optional[Dict[str, Any]]:
    """Applique un preset sur un plugin et retourne une erreur structurée s'il est inconnu."""
    if not hasattr(plugin, "apply_preset"):
        return None
    if plugin.apply_preset(preset_name):
        return None
    available = list(plugin.get_factory_presets().keys()) if hasattr(plugin, "get_factory_presets") else []
    return {
        "status": "error",
        "message": f"Preset '{preset_name}' introuvable pour {plugin.name}. Presets disponibles : {available}",
        "available_presets": available,
    }


@action_registry.register(
    name="novadaw_get_available_plugins",
    description="Retourne la liste des plugins audio et instruments disponibles dans NovaDAW, dont Nova Piano.",
    tags=["plugins"]
)
def get_available_plugins(app) -> Dict[str, Any]:
    ensure_plugins_loaded()
    plugins = plugin_registry.get_available_plugins()
    return {
        "count": len(plugins),
        "plugins": plugins
    }


@action_registry.register(
    name="novadaw_add_plugin_to_track",
    description="Ajoute un plugin natif à une piste ou au Master. Nova Piano (novadaw.piano) est un instrument pour piste MIDI.",
    tags=["plugins", "track"]
)
def add_plugin_to_track(app, track_id: str, plugin_type_id: str) -> Dict[str, Any]:
    ensure_plugins_loaded()
    track = app.project.get_track(track_id)
    if not track:
        return {"success": False, "error": f"Piste non trouvée : '{track_id}'"}
    if plugin_type_id == "novadaw.piano" and track.track_type != "midi":
        return {"success": False, "error": "Nova Piano nécessite une piste MIDI."}

    plugin = plugin_registry.create_plugin(plugin_type_id)
    if not plugin:
        return {"success": False, "error": f"Type de plugin inconnu : '{plugin_type_id}'"}

    if hasattr(plugin, "set_project"):
        plugin.set_project(app.project)

    is_inst = getattr(plugin, "is_instrument", False) or getattr(plugin, "category", "") == "instrument"
    if is_inst:
        track.add_plugin(plugin, index=0)
        if track.track_type == "midi":
            track.plugin_path = plugin_type_id
            track.plugin_name = plugin.name
            if hasattr(app.project, "add_rack_plugin"):
                app.project.add_rack_plugin(plugin_type_id, plugin.name, "instrument")
    else:
        track.add_plugin(plugin)

    _notify_plugin_change(app, track)

    return {
        "success": True,
        "track_id": track.id,
        "track_name": track.name,
        "plugin_instance_id": plugin.instance_id,
        "plugin_name": plugin.name,
        "stack_position": track.plugins.index(plugin)
    }


@action_registry.register(
    name="novadaw_remove_plugin_from_track",
    description="Retire un plugin de la pile d'effets d'une piste par son identifiant d'instance.",
    tags=["plugins", "track"]
)
def remove_plugin_from_track(app, track_id: str, plugin_instance_id: str) -> Dict[str, Any]:
    track = app.project.get_track(track_id)
    if not track:
        return {"success": False, "error": f"Piste non trouvée : '{track_id}'"}

    removed = track.remove_plugin(plugin_instance_id)
    if removed:
        _notify_plugin_change(app, track)
        return {"success": True, "message": f"Plugin '{plugin_instance_id}' retiré de la piste '{track.name}'"}
    else:
        return {"success": False, "error": f"Plugin '{plugin_instance_id}' non trouvé sur la piste '{track.name}'"}


@action_registry.register(
    name="novadaw_set_plugin_bypass",
    description="Active ou désactive (bypass) un plugin sur une piste.",
    tags=["plugins", "track"]
)
def set_plugin_bypass(app, track_id: str, plugin_instance_id: str, enabled: bool) -> Dict[str, Any]:
    track = app.project.get_track(track_id)
    if not track:
        return {"success": False, "error": f"Piste non trouvée : '{track_id}'"}

    plugin = track.get_plugin(plugin_instance_id)
    if not plugin:
        return {"success": False, "error": f"Plugin '{plugin_instance_id}' non trouvé sur la piste '{track.name}'"}

    plugin.enabled = enabled
    _notify_plugin_change(app, track)
    return {
        "success": True,
        "track_id": track.id,
        "plugin_instance_id": plugin.instance_id,
        "plugin_name": plugin.name,
        "enabled": plugin.enabled
    }


@action_registry.register(
    name="novadaw_configure_plugin",
    description="Modifie les paramètres d'une instance de plugin, dont les réglages de Nova Piano et le preset.",
    tags=["plugins", "track"]
)
def configure_plugin(app, track_id: str, plugin_instance_id: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
    track = app.project.get_track(track_id)
    if not track:
        return {"success": False, "error": f"Piste non trouvée : '{track_id}'"}

    plugin = track.get_plugin(plugin_instance_id)
    if not plugin:
        return {"success": False, "error": f"Plugin '{plugin_instance_id}' non trouvé sur la piste '{track.name}'"}

    remaining = dict(parameters)
    preset_val = remaining.pop("preset", None)
    if preset_val is None and "preset_name" in remaining and hasattr(plugin, "apply_preset"):
        preset_val = remaining.pop("preset_name")

    if preset_val is not None:
        if hasattr(plugin, "apply_preset"):
            if not plugin.apply_preset(str(preset_val)):
                available = list(plugin.get_factory_presets().keys()) if hasattr(plugin, "get_factory_presets") else []
                return {
                    "success": False,
                    "error": f"Preset '{preset_val}' introuvable pour '{plugin.name}'.",
                    "available_presets": available,
                }
        else:
            remaining["preset_name"] = preset_val

    for key, value in remaining.items():
        if key == "enabled":
            plugin.enabled = bool(value)
        else:
            plugin.set_parameter(key, value)

    _notify_plugin_change(app, track)
    return {
        "success": True,
        "track_id": track.id,
        "plugin_instance_id": plugin.instance_id,
        "plugin_name": plugin.name,
        "state": plugin.get_state()
    }


@action_registry.register(
    name="novadaw_get_track_plugins",
    description="Inspecte la pile complète d'effets et plugins d'une piste ou du Master avec leurs paramètres détaillés.",
    tags=["plugins", "track"]
)
def get_track_plugins(app, track_id_or_name: str) -> Dict[str, Any]:
    track = app.project.get_track(track_id_or_name)
    if not track:
        raise ValueError(f"Piste '{track_id_or_name}' introuvable.")

    plugins_list = []
    for idx, p in enumerate(track.plugins):
        plugins_list.append({
            "slot_index": idx,
            "instance_id": getattr(p, "instance_id", str(idx)),
            "type_id": getattr(p, "plugin_type_id", "unknown"),
            "name": getattr(p, "name", "Plugin"),
            "category": getattr(p, "category", "effect"),
            "enabled": getattr(p, "enabled", True),
            "state": p.get_state() if hasattr(p, "get_state") else {}
        })

    return {
        "track_id": track.id,
        "track_name": track.name,
        "plugins_count": len(plugins_list),
        "plugins": plugins_list
    }


@action_registry.register(
    name="novadaw_configure_equalizer",
    description="Configure l'égaliseur paramétrique d'une piste ou du Master (fréquence, gain dB, Q, type de filtre, gain master ou presets: 'bass_boost', 'vocal_clarity', 'warm_master', 'bright_air', 'flat'). Ajoute l'EQ automatiquement si absent.",
    tags=["plugins", "eq"]
)
def configure_equalizer(
    app,
    track_id_or_name: str,
    plugin_instance_id: Optional[str] = None,
    band_index: Optional[int] = None,
    frequency: Optional[float] = None,
    gain_db: Optional[float] = None,
    q: Optional[float] = None,
    filter_type: Optional[str] = None,
    master_gain_db: Optional[float] = None,
    preset: Optional[str] = None
) -> Dict[str, Any]:
    ensure_plugins_loaded()
    track = app.project.get_track(track_id_or_name)
    if not track:
        raise ValueError(f"Piste '{track_id_or_name}' introuvable.")

    # Trouver l'instance EQ existante ou en ajouter une
    eq_plugin = None
    if plugin_instance_id:
        eq_plugin = track.get_plugin(plugin_instance_id)

    if not eq_plugin:
        for p in track.plugins:
            if getattr(p, "plugin_type_id", None) == "novadaw.equalizer":
                eq_plugin = p
                break

    if not eq_plugin:
        eq_plugin = plugin_registry.create_plugin("novadaw.equalizer")
        track.add_plugin(eq_plugin)

    # Application de preset
    if preset:
        p_name = preset.lower().strip()
        if p_name in ("bass_boost", "bass"):
            if len(eq_plugin.bands) > 0:
                eq_plugin.bands[0].gain_db = 5.0
                eq_plugin.bands[0].invalidate_cache()
            if len(eq_plugin.bands) > 1:
                eq_plugin.bands[1].gain_db = 2.5
                eq_plugin.bands[1].invalidate_cache()
        elif p_name in ("bass_cut", "low_cut", "high_pass", "hpf"):
            eq_plugin.apply_bass_cut(cutoff_freq=120.0, enabled=True)
        elif p_name in ("vocal_clarity", "vocal"):
            if len(eq_plugin.bands) > 2:
                eq_plugin.bands[1].gain_db = -2.0  # Nettoyage bas-médiums
                eq_plugin.bands[1].invalidate_cache()
            mid_idx = min(len(eq_plugin.bands) - 2, max(2, len(eq_plugin.bands) // 2))
            eq_plugin.bands[mid_idx].gain_db = 3.5  # Présence voix
            eq_plugin.bands[mid_idx].invalidate_cache()
            eq_plugin.bands[-1].gain_db = 2.0      # Air
            eq_plugin.bands[-1].invalidate_cache()
        elif p_name in ("warm_master", "warmth"):
            if len(eq_plugin.bands) > 0:
                eq_plugin.bands[0].gain_db = 1.5
                eq_plugin.bands[0].invalidate_cache()
            eq_plugin.bands[-1].gain_db = 1.2
            eq_plugin.bands[-1].invalidate_cache()
        elif p_name in ("bright_air", "air"):
            eq_plugin.bands[-1].gain_db = 4.0
            eq_plugin.bands[-1].invalidate_cache()
        elif p_name in ("flat", "reset"):
            for b in eq_plugin.bands:
                b.gain_db = 0.0
                b.invalidate_cache()
            eq_plugin.master_gain_db = 0.0

    # Ajustement de bande individuelle
    if band_index is not None and 0 <= band_index < len(eq_plugin.bands):
        target_band = eq_plugin.bands[band_index]
        if frequency is not None:
            target_band.frequency = max(20.0, min(20000.0, float(frequency)))
        if gain_db is not None:
            target_band.gain_db = max(-24.0, min(24.0, float(gain_db)))
        if q is not None:
            target_band.q = max(0.1, min(10.0, float(q)))
        if filter_type is not None:
            target_band.filter_type = str(filter_type)
        target_band.invalidate_cache()

    if master_gain_db is not None:
        eq_plugin.master_gain_db = max(-24.0, min(24.0, float(master_gain_db)))

    if hasattr(app, "refresh_project_ui"):
        app.refresh_project_ui()

    return {
        "status": "success",
        "track_id": track.id,
        "track_name": track.name,
        "plugin_instance_id": eq_plugin.instance_id,
        "master_gain_db": eq_plugin.master_gain_db,
        "preset_applied": preset,
        "bands_count": len(eq_plugin.bands),
        "bands": [b.to_dict() for b in eq_plugin.bands]
    }


@action_registry.register(
    name="novadaw_configure_compressor",
    description="Configure le compresseur dynamique d'une piste ou du Master (threshold_db, ratio, attack_ms, release_ms, makeup_gain_db, mix, knee_db ou presets: 'punchy_drums', 'vocal_leveler', 'master_glue', 'parallel_crush', 'gentle'). Ajoute le compresseur si absent.",
    tags=["plugins", "compressor"]
)
def configure_compressor(
    app,
    track_id_or_name: str,
    plugin_instance_id: Optional[str] = None,
    threshold_db: Optional[float] = None,
    ratio: Optional[float] = None,
    attack_ms: Optional[float] = None,
    release_ms: Optional[float] = None,
    makeup_gain_db: Optional[float] = None,
    mix: Optional[float] = None,
    knee_db: Optional[float] = None,
    preset: Optional[str] = None
) -> Dict[str, Any]:
    ensure_plugins_loaded()
    track = app.project.get_track(track_id_or_name)
    if not track:
        raise ValueError(f"Piste '{track_id_or_name}' introuvable.")

    comp_plugin = None
    if plugin_instance_id:
        comp_plugin = track.get_plugin(plugin_instance_id)

    if not comp_plugin:
        for p in track.plugins:
            if getattr(p, "plugin_type_id", None) == "novadaw.compressor":
                comp_plugin = p
                break

    if not comp_plugin:
        comp_plugin = plugin_registry.create_plugin("novadaw.compressor")
        track.add_plugin(comp_plugin)

    if preset:
        p_name = preset.lower().strip()
        if p_name in ("punchy_drums", "drums"):
            comp_plugin.threshold_db = -16.0
            comp_plugin.ratio = 4.0
            comp_plugin.attack_ms = 30.0
            comp_plugin.release_ms = 90.0
            comp_plugin.makeup_gain_db = 3.0
            comp_plugin.mix = 1.0
            comp_plugin.knee_db = 3.0
        elif p_name in ("vocal_leveler", "vocal"):
            comp_plugin.threshold_db = -20.0
            comp_plugin.ratio = 3.2
            comp_plugin.attack_ms = 12.0
            comp_plugin.release_ms = 140.0
            comp_plugin.makeup_gain_db = 4.0
            comp_plugin.mix = 1.0
            comp_plugin.knee_db = 6.0
        elif p_name in ("master_glue", "master"):
            comp_plugin.threshold_db = -12.0
            comp_plugin.ratio = 2.0
            comp_plugin.attack_ms = 30.0
            comp_plugin.release_ms = 100.0
            comp_plugin.makeup_gain_db = 1.5
            comp_plugin.mix = 1.0
            comp_plugin.knee_db = 4.0
        elif p_name in ("parallel_crush", "crush"):
            comp_plugin.threshold_db = -26.0
            comp_plugin.ratio = 8.0
            comp_plugin.attack_ms = 5.0
            comp_plugin.release_ms = 50.0
            comp_plugin.makeup_gain_db = 6.0
            comp_plugin.mix = 0.45
            comp_plugin.knee_db = 2.0
        elif p_name in ("gentle", "soft"):
            comp_plugin.threshold_db = -10.0
            comp_plugin.ratio = 1.8
            comp_plugin.attack_ms = 25.0
            comp_plugin.release_ms = 200.0
            comp_plugin.makeup_gain_db = 1.0
            comp_plugin.mix = 1.0
            comp_plugin.knee_db = 5.0

    if threshold_db is not None:
        comp_plugin.threshold_db = max(-60.0, min(0.0, float(threshold_db)))
    if ratio is not None:
        comp_plugin.ratio = max(1.0, min(20.0, float(ratio)))
    if attack_ms is not None:
        comp_plugin.attack_ms = max(0.1, min(500.0, float(attack_ms)))
    if release_ms is not None:
        comp_plugin.release_ms = max(5.0, min(3000.0, float(release_ms)))
    if makeup_gain_db is not None:
        comp_plugin.makeup_gain_db = max(-24.0, min(24.0, float(makeup_gain_db)))
    if mix is not None:
        comp_plugin.mix = max(0.0, min(1.0, float(mix)))
    if knee_db is not None:
        comp_plugin.knee_db = max(0.0, min(12.0, float(knee_db)))

    if hasattr(app, "refresh_project_ui"):
        app.refresh_project_ui()

    return {
        "status": "success",
        "track_id": track.id,
        "track_name": track.name,
        "plugin_instance_id": comp_plugin.instance_id,
        "preset_applied": preset,
        "state": comp_plugin.get_state()
    }


@action_registry.register(
    name="novadaw_configure_mixer",
    description="Configure la console de mixage globale et le bus Master (volume master 0.0-1.5, panoramique master -1.0 à +1.0, switch mono, switch dim -12dB).",
    tags=["plugins", "mixer"]
)
def configure_mixer(
    app,
    master_volume: Optional[float] = None,
    master_pan: Optional[float] = None,
    mono: Optional[bool] = None,
    dim: Optional[bool] = None
) -> Dict[str, Any]:
    master_t = app.project.ensure_master_track()
    mixer_p = None
    for p in master_t.plugins:
        if getattr(p, "plugin_type_id", None) == "novadaw.mixer":
            mixer_p = p
            break

    if mixer_p:
        if master_volume is not None:
            mixer_p.master_volume = max(0.0, min(1.5, float(master_volume)))
        if master_pan is not None:
            mixer_p.master_pan = max(-1.0, min(1.0, float(master_pan)))
        if mono is not None:
            mixer_p.mono_switch = bool(mono)
        if dim is not None:
            mixer_p.dim_switch = bool(dim)

    if master_volume is not None:
        master_t.volume = max(0.0, min(1.5, float(master_volume)))
        if hasattr(app, "_on_master_vol_changed"):
            app._on_master_vol_changed(master_t.volume)
        elif hasattr(app, "transport_bar") and hasattr(app.transport_bar, "slider_master"):
            app.transport_bar.slider_master.setValue(int(master_t.volume * 100))

    if master_pan is not None:
        master_t.pan = max(-1.0, min(1.0, float(master_pan)))

    if hasattr(app, "mixer_widget") and app.mixer_widget:
        app.mixer_widget.refresh_tracks()

    return {
        "status": "success",
        "master_volume": master_t.volume,
        "master_pan": master_t.pan,
        "mono": mixer_p.mono_switch if mixer_p else False,
        "dim": mixer_p.dim_switch if mixer_p else False
    }


@action_registry.register(
    name="novadaw_get_mixer_levels",
    description="Mesure les niveaux audio crêtes (Peak dB L/R) et RMS en direct pour le bus Master et chaque piste du projet.",
    tags=["plugins", "mixer", "meter"]
)
def get_mixer_levels(app) -> Dict[str, Any]:
    master_t = app.project.ensure_master_track()
    mixer_p = None
    for p in master_t.plugins:
        if getattr(p, "plugin_type_id", None) == "novadaw.mixer":
            mixer_p = p
            break

    tracks_peaks = {}
    if mixer_p:
        for t in app.project.tracks:
            peaks = mixer_p.track_peaks.get(t.id, (-60.0, -60.0))
            tracks_peaks[t.name] = {
                "track_id": t.id,
                "peak_left_db": round(peaks[0], 1),
                "peak_right_db": round(peaks[1], 1)
            }

    return {
        "master": {
            "peak_left_db": round(mixer_p.peak_left_db, 1) if mixer_p else -60.0,
            "peak_right_db": round(mixer_p.peak_right_db, 1) if mixer_p else -60.0,
            "rms_left_db": round(mixer_p.rms_left_db, 1) if mixer_p else -60.0,
            "rms_right_db": round(mixer_p.rms_right_db, 1) if mixer_p else -60.0,
        },
        "tracks": tracks_peaks
    }


@action_registry.register(
    name="novadaw_get_hardware_devices",
    description="Retourne les informations matérielles complètes du système : cartes graphiques (GPU, VRAM, version pilote), pilotes audio (ASIO, WASAPI, etc.) et périphériques actifs.",
    tags=["hardware", "gpu", "audio"]
)
def get_hardware_devices_action(app) -> Dict[str, Any]:
    from core.hardware_manager import hardware_manager
    gpus = hardware_manager.get_gpu_devices()
    host_apis = hardware_manager.get_audio_host_apis()
    devices = hardware_manager.get_audio_devices()

    current_audio = {
        "sample_rate": app.audio_engine.sample_rate if hasattr(app, "audio_engine") else 44100,
        "buffer_size": app.audio_engine.block_size if hasattr(app, "audio_engine") else 512,
        "output_device": getattr(app.audio_engine, "output_device", None),
        "is_playing": app.audio_engine.is_playing if hasattr(app, "audio_engine") else False
    }

    return {
        "gpus": gpus,
        "audio_host_apis": [h["name"] for h in host_apis],
        "output_devices_count": len(devices["outputs"]),
        "input_devices_count": len(devices["inputs"]),
        "current_audio_settings": current_audio,
        "graphics_settings": hardware_manager.settings.get("graphics", {})
    }


@action_registry.register(
    name="novadaw_set_audio_device",
    description="Configure le périphérique de sortie audio, la fréquence d'échantillonnage ou la taille du buffer en direct et redémarre le moteur audio.",
    tags=["hardware", "audio"]
)
def set_audio_device_action(
    app,
    output_device_index: Optional[int] = None,
    sample_rate: Optional[int] = None,
    buffer_size: Optional[int] = None
) -> Dict[str, Any]:
    from core.hardware_manager import hardware_manager

    if hasattr(app, "audio_engine"):
        app.audio_engine.configure_device(
            output_device=output_device_index,
            sample_rate=sample_rate,
            buffer_size=buffer_size
        )
        # Sauvegarder dans les préférences
        if output_device_index is not None:
            hardware_manager.settings["audio"]["output_device_index"] = output_device_index
        if sample_rate is not None:
            hardware_manager.settings["audio"]["sample_rate"] = int(sample_rate)
        if buffer_size is not None:
            hardware_manager.settings["audio"]["buffer_size"] = int(buffer_size)
        hardware_manager.save_settings()

        latency_ms = hardware_manager.calculate_latency_ms(
            app.audio_engine.block_size,
            app.audio_engine.sample_rate
        )

        if hasattr(app, "lbl_engine_info"):
            app.lbl_engine_info.setText(
                f"Moteur Audio : {app.audio_engine.sample_rate} Hz Stéréo | Buffer: {app.audio_engine.block_size} ({latency_ms} ms)"
            )

        return {
            "status": "success",
            "output_device_index": app.audio_engine.output_device,
            "sample_rate": app.audio_engine.sample_rate,
            "buffer_size": app.audio_engine.block_size,
            "latency_ms": latency_ms
        }

    return {"status": "error", "message": "Moteur audio indisponible."}


@action_registry.register(
    name="novadaw_configure_drum_machine",
    description="Configure l'instrument virtuel Nova Drums VSTi d'une piste (presets: 'Studio Acoustic', 'Punchy Rock', 'Trap / Modern', 'Big Hall Ambience', 'Tight & Dry', master_volume, réverbération: room_size, damping, wet_mix, et réglages par pad: volume, pan, tune, reverb_send).",
    tags=["plugins", "drums", "instrument"]
)
def configure_drum_machine(
    app,
    track_id_or_name: str,
    preset: Optional[str] = None,
    master_volume: Optional[float] = None,
    reverb_enabled: Optional[bool] = None,
    reverb_room_size: Optional[float] = None,
    reverb_damping: Optional[float] = None,
    reverb_wet_mix: Optional[float] = None,
    pad_id: Optional[str] = None,
    pad_volume: Optional[float] = None,
    pad_pan: Optional[float] = None,
    pad_tune: Optional[float] = None,
    pad_reverb_send: Optional[float] = None,
    pad_sample: Optional[str] = None
) -> Dict[str, Any]:
    ensure_plugins_loaded()
    track = app.project.get_track(track_id_or_name)
    if not track:
        raise ValueError(f"Piste '{track_id_or_name}' introuvable.")

    drum_plugin = None
    for p in track.plugins:
        if getattr(p, "plugin_type_id", None) == "novadaw.drum_machine":
            drum_plugin = p
            break

    if not drum_plugin:
        drum_plugin = plugin_registry.create_plugin("novadaw.drum_machine")
        if drum_plugin:
            track.plugins.insert(0, drum_plugin)

    if not drum_plugin:
        return {"status": "error", "message": "Impossible d'instancier Nova Drums VSTi."}

    if track.track_type == "midi":
        track.plugin_path = "novadaw.drum_machine"
        track.plugin_name = "Nova Drums VSTi"
        if hasattr(app.project, "add_rack_plugin"):
            app.project.add_rack_plugin("novadaw.drum_machine", "Nova Drums VSTi", "instrument")

    if preset:
        drum_plugin.apply_preset(preset)

    if master_volume is not None:
        drum_plugin.master_volume = max(0.0, min(2.0, float(master_volume)))

    if reverb_enabled is not None:
        drum_plugin.reverb.enabled = bool(reverb_enabled)

    if reverb_room_size is not None:
        drum_plugin.reverb.room_size = max(0.0, min(1.0, float(reverb_room_size)))

    if reverb_damping is not None:
        drum_plugin.reverb.damping = max(0.0, min(1.0, float(reverb_damping)))

    if reverb_wet_mix is not None:
        drum_plugin.reverb.wet_mix = max(0.0, min(1.0, float(reverb_wet_mix)))

    if pad_id:
        p_needle = pad_id.lower().strip()
        target_pad = None
        for p in drum_plugin.pads:
            if p.pad_id.lower() == p_needle or p.name.lower() == p_needle:
                target_pad = p
                break
        if target_pad:
            if pad_sample is not None and hasattr(drum_plugin, "set_pad_sample"):
                drum_plugin.set_pad_sample(target_pad.pad_id, pad_sample)
            if pad_volume is not None:
                target_pad.volume = max(0.0, min(2.0, float(pad_volume)))
            if pad_pan is not None:
                target_pad.pan = max(-1.0, min(1.0, float(pad_pan)))
            if pad_tune is not None:
                target_pad.tune = max(-12.0, min(12.0, float(pad_tune)))
            if pad_reverb_send is not None:
                target_pad.reverb_send = max(0.0, min(1.0, float(pad_reverb_send)))
            drum_plugin.invalidate_cache(target_pad.pad_id)
            drum_plugin.warm_up_cache([target_pad.pad_id], async_bg=True)

    if hasattr(app, "refresh_project_ui"):
        app.refresh_project_ui()

    return {
        "status": "success",
        "track_id": track.id,
        "track_name": track.name,
        "plugin_instance_id": drum_plugin.instance_id,
        "state": drum_plugin.get_state()
    }


@action_registry.register(
    name="novadaw_configure_vocal_glide",
    description="Configure Nova Vocal Glide sur une piste (nivellement vocal automatique : preset, target_db -42..-3, activity_threshold_db -72..-12, aggressiveness 0..100, speed 0..100, max_boost_db 0..15, max_cut_db 0..15, idle_behavior 'return_zero'|'hold', sidechain_vocal_filter, output_trim_db -12..12, write_automation_live, crystallize_auto_bypass, crystallize_detail 'precise'|'musical'|'smooth', enabled). Ajoute le plugin si absent.",
    tags=["plugins", "vocal", "dynamics"]
)
def configure_vocal_glide(
    app,
    track_id_or_name: str,
    plugin_instance_id: Optional[str] = None,
    preset: Optional[str] = None,
    target_db: Optional[float] = None,
    activity_threshold_db: Optional[float] = None,
    aggressiveness: Optional[float] = None,
    speed: Optional[float] = None,
    max_boost_db: Optional[float] = None,
    max_cut_db: Optional[float] = None,
    idle_behavior: Optional[str] = None,
    sidechain_vocal_filter: Optional[bool] = None,
    output_trim_db: Optional[float] = None,
    write_automation_live: Optional[bool] = None,
    crystallize_auto_bypass: Optional[bool] = None,
    crystallize_detail: Optional[str] = None,
    enabled: Optional[bool] = None
) -> Dict[str, Any]:
    track, glide_plugin = _get_or_create_plugin(
        app, track_id_or_name, "novadaw.vocal_glide", plugin_instance_id=plugin_instance_id
    )

    if preset:
        err = _apply_named_preset(glide_plugin, preset)
        if err:
            return err

    updates: Dict[str, Any] = {}
    for key, val in (
        ("target_db", target_db),
        ("activity_threshold_db", activity_threshold_db),
        ("aggressiveness", aggressiveness),
        ("speed", speed),
        ("max_boost_db", max_boost_db),
        ("max_cut_db", max_cut_db),
        ("idle_behavior", idle_behavior.strip().lower() if idle_behavior is not None else None),
        ("sidechain_vocal_filter", sidechain_vocal_filter),
        ("output_trim_db", output_trim_db),
        ("write_automation_live", write_automation_live),
        ("crystallize_auto_bypass", crystallize_auto_bypass),
        ("crystallize_detail", crystallize_detail.strip().lower() if crystallize_detail is not None else None),
        ("enabled", enabled),
    ):
        if val is not None:
            updates[key] = val

    if updates:
        glide_plugin.set_state(updates)

    _notify_plugin_change(app, track)

    return {
        "status": "success",
        "track_id": track.id,
        "track_name": track.name,
        "plugin_instance_id": glide_plugin.instance_id,
        "enabled": glide_plugin.enabled,
        "preset_name": getattr(glide_plugin, "preset_name", None),
        "available_presets": list(glide_plugin.get_factory_presets().keys()),
        "state": glide_plugin.get_state()
    }


@action_registry.register(
    name="novadaw_crystallize_vocal_glide",
    description="Analyse les clips audio d'une piste via Nova Vocal Glide et génère la piste d'automatisation Volume correspondante (scope: 'track' ou 'loop', detail_mode: 'precise'|'musical'|'smooth', auto_bypass optionnel).",
    tags=["plugins", "vocal", "automation"]
)
def crystallize_vocal_glide(
    app,
    track_id_or_name: str,
    plugin_instance_id: Optional[str] = None,
    scope: str = "track",
    detail_mode: Optional[str] = None,
    auto_bypass: Optional[bool] = None,
    start_beat: Optional[float] = None,
    end_beat: Optional[float] = None
) -> Dict[str, Any]:
    track, glide_plugin = _get_or_create_plugin(
        app, track_id_or_name, "novadaw.vocal_glide", plugin_instance_id=plugin_instance_id
    )

    project = getattr(app, "project", None)
    bpm = float(getattr(project, "bpm", 120.0)) if project else 120.0
    sr = int(getattr(getattr(app, "audio_engine", None), "sample_rate", 44100) or 44100)

    win_start = float(start_beat) if start_beat is not None else None
    win_end = float(end_beat) if end_beat is not None else None
    if win_start is None and win_end is None and str(scope).strip().lower() == "loop":
        if project and getattr(project, "loop_enabled", False) and project.loop_end_beat > project.loop_start_beat:
            win_start = float(project.loop_start_beat)
            win_end = float(project.loop_end_beat)

    mode = detail_mode.strip().lower() if detail_mode is not None else None
    pts, stats = glide_plugin.analyze_track_to_automation_points(
        track=track,
        bpm=bpm,
        sample_rate=sr,
        start_beat=win_start,
        end_beat=win_end,
        detail_mode=mode,
    )

    if not pts:
        return {
            "status": "error",
            "message": f"Aucun clip audio exploitable trouvé sur la piste '{track.name}' dans la plage sélectionnée."
        }

    lane = track.add_automation_lane("volume", seed_initial_point=False)
    lane.enabled = True
    lane.visible = True
    lane.mode = "read"
    track.automation_expanded = True

    if win_start is not None and win_end is not None:
        lane.points = [p for p in lane.points if p.beat < win_start or p.beat > win_end]
    else:
        lane.points.clear()

    for beat, val in pts:
        lane.add_point(beat, val, curve_type="linear", merge_tol=1e-3)

    should_bypass = glide_plugin.crystallize_auto_bypass if auto_bypass is None else bool(auto_bypass)
    if should_bypass:
        glide_plugin.enabled = False

    _notify_plugin_change(app, track)

    return {
        "status": "success",
        "track_id": track.id,
        "track_name": track.name,
        "plugin_instance_id": glide_plugin.instance_id,
        "plugin_enabled": glide_plugin.enabled,
        "point_count": stats["point_count"],
        "min_gain_db": round(float(stats["min_gain_db"]), 2),
        "max_gain_db": round(float(stats["max_gain_db"]), 2),
        "was_clamped": bool(stats.get("was_clamped", False)),
        "analyzed_beats": round(float(stats.get("analyzed_beats", 0.0)), 3)
    }


@action_registry.register(
    name="novadaw_configure_piano",
    description=(
        "Assigne et règle Nova Piano sur une piste MIDI. "
        "Espaces de réverbération (reverb_room) : small_room, large_room, concert_hall, cathedral. "
        "Dosage reverb_mix, dureté du marteau hammer_hardness et résonance de la table "
        "soundboard_resonance de 0 à 1, master_volume de 0 à 2, et activation enabled."
    ),
    tags=["plugins", "piano", "instrument"],
)
def configure_piano(
    app,
    track_id_or_name: str,
    reverb_room: Optional[str] = None,
    reverb_mix: Optional[float] = None,
    master_volume: Optional[float] = None,
    enabled: Optional[bool] = None,
    hammer_hardness: Optional[float] = None,
    soundboard_resonance: Optional[float] = None,
) -> Dict[str, Any]:
    from math import isfinite
    from plugins.piano.piano_dsp import ROOMS
    from plugins.piano.piano_plugin import NovaPianoPlugin

    track = app.project.get_track(track_id_or_name)
    if track is None or track.track_type != "midi":
        return {"status": "error", "message": "Sélectionnez une piste MIDI existante pour Nova Piano."}

    room = reverb_room.strip().lower() if reverb_room is not None else None
    if room is not None and room not in ROOMS:
        return {"status": "error", "message": f"Espace inconnu : {reverb_room}", "rooms": list(ROOMS)}
    parameters = {
        "reverb_mix": reverb_mix,
        "master_volume": master_volume,
        "hammer_hardness": hammer_hardness,
        "soundboard_resonance": soundboard_resonance,
    }
    for name, value in parameters.items():
        limit = NovaPianoPlugin.PARAMETER_LIMITS[name]
        if value is not None and (not isfinite(value) or not 0 <= value <= limit):
            return {"status": "error", "message": f"{name} doit être compris entre 0 et {limit}."}

    track, piano = _get_or_create_plugin(app, track.id, "novadaw.piano", insert_at_front=True)
    parameters.update(reverb_room=room, enabled=enabled)
    updates = {key: value for key, value in parameters.items() if value is not None}
    piano.set_state(updates)
    _notify_plugin_change(app, track)
    return {
        "status": "success",
        "track_id": track.id,
        "track_name": track.name,
        "plugin_instance_id": piano.instance_id,
        "state": piano.get_state(),
        "rooms": {key: data[0] for key, data in ROOMS.items()},
    }


@action_registry.register(
    name="novadaw_configure_guitar",
    description="Configure l'instrument virtuel Nova Guitar sur une piste MIDI (presets acoustiques/électriques/basse, guitar_mode 'acoustic_steel'|'acoustic_nylon'|'electric_clean'|'electric_bass', string_tension 0..1, pluck_position 0.04..0.5, brightness 0.05..0.98, sustain_sec 0.4..12, release_sec 0.04..2, pick_attack 0..1, palm_mute 0..1, body_resonance 0..1.5, pickup_position 0.06..0.45, stereo_width 0..1, reverb_mix 0..0.65, reverb_room 0.1..0.95, dual_polarization, master_volume 0..2, enabled). Ajoute l'instrument si absent.",
    tags=["plugins", "guitar", "instrument"]
)
def configure_guitar(
    app,
    track_id_or_name: str,
    plugin_instance_id: Optional[str] = None,
    preset: Optional[str] = None,
    guitar_mode: Optional[str] = None,
    string_tension: Optional[float] = None,
    pluck_position: Optional[float] = None,
    brightness: Optional[float] = None,
    sustain_sec: Optional[float] = None,
    release_sec: Optional[float] = None,
    pick_attack: Optional[float] = None,
    palm_mute: Optional[float] = None,
    body_resonance: Optional[float] = None,
    pickup_position: Optional[float] = None,
    stereo_width: Optional[float] = None,
    reverb_mix: Optional[float] = None,
    reverb_room: Optional[float] = None,
    dual_polarization: Optional[bool] = None,
    master_volume: Optional[float] = None,
    enabled: Optional[bool] = None
) -> Dict[str, Any]:
    track, guitar_plugin = _get_or_create_plugin(
        app, track_id_or_name, "novadaw.guitar", plugin_instance_id=plugin_instance_id, insert_at_front=True
    )

    if preset:
        err = _apply_named_preset(guitar_plugin, preset)
        if err:
            return err

    valid_modes = {"acoustic_steel", "acoustic_nylon", "electric_clean", "electric_bass"}
    clean_mode = guitar_mode.strip().lower() if guitar_mode is not None else None
    if clean_mode is not None and clean_mode not in valid_modes:
        return {
            "status": "error",
            "message": f"Mode guitare invalide '{guitar_mode}'. Modes supportés : {sorted(valid_modes)}"
        }

    updates: Dict[str, Any] = {}
    for key, val in (
        ("guitar_mode", clean_mode),
        ("string_tension", string_tension),
        ("pluck_position", pluck_position),
        ("brightness", brightness),
        ("sustain_sec", sustain_sec),
        ("release_sec", release_sec),
        ("pick_attack", pick_attack),
        ("palm_mute", palm_mute),
        ("body_resonance", body_resonance),
        ("pickup_position", pickup_position),
        ("stereo_width", stereo_width),
        ("reverb_mix", reverb_mix),
        ("reverb_room", reverb_room),
        ("dual_polarization", dual_polarization),
        ("master_volume", master_volume),
        ("enabled", enabled),
    ):
        if val is not None:
            updates[key] = val

    if updates:
        guitar_plugin.set_state(updates)

    _notify_plugin_change(app, track)

    return {
        "status": "success",
        "track_id": track.id,
        "track_name": track.name,
        "plugin_instance_id": guitar_plugin.instance_id,
        "preset_name": guitar_plugin.preset_name,
        "available_presets": list(guitar_plugin.get_factory_presets().keys()),
        "state": guitar_plugin.get_state()
    }


@action_registry.register(
    name="novadaw_configure_guitar_amp",
    description="Configure l'amplificateur de guitare Nova Guitar Amp sur une piste (presets: 'Clean Studio (Neutre)', 'Warm Clean (Tube)', 'Blues Crunch', 'Classic Rock Overdrive', 'High Gain Metal', volume 0..2, distortion 0..1, low_db -12..12, mid_db -12..12, treble_db -12..12, cab_enabled, enabled). Ajoute l'ampli si absent.",
    tags=["plugins", "guitar", "amp", "effect"]
)
def configure_guitar_amp(
    app,
    track_id_or_name: str,
    plugin_instance_id: Optional[str] = None,
    preset: Optional[str] = None,
    volume: Optional[float] = None,
    distortion: Optional[float] = None,
    low_db: Optional[float] = None,
    mid_db: Optional[float] = None,
    treble_db: Optional[float] = None,
    cab_enabled: Optional[bool] = None,
    enabled: Optional[bool] = None
) -> Dict[str, Any]:
    track, amp_plugin = _get_or_create_plugin(
        app, track_id_or_name, "novadaw.guitar_amp", plugin_instance_id=plugin_instance_id
    )

    if preset:
        err = _apply_named_preset(amp_plugin, preset)
        if err:
            return err

    updates: Dict[str, Any] = {}
    for key, val in (
        ("volume", volume),
        ("distortion", distortion),
        ("low_db", low_db),
        ("mid_db", mid_db),
        ("treble_db", treble_db),
        ("cab_enabled", cab_enabled),
        ("enabled", enabled),
    ):
        if val is not None:
            updates[key] = val

    if updates:
        amp_plugin.set_state(updates)

    _notify_plugin_change(app, track)

    return {
        "status": "success",
        "track_id": track.id,
        "track_name": track.name,
        "plugin_instance_id": amp_plugin.instance_id,
        "preset_name": amp_plugin.preset_name,
        "available_presets": list(amp_plugin.get_factory_presets().keys()),
        "state": amp_plugin.get_state()
    }

