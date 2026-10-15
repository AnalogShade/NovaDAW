# 🏛️ Architecture Globale du Système NovaDAW

Ce document décrit en détail l'architecture logicielle, les flux de données et l'organisation modulaire de **NovaDAW**.

---

## 1. Vue d'Ensemble des Couches Logicielles

NovaDAW repose sur une architecture en couches découplées pour garantir une fluidité totale de l'interface utilisateur, une latence audio minimale et une sécurité absolue face aux extensions externes.

```mermaid
flowchart TD
    subgraph UI_Layer["Couche Présentation (PySide6 / Qt 6)"]
        MW["MainWindow (Fenêtre Principale)"]
        INSP["TrackInspector (Inspecteur F2)"]
        TL["TimelineView & Ruler (Grille & Règle)"]
        TB["TransportBar (Barre de Transport)"]
        PR["PianoRoll (Séquenceur MIDI)"]
        AE["AudioEditor (Forme d'onde)"]
        MX["Mixer Console (Table F5)"]
        FX["MasterFxRack (Effets F6)"]
        RK["VstRack & FloatingPluginRack (F11)"]
    end

    subgraph Command_Layer["Couche Actions & Orchestration"]
        AR["ActionRegistry (core/action_registry.py)"]
        ACT["Handlers d'actions (core/actions/*)"]
        IPC["NovaIpcServer / NovaIpcClient (core/ipc/*)"]
        MCP["FastMCP Server (core/mcp/server.py)"]
    end

    subgraph Core_Model["Couche Métier & Modèle de Données"]
        PROJ["Project (core/project.py)"]
        TRK["Track (Audio / MIDI / Master)"]
        CLP["MidiClip & AudioClip"]
        SER["Serializer (.ndaw JSON)"]
    end

    subgraph DSP_Layer["Couche Plugins & DSP (plugins/*)"]
        REG["PluginRegistry & BasePlugin"]
        EQ["Parametric Equalizer (RBJ IIR Biquad)"]
        CMP["Dynamic Compressor (Giannoulis Peak/RMS)"]
        SYN["NovaSynth (PolyBLEP, SVF, Stacking, 10 Bus)"]
        DRM["Nova Drums VSTi (Samples FP32, Freeverb)"]
        VST["IsolatedPlugin (VST3 Multiprocessing Spawn)"]
    end

    subgraph Audio_Hardware["Couche Audio & Matériel (core/*)"]
        ENG["AudioEngine (sounddevice, ASIO-Guard RAM Cache)"]
        HW["HardwareManager (GPU, VRAM, ASIO, WASAPI)"]
        IMP["AudioImporter (WAV, MP3, FLAC, Resampling)"]
    end

    MW --> UI_Layer
    MW --> AR
    MCP --> IPC --> AR
    AR --> ACT --> PROJ
    AR --> ACT --> ENG
    ENG --> PROJ
    ENG --> DSP_Layer
    ENG --> Audio_Hardware
    PROJ --> TRK --> CLP
    PROJ --> SER
    REG --> EQ & CMP & SYN & DRM
```

---

## 2. Le Pipeline Audio Temps Réel (Signal Flow)

Le traitement audio temps réel s'exécute dans un thread de haute priorité géré par la bibliothèque `sounddevice` (PortAudio).

### Diagramme de Flux Audio (De la Timeline aux Haut-Parleurs) :

```mermaid
flowchart LR
    subgraph Sources["Sources Sonores"]
        MC["Clips MIDI"] --> INST["Instrument (NovaSynth / Nova Drums / VST3)"]
        AC["Clips Audio"] --> BUF["Buffer Audio RAM"]
        PV["Prévisualisation (Piano Roll / Clavier)"] --> PVB["_preview_buffers"]
    end

    subgraph Track_Processing["Traitement par Piste (Track)"]
        INST --> TRK_FX["Inserts FX Piste (EQ, Comp, VST3)"]
        BUF --> TRK_FX
        TRK_FX --> VOL_PAN["Fader Volume & Panoramique"]
    end

    subgraph Master_Bus["Bus Master"]
        VOL_PAN --> SUM["Sommation Stéréo (Accumulateur FP32)"]
        PVB --> SUM
        SUM --> MST_MIX["novadaw.mixer (Master Pan, Mono, Dim -12dB)"]
        MST_MIX --> MST_FX["Pile d'Effets Master (EQ, Compresseur, VST3)"]
        MST_FX --> MST_VOL["Fader Master"]
        MST_VOL --> TANH["Soft Clipper Analogique np.tanh()"]
    end

    subgraph Output["Sortie Physique"]
        TANH --> SD["sounddevice OutputStream (WASAPI / ASIO)"]
    end
```

### Détails Techniques du Pipeline :
1. **Échantillonnage et Grille Temporelle** :
   À chaque appel de `_audio_callback(outdata, frames, time_info, status)` :
   - NovaDAW calcule le nombre de temps musicaux correspondant aux `frames` reçus :
     $$\Delta\text{beats} = \frac{\text{frames}}{\text{sample\_rate}} \times \frac{\text{BPM}}{60}$$
   - Si la lecture est en cours, `current_beat` avance de $\Delta\text{beats}$.
   - Si la boucle est active et dépasse `loop_end_beat`, le curseur se repositionne de façon parfaitement étanche sur `loop_start_beat`.

2. **ASIO-Guard et Cache Hybride** :
   Pour éviter tout glitch ou décrochage audio (buffer underrun) lors de projets chargés :
   - NovaDAW intègre un pré-calcul et un système de cache en mémoire vive (`_track_audio_caches`).
   - Les pistes MIDI ou audio stables sont rendues en tâche de fond dans la mémoire vive, réduisant la charge CPU instantanée pendant la lecture continue.

3. **Prévisualisation à Latence Zéro** :
   - Lorsque l'utilisateur clique sur le clavier du Piano Roll ou sur un pad de batterie, un bloc audio est immédiatement injecté dans la file protégée `_preview_buffers`.
   - Ce signal traverse la chaîne d'effets de la piste sélectionnée (`_process_track_effects_isolated`) et s'additionne au mixage en cours avec un délai inférieur à 8 ms.

---

## 3. Le Pipeline d'Enregistrement Audio et MIDI

NovaDAW permet l'enregistrement direct de micros/lignes ainsi que l'enregistrement de patterns MIDI en temps réel.

```mermaid
sequenceDiagram
    participant User as Utilisateur / Micro
    participant Engine as AudioEngine (_record_callback)
    participant Lock as _record_lock
    participant Buffer as _recorded_audio_blocks
    participant Import as AudioImporter (Resampler)
    participant Track as AudioTrack (AudioClip)

    User->>Engine: Capture signal physique (Microphone)
    Engine->>Lock: Acquérir verrou
    Engine->>Buffer: Stocker bloc indata.copy() (FP32)
    Engine->>Lock: Libérer verrou
    Note over User,Engine: Arrêt de l'enregistrement (Touche R ou Stop)
    Engine->>Buffer: Concaténation des blocs
    Engine->>Import: Rééchantillonnage si sr_capture != sr_projet
    Engine->>Engine: Compensation latence matérielle (Record Offset)
    Engine->>Track: Création et injection du nouvel AudioClip
```

---

## 4. Architecture de Commande Découplée : ActionRegistry, IPC et FastMCP

NovaDAW adopte un motif architectural de type **Command Pattern centralisé** :

1. **`core/action_registry.py`** :
   - Registre central de toutes les capacités exécutables du logiciel.
   - Toute fonction métier décorée avec `@action_registry.register` devient instantanément invocable par l'interface PySide6, par des scripts ou par un agent d'IA.
   - Les métadonnées (types des paramètres, docstrings, valeurs par défaut) sont inspectées dynamiquement via le module `inspect`.

2. **`core/ipc/` (Pont TCP IPC Local)** :
   - `NovaIpcServer` s'exécute sur le port local (par défaut 8765, ou port incrémenté si occupé).
   - Les commandes transitent au format JSON : `{"action": "nom_action", "params": {...}}`.
   - Les commandes reçues du réseau sont dispatchées en toute sécurité sur le **Thread Principal Qt** via les mécanismes de signaux/slots pour éviter tout crash de mémoire partagée.

3. **`core/mcp/server.py` (Serveur FastMCP)** :
   - Expose automatiquement les 31+ actions sous forme d'outils Model Context Protocol aux agents intelligents (Antigravity, Claude Desktop, Cursor).
   - **Découplage absolu** : Le serveur FastMCP n'a pas besoin d'être édité manuellement quand une nouvelle action est créée ; il lit dynamiquement le registre.

---

## 5. Hébergement Crash-Isolé des Plugins VST3 (`core/vst_host.py`)

Les plugins VST3 natifs externes (Pedalboard / JUCE) sont programmés en C++ natif et peuvent contenir des erreurs de mémoire fatales pour un processus Python. NovaDAW résout ce problème par une isolation de processus complète :

```mermaid
flowchart TD
    subgraph Parent_Process["Processus Principal NovaDAW"]
        APP["PySide6 Application & AudioEngine"]
        ISO["IsolatedPlugin Wrapper"]
        PIPE_P["multiprocessing.Pipe (Parent End)"]
    end

    subgraph Child_Process["Sous-Processus Isolateur VST (Spawn)"]
        PIPE_C["multiprocessing.Pipe (Child End)"]
        WORKER["_host Worker Function"]
        PEDAL["Pedalboard VST3 Plugin Instance"]
        HOOK["vtable multibus shadow hook (_apply_multibus_vst_fix)"]
        GUI["Native VST Editor Window (Main Thread du fils)"]
    end

    APP <--> ISO <--> PIPE_P
    PIPE_P <-->|"Commandes: render, state, restore, editor"| PIPE_C
    PIPE_C <--> WORKER
    WORKER <--> HOOK <--> PEDAL
    WORKER <--> GUI
```

### Avantages Majeurs de cette Architecture :
- **Immunité aux Segfaults** : Si un plugin VST3 subit une violation d'accès mémoire (`0xC0000005`), le sous-processus s'arrête brutalement, mais le processus principal de NovaDAW reste intact et continue de jouer le reste du projet.
- **Support des fenêtres graphiques natives** : L'éditeur natif du plugin VST3 s'exécute sur le thread principal du processus fils, évitant tout blocage de la boucle d'événements Qt de NovaDAW.
