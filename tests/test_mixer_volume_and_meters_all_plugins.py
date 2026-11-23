"""
tests/test_mixer_volume_and_meters_all_plugins.py - Vérification complète du volume de piste
du Mixeur et des VU-mètres dans toutes les situations et avec tous les plugins (Nova Drums,
NovaSynth, Nova Guitar, piles multi-plugins EQ/Compresseur/Amp/Vocal Glide, lecture, live, audition).
"""
import os
import sys
import numpy as np
import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from PySide6.QtWidgets import QApplication

from core.audio_engine import AudioEngine
from core.project import Project, Track, MidiClip, MidiNote
from plugins.registry import plugin_registry, ensure_plugins_loaded
from plugins.mixer.mixer_gui import MixerConsoleWidget
from ui.plugin_dialogs import open_native_plugin_editor, find_track_for_plugin


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture(autouse=True)
def _load_plugins():
    ensure_plugins_loaded()


class _DummyActiveStream:
    active = True


def _setup_engine_with_active_stream(project: Project) -> AudioEngine:
    engine = AudioEngine(sample_rate=44100, block_size=256)
    engine.set_project(project)
    # Simuler un flux audio matériel actif pour tester _audio_callback et play_preview_buffer
    if not (engine._stream and getattr(engine._stream, "active", False)):
        engine._stream = _DummyActiveStream()
    return engine


def test_nova_drums_gui_pad_respects_mixer_volume_mute_solo_and_meter(qapp):
    """Dans l'interface de Nova Drums, le volume du mixeur et le meter de la piste doivent réagir."""
    proj = Project.create_empty("Test Nova Drums Mixer")
    drum_track = Track(
        name="Batterie",
        track_type="midi",
        volume=0.8,
        plugin_path="novadaw.drum_machine",
        plugin_name="Nova Drums VSTi",
    )
    drums = plugin_registry.create_plugin("novadaw.drum_machine")
    drum_track.add_plugin(drums)
    other_track = Track(name="Autre", track_type="midi", volume=0.8)
    proj.add_track(drum_track)
    proj.add_track(other_track)

    master = proj.ensure_master_track()
    mixer = next(p for p in master.plugins if getattr(p, "plugin_type_id", None) == "novadaw.mixer")
    console = MixerConsoleWidget(mixer)
    strip = console.strips[drum_track.id]

    engine = _setup_engine_with_active_stream(proj)
    dlg = open_native_plugin_editor(drums, track=drum_track)
    try:
        drum_gui = dlg.editor
        kick_pad = next(p for p in drums.pads if p.pad_id == "kick")

        # 1. Volume à 100% via le fader du Mixeur -> signal audible + meter oscille
        strip.slider_vol.setValue(100)
        assert abs(drum_track.volume - 1.0) < 1e-4

        drum_gui._play_pad(kick_pad)
        out_full = np.zeros((256, 2), dtype=np.float32)
        engine._audio_callback(out_full, 256, None, None)
        rms_full = float(np.sqrt(np.mean(out_full ** 2)))
        assert rms_full > 0.005
        assert drum_track.id in mixer.track_peaks
        peak_full_l, _ = mixer.track_peaks[drum_track.id]
        assert peak_full_l > -45.0

        console._update_meters()
        assert strip.vu_meter.peak_left_db > -45.0
        assert strip.lbl_peak_db.text() != "-∞ dB"

        # 2. Changement du fader Mixeur EN TEMPS RÉEL pendant que le son résonne (100% -> 0%)
        strip.slider_vol.setValue(0)
        assert drum_track.volume == 0.0
        out_zero_mid = np.zeros((256, 2), dtype=np.float32)
        engine._audio_callback(out_zero_mid, 256, None, None)
        assert np.allclose(out_zero_mid, 0.0)

        # 3. Nouvelle frappe avec volume à 20% -> niveau proportionnellement plus bas qu'à 100%
        with engine._preview_lock:
            engine._preview_buffers.clear()
        mixer.reset()
        strip.slider_vol.setValue(20)
        drum_gui._play_pad(kick_pad)
        out_low = np.zeros((256, 2), dtype=np.float32)
        engine._audio_callback(out_low, 256, None, None)
        rms_low = float(np.sqrt(np.mean(out_low ** 2)))
        assert 0.0 < rms_low < rms_full * 0.35

        # 4. Mute dans le Mixeur -> silence total
        strip.btn_m.click()
        assert drum_track.muted is True
        drum_gui._play_pad(kick_pad)
        out_muted = np.zeros((256, 2), dtype=np.float32)
        engine._audio_callback(out_muted, 256, None, None)
        assert np.allclose(out_muted, 0.0)
        strip.btn_m.click()

        # 5. Solo sur une autre piste -> la piste batterie est coupée
        console.strips[other_track.id].btn_s.click()
        drum_gui._play_pad(kick_pad)
        out_unsolo = np.zeros((256, 2), dtype=np.float32)
        engine._audio_callback(out_unsolo, 256, None, None)
        assert np.allclose(out_unsolo, 0.0)
    finally:
        dlg.close()
        console.close()
        engine.close()


def test_nova_drums_fallback_player_respects_track_volume_and_meter(qapp, monkeypatch):
    """Même sans flux audio matériel actif (mode fallback), le volume et le meter sont respectés."""
    proj = Project.create_empty("Test Fallback Drums")
    drum_track = Track(
        name="Batterie Fallback",
        track_type="midi",
        volume=0.0,
        plugin_path="novadaw.drum_machine",
        plugin_name="Nova Drums VSTi",
    )
    drums = plugin_registry.create_plugin("novadaw.drum_machine")
    drum_track.add_plugin(drums)
    proj.add_track(drum_track)
    proj.ensure_master_track()

    engine = AudioEngine(sample_rate=44100, block_size=256)
    engine.set_project(proj)
    engine._stream = None  # Force fallback path

    played = []
    from plugins.drum_machine import drum_gui as drum_gui_mod
    monkeypatch.setattr(
        drum_gui_mod.LowLatencySoundPlayer,
        "get_instance",
        classmethod(lambda cls: type("MockPlayer", (), {"play": lambda self, buf, choke_group=0: played.append(buf.copy())})()),
    )

    dlg = open_native_plugin_editor(drums, track=drum_track)
    try:
        kick_pad = next(p for p in drums.pads if p.pad_id == "kick")
        drum_gui = dlg.editor

        # Volume = 0 -> buffer joué nul
        drum_track.volume = 0.0
        drum_gui._play_pad(kick_pad)
        assert len(played) == 1
        assert np.allclose(played[-1], 0.0)

        # Volume = 0.75 -> buffer joué non nul + peak mis à jour
        drum_track.volume = 0.75
        drum_gui._play_pad(kick_pad)
        assert len(played) == 2
        assert np.max(np.abs(played[-1])) > 0.01
        mixer = engine._get_active_mixer_plugin()
        assert drum_track.id in mixer.track_peaks
        assert mixer.track_peaks[drum_track.id][0] > -45.0
    finally:
        dlg.close()
        engine.close()


def test_nova_guitar_and_synth_gui_audition_respect_mixer_volume_and_meter(qapp):
    """Les boutons d'audition de Nova Guitar et NovaSynth passent par le volume et le meter de piste."""
    proj = Project.create_empty("Test Guitar & Synth Audition")
    guitar_track = Track(
        name="Guitare",
        track_type="midi",
        volume=1.0,
        plugin_path="novadaw.guitar",
        plugin_name="Nova Guitar",
    )
    guitar = plugin_registry.create_plugin("novadaw.guitar")
    guitar_track.add_plugin(guitar)

    synth_track = Track(
        name="Synth",
        track_type="midi",
        volume=1.0,
        plugin_path="novadaw.synth",
        plugin_name="NovaSynth",
    )
    synth = plugin_registry.create_plugin("novadaw.synth")
    synth_track.add_plugin(synth)

    proj.add_track(guitar_track)
    proj.add_track(synth_track)
    master = proj.ensure_master_track()
    mixer = next(p for p in master.plugins if getattr(p, "plugin_type_id", None) == "novadaw.mixer")
    console = MixerConsoleWidget(mixer)
    engine = _setup_engine_with_active_stream(proj)

    dlg_g = open_native_plugin_editor(guitar, track=guitar_track)
    dlg_s = open_native_plugin_editor(synth, track=synth_track)
    try:
        # 1. Nova Guitar audition à 100% puis 0%
        dlg_g.editor._audition_notes([40])
        out_g1 = np.zeros((256, 2), dtype=np.float32)
        engine._audio_callback(out_g1, 256, None, None)
        assert float(np.max(np.abs(out_g1))) > 0.005
        assert mixer.track_peaks[guitar_track.id][0] > -50.0

        console.strips[guitar_track.id].slider_vol.setValue(0)
        out_g0 = np.zeros((256, 2), dtype=np.float32)
        engine._audio_callback(out_g0, 256, None, None)
        assert np.allclose(out_g0, 0.0)

        # 2. NovaSynth audition à 100% puis 0%
        with engine._preview_lock:
            engine._preview_buffers.clear()
        dlg_s.editor._on_audition_note(60)
        out_s1 = np.zeros((256, 2), dtype=np.float32)
        engine._audio_callback(out_s1, 256, None, None)
        assert float(np.max(np.abs(out_s1))) > 0.005
        assert mixer.track_peaks[synth_track.id][0] > -50.0

        console.strips[synth_track.id].slider_vol.setValue(0)
        out_s0 = np.zeros((256, 2), dtype=np.float32)
        engine._audio_callback(out_s0, 256, None, None)
        assert np.allclose(out_s0, 0.0)
    finally:
        dlg_g.close()
        dlg_s.close()
        console.close()
        engine.close()


def test_multiple_stacked_plugins_never_bypass_mixer_volume_or_meter(qapp):
    """
    Vérifie qu'avec 1 ou plusieurs plugins branchés sur une piste (Instrument + EQ + Compresseur
    + Guitar Amp + Vocal Glide), le volume du mixeur et le VU-mètre fonctionnent toujours
    en lecture et en direct.
    """
    proj = Project.create_empty("Test Stacked Plugins")
    drum_track = Track(
        name="Batterie FX Stack",
        track_type="midi",
        volume=1.0,
        plugin_path="novadaw.drum_machine",
        plugin_name="Nova Drums VSTi",
    )
    drums = plugin_registry.create_plugin("novadaw.drum_machine")
    eq = plugin_registry.create_plugin("novadaw.equalizer")
    eq.bands[0].gain_db = 4.0
    comp = plugin_registry.create_plugin("novadaw.compressor")
    amp = plugin_registry.create_plugin("novadaw.guitar_amp")
    glide = plugin_registry.create_plugin("novadaw.vocal_glide")

    drum_track.add_plugin(drums)
    drum_track.add_plugin(eq)
    drum_track.add_plugin(comp)
    drum_track.add_plugin(amp)
    drum_track.add_plugin(glide)
    drum_track.clips.append(
        MidiClip(
            name="Beat",
            start_beat=0.0,
            length_beats=4.0,
            notes=[MidiNote(pitch=36, start_beat=0.0, duration=0.5, velocity=120)],
        )
    )
    proj.add_track(drum_track)
    master = proj.ensure_master_track()
    mixer = next(p for p in master.plugins if getattr(p, "plugin_type_id", None) == "novadaw.mixer")
    console = MixerConsoleWidget(mixer)
    strip = console.strips[drum_track.id]
    engine = _setup_engine_with_active_stream(proj)

    try:
        # 1. Lecture avec volume = 100%
        engine.is_playing = True
        engine.current_beat = 0.0
        out_100 = np.zeros((512, 2), dtype=np.float32)
        engine._audio_callback(out_100, 512, None, None)
        assert float(np.max(np.abs(out_100))) > 0.005
        assert mixer.track_peaks[drum_track.id][0] > -45.0

        # 2. Lecture avec volume = 0% -> silence absolu malgré EQ/Comp/Amp/VocalGlide actifs
        strip.slider_vol.setValue(0)
        engine.current_beat = 0.0
        drums.reset()
        out_0 = np.zeros((512, 2), dtype=np.float32)
        engine._audio_callback(out_0, 512, None, None)
        assert np.allclose(out_0, 0.0)

        # 3. Oscillation du meter : pic puis décroissance au silence
        strip.slider_vol.setValue(100)
        engine.current_beat = 0.0
        drums.reset()
        mixer.reset()
        out_hit = np.zeros((512, 2), dtype=np.float32)
        engine._audio_callback(out_hit, 512, None, None)
        console._update_meters()
        pk_after_hit = strip.vu_meter.peak_left_db
        assert pk_after_hit > -45.0

        engine.is_playing = False
        for _ in range(40):
            console._update_meters()
        assert strip.vu_meter.peak_left_db < pk_after_hit
        assert strip.vu_meter.peak_left_db == -60.0
        assert strip.lbl_peak_db.text() == "-∞ dB"
    finally:
        console.close()
        engine.close()


def test_two_tracks_with_same_plugin_resolve_independently(qapp):
    """Deux pistes utilisant Nova Drums doivent pointer chacune vers leur propre tranche de mixeur."""
    proj = Project.create_empty("Two Drum Tracks")
    t1 = Track(name="Drums 1", track_type="midi", volume=0.0, plugin_path="novadaw.drum_machine")
    t2 = Track(name="Drums 2", track_type="midi", volume=1.0, plugin_path="novadaw.drum_machine")
    d1 = plugin_registry.create_plugin("novadaw.drum_machine")
    d2 = plugin_registry.create_plugin("novadaw.drum_machine")
    t1.add_plugin(d1)
    t2.add_plugin(d2)
    proj.add_track(t1)
    proj.add_track(t2)
    proj.ensure_master_track()

    engine = _setup_engine_with_active_stream(proj)
    try:
        assert engine.get_track_for_plugin(d1) is t1
        assert engine.get_track_for_plugin(d2) is t2
        assert find_track_for_plugin(d2, type("P", (), {"project": proj})()) is t2

        # Jouer sur Drums 2 doit produire du son sur t2 même si t1 est à 0%
        dlg2 = open_native_plugin_editor(d2, track=t2)
        try:
            kick_pad = next(p for p in d2.pads if p.pad_id == "kick")
            dlg2.editor._play_pad(kick_pad)
            out = np.zeros((256, 2), dtype=np.float32)
            engine._audio_callback(out, 256, None, None)
            assert float(np.max(np.abs(out))) > 0.005
            mixer = engine._get_active_mixer_plugin()
            assert mixer.track_peaks.get(t2.id, (-60.0, -60.0))[0] > -45.0
            assert t1.id not in mixer.track_peaks
        finally:
            dlg2.close()
    finally:
        engine.close()


def test_live_native_notes_respect_mute_and_solo(qapp):
    """Les notes live clavier/piano roll respectent Mute et Solo dans le mixeur."""
    proj = Project.create_empty("Live Mute Solo")
    t1 = Track(name="Synth 1", track_type="midi", volume=0.9)
    t2 = Track(name="Synth 2", track_type="midi", volume=0.9)
    proj.add_track(t1)
    proj.add_track(t2)
    proj.ensure_master_track()

    engine = _setup_engine_with_active_stream(proj)
    try:
        t1.muted = True
        engine.start_live_note(60, velocity=100, track=t1)
        out_muted = np.zeros((256, 2), dtype=np.float32)
        engine._audio_callback(out_muted, 256, None, None)
        assert np.allclose(out_muted, 0.0)

        t1.muted = False
        out_unmuted = np.zeros((256, 2), dtype=np.float32)
        engine._audio_callback(out_unmuted, 256, None, None)
        assert float(np.max(np.abs(out_unmuted))) > 0.01

        t2.soloed = True
        out_other_solo = np.zeros((256, 2), dtype=np.float32)
        engine._audio_callback(out_other_solo, 256, None, None)
        assert np.allclose(out_other_solo, 0.0)
    finally:
        engine.close()
