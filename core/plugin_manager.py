"""
core/plugin_manager.py - Gestionnaire et scanner universel de plugins VST3 & VST2 (64-bit & 32-bit)
Détection multiplateforme étendue (Windows Registry, tous les disques, Steinberg, Common Files, ProgramData),
hôte natif VST3 et VST 2.4 (.dll) avec isolation des crashs, mise en cache JSON instantanée,
scan incrémental rapide et surveillance automatique en arrière-plan sans ralentir le démarrage.
"""
import os
import sys
import json
import string
import threading
import time
from dataclasses import dataclass, asdict
from typing import List, Optional, Dict, Any, Set

from PySide6.QtCore import QObject, Signal, QThread, QFileSystemWatcher, QTimer
from core.vst_host import IsolatedPlugin, _get_pe_machine


@dataclass
class PluginInfo:
    name: str
    file_path: str
    plugin_type: str  # "instrument", "effect", "unknown"
    is_compatible: bool
    error_message: Optional[str] = None
    parameters_count: int = 0
    format: str = "vst3"  # "vst3", "vst2", "native"
    file_mtime: float = 0.0
    file_size: int = 0

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
            format=data.get("format", "vst3"),
            file_mtime=float(data.get("file_mtime", 0.0)),
            file_size=int(data.get("file_size", 0)),
        )


def is_vst2_dll(file_path: str) -> bool:
    """
    Vérifie si une bibliothèque DLL Windows est un plugin audio VST2 sans l'exécuter.
    Inspecte la table d'exportation PE pour la présence de 'main' ou 'VSTPluginMain'.
    """
    if not file_path.lower().endswith(".dll") or not os.path.isfile(file_path):
        return False
    base_lower = os.path.basename(file_path).lower()
    if base_lower.startswith("waveshell"):
        return False
    try:
        import pefile
        pe = pefile.PE(file_path, fast_load=True)
        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_EXPORT']])
        if hasattr(pe, 'DIRECTORY_ENTRY_EXPORT') and pe.DIRECTORY_ENTRY_EXPORT:
            for exp in pe.DIRECTORY_ENTRY_EXPORT.symbols:
                if exp.name and exp.name.lower() in (b"main", b"vstpluginmain"):
                    return True
    except Exception:
        try:
            with open(file_path, "rb") as f:
                head = f.read(1024 * 1024)
                if b"VSTPluginMain" in head or b"VstPluginMain" in head:
                    return True
        except Exception:
            pass
    return False


_SKIP_DIR_KEYWORDS = (
    "$recycle.bin",
    "system volume information",
    "windows",
    "windowsapps",
    "microsoft",
    "nvidia",
    "videolan",
    "ultimaker",
    "makehuman",
    "blender foundation",
    "freecad",
    "godot",
    "unity",
    "steam",
    "epic games",
    "battle.net",
    "diablo",
    "overwatch",
    "warcraft",
    "hearthstone",
    "adobe",
    "google",
    "jetbrains",
    "git",
    "nodejs",
    "dotnet",
    "waves central",
    "waveshells",
    "elicenser",
    "syncrosoft",
)


def _should_skip_directory(dir_path: str) -> bool:
    """Filtre les dossiers système, moteurs de jeux ou dossiers privés verrouillés (ex: Cubase interne)."""
    norm = os.path.normpath(dir_path).lower()
    parts = norm.split(os.sep)
    if any(p in _SKIP_DIR_KEYWORDS for p in parts):
        return True
    # Les sous-dossiers VSTPlugins/Components internes à l'installation de Cubase sont verrouillés par eLicenser Cubase
    if "steinberg" in norm and "cubase" in norm and ("vstplugins" in norm or "components" in norm):
        return True
    return False


def _discover_windows_registry_plugin_dirs() -> List[str]:
    """
    Interroge dynamiquement le Registre Windows (HKLM & HKCU, 64-bit & WOW6432Node)
    pour découvrir tous les dossiers de plugins VST configurés par les installateurs
    audio (Steinberg, Native Instruments, Spectrasonics, IK Multimedia, Toontrack, etc.).
    """
    found: List[str] = []
    if sys.platform != "win32":
        return found

    def _add(p: Any):
        if isinstance(p, str) and p.strip():
            cleaned = os.path.normpath(p.strip().strip('"'))
            if os.path.isdir(cleaned) and not _should_skip_directory(cleaned) and cleaned not in found:
                found.append(cleaned)

    try:
        import winreg
        # 1. Clés standards VSTPluginsPath
        for root_key, subkey, val_name in [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\VST", "VSTPluginsPath"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\VST", "VSTPluginsPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\VST", "VSTPluginsPath"),
        ]:
            try:
                with winreg.OpenKey(root_key, subkey) as key:
                    val, _ = winreg.QueryValueEx(key, val_name)
                    _add(val)
            except OSError:
                pass

        # 2. Clés d'éditeurs audio connus
        audio_vendors = [
            "Native Instruments", "Spectrasonics", "IK Multimedia", "Steinberg",
            "Toontrack", "Cakewalk", "Arturia", "FabFilter", "Xfer", "ValhallaDSP",
            "iZotope", "Celemony", "Korg", "Roland", "u-he", "Output",
            "Spitfire Audio", "Soundtoys", "Eventide", "Softube", "Plugin Alliance",
        ]
        value_keywords = ("vst64", "vst3", "vstdir", "vstpath", "vstplugins", "pluginpath", "plugindir")

        for root_key in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for base_branch in (r"SOFTWARE", r"SOFTWARE\WOW6432Node"):
                for vendor in audio_vendors:
                    vendor_path = f"{base_branch}\\{vendor}"
                    try:
                        with winreg.OpenKey(root_key, vendor_path) as vkey:
                            # Valeurs directes
                            idx = 0
                            while True:
                                try:
                                    vname, vval, _ = winreg.EnumValue(vkey, idx)
                                    if any(k in vname.lower() for k in value_keywords):
                                        _add(vval)
                                    idx += 1
                                except OSError:
                                    break
                            # Sous-clés (ex: HKLM\SOFTWARE\Native Instruments\Kontakt 5 -> InstallVST64Dir)
                            s_idx = 0
                            while True:
                                try:
                                    sub_name = winreg.EnumKey(vkey, s_idx)
                                    with winreg.OpenKey(vkey, sub_name) as subk:
                                        v_idx = 0
                                        while True:
                                            try:
                                                vname, vval, _ = winreg.EnumValue(subk, v_idx)
                                                if any(k in vname.lower() for k in value_keywords):
                                                    _add(vval)
                                                v_idx += 1
                                            except OSError:
                                                break
                                    s_idx += 1
                                except OSError:
                                    break
                    except OSError:
                        pass
    except Exception:
        pass

    return found


def get_default_plugin_directories() -> List[str]:
    """
    Retourne dynamiquement l'ensemble des répertoires standards de plugins audio (VST3 et VST2)
    pour le système d'exploitation actuel, incluant les clés de registre Windows, tous les disques
    locaux et les dossiers d'éditeurs réputés. Les dossiers 64-bit sont priorisés.
    """
    dirs_64: List[str] = []
    dirs_32: List[str] = []

    def _add_if_dir(path: Optional[str], is_32bit: bool = False):
        if not path:
            return
        norm = os.path.normpath(path)
        if not os.path.isdir(norm) or _should_skip_directory(norm):
            return
        if is_32bit or "(x86)" in norm.lower() or "32 bit" in norm.lower() or "32bit" in norm.lower():
            if norm not in dirs_32 and norm not in dirs_64:
                dirs_32.append(norm)
        else:
            if norm not in dirs_64:
                dirs_64.append(norm)

    if sys.platform == "win32":
        # 1. Standard Windows 64-bit Common Files VST3 et VST2
        common_files = os.environ.get("COMMONPROGRAMFILES", r"C:\Program Files\Common Files")
        _add_if_dir(os.path.join(common_files, "VST3"))
        _add_if_dir(os.path.join(common_files, "Steinberg", "VST3"))
        _add_if_dir(os.path.join(common_files, "VST2"))
        _add_if_dir(os.path.join(common_files, "Steinberg", "Shared Components"))

        # 2. Dossier Local/Roaming de l'utilisateur
        local_appdata = os.environ.get("LOCALAPPDATA")
        if local_appdata:
            _add_if_dir(os.path.join(local_appdata, "Programs", "Common", "VST3"))
        appdata = os.environ.get("APPDATA")
        if appdata:
            _add_if_dir(os.path.join(appdata, "VST3"))

        # 3. Program Files 64-bit standards (Steinberg, VstPlugins, Native Instruments, Cakewalk)
        prog_files = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        _add_if_dir(os.path.join(prog_files, "VSTPlugins"))
        _add_if_dir(os.path.join(prog_files, "Steinberg", "VstPlugins"))
        _add_if_dir(os.path.join(prog_files, "Steinberg", "VST3"))
        _add_if_dir(os.path.join(prog_files, "Native Instruments", "VSTPlugins 64 bit"))
        _add_if_dir(os.path.join(prog_files, "Cakewalk", "VstPlugins"))

        # 4. Spectrasonics plug-ins (Omnisphere, Trilian, Keyscape) - 64-bit prioritaire
        prog_data = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
        _add_if_dir(os.path.join(prog_data, "Spectrasonics", "plug-ins", "64bit"))
        _add_if_dir(os.path.join(prog_data, "Spectrasonics", "plug-ins"), is_32bit=True)

        # 5. Clés de Registre Windows dynamiques (Steinberg, NI, IK Multimedia, Cakewalk, etc.)
        for reg_dir in _discover_windows_registry_plugin_dirs():
            _add_if_dir(reg_dir)

        # 6. Dossiers VST à la racine de tous les disques locaux (ex: C:\VSTPlugins, D:\VSTPlugins, E:\VST3)
        for drive_letter in string.ascii_uppercase:
            drive_root = f"{drive_letter}:\\"
            if not os.path.isdir(drive_root):
                continue
            for rel in (
                "VSTPlugins",
                "VstPlugins",
                "VST3",
                "VST2",
                "VST",
                r"Steinberg\VstPlugins",
                r"Audio\VSTPlugins",
                r"Plugins\VST",
                r"Program Files\Common Files\VST3",
                r"Program Files\VSTPlugins",
                r"Program Files\Steinberg\VstPlugins",
                r"Program Files\Native Instruments\VSTPlugins 64 bit",
                r"Program Files\Cakewalk\VstPlugins",
            ):
                candidate = os.path.join(drive_root, rel)
                if os.path.isdir(candidate):
                    _add_if_dir(candidate)

        # 7. Standard 32-bit Common Files & Program Files (x86)
        common_x86 = os.environ.get("COMMONPROGRAMFILES(X86)")
        if common_x86:
            _add_if_dir(os.path.join(common_x86, "VST3"), is_32bit=True)
            _add_if_dir(os.path.join(common_x86, "Steinberg", "VST3"), is_32bit=True)
            _add_if_dir(os.path.join(common_x86, "VST2"), is_32bit=True)

        prog_x86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
        _add_if_dir(os.path.join(prog_x86, "VSTPlugins"), is_32bit=True)
        _add_if_dir(os.path.join(prog_x86, "Steinberg", "VstPlugins"), is_32bit=True)
        _add_if_dir(os.path.join(prog_x86, "Cakewalk", "VstPlugins"), is_32bit=True)
        _add_if_dir(os.path.join(prog_x86, "Native Instruments", "VSTPlugins 32 bit"), is_32bit=True)

    elif sys.platform == "darwin":  # macOS
        for path in [
            "/Library/Audio/Plug-Ins/VST3",
            os.path.expanduser("~/Library/Audio/Plug-Ins/VST3"),
            "/Library/Audio/Plug-Ins/VST",
            os.path.expanduser("~/Library/Audio/Plug-Ins/VST"),
            "/Library/Audio/Plug-Ins/Components",
            os.path.expanduser("~/Library/Audio/Plug-Ins/Components"),
        ]:
            _add_if_dir(path)

    else:  # Linux
        for path in [
            "/usr/lib/vst3",
            "/usr/local/lib/vst3",
            "/usr/lib/vst",
            "/usr/local/lib/vst",
            os.path.expanduser("~/.vst3"),
            os.path.expanduser("~/.vst"),
            os.path.expanduser("~/.lxvst"),
        ]:
            _add_if_dir(path)

    return dirs_64 + dirs_32


# Rétrocompatibilité avec les tests et le code existant
get_default_vst3_directories = get_default_plugin_directories


def _collect_candidate_plugin_files(scan_dirs: List[str]) -> List[str]:
    """
    Parcourt les dossiers fournis pour collecter tous les fichiers .vst3 et .dll VST2 valides,
    tout en ignorant les doublons 32-bit lorsqu'une version 64-bit du même plugin existe.
    """
    raw_vst3: List[str] = []
    raw_dll_64: List[str] = []
    raw_dll_32: List[str] = []
    visited_dirs: Set[str] = set()

    for directory in scan_dirs:
        if not os.path.isdir(directory) or _should_skip_directory(directory):
            continue
        norm_d = os.path.normpath(directory).lower()
        if norm_d in visited_dirs:
            continue
        visited_dirs.add(norm_d)

        for root, dirs, files in os.walk(directory):
            # Filtrer les sous-dossiers non-audio ou verrouillés avant d'y descendre
            for d in list(dirs):
                full_sub = os.path.join(root, d)
                if d.lower().endswith(".vst3"):
                    if not d.lower().startswith("waveshell") and "cubase plug-in set" not in d.lower():
                        raw_vst3.append(os.path.normpath(full_sub))
                    dirs.remove(d)
                elif _should_skip_directory(full_sub):
                    dirs.remove(d)

            for f in files:
                lower_f = f.lower()
                if lower_f.startswith("waveshell") or "cubase plug-in set" in lower_f:
                    continue
                full_p = os.path.normpath(os.path.join(root, f))
                if lower_f.endswith(".vst3"):
                    raw_vst3.append(full_p)
                elif lower_f.endswith(".dll"):
                    in_vst_dir = any(
                        k in root.lower()
                        for k in ("vst", "steinberg", "spectrasonics", "native instruments", "plug-ins", "toontrack", "cakewalk", "ik multimedia")
                    )
                    if in_vst_dir and is_vst2_dll(full_p):
                        machine = _get_pe_machine(full_p)
                        if machine == 0x8664:
                            raw_dll_64.append(full_p)
                        elif machine == 0x014C:
                            raw_dll_32.append(full_p)

    # Déduplication : si un plugin DLL existe en 64-bit, ignorer sa copie 32-bit redondante
    x64_basenames = {os.path.basename(p).lower() for p in raw_dll_64}
    # Également ignorer les VST3 32-bit dans Program Files (x86) si le VST3 64-bit existe
    x64_vst3_names = {
        os.path.basename(p).lower()
        for p in raw_vst3
        if "(x86)" not in p.lower()
    }

    filtered_vst3 = []
    seen_paths: Set[str] = set()
    for p in raw_vst3:
        if "(x86)" in p.lower() and os.path.basename(p).lower() in x64_vst3_names:
            continue
        lp = p.lower()
        if lp not in seen_paths:
            seen_paths.add(lp)
            filtered_vst3.append(p)

    filtered_dlls = []
    seen_dll_basenames: Set[str] = set()
    # 1. Ajouter les DLLs 64-bit en priorité (une seule copie par nom de fichier)
    for p in raw_dll_64:
        lp = p.lower()
        base = os.path.basename(lp)
        if lp not in seen_paths and base not in seen_dll_basenames:
            seen_paths.add(lp)
            seen_dll_basenames.add(base)
            filtered_dlls.append(p)

    # 2. Ajouter les DLLs 32-bit uniquement si aucune version 64-bit du même nom n'existe
    for p in raw_dll_32:
        lp = p.lower()
        base = os.path.basename(lp)
        if base in x64_basenames or base in seen_dll_basenames:
            continue
        if lp not in seen_paths:
            seen_paths.add(lp)
            seen_dll_basenames.add(base)
            filtered_dlls.append(p)

    return sorted(filtered_vst3 + filtered_dlls)


class PluginLoadWorker(QThread):
    """Worker asynchrone pour charger un plugin VST3 ou VST2 lourd sans figer l'interface"""
    loaded = Signal(str, object)  # file_path, plugin_instance
    error = Signal(str, str)      # file_path, error_message

    def __init__(self, file_path: str, parent=None, instance_key=None):
        super().__init__(parent)
        self.file_path = file_path
        self.instance_key = instance_key

    def run(self):
        try:
            plugin = global_plugin_manager.get_or_load_plugin(self.file_path, self.instance_key)
            if plugin is None:
                raise RuntimeError(global_plugin_manager.load_errors.get(self.instance_key or self.file_path, "Chargement impossible"))
            self.loaded.emit(self.file_path, plugin)
        except Exception as e:
            self.error.emit(self.file_path, str(e))


class PluginScanWorker(QThread):
    """
    Worker en arrière-plan pour scanner les dossiers de plugins sans figer l'interface.
    Supporte le scan incrémental ultra-rapide (évite de ré-analyser les fichiers déjà connus et inchangés).
    """
    progress = Signal(int, int, str)  # index, total, plugin_name
    plugin_found = Signal(object)      # PluginInfo
    finished_scan = Signal(list)       # List[PluginInfo]

    def __init__(self, directories: List[str], parent=None, incremental: bool = True,
                 existing_cache: Optional[Dict[str, PluginInfo]] = None, deep_scan: bool = False):
        super().__init__(parent)
        self.directories = list(directories)
        self.incremental = incremental
        self.existing_cache = existing_cache or {}
        self.deep_scan = deep_scan

    def run(self):
        scan_dirs = list(self.directories)
        if self.deep_scan and sys.platform == "win32":
            prog_files = os.environ.get("PROGRAMFILES", r"C:\Program Files")
            prog_x86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
            prog_data = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
            for root_prog in [prog_files, prog_data, prog_x86]:
                if os.path.isdir(root_prog) and root_prog not in scan_dirs:
                    scan_dirs.append(root_prog)

        found_files = _collect_candidate_plugin_files(scan_dirs)
        total = len(found_files)
        results: List[PluginInfo] = []

        for idx, file_path in enumerate(found_files):
            if self.isInterruptionRequested():
                break

            base_name = os.path.splitext(os.path.basename(file_path))[0]
            self.progress.emit(idx + 1, total, base_name)

            # Vérification incrémentale rapide : si le fichier est en cache et non modifié, réutiliser
            # (Sauf si c'était une ancienne entrée VST2 marquée incompatible avant la prise en charge native VST2)
            if self.incremental and file_path in self.existing_cache:
                cached = self.existing_cache[file_path]
                is_stale_vst2 = (
                    cached.format == "vst2"
                    and not cached.is_compatible
                    and cached.error_message
                    and ("hérité" in cached.error_message.lower() or "vst3" in cached.error_message.lower())
                )
                if not is_stale_vst2:
                    try:
                        cur_mtime = os.path.getmtime(file_path)
                        cur_size = os.path.getsize(file_path)
                        if (cached.file_mtime == cur_mtime and cached.file_size == cur_size) or (cached.file_mtime == 0.0 and cached.is_compatible):
                            cached.file_mtime = cur_mtime
                            cached.file_size = cur_size
                            results.append(cached)
                            self.plugin_found.emit(cached)
                            continue
                    except OSError:
                        pass

            info = PluginManager.probe_plugin(file_path)
            results.append(info)
            self.plugin_found.emit(info)

        self.finished_scan.emit(results)


class PluginManager(QObject):
    """
    Gestionnaire central des plugins audio (VST3 et VST2 64-bit/32-bit) pour NovaDAW.
    Gère le stockage des chemins personnalisés, le cache ultra-rapide au démarrage,
    l'auto-détection en arrière-plan et les instances actives de plugins.
    """
    scan_updated = Signal()
    editor_opened = Signal(str)  # file_path
    editor_closed = Signal(str)  # file_path

    def __init__(self, parent=None):
        super().__init__(parent)
        self.config_dir = os.path.join(os.path.expanduser("~"), ".novadaw")
        os.makedirs(self.config_dir, exist_ok=True)
        self.settings_file = os.path.join(self.config_dir, "plugin_settings.json")
        self.cache_file = os.path.join(self.config_dir, "plugins_cache.json")

        self.custom_directories: List[str] = []
        self.plugins: List[PluginInfo] = []
        self.active_instances: Dict[str, Any] = {}  # file_path -> IsolatedPlugin instance
        self.open_editors: Dict[str, threading.Event] = {}  # file_path -> close_event

        self._loading = set()
        self.saved_states = {}
        self.load_errors = {}
        self._instance_lock = threading.RLock()

        # Surveillance automatique des dossiers de plugins
        self._dir_watcher = QFileSystemWatcher(self)
        self._dir_watcher.directoryChanged.connect(self._on_watched_directory_changed)
        self._background_scan_worker: Optional[PluginScanWorker] = None
        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(1200)
        self._debounce_timer.timeout.connect(self.trigger_background_scan)

        self.load_settings()
        self.load_cache()
        self._update_watcher_paths()

    def _update_watcher_paths(self):
        """Met à jour la liste des répertoires surveillés par QFileSystemWatcher"""
        current_watched = set(self._dir_watcher.directories())
        all_dirs = set(self.get_all_directories())
        to_remove = [d for d in current_watched if d not in all_dirs]
        to_add = [d for d in all_dirs if d not in current_watched and os.path.isdir(d)]
        if to_remove:
            self._dir_watcher.removePaths(to_remove)
        if to_add:
            self._dir_watcher.addPaths(to_add)

    def _on_watched_directory_changed(self, path: str):
        """Déclenché lorsqu'un fichier ou sous-dossier est modifié/ajouté dans un dossier surveillé"""
        self._debounce_timer.start()

    def trigger_background_scan(self, deep_scan: bool = False):
        """Lance une vérification incrémentale en arrière-plan sans bloquer l'interface"""
        if self._background_scan_worker and self._background_scan_worker.isRunning():
            return
        dirs = self.get_all_directories()
        if not dirs:
            return
        cache_map = {p.file_path: p for p in self.plugins}
        self._background_scan_worker = PluginScanWorker(
            directories=dirs,
            parent=self,
            incremental=True,
            existing_cache=cache_map,
            deep_scan=deep_scan
        )
        self._background_scan_worker.finished_scan.connect(self._on_background_scan_finished)
        self._background_scan_worker.start()

    def _on_background_scan_finished(self, results: List[PluginInfo]):
        old_sig = [(p.file_path, p.is_compatible, p.plugin_type) for p in self.plugins]
        new_sig = [(p.file_path, p.is_compatible, p.plugin_type) for p in results]
        if old_sig != new_sig:
            self.plugins = results
            self.save_cache()
            self.scan_updated.emit()
            print(f"[PluginManager] Surveillance automatique : {len(results)} plugins mis à jour.")

    def is_editor_open(self, file_path: str) -> bool:
        """Indique si l'éditeur VST d'un plugin donné est actuellement ouvert"""
        return file_path in self.open_editors and not self.open_editors[file_path].is_set()

    def close_editor(self, file_path: str):
        """Ferme programmatiquement l'interface d'un plugin VST"""
        evt = self.open_editors.get(file_path)
        if evt:
            evt.set()

    def close_all_editors(self):
        """Ferme toutes les fenêtres de plugins ouvertes (appelé à la fermeture de NovaDAW)"""
        for path, evt in list(self.open_editors.items()):
            try:
                evt.set()
            except Exception:
                pass
        self.open_editors.clear()

    def get_all_directories(self) -> List[str]:
        """Combine répertoires standards du système et dossiers personnalisés de l'utilisateur"""
        all_dirs = list(get_default_plugin_directories())
        for cd in self.custom_directories:
            norm_cd = os.path.normpath(cd)
            if norm_cd not in all_dirs and os.path.isdir(norm_cd):
                all_dirs.append(norm_cd)
        return all_dirs

    def add_custom_directory(self, path: str) -> bool:
        norm_path = os.path.normpath(path)
        if os.path.isdir(norm_path) and norm_path not in self.custom_directories:
            self.custom_directories.append(norm_path)
            self.save_settings()
            self._update_watcher_paths()
            return True
        return False

    def remove_custom_directory(self, path: str):
        norm_path = os.path.normpath(path)
        if norm_path in self.custom_directories:
            self.custom_directories.remove(norm_path)
            self.save_settings()
            self._update_watcher_paths()

    def load_settings(self):
        if os.path.exists(self.settings_file):
            try:
                with open(self.settings_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.custom_directories = [os.path.normpath(d) for d in data.get("custom_directories", [])]
            except Exception as e:
                print(f"[PluginManager] Erreur lecture paramètres : {e}")

    def save_settings(self):
        try:
            with open(self.settings_file, "w", encoding="utf-8") as f:
                json.dump({"custom_directories": self.custom_directories}, f, indent=2)
        except Exception as e:
            print(f"[PluginManager] Erreur sauvegarde paramètres : {e}")

    def load_cache(self):
        """Charge instantanément la liste des plugins depuis le cache JSON et met à niveau les entrées VST2."""
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    loaded = [PluginInfo.from_dict(d) for d in data]

                # Filtrer les conteneurs WaveShell et doublons DLL (en priorisant 64-bit)
                x64_dll_names = {
                    os.path.basename(p.file_path).lower()
                    for p in loaded
                    if p.file_path.lower().endswith(".dll")
                    and os.path.isfile(p.file_path)
                    and _get_pe_machine(p.file_path) == 0x8664
                }
                filtered: List[PluginInfo] = []
                seen_dll_basenames: Set[str] = set()
                needs_save = False
                for p in loaded:
                    base_lower = os.path.basename(p.file_path).lower()
                    if base_lower.startswith("waveshell") or _should_skip_directory(os.path.dirname(p.file_path)):
                        needs_save = True
                        continue
                    if p.file_path.lower().endswith(".dll") and os.path.isfile(p.file_path):
                        if _get_pe_machine(p.file_path) == 0x014C and base_lower in x64_dll_names:
                            needs_save = True
                            continue
                        if base_lower in seen_dll_basenames:
                            needs_save = True
                            continue
                        seen_dll_basenames.add(base_lower)
                    # Mise à niveau automatique des entrées VST2 anciennement marquées "héritées / non compatibles"
                    if (
                        p.format == "vst2"
                        and not p.is_compatible
                        and p.error_message
                        and ("hérité" in p.error_message.lower() or "vst3" in p.error_message.lower())
                        and os.path.isfile(p.file_path)
                    ):
                        upgraded = self.probe_plugin(p.file_path)
                        filtered.append(upgraded)
                        needs_save = True
                    else:
                        filtered.append(p)

                self.plugins = filtered
                if needs_save:
                    self.save_cache()
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
        Teste la compatibilité d'un plugin (VST3 ou VST2 64-bit/32-bit) avec isolation complète des erreurs.
        Ne fait JAMAIS planter l'application en cas de crash ou d'incompatibilité.
        """
        file_path = os.path.normpath(file_path)
        base_name = os.path.splitext(os.path.basename(file_path))[0]
        is_dll = file_path.lower().endswith(".dll")
        fmt = "vst2" if is_dll else "vst3"
        cur_mtime = 0.0
        cur_size = 0
        try:
            cur_mtime = os.path.getmtime(file_path)
            cur_size = os.path.getsize(file_path)
        except OSError:
            pass

        if is_dll and not is_vst2_dll(file_path):
            return PluginInfo(
                name=base_name,
                file_path=file_path,
                plugin_type="unknown",
                is_compatible=False,
                error_message="Bibliothèque DLL non-VST ou incompatible",
                parameters_count=0,
                format="dll",
                file_mtime=cur_mtime,
                file_size=cur_size,
            )

        try:
            plugin = IsolatedPlugin(file_path, timeout=8, probe_only=True)
            try:
                return PluginInfo(
                    name=plugin.name or base_name,
                    file_path=file_path,
                    plugin_type="instrument" if plugin.is_instrument else "effect",
                    is_compatible=True,
                    error_message=None,
                    parameters_count=plugin.parameters_count,
                    format=fmt,
                    file_mtime=cur_mtime,
                    file_size=cur_size,
                )
            finally:
                plugin.dispose()
        except Exception as e:
            err = str(e)
            return PluginInfo(
                name=base_name,
                file_path=file_path,
                plugin_type="unknown",
                is_compatible=False,
                error_message=err,
                format=fmt,
                file_mtime=cur_mtime,
                file_size=cur_size,
            )

    def add_plugin_file(self, file_path: str) -> PluginInfo:
        """Ajoute manuellement un plugin VST3 ou VST2 sélectionné par l'utilisateur"""
        info = self.probe_plugin(file_path)
        self.plugins = [p for p in self.plugins if p.file_path != file_path]
        self.plugins.append(info)
        self.save_cache()
        self.scan_updated.emit()
        return info

    def get_compatible_instruments(self) -> List[PluginInfo]:
        """Retourne tous les instruments VSTi (VST3 et VST2) compatibles"""
        return [p for p in self.plugins if p.is_compatible and p.plugin_type == "instrument"]

    def get_compatible_effects(self) -> List[PluginInfo]:
        """Retourne tous les effets VST FX (VST3 et VST2) compatibles"""
        return [p for p in self.plugins if p.is_compatible and p.plugin_type == "effect"]

    def get_all_detected_plugins(self) -> List[PluginInfo]:
        """Retourne tous les plugins détectés sur le système"""
        return list(self.plugins)

    def get_or_load_plugin(self, file_path: str, instance_key=None) -> Optional[Any]:
        """Charge ou récupère une instance en cache d'un plugin VST3 ou VST2"""
        key = instance_key or file_path
        with self._instance_lock:
            if key in self.active_instances:
                return self.active_instances[key]
            if key in self.load_errors:
                return None
            plugin = None
            try:
                plugin = IsolatedPlugin(file_path)
                state = self.saved_states.get(key, self.saved_states.get(file_path))
                if state is None and key != file_path and file_path in self.active_instances:
                    state = self.active_instances[file_path].raw_state
                if state is not None:
                    plugin.raw_state = state
                self.active_instances[key] = plugin
                return plugin
            except Exception as exc:
                if plugin is not None:
                    plugin.dispose()
                self.load_errors[key] = str(exc)
                return None

    def get_ready_plugin(self, path, key):
        """The audio callback never initializes a native plugin."""
        if key in self.active_instances:
            return self.active_instances[key]
        if threading.current_thread() is threading.main_thread():
            return self.get_or_load_plugin(path, key)
        if key not in self._loading and key not in self.load_errors:
            self._loading.add(key)
            def load():
                try:
                    self.get_or_load_plugin(path, key)
                finally:
                    self._loading.discard(key)
            threading.Thread(target=load, daemon=True).start()
        return None

    def capture_states(self):
        import base64
        for key, plugin in list(self.active_instances.items()):
            try:
                self.saved_states[key] = plugin.raw_state
            except Exception:
                pass
        return {key: base64.b64encode(value).decode("ascii")
                for key, value in self.saved_states.items()}

    def restore_states(self, states):
        import base64
        self.release_instances()
        self.saved_states = {key: base64.b64decode(value) for key, value in states.items()}

    def release_instances(self):
        self.close_all_editors()
        with self._instance_lock:
            for plugin in self.active_instances.values():
                plugin.dispose()
            self.active_instances.clear()
            self.load_errors.clear()


# Instance globale partagée
global_plugin_manager = PluginManager()
