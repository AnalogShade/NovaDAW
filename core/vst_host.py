"""Crash-isolated Universal VST3 & VST2 (64-bit & 32-bit) host. Native editors run on the child's main thread."""
import ctypes
from ctypes import (
    c_int32, c_int16, c_uint32, c_ssize_t, c_size_t, c_void_p,
    c_float, c_double, c_char, c_ubyte, c_wchar_p, POINTER, Structure, byref
)
import multiprocessing
import os
import struct
import subprocess
import sys
import threading
import time
import numpy as np


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


class VST2Plugin64:
    """Native 64-bit VST 2.4 Host wrapper compatible with Pedalboard's plugin API."""

    def __init__(self, path: str, probe_only: bool = False):
        self.path = os.path.normpath(path)
        self._lock = threading.RLock()
        self._sample_rate = 44100.0
        self._block_size = 512
        self._editor_hwnd = None
        self._window_closed_by_user = False

        self._time_info = _VstTimeInfo(
            samplePos=0.0,
            sampleRate=self._sample_rate,
            nanoSeconds=0.0,
            ppqPos=0.0,
            tempo=120.0,
            barStartPos=0.0,
            cycleStartPos=0.0,
            cycleEndPos=0.0,
            timeSigNumerator=4,
            timeSigDenominator=4,
            smpteOffset=0,
            smpteFrameRate=0,
            samplesToNextClock=0,
            flags=(1 << 10) | (1 << 13),
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
                return 0
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

            # Warmup default program & chunk so synths (e.g. Omnisphere, FM8, Absynth 5)
            # initialize their default patch immediately before the first MIDI note
            try:
                eff.dispatcher(self._eff_ptr, 2, 0, 0, None, 0.0)  # effSetProgram(0)
                er_warm = POINTER(_ERect)()
                eff.dispatcher(self._eff_ptr, 13, 0, 0, byref(er_warm), 0.0)  # effEditGetRect
                if eff.flags & (1 << 5):
                    c_warm = c_void_p()
                    eff.dispatcher(self._eff_ptr, 23, 0, 0, byref(c_warm), 0.0)  # effGetChunk
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
            except Exception:
                pass
            eff.dispatcher(self._eff_ptr, 12, 0, 0, None, 0.0)
            eff.dispatcher(self._eff_ptr, 12, 0, 1, None, 0.0)

    @property
    def raw_state(self) -> bytes:
        with self._lock:
            eff = self._eff_ptr.contents
            if eff.flags & (1 << 5):  # effFlagsProgramChunks
                chunk_ptr = c_void_p()
                sz = eff.dispatcher(self._eff_ptr, 23, 0, 0, byref(chunk_ptr), 0.0)
                if sz > 0 and chunk_ptr.value:
                    return ctypes.string_at(chunk_ptr.value, sz)
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
            elif eff.setParameter and len(data) >= 4:
                count = min(self.parameters_count, len(data) // 4)
                vals = struct.unpack(f"<{count}f", data[: count * 4])
                for i, v in enumerate(vals):
                    eff.setParameter(self._eff_ptr, i, float(v))

    def __call__(self, *args, **kwargs):
        sample_rate = float(kwargs.get("sample_rate", self._sample_rate))
        reset_flag = bool(kwargs.get("reset", False))
        num_channels = int(kwargs.get("num_channels", 2))

        first_arg = args[0] if args else kwargs.get("input_array", kwargs.get("midi_messages"))
        is_midi_input = isinstance(first_arg, list) or "duration" in kwargs

        with self._lock:
            eff = self._eff_ptr.contents
            if sample_rate > 0 and sample_rate != self._sample_rate:
                self._sample_rate = sample_rate
                eff.dispatcher(self._eff_ptr, 10, 0, 0, None, float(self._sample_rate))

            if reset_flag:
                self.reset()

            if is_midi_input:
                duration = float(kwargs.get("duration", args[1] if len(args) > 1 else 1.0))
                total_frames = max(1, int(round(duration * self._sample_rate)))
                midi_events = _normalize_midi_messages(first_arg, self._sample_rate)
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

            n_in = max(2, self.num_inputs, out_ch)
            n_out = max(2, self.num_outputs, out_ch)
            block_size = self._block_size

            in_bufs = [(c_float * block_size)() for _ in range(n_in)]
            out_bufs = [(c_float * block_size)() for _ in range(n_out)]
            in_ptrs = (POINTER(c_float) * n_in)(*[ctypes.cast(b, POINTER(c_float)) for b in in_bufs])
            out_ptrs = (POINTER(c_float) * n_out)(*[ctypes.cast(b, POINTER(c_float)) for b in out_bufs])

            midi_idx = 0
            num_midi = len(midi_events)
            pos = 0

            while pos < total_frames:
                block = min(block_size, total_frames - pos)

                if in_planar is not None:
                    for c in range(n_in):
                        src_c = c if c < in_planar.shape[0] else 0
                        slice_arr = np.ascontiguousarray(in_planar[src_c, pos : pos + block], dtype=np.float32)
                        ctypes.memmove(in_bufs[c], slice_arr.ctypes.data, block * 4)
                else:
                    for c in range(n_in):
                        ctypes.memset(in_bufs[c], 0, block * 4)

                for c in range(n_out):
                    ctypes.memset(out_bufs[c], 0, block * 4)

                # Collect MIDI events in [pos, pos + block)
                block_events = []
                while midi_idx < num_midi and midi_events[midi_idx][0] < pos + block:
                    ev_sample, b0, b1, b2 = midi_events[midi_idx]
                    delta = max(0, min(block - 1, ev_sample - pos))
                    mev = _VstMidiEvent(
                        type=1,
                        byteSize=ctypes.sizeof(_VstMidiEvent),
                        deltaFrames=delta,
                        flags=1,
                        noteLength=0,
                        noteOffset=0,
                    )
                    mev.midiData[0] = b0
                    mev.midiData[1] = b1
                    mev.midiData[2] = b2
                    mev.midiData[3] = 0
                    block_events.append(mev)
                    midi_idx += 1

                if block_events:
                    ev_count = len(block_events)

                    class _VstEventsDyn(Structure):
                        _fields_ = [
                            ("numEvents", c_int32),
                            ("reserved", c_ssize_t),
                            ("events", c_void_p * ev_count),
                        ]

                    ev_container = _VstEventsDyn(numEvents=ev_count, reserved=0)
                    for i, mev in enumerate(block_events):
                        ev_container.events[i] = ctypes.addressof(mev)
                    eff.dispatcher(self._eff_ptr, 25, 0, 0, byref(ev_container), 0.0)

                if eff.processReplacing:
                    eff.processReplacing(self._eff_ptr, in_ptrs, out_ptrs, block)
                elif eff.process:
                    eff.process(self._eff_ptr, in_ptrs, out_ptrs, block)

                for c in range(out_ch):
                    if c < n_out:
                        out_view = np.ctypeslib.as_array(out_bufs[c])[:block]
                        out_planar[c, pos : pos + block] = out_view

                pos += block

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
                while user32.PeekMessageW(byref(msg), None, 0, 0, 1):
                    if msg.message == 0x0012:  # WM_QUIT
                        self._window_closed_by_user = True
                        break
                    user32.TranslateMessage(byref(msg))
                    user32.DispatchMessageW(byref(msg))

                if self._window_closed_by_user or close_event.is_set():
                    break

                with self._lock:
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
        if getattr(self, "_proc", None) and self._proc.poll() is None:
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


def _host(connection, path, probe_only=False):
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
        context = multiprocessing.get_context("spawn")
        self._connection, child = context.Pipe()
        if worker is _host:
            self._process = context.Process(target=worker, args=(child, path, probe_only), daemon=True)
        else:
            self._process = context.Process(target=worker, args=(child, path), daemon=True)
        self._process.start()
        child.close()
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

    def __call__(self, *args, **kwargs):
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
        if self._process.is_alive():
            self._process.terminate()
        self._process.join(timeout=1)
        self._connection.close()
