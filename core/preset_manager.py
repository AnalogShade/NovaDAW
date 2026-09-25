"""
core/preset_manager.py - Système natif et universel de gestion de presets pour NovaDAW.

Permet de :
- Sauvegarder l'état complet de n'importe quel plugin (synthétiseur, boîte à rythmes, égaliseur, compresseur, mixeur, etc.).
- Charger et appliquer un preset instantanément.
- Lister les presets d'usine et les presets créés par l'utilisateur.
- Importer et exporter des presets au format JSON standardisé (.json / .ndawpreset).
- Supprimer des presets utilisateur.
"""
import os
import json
import time
import re
import shutil
from pathlib import Path
from typing import Dict, Any, List, Optional, Union


class PresetInfo(dict):
    """
    Représente les métadonnées d'un preset, accessible par clé dict ou par attribut.
    """
    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(f"'PresetInfo' object has no attribute '{name}'")

    def __setattr__(self, name: str, value: Any) -> None:
        self[name] = value


class PresetManager:
    """
    Gestionnaire centralisé de presets pour tous les plugins NovaDAW.
    Gère la persistance sur disque dans le dossier 'presets/<plugin_type_id>/'.
    """

    def __init__(self, base_presets_dir: Optional[Union[str, Path]] = None, base_dir: Optional[Union[str, Path]] = None):
        target_dir = base_dir if base_dir is not None else base_presets_dir
        if target_dir is None:
            # Emplacement par défaut dans le projet NovaDAW
            project_root = Path(__file__).resolve().parent.parent
            self.base_dir = project_root / "presets"
        else:
            self.base_dir = Path(target_dir)

        self._ensure_base_directory()

    def _ensure_base_directory(self) -> None:
        try:
            self.base_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            print(f"[PresetManager] Erreur création dossier presets : {e}")

    def get_plugin_preset_dir(self, plugin_type_id: str) -> Path:
        """Retourne et crée si nécessaire le dossier de presets dédié à un plugin."""
        safe_type = re.sub(r'[^\w\.-]', '_', str(plugin_type_id).strip())
        target_dir = self.base_dir / safe_type
        target_dir.mkdir(parents=True, exist_ok=True)
        return target_dir

    def sanitize_filename(self, name: str) -> str:
        """Nettoie une chaîne pour en faire un nom de fichier valide."""
        clean = re.sub(r'[\\/*?:"<>|]', "", name).strip()
        return clean or "Preset"

    def preset_exists(self, plugin_type_id: str, preset_name: str) -> bool:
        """Vérifie si un preset utilisateur existe sur disque."""
        p_dir = self.get_plugin_preset_dir(plugin_type_id)
        safe_name = self.sanitize_filename(preset_name)
        target = p_dir / f"{safe_name}.json"
        if target.exists():
            return True
        for f in p_dir.glob("*.json"):
            if f.stem.lower() == preset_name.lower():
                return True
        return False

    def list_presets(self, plugin_type_id: str, plugin_instance: Optional[Any] = None) -> List[PresetInfo]:
        """
        Liste tous les presets disponibles pour ce type de plugin :
        - Presets d'usine (factory)
        - Presets utilisateur enregistrés sur disque (user)
        """
        results: List[PresetInfo] = []
        seen_names = set()

        # 1. Presets d'usine fournis par le plugin (s'il en possède)
        if plugin_instance and hasattr(plugin_instance, "get_factory_presets"):
            try:
                factory = plugin_instance.get_factory_presets()
                if isinstance(factory, dict):
                    for fname in factory.keys():
                        results.append(PresetInfo({
                            "name": fname,
                            "plugin_type_id": plugin_type_id,
                            "is_factory": True,
                            "file_path": None,
                        }))
                        seen_names.add(fname.lower())
            except Exception as e:
                print(f"[PresetManager] Erreur lecture presets usine : {e}")

        # 2. Presets utilisateur stockés sur disque
        p_dir = self.get_plugin_preset_dir(plugin_type_id)
        if p_dir.exists():
            for f in sorted(p_dir.glob("*.json")):
                p_name = f.stem
                if p_name.lower() in seen_names:
                    continue
                try:
                    with open(f, "r", encoding="utf-8") as fp:
                        data = json.load(fp)
                    display_name = data.get("name") or data.get("preset_name") or p_name
                    results.append(PresetInfo({
                        "name": display_name,
                        "plugin_type_id": plugin_type_id,
                        "is_factory": False,
                        "file_path": str(f),
                        "created_at": data.get("created_at"),
                        "author": data.get("author", "Utilisateur")
                    }))
                    seen_names.add(display_name.lower())
                except Exception:
                    # Fichier corrompu ou illisible ignoré
                    pass

        return results

    def save_preset(
        self,
        plugin_or_type: Any = None,
        preset_name: str = "",
        state: Optional[Dict[str, Any]] = None,
        author: str = "Utilisateur",
        description: str = "",
        plugin: Any = None,
        plugin_type_id: Optional[str] = None
    ) -> str:
        """
        Sauvegarde l'état d'un plugin dans un preset JSON persistant.
        Supporte :
          - save_preset(plugin_instance, preset_name, author="...")
          - save_preset(plugin_type_id, preset_name, state_dict, author="...")
          - save_preset(plugin_type_id=..., preset_name=..., state=...)
        """
        if plugin_or_type is None:
            plugin_or_type = plugin if plugin is not None else plugin_type_id

        if not plugin_or_type:
            raise ValueError("Aucun plugin ou plugin_type_id spécifié.")

        if isinstance(plugin_or_type, str):
            actual_type_id = plugin_or_type
            plugin_name = plugin_or_type
            inst = None
            if state is None:
                state = {}
        else:
            inst = plugin_or_type
            actual_type_id = getattr(inst, "plugin_type_id", "generic.plugin")
            plugin_name = getattr(inst, "name", "Plugin")
            if state is None:
                if hasattr(inst, "get_state"):
                    state = inst.get_state()
                elif hasattr(inst, "to_dict"):
                    state = inst.to_dict()
                else:
                    raise ValueError(f"Le plugin {plugin_name} n'implémente pas get_state().")

        # Désimbriquer si le dictionnaire passé est le retour de to_dict() qui contient déjà 'state'
        if isinstance(state, dict) and "state" in state and isinstance(state["state"], dict):
            state = state["state"]

        preset_clean_name = self.sanitize_filename(preset_name)

        payload = {
            "version": "1.0",
            "name": preset_name,
            "plugin_type_id": actual_type_id,
            "plugin_name": plugin_name,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "author": author,
            "description": description,
            "state": state
        }

        p_dir = self.get_plugin_preset_dir(actual_type_id)
        target_file = p_dir / f"{preset_clean_name}.json"

        with open(target_file, "w", encoding="utf-8") as fp:
            json.dump(payload, fp, indent=2, ensure_ascii=False)

        # Mettre à jour l'attribut preset_name du plugin si présent
        if inst is not None and hasattr(inst, "preset_name"):
            inst.preset_name = preset_name

        return str(target_file)

    def load_preset(self, plugin: Any, preset_name_or_path: str) -> bool:
        """
        Charge et applique un preset dans un plugin (d'usine ou utilisateur).
        """
        if not plugin or not preset_name_or_path:
            return False

        # 1. Vérifier si c'est un preset d'usine natif du plugin
        if hasattr(plugin, "get_factory_presets"):
            factory_presets = plugin.get_factory_presets()
            if isinstance(factory_presets, dict):
                for k, v in factory_presets.items():
                    if k.lower() == preset_name_or_path.lower():
                        if hasattr(plugin, "apply_preset"):
                            return plugin.apply_preset(k)
                        elif hasattr(plugin, "set_state"):
                            plugin.set_state(v)
                            if hasattr(plugin, "preset_name"):
                                plugin.preset_name = k
                            return True

        # 2. Vérifier si c'est un chemin de fichier direct
        p_path = Path(preset_name_or_path)
        if not p_path.exists() or not p_path.is_file():
            # Chercher dans le dossier du plugin
            plugin_type_id = getattr(plugin, "plugin_type_id", "generic.plugin")
            p_dir = self.get_plugin_preset_dir(plugin_type_id)
            safe_name = self.sanitize_filename(preset_name_or_path)
            candidate = p_dir / f"{safe_name}.json"
            if candidate.exists():
                p_path = candidate
            else:
                # Recherche insensible à la casse
                found = None
                for f in p_dir.glob("*.json"):
                    if f.stem.lower() == preset_name_or_path.lower():
                        found = f
                        break
                if found:
                    p_path = found
                else:
                    return False

        try:
            with open(p_path, "r", encoding="utf-8") as fp:
                data = json.load(fp)

            state = data.get("state")
            if state is not None:
                # Désimbriquer si le dictionnaire passé contient une sous-clé 'state'
                if isinstance(state, dict) and "state" in state and isinstance(state["state"], dict):
                    state = state["state"]

                if hasattr(plugin, "set_state"):
                    plugin.set_state(state)
                elif hasattr(plugin, "from_dict"):
                    plugin.from_dict(state)

                loaded_name = data.get("name") or data.get("preset_name") or p_path.stem
                if hasattr(plugin, "preset_name"):
                    plugin.preset_name = loaded_name
                return True
        except Exception as e:
            print(f"[PresetManager] Erreur chargement preset {p_path}: {e}")

        return False

    def delete_preset(self, plugin_type_id: str, preset_name: str) -> bool:
        """Supprime un preset utilisateur."""
        p_dir = self.get_plugin_preset_dir(plugin_type_id)
        safe_name = self.sanitize_filename(preset_name)
        target_file = p_dir / f"{safe_name}.json"
        if not target_file.exists():
            for f in p_dir.glob("*.json"):
                if f.stem.lower() == preset_name.lower():
                    target_file = f
                    break

        if target_file.exists():
            try:
                target_file.unlink()
                return True
            except Exception as e:
                print(f"[PresetManager] Erreur suppression preset {target_file}: {e}")
                return False
        return False

    def export_preset(
        self,
        plugin_or_type: Any,
        preset_name_or_file_path: str,
        export_target_path: Optional[str] = None
    ) -> str:
        """
        Exporte un preset vers un fichier externe (.ndawpreset ou .json).
        Supporte :
          - export_preset(plugin_type_id, preset_name, export_target_path)
          - export_preset(plugin_instance, export_target_path)
        """
        if export_target_path is not None:
            # export_preset(plugin_type_id, preset_name, export_target_path)
            plugin_type_id = str(plugin_or_type)
            preset_name = preset_name_or_file_path
            p_dir = self.get_plugin_preset_dir(plugin_type_id)
            safe_name = self.sanitize_filename(preset_name)
            src_file = p_dir / f"{safe_name}.json"
            if not src_file.exists():
                for f in p_dir.glob("*.json"):
                    if f.stem.lower() == preset_name.lower():
                        src_file = f
                        break

            target = Path(export_target_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            if src_file.exists():
                shutil.copy2(src_file, target)
                return str(target)
            else:
                payload = {
                    "version": "1.0",
                    "name": preset_name,
                    "plugin_type_id": plugin_type_id,
                    "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "state": {}
                }
                with open(target, "w", encoding="utf-8") as fp:
                    json.dump(payload, fp, indent=2, ensure_ascii=False)
                return str(target)
        else:
            plugin = plugin_or_type
            target_path = preset_name_or_file_path
            name = getattr(plugin, "preset_name", getattr(plugin, "name", "Preset"))
            state = plugin.get_state() if hasattr(plugin, "get_state") else (
                plugin.to_dict() if hasattr(plugin, "to_dict") else {}
            )
            payload = {
                "version": "1.0",
                "name": name,
                "plugin_type_id": getattr(plugin, "plugin_type_id", "generic"),
                "plugin_name": getattr(plugin, "name", "Plugin"),
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "state": state
            }
            target = Path(target_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "w", encoding="utf-8") as fp:
                json.dump(payload, fp, indent=2, ensure_ascii=False)
            return str(target)

    def import_preset(self, plugin_or_type: Any, file_path: str) -> Optional[PresetInfo]:
        """
        Importe un preset externe (.json/.ndawpreset).
        Retourne PresetInfo en cas de succès, None sinon.
        """
        p = Path(file_path)
        if not p.exists():
            return None

        try:
            with open(p, "r", encoding="utf-8") as fp:
                data = json.load(fp)

            state = data.get("state")
            if state is None:
                return None

            preset_name = data.get("name") or data.get("preset_name") or p.stem
            plugin_type_id = data.get("plugin_type_id")
            if isinstance(plugin_or_type, str):
                plugin_type_id = plugin_or_type
                plugin = None
            else:
                plugin = plugin_or_type
                if plugin and hasattr(plugin, "plugin_type_id"):
                    plugin_type_id = plugin.plugin_type_id

            if not plugin_type_id:
                plugin_type_id = "generic"

            # Sauvegarder dans la bibliothèque de presets
            saved_path = self.save_preset(
                plugin_type_id,
                preset_name,
                state=state,
                author=data.get("author", "Importé")
            )

            # Appliquer si un plugin a été passé
            if plugin is not None:
                if hasattr(plugin, "set_state"):
                    plugin.set_state(state)
                elif hasattr(plugin, "from_dict"):
                    plugin.from_dict(state)
                if hasattr(plugin, "preset_name"):
                    plugin.preset_name = preset_name

            info = PresetInfo({
                "name": preset_name,
                "plugin_type_id": plugin_type_id,
                "is_factory": False,
                "file_path": saved_path,
                "created_at": data.get("created_at"),
                "author": data.get("author", "Importé")
            })
            return info
        except Exception as e:
            print(f"[PresetManager] Erreur import preset: {e}")
            return None


# Instance globale partagée
global_preset_manager = PresetManager()
