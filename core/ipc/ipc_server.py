"""
core/ipc/ipc_server.py - Serveur IPC TCP local thread-safe pour NovaDAW
S'exécute directement sur la boucle événementielle Qt de l'application principale.
"""
import os
import json
import tempfile
from typing import Optional, Dict
from PySide6.QtCore import QObject
from PySide6.QtNetwork import QTcpServer, QTcpSocket, QHostAddress

from core.action_registry import action_registry


def get_ipc_info_path() -> str:
    """Chemin du fichier temporaire contenant les coordonnées du serveur IPC NovaDAW actif."""
    return os.path.join(tempfile.gettempdir(), "novadaw_ipc.json")


class NovaIpcServer(QObject):
    """
    Serveur IPC TCP local permettant aux clients externes (Serveur MCP, IA)
    de piloter NovaDAW de manière 100% thread-safe sur le thread GUI principal de Qt.
    """

    def __init__(self, app_window, host: str = "127.0.0.1", port: int = 8765, parent=None):
        super().__init__(parent)
        self.app = app_window
        self.host = host
        self.preferred_port = port
        self.actual_port: Optional[int] = None
        self._server = QTcpServer(self)
        self._server.newConnection.connect(self._on_new_connection)
        self._buffers: Dict[QTcpSocket, bytearray] = {}

    def start(self) -> bool:
        """Démarre l'écoute TCP locale sur 127.0.0.1."""
        address = QHostAddress(self.host)
        # Essayer d'abord le port préféré (8765), sinon laisser l'OS attribuer un port libre (0)
        success = self._server.listen(address, self.preferred_port)
        if not success:
            success = self._server.listen(address, 0)

        if success:
            self.actual_port = self._server.serverPort()
            self._write_ipc_info()
            return True
        else:
            print(f"[NovaDAW IPC] Impossible de démarrer le serveur IPC : {self._server.errorString()}")
            return False

    def stop(self):
        """Arrête le serveur et nettoie le fichier de coordonnées."""
        if self._server.isListening():
            self._server.close()
        self._remove_ipc_info()

    def _write_ipc_info(self):
        try:
            info = {
                "host": self.host,
                "port": self.actual_port,
                "pid": os.getpid(),
            }
            with open(get_ipc_info_path(), "w", encoding="utf-8") as f:
                json.dump(info, f)
        except Exception as e:
            print(f"[NovaDAW IPC] Erreur écriture fichier IPC : {e}")

    def _remove_ipc_info(self):
        path = get_ipc_info_path()
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass

    def _on_new_connection(self):
        socket = self._server.nextPendingConnection()
        if socket:
            self._buffers[socket] = bytearray()
            socket.readyRead.connect(lambda s=socket: self._on_ready_read(s))
            socket.disconnected.connect(lambda s=socket: self._on_disconnected(s))

    def _on_ready_read(self, socket: QTcpSocket):
        buffer = self._buffers.get(socket, bytearray())
        data = socket.readAll().data()
        buffer.extend(data)

        # Les messages sont délimités par des retours à la ligne '\n'
        while b"\n" in buffer:
            line, _, rest = buffer.partition(b"\n")
            buffer = bytearray(rest)
            self._buffers[socket] = buffer
            
            line_str = line.decode("utf-8", errors="replace").strip()
            if line_str:
                self._handle_request(socket, line_str)

    def _handle_request(self, socket: QTcpSocket, raw_json: str):
        try:
            req = json.loads(raw_json)
            req_id = req.get("id", "req-0")
            action_name = req.get("action")
            params = req.get("params", {})

            # Notification visuelle discrète dans la barre d'état
            if hasattr(self.app, "statusBar"):
                status_msg = f"🤖 IA MCP : {action_name}"
                self.app.statusBar().showMessage(status_msg, 3000)

            # Exécution directe sur le thread principal Qt
            result = action_registry.execute(action_name, self.app, params)

            response = {
                "id": req_id,
                "success": True,
                "result": result,
                "error": None
            }
        except Exception as e:
            import traceback
            traceback.print_exc()
            response = {
                "id": req.get("id", "req-0") if "req" in locals() else "err",
                "success": False,
                "result": None,
                "error": f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
            }

        try:
            payload = (json.dumps(response) + "\n").encode("utf-8")
            socket.write(payload)
            socket.flush()
        except Exception as e:
            print(f"[NovaDAW IPC] Erreur envoi réponse : {e}")

    def _on_disconnected(self, socket: QTcpSocket):
        self._buffers.pop(socket, None)
        try:
            from shiboken6 import isValid
            if isValid(socket):
                socket.deleteLater()
        except Exception:
            pass
