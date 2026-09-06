"""
core/ipc/ipc_client.py - Client IPC pour communiquer avec l'instance active de NovaDAW
Utilisé par le serveur MCP (FastMCP) et les scripts d'automatisation.
"""
import os
import json
import socket
import tempfile
import uuid
from typing import Dict, Any, Optional

from core.ipc.ipc_server import get_ipc_info_path


class NovaIpcClient:
    """Client synchrone léger communiquant avec le serveur IPC de NovaDAW."""

    def __init__(self, host: str = "127.0.0.1", port: Optional[int] = None):
        self.default_host = host
        self.default_port = port or 8765

    def get_server_coords(self) -> tuple[str, int]:
        """Récupère l'hôte et le port de l'instance NovaDAW active."""
        path = get_ipc_info_path()
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    info = json.load(f)
                    return info.get("host", self.default_host), int(info.get("port", self.default_port))
            except Exception:
                pass
        return self.default_host, self.default_port

    def is_server_running(self) -> bool:
        """Vérifie si NovaDAW est actuellement ouvert et écoute les commandes."""
        host, port = self.get_server_coords()
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except (socket.error, ConnectionRefusedError, TimeoutError):
            return False

    def send_action(self, action: str, params: Optional[Dict[str, Any]] = None, timeout: float = 5.0) -> Any:
        """
        Envoie une action à NovaDAW et attend la réponse.
        Lève une exception explicite si NovaDAW n'est pas démarré ou si l'action échoue.
        """
        host, port = self.get_server_coords()
        req_id = str(uuid.uuid4())[:8]
        payload = {
            "id": req_id,
            "action": action,
            "params": params or {}
        }

        try:
            with socket.create_connection((host, port), timeout=timeout) as s:
                s.settimeout(timeout)
                data = json.dumps(payload) + "\n"
                s.sendall(data.encode("utf-8"))

                # Lecture de la réponse jusqu'au saut de ligne
                response_bytes = bytearray()
                while b"\n" not in response_bytes:
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    response_bytes.extend(chunk)

                if not response_bytes:
                    raise ConnectionError("NovaDAW a fermé la connexion avant d'envoyer la réponse.")

                line, _, _ = response_bytes.partition(b"\n")
                resp = json.loads(line.decode("utf-8"))

                if not resp.get("success"):
                    raise RuntimeError(resp.get("error", "Erreur inconnue lors de l'exécution dans NovaDAW."))

                return resp.get("result")

        except (ConnectionRefusedError, socket.error) as e:
            raise ConnectionError(
                f"Impossible de se connecter à NovaDAW sur {host}:{port}. "
                f"Assurez-vous que NovaDAW est bien lancé !"
            ) from e
