"""
plugins/base.py - Classe de base pour l'architecture de plugins de NovaDAW.

Définit l'interface commune que tout plugin (interne ou externe) doit implémenter :
- Traitement DSP du signal audio (process)
- Gestion des paramètres et sérialisation de l'état (get_state / set_state)
- Interface utilisateur dédiée (create_editor)
- Bypass et réinitialisation (reset)
"""
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List, Callable
import numpy as np


@dataclass
class AutomationProcessContext:
    """
    Contexte temporel et d'automatisation transmis aux plugins lors du traitement audio.
    Permet à un plugin (ex: Nova Vocal Glide) de lire ou écrire des points d'automatisation
    sur sa piste hôte sans dépendre de l'interface graphique.
    """
    track_id: str
    start_beat: float
    end_beat: float
    bpm: float
    sample_rate: int
    is_playing: bool = False
    automation_sink: Optional[Callable[[str, str, float, float, str], None]] = None
    read_automation: Optional[Callable[[str, float], Optional[float]]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def emit_automation(
        self,
        target_param: str,
        beat: float,
        value: float,
        plugin_id: Optional[str] = None,
    ) -> None:
        """Émet un point d'automatisation vers le collecteur du moteur audio."""
        if self.automation_sink is not None:
            self.automation_sink(
                self.track_id,
                target_param,
                float(beat),
                float(value),
                plugin_id or "",
            )


class BasePlugin(ABC):
    """
    Classe de base abstraite pour tous les plugins NovaDAW.
    Chaque instance de plugin possède un identifiant unique, un nom,
    un état d'activation (bypass) et gère son propre DSP et interface graphique.
    """

    def __init__(
        self,
        plugin_type_id: str,
        name: str = "Plugin",
        category: str = "effect",
        icon: str = "🎛️",
        instance_id: Optional[str] = None
    ):
        self.instance_id: str = instance_id or str(uuid.uuid4())[:8]
        self.plugin_type_id: str = plugin_type_id
        self.name: str = name
        self.category: str = category  # "eq", "dynamics", "mixer", "utility", etc.
        self.icon: str = icon
        self.enabled: bool = True
        self.description: str = ""
        self.supports_automation_write: bool = False
        self.automatable_outputs: List[str] = []

    @abstractmethod
    def process(self, audio: np.ndarray, sample_rate: int) -> np.ndarray:
        """
        Traite un buffer audio stéréo.
        audio: ndarray de forme (N, 2) ou (N,) en float32
        sample_rate: fréquence d'échantillonnage en Hz (ex: 44100)
        Retourne: ndarray (N, 2) en float32
        """
        pass

    def process_with_context(
        self,
        audio: np.ndarray,
        sample_rate: int,
        context: Optional[AutomationProcessContext] = None,
    ) -> np.ndarray:
        """
        Point d'entrée étendu avec contexte temporel et bus d'automatisation.
        Par défaut, délègue à `process(audio, sample_rate)` pour préserver la compatibilité.
        """
        return self.process(audio, sample_rate)

    @abstractmethod
    def get_state(self) -> Dict[str, Any]:
        """
        Retourne un dictionnaire sérialisable en JSON représentant
        l'état complet et les paramètres du plugin.
        """
        pass

    @abstractmethod
    def set_state(self, state: Dict[str, Any]) -> None:
        """
        Restaure l'état et les paramètres du plugin depuis un dictionnaire.
        """
        pass

    def get_parameter(self, name: str) -> Any:
        """Récupère la valeur d'un paramètre individuel"""
        return self.get_state().get(name)

    def set_parameter(self, name: str, value: Any) -> None:
        """Modifie la valeur d'un paramètre individuel"""
        current_state = self.get_state()
        current_state[name] = value
        self.set_state(current_state)

    def reset(self) -> None:
        """
        Réinitialise les états internes (filtres, mémoires de délai,
        enveloppes, compteurs) lors d'un arrêt ou repositionnement de lecture.
        """
        pass

    def create_editor(self, parent=None):
        """
        Instancie et retourne le widget graphique PySide6 pour éditer ce plugin.
        Retourne None si le plugin n'a pas d'interface graphique dédiée.
        """
        return None

    def to_dict(self) -> Dict[str, Any]:
        """Sérialisation complète pour sauvegarde dans le fichier de projet .ndaw"""
        return {
            "instance_id": self.instance_id,
            "plugin_type_id": self.plugin_type_id,
            "name": self.name,
            "category": self.category,
            "enabled": self.enabled,
            "state": self.get_state(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BasePlugin":
        """Instancie et restaure un plugin à partir de ses données sérialisées"""
        instance = cls(
            plugin_type_id=data.get("plugin_type_id", ""),
            name=data.get("name", "Plugin"),
            instance_id=data.get("instance_id")
        )
        instance.enabled = data.get("enabled", True)
        if "state" in data:
            instance.set_state(data["state"])
        return instance


def resolve_plugin_track(plugin: Any, widget: Optional[Any] = None, engine: Optional[Any] = None) -> Optional[Any]:
    """Retrouve la piste hébergeant un plugin (via AudioEngine ou la fenêtre hôte NativePluginDialog)."""
    if engine is not None and hasattr(engine, "get_track_for_plugin"):
        track = engine.get_track_for_plugin(plugin)
        if track is not None:
            return track
    if widget is not None:
        win = widget.window() if hasattr(widget, "window") else None
        if win is not None and getattr(win, "track", None) is not None:
            return win.track
        cur = widget
        while cur is not None:
            trk = getattr(cur, "track", None) or getattr(cur, "current_track", None)
            if trk is not None:
                return trk
            cur = cur.parent() if hasattr(cur, "parent") and callable(cur.parent) else None
    return None


def play_plugin_audition(
    plugin: Any,
    wave: np.ndarray,
    widget: Optional[Any] = None,
    choke_group: int = 0,
    fallback_player: Optional[Callable[[np.ndarray], None]] = None,
) -> bool:
    """
    Joue un son d'audition depuis l'interface d'un plugin en passant par le moteur audio
    et la tranche de mixeur de sa piste (volume, pan, mute/solo, effets, VU-mètre).
    """
    if wave is None or len(wave) == 0:
        return False

    engine = None
    try:
        from core.audio_engine import get_global_audio_engine
        engine = get_global_audio_engine()
    except Exception:
        engine = None

    track = resolve_plugin_track(plugin, widget=widget, engine=engine)

    if engine is not None and hasattr(engine, "play_preview_buffer"):
        if engine.play_preview_buffer(wave, choke_group=choke_group, track=track):
            return True

    if fallback_player is None:
        return False

    out_wave = wave.copy()
    if out_wave.ndim == 1:
        out_wave = np.column_stack((out_wave, out_wave)).astype(np.float32)
    else:
        out_wave = np.ascontiguousarray(out_wave[:, :2], dtype=np.float32)

    mixer_p = engine._get_active_mixer_plugin() if (engine is not None and hasattr(engine, "_get_active_mixer_plugin")) else None
    if track is not None:
        if engine is not None and hasattr(engine, "_process_track_effects_isolated"):
            try:
                out_wave = engine._process_track_effects_isolated(track, out_wave, plugin)
            except Exception:
                pass
        has_solo = engine._has_any_solo() if (engine is not None and hasattr(engine, "_has_any_solo")) else False
        if getattr(track, "muted", False) or (has_solo and not getattr(track, "soloed", False)):
            if mixer_p is not None:
                mixer_p.update_track_peak(track.id, 0.0, 0.0)
            return True
        vol = max(0.0, float(getattr(track, "volume", 0.8)))
        pan = max(-1.0, min(1.0, float(getattr(track, "pan", 0.0))))
        out_wave[:, 0] *= vol * (1.0 - max(0.0, pan))
        out_wave[:, 1] *= vol * (1.0 + min(0.0, pan))

        if mixer_p is not None and len(out_wave) > 0:
            pk_l = float(np.max(np.abs(out_wave[:, 0])))
            pk_r = float(np.max(np.abs(out_wave[:, 1])))
            mixer_p.update_track_peak(track.id, pk_l, pk_r)

    if mixer_p is not None and getattr(mixer_p, "enabled", True):
        out_wave = mixer_p.process(out_wave, int(getattr(engine, "sample_rate", 44100)))

    fallback_player(out_wave)
    return True
