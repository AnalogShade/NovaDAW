# 🎛️ Spécification Complète du Système de Plugins & DSP

NovaDAW intègre un système modulaire de plugins audio et d'instruments virtuels, combinant des processeurs DSP internes en précision FP32, une gestion universelle de presets et un hôte de plugins externes VST3 hautement sécurisé.

---

## 1. Contrat Fondamental : `BasePlugin` (`plugins/base.py`)

Tout plugin interne (effet ou instrument) doit hériter de la classe abstraite `BasePlugin` et respecter son cycle de vie.

### 1.1. Interface Abstraite et Méthodes Obligatoires

```python
class BasePlugin(ABC):
    def __init__(self, plugin_type_id: str, name: str = "Plugin", category: str = "effect", icon: str = "🎛️", instance_id: Optional[str] = None):
        self.instance_id: str = instance_id or str(uuid.uuid4())[:8]
        self.plugin_type_id: str = plugin_type_id
        self.name: str = name
        self.category: str = category  # "eq", "dynamics", "mixer", "instrument", "utility"
        self.icon: str = icon
        self.enabled: bool = True      # Bypass si False

    @abstractmethod
    def process(self, audio: np.ndarray, sample_rate: int) -> np.ndarray:
        """
        Traite un buffer audio stéréo.
        audio: ndarray shape (N, 2) dtype float32
        sample_rate: fréquence d'échantillonnage en Hz (ex: 44100, 48000)
        Retourne: ndarray (N, 2) dtype float32
        """
        pass

    @abstractmethod
    def get_state(self) -> Dict[str, Any]:
        """Retourne l'état complet du plugin (sérialisable en JSON pur)."""
        pass

    @abstractmethod
    def set_state(self, state: Dict[str, Any]) -> None:
        """Restaure l'état du plugin depuis un dictionnaire."""
        pass

    def reset(self) -> None:
        """Réinitialise les mémoires de délai et intégrateurs lors de l'arrêt du transport."""
        pass

    def create_editor(self, parent=None) -> Optional[QWidget]:
        """Retourne le widget graphique PySide6 dédié, ou None."""
        return None
```

### 1.2. Sérialisation et Restauration
- Les méthodes `to_dict()` et `from_dict()` assurent l'enregistrement transparent dans le fichier de projet `.ndaw`.
- L'état sérialisé contient systématiquement `instance_id`, `plugin_type_id`, `name`, `category`, `enabled`, et le dictionnaire `state`.

---

## 2. Le Standard Modulaire des Plugins : Le "Module Blueprint" & `NativePluginDialog`

> [!NOTE]
> Pour l'architecture du scanner VST, du cache incrémental, du Rack de plugins projet (`project.plugin_rack`) et de l'isolation des instances par piste (`track:<id>:...`), consultez [**`doc/plugin_system_and_vst_host.md`**](plugin_system_and_vst_host.md). Pour le stockage des presets et la protection des presets d'usine, consultez [**`doc/preset_management.md`**](preset_management.md).

Afin de garantir une **cohérence parfaite** à travers tout le logiciel, chaque plugin interne (`novadaw.*`) est conçu comme un **module autonome standardisé** dont l'éditeur (`plugin.create_editor()`) est automatiquement encapsulé par [**`NativePluginDialog`**](../ui/plugin_dialogs.py).

### 2.1. La Barre Universelle de Presets (`NativePluginDialog`)
Lorsque l'utilisateur ouvre l'interface d'un plugin interne via `open_native_plugin_editor(plugin, parent, track)`, `NativePluginDialog` injecte automatiquement en haut de la fenêtre la barre unifiée `native_plugin_preset_bar` :

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ BARRE SUPÉRIEURE UNIFIÉE (Gérée automatiquement par NativePluginDialog)                │
├───────────────────────┬──────────────────────────┬──────────────┬───────────┬──────────┤
│ Preset : [Menu Combo] │ 💾 Enregistrer           │ 📂 Importer  │ 📤 Export │ 🗑️ Suppr │
│ ([Usine] + [User])    │ (Protège les Factory)    │ (.json/.ndaw)│           │          │
├───────────────────────┴──────────────────────────┴──────────────┴───────────┴──────────┤
│ EN-TÊTE INTERNE & ZONE SPÉCIFIQUE DU PLUGIN (retournés par plugin.create_editor())     │
│ (Identité visuelle, 🔄 Reset, ⚡ Bypass, faders, courbes interactives, oscillateurs)   │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

#### Règles d'Implémentation pour les Agents :
1. **Ne pas dupliquer la barre de presets dans le widget interne** : `NativePluginDialog` fournit déjà `combo_presets`, `💾 Enregistrer`, `📂 Importer...`, `📤 Exporter...` et `🗑️ Supprimer`. Si un widget interne possède un ancien conteneur nommé `preset_container`, `NativePluginDialog` le masque automatiquement (`internal_preset_bar.hide()`).
2. **Méthodes de synchronisation attendues sur le widget de `create_editor()`** :
   - `_load_from_plugin()` ou `_refresh_ui()` : appelée automatiquement par `NativePluginDialog._sync_editor_ui()` après le chargement ou l'importation d'un preset pour rafraîchir tous les contrôles graphiques.
3. **Invalidation du cache ASIO-Guard** : Tout changement de paramètre DSP dans le widget ou chargement de preset doit invalider le cache de la piste associée via `audio_engine.invalidate_track_cache(track_id)`.

### 2.2. Intégration avec le Gestionnaire de Presets (`core/preset_manager.py`)
Tout plugin implémentant `BasePlugin` s'interface avec le gestionnaire centralisé :
- Les presets d'usine sont définis dans `FACTORY_PRESETS` et retournés par `get_factory_presets()`.
- Les presets utilisateur sont stockés dans `presets/<plugin_type_id>/<nom_preset>.json`.
- Le gestionnaire protège strictement les presets d'usine contre l'écrasement ou la suppression (`is_factory_preset()`).

---

## 3. Le Registre des Plugins : `PluginRegistry` (`plugins/registry.py`)

Le registre est un singleton centralisant l'énumération et l'instanciation dynamique des plugins :

- Décorateur d'enregistrement automatique :
  ```python
  @register_plugin(
      plugin_type_id="novadaw.mon_plugin",
      name="Mon Plugin",
      category="effect",
      icon="✨",
      description="Description...",
      is_default=False
  )
  class MonPlugin(BasePlugin):
      ...
  ```
- Fonction de chargement garanti : `ensure_plugins_loaded()` importe explicitement les modules intégrés au démarrage pour alimenter le registre.

---

## 4. Les Processeurs d'Effets Intégrés

### 4.1. Égaliseur Paramétrique Haute Précision (`novadaw.equalizer`)
- **Filtres IIR Biquad** : Implémente l'architecture *Direct Form II Transposed* basée sur le standard *RBJ Audio EQ Cookbook* de Robert Bristow-Johnson.
- **Topologie Modulaire** : Configuration par défaut en 3, 10, 12 ou 24 bandes.
- **Types de Filtres Supportés** :
  - `peaking` : Filtre en cloche avec fréquence centrale, gain en dB et facteur de qualité $Q$.
  - `low_shelf` : Filtre plateau pour les basses fréquences.
  - `high_shelf` : Filtre plateau pour les hautes fréquences.
  - `low_pass` : Filtre passe-bas à résonance.
  - `high_pass` : Filtre passe-haut à résonance.
- **Continuité Temporelle** : Les états de retard (`zi_left`, `zi_right`) sont maintenus entre chaque bloc audio pour garantir un traitement **zéro-clic**.
- **Presets d'Usine** : `bass_boost`, `vocal_clarity`, `warm_master`, `bright_air`, `flat`.
- **Interface Graphique** : Courbe logarithmique (20 Hz - 20 kHz) interactive avec nœuds déplaçables à la souris en direct.

### 4.2. Compresseur Dynamique de Studio (`novadaw.compressor`)
- **Détecteur Découplé sans Distorsion** : Architecture Giannoulis éliminant toute distorsion harmonique et tout artefact de passage à zéro sur les basses fréquences ($\text{THD} < 0.05\%$).
- **Modes de Détection** :
  - `peak` : Détection crête ultra-rapide et percutante (idéal pour batterie, percussions, transitoires).
  - `rms` : Détection RMS douce et musicale (idéal pour voix, basse, bus master).
- **Courbure Soft-Knee** : Transition progressive réglable de 0 dB (Hard-Knee franc) à 12 dB (Soft-Knee transparent).
- **Sidechain HPF Débrayable (80 Hz)** : Filtre passe-haut sur le circuit de détection pour éviter l'effet de pompage indésirable provoqué par les basses.
- **Compression Parallèle** : Contrôle Dry/Wet Mix permettant la compression percutante sans perte de dynamique naturelle.
- **Télémétrie en Direct** : VU-mètre de Réduction de Gain (GR) et indicateurs Peak In/Out rafraîchis en temps réel.
- **Presets d'Usine** : `punchy_drums`, `vocal_leveler`, `master_glue`, `parallel_crush`, `gentle`.

### 4.3. Console de Mixage (`novadaw.mixer`)
- Console complète intégrée au Master et aux tranches individuelles.
- Faders de volume gradués en décibels professionnels avec échelle logarithmique.
- Panoramique stéréo avec compensation de puissance (Pan Law).
- Commandes d'écoute Master : Commutateur **Mono** (sommation $(L+R)/2$) et atténuateur **Dim** (-12 dB).
- VU-mètres dynamiques L/R haute résolution avec mémoire de crête et détection de clipping.

---

## 5. Les Instruments Virtuels Intégrés (VSTi)

### 5.1. Boîte à Rythmes & Batterie Nova Drums VSTi (`novadaw.drum_machine`)
- **Échantillonnage Haute Fidélité FP32** : 9 éléments acoustiques de studio générés par IA via Stable Audio 3 Medium (1.4B) + SAME-Large sur GPU.
- **Mapping General MIDI Standard** :
  Kick (36), Snare (38), Closed Hat (42), Open Hat (46), Low Tom (41), Mid Tom (47), High Tom (50), Crash (49), Ride (51).
- **Choke Group Intelligent** : Le charleston fermé étouffe instantanément et naturellement la résonance du charleston ouvert avec un fondu progressif de 5 ms.
- **Processeur de Réverbération Stéréo Intégré** : Algorithme Freeverb/Schroeder avec réglages de *Room Size*, *Damping*, *Width* et mélange *Wet/Dry*.
- **Contrôles Acoustiques par Pad** : Volume, panoramique, pitch ($\pm 12$ demi-tons), durée de decay et envoi réverbération (*Reverb Send*).

### 5.2. Synthétiseur Polyphonique Modulaire NovaSynth (`novadaw.synth`)
- **Synthèse 100% DSP Pure (FP32)** :
  - 7 formes d'ondes anti-aliasées PolyBLEP : `saw`, `square` (PWM), `sine`, `triangle`, `noise`, `fm` (2 opérateurs), et `supersaw` (7 voix stéréo Roland JP-8000 style).
- **Sound Stacking (Empilement de Couches)** : Superposition d'un nombre illimité de couches sonores par note avec transposition d'octaves et micro-désaccordage (*Fine Tune*).
- **Matrice Multi-Sorties 10 Bus Stéréo (Bus 0 à Bus 9 / Out 1 à Out 10)** : Permet à plusieurs pistes du DAW de piloter la même instance sur des canaux audio séparés.
- **Double Enveloppe ADSR & Filtre Analogique Cytomic SVF** :
  - Filtre State-Variable multimode (Passe-Bas, Passe-Haut, Passe-Bande, Rejet/Notch) avec résonance $Q$ jusqu'à 10 et saturation analogique (*Drive*).
- **LFO Multi-Destinations & Effets d'Espace** : LFO routable vers pitch, cutoff, pan ou volume, Délai Ping-Pong stéréo et Réverbération studio.
- **Banque d'Usine de 34 Presets** : Leads Cyberpunk, Pads, Basses, Harpes, Violons et Rhodes.

---

## 6. Hôte Crash-Isolé pour Plugins Tiers VST3 (`core/vst_host.py`)

Les plugins VST3 externes tiers s'exécutent dans un environnement hermétique via `IsolatedPlugin` :
1. **Sous-processus Dédié** : Chaque instance externe tourne dans un processus séparé (`spawn`) avec communication par IPC Pipe binaire.
2. **Hook de Table Virtuelle Multicanal (`_apply_multibus_vst_fix`)** : Empêche les violations d'accès mémoire (`0xC0000005`) causées par les instruments multicanaux sous Pedalboard/JUCE.
3. **Fenêtres Graphiques Natives** : L'éditeur natif VST3 s'exécute sur le thread principal du processus fils sans bloquer la boucle d'événements Qt de NovaDAW.
4. **Contrôle d'Erreur sans Crash** : En cas de crash du plugin, seule son instance s'arrête en silence ; NovaDAW continue de fonctionner normalement.

---

## 7. Guide Pas-à-Pas : Créer un Nouveau Plugin Modulaire

Pour ajouter un nouveau plugin selon les règles de l'art du Module Blueprint :

1. **Créer le package dans `plugins/mon_plugin/`** :
   - `mon_plugin_plugin.py` : Dérive de `BasePlugin`, implémente le DSP FP32, `get_state()`, `set_state()`, `reset()`, et le dictionnaire `FACTORY_PRESETS`.
   - `mon_plugin_gui.py` : Construit l'interface avec la barre d'en-tête standardisée (Titre, Presets, Save, Load, Reset, Bypass) reliée à `PresetManager`.
2. **Enregistrer dans `plugins/registry.py`** avec `@register_plugin` et déclarer dans `ensure_plugins_loaded()`.
3. **Écrire le test unitaire** dans `tests/test_mon_plugin.py` validant le traitement audio stéréo FP32, le chargement des presets et la sérialisation d'état.
4. **Valider avec pytest** :
   ```powershell
   python -m pytest tests/test_mon_plugin.py -q
   ```
