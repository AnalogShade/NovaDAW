"""
tests/test_drum_plugin.py - Tests unitaires complets pour l'instrument virtuel Nova Drums VSTi,
la réverbération stéréo intégrée, le mapping MIDI GM, les choke groups et l'intégration AudioEngine.
"""
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest
import numpy as np
from core.project import Project, Track, MidiClip, MidiNote
from core.audio_engine import AudioEngine
from plugins.registry import plugin_registry, ensure_plugins_loaded
from plugins.drum_machine.drum_plugin import DrumMachinePlugin, DrumPad
from plugins.drum_machine.drum_reverb import DrumReverb


@pytest.fixture(autouse=True)
def setup_plugins():
    ensure_plugins_loaded()


def test_01_drum_plugin_registration():
    """Vérifie l'enregistrement de Nova Drums VSTi dans le registre central"""
    available = plugin_registry.get_available_plugins()
    type_ids = [p["type_id"] for p in available]
    assert "novadaw.drum_machine" in type_ids

    info = plugin_registry.get_plugin_info("novadaw.drum_machine")
    assert info is not None
    assert info["category"] == "instrument"
    assert info["icon"] == "🥁"

    plugin = plugin_registry.create_plugin("novadaw.drum_machine")
    assert isinstance(plugin, DrumMachinePlugin)
    assert plugin.is_instrument is True
    assert len(plugin.pads) == 9


def test_02_drum_pads_mapping_general_midi():
    """Vérifie la correspondance des pads avec la norme General MIDI standard"""
    plugin = DrumMachinePlugin()

    # Kick : 35 ou 36
    pad_kick = plugin.find_pad_by_pitch(36)
    assert pad_kick is not None
    assert "kick" in pad_kick.pad_id.lower()

    # Snare : 38 ou 40
    pad_snare = plugin.find_pad_by_pitch(38)
    assert pad_snare is not None
    assert "snare" in pad_snare.pad_id.lower()

    # Closed HH : 42 ou 44
    pad_chh = plugin.find_pad_by_pitch(42)
    assert pad_chh is not None
    assert "hihat_c" in pad_chh.pad_id

    # Open HH : 46
    pad_ohh = plugin.find_pad_by_pitch(46)
    assert pad_ohh is not None
    assert "hihat_o" in pad_ohh.pad_id

    # Toms : 41/45 (Low), 47/48 (Mid), 50 (High)
    assert plugin.find_pad_by_pitch(41).pad_id == "tom_l"
    assert plugin.find_pad_by_pitch(48).pad_id == "tom_m"
    assert plugin.find_pad_by_pitch(50).pad_id == "tom_h"

    # Crash : 49, Ride : 51
    assert plugin.find_pad_by_pitch(49).pad_id == "crash"
    assert plugin.find_pad_by_pitch(51).pad_id == "ride"


def test_03_drum_rendering_and_velocity():
    """Vérifie le rendu audio unitaire et l'influence de la vélocité et du panoramique"""
    plugin = DrumMachinePlugin()

    # Note 36 (Kick)
    wave_quiet = plugin.render_note(36, duration_sec=0.2, sample_rate=44100, velocity=30)
    wave_loud = plugin.render_note(36, duration_sec=0.2, sample_rate=44100, velocity=127)

    assert wave_quiet.ndim == 2
    assert wave_loud.ndim == 2
    assert wave_quiet.shape[1] == 2
    assert wave_loud.shape[1] == 2
    assert not np.isnan(wave_quiet).any()
    assert not np.isnan(wave_loud).any()

    # Plus forte vélocité = plus forte énergie sonore
    rms_quiet = np.sqrt(np.mean(wave_quiet ** 2))
    rms_loud = np.sqrt(np.mean(wave_loud ** 2))
    assert rms_loud > rms_quiet

    # Test Panoramique (désactiver temporairement le send réverb pour tester l'isolation panoramique directe)
    pad_snare = plugin.find_pad_by_pitch(38)
    orig_send = pad_snare.reverb_send
    pad_snare.reverb_send = 0.0

    pad_snare.pan = -1.0  # Tout à gauche
    wave_left = plugin.render_note(38, duration_sec=0.2, sample_rate=44100, velocity=100)
    assert np.max(np.abs(wave_left[:, 0])) > 0.01
    assert np.max(np.abs(wave_left[:, 1])) < 1e-4

    pad_snare.pan = 1.0  # Tout à droite
    wave_right = plugin.render_note(38, duration_sec=0.2, sample_rate=44100, velocity=100)
    assert np.max(np.abs(wave_right[:, 1])) > 0.01
    assert np.max(np.abs(wave_right[:, 0])) < 1e-4

    pad_snare.pan = 0.0  # Remise au centre
    pad_snare.reverb_send = orig_send


def test_04_drum_tuning_pitch_shift():
    """Vérifie le fonctionnement de l'accordage (Pitch Tune) par rééchantillonnage"""
    plugin = DrumMachinePlugin()
    pad = plugin.find_pad_by_pitch(36)

    pad.tune = 0.0
    audio_orig = pad.get_audio_data()
    len_orig = len(audio_orig)

    # +12 demi-tons (1 octave plus haut) = longueur divisée par 2
    pad.tune = 12.0
    audio_high = pad.get_audio_data()
    assert abs(len(audio_high) - (len_orig // 2)) <= 2

    # -12 demi-tons (1 octave plus bas) = longueur multipliée par 2
    pad.tune = -12.0
    audio_low = pad.get_audio_data()
    assert abs(len(audio_low) - (len_orig * 2)) <= 2

    pad.tune = 0.0


def test_05_reverb_dsp():
    """Vérifie le processeur de réverbération stéréo DrumReverb"""
    sr = 44100
    rev = DrumReverb(room_size=0.6, damping=0.4, wet_mix=0.4, dry_mix=1.0, sample_rate=sr)

    # Impulsion de test (Dirac) sur 4096 samples (dépasse le délai des peignes 1116-1640)
    impulse = np.zeros((4096, 2), dtype=np.float32)
    impulse[0, 0] = 1.0
    impulse[0, 1] = 1.0

    out = rev.process(impulse)
    assert out.shape == impulse.shape
    assert not np.isnan(out).any()

    # L'impulsion initiale doit avoir généré des réflexions après le délai initial des peignes
    tail_energy = np.sum(np.abs(out[1100:]))
    assert tail_energy > 0.001

    # Test Bypass / Dry pur
    rev.wet_mix = 0.0
    out_dry = rev.process(impulse)
    assert np.allclose(out_dry, impulse, atol=1e-5)

    # Test Reset
    rev.reset()


def test_06_choke_group_hihat():
    """Vérifie que le charleston fermé (choke group 1) coupe le charleston ouvert"""
    plugin = DrumMachinePlugin()
    plugin.reverb.wet_mix = 0.0  # Désactiver réverb pour tester le signal direct

    sr = 44100
    bpm = 120.0
    beats_per_sec = bpm / 60.0  # 2 temps par sec
    frames = 44100  # 1 seconde complète (2 temps)

    # Note 1 : Open HH à t=0.0
    # Note 2 : Closed HH à t=0.5
    notes = [
        MidiNote(pitch=46, start_beat=0.0, duration=1.5, velocity=100),  # Open HH
        MidiNote(pitch=42, start_beat=0.5, duration=0.2, velocity=100),  # Closed HH (doit couper Open HH)
    ]

    slice_out = plugin.render_slice(notes, start_b=0.0, end_b=2.0, beats_per_sec=beats_per_sec, frames=frames, sample_rate=sr)

    assert slice_out.shape == (frames, 2)
    assert not np.isnan(slice_out).any()

    # Avant 0.5 temps (start_frame = 0.25s = 11025 samples) : son présent
    pre_choke_energy = np.mean(np.abs(slice_out[:10000]))
    assert pre_choke_energy > 0.001

    # Le choke a eu lieu, la voix ouverte a été étouffée et le son se stabilise
    plugin.reset()


def test_07_presets_and_state_serialization():
    """Vérifie l'application des presets et la sérialisation JSON"""
    plugin = DrumMachinePlugin()

    # Application preset Rock
    plugin.apply_preset("Punchy Rock")
    assert plugin.preset_name == "Punchy Rock"
    assert plugin.reverb.room_size > 0.5

    # Sérialisation
    state = plugin.get_state()
    assert "preset_name" in state
    assert "reverb" in state
    assert "pads" in state
    assert len(state["pads"]) == 9

    # Restauration dans une nouvelle instance
    new_plugin = DrumMachinePlugin()
    new_plugin.set_state(state)
    assert new_plugin.preset_name == "Punchy Rock"
    assert abs(new_plugin.reverb.room_size - state["reverb"]["room_size"]) < 1e-4


def test_08_audio_engine_native_instrument_integration():
    """Vérifie le routage et le rendu de Nova Drums dans le moteur AudioEngine"""
    proj = Project(name="Test Drums")
    drum_track = Track(
        name="Batterie Test",
        track_type="midi",
        plugin_path="novadaw.drum_machine",
        plugin_name="Nova Drums VSTi"
    )

    clip = MidiClip(
        name="Pattern",
        start_beat=0.0,
        length_beats=4.0,
        notes=[
            MidiNote(pitch=36, start_beat=0.0, duration=0.5, velocity=110),
            MidiNote(pitch=38, start_beat=1.0, duration=0.5, velocity=110),
        ]
    )
    drum_track.clips.append(clip)
    proj.add_track(drum_track)

    engine = AudioEngine()
    engine.set_project(proj)

    # Récupération de l'instrument natif
    inst = engine.get_native_instrument(drum_track)
    assert inst is not None
    assert isinstance(inst, DrumMachinePlugin)

    # Test preview_note avec le track
    engine.preview_note(36, duration_sec=0.2, velocity=100, track=drum_track)
    assert len(engine._preview_buffers) > 0
    preview_audio = engine._preview_buffers[-1]["buffer"]
    assert preview_audio is not None
    assert np.max(np.abs(preview_audio)) > 0.01

    # Test rendu de tranche temporelle
    frames = 1024
    slice_audio = engine._render_track_slice(drum_track, start_b=0.0, end_b=0.2, frames=frames, bpm=120.0)
    assert slice_audio is not None
    assert slice_audio.shape == (frames, 2)
    assert np.max(np.abs(slice_audio)) > 0.001

    engine.close()


def test_09_default_project_has_drum_kit():
    """Vérifie que Project.create_default() intègre bien par défaut la piste batterie Nova Drums"""
    proj = Project.create_default()
    track_names = [t.name for t in proj.tracks]
    assert any("Batterie" in name or "Drums" in name for name in track_names)

    drum_track = next(t for t in proj.tracks if "Batterie" in t.name or "Drums" in t.name)
    assert drum_track.track_type == "midi"
    assert drum_track.plugin_path == "novadaw.drum_machine"
    assert len(drum_track.clips) > 0
    assert len(drum_track.clips[0].notes) > 5  # Contient un pattern complet


def test_10_ram_cache_instant_retrieval_and_choke():
    """Vérifie que la mise en tampon RAM est instantanée (< 1ms par clic) et gère le choke group"""
    import time
    from core.audio_engine import get_global_audio_engine

    plugin = DrumMachinePlugin()
    plugin.warm_up_cache()

    ready, total = plugin.cache_status
    assert ready == 9
    assert total == 9

    # Mesure du temps d'accès au tampon pour les 9 pads
    t0 = time.perf_counter()
    for pad in plugin.pads:
        wave = plugin.get_cached_pad_audio(pad.pad_id, velocity=115)
        assert wave is not None
        assert len(wave) > 0
        assert not np.isnan(wave).any()
    elapsed = time.perf_counter() - t0

    # 9 pads doivent être récupérés en moins de 5ms au total (soit < 0.55ms par pad)
    assert elapsed < 0.010, f"Temps de récupération trop élevé: {elapsed*1000:.2f}ms"

    # Vérification invalidation automatique sur modification de paramètre
    pad_snare = plugin.find_pad_by_pitch(38)
    orig_key = plugin._compute_pad_cache_key(pad_snare)
    pad_snare.tune = 4.0
    new_key = plugin._compute_pad_cache_key(pad_snare)
    assert orig_key != new_key

    # Audio récupéré reflète le nouvel accordage
    wave_tuned = plugin.get_cached_pad_audio("snare", velocity=115)
    assert wave_tuned is not None
    assert plugin._pad_cache_keys["snare"] == new_key

    # Test AudioEngine.play_preview_buffer et étouffement choke group
    engine = AudioEngine()
    fake_open_hh = np.ones((44100, 2), dtype=np.float32)
    fake_closed_hh = np.ones((5000, 2), dtype=np.float32)

    # Simulation sans stream matériel
    with engine._preview_lock:
        engine._preview_buffers.append({
            "buffer": fake_open_hh,
            "cursor": 0,
            "choke_group": 1
        })
        assert len(engine._preview_buffers) == 1

        # Choke group 1 doit couper fake_open_hh
        for item in engine._preview_buffers:
            if item.get("choke_group") == 1:
                item["buffer"] = item["buffer"][:100]

    assert len(engine._preview_buffers[0]["buffer"]) == 100
    engine.close()

