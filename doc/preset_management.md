# 💾 Spécification du Système de Gestion des Presets (`core/preset_manager.py`)

Ce document décrit le fonctionnement du gestionnaire universel de presets de **NovaDAW** ([`PresetManager`](../core/preset_manager.py)), le format de stockage sur disque, la protection des presets d'usine et son intégration dans l'interface utilisateur.

---

## 1. Architecture & Stockage (`core/preset_manager.py`)

Le singleton `preset_manager` (instance de [`PresetManager`](../core/preset_manager.py)) centralise la lecture, l'écriture, l'importation, l'exportation et la suppression des presets pour tous les modules héritant de [`BasePlugin`](../plugins/base.py).

### 1.1 Arborescence sur Disque
Les presets sont organisés par identifiant de type de plugin (`plugin_type_id`) dans le dossier racine `presets/` du projet :
```text
presets/
├── novadaw.synth/
│   ├── Supernova_Lead.json
│   └── Warm_Analog_Pad.json
├── novadaw.drum_machine/
│   └── 808_Trap_Kit.json
├── novadaw.equalizer/
│   └── Vocal_Presence_Boost.json
├── novadaw.compressor/
│   └── Master_Glue.json
└── novadaw.mixer/
    └── Studio_Default.json
```
- **Sanitisation des noms de fichiers** : [`PresetManager._sanitize_filename(name)`](../core/preset_manager.py) conserve uniquement les caractères alphanumériques et `_- `, et remplace les espaces par `_`.

### 1.2 Schéma JSON Standardisé d'un Preset (`.json` / `.ndawpreset`)
Chaque fichier de preset respecte la structure suivante :
```json
{
  "version": "1.0",
  "name": "Supernova Lead",
  "plugin_type_id": "novadaw.synth",
  "plugin_name": "NovaSynth (Polyphonic Subtractive Synthesizer)",
  "created_at": "2026-10-12T09:30:00",
  "author": "User",
  "description": "",
  "state": {
    "enabled": true,
    "...": "État sérialisé propre au plugin via plugin.get_state()"
  }
}
```

---

## 2. Protection Stricte des Presets d'Usine (Factory Presets)

NovaDAW distingue deux catégories de presets :
1. **Presets d'Usine (`Factory`)** :
   - Définis dans le code par `plugin.get_factory_presets()` **ou** présents sur le disque avec le marqueur `"author": "NovaDAW Factory"` ou `"is_factory": true`.
2. **Presets Utilisateur (`User`)** :
   - Créés ou importés par l'utilisateur (`"author": "User"`).

### Règles d'Inviolabilité ([`PresetManager.is_factory_preset`](../core/preset_manager.py))
> [!IMPORTANT]
> Les presets d'usine sont **en lecture seule absolue**.

- **Interdiction d'écrasement** : Appeler [`save_preset(plugin, name)`](../core/preset_manager.py) avec le nom d'un preset d'usine existant lève une `PermissionError` (sauf si `allow_factory_write=True` est explicitement passé par un script de génération interne).
- **Interdiction de suppression** : Appeler [`delete_preset(plugin_type_id, name, plugin)`](../core/preset_manager.py) sur un preset d'usine retourne `False` sans toucher au fichier disque.
- **Protection UI** : Dans [`NativePluginDialog`](../ui/plugin_dialogs.py), le bouton `🗑️ Supprimer` est automatiquement désactivé (`setEnabled(False)`) dès qu'un preset d'usine est sélectionné dans `combo_presets`. Si l'utilisateur tente d'enregistrer sous un nom d'usine, une boîte de dialogue l'invite à choisir un nouveau nom.

---

## 3. API Publique de `PresetManager`

| Méthode | Signature | Comportement |
| :--- | :--- | :--- |
| `is_factory_preset` | `(plugin, name: str) -> bool` | Vérifie si `name` appartient à `plugin.get_factory_presets()` ou possède `"author": "NovaDAW Factory"` / `"is_factory": True` sur disque. |
| `save_preset` | `(plugin, name: str, author="User", description="", allow_factory_write=False) -> str` | Appelle `plugin.get_state()`, écrit le JSON dans `presets/<plugin_type_id>/<sanitized>.json` et retourne le chemin. Lève `PermissionError` si preset d'usine. |
| `load_preset` | `(plugin, name_or_path: str) -> bool` | Charge un preset par son chemin absolu, depuis le dossier `presets/<type_id>/`, ou depuis `plugin.get_factory_presets()`, puis applique `plugin.set_state(state)`. |
| `list_presets` | `(plugin_type_id: str, factory_names: Optional[set] = None) -> List[Dict]` | Retourne la liste triée des presets utilisateur sur disque (en excluant les presets d'usine pour éviter les doublons dans l'UI). |
| `delete_preset` | `(plugin_type_id: str, name: str, plugin=None) -> bool` | Supprime un preset utilisateur sur disque (refuse si preset d'usine). |
| `export_preset_to_file` | `(plugin, file_path: str, name: Optional[str] = None) -> bool` | Exporte l'état courant du plugin vers un fichier externe `.json` ou `.ndawpreset`. |
| `import_preset_from_file` | `(plugin, file_path: str, save_to_library: bool = True) -> Optional[str]` | Charge un fichier `.json` / `.ndawpreset` externe dans le plugin et l'ajoute à la bibliothèque utilisateur (sauf si son nom entre en conflit avec un preset d'usine). |

---

## 4. Intégration UI dans `NativePluginDialog` (`ui/plugin_dialogs.py`)

L'intégration des presets dans l'interface graphique suit un contrat unique géré par [`NativePluginDialog`](../ui/plugin_dialogs.py) :
1. **Construction de la liste (`_refresh_preset_list`)** :
   - Élément 0 : `"— Sélectionner un Preset —"` (`data = None`).
   - Presets d'usine : Affichés sous `"✨ [Usine] <Nom>"` avec `data = ("factory", name)`.
   - Presets utilisateur : Affichés sous `"👤 [User] <Nom>"` avec `data = ("user", name)`.
2. **Application immédiate (`_on_preset_combo_changed`)** :
   - Appelle `preset_manager.load_preset(self.plugin, name)`.
   - Appelle `_sync_editor_ui()` (`editor_widget._load_from_plugin()`, `_refresh_ui()`, `update()`) pour mettre à jour tous les potentiomètres/courbes du plugin.
   - Invalide le cache ASIO-Guard (`_notify_audio_engine_state_changed()`) pour que la lecture temps réel reflète immédiatement le preset choisi.
