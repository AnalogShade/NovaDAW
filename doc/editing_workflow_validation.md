# Édition MIDI et sauvegarde — validation du 29 octobre 2026

Branche : `codex/editing-workflow-improvements`.
État initial : dépôt propre sur `main` ; branche dédiée créée avant toute modification.

## Changements livrés

- Piano roll sur les 128 hauteurs MIDI, sélection multiple par Ctrl+clic et rectangle Ctrl+glisser.
- Déplacement et redimensionnement de groupes, décalage au clavier, transposition par demi-ton ou octave, quantification des débuts et réglage de vélocité.
- Copier/couper/coller des notes à la tête de lecture relative au clip, duplication consécutive, suppression par identité : deux notes identiques restent indépendantes.
- Historique local de 100 opérations avec annuler/rétablir, un glisser par opération, effacement complet réversible. Les changements externes invalident l'historique et annulent un glisser en cours.
- Extension du clip pour rendre les notes ajoutées ou déplacées au-delà de sa fin audibles. Les longueurs et les notes restent dans le format `.ndaw` existant.
- Rafraîchissement de la tête de lecture dans les coordonnées locales du clip ; relâchement des notes de préécoute sur la piste d'origine lors d'un changement de piste ou du masquage de l'éditeur.
- Invalidation du cache audio de la piste réellement éditée, même si la sélection de piste de la fenêtre diffère du clip ouvert.
- Actions MIDI/MCP : une cible explicite inconnue échoue, le filtre de piste est respecté, les lots de notes sont validés avant toute modification et les valeurs temporelles non finies sont rejetées. Les mutations actualisent l'interface et le cache audio de la bonne piste.
- Sauvegarde atomique : écriture temporaire dans le dossier de destination, flush/fsync puis remplacement. La version enregistrée reste intacte si la nouvelle sauvegarde échoue.

## Tests réellement exécutés

Environnement : Python 3.13, PySide6, Windows ; `NOVADAW_SILENT_TESTS=1` et `QT_QPA_PLATFORM=offscreen`.
Aucun flux audio physique pendant les tests.

- Référence initiale : `test_audio_editing_and_grid.py` et `test_zoom_and_autoscroll.py`, **18 tests réussis** avant modification.
- Suite complète : les **34 modules** `tests/test_*.py` ont été lancés chacun dans un processus distinct. **273 tests réussis, aucun échec ni test ignoré** à cette étape.
- Après les dernières vérifications de navigation et d'interface, les trois nouveaux modules ont été relancés ensemble : **37 tests réussis**, dont deux cas ajoutés après la suite complète. Bilan : **275 tests distincts réussis**, répartis dans 34 modules.
- Les six tests MCP existants ont également été relancés avec les nouveaux tests ciblés après les corrections des actions.
- `git diff --check` : aucune erreur d'espacement.
- Rendu Qt du piano roll à 1200 × 500 avec le thème du projet et polices Segoe UI chargées explicitement pour le rendu offscreen ; inspection de l'agencement, des notes et des nouvelles commandes.

Les processus séparés suivent la recommandation de `doc/testing_and_qa.md`, qui documente un arrêt natif Qt préexistant lors de certains lancements de toute la suite dans un seul processus.

## Couverture des nouveaux tests

| Module | Tests | Comportements vérifiés |
| --- | ---: | --- |
| `test_piano_roll_editing.py` | 16 | Entrées souris/clavier Qt, sélection, quantification, groupe, duplication, collage, vélocité, historique, frontières MIDI/temporelles, notes identiques, extension du clip, changements externes, préécoute, rafraîchissement du curseur, raccourcis de fenêtre, dirty state et cache ciblé. |
| `test_midi_edit_safety.py` | 17 | Cibles inconnues et filtres de piste, invalidation après les six actions d'édition/création, lots invalides atomiques, nombres non finis, extension après quantification. |
| `test_atomic_save.py` | 4 | Aller-retour `.ndaw`, noms accentués, remplacement réussi, erreurs de sérialisation, de synchronisation et de remplacement ; conservation du fichier initial et nettoyage des temporaires. |

## Limites et suites recommandées

- L'historique est propre au clip MIDI ouvert, limité à 100 opérations et non conservé lors d'un changement de clip, d'une fermeture ou d'une édition externe. Il ne constitue pas encore un historique global de l'arrangement.
- Le presse-papiers de notes est interne au piano roll ; il ne transporte pas de fichier MIDI vers d'autres logiciels.
- Les quantifications et vélocités s'appliquent à la sélection ; utiliser Ctrl+A pour traiter tout le clip. Les durées ne sont pas quantifiées.
- La sauvegarde atomique ne remplace ni les sauvegardes versionnées ni un système de récupération automatique. Une interruption brutale peut laisser un temporaire ; aucun nettoyage des fichiers utilisateur n'est effectué.
- Les validations audio sont silencieuses et logicielles : aucune écoute sur carte son, mesure ASIO réelle ou qualification supplémentaire de plugins tiers n'est revendiquée.
- Prochaines priorités : historique global de l'arrangement, sélection et édition de plusieurs clips, sauvegardes de récupération versionnées, puis contrôles graphiques de vélocité et édition MIDI avancée.

Aucun déploiement, changement de clé ou d'identifiant, modification de `settings.json` par l'implémentation, ni modification du schéma de projet.
