# 🗺️ Feuille de Route, Évolutions Futures & Guide d'Extension

Ce document trace les perspectives d'évolution de **NovaDAW** et définit les patrons d'extension recommandés pour enrichir le logiciel sans jamais introduire de régression.

---

## 1. Vision et Axes d'Évolution

NovaDAW ambitionne de devenir la station de production musicale open-source de référence, nativement conçue pour la synergie entre musiciens humains et intelligence artificielle agentique.

### Principaux Axes de Développement :
1. **Intégration Avancée de Contrôleurs MIDI Matériels** :
   - Support des surfaces de contrôle standard (Mackie Control Universal / MCU, HUI, Novation Launchpad).
   - Mapping MIDI Learn interactif pour tous les potentiomètres et faders.
2. **Pistes d'Automation Graphiques** :
   - Courbes d'automation sur la timeline (Volume, Panoramique, Cutoff, Résonance, Paramètres VST).
   - Outils de dessin de courbes (Bézier, interpolation linéaire).
3. **Moteur de Pitch-Shifting et Time-Stretching en Temps Réel** :
   - Algorithmes d'étirement temporel de haute qualité (WSOLA / Rubber Band / Phase Vocoder) pour adapter automatiquement les boucles audio au BPM du projet.
4. **Pistes d'Accords & Générateur Harmonique IA** :
   - Piste de référence harmonique (Chord Track style Cubase Pro) guidant les synthétiseurs et les suggestions mélodiques de l'agent.
5. **Enrichissement de la Suite d'Instruments & Effets Natifs** :
   - Émulateur d'ampli guitare & baffle à convolution IR.
   - Processeur de chorus/flanger analogique stéréo.
   - Instrument échantillonneur polyphonique avec mapping de zones et vélocités (NovaSampler).

---

## 2. Patrons d'Extension Recommandés (Patterns sans Régression)

Lorsqu'un agent IA ou un développeur est chargé d'ajouter une nouvelle brique au projet, il doit se conformer aux patrons ci-dessous.

### 2.1. Patron d'Ajout d'un Nouvel Effet Audio Interne
1. **Dossier Dédié** : Créer `plugins/nom_effet/` avec `nom_effet_plugin.py` et `nom_effet_gui.py`.
2. **Héritage** : Dériver de `BasePlugin` (`plugins/base.py`).
3. **DSP Isolé** : Conserver l'état interne (tampons de delay, filtres) dans des attributs d'instance, jamais dans des variables globales.
4. **Enregistrement** : Appliquer `@register_plugin(plugin_type_id="novadaw.nom_effet", ...)` et importer dans `plugins/registry.py:ensure_plugins_loaded()`.
5. **Test Associé** : Créer `tests/test_nom_effet.py` vérifiant la forme `(N, 2)` en FP32, la sérialisation `get_state()`/`set_state()`, et le `reset()`.

### 2.2. Patron d'Ajout d'une Nouvelle Action Exposable à l'IA
1. **Fichier Cible** : Ouvrir le sous-module adapté dans `core/actions/` (ex: `core/actions/project.py`, `core/actions/midi.py`).
2. **Décorateur Déclaratif** :
   ```python
   @action_registry.register(
       name="novadaw_nom_action",
       description="Explication précise pour l'IA du but et des paramètres attendus.",
       tags=["domaine"]
   )
   def nom_action(app, param_obligatoire: str, param_optionnel: float = 1.0) -> dict:
       # Accès thread-safe aux modèles et à l'UI via 'app'
       ...
       return {"status": "success", "data": ...}
   ```
3. **Zéro Édition de `core/mcp/server.py`** : FastMCP découvre l'outil automatiquement au redémarrage.
4. **Validation** : Vérifier l'exécution dans `tests/test_mcp_integration.py`.

### 2.3. Patron d'Évolution du Modèle de Données (`core/project.py`)
1. **Valeurs par Défaut Impératives** :
   Tout nouveau champ ajouté à une dataclass (`Track`, `Project`, `MidiClip`, etc.) doit obligatoirement avoir une valeur par défaut.
2. **Désérialisation Tolérante** :
   Dans la méthode `from_dict()`, utiliser impérativement `.get("nouveau_champ", valeur_par_defaut)`.
3. **Test de Rétrocompatibilité** :
   S'assurer que `tests/test_project.py` et les fichiers `.ndaw` existants se chargent sans aucune exception.

---

## 3. Règle de Documentation Continue

Toute nouvelle fonctionnalité implémentée dans NovaDAW doit faire l'objet :
- D'une mise à jour de la spécification correspondante dans le dossier `doc/`.
- D'une mise à jour de la documentation utilisateur dans `README.md` si la fonctionnalité est accessible depuis l'interface.
- D'un test unitaire assurant son maintien dans le temps.
