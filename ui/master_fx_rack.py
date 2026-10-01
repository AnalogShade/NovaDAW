"""
ui/master_fx_rack.py - Panneau d'Effets de Traitement & Mastering du Bus Master (👑 Master FX).

Fournit un rack complet intégré dans la zone inférieure (lower_zone) pour insérer,
ordonner, configurer et bypasser les processeurs audio de mastering sur le bus stéréo
(Égaliseur Paramétrique, Compresseur Studio, plugins VST3 audio externes).
"""
import os
from typing import Optional, List, Any
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QScrollArea, QMenu, QFileDialog, QMessageBox, QSizePolicy
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont

from core.project import Project, Track
from core.plugin_manager import global_plugin_manager
from plugins.registry import plugin_registry, ensure_plugins_loaded
from ui.plugin_dialogs import (
    open_native_plugin_editor, open_plugin_editor_gui,
    analyze_plugin_file, ensure_plugin_loaded
)


class MasterFxSlotCard(QFrame):
    """Carte représentant un slot d'effet inséré sur la piste Master."""

    def __init__(
        self,
        slot_number: int,
        plugin_name: str,
        plugin_icon: str,
        is_enabled: bool,
        description: str,
        on_toggle_bypass,
        on_open_editor,
        on_move_up=None,
        on_move_down=None,
        on_remove=None,
        can_move_up: bool = False,
        can_move_down: bool = False,
        parent=None
    ):
        super().__init__(parent)
        self.setObjectName("master_fx_slot")
        self.setMinimumHeight(56)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.setStyleSheet("""
            QFrame#master_fx_slot {
                background-color: #171a24;
                border: 1px solid #2d3345;
                border-radius: 6px;
            }
            QFrame#master_fx_slot:hover {
                border-color: #f59e0b;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(10)

        # 1. Bouton Bypass / Power (⏻)
        self.btn_power = QPushButton("⏻")
        self.btn_power.setFixedSize(26, 26)
        if is_enabled:
            self.btn_power.setStyleSheet("""
                QPushButton {
                    background-color: #064e3b;
                    color: #34d399;
                    border: 1px solid #059669;
                    border-radius: 13px;
                    font-size: 13px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: #047857;
                }
            """)
            self.btn_power.setToolTip("Effet actif (Cliquer pour bypasser)")
        else:
            self.btn_power.setStyleSheet("""
                QPushButton {
                    background-color: #262626;
                    color: #737373;
                    border: 1px solid #404040;
                    border-radius: 13px;
                    font-size: 13px;
                }
                QPushButton:hover {
                    background-color: #333333;
                    color: #a3a3a3;
                }
            """)
            self.btn_power.setToolTip("Effet bypassé (Cliquer pour activer)")

        self.btn_power.clicked.connect(on_toggle_bypass)
        layout.addWidget(self.btn_power)

        # 2. Numéro du slot (#01, #02...)
        lbl_num = QLabel(f"#{slot_number:02d}")
        lbl_num.setStyleSheet("font-weight: bold; color: #fbbf24; font-size: 11px;")
        layout.addWidget(lbl_num)

        # 3. Icône
        lbl_icon = QLabel(plugin_icon)
        lbl_icon.setStyleSheet("font-size: 16px;")
        layout.addWidget(lbl_icon)

        # 4. Informations du plugin
        info_layout = QVBoxLayout()
        info_layout.setSpacing(1)

        lbl_name = QLabel(plugin_name)
        lbl_name.setStyleSheet("font-weight: bold; font-size: 12px; color: #f1f5f9;")
        info_layout.addWidget(lbl_name)

        lbl_desc = QLabel(description or "Processeur audio de mastering stéréo")
        lbl_desc.setStyleSheet("font-size: 10px; color: #94a3b8;")
        info_layout.addWidget(lbl_desc)

        layout.addLayout(info_layout, stretch=1)

        # 5. Bouton Ouvrir Éditeur [e]
        btn_open = QPushButton("🎛️ Éditeur [e]")
        btn_open.setFixedHeight(26)
        btn_open.setStyleSheet("""
            QPushButton {
                background-color: #1e2230;
                color: #fbbf24;
                border: 1px solid #d97706;
                border-radius: 4px;
                padding: 2px 10px;
                font-size: 11px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #d97706;
                color: #ffffff;
            }
        """)
        btn_open.setToolTip("Ouvrir l'interface graphique de réglages de cet effet")
        btn_open.clicked.connect(on_open_editor)
        layout.addWidget(btn_open)

        # 6. Bouton Déplacer vers le haut [▲]
        if on_move_up:
            btn_up = QPushButton("▲")
            btn_up.setFixedSize(22, 26)
            btn_up.setEnabled(can_move_up)
            btn_up.setStyleSheet("""
                QPushButton {
                    background-color: #1e2230;
                    color: #94a3b8;
                    border: 1px solid #334155;
                    border-radius: 3px;
                    font-size: 10px;
                }
                QPushButton:hover:enabled {
                    background-color: #334155;
                    color: #f1f5f9;
                }
                QPushButton:disabled {
                    color: #475569;
                    border-color: #1e293b;
                }
            """)
            btn_up.setToolTip("Monter dans la chaîne de mastering")
            btn_up.clicked.connect(on_move_up)
            layout.addWidget(btn_up)

        # 7. Bouton Déplacer vers le bas [▼]
        if on_move_down:
            btn_down = QPushButton("▼")
            btn_down.setFixedSize(22, 26)
            btn_down.setEnabled(can_move_down)
            btn_down.setStyleSheet("""
                QPushButton {
                    background-color: #1e2230;
                    color: #94a3b8;
                    border: 1px solid #334155;
                    border-radius: 3px;
                    font-size: 10px;
                }
                QPushButton:hover:enabled {
                    background-color: #334155;
                    color: #f1f5f9;
                }
                QPushButton:disabled {
                    color: #475569;
                    border-color: #1e293b;
                }
            """)
            btn_down.setToolTip("Descendre dans la chaîne de mastering")
            btn_down.clicked.connect(on_move_down)
            layout.addWidget(btn_down)

        # 8. Bouton Supprimer [🗑️]
        if on_remove:
            btn_del = QPushButton("🗑️")
            btn_del.setFixedSize(26, 26)
            btn_del.setStyleSheet("""
                QPushButton {
                    background-color: #26161c;
                    color: #f87171;
                    border: 1px solid #7f1d1d;
                    border-radius: 3px;
                    font-size: 11px;
                }
                QPushButton:hover {
                    background-color: #7f1d1d;
                    color: #ffffff;
                }
            """)
            btn_del.setToolTip("Retirer cet effet de la piste Master")
            btn_del.clicked.connect(on_remove)
            layout.addWidget(btn_del)


class MasterFxWidget(QWidget):
    """
    Panneau de gestion des Effets Master (👑 Master FX Rack).
    Permet d'ajouter, réordonner, activer et configurer les effets de mastering sur le bus stéréo.
    """
    fx_changed = Signal()

    def __init__(self, project: Project, parent=None):
        super().__init__(parent)
        self.project = project
        self.setObjectName("master_fx_rack")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("""
            QWidget#master_fx_rack {
                background-color: #13151b;
                color: #e2e8f0;
            }
            QLabel {
                font-size: 12px;
            }
            QPushButton {
                background-color: #1e2230;
                color: #f1f5f9;
                border: 1px solid #334155;
                border-radius: 4px;
                padding: 5px 10px;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #2a3144;
                border-color: #f59e0b;
            }
        """)

        self._init_ui()
        self.refresh_rack()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(14, 10, 14, 10)
        main_layout.setSpacing(8)

        # 1. Barre d'outils supérieure
        top_bar = QHBoxLayout()
        top_bar.setSpacing(10)

        lbl_crown = QLabel("👑")
        lbl_crown.setStyleSheet("font-size: 18px;")
        top_bar.addWidget(lbl_crown)

        lbl_title = QLabel("EFFETS MASTER (BUS STÉRÉO) — MASTERING & INSERTIONS AUDIO")
        lbl_title.setStyleSheet("font-weight: bold; font-size: 13px; color: #fbbf24; letter-spacing: 0.5px;")
        top_bar.addWidget(lbl_title)

        self.lbl_status = QLabel("0 effet actif")
        self.lbl_status.setStyleSheet("color: #94a3b8; font-size: 11px; font-weight: 500;")
        top_bar.addWidget(self.lbl_status)

        top_bar.addStretch()

        # Bouton Bypass Global (A/B Mastering)
        self.btn_bypass_all = QPushButton("⏻ Bypass Global")
        self.btn_bypass_all.setToolTip("Bypass / Réactive tous les effets de mastering à la fois pour comparaison A/B")
        self.btn_bypass_all.setStyleSheet("""
            QPushButton {
                background-color: #1e2230;
                color: #e2e8f0;
                border: 1px solid #475569;
                border-radius: 4px;
                padding: 5px 10px;
                font-weight: bold;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #334155;
                border-color: #fbbf24;
            }
        """)
        self.btn_bypass_all.clicked.connect(self.toggle_global_bypass)
        top_bar.addWidget(self.btn_bypass_all)

        # Bouton Ajouter Égaliseur
        btn_add_eq = QPushButton("📊 + Égaliseur")
        btn_add_eq.setToolTip("Ajouter un Égaliseur Paramétrique de mastering (3 à 24 bandes)")
        btn_add_eq.setStyleSheet("""
            QPushButton {
                background-color: #0369a1;
                color: #ffffff;
                border: 1px solid #38bdf8;
                border-radius: 4px;
                padding: 5px 10px;
                font-weight: bold;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #0284c7;
            }
        """)
        btn_add_eq.clicked.connect(self.add_equalizer)
        top_bar.addWidget(btn_add_eq)

        # Bouton Ajouter Compresseur
        btn_add_comp = QPushButton("🗜️ + Compresseur")
        btn_add_comp.setToolTip("Ajouter un Compresseur Dynamique de studio pour le bus master")
        btn_add_comp.setStyleSheet("""
            QPushButton {
                background-color: #b45309;
                color: #ffffff;
                border: 1px solid #fbbf24;
                border-radius: 4px;
                padding: 5px 10px;
                font-weight: bold;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #d97706;
            }
        """)
        btn_add_comp.clicked.connect(self.add_compressor)
        top_bar.addWidget(btn_add_comp)

        # Bouton Menu VST3
        self.btn_add_vst = QPushButton("📁 + Effet VST3…")
        self.btn_add_vst.setToolTip("Ajouter un effet VST3 externe sur le bus Master")
        self.btn_add_vst.setStyleSheet("""
            QPushButton {
                background-color: #4c1d95;
                color: #ffffff;
                border: 1px solid #c084fc;
                border-radius: 4px;
                padding: 5px 10px;
                font-weight: bold;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #6b21a8;
            }
        """)
        self.btn_add_vst.clicked.connect(self._show_vst_menu)
        top_bar.addWidget(self.btn_add_vst)

        main_layout.addLayout(top_bar)

        # 2. Zone défilante des cartes de slots
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("border: none; background: transparent;")

        self.rack_container = QWidget()
        self.rack_layout = QVBoxLayout(self.rack_container)
        self.rack_layout.setContentsMargins(0, 4, 0, 4)
        self.rack_layout.setSpacing(6)
        self.rack_layout.addStretch()

        scroll.setWidget(self.rack_container)
        main_layout.addWidget(scroll)

    def set_project(self, project: Project):
        self.project = project
        self.refresh_rack()

    def get_master_track(self) -> Optional[Track]:
        if not self.project:
            return None
        return self.project.ensure_master_track()

    def get_master_insert_plugins(self) -> List[Any]:
        """Retourne la liste des effets natifs du master en filtrant le mixeur interne."""
        master_t = self.get_master_track()
        if not master_t or not hasattr(master_t, "plugins"):
            return []
        return [p for p in master_t.plugins if getattr(p, "plugin_type_id", None) != "novadaw.mixer"]

    def refresh_rack(self):
        """Reconstruit entièrement la liste des effets master affichés."""
        # Nettoyer les slots actuels
        while self.rack_layout.count() > 1:
            item = self.rack_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        master_t = self.get_master_track()
        if not master_t:
            return

        insert_plugins = self.get_master_insert_plugins()
        vst_effects = getattr(master_t, "insert_effects", [])
        total_effects = len(insert_plugins) + len(vst_effects)

        # Mettre à jour l'étiquette de statut
        active_count = sum(1 for p in insert_plugins if getattr(p, "enabled", True)) + len(vst_effects)
        if total_effects == 0:
            self.lbl_status.setText("Aucun effet inséré")
        else:
            self.lbl_status.setText(f"{active_count}/{total_effects} effet(s) actif(s)")

        slot_idx = 1

        # 1. Rendu des plugins audio natifs (Égaliseur, Compresseur, etc.)
        for p_idx, plugin in enumerate(insert_plugins):
            is_enabled = getattr(plugin, "enabled", True)
            p_name = getattr(plugin, "name", "Effet Audio")
            p_icon = getattr(plugin, "icon", "🎛️")
            p_desc = getattr(plugin, "description", "")

            can_up = p_idx > 0
            can_down = p_idx < len(insert_plugins) - 1

            slot_widget = MasterFxSlotCard(
                slot_number=slot_idx,
                plugin_name=p_name,
                plugin_icon=p_icon,
                is_enabled=is_enabled,
                description=p_desc,
                on_toggle_bypass=lambda p=plugin: self._toggle_plugin_bypass(p),
                on_open_editor=lambda p=plugin: open_native_plugin_editor(p, self),
                on_move_up=lambda idx=p_idx: self._move_plugin_up(idx),
                on_move_down=lambda idx=p_idx: self._move_plugin_down(idx),
                on_remove=lambda p=plugin: self.remove_native_plugin(p),
                can_move_up=can_up,
                can_move_down=can_down,
                parent=self
            )
            self.rack_layout.insertWidget(self.rack_layout.count() - 1, slot_widget)
            slot_idx += 1

        # 2. Rendu des effets VST3 externes
        for v_idx, vst_path in enumerate(vst_effects):
            vst_name = os.path.splitext(os.path.basename(vst_path))[0]
            slot_widget = MasterFxSlotCard(
                slot_number=slot_idx,
                plugin_name=f"VST3: {vst_name}",
                plugin_icon="🎛️",
                is_enabled=True,
                description=f"Plugin audio VST3 externe ({os.path.basename(vst_path)})",
                on_toggle_bypass=lambda: None,
                on_open_editor=lambda path=vst_path: open_plugin_editor_gui(
                    path, self, f"track:{master_t.id}:effect:{path}"
                ),
                on_move_up=None,
                on_move_down=None,
                on_remove=lambda idx=v_idx: self.remove_vst_effect(idx),
                can_move_up=False,
                can_move_down=False,
                parent=self
            )
            self.rack_layout.insertWidget(self.rack_layout.count() - 1, slot_widget)
            slot_idx += 1

        # 3. Message si aucun effet n'est inséré
        if total_effects == 0:
            empty_banner = QFrame()
            empty_banner.setStyleSheet("""
                QFrame {
                    background-color: #181b26;
                    border: 1px solid #2d3345;
                    border-radius: 6px;
                    padding: 12px;
                }
            """)
            eb_layout = QVBoxLayout(empty_banner)
            eb_layout.setSpacing(4)

            lbl_hint_title = QLabel("💡 Bus Master Stéréo sans effet d'insertion")
            lbl_hint_title.setStyleSheet("font-weight: bold; color: #fbbf24; font-size: 12px;")
            eb_layout.addWidget(lbl_hint_title)

            lbl_hint_text = QLabel(
                "Le bus Master reçoit la somme audio de toutes vos pistes arrangées. "
                "Pour sculpter le mixage final, ajoutez un Égaliseur paramétrique pour équilibrer le spectre sonore "
                "ou un Compresseur dynamique de studio pour stabiliser la dynamique globale."
            )
            lbl_hint_text.setStyleSheet("color: #94a3b8; font-size: 11px;")
            lbl_hint_text.setWordWrap(True)
            eb_layout.addWidget(lbl_hint_text)

            self.rack_layout.insertWidget(self.rack_layout.count() - 1, empty_banner)

        # 4. Carte d'insertion cliquable en bas
        bottom_add_card = self._create_bottom_add_card(slot_idx)
        self.rack_layout.insertWidget(self.rack_layout.count() - 1, bottom_add_card)

    def _create_bottom_add_card(self, next_idx: int) -> QWidget:
        card = QFrame()
        card.setObjectName("bottom_add_card")
        card.setMinimumHeight(44)
        card.setCursor(Qt.PointingHandCursor)
        card.setStyleSheet("""
            QFrame#bottom_add_card {
                background-color: #151822;
                border: 1px dashed #d97706;
                border-radius: 6px;
            }
            QFrame#bottom_add_card:hover {
                background-color: #1f2537;
                border-color: #fbbf24;
            }
        """)
        c_layout = QHBoxLayout(card)
        c_layout.setContentsMargins(14, 6, 14, 6)
        c_layout.setSpacing(10)

        lbl_num = QLabel(f"#{next_idx:02d}")
        lbl_num.setStyleSheet("font-weight: bold; color: #78350f; font-size: 11px;")
        c_layout.addWidget(lbl_num)

        lbl_icon = QLabel("➕")
        lbl_icon.setStyleSheet("font-size: 14px; color: #fbbf24;")
        c_layout.addWidget(lbl_icon)

        lbl_text = QLabel("Cliquer pour insérer un effet audio sur le bus Master (Égaliseur, Compresseur, VST3…)")
        lbl_text.setStyleSheet("color: #d1d5db; font-size: 11px; font-weight: 500;")
        c_layout.addWidget(lbl_text, stretch=1)

        btn_add = QPushButton("+ Insérer Effet…")
        btn_add.setStyleSheet("""
            QPushButton {
                background-color: #d97706;
                color: #ffffff;
                font-weight: bold;
                border-radius: 3px;
                padding: 4px 10px;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #f59e0b;
            }
        """)
        btn_add.clicked.connect(self._show_add_menu)
        c_layout.addWidget(btn_add)

        card.mousePressEvent = lambda e: self._show_add_menu()
        return card

    def _show_add_menu(self):
        """Menu contextuel pour choisir un effet à ajouter sur le Master."""
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #1a1e29;
                color: #f1f5f9;
                border: 1px solid #d97706;
                font-size: 11px;
                padding: 4px;
            }
            QMenu::item {
                padding: 6px 20px 6px 10px;
                border-radius: 3px;
            }
            QMenu::item:selected {
                background-color: #f59e0b;
                color: #0b0d13;
                font-weight: bold;
            }
            QMenu::separator {
                height: 1px;
                background-color: #334155;
                margin: 4px 6px;
            }
        """)

        act_eq = menu.addAction("📊 Égaliseur Paramétrique (3/10/12/24 bandes)")
        act_eq.triggered.connect(self.add_equalizer)

        act_comp = menu.addAction("🗜️ Compresseur Dynamique (Studio)")
        act_comp.triggered.connect(self.add_compressor)

        menu.addSeparator()

        effects = global_plugin_manager.get_compatible_effects()
        if effects:
            menu_vst = menu.addMenu("📁 Effets VST3 externes scannés…")
            menu_vst.setStyleSheet(menu.styleSheet())
            for fx in effects:
                act_fx = menu_vst.addAction(f"🎛️ {fx.name}")
                act_fx.triggered.connect(lambda _, path=fx.file_path: self.add_vst_effect(path))

        act_browse = menu.addAction("➕ Parcourir un fichier .vst3…")
        act_browse.triggered.connect(self.browse_vst_file)

        menu.exec(self.cursor().pos())

    def _show_vst_menu(self):
        """Affiche le menu dédié aux effets VST3 externes sous le bouton d'en-tête."""
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #1a1e29;
                color: #f1f5f9;
                border: 1px solid #c084fc;
                font-size: 11px;
                padding: 4px;
            }
            QMenu::item {
                padding: 6px 20px 6px 10px;
                border-radius: 3px;
            }
            QMenu::item:selected {
                background-color: #9333ea;
                color: #ffffff;
                font-weight: bold;
            }
            QMenu::separator {
                height: 1px;
                background-color: #334155;
                margin: 4px 6px;
            }
        """)

        effects = global_plugin_manager.get_compatible_effects()
        if effects:
            for fx in effects:
                act_fx = menu.addAction(f"🎛️ {fx.name}")
                act_fx.triggered.connect(lambda _, path=fx.file_path: self.add_vst_effect(path))
            menu.addSeparator()

        act_browse = menu.addAction("➕ Parcourir un fichier .vst3…")
        act_browse.triggered.connect(self.browse_vst_file)

        menu.exec(self.btn_add_vst.mapToGlobal(self.btn_add_vst.rect().bottomLeft()))

    def add_equalizer(self):
        """Ajoute un Égaliseur Paramétrique à la chaîne Master et ouvre son éditeur."""
        master_t = self.get_master_track()
        if not master_t:
            return
        ensure_plugins_loaded()
        eq = plugin_registry.create_plugin("novadaw.equalizer")
        if eq:
            if hasattr(eq, "set_project"):
                eq.set_project(self.project)
            master_t.add_plugin(eq)
            self.refresh_rack()
            self.fx_changed.emit()
            open_native_plugin_editor(eq, self)

    def add_compressor(self):
        """Ajoute un Compresseur Dynamique à la chaîne Master et ouvre son éditeur."""
        master_t = self.get_master_track()
        if not master_t:
            return
        ensure_plugins_loaded()
        comp = plugin_registry.create_plugin("novadaw.compressor")
        if comp:
            if hasattr(comp, "set_project"):
                comp.set_project(self.project)
            master_t.add_plugin(comp)
            self.refresh_rack()
            self.fx_changed.emit()
            open_native_plugin_editor(comp, self)

    def add_vst_effect(self, file_path: str):
        """Ajoute un effet VST3 externe à la chaîne Master."""
        master_t = self.get_master_track()
        if not master_t:
            return
        if not hasattr(master_t, "insert_effects"):
            master_t.insert_effects = []
        if file_path in master_t.insert_effects:
            return

        if not ensure_plugin_loaded(file_path, self, f"track:{master_t.id}:effect:{file_path}"):
            return

        master_t.insert_effects.append(file_path)
        info = next((p for p in global_plugin_manager.plugins if p.file_path == file_path), None)
        if self.project:
            self.project.add_rack_plugin(file_path, info.name if info else os.path.basename(file_path), "effect")

        self.refresh_rack()
        self.fx_changed.emit()

    def browse_vst_file(self):
        """Ouvre l'explorateur pour sélectionner un plugin .vst3 audio."""
        master_t = self.get_master_track()
        if not master_t:
            return
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Sélectionner un effet VST3 pour le Master",
            "",
            "Plugins VST3 (*.vst3);;Tous les fichiers (*.*)"
        )
        if file_path:
            info = analyze_plugin_file(file_path, self)
            if info.is_compatible and info.plugin_type == "effect":
                self.add_vst_effect(file_path)
            else:
                QMessageBox.warning(self, "Plugin Incompatible", f"Le plugin sélectionné n'est pas un effet compatible :\n{info.error_message}")

    def _toggle_plugin_bypass(self, plugin: Any):
        plugin.enabled = not getattr(plugin, "enabled", True)
        self.refresh_rack()
        self.fx_changed.emit()

    def toggle_global_bypass(self):
        """Active ou désactive tous les effets de la chaîne Master (A/B Mastering test)."""
        insert_plugins = self.get_master_insert_plugins()
        if not insert_plugins:
            return

        any_enabled = any(getattr(p, "enabled", True) for p in insert_plugins)
        new_state = not any_enabled
        for p in insert_plugins:
            p.enabled = new_state

        self.refresh_rack()
        self.fx_changed.emit()

    def _move_plugin_up(self, display_idx: int):
        master_t = self.get_master_track()
        if not master_t or not hasattr(master_t, "plugins"):
            return
        insert_plugins = self.get_master_insert_plugins()
        if 0 < display_idx < len(insert_plugins):
            p_to_move = insert_plugins[display_idx]
            p_prev = insert_plugins[display_idx - 1]

            real_idx_move = master_t.plugins.index(p_to_move)
            real_idx_prev = master_t.plugins.index(p_prev)

            master_t.move_plugin(real_idx_move, real_idx_prev)
            self.refresh_rack()
            self.fx_changed.emit()

    def _move_plugin_down(self, display_idx: int):
        master_t = self.get_master_track()
        if not master_t or not hasattr(master_t, "plugins"):
            return
        insert_plugins = self.get_master_insert_plugins()
        if 0 <= display_idx < len(insert_plugins) - 1:
            p_to_move = insert_plugins[display_idx]
            p_next = insert_plugins[display_idx + 1]

            real_idx_move = master_t.plugins.index(p_to_move)
            real_idx_next = master_t.plugins.index(p_next)

            master_t.move_plugin(real_idx_move, real_idx_next)
            self.refresh_rack()
            self.fx_changed.emit()

    def remove_native_plugin(self, plugin: Any):
        master_t = self.get_master_track()
        if not master_t or not hasattr(master_t, "plugins"):
            return
        instance_id = getattr(plugin, "instance_id", None)
        if instance_id:
            master_t.remove_plugin(instance_id)
            self.refresh_rack()
            self.fx_changed.emit()

    def remove_vst_effect(self, vst_idx: int):
        master_t = self.get_master_track()
        if not master_t or not hasattr(master_t, "insert_effects"):
            return
        if 0 <= vst_idx < len(master_t.insert_effects):
            master_t.insert_effects.pop(vst_idx)
            self.refresh_rack()
            self.fx_changed.emit()
