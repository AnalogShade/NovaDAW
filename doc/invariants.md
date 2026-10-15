# 🛡️ Invariants Systèmes, Cohérence & Intentionnalité dans NovaDAW

Ce document définit les **invariants d'ingénierie** du projet NovaDAW.
Ces règles ne constituent pas une bureaucratie restrictive, mais le socle garantissant la **stabilité**, la **non-régression** et la **cohérence** du logiciel au fil de son évolution.

---

## 1. Intentionnalité & Stabilité de l'Interface Utilisateur (UI & UX)

L'interface de NovaDAW est inspirée des standards professionnels de **Cubase Pro**. Elle doit concilier **stabilité ergonomique** et **capacité d'évolution naturelle**.

### 1.1. Le Principe d'Intentionnalité Graphique
- **Règle fondamentale** : Chaque modification apportée à l'interface doit avoir une **raison d'être directe et explicite**.
  - Si l'utilisateur demande une retouche d'algorithme DSP ou un calcul logique, l'interface existante doit rester rigoureusement inchangée (aucun bouton redimensionné, aucune couleur modifiée accidentellement).
  - Si l'utilisateur demande d'ajouter ou d'étendre une fonctionnalité (ex: nouveau paramètre, sélecteur de preset, nouvel outil), l'interface **peut et doit être enrichie** de manière harmonieuse en respectant la charte graphique en place (`style.qss`, thème sombre, typographie).

### 1.2. Agencement Tri-Zones & Hiérarchie Visuelle
- **Zone Supérieure (Upper Zone)** :
  - **Barre de marque** (`#nova_brand_bar`, hauteur 38 px) : Logo cosmique Supernova, libellé `NOVADAW`, badge `PRO`, boutons d'accès rapide (`+ Piste`, `Mixeur`, `Plugins F11`, `Périphériques Ctrl+,`) et indicateur d'état IA MCP (`#34d399`).
  - **Barre d'outils d'édition** (`EditingToolbar`) : Outils Pointeur (`1`), Ciseaux (`2`), Gomme (`3`), Scinder curseur (`Ctrl+K`), Dupliquer (`Ctrl+D`), Snap (`J`), Résolution de grille, Zoom.
- **Zone Centrale (Arranger Zone)** :
  - **Inspecteur de Piste à gauche** (`TrackInspector`, largeur ~325 px, bascule via `F2` ou bouton `◀`) : Présente les propriétés de la piste sélectionnée, routage, inserts d'effets, fader de volume et pan.
  - **Panneau des En-têtes** (`TrackHeaderWidget`, min 260 px, max 550 px) : Boutons Mute (`M`), Solo (`S`), Enregistrement (`R`), Volume slider, Pan, couleur de piste, nom éditable.
  - **Timeline & Grille** (`TimelineRuler`, `TimelineGrid`) : Règle graduée en mesures/temps, affichage des clips audio et MIDI, tête de lecture verticale, zone de boucle ombrée.
- **Zone Inférieure (Lower Zone)** :
  - **Barre de Transport intégrée** (`TransportBar`) : Placée au centre au-dessus des onglets, regroupant Play (`Espace`), Stop (`0`), Record (`R`), Loop (`L`), BPM, signature rythmique, position temporelle et VU-mètre Master crête.
  - **Onglets d'Édition** (`QTabWidget` avec bouton de minimisation `▼` / `▲`, bascule `F3`) :
    1. 🎹 **Séquenceur MIDI (Piano Roll)** (`PianoRoll`)
    2. 🔊 **Éditeur Audio** (`AudioEditor`)
    3. 🎛️ **Mixeur (Console de Mixage)** (`F5`)
    4. 👑 **Effets Master (Master FX)** (`F6`)
    5. 🎛️ **Plugins du Projet (Rack VST)** (`F11`)

### 1.3. Préservation des Raccourcis Clavier Usuels
Les raccourcis suivants sont standardisés pour préserver les automatismes de l'utilisateur :
- `Espace` : Démarrer / Mettre en pause la lecture.
- `0` (Pavé numérique) : Arrêt (Stop) et retour au repère de boucle ou au début.
- `L` : Activer / Désactiver la région de boucle.
- `R` : Activer / Désactiver l'enregistrement.
- `F2` : Afficher / Masquer l'Inspecteur de Piste.
- `F3` : Afficher / Masquer l'Éditeur Inférieur (Lower Zone).
- `F5` : Ouvrir la Console de Mixage (Mixeur).
- `F6` : Ouvrir le Rack d'Effets Master.
- `F11` : Ouvrir le Rack de Plugins Flottant.
- `1` / `2` / `3` : Outils Pointeur / Ciseaux / Gomme.
- `Ctrl + T` : Ajouter une nouvelle piste.
- `Ctrl + M` : Inspecter la piste Master.
- `Ctrl + S` / `Ctrl + O` : Enregistrer / Ouvrir un projet.
- `Ctrl + I` / `Ctrl + E` : Importer un fichier audio / Exporter le mixage en WAV.
- `Ctrl + K` : Scinder le clip sous la tête de lecture.
- `Ctrl + D` : Dupliquer le clip sélectionné.
- `J` : Activer / Désactiver l'aimantage magnétique (Snap).

---

## 2. Invariants Moteur Audio & Traitement DSP

### 2.1. Précision Numérique et Format de Données
- Le traitement audio interne est en **virgule flottante 32 bits (FP32)** : `np.float32`.
- Tout buffer audio stéréo est représenté sous la forme `(N, 2)` (canaux gauche et droite).
- Les signaux mono sont dupliqués en stéréo via `np.column_stack((mono, mono))`.

### 2.2. Protection du Bus Master et Sommation
- Sommation continue des pistes actives (respect des mutes et solos).
- Application en fin de chaîne Master d'un écrêtage doux non-linéaire :
  ```python
  out = np.tanh(out)
  ```
  Cela protège l'audition et les convertisseurs physiques contre toute distorsion numérique brutale ($\pm 1.0$).

### 2.3. Continuité Temporelle et Zéro-Clic
- Tout filtre numérique (égaliseur paramétrique biquad IIR, filtres d'état variable Cytomic SVF) conserve son vecteur d'état (`zi_left`, `zi_right`, intégrateurs) d'un bloc à l'autre.
- La réinitialisation des mémoires (`reset()`) n'intervient qu'en cas d'arrêt explicite (`stop()`) ou de saut de lecture (`seek_beat()`).

### 2.4. Thread-Safety du Moteur Audio
- Le thread temps réel géré par `sounddevice` ne doit contenir aucune opération bloquante d'I/O disque ni d'appel direct aux widgets Qt.
- Les données partagées entre l'UI et le thread audio (prévisualisation de notes, capture micro) sont protégées par des verrous `threading.Lock()` légers.

---

## 3. Cohérence Modulaire des Plugins & Presets

Tout plugin audio (effet ou instrument) répond au **Module Blueprint** de NovaDAW :
1. **Interface Commune (`BasePlugin`)** : Hérite de `BasePlugin` (`plugins/base.py`) avec `process()`, `get_state()`, `set_state()`, `reset()`, `create_editor()`.
2. **Conteneur Universel de Fenêtre (`NativePluginDialog`)** :
   - Toute fenêtre d'édition de plugin interne ouverte via `open_native_plugin_editor()` est encapsulée par `NativePluginDialog` (`ui/plugin_dialogs.py`), qui injecte automatiquement la barre supérieure unifiée (`combo_presets`, `💾 Enregistrer`, `📂 Importer...`, `📤 Exporter...`, `🗑️ Supprimer`) et masque tout `preset_container` interne redondant.
3. **Protection Absolue des Presets d'Usine (`Factory Presets`)** :
   - Les presets définis par `get_factory_presets()` ou marqués `"author": "NovaDAW Factory"` sont en lecture seule stricte (`PresetManager.is_factory_preset` interdit tout écrasement ou suppression).
4. **Isolation des Instances & Chargement VST3 Asynchrone** :
   - Chaque piste possède ses propres instances de plugins (`track:{track.id}:instrument:{path}` / `track:{track.id}:effect:{path}`), distinctes du modèle présent dans `project.plugin_rack`.
   - **Interdiction d'instancier un VST3 sur le thread audio** : Le callback audio utilise exclusivement `global_plugin_manager.get_ready_plugin()`, qui charge les binaires VST3 en arrière-plan sans jamais bloquer le flux temps réel.
5. **Invalidation du Cache ASIO-Guard** :
   - Toute modification de paramètre d'instrument virtuel, de routage `synth_output_bus` / `synth_route_track_id` ou de notes MIDI doit appeler `audio_engine.invalidate_track_cache(track_id)`.

---

## 4. Modèle de Données & Persistance (`.ndaw` & `settings.json`)

### 4.1. Structure du Modèle
- Dataclasses : `Project`, `Track`, `MidiClip`, `AudioClip`, `MidiNote`.
- Les méthodes `to_dict()` et `from_dict()` assurent la sérialisation au format JSON lisible (`.ndaw` v1.1).
- La piste Master (`master_track`) est garantie d'exister en permanence via `ensure_master_track()`.

### 4.2. Rétrocompatibilité Totale des Projets
- Tout projet `.ndaw` sauvegardé par une version antérieure doit pouvoir être rechargé sans perte.
- Tout nouveau champ ajouté au modèle de données doit comporter une valeur par défaut sûre dans `dataclass` et être extrait via `.get("champ", defaut)` dans `from_dict()`.
- Les données audio volumineuses (`audio_data`) ne sont jamais intégrées en base64 dans le JSON : seul le chemin de fichier source et les offsets (`source_offset_beats`, `source_duration_beats`) sont persistés.

### 4.3. Cohabitation Non-Destructive dans `settings.json`
- Le fichier `settings.json` est partagé entre `HardwareManager` (clés `"audio"` et `"graphics"`) et `RecentProjectsManager` (clé `"recent_projects"`). Toute écriture dans `settings.json` doit d'abord fusionner le dictionnaire existant sur disque.

---

## 5. Découplage des Actions et Orchestration IA

- **Décorateur Central** : Toute action manipulable par l'IA ou par des raccourcis est enregistrée via `@action_registry.register` dans le sous-dossier `core/actions/`.
- **Injection d'Application** : Le premier paramètre de la fonction d'action est conventionnellement `app` (instance de `MainWindow`), permettant une exécution thread-safe sur le thread Qt.
- **Auto-Découverte FastMCP** : Le serveur FastMCP expose dynamiquement les actions sans qu'il soit nécessaire d'altérer la configuration du serveur pour chaque nouvelle commande.
