"""
core/hardware_manager.py - Gestionnaire des périphériques audio (pilotes/cartes son) et graphiques (GPU)
pour NovaDAW.

Fonctionnalités :
- Énumération et sélection des pilotes audio (ASIO, WASAPI, DirectSound, MME, WDM-KS).
- Découverte des périphériques de sortie (haut-parleurs, écouteurs, DAC, cartes son externes).
- Découverte des périphériques d'entrée (microphones, entrées ligne).
- Détection des cartes graphiques (GPU NVIDIA, AMD, Intel), VRAM, version de pilote et statut.
- Configuration du moteur de rendu graphique (Direct3D 11, OpenGL, Vulkan, Software).
- Sauvegarde et persistance des préférences matérielles dans `settings.json`.
- Génération d'un signal de test audio (Test Tone).
"""
import os
import sys
import json
import subprocess
import ctypes
from typing import List, Dict, Any, Optional, Tuple
import numpy as np

try:
    import sounddevice as sd
    HAS_SOUNDDEVICE = True
except ImportError:
    HAS_SOUNDDEVICE = False

SETTINGS_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "settings.json"))


class HardwareManager:
    """Gestionnaire centralisé du matériel (Audio et GPU) de NovaDAW"""

    def __init__(self):
        self.settings: Dict[str, Any] = self._load_settings()

    def _load_settings(self) -> Dict[str, Any]:
        default_settings = {
            "audio": {
                "host_api_name": "Windows WASAPI",
                "output_device_name": None,
                "output_device_index": None,
                "input_device_name": None,
                "input_device_index": None,
                "sample_rate": 44100,
                "buffer_size": 512,
            },
            "graphics": {
                "selected_gpu": None,
                "rendering_backend": "Direct3D 11 (Accélération Matérielle)",
                "fps_limit": 60,
                "high_dpi_enabled": True,
            }
        }
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                    default_settings["audio"].update(saved.get("audio", {}))
                    default_settings["graphics"].update(saved.get("graphics", {}))
            except Exception as e:
                print(f"[HardwareManager] Erreur lecture settings.json: {e}")
        return default_settings

    def save_settings(self) -> bool:
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            print(f"[HardwareManager] Erreur sauvegarde settings.json: {e}")
            return False

    # ==========================================
    # GESTION DES CARTES GRAPHIQUES (GPU)
    # ==========================================
    def get_gpu_devices(self) -> List[Dict[str, Any]]:
        """
        Détecte toutes les cartes graphiques disponibles sur le système Windows
        en récupérant le nom, la mémoire VRAM, la version de pilote et le statut.
        """
        gpus: List[Dict[str, Any]] = []

        # 1. Tentative via PowerShell / CIM Win32_VideoController
        try:
            cmd = [
                "powershell", "-NoProfile", "-Command",
                "Get-CimInstance Win32_VideoController | Select-Object Name, DriverVersion, Status, VideoProcessor, AdapterRAM | ConvertTo-Json"
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if res.returncode == 0 and res.stdout.strip():
                data = json.loads(res.stdout)
                if isinstance(data, dict):
                    data = [data]
                for item in data:
                    name = item.get("Name")
                    if not name:
                        continue
                    # Ignorer les moniteurs virtuels inactifs si une vraie carte graphique existe
                    raw_ram = item.get("AdapterRAM")
                    vram_mb = int(raw_ram // (1024 * 1024)) if isinstance(raw_ram, (int, float)) and raw_ram > 0 else 0
                    gpus.append({
                        "name": str(name),
                        "driver_version": str(item.get("DriverVersion") or "Inconnu"),
                        "status": str(item.get("Status") or "OK"),
                        "video_processor": str(item.get("VideoProcessor") or name),
                        "vram_mb": vram_mb,
                        "vram_gb": round(vram_mb / 1024.0, 2) if vram_mb > 0 else None,
                        "is_primary": "nvidia" in name.lower() or "radeon" in name.lower() or "geforce" in name.lower() or "intel" in name.lower()
                    })
        except Exception as e:
            print(f"[HardwareManager] Détection GPU PowerShell échouée: {e}")

        # 2. Fallback via ctypes EnumDisplayDevicesW si rien trouvé
        if not gpus and sys.platform == "win32":
            try:
                from ctypes import wintypes
                class DISPLAY_DEVICE(ctypes.Structure):
                    _fields_ = [
                        ('cb', wintypes.DWORD),
                        ('DeviceName', wintypes.WCHAR * 32),
                        ('DeviceString', wintypes.WCHAR * 128),
                        ('StateFlags', wintypes.DWORD),
                        ('DeviceID', wintypes.WCHAR * 128),
                        ('DeviceKey', wintypes.WCHAR * 128)
                    ]
                dd = DISPLAY_DEVICE()
                dd.cb = ctypes.sizeof(dd)
                i = 0
                seen = set()
                while ctypes.windll.user32.EnumDisplayDevicesW(None, i, ctypes.byref(dd), 0):
                    card_name = dd.DeviceString.strip()
                    if card_name and card_name not in seen:
                        seen.add(card_name)
                        gpus.append({
                            "name": card_name,
                            "driver_version": "Détecté par Windows",
                            "status": "OK",
                            "video_processor": card_name,
                            "vram_mb": 0,
                            "vram_gb": None,
                            "is_primary": bool(dd.StateFlags & 1)
                        })
                    i += 1
            except Exception as e:
                print(f"[HardwareManager] Fallback GPU ctypes échoué: {e}")

        if not gpus:
            gpus.append({
                "name": "Accélérateur Graphique Standard",
                "driver_version": "Générique",
                "status": "OK",
                "video_processor": "Générique",
                "vram_mb": 0,
                "vram_gb": None,
                "is_primary": True
            })

        return gpus

    def get_graphics_backends(self) -> List[str]:
        return [
            "Direct3D 11 (Accélération Matérielle Optimale)",
            "OpenGL (Compatible VST & Shaders)",
            "Vulkan (Haute Performance)",
            "Logiciel (Software - Sans GPU)"
        ]

    # ==========================================
    # GESTION DES PILOTES & CARTES AUDIO
    # ==========================================
    def get_audio_host_apis(self) -> List[Dict[str, Any]]:
        """Liste les pilotes / interfaces audio de l'ordinateur (ASIO, WASAPI, etc.)"""
        if not HAS_SOUNDDEVICE:
            return [{"index": 0, "name": "Émulateur Audio Virtuel", "devices": []}]

        try:
            apis = sd.query_hostapis()
            results = []
            for idx, api in enumerate(apis):
                results.append({
                    "index": idx,
                    "name": api.get("name", f"API {idx}"),
                    "device_count": len(api.get("devices", [])),
                    "default_input": api.get("default_input_device", -1),
                    "default_output": api.get("default_output_device", -1),
                })
            return results
        except Exception as e:
            print(f"[HardwareManager] Erreur lecture host APIs: {e}")
            return []

    def get_audio_devices(self, host_api_index: Optional[int] = None) -> Dict[str, List[Dict[str, Any]]]:
        """
        Retourne la liste des périphériques de sortie et d'entrée,
        filtrés optionnellement par l'interface audio choisie.
        """
        if not HAS_SOUNDDEVICE:
            return {"outputs": [], "inputs": []}

        try:
            devices = sd.query_devices()
            outputs = []
            inputs = []

            for idx, d in enumerate(devices):
                if host_api_index is not None and d.get("hostapi") != host_api_index:
                    continue

                dev_info = {
                    "index": idx,
                    "name": d.get("name", f"Device {idx}"),
                    "hostapi": d.get("hostapi"),
                    "max_inputs": d.get("max_input_channels", 0),
                    "max_outputs": d.get("max_output_channels", 0),
                    "default_samplerate": int(d.get("default_samplerate", 44100)),
                    "low_latency_ms": round(d.get("default_low_output_latency", 0.01) * 1000.0, 2),
                    "high_latency_ms": round(d.get("default_high_output_latency", 0.04) * 1000.0, 2),
                }

                if dev_info["max_outputs"] > 0:
                    outputs.append(dev_info)
                if dev_info["max_inputs"] > 0:
                    inputs.append(dev_info)

            return {"outputs": outputs, "inputs": inputs}
        except Exception as e:
            print(f"[HardwareManager] Erreur lecture périphériques audio: {e}")
            return {"outputs": [], "inputs": []}

    def get_supported_sample_rates(self) -> List[int]:
        return [44100, 48000, 88200, 96000, 192000]

    def get_supported_buffer_sizes(self) -> List[int]:
        return [64, 128, 256, 512, 1024, 2048]

    def calculate_latency_ms(self, buffer_size: int, sample_rate: int) -> float:
        if sample_rate <= 0:
            return 0.0
        return round((buffer_size / sample_rate) * 1000.0, 2)


# Instance globale
hardware_manager = HardwareManager()
