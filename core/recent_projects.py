"""
core/recent_projects.py - Gestionnaire centralisé de l'historique des projets récents pour NovaDAW.

Permet de :
- Mémoriser les projets ouverts ou enregistrés.
- Éviter les doublons (avec normalisation de chemin insensible à la casse sous Windows).
- Placer le projet le plus récemment utilisé en tête de liste.
- Limiter la liste à un nombre maximal d'éléments configurables (par défaut 10).
- Sauvegarder et charger la liste dans settings.json en préservant les autres paramètres (Audio, GPU, etc.).
"""
import os
import json
from typing import List, Optional

SETTINGS_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "settings.json"))
DEFAULT_MAX_RECENT_PROJECTS = 10


class RecentProjectsManager:
    """Gestionnaire de persistance et de manipulation des projets récents."""

    def __init__(self, settings_file: Optional[str] = None, max_items: int = DEFAULT_MAX_RECENT_PROJECTS):
        self.settings_file = os.path.abspath(settings_file) if settings_file else SETTINGS_FILE
        self.max_items = max_items
        self._recent_projects: List[str] = []
        self.load()

    def load(self) -> List[str]:
        """Charge la liste des projets récents depuis le fichier de configuration."""
        self._recent_projects = []
        if os.path.exists(self.settings_file):
            try:
                with open(self.settings_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    raw_list = data.get("recent_projects", [])
                    if isinstance(raw_list, list):
                        seen = set()
                        for item in raw_list:
                            if isinstance(item, str) and item.strip():
                                clean_path = os.path.abspath(os.path.normpath(item.strip()))
                                norm_key = os.path.normcase(clean_path)
                                if norm_key not in seen:
                                    seen.add(norm_key)
                                    self._recent_projects.append(clean_path)
                                    if len(self._recent_projects) >= self.max_items:
                                        break
            except Exception as e:
                print(f"[RecentProjectsManager] Erreur chargement {self.settings_file}: {e}")

        return list(self._recent_projects)

    def get_recent_projects(self) -> List[str]:
        """Retourne la copie de la liste actuelle des chemins de projets récents."""
        return list(self._recent_projects)

    def add_recent_project(self, file_path: str) -> None:
        """
        Ajoute ou remonte un projet en tête de liste et sauvegarde l'état.
        """
        if not file_path or not isinstance(file_path, str) or not file_path.strip():
            return

        clean_path = os.path.abspath(os.path.normpath(file_path.strip()))
        norm_key = os.path.normcase(clean_path)

        # Retirer toute occurrence précédente indépendamment de la casse
        self._recent_projects = [
            p for p in self._recent_projects
            if os.path.normcase(p) != norm_key
        ]

        # Insérer en première position
        self._recent_projects.insert(0, clean_path)

        # Tronquer à max_items
        if len(self._recent_projects) > self.max_items:
            self._recent_projects = self._recent_projects[:self.max_items]

        self.save()

    def remove_recent_project(self, file_path: str) -> bool:
        """
        Supprime un projet de la liste des récents et enregistre les modifications.
        """
        if not file_path or not isinstance(file_path, str):
            return False

        norm_key = os.path.normcase(os.path.abspath(os.path.normpath(file_path.strip())))
        initial_len = len(self._recent_projects)
        self._recent_projects = [
            p for p in self._recent_projects
            if os.path.normcase(p) != norm_key
        ]

        if len(self._recent_projects) != initial_len:
            self.save()
            return True
        return False

    def clear_recent_projects(self) -> None:
        """Vide l'intégralité de la liste des projets récents et sauvegarde."""
        self._recent_projects = []
        self.save()

    def save(self) -> bool:
        """
        Sauvegarde la liste des projets récents dans le fichier settings.json
        tout en préservant scrupuleusement les autres clés existantes (audio, graphics, etc.).
        """
        try:
            current_settings = {}
            if os.path.exists(self.settings_file):
                try:
                    with open(self.settings_file, "r", encoding="utf-8") as f:
                        current_settings = json.load(f)
                        if not isinstance(current_settings, dict):
                            current_settings = {}
                except Exception:
                    current_settings = {}

            current_settings["recent_projects"] = list(self._recent_projects)

            # Créer le répertoire parent si nécessaire
            parent_dir = os.path.dirname(self.settings_file)
            if parent_dir and not os.path.exists(parent_dir):
                os.makedirs(parent_dir, exist_ok=True)

            with open(self.settings_file, "w", encoding="utf-8") as f:
                json.dump(current_settings, f, indent=2, ensure_ascii=False)

            # Synchroniser également avec hardware_manager si chargé en mémoire
            try:
                from core.hardware_manager import hardware_manager
                if hasattr(hardware_manager, "settings") and isinstance(hardware_manager.settings, dict):
                    hardware_manager.settings["recent_projects"] = list(self._recent_projects)
            except Exception:
                pass

            return True
        except Exception as e:
            print(f"[RecentProjectsManager] Erreur sauvegarde {self.settings_file}: {e}")
            return False


# Instance globale partagée
recent_projects_manager = RecentProjectsManager()
