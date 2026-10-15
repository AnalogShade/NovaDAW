# 🎛️ Spécification de la Gestion Matérielle, Pilotes Audio, Latence & GPU (`core/hardware_manager.py`, `ui/device_settings_dialog.py`)

Ce document spécifie la gestion des interfaces audio (ASIO, WASAPI, DirectSound, MME), l'auto-configuration au démarrage, le calcul de latence style Cubase, l'ouverture des panneaux de contrôle constructeurs et la détection GPU dans **NovaDAW**.

---

## 1. Architecture de `HardwareManager` (`core/hardware_manager.py`)

Le singleton `hardware_manager` (instance de [`HardwareManager`](../core/hardware_manager.py)) gère la détection matérielle et persiste ses réglages dans `settings.json` à la racine du projet.

### 1.1 Structure de `settings.json` (Clés `audio` et `graphics`)
```json
{
  "audio": {
    "driver_api": "WASAPI",
    "output_device_index": 4,
    "output_device_name": "Focusrite USB Audio",
    "input_device_index": 2,
    "input_device_name": "Focusrite USB Audio",
    "sample_rate": 48000,
    "buffer_size": 256,
    "record_offset_samples": 0,
    "asio_guard": true
  },
  "graphics": {
    "gpu_index": 0,
    "gpu_name": "NVIDIA GeForce RTX ...",
    "rendering_backend": "Direct3D 11 (Défaut Windows)",
    "vsync": true,
    "fps_limit": 60,
    "antialiasing": true
  },
  "recent_projects": []
}
```
> [!IMPORTANT]
> **Cohabitation non-destructive dans `settings.json`** : `HardwareManager.save_settings()` et [`RecentProjectsManager`](../core/recent_projects.py) partagent le même fichier `settings.json`. Toute sauvegarde doit lire le JSON existant sur disque avant d'écrire afin de ne jamais écraser la clé `recent_projects` (ou inversement `audio`/`graphics`).

---

## 2. Auto-Configuration Intelligente du Matériel Audio (`auto_configure_audio`)

Lors du premier lancement ou si le périphérique précédemment sauvegardé a été débranché, [`HardwareManager.auto_configure_audio(force=False)`](../core/hardware_manager.py) sélectionne automatiquement la meilleure configuration selon l'ordre de priorité suivant :

### 2.1 Sélection de la Sortie Audio (`output_device_index`)
1. **Périphérique sauvegardé valide** : Recherche d'abord par correspondance exacte de `output_device_name` (pour survivre aux changements d'index PortAudio lorsque l'on branche un périphérique USB), puis par `output_device_index`.
2. **Interfaces Studio Professionnelles (`KNOWN_STUDIO_BRANDS`)** :
   - Recherche les marques reconnues (`focusrite`, `scarlett`, `clarett`, `steinberg`, `ur22`, `ur44`, `rme`, `babyface`, `fireface`, `universal audio`, `apollo`, `volt`, `motu`, `presonus`, `audiobox`, `behringer`, `umc`, `audient`, `id4`, `id14`, `ssl`, `native instruments`, `komplete`, `arturia`, `minifuse`, `mackie`, `tascam`, `zoom`, `roland`, `rubix`, `m-audio`, `apogee`, `evo`, `avid`, `eleven rack`, `digidesign`, `pro tools`, `asio4all`, `fl studio asio`, `realtek asio`).
   - Priorise les APIs à faible latence (**`ASIO`** en premier, puis **`Windows WASAPI`**).
3. **Sortie Windows WASAPI par défaut** : Utilise le périphérique de sortie par défaut de l'API `WASAPI`.
4. **Sortie système par défaut** : Utilise `get_default_output_device_index()`.

### 2.2 Sélection de l'Entrée Audio (`input_device_index`)
1. Périphérique d'entrée sauvegardé valide (par nom puis par index).
2. Entrée appartenant à la même marque studio ou à la même API hôte que la sortie sélectionnée.
3. Entrée système par défaut (`get_default_input_device_index()`).

### 2.3 Fréquence d'Échantillonnage & Taille de Buffer Optimales
- **[`get_optimal_sample_rate(device_index)`](../core/hardware_manager.py)** :
  - Lit le `default_samplerate` matériel via `sd.query_devices()`.
  - Teste la validité réelle via `sd.check_output_settings(device=..., samplerate=...)` parmi `[default_sr, 48000, 44100, 96000, 88200]`.
- **[`get_optimal_buffer_size(device_index, sample_rate)`](../core/hardware_manager.py)** :
  - Si l'API hôte est **`ASIO`** ou **`WASAPI`** : **`256 samples`** (basse latence studio).
  - Si l'API hôte est **`DirectSound`** ou **`MME`** : **`512 samples`** (stabilité accrue).

---

## 3. Tableau de Bord des Latences & Compensation d'Enregistrement

### 3.1 Calcul Temps Réel des Latences ([`get_device_latencies`](../core/hardware_manager.py))
À partir des propriétés PortAudio (`default_low_output_latency`, `default_low_input_latency`) et de la durée du bloc (`buffer_ms = (buffer_size / sample_rate) * 1000.0`) :
- `input_latency_ms = max(buffer_ms, driver_in_ms + buffer_ms * 0.5)`
- `output_latency_ms = max(buffer_ms, driver_out_ms + buffer_ms)`
- `roundtrip_latency_ms = input_latency_ms + output_latency_ms` (Latence Totale Aller-Retour / RTL).

Dans [`DeviceSettingsDialog`](../ui/device_settings_dialog.py), un code couleur indique la qualité temps réel de la RTL :
- **Vert (`#10b981`)** : $\le 15\text{ ms}$ (Temps réel optimal).
- **Ambre (`#f59e0b`)** : $15\text{ ms} < \text{RTL} \le 30\text{ ms}$ (Modérée).
- **Rouge (`#ef4444`)** : $> 30\text{ ms}$ (Latence élevée).

### 3.2 Compensation de Latence d'Enregistrement (`record_offset_samples`)
- Réglable de **`-10000` à `+10000` échantillons** dans [`DeviceSettingsDialog`](../ui/device_settings_dialog.py) et transmis à `AudioEngine.record_latency_compensation_samples`.
- Lors de [`AudioEngine.stop_recording()`](../core/audio_engine.py) :
  - Si `offset > 0` : Tronque les `offset` premiers échantillons du buffer capturé (`audio_data = audio_data[offset:]`) pour recaler un enregistrement en retard.
  - Si `offset < 0` : Insère `-offset` échantillons de silence au début du buffer capturé.

---

## 4. Ouverture des Panneaux de Contrôle Constructeurs (`open_audio_driver_control_panel`)

Le bouton **"🎛️ Tableau de bord du pilote (ASIO / Matériel)"** de [`DeviceSettingsDialog`](../ui/device_settings_dialog.py) appelle [`HardwareManager.open_audio_driver_control_panel()`](../core/hardware_manager.py) qui détecte le nom du périphérique actif et lance l'application native du fabricant :
- **Avid / Digidesign Eleven Rack** : `Eleven Rack Control Panel.exe`
- **Focusrite (Scarlett / Clarett)** : `Focusrite Control.exe` / `FocusriteControl2.exe`
- **ASIO4ALL** : `a4apanel.exe` / `a4apanel64.exe`
- **RME (Babyface / Fireface)** : `TotalMixFX.exe` + `firefaceusb.exe`
- **Steinberg / Yamaha** : `ysusb_cp.exe`
- **PreSonus** : `UniversalControl.exe`
- **MOTU** : `MOTU Audio Console.exe`
- **Repli universel Windows** : Si aucun exécutable constructeur dédié n'est trouvé sur le disque, ouvre le panneau Son de Windows via `control mmsys.cpl sounds`.

---

## 5. Détection GPU & Paramètres Graphiques (`get_available_gpus`)

[`HardwareManager.get_available_gpus()`](../core/hardware_manager.py) détecte les cartes graphiques installées sans dépendance lourde :
1. **Méthode principale (PowerShell CIM)** : Interroge `Get-CimInstance Win32_VideoController | Select-Object Name, AdapterRAM, DriverVersion | ConvertTo-Json -Compress` avec `CREATE_NO_WINDOW` et convertit `AdapterRAM` en Mo (`vram_mb`).
2. **Méthode de repli (Win32 API `ctypes`)** : Utilise `ctypes.windll.user32.EnumDisplayDevicesW` pour énumérer les adaptateurs d'affichage actifs (`DISPLAY_DEVICE_ATTACHED_TO_DESKTOP`) si PowerShell est indisponible.
3. **Repli minimal** : Retourne `" Carte Graphique Principale (Pilote Système)"`.
