"""
core/ipc - Couche de communication inter-processus (IPC) pour NovaDAW
"""
from core.ipc.ipc_server import NovaIpcServer
from core.ipc.ipc_client import NovaIpcClient

__all__ = ["NovaIpcServer", "NovaIpcClient"]
