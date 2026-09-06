"""
core/plugin_manager.py - Gestionnaire et scanner dynamique de plugins VST3
Détection multiplateforme (zéro chemin codé en dur), validation de compatibilité,
mise en cache JSON et support des instruments et effets.
"""
import os
import sys
import json
from dataclasses import dataclass, asdict
from typing import List, Optional, Dict, Any

from PySide6.QtCore import QObject, Signal, QThread


@dataclass
class PluginInfo:
    name: str
    file_path: str
    plugin_type: str  # "instrument", "effect", "unknown"
    is_compatible: bool
    error_message: Optional[str] = None
    parameters_count: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "PluginInfo":
        return cls(
            name=data.get("name", "Plugin"),
            file_path=data.get("file_path", ""),
            plugin_type=data.get("plugin_type", "unknown"),
            is_compatible=data.get("is_compatible", False),
            error_message=data.get("error_message"),
            parameters_count=data.get("parameters_count", 0),
        )


def get_default_vst3_directories() -> List[str]:
    """
    Retourne dynamiquement les répertoires standards de plugins VST3 pour le système
    d'exploitation actuel, sans AUCUN chemin spécifique codé en dur pour un utilisateur.
    """
    dirs: List[str] = []

    if sys.platform == "win32":
        # 1. Standard Windows 64-bit Common Files VST3
        common_files = os.environ.get("COMMONPROGRAMFILES", r"C:\Program Files\Common Files")
        std_vst3 = os.path.join(common_files, "VST3")
        if os.path.isdir(std_vst3) and std_vst3 not in dirs:
            dirs.append(std_vst3)

        # 2. Dossier Local de l'utilisateur (AppData\Local\Programs\Common\VST3)
        local_appdata = os.environ.get("LOCALAPPDATA")
        if local_appdata:
            user_vst3 = os.path.join(local_appdata, "Programs", "Common", "VST3")
            if os.path.isdir(user_vst3) and user_vst3 not in dirs:
                dirs.append(user_vst3)

        # 3. Dossier Steinberg standard
        prog_files = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        steinberg_dir = os.path.join(prog_files, "Steinberg")
        if os.path.isdir(steinberg_dir):
            for root, subdirs, _ in os.walk(steinberg_dir):
                if os.path.basename(root).lower() == "vst3" and root not in dirs:
                    dirs.append(root)

        # 4. Standard 32-bit (si présent sur système 64-bit)
        common_x86 = os.environ.get("COMMONPROGRAMFILES(X86)")
        if common_x86:
            std_x86 = os.path.join(common_x86, "VST3")
            if os.path.isdir(std_x86) and std_x86 not in dirs:
                dirs.append(std_x86)

    elif sys.platform == "darwin":  # macOS
        for path in ["/Library/Audio/Plug-Ins/VST3", os.path.expanduser("~/Library/Audio/Plug-Ins/VST3")]:
            if os.path.isdir(path) and path not in dirs:
                dirs.append(path)

    else:  # Linux
        for path in ["/usr/lib/vst3", "/usr/local/lib/vst3", os.path.expanduser("~/.vst3")]:
            if os.path.isdir(path) and path not in dirs:
                dirs.append(path)

    return dirs


class PluginScanWorker(QThread):
    """Worker en arrière-plan pour scanner les dossiers de plugins sans figer l'interface"""
    progress = Signal(int, int, str)  # index, total, plugin_name
    plugin_found = Signal(object)      # PluginInfo
    finished_scan = Signal(list)       # List[PluginInfo]

    def __init__(self, directories: List[str], parent=None):
        super().__init__(parent)
        self.directories = directories

    def run(self):
        try:
            import pedalboard
            has_pedalboard = True
        except ImportError:
            has_pedalboard = False

        # 1. Recherche récursive de tous les fichiers .vst3
        found_files = []
        for directory in self.directories:
            if not os.path.isdir(directory):
                continue
            for root, dirs, files in os.walk(directory):
                # Sous Windows, un plugin peut être un fichier .vst3 ou un dossier .vst3 bundle
                for d in list(dirs):
                    if d.lower().endswith(".vst3"):
                        full_bundle = os.path.join(root, d)
                        found_files.append(full_bundle)
                        dirs.remove(d)  # Ne pas descendre plus bas dans le bundle
                for f in files:
                    if f.lower().endswith(".vst3"):
                        found_files.append(os.path.join(root, f))

        found_files = sorted(list(set(found_files)))
        total = len(found_files)
        results: List[PluginInfo] = []

        for idx, file_path in enumerate(found_files):
            base_name = os.path.splitext(os.path.basename(file_path))[0]
            self.progress.emit(idx + 1, total, base_name)

            if not has_pedalboard:
                info = PluginInfo(
                    name=base_name,
                    file_path=file_path,
                    plugin_type="unknown",
                    is_compatible=False,
                    error_message="Bibliothèque pedalboard non installée"
                )
            else:
                info = PluginManager.probe_plugin(file_path)

            results.append(info)
            self.plugin_found.emit(info)

        self.finished_scan.emit(results)


class PluginManager(QObject):
    """
    Gestionnaire central des plugins VST3 pour NovaDAW.
    Gère le stockage des chemins personnalisés, le cache et les instances de plugins.
    """
    scan_updated = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.config_dir = os.path.join(os.path.expanduser("~"), ".novadaw")
        os.makedirs(self.config_dir, exist_ok=True)
        self.settings_file = os.path.join(self.config_dir, "plugin_settings.json")
        self.cache_file = os.path.join(self.config_dir, "plugins_cache.json")

        self.custom_directories: List[str] = []
        self.plugins: List[PluginInfo] = []
        self.active_instances: Dict[str, Any] = {}  # file_path -> pedalboard plugin instance

        self.load_settings()
        self.load_cache()

    def get_all_directories(self) -> List[str]:
        """Combine répertoires standards du système et dossiers personnalisés de l'utilisateur"""
        all_dirs = list(get_default_vst3_directories())
        for cd in self.custom_directories:
            if cd not in all_dirs and os.path.isdir(cd):
                all_dirs.append(cd)
        return all_dirs

    def add_custom_directory(self, path: str) -> bool:
        if os.path.isdir(path) and path not in self.custom_directories:
            self.custom_directories.append(path)
            self.save_settings()
            return True
        return False

    def remove_custom_directory(self, path: str):
        if path in self.custom_directories:
            self.custom_directories.remove(path)
            self.save_settings()

    def load_settings(self):
        if os.path.exists(self.settings_file):
            try:
                with open(self.settings_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.custom_directories = data.get("custom_directories", [])
            except Exception as e:
                print(f"[PluginManager] Erreur lecture paramètres : {e}")

    def save_settings(self):
        try:
            with open(self.settings_file, "w", encoding="utf-8") as f:
                json.dump({"custom_directories": self.custom_directories}, f, indent=2)
        except Exception as e:
            print(f"[PluginManager] Erreur sauvegarde paramètres : {e}")

    def load_cache(self):
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.plugins = [PluginInfo.from_dict(d) for d in data]
            except Exception as e:
                print(f"[PluginManager] Erreur lecture cache : {e}")

    def save_cache(self):
        try:
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump([p.to_dict() for p in self.plugins], f, indent=2)
        except Exception as e:
            print(f"[PluginManager] Erreur sauvegarde cache : {e}")

    @staticmethod
    def probe_plugin(file_path: str) -> PluginInfo:
        """
        Teste la compatibilité d'un plugin VST3 avec isolation des erreurs.
        Ne fait JAMAIS planter l'application en cas de crash ou d'incompatibilité.
        """
        base_name = os.path.splitext(os.path.basename(file_path))[0]
        try:
            import pedalboard
            # Tentative de chargement du plugin
            plugin = pedalboard.load_plugin(file_path)
            name = getattr(plugin, "name", None) or base_name
            is_inst = getattr(plugin, "is_instrument", False)
            is_fx = getattr(plugin, "is_effect", False)

            p_type = "instrument" if is_inst else ("effect" if is_fx else "unknown")
            param_count = len(getattr(plugin, "parameters", {}))

            return PluginInfo(
                name=name,
                file_path=file_path,
                plugin_type=p_type,
                is_compatible=True,
                parameters_count=param_count
            )
        except Exception as e:
            err = str(e)
            return PluginInfo(
                name=base_name,
                file_path=file_path,
                plugin_type="unknown",
                is_compatible=False,
                error_message=err
            )

    def add_plugin_file(self, file_path: str) -> PluginInfo:
        """Ajoute manuellement un plugin VST3 sélectionné par l'utilisateur"""
        info = self.probe_plugin(file_path)
        # Remplacer si déjà présent, sinon ajouter
        self.plugins = [p for p in self.plugins if p.file_path != file_path]
        self.plugins.append(info)
        self.save_cache()
        self.scan_updated.emit()
        return info

    def get_compatible_instruments(self) -> List[PluginInfo]:
        """Retourne uniquement les instruments VSTi compatibles"""
        return [p for p in self.plugins if p.is_compatible and p.plugin_type == "instrument"]

    def get_compatible_effects(self) -> List[PluginInfo]:
        """Retourne uniquement les effets VST FX compatibles"""
        return [p for p in self.plugins if p.is_compatible and p.plugin_type == "effect"]

    def get_or_load_plugin(self, file_path: str) -> Optional[Any]:
        """Charge ou récupère une instance en cache d'un plugin VST3"""
        if file_path in self.active_instances:
            return self.active_instances[file_path]

        if not os.path.exists(file_path):
            return None

        try:
            import pedalboard
            plugin = pedalboard.load_plugin(file_path)
            self.active_instances[file_path] = plugin
            return plugin
        except Exception as e:
            print(f"[PluginManager] Impossible d'instancier {file_path}: {e}")
            return None


# Instance globale partagée
global_plugin_manager = PluginManager()
