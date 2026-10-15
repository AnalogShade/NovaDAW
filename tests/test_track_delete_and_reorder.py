"""
tests/test_track_delete_and_reorder.py - Tests complets pour la suppression de piste
(menu contextuel sur le nom/en-tête/timeline, bouton, inspecteur, indication claire)
et le réordonnancement des pistes par glisser-déposer sur la bande de couleur (curseur petite main
et intégrité totale des données).
"""
import sys
import numpy as np
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QPoint, QPointF
from PySide6.QtGui import QMouseEvent

from core.project import Project, Track, MidiClip, MidiNote, AudioClip
from ui.track_header import TrackHeaderWidget, TrackColorGrip
from ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def test_color_bar_is_hand_grip_and_changes_cursor(qapp):
    """Vérifie que la bande de couleur affiche le curseur petite main (OpenHandCursor) au survol et ClosedHandCursor au clic-glisser."""
    proj = Project.create_empty()
    t1 = Track(name="Piste Haute", track_type="midi", color="#38bdf8")
    proj.add_track(t1)

    header = TrackHeaderWidget(t1, project=proj)
    header.resize(260, 76)
    header.show()
    qapp.processEvents()

    assert isinstance(header.color_bar, TrackColorGrip)
    assert header.color_bar.cursor().shape() == Qt.OpenHandCursor

    # Simuler un clic gauche maintenu sur la bande de couleur
    press_ev = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        QPointF(4, 20),
        header.color_bar.mapToGlobal(QPoint(4, 20)),
        Qt.LeftButton,
        Qt.LeftButton,
        Qt.NoModifier,
    )
    header.color_bar.mousePressEvent(press_ev)
    assert header._reordering_track is True
    assert header.color_bar.cursor().shape() == Qt.ClosedHandCursor

    # Relâcher le clic
    rel_ev = QMouseEvent(
        QMouseEvent.Type.MouseButtonRelease,
        QPointF(4, 20),
        header.color_bar.mapToGlobal(QPoint(4, 20)),
        Qt.LeftButton,
        Qt.NoButton,
        Qt.NoModifier,
    )
    header.color_bar.mouseReleaseEvent(rel_ev)
    assert header._reordering_track is False
    assert header.color_bar.cursor().shape() == Qt.OpenHandCursor
    header.close()


def test_right_click_on_track_name_and_header_deletes_track_with_clear_indication(qapp):
    """
    Vérifie que le menu contextuel (clic droit sur le nom de la piste ou sur l'en-tête)
    identifie clairement le nom de la piste et permet de supprimer réellement la piste
    avec une indication explicite dans la barre d'état.
    """
    win = MainWindow()
    win.resize(1280, 800)
    win.show()
    qapp.processEvents()

    t1 = Track(name="Synth Lead Alpha", track_type="midi", color="#38bdf8")
    t2 = Track(name="Basse Groove Beta", track_type="midi", color="#a855f7")
    win.project.add_track(t1)
    win.project.add_track(t2)
    win.refresh_project_ui()
    qapp.processEvents()

    assert len(win.project.tracks) == 2

    # Récupérer l'en-tête de la piste 2
    header_t2 = win.headers_layout.itemAt(1).widget()
    assert isinstance(header_t2, TrackHeaderWidget)
    assert header_t2.track.id == t2.id

    # Vérifier que la politique de menu contextuel du QLineEdit du nom est bien CustomContextMenu
    # (remplaçant le menu texte inutile de Qt par le menu de piste)
    assert header_t2.txt_name.contextMenuPolicy() == Qt.CustomContextMenu

    # Construire le menu contextuel de la piste 2
    menu = header_t2._build_track_context_menu()
    actions = menu.actions()
    action_texts = [a.text() for a in actions if not a.isSeparator()]

    # L'en-tête du menu doit clairement indiquer le nom de la piste ciblée
    assert any("Basse Groove Beta" in txt for txt in action_texts)

    # Trouver l'action de suppression de piste
    del_action = next((a for a in actions if a.objectName() == "action_delete_track"), None)
    assert del_action is not None
    assert "Basse Groove Beta" in del_action.text()
    assert "Delete" in del_action.text() or "Supprimer" in del_action.text()

    # Déclencher l'action de suppression depuis le menu contextuel
    del_action.trigger()
    qapp.processEvents()

    # La piste 2 doit être supprimée du projet, des en-têtes et du mixeur
    assert len(win.project.tracks) == 1
    assert win.project.tracks[0].id == t1.id
    assert win.headers_layout.count() - 1 == 1
    if win.mixer_widget:
        assert t2.id not in win.mixer_widget.strips
        assert t1.id in win.mixer_widget.strips

    # Le message de statut doit indiquer clairement quelle piste a été supprimée
    status_msg = win.statusBar().currentMessage()
    assert "Basse Groove Beta" in status_msg
    assert "supprimée" in status_msg

    # La piste restante (t1) doit être automatiquement sélectionnée
    assert win.selected_track_id == t1.id

    win.ipc_server.stop()
    win.close()


def test_clicking_track_name_or_empty_timeline_lane_selects_track_and_deletes(qapp):
    """
    Vérifie que cliquer sur le champ texte du nom d'une piste ou sur sa ligne vide dans
    la timeline sélectionne immédiatement la piste, et que la suppression via l'inspecteur
    ou le menu contextuel de la timeline fonctionne proprement.
    """
    win = MainWindow()
    win.resize(1280, 800)
    win.show()
    qapp.processEvents()

    t1 = Track(name="Piste Un", track_type="midi", color="#38bdf8")
    t2 = Track(name="Piste Deux", track_type="audio", color="#10b981")
    t3 = Track(name="Piste Trois", track_type="midi", color="#f59e0b")
    win.project.add_track(t1)
    win.project.add_track(t2)
    win.project.add_track(t3)
    win.refresh_project_ui()
    win._on_track_selected(t1.id)
    qapp.processEvents()

    assert win.selected_track_id == t1.id

    # 1. Cliquer sur le QLineEdit txt_name de la piste 2 doit sélectionner la piste 2
    header_t2 = win.headers_layout.itemAt(1).widget()
    press_name = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        QPointF(10, 8),
        header_t2.txt_name.mapToGlobal(QPoint(10, 8)),
        Qt.LeftButton,
        Qt.LeftButton,
        Qt.NoModifier,
    )
    QApplication.sendEvent(header_t2.txt_name, press_name)
    qapp.processEvents()
    assert win.selected_track_id == t2.id
    assert header_t2.is_selected is True

    # 2. Cliquer sur la ligne vide de la piste 3 dans la TimelineGrid doit sélectionner la piste 3
    # Piste 1: y=[0..76], Piste 2: y=[76..152], Piste 3: y=[152..228]
    click_lane_3 = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        QPointF(200, 180),
        win.timeline_grid.mapToGlobal(QPoint(200, 180)),
        Qt.LeftButton,
        Qt.LeftButton,
        Qt.NoModifier,
    )
    win.timeline_grid.mousePressEvent(click_lane_3)
    rel_lane_3 = QMouseEvent(
        QMouseEvent.Type.MouseButtonRelease,
        QPointF(200, 180),
        win.timeline_grid.mapToGlobal(QPoint(200, 180)),
        Qt.LeftButton,
        Qt.NoButton,
        Qt.NoModifier,
    )
    win.timeline_grid.mouseReleaseEvent(rel_lane_3)
    qapp.processEvents()
    assert win.selected_track_id == t3.id
    assert win.timeline_grid.selected_track_id == t3.id

    # 3. Supprimer la piste 3 via le menu contextuel de la ligne de timeline
    lane_menu = win.timeline_grid._build_track_lane_context_menu(t3, 200.0)
    del_act = next((a for a in lane_menu.actions() if a.objectName() == "action_delete_track"), None)
    assert del_act is not None
    assert "Piste Trois" in del_act.text()
    del_act.trigger()
    qapp.processEvents()

    assert [t.name for t in win.project.tracks] == ["Piste Un", "Piste Deux"]
    assert "Piste Trois" in win.statusBar().currentMessage()

    # 4. Supprimer la piste sélectionnée (Piste Deux) via le bouton de l'inspecteur
    assert win.selected_track_id == t2.id
    assert win.inspector.btn_delete_track.isVisible() is True
    win.inspector.btn_delete_track.click()
    qapp.processEvents()

    assert [t.name for t in win.project.tracks] == ["Piste Un"]
    assert "Piste Deux" in win.statusBar().currentMessage()

    win.ipc_server.stop()
    win.close()


def test_drag_and_drop_reorder_tracks_preserves_all_data(qapp):
    """
    Vérifie que glisser-déposer la bande de couleur d'une piste du bas vers le haut
    (et inversement) réordonne les pistes dans le projet, les en-têtes, la timeline et
    le mixeur tout en conservant 100% des données (clips MIDI, notes, clips Audio,
    échantillons, plugins, volume, pan, mute, solo, armement, hauteur, sérialisation).
    """
    win = MainWindow()
    win.resize(1280, 800)
    win.show()
    qapp.processEvents()

    # Piste du haut (Index 0) : Piste MIDI riche
    track_top = Track(
        name="Top MIDI Synth",
        track_type="midi",
        color="#38bdf8",
        volume=0.72,
        pan=-0.35,
        muted=True,
        soloed=False,
        armed=False,
        height=84,
        plugin_path="novadaw.synth",
        plugin_name="NovaSynth",
    )
    midi_clip = MidiClip(
        name="Arp Melody",
        start_beat=4.0,
        length_beats=8.0,
        color="#38bdf8",
        notes=[
            MidiNote(pitch=60, start_beat=0.0, duration=1.0, velocity=110),
            MidiNote(pitch=67, start_beat=2.0, duration=1.5, velocity=95),
        ],
    )
    track_top.clips.append(midi_clip)

    # Piste du bas (Index 1) : Piste Audio riche
    track_bottom = Track(
        name="Bottom Audio Vocal",
        track_type="audio",
        color="#10b981",
        volume=1.15,
        pan=0.45,
        muted=False,
        soloed=True,
        armed=True,
        height=92,
    )
    samples = np.sin(np.linspace(0, 20 * np.pi, 4410, dtype=np.float32))
    audio_clip = AudioClip(
        name="Vocal Take 1",
        start_beat=2.0,
        length_beats=4.0,
        gain=1.25,
        color="#10b981",
        source_offset_beats=0.5,
        audio_data=samples.copy(),
        sample_rate=44100,
    )
    track_bottom.clips.append(audio_clip)

    win.project.add_track(track_top)
    win.project.add_track(track_bottom)
    win.refresh_project_ui()
    qapp.processEvents()

    # Sélectionner le clip audio de la piste du bas avant le déplacement
    win.timeline_grid.selected_clip = (track_bottom, audio_clip)

    assert [t.id for t in win.project.tracks] == [track_top.id, track_bottom.id]

    header_bottom = win.headers_layout.itemAt(1).widget()
    assert header_bottom.track.id == track_bottom.id

    # Simuler le glisser-déposer de la bande de couleur de la piste du bas (y=120) vers le haut (y=10)
    start_global_y = win.headers_container.mapToGlobal(QPoint(4, 120)).y()
    header_bottom._start_reorder_drag(start_global_y)
    assert header_bottom._reordering_track is True

    target_global_y = win.headers_container.mapToGlobal(QPoint(4, 10)).y()
    header_bottom._update_reorder_drag(target_global_y)
    header_bottom._finish_reorder_drag()
    qapp.processEvents()

    # 1. Vérifier le nouvel ordre dans project.tracks : la piste du bas est devenue la piste du dessus !
    assert [t.id for t in win.project.tracks] == [track_bottom.id, track_top.id]
    assert [t.name for t in win.project.tracks] == ["Bottom Audio Vocal", "Top MIDI Synth"]

    # 2. Vérifier que les widgets d'en-tête sont dans le nouvel ordre sans avoir été détruits
    h0 = win.headers_layout.itemAt(0).widget()
    h1 = win.headers_layout.itemAt(1).widget()
    assert h0 is header_bottom
    assert h0.track.id == track_bottom.id
    assert h1.track.id == track_top.id

    # 3. Vérifier l'ordre vertical dans la TimelineGrid
    layout = win.timeline_grid.get_track_layout()
    assert layout[0][0].id == track_bottom.id
    assert layout[0][1] == 0
    assert layout[0][2] == 92
    assert layout[1][0].id == track_top.id
    assert layout[1][1] == 92
    assert layout[1][2] == 84

    # 4. Vérifier l'ordre des tranches dans le Mixeur
    if win.mixer_widget:
        assert list(win.mixer_widget.strips.keys()) == [track_bottom.id, track_top.id]

    # 5. Vérifier l'intégrité absolue de toutes les données des deux pistes
    b_after = win.project.tracks[0]
    t_after = win.project.tracks[1]

    assert b_after.volume == pytest.approx(1.15)
    assert b_after.pan == pytest.approx(0.45)
    assert b_after.muted is False
    assert b_after.soloed is True
    assert b_after.armed is True
    assert b_after.height == 92
    assert len(b_after.clips) == 1
    assert b_after.clips[0].id == audio_clip.id
    assert b_after.clips[0].name == "Vocal Take 1"
    assert b_after.clips[0].start_beat == pytest.approx(2.0)
    assert b_after.clips[0].source_offset_beats == pytest.approx(0.5)
    assert np.allclose(b_after.clips[0].audio_data, samples)

    assert t_after.volume == pytest.approx(0.72)
    assert t_after.pan == pytest.approx(-0.35)
    assert t_after.muted is True
    assert t_after.soloed is False
    assert t_after.armed is False
    assert t_after.height == 84
    assert t_after.plugin_path == "novadaw.synth"
    assert len(t_after.clips) == 1
    assert t_after.clips[0].id == midi_clip.id
    assert len(t_after.clips[0].notes) == 2
    assert [n.pitch for n in t_after.clips[0].notes] == [60, 67]

    # La sélection de clip dans la timeline est toujours intacte
    assert win.timeline_grid.selected_clip == (track_bottom, audio_clip)

    # 6. Vérifier que le glisser-déposer inverse (du haut vers le bas) fonctionne également
    top_start_global_y = win.headers_container.mapToGlobal(QPoint(4, 20)).y()
    header_bottom._start_reorder_drag(top_start_global_y)
    bottom_target_global_y = win.headers_container.mapToGlobal(QPoint(4, 150)).y()
    header_bottom._update_reorder_drag(bottom_target_global_y)
    header_bottom._finish_reorder_drag()
    qapp.processEvents()

    assert [t.id for t in win.project.tracks] == [track_top.id, track_bottom.id]

    win.ipc_server.stop()
    win.close()
