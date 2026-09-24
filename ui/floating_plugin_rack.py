"""
ui/floating_plugin_rack.py - Fenêtre Flottante du Rack de Plugins (Style Cubase Pro F11)
Fenêtre dédiée et autonome pour visualiser et gérer la pile (stack) d'instruments
et d'effets du projet, avec bouton d'épinglage au premier plan (Stay on Top).
"""
from typing import Optional
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QCheckBox, QFrame, QSizePolicy
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon

from core.project import Project
from ui.vst_rack import VstRackWidget


class FloatingPluginRackDialog(QDialog):
    """
    Fenêtre Flottante dédiée aux plugins du projet (Style Cubase F11 VST Instruments & Effect Rack).
    Permet de maintenir la stack d'effets/instruments ouverte au-dessus de l'arrangement.
    """
    rack_changed = Signal()

    def __init__(self, project: Project, parent=None):
        super().__init__(parent)
        self.project = project
        self.setObjectName("floating_plugin_rack_dialog")

        # Fenêtre autonome avec contrôle complet
        self.setWindowTitle("🎛️ Rack de Plugins du Projet (VST Instruments & Effets) — F11")
        self.resize(760, 520)
        self.setMinimumSize(580, 360)
        self.setSizeGripEnabled(True)

        # Style sombre professionnel type Cubase Pro
        self.setStyleSheet("""
            QDialog#floating_plugin_rack_dialog {
                background-color: #121319;
                color: #e2e8f0;
            }
            QFrame#header_bar {
                background-color: #181a24;
                border-bottom: 1px solid #282a3c;
                padding: 6px 12px;
            }
            QLabel#dialog_title {
                font-size: 13px;
                font-weight: bold;
                color: #38bdf8;
                letter-spacing: 0.5px;
            }
            QCheckBox {
                color: #94a3b8;
                font-size: 11px;
            }
            QCheckBox::indicator:checked {
                background-color: #38bdf8;
                border: 1px solid #0284c7;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Barre supérieure d'options de la fenêtre flottante
        header_bar = QFrame()
        header_bar.setObjectName("header_bar")
        h_layout = QHBoxLayout(header_bar)
        h_layout.setContentsMargins(14, 8, 14, 8)
        h_layout.setSpacing(12)

        lbl_icon = QLabel("🎛️")
        lbl_icon.setStyleSheet("font-size: 16px;")
        h_layout.addWidget(lbl_icon)

        lbl_title = QLabel("RACK DE PLUGINS DU PROJET — STYLE CUBASE (F11)")
        lbl_title.setObjectName("dialog_title")
        h_layout.addWidget(lbl_title)

        h_layout.addStretch()

        # Option Épingler au premier plan (Stay on Top)
        self.chk_stay_on_top = QCheckBox("📌 Toujours au premier plan")
        self.chk_stay_on_top.setChecked(True)
        self.chk_stay_on_top.toggled.connect(self._toggle_stay_on_top)
        h_layout.addWidget(self.chk_stay_on_top)

        # Bouton fermer la fenêtre flottante
        btn_close_top = QPushButton("✕")
        btn_close_top.setFixedSize(22, 22)
        btn_close_top.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                color: #94a3b8;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                color: #ffffff;
                background-color: #ef4444;
                border-radius: 3px;
            }
        """)
        btn_close_top.clicked.connect(self.close)
        h_layout.addWidget(btn_close_top)

        layout.addWidget(header_bar)

        # Widget central de rack VST
        self.rack_widget = VstRackWidget(self.project, self)
        self.rack_widget.rack_changed.connect(self.rack_changed.emit)
        layout.addWidget(self.rack_widget, stretch=1)

        # Appliquer Stay on Top initial
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)

    def set_project(self, project: Project):
        self.project = project
        self.rack_widget.set_project(project)

    def refresh_rack(self):
        self.rack_widget.refresh_rack()

    def _toggle_stay_on_top(self, checked: bool):
        was_visible = self.isVisible()
        self.setWindowFlag(Qt.WindowStaysOnTopHint, checked)
        if was_visible:
            self.show()
            self.raise_()
            self.activateWindow()

    def show_and_focus(self):
        """Affiche la fenêtre flottante et la place au premier plan"""
        self.show()
        self.raise_()
        self.activateWindow()
