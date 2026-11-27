"""Reusable opaque-box measurement and verification helpers for E2E playback tests."""

from __future__ import annotations

import importlib
import math
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import soundfile as sf

from core.serializer import load_project
from plugins.registry import ensure_plugins_loaded

REPO_ROOT = Path(__file__).resolve().parents[2]
SOUS_LA_CENDRE_PATH = (
    r"C:\Users\Utilisateur\Google Drive\Ma musique\avec NovaDAW\test full band\test full band_001.ndaw"
)


@dataclass
class CallbackTimingStats:
    block_size: int
    sample_rate: int
    budget_ms: float
    median_ms: float
    p95_ms: float
    max_ms: float
    avg_ms: float
    cpu_percent: float
    overruns: int
    total_blocks: int
    non_finite_count: int
    peak_abs: float
    rms: float
    elapsed_samples: int


def load_sous_la_cendre(
    path: Optional[Path | str] = None,
    unsolo: bool = True,
) -> Tuple[Any, Dict[str, Any]]:
    """Load the 'Sous la cendre' .ndaw project in memory without altering the disk file."""
    ensure_plugins_loaded()
    target = Path(path or SOUS_LA_CENDRE_PATH)
    project = load_project(str(target))
    clip_count = 0
    max_beat = 0.0
    vst_count = 0

    for track in project.tracks:
        if unsolo:
            track.muted = False
            track.soloed = False
        clip_count += len(track.clips)
        vst_count += len(getattr(track, "insert_effects", []) or [])
        for clip in track.clips:
            max_beat = max(max_beat, float(clip.start_beat) + float(clip.length_beats))

    if getattr(project, "master_track", None) is not None:
        vst_count += len(getattr(project.master_track, "insert_effects", []) or [])

    metadata = {
        "name": project.name,
        "bpm": float(project.bpm),
        "time_sig_num": int(project.time_sig_num),
        "time_sig_den": int(project.time_sig_den),
        "track_count": len(project.tracks),
        "clip_count": clip_count,
        "total_beats": max(max_beat, float(getattr(project, "loop_end_beat", 0.0))),
        "external_vst_count": vst_count,
    }
    return project, metadata


def measure_asio_guard_warmup(
    engine: Any,
    timeout_sec: float = 60.0,
) -> Dict[str, Any]:
    """Trigger ASIO-Guard warmup and wait until all eligible tracks finish caching."""
    project = getattr(engine, "project", None)
    if project is None:
        return {"elapsed_sec": 0.0, "timed_out": False, "track_status": {}}

    t0 = time.perf_counter()

    # Support future M2 wait_for_track_cache helper if present.
    wait_fn = getattr(engine, "wait_for_track_cache", None)
    if callable(wait_fn):
        engine._asio_guard_enabled = True
        engine._ensure_tracks_cached_async(force=True)
        try:
            wait_fn(timeout=timeout_sec)
        except TypeError:
            wait_fn(timeout_sec)
        elapsed = time.perf_counter() - t0
    else:
        engine._asio_guard_enabled = True
        engine._ensure_tracks_cached_async(force=True)
        deadline = t0 + timeout_sec
        eligible_ids = [t.id for t in project.tracks if not getattr(t, "armed", False)]

        while time.perf_counter() < deadline:
            worker = getattr(engine, "_cache_worker", None)
            with engine._track_cache_lock:
                caches = engine._track_audio_caches
                all_ready = all(
                    (tid in caches) and bool(caches[tid].get("fully_rendered", False))
                    for tid in eligible_ids
                )
            if all_ready and (worker is None or not worker.is_alive()):
                break
            time.sleep(0.01)

        worker = getattr(engine, "_cache_worker", None)
        if worker is not None and worker.is_alive():
            remaining = max(0.0, deadline - time.perf_counter())
            worker.join(timeout=remaining)
        elapsed = time.perf_counter() - t0

    track_status: Dict[str, Dict[str, Any]] = {}
    timed_out = False
    with engine._track_cache_lock:
        for track in project.tracks:
            if getattr(track, "armed", False):
                track_status[track.id] = {"name": track.name, "armed": True, "fully_rendered": False, "rendered_frames": 0}
                continue
            c = engine._track_audio_caches.get(track.id)
            fully = bool(c and c.get("fully_rendered", False))
            frames = int(c.get("rendered_frames", 0)) if c else 0
            if not fully:
                timed_out = True
            track_status[track.id] = {
                "name": track.name,
                "armed": False,
                "fully_rendered": fully,
                "rendered_frames": frames,
            }

    return {
        "elapsed_sec": float(elapsed),
        "timed_out": timed_out,
        "track_status": track_status,
    }


def measure_callback_timing(
    engine: Any,
    block_size: int,
    start_beat: float = 132.0,
    num_blocks: int = 40,
    warmup_asio_guard: bool = False,
    warmup_timeout_sec: float = 45.0,
) -> CallbackTimingStats:
    """Drive real AudioEngine._audio_callback blocks and collect timing & signal stats."""
    if block_size <= 0 or num_blocks <= 0:
        raise ValueError("block_size and num_blocks must be positive")

    if warmup_asio_guard:
        measure_asio_guard_warmup(engine, timeout_sec=warmup_timeout_sec)

    engine.play()
    engine.seek_beat(float(start_beat))

    out = np.zeros((block_size, 2), dtype=np.float32)
    times_ms: List[float] = []
    non_finite_count = 0
    peak_abs = 0.0
    sum_sq = 0.0
    total_scalars = 0

    try:
        for _ in range(num_blocks):
            out.fill(0.0)
            t0 = time.perf_counter()
            engine._audio_callback(out, block_size, None, None)
            dt_ms = (time.perf_counter() - t0) * 1000.0
            times_ms.append(dt_ms)

            finite_mask = np.isfinite(out)
            if not np.all(finite_mask):
                non_finite_count += int(np.size(out) - np.count_nonzero(finite_mask))
                clean = np.where(finite_mask, out, 0.0)
            else:
                clean = out

            block_peak = float(np.max(np.abs(clean)))
            if block_peak > peak_abs:
                peak_abs = block_peak
            sum_sq += float(np.sum(clean.astype(np.float64) ** 2))
            total_scalars += int(clean.size)
    finally:
        engine.pause()

    sample_rate = int(engine.sample_rate)
    budget_ms = (float(block_size) / float(sample_rate)) * 1000.0
    arr = np.asarray(times_ms, dtype=np.float64)
    avg_ms = float(np.mean(arr))
    median_ms = float(np.median(arr))
    p95_ms = float(np.percentile(arr, 95))
    max_ms = float(np.max(arr))
    overruns = int(np.sum(arr > budget_ms))
    cpu_percent = (avg_ms / budget_ms) * 100.0 if budget_ms > 0.0 else 0.0
    rms = math.sqrt(sum_sq / max(1, total_scalars))

    return CallbackTimingStats(
        block_size=int(block_size),
        sample_rate=sample_rate,
        budget_ms=budget_ms,
        median_ms=median_ms,
        p95_ms=p95_ms,
        max_ms=max_ms,
        avg_ms=avg_ms,
        cpu_percent=cpu_percent,
        overruns=overruns,
        total_blocks=int(num_blocks),
        non_finite_count=non_finite_count,
        peak_abs=peak_abs,
        rms=rms,
        elapsed_samples=int(num_blocks * block_size),
    )


def export_wav_array(engine: Any, wav_path: Path | str, end_bar: int = 8) -> Tuple[np.ndarray, int]:
    """Export project mixdown to WAV via AudioEngine.export_wav and read back float32 array."""
    target = Path(wav_path)
    ok = engine.export_wav(str(target), end_bar=end_bar)
    if not ok or not target.is_file():
        raise RuntimeError(f"AudioEngine.export_wav failed for {target}")
    audio, sr = sf.read(str(target), dtype="float32")
    return np.asarray(audio, dtype=np.float32), int(sr)


def compute_rmse(a: np.ndarray, b: np.ndarray) -> float:
    """Compute root-mean-square error between two equal-shaped audio arrays."""
    arr_a = np.asarray(a, dtype=np.float64)
    arr_b = np.asarray(b, dtype=np.float64)
    if arr_a.shape != arr_b.shape:
        raise ValueError(f"Shape mismatch for RMSE: {arr_a.shape} vs {arr_b.shape}")
    if arr_a.size == 0:
        return 0.0
    return float(np.sqrt(np.mean((arr_a - arr_b) ** 2)))


def _read_requirement_lines(req_path: Path) -> List[str]:
    if not req_path.is_file():
        return []
    lines: List[str] = []
    for raw_line in req_path.read_text(encoding="utf-8", errors="replace").splitlines():
        # Strip only full-line or whitespace-preceded comments; preserve VCS #egg= fragments.
        line = re.split(r"(?:^|\s)#", raw_line, maxsplit=1)[0].strip()
        if line:
            lines.append(line)
    return lines


def _parse_requirements_file(req_path: Path) -> List[str]:
    pkgs: List[str] = []
    for line in _read_requirement_lines(req_path):
        if line.startswith("-r ") or line.startswith("--requirement"):
            continue
        egg_match = re.search(r"[#&]egg=([A-Za-z0-9_.\-]+)", line)
        if egg_match:
            pkgs.append(egg_match.group(1).lower())
            continue
        cleaned = re.sub(r"^(?:-e|--editable)(?:\s+|=)", "", line).strip()
        if cleaned.startswith("-"):
            continue
        match = re.match(r"^([A-Za-z0-9_.\-]+)", cleaned)
        if match:
            pkgs.append(match.group(1).lower())
    return pkgs


def inspect_mit_license_compliance(repo_root: Path | str = REPO_ROOT) -> Dict[str, Any]:
    """Verify MIT license and absence of pedalboard/JUCE/GPLv3 runtime dependencies."""
    root = Path(repo_root)
    license_path = root / "LICENSE"
    req_path = root / "requirements.txt"
    req_dev_path = root / "requirements-dev.txt"
    third_party_path = root / "THIRD_PARTY_LICENSES.md"

    reasons: List[str] = []

    license_text = license_path.read_text(encoding="utf-8", errors="replace") if license_path.is_file() else ""
    mit_present = ("mit license" in license_text.lower()) and (
        "permission is hereby granted, free of charge" in license_text.lower()
    )
    if not mit_present:
        reasons.append("Root LICENSE file missing or not MIT License")

    runtime_pkgs = _parse_requirements_file(req_path)
    dev_pkgs = _parse_requirements_file(req_dev_path)
    all_pkgs = runtime_pkgs + dev_pkgs

    forbidden_markers = ("pedalboard", "juce", "dawdreamer", "gpl", "pyaudio-gpl")
    raw_req_lines = _read_requirement_lines(req_path) + _read_requirement_lines(req_dev_path)
    raw_line_hits = [
        marker
        for line in raw_req_lines
        for marker in forbidden_markers
        if marker in line.lower() and not line.lower().startswith("-r ")
    ]
    forbidden_found = sorted(
        set(
            [pkg for pkg in all_pkgs if any(marker in pkg for marker in forbidden_markers)]
            + raw_line_hits
        )
    )
    if forbidden_found:
        reasons.append(f"Forbidden audio/GPL packages found: {forbidden_found}")

    third_party_text = (
        third_party_path.read_text(encoding="utf-8", errors="replace")
        if third_party_path.is_file()
        else ""
    )
    tp_lower = re.sub(r"[*_`]+", "", third_party_text.lower())
    no_gplv3_runtime = (
        ("aucune dépendance gplv3 ou agpl" in tp_lower or "no gplv3" in tp_lower)
        and ("pedalboard" not in tp_lower)
    )
    if not no_gplv3_runtime:
        reasons.append("THIRD_PARTY_LICENSES.md missing explicit zero-GPLv3/AGPL statement or references forbidden dep")

    return {
        "is_compliant": bool(mit_present and not forbidden_found and no_gplv3_runtime),
        "mit_license_present": mit_present,
        "runtime_packages": runtime_pkgs,
        "dev_packages": dev_pkgs,
        "forbidden_packages": forbidden_found,
        "third_party_no_gplv3": no_gplv3_runtime,
        "reasons": reasons,
    }


def inspect_native_dsp_support() -> Dict[str, Any]:
    """Dynamically inspect core.dsp_native and core/bin/nova_dsp.dll availability."""
    dll_path = REPO_ROOT / "core" / "bin" / "nova_dsp.dll"
    c_path = REPO_ROOT / "core" / "bin" / "nova_dsp.c"
    py_path = REPO_ROOT / "core" / "dsp_native.py"

    module = None
    import_error = None
    native_active = False

    if py_path.is_file():
        try:
            module = importlib.import_module("core.dsp_native")
            is_avail_fn = getattr(module, "is_available", None)
            if callable(is_avail_fn):
                native_active = bool(is_avail_fn())
            else:
                native_active = bool(
                    getattr(module, "HAS_NATIVE_DSP", False)
                    or getattr(module, "NATIVE_AVAILABLE", False)
                    or getattr(module, "AVAILABLE", False)
                    or getattr(module, "_lib", None) is not None
                )
        except Exception as exc:
            import_error = repr(exc)

    return {
        "module_file_exists": py_path.is_file(),
        "c_source_exists": c_path.is_file(),
        "dll_exists": dll_path.is_file(),
        "dll_path": str(dll_path),
        "native_active": native_active,
        "import_error": import_error,
        "module": module,
    }
