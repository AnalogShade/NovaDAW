"""Crash-isolated VST host. Native editors run on the child's main thread."""
import multiprocessing
import threading


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
        import ctypes
        from ctypes import c_void_p, c_int32

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

        # Retain references on plugin object to prevent garbage collection
        plugin._multibus_fix_refs = (new_vtable, callback_ref, dummy_float_buffer, chan_pointers)
    except Exception:
        pass


def _host(connection, path):
    try:
        import pedalboard
        plugin = pedalboard.load_plugin(path)
        _apply_multibus_vst_fix(plugin)
        connection.send((True, dict(name=plugin.name, is_instrument=plugin.is_instrument,
                                    is_effect=plugin.is_effect,
                                    parameters_count=len(plugin.parameters))))
    except Exception as exc:
        connection.send((False, str(exc)))
        return
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
    connection.close()


class IsolatedPlugin:
    def __init__(self, path, timeout=60, worker=_host):
        self._lock = threading.RLock()
        self._failure = None
        context = multiprocessing.get_context("spawn")
        self._connection, child = context.Pipe()
        self._process = context.Process(target=worker, args=(child, path), daemon=True)
        self._process.start()
        child.close()
        try:
            metadata = self._receive(timeout)
            self.__dict__.update(metadata)
        except Exception:
            self.dispose()
            raise

    def _receive(self, timeout):
        try:
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
