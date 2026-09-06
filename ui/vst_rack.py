"""
ui/vst_rack.py - Panneau du Rack VST Global du Projet (Style Cubase F11)
Permet d'ajouter, visualiser et manipuler une stack de plugins VST3
chargés globalement dans le projet indépendamment des pistes.
"""
from typing import Optional
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QScrollArea, QMenu, QFileDialog, QMessageBox
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor

from core.project import Project
from core.plugin_manager import global_plugin_manager
from ui.plugin_dialogs import open_plugin_editor_gui


class VstRackWidget(QWidget):
    """
    Rack VST global du projet (style Cubase F11 VST Instruments Rack).
    Permet de maintenir une stack de plugins chargés dans le projet.
    """
    rack_changed = Signal()

    def __init__(self, project: Project, parent=None):
        super().__init__(parent)
        self.project = project
        self.setStyleSheet("""
            QWidget {
                background-color: #14151c;
                color: #e2e8f0;
            }
            QLabel {
                font-size: 12px;
            }
            QPushButton {
                background-color: #242838;
                color: #f1f5f9;
                border: 1px solid #383d52;
                border-radius: 4px;
                padding: 6px 12px;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #31364c;
                border-color: #38bdf8;
            }
            QPushButton#btn_add {
                background-color: #0284c7;
                color: #ffffff;
                font-weight: bold;
                border: none;
            }
            QPushButton#btn_add:hover {
                background-color: #0369a1;
            }
            QFrame#rack_slot {
                background-color: #1b1d28;
                border: 1px solid #282a3c;
                border-radius: 6px;
            }
            QFrame#rack_slot:hover {
                border-color: #38bdf8;
            }
        """)

        self._init_ui()
        self.refresh_rack()
        global_plugin_manager.scan_updated.connect(self.refresh_rack)

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 10, 12, 10)
        main_layout.setSpacing(10)

        # Barre d'actions supérieure
        top_bar = QHBoxLayout()
        lbl_title = QLabel("🎛️ RACK VST DU PROJET (Stack Instruments & Effets)")
        lbl_title.setStyleSheet("font-weight: bold; font-size: 13px; color: #38bdf8;")
        top_bar.addWidget(lbl_title)
        top_bar.addStretch()

        self.btn_add_inst = QPushButton("+ Charger un Instrument...")
        self.btn_add_inst.setObjectName("btn_add")
        self.btn_add_inst.clicked.connect(self._show_add_instrument_menu)
        top_bar.addWidget(self.btn_add_inst)

        self.btn_add_fx = QPushButton("+ Charger un Effet...")
        self.btn_add_fx.clicked.connect(self._show_add_effect_menu)
        top_bar.addWidget(self.btn_add_fx)

        self.btn_browse = QPushButton("📁 Parcourir .vst3...")
        self.btn_browse.clicked.connect(self._browse_vst_file)
        top_bar.addWidget(self.btn_browse)

        main_layout.addLayout(top_bar)

        # Zone scrollable pour la stack
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("border: none; background: transparent;")

        self.stack_container = QWidget()
        self.stack_layout = QVBoxLayout(self.stack_container)
        self.stack_layout.setContentsMargins(0, 4, 0, 4)
        self.stack_layout.setSpacing(6)
        self.stack_layout.addStretch()

        scroll.setWidget(self.stack_container)
        main_layout.addWidget(scroll)

    def set_project(self, project: Project):
        self.project = project
        self.refresh_rack()

    def refresh_rack(self):
        # Nettoyer les slots existants
        while self.stack_layout.count() > 1:
            item = self.stack_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not hasattr(self.project, "plugin_rack") or not self.project.plugin_rack:
            empty_frame = QFrame()
            ef_layout = QVBoxLayout(empty_frame)
            ef_layout.setContentsMargins(20, 30, 20, 30)
            ef_layout.setAlignment(Qt.AlignCenter)

            lbl_empty = QLabel("Aucun plugin chargé dans le Rack du projet pour le moment.")
            lbl_empty.setStyleSheet("color: #64748b; font-size: 13px; font-style: italic;")
            ef_layout.addWidget(lbl_empty)

            btn_quick_add = QPushButton("+ Charger Kontakt, SampleTank ou un autre VST")
            btn_quick_add.setObjectName("btn_add")
            btn_quick_add.setFixedWidth(300)
            btn_quick_add.clicked.connect(self._show_add_instrument_menu)
            ef_layout.addWidget(btn_quick_add, alignment=Qt.AlignCenter)

            self.stack_layout.insertWidget(0, empty_frame)
            return

        for idx, rack_item in enumerate(self.project.plugin_rack, 1):
            slot_widget = self._create_slot_card(idx, rack_item)
            self.stack_layout.insertWidget(self.stack_layout.count() - 1, slot_widget)

    def _create_slot_card(self, index: int, rack_item: dict) -> QWidget:
        card = QFrame()
        card.setObjectName("rack_slot")
        card.setFixedHeight(54)

        c_layout = QHBoxLayout(card)
        c_layout.setContentsMargins(12, 6, 12, 6)
        c_layout.setSpacing(12)

        # Numéro de slot
        lbl_num = QLabel(f"#{index:02d}")
        lbl_num.setStyleSheet("font-weight: bold; color: #64748b; font-size: 12px;")
        c_layout.addWidget(lbl_num)

        # Icône et Type
        is_inst = rack_item.get("plugin_type") == "instrument"
        lbl_icon = QLabel("🎹" if is_inst else "🎛️")
        lbl_icon.setStyleSheet("font-size: 16px;")
        c_layout.addWidget(lbl_icon)

        # Nom et détails
        info_layout = QVBoxLayout()
        info_layout.setSpacing(1)

        lbl_name = QLabel(rack_item.get("name", "Plugin"))
        lbl_name.setStyleSheet("font-weight: bold; font-size: 13px; color: #ffffff;")
        info_layout.addWidget(lbl_name)

        # Compter les pistes utilisant ce plugin
        path = rack_item.get("file_path", "")
        used_by = []
        for t in self.project.tracks:
            if t.plugin_path == path:
                used_by.append(t.name)
            if hasattr(t, "insert_effects") and path in t.insert_effects:
                used_by.append(t.name)

        if used_by:
            usage_str = f"Utilisé par : {', '.join(used_by)}"
            usage_color = "#10b981"
        else:
            usage_str = "Non affecté (prêt dans le projet)"
            usage_color = "#64748b"

        lbl_used = QLabel(usage_str)
        lbl_used.setStyleSheet(f"font-size: 10px; color: {usage_color};")
        info_layout.addWidget(lbl_used)

        c_layout.addLayout(info_layout, stretch=1)

        # Bouton [e] Interface
        btn_e = QPushButton("🎹 Ouvrir Interface [e]")
        btn_e.setStyleSheet("""
            QPushButton {
                background-color: #1c2638;
                color: #38bdf8;
                font-weight: bold;
                border: 1px solid #38bdf8;
                padding: 4px 10px;
            }
            QPushButton:hover {
                background-color: #0284c7;
                color: #ffffff;
            }
        """)
        btn_e.clicked.connect(lambda _, p=path: open_plugin_editor_gui(p, self))
        c_layout.addWidget(btn_e)

        # Bouton Supprimer
        btn_del = QPushButton("✕")
        btn_del.setFixedSize(24, 24)
        btn_del.setStyleSheet("background: transparent; border: none; color: #ef4444; font-size: 13px; font-weight: bold;")
        btn_del.setToolTip("Retirer ce plugin du projet")
        btn_del.clicked.connect(lambda _, rid=rack_item["id"]: self._remove_plugin(rid))
        c_layout.addWidget(btn_del)

        return card

    def _show_add_instrument_menu(self):
        instruments = global_plugin_manager.get_compatible_instruments()
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background-color: #1a1c26; color: #ffffff; border: 1px solid #33384a; }
            QMenu::item:selected { background-color: #0284c7; }
        """)
        if not instruments:
            act = menu.addAction("Aucun instrument VST3 scanné")
            act.setEnabled(False)
        else:
            for inst in instruments:
                act = menu.addAction(f"🎹 {inst.name}")
                act.triggered.connect(lambda _, i=inst: self._add_plugin(i.file_path, i.name, "instrument"))

        menu.addSeparator()
        act_scan = menu.addAction("🔍 Scanner les dossiers...")
        act_scan.triggered.connect(self._trigger_scan)
        menu.exec(self.btn_add_inst.mapToGlobal(self.btn_add_inst.rect().bottomLeft()))

    def _show_add_effect_menu(self):
        effects = global_plugin_manager.get_compatible_effects()
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background-color: #1a1c26; color: #ffffff; border: 1px solid #33384a; }
            QMenu::item:selected { background-color: #0284c7; }
        """)
        if not effects:
            act = menu.addAction("Aucun effet VST3 scanné")
            act.setEnabled(False)
        else:
            for fx in effects:
                act = menu.addAction(f"🎛️ {fx.name}")
                act.triggered.connect(lambda _, f=fx: self._add_plugin(f.file_path, f.name, "effect"))

        menu.exec(self.btn_add_fx.mapToGlobal(self.btn_add_fx.rect().bottomLeft()))

    def _browse_vst_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Charger un plugin VST3 dans le projet",
            "",
            "Plugins VST3 (*.vst3);;Tous les fichiers (*.*)"
        )
        if file_path:
            info = global_plugin_manager.add_plugin_file(file_path)
            if info.is_compatible:
                self._add_plugin(file_path, info.name, info.plugin_type)
            else:
                QMessageBox.warning(self, "Incompatible", f"Le plugin n'a pas pu être chargé :\n{info.error_message}")

    def _add_plugin(self, file_path: str, name: str, plugin_type: str):
        if not hasattr(self.project, "plugin_rack"):
            self.project.plugin_rack = []

        # Vérifier si déjà présent
        if any(p.get("file_path") == file_path for p in self.project.plugin_rack):
            QMessageBox.information(self, "Déjà présent", f"Le plugin '{name}' est déjà chargé dans le Rack du projet.")
            return

        self.project.add_rack_plugin(file_path, name, plugin_type)
        self.refresh_rack()
        self.rack_changed.emit()

    def _remove_plugin(self, rack_id: str):
        self.project.remove_rack_plugin(rack_id)
        self.refresh_rack()
        self.rack_changed.emit()

    def _trigger_scan(self):
        from ui.plugin_dialogs import PluginManagerDialog
        dlg = PluginManagerDialog(self)
        dlg.start_scan()
        dlg.exec()
        self.refresh_rack()
