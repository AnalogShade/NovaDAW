"""
plugins/registry.py - Registre central des plugins pour NovaDAW.

Permet l'enregistrement, l'énumération et l'instanciation dynamique
de tous les types de plugins installés dans NovaDAW.
"""
from typing import Dict, Type, Optional, List, Any
from plugins.base import BasePlugin


class PluginRegistry:
    """Registre singleton pour les classes de plugins NovaDAW"""

    def __init__(self):
        self._registry: Dict[str, Dict[str, Any]] = {}

    def register(
        self,
        plugin_type_id: str,
        plugin_class: Type[BasePlugin],
        name: str,
        category: str = "effect",
        icon: str = "🎛️",
        description: str = "",
        is_default: bool = False
    ):
        """Enregistre une classe de plugin dans le registre"""
        self._registry[plugin_type_id] = {
            "type_id": plugin_type_id,
            "class": plugin_class,
            "name": name,
            "category": category,
            "icon": icon,
            "description": description,
            "is_default": is_default
        }

    def get_plugin_info(self, plugin_type_id: str) -> Optional[Dict[str, Any]]:
        return self._registry.get(plugin_type_id)

    def get_available_plugins(self, category: Optional[str] = None) -> List[Dict[str, Any]]:
        """Retourne la liste de métadonnées des plugins enregistrés"""
        plugins = []
        for info in self._registry.values():
            if category is None or info["category"] == category:
                plugins.append({
                    "type_id": info["type_id"],
                    "name": info["name"],
                    "category": info["category"],
                    "icon": info["icon"],
                    "description": info["description"],
                    "is_default": info["is_default"]
                })
        return plugins

    def create_plugin(self, plugin_type_id: str, **kwargs) -> Optional[BasePlugin]:
        """Crée une nouvelle instance du plugin demandé"""
        info = self._registry.get(plugin_type_id)
        if not info:
            print(f"[PluginRegistry] Type de plugin inconnu : {plugin_type_id}")
            return None
        plugin_class = info["class"]
        return plugin_class(**kwargs)

    def create_from_dict(self, data: Dict[str, Any]) -> Optional[BasePlugin]:
        """Désérialise et recrée un plugin à partir de son dictionnaire de sauvegarde"""
        type_id = data.get("plugin_type_id")
        if not type_id:
            return None
        info = self._registry.get(type_id)
        if not info:
            print(f"[PluginRegistry] Type de plugin inconnu pour désérialisation : {type_id}")
            return None
        plugin_class = info["class"]
        instance = plugin_class(
            instance_id=data.get("instance_id")
        )
        instance.enabled = data.get("enabled", True)
        if "state" in data:
            instance.set_state(data["state"])
        return instance


plugin_registry = PluginRegistry()


def register_plugin(
    plugin_type_id: str,
    name: str,
    category: str = "effect",
    icon: str = "🎛️",
    description: str = "",
    is_default: bool = False
):
    """Décorateur pour enregistrer automatiquement une classe de plugin"""
    def decorator(cls: Type[BasePlugin]):
        plugin_registry.register(
            plugin_type_id=plugin_type_id,
            plugin_class=cls,
            name=name,
            category=category,
            icon=icon,
            description=description,
            is_default=is_default
        )
        return cls
    return decorator


def ensure_plugins_loaded():
    """Charge et enregistre tous les plugins par défaut intégrés"""
    try:
        import plugins.equalizer.equalizer_plugin
    except ImportError as e:
        print(f"[PluginRegistry] Erreur chargement Equalizer: {e}")

    try:
        import plugins.compressor.compressor_plugin
    except ImportError as e:
        print(f"[PluginRegistry] Erreur chargement Compressor: {e}")

    try:
        import plugins.mixer.mixer_plugin
    except ImportError as e:
        print(f"[PluginRegistry] Erreur chargement Mixer: {e}")

    try:
        import plugins.drum_machine.drum_plugin
    except ImportError as e:
        print(f"[PluginRegistry] Erreur chargement DrumMachine: {e}")

    try:
        import plugins.synth.synth_plugin
    except ImportError as e:
        print(f"[PluginRegistry] Erreur chargement NovaSynth: {e}")
