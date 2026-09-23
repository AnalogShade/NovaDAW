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
from typing import Dict, Any, Optional
import numpy as np


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

    @abstractmethod
    def process(self, audio: np.ndarray, sample_rate: int) -> np.ndarray:
        """
        Traite un buffer audio stéréo.
        audio: ndarray de forme (N, 2) ou (N,) en float32
        sample_rate: fréquence d'échantillonnage en Hz (ex: 44100)
        Retourne: ndarray (N, 2) en float32
        """
        pass

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
