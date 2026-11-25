"""
core/actions/plugins.py - Actions de gestion de la pile de plugins pour NovaDAW et le protocole MCP.
"""
from typing import Dict, Any, List, Optional
from core.action_registry import action_registry
from plugins.registry import plugin_registry, ensure_plugins_loaded


def _notify_plugin_change(app, track=None, plugin=None) -> None:
    """Invalide le cache audio de la piste, marque le projet modifié et rafraîchit l'UI + éditeurs ouverts."""
    if track is not None:
        engine = getattr(app, "audio_engine", None)
        if engine is not None:
            if getattr(track, "track_type", "") == "master":
                if hasattr(engine, "invalidate_all_track_caches"):
                    engine.invalidate_all_track_caches(trigger_async=True)
            elif hasattr(engine, "invalidate_track_cache"):
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
        sync_open_native_editor(plugin)
    except Exception:
        pass


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

    _notify_plugin_change(app, track, plugin)

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
    _notify_plugin_change(app, track, plugin)
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

    _notify_plugin_change(app, track, plugin)
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
    description="Configure l'égaliseur paramétrique d'une piste ou du Master (band_count: 3/10/12/24, fréquence, gain dB, Q, type de filtre, band_enabled, coupe-bas HPF bass_cut_enabled/bass_cut_freq, gain master ou presets: 'bass_boost', 'bass_cut', 'vocal_clarity', 'warm_master', 'bright_air', 'flat'). Ajoute l'EQ automatiquement si absent.",
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
    preset: Optional[str] = None,
    band_count: Optional[int] = None,
    band_enabled: Optional[bool] = None,
    bass_cut_enabled: Optional[bool] = None,
    bass_cut_freq: Optional[float] = None,
    enabled: Optional[bool] = None,
) -> Dict[str, Any]:
    track, eq_plugin = _get_or_create_plugin(
        app, track_id_or_name, "novadaw.equalizer", plugin_instance_id=plugin_instance_id
    )

    if band_count is not None:
        eq_plugin.set_band_count(int(band_count))

    if preset:
        err = _apply_named_preset(eq_plugin, preset)
        if err:
            return err

    if bass_cut_enabled is not None:
        eq_plugin.apply_bass_cut(
            cutoff_freq=float(bass_cut_freq) if bass_cut_freq is not None else 120.0,
            enabled=bool(bass_cut_enabled),
        )

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
        if band_enabled is not None:
            target_band.enabled = bool(band_enabled)
        target_band.invalidate_cache()

    if master_gain_db is not None:
        eq_plugin.master_gain_db = max(-24.0, min(24.0, float(master_gain_db)))

    if enabled is not None:
        eq_plugin.enabled = bool(enabled)

    _notify_plugin_change(app, track, eq_plugin)

    return {
        "status": "success",
        "track_id": track.id,
        "track_name": track.name,
        "plugin_instance_id": eq_plugin.instance_id,
        "enabled": eq_plugin.enabled,
        "master_gain_db": eq_plugin.master_gain_db,
        "preset_applied": preset,
        "bands_count": len(eq_plugin.bands),
        "bands": [b.to_dict() for b in eq_plugin.bands]
    }


@action_registry.register(
    name="novadaw_configure_compressor",
    description="Configure le compresseur dynamique d'une piste ou du Master (threshold_db, ratio, attack_ms, release_ms, makeup_gain_db, auto_makeup, mix, knee_db, detection_mode 'peak'|'rms', sidechain_hpf_hz ou presets d'usine/utilisateur). Ajoute le compresseur si absent.",
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
    preset: Optional[str] = None,
    detection_mode: Optional[str] = None,
    sidechain_hpf_hz: Optional[float] = None,
    auto_makeup: Optional[bool] = None,
    enabled: Optional[bool] = None,
) -> Dict[str, Any]:
    track, comp_plugin = _get_or_create_plugin(
        app, track_id_or_name, "novadaw.compressor", plugin_instance_id=plugin_instance_id
    )

    if preset:
        err = _apply_named_preset(comp_plugin, preset)
        if err:
            return err

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
    if detection_mode is not None:
        dm = detection_mode.strip().lower()
        if dm not in ("peak", "rms"):
            return {"status": "error", "message": f"detection_mode invalide '{detection_mode}' (attendus: 'peak', 'rms')."}
        comp_plugin.detection_mode = dm
    if sidechain_hpf_hz is not None:
        comp_plugin.sidechain_hpf_hz = max(0.0, min(500.0, float(sidechain_hpf_hz)))
    if auto_makeup is not None:
        comp_plugin.auto_makeup = bool(auto_makeup)
    if enabled is not None:
        comp_plugin.enabled = bool(enabled)

    _notify_plugin_change(app, track, comp_plugin)

    return {
        "status": "success",
        "track_id": track.id,
        "track_name": track.name,
        "plugin_instance_id": comp_plugin.instance_id,
        "preset_applied": getattr(comp_plugin, "preset_name", preset),
        "available_presets": list(comp_plugin.get_factory_presets().keys()),
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

    if hasattr(app, "set_dirty"):
        app.set_dirty(True)

    if hasattr(app, "mixer_widget") and app.mixer_widget:
        app.mixer_widget.refresh_tracks()
        if hasattr(app.mixer_widget, "sync_controls_from_tracks"):
            app.mixer_widget.sync_controls_from_tracks()
    try:
        from ui.plugin_dialogs import sync_open_native_editor
        sync_open_native_editor(mixer_p)
    except Exception:
        pass

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
        "effective_buffer_size": app.audio_engine.effective_block_size if hasattr(app, "audio_engine") else 512,
        "output_device": getattr(app.audio_engine, "output_device", None),
        "wasapi_exclusive": getattr(app.audio_engine, "_wasapi_exclusive", True) if hasattr(app, "audio_engine") else True,
        "streaming_mode": getattr(app.audio_engine, "streaming_mode", False) if hasattr(app, "audio_engine") else False,
        "active_stream_mode": getattr(app.audio_engine, "active_stream_mode", "shared") if hasattr(app, "audio_engine") else "shared",
        "is_playing": app.audio_engine.is_playing if hasattr(app, "audio_engine") else False
    }

    return {
        "gpus": gpus,
        "audio_host_apis": [h["name"] for h in host_apis],
        "output_devices_count": len(devices["outputs"]),
        "input_devices_count": len(devices["inputs"]),
        "output_devices": devices["outputs"],
        "input_devices": devices["inputs"],
        "current_audio_settings": current_audio,
        "graphics_settings": hardware_manager.settings.get("graphics", {})
    }


@action_registry.register(
    name="novadaw_set_audio_device",
    description="Configure le périphérique de sortie et/ou d'entrée audio, la fréquence d'échantillonnage ou la taille du buffer en direct et redémarre le moteur audio.",
    tags=["hardware", "audio"]
)
def set_audio_device_action(
    app,
    output_device_index: Optional[int] = None,
    sample_rate: Optional[int] = None,
    buffer_size: Optional[int] = None,
    wasapi_exclusive: Optional[bool] = None,
    streaming_mode: Optional[bool] = None,
    input_device_index: Optional[int] = None,
) -> Dict[str, Any]:
    from core.hardware_manager import hardware_manager

    if hasattr(app, "audio_engine"):
        if input_device_index is not None:
            app.audio_engine.input_device = int(input_device_index)
            hardware_manager.settings["audio"]["input_device_index"] = int(input_device_index)
        app.audio_engine.configure_device(
            output_device=output_device_index,
            sample_rate=sample_rate,
            buffer_size=buffer_size,
            wasapi_exclusive=wasapi_exclusive,
            streaming_mode=streaming_mode,
        )
        # Sauvegarder dans les préférences
        if output_device_index is not None:
            hardware_manager.settings["audio"]["output_device_index"] = output_device_index
        if sample_rate is not None:
            hardware_manager.settings["audio"]["sample_rate"] = int(sample_rate)
        if buffer_size is not None:
            hardware_manager.settings["audio"]["buffer_size"] = int(buffer_size)
        if wasapi_exclusive is not None:
            hardware_manager.settings["audio"]["wasapi_exclusive"] = bool(wasapi_exclusive)
        if streaming_mode is not None:
            hardware_manager.settings["audio"]["streaming_mode"] = bool(streaming_mode)
        hardware_manager.save_settings()

        if hasattr(app, "_refresh_engine_info_label"):
            app._refresh_engine_info_label()
        elif hasattr(app, "lbl_engine_info"):
            latency_ms = hardware_manager.calculate_latency_ms(
                app.audio_engine.block_size,
                app.audio_engine.sample_rate
            )
            app.lbl_engine_info.setText(
                f"Moteur Audio : {app.audio_engine.sample_rate} Hz Stéréo | Buffer: {app.audio_engine.block_size} ({latency_ms} ms)"
            )

        latencies = hardware_manager.get_device_latencies(
            output_device_index=app.audio_engine.output_device,
            sample_rate=app.audio_engine.sample_rate,
            buffer_size=app.audio_engine.block_size,
            streaming_mode=app.audio_engine.streaming_mode,
            wasapi_exclusive=app.audio_engine._wasapi_exclusive,
        )

        return {
            "status": "success",
            "output_device_index": app.audio_engine.output_device,
            "sample_rate": app.audio_engine.sample_rate,
            "buffer_size": app.audio_engine.block_size,
            "effective_buffer_size": app.audio_engine.effective_block_size,
            "streaming_mode": app.audio_engine.streaming_mode,
            "wasapi_exclusive": app.audio_engine._wasapi_exclusive,
            "latency_ms": latencies.get("output_ms", 0.0),
        }

    return {"status": "error", "message": "Moteur audio indisponible."}


@action_registry.register(
    name="novadaw_configure_drum_machine",
    description="Configure l'instrument virtuel Nova Drums VSTi d'une piste (presets: 'Studio Acoustic', 'Punchy Rock', 'Trap / Modern', 'Big Hall Ambience', 'Tight & Dry' ou presets utilisateur, master_volume, réverbération: room_size, damping, width, wet_mix, et réglages par pad: volume, pan, tune, decay, reverb_send, muted, soloed, pad_sample).",
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
    pad_sample: Optional[str] = None,
    reverb_width: Optional[float] = None,
    pad_decay: Optional[float] = None,
    pad_muted: Optional[bool] = None,
    pad_soloed: Optional[bool] = None,
    enabled: Optional[bool] = None,
    plugin_instance_id: Optional[str] = None,
) -> Dict[str, Any]:
    track, drum_plugin = _get_or_create_plugin(
        app, track_id_or_name, "novadaw.drum_machine", plugin_instance_id=plugin_instance_id, insert_at_front=True
    )

    if preset:
        err = _apply_named_preset(drum_plugin, preset)
        if err:
            return err

    if enabled is not None:
        drum_plugin.enabled = bool(enabled)

    if master_volume is not None:
        drum_plugin.master_volume = max(0.0, min(2.0, float(master_volume)))

    if reverb_enabled is not None:
        drum_plugin.reverb.enabled = bool(reverb_enabled)

    if reverb_room_size is not None:
        drum_plugin.reverb.room_size = max(0.0, min(1.0, float(reverb_room_size)))

    if reverb_damping is not None:
        drum_plugin.reverb.damping = max(0.0, min(1.0, float(reverb_damping)))

    if reverb_width is not None:
        drum_plugin.reverb.width = max(0.0, min(1.0, float(reverb_width)))

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
            if pad_decay is not None:
                target_pad.decay = max(0.1, min(2.0, float(pad_decay)))
            if pad_reverb_send is not None:
                target_pad.reverb_send = max(0.0, min(1.0, float(pad_reverb_send)))
            if pad_muted is not None:
                target_pad.muted = bool(pad_muted)
            if pad_soloed is not None:
                target_pad.soloed = bool(pad_soloed)
            drum_plugin.invalidate_cache(target_pad.pad_id)
            drum_plugin.warm_up_cache([target_pad.pad_id], async_bg=True)
    elif any(v is not None for v in (reverb_enabled, reverb_room_size, reverb_damping, reverb_width, reverb_wet_mix)):
        drum_plugin.invalidate_cache()
        drum_plugin.warm_up_cache(async_bg=True)

    _notify_plugin_change(app, track, drum_plugin)

    return {
        "status": "success",
        "track_id": track.id,
        "track_name": track.name,
        "plugin_instance_id": drum_plugin.instance_id,
        "enabled": drum_plugin.enabled,
        "preset_name": drum_plugin.preset_name,
        "available_presets": list(drum_plugin.get_factory_presets().keys()),
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

    if write_automation_live is not None and getattr(track, "track_type", "") != "master":
        if bool(write_automation_live):
            lane = track.add_automation_lane("volume")
            lane.enabled = True
            lane.visible = True
            lane.mode = "write"
            track.automation_expanded = True
        else:
            lane = track.get_automation_lane("volume")
            if lane is not None and lane.mode == "write":
                lane.mode = "read"

    _notify_plugin_change(app, track, glide_plugin)

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

    _notify_plugin_change(app, track, glide_plugin)

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
        "Assigne et règle Nova Piano sur une piste MIDI (preset optionnel, "
        "espaces de réverbération reverb_room : small_room, large_room, concert_hall, cathedral ; "
        "dosage reverb_mix, dureté du marteau hammer_hardness et résonance de la table "
        "soundboard_resonance de 0 à 1, master_volume de 0 à 2, et activation enabled)."
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
    preset: Optional[str] = None,
    plugin_instance_id: Optional[str] = None,
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

    track, piano = _get_or_create_plugin(
        app, track.id, "novadaw.piano", plugin_instance_id=plugin_instance_id, insert_at_front=True
    )
    if preset:
        err = _apply_named_preset(piano, preset)
        if err:
            return err
    parameters.update(reverb_room=room, enabled=enabled)
    updates = {key: value for key, value in parameters.items() if value is not None}
    if updates:
        piano.set_state(updates)
    _notify_plugin_change(app, track, piano)
    return {
        "status": "success",
        "track_id": track.id,
        "track_name": track.name,
        "plugin_instance_id": piano.instance_id,
        "available_presets": list(piano.get_factory_presets().keys()),
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

    _notify_plugin_change(app, track, guitar_plugin)

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

    _notify_plugin_change(app, track, amp_plugin)

    return {
        "status": "success",
        "track_id": track.id,
        "track_name": track.name,
        "plugin_instance_id": amp_plugin.instance_id,
        "preset_name": amp_plugin.preset_name,
        "available_presets": list(amp_plugin.get_factory_presets().keys()),
        "state": amp_plugin.get_state()
    }


@action_registry.register(
    name="novadaw_reorder_track_plugin",
    description="Réordonne un plugin natif dans la pile d'une piste ou du Master (new_index >= 0 ; sur le Master, novadaw.mixer reste toujours en position 0).",
    tags=["plugins", "track"]
)
def reorder_track_plugin(
    app,
    track_id_or_name: str,
    plugin_instance_id: str,
    new_index: int,
) -> Dict[str, Any]:
    track = app.project.get_track(track_id_or_name)
    if not track:
        return {"status": "error", "message": f"Piste '{track_id_or_name}' introuvable."}

    plugin = track.get_plugin(plugin_instance_id)
    if not plugin:
        return {"status": "error", "message": f"Plugin '{plugin_instance_id}' introuvable sur '{track.name}'."}

    cur_idx = track.plugins.index(plugin)
    if getattr(track, "track_type", "") == "master":
        if getattr(plugin, "plugin_type_id", "") == "novadaw.mixer":
            return {"status": "error", "message": "Le plugin Master Mixer doit rester en position 0."}
        target_idx = max(1, min(len(track.plugins) - 1, int(new_index)))
    else:
        target_idx = max(0, min(len(track.plugins) - 1, int(new_index)))

    track.move_plugin(cur_idx, target_idx)
    _notify_plugin_change(app, track, plugin)

    return {
        "status": "success",
        "track_id": track.id,
        "plugin_instance_id": plugin.instance_id,
        "old_index": cur_idx,
        "new_index": track.plugins.index(plugin),
        "stack": [
            {"slot_index": i, "instance_id": p.instance_id, "type_id": p.plugin_type_id, "name": p.name}
            for i, p in enumerate(track.plugins)
        ],
    }


@action_registry.register(
    name="novadaw_list_plugin_presets",
    description="Liste l'ensemble des presets d'usine (Factory) et utilisateur (User) disponibles pour un type de plugin Nova (ex: 'novadaw.synth', 'novadaw.drum_machine', 'novadaw.piano', 'novadaw.guitar', 'novadaw.guitar_amp', 'novadaw.vocal_glide', 'novadaw.equalizer', 'novadaw.compressor').",
    tags=["plugins", "preset"]
)
def list_plugin_presets(
    app,
    plugin_type_id: str,
    track_id_or_name: Optional[str] = None,
) -> Dict[str, Any]:
    from core.preset_manager import global_preset_manager
    ensure_plugins_loaded()

    inst = None
    if track_id_or_name:
        track = app.project.get_track(track_id_or_name)
        if track:
            for p in track.plugins:
                if getattr(p, "plugin_type_id", None) == plugin_type_id:
                    inst = p
                    break
    if inst is None:
        inst = plugin_registry.create_plugin(plugin_type_id)
        if inst is None:
            return {"status": "error", "message": f"Type de plugin inconnu : '{plugin_type_id}'."}

    presets = global_preset_manager.list_presets(plugin_type_id, inst)
    return {
        "status": "success",
        "plugin_type_id": plugin_type_id,
        "count": len(presets),
        "presets": [dict(p) for p in presets],
    }


@action_registry.register(
    name="novadaw_save_plugin_preset",
    description="Sauvegarde les réglages actuels d'un plugin d'une piste en tant que preset utilisateur persistant (.json), sans jamais écraser les presets d'usine protégés.",
    tags=["plugins", "preset"]
)
def save_plugin_preset(
    app,
    track_id_or_name: str,
    preset_name: str,
    plugin_instance_id: Optional[str] = None,
    plugin_type_id: Optional[str] = None,
    description: str = "",
) -> Dict[str, Any]:
    from core.preset_manager import global_preset_manager
    track = app.project.get_track(track_id_or_name)
    if not track:
        return {"status": "error", "message": f"Piste '{track_id_or_name}' introuvable."}

    plugin = None
    if plugin_instance_id:
        plugin = track.get_plugin(plugin_instance_id)
    elif plugin_type_id:
        for p in track.plugins:
            if getattr(p, "plugin_type_id", None) == plugin_type_id:
                plugin = p
                break
    elif track.plugins:
        non_mixer = [p for p in track.plugins if getattr(p, "plugin_type_id", "") != "novadaw.mixer"]
        plugin = non_mixer[0] if non_mixer else track.plugins[0]

    if not plugin:
        return {"status": "error", "message": f"Aucun plugin correspondant trouvé sur la piste '{track.name}'."}

    clean_name = preset_name.strip()
    if not clean_name:
        return {"status": "error", "message": "Le nom du preset ne peut pas être vide."}

    try:
        saved_path = global_preset_manager.save_preset(
            plugin, clean_name, author="MCP AI", description=description
        )
    except ValueError as ve:
        return {"status": "error", "message": str(ve)}

    try:
        from ui.plugin_dialogs import sync_open_native_editor
        sync_open_native_editor(plugin)
    except Exception:
        pass

    return {
        "status": "success",
        "track_id": track.id,
        "plugin_instance_id": plugin.instance_id,
        "plugin_type_id": plugin.plugin_type_id,
        "preset_name": clean_name,
        "file_path": saved_path,
    }


@action_registry.register(
    name="novadaw_delete_plugin_preset",
    description="Supprime un preset utilisateur sur disque (refuse systématiquement la suppression des presets d'usine NovaDAW).",
    tags=["plugins", "preset"]
)
def delete_plugin_preset(app, plugin_type_id: str, preset_name: str) -> Dict[str, Any]:
    from core.preset_manager import global_preset_manager
    ensure_plugins_loaded()
    if global_preset_manager.is_factory_preset(plugin_type_id, preset_name):
        return {"status": "error", "message": f"Le preset d'usine '{preset_name}' est protégé et ne peut pas être supprimé."}
    deleted = global_preset_manager.delete_preset(plugin_type_id, preset_name)
    if not deleted:
        return {"status": "error", "message": f"Preset utilisateur '{preset_name}' introuvable pour '{plugin_type_id}'."}
    return {"status": "success", "plugin_type_id": plugin_type_id, "deleted_preset": preset_name}


@action_registry.register(
    name="novadaw_list_external_vst_plugins",
    description="Liste les plugins externes VST3 / VST2 découverts sur le système par le scanner de NovaDAW.",
    tags=["plugins", "vst"]
)
def list_external_vst_plugins(
    app,
    compatible_only: bool = False,
    plugin_type: Optional[str] = None,
) -> Dict[str, Any]:
    from core.plugin_manager import global_plugin_manager
    type_filter = plugin_type.strip().lower() if plugin_type else None
    items = []
    for p in getattr(global_plugin_manager, "plugins", []):
        is_comp = bool(getattr(p, "is_compatible", True))
        if compatible_only and not is_comp:
            continue
        p_type = str(getattr(p, "plugin_type", ""))
        if type_filter and type_filter not in p_type.lower():
            continue
        items.append({
            "name": getattr(p, "name", ""),
            "file_path": getattr(p, "file_path", ""),
            "format": getattr(p, "format", ""),
            "plugin_type": p_type,
            "vendor": getattr(p, "vendor", ""),
            "is_compatible": is_comp,
            "status": getattr(p, "status", ""),
        })
    return {"status": "success", "count": len(items), "external_plugins": items}


@action_registry.register(
    name="novadaw_get_plugin_rack",
    description="Retourne le catalogue complet du Rack de plugins du projet actif (instruments et effets internes ou VST montés dans le projet).",
    tags=["plugins", "rack"]
)
def get_plugin_rack(app) -> Dict[str, Any]:
    if hasattr(app.project, "sync_rack_plugins"):
        app.project.sync_rack_plugins()
    rack = list(getattr(app.project, "plugin_rack", []))
    return {"status": "success", "count": len(rack), "plugin_rack": rack}


@action_registry.register(
    name="novadaw_assign_vst_plugin",
    description="Assigne un plugin externe (.vst3 / .dll) comme instrument d'une piste MIDI (role='instrument') ou comme effet d'insert d'une piste / du Master (role='effect').",
    tags=["plugins", "vst", "track"]
)
def assign_vst_plugin(
    app,
    track_id_or_name: str,
    plugin_path: str,
    role: str = "auto",
) -> Dict[str, Any]:
    import os
    from core.plugin_manager import global_plugin_manager

    track = app.project.get_track(track_id_or_name)
    if not track:
        return {"status": "error", "message": f"Piste '{track_id_or_name}' introuvable."}

    clean_path = os.path.abspath(os.path.expanduser(plugin_path.strip()))
    if not os.path.exists(clean_path):
        return {"status": "error", "message": f"Fichier ou bundle VST introuvable : '{plugin_path}'."}

    p_name = os.path.splitext(os.path.basename(clean_path))[0]
    detected_role = role.strip().lower()
    if detected_role not in ("auto", "instrument", "effect"):
        return {"status": "error", "message": "role doit être 'auto', 'instrument' ou 'effect'."}

    for info in getattr(global_plugin_manager, "plugins", []):
        if os.path.normcase(getattr(info, "file_path", "")) == os.path.normcase(clean_path):
            p_name = getattr(info, "name", p_name) or p_name
            if detected_role == "auto":
                detected_role = "instrument" if "inst" in str(getattr(info, "plugin_type", "")).lower() else "effect"
            break

    if detected_role == "auto":
        detected_role = "instrument" if track.track_type == "midi" else "effect"

    if detected_role == "instrument":
        if track.track_type != "midi":
            return {"status": "error", "message": "Un instrument VST ne peut être assigné qu'à une piste MIDI."}
        track.plugin_path = clean_path
        track.plugin_name = p_name
        if hasattr(app.project, "add_rack_plugin"):
            app.project.add_rack_plugin(clean_path, p_name, "instrument")
    else:
        if clean_path not in track.insert_effects:
            track.insert_effects.append(clean_path)
        if hasattr(app.project, "add_rack_plugin"):
            app.project.add_rack_plugin(clean_path, p_name, "effect")

    _notify_plugin_change(app, track)
    return {
        "status": "success",
        "track_id": track.id,
        "track_name": track.name,
        "role": detected_role,
        "plugin_name": p_name,
        "plugin_path": clean_path,
        "insert_effects": list(track.insert_effects),
    }


@action_registry.register(
    name="novadaw_remove_vst_plugin",
    description="Désassigne l'instrument VST externe ou retire un effet d'insert VST externe d'une piste ou du Master.",
    tags=["plugins", "vst", "track"]
)
def remove_vst_plugin(
    app,
    track_id_or_name: str,
    plugin_path: Optional[str] = None,
) -> Dict[str, Any]:
    import os
    track = app.project.get_track(track_id_or_name)
    if not track:
        return {"status": "error", "message": f"Piste '{track_id_or_name}' introuvable."}

    removed_instrument = False
    removed_effects = []

    if plugin_path:
        norm_target = os.path.normcase(os.path.abspath(os.path.expanduser(plugin_path.strip())))
        if track.plugin_path and os.path.normcase(os.path.abspath(track.plugin_path)) == norm_target:
            track.plugin_path = None
            track.plugin_name = None
            removed_instrument = True
        kept = []
        for fx in track.insert_effects:
            if os.path.normcase(os.path.abspath(fx)) == norm_target:
                removed_effects.append(fx)
            else:
                kept.append(fx)
        track.insert_effects = kept
    else:
        if track.plugin_path and not str(track.plugin_path).startswith("novadaw."):
            track.plugin_path = None
            track.plugin_name = None
            removed_instrument = True
        removed_effects = list(track.insert_effects)
        track.insert_effects.clear()

    _notify_plugin_change(app, track)
    return {
        "status": "success",
        "track_id": track.id,
        "removed_instrument": removed_instrument,
        "removed_effects": removed_effects,
    }

