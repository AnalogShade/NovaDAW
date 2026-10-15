# NovaDAW 🌌 - Digital Audio Workstation Open-Source & Pilotée par IA

**NovaDAW** est un logiciel de production et de création musicale (DAW) libre, modulaire et open-source conçu en Python et PySide6 (Qt 6), inspiré de l'ergonomie et du design professionnel de Cubase Pro.

Il intègre une architecture avancée de plugins DSP, un importateur audio universel, un gestionnaire complet des cartes graphiques et des pilotes audio (ASIO/WASAPI), ainsi qu'une **intégration complète avec le Model Context Protocol (MCP)** permettant à un agent IA (Google Antigravity, Claude, Codex) de composer, mixer et sculpter le son en direct avec vous.

---

## 🌟 Nouvelles Fonctionnalités Majeures (v1.1)

### 1. 🖥️ Section Périphériques Audio & Graphiques (Hardware Manager)
- **Pilotes Audio Professionnels** : Sélection en direct des systèmes hôtes **Windows WASAPI**, **ASIO**, **DirectSound**, **MME** et **WDM-KS**.
- **Routage et DSP** :
  - Choix précis des périphériques d'entrée (microphones, lignes) et de sortie (enceintes, casques, interfaces audio externes comme Eleven Rack, Focusrite, etc.).
  - Réglage de la fréquence d'échantillonnage (44.1 kHz à 192 kHz) et de la taille du tampon buffer (32 à 4096 échantillons).
  - Calcul et affichage en temps réel de la **latence théorique** en millisecondes.
  - Bouton **Tester la Sortie Audio** jouant un son sinusoïdal pur à 440 Hz pour valider immédiatement le routage.
- **Cartes Graphiques (GPU) & Affichage** :
  - Détection automatique et détaillée de toutes les cartes graphiques installées (GPU principal et secondaire, p. ex. NVIDIA GeForce RTX 2060, AMD Radeon, Intel Arc).
  - Affichage de la mémoire **VRAM dédiée (Mo/Go GDDR)**, de la version exacte du pilote d'affichage et du statut matériel.
  - Choix du moteur de rendu UI (**Direct3D 11 / OpenGL / Vulkan**) et limiteur de fréquence d'images (**60 FPS, 120 FPS, 144 FPS ou VSync**).
  - Accélération matérielle GPU activable pour les visualisations audio (courbes EQ, FFT, VU-mètres).
- **Accès Rapide** : Menu *Périphériques > Configuration Matériel & Audio (Pilotes, GPU)* ou clic direct sur le libellé audio dans la barre de statut.

### 2. 📥 Importateur Audio Universel & Explorateur Windows
- **Explorateur Windows Natif** : Boîte de dialogue native Windows pour parcourir vos dossiers et disques.
- **Support de TOUS les Formats Audio** : Importation transparente de fichiers **WAV, MP3, FLAC, OGG, AIFF, M4A, AAC, CAF, WMA**.
- **Traitement Automatique** :
  - Détection du nombre de canaux et conversion intelligente mono vers stéréo.
  - Rééchantillonnage automatique à la fréquence d'échantillonnage active du projet.
  - Placement automatique sur la piste audio sélectionnée ou création immédiate d'une nouvelle piste.
- **Raccourcis & Accès Directs** :
  - Menu principal centralisé : *Fichier > 📥 Importer > 🎵 Fichier Audio...* (`Ctrl + I`).
  - Éditeur audio de la zone inférieure ("Lower Zone").
  - Action MCP automatisée pour les agents IA (`novadaw_import_audio_file`).

### 3. 🤖 Orchestration Complète par Agent IA (Protocole MCP & Pont TCP IPC)
NovaDAW transforme la station de travail audio numérique en un environnement collaboratif où l'utilisateur dialogue avec un agent de codage / composition IA :
- **Serveur Officiel FastMCP** (`core/mcp/server.py`) : 44 outils d'automatisation audio exposés via le protocole standard Model Context Protocol.
- **Pont TCP IPC non-bloquant** : L'interface utilisateur graphique PySide6 reste ultra-fluide pendant que l'agent manipule les pistes et les paramètres sonores en arrière-plan.
- **Outils MCP Intégrés** :
  - **Plugins & Mixage** :
    - `novadaw_configure_equalizer` : Application de presets (`bass_boost`, `vocal_clarity`, `warm_master`, `bright_air`, `flat`) ou réglage précis bande par bande (fréquence, gain, Q, type de filtre).
    - `novadaw_configure_compressor` : Application de presets (`punchy_drums`, `vocal_leveler`, `master_glue`, `parallel_crush`) ou réglage des paramètres (seuil, ratio, attaque, relâchement, makeup gain, mix dry/wet).
    - `novadaw_configure_mixer` : Contrôle du volume master, panoramique, activation du mode mono et de l'atténuateur Dim (-12 dB).
    - `novadaw_get_mixer_levels` : Télémétrie en temps réel des niveaux crête L/R et RMS.
    - `novadaw_get_track_plugins`, `novadaw_add_plugin`, `novadaw_remove_plugin`, `novadaw_set_plugin_bypass`.
  - **Matériel & Pilotes** :
    - `novadaw_get_hardware_devices` : Inspection des GPU, pilotes audio, fréquences et buffers.
    - `novadaw_set_audio_device` : Reconfiguration à chaud du périphérique de sortie, du sample rate et du buffer size.
  - **Projet & Timeline** :
    - `novadaw_import_audio_file` : Importation directe de tout fichier audio depuis le disque.
    - `novadaw_create_track`, `novadaw_delete_track`, `novadaw_set_track_volume`, `novadaw_set_track_mute`, `novadaw_set_track_solo`.
    - `novadaw_transport_play`, `novadaw_transport_stop`, `novadaw_transport_pause`, `novadaw_transport_loop`.

### 4. 🌌 Identité Visuelle & Logo Supernova
- Nouveau logo et emblème cosmique **Supernova** haute résolution.
- Icône Windows multi-résolutions (`.ico` de 16x16 à 256x256) intégrée aux fenêtres et au raccourci du Bureau Windows (`NovaDAW.lnk`).
- Script de régénération automatique du raccourci (`scripts/update_desktop_shortcut.py`).

---

## 🎛️ Fonctionnalités Audio & Studio

- **Console de Mixage Pro (`novadaw.mixer`)** :
  - Tranches multipistes avec faders dB, panoramique, Solo/Mute/Rec, et **VU-mètres dynamiques L/R**.
  - Tranche Master avec commutateur Mono et Dim (-12 dB).
- **Égaliseur Paramétrique Haute Précision (`novadaw.equalizer`)** :
  - Mode **3, 10, 12 ou 24 bandes**.
  - Filtres IIR biquad (Peaking, Low/High Shelf, Low/High Pass) sans distorsion ni clics.
  - Interface interactive : courbe logarithmique 20 Hz - 20 kHz avec nœuds déplaçables à la souris.
- **Compresseur Dynamique de Studio (`novadaw.compressor`)** :
  - Détection RMS/Peak, Soft-Knee, enveloppe d'attaque et de relâchement balistique.
  - Affichage de la courbe dynamique et **VU-mètre de Réduction de Gain (GR) en direct**.
- **Instrument Virtuel de Batterie Nova Drums VSTi (`novadaw.drum_machine`)** :
  - **Échantillonnage Haute Précision FP32 (32-bit Float)** : 9 éléments acoustiques de studio générés localement par IA via le modèle **Stable Audio 3 Medium (1.4B) + SAME-Large sur GPU** (Grosse caisse, Caisse claire, Charleston fermé & ouvert, Toms aigu/médium/grave, Cymbale Crash, Cymbale Ride).
  - **Processeur de Réverbération Stéréo Intégré** : Réverbération algorithmique de studio (Schroeder / Freeverb) avec contrôles de taille de pièce (*Room Size*), amortissement des hautes fréquences (*Damping*), largeur stéréo (*Width*) et mélange *Wet/Dry*.
  - **Gestion Acoustique par Pad** : Contrôle indépendant du volume, panoramique, pitch / accordage (±12 demi-tons), decay d'enveloppe et niveau d'envoi réverb (*Reverb Send*).
  - **Choke Group Intelligent** : Étouffement naturel du charleston ouvert lors de la frappe du charleston fermé.
- **Synthétiseur Polyphonique Modulaire NovaSynth (`novadaw.synth`)** :
  - **Synthèse DSP 100% Électronique Pure (FP32)** : Aucun sample nécessaire. 7 formes d'ondes anti-aliasées (PolyBLEP Saw, Square/Pulse avec PWM, Sine, Triangle, Bruit rose/blanc, FM 2-opérateurs, et SuperSaw 7-voix stéréo style Roland JP-8000).
  - **Empilement de Couches Sonores (Sound Stacking)** : Possibilité de créer, dupliquer et superposer un nombre illimité de couches sonores indépendantes par note avec transposition d'octaves, demi-tons et micro-désaccordage (*Fine Tune*).
  - **Matrice Multi-Sorties 10 Bus Stéréo** : Routage assignable de chaque couche sonore vers 10 sorties stéréo indépendantes (Bus 0 à Bus 9 / Out 1 à Out 10) permettant à plusieurs pistes de piloter le même synthétiseur sur des canaux audio séparés.
  - **Double Enveloppe ADSR & Filtre State-Variable Analogique (SVF)** : Filtre multimode sans instabilité numérique (Passe-Bas, Passe-Haut, Passe-Bande, Rejet/Notch) avec résonance Q jusqu'à 10.0, saturation de distorsion intégrée et enveloppe de filtre dédiée.
  - **LFO Multi-Destinations & Effets d'Espace** : LFO routable vers le pitch, le cutoff, le panoramique ou le volume, complété par un Délai Ping-Pong stéréo et une Réverbération de studio.
  - **Design Cyberpunk & Interface Séduisante** : Arrière-plan orné d'une grille hexagonale et circuits néon, oscilloscope temps réel réactif, visualisateurs interactifs de courbe ADSR et de réponse en fréquence de filtre (Bode plot), et clavier d'écoute à latence nulle (<8ms).
  - **Banque de Presets d'Usine** : *Cyberpunk Acid Lead*, *Neon Horizon SuperSaw*, *Deep Sub & Punch Bass*, *Ethereal Dream Pad*, *80s Synthwave Pluck*, *Sci-Fi FM Resonator*.
  - **Contrôle Total par Protocole MCP** : Entièrement pilotable via `novadaw_configure_synth`, `novadaw_add_synth_layer`, `novadaw_remove_synth_layer`, `novadaw_set_synth_preset` et `novadaw_get_synth_state`.
- **Export Mixdown WAV** : Rendu direct avec application des chaînes d'effets et du mastering.

---

## 🚀 Démarrage Rapide

Pour lancer le logiciel, assurez-vous d'être dans le dossier du projet et exécutez :

```powershell
python main.py
```

### Ajouter un plugin au projet et l’utiliser sur une piste

1. Ouvrir **Plugins > Ajouter un plugin au projet…**. Rechercher un nom, sélectionner un instrument ou un effet, puis cliquer **Ajouter au projet**. Le rack reste accessible avec **F11**.
2. Créer une piste MIDI avec **Ctrl+T** et choisir son **Instrument de sortie**, ou sélectionner une piste existante et changer **Sortie MIDI — Instrument** dans l’inspecteur.
3. Ouvrir l’interface depuis l’inspecteur pour régler cette piste. Le rack fournit les réglages de départ des nouvelles instances ; chaque piste conserve ensuite ses propres réglages.
4. Pour un effet, utiliser **Pile de plugins & effets > Ajouter un Plugin** sur la piste ou le Master.
5. Enregistrer le projet : le rack, le routage et les états VST sont conservés. Les plugins et banques de sons doivent rester installés sur la machine.

Les VST3 sont hébergés dans des processus séparés : une erreur native du plugin ne ferme plus NovaDAW. Le scan valide le chargement, pas toutes les fonctions du plugin. Un instrument qui ne fonctionne pas reste silencieux et signale une erreur, sans remplacement automatique par le synthétiseur interne.

Voir [le compte rendu des tests du parcours plugins](tests/PLUGIN_WORKFLOW_QA.md) pour les vérifications et limites de compatibilité observées.

### Raccourcis Clavier :
- **Espace** : Lecture / Pause
- **0 (Pavé numérique)** : Stop (retour au début ou au repère de boucle)
- **L** : Activer / Désactiver la boucle
- **F5** : Afficher / Masquer la Console de Mixage (Mixer)
- **Ctrl + I** : Importer un fichier audio (tout format)
- **Ctrl + P** : Ouvrir la configuration des Périphériques (Audio, GPU, MCP)
- **Ctrl + M** : Inspecter la Piste Master et sa pile d'effets
- **Ctrl + T** : Ajouter une nouvelle piste
- **Ctrl + S** : Enregistrer le projet (`.ndaw`)
- **Ctrl + O** : Ouvrir un projet existant
- **Ctrl + E** : Exporter le mixage en WAV

---

## 🤖 Connexion avec Antigravity / Claude Desktop (MCP)

NovaDAW est déjà configuré comme serveur MCP. Pour l'ajouter manuellement dans un autre environnement compatible :

```json
{
  "mcpServers": {
    "novadaw": {
      "command": "python",
      "args": ["c:/Users/Utilisateur/Documents/Dev/music software/core/mcp/server.py"]
    }
  }
}
```

---

## 📚 Documentation & Spécifications Techniques (`doc/`)

L'architecture complète du logiciel et les contrats d'ingénierie destinés aux développeurs et aux agents IA sont documentés dans [**`AGENT.md`**](AGENT.md) et dans le dossier [**`doc/`**](doc/README.md) :
- [**Guide Opérationnel des Agents IA (`AGENT.md`)**](AGENT.md)
- [**Index Complet des Spécifications (`doc/README.md`)**](doc/README.md)

---

## 🎹 Architecture du Codebase

```text
music software/
├── main.py                         # Point d'entrée de NovaDAW avec ID d'application Windows
├── AGENT.md                        # Guide maître d'architecture et de comportement pour Agents IA
├── GEMINI.md                       # Directives d'autonomie et pilotage MCP NovaDAW
├── README.md                       # Présentation générale et guide de démarrage
├── assets/
│   ├── nova_icon.ico               # Icône officielle multi-résolution Windows (16 à 256px)
│   ├── nova_icon.png               # Icône Supernova haute résolution (512x512)
│   ├── nova_logo_full.png          # Logo complet NovaDAW
│   └── style.qss                   # Thème sombre professionnel inspiré de Cubase Pro
├── core/
│   ├── action_registry.py          # Registre déclaratif central des actions (@action_registry)
│   ├── audio_engine.py             # Moteur audio FP32 temps réel, cache ASIO-Guard, enregistrement
│   ├── audio_importer.py           # Importateur universel (WAV, MP3, FLAC, OGG, AIFF, M4A) & crêtes 1:1
│   ├── hardware_manager.py         # Auto-config ASIO/WASAPI, latence RTL, panneaux pilotes, GPU
│   ├── plugin_manager.py           # Scanner VST3/VST2 incrémental, watcher temps réel, instances
│   ├── preset_manager.py           # Gestionnaire universel de presets & protection Factory Presets
│   ├── project.py                  # Modèle de données (Project, Track, Master, MidiClip, AudioClip)
│   ├── recent_projects.py          # Historique MRU des projets récents
│   ├── serializer.py               # Sérialisation & rétrocompatibilité au format .ndaw (v1.1)
│   ├── vst_host.py                 # Wrapper d'hébergement sécurisé des plugins VST3 (IsolatedPlugin)
│   ├── actions/                    # 44 handlers d'actions (transport, midi, project, plugins, synth)
│   ├── ipc/                        # Serveur et client TCP IPC JSON non-bloquant (port 8765)
│   └── mcp/
│       └── server.py               # Serveur officiel FastMCP (auto-exposition des 44 outils IA)
├── doc/                            # Spécifications techniques complètes (14 fichiers thématiques)
├── plugins/
│   ├── base.py                     # Contrat abstrait BasePlugin
│   ├── registry.py                 # Registre dynamique des plugins internes (PluginRegistry)
│   ├── synth/                      # NovaSynth : Synthétiseur polyphonique multi-couches & multi-sorties
│   ├── drum_machine/               # Nova Drums VSTi : Boîte à rythmes 16 pads, synthèse & samples
│   ├── equalizer/                  # NovaEQ : Égaliseur paramétrique biquad IIR 3/10/12/24 bandes
│   ├── compressor/                 # NovaCompressor : Compresseur dynamique de studio avec GR meter
│   └── mixer/                      # NovaMixer : Console de mixage multi-pistes & bus Master
├── presets/                        # Bibliothèque de presets JSON par type de plugin
├── tests/                          # Suite complète de tests unitaires et de stress (pytest)
└── ui/
    ├── main_window.py              # Fenêtre principale Cubase Pro, gestion Dirty State & menus
    ├── transport_bar.py            # Barre de transport (Play, Stop, Rec, BPM, Loop, Master VU)
    ├── editing_toolbar.py          # Barre d'outils (Select, Split, Erase, Snap, Grille, Zoom)
    ├── track_header.py             # En-têtes de pistes (ResetableSlider, CompactNumEdit, Drag & Resize)
    ├── inspector.py                # Inspecteur latéral de piste et routage multi-sorties
    ├── timeline_view.py            # Règle temporelle, grille d'arrangement et clips
    ├── piano_roll.py               # Séquenceur MIDI interactif avec clavier vertical
    ├── audio_editor.py             # Éditeur de forme d'onde audio et trimming non-destructif
    ├── vst_rack.py                 # Onglet Rack de plugins du projet
    ├── floating_plugin_rack.py     # Fenêtre flottante du Rack VST (F11)
    ├── master_fx_rack.py           # Onglet Master FX avec réorganisation DSP et Bypass Global A/B
    ├── plugin_dialogs.py           # NativePluginDialog (barre universelle de presets) & gestionnaire VST
    ├── project_plugin_dialog.py    # Sélecteur de plugins avec diagnostic VST2 Legacy
    ├── device_settings_dialog.py   # Configuration Audio ASIO/WASAPI, Latence RTL, GPU & MCP
    ├── glow_effects.py             # Effets de lueur néon 360° (Mute, Solo, Record Arm)
    ├── dialogs.py                  # Boîtes de dialogue de création de piste
    └── help_dialog.py              # Guide interactif et aide intégrée
```

---

## 📜 Licences et Conformité Commerciale

L'ensemble des dépendances et du code source respecte scrupuleusement les licences permissives open source :
- **NumPy** : BSD-3-Clause
- **SciPy** : BSD-3-Clause
- **sounddevice** : MIT
- **soundfile** : BSD-3-Clause
- **pedalboard** : Apache 2.0
- **PySide6** : LGPLv3
- **Zéro Licence Contaminante (Pas de GPL)** : Tous les algorithmes de filtrage biquad, de compression dynamique, de mixage et de pont IPC sont entièrement libres d'utilisation commerciale.
