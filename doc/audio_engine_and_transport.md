# ⏱️ Spécification du Moteur Audio & de la Barre de Transport

Ce document décrit en détail le fonctionnement interne du moteur audio temps réel (`core/audio_engine.py`), la gestion des périphériques matériels (`core/hardware_manager.py`) et le comportement de la barre de transport (`ui/transport_bar.py`).

---

## 1. Le Moteur Audio Temps Réel (`AudioEngine`)

`AudioEngine` est le cœur du traitement sonore de NovaDAW. Il assure la passerelle entre le modèle de données du projet et les pilotes audio matériels du système d'exploitation Windows.

### 1.1. Pilotes Audio Hôtes & Gestion du Matériel
NovaDAW s'interface avec les pilotes audio via la bibliothèque `sounddevice` (surcouche PortAudio) et `core/hardware_manager.py` :
- **Pilotes Supportés** :
  - **ASIO (Audio Stream Input/Output)** : Pilote de studio ultra-faible latence (< 5 ms).
  - **Windows WASAPI (Windows Audio Session API)** : Pilote natif moderne recommandé en mode exclusif ou partagé.
  - **DirectSound & MME** : Pilotes universels pour compatibilité maximale.
- **Réglages du Buffer & Latence** :
  - Fréquence d'échantillonnage : 44.1 kHz, 48 kHz, 88.2 kHz, 96 kHz, 192 kHz.
  - Taille du tampon buffer : 32, 64, 128, 256, 512, 1024, 2048 échantillons.
  - Calcul de la latence théorique :
    $$\text{Latence (ms)} = \frac{\text{block\_size}}{\text{sample\_rate}} \times 1000$$
    *(Exemple : 512 échantillons à 48 kHz $\approx 10.67\text{ ms}$).*
- **Bouton de Test de Sortie (Test Tone)** :
  Génère une onde sinusoïdale pure à 440 Hz ($A4$) d'une durée de 1.5 seconde avec fondu progressif pour valider instantanément la chaîne d'écoute.

> [!NOTE]
> Pour l'auto-configuration ASIO/WASAPI, le calcul des latences RTL, les panneaux constructeurs et la détection GPU, consultez [**`doc/hardware_and_drivers.md`**](hardware_and_drivers.md).

### 1.2. Mécanisme ASIO-Guard & Cache Hybride en RAM (`_track_audio_caches`)
Pour concilier latence ultra-faible lors de l'édition et charge CPU modérée lors de la lecture d'un grand nombre de pistes :
- **Pré-rendu progressif (`_ensure_tracks_cached_async`)** : Un seul worker daemon (`NovaDAW-AsioGuard-PreRender`) regroupe les demandes. `_iter_track_offline` rend les instruments natifs par blocs de 4096 échantillons, en alternant les pistes. Chaque préfixe terminé est publié immédiatement dans `_track_audio_caches`, avec `rendered_frames`; il est utilisable sans attendre le rendu complet du morceau. `_render_track_offline` conserve son API synchrone pour les appels existants.
- **Isolation et annulation** : Les notes et réglages des instruments natifs sont copiés pour le précalcul; les états DSP en direct restent indépendants. Un numéro de génération empêche la publication de résultats obsolètes après invalidation, fermeture ou changement de projet. Les pistes MIDI routées sont invalidées avec leur instrument source. Les VST externes restent sur leur chemin existant et ne sont pas remplacés par un cache silencieux.
- **Lecture sûre** : Un bloc ne provient du cache que si tous les échantillons nécessaires sont disponibles, y compris les deux côtés d'une boucle. Sinon, le moteur utilise le rendu en direct. Les inserts et le Master restent traités en temps réel; les pistes armées ne passent pas par le cache.
- **Signature de Validation (`_compute_track_hash`)** : Calcule une empreinte MD5 incluant `track.id`, `track.plugin_path`, `bpm`, `sample_rate`, l'état complet `native_inst.get_state()` (de la piste ou de la piste cible `synth_route_track_id`) ainsi que la position/durée/vélocité de toutes les notes et clips.
- **Contrat d'Invalidation Obligatoire** :
  - Toute modification de notes MIDI, de position de clip ou de paramètre d'instrument virtuel doit appeler **`audio_engine.invalidate_track_cache(track_id)`** (ou `invalidate_all_track_caches()`), ce qui déclenche un recalcul asynchrone avec debounce (`0.35 s` via `_schedule_debounce_caching`).
- **Préparation automatique** : L'ouverture du projet et les invalidations déclenchent la préparation en arrière-plan. `play()` reste non bloquant : il lance le transport, préchauffe les instruments et demande le cache sans créer un deuxième worker. La disponibilité effective dépend du projet, du matériel et de la charge; aucune absence universelle de coupure n'est garantie.
- **Mesure reproductible** : `python scripts/benchmark_audio_startup.py --project "chemin/session.ndaw" --baseline-ref 3f1b250 --output scratch/comparaison.json`. Le test est silencieux et mesure des callbacks logiciels cadencés; il ne mesure pas la latence du pilote physique.

### 1.3. Prévisualisation Temps Réel Isolée & Choke Groups (`preview_note`, `play_preview_buffer`)
- **`_process_track_effects_isolated(track, audio, native_inst)`** : Applique tous les effets d'insert de la piste (VST3 + plugins natifs EQ/Compresseur) sur une note prévisualisée en sauvegardant/restaurant les états continus des filtres IIR (`zi_left`, `zi_right` de `NovaEQ`) si la lecture est active, évitant tout clic audio.
- **`play_preview_buffer(wave, choke_group, track)`** : Injecte un buffer stéréo dans `_preview_buffers` avec support des **Choke Groups** (ex: un Hi-Hat fermé coupe immédiatement un Hi-Hat ouvert du même `choke_group` avec un fondu de 5 ms).

### 1.4. Boucle Audio Temps Réel (`_audio_callback`)
La fonction de rappel est cadencée par le pilote matériel :
```python
def _audio_callback(self, outdata, frames, time_info, status):
    # 1. Traitement des sons de prévisualisation (_preview_buffers avec choke_group)
    # 2. Rendu et sommation des pistes actives si is_playing == True (depuis cache ASIO-Guard ou live)
    # 3. Avancement de la tête de lecture (current_beat) et gestion du rebouclage
    # 4. Traitement du bus Master (novadaw.mixer, effets d'insert VST3 & natifs)
    # 5. Mesure des niveaux de crête (_master_peak_l/r) et détection de distorsion (> 1.0)
    # 6. Écrêtage doux analogique np.tanh(out)
    # 7. Écriture dans outdata
```

---

## 2. La Barre de Transport (`TransportBar`)

Positionnée au centre de la fenêtre principale, au-dessus des onglets de la zone inférieure, la barre de transport pilote l'ensemble de la lecture et de la synchronisation.

### 2.1. États de Transport et Raccourcis Associés

| Commande | Raccourci Clavier | Action & Comportement |
| :--- | :--- | :--- |
| **Play / Pause** | `Espace` | Bascule entre lecture active et pause. La tête de lecture s'arrête à sa position courante. |
| **Stop** | `0` (Pavé numérique) | Arrête la lecture et repositionne la tête de lecture : au début de la boucle si la boucle est active, sinon à 0.0. Réinitialise tous les plugins (`reset()`). |
| **Enregistrement (Record)** | `R` | Déclenche ou arrête la capture audio/MIDI sur les pistes armées tout en activant la lecture. |
| **Boucle (Loop)** | `L` | Active ou désactive le mode boucle entre `loop_start_beat` et `loop_end_beat`. |
| **Aller au Début** | `Home` | Repositionne le curseur à 0.0. |
| **Aller à la Fin** | `End` | Repositionne le curseur après le dernier clip du projet. |
| **Pas Arrière / Avant** | `Ctrl + Gauche` / `Ctrl + Droite` | Déplace la tête de lecture d'une mesure entière (4 temps en 4/4). |

### 2.2. Gestion Précise de la Boucle (Loop Engine)
- Lorsque `loop_enabled = True` et `end_beat >= loop_end_beat`, le moteur audio effectue un rebouclage continu :
  $$\text{overshoot} = \text{end\_beat} - \text{loop\_end\_beat}$$
  $$\text{current\_beat} = \text{loop\_start\_beat} + (\text{overshoot} \pmod{\text{loop\_length}})$$
- Cela garantit une transition sans aucun clic ni silence entre la fin et le début de la région de boucle.

### 2.3. Tempo (BPM) et Métrique
- **BPM Réglable** : De 20.0 à 300.0 BPM. La modification du BPM à chaud ajuste immédiatement le calcul temporel du moteur audio sans interrompre le flux sonore.
- **Signature Rythmique** : Par défaut 4/4. Calcul du nombre de temps par mesure :
  $$\text{beats\_per\_bar} = \text{num} \times \frac{4}{\text{den}}$$

### 2.4. Indicateur Master & VU-Mètre Crête
- Affichage en temps réel des crêtes stéréo Gauche / Droite en dBFS.
- Témoin de clipping rouge dès que le signal dépasse $0\text{ dBFS}$ ($> 1.0$), persistant pendant 1.8 seconde pour alerter l'utilisateur et réinitialisable par clic.

---

## 3. Système d'Enregistrement Direct

### 3.1. Enregistrement Audio (Microphone / Entrée Ligne)
- **Capture Dédiée** : `AudioEngine._input_stream` capture les blocs audio physiques en tâche de fond dans `_recorded_audio_blocks` sous protection `_record_lock`.
- **Rééchantillonnage Automatique** : Si le microphone fonctionne à une fréquence différente du projet (ex: micro USB à 48 kHz sur un projet à 44.1 kHz), NovaDAW rééchantillonne automatiquement le signal via filtre polyphase SciPy (`resample_poly`) ou interpolation linéaire sans perte.
- **Compensation de Latence Matérielle (Record Offset)** :
  Décale les échantillons enregistrés de `record_latency_compensation_samples` pour aligner parfaitement la prise avec la grille temporelle existante.

### 3.2. Enregistrement MIDI en Direct
- Lors de l'écoute sur une piste MIDI armée (`armed = True`), les frappes de notes (Piano Roll interactif ou contrôleur externe) sont horodatées relativement au temps de départ de l'enregistrement et insérées sous forme d'objets `MidiNote` dans un nouveau `MidiClip`.

---

## 4. Exportation Mixdown Hors-Ligne (Offline WAV Render)

La fonction d'exportation (`novadaw_export_wav` / `Ctrl+E`) réalise un rendu haute fidélité du projet complet :
1. **Calcul de la Durée Totale** : Analyse de la fin du dernier clip audio ou MIDI.
2. **Traitement par Blocs Sans Contrainte Temps Réel** : Rendu à vitesse maximale (non limité à la vitesse 1x de la carte son).
3. **Application Complète des Chaînes de Plugins** : Rendu des instruments virtuels, traitement par chaque effet d'insert, sommation dans le bus Master et passage par les effets de mastering.
4. **Écriture WAV FP32 ou PCM 24-bit** via `soundfile.write`.
