# Licences des Composants et Bibliothèques Tierces

NovaDAW s'appuie uniquement sur des bibliothèques et composants tiers **sans aucune dépendance GPL forte (zéro GPLv3)**. Leurs codes et licences restent la propriété de leurs auteurs respectifs et sont régis par leurs propres conditions :

## 1. Dépendances Python principales (`requirements.txt`)

| Composant / Bibliothèque | Usage dans NovaDAW | Licence vérifiée |
| :--- | :--- | :--- |
| **PySide6 / shiboken6 (Qt 6)** | Interface graphique (UI) et décodage audio étendu (`QAudioDecoder`) | **LGPL-3.0-only** *(ou GPL / Commerciale Qt)* |
| **NumPy** | Traitement matriciel et buffers DSP | **BSD-3-Clause** |
| **SciPy** | Filtrage IIR, rééchantillonnage et convolution | **BSD-3-Clause** |
| **sounddevice** (PortAudio) | Entrées/sorties audio temps réel (ASIO/WASAPI) | **MIT** |
| **soundfile** (libsndfile) | Lecture/écriture audio (WAV, FLAC, OGG, AIFF, MP3) | **BSD-3-Clause** (wrapper) / **LGPL-2.1+** (`libsndfile`) |
| **cffi / pycparser** | Liaison FFI utilisée par `sounddevice` et `soundfile` | **MIT-0** / **BSD-3-Clause** |

## 2. SDK & Bridges C/C++ natifs (`core/bin/`)

| Composant | Usage dans NovaDAW | Licence vérifiée |
| :--- | :--- | :--- |
| **Steinberg VST 3.8+ SDK (`vst3_pluginterfaces`)** | En-têtes d'interface du bridge VST3 64-bit (`vst3_bridge_x64.cpp`) | **MIT** *(Copyright © Steinberg Media Technologies GmbH)* |
| **`vst2_bridge_x86.c`** | Bridge VST 2.4 32-bit écrit avec l'API Win32 standard | Code propriétaire NovaDAW (API Win32 standard) |

## 3. Outils optionnels et de développement (`requirements-dev.txt`)

| Composant / Bibliothèque | Usage dans NovaDAW | Licence vérifiée |
| :--- | :--- | :--- |
| **mcp** *(et dépendances : `anyio`, `httpx`, `pydantic`, `starlette`, `uvicorn`, `python-multipart`, `certifi`, `pywin32`)* | Serveur FastMCP pour pilotage par agent IA | **MIT** / **BSD-3-Clause** / **Apache-2.0** / **MPL-2.0** (`certifi`) / **PSF** (`pywin32`) |
| **pefile** *(optionnel)* | Inspection d'en-têtes PE des DLL VST2 | **MIT** |
| **pytest** *(tests)* | Exécution de la suite de tests automatisés | **MIT** |
| **PyInstaller** *(compilation)* | Empaquetage en exécutable autonome | **GPLv2-or-later avec exception bootloader** *(autorise les programmes non libres)* |

> [!NOTE]
> **Rappel concernant PySide6 (LGPLv3) et Steinberg VST3 SDK (MIT)** :
> - **Aucune dépendance GPLv3** n'est requise ni utilisée dans NovaDAW.
> - **PySide6** et `libsndfile` sont utilisés sous **LGPL** : en cas de compilation en exécutable autonome, veillez à conserver les bibliothèques dynamiques (DLL) et mentions de licence correspondantes.
> - **VST®** est une marque déposée de Steinberg Media Technologies GmbH ; l'utilisation des en-têtes VST 3.8+ dans `core/bin/vst3_sdk/pluginterfaces/` respecte la licence **MIT** incluse dans [`core/bin/vst3_sdk/pluginterfaces/LICENSE.txt`](core/bin/vst3_sdk/pluginterfaces/LICENSE.txt).
