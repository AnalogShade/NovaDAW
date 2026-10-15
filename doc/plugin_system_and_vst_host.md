# 🔌 Spécification du Système de Plugins & Hôte VST (`core/plugin_manager.py`, `core/vst_host.py`)

Ce document définit l'architecture complète de découverte, d'hébergement, d'instanciation et de cycle de vie des plugins dans **NovaDAW**. Tout agent intervenant sur le chargement de plugins, le Rack VST, l'assignation aux pistes ou les fenêtres d'édition de plugins doit respecter strictement ces spécifications.

---

## 1. Architecture Duale des Plugins

NovaDAW unifie deux familles de plugins sous un modèle de routage commun dans [`Project`](../core/project.py) et [`Track`](../core/project.py) :

| Caractéristique | Plugins Internes Natifs (`novadaw.*`) | Plugins Externes (`.vst3` / `.dll`) |
| :--- | :--- | :--- |
| **Identifiant (`plugin_path`)** | Chaîne préfixée : `novadaw.synth`, `novadaw.drum_machine`, `novadaw.equalizer`, `novadaw.compressor`, `novadaw.mixer` | Chemin absolu sur le disque vers le binaire `.vst3` ou `.dll` |
| **Registre / Gestionnaire** | [`PluginRegistry`](../plugins/registry.py) (`plugins/registry.py`) | [`PluginManager`](../core/plugin_manager.py) (`global_plugin_manager`) |
| **Classe d'instance** | Sous-classe de [`BasePlugin`](../plugins/base.py) | [`IsolatedPlugin`](../core/vst_host.py) encapsulant `pedalboard.load_plugin` |
| **Interface Graphique (UI)** | Widget PySide6 retourné par `plugin.create_editor()` et encapsulé dans [`NativePluginDialog`](../ui/plugin_dialogs.py) | Éditeur natif OS ouvert via `plugin.show_editor()` ou panneau générique de paramètres [`PluginEditorDialog`](../ui/plugin_dialogs.py) |
| **Persistance d'état** | Dictionnaire JSON (`plugin.get_state()` / `plugin.set_state()`) stocké dans `native_state` et `track.plugins` | Octets `raw_state` encodés en Base64 dans `project.plugin_states` |

---

## 2. Scanner de Plugins, Surveillance Temps Réel & Cache (`core/plugin_manager.py`)

### 2.1 Découverte Multi-Sources
[`PluginManager.get_default_vst_dirs()`](../core/plugin_manager.py) explore automatiquement :
1. **Registre Windows** : `HKLM\SOFTWARE\VST` et `HKCU\SOFTWARE\VST` (`VSTPluginsPath`).
2. **Dossiers standards VST3** : `C:\Program Files\Common Files\VST3`, `C:\Program Files (x86)\Common Files\VST3`, `%LOCALAPPDATA%\Programs\Common\VST3`.
3. **Dossiers standards & constructeurs VST2** : `Common Files\VST2`, `Steinberg\VSTPlugins`, `VstPlugins`, `Native Instruments\VSTPlugins 64 bit`, `Cakewalk\VstPlugins`.
4. **Bundles constructeurs spécifiques** : `C:\ProgramData\Spectrasonics\plug-ins64` (Omnisphere, Keyscape, Trilian, Stylus RMX).
5. **Dossiers personnalisés de l'utilisateur** : Persistés dans `~/.novadaw/plugin_settings.json` (gérés via [`PluginFolderManagerDialog`](../ui/plugin_dialogs.py)).

### 2.2 Cache Incrémental & Surveillance `QFileSystemWatcher`
- **Fichier de cache** : `~/.novadaw/plugins_cache.json`.
- **Clé de validation incrémentale** : Chaque entrée de cache stocke `file_mtime` et `file_size` (calculés via `_get_path_mtime_and_size`, qui inspecte `Contents/x86_64-win/*.vst3` à l'intérieur des bundles `.vst3`).
- **Auto-détection en arrière-plan** :
  - `QFileSystemWatcher` surveille les répertoires VST existants.
  - Tout changement sur le disque déclenche un timer de debounce de **`1200 ms`** (`_watcher_debounce_timer`) qui lance `trigger_background_scan()`.
  - Le scan rapide (`scan_for_new_plugins()`) s'exécute dans un thread daemon sans jamais bloquer l'interface Qt et émet le signal `scan_updated` uniquement si un changement est détecté.
- **Deep Scan (`PluginScanWorker`)** :
  - Accessible depuis [`PluginManagerDialog`](../ui/plugin_dialogs.py) pour inspecter en profondeur les binaires et extraire la liste complète des paramètres (`parameters`).

### 2.3 Détection VST2 Legacy sans Exécution Binaire (`is_vst2_dll`)
- `pedalboard` ne charge nativement que les formats **VST3** (et Audio Unit sur macOS).
- Pour éviter de charger des DLL système aléatoires ou de faire planter le processus, [`is_vst2_dll(file_path)`](../core/plugin_manager.py) inspecte la table d'export PE (`pefile`) sans exécuter le code binaire :
  - Vérifie la présence des symboles d'entrée officiels Steinberg VST 2.x : `VSTPluginMain` ou `main`.
  - Ignore automatiquement les DLL d'infrastructure connues (`vcruntime`, `msvcp`, `qt5`, `d3d`, `libfftw`, etc.).
- Les plugins VST2 détectés reçoivent le statut `" Non supporté (VST2 Legacy)"` et sont classifiés (`Instrument` vs `Effet`) par heuristique de nom/chemin afin d'afficher un diagnostic explicite de migration vers VST3 dans [`ProjectPluginDialog`](../ui/project_plugin_dialog.py).

---

## 3. Cycle de Vie : Rack du Projet vs Instances par Piste

### 3.1 Le Rack de Plugins du Projet (`project.plugin_rack`)
Dans [`Project`](../core/project.py), `project.plugin_rack` est une liste de dictionnaires représentant les plugins montés dans le Rack du projet (affiché dans l'onglet Zone Inférieure [`VstRackWidget`](../ui/vst_rack.py) et la fenêtre flottante `F11` [`FloatingPluginRackDialog`](../ui/floating_plugin_rack.py)) :
```python
{
    "id": "uuid",
    "path": "novadaw.synth" | "C:/.../Plugin.vst3",
    "name": "NovaSynth",
    "category": "Instrument" | "Effet",
    "format": "Interne" | "VST3",
    "vendor": "NovaDAW",
    "native_state": {...}  # Optionnel : état template du plugin dans le rack
}
```
- **Synchronisation automatique** : [`project.sync_rack_plugins()`](../core/project.py) garantit que tout plugin utilisé sur une piste (`track.plugin_path`, `track.insert_effects`, `track.plugins`) ou sur `master_track` figure obligatoirement dans `project.plugin_rack`.
- **Instance Modèle (Template) du Rack** : [`project.get_rack_native_plugin(type_id)`](../core/project.py) maintient une instance dédiée au Rack (dans `_rack_native_plugins`). Lorsqu'un utilisateur édite un plugin depuis le Rack VST avant de l'assigner à une piste, cet état sert de modèle initial lors de la création de l'instance de piste.
- **Protection contre le retrait** : [`VstRackWidget._remove_plugin`](../ui/vst_rack.py) interdit de retirer un plugin du Rack s'il est actuellement assigné à au moins une piste ou au bus Master, et affiche la liste des pistes utilisatrices.

### 3.2 Clés d'Isolation des Instances par Piste (`instance_key`)
Chaque piste doit posséder sa propre instance indépendante de plugin afin que les mémoires de filtres, délais et paramètres ne bavent pas d'une piste à l'autre :
- **Instrument VST3 sur une piste** : `f"track:{track.id}:instrument:{track.plugin_path}"`
- **Effet d'insert VST3 sur une piste** : `f"track:{track.id}:effect:{fx_path}"`
- **Plugin natif sur une piste** : Instance Python distincte stockée dans la liste `track.plugins` (et son `plugin_type_id` référencé dans `track.insert_plugins` ou `track.plugin_path`).

### 3.3 Règle Critique : Chargement Asynchrone & Thread Audio (`get_ready_plugin`)
> [!CAUTION]
> **Le callback audio temps réel (`AudioEngine._audio_callback`) ne doit JAMAIS instancier un plugin VST3 sur le disque.**

- Le moteur audio appelle exclusivement [`global_plugin_manager.get_ready_plugin(path, instance_key)`](../core/plugin_manager.py).
- Si l'instance est déjà chargée dans `_loaded_instances`, elle est retournée immédiatement en $O(1)$.
- Si elle n'est pas encore chargée, `get_ready_plugin()` lance un thread daemon (`_async_load`) en arrière-plan et retourne `None` pour le cycle audio courant (évitant tout décrochage/xrun ou deadlock COM/GUI sous Windows).
- Côté interface graphique, l'ouverture d'un plugin VST3 lourd utilise [`PluginLoadWorker`](../ui/plugin_dialogs.py) avec [`PluginLoadingDialog`](../ui/plugin_dialogs.py) pour garder l'UI fluide.

---

## 4. Fenêtres d'Édition et Conteneur Universel (`ui/plugin_dialogs.py`)

### 4.1 `NativePluginDialog` (Conteneur Universel pour Plugins Internes)
Tous les plugins internes (`novadaw.*`) sont ouverts via [`open_native_plugin_editor(plugin, parent, track)`](../ui/plugin_dialogs.py) qui instancie [`NativePluginDialog`](../ui/plugin_dialogs.py) :
1. **Barre de Presets Unifiée (`native_plugin_preset_bar`)** :
   - `NativePluginDialog` construit automatiquement en haut de la fenêtre :
     - Un sélecteur `combo_presets` listant les presets d'usine (`[Usine] ...`) et les presets utilisateur (`[User] ...`).
     - Quatre boutons d'action : `💾 Enregistrer`, `📂 Importer...`, `📤 Exporter...`, `🗑️ Supprimer`.
   - **Déduplication automatique** : Si le widget retourné par `plugin.create_editor()` contient un sous-widget nommé `preset_container`, `NativePluginDialog` le masque automatiquement (`internal_preset_bar.hide()`) pour éviter toute double barre de presets.
2. **Invalidation du Cache Audio (ASIO-Guard)** :
   - Lorsqu'un preset est chargé ou que la fenêtre d'un plugin se ferme (`closeEvent`), `NativePluginDialog` appelle `audio_engine.invalidate_track_cache(track.id)` (ou `invalidate_all_track_caches()`) afin que le pré-rendu RAM prenne immédiatement en compte le nouveau timbre.

### 4.2 `open_plugin_editor_gui` (Plugins VST3 Externes)
Pour les plugins VST3 externes :
- Si `plugin.has_editor` est vrai, ouvre la fenêtre native du constructeur via `plugin.show_editor()` (ou `open_editor()`), puis capture l'état binaire `raw_state` dans `global_plugin_manager._saved_states` à la fermeture.
- Sinon, ouvre [`PluginEditorDialog`](../ui/plugin_dialogs.py) qui génère dynamiquement des sliders et cases à cocher à partir des paramètres exposés par le VST3.
