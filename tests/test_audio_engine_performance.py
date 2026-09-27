"""
tests/test_audio_engine_performance.py - Validation des performances et du mode silencieux de l'AudioEngine
"""
import os
import time
import pytest
import numpy as np

# Force silent mode for tests
os.environ["NOVADAW_SILENT_TESTS"] = "1"

from core.audio_engine import AudioEngine
from core.project import Project, Track, MidiClip, MidiNote


def test_audio_engine_silent_stream():
    """Vérifie que l'AudioEngine n'ouvre aucun flux audio physique en mode silencieux."""
    engine = AudioEngine(sample_rate=44100, block_size=1024)
    assert engine._stream is None, "Le flux audio matériel ne doit pas être ouvert en mode silencieux"
    engine.close()


def test_audio_engine_callback_budget():
    """Vérifie que le traitement audio en multipiste respecte le budget temps réel (aucun overrun)."""
    engine = AudioEngine(sample_rate=44100, block_size=1024)
    project = Project(name="PerfTest")
    project.bpm = 128.0

    # Piste batterie
    t_drum = Track(name="drum", track_type="midi")
    t_drum.plugin_path = "novadaw.drum_machine"
    clip_drum = MidiClip(name="Drums", start_beat=0.0, length_beats=16.0)
    for b in range(16):
        clip_drum.notes.append(MidiNote(pitch=36, start_beat=b * 1.0, duration=0.25, velocity=110))
        clip_drum.notes.append(MidiNote(pitch=38, start_beat=b * 1.0 + 0.5, duration=0.25, velocity=105))
        clip_drum.notes.append(MidiNote(pitch=42, start_beat=b * 1.0 + 0.25, duration=0.2, velocity=90))
    t_drum.clips.append(clip_drum)
    project.tracks.append(t_drum)

    # Piste synthé
    t_synth = Track(name="synth", track_type="midi")
    t_synth.plugin_path = "novadaw.synth"
    clip_synth = MidiClip(name="Synth", start_beat=0.0, length_beats=16.0)
    for b in range(16):
        clip_synth.notes.append(MidiNote(pitch=62, start_beat=b * 1.0, duration=0.8, velocity=100))
        clip_synth.notes.append(MidiNote(pitch=65, start_beat=b * 1.0, duration=0.8, velocity=100))
        clip_synth.notes.append(MidiNote(pitch=69, start_beat=b * 1.0, duration=0.8, velocity=100))
    t_synth.clips.append(clip_synth)
    project.tracks.append(t_synth)

    engine.set_project(project)
    drum_inst = engine.get_native_instrument(t_drum)
    drum_inst.apply_preset("Punchy Rock")
    drum_inst.warm_up_cache(async_bg=False)

    synth_inst = engine.get_native_instrument(t_synth)
    synth_inst.apply_preset("Neon Horizon SuperSaw")

    engine.play()
    engine.current_beat = 4.0

    frames = 1024
    outdata = np.zeros((frames, 2), dtype=np.float32)

    # Mesurer 30 blocs audio
    times = []
    for _ in range(30):
        t0 = time.perf_counter()
        engine._audio_callback(outdata, frames, None, None)
        t1 = time.perf_counter()
        times.append((t1 - t0) * 1000)

    engine.close()

    budget_ms = (frames / 44100) * 1000  # 23.22 ms
    avg_ms = sum(times) / len(times)
    assert avg_ms < budget_ms, f"Temps moyen de callback ({avg_ms:.2f} ms) dépasse le budget ({budget_ms:.2f} ms)"
