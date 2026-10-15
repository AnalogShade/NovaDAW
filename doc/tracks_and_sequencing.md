# 🎼 Spécification des Pistes, Clips & Séquençage (Arrangement & Piano Roll)

Ce document décrit en détail le modèle de données des pistes et des clips (`core/project.py`), l'arrangement de la timeline (`ui/timeline_view.py`), les outils d'édition (`ui/editing_toolbar.py`), le séquenceur MIDI Piano Roll (`ui/piano_roll.py`) et l'éditeur audio (`ui/audio_editor.py`).

---

## 1. Modèle des Pistes (`Track`)

La classe `Track` représente une piste dans la structure de NovaDAW.

### 1.1. Typologie des Pistes
- **Piste MIDI (`track_type = "midi"`)** :
  - Reçoit des clips MIDI (`MidiClip`).
  - Pilotée par un instrument virtuel assigné via `plugin_path` (ex: `"novadaw.synth"`, `"novadaw.drum_machine"`, ou un chemin de fichier `.vst3`).
  - **Routage Multi-Sorties & Partage d'Instrument** :
    - `synth_output_bus` (`0` à `9`, affichés `Out 1` à `Out 10`) : Isole l'écoute de la piste sur un bus de sortie spécifique de `NovaSynth`.
    - `synth_route_track_id` : Permet de router une piste MIDI secondaire vers l'instance `NovaSynth` hébergée sur une autre piste maîtresse.
- **Piste Audio (`track_type = "audio"`)** :
  - Reçoit des clips audio (`AudioClip`).
  - Lecture directe de fichiers d'échantillons en mémoire sans instrument intermédiaire.
- **Piste Master (`track_type = "master"`)** :
  - Piste unique créée automatiquement au lancement du projet (`ensure_master_track()`).
  - Reçoit le mixage sommé de l'ensemble des pistes.
  - Héberge la console de mixage `novadaw.mixer` et la chaîne d'effets de mastering (`get_master_insert_plugins()`).

### 1.2. Propriétés, Duplication et Réorganisation d'une Piste
- `id` : Identifiant unique à 8 caractères hexadécimaux.
- `name` : Nom lisible affiché dans l'en-tête et l'inspecteur.
- `color` : Couleur hexadécimale (ex: `#38bdf8` pour synthés, `#f59e0b` pour batterie, `#10b981` pour audio).
- `volume` : Gain linéaire de 0.0 à 1.5 (0.8 par défaut).
- `pan` : Panoramique de -1.0 (100% gauche) à +1.0 (100% droite).
- `muted` / `soloed` / `armed` : États booléens d'écoute et d'armement d'enregistrement.
- `height` : Hauteur d'affichage personnalisable en pixels (**`46 px` à `320 px`**, par défaut **`88 px`**).
- `plugins` : Liste ordonnée d'instances de plugins natifs (`BasePlugin`) insérés sur la tranche.
- `insert_plugins` : Liste ordonnée des identifiants `plugin_type_id` des effets natifs d'insertion.
- `insert_effects` : Liste des chemins vers les effets VST3 externes insérés.
- **Opérations sur les pistes dans `Project`** :
  - `project.duplicate_track(track_id)` (`Ctrl+Shift+D`) : Clone intégralement une piste (clips MIDI/Audio, instances de plugins et leurs états `get_state()`, effets VST3, hauteur, couleur, routage) et l'insère juste en dessous.
  - `project.move_track(track_id, new_index)` (`Alt+Up` / `Alt+Down` ou glisser-déposer `TrackColorGrip`) : Réordonne la piste dans l'arrangement et la console de mixage.

---

## 2. Modèle des Clips et Notes

### 2.1. Les Notes MIDI (`MidiNote`)
Structure élémentaire d'un événement musical :
```python
@dataclass
class MidiNote:
    pitch: int       # Hauteur MIDI de 0 à 127 (60 = Do central / C4)
    start_beat: float # Position temporelle relative au début du clip (en temps)
    duration: float   # Durée de la note (en temps, ex: 1.0 = une noire)
    velocity: int = 100 # Force de frappe de 1 à 127
```

### 2.2. Les Clips MIDI (`MidiClip`)
Conteneur de motifs sur la grille :
- `start_beat` : Position temporelle absolue sur la timeline du projet (en temps).
- `length_beats` : Longueur du bloc (par défaut 4.0 temps = 1 mesure en 4/4).
- `notes` : Liste d'objets `MidiNote`.
- **Méthode `split(split_beat)`** : Scinde proprement le clip en deux sous-clips indépendants. Les notes coupées par la lame de rasoir ont leur durée ajustée ou sont réparties avec précision sur la seconde partie avec un temps recalculé.

### 2.3. Les Clips Audio (`AudioClip`) & Trimming Non-Destructif
Conteneur d'échantillons et fichiers audio :
- `file_path` : Chemin d'accès au fichier audio source.
- `source_offset_beats` : Décalage de lecture à l'intérieur du fichier audio source (en temps).
- `source_duration_beats` : Durée totale du fichier audio source en temps.
- `audio_data` : Matrice NumPy FP32 `(N, 2)` chargée en RAM (non sérialisée dans le JSON).
- **Redimensionnement / Trimming Non-Destructif (`get_max_allowed_length_beats`)** :
  - Redimensionner un `AudioClip` sur la timeline **rogne (trim)** la fenêtre de lecture sans jamais étirer (time-stretch) la forme d'onde.
  - La longueur maximale autorisée est bornée par `source_duration_beats - source_offset_beats`.
- **Cache de Crêtes Waveform 1:1 (`get_slice_waveform_peaks`)** :
  - Calcule les crêtes exactement sur la tranche visible `[source_offset_beats, source_offset_beats + length_beats]` pour que l'affichage graphique corresponde à l'échantillon près à l'audio entendu.

---

## 3. La Vue Timeline & la Grille d'Arrangement (`ui/timeline_view.py`)

La timeline réunit la règle temporelle supérieure (`TimelineRuler`) et la grille de pistes (`TimelineGrid`).

### 3.1. Règle Temporelle & Marqueurs de Boucle
- Affiche la graduation en mesures (`Bar 1`, `Bar 2`, etc.) et en subdivisions de temps.
- **Clic simple** : Déplace la tête de lecture instantanément à la position sélectionnée.
- **Clic-Glisser sur la zone supérieure de la règle** : Définit graphiquement la région de boucle (`loop_start_beat` et `loop_end_beat`).
- La zone bouclée apparaît avec une teinte bleutée translucide sur l'ensemble des pistes.

### 3.2. Aimantage et Résolution de Grille (Snap to Grid)
- Commutable via le raccourci **`J`** ou l'icône aimant dans la barre d'outils.
- Résolutions disponibles :
  - `1 Mesure (Bar)` : 4.0 temps (en 4/4).
  - `1/2 Mesure` : 2.0 temps.
  - `1 Temps (Noire / 1/4)` : 1.0 temps.
  - `1/2 Temps (Croche / 1/8)` : 0.5 temps.
  - `1/4 Temps (Double-Croche / 1/16)` : 0.25 temps.
- Lors du déplacement ou redimensionnement d'un clip, sa position est automatiquement aimantée au cran le plus proche si le Snap est activé.

### 3.3. Défilement Automatique (Follow Playhead)
- Activable/désactivable avec le raccourci **`F`**.
- Lorsque la tête de lecture atteint le bord droit de l'écran, la vue scrolle automatiquement vers la mesure suivante.

---

## 4. Les Outils d'Édition (`ui/editing_toolbar.py`)

Inspirés de la palette d'outils de Cubase Pro :

| Outil | Raccourci | Icône | Action |
| :--- | :--- | :--- | :--- |
| **Pointeur (Select)** | `1` | 🖱️ | Sélectionne, déplace et redimensionne les clips par leurs poignées latérales. |
| **Ciseaux (Split)** | `2` | ✂️ | Scinde immédiatement tout clip cliqué à la position de la souris. |
| **Gomme (Erase)** | `3` | 🗑️ | Supprime en un clic le clip survolé. |

### Raccourcis d'Édition Universels :
- `Ctrl + K` : **Scinder à la tête de lecture** (coupe le clip sélectionné exactement sous le curseur de lecture).
- `Ctrl + D` : **Dupliquer** (crée une copie consécutive immédiate du clip sélectionné).
- `Ctrl + C` / `Ctrl + X` / `Ctrl + V` : **Copier / Couper / Coller** à la position de la tête de lecture.
- `Delete` : Supprime le ou les clips sélectionnés.

---

## 5. Le Séquenceur MIDI : Piano Roll (`ui/piano_roll.py`)

Intégré dans le premier onglet de la zone inférieure :
- **Clavier Virtuel Vertical** : Échelle chromatique complète (de C-2 à G8, 128 touches).
  - Le clic sur une touche joue immédiatement la note sur l'instrument assigné à la piste (`AudioEngine.preview_note`).
- **Grille Piano Roll** :
  - **Clic gauche sur case vide** : Ajoute une note avec la durée et vélocité par défaut.
  - **Clic droit sur une note** : Supprime la note.
  - **Glisser sur le bord droit d'une note** : Ajuste sa durée.
  - **Sélection multiple par rectangle de sélection**.
- **Outils d'Édition Musicale Intégrés** :
  - **Quantification** (`novadaw_quantize_notes`) : Aligne automatiquement les notes sur la grille temporelle (noire, croche, double-croche).
  - **Transposition** (`novadaw_transpose_clip`) : Décale l'ensemble des notes de $\pm N$ demi-tons ou par octave complète ($\pm 12$).

---

## 6. L'Éditeur Audio (`ui/audio_editor.py`)

Dédié à l'inspection et à l'édition des clips audio :
- Visualisation haute résolution de la forme d'onde avec lignes de repère d'amplitude.
- Réglage du gain du clip individuel.
- Visualisation et ajustement du décalage interne (`source_offset_beats`).
- Bouton direct **"Importer un fichier audio"** ouvrant l'explorateur pour remplacer ou charger une nouvelle piste sans quitter l'éditeur.
