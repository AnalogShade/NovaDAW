"""
core/mcp/server.py - Serveur officiel Model Context Protocol (MCP) pour NovaDAW

Découvre automatiquement toutes les actions enregistrées via `@action_registry.register`
et les expose instantanément aux clients IA (Antigravity, Claude Desktop, Cursor, Codex...).
"""
import sys
import os
import json
import inspect
from typing import List, Dict, Any, Optional, Union

# S'assurer que le dossier racine du projet est dans sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from mcp.server.fastmcp import FastMCP
from core.action_registry import action_registry, ActionDefinition
from core.ipc.ipc_client import NovaIpcClient
import core.actions  # Enregistre toutes les actions (transport, midi, project)

# Initialisation du serveur FastMCP
mcp = FastMCP(
    "NovaDAW",
    dependencies=["PySide6", "sounddevice", "numpy", "mcp"]
)

# Client IPC connecté à l'instance NovaDAW en cours d'exécution
ipc_client = NovaIpcClient()


def _build_mcp_wrapper(action: ActionDefinition, client: NovaIpcClient):
    """
    Construit dynamiquement une fonction Python typée pour FastMCP,
    en extrayant les paramètres publics (sans 'app') et leur docstring.
    """
    public_params = action.get_public_parameters()
    param_strs = []

    for p in public_params:
        p_type_name = inspect.formatannotation(p.type_annotation)
        p_type_name = p_type_name.replace("typing.", "")

        if p.has_default:
            default_val = repr(p.default)
            param_strs.append(f"{p.name}: {p_type_name} = {default_val}")
        else:
            param_strs.append(f"{p.name}: {p_type_name}")

    params_code = ", ".join(param_strs)
    func_name = action.name

    code = f"""def {func_name}({params_code}) -> str:
    '''{action.description}'''
    params = locals()
    try:
        res = client.send_action('{action.name}', params)
        if isinstance(res, (dict, list)):
            return json.dumps(res, ensure_ascii=False, indent=2)
        return str(res)
    except ConnectionError as ce:
        return f"⚠️ Erreur de connexion : {{ce}}"
    except Exception as e:
        return f"❌ Erreur lors de l'exécution : {{e}}"
"""
    scope = {
        "client": client,
        "json": json,
        "List": List,
        "Dict": Dict,
        "Any": Any,
        "Optional": Optional,
        "Union": Union,
        "NoneType": type(None),
        "ConnectionError": ConnectionError,
    }
    exec(code, scope)
    return scope[func_name]


# --- Auto-Enregistrement de tous les Outils dans FastMCP ---
for action_name, action in action_registry.get_all().items():
    wrapper_fn = _build_mcp_wrapper(action, ipc_client)
    mcp.add_tool(wrapper_fn, name=action.name, description=action.description)


# --- Ressources MCP ---
@mcp.resource("novadaw://project/summary")
def resource_project_summary() -> str:
    """Fournit un instantané en lecture seule de la structure du projet NovaDAW actif."""
    if not ipc_client.is_server_running():
        return "NovaDAW n'est pas ouvert actuellement."
    try:
        res = ipc_client.send_action("novadaw_get_project_summary")
        return json.dumps(res, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Erreur : {e}"


@mcp.resource("novadaw://music/notes_guide")
def resource_music_notes_guide() -> str:
    """Guide des hauteurs de notes MIDI et équivalences de tempo/mesures."""
    guide = {
        "standard_notes": {
            "C1": 24, "C2": 36, "C3": 48, "C4 (Middle C)": 60,
            "A4 (440Hz)": 69, "C5": 72, "C6": 84
        },
        "time_conversions_4_4": {
            "1 Bar (1 Mesure)": "4.0 beats (temps)",
            "1/2 Note (Blanche)": "2.0 beats",
            "1/4 Note (Noire)": "1.0 beat",
            "1/8 Note (Croche)": "0.5 beat",
            "1/16 Note (Double-croche)": "0.25 beat"
        },
        "tips": [
            "Pour une mélodie standard en 4/4 à 120 BPM, 1 noire dure 1.0 temps (0.5 seconde).",
            "Pour régler une boucle sur 4 mesures complètes, début=0.0 et fin=16.0."
        ]
    }
    return json.dumps(guide, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    # Point d'entrée pour lancer le serveur MCP en mode stdio
    mcp.run()
