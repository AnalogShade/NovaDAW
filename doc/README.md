# 📚 Spécifications et Documentation Technique NovaDAW

Bienvenue dans le centre de spécifications techniques et d'architecture de **NovaDAW**.

Ce dossier rassemble l'ensemble des spécifications fonctionnelles, architecturales et techniques du projet. Il sert de **source de vérité unique et de guide d'ingénierie** pour les développeurs et les agents d'intelligence artificielle (Claude, Codex, Gemini, Antigravity, Cursor) afin de concilier un développement à haute vélocité (*Vibe Coding*) avec une **cohérence modulaire exemplaire**, une **intentionnalité claire** de chaque changement et la **non-régression** des fonctionnalités.

---

## 🧭 Plan Complet de la Documentation (15 Fichiers)

| N° | Document | Objet & Périmètre |
| :--- | :--- | :--- |
| **00** | [**`AGENT.md`**](../AGENT.md) | **Guide maître d'intervention pour tout Agent IA** (Principes cardinaux, Matrice de Routage Contextuel, Chaîne de Synchronisation en 6 points, Pièges connus). |
| **01** | [**`invariants.md`**](invariants.md) | **Invariants d'ingénierie & Stabilité** (Intentionnalité UI, formats FP32, threading Qt/Audio, persistance `.ndaw`, protection Factory Presets). |
| **02** | [**`vibe_coding_guidelines.md`**](vibe_coding_guidelines.md) | **Règles de l'art du Vibe Coding** (Modifications motivées, chirurgie ciblée, palette de couleurs, tests continus). |
| **03** | [**`architecture.md`**](architecture.md) | **Architecture globale du logiciel** (Vue en 4 couches : UI, Modèle, Moteur Audio, Pont IPC/MCP et routage multi-sorties). |
| **04** | [**`project_and_serialization.md`**](project_and_serialization.md) | **Cycle de vie du Projet, Format `.ndaw` v1.1, Dirty State & Projets Récents** (`Project`, `serializer.py`, `RecentProjectsManager`, `audio_importer.py`). |
| **05** | [**`audio_engine_and_transport.md`**](audio_engine_and_transport.md) | **Moteur Audio & Barre de Transport** (`AudioEngine`, cache hybride RAM ASIO-Guard, enregistrement direct Audio/MIDI, prévisualisation et Choke Groups, boucle, export WAV). |
| **06** | [**`hardware_and_drivers.md`**](hardware_and_drivers.md) | **Matériel Audio, Pilotes ASIO/WASAPI, Latence & GPU** (`HardwareManager`, auto-configuration studio, tableau de bord latence RTL Cubase 6, compensation d'enregistrement, panneaux constructeurs, GPU). |
| **07** | [**`tracks_and_sequencing.md`**](tracks_and_sequencing.md) | **Pistes, Clips & Séquençage** (`Track`, `MidiClip`, `AudioClip` trimming non-destructif 1:1, routage multi-bus, duplication/réordonnancement, Piano Roll, Éditeur Audio, Timeline). |
| **08** | [**`plugin_system_and_vst_host.md`**](plugin_system_and_vst_host.md) | **Système d'Hébergement de Plugins & Hôte VST** (Architecture duale Natif vs VST3, scanner incrémental & watcher, détection VST2 PE, Rack Projet vs Instances de pistes, `NativePluginDialog`). |
| **09** | [**`plugins_specification.md`**](plugins_specification.md) | **Spécifications DSP des 5 Plugins Internes** (`BasePlugin`, `NovaSynth` multi-couches/multi-bus, `NovaDrumMachine`, `NovaEQ`, `NovaCompressor`, `NovaMixer`). |
| **10** | [**`preset_management.md`**](preset_management.md) | **Gestionnaire Universel de Presets** (`PresetManager`, structure `presets/<type_id>/`, schéma JSON/`.ndawpreset`, protection stricte des presets d'usine `NovaDAW Factory`). |
| **11** | [**`ui_ergonomics_and_sync.md`**](ui_ergonomics_and_sync.md) | **Ergonomie UI, Composants Sur-Mesure & Synchronisation** (`ResetableSlider`, `CompactNumEdit`, `TrackColorGrip`, `set_button_glow`, `MasterFxWidget` A/B, contrats de synchronisation bidirectionnelle). |
| **12** | [**`mcp_and_automation.md`**](mcp_and_automation.md) | **Orchestration IA & Protocole FastMCP** (`ActionRegistry`, catalogue des **44 outils MCP**, serveur TCP IPC non-bloquant, guide d'ajout d'action). |
| **13** | [**`testing_and_qa.md`**](testing_and_qa.md) | **Stratégie de Tests & Assurance Qualité** (Suite `pytest` avec `NOVADAW_SILENT_TESTS=1`, tests de stress audio, QA workflow plugins). |
| **14** | [**`roadmap_and_evolution.md`**](roadmap_and_evolution.md) | **Feuille de route & guide d'extension** (Recettes pas-à-pas pour ajouter un plugin, une action MCP ou un composant UI). |

---

## 🎯 Règle d'Or : Intentionnalité & Cohérence Modulaire

> **"Chaque changement doit avoir une raison d'être liée à la demande, et chaque module partage une ergonomie familière."**
> 
> - **Zéro changement cosmétique involontaire** : Une correction algorithmique ou logique ne doit jamais modifier l'aspect visuel des éléments existants.
> - **Évolution naturelle de l'UI** : Lorsqu'une fonctionnalité demande des contrôles graphiques, l'interface est enrichie harmonieusement en respectant la charte visuelle Cubase Pro.
> - **Synchronisation de bout en bout** : Toute nouvelle propriété traverse proprement le Modèle $\rightarrow$ Serializer `.ndaw` $\rightarrow$ Moteur Audio (invalidation ASIO-Guard) $\rightarrow$ Vues UI synchronisées $\rightarrow$ Outils MCP $\rightarrow$ Tests unitaires.
> - **Validation par les tests** : Chaque intervention est validée par l'exécution de la suite de tests automatisés (`python -m pytest`).
