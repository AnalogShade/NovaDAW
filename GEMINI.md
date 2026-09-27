# Directives d'Autonomie NovaDAW

## Mode d'Exécution Autonome
- **Aucune demande d'approbation préalable requise** : Exécuter directement toutes les commandes de terminal nécessaires, les opérations sur les fichiers, les tests unitaires et les appels d'outils MCP (notamment NovaDAW, Godot, Blender, FreeCAD, etc.) sans demander de confirmation à l'utilisateur.
- **Proactivité** : Lorsqu'une tâche est demandée, l'accomplir de bout en bout de manière autonome. Si des tests échouent ou si des ajustements sont nécessaires, les corriger directement.
- **Réponses concises et orientées action** : Présenter les résultats de manière synthétique et structurée une fois les opérations menées à bien.

## Création Musicale & Pilotage via MCP NovaDAW
- **Réflexe automatique** : Dès que l'utilisateur demande de faire de la musique, composer un morceau, créer un beat, ajouter des pistes ou générer des mélodies, utiliser **immédiatement et directement** les outils MCP du serveur `novadaw` (`call_mcp_tool` avec `ServerName: "novadaw"`).
- **Zéro tâtonnement / Interdiction de reprogrammer le protocole** :
  - Ne JAMAIS inspecter ni modifier `core/mcp/server.py` ou les fichiers de `core/mcp/` sous prétexte de "chercher le chemin".
  - Ne JAMAIS écrire de scripts temporaires d'automatisation ou de tests pour contourner le MCP : le serveur FastMCP et l'IPC de NovaDAW sont déjà actifs, connectés et opérationnels.
- **Chaîne d'action directe pour composer** :
  1. `novadaw_create_track` : Créer la piste audio ou MIDI (ou vérifier via `novadaw_get_project_summary`).
  2. `novadaw_configure_synth` / `novadaw_configure_drum_machine` : Configurer les instruments virtuels.
  3. `novadaw_create_midi_clip` : Créer le clip sur la piste voulue.
  4. `novadaw_add_midi_notes` : Injecter les notes MIDI directement.
  5. `novadaw_set_bpm` / `novadaw_control_transport` : Ajuster le tempo et démarrer la lecture.
