# NovaDAW 🎵 - Digital Audio Workstation Open-Source

**NovaDAW** est un logiciel de production et de création musicale (DAW) libre et open-source conçu en Python et PySide6 (Qt 6), inspiré de l'ergonomie et du design de Cubase Pro.

---

## 🌟 Fonctionnalités Incluses (MVP)

- **Interface Moderne Sombre** : Thème graphite inspiré de Cubase 13 avec accents de couleurs, faders fluides et afficheurs digitaux.
- **Nouveau Projet Automatique** : Démarrage direct avec tempo à 120 BPM, signature 4/4 et pistes de démonstration prêtes à l'emploi.
- **Gestion de Pistes Flexible** :
  - Création de pistes **MIDI** (instruments virtuels) ou **Audio** (échantillons, enregistrements).
  - Contrôles de piste individuels : **Mute** (M), **Solo** (S), **Armement Enregistrement** (R), potentiomètre de **Volume** et curseur de **Panoramique**.
- **Timeline & Grille d'Arrangement** :
  - Règle temporelle avec mesures/temps (Bars & Beats) et affichage en secondes.
  - **Gestion de boucle (Loop Region)** : Déplacement de l'intervalle de boucle sur la règle avec drapeaux [L] et [R].
  - **Création de blocs** : Double-cliquez sur une zone vide de piste pour générer automatiquement un bloc calé sur la grille ou sur l'intervalle de boucle.
  - Déplacement et redimensionnement des blocs à la souris.
- **Zone Inférieure Intégrée ("Lower Zone" style Cubase)** :
  - **Piano Roll MIDI interactif** :
    - Clavier de piano à gauche (C1 à B6) qui **joue le son de la note quand on clique dessus** !
    - Grille matricielle de composition de notes avec grille magnétique (1/16, 1/8, 1/4, 1/2, 1 mesure).
    - Clic gauche pour ajouter / déplacer une note, étirement sur le bord pour régler la durée, clic droit pour supprimer.
  - **Éditeur Audio** :
    - Visualiseur de forme d'onde haute résolution.
    - Bouton d'importation de fichiers WAV/Audio avec réglage de gain et calcul automatique du calage temporel.
- **Moteur Audio Temps Réel & Synthétiseur Intégré** :
  - Moteur audio temps réel multithreadé à faible latence propulsé par `sounddevice` et `numpy`.
  - Synthétiseur polyphonique riche intégré (mélange harmonique et enveloppe ADSR sans clic) pour entendre instantanément vos mélodies MIDI.
  - Barre de transport complète : Play, Stop, Record, Loop, BPM ajustable et Master Volume.
  - Tête de lecture animée en temps réel (60 FPS).
- **Enregistrement & Export** :
  - Format de projet propriétaire structuré `.ndaw` (Enregistrer / Ouvrir).
  - **Exportation Mixdown Audio (WAV)** : Rendu direct du projet complet vers un fichier WAV stéréo haute qualité.

---

## 🚀 Démarrage Rapide

Pour lancer le logiciel, assurez-vous d'être dans le dossier du projet et exécutez :

```powershell
python main.py
```

### Raccourcis Clavier :
- **Espace** : Lecture / Pause
- **0 (Pavé numérique)** : Stop (retour au début ou au début de la boucle)
- **L** : Activer / Désactiver la boucle
- **Ctrl + T** : Ajouter une nouvelle piste
- **Ctrl + S** : Enregistrer le projet
- **Ctrl + O** : Ouvrir un projet existant
- **Ctrl + E** : Exporter le mixage en WAV

---

## 🎹 Architecture du Projet

```
music software/
├── main.py                  # Point d'entrée de l'application
├── README.md                # Documentation
├── assets/
│   └── style.qss            # Thème sombre QSS professionnel (Cubase Dark)
├── core/
│   ├── project.py           # Modèle de données (Project, Track, MidiClip, AudioClip, MidiNote)
│   ├── audio_engine.py      # Moteur audio sounddevice, synthétiseur et mixeur
│   └── serializer.py        # Sauvegarde et chargement de projet .ndaw
└── ui/
    ├── main_window.py       # Fenêtre principale avec docking et synchronisation
    ├── transport_bar.py     # Barre de transport (Play, Stop, BPM, Loop, Compteurs)
    ├── track_header.py      # En-têtes de pistes (Volume, Pan, M, S, R)
    ├── timeline_view.py     # Grille d'arrangement, règle temporelle et clips
    ├── piano_roll.py        # Séquenceur MIDI avec clavier virtuel interactif
    ├── audio_editor.py      # Inspecteur de clip audio et affichage waveform
    └── dialogs.py           # Boîtes de dialogue (Ajout de piste, etc.)
```

---

## 🔮 Évolutions Futures & Support VST3

Le logiciel a été spécialement architecturé pour accueillir :
1. **Hébergement de VST3** : Intégration directe via la bibliothèque [`pedalboard`](https://github.com/spotify/pedalboard) de Spotify, permettant de charger des instruments VST3 et des effets de guitare/ampli (comme Helix Native, Amplitube, Serum, etc.) sans dépendance C++ complexe.
2. **Enregistrement Audio en direct** : Branchement de l'entrée microphone/carte son (comme votre Avid Eleven Rack) directement dans une piste armée en rouge.
