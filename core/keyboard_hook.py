"""
core/keyboard_hook.py - Hook clavier bas niveau Windows (WH_KEYBOARD_LL) pour NovaDAW.
Permet au Clavier Musical Virtuel (Musical Typing) de capturer les frappes du clavier d'ordinateur
(touches A-K, W-O, etc.) et les raccourcis (Ctrl+K) en temps réel, même lorsqu'une fenêtre externe
(ex: éditeur VST natif Omnisphere, Serum, Kontakt) possède le focus système Win32.
"""
import ctypes
import os
import sys
import threading
import time
from ctypes import wintypes
from typing import Callable, Optional, Set

# Codes de messages Win32
WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_QUIT = 0x0012
GA_ROOT = 2

# Codes Qt.Key correspondants
KEY_UP = 0x01000013
KEY_DOWN = 0x01000015
KEY_LEFT = 0x01000012
KEY_RIGHT = 0x01000014
KEY_SEMICOLON = 0x3B
KEY_APOSTROPHE = 0x27
KEY_BRACKETLEFT = 0x5B
KEY_BRACKETRIGHT = 0x5D
KEY_COMMA = 0x2C
KEY_PERIOD = 0x2E
KEY_SLASH = 0x2F
KEY_SPACE = 0x20

# Ensemble des touches gérées par Musical Typing (notes + contrôles octave / vélocité)
MUSICAL_QT_KEYS: Set[int] = {
    # Notes de base (Home row / Do-Si)
    0x41,  # A (Do)
    0x51,  # Q (Do - AZERTY)
    0x57,  # W (Do#)
    0x5A,  # Z (Do# - AZERTY / Octave Down)
    0x53,  # S (Ré)
    0x45,  # E (Ré#)
    0x44,  # D (Mi)
    0x46,  # F (Fa)
    0x54,  # T (Fa#)
    0x47,  # G (Sol)
    0x59,  # Y (Sol#)
    0x48,  # H (La)
    0x55,  # U (La#)
    0x4A,  # J (Si)
    # Notes octave supérieure
    0x4B,  # K (Do +1)
    0x4F,  # O (Do# +1)
    0x4C,  # L (Ré +1)
    0x50,  # P (Ré# +1)
    KEY_SEMICOLON,   # ; (Mi +1)
    0x4D,            # M (Mi +1 AZERTY)
    KEY_APOSTROPHE,  # ' (Fa +1)
    # Contrôles d'octave et de vélocité
    0x58,  # X (Octave +)
    0x43,  # C (Vélocité -)
    0x56,  # V (Vélocité +)
    KEY_BRACKETLEFT,   # [ (Octave -)
    KEY_BRACKETRIGHT,  # ] (Octave +)
    KEY_UP,            # Up (Octave +)
    KEY_DOWN,          # Down (Octave -)
}


def win_vk_to_qt_key(vk: int) -> int:
    """Convertit un code de touche virtuelle Windows (VK_*) en code Qt.Key équivalent."""
    # Lettres A-Z (0x41 à 0x5A)
    if 0x41 <= vk <= 0x5A:
        return vk
    # Chiffres 0-9 (0x30 à 0x39)
    if 0x30 <= vk <= 0x39:
        return vk
    vk_map = {
        0xBA: KEY_SEMICOLON,    # VK_OEM_1 (;:)
        0xDE: KEY_APOSTROPHE,   # VK_OEM_7 ('")
        0xDB: KEY_BRACKETLEFT,  # VK_OEM_4 ([{)
        0xDD: KEY_BRACKETRIGHT, # VK_OEM_6 (]})
        0xBC: KEY_COMMA,        # VK_OEM_COMMA (<,)
        0xBE: KEY_PERIOD,       # VK_OEM_PERIOD (>.)
        0xBF: KEY_SLASH,        # VK_OEM_2 (?/)
        0x26: KEY_UP,           # VK_UP
        0x28: KEY_DOWN,         # VK_DOWN
        0x25: KEY_LEFT,         # VK_LEFT
        0x27: KEY_RIGHT,        # VK_RIGHT
        0x20: KEY_SPACE,        # VK_SPACE
    }
    return vk_map.get(vk, vk)


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class LowLevelKeyboardHook:
    """
    Gestionnaire singleton du hook Windows bas niveau WH_KEYBOARD_LL.
    S'exécute sur un thread dédié ultra-léger avec une boucle de messages Win32 native
    pour garantir une latence minimale (< 1 ms) sans jamais impacter le thread UI de Qt.
    """
    _instance: Optional['LowLevelKeyboardHook'] = None

    def __init__(self):
        self._lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None
        self._thread_id: Optional[int] = None
        self._hook_handle = None
        self._hook_proc_ref = None

        self._is_active: bool = False
        self._text_editing_focused: bool = False
        self._held_vks: Set[int] = set()

        self._key_callback: Optional[Callable[[int, bool], None]] = None
        self._toggle_callback: Optional[Callable[[], None]] = None
        self._window_focus_callback: Optional[Callable[[int], None]] = None
        self._last_fg_hwnd: int = 0

        self._init_win32_api()

    @classmethod
    def get_instance(cls) -> 'LowLevelKeyboardHook':
        if cls._instance is None:
            cls._instance = LowLevelKeyboardHook()
        return cls._instance

    def _init_win32_api(self):
        if sys.platform != "win32":
            return
        try:
            self._user32 = ctypes.windll.user32
            self._kernel32 = ctypes.windll.kernel32

            self._kernel32.GetModuleHandleW.restype = ctypes.c_void_p
            self._kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]

            self._user32.SetWindowsHookExW.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD]
            self._user32.SetWindowsHookExW.restype = ctypes.c_void_p

            self._user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
            self._user32.UnhookWindowsHookEx.restype = wintypes.BOOL

            self._user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
            self._user32.CallNextHookEx.restype = ctypes.c_ssize_t

            self._user32.GetForegroundWindow.argtypes = []
            self._user32.GetForegroundWindow.restype = wintypes.HWND

            self._user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
            self._user32.GetWindowThreadProcessId.restype = wintypes.DWORD

            self._user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
            self._user32.GetWindowTextW.restype = ctypes.c_int

            self._user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
            self._user32.GetWindowTextLengthW.restype = ctypes.c_int

            self._user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
            self._user32.GetAncestor.restype = wintypes.HWND

            self._user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
            self._user32.GetAsyncKeyState.restype = ctypes.c_short

            self._user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            self._user32.PostThreadMessageW.restype = wintypes.BOOL

            self._HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
        except Exception as e:
            print(f"[LowLevelKeyboardHook] Erreur initialisation Win32 API : {e}")

    def is_novadaw_or_plugin_window(self, hwnd: int) -> bool:
        """Détermine si la fenêtre spécifiée appartient à NovaDAW ou à l'un de ses plugins VST externes."""
        if not hwnd or sys.platform != "win32":
            return False
        try:
            fg_pid = wintypes.DWORD()
            self._user32.GetWindowThreadProcessId(hwnd, ctypes.byref(fg_pid))
            pid = fg_pid.value

            # 1. Processus principal de NovaDAW
            if pid == os.getpid():
                return True

            # 2. Processus enfants isolés de plugins VST (Omnisphere, Serum, etc.)
            try:
                from core.vst_host import get_active_plugin_pids
                if pid in get_active_plugin_pids():
                    return True
            except Exception:
                pass

            try:
                from core.plugin_manager import global_plugin_manager
                if pid in getattr(global_plugin_manager, "track_id_by_pid", {}):
                    return True
            except Exception:
                pass

            # 3. Vérification du titre de la fenêtre et de sa fenêtre racine parente
            for h in (hwnd, self._user32.GetAncestor(hwnd, GA_ROOT)):
                if not h:
                    continue
                length = self._user32.GetWindowTextLengthW(h)
                if length > 0:
                    buf = ctypes.create_unicode_buffer(length + 1)
                    self._user32.GetWindowTextW(h, buf, length + 1)
                    title = buf.value.lower()
                    if "novadaw" in title or "omnisphere" in title:
                        return True
        except Exception:
            pass
        return False

    def _hook_procedure(self, nCode, wParam, lParam):
        """Procédure de rappel appelée par Windows pour chaque événement clavier du système."""
        if nCode < 0:
            return self._user32.CallNextHookEx(None, nCode, wParam, lParam)

        # On ne traite que les messages de touche enfoncée ou relâchée
        if wParam not in (WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP):
            return self._user32.CallNextHookEx(None, nCode, wParam, lParam)

        try:
            kb = KBDLLHOOKSTRUCT.from_address(lParam)
            vk = int(kb.vkCode)
            is_down = wParam in (WM_KEYDOWN, WM_SYSKEYDOWN)
            is_up = wParam in (WM_KEYUP, WM_SYSKEYUP)

            fg_hwnd = self._user32.GetForegroundWindow()

            # Détecter le changement de fenêtre active pour synchroniser la piste du plugin
            if fg_hwnd and fg_hwnd != self._last_fg_hwnd:
                self._last_fg_hwnd = fg_hwnd
                if self._window_focus_callback:
                    try:
                        self._window_focus_callback(fg_hwnd)
                    except Exception:
                        pass

            # Si la fenêtre au premier plan n'est ni NovaDAW ni un plugin NovaDAW, ne rien intercepter
            if not self.is_novadaw_or_plugin_window(fg_hwnd):
                return self._user32.CallNextHookEx(None, nCode, wParam, lParam)

            # Vérifier le raccourci global Ctrl+K pour basculer Musical Typing
            is_ctrl = bool(self._user32.GetAsyncKeyState(0x11) & 0x8000)  # VK_CONTROL
            if vk == 0x4B and is_ctrl:  # Touche 'K' + Ctrl
                if is_down:
                    if vk not in self._held_vks:
                        self._held_vks.add(vk)
                        if self._toggle_callback:
                            try:
                                self._toggle_callback()
                            except Exception:
                                pass
                elif is_up:
                    self._held_vks.discard(vk)
                return 1  # Intercepter et consommer

            # Si le mode Musical Typing n'est pas activé, laisser passer normalement
            if not self._is_active:
                return self._user32.CallNextHookEx(None, nCode, wParam, lParam)

            # Si un champ de saisie de texte dans NovaDAW est actif (renommage de piste, tempo...), laisser passer
            if self._text_editing_focused:
                return self._user32.CallNextHookEx(None, nCode, wParam, lParam)

            # Laisser passer les combinaisons avec Ctrl ou Alt (Ctrl+Z, Ctrl+S, Alt+F4, etc.)
            is_alt = bool(self._user32.GetAsyncKeyState(0x12) & 0x8000)   # VK_MENU
            if is_ctrl or is_alt:
                return self._user32.CallNextHookEx(None, nCode, wParam, lParam)

            # Convertir le code de touche Windows vers le code Qt équivalent
            qt_key = win_vk_to_qt_key(vk)

            # Si ce n'est pas une touche de note ou de contrôle musical, laisser passer (ex: Espace, Entrée, Esc)
            if qt_key not in MUSICAL_QT_KEYS:
                return self._user32.CallNextHookEx(None, nCode, wParam, lParam)

            # Traitement de la touche musicale (Note ou Contrôle)
            if is_down:
                # Éviter l'auto-répétition des touches sous Windows
                if vk in self._held_vks:
                    return 1
                self._held_vks.add(vk)
                if self._key_callback:
                    try:
                        self._key_callback(qt_key, True)
                    except Exception:
                        pass
            elif is_up:
                self._held_vks.discard(vk)
                if self._key_callback:
                    try:
                        self._key_callback(qt_key, False)
                    except Exception:
                        pass

            # Consommer l'événement pour que le plugin externe ne reçoive pas de raccourcis indésirables
            return 1

        except Exception:
            return self._user32.CallNextHookEx(None, nCode, wParam, lParam)

    def _worker_loop(self, ready_event: threading.Event):
        """Boucle de message Windows native sur le thread dédié."""
        self._thread_id = self._kernel32.GetCurrentThreadId()

        # Forcer la création de la file de messages du thread
        msg = wintypes.MSG()
        self._user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)

        self._hook_proc_ref = self._HOOKPROC(self._hook_procedure)
        self._hook_handle = self._user32.SetWindowsHookExW(
            WH_KEYBOARD_LL,
            ctypes.cast(self._hook_proc_ref, ctypes.c_void_p),
            0,
            0
        )
        ready_event.set()

        if not self._hook_handle:
            err = self._kernel32.GetLastError()
            print(f"[LowLevelKeyboardHook] Avertissement : échec d'installation du hook bas niveau (code {err})")
            return

        # Boucle de messages Win32 standard
        while self._user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            self._user32.TranslateMessage(ctypes.byref(msg))
            self._user32.DispatchMessageW(ctypes.byref(msg))

        if self._hook_handle:
            try:
                self._user32.UnhookWindowsHookEx(self._hook_handle)
            except Exception:
                pass
            self._hook_handle = None

    def start(self, key_callback: Callable[[int, bool], None],
              toggle_callback: Callable[[], None],
              window_focus_callback: Optional[Callable[[int], None]] = None):
        """Démarre le thread de surveillance du hook bas niveau."""
        with self._lock:
            self._key_callback = key_callback
            self._toggle_callback = toggle_callback
            self._window_focus_callback = window_focus_callback

            if sys.platform != "win32":
                return

            if self._thread is not None and self._thread.is_alive():
                return

            ready_event = threading.Event()
            self._thread = threading.Thread(
                target=self._worker_loop,
                args=(ready_event,),
                daemon=True,
                name="NovaDAW-LowLevelKeyboardHook"
            )
            self._thread.start()
            ready_event.wait(timeout=1.5)

    def stop(self):
        """Arrête proprement le hook et le thread de boucle de messages."""
        with self._lock:
            self._is_active = False
            self._held_vks.clear()
            if sys.platform == "win32" and self._thread_id:
                try:
                    self._user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
                except Exception:
                    pass
            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=1.0)
            self._thread = None
            self._thread_id = None
            self._hook_handle = None

    def set_active(self, active: bool):
        """Active ou désactive la prise en charge des notes musicales par le hook."""
        self._is_active = bool(active)
        if not self._is_active:
            self._held_vks.clear()

    def set_text_editing_focused(self, focused: bool):
        """Indique si un widget d'édition de texte (ex: QLineEdit) possède le focus."""
        self._text_editing_focused = bool(focused)


# Fonctions d'accès public
def start_global_keyboard_hook(key_callback: Callable[[int, bool], None],
                               toggle_callback: Callable[[], None],
                               window_focus_callback: Optional[Callable[[int], None]] = None):
    LowLevelKeyboardHook.get_instance().start(key_callback, toggle_callback, window_focus_callback)


def stop_global_keyboard_hook():
    LowLevelKeyboardHook.get_instance().stop()


def set_global_hook_active(active: bool):
    LowLevelKeyboardHook.get_instance().set_active(active)


def set_text_editing_focused(focused: bool):
    LowLevelKeyboardHook.get_instance().set_text_editing_focused(focused)
