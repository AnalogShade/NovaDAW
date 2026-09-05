"""
ui/main_window.py - Fenêtre principale du DAW inspirée de Cubase Pro
"""
import os
from typing import Optional
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QScrollArea, QTabWidget, QFileDialog, QMessageBox, QStatusBar,
    QFrame, QLabel, QPushButton, QSizePolicy
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence, QIcon

from core.project import Project, Track, MidiClip, AudioClip
from core.serializer import save_project, load_project
from core.audio_engine import AudioEngine
from ui.transport_bar import TransportBar
from ui.track_header import TrackHeaderWidget
from ui.timeline_view import TimelineRuler, TimelineGrid
from ui.piano_roll import PianoRoll
from ui.audio_editor import AudioEditor
from ui.dialogs import AddTrackDialog
from ui.help_dialog import HelpDialog


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("NovaDAW - Digital Audio Workstation")
        self.resize(1280, 820)
        self.setMinimumSize(960, 600)

        # 1. Moteur Audio & Projet
        self.project = Project.create_default()
        self.audio_engine = AudioEngine()
        self.audio_engine.set_project(self.project)

        self.is_lower_zone_minimized = False
        self._saved_lower_height = 270

        # 2. Construction de l'interface
        self._init_menus()
        self._init_central_ui()
        self._init_status_bar()

        # 3. Timer d'animation de la tête de lecture (60 FPS)
        self.playback_timer = QTimer(self)
        self.playback_timer.setInterval(16)
        self.playback_timer.timeout.connect(self._update_playback_ui)
        self.playback_timer.start()

        # Recharger l'affichage avec les pistes initiales
        self.refresh_project_ui()

    def _init_menus(self):
        menubar = self.menuBar()

        # Menu Fichier
        menu_file = menubar.addMenu("&Fichier")

        act_new = QAction("&Nouveau Projet", self)
        act_new.setShortcut(QKeySequence.New)
        act_new.triggered.connect(self.new_project)
        menu_file.addAction(act_new)

        act_open = QAction("&Ouvrir Projet...", self)
        act_open.setShortcut(QKeySequence.Open)
        act_open.triggered.connect(self.open_project_dialog)
        menu_file.addAction(act_open)

        act_save = QAction("&Enregistrer", self)
        act_save.setShortcut(QKeySequence.Save)
        act_save.triggered.connect(self.save_project_action)
        menu_file.addAction(act_save)

        act_save_as = QAction("Enregistrer &sous...", self)
        act_save_as.setShortcut(QKeySequence.SaveAs)
        act_save_as.triggered.connect(self.save_project_as_dialog)
        menu_file.addAction(act_save_as)

        menu_file.addSeparator()

        act_export = QAction("⚡ &Exporter Mixage Audio (WAV)...", self)
        act_export.setShortcut("Ctrl+E")
        act_export.triggered.connect(self.export_audio_dialog)
        menu_file.addAction(act_export)

        menu_file.addSeparator()

        act_exit = QAction("&Quitter", self)
        act_exit.setShortcut("Ctrl+Q")
        act_exit.triggered.connect(self.close)
        menu_file.addAction(act_exit)

        # Menu Piste
        menu_track = menubar.addMenu("&Piste")

        act_add_track = QAction("+ &Ajouter une piste...", self)
        act_add_track.setShortcut("Ctrl+T")
        act_add_track.triggered.connect(self.show_add_track_dialog)
        menu_track.addAction(act_add_track)

        # Menu Transport
        menu_transport = menubar.addMenu("&Transport")

        act_play = QAction("Lecture / Pause", self)
        act_play.setShortcut(Qt.Key_Space)
        act_play.triggered.connect(self._toggle_play)
        menu_transport.addAction(act_play)

        act_stop = QAction("Arrêt (Stop)", self)
        act_stop.setShortcut("0")
        act_stop.triggered.connect(self._on_stop)
        menu_transport.addAction(act_stop)

        act_loop = QAction("Activer/Désactiver Boucle", self)
        act_loop.setShortcut("L")
        act_loop.triggered.connect(self._toggle_loop)
        menu_transport.addAction(act_loop)

        act_start = QAction("Aller au début", self)
        act_start.setShortcut("Home")
        act_start.triggered.connect(self._on_goto_start)
        menu_transport.addAction(act_start)

        act_end = QAction("Aller à la fin", self)
        act_end.setShortcut("End")
        act_end.triggered.connect(self._on_goto_end)
        menu_transport.addAction(act_end)

        # Menu Affichage
        menu_view = menubar.addMenu("&Affichage")
        act_toggle_zone = QAction("Afficher / Masquer Éditeur Inférieur", self)
        act_toggle_zone.setShortcut("F3")
        act_toggle_zone.triggered.connect(self._toggle_lower_zone)
        menu_view.addAction(act_toggle_zone)

        # Menu Aide
        menu_help = menubar.addMenu("&Aide")

        act_doc = QAction("📖 &Documentation & Raccourcis Clavier...", self)
        act_doc.setShortcut("F1")
        act_doc.triggered.connect(self.show_help_dialog)
        menu_help.addAction(act_doc)

        menu_help.addSeparator()

        act_about = QAction("ℹ️ À &propos de NovaDAW...", self)
        act_about.triggered.connect(self.show_about_dialog)
        menu_help.addAction(act_about)

    def _init_central_ui(self):
        main_widget = QWidget()
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Splitter Vertical : Arrangement en haut, Panneau Inférieur (Transport + Éditeurs) en bas
        self.main_splitter = QSplitter(Qt.Vertical)

        # --- 1. ZONE ARRANGEMENT (HAUT) ---
        arranger_widget = QWidget()
        arranger_layout = QHBoxLayout(arranger_widget)
        arranger_layout.setContentsMargins(0, 0, 0, 0)
        arranger_layout.setSpacing(0)

        # Panneau de gauche : En-têtes de pistes
        left_panel = QWidget()
        left_panel.setFixedWidth(240)
        left_panel.setStyleSheet("background-color: #161820; border-right: 1px solid #282a36;")
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)

        # Bouton Ajouter Piste bien visible en haut à gauche (remplace l'espace vide)
        header_bar = QFrame()
        header_bar.setFixedHeight(30)
        header_bar.setStyleSheet("background-color: #14151c; border-bottom: 1px solid #282a36;")
        hb_layout = QHBoxLayout(header_bar)
        hb_layout.setContentsMargins(4, 2, 4, 2)
        hb_layout.setSpacing(6)

        self.btn_add_track_top = QPushButton("+ Ajouter Piste")
        self.btn_add_track_top.setObjectName("btn_add_track")
        self.btn_add_track_top.setFixedHeight(24)
        self.btn_add_track_top.clicked.connect(self.show_add_track_dialog)
        hb_layout.addWidget(self.btn_add_track_top)
        left_layout.addWidget(header_bar)

        # Zone de défilement des en-têtes
        self.headers_scroll = QScrollArea()
        self.headers_scroll.setWidgetResizable(True)
        self.headers_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.headers_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.headers_scroll.setStyleSheet("border: none; background: #161820;")

        self.headers_container = QWidget()
        self.headers_layout = QVBoxLayout(self.headers_container)
        self.headers_layout.setContentsMargins(0, 0, 0, 0)
        self.headers_layout.setSpacing(0)
        self.headers_layout.addStretch()
        self.headers_scroll.setWidget(self.headers_container)

        left_layout.addWidget(self.headers_scroll)
        arranger_layout.addWidget(left_panel)

        # Panneau de droite : Règle + Timeline
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        # Règle temporelle
        self.ruler = TimelineRuler(self)
        self.ruler.seek_requested.connect(self._on_seek)
        self.ruler.loop_changed.connect(self._on_loop_region_changed)
        right_layout.addWidget(self.ruler)

        # Zone de défilement de la grille timeline
        self.timeline_scroll = QScrollArea()
        self.timeline_scroll.setWidgetResizable(True)
        self.timeline_scroll.setStyleSheet("border: none; background: #121318;")

        self.timeline_grid = TimelineGrid(self.project, self)
        self.timeline_grid.seek_requested.connect(self._on_seek)
        self.timeline_grid.clip_double_clicked.connect(self._on_clip_double_clicked)
        self.timeline_grid.project_modified.connect(self._on_project_modified)
        self.timeline_scroll.setWidget(self.timeline_grid)

        # Synchronisation des scrolls
        self.timeline_scroll.verticalScrollBar().valueChanged.connect(
            self.headers_scroll.verticalScrollBar().setValue
        )
        self.timeline_scroll.horizontalScrollBar().valueChanged.connect(
            lambda v: self.ruler.scroll(v, 0)
        )

        right_layout.addWidget(self.timeline_scroll)
        arranger_layout.addWidget(right_panel)

        self.main_splitter.addWidget(arranger_widget)

        # --- 2. CONTENEUR INFÉRIEUR (BARRE DE TRANSPORT CENTRÉE + ONGLETS ÉDITEURS) ---
        bottom_container = QWidget()
        bottom_layout = QVBoxLayout(bottom_container)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(0)

        # Barre de transport positionnée en bas au centre, directement au-dessus des onglets !
        self.transport_bar = TransportBar(self)
        self.transport_bar.play_toggled.connect(self._on_play_toggled)
        self.transport_bar.stop_clicked.connect(self._on_stop)
        self.transport_bar.loop_toggled.connect(self._on_loop_toggled)
        self.transport_bar.bpm_changed.connect(self._on_bpm_changed)
        self.transport_bar.master_volume_changed.connect(self._on_master_vol_changed)
        self.transport_bar.goto_start_clicked.connect(self._on_goto_start)
        self.transport_bar.goto_end_clicked.connect(self._on_goto_end)
        self.transport_bar.step_rewind.connect(self._on_step_relative)
        self.transport_bar.step_forward.connect(self._on_step_relative)
        bottom_layout.addWidget(self.transport_bar)

        # Zone inférieure avec onglets
        self.lower_zone = QTabWidget()

        # Bouton Flèche de minimisation vers le bas (▼ / ▲) dans le coin supérieur droit des onglets
        self.btn_toggle_lower = QPushButton("▼")
        self.btn_toggle_lower.setObjectName("btn_toggle_lower")
        self.btn_toggle_lower.setToolTip("Minimiser la zone d'édition (ne garder que les onglets)")
        self.btn_toggle_lower.clicked.connect(self._toggle_lower_zone)
        self.lower_zone.setCornerWidget(self.btn_toggle_lower, Qt.TopRightCorner)

        # Clic sur un onglet ré-ouvre automatiquement si minimisé
        self.lower_zone.tabBarClicked.connect(self._on_tab_clicked)

        self.piano_roll = PianoRoll(self.audio_engine, self)
        self.piano_roll.notes_updated.connect(self.timeline_grid.update)
        self.lower_zone.addTab(self.piano_roll, "🎹 Séquenceur MIDI (Piano Roll)")

        self.audio_editor = AudioEditor(self.audio_engine, self)
        self.audio_editor.clip_modified.connect(self.timeline_grid.update)
        self.lower_zone.addTab(self.audio_editor, "🔊 Éditeur Audio")

        bottom_layout.addWidget(self.lower_zone)
        self.main_splitter.addWidget(bottom_container)

        # Tailles initiales du splitter : Arrangement généreux, bas compact
        self.main_splitter.setSizes([480, 320])

        main_layout.addWidget(self.main_splitter)
        self.setCentralWidget(main_widget)

    def _init_status_bar(self):
        status = QStatusBar()
        status.setStyleSheet("background-color: #121316; color: #64748b; font-size: 11px; border-top: 1px solid #252834;")
        self.setStatusBar(status)
        self.lbl_engine_info = QLabel("Moteur Audio : 44.1 kHz Stéréo | Espace = Lecture/Arrêt | Double-clic = Créer/Éditer un bloc")
        status.addPermanentWidget(self.lbl_engine_info)
        status.showMessage("Prêt - Bienvenue dans NovaDAW")

    def _toggle_lower_zone(self):
        """Bascule entre état minimisé (onglets uniquement) et agrandi"""
        if not self.is_lower_zone_minimized:
            # Minimiser : masquer le contenu des onglets et fixer la hauteur à celle de la barre d'onglets
            self.is_lower_zone_minimized = True
            self.btn_toggle_lower.setText("▲")
            self.btn_toggle_lower.setToolTip("Agrandir la zone d'édition")
            tab_height = self.lower_zone.tabBar().sizeHint().height() + 8
            self.piano_roll.setVisible(False)
            self.audio_editor.setVisible(False)
            self.lower_zone.setFixedHeight(max(34, tab_height))
            self.statusBar().showMessage("Zone d'édition minimisée", 2000)
        else:
            self._expand_lower_zone()

    def _expand_lower_zone(self):
        """Ré-agrandit la zone inférieure"""
        self.is_lower_zone_minimized = False
        self.btn_toggle_lower.setText("▼")
        self.btn_toggle_lower.setToolTip("Minimiser la zone d'édition")
        self.piano_roll.setVisible(True)
        self.audio_editor.setVisible(True)
        self.lower_zone.setMinimumHeight(140)
        self.lower_zone.setMaximumHeight(16777215)
        # Restaurer une taille équilibrée
        self.main_splitter.setSizes([480, 320])
        self.statusBar().showMessage("Zone d'édition agrandie", 2000)

    def _on_tab_clicked(self, index: int):
        if self.is_lower_zone_minimized:
            self._expand_lower_zone()

    def refresh_project_ui(self):
        self.setWindowTitle(f"NovaDAW - {self.project.name}")

        self.ruler.set_loop(self.project.loop_enabled, self.project.loop_start_beat, self.project.loop_end_beat)
        self.timeline_grid.project = self.project
        self.timeline_grid.update_dimensions()

        # Reconstruire les en-têtes
        while self.headers_layout.count() > 1:
            child = self.headers_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        for track in self.project.tracks:
            header = TrackHeaderWidget(track)
            header.track_modified.connect(self._on_project_modified)
            header.track_deleted.connect(self._on_track_deleted)
            self.headers_layout.insertWidget(self.headers_layout.count() - 1, header)

        self.transport_bar.spin_bpm.setValue(self.project.bpm)
        self.transport_bar.btn_loop.setChecked(self.project.loop_enabled)

        self.timeline_grid.update()
        self.ruler.update()

    def show_add_track_dialog(self):
        dlg = AddTrackDialog(self)
        if dlg.exec():
            data = dlg.get_track_data()
            new_track = Track(
                name=data["name"],
                track_type=data["track_type"],
                color=data["color"]
            )
            self.project.add_track(new_track)
            self.refresh_project_ui()
            self.statusBar().showMessage(f"Piste '{new_track.name}' ajoutée.", 3000)

    def _on_track_deleted(self, track_id: str):
        self.project.remove_track(track_id)
        self.refresh_project_ui()
        self.statusBar().showMessage("Piste supprimée.", 3000)

    def _on_clip_double_clicked(self, track: Track, clip):
        # Auto-agrandir la zone inférieure si elle était minimisée !
        if self.is_lower_zone_minimized:
            self._expand_lower_zone()

        if isinstance(clip, MidiClip):
            self.lower_zone.setCurrentWidget(self.piano_roll)
            self.piano_roll.open_clip(track, clip)
            self.statusBar().showMessage(f"Édition du bloc MIDI : {clip.name}", 3000)
        elif isinstance(clip, AudioClip):
            self.lower_zone.setCurrentWidget(self.audio_editor)
            self.audio_editor.open_clip(track, clip)
            self.statusBar().showMessage(f"Édition du clip Audio : {clip.name}", 3000)

    def _on_play_toggled(self, playing: bool):
        if playing:
            self.audio_engine.play()
            self.statusBar().showMessage("Lecture en cours...", 2000)
        else:
            self.audio_engine.pause()
            self.statusBar().showMessage("En pause", 2000)

    def _toggle_play(self):
        new_state = not self.audio_engine.is_playing
        self.transport_bar.set_playing_state(new_state)
        self._on_play_toggled(new_state)

    def _on_stop(self):
        self.audio_engine.stop()
        self.transport_bar.set_playing_state(False)
        self.ruler.set_playhead(self.audio_engine.current_beat)
        self.timeline_grid.set_playhead(self.audio_engine.current_beat)
        self.transport_bar.update_position(self.audio_engine.current_beat, self.project.bpm)
        self.statusBar().showMessage("Arrêt - Curseur réinitialisé", 2000)

    def _on_goto_start(self):
        """Aller au tout début du morceau (Mesure 1, temps 0)"""
        self._on_seek(0.0)
        self.statusBar().showMessage("Curseur déplacé au début (Mesure 1)", 2000)

    def _on_goto_end(self):
        """Aller à la fin du morceau (après le dernier bloc)"""
        max_beat = 16.0
        for track in self.project.tracks:
            for clip in track.clips:
                max_beat = max(max_beat, clip.start_beat + clip.length_beats)
        self._on_seek(max_beat)
        self.statusBar().showMessage(f"Curseur déplacé à la fin (Temps {int(max_beat)})", 2000)

    def _on_step_relative(self, delta_beats: float):
        """Avancer ou reculer d'un certain nombre de temps"""
        new_beat = max(0.0, self.audio_engine.current_beat + delta_beats)
        self._on_seek(new_beat)

    def _toggle_loop(self):
        enabled = not self.project.loop_enabled
        self.transport_bar.btn_loop.setChecked(enabled)
        self._on_loop_toggled(enabled)

    def _on_loop_toggled(self, enabled: bool):
        self.project.loop_enabled = enabled
        self.ruler.set_loop(enabled, self.project.loop_start_beat, self.project.loop_end_beat)
        self.timeline_grid.update()

    def _on_loop_region_changed(self, start_b: float, end_b: float):
        self.project.loop_start_beat = start_b
        self.project.loop_end_beat = end_b
        self.project.loop_enabled = True
        self.transport_bar.btn_loop.setChecked(True)
        self.timeline_grid.update()

    def _on_seek(self, beat: float):
        self.audio_engine.seek_beat(beat)
        self.ruler.set_playhead(beat)
        self.timeline_grid.set_playhead(beat)
        self.transport_bar.update_position(beat, self.project.bpm)

    def _on_bpm_changed(self, bpm: float):
        self.project.bpm = bpm

    def _on_master_vol_changed(self, vol: float):
        self.audio_engine.master_volume = vol

    def _on_project_modified(self):
        self.timeline_grid.update()

    def _update_playback_ui(self):
        if self.audio_engine.is_playing:
            beat = self.audio_engine.current_beat
            self.ruler.set_playhead(beat)
            self.timeline_grid.set_playhead(beat)
            self.transport_bar.update_position(beat, self.project.bpm)

    # --- Actions Fichier ---

    def new_project(self):
        self.audio_engine.stop()
        self.project = Project.create_default()
        self.audio_engine.set_project(self.project)
        self.refresh_project_ui()
        self.statusBar().showMessage("Nouveau projet créé.", 3000)

    def open_project_dialog(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Ouvrir un projet NovaDAW",
            "",
            "Fichiers NovaDAW (*.ndaw);;Tous les fichiers (*.*)"
        )
        if file_path:
            try:
                self.audio_engine.stop()
                self.project = load_project(file_path)
                self.audio_engine.set_project(self.project)
                self.refresh_project_ui()
                self.statusBar().showMessage(f"Projet chargé : {os.path.basename(file_path)}", 4000)
            except Exception as e:
                QMessageBox.critical(self, "Erreur d'ouverture", f"Impossible d'ouvrir le projet :\n{e}")

    def save_project_action(self):
        if self.project.file_path:
            try:
                save_project(self.project, self.project.file_path)
                self.statusBar().showMessage("Projet enregistré avec succès.", 3000)
            except Exception as e:
                QMessageBox.critical(self, "Erreur d'enregistrement", f"Impossible d'enregistrer le projet :\n{e}")
        else:
            self.save_project_as_dialog()

    def save_project_as_dialog(self):
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Enregistrer le projet sous...",
            f"{self.project.name}.ndaw",
            "Fichiers NovaDAW (*.ndaw);;Tous les fichiers (*.*)"
        )
        if file_path:
            if not file_path.endswith(".ndaw"):
                file_path += ".ndaw"
            try:
                self.project.name = os.path.splitext(os.path.basename(file_path))[0]
                save_project(self.project, file_path)
                self.refresh_project_ui()
                self.statusBar().showMessage(f"Projet enregistré sous {os.path.basename(file_path)}", 4000)
            except Exception as e:
                QMessageBox.critical(self, "Erreur d'enregistrement", f"Impossible d'enregistrer le projet :\n{e}")

    def export_audio_dialog(self):
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Exporter le mixage audio (WAV)",
            f"{self.project.name}.wav",
            "Fichiers WAV (*.wav)"
        )
        if file_path:
            if not file_path.endswith(".wav"):
                file_path += ".wav"
            self.statusBar().showMessage("Exportation audio en cours...", 5000)
            try:
                end_bar = int(max(8, (self.project.loop_end_beat / 4.0)))
                success = self.audio_engine.export_wav(file_path, end_bar=end_bar)
                if success:
                    QMessageBox.information(self, "Exportation terminée", f"Le fichier audio a été généré avec succès :\n{file_path}")
                    self.statusBar().showMessage(f"Audio exporté : {os.path.basename(file_path)}", 5000)
            except Exception as e:
                QMessageBox.critical(self, "Erreur d'exportation", f"Erreur lors de l'export WAV :\n{e}")

    def show_help_dialog(self, initial_tab=0):
        dlg = HelpDialog(self)
        if initial_tab != 0:
            # Sélecteur d'onglet direct si demandé
            tab_widget = dlg.findChild(QTabWidget)
            if tab_widget:
                tab_widget.setCurrentIndex(initial_tab)
        dlg.exec()

    def show_about_dialog(self):
        self.show_help_dialog(initial_tab=2)

    def closeEvent(self, event):
        self.audio_engine.close()
        super().closeEvent(event)
