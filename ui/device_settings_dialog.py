"""
ui/device_settings_dialog.py - Boîte de dialogue de configuration des périphériques Audio et Graphiques (GPU)
pour NovaDAW.
"""
import os
from typing import Optional
from PySide6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QSpinBox, QCheckBox, QTabWidget, QGroupBox, QFormLayout,
    QMessageBox, QFrame, QScrollArea, QSizePolicy
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon, QFont, QColor

from core.hardware_manager import hardware_manager
from core.audio_engine import AudioEngine


class DeviceSettingsDialog(QDialog):
    """Boîte de dialogue de configuration des cartes son, pilotes audio, GPU et intégration MCP."""

    settings_applied = Signal()

    def __init__(self, audio_engine: AudioEngine, parent=None):
        super().__init__(parent)
        self.audio_engine = audio_engine
        self.setWindowTitle("Configuration Périphériques Audio & Graphiques - NovaDAW")
        self.resize(780, 640)
        self.setMinimumSize(700, 560)

        icon_path = os.path.join(os.path.dirname(__file__), "..", "assets", "nova_icon.png")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        self.setStyleSheet("""
            QDialog {
                background-color: #11131b;
                color: #e2e8f0;
            }
            QTabWidget::pane {
                border: 1px solid #232738;
                background-color: #151722;
                border-radius: 6px;
            }
            QTabBar::tab {
                background: #191c28;
                border: 1px solid #232738;
                border-bottom: none;
                padding: 8px 18px;
                margin-right: 3px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                color: #94a3b8;
                font-weight: bold;
            }
            QTabBar::tab:selected {
                background: #151722;
                color: #38bdf8;
                border-bottom: 2px solid #38bdf8;
            }
            QGroupBox {
                background-color: #161824;
                border: 1px solid #262b3c;
                border-radius: 6px;
                margin-top: 18px;
                font-weight: bold;
                color: #38bdf8;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 10px;
                top: 2px;
                padding: 1px 8px;
                background-color: #1e2233;
                border-radius: 4px;
                color: #7dd3fc;
                font-size: 11px;
            }
            QLabel {
                color: #cbd5e1;
            }
            QComboBox {
                background-color: #1a1d29;
                border: 1px solid #31374a;
                border-radius: 4px;
                padding: 4px 10px;
                min-height: 26px;
                color: #f8fafc;
                font-size: 11px;
            }
            QComboBox:hover {
                border-color: #38bdf8;
            }
            QComboBox::drop-down {
                border: none;
                width: 24px;
            }
            QComboBox QAbstractItemView {
                background-color: #151822;
                border: 1px solid #31374a;
                selection-background-color: #0284c7;
                selection-color: white;
                color: #f1f5f9;
                padding: 4px;
            }
        """)

        self._init_ui()
        self._load_current_values()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(14)

        # En-tête stylisé avec logo
        header_frame = QFrame()
        header_frame.setStyleSheet("background: #141722; border: 1px solid #232738; border-radius: 8px; padding: 6px;")
        header_layout = QHBoxLayout(header_frame)
        header_layout.setContentsMargins(10, 8, 10, 8)

        lbl_logo = QLabel()
        icon_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "assets", "nova_icon.png"))
        if os.path.exists(icon_path):
            from PySide6.QtGui import QPixmap
            lbl_logo.setPixmap(QPixmap(icon_path).scaled(32, 32, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            lbl_logo.setText("🌌")
            lbl_logo.setStyleSheet("font-size: 24px;")
        header_layout.addWidget(lbl_logo)

        v_head = QVBoxLayout()
        v_head.setSpacing(2)
        lbl_title = QLabel("Gestionnaire de Périphériques & Matériel NovaDAW")
        lbl_title.setStyleSheet("font-size: 14px; font-weight: bold; color: #38bdf8;")
        lbl_subtitle = QLabel("Configurez votre carte audio, votre pilote ASIO/WASAPI, votre carte graphique et l'orchestration IA.")
        lbl_subtitle.setStyleSheet("font-size: 11px; color: #94a3b8;")
        v_head.addWidget(lbl_title)
        v_head.addWidget(lbl_subtitle)
        header_layout.addLayout(v_head)
        header_layout.addStretch()

        main_layout.addWidget(header_frame)

        # Onglets
        self.tabs = QTabWidget()

        # Tab 1 : Audio
        self.tab_audio = QWidget()
        self._setup_audio_tab()
        self.tabs.addTab(self.tab_audio, "🎚️ Carte Audio & Pilote")

        # Tab 2 : Graphique / GPU
        self.tab_gpu = QWidget()
        self._setup_gpu_tab()
        self.tabs.addTab(self.tab_gpu, "🖥️ Carte Graphique & Affichage")

        # Tab 3 : IA / MCP
        self.tab_mcp = QWidget()
        self._setup_mcp_tab()
        self.tabs.addTab(self.tab_mcp, "🤖 Contrôle IA & Protocole MCP")

        main_layout.addWidget(self.tabs)

        # Barre d'actions en bas
        bottom_layout = QHBoxLayout()
        bottom_layout.setSpacing(10)

        self.btn_test_sound = QPushButton("▶ Tester la Sortie Audio")
        self.btn_test_sound.setStyleSheet("background-color: #0284c7; color: white; font-weight: bold; padding: 6px 14px;")
        self.btn_test_sound.clicked.connect(self._on_test_sound)
        bottom_layout.addWidget(self.btn_test_sound)

        bottom_layout.addStretch()

        self.btn_cancel = QPushButton("Annuler")
        self.btn_cancel.clicked.connect(self.reject)
        bottom_layout.addWidget(self.btn_cancel)

        self.btn_apply = QPushButton("Appliquer & Sauvegarder")
        self.btn_apply.setStyleSheet("background-color: #10b981; color: #022c22; font-weight: bold; padding: 6px 16px;")
        self.btn_apply.clicked.connect(self._on_apply_and_save)
        bottom_layout.addWidget(self.btn_apply)

        main_layout.addLayout(bottom_layout)

    def _setup_audio_tab(self):
        layout = QVBoxLayout(self.tab_audio)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(14)

        grp_driver = QGroupBox("Pilote Audio & Système Hôte")
        grp_driver_l = QFormLayout(grp_driver)
        grp_driver_l.setContentsMargins(14, 22, 14, 14)
        grp_driver_l.setVerticalSpacing(12)

        self.combo_host_api = QComboBox()
        self.combo_host_api.currentIndexChanged.connect(self._on_host_api_changed)
        grp_driver_l.addRow("Interface / Système Hôte :", self.combo_host_api)

        layout.addWidget(grp_driver)

        grp_devices = QGroupBox("Routage des Périphériques")
        grp_devices_l = QFormLayout(grp_devices)
        grp_devices_l.setContentsMargins(14, 22, 14, 14)
        grp_devices_l.setVerticalSpacing(12)

        self.combo_output = QComboBox()
        self.combo_output.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        grp_devices_l.addRow("Périphérique de Sortie (Haut-parleurs / Casque) :", self.combo_output)

        self.combo_input = QComboBox()
        self.combo_input.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        grp_devices_l.addRow("Périphérique d'Entrée (Microphone / Ligne) :", self.combo_input)

        layout.addWidget(grp_devices)

        grp_dsp = QGroupBox("Performance & Latence du Signal")
        grp_dsp_l = QFormLayout(grp_dsp)
        grp_dsp_l.setContentsMargins(14, 22, 14, 14)
        grp_dsp_l.setVerticalSpacing(12)

        self.combo_sample_rate = QComboBox()
        for sr in hardware_manager.get_supported_sample_rates():
            self.combo_sample_rate.addItem(f"{sr} Hz" if sr < 100000 else f"{sr // 1000} kHz", sr)
        self.combo_sample_rate.currentIndexChanged.connect(self._update_latency_label)
        grp_dsp_l.addRow("Fréquence d'Échantillonnage :", self.combo_sample_rate)

        self.combo_buffer_size = QComboBox()
        for buf in hardware_manager.get_supported_buffer_sizes():
            self.combo_buffer_size.addItem(f"{buf} échantillons", buf)
        self.combo_buffer_size.currentIndexChanged.connect(self._update_latency_label)
        grp_dsp_l.addRow("Taille du Tampon (Buffer Size) :", self.combo_buffer_size)

        self.lbl_latency = QLabel("Latence calculée : -- ms")
        self.lbl_latency.setStyleSheet("color: #38bdf8; font-weight: bold; font-family: Consolas;")
        grp_dsp_l.addRow("Latence Estimée :", self.lbl_latency)

        layout.addWidget(grp_dsp)
        layout.addStretch()

    def _setup_gpu_tab(self):
        layout = QVBoxLayout(self.tab_gpu)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(14)

        grp_gpu = QGroupBox("Cartes Graphiques Détectées")
        grp_gpu_l = QVBoxLayout(grp_gpu)
        grp_gpu_l.setContentsMargins(14, 22, 14, 14)
        grp_gpu_l.setSpacing(10)

        self.combo_gpu = QComboBox()
        self.combo_gpu.currentIndexChanged.connect(self._on_gpu_selected)
        grp_gpu_l.addWidget(self.combo_gpu)

        # Fiche d'informations GPU en direct
        self.frame_gpu_info = QFrame()
        self.frame_gpu_info.setObjectName("frame_gpu_info")
        self.frame_gpu_info.setMinimumHeight(130)
        self.frame_gpu_info.setStyleSheet("""
            QFrame#frame_gpu_info {
                background-color: #0f121c;
                border: 1px solid #232838;
                border-radius: 6px;
            }
            QFrame#frame_gpu_info QLabel {
                background: transparent;
                border: none;
                font-size: 11px;
            }
        """)
        f_info_l = QFormLayout(self.frame_gpu_info)
        f_info_l.setContentsMargins(14, 12, 14, 12)
        f_info_l.setVerticalSpacing(8)

        self.lbl_gpu_name = QLabel("-")
        self.lbl_gpu_name.setStyleSheet("color: #f8fafc; font-weight: bold;")
        f_info_l.addRow("Nom du Processeur :", self.lbl_gpu_name)

        self.lbl_gpu_vram = QLabel("-")
        self.lbl_gpu_vram.setStyleSheet("color: #10b981; font-weight: bold;")
        f_info_l.addRow("Mémoire VRAM Dédiée :", self.lbl_gpu_vram)

        self.lbl_gpu_driver = QLabel("-")
        self.lbl_gpu_driver.setStyleSheet("color: #94a3b8; font-family: Consolas;")
        f_info_l.addRow("Version du Pilote :", self.lbl_gpu_driver)

        self.lbl_gpu_status = QLabel("Opérationnel (OK)")
        self.lbl_gpu_status.setStyleSheet("color: #38bdf8;")
        f_info_l.addRow("Statut Matériel :", self.lbl_gpu_status)

        grp_gpu_l.addWidget(self.frame_gpu_info)
        layout.addWidget(grp_gpu)

        grp_render = QGroupBox("Moteur de Rendu Graphique & Fluidité")
        grp_render_l = QFormLayout(grp_render)
        grp_render_l.setContentsMargins(14, 22, 14, 14)
        grp_render_l.setVerticalSpacing(12)

        self.combo_render_backend = QComboBox()
        for b in hardware_manager.get_graphics_backends():
            self.combo_render_backend.addItem(b)
        grp_render_l.addRow("Moteur Graphique UI :", self.combo_render_backend)

        self.combo_fps = QComboBox()
        self.combo_fps.addItem("60 FPS (Équilibré)", 60)
        self.combo_fps.addItem("120 FPS (Très Fluide)", 120)
        self.combo_fps.addItem("144 FPS (Écrans Gaming)", 144)
        self.combo_fps.addItem("Illimité / Synchronisé VSync", 0)
        grp_render_l.addRow("Fréquence d'Affichage :", self.combo_fps)

        self.chk_hardware_accel = QCheckBox("Activer l'accélération matérielle GPU pour les visualisations (Spectrogrammes, EQ, VU-mètres)")
        self.chk_hardware_accel.setChecked(True)
        grp_render_l.addRow(self.chk_hardware_accel)

        layout.addWidget(grp_render)
        layout.addStretch()

    def _setup_mcp_tab(self):
        layout = QVBoxLayout(self.tab_mcp)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(14)

        grp_mcp = QGroupBox("Serveur MCP & Orchestration par Agent IA")
        grp_mcp_l = QVBoxLayout(grp_mcp)
        grp_mcp_l.setContentsMargins(14, 22, 14, 14)
        grp_mcp_l.setSpacing(10)

        lbl_desc = QLabel(
            "NovaDAW intègre un pont TCP IPC et un serveur officiel <b>Model Context Protocol (FastMCP)</b>.<br>"
            "Votre agent de codage (<b>Google Antigravity</b>, Claude Desktop, Cursor, Codex) peut manipuler directement "
            "le son, ajuster les égaliseurs, compresseurs, mixeurs, créer des pistes et importer des fichiers audio pour vous !"
        )
        lbl_desc.setWordWrap(True)
        lbl_desc.setStyleSheet("color: #cbd5e1; font-size: 11px;")
        grp_mcp_l.addWidget(lbl_desc)

        info_box = QFrame()
        info_box.setObjectName("info_box")
        info_box.setMinimumHeight(110)
        info_box.setStyleSheet("""
            QFrame#info_box {
                background-color: #0d1117;
                border: 1px solid #30363d;
                border-radius: 6px;
            }
            QFrame#info_box QLabel {
                background: transparent;
                border: none;
                font-size: 11px;
            }
        """)
        info_box_l = QFormLayout(info_box)
        info_box_l.setContentsMargins(14, 12, 14, 12)
        info_box_l.setVerticalSpacing(8)

        lbl_ipc_status = QLabel("🟢 En ligne (Serveur TCP actif)")
        lbl_ipc_status.setStyleSheet("color: #10b981; font-weight: bold;")
        info_box_l.addRow("Statut IPC Temps Réel :", lbl_ipc_status)

        lbl_ipc_host = QLabel("127.0.0.1 (Localhost)")
        lbl_ipc_host.setStyleSheet("color: #f1f5f9; font-family: Consolas;")
        info_box_l.addRow("Adresse d'écoute :", lbl_ipc_host)

        lbl_mcp_tools = QLabel("31 Outils d'automatisation IA (Mixeur, EQ, Compresseur, Timeline, Transport, Import)")
        lbl_mcp_tools.setStyleSheet("color: #38bdf8; font-weight: bold;")
        info_box_l.addRow("Outils Disponibles :", lbl_mcp_tools)

        grp_mcp_l.addWidget(info_box)
        layout.addWidget(grp_mcp)

        grp_guide = QGroupBox("Configuration Rapide dans Antigravity / Claude Desktop")
        grp_guide_l = QVBoxLayout(grp_guide)
        grp_guide_l.setContentsMargins(14, 22, 14, 14)
        grp_guide_l.setSpacing(10)
        guide_text = (
            "Pour connecter Antigravity ou un agent externe, ajoutez simplement le serveur à votre configuration MCP :<br>"
            "<pre style='background: #161b22; color: #79c0ff; padding: 6px; border-radius: 4px;'>"
            "\"novadaw\": {\n"
            "  \"command\": \"python\",\n"
            "  \"args\": [\"c:/Users/.../music software/core/mcp/server.py\"]\n"
            "}"
            "</pre>"
        )
        lbl_code = QLabel(guide_text)
        lbl_code.setTextFormat(Qt.RichText)
        lbl_code.setWordWrap(True)
        grp_guide_l.addWidget(lbl_code)

        layout.addWidget(grp_guide)
        layout.addStretch()

    def _load_current_values(self):
        # 1. Host APIs
        apis = hardware_manager.get_audio_host_apis()
        self.combo_host_api.clear()
        selected_api_idx = 0

        target_api_name = hardware_manager.settings["audio"].get("host_api_name", "Windows WASAPI")
        for i, api in enumerate(apis):
            self.combo_host_api.addItem(api["name"], api["index"])
            if target_api_name.lower() in api["name"].lower():
                selected_api_idx = i

        if self.combo_host_api.count() > 0:
            self.combo_host_api.setCurrentIndex(selected_api_idx)

        # 2. Sample rate & Buffer size
        cur_sr = getattr(self.audio_engine, "sample_rate", 44100)
        idx_sr = self.combo_sample_rate.findData(cur_sr)
        if idx_sr >= 0:
            self.combo_sample_rate.setCurrentIndex(idx_sr)

        cur_buf = getattr(self.audio_engine, "block_size", 512)
        idx_buf = self.combo_buffer_size.findData(cur_buf)
        if idx_buf >= 0:
            self.combo_buffer_size.setCurrentIndex(idx_buf)

        self._update_latency_label()

        # 3. GPU List
        gpus = hardware_manager.get_gpu_devices()
        self.combo_gpu.clear()
        self.gpus_data = gpus
        sel_gpu_name = hardware_manager.settings["graphics"].get("selected_gpu")
        selected_gpu_idx = 0

        for i, g in enumerate(gpus):
            vram_txt = f" ({g['vram_mb']} Mo VRAM)" if g.get("vram_mb") else ""
            self.combo_gpu.addItem(f"{g['name']}{vram_txt}", g)
            if sel_gpu_name and sel_gpu_name.lower() in g["name"].lower():
                selected_gpu_idx = i
            elif g.get("is_primary") and not sel_gpu_name:
                selected_gpu_idx = i

        if self.combo_gpu.count() > 0:
            self.combo_gpu.setCurrentIndex(selected_gpu_idx)
            self._on_gpu_selected(selected_gpu_idx)

        # Backend
        cur_backend = hardware_manager.settings["graphics"].get("rendering_backend")
        if cur_backend:
            idx_b = self.combo_render_backend.findText(cur_backend, Qt.MatchContains)
            if idx_b >= 0:
                self.combo_render_backend.setCurrentIndex(idx_b)

    def _on_host_api_changed(self, index: int):
        host_api_idx = self.combo_host_api.currentData()
        devices = hardware_manager.get_audio_devices(host_api_idx)

        self.combo_output.clear()
        self.combo_output.addItem("Périphérique de sortie par défaut du système", -1)
        cur_out_dev = getattr(self.audio_engine, "output_device", None)
        sel_out_idx = 0

        for idx, d in enumerate(devices["outputs"]):
            self.combo_output.addItem(f"{d['name']} ({d['max_outputs']} canaux)", d["index"])
            if cur_out_dev is not None and d["index"] == cur_out_dev:
                sel_out_idx = idx + 1

        self.combo_output.setCurrentIndex(sel_out_idx)

        self.combo_input.clear()
        self.combo_input.addItem("Périphérique d'entrée par défaut", -1)
        cur_in_dev = getattr(self.audio_engine, "input_device", None)
        sel_in_idx = 0

        for idx, d in enumerate(devices["inputs"]):
            self.combo_input.addItem(f"{d['name']} ({d['max_inputs']} canaux)", d["index"])
            if cur_in_dev is not None and d["index"] == cur_in_dev:
                sel_in_idx = idx + 1

        self.combo_input.setCurrentIndex(sel_in_idx)

    def _update_latency_label(self):
        sr = self.combo_sample_rate.currentData() or 44100
        buf = self.combo_buffer_size.currentData() or 512
        lat = hardware_manager.calculate_latency_ms(buf, sr)
        self.lbl_latency.setText(f"Latence théorique du buffer : {lat:.2f} ms @ {sr} Hz")

    def _on_gpu_selected(self, index: int):
        if not hasattr(self, "gpus_data") or index < 0 or index >= len(self.gpus_data):
            return
        g = self.gpus_data[index]
        self.lbl_gpu_name.setText(g.get("name", "Inconnu"))
        vram_mb = g.get("vram_mb", 0)
        if vram_mb > 0:
            self.lbl_gpu_vram.setText(f"{vram_mb} Mo ({g.get('vram_gb', 0)} Go) GDDR")
        else:
            self.lbl_gpu_vram.setText("Partagée / Mémoire Système")
        self.lbl_gpu_driver.setText(g.get("driver_version", "Standard"))
        self.lbl_gpu_status.setText(f"{g.get('status', 'OK')} (Prêt pour accélération Direct3D/OpenGL)")

    def _on_test_sound(self):
        """Joue le signal sonore de test pour vérifier la sortie audio active."""
        if hasattr(self.audio_engine, "play_test_tone"):
            self.audio_engine.play_test_tone(freq=440.0, duration_sec=0.6)

    def _on_apply_and_save(self):
        # 1. Récupération des réglages audio
        host_api_name = self.combo_host_api.currentText()
        out_dev_idx = self.combo_output.currentData()
        in_dev_idx = self.combo_input.currentData()
        sr = self.combo_sample_rate.currentData()
        buf = self.combo_buffer_size.currentData()

        # Application au moteur audio
        self.audio_engine.configure_device(
            output_device=out_dev_idx if out_dev_idx >= 0 else None,
            input_device=in_dev_idx if in_dev_idx >= 0 else None,
            sample_rate=sr,
            block_size=buf
        )

        # 2. Sauvegarde dans HardwareManager
        hardware_manager.settings["audio"]["host_api_name"] = host_api_name
        hardware_manager.settings["audio"]["output_device_index"] = out_dev_idx if out_dev_idx >= 0 else None
        hardware_manager.settings["audio"]["output_device_name"] = self.combo_output.currentText()
        hardware_manager.settings["audio"]["input_device_index"] = in_dev_idx if in_dev_idx >= 0 else None
        hardware_manager.settings["audio"]["input_device_name"] = self.combo_input.currentText()
        hardware_manager.settings["audio"]["sample_rate"] = sr
        hardware_manager.settings["audio"]["buffer_size"] = buf

        # 3. Réglages graphiques
        gpu_data = self.combo_gpu.currentData()
        if gpu_data:
            hardware_manager.settings["graphics"]["selected_gpu"] = gpu_data.get("name")
        hardware_manager.settings["graphics"]["rendering_backend"] = self.combo_render_backend.currentText()
        hardware_manager.settings["graphics"]["fps_limit"] = self.combo_fps.currentData()
        hardware_manager.settings["graphics"]["high_dpi_enabled"] = self.chk_hardware_accel.isChecked()

        hardware_manager.save_settings()
        self.settings_applied.emit()
        self.accept()
