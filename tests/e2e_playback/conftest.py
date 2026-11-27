"""Shared pytest configuration & fixtures for NovaDAW E2E playback tests."""

from __future__ import annotations

import copy
import os
import sys
from pathlib import Path
from typing import Callable, Dict, Tuple, Any

import pytest

# Enforce silent test mode & offscreen Qt before importing engine or UI modules.
os.environ.setdefault("NOVADAW_SILENT_TESTS", "1")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import core.hardware_manager as hm_mod  # noqa: E402
from core.audio_engine import AudioEngine  # noqa: E402
from plugins.registry import ensure_plugins_loaded  # noqa: E402
from tests.e2e_playback.harness import SOUS_LA_CENDRE_PATH, load_sous_la_cendre  # noqa: E402


ensure_plugins_loaded()


@pytest.fixture(autouse=True)
def isolated_hardware_settings(monkeypatch, tmp_path):
    """Redirect HardwareManager SETTINGS_FILE to tmp_path and restore in-memory settings."""
    monkeypatch.setenv("NOVADAW_SILENT_TESTS", "1")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setattr(hm_mod, "SETTINGS_FILE", str(tmp_path / "settings.json"))
    saved_settings = copy.deepcopy(hm_mod.hardware_manager.settings)
    yield tmp_path / "settings.json"
    hm_mod.hardware_manager.settings.clear()
    hm_mod.hardware_manager.settings.update(saved_settings)


@pytest.fixture
def silent_engine_factory(isolated_hardware_settings) -> Callable[..., AudioEngine]:
    """Create silent AudioEngine instances and reliably close them + join cache workers."""
    created: list[AudioEngine] = []

    def _factory(
        sample_rate: int = 44100,
        block_size: int = 128,
        asio_guard: bool = False,
        output_device: int = 0,
        input_device: int = 0,
    ) -> AudioEngine:
        engine = AudioEngine(
            sample_rate=sample_rate,
            block_size=block_size,
            output_device=output_device,
            input_device=input_device,
        )
        engine._asio_guard_enabled = bool(asio_guard)
        created.append(engine)
        return engine

    yield _factory

    for eng in created:
        worker = getattr(eng, "_cache_worker", None)
        try:
            eng.is_playing = False
            eng.close()
        finally:
            if worker is not None and worker.is_alive():
                worker.join(timeout=5.0)


@pytest.fixture
def sous_la_cendre_path() -> Path:
    """Return path to the 'Sous la cendre' study project, skipping if unavailable."""
    path = Path(SOUS_LA_CENDRE_PATH)
    if not path.is_file():
        pytest.skip(f"Study project not found: {path}")
    return path


@pytest.fixture
def sous_la_cendre_project(sous_la_cendre_path: Path) -> Tuple[Any, Dict[str, Any]]:
    """Load 'Sous la cendre' cleanly with all tracks unmuted/unsoloed in memory."""
    return load_sous_la_cendre(path=sous_la_cendre_path, unsolo=True)


def cleanup_qt_widgets(widgets: list[Any], qapp: Any) -> None:
    """Safely stop MixerConsoleWidget meter timers, close, and deleteLater Qt widgets."""
    for w in reversed(widgets):
        if w is None:
            continue
        meter_timer = getattr(w, "meter_timer", None)
        if meter_timer is not None:
            try:
                meter_timer.stop()
            except Exception:
                pass
        try:
            w.close()
        except Exception:
            pass
        try:
            w.deleteLater()
        except Exception:
            pass
    if qapp is not None:
        qapp.processEvents()
        qapp.processEvents()


@pytest.fixture
def qt_widget_cleanup(qapp):
    """Register Qt widgets during a test and cleanly tear them down without quitting qapp."""
    widgets: list[Any] = []

    def _register(widget: Any) -> Any:
        widgets.append(widget)
        return widget

    yield _register
    cleanup_qt_widgets(widgets, qapp)
