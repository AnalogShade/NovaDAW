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
- **Serveur Officiel FastMCP** (`core/mcp/server.py`) : 31 outils d'automatisation audio exposés via le protocole standard Model Context Protocol.
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
  - **Piste par Défaut & Presets** : Piste batterie avec groove 4/4 intégrée par défaut dans les nouveaux projets, et presets d'usine (*Studio Acoustic*, *Punchy Rock*, *Trap / Modern*, *Big Hall Ambience*, *Tight & Dry*).
- **Piano Roll MIDI & Synthétiseur Intégré** :
  - Clavier interactif (C1 à B6) jouant les notes en direct au clic.
  - Grille magnétique (1/16, 1/8, 1/4, 1/2, 1 mesure).
  - Synthétiseur polyphonique riche à modélisation harmonique et enveloppe ADSR.
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

## 🎹 Architecture du Codebase

```
music software/
├── main.py                         # Point d'entrée de NovaDAW avec ID d'application Windows
├── README.md                       # Documentation complète
├── assets/
│   ├── nova_icon.ico               # Icône officielle multi-résolution Windows (16 à 256px)
│   ├── nova_icon.png               # Icône Supernova haute résolution (512x512)
│   ├── nova_logo_full.png          # Logo complet NovaDAW
│   └── style.qss                   # Thème sombre professionnel inspiré de Cubase
├── core/
│   ├── audio_engine.py             # Moteur audio sounddevice, routage, test tone, latence
│   ├── audio_importer.py           # Importateur universel (WAV, MP3, FLAC, OGG, AIFF, M4A...)
│   ├── hardware_manager.py         # Détection GPU, VRAM, pilotes audio hôtes (WASAPI, ASIO...)
│   ├── project.py                  # Modèle de données (Pistes, Master, Clips, Plugins)
│   ├── serializer.py               # Persistance de projet au format .ndaw
│   ├── actions/                    # Handlers d'actions (pistes, transport, plugins, hardware)
│   └── mcp/
│       ├── server.py               # Serveur FastMCP (31 outils d'automatisation IA)
│       └── tcp_ipc.py              # Pont client/serveur TCP IPC temps réel
├── plugins/
│   ├── base.py                     # Classe de base BasePlugin
│   ├── registry.py                 # Registre dynamique des plugins
│   ├── equalizer/                  # Égaliseur paramétrique modulaire 3/10/12/24 bandes
│   ├── compressor/                 # Compresseur dynamique de studio avec VU-mètre GR
│   └── mixer/                      # Table de mixage console avec VU-mètres crête L/R
├── scripts/
│   └── update_desktop_shortcut.py  # Script de mise à jour du raccourci Bureau Windows
├── tests/
│   ├── test_plugins.py             # Tests unitaires des plugins DSP
│   ├── test_project.py             # Tests du modèle de données et sérialisation
│   ├── test_tcp_ipc.py             # Tests du protocole IPC temps réel
│   └── test_v1_1_features.py       # Tests de l'importateur, du hardware manager et des actions MCP
└── ui/
    ├── main_window.py              # Fenêtre principale avec logo Supernova et menus
    ├── device_settings_dialog.py   # Boîte de dialogue Configuration Matériel, Audio & IA
    ├── transport_bar.py            # Barre de transport (Play, Stop, BPM, Loop)
    ├── track_header.py             # En-têtes de pistes (Volume, Pan, Mute, Solo, Rec)
    ├── timeline_view.py            # Grille d'arrangement et clips audio/MIDI
    ├── piano_roll.py               # Séquenceur de notes avec clavier interactif
    └── audio_editor.py             # Visualiseur de waveform et import audio
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
