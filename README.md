# NovaDAW 🌌 — Station de Travail Audio Numérique (DAW)

**NovaDAW** est une station de travail audio numérique (DAW) modulaire conçue en **Python** et **PySide6 (Qt 6)**, inspirée de l'ergonomie de studio professionnelle. Elle combine un moteur audio temps réel **FP32 (32-bit Float)**, une console de mixage, des instruments et effets internes (`NovaSynth`, `Nova Drums`, `NovaEQ`, `NovaCompressor`), l'hébergement isolé de plugins externes **VST3 / VST2 (32 & 64 bits)**, ainsi qu'un serveur **Model Context Protocol (MCP)** pour composer et mixer en collaboration avec un agent IA.

> [!IMPORTANT]
> **Statut du dépôt — Logiciel *Source-Available* (Non Open-Source)** :
> NovaDAW est mis à disposition publiquement pour être téléchargé, testé et utilisé **gratuitement** (y compris dans un cadre **professionnel** pour produire vos œuvres musicales), **mais il ne s'agit pas d'un logiciel open-source**. Il est strictement interdit de vendre, revendre, louer ou vous approprier le logiciel (code source ou exécutable) sans accord écrit préalable. Voir la section [Licence](#-licence-et-droits-dutilisation) et [`LICENSE`](LICENSE).

---

## ⚠️ Avertissement et Mises en Garde d'Utilisation

Avant d'installer, compiler ou utiliser NovaDAW, veuillez lire attentivement les points suivants :

1. **Logiciel fourni « TEL QUEL » (« AS IS »)** : NovaDAW est distribué sans aucune garantie de fonctionnement continu, d'absence de bugs ou de compatibilité avec votre matériel ou vos plugins tiers.
2. **Sécurité auditive et matérielle** : Comme tout logiciel audio temps réel, une mauvaise configuration, un gain excessif, un effet auto-oscillant, un larsen ou un plugin tiers instable peut générer des **pics sonores brutaux**. **Baissez toujours le volume physique de votre interface audio, casque ou enceintes avant de lancer la lecture, un enregistrement ou un test audio.**
3. **Sauvegarde de vos projets** : Effectuez régulièrement des copies de sauvegarde de vos fichiers `.ndaw` et de vos fichiers audio sources.
4. **Plugins VST tiers & Automatisation IA (MCP)** : Les plugins externes (`.vst3` / `.dll`) et les actions déclenchées par des agents IA via MCP s'exécutent sous votre entière supervision et responsabilité.
5. **Exclusion totale de responsabilité** : En utilisant ce logiciel, vous acceptez que l'auteur ne puisse en aucun cas être tenu responsable des **pertes financières ou commerciales** (perte de projets, de données, de revenus ou interruption d'activité), des **dommages matériels**, ni des **dommages physiques (y compris auditifs) ou psychologiques** pouvant résulter de l'utilisation ou d'une mauvaise utilisation du logiciel. Consultez [`LICENSE`](LICENSE) pour les conditions juridiques complètes.

---

## 🚀 Installation et Lancement (Prise en Charge Rapide)

### Prérequis
- **Système recommandé** : Windows 10 / Windows 11 (64 bits) pour le support complet ASIO / WASAPI et des bridges VST2/VST3 *(compatible Python multiplateforme pour l'édition/tests de base)*.
- **Python** : Python **3.10 à 3.13** installé avec `pip`.

### 1. Télécharger et installer les dépendances

```powershell
git clone https://github.com/AnalogShade/NovaDAW.git
cd NovaDAW

# Créer et activer un environnement virtuel (recommandé)
python -m venv .venv
.venv\Scripts\Activate.ps1

# Installer les dépendances nécessaires pour lancer NovaDAW
pip install -r requirements.txt
```

*(Optionnel)* Pour activer également le serveur IA **MCP**, les outils de **tests** (`pytest`) et la **compilation** (`pyinstaller`) :
```powershell
pip install -r requirements-dev.txt
```

### 2. Lancer NovaDAW

```powershell
python main.py
```
Sous Windows, vous pouvez aussi double-cliquer sur [`Lancer_NovaDAW.bat`](Lancer_NovaDAW.bat) (il détecte automatiquement `.venv` ou votre installation Python) ou générer un raccourci Bureau avec :
```powershell
python scripts/update_desktop_shortcut.py
```

---

## 🛠️ Compilation (Générer un Exécutable Windows)

Si vous souhaitez compiler NovaDAW en exécutable autonome pour votre usage personnel ou interne :

```powershell
pip install pyinstaller
pyinstaller --noconfirm --windowed --icon="assets/nova_icon.ico" --name="NovaDAW" ^
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
> Rappel : Toute compilation ou copie est réservée à votre usage autorisé selon [`LICENSE`](LICENSE) et doit respecter les licences des bibliothèques tierces décrites dans [`THIRD_PARTY_LICENSES.md`](THIRD_PARTY_LICENSES.md) (notamment PySide6 sous LGPLv3 et Steinberg VST3 SDK sous MIT). Aucune bibliothèque GPLv3 n'est utilisée par NovaDAW.

---

## 🧪 Tester le Logiciel (Suite Automatisée)

La suite de tests automatisés utilise `pytest` avec isolation matérielle (`NOVADAW_SILENT_TESTS=1`) afin de **garantir un silence absolu** et de ne jamais envoyer de signal vers vos haut-parleurs pendant les tests.

```powershell
# Installer les outils de test (si ce n'est pas déjà fait)
pip install -r requirements-dev.txt

# Lancer la suite complète de tests (mode silencieux)
$env:NOVADAW_SILENT_TESTS="1"
$env:QT_QPA_PLATFORM="offscreen"
python -m pytest -q
```

Pour exécuter un module de test en particulier (par exemple le manuel, le moteur synthé ou le MCP) :
```powershell
python -m pytest tests/test_help_manual.py -q
python -m pytest tests/test_synth_plugin.py -q
python -m pytest tests/test_mcp_integration.py -q
```

*(Note : en cas d'exécution sur un système sans affichage graphique ou si certains tests Qt interactifs entrent en conflit dans un même processus, exécutez les fichiers `tests/test_*.py` individuellement comme documenté dans [`doc/testing_and_qa.md`](doc/testing_and_qa.md).)*

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
- **Importateur Audio Universel & Éditeur MIDI** : Import WAV, MP3, FLAC, OGG, AIFF, M4A, AAC, etc. ; Piano Roll 128 notes avec sélection multiple, quantification (`Q`), transposition et annulation/rétablissement (`Ctrl+Z` / `Ctrl+Y`).
- **Sauvegarde Atomique (`.ndaw`)** : Remplacement atomique des fichiers pour préserver la version précédente en cas d'erreur d'écriture.
- **Orchestration par Agent IA (MCP)** (`core/mcp/server.py`) : Serveur FastMCP communiquant par pont TCP IPC non-bloquant (port `8765`) pour créer des pistes, programmer du MIDI, configurer les instruments/effets et piloter le transport en direct.

---

## 🎹 Utilisation Rapide & Raccourcis Essentiels

### Manuel intégré hors ligne
Ouvrez **Aide → Documentation** ou appuyez sur **F1** (ou le bouton **Aide F1** en haut à droite) directement dans NovaDAW pour accéder au manuel complet (parcours *Mon premier morceau*, 23 chapitres, recherche insensible aux accents et liste complète des raccourcis).

### Utiliser un plugin sur une piste
1. Ouvrir **Plugins → Ajouter un plugin au projet…** (**F11** pour le rack flottant).
2. Créer une piste MIDI (**Ctrl+T**) et sélectionner son **Instrument de sortie** dans l'inspecteur.
3. Ajouter des effets via **Pile de plugins & effets → Ajouter un Plugin** sur une piste ou sur le Master.
4. Enregistrer votre projet (**Ctrl+S**). Les plugins externes et banques de sons doivent rester installés sur la machine.

### Raccourcis clavier principaux
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
- **Ctrl + P** : Configuration Matériel & Audio (ASIO/WASAPI, GPU, MCP)
- **Ctrl + M** : Inspecter la Piste Master et ses effets
- **Ctrl + S / O / E** : Enregistrer (`.ndaw`) / Ouvrir / Exporter en WAV

---

## 🤖 Connexion avec un Client MCP (Antigravity, Claude Desktop, etc.)

Pour connecter un client compatible MCP à NovaDAW, lancez d'abord NovaDAW, puis ajoutez l'entrée suivante dans la configuration MCP de votre client en remplaçant `<CHEMIN_VERS_NOVADAW>` par le chemin absolu de votre dossier NovaDAW :

```json
{
  "mcpServers": {
    "novadaw": {
      "command": "python",
      "args": ["<CHEMIN_VERS_NOVADAW>/core/mcp/server.py"]
    }
  }
}
```

---

## 📚 Documentation Technique Complète (`doc/`)

Pour comprendre l'architecture interne, les contrats d'ingénierie ou les limites vérifiées du logiciel :
- [**Index Complet des Spécifications (`doc/README.md`)**](doc/README.md)
- [**Audit et Validation du Manuel (`doc/help_audit.md`)**](doc/help_audit.md)
- [**Stratégie de Tests & QA (`doc/testing_and_qa.md`)**](doc/testing_and_qa.md)
- [**Guide d'Architecture pour Agents (`AGENT.md`)**](AGENT.md)

---

## 📜 Licence et Droits d'Utilisation

Copyright © 2026 louisconumerique.ca (AnalogShade). Tous droits réservés.

Ce projet est distribué sous la **Licence Propriétaire Source-Available NovaDAW** (voir le fichier complet [`LICENSE`](LICENSE)) :

- ✅ **Vous pouvez** :
  - Télécharger, installer, compiler et utiliser NovaDAW **gratuitement**, tel qu'il est fourni.
  - Utiliser NovaDAW dans un cadre **personnel, éducatif ou professionnel/commercial** (par exemple pour créer, enregistrer, mixer et commercialiser vos propres morceaux et productions audio).
  - Consulter et modifier le code source pour votre usage privé ou interne.
- ❌ **Vous ne pouvez PAS** (sans accord écrit préalable de l'auteur) :
  - **Vendre, revendre, louer, sous-licencier ou commercialiser** NovaDAW (code source ou binaire compilé), en tout ou en partie, tel quel ou modifié.
  - **Vous approprier le projet**, le redistribuer sous un autre nom ou retirer les mentions d'auteur, de licence et d'avertissement.
  - Intégrer le code source de NovaDAW dans un produit commercial tiers.

Les bibliothèques tierces utilisées par NovaDAW restent soumises à leurs licences respectives, détaillées dans [`THIRD_PARTY_LICENSES.md`](THIRD_PARTY_LICENSES.md).
