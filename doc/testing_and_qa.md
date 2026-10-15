# 🧪 Stratégie de Tests, Validation Automatisée & Assurance Qualité (QA)

Ce document décrit l'architecture de test de **NovaDAW**, les procédures d'exécution de la suite automatisée et les règles de validation garantissant la **non-régression absolue** lors du développement assisté par IA.

---

## 1. Philosophie des Tests dans NovaDAW

Dans un logiciel audio intégrant du DSP temps réel, de l'I/O matériel et une interface graphique réactive, les bugs régressifs peuvent être destructeurs (clics numériques, désynchronisations, blocages de thread ou plantages mémoire).

La politique de test repose sur deux principes cardinaux :
1. **Zéro Régression Tolérée** : Tout test qui passait avant une intervention **doit obligatoirement continuer de passer**.
2. **Environnement Silencieux et Isolé (`NOVADAW_SILENT_TESTS`)** : Les tests automatisés s'exécutent en isolant les cartes sons physiques réelles pour ne pas saturer les haut-parleurs de l'utilisateur ni dépendre d'un périphérique spécifique.

---

## 2. Cartographie de la Suite de Tests (`tests/`)

La suite de tests comprend plus de 90 tests automatisés couvrant l'ensemble du système :

| Fichier de Test | Périmètre & Fonctionnalités Vérifiées |
| :--- | :--- |
| `test_project.py` | Modèle de données (`Track`, `Project`, `MidiClip`, `AudioClip`), intégrité JSON `.ndaw`, création d'instances. |
| `test_project_plugin_workflow.py` | **Parcours complet des plugins** : Recherche, ajout au rack, assignation d'instruments, isolation des plantages VST, absence de superposition sonore, restauration des états natifs. |
| `test_plugins.py` & `test_plugins_stack.py` | Traitement DSP, ordre d'évaluation des chaînes d'effets, bypass, reset des mémoires de délai. |
| `test_compressor_realtime.py` | Compresseur Giannoulis : seuil, ratio, soft-knee, réduction de gain (GR), absence de distorsion harmonique. |
| `test_drum_plugin.py` | Nova Drums VSTi : chargement d'échantillons FP32, GM mapping, choke group charleston, Freeverb intégrée. |
| `test_synth_plugin.py` & `test_synth_improvements.py` | NovaSynth : PolyBLEP anti-aliasing, SVF Cytomic, matrice multi-sorties 10 bus, sound stacking, presets d'usine. |
| `test_tcp_ipc.py` & `test_mcp_integration.py` | Serveur et client TCP IPC, protocoles d'actions, intégration et robustesse FastMCP. |
| `test_v1_1_features.py` | Importateur audio multi-formats, détection GPU/VRAM et pilotes audio hôtes. |
| `test_unsaved_changes_protection.py` | Détection de projet modifié (`is_dirty`), alertes de sauvegarde à la fermeture et protection contre la perte de travail. |
| `test_audio_editing_and_grid.py` | Outils de découpe (Split), aimantage de grille (Snap), duplication de blocs. |
| `test_loop_and_ruler.py` | Règle temporelle, positionnement de la boucle et continuité de phase. |
| `test_zoom_and_autoscroll.py` | Zoom horizontal/vertical et défilement automatique avec la tête de lecture. |
| `test_recording.py` & `test_live_recording_and_waveform.py` | Flux de capture microphone, compensation de latence et rendu temps réel de la forme d'onde. |

---

## 3. Commandes d'Exécution

### 3.1. Exécution Ciblée d'un Module de Test (Recommandé en Vibe Coding)
Pour valider rapidement une intervention sans attendre la suite globale :
```powershell
python -m pytest tests/test_project_plugin_workflow.py -q
```

### 3.2. Exécution d'un Test Spécifique
```powershell
python -m pytest tests/test_drum_plugin.py -k "test_choke_group" -v
```

### 3.3. Exécution Complète de la Suite de Non-Régression
```powershell
python -m pytest -q
```

---

## 4. Protocole de Résolution d'Anomalie pour un Agent IA

Si lors d'une intervention un test échoue :

1. **Ne jamais désactiver le test** : Il est strictement prohibé de commenter un `assert`, de décorer le test avec `@pytest.mark.skip` ou de modifier les attentes du test pour "faire passer au vert" sans accord explicite.
2. **Analyser le diff récent** : L'échec signale un effet de bord inattendu introduit par les dernières modifications.
3. **Corriger la cause racine** : Ajuster l'implémentation pour satisfaire à la fois le nouveau comportement et l'ancien contrat testé.
4. **Re-tester immédiatement** jusqu'à obtention d'un passage à 100%.

---

## 5. Limites Observées & Cas Particuliers Connus

Comme consigné dans `tests/PLUGIN_WORKFLOW_QA.md` :
- **Plugins VST3 natifs complexes** : Certains plugins tiers propriétaires (ex: Kontakt lors de certains rendus MIDI ou Prologue pour son éditeur fenêtré) peuvent provoquer des instabilités natives. L'architecture `IsolatedPlugin` garantit que ces plantages sont contenus dans le processus fils sans fermer NovaDAW.
- **Environnement Headless / CI** : Certains tests instancient des widgets PySide6 avec `pytest-qt` ou une fixture `QApplication`. En environnement d'intégration continue sans écran, utiliser la variable d'environnement `QT_QPA_PLATFORM=offscreen`.
