"""
ui/main_window.py - Fenêtre principale du DAW inspirée de Cubase Pro
"""
import os
import time
from typing import Optional, Dict, Any
import soundfile as sf
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QScrollArea, QTabWidget, QFileDialog, QMessageBox, QStatusBar,
    QFrame, QLabel, QPushButton, QSizePolicy
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence, QIcon, QPixmap

from core.project import Project, Track, MidiClip, AudioClip
from core.serializer import save_project, load_project
from core.audio_engine import AudioEngine
from core.audio_importer import load_audio_file, QT_FILE_DIALOG_FILTER
from core.hardware_manager import hardware_manager
from ui.transport_bar import TransportBar
from ui.track_header import TrackHeaderWidget
from ui.timeline_view import TimelineRuler, TimelineGrid
from ui.editing_toolbar import EditingToolbar
from ui.piano_roll import PianoRoll
from ui.audio_editor import AudioEditor
from ui.dialogs import AddTrackDialog
from ui.help_dialog import HelpDialog
from ui.device_settings_dialog import DeviceSettingsDialog
from core.ipc import NovaIpcServer
from core.plugin_manager import global_plugin_manager
from ui.inspector import TrackInspector
from ui.vst_rack import VstRackWidget
from ui.floating_plugin_rack import FloatingPluginRackDialog
from ui.plugin_dialogs import analyze_plugin_file, PluginManagerDialog, PluginFolderManagerDialog, open_plugin_editor_gui, open_native_plugin_editor
from plugins.registry import plugin_registry, ensure_plugins_loaded
import core.actions


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("NovaDAW - Digital Audio Workstation")
        self.resize(1280, 820)
        self.setMinimumSize(960, 600)

        # Icône officielle Supernova
        self.icon_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "assets", "nova_icon.png"))
        if os.path.exists(self.icon_path):
            self.setWindowIcon(QIcon(self.icon_path))

        # 1. Moteur Audio & Projet
        self.project = Project.create_empty()
        self.audio_engine = AudioEngine()
        self.audio_engine.set_project(self.project)

        self.is_lower_zone_minimized = False
        self._saved_lower_height = 270
        self._active_recording_clips: Dict[str, Any] = {}
        self.floating_vst_rack: Optional[FloatingPluginRackDialog] = None

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
        global_plugin_manager.scan_updated.connect(self.refresh_project_ui)

        # 4. Serveur IPC pour le contrôle distant et protocole MCP (IA)
        self.ipc_server = NovaIpcServer(self)
        if self.ipc_server.start():
            print(f"[NovaDAW] Serveur IPC actif sur le port {self.ipc_server.actual_port} (Support MCP prêt).")

        # 5. Déclenchement automatique et non-bloquant de la détection de nouveaux plugins (2s après démarrage)
        QTimer.singleShot(2000, lambda: global_plugin_manager.trigger_background_scan())

    def _init_menus(self):
        menubar = self.menuBar()

        # Menu Fichier
        menu_file = menubar.addMenu("&Fichier")

        act_new = QAction("&Nouveau Projet", self)
        act_new.setShortcut(QKeySequence.New)
        act_new.triggered.connect(self.new_project)
        menu_file.addAction(act_new)

        act_demo = QAction("🎼 &Charger le Projet Démo", self)
        act_demo.triggered.connect(self.load_demo_project)
        menu_file.addAction(act_demo)

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

        # Sous-menu Importer
        menu_import = menu_file.addMenu("📥 &Importer")

        act_import_audio = QAction("🎵 &Fichier Audio (WAV, MP3, FLAC, OGG, AIFF, M4A...)...", self)
        act_import_audio.setShortcut("Ctrl+I")
        act_import_audio.triggered.connect(self.import_audio_dialog)
        menu_import.addAction(act_import_audio)

        # Sous-menu Exporter
        menu_export = menu_file.addMenu("⚡ &Exporter")

        act_export_audio = QAction("🎵 &Mixage Audio (WAV)...", self)
        act_export_audio.setShortcut("Ctrl+E")
        act_export_audio.triggered.connect(self.export_audio_dialog)
        menu_export.addAction(act_export_audio)

        menu_file.addSeparator()

        act_exit = QAction("&Quitter", self)
        act_exit.setShortcut("Ctrl+Q")
        act_exit.triggered.connect(self.close)
        menu_file.addAction(act_exit)

        # Menu Édition
        menu_edit = menubar.addMenu("&Édition")

        act_cut = QAction("✂️ &Couper", self)
        act_cut.setShortcut("Ctrl+X")
        act_cut.triggered.connect(self._on_cut_clip)
        menu_edit.addAction(act_cut)

        act_copy = QAction("📋 &Copier", self)
        act_copy.setShortcut("Ctrl+C")
        act_copy.triggered.connect(self._on_copy_clip)
        menu_edit.addAction(act_copy)

        act_paste = QAction("📥 Co&ller à la tête de lecture", self)
        act_paste.setShortcut("Ctrl+V")
        act_paste.triggered.connect(self._on_paste_clip)
        menu_edit.addAction(act_paste)

        act_dup = QAction("📑 &Dupliquer", self)
        act_dup.setShortcut("Ctrl+D")
        act_dup.triggered.connect(self._on_duplicate_clip)
        menu_edit.addAction(act_dup)

        act_del = QAction("🗑️ &Supprimer", self)
        act_del.setShortcut("Delete")
        act_del.triggered.connect(self._on_delete_clip)
        menu_edit.addAction(act_del)

        menu_edit.addSeparator()

        act_split = QAction("✂️ &Scinder au curseur (Playhead)", self)
        act_split.setShortcut("Ctrl+K")
        act_split.triggered.connect(self._on_split_playhead)
        menu_edit.addAction(act_split)

        menu_edit.addSeparator()

        act_snap = QAction("🧲 Activer / Désactiver &Aimantage (Snap)", self)
        act_snap.setShortcut("J")
        act_snap.triggered.connect(self._toggle_snap)
        menu_edit.addAction(act_snap)

        # Menu Piste
        menu_track = menubar.addMenu("&Piste")

        act_add_track = QAction("+ &Ajouter une piste...", self)
        act_add_track.setShortcut("Ctrl+T")
        act_add_track.triggered.connect(self.show_add_track_dialog)
        menu_track.addAction(act_add_track)

        act_master = QAction("🔴 &Inspecter Piste Master", self)
        act_master.setShortcut("Ctrl+M")
        act_master.triggered.connect(self.select_master_track)
        menu_track.addAction(act_master)

        # Menu Plugins
        menu_plugins = menubar.addMenu("&Plugins")
        act_project_plugin = QAction("➕ Ajouter un plugin au projet…", self)
        act_project_plugin.triggered.connect(self.add_project_plugin)
        menu_plugins.addAction(act_project_plugin)
        menu_plugins.addSeparator()

        act_mixer = QAction("🎛️ &Console de Mixage (Mixeur)...", self)
        act_mixer.setShortcut("F5")
        act_mixer.triggered.connect(self.show_mixer_console)
        menu_plugins.addAction(act_mixer)

        act_eq = QAction("📊 Ajouter &Égaliseur Paramétrique...", self)
        act_eq.triggered.connect(self.add_eq_to_selected_track)
        menu_plugins.addAction(act_eq)

        act_comp = QAction("🗜️ Ajouter &Compresseur Dynamique...", self)
        act_comp.triggered.connect(self.add_comp_to_selected_track)
        menu_plugins.addAction(act_comp)

        menu_plugins.addSeparator()

        act_rack = QAction("🎛️ &Rack de Plugins Flottant (F11)…", self)
        act_rack.setShortcut("F11")
        act_rack.triggered.connect(self.show_floating_vst_rack)
        menu_plugins.addAction(act_rack)

        menu_plugins.addSeparator()

        act_scan = QAction("🔍 &Scanner les dossiers de plugins...", self)
        act_scan.triggered.connect(self.open_plugin_manager_and_scan)
        menu_plugins.addAction(act_scan)

        act_add_plugin = QAction("➕ &Ajouter un plugin manuellement (.vst3)...", self)
        act_add_plugin.triggered.connect(self.add_plugin_file_dialog)
        menu_plugins.addAction(act_add_plugin)

        act_folders = QAction("📁 &Gérer les dossiers de plugins...", self)
        act_folders.triggered.connect(self.open_plugin_folders_dialog)
        menu_plugins.addAction(act_folders)

        menu_plugins.addSeparator()

        act_plugin_mgr = QAction("⚙️ &Gestionnaire de plugins...", self)
        act_plugin_mgr.setShortcut("F4")
        act_plugin_mgr.triggered.connect(self.open_plugin_manager_dialog)
        menu_plugins.addAction(act_plugin_mgr)

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

        act_record = QAction("Enregistrement (Record)", self)
        act_record.setShortcut("R")
        act_record.triggered.connect(self._toggle_record)
        menu_transport.addAction(act_record)

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

        act_toggle_inspector = QAction("Afficher / Masquer &Inspecteur de Piste", self)
        act_toggle_inspector.setShortcut("F2")
        act_toggle_inspector.triggered.connect(self._toggle_inspector)
        menu_view.addAction(act_toggle_inspector)

        act_toggle_zone = QAction("Afficher / Masquer Éditeur Inférieur", self)
        act_toggle_zone.setShortcut("F3")
        act_toggle_zone.triggered.connect(self._toggle_lower_zone)
        menu_view.addAction(act_toggle_zone)

        menu_view.addSeparator()

        act_zoom_tracks_in = QAction("🔍 Agrandir la hauteur des pistes", self)
        act_zoom_tracks_in.setShortcut("Ctrl+Alt+Up")
        act_zoom_tracks_in.triggered.connect(self._zoom_track_heights_in)
        menu_view.addAction(act_zoom_tracks_in)

        act_zoom_tracks_out = QAction("🔍 Réduire la hauteur des pistes", self)
        act_zoom_tracks_out.setShortcut("Ctrl+Alt+Down")
        act_zoom_tracks_out.triggered.connect(self._zoom_track_heights_out)
        menu_view.addAction(act_zoom_tracks_out)

        act_zoom_tracks_reset = QAction("Restaurer la hauteur par défaut des pistes", self)
        act_zoom_tracks_reset.setShortcut("Ctrl+Alt+0")
        act_zoom_tracks_reset.triggered.connect(self._zoom_track_heights_reset)
        menu_view.addAction(act_zoom_tracks_reset)

        # Menu Périphériques (Audio & GPU)
        menu_devices = menubar.addMenu("&Périphériques")

        act_devices = QAction("⚙️ &Configuration Audio & Graphique (GPU)...", self)
        act_devices.setShortcut("Ctrl+,")
        act_devices.triggered.connect(self.open_device_settings_dialog)
        menu_devices.addAction(act_devices)

        act_test_sound = QAction("▶ &Tester la Sortie Audio (Test Tone)", self)
        act_test_sound.triggered.connect(lambda: self.audio_engine.play_test_tone())
        menu_devices.addAction(act_test_sound)

        menu_devices.addSeparator()

        act_mcp = QAction("🤖 &Statut IA & Protocole MCP...", self)
        act_mcp.triggered.connect(self.open_mcp_settings_dialog)
        menu_devices.addAction(act_mcp)

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

        # --- BARRE DE MARQUE & LOGO SUPERNOVA NOVADAW (HAUT À GAUCHE) ---
        brand_bar = QFrame()
        brand_bar.setFixedHeight(38)
        brand_bar.setObjectName("nova_brand_bar")
        brand_bar.setStyleSheet("""
            QFrame#nova_brand_bar {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #10121a, stop:0.25 #151824, stop:0.75 #131520, stop:1 #0e0f17);
                border-bottom: 1px solid #232738;
            }
        """)
        bb_layout = QHBoxLayout(brand_bar)
        bb_layout.setContentsMargins(10, 4, 12, 4)
        bb_layout.setSpacing(10)

        # 1. Logo Supernova & Identité visuelle NovaDAW
        self.lbl_logo_icon = QLabel()
        if hasattr(self, "icon_path") and os.path.exists(self.icon_path):
            pix = QPixmap(self.icon_path).scaled(26, 26, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.lbl_logo_icon.setPixmap(pix)
        else:
            self.lbl_logo_icon.setText("🌌")
            self.lbl_logo_icon.setStyleSheet("font-size: 20px;")
        self.lbl_logo_icon.setCursor(Qt.PointingHandCursor)
        self.lbl_logo_icon.setToolTip("À propos de NovaDAW")
        self.lbl_logo_icon.mousePressEvent = lambda e: self.show_about_dialog()
        bb_layout.addWidget(self.lbl_logo_icon)

        lbl_brand = QLabel("NOVADAW")
        lbl_brand.setStyleSheet("font-size: 13px; font-weight: 900; letter-spacing: 1.5px; color: #38bdf8;")
        bb_layout.addWidget(lbl_brand)

        badge_pro = QLabel("PRO")
        badge_pro.setStyleSheet("background: #1e1b4b; color: #a5b4fc; font-size: 9px; font-weight: bold; padding: 2px 6px; border-radius: 4px; border: 1px solid #4338ca;")
        bb_layout.addWidget(badge_pro)

        # Séparateur vertical
        sep1 = QFrame()
        sep1.setFrameShape(QFrame.VLine)
        sep1.setStyleSheet("color: #2b2f42; max-height: 20px;")
        bb_layout.addWidget(sep1)

        # Boutons rapides d'action
        btn_quick_track = QPushButton("+ Piste")
        btn_quick_track.setFixedHeight(26)
        btn_quick_track.setStyleSheet("font-size: 11px; padding: 2px 10px; background-color: #1e2230; border: 1px solid #2f354a;")
        btn_quick_track.clicked.connect(self.show_add_track_dialog)
        bb_layout.addWidget(btn_quick_track)

        btn_quick_mixer = QPushButton("🎛️ Mixeur")
        btn_quick_mixer.setFixedHeight(26)
        btn_quick_mixer.setStyleSheet("font-size: 11px; padding: 2px 10px; background-color: #1e2230; border: 1px solid #2f354a;")
        btn_quick_mixer.clicked.connect(self.show_mixer_console)
        bb_layout.addWidget(btn_quick_mixer)

        self.btn_quick_plugins = QPushButton("🎛️ Plugins (F11)")
        self.btn_quick_plugins.setFixedHeight(26)
        self.btn_quick_plugins.setStyleSheet("""
            QPushButton {
                font-size: 11px;
                font-weight: bold;
                padding: 2px 12px;
                background-color: #162032;
                color: #38bdf8;
                border: 1px solid #0284c7;
                border-radius: 3px;
            }
            QPushButton:hover {
                background-color: #0284c7;
                color: #ffffff;
            }
        """)
        self.btn_quick_plugins.setToolTip("Ouvrir la Fenêtre Flottante du Rack de Plugins (F11 - Style Cubase)")
        self.btn_quick_plugins.clicked.connect(self.show_floating_vst_rack)
        bb_layout.addWidget(self.btn_quick_plugins)

        bb_layout.addStretch()

        # Côté droit : Accès Périphériques Audio & GPU et Statut IA
        self.btn_quick_devices = QPushButton("⚙️ Périphériques (Audio & GPU)")
        self.btn_quick_devices.setFixedHeight(26)
        self.btn_quick_devices.setToolTip("Configurer les cartes son, pilotes audio (ASIO/WASAPI) et cartes graphiques (Ctrl+,)")
        self.btn_quick_devices.setStyleSheet("font-size: 11px; padding: 2px 10px; background-color: #1f2438; color: #93c5fd; border: 1px solid #3b82f6;")
        self.btn_quick_devices.clicked.connect(self.open_device_settings_dialog)
        bb_layout.addWidget(self.btn_quick_devices)

        self.lbl_mcp_badge = QLabel("🤖 IA MCP : Connecté")
        self.lbl_mcp_badge.setToolTip("Le serveur MCP local est actif et à l'écoute des agents IA (Antigravity, Cursor, Codex)")
        self.lbl_mcp_badge.setStyleSheet("color: #34d399; font-size: 11px; font-weight: 600; padding: 2px 8px; background: #06281e; border: 1px solid #059669; border-radius: 4px;")
        bb_layout.addWidget(self.lbl_mcp_badge)

        main_layout.addWidget(brand_bar)

        # Palette & Barre d'outils d'édition vers le haut de l'arrangement
        self.editing_toolbar = EditingToolbar(self)
        main_layout.addWidget(self.editing_toolbar)

        # Splitter Vertical : Arrangement en haut, Panneau Inférieur (Transport + Éditeurs) en bas
        self.main_splitter = QSplitter(Qt.Vertical)
        self.main_splitter.setObjectName("main_vertical_splitter")
        self.main_splitter.setHandleWidth(5)

        # --- 1. ZONE ARRANGEMENT (HAUT) AVEC SPLITTER HORIZONTAL ---
        self.arranger_splitter = QSplitter(Qt.Horizontal)
        self.arranger_splitter.setObjectName("arranger_splitter")
        self.arranger_splitter.setHandleWidth(5)

        # Panneau d'inspection de piste à gauche (style Cubase)
        self.inspector = TrackInspector(self.project, self)
        self.inspector.track_modified.connect(self._on_project_modified)
        self.inspector.track_modified.connect(self.refresh_project_ui)
        self.arranger_splitter.addWidget(self.inspector)

        # Panneau central-gauche : En-têtes de pistes (redimensionnable)
        left_panel = QWidget()
        left_panel.setObjectName("left_headers_panel")
        left_panel.setMinimumWidth(180)
        left_panel.setMaximumWidth(550)
        left_panel.setStyleSheet("QWidget#left_headers_panel { background-color: #161820; border-right: 1px solid #282a36; }")
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)

        # Barre supérieure des en-têtes avec bouton Masquer/Afficher Inspecteur et Ajouter Piste
        header_bar = QFrame()
        header_bar.setObjectName("track_header_bar")
        header_bar.setFixedHeight(30)
        header_bar.setStyleSheet("QFrame#track_header_bar { background-color: #14151c; border-bottom: 1px solid #282a36; }")
        hb_layout = QHBoxLayout(header_bar)
        hb_layout.setContentsMargins(4, 2, 4, 2)
        hb_layout.setSpacing(4)

        self.btn_toggle_insp_top = QPushButton("◀")
        self.btn_toggle_insp_top.setToolTip("Afficher / Masquer l'Inspecteur de Piste (F2)")
        self.btn_toggle_insp_top.setFixedSize(22, 22)
        self.btn_toggle_insp_top.setStyleSheet("padding: 0px; font-size: 10px; font-weight: bold;")
        self.btn_toggle_insp_top.clicked.connect(self._toggle_inspector)
        hb_layout.addWidget(self.btn_toggle_insp_top)

        self.btn_add_track_top = QPushButton("+ Piste")
        self.btn_add_track_top.setObjectName("btn_add_track")
        self.btn_add_track_top.setFixedHeight(24)
        self.btn_add_track_top.clicked.connect(self.show_add_track_dialog)
        hb_layout.addWidget(self.btn_add_track_top)
        left_layout.addWidget(header_bar)

        # Zone de défilement des en-têtes
        self.headers_scroll = QScrollArea()
        self.headers_scroll.setObjectName("headers_scroll")
        self.headers_scroll.setWidgetResizable(True)
        self.headers_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.headers_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.headers_scroll.setStyleSheet("QScrollArea#headers_scroll { border: none; background: #161820; }")

        self.headers_container = QWidget()
        self.headers_container.setObjectName("headers_container")
        self.headers_container.setStyleSheet("QWidget#headers_container { background: transparent; }")
        self.headers_layout = QVBoxLayout(self.headers_container)
        self.headers_layout.setContentsMargins(0, 0, 0, 0)
        self.headers_layout.setSpacing(0)
        self.headers_layout.addStretch()
        self.headers_scroll.setWidget(self.headers_container)

        left_layout.addWidget(self.headers_scroll)
        self.arranger_splitter.addWidget(left_panel)

        # Panneau de droite : Règle + Timeline
        right_panel = QWidget()
        right_panel.setMinimumWidth(300)
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
        self.timeline_grid.clip_selected.connect(self._on_clip_selected)
        self.timeline_grid.clip_double_clicked.connect(self._on_clip_double_clicked)
        self.timeline_grid.project_modified.connect(self._on_project_modified)
        self.timeline_grid.track_height_changed.connect(self._on_track_height_changed)
        self.timeline_grid.status_message.connect(self.statusBar().showMessage)

        # Connexions de la barre d'outils d'édition
        self.editing_toolbar.tool_changed.connect(self._on_tool_changed)
        self.editing_toolbar.split_playhead_requested.connect(self._on_split_playhead)
        self.editing_toolbar.copy_requested.connect(self._on_copy_clip)
        self.editing_toolbar.cut_requested.connect(self._on_cut_clip)
        self.editing_toolbar.paste_requested.connect(self._on_paste_clip)
        self.editing_toolbar.duplicate_requested.connect(self._on_duplicate_clip)
        self.editing_toolbar.delete_requested.connect(self._on_delete_clip)
        self.editing_toolbar.snap_toggled.connect(self._on_snap_toggled)
        self.editing_toolbar.grid_resolution_changed.connect(self._on_grid_resolution_changed)
        self.editing_toolbar.zoom_in_requested.connect(self._zoom_timeline_in)
        self.editing_toolbar.zoom_out_requested.connect(self._zoom_timeline_out)
        self.editing_toolbar.zoom_reset_requested.connect(self._zoom_timeline_reset)

        self.timeline_scroll.setWidget(self.timeline_grid)

        # Synchronisation des scrolls
        self.timeline_scroll.verticalScrollBar().valueChanged.connect(
            self.headers_scroll.verticalScrollBar().setValue
        )
        self.timeline_scroll.horizontalScrollBar().valueChanged.connect(
            lambda v: self.ruler.scroll(v, 0)
        )

        right_layout.addWidget(self.timeline_scroll)
        self.arranger_splitter.addWidget(right_panel)

        self.arranger_splitter.setCollapsible(0, True)
        self.arranger_splitter.setCollapsible(1, False)
        self.arranger_splitter.setCollapsible(2, False)
        self.arranger_splitter.setSizes([230, 240, 810])
        self.arranger_splitter.setStretchFactor(0, 0)
        self.arranger_splitter.setStretchFactor(1, 0)
        self.arranger_splitter.setStretchFactor(2, 1)

        self.main_splitter.addWidget(self.arranger_splitter)

        # --- 2. CONTENEUR INFÉRIEUR (BARRE DE TRANSPORT CENTRÉE + ONGLETS ÉDITEURS) ---
        bottom_container = QWidget()
        bottom_layout = QVBoxLayout(bottom_container)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(0)

        # Barre de transport positionnée en bas au centre, directement au-dessus des onglets !
        self.transport_bar = TransportBar(self)
        self.transport_bar.play_toggled.connect(self._on_play_toggled)
        self.transport_bar.stop_clicked.connect(self._on_stop)
        self.transport_bar.record_toggled.connect(self._on_record_toggled)
        self.transport_bar.loop_toggled.connect(self._on_loop_toggled)
        self.transport_bar.bpm_changed.connect(self._on_bpm_changed)
        self.transport_bar.master_volume_changed.connect(self._on_master_vol_changed)
        self.transport_bar.master_clip_reset.connect(lambda: self.audio_engine.reset_master_clip() if hasattr(self.audio_engine, "reset_master_clip") else None)
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
        self.piano_roll.seek_requested.connect(self._on_seek)
        self.lower_zone.addTab(self.piano_roll, "🎹 Séquenceur MIDI (Piano Roll)")

        self.audio_editor = AudioEditor(self.audio_engine, self)
        self.audio_editor.clip_modified.connect(self.timeline_grid.update)
        self.lower_zone.addTab(self.audio_editor, "🔊 Éditeur Audio")

        # Console de Mixage (Mixeur)
        master_t = self.project.ensure_master_track()
        mixer_p = None
        for p in master_t.plugins:
            if getattr(p, "plugin_type_id", None) == "novadaw.mixer":
                mixer_p = p
                break
        if mixer_p:
            self.mixer_widget = mixer_p.create_editor(self)
            self.lower_zone.addTab(self.mixer_widget, "🎛️ Mixeur (F5)")
        else:
            self.mixer_widget = None

        self.vst_rack = VstRackWidget(self.project, self)
        self.vst_rack.rack_changed.connect(self._on_rack_changed)
        self.lower_zone.addTab(self.vst_rack, "🎛️ Plugins du projet (F11)")

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
        lat_ms = hardware_manager.calculate_latency_ms(self.audio_engine.block_size, self.audio_engine.sample_rate)
        self.lbl_engine_info = QLabel(f"⚙️ Moteur Audio : {self.audio_engine.sample_rate} Hz Stéréo | Tampon: {self.audio_engine.block_size} ({lat_ms} ms)")
        self.lbl_engine_info.setCursor(Qt.PointingHandCursor)
        self.lbl_engine_info.setToolTip("Cliquez pour configurer vos périphériques audio, pilotes ou carte graphique")
        self.lbl_engine_info.setStyleSheet("color: #38bdf8; font-weight: 600; padding: 0 4px;")
        self.lbl_engine_info.mousePressEvent = lambda e: self.open_device_settings_dialog()
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

        if hasattr(self, "inspector"):
            self.inspector.project = self.project
        if hasattr(self, "vst_rack"):
            self.vst_rack.set_project(self.project)
        if hasattr(self, "floating_vst_rack") and self.floating_vst_rack:
            self.floating_vst_rack.set_project(self.project)
        if hasattr(self, "mixer_widget") and self.mixer_widget:
            master_t = self.project.ensure_master_track()
            for p in master_t.plugins:
                if getattr(p, "plugin_type_id", None) == "novadaw.mixer":
                    self.mixer_widget.mixer = p
                    if hasattr(p, "set_project"):
                        p.set_project(self.project)
                    break
            self.mixer_widget.refresh_tracks()

        # Reconstruire les en-têtes
        while self.headers_layout.count() > 1:
            child = self.headers_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        for track in self.project.tracks:
            header = TrackHeaderWidget(track, parent=self.headers_container, project=self.project)
            header.track_modified.connect(self._on_project_modified)
            header.track_deleted.connect(self._on_track_deleted)
            header.track_selected.connect(self._on_track_selected)
            header.track_height_changed.connect(self._on_track_height_changed)
            self.headers_layout.insertWidget(self.headers_layout.count() - 1, header)

        # Mettre à jour l'inspecteur avec la piste sélectionnée
        if self.project.tracks:
            sel = getattr(self, "selected_track_id", None)
            target = self.project.get_track(sel) if sel else self.project.tracks[0]
            if target:
                self._on_track_selected(target.id)
            else:
                self.selected_track_id = self.project.tracks[0].id
                self._on_track_selected(self.selected_track_id)
        else:
            self.selected_track_id = None
            if hasattr(self, "inspector"):
                self.inspector.set_track(None)
            if hasattr(self, "piano_roll"):
                self.piano_roll.clear()
            if hasattr(self, "audio_editor"):
                self.audio_editor.clear()
            if hasattr(self, "timeline_grid"):
                self.timeline_grid.selected_clip = None
                self.timeline_grid.time_selection = None

        self.transport_bar.spin_bpm.setValue(self.project.bpm)
        self.transport_bar.btn_loop.setChecked(self.project.loop_enabled)

        self.timeline_grid.update()
        self.ruler.update()

    def _on_track_selected(self, track_id: Optional[str]):
        """Appelé lors d'un clic sur une piste : met à jour l'Inspecteur de piste et le Piano Roll"""
        track = self.project.get_track(track_id) if track_id else None
        if track:
            self.selected_track_id = track.id
            if hasattr(self, "inspector"):
                self.inspector.set_track(track)

            if hasattr(self, "piano_roll"):
                self.piano_roll.set_active_track(track)

            # Mise en surbrillance de l'en-tête actif
            for i in range(self.headers_layout.count() - 1):
                item = self.headers_layout.itemAt(i)
                if item and item.widget() and isinstance(item.widget(), TrackHeaderWidget):
                    item.widget().set_selected(item.widget().track.id == track.id)
        else:
            self.selected_track_id = None
            if hasattr(self, "inspector"):
                self.inspector.set_track(None)
            if hasattr(self, "piano_roll"):
                self.piano_roll.clear()
            for i in range(self.headers_layout.count() - 1):
                item = self.headers_layout.itemAt(i)
                if item and item.widget() and isinstance(item.widget(), TrackHeaderWidget):
                    item.widget().set_selected(False)

    def _on_rack_changed(self):
        """Appelé lors d'un changement dans la stack de plugins du projet"""
        if hasattr(self, "inspector") and self.inspector.current_track:
            self.inspector.set_track(self.inspector.current_track)
        self._on_project_modified()
        self.refresh_project_ui()

    def add_project_plugin(self):
        self.show_floating_vst_rack()
        if self.floating_vst_rack:
            self.floating_vst_rack.rack_widget.show_add_dialog()

    def show_vst_rack(self):
        """Bascule immédiatement vers l'onglet du Rack VST (F11)"""
        if self.is_lower_zone_minimized:
            self._expand_lower_zone()
        self.lower_zone.setCurrentWidget(self.vst_rack)
        self.statusBar().showMessage("Rack VST du Projet affiché (F11)", 2000)

    def show_floating_vst_rack(self):
        """Ouvre ou bascule sur la fenêtre flottante dédiée du Rack de Plugins (F11 - Style Cubase)."""
        if not self.floating_vst_rack:
            self.floating_vst_rack = FloatingPluginRackDialog(self.project, self)
            self.floating_vst_rack.rack_changed.connect(self._on_rack_changed)
        else:
            self.floating_vst_rack.set_project(self.project)

        self.floating_vst_rack.show_and_focus()
        self.statusBar().showMessage("Rack de Plugins flottant affiché (F11)", 2000)

    def show_mixer_console(self):
        """Bascule immédiatement vers l'onglet de la Console de Mixage (F5)"""
        if self.is_lower_zone_minimized:
            self._expand_lower_zone()
        if hasattr(self, "mixer_widget") and self.mixer_widget:
            self.lower_zone.setCurrentWidget(self.mixer_widget)
            self.statusBar().showMessage("Console de Mixage affichée (F5)", 2000)

    def select_master_track(self):
        """Sélectionne la piste Master dans l'inspecteur pour afficher sa pile de plugins"""
        master_t = self.project.ensure_master_track()
        self._on_track_selected(master_t.id)
        self.statusBar().showMessage("Piste Master sélectionnée dans l'Inspecteur (Ctrl+M)", 2000)

    def add_eq_to_selected_track(self):
        target_id = getattr(self, "selected_track_id", None)
        track = self.project.get_track(target_id) if target_id else (self.project.tracks[0] if self.project.tracks else None)
        if track:
            ensure_plugins_loaded()
            eq = plugin_registry.create_plugin("novadaw.equalizer")
            if eq:
                track.add_plugin(eq)
                self.refresh_project_ui()
                open_native_plugin_editor(eq, self)
                self.statusBar().showMessage(f"Égaliseur ajouté sur {track.name}", 2000)

    def add_comp_to_selected_track(self):
        target_id = getattr(self, "selected_track_id", None)
        track = self.project.get_track(target_id) if target_id else (self.project.tracks[0] if self.project.tracks else None)
        if track:
            ensure_plugins_loaded()
            comp = plugin_registry.create_plugin("novadaw.compressor")
            if comp:
                track.add_plugin(comp)
                self.refresh_project_ui()
                open_native_plugin_editor(comp, self)
                self.statusBar().showMessage(f"Compresseur ajouté sur {track.name}", 2000)

    def _on_track_height_changed(self, track_id: str, height: int, apply_all: bool):
        """Gère le redimensionnement fluide de la hauteur des pistes"""
        if apply_all:
            for t in self.project.tracks:
                t.height = height
            for i in range(self.headers_layout.count() - 1):
                item = self.headers_layout.itemAt(i)
                if item and item.widget() and isinstance(item.widget(), TrackHeaderWidget):
                    item.widget().set_track_height(height)
        else:
            t = self.project.get_track(track_id)
            if t:
                t.height = height
            for i in range(self.headers_layout.count() - 1):
                item = self.headers_layout.itemAt(i)
                if item and item.widget() and isinstance(item.widget(), TrackHeaderWidget):
                    if item.widget().track.id == track_id:
                        item.widget().set_track_height(height)
                        break

        self.timeline_grid.update_dimensions()
        self.timeline_grid.update()
        self._on_project_modified()

    def _zoom_track_heights_in(self):
        cur_h = getattr(self.project.tracks[0], "height", 76) if self.project.tracks else 76
        new_h = min(300, cur_h + 16)
        self._on_track_height_changed("", new_h, apply_all=True)
        self.statusBar().showMessage(f"Hauteur des pistes : {new_h}px", 1500)

    def _zoom_track_heights_out(self):
        cur_h = getattr(self.project.tracks[0], "height", 76) if self.project.tracks else 76
        new_h = max(48, cur_h - 16)
        self._on_track_height_changed("", new_h, apply_all=True)
        self.statusBar().showMessage(f"Hauteur des pistes : {new_h}px", 1500)

    def _zoom_track_heights_reset(self):
        self._on_track_height_changed("", 76, apply_all=True)
        self.statusBar().showMessage("Hauteur des pistes réinitialisée à 76px", 1500)

    def _toggle_inspector(self):
        """Affiche ou masque l'Inspecteur de Piste (F2)"""
        vis = not self.inspector.isVisible()
        self.inspector.setVisible(vis)
        if hasattr(self, "btn_toggle_insp_top"):
            self.btn_toggle_insp_top.setText("◀" if vis else "▶")
            self.btn_toggle_insp_top.setToolTip("Masquer l'Inspecteur (F2)" if vis else "Afficher l'Inspecteur (F2)")
        self.statusBar().showMessage(f"Inspecteur de piste {'affiché' if vis else 'masqué'} (F2)", 2000)

    def show_add_track_dialog(self):
        dlg = AddTrackDialog(self)
        if dlg.exec():
            data = dlg.get_track_data()
            new_track = Track(
                name=data["name"],
                track_type=data["track_type"],
                color=data["color"],
                plugin_path=data.get("plugin_path"),
                plugin_name=data.get("plugin_name"),
            )
            self.project.add_track(new_track)
            if new_track.plugin_path:
                self.project.add_rack_plugin(new_track.plugin_path, new_track.plugin_name, "instrument")
            self.selected_track_id = new_track.id
            self.refresh_project_ui()
            self._on_track_selected(new_track.id)
            inst_info = f" (Instrument : {new_track.plugin_name})" if new_track.plugin_name else ""
            self.statusBar().showMessage(f"Piste '{new_track.name}'{inst_info} ajoutée.", 3000)

    def _on_track_deleted(self, track_id: str):
        self.project.remove_track(track_id)
        self.refresh_project_ui()
        self.statusBar().showMessage("Piste supprimée.", 3000)

    def _on_clip_selected(self, track: Track, clip):
        """Appelé lors d'un clic de sélection sur un bloc"""
        self._on_track_selected(track.id)
        self.statusBar().showMessage(f"Bloc sélectionné : {clip.name} ({track.name}) | [Suppr] pour supprimer | [Ctrl+D] pour dupliquer", 4000)
        # Si la zone inférieure est active et non minimisée, charger directement le bloc sélectionné
        if not self.is_lower_zone_minimized:
            if isinstance(clip, MidiClip):
                self.lower_zone.setCurrentWidget(self.piano_roll)
                self.piano_roll.open_clip(track, clip)
            elif isinstance(clip, AudioClip):
                self.lower_zone.setCurrentWidget(self.audio_editor)
                self.audio_editor.open_clip(track, clip)

    def _on_clip_double_clicked(self, track: Track, clip):
        self._on_track_selected(track.id)
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

    def _on_tool_changed(self, tool_name: str):
        self.timeline_grid.set_active_tool(tool_name)
        tool_labels = {"select": "Pointeur (1)", "split": "Ciseaux / Scission (2)", "erase": "Gomme (3)"}
        self.statusBar().showMessage(f"Outil actif : {tool_labels.get(tool_name, tool_name)}", 2500)

    def _on_copy_clip(self):
        self.timeline_grid.copy_selected_clip()

    def _on_cut_clip(self):
        self.timeline_grid.cut_selected_clip()

    def _on_paste_clip(self):
        self.timeline_grid.paste_clip_at_playhead()

    def _on_duplicate_clip(self):
        self.timeline_grid.duplicate_selected_clip()

    def _on_delete_clip(self):
        self.timeline_grid.delete_selected_clip()

    def _on_split_playhead(self):
        self.timeline_grid.split_at_playhead()

    def _on_snap_toggled(self, enabled: bool):
        self.timeline_grid.set_snap_enabled(enabled)
        state_str = "activé" if enabled else "désactivé"
        self.statusBar().showMessage(f"Aimantage à la grille (Snap) {state_str}", 2000)

    def _toggle_snap(self):
        new_val = not self.timeline_grid.snap_enabled
        self.editing_toolbar.set_snap_enabled(new_val)

    def _on_grid_resolution_changed(self, res_beats: float):
        self.timeline_grid.set_grid_resolution(res_beats)
        if res_beats <= 0:
            self.statusBar().showMessage("Grille : Désactivée / Libre", 2000)
        else:
            self.statusBar().showMessage(f"Résolution de grille réglée à {res_beats} temps", 2000)

    def _zoom_timeline_in(self):
        new_ppb = min(240.0, self.timeline_grid.pixels_per_beat * 1.25)
        self.timeline_grid.set_zoom(new_ppb)
        self.ruler.set_zoom(new_ppb)
        self.statusBar().showMessage(f"Zoom arrangement : {int((new_ppb / 40.0) * 100)}%", 1500)

    def _zoom_timeline_out(self):
        new_ppb = max(8.0, self.timeline_grid.pixels_per_beat / 1.25)
        self.timeline_grid.set_zoom(new_ppb)
        self.ruler.set_zoom(new_ppb)
        self.statusBar().showMessage(f"Zoom arrangement : {int((new_ppb / 40.0) * 100)}%", 1500)

    def _zoom_timeline_reset(self):
        self.timeline_grid.set_zoom(40.0)
        self.ruler.set_zoom(40.0)
        self.statusBar().showMessage("Zoom arrangement : 100%", 1500)

    def _on_play_toggled(self, playing: bool):
        if playing:
            from ui.plugin_dialogs import ensure_plugin_loaded
            for track in self.project.tracks + ([self.project.master_track] if self.project.master_track else []):
                paths = [(track.plugin_path, f"track:{track.id}:instrument:{track.plugin_path}")]
                paths += [(path, f"track:{track.id}:effect:{path}") for i, path in enumerate(track.insert_effects)]
                for path, key in paths:
                    if not path or path.startswith("novadaw."):
                        continue
                    if not ensure_plugin_loaded(path, self, key):
                        self.transport_bar.set_playing_state(False)
                        return
            self.audio_engine.play()
            self.statusBar().showMessage("Lecture en cours...", 2000)
        else:
            if getattr(self.audio_engine, "is_recording", False):
                self._finish_recording()
                self.transport_bar.set_recording_state(False)
            self.audio_engine.pause()
            self.statusBar().showMessage("En pause", 2000)

    def _toggle_play(self):
        new_state = not self.audio_engine.is_playing
        self.transport_bar.set_playing_state(new_state)
        self._on_play_toggled(new_state)

    def _toggle_record(self):
        new_state = not getattr(self.audio_engine, "is_recording", False)
        self.transport_bar.set_recording_state(new_state)
        self._on_record_toggled(new_state)

    def _on_record_toggled(self, recording: bool):
        if recording:
            # 0. Règle absolue : si aucune piste n'existe, NE PAS créer de piste automatiquement.
            # L'utilisateur doit explicitement créer sa piste d'abord.
            if not self.project.tracks:
                self.transport_bar.set_recording_state(False)
                if self.isVisible():
                    QMessageBox.information(
                        self,
                        "Aucune piste",
                        "Veuillez créer une piste avant d'enregistrer (Ctrl+T ou '+ Piste')."
                    )
                self.statusBar().showMessage("⚠️ Enregistrement impossible : aucune piste. Veuillez d'abord ajouter une piste (Ctrl+T).", 4000)
                return

            # 1. Vérifier si l'utilisateur a sélectionné une piste existante
            # Si aucune piste n'est armée, ou si une seule piste était précédemment armée et que l'utilisateur
            # a sélectionné une autre piste, transférer l'armement sur la piste sélectionnée.
            sel_track = self.project.get_track(self.selected_track_id) if getattr(self, "selected_track_id", None) else None
            currently_armed = [t for t in self.project.tracks if getattr(t, "armed", False)]

            if not currently_armed:
                target_track = sel_track or self.project.tracks[0]
                target_track.armed = True
                self.selected_track_id = target_track.id
            elif sel_track and len(currently_armed) == 1 and currently_armed[0].id != sel_track.id:
                currently_armed[0].armed = False
                sel_track.armed = True

            # Synchroniser les boutons d'armement sur les en-têtes et l'inspecteur
            for i in range(self.headers_layout.count() - 1):
                item = self.headers_layout.itemAt(i)
                if item and item.widget() and isinstance(item.widget(), TrackHeaderWidget):
                    item.widget().update_arm_state(item.widget().track.armed)
                    item.widget().set_selected(item.widget().track.id == self.selected_track_id)

            if hasattr(self, "inspector") and self.inspector.current_track:
                self.inspector.btn_rec.setChecked(getattr(self.inspector.current_track, "armed", False))

            armed_tracks = [t for t in self.project.tracks if getattr(t, "armed", False)]

            # 2. Création immédiate des blocs d'enregistrement en direct sur chaque piste armée
            start_beat = self.audio_engine.current_beat
            self._active_recording_clips.clear()

            for track in armed_tracks:
                has_instrument = bool(track.plugin_path or (hasattr(self.audio_engine, "get_native_instrument") and self.audio_engine.get_native_instrument(track)))
                is_audio_target = (track.track_type == "audio") or (not has_instrument)

                if is_audio_target:
                    live_clip = AudioClip(
                        name=f"Prise Audio {len(track.clips) + 1}",
                        start_beat=start_beat,
                        length_beats=0.1,
                        color="#ef4444"
                    )
                    setattr(live_clip, "_is_recording", True)
                    track.clips.append(live_clip)
                    self._active_recording_clips[track.id] = live_clip
                else:
                    live_clip = MidiClip(
                        name=f"Prise MIDI {len(track.clips) + 1}",
                        start_beat=start_beat,
                        length_beats=0.1,
                        color="#f59e0b"
                    )
                    setattr(live_clip, "_is_recording", True)
                    track.clips.append(live_clip)
                    self._active_recording_clips[track.id] = live_clip

            self.timeline_grid.update_dimensions()
            self.timeline_grid.update()

            # 3. Démarrer l'enregistrement audio
            self.audio_engine.start_recording(start_beat)

            # 4. Lancer la lecture simultanément
            if not self.audio_engine.is_playing:
                self.audio_engine.play()
                self.transport_bar.set_playing_state(True)

            self.transport_bar.set_recording_state(True)
            self.statusBar().showMessage("🔴 Enregistrement en direct... (Espace ou [0] pour terminer)", 4000)

        else:
            self._finish_recording()
            self.transport_bar.set_recording_state(False)

    def _finish_recording(self):
        """Finalise la session d'enregistrement, sauvegarde l'audio et pérennise les blocs."""
        if not getattr(self.audio_engine, "is_recording", False):
            return

        rec_data = self.audio_engine.stop_recording()
        start_beat = rec_data.get("start_beat", 0.0)
        end_beat = self.audio_engine.current_beat
        dur_beats = max(0.25, end_beat - start_beat)
        audio_data = rec_data.get("audio")
        midi_notes = rec_data.get("midi_notes", [])

        armed_tracks = [t for t in self.project.tracks if getattr(t, "armed", False)]
        created_clips_count = 0

        # Dossier de sauvegarde des enregistrements
        rec_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "recordings"))
        os.makedirs(rec_dir, exist_ok=True)

        for track in armed_tracks:
            clip = self._active_recording_clips.get(track.id)
            has_instrument = bool(track.plugin_path or (hasattr(self.audio_engine, "get_native_instrument") and self.audio_engine.get_native_instrument(track)))
            is_audio_target = (track.track_type == "audio") or (not has_instrument) or (audio_data is not None and len(audio_data) > 0 and not midi_notes)

            if is_audio_target:
                if track.track_type != "audio" and not has_instrument:
                    track.track_type = "audio"

                if not clip or clip not in track.clips:
                    clip = AudioClip(
                        name=f"Prise Audio {len(track.clips) + 1}",
                        start_beat=start_beat,
                        length_beats=dur_beats,
                        color="#ef4444"
                    )
                    track.clips.append(clip)

                setattr(clip, "_is_recording", False)
                clip.start_beat = start_beat
                clip.length_beats = dur_beats
                clip.name = f"Prise Audio {len(track.clips)}"
                clip.color = track.color or "#10b981"

                file_path = None
                if audio_data is not None and len(audio_data) > 0:
                    timestamp = int(time.time())
                    safe_name = "".join(c for c in track.name if c.isalnum() or c in (' ', '_', '-')).strip().replace(' ', '_')
                    wav_path = os.path.join(rec_dir, f"{safe_name}_{timestamp}.wav")
                    try:
                        sf.write(wav_path, audio_data, self.audio_engine.sample_rate)
                        file_path = wav_path
                    except Exception as e:
                        print(f"[MainWindow] Erreur sauvegarde WAV enregistrement : {e}")

                clip.file_path = file_path
                clip.audio_data = audio_data
                clip.sample_rate = self.audio_engine.sample_rate
                clip._waveform_cache = None
                created_clips_count += 1

            elif track.track_type == "midi":
                if not clip or clip not in track.clips:
                    clip = MidiClip(
                        name=f"Prise MIDI {len(track.clips) + 1}",
                        start_beat=start_beat,
                        length_beats=dur_beats,
                        color="#f59e0b"
                    )
                    track.clips.append(clip)

                setattr(clip, "_is_recording", False)
                clip.start_beat = start_beat
                clip.length_beats = dur_beats
                clip.name = f"Prise MIDI {len(track.clips)}"
                if midi_notes:
                    clip.notes = list(midi_notes)
                created_clips_count += 1

        self._active_recording_clips.clear()
        self.timeline_grid.update_dimensions()
        self.timeline_grid.update()
        self._on_project_modified()

        if created_clips_count > 0:
            self.statusBar().showMessage(f"✅ Enregistrement terminé ({dur_beats:.1f} temps) : {created_clips_count} bloc(s) inséré(s) sur la timeline !", 4000)
        else:
            self.statusBar().showMessage("Enregistrement terminé.", 2000)


    def _on_stop(self):
        if getattr(self.audio_engine, "is_recording", False):
            self._finish_recording()
            self.transport_bar.set_recording_state(False)
        self.audio_engine.stop()
        self.transport_bar.set_playing_state(False)
        self.ruler.set_playhead(self.audio_engine.current_beat)
        self.timeline_grid.set_playhead(self.audio_engine.current_beat)
        self.piano_roll.set_playhead(self.audio_engine.current_beat)
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
        self.transport_bar.btn_loop.setChecked(not self.project.loop_enabled)

    def _on_loop_toggled(self, enabled: bool):
        self.project.loop_enabled = enabled
        if self.transport_bar.btn_loop.isChecked() != enabled:
            self.transport_bar.btn_loop.blockSignals(True)
            self.transport_bar.btn_loop.setChecked(enabled)
            self.transport_bar.btn_loop.blockSignals(False)
        self.ruler.set_loop(enabled, self.project.loop_start_beat, self.project.loop_end_beat)
        self.timeline_grid.update()
        state_str = "activée" if enabled else "désactivée"
        self.statusBar().showMessage(f"Lecture en boucle {state_str} (L)", 2000)

    def _on_loop_region_changed(self, start_b: float, end_b: float):
        self.project.loop_start_beat = start_b
        self.project.loop_end_beat = end_b
        self.project.loop_enabled = True
        self.transport_bar.btn_loop.blockSignals(True)
        self.transport_bar.btn_loop.setChecked(True)
        self.transport_bar.btn_loop.blockSignals(False)
        self.ruler.set_loop(True, start_b, end_b)
        self.timeline_grid.update()
        self.statusBar().showMessage("Boucle définie et activée", 2000)

    def _on_seek(self, beat: float):
        self.audio_engine.seek_beat(beat)
        self.ruler.set_playhead(beat)
        self.timeline_grid.set_playhead(beat)
        self.piano_roll.set_playhead(beat)
        self.transport_bar.update_position(beat, self.project.bpm)

    def _on_bpm_changed(self, bpm: float):
        self.project.bpm = bpm

    def _on_master_vol_changed(self, vol: float):
        self.audio_engine.master_volume = vol

    def _on_project_modified(self):
        self.timeline_grid.update()
        if hasattr(self, "piano_roll") and getattr(self, "selected_track_id", None):
            track = self.project.get_track(self.selected_track_id)
            if track:
                self.piano_roll.set_active_track(track)

        # Synchroniser l'Inspecteur avec la piste active courante
        if hasattr(self, "inspector") and self.inspector.current_track:
            self.inspector.sync_controls_from_track()

        # Synchroniser les en-têtes de pistes de l'arrangeur
        if hasattr(self, "headers_layout"):
            for i in range(self.headers_layout.count() - 1):
                item = self.headers_layout.itemAt(i)
                if item and item.widget() and isinstance(item.widget(), TrackHeaderWidget):
                    item.widget().sync_controls_from_track()

    def _update_playback_ui(self):
        if self.audio_engine.plugin_errors:
            errors = list(self.audio_engine.plugin_errors.values())
            self.audio_engine.plugin_errors.clear()
            self.statusBar().showMessage("Plugin indisponible — " + errors[-1], 10000)

        # Mise à jour du VU-mètre Master et de l'indicateur de distorsion
        if hasattr(self, "transport_bar") and hasattr(self.audio_engine, "get_master_peaks"):
            pk_l, pk_r, clipped = self.audio_engine.get_master_peaks()
            self.transport_bar.update_master_meter(pk_l, pk_r, clipped)

        if self.audio_engine.is_playing:
            beat = self.audio_engine.current_beat
            self.ruler.set_playhead(beat)
            self.timeline_grid.set_playhead(beat)
            self.piano_roll.set_playhead(beat)
            self.transport_bar.update_position(beat, self.project.bpm)

            # Si enregistrement actif, mise à jour des blocs et forme d'onde en temps réel
            if getattr(self.audio_engine, "is_recording", False) and getattr(self, "_active_recording_clips", None):
                live_audio = self.audio_engine.get_live_recording_audio()
                live_notes = self.audio_engine.get_live_recording_notes()
                for track_id, clip in list(self._active_recording_clips.items()):
                    clip.length_beats = max(0.1, beat - clip.start_beat)
                    if isinstance(clip, AudioClip) and live_audio is not None and len(live_audio) > 0:
                        clip.audio_data = live_audio
                        clip._waveform_cache = None
                    elif isinstance(clip, MidiClip) and live_notes:
                        clip.notes = list(live_notes)

                self.timeline_grid.update_dimensions()
                self.timeline_grid.update()


    # --- Actions Fichier ---

    def new_project(self):
        global_plugin_manager.close_all_editors()
        self.audio_engine.stop()
        self.project = Project.create_empty()
        self.audio_engine.set_project(self.project)
        self.selected_track_id = None
        self._on_seek(0.0)
        self.refresh_project_ui()
        self.statusBar().showMessage("Nouveau projet vide créé.", 3000)

    def load_demo_project(self):
        global_plugin_manager.close_all_editors()
        self.audio_engine.stop()
        self.project = Project.create_demo()
        self.audio_engine.set_project(self.project)
        self.selected_track_id = None
        self._on_seek(0.0)
        self.refresh_project_ui()
        self.statusBar().showMessage("Projet de démonstration chargé.", 3000)

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

    # --- Actions Menu Plugins ---

    def open_plugin_manager_dialog(self):
        dlg = PluginManagerDialog(self)
        dlg.exec()

    def open_plugin_manager_and_scan(self):
        dlg = PluginManagerDialog(self)
        dlg.show()
        dlg.start_scan()
        dlg.exec()

    def open_plugin_folders_dialog(self):
        dlg = PluginFolderManagerDialog(self)
        dlg.folders_changed.connect(self.open_plugin_manager_and_scan)
        dlg.exec()

    def add_plugin_file_dialog(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Ajouter un plugin VST3",
            "",
            "Plugins VST3 (*.vst3);;Tous les fichiers (*.*)"
        )
        if file_path:
            info = analyze_plugin_file(file_path, self)
            status = "compatible et prêt à l'emploi ✅" if info.is_compatible else f"incompatible ❌ ({info.error_message})"
            QMessageBox.information(
                self,
                "Plugin VST3",
                f"Plugin '{info.name}' analysé :\n{status}"
            )
            self.refresh_project_ui()

    def import_audio_dialog(self, target_track_id: Optional[str] = None):
        """Ouvre l'explorateur Windows pour importer n'importe quel type de fichier audio."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Importer un fichier audio dans NovaDAW (WAV, MP3, FLAC, OGG, AIFF, M4A...)",
            "",
            QT_FILE_DIALOG_FILTER
        )
        if not file_path:
            return

        try:
            from core.actions.project import import_audio_file_action
            res = import_audio_file_action(
                self,
                file_path=file_path,
                track_id_or_name=target_track_id or getattr(self, "selected_track_id", None),
                start_beat=self.audio_engine.current_beat
            )
            self.statusBar().showMessage(
                f"Fichier audio importé avec succès : {res['clip_name']} ({res['duration_seconds']}s sur {res['track_name']})",
                4000
            )
        except Exception as e:
            QMessageBox.warning(self, "Erreur d'importation", f"Impossible d'importer le fichier audio :\n{e}")

    def open_device_settings_dialog(self, initial_tab: int = 0):
        """Ouvre la boîte de dialogue de configuration des périphériques Audio et GPU."""
        dlg = DeviceSettingsDialog(self.audio_engine, self)
        dlg.tabs.setCurrentIndex(initial_tab)
        dlg.settings_applied.connect(self._on_hardware_settings_applied)
        dlg.exec()

    def open_mcp_settings_dialog(self):
        """Ouvre directement l'onglet MCP / IA du panneau des périphériques."""
        self.open_device_settings_dialog(initial_tab=2)

    def _on_hardware_settings_applied(self):
        lat_ms = hardware_manager.calculate_latency_ms(
            self.audio_engine.block_size,
            self.audio_engine.sample_rate
        )
        self.lbl_engine_info.setText(
            f"⚙️ Moteur Audio : {self.audio_engine.sample_rate} Hz Stéréo | Tampon: {self.audio_engine.block_size} ({lat_ms} ms)"
        )
        self.statusBar().showMessage("Configuration des périphériques mise à jour avec succès.", 3000)

    def closeEvent(self, event):
        global_plugin_manager.close_all_editors()
        if hasattr(self, "ipc_server"):
            self.ipc_server.stop()
        self.audio_engine.close()
        global_plugin_manager.release_instances()
        super().closeEvent(event)
