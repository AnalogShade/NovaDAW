"""Silent, paced callback benchmark with an optional Git baseline.

python scripts/benchmark_audio_startup.py --project "recordings/song.ndaw" \
    --baseline-ref 3f1b250 --output scratch/audio-startup-comparison.json

Measures software callbacks, not physical driver latency or captured dropouts.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import types

os.environ["NOVADAW_SILENT_TESTS"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from core.audio_engine import AudioEngine
from core.serializer import load_project


def measure(engine_class, project_path, seconds):
    project = load_project(str(project_path))
    engine = engine_class(sample_rate=44100, block_size=256, output_device=0, input_device=0)
    engine._asio_guard_enabled = False
    engine.set_project(project)
    engine._asio_guard_enabled = True
    start = time.perf_counter()
    worker = threading.Thread(target=engine._ensure_tracks_cached)
    worker.start()
    engine.is_playing = True
    output = np.zeros((256, 2), dtype=np.float32)
    times = []
    prefix_ready = None
    late = 0
    try:
        for index in range(int(seconds * 44100 / 256)):
            with engine._track_cache_lock:
                caches = engine._track_audio_caches
                ready = len(caches) == len(project.tracks) and all(
                    cache.get("rendered_frames", len(cache["audio"]) if cache.get("fully_rendered") else 0) >= 4096
                    for cache in caches.values()
                )
            if ready and prefix_ready is None:
                prefix_ready = time.perf_counter() - start
            block_start = time.perf_counter()
            engine._audio_callback(output, 256, None, None)
            times.append((time.perf_counter() - block_start) * 1000)
            remaining = start + (index + 1) * 256 / 44100 - time.perf_counter()
            if remaining > 0:
                time.sleep(remaining)
            else:
                late += 1
        engine.is_playing = False
        worker.join()
        return {
            "first_prefix_all_tracks_seconds": prefix_ready,
            "full_cache_seconds": time.perf_counter() - start,
            "callback_median_ms": float(np.median(times)),
            "callback_p95_ms": float(np.percentile(times, 95)),
            "callback_max_ms": max(times),
            "callback_over_budget": sum(t > 256 / 44100 * 1000 for t in times),
            "blocks": len(times), "schedule_late_blocks": late,
        }
    finally:
        engine.is_playing = False
        engine.close()
        worker.join()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--seconds", type=float, default=3.0)
    parser.add_argument("--baseline-ref")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.seconds <= 0:
        parser.error("--seconds must be positive")
    versions = []
    if args.baseline_ref:
        source = subprocess.check_output(
            ["git", "show", f"{args.baseline_ref}:core/audio_engine.py"],
            cwd=Path(__file__).resolve().parents[1],
        ).decode("utf-8")
        module = types.ModuleType("nova_baseline_engine")
        sys.modules[module.__name__] = module
        exec(compile(source, "baseline/audio_engine.py", "exec"), module.__dict__)
        versions.append((args.baseline_ref, module.AudioEngine))
    versions.append(("working-tree", AudioEngine))
    results = []
    for label, cls in versions:
        result = {"version": label, **measure(cls, args.project, args.seconds)}
        results.append(result)
        print(json.dumps(result), flush=True)
    if args.output:
        args.output.write_text(json.dumps(results, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
