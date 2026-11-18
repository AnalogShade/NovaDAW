# Licences des Composants, Dépendances et Ressources Tierces

Le code source de **NovaDAW** (écrit en Python, C et C++ dans ce dépôt) est distribué sous la [**licence MIT**](LICENSE).
Conformément aux principes de transparence et de respect du travail des auteurs tiers, ce document recense l'ensemble des **bibliothèques directes, dépendances transitives, SDK natifs et ressources audio** utilisés par le projet (de manière proportionnelle à leur rôle réel), ainsi que leurs licences et sites officiels.

> [!NOTE]
> **Distribution sous forme de code source** : NovaDAW est actuellement distribué uniquement sous forme de code source. Les dépendances Python ci-dessous ne sont pas embarquées dans le dépôt : elles sont installées dynamiquement par l'utilisateur via `pip` depuis leurs distributions officielles. Aucune dépendance **GPLv3** ou **AGPL** n'est requise ni utilisée par NovaDAW.

---

## 1. Dépendances Python Principales — Directes et Transitives (`requirements.txt`)

### 1.1 Dépendances directes (cœur de l'application)

| Paquet (version vérifiée) | Rôle dans NovaDAW | Auteurs / Organisation | Licence | Site officiel / Dépôt |
| :--- | :--- | :--- | :--- | :--- |
| **PySide6** (`6.11.1`) *(+ `PySide6_Essentials`, `PySide6_Addons`, `shiboken6`)* | Interface graphique Qt 6 et décodage audio étendu via `QAudioDecoder` (inclut le plugin `ffmpegmediaplugin.dll` basé sur [FFmpeg](https://ffmpeg.org)) | Qt for Python Team / The Qt Company *(FFmpeg Project)* | **LGPL-3.0-only** *(FFmpeg : LGPL-2.1+)* | [pyside.org](https://pyside.org) · [qt.io](https://www.qt.io) · [ffmpeg.org](https://ffmpeg.org) |
| **numpy** (`2.3.3`) | Calcul vectoriel et buffers audio FP32 *(inclut [OpenBLAS](https://github.com/OpenMathLib/OpenBLAS/)/LAPACK `BSD-3-Clause`, dragon4 `MIT`, libdivide `Zlib`)* | Travis E. Oliphant et al. / NumPy Developers | **BSD-3-Clause** | [numpy.org](https://numpy.org) · [github.com/numpy/numpy](https://github.com/numpy/numpy) |
| **scipy** (`1.17.1`) | Filtrage IIR biquad, rééchantillonnage polyphase et convolution FFT | SciPy Developers | **BSD-3-Clause** | [scipy.org](https://scipy.org/) |
| **sounddevice** (`0.5.5`) | Entrées/sorties audio temps réel via **PortAudio** | Matthias Geier *(PortAudio : Ross Bencina & Phil Burk)* | **MIT** *(voir note ASIO ci-dessous)* | [github.com/spatialaudio/python-sounddevice](https://github.com/spatialaudio/python-sounddevice/) · [portaudio.com](http://www.portaudio.com/) |
| **soundfile** (`0.14.0`) | Lecture/écriture audio (WAV, FLAC, OGG, AIFF, MP3) via **`libsndfile`** | Bastian Bechtold *(libsndfile : Erik de Castro Lopo et contributeurs)* | **BSD-3-Clause** (wrapper) / **LGPL-2.1+** (`libsndfile`) | [github.com/bastibe/python-soundfile](https://github.com/bastibe/python-soundfile) · [libsndfile.github.io/libsndfile](https://libsndfile.github.io/libsndfile/) |

### 1.2 Dépendances transitives (installées automatiquement par `requirements.txt`)

| Paquet (version vérifiée) | Installé par | Auteurs / Organisation | Licence | Site officiel / Dépôt |
| :--- | :--- | :--- | :--- | :--- |
| **cffi** (`2.1.1`) | `sounddevice`, `soundfile` | Armin Rigo, Maciej Fijalkowski | **MIT-0** | [github.com/python-cffi/cffi](https://github.com/python-cffi/cffi) |
| **pycparser** (`3.0`) | `cffi` | Eli Bendersky | **BSD-3-Clause** | [github.com/eliben/pycparser](https://github.com/eliben/pycparser) |
| **typing_extensions** (`4.16.0`) | `soundfile` | Guido van Rossum et al. | **PSF-2.0** | [github.com/python/typing_extensions](https://github.com/python/typing_extensions) |

> **Précision concernant PortAudio / ASIO (`sounddevice`)** :
> Le paquet `_sounddevice_data` fournit deux DLL PortAudio sous Windows : `libportaudio64bit.dll` (**MIT**, WASAPI/MME/DirectSound/WDM-KS) et `libportaudio64bit-asio.dll` (compilée avec le SDK Steinberg ASIO). NovaDAW **n'active pas** `SD_ENABLE_ASIO` et utilise par défaut la DLL **MIT** (`libportaudio64bit.dll`) ainsi que le mode **WASAPI Exclusif**. Si vous décidez un jour de distribuer un exécutable autonome, veillez à exclure `libportaudio64bit-asio.dll` à moins de détenir une licence Steinberg ASIO SDK conforme.

---

## 2. SDK, Bridges C/C++ Natifs & Références DSP (`core/bin/`, `plugins/`)

| Composant | Usage dans NovaDAW | Licence / Auteur | Site officiel |
| :--- | :--- | :--- | :--- |
| **Steinberg VST 3.8+ SDK (`pluginterfaces`)** | En-têtes d'interface du bridge VST3 64-bit (`vst3_bridge_x64.cpp`) | **MIT** *(Copyright © Steinberg Media Technologies GmbH)* — voir [`core/bin/vst3_sdk/pluginterfaces/LICENSE.txt`](core/bin/vst3_sdk/pluginterfaces/LICENSE.txt) | [steinberg.net](https://www.steinberg.net) · [github.com/steinbergmedia/vst3sdk](https://github.com/steinbergmedia/vst3sdk) |
| **`vst3_bridge_x64.cpp` / `.exe`** | Bridge natif isolé VST3 64-bit (API Win32 + SDK VST3 MIT) | **MIT** *(NovaDAW)* | — |
| **`vst2_bridge_x86.c` / `.exe`** | Bridge natif isolé VST 2.4 32-bit écrit avec l'API Win32 standard et définitions ABI autonomes (aucun fichier du SDK VST2 Steinberg inclus) | **MIT** *(NovaDAW)* | — |

> [!NOTE]
> **Rappel concernant les licences LGPL, les marques et les algorithmes DSP** :
> - **PySide6 (`LGPL-3.0-only`)**, son plugin **`ffmpegmediaplugin.dll` (`LGPL`)** et **`libsndfile` (`LGPL-2.1+`)** sont compatibles avec la diffusion du code source de NovaDAW sous licence MIT. Si vous compilez un exécutable autonome pour le redistribuer, utilisez un empaquetage conservant les bibliothèques dynamiques (DLL) séparées (ex. PyInstaller `--onedir`) et incluez les notices et textes de licences LGPL correspondants.
> - **VST®** est une marque déposée de Steinberg Media Technologies GmbH ; son usage dans NovaDAW est purement nominatif pour indiquer la compatibilité avec le format de plugins.
> - **Références DSP & Algorithmes internes** : les modules natifs `NovaEQ`, `NovaCompressor`, `NovaSynth` et `Nova Drums` ont été implémentés en Python/NumPy/SciPy à partir d'architectures DSP publiques et académiques classiques (filtres biquad *Audio EQ Cookbook* de Robert Bristow-Johnson, architecture de compresseur de Giannoulis/Massberg/Reiss, filtres SVF Chamberlin/Cytomic, oscillateurs PolyBLEP, et réverbération Schroeder/Freeverb).

---

## 3. Outils Optionnels (IA MCP, Tests, Inspection & Packaging — `requirements-dev.txt`)

### 3.1 Dépendances directes (optionnelles / développement)

| Paquet (version vérifiée) | Rôle optionnel | Auteurs / Organisation | Licence | Site officiel / Dépôt |
| :--- | :--- | :--- | :--- | :--- |
| **mcp** (`1.28.1`) | Serveur FastMCP pour orchestration par agent IA (`core/mcp/server.py`) | Anthropic, PBC et contributeurs MCP | **MIT** | [modelcontextprotocol.io](https://modelcontextprotocol.io) |
| **pefile** (`2023.2.7`) | Inspection statique d'en-têtes PE des DLL VST2 | Ero Carrera | **MIT** | [github.com/erocarrera/pefile](https://github.com/erocarrera/pefile) |
| **pytest** (`9.1.1`) | Suite de tests automatisés | Holger Krekel, Ronny Pfannschmidt et al. | **MIT** | [docs.pytest.org](https://docs.pytest.org/en/latest/) |
| **PyInstaller** (`6.15.0`) | Empaquetage optionnel en exécutable autonome *(outil de build uniquement)* | Hartmut Goebel, Martin Zibricky et al. | **GPLv2-or-later avec exception bootloader** *(autorise les logiciels MIT et commerciaux)* | [pyinstaller.org](https://www.pyinstaller.org/) |

### 3.2 Dépendances transitives (installées automatiquement par `requirements-dev.txt`)

| Paquet (version vérifiée) | Installé par | Auteurs / Organisation | Licence | Site officiel / Dépôt |
| :--- | :--- | :--- | :--- | :--- |
| **anyio** (`4.15.1`) | `mcp` | Alex Grönholm | **MIT** | [github.com/agronholm/anyio](https://github.com/agronholm/anyio) |
| **idna** (`3.19`) | `anyio` / `httpx` | Kim Davies | **BSD-3-Clause** | [github.com/kjd/idna](https://github.com/kjd/idna) |
| **httpx** (`0.28.1`) | `mcp` | Tom Christie | **BSD-3-Clause** | [github.com/encode/httpx](https://github.com/encode/httpx) |
| **httpx-sse** (`0.4.3`) | `mcp` | Florimond Manca | **MIT** | [github.com/florimondmanca/httpx-sse](https://github.com/florimondmanca/httpx-sse) |
| **httpcore** (`1.0.9`) | `httpx` | Tom Christie | **BSD-3-Clause** | [encode.io/httpcore](https://www.encode.io/httpcore/) |
| **h11** (`0.16.0`) | `httpcore` | Nathaniel J. Smith | **MIT** | [github.com/python-hyper/h11](https://github.com/python-hyper/h11) |
| **certifi** (`2026.4.22`) | `httpx` | Kenneth Reitz / Certifi | **MPL-2.0** | [github.com/certifi/python-certifi](https://github.com/certifi/python-certifi) |
| **jsonschema** (`4.26.0`) | `mcp` | Julian Berman | **MIT** | [github.com/python-jsonschema/jsonschema](https://github.com/python-jsonschema/jsonschema) |
| **attrs** (`26.1.0`) | `jsonschema` | Hynek Schlawack | **MIT** | [github.com/python-attrs/attrs](https://github.com/python-attrs/attrs) |
| **jsonschema-specifications** (`2025.9.1`) | `jsonschema` | Julian Berman | **MIT** | [github.com/python-jsonschema/jsonschema-specifications](https://github.com/python-jsonschema/jsonschema-specifications) |
| **referencing** (`0.37.0`) | `jsonschema` | Julian Berman | **MIT** | [github.com/python-jsonschema/referencing](https://github.com/python-jsonschema/referencing) |
| **rpds-py** (`2026.6.3`) | `referencing` | Julian Berman | **MIT** | [github.com/crate-py/rpds](https://github.com/crate-py/rpds) |
| **pydantic** (`2.13.5`) | `mcp` | Samuel Colvin, Pydantic Team | **MIT** | [github.com/pydantic/pydantic](https://github.com/pydantic/pydantic) |
| **pydantic-settings** (`2.14.2`) | `mcp` | Samuel Colvin, Eric Jolibois, Hasan Ramezani | **MIT** | [github.com/pydantic/pydantic-settings](https://github.com/pydantic/pydantic-settings) |
| **pydantic_core** (`2.46.5`) | `pydantic` | Samuel Colvin, Pydantic Team | **MIT** | [github.com/pydantic/pydantic-core](https://github.com/pydantic/pydantic-core) |
| **annotated-types** (`0.8.0`) | `pydantic` | Adrian Garcia Badaracco, Samuel Colvin, Zac Hatfield-Dodds | **MIT** | [github.com/annotated-types/annotated-types](https://github.com/annotated-types/annotated-types) |
| **typing-inspection** (`0.4.4`) | `pydantic` / `pydantic-settings` | Victorien Plot | **MIT** | [github.com/pydantic/typing-inspection](https://github.com/pydantic/typing-inspection) |
| **python-dotenv** (`1.2.3`) | `pydantic-settings` | Saurabh Kumar | **BSD-3-Clause** | [github.com/theskumar/python-dotenv](https://github.com/theskumar/python-dotenv) |
| **PyJWT** (`2.14.0`) | `mcp` | Jose Padilla | **MIT** | [github.com/jpadilla/pyjwt](https://github.com/jpadilla/pyjwt) |
| **python-multipart** (`0.0.32`) | `mcp` | Andrew Dunham | **Apache-2.0** | [github.com/Kludex/python-multipart](https://github.com/Kludex/python-multipart) |
| **pywin32** (`312`) | `mcp` (Windows) | Mark Hammond et al. | **PSF** | [github.com/mhammond/pywin32](https://github.com/mhammond/pywin32) |
| **starlette** (`1.6.0`) | `mcp` | Tom Christie | **BSD-3-Clause** | [github.com/Kludex/starlette](https://github.com/Kludex/starlette) |
| **sse-starlette** (`3.4.11`) | `mcp` | sysid | **BSD-3-Clause** | [github.com/sysid/sse-starlette](https://github.com/sysid/sse-starlette) |
| **uvicorn** (`0.53.0`) | `mcp` | Tom Christie | **BSD-3-Clause** | [github.com/Kludex/uvicorn](https://github.com/Kludex/uvicorn) |
| **click** (`8.5.0`) | `uvicorn` | Pallets Team | **BSD-3-Clause** | [github.com/pallets/click](https://github.com/pallets/click/) |
| **colorama** (`0.4.6`) | `pytest` (Windows) | Jonathan Hartley | **BSD-3-Clause** | [github.com/tartley/colorama](https://github.com/tartley/colorama) |
| **iniconfig** (`2.3.0`) | `pytest` | Ronny Pfannschmidt, Holger Krekel | **MIT** | [github.com/pytest-dev/iniconfig](https://github.com/pytest-dev/iniconfig) |
| **packaging** (`25.0`) | `pytest` | Donald Stufft / PyPA | **Apache-2.0 OR BSD-2-Clause** | [github.com/pypa/packaging](https://github.com/pypa/packaging) |
| **pluggy** (`1.6.0`) | `pytest` | Holger Krekel | **MIT** | [github.com/pytest-dev/pluggy](https://github.com/pytest-dev/pluggy) |
| **Pygments** (`2.21.0`) | `pytest` | Georg Brandl | **BSD-2-Clause** | [pygments.org](https://pygments.org) |
| **setuptools** (`80.9.0`) | `PyInstaller` | Python Packaging Authority | **MIT** | [github.com/pypa/setuptools](https://github.com/pypa/setuptools) |
| **altgraph** (`0.17.4`) | `PyInstaller` | Ronald Oussoren | **MIT** | [altgraph.readthedocs.io](https://altgraph.readthedocs.io) |
| **pywin32-ctypes** (`0.2.3`) | `PyInstaller` | Enthought Inc. | **BSD-3-Clause** | [github.com/enthought/pywin32-ctypes](https://github.com/enthought/pywin32-ctypes) |
| **pyinstaller-hooks-contrib** (`2025.8`) | `PyInstaller` | PyInstaller Contributors | **Apache-2.0 / GPL-2.0** | [github.com/pyinstaller/pyinstaller-hooks-contrib](https://github.com/pyinstaller/pyinstaller-hooks-contrib) |

---

## 4. Échantillons Audio du Kit par Défaut (`Nova Drums` — `default_kit`)

*(Note : cette section concerne **uniquement** les 12 fichiers `.wav` du kit de batterie par défaut du plugin **Nova Drums** ; le logiciel NovaDAW lui-même n'embarque et n'exécute aucun modèle d'intelligence artificielle générative.)*

Les échantillons `.wav` du kit de batterie par défaut inclus dans :
- `assets/drum_kits/default_kit/*.wav`
- `plugins/drum_machine/samples/default_kit/*.wav`

ont été générés hors ligne à l'aide du modèle **Stable Audio 3 Medium (`1.4B`) + `SAME-Large`** (scripts utilitaires optionnels [`scripts/generate_drum_kit_fp32.py`](scripts/generate_drum_kit_fp32.py) et [`scripts/generate_acoustic_kick_fp32.py`](scripts/generate_acoustic_kick_fp32.py), qui utilisent `torch` / `torchaudio` — [pytorch.org](https://pytorch.org), **BSD-3-Clause** — et le dépôt `stable-audio-3` — **MIT** pour le code).

- **Attribution pour ces échantillons** : *Default drum kit samples generated with Stable Audio 3 — Powered by Stability AI (Stability AI Community License, Copyright © Stability AI Ltd. All Rights Reserved).*
- **Licence applicable aux poids du modèle & sorties générées** : [**Stability AI Community License**](https://stability.ai/license)
- **Liens officiels** : [stability.ai](https://stability.ai) · [github.com/Stability-AI/stable-audio-3](https://github.com/Stability-AI/stable-audio-3) · [huggingface.co/collections/stabilityai/stable-audio-3](https://huggingface.co/collections/stabilityai/stable-audio-3)
- **Conditions applicables à ces fichiers `.wav`** :
  1. **Exclusion de la licence MIT** : ces fichiers `.wav` générés ne sont **pas** couverts par la licence MIT du code source de NovaDAW, mais restent régis par la **Stability AI Community License**.
  2. **Usage gratuit sous seuil de revenus (< 1 000 000 USD / an)** : la *Stability AI Community License* autorise gratuitement l'usage personnel, la recherche et l'usage commercial tant que votre chiffre d'affaires annuel brut (toutes sources confondues) est **inférieur à 1 000 000 USD par an** ; au-delà, une licence *Enterprise* auprès de Stability AI est requise.
  3. **Modèles concurrents** : ces sorties ne doivent pas être utilisées pour entraîner des modèles fondationnels concurrents.
  4. **Remplacement libre** : tout utilisateur peut remplacer ces `.wav` dans `Nova Drums` par ses propres échantillons ou des sons CC0 sans aucune limitation sur le reste de NovaDAW.

---

## 5. Contact, Transparence et Mise en Conformité de Bonne Foi

Nous accordons la plus grande importance au respect des licences, des crédits d'auteurs et de la propriété intellectuelle de chaque projet, entreprise ou contributeur cité ci-dessus.

Si vous êtes titulaire de droits, mainteneur d'une bibliothèque ou utilisateur et estimez qu'une mention légale, un lien d'attribution, une exclusion ou un fichier inclus dans ce dépôt nécessite une précision, une correction ou un retrait :
- **Contactez-nous directement via notre site web : [https://louisconumerique.ca](https://louisconumerique.ca)** (ou ouvrez une issue sur [github.com/AnalogShade/NovaDAW](https://github.com/AnalogShade/NovaDAW)).

Toute éventuelle omission ou imprécision serait strictement involontaire ; nous nous engageons à examiner rapidement toute demande et à effectuer les ajustements nécessaires dans les plus brefs délais.
