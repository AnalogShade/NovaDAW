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
                "buffer_size": 256,
                "record_offset_samples": 0,
                "asio_guard_enabled": True,
                "release_driver_background": False,
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
        return [64, 128, 192, 256, 384, 512, 768, 1024, 2048]

    def calculate_latency_ms(self, buffer_size: int, sample_rate: int) -> float:
        if sample_rate <= 0:
            return 0.0
        return round((buffer_size / sample_rate) * 1000.0, 2)

    def get_device_latencies(
        self,
        output_device_index: Optional[int] = None,
        input_device_index: Optional[int] = None,
        sample_rate: int = 44100,
        buffer_size: int = 256,
        **kwargs
    ) -> Dict[str, float]:
        """
        Calcule les latences réelles (Buffer + Convertisseurs ADC/DAC) pour l'entrée,
        la sortie et l'aller-retour total (RTL) style Cubase 6.
        """
        # Gérer flexibilité d'appel
        sr = int(kwargs.get("sample_rate", sample_rate))
        buf = int(kwargs.get("buffer_size", buffer_size))
        out_idx = kwargs.get("output_device_index", output_device_index)
        in_idx = kwargs.get("input_device_index", input_device_index)

        sr = max(1, sr)
        base_buf_ms = (buf / sr) * 1000.0

        out_lat_ms = base_buf_ms
        in_lat_ms = base_buf_ms

        if HAS_SOUNDDEVICE:
            try:
                if out_idx is not None and out_idx >= 0:
                    d = sd.query_devices(out_idx)
                    hw_low = d.get("default_low_output_latency", 0.0)
                    if hw_low > 0:
                        out_lat_ms = max(base_buf_ms, hw_low * 1000.0)
                if in_idx is not None and in_idx >= 0:
                    d_in = sd.query_devices(in_idx)
                    hw_in_low = d_in.get("default_low_input_latency", 0.0)
                    if hw_in_low > 0:
                        in_lat_ms = max(base_buf_ms, hw_in_low * 1000.0)
            except Exception:
                pass

        roundtrip_ms = in_lat_ms + out_lat_ms
        return {
            "input_ms": round(in_lat_ms, 2),
            "output_ms": round(out_lat_ms, 2),
            "roundtrip_ms": round(roundtrip_ms, 2),
            "buffer_ms": round(base_buf_ms, 2),
            "input_latency_ms": round(in_lat_ms, 2),
            "output_latency_ms": round(out_lat_ms, 2),
            "roundtrip_latency_ms": round(roundtrip_ms, 2),
            "buffer_duration_ms": round(base_buf_ms, 2)
        }

    def open_audio_driver_control_panel(self) -> Tuple[bool, str]:
        """
        Ouvre directement le panneau de configuration natif du fabricant de la carte audio
        (ex: Avid Eleven Rack, Focusrite Control, RME TotalMix, MOTU, Steinberg, ASIO4ALL)
        ou le panneau audio Windows (mmsys.cpl), exactement comme le bouton 'Panneau de configuration' de Cubase.
        Retourne (succès: bool, nom_du_panneau: str).
        """
        vendor_candidates = [
            (r"C:\Program Files (x86)\Avid\Eleven Rack\Panel.exe", "Panneau de Contrôle Avid Eleven Rack"),
            (r"C:\Program Files\Avid\Eleven Rack\Panel.exe", "Panneau de Contrôle Avid Eleven Rack"),
            (r"C:\Program Files\Focusrite\Focusrite Control\Focusrite Control.exe", "Focusrite Control"),
            (r"C:\Program Files\FocusritePCIe\Focusrite Control.exe", "Focusrite Control"),
            (r"C:\Program Files (x86)\ASIO4ALL v2\a4apanel.exe", "Panneau de Contrôle ASIO4ALL"),
            (r"C:\Program Files (x86)\ASIO4ALL v2\a4apanel64.exe", "Panneau de Contrôle ASIO4ALL 64-bit"),
            (r"C:\Program Files\RME\TotalMix\TotalMix.exe", "RME TotalMix FX"),
            (r"C:\Windows\System32\TotalMix.exe", "RME TotalMix FX"),
            (r"C:\Program Files\Yamaha\Yamaha Steinberg USB Driver\ysusb_cpl.exe", "Panneau Yamaha / Steinberg USB"),
            (r"C:\Program Files (x86)\Yamaha\Yamaha Steinberg USB Driver\ysusb_cpl.exe", "Panneau Yamaha / Steinberg USB"),
            (r"C:\Program Files\Steinberg\UR-C\UR-C_Extension.exe", "Steinberg UR-C Control Panel"),
            (r"C:\Program Files\PreSonus\Universal Control\Universal Control.exe", "PreSonus Universal Control"),
            (r"C:\Program Files\MOTU\Pro Audio\MOTU Pro Audio Control.exe", "MOTU Pro Audio Control"),
        ]
        for path, name in vendor_candidates:
            if os.path.exists(path):
                try:
                    subprocess.Popen([path])
                    return True, name
                except Exception:
                    pass

        try:
            subprocess.Popen(["control", "mmsys.cpl", "sounds"])
            return True, "Panneau Son & Matériel Windows (mmsys.cpl)"
        except Exception as e:
            return False, str(e)

    KNOWN_STUDIO_BRANDS = (
        "scarlett", "focusrite", "eleven", "avid", "uad", "apollo", "steinberg",
        "ur22", "ur24", "ur44", "ur12", "ur824", "motu", "rme", "babyface", "fireface",
        "presonus", "audiobox", "quantum", "studio 24", "behringer", "u-phoria", "umc",
        "audient", "id4", "id14", "id22", "id44", "evo", "roland", "rubix", "quad-capture",
        "ssl", "solid state", "volt", "arturia", "minifuse", "audiofuse", "m-audio",
        "air", "fast track", "yamaha", "ag03", "ag06", "tascam", "zoom", "u-22", "u-24",
        "u-44", "apogee", "antelope", "native instruments", "komplete", "traktor",
        "clairett", "scarlet", "mackie", "onyx"
    )

    def find_device_by_name(self, target_name: str, is_input: bool = False) -> Optional[int]:
        """Retrouve l'index actuel d'un périphérique audio d'après son nom (ou partie du nom)."""
        if not HAS_SOUNDDEVICE or not target_name:
            return None
        # Si le nom est un texte générique de sélection par défaut, ne pas chercher de matériel
        target_clean = target_name.strip().lower()
        if any(w in target_clean for w in ("défaut", "defaut", "default", "périphérique", "peripherique")):
            return None
        try:
            devices = sd.query_devices()
            ch_key = "max_input_channels" if is_input else "max_output_channels"
            # 1. Correspondance exacte
            for idx, d in enumerate(devices):
                if d.get(ch_key, 0) > 0 and d.get("name", "").strip().lower() == target_clean:
                    return idx
            # 2. Correspondance partielle
            for idx, d in enumerate(devices):
                if d.get(ch_key, 0) > 0 and target_clean in d.get("name", "").lower():
                    return idx
            return None
        except Exception:
            return None

    def get_default_output_device_index(self) -> Optional[int]:
        """
        Détermine universellement le périphérique de sortie optimal, quel que soit le matériel de l'utilisateur :
        1. Si un périphérique a été précédemment sélectionné et existe encore (par index ou nom), le conserver.
        2. Sinon, détecter les interfaces audio studio dédiées (Focusrite, Scarlett, Universal Audio, Eleven Rack, Steinberg, etc.) sur les pilotes faible latence (ASIO, WASAPI).
        3. Sinon, utiliser la sortie par défaut de Windows WASAPI (faible latence native).
        4. Repli final sur la sortie par défaut du système.
        """
        if not HAS_SOUNDDEVICE:
            return None
        try:
            # 1. Vérifier si un périphérique est déjà sauvegardé
            saved_name = self.settings.get("audio", {}).get("output_device_name")
            saved_idx = self.settings.get("audio", {}).get("output_device_index")
            if saved_name:
                resolved_idx = self.find_device_by_name(saved_name, is_input=False)
                if resolved_idx is not None:
                    return resolved_idx
            if saved_idx is not None and isinstance(saved_idx, int) and saved_idx >= 0:
                try:
                    d = sd.query_devices(saved_idx)
                    if d.get("max_output_channels", 0) > 0:
                        return saved_idx
                except Exception:
                    pass

            host_apis = sd.query_hostapis()
            devices = sd.query_devices()

            # Identifier les API faible latence (ASIO, WASAPI)
            preferred_api_indices = []
            for i, api in enumerate(host_apis):
                api_name = api.get("name", "").lower()
                if "asio" in api_name:
                    preferred_api_indices.insert(0, i)
                elif "wasapi" in api_name:
                    preferred_api_indices.append(i)

            # 2. Chercher une interface audio externe / studio dédiée sur les API faible latence
            for api_idx in preferred_api_indices:
                for idx, d in enumerate(devices):
                    if d.get("hostapi") == api_idx and d.get("max_output_channels", 0) > 0:
                        dev_name = d.get("name", "").lower()
                        if any(brand in dev_name for brand in self.KNOWN_STUDIO_BRANDS):
                            return idx

            # 3. Prendre la sortie par défaut de WASAPI (carte son principale de l'utilisateur)
            for api_idx in preferred_api_indices:
                def_out = host_apis[api_idx].get("default_output_device", -1)
                if def_out >= 0:
                    try:
                        d = sd.query_devices(def_out)
                        if d.get("max_output_channels", 0) > 0:
                            return def_out
                    except Exception:
                        pass

            # 4. Repli : toute interface studio détectée sur n'importe quel pilote
            for idx, d in enumerate(devices):
                if d.get("max_output_channels", 0) > 0:
                    dev_name = d.get("name", "").lower()
                    if any(brand in dev_name for brand in self.KNOWN_STUDIO_BRANDS):
                        return idx

            # 5. Repli : sortie par défaut absolue du système
            try:
                def_sys = sd.default.device[1]
                if def_sys is not None and def_sys >= 0:
                    return int(def_sys)
            except Exception:
                pass

            return None
        except Exception as e:
            print(f"[HardwareManager] Erreur sélection périphérique sortie auto: {e}")
            return None

    def get_default_input_device_index(self, host_api_index: Optional[int] = None) -> Optional[int]:
        """
        Détermine universellement le périphérique d'entrée optimal (Microphone / Entrée ligne) :
        1. Si un périphérique spécifique a été sauvegardé (par nom explicite ou index), le valider et le conserver.
        2. Si 'Entrée par défaut' est demandée ou configurée, utiliser l'entrée par défaut configurée dans Windows
           (ex: casque Oculus Rift, micro USB, micro casque gamer, micro dédié).
        3. Aligner sur le pilote audio (Host API) actif (WASAPI, DirectSound, ASIO, etc.).
        4. Repli sur l'entrée par défaut globale de Windows (sd.default.device[0]).
        5. Repli final sur tout microphone ou entrée disponible.
        """
        if not HAS_SOUNDDEVICE:
            return None
        try:
            saved_name = self.settings.get("audio", {}).get("input_device_name")
            saved_idx = self.settings.get("audio", {}).get("input_device_index")

            # 1. Vérifier si un périphérique spécifique (non "par défaut") est sauvegardé
            is_generic_default = (
                not saved_name or
                any(w in saved_name.lower() for w in ("défaut", "defaut", "default", "périphérique", "peripherique"))
            )
            if not is_generic_default:
                if saved_name:
                    resolved_idx = self.find_device_by_name(saved_name, is_input=True)
                    if resolved_idx is not None:
                        return resolved_idx
                if saved_idx is not None and isinstance(saved_idx, int) and saved_idx >= 0:
                    try:
                        d = sd.query_devices(saved_idx)
                        if d.get("max_input_channels", 0) > 0:
                            return saved_idx
                    except Exception:
                        pass

            host_apis = sd.query_hostapis()
            devices = sd.query_devices()

            # 2. Entrée par défaut de l'API hôte active (DirectSound, WASAPI, etc.)
            target_api_idx = host_api_index
            if target_api_idx is None:
                saved_api_name = self.settings.get("audio", {}).get("host_api_name", "").lower()
                for idx, api in enumerate(host_apis):
                    if saved_api_name and saved_api_name in api.get("name", "").lower():
                        target_api_idx = idx
                        break

            if target_api_idx is not None and 0 <= target_api_idx < len(host_apis):
                def_in = host_apis[target_api_idx].get("default_input_device", -1)
                if def_in >= 0:
                    try:
                        d = sd.query_devices(def_in)
                        if d.get("max_input_channels", 0) > 0:
                            return def_in
                    except Exception:
                        pass

            # 3. Entrée par défaut Windows globale (Microphone Windows par défaut, ex: Oculus Rift)
            try:
                def_sys = sd.default.device[0]
                if def_sys is not None and def_sys >= 0:
                    d = sd.query_devices(int(def_sys))
                    if d.get("max_input_channels", 0) > 0:
                        return int(def_sys)
            except Exception:
                pass

            # 4. Entrée par défaut de WASAPI ou DirectSound
            for api in host_apis:
                def_in = api.get("default_input_device", -1)
                if def_in >= 0:
                    try:
                        d = sd.query_devices(def_in)
                        if d.get("max_input_channels", 0) > 0:
                            return def_in
                    except Exception:
                        pass

            # 5. Repli : rechercher un microphone dans les périphériques
            for idx, d in enumerate(devices):
                if d.get("max_input_channels", 0) > 0:
                    dev_name = d.get("name", "").lower()
                    if any(m in dev_name for m in ("micro", "rift", "headset", "casque")):
                        return idx

            # 6. Repli ultime : n'importe quel périphérique avec entrée active
            for idx, d in enumerate(devices):
                if d.get("max_input_channels", 0) > 0:
                    return idx

            return None
        except Exception:
            return None

    def get_optimal_sample_rate(self, device_index: Optional[int], is_input: bool = False) -> int:
        """
        Détecte et valide automatiquement la fréquence d'échantillonnage compatible avec le matériel,
        sans émettre de son ni provoquer d'erreur PortAudio.
        """
        if not HAS_SOUNDDEVICE or device_index is None or device_index < 0:
            return 44100
        try:
            d = sd.query_devices(device_index)
            native_sr = int(d.get("default_samplerate", 44100))
            candidates = [native_sr, 44100, 48000, 96000, 88200]
            seen = set()
            ordered_candidates = [x for x in candidates if not (x in seen or seen.add(x))]

            check_func = sd.check_input_settings if is_input else sd.check_output_settings
            for candidate in ordered_candidates:
                try:
                    check_func(device=device_index, samplerate=candidate)
                    return candidate
                except Exception:
                    continue
            return native_sr
        except Exception:
            return 44100

    def get_optimal_buffer_size(self, device_index: Optional[int], sample_rate: int = 44100) -> int:
        """Détermine la taille de buffer optimale selon le pilote (faible latence stable)."""
        if not HAS_SOUNDDEVICE or device_index is None or device_index < 0:
            return 512
        try:
            d = sd.query_devices(device_index)
            host_api_idx = d.get("hostapi", 0)
            api_info = sd.query_hostapis(host_api_idx)
            api_name = api_info.get("name", "").lower()

            # Pilotes faible latence (ASIO, WASAPI) : 256 ou 512 échantillons
            if "asio" in api_name:
                return 256
            elif "wasapi" in api_name:
                low_lat = d.get("default_low_output_latency", 0.01)
                return 256 if low_lat <= 0.005 else 512
            else:
                return 512
        except Exception:
            return 512

    def auto_configure_audio(self, force: bool = False) -> Dict[str, Any]:
        """
        Configure automatiquement et universellement l'environnement audio de l'utilisateur :
        - Détection du meilleur périphérique de sortie
        - Détection du meilleur microphone/entrée
        - Négociation de la fréquence native (44.1kHz, 48kHz, etc.)
        - Calcul du buffer optimal faible latence
        - Sauvegarde automatique dans settings.json
        """
        out_idx = self.get_default_output_device_index()
        in_idx = self.get_default_input_device_index()

        out_name = None
        host_api_name = "Windows WASAPI"
        if out_idx is not None and HAS_SOUNDDEVICE:
            try:
                d = sd.query_devices(out_idx)
                out_name = d.get("name")
                api_info = sd.query_hostapis(d.get("hostapi", 0))
                host_api_name = api_info.get("name", host_api_name)
            except Exception:
                pass

        in_name = None
        if in_idx is not None and HAS_SOUNDDEVICE:
            try:
                d_in = sd.query_devices(in_idx)
                in_name = d_in.get("name")
            except Exception:
                pass

        sample_rate = self.get_optimal_sample_rate(out_idx, is_input=False)
        buffer_size = self.get_optimal_buffer_size(out_idx, sample_rate)

        audio_cfg = self.settings.setdefault("audio", {})
        audio_cfg["host_api_name"] = host_api_name
        audio_cfg["output_device_name"] = out_name
        audio_cfg["output_device_index"] = out_idx
        audio_cfg["input_device_name"] = in_name
        audio_cfg["input_device_index"] = in_idx
        audio_cfg["sample_rate"] = sample_rate
        audio_cfg["buffer_size"] = buffer_size

        self.save_settings()
        return audio_cfg


# Instance globale
hardware_manager = HardwareManager()
