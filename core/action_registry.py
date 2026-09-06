"""
core/action_registry.py - Registre automatique d'actions pour NovaDAW et le protocole MCP

Ce système permet à toute nouvelle fonctionnalité de NovaDAW d'être automatiquement :
1. Enregistrée comme action exécutable sur le thread principal de l'application (thread-safe).
2. Exposée dynamiquement au protocole MCP sans avoir à modifier le serveur MCP.
"""
import inspect
from dataclasses import dataclass, field
from typing import Callable, Dict, Any, List, Optional
import functools


@dataclass
class ActionParameter:
    name: str
    type_annotation: Any
    default: Any
    has_default: bool


@dataclass
class ActionDefinition:
    name: str
    func: Callable
    description: str
    parameters: List[ActionParameter] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)

    def get_public_parameters(self) -> List[ActionParameter]:
        """Retourne les paramètres visibles pour l'IA (en omettant le premier paramètre 'app')"""
        return self.parameters[1:] if self.parameters and self.parameters[0].name == "app" else self.parameters


class ActionRegistry:
    """Registre centralisé des actions NovaDAW"""

    def __init__(self):
        self._actions: Dict[str, ActionDefinition] = {}

    def register(self, name: Optional[str] = None, description: Optional[str] = None, tags: Optional[List[str]] = None):
        """
        Décorateur pour enregistrer une action NovaDAW.
        Le premier paramètre de la fonction décorée doit TOUJOURS être `app` (l'instance de MainWindow).
        
        Exemple :
        ```python
        @action_registry.register(
            name="novadaw_set_loop_region",
            description="Définit la région de boucle...",
            tags=["transport", "loop"]
        )
        def set_loop_region(app, start_beat: float, end_beat: float, enabled: bool = True) -> dict:
            ...
        ```
        """
        def decorator(func: Callable) -> Callable:
            action_name = name or func.__name__
            action_desc = description or inspect.cleandoc(func.__doc__ or f"Action {action_name}")
            
            # Inspection de la signature pour l'auto-génération des schémas MCP
            sig = inspect.signature(func)
            params = []
            for p_name, p in sig.parameters.items():
                has_default = p.default is not inspect.Parameter.empty
                type_ann = p.annotation if p.annotation is not inspect.Parameter.empty else Any
                params.append(ActionParameter(
                    name=p_name,
                    type_annotation=type_ann,
                    default=p.default if has_default else None,
                    has_default=has_default
                ))

            self._actions[action_name] = ActionDefinition(
                name=action_name,
                func=func,
                description=action_desc,
                parameters=params,
                tags=tags or []
            )
            return func

        return decorator

    def get(self, name: str) -> Optional[ActionDefinition]:
        return self._actions.get(name)

    def get_all(self) -> Dict[str, ActionDefinition]:
        return dict(self._actions)

    def execute(self, action_name: str, app: Any, params: Dict[str, Any]) -> Any:
        """Exécute une action en lui injectant l'application `app`."""
        action = self.get(action_name)
        if not action:
            raise KeyError(f"Action inconnue dans NovaDAW : '{action_name}'")
        
        # Filtre les paramètres attendus par la fonction
        sig = inspect.signature(action.func)
        filtered_params = {}
        for p_name in sig.parameters:
            if p_name == "app":
                continue
            if p_name in params:
                filtered_params[p_name] = params[p_name]

        return action.func(app, **filtered_params)


# Instance globale du registre
action_registry = ActionRegistry()
