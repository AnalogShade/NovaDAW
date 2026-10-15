# 📁 Spécification du Projet, Sérialisation `.ndaw`, Dirty State & Projets Récents

Ce document définit le cycle de vie d'un projet **NovaDAW**, le format de fichier `.ndaw` (JSON v1.1), la détection des modifications non enregistrées (`is_dirty`), la gestion des projets récents (`RecentProjectsManager`) et l'importation de fichiers audio.

---

## 1. Modèle de Projet (`core/project.py`)

La classe [`Project`](../core/project.py) est la racine unique de l'état musical du DAW.

### 1.1 Modes d'Initialisation
- **[`Project.create_empty()`](../core/project.py)** *(Mode par défaut au démarrage de `MainWindow`)* :
  - Crée un projet vierge `"Sans titre"` à `120.0 BPM`, signature `4/4`, région de boucle `0.0 -> 16.0` temps (`loop_enabled = True`), 0 piste ordinaire (`tracks = []`), et une piste Master (`master_track`) pré-équipée d'une instance active de `novadaw.mixer` (`NovaMixer`).
- **[`Project.create_demo()`](../core/project.py)** *(Accessible via Fichier > Charger le Projet Démo ou MCP `novadaw_load_demo_project`)* :
  - Crée un projet pré-arrangé avec pistes MIDI (`Lead Synth`, `Bassline`, `Chords Pad`), piste `Audio / Drums`, clips MIDI de démonstration et piste Master.

---

## 2. Spécification du Format `.ndaw` v1.1 (`core/serializer.py`)

Les projets sont sauvegardés via [`save_project(project, file_path)`](../core/serializer.py) et chargés via [`load_project(file_path)`](../core/serializer.py) en JSON UTF-8 indenté (`indent=2`).

### 2.1 Schéma JSON Complet (`.ndaw` v1.1)
```json
{
  "version": "1.1",
  "name": "Mon Projet",
  "bpm": 120.0,
  "time_signature": [4, 4],
  "loop_enabled": true,
  "loop_start_beat": 0.0,
  "loop_end_beat": 16.0,
  "plugin_rack": [
    {
      "id": "uuid",
      "path": "novadaw.synth",
      "name": "NovaSynth",
      "category": "Instrument",
      "format": "Interne",
      "vendor": "NovaDAW",
      "native_state": { "enabled": true, "...": "..." }
    }
  ],
  "plugin_states": {
    "track:uuid:instrument:C:/.../Plugin.vst3": "BASE64_RAW_VST_STATE=="
  },
  "master_track": {
    "id": "master",
    "name": "Master",
    "track_type": "master",
    "volume": 0.9,
    "pan": 0.0,
    "muted": false,
    "color": "#ef4444",
    "plugin_path": null,
    "insert_effects": [],
    "insert_plugins": ["novadaw.mixer"],
    "plugins_state": [
      { "type_id": "novadaw.mixer", "state": { "enabled": true, "...": "..." } }
    ]
  },
  "tracks": [
    {
      "id": "uuid",
      "name": "Lead Synth",
      "track_type": "midi",
      "volume": 0.8,
      "pan": 0.0,
      "muted": false,
      "soloed": false,
      "armed": false,
      "color": "#3b82f6",
      "height": 88,
      "plugin_path": "novadaw.synth",
      "synth_output_bus": 0,
      "synth_route_track_id": null,
      "insert_effects": [],
      "insert_plugins": ["novadaw.equalizer"],
      "plugins_state": [
        { "type_id": "novadaw.synth", "state": { "...": "..." } },
        { "type_id": "novadaw.equalizer", "state": { "...": "..." } }
      ],
      "clips": [
        {
          "type": "midi",
          "id": "uuid",
          "name": "Mélodie",
          "start_beat": 0.0,
          "length_beats": 8.0,
          "color": "#3b82f6",
          "notes": [
            { "pitch": 60, "start_beat": 0.0, "duration": 1.0, "velocity": 100 }
          ]
        },
        {
          "type": "audio",
          "id": "uuid",
          "name": "Guitar Stem",
          "start_beat": 0.0,
          "length_beats": 4.0,
          "file_path": "C:/.../guitar.wav",
          "color": "#10b981",
          "sample_rate": 44100,
          "source_offset_beats": 0.0,
          "source_duration_beats": 16.0
        }
      ]
    }
  ]
}
```

### 2.2 Règles de Rétrocompatibilité et Désérialisation
1. **Fallback `.get()` obligatoire** : Tout nouveau champ ajouté à `Project`, `Track`, `MidiClip` ou `AudioClip` doit être lu dans [`from_dict`](../core/serializer.py) via `data.get("champ", valeur_par_defaut)` pour que les fichiers `.ndaw` v1.0 continuent de s'ouvrir sans erreur.
2. **Reconstruction des clips Audio** : Lors du chargement (`load_project`), si `clip.file_path` existe sur le disque, [`load_audio_file`](../core/audio_importer.py) recharge automatiquement `audio_data` et recalcule les crêtes graphiques (`waveform_peaks`) correspondant à la tranche `[source_offset_beats, source_offset_beats + length_beats]`.
3. **Reconstruction des états VST3 & Natifs** :
   - Les états binaires VST3 (`plugin_states`) sont décodés depuis Base64 et restaurés dans `global_plugin_manager`.
   - Les plugins natifs (`plugins_state`) sont réinstanciés via `plugin_registry.create_plugin(type_id)` et restaurés via `plugin.set_state(state)`.

---

## 3. Gestion du Dirty State & Protection contre la Perte de Données (`ui/main_window.py`)

[`MainWindow`](../ui/main_window.py) suit en permanence si le projet comporte des modifications non enregistrées :

- **Empreinte d'état (`_capture_project_snapshot`)** : Sérialise l'état courant du projet via `project_to_dict(self.project)` en JSON trié (`sort_keys=True`) et le compare à `_saved_project_snapshot`.
- **Marquage (`_mark_project_dirty`)** : Met `_is_dirty = True` et ajoute un astérisque `*` dans le titre de la fenêtre (`NovaDAW - Mon Projet *`).
- **Garde-fou avant fermeture ou remplacement (`_confirm_save_if_dirty`)** :
  - Appelé systématiquement avant : `new_project()`, `load_demo_project()`, `open_project_dialog()`, `open_recent_project()`, et `closeEvent()`.
  - Affiche une boîte de dialogue `QMessageBox` avec 3 boutons : **Enregistrer** (`Save`), **Ne pas enregistrer** (`Discard`), **Annuler** (`Cancel`).
  - **Hook de test unitaire** : En environnement de test, `window._test_save_prompt_response` peut être fixé à `"save"`, `"discard"` ou `"cancel"` pour tester tous les scénarios sans bloquer sur une fenêtre modale.

---

## 4. Gestionnaire de Projets Récents (`core/recent_projects.py`)

La classe [`RecentProjectsManager`](../core/recent_projects.py) (singleton `recent_projects_manager`) gère l'historique des fichiers `.ndaw` ouverts ou enregistrés :

1. **Ordre MRU (Most Recently Used) & Limite** : Conserve au maximum **`MAX_RECENT_PROJECTS = 10`** chemins absolus, le plus récent en position `0`.
2. **Normalisation insensible à la casse** : Utilise `_normalize_path()` (`os.path.normcase(os.path.normpath(os.path.abspath(p)))`) pour éviter les doublons sous Windows (`C:\Projet.ndaw` vs `c:\projet.ndaw`).
3. **Gestion des fichiers introuvables** : Lorsqu'un utilisateur clique sur un projet récent supprimé ou déplacé sur le disque, `MainWindow.open_recent_project` propose de le retirer immédiatement de la liste (`remove_project(path)`).

---

## 5. Importateur Audio Universel (`core/audio_importer.py`)

[`load_audio_file(file_path, target_sr, num_peaks=400)`](../core/audio_importer.py) prend en charge l'importation multi-format (`WAV`, `FLAC`, `OGG`, `AIFF`, `MP3`, `M4A`, `AAC`) :
1. **Décodage primaire** : Utilise `soundfile.read(file_path, dtype="float32", always_2d=True)`.
2. **Décodage de repli (`_decode_with_miniaudio`)** : Si `libsndfile` échoue sur un conteneur compressé (`MP3`/`M4A`/`AAC`), bascule automatiquement sur `miniaudio.decode_file(..., output_format=SampleFormat.FLOAT32, nchannels=2, sample_rate=target_sr)`.
3. **Normalisation Stéréo & Rééchantillonnage** : Convertit tout signal mono en stéréo `(N, 2)` `float32` et rééchantillonne vers `target_sr` via `scipy.signal.resample_poly` (ou interpolation linéaire NumPy en fallback).
4. **Tranches d'onde fidèles 1:1 (`get_slice_waveform_peaks`)** : Calcule les pics d'amplitude exactement sur l'intervalle `[start_offset_beats, start_offset_beats + length_beats]` afin que le redimensionnement d'un clip audio sur la timeline **rogne (trim)** le clip sans jamais déformer ni étirer visuellement la forme d'onde.
