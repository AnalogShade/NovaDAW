# 🖥️ Spécification de l'Ergonomie UI, des Composants Personnalisés & de la Synchronisation (`ui/*`)

Ce document définit l'architecture de l'interface graphique PySide6 de **NovaDAW** (inspirée de **Steinberg Cubase Pro**), les composants interactifs réutilisables, le Rack Master FX et les contrats de synchronisation bidirectionnelle entre les vues.

---

## 1. Architecture Spatiale de `MainWindow` (`ui/main_window.py`)

La fenêtre principale ([`MainWindow`](../ui/main_window.py)) est divisée en **4 zones ergonomiques** :

1. **Barre Supérieure Fixe** :
   - [`TransportBar`](../ui/transport_bar.py) : Play (`Space`), Stop, Pause, Record (`R` / `*`), Loop (`L`), Sélecteur BPM (`20–300`), Signature rythmique, Compteur Temporel (`Mesure.Temps.Sous-temps` & `MM:SS.ms`), Fader de Volume Master et VU-mètre Master stéréo avec voyant d'écrêtage (`CLIP`).
   - [`EditingToolbar`](../ui/editing_toolbar.py) : Outils d'édition (`1` Pointeur/Select, `2` Ciseaux/Split, `3` Gomme/Erase), boutons Couper/Copier/Coller/Dupliquer/Scinder, Aimantage (`J` Snap), Grille (`1/1`, `1/2`, `1/4`, `1/8`, `1/16`), Auto-Scroll (`F`) et Zoom horizontal (`-` / `+`).
2. **Panneau Latéral Gauche (`TrackInspector`)** :
   - [`TrackInspector`](../ui/inspector.py) (`Ctrl+I` ou bouton inspecteur) : Affiche et contrôle en détail la piste sélectionnée (ou la piste **Master**) : Nom, Couleur, Volume, Panoramique, boutons `M`/`S`/`R`, Instrument virtuel assigné (`plugin_path`), Routage multi-sorties (`synth_output_bus` `Out 1..10`, `synth_route_track_id`) et Chaîne d'effets d'insertion (`insert_plugins` / `insert_effects`).
3. **Zone Centrale d'Arrangement (Timeline)** :
   - Colonne des en-têtes de pistes ([`TrackHeaderWidget`](../ui/track_header.py)) synchronisée verticalement (`verticalScrollBar`) avec la grille d'arrangement ([`TimelineGrid`](../ui/timeline_view.py)) et surmontée de la règle temporelle ([`TimelineRuler`](../ui/timeline_view.py)).
4. **Zone Inférieure à Onglets (`lower_tabs`, repliable via `F12`)** :
   - Onglet 0 : **Éditeur MIDI (`PianoRoll`)** ([`ui/piano_roll.py`](../ui/piano_roll.py))
   - Onglet 1 : **Éditeur Audio (`AudioEditor`)** ([`ui/audio_editor.py`](../ui/audio_editor.py))
   - Onglet 2 : **Console de Mixage (`MixerWidget`)** ([`plugins/mixer/widget.py`](../plugins/mixer/widget.py), raccourci `F3`)
   - Onglet 3 : **Rack de Plugins (`VstRackWidget`)** ([`ui/vst_rack.py`](../ui/vst_rack.py), ou version flottante via `F11` [`FloatingPluginRackDialog`](../ui/floating_plugin_rack.py))
   - Onglet 4 : **Master FX (`MasterFxWidget`)** ([`ui/master_fx_rack.py`](../ui/master_fx_rack.py), raccourci `F6` / `Ctrl+M`)

---

## 2. Composants Interactifs Sur-Mesure

### 2.1 `ResetableSlider` (`ui/track_header.py`, `ui/inspector.py`)
Sous-classe de `QSlider` prenant en charge une valeur nominale (`default_value`) :
- **Comportement** : Un **double-clic gauche** sur le curseur le réinitialise instantanément à sa valeur nominale :
  - Volume de piste : **`80`** (`0.8` / `80%`)
  - Panoramique : **`0`** (`Centre / C`)

### 2.2 `CompactNumEdit` (`ui/track_header.py`, `ui/inspector.py`)
Sous-classe de `QLineEdit` conçue pour la saisie rapide au clavier de valeurs numériques de mixage :
- Devient éditable au clic ou au focus, sélectionne tout le texte automatiquement (`QTimer.singleShot(0, self.selectAll)`), et valide sur `Entrée` ou perte de focus (`editingFinished`).
- **Formats reconnus pour le Volume** ([`_parse_volume_text`](../ui/track_header.py)) :
  - Pourcentage : `"80%"` ou `"80"` $\rightarrow$ `0.80`
  - Décimal normalisé : `"0.75"` $\rightarrow$ `0.75`
- **Formats reconnus pour le Panoramique** ([`_parse_pan_text`](../ui/track_header.py)) :
  - Centre : `"C"`, `"CENTER"`, `"CENTRE"`, `"0"` $\rightarrow$ `0.0`
  - Gauche (Left / Gauche) : `"L30"`, `"G30"`, `"-30"` $\rightarrow$ `-0.30`
  - Droite (Right / Droite) : `"R45"`, `"D45"`, `"+45"`, `"45"` $\rightarrow$ `+0.45`

### 2.3 `TrackColorGrip` & Réorganisation Drag-and-Drop (`ui/track_header.py`)
- La barre de couleur verticale à gauche de chaque [`TrackHeaderWidget`](../ui/track_header.py) est un [`TrackColorGrip`](../ui/track_header.py) affichant l'icône `⇅`.
- **Glisser-déposer vertical** : Génère un `QDrag` avec le MIME type `"application/x-novadaw-track-id"` et une miniature semi-transparente de l'en-tête de piste, permettant de réordonner les pistes à la volée dans la Timeline (et donc dans le Mixeur).

### 2.4 Redimensionnement Vertical Adaptatif des Pistes (`ui/track_header.py`)
- La bordure inférieure (`6 px`) de chaque [`TrackHeaderWidget`](../ui/track_header.py) permet d'ajuster la hauteur de la piste (`track.height`) entre **`MIN_TRACK_HEIGHT = 46 px`** et **`MAX_TRACK_HEIGHT = 320 px`** (défaut : **`88 px`**).
- **Modificateur global** : Maintenir **`Shift`** ou **`Alt`** pendant le glissement redimensionne **toutes les pistes du projet simultanément** (`track_resize_all_requested`).
- **Affichage adaptatif (Responsive Layout)** :
  - Si `height >= 72 px` : Affiche les 3 rangées (1: Nom + M/S/R, 2: Volume + Pan, 3: Instrument + FX).
  - Si `52 px <= height < 72 px` : Masque la rangée 3 (Instrument/FX).
  - Si `height < 52 px` : Mode compact (affiche uniquement la rangée 1 : Nom + M/S/R).

### 2.5 Effets de Lueur Néon 360° (`ui/glow_effects.py`)
La fonction [`set_button_glow(button, active, color_hex, blur_radius=18)`](../ui/glow_effects.py) attache un `QGraphicsDropShadowEffect` centré (`offset = (0, 0)`) sur les boutons d'état actifs :
- **Mute (`M`)** : Orange Ambre (`#f59e0b`)
- **Solo (`S`)** : Jaune Or (`#facc15`)
- **Record Arm (`R` / `●`)** : Rouge Néon (`#ff2244`, `blur_radius=20`)

---

## 3. Rack Master FX & Chaîne de Mastering (`ui/master_fx_rack.py`)

L'onglet **Master FX** ([`MasterFxWidget`](../ui/master_fx_rack.py), accessible via `F6`, `Ctrl+M` ou le bouton **FX MASTER** de la console) gère la chaîne de traitement du bus stéréo final (`project.master_track`) :

1. **Filtrage du Mixeur interne** :
   - La piste Master contient toujours `novadaw.mixer` en premier maillon interne, mais [`project.get_master_insert_plugins()`](../core/project.py) filtre `novadaw.mixer` pour n'afficher dans les cartes [`MasterFxSlotCard`](../ui/master_fx_rack.py) que les processeurs d'insertion de mastering (`NovaEQ`, `NovaCompressor`, VST3 externes).
2. **Réorganisation de l'ordre DSP (`▲` / `▼`)** :
   - [`move_plugin_up`](../ui/master_fx_rack.py) et [`move_plugin_down`](../ui/master_fx_rack.py) permutent l'ordre des effets à la fois dans `master_track.insert_plugins` et dans `master_track.plugins` (en préservant la position 0 de `novadaw.mixer`), car l'ordre de traitement modifie directement le rendu sonore (ex: `EQ -> Compresseur` vs `Compresseur -> EQ`).
3. **Bypass Global Mastering A/B (`toggle_global_bypass`)** :
   - Le bouton **⚡ Bypass Global Mastering (A/B)** sauvegarde l'état individuel `enabled` de chaque slot dans `_saved_bypass_states` avant de désactiver toute la chaîne d'inserts Master, et restaure fidèlement ces états individuels lorsqu'on le réactive.

---

## 4. Contrats de Synchronisation Bidirectionnelle Obligatoire

> [!IMPORTANT]
> Dans NovaDAW, une même donnée du modèle est souvent visible dans 3 ou 4 composants simultanément. Toute modification déclenchée depuis l'un de ces composants (ou via MCP) doit mettre à jour les autres **sans déclencher de boucle infinie de signaux Qt** (toujours utiliser `_updating_ui = True` ou `blockSignals(True)`).

| Donnée modifiée | Composants UI devant rester synchronisés |
| :--- | :--- |
| **Volume / Pan / Mute / Solo / Arm d'une piste** | [`TrackHeaderWidget`](../ui/track_header.py) $\leftrightarrow$ [`TrackInspector`](../ui/inspector.py) $\leftrightarrow$ [`ChannelStripWidget` (Mixer)](../plugins/mixer/widget.py) |
| **Volume / Mute du Bus Master** | [`TransportBar.vol_slider`](../ui/transport_bar.py) $\leftrightarrow$ [`MixerWidget` (Tranche Master)](../plugins/mixer/widget.py) $\leftrightarrow$ [`TrackInspector` (Mode Master)](../ui/inspector.py) $\leftrightarrow$ `AudioEngine.master_volume` |
| **Plugins du Rack Projet (`plugin_rack`)** | [`VstRackWidget` (Zone Inférieure)](../ui/vst_rack.py) $\leftrightarrow$ [`FloatingPluginRackDialog` (`F11`)](../ui/floating_plugin_rack.py) $\leftrightarrow$ Menus d'assignation de [`TrackInspector`](../ui/inspector.py) et [`TrackHeaderWidget`](../ui/track_header.py) |
| **Inserts du Bus Master** | [`MasterFxWidget`](../ui/master_fx_rack.py) $\leftrightarrow$ [`TrackInspector` (Mode Master)](../ui/inspector.py) $\leftrightarrow$ Indicateurs de la tranche Master dans [`MixerWidget`](../plugins/mixer/widget.py) |
| **Sélection de Clip (MIDI ou Audio)** | [`TimelineGrid`](../ui/timeline_view.py) $\leftrightarrow$ [`PianoRoll`](../ui/piano_roll.py) (si `MidiClip`) ou [`AudioEditor`](../ui/audio_editor.py) (si `AudioClip`) |
| **Hauteur / Ordre des Pistes** | Liste des [`TrackHeaderWidget`](../ui/track_header.py) $\leftrightarrow$ Rangées de [`TimelineGrid`](../ui/timeline_view.py) $\leftrightarrow$ Tranches de [`MixerWidget`](../plugins/mixer/widget.py) |
