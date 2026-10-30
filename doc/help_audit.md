# Audit du menu Aide et manuel utilisateur intégré

Audit et réalisation : 29 octobre 2026. Le code a été utilisé comme référence
fonctionnelle lorsque les anciens textes contredisaient l’implémentation.

## 1. État initial

L’arbre de travail Git était propre, sur `codex/editing-workflow-improvements`.
Conformément à la clarification de l’utilisateur, le travail reste sur cette
branche existante : aucune branche supplémentaire n’a été créée.

Le dernier menu de la barre principale était déjà **Aide**. Il proposait
Documentation (F1) et À propos. `ui/help_dialog.py` ouvrait une fenêtre modale,
780 × 580, minimum 640 × 480, avec trois onglets : raccourcis, guide de sept
sections, À propos. La table des raccourcis était la première page.

Le guide contenait des explications utiles sur les pistes, clips, outils,
boucles et piano roll, conservées et développées dans le nouveau manuel.
Il n’existait ni recherche, ni navigation par chapitre, ni liens pédagogiques
entre sujets. La fenêtre modale empêchait de suivre les procédures dans Nova.

Le README et `doc/` apportaient une documentation technique substantielle.
Les infobulles de l’inspecteur, des en-têtes, du transport et des plugins étaient
déjà nombreuses. Ces ressources étaient principalement orientées développeurs,
sans parcours continu adapté à une première composition.

## 2. Architecture et inventaire vérifié

| Domaine réellement présent | Sources principales examinées | Couverture dans l’aide |
| --- | --- | --- |
| Application Qt, menus, panneaux, commandes globales | `main.py`, `ui/main_window.py`, `assets/style.qss` | Interface, démarrage, raccourcis |
| Pistes MIDI/Audio/Master, clips et notes | `core/project.py`, `ui/dialogs.py`, `ui/track_header.py`, `ui/inspector.py` | Pistes, routage MIDI, arrangement |
| Grille, outils, scission, presse-papiers, zoom, boucle | `ui/timeline_view.py`, `ui/editing_toolbar.py` | Arrangement, transport, gestes |
| Sélection multiple, quantification, transposition, vélocité, historique local | `ui/piano_roll.py` | Piano roll, raccourcis contextuels |
| Clavier d’ordinateur, notes tenues, octaves, vélocité | `ui/virtual_keyboard.py` | Clavier musical, enregistrement |
| Import et visualisation audio, gain, rognage | `core/audio_importer.py`, `ui/audio_editor.py`, `core/project.py` | Audio, formats, limites de réouverture |
| Lecture, cache, capture audio/MIDI, export WAV | `core/audio_engine.py`, méthodes transport/enregistrement/export de `ui/main_window.py` | Transport, prise, export |
| Instruments internes : synthé par défaut, NovaSynth, Nova Drums | `core/audio_engine.py`, `plugins/synth/`, `plugins/drum_machine/` | Synthèse par couches, sorties, batterie GM, exercices |
| NovaMixer, NovaEQ, NovaCompressor et inserts Master | `plugins/mixer/`, `plugins/equalizer/`, `plugins/compressor/`, `ui/master_fx_rack.py` | Mixage, paramètres essentiels, bypass, ordre |
| Catalogue de plugins, instances par piste, VST et scan | `core/plugin_manager.py`, `core/vst_host.py`, `ui/vst_rack.py`, `ui/project_plugin_dialog.py`, `ui/plugin_dialogs.py` | Plugins, affectation, compatibilité et diagnostic |
| Presets usine/utilisateur, import/export | `core/preset_manager.py`, `ui/plugin_dialogs.py` | Presets, protection usine, conservation des sources |
| Sauvegarde atomique, médias référencés, projets récents, protection des modifications | `core/serializer.py`, `core/recent_projects.py`, `ui/main_window.py` | Projets, organisation, récupération et limites |
| Périphériques, tampon, fréquence, compensation, informations GPU | `core/hardware_manager.py`, `ui/device_settings_dialog.py` | Matériel, latence et dépannage |
| Registre d’actions, pont IPC local, serveur MCP stdio | `core/action_registry.py`, `core/actions/`, `core/ipc/`, `core/mcp/` | IA/MCP, prérequis et limites de l’automatisation |

Les spécifications de `doc/` et les tests existants ont servi de contexte
complémentaire. L’aide n’importe pas le moteur audio, les plugins ou MCP :
son contenu et sa recherche restent indépendants du projet ouvert.

## 3. Lacunes et contradictions relevées

- Aucun premier parcours complet allant d’un projet vide à quatre notes audibles.
- Édition MIDI avancée absente du guide : sélection multiple, rectangle,
  quantification, transposition, vélocité, annuler/rétablir et portée de l’historique.
- Enregistrement, gestion des presets et routage par canal MIDI insuffisamment expliqués.
- Raccourcis manquants : import, enregistrement, duplication/suppression/déplacement
  des pistes, Master, F6, suivi de lecture et commandes du piano roll.
- Conflit de présentation Ctrl+K/Ctrl+B : la table citait Ctrl+B, le guide et
  le bouton de scission affichaient Ctrl+K, alors que Ctrl+K ouvre le clavier musical.
  Un gestionnaire local historique de timeline reconnaît encore Ctrl+K pour scinder.
- Les libellés `[e]` de plusieurs boutons ne sont pas accompagnés d’un raccourci E effectif.
- L’ancien texte décrivait le rack comme une instance sonore partagée par défaut,
  alors que les pistes ont leurs instances distinctes, sauf routage NovaSynth explicite.
- Export annoncé systématiquement en 44,1 kHz et morceau complet : le moteur
  écrit du PCM 24 bits à sa fréquence active ; le dialogue calcule la durée
  depuis la fin de boucle, avec au moins huit mesures et une division par quatre.
- Présentation trop absolue des formats audio et de la compatibilité VST.
  L’import peut utiliser un décodeur de secours ; la réouverture des projets lit
  les fichiers audio avec soundfile et n’emploie pas ce même secours.
- Absence d’explication des chemins audio référencés, de la non-collecte des médias
  par Enregistrer sous et de l’absence d’autosauvegarde périodique.
- L’onglet À propos avançait des nombres d’outils MCP contradictoires et des
  promesses générales non nécessaires à l’apprentissage.
- Risque de confusion entre modulation du synthé, commandes IA ponctuelles et
  véritables courbes d’automation, absentes du modèle actuel.
- L’éditeur audio définit des méthodes de zoom encore vides ; certaines préférences
  GPU sont mémorisées sans preuve d’une accélération effective de tous les traitements.

Autres éléments préexistants consignés pour suivi : plusieurs raccourcis de
fenêtre sont enregistrés à la fois comme QAction et QShortcut (F5, F6, F11,
Ctrl+K, Ctrl+0, F), parfois avec des destinations différentes pour F11.
L’inventaire décrit leurs commandes déclarées ; cela ne garantit pas l’absence
d’ambiguïté Qt dans chaque contexte. La présente intervention ne remanie pas
ce système global. Les menus et boutons restent des voies d’accès alternatives.

## 4. Améliorations livrées

Le centre d’aide est désormais un manuel français hors ligne, structuré en
**23 chapitres, 80 sections et 63 entrées de raccourcis par contexte**.
Les gestes de souris disposent aussi d’une référence dédiée.

Les catégories couvrent Découvrir, Composer, Audio et mixage, Instruments et
plugins, Projets et assistance, Outils avancés et Référence. Le contenu va du
vocabulaire élémentaire jusqu’aux bus NovaSynth et au contrôle MCP.

Fonctionnalités de consultation :

- Accueil pédagogique au premier accès, avec liens vers les prochaines étapes.
- Recherche sur titres, catégories, mots-clés, texte et raccourcis ; insensible
  aux accents et à la casse, avec intersection des mots recherchés.
- Message explicite en cas d’absence de résultat ; Entrée ouvre le premier résultat.
- Table des matières de chaque chapitre avec ancres internes.
- Liens entre chapitres, historique Précédent/Suivant, retour Accueil.
- Fenêtre non modale, réutilisée après fermeture, permettant de pratiquer en parallèle.
- Liste latérale sur fenêtre large et sélecteur de chapitre en dessous de 700 px.
- Texte avec retour à la ligne, contenu défilant, fenêtre redimensionnable
  jusqu’au minimum de 480 × 360.
- Commandes nommées pour les outils d’accessibilité, focus visible,
  navigation clavier, Ctrl+F, Alt+←/→ et fermeture par Échap.
- Aucun lien externe ouvert et aucune requête réseau pour consulter le manuel.

Le menu Aide conserve F1 et À propos, et ajoute les accès directs au premier
morceau et aux raccourcis. Le bouton **Aide F1** est placé à l’extrémité droite
de la barre supérieure. Le libellé et l’infobulle du bouton Scinder affichent
maintenant Ctrl+B. Les deux spécifications qui recommandaient Ctrl+K pour la
scission ont été corrigées ; le README renvoie au manuel et à ce rapport.

## 5. Couverture et limites restantes

Les commandes et parcours principaux de toutes les familles fonctionnelles
présentes sont documentés. Les procédures de démarrage, de batterie, de prise,
de mixage et d’export proposent des étapes ou exercices concrets. La FAQ réunit
les problèmes de son, focus clavier, craquements, sources perdues, plugins
absents, export tronqué et récupération.

Le manuel explique explicitement l’absence de courbes d’automation, de time-stretch,
d’autosauvegarde périodique et d’archive autonome des médias. Il distingue canal
MIDI, bus audio et partage d’instance. Il n’invente pas un chemin d’entrée MIDI
matériel ni une garantie de compatibilité de tous les VST2/anciens plugins.

Ce n’est pas une référence exhaustive de chaque paramètre DSP sérialisable ni
une copie des schémas de tous les outils MCP. Les contrôles usuels de l’interface
sont expliqués ; les paramètres avancés exposés seulement par MCP doivent être
consultés dans les schémas actifs du client. Les manuels des VST tiers et leurs
licences restent propres à leurs constructeurs.

## 6. Validation

**292 tests réussis, 35 modules exécutés dans des processus séparés**, sans
échec ni test ignoré. Le moteur physique est neutralisé par le harnais existant
avec `NOVADAW_SILENT_TESTS=1`. Les tests Qt utilisent la plateforme offscreen.

Les **17 nouveaux tests** de `tests/test_help_manual.py` couvrent :

- Unicité des identifiants, résolution de tous les liens et ancres, absence de liens réseau.
- Recherche accentuée/non accentuée, plusieurs mots, commandes clavier,
  requête vide et aucun résultat.
- Ouverture des résultats, liens connexes, historique et suppression de sa
  branche suivante après une nouvelle consultation.
- Défilement par table des matières et rejet des liens inconnus/externes.
- Tailles 480 × 360, 640 × 480, 1000 × 720 et 1440 × 900 ; chaque chapitre
  est testé sans barre de défilement horizontale du contenu.
- Sélecteur compact, Ctrl+F et Échap par événements clavier Qt.
- Ouverture **par une frappe F1 réelle**, entrées de menu, bouton supérieur,
  À propos, réutilisation non modale et absence de modification du projet.
- Inventaire des raccourcis de menus, à la fois par analyse syntaxique du code
  et par inspection des QAction réellement construites.

Une première passe a détecté un débordement horizontal des tableaux et une
assertion trop dépendante de la hauteur d’un titre. Les tableaux ont été
ajustés et le test d’ancre mesure désormais le déplacement effectif.
Les polices Segoe UI ont été chargées explicitement pour les rendus offscreen
Windows et les contrôles de reflow : cette plateforme ne découvre pas les
polices système automatiquement. Les rendus large et compact ont été inspectés.

La commande globale `python -m pytest tests/ -q -p no:cacheprovider` s’arrête
brutalement dans `test_piano_roll_editing.py`, au traitement des événements Qt.
Le même arrêt apparaît en excluant les nouveaux tests, puis dans une copie
du **HEAD initial non modifié**, extraite dans `scratch/help-baseline`.
Le module du piano roll passe séparément, ainsi qu’avec les tests du manuel
(33 réussites). Le passage intégral par module dans des processus indépendants
a permis de vérifier tous les tests malgré ce problème d’ordre/de cycle de vie
Qt préexistant. L’exécution monoprocessus ne doit donc pas être annoncée verte.

Pour reproduire la validation isolée sans dépendance supplémentaire :

```powershell
$env:NOVADAW_SILENT_TESTS = '1'
$env:QT_QPA_PLATFORM = 'offscreen'
Get-ChildItem tests/test_*.py | ForEach-Object {
    python -m pytest $_.FullName -q -p no:cacheprovider
    if ($LASTEXITCODE -ne 0) { throw "Échec : $($_.Name)" }
}
```

`git diff --check` ne signale aucune erreur d’espacement.
Les logs détaillés et captures de cette session sont dans `scratch/`, ignoré
par Git. Les parcours ont été vérifiés par code et widgets Qt ; aucune écoute
matérielle, licence VST tierce ou connexion à un modèle IA externe n’a été
testée. Aucun déploiement ni changement du moteur audio ou du format .ndaw.

## 7. Améliorations futures recommandées

1. Centraliser les raccourcis et leurs descriptions pour éviter les inscriptions
   Qt multiples, puis tester les frappes de toutes les commandes globales.
2. Donner au dialogue d’export une durée explicite, une option fin de dernier
   clip avec queue d’effets et un calcul correct de toutes les signatures.
3. Ajouter une collecte/relocation des médias et une récupération automatique
   versionnée, avec présentation claire de leur portée.
4. Stabiliser le cycle de vie des widgets/fixtures Qt afin de rendre la suite
   entière fiable dans un seul processus.
5. Proposer une aide contextuelle « ce panneau » et des exercices guidés avec
   projets exemples, puis les observer avec de vrais débutants.
6. Ajouter une référence avancée des outils MCP générée depuis leurs schémas,
   et distinguer le service IPC local de la connexion effective d’un client IA.
7. Tester avec lecteurs d’écran Windows et facteurs de mise à l’échelle élevés ;
   les noms accessibles et la navigation clavier ne suffisent pas à certifier
   une compatibilité complète avec chaque technologie d’assistance.

## 8. Git et maintenance

Branche conservée : `codex/editing-workflow-improvements`, à la demande de
l’utilisateur. Le commit de livraison inclut ce rapport, le contenu du manuel,
la fenêtre de consultation, les points d’accès, les corrections de libellés et
les tests. Son identifiant est communiqué dans le compte rendu de livraison.

Pour enrichir le manuel, modifier `ui/help_content.py` : chaque chapitre dispose
d’un identifiant stable, d’une catégorie, de sections et de liens connexes.
Lorsqu’un raccourci change, mettre à jour `SHORTCUTS` et vérifier les tests
d’inventaire. Ne pas utiliser les anciens nombres d’outils MCP des documents
techniques comme référence : le registre actif et les schémas exposés font foi.
