"""
tests/test_vocal_glide.py - Tests complets pour le plugin Nova Vocal Glide :
- Enregistrement par défaut, presets, sérialisation .ndaw
- Traitement DSP (boost des passages faibles, atténuation des passages forts, respect du seuil de bruit, agressivité, bypass)
- Cristallisation hors-ligne et bouton UI vers la piste d'automatisation Volume native
- Écriture temps réel via AutomationProcessContext
- Mode Monitoring Live Micro (entrée directe -> Nova Vocal Glide -> sortie, à l'arrêt et en lecture)
- Bouton d'aide (?) intégré et fenêtre de guide utilisateur
- Intégration UI complète (NativePluginDialog, Inspector, TrackHeader, MainWindow)
"""
import os
import sys
import numpy as np
import pytest
from PySide6.QtWidgets import QApplication

from core.project import Project, Track, AudioClip
from core.serializer import save_project, load_project
from core.audio_engine import AudioEngine
from plugins.registry import plugin_registry, ensure_plugins_loaded
from plugins.base import AutomationProcessContext
from plugins.vocal_glide.vocal_glide_plugin import VocalGlidePlugin
from plugins.vocal_glide.vocal_glide_gui import VocalGlideHelpDialog
from ui.plugin_dialogs import NativePluginDialog
from ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def _make_vocal_tone(amp: float, dur_sec: float = 0.6, sr: int = 44100, freq: float = 260.0) -> np.ndarray:
    t = np.linspace(0.0, dur_sec, int(sr * dur_sec), endpoint=False, dtype=np.float32)
    wave = (amp * np.sin(2.0 * np.pi * freq * t)).astype(np.float32)
    return np.column_stack((wave, wave))


def test_vocal_glide_registry_presets_and_serialization(tmp_path):
    ensure_plugins_loaded()
    info = plugin_registry.get_plugin_info("novadaw.vocal_glide")
    assert info is not None
    assert info["name"] == "Nova Vocal Glide"
    assert info["is_default"] is True

    proj = Project.create_empty("Test Nova Vocal Glide")
    t = Track(name="Chant Lead", track_type="audio")
    glide = plugin_registry.create_plugin("novadaw.vocal_glide")
    assert isinstance(glide, VocalGlidePlugin)

    presets = glide.get_factory_presets()
    assert len(presets) >= 4
    glide.set_state(presets["Voix Douce / Ballade"])
    glide.target_db = -17.5
    glide.aggressiveness = 85.0
    t.add_plugin(glide)
    proj.add_track(t)

    path = str(tmp_path / "vocal_glide_test.ndaw")
    save_project(proj, path)
    loaded = load_project(path)

    lt = loaded.tracks[0]
    assert len(lt.plugins) == 1
    lg = lt.plugins[0]
    assert isinstance(lg, VocalGlidePlugin)
    assert lg.target_db == pytest.approx(-17.5)
    assert lg.aggressiveness == pytest.approx(85.0)
    assert lg.max_boost_db == pytest.approx(12.0)


def test_vocal_glide_dsp_boost_cut_threshold_and_bypass():
    sr = 44100
    glide = VocalGlidePlugin(
        target_db=-16.0,
        activity_threshold_db=-45.0,
        aggressiveness=90.0,
        speed=85.0,
        max_boost_db=10.0,
        max_cut_db=10.0,
        sidechain_vocal_filter=True,
    )

    quiet = _make_vocal_tone(0.056, dur_sec=0.8, sr=sr)
    out_quiet = glide.process(quiet, sr)
    assert glide.is_vocal_active is True
    assert glide.current_glide_gain_db > 3.0
    rms_in_q = float(np.sqrt(np.mean(quiet[-4096:] ** 2)))
    rms_out_q = float(np.sqrt(np.mean(out_quiet[-4096:] ** 2)))
    assert rms_out_q > rms_in_q * 1.4

    glide.reset()
    loud = _make_vocal_tone(0.70, dur_sec=0.8, sr=sr)
    out_loud = glide.process(loud, sr)
    assert glide.is_vocal_active is True
    assert glide.current_glide_gain_db < -3.0
    rms_in_l = float(np.sqrt(np.mean(loud[-4096:] ** 2)))
    rms_out_l = float(np.sqrt(np.mean(out_loud[-4096:] ** 2)))
    assert rms_out_l < rms_in_l * 0.75

    glide.reset()
    noise = _make_vocal_tone(0.0018, dur_sec=0.8, sr=sr)
    glide.process(noise, sr)
    assert glide.is_vocal_active is False
    assert abs(glide.current_glide_gain_db) < 0.25

    glide_soft = VocalGlidePlugin(
        target_db=-16.0, activity_threshold_db=-45.0, aggressiveness=30.0, speed=85.0, max_boost_db=10.0
    )
    glide_hard = VocalGlidePlugin(
        target_db=-16.0, activity_threshold_db=-45.0, aggressiveness=95.0, speed=85.0, max_boost_db=10.0
    )
    glide_soft.process(quiet, sr)
    glide_hard.process(quiet, sr)
    assert glide_hard.current_glide_gain_db > glide_soft.current_glide_gain_db + 1.0

    glide.enabled = False
    out_byp = glide.process(quiet, sr)
    assert np.allclose(out_byp, quiet, atol=1e-6)


def test_vocal_glide_live_automation_write_context():
    glide = VocalGlidePlugin(
        target_db=-16.0,
        activity_threshold_db=-45.0,
        aggressiveness=85.0,
        speed=80.0,
        write_automation_live=True,
    )
    emitted = []
    ctx = AutomationProcessContext(
        track_id="trk1",
        start_beat=1.0,
        end_beat=1.2,
        bpm=120.0,
        sample_rate=44100,
        is_playing=True,
        automation_sink=lambda tid, param, beat, val, pid: emitted.append((tid, param, beat, val, pid)),
        read_automation=lambda param, beat: 0.8,
    )
    quiet = _make_vocal_tone(0.05, dur_sec=0.2, sr=44100)
    glide.process_with_context(quiet, 44100, ctx)
    assert len(emitted) == 1
    assert emitted[0][0] == "trk1"
    assert emitted[0][1] == "volume"
    assert emitted[0][3] > 0.8


def test_vocal_glide_crystallize_help_and_ui_workflow(qapp):
    os.environ["NOVADAW_SILENT_TESTS"] = "1"
    win = MainWindow()
    try:
        win.resize(1280, 800)
        t = Track(name="Chant Lead", track_type="audio", volume=0.8)

        sr = 44100
        q_part = _make_vocal_tone(0.05, dur_sec=1.0, sr=sr)
        s_part = np.zeros((int(0.5 * sr), 2), dtype=np.float32)
        l_part = _make_vocal_tone(0.65, dur_sec=1.0, sr=sr)
        full_audio = np.vstack((q_part, s_part, l_part))

        clip = AudioClip(name="Vocal Take", start_beat=0.0, length_beats=5.0, audio_data=full_audio, sample_rate=sr)
        t.clips.append(clip)

        glide = VocalGlidePlugin(
            target_db=-16.0,
            activity_threshold_db=-45.0,
            aggressiveness=85.0,
            speed=85.0,
            max_boost_db=9.0,
            max_cut_db=9.0,
            crystallize_auto_bypass=True,
        )
        t.add_plugin(glide)
        win.project.add_track(t)
        win.refresh_project_ui()
        qapp.processEvents()

        dlg = NativePluginDialog(glide, win, track=t)
        dlg.show()
        qapp.processEvents()
        widget = dlg.editor
        assert widget is not None

        # Vérifier le bouton d'aide (?) et la fenêtre d'aide intégrée
        assert hasattr(widget, "btn_help")
        assert widget.btn_help.text() == "?"
        help_dlg = VocalGlideHelpDialog(widget)
        assert "Nova Vocal Glide" in help_dlg.windowTitle()
        help_dlg.close()

        # Clic sur Cristalliser
        widget.btn_crystallize.click()
        qapp.processEvents()

        lane = t.get_automation_lane("volume")
        assert lane is not None
        assert len(lane.points) >= 4
        assert t.automation_expanded is True
        assert glide.enabled is False
        assert widget.btn_bypass.isChecked() is True

        assert lane.evaluate(1.0) > 0.95
        assert lane.evaluate(4.0) < lane.evaluate(1.0) - 0.25
        dlg.close()
    finally:
        win.close()


def test_live_audio_monitoring_through_vocal_glide(qapp):
    os.environ["NOVADAW_SILENT_TESTS"] = "1"
    engine = AudioEngine()
    try:
        proj = Project.create_empty("Live Mon Test")
        t = Track(name="Micro Chant", track_type="audio", volume=1.0, monitoring=True)
        glide = VocalGlidePlugin(
            target_db=-16.0,
            activity_threshold_db=-45.0,
            aggressiveness=90.0,
            speed=90.0,
            max_boost_db=10.0,
        )
        t.add_plugin(glide)
        proj.add_track(t)
        engine.project = proj

        active = engine.sync_project_monitoring()
        assert active is True

        quiet_block = _make_vocal_tone(0.05, dur_sec=0.05, sr=engine.sample_rate)
        out = np.zeros((512, 2), dtype=np.float32)
        for _ in range(10):
            engine.feed_live_input_block(quiet_block[:512], engine.sample_rate)
            engine._audio_callback(out, 512, None, None)

        assert float(np.max(np.abs(out))) > 0.05
        assert glide.is_vocal_active is True
        assert glide.current_glide_gain_db > 1.5
    finally:
        engine.close()
