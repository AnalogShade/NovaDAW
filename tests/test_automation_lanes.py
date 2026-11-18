"""
tests/test_automation_lanes.py - Tests complets pour les pistes d'automatisation :
- Modèle de données (Volume, Pan, linéaire, angle droit/step, courbe lissée/tension, sérialisation)
- Bouton déroulant sur TrackHeaderWidget, création/repliage, AutomationHeaderWidget
- Réordonnancement groupé (piste + ses sous-pistes suivent ensemble)
- Outils d'édition dans TimelineGrid (Pointeur, Crayon/Draw à main levée, Gomme, Ciseaux, courbure)
- Application audio sample-accurate (Volume & Pan)
- Fondation de pilotage par plugin (Nova Vocal Glide via AutomationProcessContext)
"""
import os
import sys
import numpy as np
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QPoint, QPointF
from PySide6.QtGui import QMouseEvent

from core.project import Project, Track, AudioClip, AutomationLane, AutomationPoint
from core.serializer import save_project, load_project
from core.audio_engine import AudioEngine
from plugins.base import BasePlugin, AutomationProcessContext
from ui.track_header import TrackHeaderWidget, AutomationHeaderWidget
from ui.automation_lane_renderer import value_to_lane_y, lane_y_to_value, simplify_freehand_points
from ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def test_automation_lane_curve_modes_and_serialization(tmp_path):
    """Vérifie l'évaluation linéaire, palier (angle droit), courbe et la persistance .ndaw."""
    proj = Project.create_empty("Test Auto")
    t = Track(name="Voix", track_type="audio", volume=0.8, pan=0.0)

    vol_lane = t.add_automation_lane("volume", seed_initial_point=False)
    vol_lane.add_point(0.0, 0.5, curve_type="linear")
    vol_lane.add_point(4.0, 1.0, curve_type="step")
    vol_lane.add_point(8.0, 0.2, curve_type="curve", curve_tension=0.0)
    vol_lane.add_point(12.0, 1.2, curve_type="linear")

    # Segment 0..4 linéaire
    assert vol_lane.evaluate(0.0) == pytest.approx(0.5)
    assert vol_lane.evaluate(2.0) == pytest.approx(0.75)

    # Segment 4..8 angle droit (step) : reste à 1.0 jusqu'à beat 8
    assert vol_lane.evaluate(4.0) == pytest.approx(1.0)
    assert vol_lane.evaluate(7.9) == pytest.approx(1.0)
    assert vol_lane.evaluate(8.0) == pytest.approx(0.2)

    # Segment 8..12 courbe en S : à mi-chemin = 0.7, à 25% différent d'une droite
    mid = vol_lane.evaluate(10.0)
    q1 = vol_lane.evaluate(9.0)
    assert mid == pytest.approx(0.7, abs=1e-3)
    assert q1 < 0.45  # La S-curve démarre plus doucement qu'une droite (qui serait à 0.45)

    # Évaluation vectorielle NumPy
    arr = vol_lane.evaluate_range(0.0, 4.0, 100)
    assert arr.shape == (100,)
    assert arr[0] == pytest.approx(0.5, abs=1e-3)
    assert arr[-1] < 1.0

    # Pan lane
    pan_lane = t.add_automation_lane("pan", seed_initial_point=False)
    pan_lane.add_point(0.0, -1.0, curve_type="linear")
    pan_lane.add_point(4.0, 1.0, curve_type="linear")
    assert pan_lane.evaluate(2.0) == pytest.approx(0.0)

    proj.add_track(t)
    save_path = str(tmp_path / "auto_test.ndaw")
    save_project(proj, save_path)
    loaded = load_project(save_path)

    lt = loaded.tracks[0]
    assert len(lt.automation_lanes) == 2
    assert lt.automation_lanes[0].target_param == "volume"
    assert len(lt.automation_lanes[0].points) == 4
    assert lt.automation_lanes[0].points[1].curve_type == "step"
    assert lt.automation_lanes[0].points[2].curve_type == "curve"
    assert lt.automation_lanes[1].target_param == "pan"


def test_pan_lane_vertical_center_mapping():
    """Vérifie que la ligne centrale du Panoramique = 0 (C), haut = Gauche (-1), bas = Droite (+1)."""
    lane = AutomationLane(target_param="pan")
    top_y = 100.0
    h = 60.0

    y_center = value_to_lane_y(lane, 0.0, top_y, h)
    y_left = value_to_lane_y(lane, -1.0, top_y, h)
    y_right = value_to_lane_y(lane, 1.0, top_y, h)

    assert y_center == pytest.approx(top_y + h / 2.0)
    assert y_left < y_center < y_right
    assert lane_y_to_value(lane, y_center, top_y, h) == pytest.approx(0.0)
    assert lane_y_to_value(lane, y_left, top_y, h) == pytest.approx(-1.0)
    assert lane_y_to_value(lane, y_right, top_y, h) == pytest.approx(1.0)


def test_audio_engine_automation_gains_and_plugin_write_context(qapp):
    """Vérifie l'application audio Volume/Pan et le hook de pilotage par plugin (Nova Vocal Glide)."""
    os.environ["NOVADAW_SILENT_TESTS"] = "1"
    engine = AudioEngine()
    try:
        proj = Project.create_empty("DSP Test")
        t = Track(name="Vocal", track_type="audio", volume=0.8, pan=0.0)
        # Signal stéréo constant 1.0
        audio = np.ones((4410, 2), dtype=np.float32)
        t.clips.append(AudioClip(name="Take", start_beat=0.0, length_beats=4.0, audio_data=audio, sample_rate=44100))

        vol_lane = t.add_automation_lane("volume", seed_initial_point=False)
        vol_lane.add_point(0.0, 0.2, curve_type="linear")
        vol_lane.add_point(1.0, 1.0, curve_type="linear")

        pan_lane = t.add_automation_lane("pan", seed_initial_point=False)
        pan_lane.add_point(0.0, -1.0, curve_type="step")
        pan_lane.add_point(0.5, 1.0, curve_type="linear")

        proj.add_track(t)
        engine.project = proj

        gain_l, gain_r, end_vol, end_pan = engine._compute_track_automation_gains(t, 0.0, 1.0, 512)
        assert isinstance(gain_l, np.ndarray)
        assert gain_l.shape == (512,)
        # Au début (beat 0.0, pan = -1.0 Gauche) : L = 0.2, R = 0.0
        assert gain_l[0] == pytest.approx(0.2, abs=1e-2)
        assert gain_r[0] == pytest.approx(0.0, abs=1e-2)
        # À la fin (beat ~1.0, pan = +1.0 Droite, vol ~1.0) : L ~ 0.0, R ~ 1.0
        assert gain_l[-1] < 0.02
        assert gain_r[-1] > 0.95
        assert end_vol == pytest.approx(1.0, abs=1e-2)
        assert end_pan == pytest.approx(1.0, abs=1e-2)

        # Test du hook Nova Vocal Glide (plugin émetteur de points d'automatisation)
        class MockVocalGlide(BasePlugin):
            def __init__(self):
                super().__init__("test.vocal_glide", "Mock Glide", category="dynamics")
                self.supports_automation_write = True
                self.automatable_outputs = ["volume"]

            def process(self, audio_buf, sr):
                return audio_buf

            def get_state(self):
                return {}

            def set_state(self, state):
                pass

            def process_with_context(self, audio_buf, sr, context: AutomationProcessContext = None):
                if context is not None:
                    context.emit_automation("volume", context.start_beat, 0.92, self.instance_id)
                return audio_buf

        glide = MockVocalGlide()
        t.add_plugin(glide)
        engine.is_playing = True
        engine._render_track_slice(t, 1.5, 2.0, 256, 120.0)
        writes = engine.drain_pending_automation_writes()
        assert len(writes) == 1
        assert writes[0][0] == t.id
        assert writes[0][1] == "volume"
        assert writes[0][2] == pytest.approx(1.5)
        assert writes[0][3] == pytest.approx(0.92)
        assert writes[0][4] == glide.instance_id
    finally:
        engine.close()


def test_ui_dropdown_automation_group_reorder_and_pencil_tool(qapp):
    """Vérifie le workflow UI complet : ajout via menu, repliage, déplacement groupé et outil Crayon."""
    win = MainWindow()
    win.resize(1280, 800)
    win.show()
    qapp.processEvents()

    t1 = Track(name="Chant Lead", track_type="audio", color="#38bdf8", height=76)
    t2 = Track(name="Guitare", track_type="midi", color="#10b981", height=76)
    win.project.add_track(t1)
    win.project.add_track(t2)
    win.refresh_project_ui()
    qapp.processEvents()

    # 1. Vérifier que le bouton d'automatisation existe sur l'en-tête de piste
    h1 = win.headers_layout.itemAt(0).widget()
    assert isinstance(h1, TrackHeaderWidget)
    assert hasattr(h1, "btn_automation")

    # Demander l'ajout de Volume et Panoramique sur la piste 1
    win._on_automation_lane_requested(t1.id, "volume")
    win._on_automation_lane_requested(t1.id, "pan")
    qapp.processEvents()

    assert len(t1.automation_lanes) == 2
    assert t1.automation_expanded is True

    # headers_layout doit contenir : TrackHeader(t1), Auto(vol), Auto(pan), TrackHeader(t2)
    widgets = [win.headers_layout.itemAt(i).widget() for i in range(win.headers_layout.count() - 1)]
    assert len(widgets) == 4
    assert isinstance(widgets[0], TrackHeaderWidget) and widgets[0].track.id == t1.id
    assert isinstance(widgets[1], AutomationHeaderWidget) and widgets[1].lane.target_param == "volume"
    assert isinstance(widgets[2], AutomationHeaderWidget) and widgets[2].lane.target_param == "pan"
    assert isinstance(widgets[3], TrackHeaderWidget) and widgets[3].track.id == t2.id

    # Vérifier la synchronisation verticale dans TimelineGrid
    t_layout = win.timeline_grid.get_track_layout()
    a_layout = win.timeline_grid.get_automation_layout()
    assert len(a_layout) == 2
    assert t_layout[0][1] == 0
    assert a_layout[0][2] == 76
    assert a_layout[1][2] == 76 + 56
    assert t_layout[1][1] == 76 + 56 + 56

    # 2. Déplacer la piste 1 vers le bas -> ses 2 sous-pistes doivent suivre en bloc !
    assert win.move_track(0, 1) is True
    qapp.processEvents()
    assert [t.id for t in win.project.tracks] == [t2.id, t1.id]

    widgets_after = [win.headers_layout.itemAt(i).widget() for i in range(win.headers_layout.count() - 1)]
    assert isinstance(widgets_after[0], TrackHeaderWidget) and widgets_after[0].track.id == t2.id
    assert isinstance(widgets_after[1], TrackHeaderWidget) and widgets_after[1].track.id == t1.id
    assert isinstance(widgets_after[2], AutomationHeaderWidget) and widgets_after[2].lane.target_param == "volume"
    assert isinstance(widgets_after[3], AutomationHeaderWidget) and widgets_after[3].lane.target_param == "pan"

    # 3. Replier le groupe d'automatisation de t1 (comme un menu déroulant)
    win._on_automation_toggled(t1.id, False)
    qapp.processEvents()
    assert t1.automation_expanded is False
    assert win.headers_layout.count() - 1 == 2
    assert len(win.timeline_grid.get_automation_layout()) == 0

    # Déplier à nouveau
    win._on_automation_toggled(t1.id, True)
    qapp.processEvents()
    assert len(win.timeline_grid.get_automation_layout()) == 2

    # Forcer un rendu complet QPainter (courbes Volume + Panoramique + ligne centrale)
    pixmap = win.timeline_grid.grab()
    assert not pixmap.isNull()

    # 4. Outil Crayon (4) : dessiner à main levée dans la sous-piste Volume de t1
    win.editing_toolbar.set_active_tool("draw")
    assert win.timeline_grid.active_tool == "draw"

    a_layout = win.timeline_grid.get_automation_layout()
    _, vol_lane, ly, lh = a_layout[0]
    ppb = win.timeline_grid.pixels_per_beat

    # Simuler un tracé du beat 1 au beat 4
    p_start = QPointF(1.0 * ppb, ly + lh * 0.7)
    p_mid = QPointF(2.5 * ppb, ly + lh * 0.25)
    p_end = QPointF(4.0 * ppb, ly + lh * 0.6)

    win.timeline_grid.mousePressEvent(QMouseEvent(
        QMouseEvent.Type.MouseButtonPress, p_start, win.timeline_grid.mapToGlobal(p_start.toPoint()),
        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier
    ))
    win.timeline_grid.mouseMoveEvent(QMouseEvent(
        QMouseEvent.Type.MouseMove, p_mid, win.timeline_grid.mapToGlobal(p_mid.toPoint()),
        Qt.NoButton, Qt.LeftButton, Qt.NoModifier
    ))
    win.timeline_grid.mouseReleaseEvent(QMouseEvent(
        QMouseEvent.Type.MouseButtonRelease, p_end, win.timeline_grid.mapToGlobal(p_end.toPoint()),
        Qt.LeftButton, Qt.NoButton, Qt.NoModifier
    ))
    qapp.processEvents()

    assert len(vol_lane.points) >= 3
    # La simplification libre produit aussi des points propres sans bruit
    simplified = simplify_freehand_points([(0.0, 0.5), (1.0, 0.5), (2.0, 0.5), (3.0, 1.0)], snap_enabled=False)
    assert len(simplified) == 3

    # 5. Outil Pointeur : créer un point dans la sous-piste Panoramique, puis l'effacer avec la Gomme
    _, pan_lane, pan_y, pan_h = a_layout[1]
    initial_pan_pts = len(pan_lane.points)
    win.editing_toolbar.set_active_tool("select")
    p_pan = QPointF(3.0 * ppb, pan_y + pan_h * 0.25)
    win.timeline_grid.mousePressEvent(QMouseEvent(
        QMouseEvent.Type.MouseButtonPress, p_pan, win.timeline_grid.mapToGlobal(p_pan.toPoint()),
        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier
    ))
    win.timeline_grid.mouseReleaseEvent(QMouseEvent(
        QMouseEvent.Type.MouseButtonRelease, p_pan, win.timeline_grid.mapToGlobal(p_pan.toPoint()),
        Qt.LeftButton, Qt.NoButton, Qt.NoModifier
    ))
    assert len(pan_lane.points) == initial_pan_pts + 1
    added_pt = next(p for p in pan_lane.points if abs(p.beat - 3.0) < 0.05)
    assert added_pt.value < 0.0  # Au-dessus de la ligne centrale = Gauche (L)

    # Outil Ciseaux sur la sous-piste Panoramique (pose un point d'ancrage step)
    win.editing_toolbar.set_active_tool("split")
    p_cut = QPointF(5.0 * ppb, pan_y + pan_h * 0.5)
    win.timeline_grid.mousePressEvent(QMouseEvent(
        QMouseEvent.Type.MouseButtonPress, p_cut, win.timeline_grid.mapToGlobal(p_cut.toPoint()),
        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier
    ))
    cut_pt = next(p for p in pan_lane.points if abs(p.beat - 5.0) < 0.05)
    assert cut_pt.curve_type == "step"

    # Outil Gomme sur le point ajouté au beat 3
    win.editing_toolbar.set_active_tool("erase")
    win.timeline_grid.mousePressEvent(QMouseEvent(
        QMouseEvent.Type.MouseButtonPress, p_pan, win.timeline_grid.mapToGlobal(p_pan.toPoint()),
        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier
    ))
    win.timeline_grid.mouseReleaseEvent(QMouseEvent(
        QMouseEvent.Type.MouseButtonRelease, p_pan, win.timeline_grid.mapToGlobal(p_pan.toPoint()),
        Qt.LeftButton, Qt.NoButton, Qt.NoModifier
    ))
    assert all(abs(p.beat - 3.0) > 0.05 for p in pan_lane.points)

    # 6. Vérifier le drainage des points émis par plugin en mode Write dans MainWindow
    vol_lane.mode = "write"
    pts_before_write = len(vol_lane.points)
    win.audio_engine.is_playing = True
    win.audio_engine._enqueue_plugin_automation_write(t1.id, "volume", 6.5, 1.15, "vocal_glide_id")
    win._update_playback_ui()
    win.audio_engine.is_playing = False
    assert len(vol_lane.points) == pts_before_write + 1
    assert vol_lane.evaluate(6.5) == pytest.approx(1.15)

    win.ipc_server.stop()
    win.close()


def test_export_wav_applies_volume_and_pan_automation(tmp_path):
    """Vérifie que le mixdown WAV applique fidèlement les courbes Volume et Panoramique."""
    import soundfile as sf
    os.environ["NOVADAW_SILENT_TESTS"] = "1"
    engine = AudioEngine(sample_rate=44100, output_device=0, input_device=0)
    try:
        proj = Project.create_empty("Export Auto")
        proj.bpm = 120.0
        t = Track(name="Tone", track_type="audio", volume=1.0, pan=0.0)
        # 2 secondes (4 beats à 120 BPM) de signal constant
        data = np.full((88200, 2), 0.5, dtype=np.float32)
        t.clips.append(AudioClip(name="Const", start_beat=0.0, length_beats=4.0, audio_data=data, sample_rate=44100))

        pan_lane = t.add_automation_lane("pan", seed_initial_point=False)
        pan_lane.add_point(0.0, -1.0, curve_type="step")  # 1re moitié à gauche
        pan_lane.add_point(2.0, 1.0, curve_type="step")   # 2e moitié à droite

        proj.add_track(t)
        engine.project = proj

        out_wav = str(tmp_path / "auto_export.wav")
        assert engine.export_wav(out_wav, end_bar=1) is True
        audio_out, _ = sf.read(out_wav)
        # 1re moitié (frames 1000..20000) : canal gauche fort, canal droit silencieux
        assert np.mean(np.abs(audio_out[1000:20000, 0])) > 0.2
        assert np.max(np.abs(audio_out[1000:20000, 1])) < 1e-4
        # 2e moitié (frames 50000..70000) : canal droit fort, canal gauche silencieux
        assert np.max(np.abs(audio_out[50000:70000, 0])) < 1e-4
        assert np.mean(np.abs(audio_out[50000:70000, 1])) > 0.2
    finally:
        engine.close()
