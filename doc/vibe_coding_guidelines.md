# 🚀 Guide du Vibe Coding Sécuritaire et Cohérent dans NovaDAW

Le concept de **Vibe Coding** désigne le développement logiciel assisté par intelligence artificielle à très haute vélocité. L'utilisateur guide l'intention musicale, technique ou ergonomique, et l'agent conçoit les briques logicielles, les interfaces et les tests associés.

Pour que cette vitesse reste une force et ne crée pas de dette technique, ce guide définit **les règles de l'art** permettant de développer vite tout en maintenant une **cohérence exemplaire** et une **parfaite intentionnalité** de chaque ligne de code.

---

## 💎 Les Principes Cardinaux du Vibe Coding dans NovaDAW

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ 1. PRINCIPE D'INTENTIONNALITÉ ET DE JUSTIFICATION                                      │
│    Savoir exactement pourquoi chaque ligne est modifiée ou ajoutée. Zéro retouche      │
│    cosmétique involontaire lors d'une tâche technique.                                 │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ 2. COHÉRENCE MODULAIRE (MODULE BLUEPRINT)                                              │
│    Tout nouveau plugin, instrument ou vue applique les conventions déjà en place       │
│    (barre d'en-tête standard, PresetManager, boutons Save/Load/Reset/Bypass).          │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ 3. ÉVOLUTION NATURELLE DE L'INTERFACE                                                  │
│    L'UI peut et doit être enrichie pour accueillir de nouvelles fonctions, tout en     │
│    respectant la charte graphique globale (thème sombre, ergonomie Cubase Pro).        │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ 4. VALIDATION AUTOMATISÉE CONTINUE                                                     │
│    La vitesse réelle repose sur des tests qui passent à 100%. Tout ajout important est │
│    assorti de son test de non-régression dans tests/.                                  │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ 5. PRÉSERVATION DES CONTRATS ET DE LA COMPATIBILITÉ                                    │
│    Tout projet .ndaw existant doit continuer de s'ouvrir sans anomalie.                │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 1. Intentionnalité : Distinguer le Changement Voulu du Changement Accidentel

Le problème majeur rencontré lors de sessions avec des agents d'IA non cadrés est le **changement collatéral non motivé** :

### Scénario A : La Retouche Technique (Algorithme / Logique)
- **Ce que demande l'utilisateur** : "Ajuste la formule de la pente du compresseur ou corrige le calcul de phase."
- **Règle** : L'interface visuelle n'a aucune raison de changer. Les boutons, couleurs et dispositions existants restent 100% identiques. Le diff se concentre sur le module de calcul DSP.

### Scénario B : L'Enrichissement Fonctionnel
- **Ce que demande l'utilisateur** : "Ajoute un commutateur Mono/Stéréo ou un contrôle de saturation."
- **Règle** : L'agent implémente le DSP **et** intègre le bouton ou le potentiomètre dans l'interface en appliquant les styles graphiques des autres boutons voisins.

### Scénario C : La Création d'un Nouveau Module
- **Ce que demande l'utilisateur** : "Crée un plugin de Chorus."
- **Règle** : L'agent adopte immédiatement le standard **Module Blueprint** (voir `doc/plugins_specification.md`) :
  - Barre d'en-tête standard : Titre, Presets, Sauvegarder, Charger, Reset, Bypass.
  - DSP FP32 stéréo.
  - Test unitaire associé.

---

## 2. Le Standard Modulaire ("Module Blueprint")

Quand l'utilisateur conçoit un nouveau module, il s'attend à retrouver une ergonomie familière :

1. **En-tête Universel** :
   - Titre et icône avec couleur d'accent.
   - Sélecteur de presets branché sur `core/preset_manager.py`.
   - Boutons `💾 Sauvegarder`, `📂 Charger`, `🔄 Reset`, `⚡ Bypass`.
2. **Zone Spécifique** :
   - Graphiques interactifs, faders, oscillateurs ou pads propres à la fonction du plugin.
3. **Sérialisation Automatique** :
   - Implémentation propre de `get_state()` et `set_state()` pour que l'état du plugin soit persisté dans les fichiers `.ndaw` sans effort supplémentaire.

---

## 3. Le Harnais de Tests : Garant de la Vitesse Réelle

La vélocité en Vibe Coding ne provient pas d'un code tapé sans vérification, mais de la **certitude que rien n'a régressé** :

1. **Tester le module modifié** :
   ```powershell
   python -m pytest tests/test_module_cible.py -q
   ```
2. **Si un test échoue** :
   - Ne jamais commenter ni désactiver l'assertion.
   - Analyser l'écart et corriger la cause racine dans le code.
3. **Ajouter un test pour toute nouvelle fonctionnalité** :
   - Un test simple validant l'état par défaut et le traitement audio suffit pour sanctuariser la fonctionnalité pour les futures sessions.

---

## 4. Grille d'Auto-Contrôle avant Restitution

Avant de clore une intervention, l'agent valide mentalement cette grille :

- [ ] **Raison d'être** : Suis-je capable d'expliquer pourquoi chaque modification de fichier a été réalisée ?
- [ ] **Stabilité des éléments existants** : Ai-je évité toute retouche cosmétique gratuite sur les composants non concernés ?
- [ ] **Cohérence modulaire** : Si un plugin a été touché ou créé, respecte-t-il le standard d'en-tête et de presets ?
- [ ] **Tests au vert** : Les tests unitaires du module passent-ils avec succès ?
- [ ] **Persistance `.ndaw`** : Le modèle de données reste-t-il compatible avec les projets existants ?
