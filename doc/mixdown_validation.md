# Diagnostic et remplacement du mixdown — Sous la pierre noire

14 octobre 2026. La composition MIDI, les sons, les volumes et le tempo sont conservés. Le premier export avait bien été déclenché par l'outil MCP `novadaw_export_wav`.

## Défaut constaté

Le premier WAV n'était pas saturé numériquement : crête de -16,98 dBFS. En revanche, ses plus grands sauts de signal apparaissaient exactement aux frontières des blocs de 4096 échantillons, ce qui caractérise un problème de continuité du rendu. Le contrôle initial, limité aux niveaux et aux valeurs non finies, n'était pas suffisant pour détecter ce défaut.

Le chemin de mixdown réutilisait `_render_track_slice`, le cache de lecture et les instances DSP des instruments en cours de lecture. Le premier export avait été lancé pendant la lecture : les mêmes filtres, délais et réverbérations pouvaient traiter alternativement des positions différentes. Le cache pouvait aussi devenir disponible en cours de rendu et remplacer un état live différent. Enfin, le cache appliquait la boucle de transport à la queue demandée en fin d'export.

Le rendu de contrôle dans un processus neuf, sans lecture ni cache, a éliminé les gros sauts du début. Cela établit la responsabilité de ce chemin partagé; les contributions respectives du changement de cache et de l'entrelacement des états DSP ne sont pas isolées individuellement. La latence matérielle n'est pas en cause dans ce rendu hors ligne : aucun délai de livraison à la carte son n'est à respecter.

## Correction ciblée

- L'export travaille avec une copie des pistes et des plugins natifs, dans un état DSP initial propre.
- Le cache de lecture est désactivé pour le mixdown, et la timeline d'export est linéaire même si la boucle de transport est activée.
- Les échantillons des clips audio sont indexés par arrondi afin d'éviter les doublons dus à des conversions telles que 4095,999999 au lieu de 4096.
- Le WAV est écrit en PCM 24 bits, au lieu du PCM 16 bits implicite.
- Le délai IPC d'export passe à 300 s, sans changer celui des commandes rapides. L'ancien délai de 5 s expliquait des réponses MCP en erreur alors que le fichier finissait par être créé; ce délai n'explique pas à lui seul les grésillements enregistrés.

Les VST tiers et l'export asynchrone avec progression ne sont pas refondus par ce changement. Les copies de plugins concernent les plugins natifs. La session testée utilise uniquement NovaSynth.

## Nouveau rendu réel

Nova a été sauvegardé, fermé normalement et relancé sur la même session. Le nouveau fichier a été généré par le vrai serveur MCP de Nova, avec le SDK MCP standard et le délai actualisé, via l'action `novadaw_export_wav`, `end_bar=33`. L'action a retourné un succès. Aucun synthétiseur extérieur ni post-traitement correcteur de clics n'a remplacé le rendu de Nova.

| Mesure | Ancien WAV | Nouveau WAV |
|---|---:|---:|
| Durée | 141,43 s | 141,43 s |
| Format | PCM 16 bits | PCM 24 bits |
| Fréquence / canaux | 44 100 Hz / 2 | 44 100 Hz / 2 |
| Crête | -16,98 dBFS | -17,14 dBFS |
| Échantillons saturés ou non finis | 0 | 0 |
| Sauts > 0,02 aux frontières de blocs, morceau entier | 261 | 0 |
| Saut maximal aux frontières dans les 8 premières secondes | 0,06464 | 0,000708 |

Le seuil 0,02 est un indicateur comparatif, pas une preuve universelle d'absence de tout défaut audible : des transitoires musicaux légitimes peuvent dépasser un seuil de variation. Le nouveau rendu correspond au rendu de contrôle isolé à la quantification PCM 16 bits près (écart maximal inférieur à 0,0000304). Pas d'écoute subjective par l'agent.

## Fichiers remplacés

- WAV maître dans le dossier Nova recordings et dans ce dossier de session du jeu.
- Copie explicite : `Relic - Sous la pierre noire - mixdown corrige.wav`.
- OGG du jeu : `godot/assets/audio/music/cave_sous_la_pierre_noire.ogg`, dérivé du nouveau WAV avec les mêmes paramètres de gain (+10 dB), qualité Vorbis 5 et fondus que la version antérieure. Crête décodée : -7,14 dBFS environ.
- Les fichiers précédents sont conservés dans `mixdown avant correction`.

La session sauvegardée a été comparée au projet original : les données des cinq pistes (notes et réglages inclus) et le BPM sont identiques.

## Vérifications

216 tests passent sur 28 modules exécutés séparément, dont 6 nouveaux cas de test : export identique avec cache froid/chaud et DSP déjà utilisé, préservation du transport et du DSP live, export linéaire malgré boucle, volume/panoramique et mixeur natif, délais MCP par défaut et explicites. Le banc Qt global reste isolé module par module pour le défaut préexistant documenté lors du correctif précédent.

Les mesures brutes sont dans `Mesures mixdown corrige.json`. Le script de musique du jeu n'a pas été modifié pour ce remplacement; seul l'asset audio change.
