"""
ui/vst_rack.py - Panneau du Rack VST Global du Projet (Style Cubase F11)
Permet d'ajouter, visualiser et manipuler une stack de plugins VST3
chargés globalement dans le projet indépendamment des pistes.
"""
from typing import Optional
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QScrollArea, QMenu, QFileDialog, QMessageBox, QSizePolicy
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QCursor

from core.project import Project
from core.plugin_manager import global_plugin_manager
from ui.plugin_dialogs import analyze_plugin_file, open_plugin_editor_gui, open_native_plugin_editor


class VstRackWidget(QWidget):
    """
    Rack VST global du projet (style Cubase F11 VST Instruments Rack).
    Permet de maintenir une stack de plugins chargés dans le projet.
    """
    rack_changed = Signal()

    def __init__(self, project: Project, parent=None):
        super().__init__(parent)
        self.project = project
        self.setObjectName("vst_rack")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("""
            QWidget#vst_rack {
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
        global_plugin_manager.editor_opened.connect(self.refresh_rack)
        global_plugin_manager.editor_closed.connect(self.refresh_rack)

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 10, 12, 10)
        main_layout.setSpacing(10)

        # Barre d'actions supérieure
        top_bar = QHBoxLayout()
        lbl_title = QLabel("🎛️ PLUGINS DU PROJET — INSTRUMENTS & EFFETS")
        lbl_title.setStyleSheet("font-weight: bold; font-size: 13px; color: #38bdf8;")
        top_bar.addWidget(lbl_title)
        top_bar.addStretch()

        self.btn_add_inst = QPushButton("+ Ajouter un plugin au projet…")
        self.btn_add_inst.setObjectName("btn_add")
        self.btn_add_inst.setMinimumWidth(160)
        self.btn_add_inst.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.btn_add_inst.clicked.connect(self.show_add_dialog)
        top_bar.addWidget(self.btn_add_inst)

        self.btn_add_fx = QPushButton("+ Ajouter un effet…")
        self.btn_add_fx.setMinimumWidth(130)
        self.btn_add_fx.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.btn_add_fx.clicked.connect(self._show_add_effect_menu)
        top_bar.addWidget(self.btn_add_fx)

        self.btn_browse = QPushButton("📁 Parcourir .vst3...")
        self.btn_browse.setMinimumWidth(130)
        self.btn_browse.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.btn_browse.clicked.connect(self._browse_vst_file)
        top_bar.addWidget(self.btn_browse)

        self.btn_scan_rack = QPushButton("🔍 Scanner…")
        self.btn_scan_rack.setMinimumWidth(100)
        self.btn_scan_rack.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.btn_scan_rack.clicked.connect(self._trigger_scan)
        top_bar.addWidget(self.btn_scan_rack)

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
        # Synchroniser le rack avec les instruments et effets des pistes du projet
        if hasattr(self.project, "sync_rack_plugins"):
            self.project.sync_rack_plugins()

        # Nettoyer les slots existants
        while self.stack_layout.count() > 1:
            item = self.stack_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        items = getattr(self.project, "plugin_rack", [])
        for idx, rack_item in enumerate(items, 1):
            slot_widget = self._create_slot_card(idx, rack_item)
            self.stack_layout.insertWidget(self.stack_layout.count() - 1, slot_widget)

        # Ajouter la carte de slot cliquable en bas de la stack
        empty_card = self._create_empty_slot_card(len(items) + 1)
        self.stack_layout.insertWidget(self.stack_layout.count() - 1, empty_card)

    def _create_empty_slot_card(self, next_idx: int) -> QWidget:
        card = QFrame()
        card.setObjectName("empty_rack_slot")
        card.setMinimumHeight(48)
        card.setCursor(Qt.PointingHandCursor)
        card.setStyleSheet("""
            QFrame#empty_rack_slot {
                background-color: #161822;
                border: 1px dashed #334155;
                border-radius: 6px;
            }
            QFrame#empty_rack_slot:hover {
                background-color: #1c2230;
                border-color: #38bdf8;
            }
        """)
        c_layout = QHBoxLayout(card)
        c_layout.setContentsMargins(14, 6, 14, 6)
        c_layout.setSpacing(12)

        lbl_num = QLabel(f"#{next_idx:02d}")
        lbl_num.setStyleSheet("font-weight: bold; color: #475569; font-size: 11px;")
        c_layout.addWidget(lbl_num)

        lbl_icon = QLabel("➕")
        lbl_icon.setStyleSheet("font-size: 14px; color: #38bdf8;")
        c_layout.addWidget(lbl_icon)

        lbl_text = QLabel("Cliquer pour assigner un nouvel Instrument ou Effet dans le Rack…")
        lbl_text.setStyleSheet("color: #94a3b8; font-size: 12px; font-weight: 500;")
        c_layout.addWidget(lbl_text, stretch=1)

        btn_add = QPushButton("+ Assigner…")
        btn_add.setObjectName("btn_add")
        btn_add.setFixedWidth(110)
        btn_add.clicked.connect(self.show_add_dialog)
        c_layout.addWidget(btn_add)

        card.mousePressEvent = lambda e: self.show_add_dialog()
        return card

    def _create_slot_card(self, index: int, rack_item: dict) -> QWidget:
        card = QFrame()
        card.setObjectName("rack_slot")
        card.setMinimumHeight(56)
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        c_layout = QHBoxLayout(card)
        c_layout.setContentsMargins(12, 6, 12, 6)
        c_layout.setSpacing(10)

        # Bouton On/Off (Bypass)
        is_enabled = rack_item.get("enabled", True)
        btn_pwr = QPushButton("⏻")
        btn_pwr.setFixedSize(26, 26)
        if is_enabled:
            btn_pwr.setStyleSheet("""
                QPushButton {
                    background-color: #0369a1;
                    color: #ffffff;
                    border: 1px solid #38bdf8;
                    border-radius: 13px;
                    font-size: 13px;
                    font-weight: bold;
                }
                QPushButton:hover { background-color: #0284c7; }
            """)
            btn_pwr.setToolTip("Plugin actif (Cliquer pour bypasser)")
        else:
            btn_pwr.setStyleSheet("""
                QPushButton {
                    background-color: #1e2230;
                    color: #64748b;
                    border: 1px solid #334155;
                    border-radius: 13px;
                    font-size: 13px;
                }
                QPushButton:hover { background-color: #262c3e; color: #94a3b8; }
            """)
            btn_pwr.setToolTip("Plugin désactivé/bypassé (Cliquer pour activer)")

        def _toggle_power():
            rack_item["enabled"] = not rack_item.get("enabled", True)
            self.refresh_rack()
            self.rack_changed.emit()

        btn_pwr.clicked.connect(_toggle_power)
        c_layout.addWidget(btn_pwr)

        # Numéro de slot
        lbl_num = QLabel(f"#{index:02d}")
        lbl_num.setStyleSheet("font-weight: bold; color: #64748b; font-size: 12px;")
        c_layout.addWidget(lbl_num)

        # Icône et Type
        is_inst = rack_item.get("plugin_type") == "instrument"
        path = rack_item.get("file_path", "")
        icon_char = "🎹" if is_inst else "🎛️"
        if path == "novadaw.synth":
            icon_char = "⚡"
        elif path == "novadaw.drum_machine":
            icon_char = "🥁"
        elif path == "novadaw.equalizer":
            icon_char = "📊"
        elif path == "novadaw.compressor":
            icon_char = "🗜️"
        elif path == "novadaw.mixer":
            icon_char = "🎚️"

        lbl_icon = QLabel(icon_char)
        lbl_icon.setStyleSheet("font-size: 16px;")
        c_layout.addWidget(lbl_icon)

        # Nom et détails
        info_layout = QVBoxLayout()
        info_layout.setSpacing(1)

        fmt = "Natif" if path.startswith("novadaw.") else ("VST2" if path.lower().endswith(".dll") else "VST3")
        lbl_name = QLabel(f"{rack_item.get('name', 'Plugin')} <span style='font-size: 10px; color: #64748b;'>[{fmt}]</span>")
        lbl_name.setTextFormat(Qt.RichText)
        lbl_name.setStyleSheet("font-weight: bold; font-size: 13px; color: #ffffff;")
        info_layout.addWidget(lbl_name)

        # Compter les pistes utilisant ce plugin (y compris master track et plugins empilés)
        used_by = []
        all_tracks = list(self.project.tracks)
        if self.project.master_track:
            all_tracks.append(self.project.master_track)

        for t in all_tracks:
            t_name = t.name or "Piste"
            if t.plugin_path == path and t_name not in used_by:
                used_by.append(t_name)
            if hasattr(t, "insert_effects") and path in t.insert_effects and t_name not in used_by:
                used_by.append(t_name)
            if hasattr(t, "plugins"):
                for p in t.plugins:
                    p_type = getattr(p, "plugin_type_id", None) or getattr(p, "id", None)
                    if p_type == path and t_name not in used_by:
                        used_by.append(t_name)

        if used_by:
            usage_str = f"Utilisé sur : {', '.join(used_by)}"
            usage_color = "#10b981"
        else:
            usage_str = "Prêt dans le projet (sélectionnable sur une piste)"
            usage_color = "#64748b"

        lbl_used = QLabel(usage_str)
        lbl_used.setStyleSheet(f"font-size: 10px; color: {usage_color};")
        info_layout.addWidget(lbl_used)

        c_layout.addLayout(info_layout, stretch=1)

        # Bouton [e] Interface
        is_open = False
        if path.startswith("novadaw."):
            from ui.plugin_dialogs import _open_native_editors
            # Vérifier si l'éditeur du rack est ouvert
            rack_p = getattr(self.project, "_rack_native_plugins", {}).get(path)
            if rack_p and getattr(rack_p, "instance_id", None) in _open_native_editors:
                is_open = _open_native_editors[rack_p.instance_id].isVisible()
            # Ou si une instance de piste est ouverte
            if not is_open:
                for t in all_tracks:
                    for p in getattr(t, "plugins", []):
                        if getattr(p, "plugin_type_id", None) == path:
                            p_id = getattr(p, "instance_id", None)
                            if p_id and p_id in _open_native_editors and _open_native_editors[p_id].isVisible():
                                is_open = True
                                break
                    if is_open:
                        break
        else:
            is_open = global_plugin_manager.is_editor_open(path)
            if not is_open:
                for t in all_tracks:
                    if global_plugin_manager.is_editor_open(f"track:{t.id}:instrument:{path}") or \
                       global_plugin_manager.is_editor_open(f"track:{t.id}:effect:{path}"):
                        is_open = True
                        break

        btn_text = f"{icon_char} Fermer Interface [e]" if is_open else f"{icon_char} Ouvrir Interface [e]"
        btn_e = QPushButton(btn_text)
        btn_e.setMinimumWidth(150)
        if is_open:
            btn_e.setStyleSheet("""
                QPushButton {
                    background-color: #0284c7;
                    color: #ffffff;
                    font-weight: bold;
                    border: 1px solid #38bdf8;
                    padding: 4px 10px;
                }
                QPushButton:hover {
                    background-color: #0369a1;
                }
            """)
            btn_e.setToolTip("L'interface du plugin est ouverte (Cliquer pour fermer)")
        else:
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
            btn_e.setToolTip("Ouvrir l'interface graphique du plugin [e]")
        btn_e.clicked.connect(lambda _, p=path: self.open_editor(p))
        c_layout.addWidget(btn_e)

        # Bouton Supprimer
        btn_del = QPushButton("✕")
        btn_del.setFixedSize(24, 24)
        btn_del.setStyleSheet("background: transparent; border: none; color: #ef4444; font-size: 13px; font-weight: bold; padding: 0px;")
        rack_id = rack_item.get("id", "")
        btn_del.clicked.connect(lambda _, rid=rack_id: self._remove_plugin(rid))
        c_layout.addWidget(btn_del)

        return card

    def show_add_dialog(self):
        from ui.project_plugin_dialog import ProjectPluginDialog
        ProjectPluginDialog(self).exec()

    def open_editor(self, path):
        if path.startswith("novadaw."):
            # Chercher les instances de ce plugin sur les pistes du projet
            track_instances = []
            all_tracks = list(self.project.tracks)
            if self.project.master_track:
                all_tracks.append(self.project.master_track)

            for t in all_tracks:
                for p in getattr(t, "plugins", []):
                    if getattr(p, "plugin_type_id", None) == path:
                        track_instances.append((t, p))

            if len(track_instances) == 1:
                # Une seule piste utilise ce plugin : ouvrir directement son instance !
                open_native_plugin_editor(track_instances[0][1], self)
            elif len(track_instances) > 1:
                # Plusieurs pistes utilisent ce plugin : afficher un menu pour choisir
                menu = QMenu(self)
                menu.setStyleSheet("""
                    QMenu { background-color: #1a1c26; color: #ffffff; border: 1px solid #33384a; }
                    QMenu::item:selected { background-color: #0284c7; }
                """)
                for t, p in track_instances:
                    act = menu.addAction(f"🎛️ Éditer sur : {t.name}")
                    act.triggered.connect(lambda _, inst=p: open_native_plugin_editor(inst, self))
                menu.addSeparator()
                act_tmpl = menu.addAction("⚙️ Éditer les réglages par défaut du Rack")
                act_tmpl.triggered.connect(lambda: open_native_plugin_editor(self.project.get_rack_native_plugin(path), self))
                menu.exec(QCursor.pos())
            else:
                plugin = self.project.get_rack_native_plugin(path)
                open_native_plugin_editor(plugin, self)
        else:
            open_plugin_editor_gui(path, self)

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
            info = analyze_plugin_file(file_path, self)
            if info.is_compatible:
                self._add_plugin(file_path, info.name, info.plugin_type)
            else:
                QMessageBox.warning(self, "Incompatible", f"Le plugin n'a pas pu être chargé :\n{info.error_message}")

    def _add_plugin(self, file_path: str, name: str, plugin_type: str):
        if not hasattr(self.project, "plugin_rack"):
            self.project.plugin_rack = []

        # Vérifier si déjà présent
        if any(p.get("file_path") == file_path for p in self.project.plugin_rack):
            return True

        if not file_path.startswith("novadaw.") and file_path not in global_plugin_manager.active_instances:
            from ui.plugin_dialogs import PluginLoadingDialog
            dlg = PluginLoadingDialog(file_path, name, self)
            if not dlg.exec():
                QMessageBox.warning(self, "Plugin indisponible", dlg.error_message or "Chargement impossible")
                return
        self.project.add_rack_plugin(file_path, name, plugin_type)
        self.refresh_rack()
        self.rack_changed.emit()
        return True

    def _remove_plugin(self, rack_id: str):
        item = next((p for p in self.project.plugin_rack if p["id"] == rack_id), None)
        if item:
            path = item["file_path"]
            all_tracks = list(self.project.tracks)
            if self.project.master_track:
                all_tracks.append(self.project.master_track)
            used = any(t.plugin_path == path or path in getattr(t, "insert_effects", []) or any(getattr(p, "plugin_type_id", None) == path for p in getattr(t, "plugins", []))
                       for t in all_tracks)
            if used:
                QMessageBox.information(self, "Plugin utilisé", "Changez la sortie MIDI ou retirez ce plugin des pistes avant de le retirer du projet.")
                return
        self.project.remove_rack_plugin(rack_id)
        self.refresh_rack()
        self.rack_changed.emit()

    def _trigger_scan(self):
        from ui.plugin_dialogs import PluginManagerDialog
        dlg = PluginManagerDialog(self)
        dlg.start_scan()
        dlg.exec()
        self.refresh_rack()
