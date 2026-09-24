# Vérification du parcours plugins — 23 septembre 2026

## Résultat automatisé

`python -m pytest -q` : **94 tests réussis**, deux avertissements Qt de dépréciation déjà présents. Les tests isolent les périphériques audio physiques ; les mesures ci-dessous portent sur les buffers numériques, pas sur une écoute des haut-parleurs.

Les 13 nouveaux tests de `test_project_plugin_workflow.py` vérifient :

- Recherche, ajout au rack, création MIDI et changement d’instrument par les widgets Qt.
- Recherche sans résultat : impossible d’ajouter une sélection masquée.
- Absence de doublon dans le rack et disparition de l’ancien instrument après changement de sortie.
- Absence de superposition du synthétiseur interne sur Nova Drums.
- Silence d’un instrument indisponible, sans substitution de son.
- Ordre des événements MIDI et poursuite du traitement des queues de notes.
- Instances indépendantes par piste, héritage des réglages du rack et restauration des états.
- Sauvegarde/rechargement du rack, des routages et des réglages natifs.
- Maintien visible de la sortie assignée lorsqu’un plugin manque.
- Traitement des inserts VST du Master.
- Crash natif simulé au chargement ou pendant un appel, et expiration bornée d’un plugin bloqué.

Un ancien test qui imposait l’interdiction de Kontakt/SampleTank par leur nom a été adapté : l’isolation du processus remplace cette liste arbitraire. Cela ne constitue pas une certification de compatibilité de ces produits.

## Essais avec les plugins installés

| Plugin | Essai | Résultat observé |
| --- | --- | --- |
| Prologue, Cubase 6 | Note MIDI et note-off, 44,1 kHz, stéréo, 0,2 s | Buffer de 2 × 8 820 échantillons, crête ≈ 0,257 ; état VST de 6 476 octets relu/restauré |
| TR5 Classic EQ | Éditeur natif ouvert, traitement simultané, fermeture | Réussi ; 95 événements Qt de 50 ms traités au moment du contrôle à 6 s ; signal de test à 0,1 conservé ; éditeur confirmé fermé à 8 s |
| Prologue | Éditeur natif | Plantage reproduit également dans un programme Pedalboard minimal indépendant ; contenu dans le processus VST de NovaDAW |
| Kontakt | Rendu MIDI | Arrêt du processus VST pendant le rendu ; erreur remontée au parent, sans fermeture du parent |
| Superior Drummer | — | Non testé ; absent du catalogue détecté utilisé pour cette session |

## Parcours Windows

Menu Plugins, entrée « Ajouter un plugin au projet… », ouverture du sélecteur et filtrage « Nova Drums » vérifiés via l’arbre d’accessibilité de l’application réelle. La capture d’écran Windows échoue avec `SetIsBorderRequired ... 0x80004002` et les clics par coordonnées sont indisponibles ; la navigation au clavier a permis ces vérifications. Le reste du parcours est exercé par les tests Qt, et non présenté comme une session manuelle complète.

## Limites

- Hôte VST3/Pedalboard, sortie stéréo ; pas de certification VST2, multi-sorties ou contrôleur MIDI physique dans cette intervention.
- L’isolation contient les plantages mais ne répare pas une incompatibilité native. Kontakt en rendu et l’éditeur de Prologue restent des cas incompatibles observés.
- Le rack initialise des instances propres à chaque piste ; ce n’est pas un instrument multitimbral partagé avec sélection de canal MIDI.
- Les appels audio passent par IPC ; la latence sous forte charge et avec de grandes banques n’a pas été caractérisée. Un hôte bloqué peut interrompre temporairement le flux avant l’expiration de son délai.
- Les modifications déjà présentes dans l’enregistrement, la timeline et l’édition audio ont été conservées.
