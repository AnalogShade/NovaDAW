"""
tests/conftest.py - Configuration globale pytest pour NovaDAW.
GARANTIE DE SILENCE ABSOLU : Protège les haut-parleurs de l'utilisateur contre tout pic,
bruit ou clic sonore lors de l'exécution des tests unitaires en isolant complètement
sounddevice du matériel physique.
"""
import os
import sys
from pathlib import Path
import numpy as np
import pytest

# Activer le mode test silencieux dès l'import
os.environ["NOVADAW_SILENT_TESTS"] = "1"

# S'assurer que le dossier racine du projet est dans sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class DummySilentStream:
    """Simulateur de flux audio sounddevice 100% silencieux sans accès matériel."""

    def __init__(self, *args, **kwargs):
        self.samplerate = int(kwargs.get("samplerate", 44100))
        self.channels = int(kwargs.get("channels", 2))
        self.blocksize = int(kwargs.get("blocksize", 512))
        self.callback = kwargs.get("callback", None)
        self.device = kwargs.get("device", None)
        self.active = False
        self._closed = False

    def start(self):
        self.active = True

    def stop(self):
        self.active = False

    def close(self):
        self.active = False
        self._closed = True

    def write(self, data):
        pass

    def read(self, frames):
        return np.zeros((frames, self.channels), dtype=np.float32), False

    def abort(self):
        self.stop()


@pytest.fixture(autouse=True, scope="session")
def silence_audio_hardware():
    """Fixture de session qui patche sounddevice pour empêcher tout flux physique vers les enceintes."""
    try:
        import sounddevice as sd

        # Sauvegarde des originaux si besoin
        orig_output = getattr(sd, "OutputStream", None)
        orig_input = getattr(sd, "InputStream", None)
        orig_play = getattr(sd, "play", None)
        orig_stop = getattr(sd, "stop", None)
        orig_rec = getattr(sd, "rec", None)

        # Remplacement par des stubs silencieux
        sd.OutputStream = DummySilentStream
        sd.InputStream = DummySilentStream
        sd.RawOutputStream = DummySilentStream
        sd.RawInputStream = DummySilentStream
        sd.Stream = DummySilentStream

        def silent_play(*args, **kwargs):
            pass

        def silent_stop(*args, **kwargs):
            pass

        def silent_rec(frames, samplerate=None, channels=1, dtype='float32', **kwargs):
            return np.zeros((int(frames), int(channels)), dtype=dtype)

        sd.play = silent_play
        sd.stop = silent_stop
        sd.rec = silent_rec

        yield

        # Restauration après la session
        if orig_output:
            sd.OutputStream = orig_output
        if orig_input:
            sd.InputStream = orig_input
        if orig_play:
            sd.play = orig_play
        if orig_stop:
            sd.stop = orig_stop
        if orig_rec:
            sd.rec = orig_rec
    except ImportError:
        yield


@pytest.fixture(scope="session")
def qapp():
    """Fixture globale session fournissant une instance QApplication propre pour les tests UI."""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app

