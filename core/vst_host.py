"""Crash-isolated VST host. Native editors run on the child's main thread."""
import multiprocessing
import threading


def _host(connection, path):
    try:
        import pedalboard
        plugin = pedalboard.load_plugin(path)
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
