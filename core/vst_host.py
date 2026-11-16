"""Crash-isolated Universal VST3 & VST2 (64-bit & 32-bit) host. Native editors run on the child's main thread."""
import ctypes
from ctypes import (
    c_int32, c_int16, c_uint32, c_ssize_t, c_size_t, c_void_p,
    c_float, c_double, c_char, c_ubyte, c_wchar_p, POINTER, Structure, byref
)
import multiprocessing
from multiprocessing import shared_memory
import os
import struct
import subprocess
import sys
import threading
import time
from typing import Optional
import numpy as np


_MAX_SHM_FRAMES = 16384
_MAX_SHM_CHANNELS = 2
_MAX_SHM_MIDI = 128
_SHM_HDR_FMT = "<iiiiidii"  # op, status, num_frames, num_channels, num_midi, sample_rate, reset_flag, err_len
_SHM_HDR_STRUCT = struct.Struct(_SHM_HDR_FMT)
_SHM_MIDI_STRUCT = struct.Struct("<iBBBB")  # sample_idx, b0, b1, b2, pad
_SHM_HEADER_SIZE = 64
_SHM_MIDI_OFFSET = _SHM_HEADER_SIZE
_SHM_MIDI_BYTES = _MAX_SHM_MIDI * _SHM_MIDI_STRUCT.size
_SHM_IN_OFFSET = _SHM_MIDI_OFFSET + _SHM_MIDI_BYTES
_SHM_AUDIO_BYTES = _MAX_SHM_CHANNELS * _MAX_SHM_FRAMES * 4
_SHM_OUT_OFFSET = _SHM_IN_OFFSET + _SHM_AUDIO_BYTES
_SHM_ERR_OFFSET = _SHM_OUT_OFFSET + _SHM_AUDIO_BYTES
_SHM_ERR_SIZE = 512
_SHM_TOTAL_SIZE = _SHM_ERR_OFFSET + _SHM_ERR_SIZE

_SHM_OP_IDLE = 0
_SHM_OP_RENDER_MIDI = 1
_SHM_OP_RENDER_AUDIO = 2
_SHM_OP_QUIT = 3

_SHM_STATUS_PENDING = 0
_SHM_STATUS_OK = 1
_SHM_STATUS_ERROR = 2


def _get_pe_machine(file_path: str) -> int:
    """Return the PE COFF Machine field (0x8664 = x64, 0x014c = x86, 0 = unknown)."""
    try:
        with open(file_path, "rb") as f:
            mz = f.read(64)
            if len(mz) < 64 or mz[:2] != b"MZ":
                return 0
            pe_offset = struct.unpack("<I", mz[60:64])[0]
            f.seek(pe_offset)
            pe_hdr = f.read(6)
            if len(pe_hdr) < 6 or pe_hdr[:4] != b"PE\x00\x00":
                return 0
            return struct.unpack("<H", pe_hdr[4:6])[0]
    except Exception:
        return 0


def _apply_multibus_vst_fix(plugin):
    """
    Fix for multi-output VST3 instruments (e.g. Kontakt, SampleTank) in Pedalboard.
    Pedalboard allocates only 2 channel pointers in renderMIDIMessages,
    causing JUCE's processBlock to pass out-of-bounds stack pointers for plugins
    expecting > 2 channels, triggering 0xC0000005 (Access Violation).
    This installs a transparent shadow vtable hook intercepting processBlock and expanding
    channel pointers to pluginInstance->getTotalNumOutputChannels() with scratch dummy buffers.
    """
    try:
        class _PybindInstance(ctypes.Structure):
            _fields_ = [
                ('ob_refcnt', ctypes.c_ssize_t),
                ('ob_type', c_void_p),
                ('value', c_void_p),
            ]

        inst = _PybindInstance.from_address(id(plugin))
        cpp_obj = inst.value
        if not cpp_obj:
            return

        plugin_inst_ptr = c_void_p.from_address(cpp_obj + 0xf0).value
        if not plugin_inst_ptr:
            return

        total_out = c_int32.from_address(plugin_inst_ptr + 0x10c).value
        if total_out <= 2:
            return

        vtable_ptr = c_void_p.from_address(plugin_inst_ptr).value
        if not vtable_ptr:
            return

        original_process_block_addr = c_void_p.from_address(vtable_ptr + 0x38).value
        if not original_process_block_addr:
            return

        PROCESS_BLOCK_PROTO = ctypes.CFUNCTYPE(None, c_void_p, c_void_p, c_void_p)
        original_process_block = PROCESS_BLOCK_PROTO(original_process_block_addr)

        MAX_SAMPLES = 65536
        dummy_float_buffer = (ctypes.c_float * MAX_SAMPLES)()
        dummy_float_ptr = ctypes.addressof(dummy_float_buffer)

        chan_ptrs_count = max(128, total_out + 16)
        chan_pointers = (c_void_p * chan_ptrs_count)()
        for i in range(chan_ptrs_count):
            chan_pointers[i] = dummy_float_ptr
        chan_pointers_addr = ctypes.addressof(chan_pointers)

        def hooked_process_block(this_ptr, audio_buf_ptr, midi_buf_ptr):
            p_num_channels = c_int32.from_address(audio_buf_ptr + 0)
            orig_num_channels = p_num_channels.value
            p_channels = c_void_p.from_address(audio_buf_ptr + 16)
            orig_channels = p_channels.value

            if orig_num_channels < total_out and orig_channels:
                orig_ptrs = (c_void_p * orig_num_channels).from_address(orig_channels)
                for c in range(orig_num_channels):
                    chan_pointers[c] = orig_ptrs[c]
                for c in range(orig_num_channels, total_out):
                    chan_pointers[c] = dummy_float_ptr

                p_num_channels.value = total_out
                p_channels.value = chan_pointers_addr
                try:
                    original_process_block(this_ptr, audio_buf_ptr, midi_buf_ptr)
                finally:
                    p_num_channels.value = orig_num_channels
                    p_channels.value = orig_channels
            else:
                original_process_block(this_ptr, audio_buf_ptr, midi_buf_ptr)

        callback_ref = PROCESS_BLOCK_PROTO(hooked_process_block)

        VTABLE_SIZE = 200
        new_vtable = (c_void_p * VTABLE_SIZE)()
        orig_vtable_entries = (c_void_p * VTABLE_SIZE).from_address(vtable_ptr)
        for i in range(VTABLE_SIZE):
            new_vtable[i] = orig_vtable_entries[i]

        new_vtable[7] = ctypes.cast(callback_ref, c_void_p).value
        c_void_p.from_address(plugin_inst_ptr).value = ctypes.addressof(new_vtable)

        plugin._multibus_fix_refs = (new_vtable, callback_ref, dummy_float_buffer, chan_pointers)
    except Exception:
        pass


# ============================================================================
# Native 64-bit VST 2.4 C-ABI Host (ctypes)
# ============================================================================

class _AEffect(Structure):
    pass


class _ERect(Structure):
    _fields_ = [
        ("top", c_int16),
        ("left", c_int16),
        ("bottom", c_int16),
        ("right", c_int16),
    ]


class _VstMidiEvent(Structure):
    _fields_ = [
        ("type", c_int32),
        ("byteSize", c_int32),
        ("deltaFrames", c_int32),
        ("flags", c_int32),
        ("noteLength", c_int32),
        ("noteOffset", c_int32),
        ("midiData", c_ubyte * 4),
        ("detune", c_char),
        ("noteOffVelocity", c_char),
        ("reserved1", c_char),
        ("reserved2", c_char),
    ]


class _VstTimeInfo(Structure):
    _fields_ = [
        ("samplePos", c_double),
        ("sampleRate", c_double),
        ("nanoSeconds", c_double),
        ("ppqPos", c_double),
        ("tempo", c_double),
        ("barStartPos", c_double),
        ("cycleStartPos", c_double),
        ("cycleEndPos", c_double),
        ("timeSigNumerator", c_int32),
        ("timeSigDenominator", c_int32),
        ("smpteOffset", c_int32),
        ("smpteFrameRate", c_int32),
        ("samplesToNextClock", c_int32),
        ("flags", c_int32),
    ]


_AudioMasterProc = ctypes.CFUNCTYPE(c_ssize_t, POINTER(_AEffect), c_int32, c_int32, c_ssize_t, c_void_p, c_float)
_DispatcherProc = ctypes.CFUNCTYPE(c_ssize_t, POINTER(_AEffect), c_int32, c_int32, c_ssize_t, c_void_p, c_float)
_ProcessProc = ctypes.CFUNCTYPE(None, POINTER(_AEffect), POINTER(POINTER(c_float)), POINTER(POINTER(c_float)), c_int32)
_ProcessDoubleProc = ctypes.CFUNCTYPE(None, POINTER(_AEffect), POINTER(POINTER(c_double)), POINTER(POINTER(c_double)), c_int32)
_SetParamProc = ctypes.CFUNCTYPE(None, POINTER(_AEffect), c_int32, c_float)
_GetParamProc = ctypes.CFUNCTYPE(c_float, POINTER(_AEffect), c_int32)

_AEffect._fields_ = [
    ("magic", c_int32),
    ("dispatcher", _DispatcherProc),
    ("process", _ProcessProc),
    ("setParameter", _SetParamProc),
    ("getParameter", _GetParamProc),
    ("numPrograms", c_int32),
    ("numParams", c_int32),
    ("numInputs", c_int32),
    ("numOutputs", c_int32),
    ("flags", c_int32),
    ("resvd1", c_ssize_t),
    ("resvd2", c_ssize_t),
    ("initialDelay", c_int32),
    ("realQualities", c_int32),
    ("offQualities", c_int32),
    ("ioRatio", c_float),
    ("object", c_void_p),
    ("user", c_void_p),
    ("uniqueID", c_int32),
    ("version", c_int32),
    ("processReplacing", _ProcessProc),
    ("processDoubleReplacing", _ProcessDoubleProc),
    ("future", c_char * 56),
]

_VSTMainProto = ctypes.CFUNCTYPE(POINTER(_AEffect), _AudioMasterProc)


def _normalize_midi_messages(midi_messages, sample_rate: float):
    """Convert [(bytes, timestamp_sec), ...] into sorted [(sample_offset, b0, b1, b2), ...]."""
    parsed = []
    if not midi_messages:
        return parsed
    for item in midi_messages:
        if isinstance(item, (tuple, list)) and len(item) >= 2:
            raw_bytes, ts = item[0], float(item[1])
        else:
            raw_bytes = getattr(item, "data", b"")
            ts = float(getattr(item, "timestamp", 0.0))
        if not raw_bytes:
            continue
        b = bytes(raw_bytes)
        b0 = b[0] if len(b) > 0 else 0
        b1 = b[1] if len(b) > 1 else 0
        b2 = b[2] if len(b) > 2 else 0
        sample_idx = max(0, int(round(ts * sample_rate)))
        parsed.append((sample_idx, b0, b1, b2))
    parsed.sort(key=lambda x: x[0])
    return parsed


_ACTIVE_PLUGIN_PIDS: set = set()

def register_plugin_pid(pid: int):
    """Enregistre le PID d'un processus de plugin VST externe."""
    if pid:
        _ACTIVE_PLUGIN_PIDS.add(int(pid))

def unregister_plugin_pid(pid: int):
    """Retire le PID d'un processus de plugin VST externe."""
    if pid:
        _ACTIVE_PLUGIN_PIDS.discard(int(pid))

def get_active_plugin_pids() -> set:
    """Retourne l'ensemble des PIDs actifs des plugins VST hébergés."""
    return set(_ACTIVE_PLUGIN_PIDS)


_global_vst_key_callback = None

def register_vst_key_callback(callback):
    """Enregistre un callback pour relayer les frappes clavier (WM_KEYDOWN/WM_KEYUP) reçues par les fenêtres VST."""
    global _global_vst_key_callback
    _global_vst_key_callback = callback

def unregister_vst_key_callback():
    global _global_vst_key_callback
    _global_vst_key_callback = None


class VST2Plugin64:
    """Native 64-bit VST 2.4 Host wrapper compatible with Pedalboard's plugin API."""

    def __init__(self, path: str, probe_only: bool = False):
        self.path = os.path.normpath(path)
        self._lock = threading.RLock()
        self._sample_rate = 44100.0
        self._block_size = 512
        self._editor_hwnd = None
        self._window_closed_by_user = False

        self._in_process = False
        self._time_info = _VstTimeInfo(
            samplePos=0.0,
            sampleRate=self._sample_rate,
            nanoSeconds=0.0,
            ppqPos=0.0,
            tempo=120.0,
            barStartPos=0.0,
            cycleStartPos=0.0,
            cycleEndPos=16.0,
            timeSigNumerator=4,
            timeSigDenominator=4,
            smpteOffset=0,
            smpteFrameRate=0,
            samplesToNextClock=0,
            flags=(1 << 1) | (1 << 9) | (1 << 10) | (1 << 11) | (1 << 13),
        )
        self._time_info_ptr = ctypes.addressof(self._time_info)

        def _host_cb(effect, opcode, index, value, ptr, opt):
            if opcode == 1:  # audioMasterVersion
                return 2400
            if opcode == 3:  # audioMasterIdle
                return 1
            if opcode == 7:  # audioMasterGetTime
                self._time_info.sampleRate = float(self._sample_rate)
                return self._time_info_ptr
            if opcode == 8:  # audioMasterProcessEvents
                return 1
            if opcode == 15:  # audioMasterSizeWindow
                self._resize_editor_window(int(index), int(value))
                return 1
            if opcode == 16:  # audioMasterGetSampleRate
                return int(self._sample_rate)
            if opcode == 17:  # audioMasterGetBlockSize
                return int(self._block_size)
            if opcode == 23:  # audioMasterGetCurrentProcessLevel
                return 2 if self._in_process else 1
            if opcode == 24:  # audioMasterGetAutomationState
                return 1
            if opcode in (32, 33) and ptr:  # Vendor / Product string
                ctypes.memmove(ptr, b"NovaDAW\x00", 8)
                return 1
            if opcode == 34:  # VendorVersion
                return 1000
            if opcode == 37:  # audioMasterCanDo
                return 1
            if opcode == 38:  # audioMasterGetLanguage
                return 1
            if opcode == 42:  # audioMasterUpdateDisplay
                return 1
            return 0

        self._host_cb_ref = _AudioMasterProc(_host_cb)

        if sys.platform == "win32":
            try:
                ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002 | 0x8000)
                dll_dir = os.path.dirname(self.path)
                if dll_dir:
                    ctypes.windll.kernel32.SetDllDirectoryW(dll_dir)
            except Exception:
                pass

        self._dll = ctypes.CDLL(self.path)
        fn_addr = None
        for export_name in ("VSTPluginMain", "main", "MAIN"):
            if hasattr(self._dll, export_name):
                fn_addr = ctypes.cast(getattr(self._dll, export_name), c_void_p).value
                break
        if not fn_addr:
            raise RuntimeError(f"Point d'entrée VSTPluginMain introuvable dans {os.path.basename(self.path)}")

        main_fn = _VSTMainProto(fn_addr)
        self._eff_ptr = main_fn(self._host_cb_ref)
        if not self._eff_ptr:
            raise RuntimeError(f"Initialisation VST2 échouée (AEffect NULL) pour {os.path.basename(self.path)}")

        eff = self._eff_ptr.contents
        if eff.magic != 0x56737450:  # 'VstP'
            raise RuntimeError(f"Signature VST2 invalide ({hex(eff.magic)}) dans {os.path.basename(self.path)}")

        base_name = os.path.splitext(os.path.basename(self.path))[0]
        self.is_instrument = bool(eff.flags & (1 << 8))  # effFlagsIsSynth
        self.is_effect = not self.is_instrument
        self.num_inputs = max(0, int(eff.numInputs))
        self.num_outputs = max(2, int(eff.numOutputs))
        self.parameters_count = max(0, int(eff.numParams))
        self.parameters = {str(i): i for i in range(self.parameters_count)}
        self.name = base_name

        self._scratch_capacity = 0
        self._scratch_n_in = 0
        self._scratch_n_out = 0
        self._c_in_bufs = None
        self._c_out_bufs = None
        self._c_in_ptrs = None
        self._c_out_ptrs = None
        self._in_views = None
        self._out_views = None
        self._max_vst_events = 128
        self._midi_ev_arr = (_VstMidiEvent * self._max_vst_events)()
        for i in range(self._max_vst_events):
            ev = self._midi_ev_arr[i]
            ev.type = 1
            ev.byteSize = ctypes.sizeof(_VstMidiEvent)
            ev.flags = 1
            ev.noteLength = 0
            ev.noteOffset = 0
            ev.midiData[3] = 0

        class _VstEventsFixed(Structure):
            _fields_ = [
                ("numEvents", c_int32),
                ("reserved", c_ssize_t),
                ("events", c_void_p * self._max_vst_events),
            ]

        self._vst_events_cls = _VstEventsFixed
        self._vst_events = _VstEventsFixed(numEvents=0, reserved=0)
        for i in range(self._max_vst_events):
            self._vst_events.events[i] = ctypes.addressof(self._midi_ev_arr[i])

        if not probe_only:
            eff.dispatcher(self._eff_ptr, 0, 0, 0, None, 0.0)  # effOpen
            eff.dispatcher(self._eff_ptr, 10, 0, 0, None, float(self._sample_rate))  # effSetSampleRate
            eff.dispatcher(self._eff_ptr, 11, 0, int(self._block_size), None, 0.0)  # effSetBlockSize
            eff.dispatcher(self._eff_ptr, 12, 0, 1, None, 0.0)  # effMainsChanged(1)
            eff.dispatcher(self._eff_ptr, 71, 0, 0, None, 0.0)  # effStartProcess

            name_buf = ctypes.create_string_buffer(128)
            eff.dispatcher(self._eff_ptr, 45, 0, 0, name_buf, 0.0)  # effGetEffectName
            eff_name = name_buf.value.decode("utf-8", errors="ignore").strip()
            if not eff_name:
                eff.dispatcher(self._eff_ptr, 48, 0, 0, name_buf, 0.0)  # effGetProductString
                eff_name = name_buf.value.decode("utf-8", errors="ignore").strip()
            if eff_name:
                self.name = eff_name

            self._ensure_scratch_buffers(max(2, self.num_inputs), max(2, self.num_outputs), 8192)

            # Warmup default program, chunk & initial audio blocks so synths (e.g. Omnisphere, FM8, Absynth 5)
            # complete their lazy STEAM/patch initialization before the first user MIDI note arrives.
            try:
                eff.dispatcher(self._eff_ptr, 2, 0, 0, None, 0.0)  # effSetProgram(0)
                er_warm = POINTER(_ERect)()
                eff.dispatcher(self._eff_ptr, 13, 0, 0, byref(er_warm), 0.0)  # effEditGetRect
                if eff.flags & (1 << 5):
                    c_warm = c_void_p()
                    eff.dispatcher(self._eff_ptr, 23, 0, 0, byref(c_warm), 0.0)  # effGetChunk
                self._flush_silent_blocks(6)
            except Exception:
                pass

    def _ensure_scratch_buffers(self, n_in: int, n_out: int, capacity: int):
        """Pre-allocate C float buffers and numpy views to avoid per-block heap allocations."""
        if (
            self._c_in_bufs is not None
            and n_in <= self._scratch_n_in
            and n_out <= self._scratch_n_out
            and capacity <= self._scratch_capacity
        ):
            return
        new_in = max(n_in, self._scratch_n_in, 2)
        new_out = max(n_out, self._scratch_n_out, 2)
        new_cap = max(capacity, self._scratch_capacity, 8192)
        self._c_in_bufs = [(c_float * new_cap)() for _ in range(new_in)]
        self._c_out_bufs = [(c_float * new_cap)() for _ in range(new_out)]
        self._c_in_ptrs = (POINTER(c_float) * new_in)(*[ctypes.cast(b, POINTER(c_float)) for b in self._c_in_bufs])
        self._c_out_ptrs = (POINTER(c_float) * new_out)(*[ctypes.cast(b, POINTER(c_float)) for b in self._c_out_bufs])
        self._in_views = [np.ctypeslib.as_array(b) for b in self._c_in_bufs]
        self._out_views = [np.ctypeslib.as_array(b) for b in self._c_out_bufs]
        self._scratch_n_in = new_in
        self._scratch_n_out = new_out
        self._scratch_capacity = new_cap

    def _flush_silent_blocks(self, num_blocks: int = 4):
        """Run silent processReplacing blocks so lazy-init synths (Omnisphere STEAM) finish internal voice resets."""
        try:
            eff = self._eff_ptr.contents
            n_in = max(2, self.num_inputs)
            n_out = max(2, self.num_outputs)
            bs = self._block_size
            in_bufs = [(c_float * bs)() for _ in range(n_in)]
            out_bufs = [(c_float * bs)() for _ in range(n_out)]
            in_ptrs = (POINTER(c_float) * n_in)(*[ctypes.cast(b, POINTER(c_float)) for b in in_bufs])
            out_ptrs = (POINTER(c_float) * n_out)(*[ctypes.cast(b, POINTER(c_float)) for b in out_bufs])
            self._in_process = True
            try:
                for _ in range(num_blocks):
                    if eff.processReplacing:
                        eff.processReplacing(self._eff_ptr, in_ptrs, out_ptrs, bs)
                    elif eff.process:
                        eff.process(self._eff_ptr, in_ptrs, out_ptrs, bs)
                    self._time_info.samplePos += bs
                    self._time_info.ppqPos = (self._time_info.samplePos / self._sample_rate) * (self._time_info.tempo / 60.0)
            finally:
                self._in_process = False
        except Exception:
            pass

    def _resize_editor_window(self, width: int, height: int):
        if not self._editor_hwnd or width <= 0 or height <= 0 or sys.platform != "win32":
            return
        try:
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            style = 0x00CA0000 | 0x00080000
            rect = wintypes.RECT(0, 0, int(width), int(height))
            user32.AdjustWindowRectEx(byref(rect), style, False, 0)
            user32.SetWindowPos(
                self._editor_hwnd, None, 0, 0,
                rect.right - rect.left, rect.bottom - rect.top,
                0x0002 | 0x0004 | 0x0010,  # SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE
            )
        except Exception:
            pass

    def reset(self):
        with self._lock:
            eff = self._eff_ptr.contents
            # Send All Sound Off (120) and All Notes Off (123) on all 16 MIDI channels
            try:
                events_arr = (_VstMidiEvent * 32)()
                for ch in range(16):
                    for idx_offset, cc in ((0, 120), (16, 123)):
                        ev = events_arr[ch + idx_offset]
                        ev.type = 1
                        ev.byteSize = ctypes.sizeof(_VstMidiEvent)
                        ev.deltaFrames = 0
                        ev.flags = 1
                        ev.midiData[0] = 0xB0 | ch
                        ev.midiData[1] = cc
                        ev.midiData[2] = 0

                class _VstEvents32(Structure):
                    _fields_ = [("numEvents", c_int32), ("reserved", c_ssize_t), ("events", c_void_p * 32)]

                container = _VstEvents32(numEvents=32, reserved=0)
                for i in range(32):
                    container.events[i] = ctypes.addressof(events_arr[i])
                eff.dispatcher(self._eff_ptr, 25, 0, 0, byref(container), 0.0)
                self._flush_silent_blocks(2)
            except Exception:
                pass

    @property
    def raw_state(self) -> bytes:
        with self._lock:
            eff = self._eff_ptr.contents
            if eff.flags & (1 << 5):  # effFlagsProgramChunks
                chunk_ptr = c_void_p()
                sz = eff.dispatcher(self._eff_ptr, 23, 0, 0, byref(chunk_ptr), 0.0)
                if sz > 0 and chunk_ptr.value:
                    data = ctypes.string_at(chunk_ptr.value, sz)
                    self._flush_silent_blocks(4)
                    return data
            if self.parameters_count > 0 and eff.getParameter:
                vals = [float(eff.getParameter(self._eff_ptr, i)) for i in range(self.parameters_count)]
                return struct.pack(f"<{len(vals)}f", *vals)
            return b""

    @raw_state.setter
    def raw_state(self, data: bytes):
        if not data:
            return
        with self._lock:
            eff = self._eff_ptr.contents
            if eff.flags & (1 << 5):  # effFlagsProgramChunks
                buf = ctypes.create_string_buffer(data, len(data))
                self._last_chunk_buf = buf
                eff.dispatcher(self._eff_ptr, 24, 0, len(data), buf, 0.0)
                self._flush_silent_blocks(4)
            elif eff.setParameter and len(data) >= 4:
                count = min(self.parameters_count, len(data) // 4)
                vals = struct.unpack(f"<{count}f", data[: count * 4])
                for i, v in enumerate(vals):
                    eff.setParameter(self._eff_ptr, i, float(v))

    def render_into_planar(
        self,
        midi_events,
        total_frames: int,
        sample_rate: float,
        num_channels: int,
        reset_flag: bool,
        in_planar: np.ndarray | None,
        out_planar: np.ndarray,
    ):
        """Render audio/MIDI directly into a caller-provided planar float32 array (zero intermediate allocations)."""
        with self._lock:
            eff = self._eff_ptr.contents
            if sample_rate > 0 and sample_rate != self._sample_rate:
                self._sample_rate = sample_rate
                eff.dispatcher(self._eff_ptr, 10, 0, 0, None, float(self._sample_rate))

            if reset_flag:
                self.reset()

            out_ch = max(1, num_channels)
            n_in = max(2, self.num_inputs, out_ch)
            n_out = max(2, self.num_outputs, out_ch)
            block_size = self._block_size

            self._ensure_scratch_buffers(n_in, n_out, block_size)
            in_ptrs = self._c_in_ptrs
            out_ptrs = self._c_out_ptrs
            in_views = self._in_views
            out_views = self._out_views

            midi_idx = 0
            num_midi = len(midi_events)
            pos = 0

            while pos < total_frames:
                block = min(block_size, total_frames - pos)

                if in_planar is not None:
                    src_rows = in_planar.shape[0]
                    for c in range(n_in):
                        src_c = c if c < src_rows else 0
                        in_views[c][:block] = in_planar[src_c, pos : pos + block]
                else:
                    for c in range(n_in):
                        in_views[c][:block].fill(0.0)

                for c in range(n_out):
                    out_views[c][:block].fill(0.0)

                ev_count = 0
                while midi_idx < num_midi and midi_events[midi_idx][0] < pos + block:
                    if ev_count < self._max_vst_events:
                        ev_sample, b0, b1, b2 = midi_events[midi_idx]
                        delta = ev_sample - pos
                        if delta < 0:
                            delta = 0
                        elif delta >= block:
                            delta = block - 1
                        mev = self._midi_ev_arr[ev_count]
                        mev.deltaFrames = delta
                        mev.midiData[0] = b0
                        mev.midiData[1] = b1
                        mev.midiData[2] = b2
                        ev_count += 1
                    midi_idx += 1

                if ev_count:
                    self._vst_events.numEvents = ev_count
                    eff.dispatcher(self._eff_ptr, 25, 0, 0, byref(self._vst_events), 0.0)

                self._in_process = True
                try:
                    self._time_info.sampleRate = float(self._sample_rate)
                    self._time_info.ppqPos = (self._time_info.samplePos / self._sample_rate) * (self._time_info.tempo / 60.0)
                    self._time_info.barStartPos = float(int(self._time_info.ppqPos // 4.0) * 4)
                    self._time_info.nanoSeconds = (self._time_info.samplePos / self._sample_rate) * 1e9
                    if eff.processReplacing:
                        eff.processReplacing(self._eff_ptr, in_ptrs, out_ptrs, block)
                    elif eff.process:
                        eff.process(self._eff_ptr, in_ptrs, out_ptrs, block)
                    self._time_info.samplePos += block
                finally:
                    self._in_process = False

                for c in range(out_ch):
                    if c < n_out:
                        out_planar[c, pos : pos + block] = out_views[c][:block]

                pos += block

    def __call__(self, *args, **kwargs):
        sample_rate = float(kwargs.get("sample_rate", self._sample_rate))
        reset_flag = bool(kwargs.get("reset", False))
        num_channels = int(kwargs.get("num_channels", 2))

        first_arg = args[0] if args else kwargs.get("input_array", kwargs.get("midi_messages"))
        is_midi_input = isinstance(first_arg, list) or "duration" in kwargs

        if is_midi_input:
            duration = float(kwargs.get("duration", args[1] if len(args) > 1 else 1.0))
            eff_sr = sample_rate if sample_rate > 0 else self._sample_rate
            total_frames = max(1, int(round(duration * eff_sr)))
            midi_events = _normalize_midi_messages(first_arg, eff_sr)
            in_planar = None
        else:
            arr = np.asarray(first_arg, dtype=np.float32)
            if arr.ndim == 1:
                in_planar = np.vstack([arr, arr])
            elif arr.shape[0] <= 16 and (arr.shape[1] > arr.shape[0] or arr.shape[0] <= 2):
                in_planar = np.ascontiguousarray(arr, dtype=np.float32)
            else:
                in_planar = np.ascontiguousarray(arr.T, dtype=np.float32)
            num_channels = in_planar.shape[0]
            total_frames = in_planar.shape[1]
            midi_events = []

        out_ch = max(1, num_channels)
        out_planar = np.zeros((out_ch, total_frames), dtype=np.float32)
        self.render_into_planar(
            midi_events=midi_events,
            total_frames=total_frames,
            sample_rate=sample_rate,
            num_channels=out_ch,
            reset_flag=reset_flag,
            in_planar=in_planar,
            out_planar=out_planar,
        )
        return out_planar

    def show_editor(self, close_event: threading.Event):
        if sys.platform != "win32":
            raise RuntimeError("L'éditeur natif VST2 nécessite Windows.")

        from ctypes import wintypes
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        WNDPROC = ctypes.WINFUNCTYPE(c_ssize_t, c_void_p, c_uint32, c_size_t, c_ssize_t)
        user32.DefWindowProcW.argtypes = [c_void_p, c_uint32, c_size_t, c_ssize_t]
        user32.DefWindowProcW.restype = c_ssize_t

        class WNDCLASSEXW(Structure):
            _fields_ = [
                ("cbSize", c_uint32),
                ("style", c_uint32),
                ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", c_int32),
                ("cbWndExtra", c_int32),
                ("hInstance", c_void_p),
                ("hIcon", c_void_p),
                ("hCursor", c_void_p),
                ("hbrBackground", c_void_p),
                ("lpszMenuName", c_wchar_p),
                ("lpszClassName", c_wchar_p),
                ("hIconSm", c_void_p),
            ]

        self._window_closed_by_user = False

        def _wnd_proc(hwnd, msg, wparam, lparam):
            if msg == 0x0010:  # WM_CLOSE
                self._window_closed_by_user = True
                return 0
            if msg in (0x0100, 0x0101) and _global_vst_key_callback:
                try:
                    _global_vst_key_callback(wparam, msg == 0x0100)
                except Exception:
                    pass
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._wnd_proc_ref = WNDPROC(_wnd_proc)
        hinst = kernel32.GetModuleHandleW(None)
        class_name = f"NovaDAWVST2Wnd_{id(self)}"

        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.style = 0x0003  # CS_HREDRAW | CS_VREDRAW
        wc.lpfnWndProc = self._wnd_proc_ref
        wc.hInstance = hinst
        wc.hCursor = user32.LoadCursorW(None, 32512)
        wc.hbrBackground = 6
        wc.lpszClassName = class_name
        user32.RegisterClassExW(byref(wc))

        with self._lock:
            eff = self._eff_ptr.contents
            erect_p = POINTER(_ERect)()
            eff.dispatcher(self._eff_ptr, 13, 0, 0, byref(erect_p), 0.0)
            w = (erect_p.contents.right - erect_p.contents.left) if erect_p else 800
            h = (erect_p.contents.bottom - erect_p.contents.top) if erect_p else 600
            if w <= 0:
                w = 800
            if h <= 0:
                h = 600

            style = 0x00CA0000 | 0x00080000  # WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX
            rect = wintypes.RECT(0, 0, w, h)
            user32.AdjustWindowRectEx(byref(rect), style, False, 0)

            hwnd = user32.CreateWindowExW(
                0,
                class_name,
                f"{self.name} - NovaDAW",
                style,
                120,
                120,
                rect.right - rect.left,
                rect.bottom - rect.top,
                None,
                None,
                hinst,
                None,
            )
            self._editor_hwnd = hwnd
            eff.dispatcher(self._eff_ptr, 14, 0, 0, c_void_p(hwnd), 0.0)  # effEditOpen

            erect_p2 = POINTER(_ERect)()
            eff.dispatcher(self._eff_ptr, 13, 0, 0, byref(erect_p2), 0.0)
            if erect_p2:
                w2 = erect_p2.contents.right - erect_p2.contents.left
                h2 = erect_p2.contents.bottom - erect_p2.contents.top
                if w2 > 0 and h2 > 0 and (w2, h2) != (w, h):
                    w, h = w2, h2
                    self._resize_editor_window(w, h)

            user32.ShowWindow(hwnd, 5)  # SW_SHOW
            user32.SetForegroundWindow(hwnd)

        msg = wintypes.MSG()
        last_rect_check = time.monotonic()
        try:
            while not close_event.is_set() and not self._window_closed_by_user:
                msg_count = 0
                while msg_count < 32 and user32.PeekMessageW(byref(msg), None, 0, 0, 1):
                    if msg.message == 0x0012:  # WM_QUIT
                        self._window_closed_by_user = True
                        break
                    if msg.message in (0x0100, 0x0101) and _global_vst_key_callback:
                        try:
                            _global_vst_key_callback(msg.wParam, msg.message == 0x0100)
                        except Exception:
                            pass
                    user32.TranslateMessage(byref(msg))
                    user32.DispatchMessageW(byref(msg))
                    msg_count += 1

                if self._window_closed_by_user or close_event.is_set():
                    break

                eff.dispatcher(self._eff_ptr, 19, 0, 0, None, 0.0)  # effEditIdle
                now = time.monotonic()
                if now - last_rect_check > 0.25:
                    last_rect_check = now
                    er_cur = POINTER(_ERect)()
                    eff.dispatcher(self._eff_ptr, 13, 0, 0, byref(er_cur), 0.0)
                    if er_cur:
                        cw = er_cur.contents.right - er_cur.contents.left
                        ch = er_cur.contents.bottom - er_cur.contents.top
                        if cw > 0 and ch > 0 and (cw, ch) != (w, h):
                            w, h = cw, ch
                            self._resize_editor_window(w, h)

                time.sleep(0.012)
        finally:
            with self._lock:
                if self._editor_hwnd:
                    hwnd_to_close = self._editor_hwnd
                    self._editor_hwnd = None
                    try:
                        eff.dispatcher(self._eff_ptr, 15, 0, 0, None, 0.0)  # effEditClose
                    except Exception:
                        pass
                    try:
                        user32.DestroyWindow(hwnd_to_close)
                    except Exception:
                        pass
            try:
                user32.UnregisterClassW(class_name, hinst)
            except Exception:
                pass


# ============================================================================
# 32-bit (x86) VST 2.4 Host Bridge Wrapper
# ============================================================================

def _get_bridge_x86_exe() -> str:
    base_dir = os.path.dirname(os.path.abspath(__file__))
    exe_path = os.path.join(base_dir, "bin", "vst2_bridge_x86.exe")
    return exe_path


class VST2Plugin32:
    """Hosts 32-bit (x86) VST 2.4 DLL plugins via core/bin/vst2_bridge_x86.exe."""

    def __init__(self, path: str, probe_only: bool = False):
        self.path = os.path.normpath(path)
        self._lock = threading.RLock()
        self._sample_rate = 44100.0
        exe = _get_bridge_x86_exe()
        if not os.path.isfile(exe):
            raise RuntimeError("Pont VST2 32-bit (vst2_bridge_x86.exe) introuvable.")

        cmd = [exe, self.path]
        if probe_only:
            cmd.append("--probe")

        creationflags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )

        status_bytes = self._read_exact(4)
        status = struct.unpack("<I", status_bytes)[0]
        if status == 0:
            err_len = struct.unpack("<I", self._read_exact(4))[0]
            err_msg = self._read_exact(err_len).decode("utf-8", errors="ignore")
            self.dispose()
            raise RuntimeError(err_msg)

        if not probe_only and getattr(self, "_proc", None) and self._proc.pid:
            register_plugin_pid(self._proc.pid)

        hdr = self._read_exact(20)
        is_synth, n_in, n_out, n_params, name_len = struct.unpack("<iiiiI", hdr)
        raw_name = self._read_exact(name_len).decode("utf-8", errors="ignore").strip() if name_len > 0 else ""
        base_name = os.path.splitext(os.path.basename(self.path))[0]
        self.name = raw_name or base_name
        self.is_instrument = bool(is_synth)
        self.is_effect = not self.is_instrument
        self.num_inputs = max(0, int(n_in))
        self.num_outputs = max(2, int(n_out))
        self.parameters_count = max(0, int(n_params))
        self.parameters = {str(i): i for i in range(self.parameters_count)}

    def _read_exact(self, count: int) -> bytes:
        buf = bytearray()
        while len(buf) < count:
            chunk = self._proc.stdout.read(count - len(buf))
            if not chunk:
                raise EOFError("Le pont VST2 32-bit s'est arrêté de manière inattendue.")
            buf.extend(chunk)
        return bytes(buf)

    def _write_exact(self, data: bytes):
        self._proc.stdin.write(data)
        self._proc.stdin.flush()

    def reset(self):
        with self._lock:
            self._write_exact(b"Z")
            ok = struct.unpack("<I", self._read_exact(4))[0]
            if not ok:
                raise RuntimeError("Échec du reset VST2 32-bit.")

    @property
    def raw_state(self) -> bytes:
        with self._lock:
            self._write_exact(b"S")
            ok, sz = struct.unpack("<II", self._read_exact(8))
            if not ok or sz == 0:
                return b""
            return self._read_exact(sz)

    @raw_state.setter
    def raw_state(self, data: bytes):
        if not data:
            return
        with self._lock:
            self._write_exact(b"L" + struct.pack("<I", len(data)) + data)
            ok = struct.unpack("<I", self._read_exact(4))[0]
            if not ok:
                raise RuntimeError("Échec de restauration d'état VST2 32-bit.")

    def __call__(self, *args, **kwargs):
        sample_rate = float(kwargs.get("sample_rate", self._sample_rate))
        reset_flag = 1 if kwargs.get("reset", False) else 0
        num_channels = int(kwargs.get("num_channels", 2))

        first_arg = args[0] if args else kwargs.get("input_array", kwargs.get("midi_messages"))
        is_midi_input = isinstance(first_arg, list) or "duration" in kwargs

        if is_midi_input:
            duration = float(kwargs.get("duration", args[1] if len(args) > 1 else 1.0))
            total_frames = max(1, int(round(duration * sample_rate)))
            midi_events = _normalize_midi_messages(first_arg, sample_rate)
            in_planar = None
            has_input = 0
        else:
            arr = np.asarray(first_arg, dtype=np.float32)
            if arr.ndim == 1:
                in_planar = np.vstack([arr, arr])
            elif arr.shape[0] <= 16 and (arr.shape[1] > arr.shape[0] or arr.shape[0] <= 2):
                in_planar = np.ascontiguousarray(arr, dtype=np.float32)
            else:
                in_planar = np.ascontiguousarray(arr.T, dtype=np.float32)
            num_channels = in_planar.shape[0]
            total_frames = in_planar.shape[1]
            midi_events = []
            has_input = 1

        out_ch = max(1, num_channels)
        with self._lock:
            hdr = struct.pack(
                "<cfiiiii",
                b"R",
                float(sample_rate),
                int(out_ch),
                int(total_frames),
                int(has_input),
                len(midi_events),
                int(reset_flag),
            )
            payload = bytearray(hdr)
            for sample_idx, b0, b1, b2 in midi_events:
                payload.extend(struct.pack("<iBBBB", int(sample_idx), int(b0), int(b1), int(b2), 0))
            if has_input and in_planar is not None:
                payload.extend(np.ascontiguousarray(in_planar, dtype=np.float32).tobytes())

            self._write_exact(bytes(payload))
            ok, ret_ch, ret_frames = struct.unpack("<Iii", self._read_exact(12))
            if not ok or ret_frames <= 0:
                return np.zeros((out_ch, max(0, total_frames)), dtype=np.float32)
            raw_audio = self._read_exact(ret_ch * ret_frames * 4)
            return np.frombuffer(raw_audio, dtype=np.float32).reshape((ret_ch, ret_frames)).copy()

    def show_editor(self, close_event: threading.Event):
        with self._lock:
            self._write_exact(b"E")
            ok, sz = struct.unpack("<II", self._read_exact(8))
            if sz > 0:
                self._read_exact(sz)
        try:
            while not close_event.is_set():
                time.sleep(0.08)
                with self._lock:
                    self._write_exact(b"T")
                    ok, is_open = struct.unpack("<II", self._read_exact(8))
                    if not is_open:
                        break
        finally:
            with self._lock:
                try:
                    self._write_exact(b"C")
                    self._read_exact(4)
                except Exception:
                    pass

    def dispose(self):
        if getattr(self, "_proc", None):
            if self._proc.pid:
                unregister_plugin_pid(self._proc.pid)
            if self._proc.poll() is None:
                try:
                    self._proc.terminate()
                    self._proc.wait(timeout=1)
                except Exception:
                    pass


# ============================================================================
# Unified Loader & Crash-Isolated Subprocess Worker
# ============================================================================

def _load_any_plugin(path: str, probe_only: bool = False):
    """Load either a VST3 (.vst3, including sub-plugins via ::) or a VST2 (.dll 64-bit / 32-bit)."""
    sub_plugin_name = None
    real_path = path
    if "::" in path:
        real_path, sub_plugin_name = path.split("::", 1)

    if real_path.lower().endswith(".dll"):
        machine = _get_pe_machine(real_path)
        if machine == 0x014C:  # 32-bit x86 DLL
            return VST2Plugin32(real_path, probe_only=probe_only)
        return VST2Plugin64(real_path, probe_only=probe_only)

    import pedalboard
    if sub_plugin_name:
        plugin = pedalboard.load_plugin(real_path, plugin_name=sub_plugin_name)
    else:
        plugin = pedalboard.load_plugin(real_path)
    _apply_multibus_vst_fix(plugin)
    return plugin


def _init_win32_sync_api():
    """Bind Win32 Event/Wait kernel32 functions with strict 64-bit pointer types."""
    if sys.platform != "win32":
        return None
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateEventW.argtypes = [c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    k32.CreateEventW.restype = c_void_p
    k32.SetEvent.argtypes = [c_void_p]
    k32.SetEvent.restype = wintypes.BOOL
    k32.WaitForSingleObject.argtypes = [c_void_p, wintypes.DWORD]
    k32.WaitForSingleObject.restype = wintypes.DWORD
    k32.WaitForMultipleObjects.argtypes = [wintypes.DWORD, POINTER(c_void_p), wintypes.BOOL, wintypes.DWORD]
    k32.WaitForMultipleObjects.restype = wintypes.DWORD
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.OpenProcess.restype = c_void_p
    k32.CloseHandle.argtypes = [c_void_p]
    k32.CloseHandle.restype = wintypes.BOOL
    k32.GetCurrentThread.argtypes = []
    k32.GetCurrentThread.restype = c_void_p
    k32.SetThreadPriority.argtypes = [c_void_p, ctypes.c_int]
    k32.SetThreadPriority.restype = wintypes.BOOL
    return k32


class _SharedRenderChannel:
    """Zero-pickle SharedMemory + Win32 Event channel for low-latency audio/MIDI rendering."""

    def __init__(self, shm_tag: str, create: bool, child_pid: Optional[int] = None):
        self.shm_tag = shm_tag
        self.create = create
        self._k32 = _init_win32_sync_api()
        self._closed = False
        self._shm = shared_memory.SharedMemory(name=shm_tag, create=create, size=_SHM_TOTAL_SIZE if create else 0)
        self._buf = self._shm.buf
        self._in_view = np.ndarray((_MAX_SHM_CHANNELS, _MAX_SHM_FRAMES), dtype=np.float32, buffer=self._buf, offset=_SHM_IN_OFFSET)
        self._out_view = np.ndarray((_MAX_SHM_CHANNELS, _MAX_SHM_FRAMES), dtype=np.float32, buffer=self._buf, offset=_SHM_OUT_OFFSET)

        self._req_evt = self._k32.CreateEventW(None, False, False, f"Local\\{shm_tag}_req")
        self._resp_evt = self._k32.CreateEventW(None, False, False, f"Local\\{shm_tag}_resp")
        self._proc_handle = self._k32.OpenProcess(0x00100000, False, int(child_pid)) if (create and child_pid) else None

        if self._proc_handle:
            self._wait_handles = (c_void_p * 2)(self._resp_evt, self._proc_handle)
            self._wait_count = 2
        else:
            self._wait_handles = (c_void_p * 1)(self._resp_evt)
            self._wait_count = 1

    def write_header(self, op: int, status: int, num_frames: int, num_channels: int, num_midi: int, sample_rate: float, reset_flag: int, err_len: int = 0):
        _SHM_HDR_STRUCT.pack_into(self._buf, 0, op, status, num_frames, num_channels, num_midi, float(sample_rate), reset_flag, err_len)

    def read_header(self):
        return _SHM_HDR_STRUCT.unpack_from(self._buf, 0)

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            if self.create and self._req_evt and self._k32:
                self.write_header(_SHM_OP_QUIT, _SHM_STATUS_PENDING, 0, 0, 0, 44100.0, 0, 0)
                self._k32.SetEvent(self._req_evt)
        except Exception:
            pass
        self._in_view = None
        self._out_view = None
        self._buf = None
        if self._shm is not None:
            try:
                self._shm.close()
            except Exception:
                pass
            if self.create:
                try:
                    self._shm.unlink()
                except Exception:
                    pass
            self._shm = None
        if self._k32:
            for h_attr in ("_req_evt", "_resp_evt", "_proc_handle"):
                h = getattr(self, h_attr, None)
                if h:
                    try:
                        self._k32.CloseHandle(h)
                    except Exception:
                        pass
                    setattr(self, h_attr, None)


def _host(connection, path, probe_only=False, shm_tag=None):
    if sys.platform == "win32":
        try:
            ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002 | 0x8000)
        except Exception:
            pass
    try:
        plugin = _load_any_plugin(path, probe_only=probe_only)
        params_count = getattr(plugin, "parameters_count", None)
        if params_count is None:
            params_count = len(getattr(plugin, "parameters", ()))
        connection.send((True, dict(
            name=plugin.name,
            is_instrument=plugin.is_instrument,
            is_effect=plugin.is_effect,
            parameters_count=params_count,
        )))
    except Exception as exc:
        connection.send((False, str(exc)))
        if probe_only:
            os._exit(0)
        return

    if probe_only:
        connection.close()
        os._exit(0)

    show = threading.Event()
    close = threading.Event()
    stop = threading.Event()
    editor = {"open": False, "error": None}

    shm_channel = None
    if shm_tag and sys.platform == "win32":
        try:
            shm_channel = _SharedRenderChannel(shm_tag, create=False)
        except Exception:
            shm_channel = None

    def _shm_serve():
        if shm_channel is None:
            return
        k32 = shm_channel._k32
        try:
            # Raise child audio worker priority (THREAD_PRIORITY_TIME_CRITICAL = 15, fallback HIGHEST = 2)
            cur_thr = k32.GetCurrentThread()
            if not k32.SetThreadPriority(cur_thr, 15):
                k32.SetThreadPriority(cur_thr, 2)
        except Exception:
            pass

        has_fast_vst2 = hasattr(plugin, "render_into_planar")
        buf = shm_channel._buf
        in_view = shm_channel._in_view
        out_view = shm_channel._out_view
        req_evt = shm_channel._req_evt
        resp_evt = shm_channel._resp_evt

        while not stop.is_set():
            wait_res = k32.WaitForSingleObject(req_evt, 250)
            if wait_res == 0x00000102:  # WAIT_TIMEOUT
                continue
            if wait_res != 0 or stop.is_set():
                break
            op, _status, num_frames, num_channels, num_midi, sample_rate, reset_flag, _ = shm_channel.read_header()
            if op == _SHM_OP_QUIT:
                break
            try:
                out_ch = max(1, min(_MAX_SHM_CHANNELS, int(num_channels)))
                frames = max(1, min(_MAX_SHM_FRAMES, int(num_frames)))
                n_midi = max(0, min(_MAX_SHM_MIDI, int(num_midi)))

                if op == _SHM_OP_RENDER_MIDI:
                    midi_events = []
                    if n_midi:
                        for i in range(n_midi):
                            s_idx, b0, b1, b2, _ = _SHM_MIDI_STRUCT.unpack_from(buf, _SHM_MIDI_OFFSET + i * _SHM_MIDI_STRUCT.size)
                            midi_events.append((s_idx, b0, b1, b2))
                        midi_events.sort(key=lambda x: x[0])

                    if has_fast_vst2:
                        plugin.render_into_planar(
                            midi_events=midi_events,
                            total_frames=frames,
                            sample_rate=sample_rate,
                            num_channels=out_ch,
                            reset_flag=bool(reset_flag),
                            in_planar=None,
                            out_planar=out_view[:, :frames],
                        )
                    else:
                        sr_f = float(sample_rate) if sample_rate > 0 else 44100.0
                        midi_msgs = [(bytes([b0, b1, b2]), s_idx / sr_f) for s_idx, b0, b1, b2 in midi_events]
                        res = plugin(
                            midi_msgs,
                            duration=frames / sr_f,
                            sample_rate=sr_f,
                            num_channels=out_ch,
                            reset=bool(reset_flag),
                        )
                        out_view[:out_ch, :frames].fill(0.0)
                        if res is not None and res.size > 0:
                            if res.ndim == 1:
                                n = min(frames, len(res))
                                out_view[0, :n] = res[:n]
                                if out_ch > 1:
                                    out_view[1, :n] = res[:n]
                            else:
                                rows = min(out_ch, res.shape[0])
                                n = min(frames, res.shape[1])
                                out_view[:rows, :n] = res[:rows, :n]
                                if rows == 1 and out_ch > 1:
                                    out_view[1, :n] = res[0, :n]

                elif op == _SHM_OP_RENDER_AUDIO:
                    if has_fast_vst2:
                        plugin.render_into_planar(
                            midi_events=[],
                            total_frames=frames,
                            sample_rate=sample_rate,
                            num_channels=out_ch,
                            reset_flag=bool(reset_flag),
                            in_planar=in_view[:out_ch, :frames],
                            out_planar=out_view[:out_ch, :frames],
                        )
                    else:
                        res = plugin(
                            in_view[:out_ch, :frames].copy(),
                            sample_rate=float(sample_rate),
                            reset=bool(reset_flag),
                        )
                        out_view[:out_ch, :frames].fill(0.0)
                        if res is not None and res.size > 0:
                            if res.ndim == 1:
                                n = min(frames, len(res))
                                out_view[0, :n] = res[:n]
                                if out_ch > 1:
                                    out_view[1, :n] = res[:n]
                            else:
                                rows = min(out_ch, res.shape[0])
                                n = min(frames, res.shape[1])
                                out_view[:rows, :n] = res[:rows, :n]
                                if rows == 1 and out_ch > 1:
                                    out_view[1, :n] = res[0, :n]

                shm_channel.write_header(op, _SHM_STATUS_OK, frames, out_ch, 0, sample_rate, 0, 0)
            except Exception as exc:
                err_b = str(exc).encode("utf-8", errors="ignore")[:_SHM_ERR_SIZE]
                buf[_SHM_ERR_OFFSET : _SHM_ERR_OFFSET + len(err_b)] = err_b
                shm_channel.write_header(op, _SHM_STATUS_ERROR, 0, 0, 0, sample_rate, 0, len(err_b))
            k32.SetEvent(resp_evt)

    def serve():
        try:
            while not stop.is_set():
                command, args, kwargs = connection.recv()
                try:
                    if command == "render":
                        result = plugin(*args, **kwargs)
                    elif command == "reset":
                        result = plugin.reset()
                    elif command == "state":
                        result = plugin.raw_state
                    elif command == "restore":
                        plugin.raw_state = args[0]
                        result = None
                    elif command == "editor":
                        saved_state = plugin.raw_state
                        close.clear()
                        editor.update(open=True, error=None)
                        show.set()
                        result = saved_state
                    elif command == "editor_status":
                        result = dict(editor)
                    elif command == "close_editor":
                        close.set()
                        result = None
                    elif command == "quit":
                        close.set()
                        stop.set()
                        show.set()
                        result = None
                    else:
                        raise ValueError(command)
                    connection.send((True, result))
                except Exception as exc:
                    connection.send((False, str(exc)))
        except (EOFError, OSError):
            close.set()
            stop.set()
            show.set()

    if shm_channel is not None:
        threading.Thread(target=_shm_serve, daemon=True, name="NovaDAW-VST-SHM").start()
    threading.Thread(target=serve, daemon=True).start()
    while not stop.is_set():
        show.wait()
        show.clear()
        if stop.is_set():
            break
        try:
            plugin.show_editor(close)
        except Exception as exc:
            editor["error"] = str(exc)
        finally:
            editor["open"] = False
    if shm_channel is not None:
        shm_channel.close()
    if hasattr(plugin, "dispose"):
        try:
            plugin.dispose()
        except Exception:
            pass
    connection.close()


def _dismiss_child_modal_dialogs(child_pid: int):
    """Dismiss any unexpected modal dialog window opened by child_pid during non-interactive probing."""
    if sys.platform != "win32" or not child_pid:
        return
    try:
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def _cb(hwnd, lparam):
            if user32.IsWindowVisible(hwnd):
                lpdw = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, byref(lpdw))
                if lpdw.value == child_pid:
                    user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
                    user32.PostMessageW(hwnd, 0x0111, 2, 0)  # WM_COMMAND IDCANCEL
            return True

        user32.EnumWindows(WNDENUMPROC(_cb), 0)
    except Exception:
        pass


class IsolatedPlugin:
    def __init__(self, path, timeout=60, worker=_host, probe_only=False):
        self._lock = threading.RLock()
        self._failure = None
        self._shm: Optional[_SharedRenderChannel] = None
        shm_tag = None
        use_shm = (not probe_only) and (worker is _host or getattr(worker, "_supports_shm", False)) and sys.platform == "win32"
        if use_shm:
            shm_tag = f"novadaw_vst_{os.getpid()}_{id(self)}_{time.monotonic_ns()}"
            try:
                self._shm = _SharedRenderChannel(shm_tag, create=True)
            except Exception:
                self._shm = None
                shm_tag = None

        context = multiprocessing.get_context("spawn")
        self._connection, child = context.Pipe()
        if worker is _host or getattr(worker, "_supports_shm", False):
            self._process = context.Process(target=worker, args=(child, path, probe_only, shm_tag), daemon=True)
        else:
            self._process = context.Process(target=worker, args=(child, path), daemon=True)
        self._process.start()
        child.close()
        if not probe_only and getattr(self._process, "pid", None):
            register_plugin_pid(self._process.pid)
            if self._shm is not None and self._shm._k32:
                try:
                    self._shm._proc_handle = self._shm._k32.OpenProcess(0x00100000, False, int(self._process.pid))
                    if self._shm._proc_handle:
                        self._shm._wait_handles = (c_void_p * 2)(self._shm._resp_evt, self._shm._proc_handle)
                        self._shm._wait_count = 2
                except Exception:
                    pass
        try:
            metadata = self._receive(timeout, dismiss_popups=probe_only)
            self.__dict__.update(metadata)
        except Exception:
            self.dispose()
            raise

    def _receive(self, timeout, dismiss_popups=False):
        try:
            if dismiss_popups and sys.platform == "win32":
                deadline = time.monotonic() + timeout
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("Le plugin ne répond plus (délai dépassé).")
                    if self._connection.poll(min(0.15, remaining)):
                        break
                    _dismiss_child_modal_dialogs(self._process.pid)
            else:
                if not self._connection.poll(timeout):
                    raise TimeoutError("Le plugin ne répond plus (délai dépassé).")
            success, value = self._connection.recv()
        except (EOFError, OSError, TimeoutError) as exc:
            self._failure = "Le processus du plugin s'est arrêté ou ne répond plus."
            self.dispose()
            raise RuntimeError(self._failure) from exc
        if not success:
            raise RuntimeError(value)
        return value

    def request(self, command, *args, timeout=5, **kwargs):
        with self._lock:
            if self._failure:
                raise RuntimeError(self._failure)
            try:
                self._connection.send((command, args, kwargs))
            except (OSError, EOFError) as exc:
                self._failure = "Le processus du plugin s'est arrêté."
                raise RuntimeError(self._failure) from exc
            return self._receive(timeout)

    def _shm_render_midi(self, midi_messages, num_frames: int, sample_rate: float, num_channels: int, reset_flag: bool, timeout_ms: int = 5000):
        shm = self._shm
        buf = shm._buf
        k32 = shm._k32
        sr = float(sample_rate) if sample_rate > 0 else 44100.0
        ev_count = 0
        if midi_messages:
            for item in midi_messages:
                if ev_count >= _MAX_SHM_MIDI:
                    break
                if isinstance(item, (tuple, list)) and len(item) >= 2:
                    raw_bytes, ts = item[0], float(item[1])
                else:
                    raw_bytes = getattr(item, "data", b"")
                    ts = float(getattr(item, "timestamp", 0.0))
                if not raw_bytes:
                    continue
                b = bytes(raw_bytes)
                b0 = b[0] if len(b) > 0 else 0
                b1 = b[1] if len(b) > 1 else 0
                b2 = b[2] if len(b) > 2 else 0
                sample_idx = max(0, int(round(ts * sr)))
                _SHM_MIDI_STRUCT.pack_into(buf, _SHM_MIDI_OFFSET + ev_count * _SHM_MIDI_STRUCT.size, sample_idx, b0, b1, b2, 0)
                ev_count += 1

        shm.write_header(_SHM_OP_RENDER_MIDI, _SHM_STATUS_PENDING, num_frames, num_channels, ev_count, sr, 1 if reset_flag else 0, 0)
        k32.SetEvent(shm._req_evt)
        wait_res = k32.WaitForMultipleObjects(shm._wait_count, shm._wait_handles, False, int(timeout_ms))
        if wait_res != 0:
            self._failure = "Le processus du plugin s'est arrêté ou ne répond plus."
            self.dispose()
            raise RuntimeError(self._failure)
        _, status, _, _, _, _, _, err_len = shm.read_header()
        if status == _SHM_STATUS_ERROR:
            msg = bytes(buf[_SHM_ERR_OFFSET : _SHM_ERR_OFFSET + min(_SHM_ERR_SIZE, err_len)]).decode("utf-8", errors="ignore")
            raise RuntimeError(msg or "Erreur de rendu VST.")
        return shm._out_view[:num_channels, :num_frames]

    def _shm_render_audio(self, in_planar: np.ndarray, num_frames: int, sample_rate: float, num_channels: int, reset_flag: bool, timeout_ms: int = 5000):
        shm = self._shm
        buf = shm._buf
        k32 = shm._k32
        rows = min(num_channels, in_planar.shape[0])
        shm._in_view[:rows, :num_frames] = in_planar[:rows, :num_frames]
        if rows < num_channels:
            shm._in_view[rows:num_channels, :num_frames] = in_planar[0, :num_frames]
        shm.write_header(_SHM_OP_RENDER_AUDIO, _SHM_STATUS_PENDING, num_frames, num_channels, 0, float(sample_rate), 1 if reset_flag else 0, 0)
        k32.SetEvent(shm._req_evt)
        wait_res = k32.WaitForMultipleObjects(shm._wait_count, shm._wait_handles, False, int(timeout_ms))
        if wait_res != 0:
            self._failure = "Le processus du plugin s'est arrêté ou ne répond plus."
            self.dispose()
            raise RuntimeError(self._failure)
        _, status, _, _, _, _, _, err_len = shm.read_header()
        if status == _SHM_STATUS_ERROR:
            msg = bytes(buf[_SHM_ERR_OFFSET : _SHM_ERR_OFFSET + min(_SHM_ERR_SIZE, err_len)]).decode("utf-8", errors="ignore")
            raise RuntimeError(msg or "Erreur de rendu VST.")
        return shm._out_view[:num_channels, :num_frames]

    def render_live_block(self, midi_messages, num_frames: int, sample_rate: float, out_stereo: np.ndarray) -> bool:
        """
        Zero-allocation live block render directly into caller's (num_frames, 2) float32 buffer.
        Returns False if lock is busy (e.g. during preset save) so the real-time callback never stalls.
        """
        if self._shm is None or num_frames > _MAX_SHM_FRAMES:
            return False
        if not self._lock.acquire(blocking=False):
            return False
        try:
            if self._failure or self._shm is None:
                return False
            out_planar = self._shm_render_midi(midi_messages, num_frames, sample_rate, 2, False, timeout_ms=2000)
            out_stereo[:num_frames, 0] = out_planar[0, :num_frames]
            out_stereo[:num_frames, 1] = out_planar[1, :num_frames]
            return True
        finally:
            self._lock.release()

    def __call__(self, *args, **kwargs):
        if self._shm is not None:
            sample_rate = float(kwargs.get("sample_rate", 44100.0))
            reset_flag = bool(kwargs.get("reset", False))
            num_channels = int(kwargs.get("num_channels", 2))
            first_arg = args[0] if args else kwargs.get("input_array", kwargs.get("midi_messages"))
            is_midi_input = isinstance(first_arg, list) or "duration" in kwargs

            if is_midi_input:
                duration = float(kwargs.get("duration", args[1] if len(args) > 1 else 1.0))
                total_frames = max(1, int(round(duration * sample_rate)))
                if total_frames <= _MAX_SHM_FRAMES and 1 <= num_channels <= _MAX_SHM_CHANNELS:
                    with self._lock:
                        if self._failure:
                            raise RuntimeError(self._failure)
                        return self._shm_render_midi(first_arg, total_frames, sample_rate, num_channels, reset_flag).copy()
            elif first_arg is not None:
                arr = np.asarray(first_arg, dtype=np.float32)
                if arr.ndim == 1:
                    in_planar = np.vstack([arr, arr])
                elif arr.shape[0] <= 16 and (arr.shape[1] > arr.shape[0] or arr.shape[0] <= 2):
                    in_planar = np.ascontiguousarray(arr, dtype=np.float32)
                else:
                    in_planar = np.ascontiguousarray(arr.T, dtype=np.float32)
                in_ch = min(_MAX_SHM_CHANNELS, in_planar.shape[0])
                total_frames = in_planar.shape[1]
                if 1 <= total_frames <= _MAX_SHM_FRAMES and 1 <= in_ch <= _MAX_SHM_CHANNELS:
                    with self._lock:
                        if self._failure:
                            raise RuntimeError(self._failure)
                        return self._shm_render_audio(in_planar, total_frames, sample_rate, in_ch, reset_flag).copy()

        return self.request("render", *args, **kwargs)

    def reset(self):
        return self.request("reset")

    @property
    def raw_state(self):
        return self.request("state")

    @raw_state.setter
    def raw_state(self, value):
        self.request("restore", value)

    def dispose(self):
        if getattr(self, "_shm", None) is not None:
            try:
                self._shm.close()
            except Exception:
                pass
            self._shm = None
        if getattr(self, "_process", None):
            if getattr(self._process, "pid", None):
                unregister_plugin_pid(self._process.pid)
            if self._process.is_alive():
                self._process.terminate()
            self._process.join(timeout=1)
        if getattr(self, "_connection", None):
            try:
                self._connection.close()
            except Exception:
                pass
