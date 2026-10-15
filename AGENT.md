# 🤖 Guide Opérationnel des Agents IA pour NovaDAW (`AGENT.md`)

Ce document est le **guide maître d'instruction et de référence** pour tout agent d'intelligence artificielle (Claude, Cursor, Codex, Gemini, Antigravity) intervenant sur le projet **NovaDAW**.

Son rôle est de permettre à n'importe quel agent de comprendre **immédiatement et sans perte de contexte** comment le système fonctionne, quels fichiers de spécifications consulter avant d'agir, et quelles chaînes de synchronisation maintenir pour garantir **zéro régression**.

---

## 🧭 1. Identité et Nature du Projet

**NovaDAW** est une station de travail audio numérique professionnelle libre et open-source conçue en **Python et PySide6 (Qt 6)**, inspirée de l'ergonomie de **Steinberg Cubase Pro**.
Le logiciel repose sur :
- Un moteur audio temps réel haute précision en virgule flottante 32 bits (**FP32**) avec cache hybride RAM **ASIO-Guard** ([`core/audio_engine.py`](core/audio_engine.py)).
- Une **architecture duale de plugins** :
  1. **Plugins Internes Natifs (`novadaw.*`)** héritant de [`BasePlugin`](plugins/base.py) (`NovaSynth`, `NovaDrumMachine`, `NovaEQ`, `NovaCompressor`, `NovaMixer`) et encapsulés graphiquement par [`NativePluginDialog`](ui/plugin_dialogs.py).
  2. **Plugins Externes (`.vst3` / `.dll`)** découverts et mis en cache par [`PluginManager`](core/plugin_manager.py) et hébergés via [`IsolatedPlugin`](core/vst_host.py).
- Un gestionnaire universel de presets ([`core/preset_manager.py`](core/preset_manager.py)) avec protection stricte des presets d'usine (`NovaDAW Factory`).
- Un gestionnaire matériel intelligent ([`core/hardware_manager.py`](core/hardware_manager.py)) prenant en charge ASIO/WASAPI, le calcul de latence aller-retour (RTL), la compensation d'enregistrement et la détection GPU.
- Une orchestration complète par IA (**44 outils MCP**) via [`ActionRegistry`](core/action_registry.py), [`NovaIpcServer`](core/ipc/ipc_server.py) et [`FastMCP`](core/mcp/server.py).

---

## ⚖️ 2. Les 6 Piliers Fondamentaux de l'Agent NovaDAW

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ 1. PRINCIPE D'INTENTIONNALITÉ ET DE JUSTIFICATION                                      │
│    Chaque modification de code ou d'interface doit avoir une raison d'être liée        │
│    à la demande de l'utilisateur. Zéro changement cosmétique ou accidentel gratuit.    │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ 2. NON-RÉGRESSION ABSOLUE                                                              │
│    Ce qui fonctionne avant ton passage doit continuer de fonctionner après.            │
│    Chaque intervention est validée par l'exécution de la suite de tests automatisés.   │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ 3. COHÉRENCE MODULAIRE (MODULE BLUEPRINT & NATIVEPLUGINDIALOG)                         │
│    Tout plugin interne hérite de BasePlugin et son éditeur est encapsulé par           │
│    NativePluginDialog (qui gère automatiquement la barre unifiée de presets).          │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ 4. ÉVOLUTION NATURELLE ET SYNCHRONISÉE DE L'INTERFACE (UI)                             │
│    L'UI évolue selon la charte Cubase Pro et maintient une synchronisation             │
│    bidirectionnelle stricte (TrackHeader <-> Inspector <-> Mixer <-> TransportBar).    │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ 5. PRÉSERVATION DES CONTRATS (.NDAW v1.1 & SETTINGS.JSON)                              │
│    Tout projet .ndaw existant doit s'ouvrir sans erreur (fallback .get()).             │
│    settings.json est partagé sans écrasement entre HardwareManager et RecentProjects.  │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ 6. SÉCURITÉ AUDIO TEMPS RÉEL ET CACHE ASIO-GUARD                                       │
│    Traitement FP32, zéro instanciation VST dans le callback audio (get_ready_plugin),  │
│    et invalidation systématique du cache ASIO-Guard après modification sonore/MIDI.    │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 🔗 3. La Chaîne de Synchronisation Obligatoire (Les 6 Maillons)

Lorsqu'un agent ajoute ou modifie une fonctionnalité (ex: nouveau paramètre de piste, nouveau routage, nouvel outil d'édition, nouveau paramètre de plugin), il **ne doit jamais s'arrêter à un seul fichier**. Il doit vérifier systématiquement ces **6 maillons** :

1. **Modèle de Données ([`core/project.py`](core/project.py))** :
   - Ajouter la propriété dans `Project`, `Track`, `MidiClip` ou `AudioClip` avec une valeur par défaut sûre.
   - Si la propriété appartient à `Track`, vérifier qu'elle est également copiée dans [`Project.duplicate_track()`](core/project.py).
2. **Sérialisation & Dirty State ([`core/serializer.py`](core/serializer.py), [`ui/main_window.py`](ui/main_window.py))** :
   - Sérialiser le champ dans `to_dict()` et le restaurer dans `from_dict()` avec `.get("champ", defaut)`.
   - Appeler `self._mark_project_dirty()` dans `MainWindow` lors de toute modification utilisateur pour activer la protection de sauvegarde (`_is_dirty`).
3. **Moteur Audio & Cache ASIO-Guard ([`core/audio_engine.py`](core/audio_engine.py))** :
   - Si la modification affecte le son rendu d'une piste (notes MIDI, instrument, bus `synth_output_bus`, piste source `synth_route_track_id`), l'inclure dans [`_compute_track_hash()`](core/audio_engine.py) **et** appeler `audio_engine.invalidate_track_cache(track.id)`.
4. **Synchronisation Bidirectionnelle de l'UI (`ui/*`)** :
   - Mettre à jour tous les composants qui affichent cet état : [`TrackHeaderWidget`](ui/track_header.py) $\leftrightarrow$ [`TrackInspector`](ui/inspector.py) $\leftrightarrow$ [`MixerWidget`](plugins/mixer/widget.py) $\leftrightarrow$ [`MasterFxWidget`](ui/master_fx_rack.py) $\leftrightarrow$ [`TransportBar`](ui/transport_bar.py).
   - Toujours protéger les mises à jour programmatiques par `blockSignals(True/False)` ou `self._updating_ui = True` pour éviter les boucles infinies de signaux Qt.
5. **Exposition IA / MCP ([`core/actions/`](core/actions/))** :
   - Si pertinent pour le pilotage IA, créer ou enrichir l'action décorée par `@action_registry.register` dans `core/actions/` (elle sera automatiquement exposée par `core/mcp/server.py`).
6. **Tests Automatisés (`tests/`)** :
   - Exécuter `pytest` avec `NOVADAW_SILENT_TESTS=1` et ajouter un test couvrant le nouveau comportement.

---

## 🗺️ 4. Matrice de Routage Contextuel ("Si tu touches à X $\rightarrow$ Lis d'abord Y")

Avant d'écrire ou de modifier du code, consulte **toujours** le fichier de spécification correspondant dans [`doc/`](doc/README.md) :

| Domaine de la demande utilisateur | Fichier(s) de spécification à lire en priorité | Fichiers code principaux concernés |
| :--- | :--- | :--- |
| **Règles générales, stabilité, raccourcis clavier** | [📄 `doc/invariants.md`](doc/invariants.md)<br>[📄 `doc/vibe_coding_guidelines.md`](doc/vibe_coding_guidelines.md) | `ui/main_window.py`, `assets/style.qss` |
| **Architecture globale & flux entre couches** | [📄 `doc/architecture.md`](doc/architecture.md) | `main.py`, `core/*`, `ui/*` |
| **Sauvegarde `.ndaw`, Dirty State, Projets Récents, Import Audio** | [📄 `doc/project_and_serialization.md`](doc/project_and_serialization.md) | `core/project.py`, `core/serializer.py`, `core/recent_projects.py`, `core/audio_importer.py` |
| **Moteur Audio, ASIO-Guard, Enregistrement, Boucle, Export WAV** | [📄 `doc/audio_engine_and_transport.md`](doc/audio_engine_and_transport.md) | `core/audio_engine.py`, `ui/transport_bar.py` |
| **Cartes son ASIO/WASAPI, Latence RTL, Panneaux pilotes, GPU** | [📄 `doc/hardware_and_drivers.md`](doc/hardware_and_drivers.md) | `core/hardware_manager.py`, `ui/device_settings_dialog.py` |
| **Pistes, Clips MIDI/Audio, Trimming Audio, Piano Roll, Timeline** | [📄 `doc/tracks_and_sequencing.md`](doc/tracks_and_sequencing.md) | `core/project.py`, `ui/timeline_view.py`, `ui/piano_roll.py`, `ui/audio_editor.py`, `ui/editing_toolbar.py` |
| **Scanner VST3/VST2, Rack Projet vs Instances Pistes, `NativePluginDialog`** | [📄 `doc/plugin_system_and_vst_host.md`](doc/plugin_system_and_vst_host.md) | `core/plugin_manager.py`, `core/vst_host.py`, `ui/vst_rack.py`, `ui/floating_plugin_rack.py`, `ui/plugin_dialogs.py`, `ui/project_plugin_dialog.py` |
| **DSP des 5 Plugins Internes (`Synth`, `Drums`, `EQ`, `Comp`, `Mixer`)** | [📄 `doc/plugins_specification.md`](doc/plugins_specification.md) | `plugins/base.py`, `plugins/synth/`, `plugins/drum_machine/`, `plugins/equalizer/`, `plugins/compressor/`, `plugins/mixer/` |
| **Presets Utilisateur, Presets d'Usine (`Factory`), Import/Export** | [📄 `doc/preset_management.md`](doc/preset_management.md) | `core/preset_manager.py`, `ui/plugin_dialogs.py` |
| **En-têtes de pistes, Inspecteur, Master FX Rack, Sliders, Glow UI** | [📄 `doc/ui_ergonomics_and_sync.md`](doc/ui_ergonomics_and_sync.md) | `ui/track_header.py`, `ui/inspector.py`, `ui/master_fx_rack.py`, `ui/glow_effects.py` |
| **Actions IA, Serveur FastMCP, Pont TCP IPC (44 outils)** | [📄 `doc/mcp_and_automation.md`](doc/mcp_and_automation.md) | `core/action_registry.py`, `core/actions/*`, `core/ipc/*`, `core/mcp/server.py` |
| **Tests unitaires `pytest`, QA Workflow Plugins, Stress Tests** | [📄 `doc/testing_and_qa.md`](doc/testing_and_qa.md)<br>[📄 `tests/PLUGIN_WORKFLOW_QA.md`](tests/PLUGIN_WORKFLOW_QA.md) | `tests/conftest.py`, `tests/test_*.py` |
| **Recettes d'ajout (Nouveau plugin, Nouvelle action MCP)** | [📄 `doc/roadmap_and_evolution.md`](doc/roadmap_and_evolution.md) | `plugins/`, `core/actions/` |

---

## ⚠️ 5. Pièges Techniques Connus ("Gotchas" Spécifiques à NovaDAW)

Tout agent intervenant sur le code doit garder en tête ces subtilités techniques :

1. **Barre de Presets des Plugins Internes (`NativePluginDialog` vs Widget Interne)** :
   - C'est [`NativePluginDialog`](ui/plugin_dialogs.py) qui génère automatiquement la barre supérieure de presets (`native_plugin_preset_bar` : `combo_presets`, `💾 Enregistrer`, `📂 Importer...`, `📤 Exporter...`, `🗑️ Supprimer`) autour de `plugin.create_editor()` et qui **masque** tout widget interne nommé `preset_container`.
   - Le widget retourné par `plugin.create_editor()` doit exposer `_load_from_plugin()` (ou `_refresh_ui()`) pour que `NativePluginDialog` puisse rafraîchir ses contrôles lorsqu'un preset est sélectionné.
2. **Rack du Projet (`project.plugin_rack`) vs Instances de Pistes (`track:<id>:...`)** :
   - Ajouter un plugin au Rack du Projet (`project.add_rack_plugin`) ne l'assigne pas automatiquement à une piste : cela l'ajoute au catalogue du projet (et crée éventuellement une instance template via `project.get_rack_native_plugin`).
   - Lorsqu'un plugin est assigné à une piste, celle-ci utilise sa propre instance isolée (`f"track:{track.id}:instrument:{path}"` ou `f"track:{track.id}:effect:{path}"` pour un VST3, ou une nouvelle instance dans `track.plugins` initialisée avec l'état du template du rack pour un plugin natif).
   - Un plugin ne peut pas être supprimé du Rack tant qu'il est assigné à une piste ou au Master.
3. **Piste Master et Filtrage de `novadaw.mixer`** :
   - `project.master_track` contient toujours `novadaw.mixer` en position 0 dans `master_track.plugins`.
   - Dans [`MasterFxWidget`](ui/master_fx_rack.py), utiliser systématiquement `project.get_master_insert_plugins()` qui filtre `novadaw.mixer` afin de n'afficher et ne réordonner que les effets d'insertion de mastering (EQ, Compresseur, VST3).
4. **Trimming Audio 1:1 Non-Destructif (`AudioClip`)** :
   - Redimensionner un `AudioClip` ne doit **jamais** étirer la forme d'onde : il modifie `length_beats` (borné par `get_max_allowed_length_beats()`) et rafraîchit les crêtes via `get_slice_waveform_peaks(source_offset_beats, length_beats, bpm)`.
5. **Partage de `settings.json`** :
   - `HardwareManager` et `RecentProjectsManager` lisent et écrivent tous deux dans `settings.json`. Toujours charger le dictionnaire JSON existant avant d'écrire une clé pour ne pas effacer les données de l'autre gestionnaire.

---

## 🔄 6. Protocole d'Intervention en 5 Étapes

1. **Cadrage du Besoin** : Identifier l'intention exacte de l'utilisateur (correction chirurgicale, évolution de fonctionnalité, ou création musicale via MCP).
2. **Consultation Ciblée de [`doc/`](doc/README.md)** : Lire le fichier de spécification correspondant dans la Matrice de Routage Contextuel (Section 4).
3. **Implémentation Complète (Les 6 Maillons)** : Appliquer les changements en vérifiant la Chaîne de Synchronisation (Section 3) sans aucun dégât collatéral.
4. **Validation Automatisée (`pytest`)** : Exécuter les tests pertinents en mode silencieux :
   ```powershell
   $env:NOVADAW_SILENT_TESTS="1"; python -m pytest tests/ -q
   ```
5. **Compte-rendu Concis & Orienté Action** : Présenter les fichiers modifiés, la logique appliquée et le résultat des tests.

---

## 🎵 7. Réflexe Automatique pour la Composition Musicale via MCP

Dès que l'utilisateur demande de créer, modifier ou jouer de la musique dans NovaDAW :
1. Utiliser **immédiatement et directement** les outils MCP du serveur `novadaw` (`call_mcp_tool` avec `ServerName: "novadaw"`).
2. Ne **jamais** modifier `core/mcp/server.py` ni écrire de scripts temporaires pour contourner le MCP : le serveur FastMCP et l'IPC sont déjà actifs et opérationnels.
3. Chaîne d'action directe :
   - `novadaw_create_track` (ou `novadaw_get_project_summary`)
   - `novadaw_configure_synth` / `novadaw_set_synth_preset` / `novadaw_configure_drum_machine`
   - `novadaw_create_midi_clip`
   - `novadaw_add_midi_notes`
   - `novadaw_set_bpm` / `novadaw_control_transport` (`action: "play"`)
