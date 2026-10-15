# Validation — démarrage audio progressif

14 octobre 2026. État initial sauvegardé et poussé sur origin/main : `3f1b250`. Correctif sur `codex/audio-startup-cache`.

## Changement ciblé

Seul le code applicatif de `core/audio_engine.py` change. Aucun algorithme DSP de NovaSynth, format .ndaw, paramètre matériel, contrôle de volume ou chaîne d'effets n'est modifié. Les fichiers supplémentaires sont des tests, un banc de mesure et la documentation.

Le pré-rendu utilise un seul worker et alterne les instruments natifs par blocs de 4096 échantillons. Chaque portion terminée devient immédiatement lisible, au lieu d'attendre une piste entière. Les réglages et notes du rendu de fond sont copiés pour préserver l'instrument en direct. Les anciennes tâches sont annulées par génération, les résultats obsolètes ne sont pas publiés, et le préfixe d'une piste inchangée ne régresse pas lors d'un redémarrage du travail. Une piste supprimée ne peut pas revenir dans le cache.

Les inserts, le Master et les pistes armées conservent leur chemin temps réel. Le chemin existant des instruments VST externes est conservé : ils ne sont pas remplacés par un cache natif silencieux. Les pistes mixtes audio/MIDI ne sont publiées qu'après ajout de leur contenu audio.

## Comparaison reproductible

Commande utilisée, avec Nova ouvert mais arrêté et sans autre suite de tests concurrente :

```powershell
python scripts/benchmark_audio_startup.py --project "recordings/Relic - Sous la pierre noire.ndaw" --baseline-ref 3f1b250 --output scratch/audio-startup-comparison-final.json
```

Même machine et même session (5 NovaSynth, 56 BPM), 44 100 Hz, blocs de 256 échantillons, 516 callbacks cadencés sur environ trois secondes au début de la chanson. Le délai disponible par bloc est de 5,80 ms. Le mode `NOVADAW_SILENT_TESTS=1` évite toute ouverture de sortie audio physique.

| Mesure | Avant (`3f1b250`) | Après |
|---|---:|---:|
| Premières 4096 trames disponibles pour toutes les pistes | pas disponibles pendant les 3 s observées | 11,93 ms |
| Cache complet du morceau | 22,92 s | 23,16 s |
| Callback médian | 1,812 ms | 0,284 ms |
| Callback au 95e percentile | 2,159 ms | 0,442 ms |
| Callback maximal | 2,599 ms | 1,918 ms |
| Callbacks dépassant 5,80 ms | 0 / 516 | 0 / 516 |

Le callback médian est environ 6,4 fois plus court sur ce démarrage. La durée totale de calcul est sensiblement identique : le gain vient de la disponibilité progressive et de la réduction du travail en direct, pas d'un calcul intégral devenu instantané.

Les premières mesures précédentes de la section dense (140 dépassements sur 240 blocs pendant le précalcul) utilisaient une autre position et un autre protocole. Elles ne doivent pas être comparées directement à cette série de démarrage. Ce tableau ne démontre pas des coupures physiques supprimées : aucune capture de la carte son n'a été effectuée.

## Non-régression

La validation exécute chaque fichier `tests/test_*.py` dans un processus séparé, avec `NOVADAW_SILENT_TESTS=1` et `QT_QPA_PLATFORM=offscreen`. Résultat : **210 tests réussis sur 27 modules**, dont 15 nouveaux tests du cache. Le module du cache a été réexécuté après ajout de son dernier test. Les résultats bruts sont dans `scratch/audio-startup-suite-final.json`. Les tests existants ne sont ni désactivés ni réécrits.

Les nouveaux tests couvrent : lecture partielle, refus de données non prêtes, copie des buffers, frontières de boucle, alternance des pistes, invalidation pendant un rendu, changement de projet, fermeture, regroupement des demandes, séparation des états DSP, routage et invalidation des dépendances, suppression, armement, contenu mixte audio/MIDI et conservation des préfixes déjà prêts. Un test compare les échantillons progressifs au vrai rendu NovaSynth en direct.

Le lancement global en un seul processus s'arrête dans Qt dans `test_track_delete_and_reorder.py`. Cet arrêt a été observé avant toute modification au moteur, sur l'état de référence. Les modules passent individuellement; le problème du cycle de vie QApplication n'a pas été mélangé à la correction audio.

## Limites et mise en service

Les mesures concernent cette session et cette machine. Une recherche très loin dans la timeline avant que son cache soit prêt, des projets plus lourds ou certains plugins externes peuvent encore nécessiter du rendu en direct. Le coût total et la mémoire du précalcul intégral n'ont pas été réduits. Le profil doit être répété avec les projets les plus lourds et avec la vraie sortie matérielle avant de promettre une absence générale de coupures.

L'application Nova déjà ouverte conserve l'ancien moteur Python. Sauvegarder puis relancer Nova pour charger la correction; aucun redémarrage forcé ni modification du morceau n'a été effectué.
