"""
core/actions/plugins.py - Actions de gestion de la pile de plugins pour NovaDAW et le protocole MCP.
"""
from typing import Dict, Any, List, Optional
from core.action_registry import action_registry
from plugins.registry import plugin_registry, ensure_plugins_loaded


@action_registry.register(
    name="novadaw_get_available_plugins",
    description="Retourne la liste des plugins audio disponibles dans NovaDAW (Égaliseur, Compresseur, Mixeur).",
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
    description="Ajoute un plugin (novadaw.equalizer, novadaw.compressor, novadaw.mixer) à la pile d'effets d'une piste ou du Master.",
    tags=["plugins", "track"]
)
def add_plugin_to_track(app, track_id: str, plugin_type_id: str) -> Dict[str, Any]:
    ensure_plugins_loaded()
    track = app.project.get_track(track_id)
    if not track:
        return {"success": False, "error": f"Piste non trouvée : '{track_id}'"}

    plugin = plugin_registry.create_plugin(plugin_type_id)
    if not plugin:
        return {"success": False, "error": f"Type de plugin inconnu : '{plugin_type_id}'"}

    if hasattr(plugin, "set_project"):
        plugin.set_project(app.project)

    track.add_plugin(plugin)
    app.refresh_project_ui()

    return {
        "success": True,
        "track_id": track.id,
        "track_name": track.name,
        "plugin_instance_id": plugin.instance_id,
        "plugin_name": plugin.name,
        "stack_position": len(track.plugins) - 1
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
        app.refresh_project_ui()
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
    app.refresh_project_ui()
    return {
        "success": True,
        "track_id": track.id,
        "plugin_instance_id": plugin.instance_id,
        "plugin_name": plugin.name,
        "enabled": plugin.enabled
    }


@action_registry.register(
    name="novadaw_configure_plugin",
    description="Modifie les paramètres d'une instance de plugin (ex: seuil, ratio, bandes d'égaliseur).",
    tags=["plugins", "track"]
)
def configure_plugin(app, track_id: str, plugin_instance_id: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
    track = app.project.get_track(track_id)
    if not track:
        return {"success": False, "error": f"Piste non trouvée : '{track_id}'"}

    plugin = track.get_plugin(plugin_instance_id)
    if not plugin:
        return {"success": False, "error": f"Plugin '{plugin_instance_id}' non trouvé sur la piste '{track.name}'"}

    for key, value in parameters.items():
        plugin.set_parameter(key, value)

    app.refresh_project_ui()
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
        if hasattr(app, "transport_bar") and hasattr(app.transport_bar, "slider_vol"):
            app.transport_bar.slider_vol.setValue(int(master_t.volume * 100))

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
    pad_reverb_send: Optional[float] = None
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
            if track.track_type == "midi":
                track.plugin_path = "novadaw.drum_machine"
                track.plugin_name = "Nova Drums VSTi"

    if not drum_plugin:
        return {"status": "error", "message": "Impossible d'instancier Nova Drums VSTi."}

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
            if pad_volume is not None:
                target_pad.volume = max(0.0, min(2.0, float(pad_volume)))
            if pad_pan is not None:
                target_pad.pan = max(-1.0, min(1.0, float(pad_pan)))
            if pad_tune is not None:
                target_pad.tune = max(-12.0, min(12.0, float(pad_tune)))
            if pad_reverb_send is not None:
                target_pad.reverb_send = max(0.0, min(1.0, float(pad_reverb_send)))

    if hasattr(app, "refresh_project_ui"):
        app.refresh_project_ui()

    return {
        "status": "success",
        "track_id": track.id,
        "track_name": track.name,
        "plugin_instance_id": drum_plugin.instance_id,
        "state": drum_plugin.get_state()
    }

