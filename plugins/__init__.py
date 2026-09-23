"""
plugins - Système modulaire de plugins pour NovaDAW
Permet l'ajout d'effets natifs (Égaliseur, Compresseur, Mixeur) et VST3
empilables sur chaque piste et sur le Master.
"""
from plugins.base import BasePlugin
from plugins.registry import plugin_registry, register_plugin

__all__ = ["BasePlugin", "plugin_registry", "register_plugin"]
