# NovaDAW 🌌 — Station de Travail Audio Numérique (DAW)

**NovaDAW** est une station de travail audio numérique (DAW) open-source et modulaire conçue en **Python** et **PySide6 (Qt 6)**, inspirée de l'ergonomie de studio professionnelle. Elle combine un moteur audio temps réel **FP32 (32-bit Float)**, une console de mixage, des instruments et effets internes (`NovaSynth`, `Nova Drums`, `NovaEQ`, `NovaCompressor`), l'hébergement isolé de plugins externes **VST3 / VST2 (32 & 64 bits)**, ainsi qu'un serveur **Model Context Protocol (MCP)** pour composer et mixer en collaboration avec un agent IA.

> [!NOTE]
> **Logiciel Open-Source sous licence MIT** :
> Le code source de NovaDAW est distribué librement sous la [**licence MIT**](LICENSE) : vous pouvez l'utiliser, le copier, le modifier et le redistribuer gratuitement (à des fins personnelles ou commerciales), sous réserve de conserver la mention de copyright et le texte de la licence MIT.
> Les bibliothèques tierces ainsi que les échantillons d'usine du kit de batterie par défaut de *Nova Drums* conservent leurs licences respectives, détaillées dans [`THIRD_PARTY_LICENSES.md`](THIRD_PARTY_LICENSES.md).

---

## ⚠️ Précautions d'Utilisation et Sécurité Auditive

Avant de lancer NovaDAW, gardez ces réflexes simples à l'esprit :

1. **Protégez vos oreilles et votre matériel** : Comme tout logiciel audio temps réel, un gain excessif, un larsen ou un plugin VST tiers instable peut produire des pics sonores brutaux. **Baissez toujours le volume physique de votre interface audio, casque ou enceintes avant de lancer la lecture, un enregistrement ou un test audio.**
2. **Sauvegardez vos projets** : Faites régulièrement des copies de sauvegarde de vos fichiers `.ndaw` et de vos fichiers audio sources.
3. **Plugins VST tiers & Automatisation IA (MCP)** : Les plugins externes (`.vst3` / `.dll`) et les commandes envoyées par un agent IA via MCP s'exécutent sous votre supervision.
4. **Logiciel fourni « TEL QUEL » (« AS IS »)** : Conformément à la [licence MIT](LICENSE), le logiciel est fourni sans garantie d'aucune sorte.

---

## 🚀 Installation Facile (Même sans Connaissances en Programmation)

> **Système recommandé** : **Windows 10 ou Windows 11 (64 bits)** pour le support complet ASIO / WASAPI et des bridges VST2 / VST3 *(l'interface et les tests de base restent compatibles Python multiplateforme)*.

Suivez ces **3 étapes simples** pour démarrer NovaDAW en quelques minutes :

### Étape 1 — Installer Python (à faire une seule fois)

1. Téléchargez **Python** (version **3.10 à 3.13** recommandée) sur [python.org/downloads](https://www.python.org/downloads/).
2. Lancez l'installateur et **cochez impérativement la case `Add python.exe to PATH`** tout en bas de la première fenêtre avant de cliquer sur **Install Now**.

*(Astuce Windows : si vous préférez utiliser le terminal, vous pouvez aussi installer Python via `winget install Python.Python.3.12`.)*

---

### Étape 2 — Récupérer NovaDAW depuis GitHub

Choisissez l'une des deux méthodes ci-dessous :

#### Option A — Méthode la plus simple (sans Git, en fichier ZIP)
Idéal si vous n'êtes pas familier avec GitHub :
1. Tout en haut de cette page GitHub, cliquez sur le bouton vert **`<> Code`**.
2. Cliquez sur **`Download ZIP`**.
3. Une fois le téléchargement terminé, faites un **clic droit** sur le fichier `NovaDAW-main.zip` → **Extraire tout...** et placez le dossier à un endroit simple (par exemple `C:\NovaDAW` ou `Documents\NovaDAW`).

#### Option B — Avec Git (`git clone`, pratique pour mettre à jour facilement)
Si vous souhaitez cloner le dépôt avec Git :
1. *(Si Git n'est pas installé sur votre PC)* : installez-le depuis [git-scm.com](https://git-scm.com/download/win) (ou dans un terminal : `winget install Git.Git`).
2. Ouvrez l'Explorateur Windows à l'emplacement où vous voulez placer le projet (ex. `Documents`), cliquez dans la barre d'adresse en haut, tapez `powershell` puis appuyez sur **Entrée**.
3. Copiez-collez ces deux commandes :
```powershell
git clone https://github.com/AnalogShade/NovaDAW.git
cd NovaDAW
```
*(Plus tard, pour récupérer les dernières mises à jour du projet, ouvrez simplement un terminal dans le dossier `NovaDAW` et tapez `git pull`.)*

---

### Étape 3 — Installer les composants requis et lancer NovaDAW

1. Ouvrez le dossier `NovaDAW` dans l'Explorateur Windows.
2. Cliquez dans la barre d'adresse du dossier, tapez `powershell` et appuyez sur **Entrée** *(une fenêtre noire ou bleue s'ouvre directement dans le bon dossier)*.
3. Copiez-collez les deux lignes suivantes et appuyez sur **Entrée** (patientez 1 à 2 minutes le temps que les bibliothèques se téléchargent) :

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

*(Optionnel — uniquement si vous voulez aussi utiliser l'assistant IA MCP, lancer les tests ou compiler un `.exe`)* :
```powershell
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

4. **Pour lancer NovaDAW** :
   - **Le plus simple** : retournez dans le dossier et **double-cliquez sur [`Lancer_NovaDAW.bat`](Lancer_NovaDAW.bat)** !
   - *(Ou depuis le terminal)* : `.venv\Scripts\python.exe main.py`
   - *(Optionnel — créer une icône sur le Bureau Windows)* : `.venv\Scripts\python.exe scripts\update_desktop_shortcut.py`

> [!TIP]
> **Pourquoi utiliser `.venv\Scripts\python.exe` ?**
> Cette commande fonctionne directement sous Windows sans déclencher de blocage PowerShell (`ExecutionPolicy`) lié à `Activate.ps1`, tout en gardant votre installation Python principale propre.

---

## 🎹 Premiers Pas en 2 Minutes (Sans Coder)

Une fois NovaDAW ouvert :
1. **Réglez votre carte son (`Ctrl + ,`)** : si vous n'entendez rien ou si vous avez une interface audio ASIO/WASAPI, ouvrez la configuration matériel avec **`Ctrl + ,`** (ou cliquez sur le bouton matériel en haut) et choisissez votre sortie audio.
2. **Jouez immédiatement avec votre clavier d'ordinateur (`Ctrl + K`)** : appuyez sur **`Ctrl + K`** pour ouvrir le clavier musical virtuel et testez les sons de **NovaSynth** avec les touches du clavier.
3. **Créez un motif MIDI** : sélectionnez la piste instrument, double-cliquez sur un bloc dans la timeline (ou créez-en un) pour ouvrir le **Piano Roll**, placez quelques notes à la souris, puis appuyez sur **Espace** pour écouter.
4. **Ouvrez le manuel complet intégré (`F1`)** : appuyez sur **`F1`** à tout moment dans NovaDAW pour consulter le guide pas à pas (*Mon premier morceau*, 23 chapitres illustrés et recherche intégrée, même sans connexion Internet).

---

## 🎛️ Fonctionnalités Principales

- **Moteur Audio FP32 & Cache ASIO-Guard** (`core/audio_engine.py`) : Traitement temps réel 32-bit float, lecture/enregistrement audio & MIDI, mode exclusif WASAPI, gestion ASIO/WASAPI/DirectSound/MME, latence aller-retour (RTL) et compensation d'enregistrement.
- **Console de Mixage Pro (`NovaMixer`)** : Tranches multipistes avec faders dB, panoramique, Solo/Mute/Rec, VU-mètres dynamiques L/R et bus Master (Mono, Dim -12 dB, rack d'effets Master avec A/B).
- **Instruments & Effets Internes** :
  - **NovaSynth** (`novadaw.synth`) : Synthétiseur polyphonique DSP 100 % synthétique (PolyBLEP, SuperSaw 7 voix, FM 2-opérateurs, double ADSR, filtre SVF multimode, LFO, Delay Ping-Pong, Reverb), empilement de couches illimité, routage sur 10 bus stéréo et 34 presets d'usine.
  - **Nova Drums** (`novadaw.drum_machine`) : Boîte à rythmes 16 pads avec échantillons FP32, mapping General MIDI, choke group charleston, accordage, decay et réverbération stéréo intégrée.
  - **NovaEQ** (`novadaw.equalizer`) : Égaliseur paramétrique IIR biquad 3 / 10 / 12 / 24 bandes avec courbe interactive 20 Hz – 20 kHz.
  - **NovaCompressor** (`novadaw.compressor`) : Compresseur dynamique de studio avec Soft-Knee et VU-mètre de réduction de gain (GR) en direct.
- **Hébergement Isolé VST3 & VST2 (32/64 bits)** (`core/vst_host.py`, `core/plugin_manager.py`) : Exécution dans des processus isolés (un crash d'un plugin externe ne ferme pas NovaDAW), scanner incrémental et Rack de projet (**F11**).
- **Importateur Audio Universel & Éditeur MIDI** : Import WAV, MP3, FLAC, OGG, AIFF, M4A, AAC, etc. (via `soundfile` et `PySide6.QtMultimedia`) ; Piano Roll 128 notes avec sélection multiple, quantification (`Q`), transposition et annulation/rétablissement (`Ctrl+Z` / `Ctrl+Y`).
- **Sauvegarde Atomique (`.ndaw`)** : Remplacement atomique des fichiers pour préserver la version précédente en cas d'erreur d'écriture.
- **Orchestration par Agent IA (MCP)** (`core/mcp/server.py`) : Serveur FastMCP communiquant par pont TCP IPC non-bloquant (port `8765`) pour créer des pistes, programmer du MIDI, configurer les instruments/effets et piloter le transport en direct.

---

## ⌨️ Raccourcis Clavier Principaux

- **Espace** : Lecture / Pause
- **0 (Pavé numérique)** : Stop (retour au début ou au repère de boucle)
- **L** : Activer / Désactiver la boucle
- **F1** : Ouvrir le manuel intégré hors ligne
- **F4** : Gestionnaire de plugins et dossiers VST
- **F5** : Afficher / Masquer la Console de Mixage
- **F11** : Rack de plugins flottant
- **Ctrl + T** : Ajouter une nouvelle piste
- **Ctrl + I** : Importer un fichier audio
- **Ctrl + K** : Ouvrir le clavier musical virtuel
- **Ctrl + ,** : Configuration Matériel & Audio (ASIO/WASAPI, GPU, MCP)
- **Ctrl + M** : Inspecter la Piste Master et ses effets
- **Ctrl + S / O / E** : Enregistrer (`.ndaw`) / Ouvrir / Exporter en WAV

### Utiliser un plugin sur une piste
1. Ouvrir **Plugins → Ajouter un plugin au projet…** (**F11** pour le rack flottant).
2. Créer une piste MIDI (**Ctrl+T**) et sélectionner son **Instrument de sortie** dans l'inspecteur.
3. Ajouter des effets via **Pile de plugins & effets → Ajouter un Plugin** sur une piste ou sur le Master.
4. Enregistrer votre projet (**Ctrl+S**). Les plugins externes et banques de sons doivent rester installés sur la machine.

---

## 🛠️ Compilation (Pour Développeurs — Générer un Exécutable Windows)

Si vous souhaitez empaqueter NovaDAW en exécutable autonome Windows :

```powershell
.venv\Scripts\python.exe -m pip install pyinstaller
.venv\Scripts\pyinstaller --noconfirm --windowed --icon="assets/nova_icon.ico" --name="NovaDAW" ^
  --add-data="assets;assets" ^
  --add-data="presets;presets" ^
  --add-data="core/bin;core/bin" ^
  --add-data="plugins/drum_machine/samples;plugins/drum_machine/samples" ^
  main.py
```
*(En PowerShell, remplacez `^` par le backtick `` ` `` ou écrivez la commande sur une seule ligne.)*
L'exécutable généré se trouvera dans `dist/NovaDAW/NovaDAW.exe`.

> **Note sur les bridges natifs VST3 / VST2 (`vst3_bridge_x64.exe`, `vst2_bridge_x86.exe`)** :
> Les binaires compilés `core/bin/vst3_bridge_x64.exe` (basé sur les en-têtes officiels Steinberg VST 3.8+ sous licence **MIT**) et `core/bin/vst2_bridge_x86.exe` sont déjà fournis dans le dépôt. Si vous modifiez leurs sources C/C++ et souhaitez les recompiler avec Visual Studio Build Tools :
> ```bat
> :: Bridge VST3 64-bit (x64 Native Tools Command Prompt)
> cl /O2 /EHsc /MT /std:c++17 /Icore\bin\vst3_sdk core\bin\vst3_bridge_x64.cpp user32.lib ole32.lib /Fe:core\bin\vst3_bridge_x64.exe
>
> :: Bridge VST2 32-bit (x86 Native Tools Command Prompt)
> cl /O2 /MT core\bin\vst2_bridge_x86.c user32.lib gdi32.lib /Fe:core\bin\vst2_bridge_x86.exe
> ```

> [!NOTE]
> Si vous redistribuez une version compilée (exécutable autonome), veillez à respecter la licence MIT de NovaDAW ainsi que les obligations des bibliothèques tierces listées dans [`THIRD_PARTY_LICENSES.md`](THIRD_PARTY_LICENSES.md) (notamment la conservation des bibliothèques dynamiques/DLL et mentions de licence pour PySide6 sous LGPLv3 et `libsndfile` sous LGPLv2.1+). Aucune bibliothèque GPLv3 n'est utilisée par NovaDAW.

---

## 🧪 Tester le Logiciel (Suite Automatisée)

La suite de tests automatisés utilise `pytest` avec isolation matérielle (`NOVADAW_SILENT_TESTS=1`) afin de **garantir un silence absolu** et de ne jamais envoyer de signal vers vos haut-parleurs pendant les tests.

```powershell
# Installer les outils de test (si ce n'est pas déjà fait)
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt

# Lancer la suite complète de tests (mode silencieux)
$env:NOVADAW_SILENT_TESTS="1"
$env:QT_QPA_PLATFORM="offscreen"
.venv\Scripts\python.exe -m pytest -q
```

Pour exécuter un module de test en particulier (par exemple le manuel, le moteur synthé ou le MCP) :
```powershell
.venv\Scripts\python.exe -m pytest tests/test_help_manual.py -q
.venv\Scripts\python.exe -m pytest tests/test_synth_plugin.py -q
.venv\Scripts\python.exe -m pytest tests/test_mcp_integration.py -q
```

*(Note : en cas d'exécution sur un système sans affichage graphique ou si certains tests Qt interactifs entrent en conflit dans un même processus, exécutez les fichiers `tests/test_*.py` individuellement comme documenté dans [`doc/testing_and_qa.md`](doc/testing_and_qa.md).)*

---

## 🤖 Connexion avec un Client MCP (Antigravity, Claude Desktop, etc.)

Pour connecter un client compatible MCP à NovaDAW, lancez d'abord NovaDAW, puis ajoutez l'entrée suivante dans la configuration MCP de votre client en remplaçant `<CHEMIN_VERS_NOVADAW>` par le chemin absolu de votre dossier NovaDAW :

```json
{
  "mcpServers": {
    "novadaw": {
      "command": "<CHEMIN_VERS_NOVADAW>/.venv/Scripts/python.exe",
      "args": ["<CHEMIN_VERS_NOVADAW>/core/mcp/server.py"]
    }
  }
}
```
*(Si vous n'utilisez pas de `.venv` et avez installé `requirements-dev.txt` dans votre Python global, vous pouvez remplacer `"command"` par `"python"`.)*

---

## 📚 Documentation Technique Complète (`doc/`)

Pour comprendre l'architecture interne, les contrats d'ingénierie ou les limites vérifiées du logiciel :
- [**Index Complet des Spécifications (`doc/README.md`)**](doc/README.md)
- [**Audit et Validation du Manuel (`doc/help_audit.md`)**](doc/help_audit.md)
- [**Stratégie de Tests & QA (`doc/testing_and_qa.md`)**](doc/testing_and_qa.md)
- [**Guide d'Architecture pour Agents (`AGENT.md`)**](AGENT.md)

---

## 📜 Licence, Crédits Tiers & Contact Conformité

Copyright © 2026 [louisconumerique.ca](https://louisconumerique.ca) (AnalogShade).

- **Code source de NovaDAW** : distribué sous la **licence MIT** (voir [`LICENSE`](LICENSE)). Vous êtes libre d'**utiliser, copier, modifier, fusionner, publier, distribuer, sous-licencier et/ou vendre** des copies du logiciel, à des fins personnelles ou commerciales, à condition d'inclure l'avis de copyright et la notice de permission MIT dans toutes les copies ou parties substantielles du logiciel. Le logiciel est fourni **« TEL QUEL »**, sans garantie d'aucune sorte.
- **Bibliothèques & SDK tiers (dépendances directes et transitives)** : l'ensemble des projets utilisés par NovaDAW (`PySide6`, `NumPy`, `SciPy`, `sounddevice`, `soundfile`, `Steinberg VST3 SDK`, `MCP`, etc.) est crédité avec liens et licences dans [`THIRD_PARTY_LICENSES.md`](THIRD_PARTY_LICENSES.md).
- **Échantillons d'usine du plugin *Nova Drums* (`default_kit/*.wav`)** : uniquement ces fichiers audio de batterie inclus dans `assets/drum_kits/default_kit/` et `plugins/drum_machine/samples/default_kit/` ont été générés hors ligne avec **Stable Audio 3 Medium** ([github.com/Stability-AI/stable-audio-3](https://github.com/Stability-AI/stable-audio-3)) — *Powered by Stability AI*. Ils sont exclus de la licence MIT et soumis à la [**Stability AI Community License**](https://stability.ai/license) (usage commercial gratuit sous le seuil de **1 000 000 USD** de revenus annuels bruts ; licence *Enterprise* requise au-delà ; interdiction d'entraîner des modèles fondationnels concurrents).
- **Contact & conformité de bonne foi** : si vous constatez une erreur d'attribution ou pensez qu'une licence pourrait avoir été enfreinte de manière involontaire, merci de nous contacter via [**louisconumerique.ca**](https://louisconumerique.ca) afin que nous puissions corriger la situation rapidement.
