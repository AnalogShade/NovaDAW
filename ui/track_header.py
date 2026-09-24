"""
ui/track_header.py - En-tête de piste individuel (Mute, Solo, Arm, Volume, Pan, Couleur)
"""
from typing import Optional
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
    QSlider, QLineEdit, QFrame, QMenu, QComboBox, QSizePolicy
)
from PySide6.QtCore import Qt, Signal
from core.project import Track
from core.plugin_manager import global_plugin_manager
from ui.glow_effects import set_button_glow


class ResetableSlider(QSlider):
    """QSlider avec réinitialisation à une valeur par défaut sur double-clic."""
    double_clicked = Signal()

    def __init__(self, orientation=Qt.Horizontal, default_value: int = 0, parent=None):
        super().__init__(orientation, parent)
        self.default_value = default_value

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.setValue(self.default_value)
            self.double_clicked.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class CompactNumEdit(QLineEdit):
    """Champ de texte compact pour saisie numérique (Volume, Pan) sélectionnant tout au focus."""
    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet("""
            QLineEdit {
                background-color: #121319;
                border: 1px solid #282a36;
                border-radius: 3px;
                color: #cbd5e1;
                font-size: 9px;
                font-weight: bold;
                padding: 0px 1px;
            }
            QLineEdit:hover {
                border-color: #3b82f6;
            }
            QLineEdit:focus {
                border: 1px solid #38bdf8;
                background-color: #191c28;
                color: #ffffff;
            }
        """)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.selectAll()

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        self.selectAll()


class TrackHeaderWidget(QFrame):
    track_modified = Signal()
    track_selected = Signal(str)  # track_id
    track_deleted = Signal(str)   # track_id
    track_height_changed = Signal(str, int, bool)  # (track_id, height, apply_all)

    RESIZE_MARGIN = 6

    def __init__(self, track: Track, parent=None, project=None):
        super().__init__(parent)
        self.track = track
        self._project_override = project
        self.is_selected = False
        self._resizing_height = False
        self._drag_start_y = 0
        self._drag_start_h = getattr(track, "height", 76)

        initial_h = getattr(self.track, "height", 76)
        self.setFixedHeight(initial_h)
        self.setObjectName("track_header")
        self.setProperty("class", "track_header")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMouseTracking(True)

        self._init_ui()
        self.update_style()
        self.set_track_height(initial_h)

    def _init_ui(self):
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 6, 0)
        main_layout.setSpacing(5)

        # 1. Bande latérale de couleur
        self.color_bar = QFrame()
        self.color_bar.setFixedWidth(5)
        self.color_bar.setStyleSheet(f"background-color: {self.track.color}; border-top-left-radius: 4px; border-bottom-left-radius: 4px;")
        main_layout.addWidget(self.color_bar)

        # Contenu principal
        content_layout = QVBoxLayout()
        content_layout.setContentsMargins(2, 2, 2, 2)
        content_layout.setSpacing(2)

        # Ligne 1 : Icône + Nom + Boutons M/S/R + Bouton Supprimer
        row1 = QHBoxLayout()
        row1.setSpacing(3)

        # Icône du type
        if "drum" in (self.track.plugin_name or "").lower() or "batterie" in self.track.name.lower() or self.track.plugin_path == "novadaw.drum_machine":
            icon = "🥁"
        elif "synth" in (self.track.plugin_name or "").lower() or self.track.plugin_path == "novadaw.synth":
            icon = "⚡"
        elif self.track.track_type == "midi":
            icon = "🎹"
        else:
            icon = "🔊"
        self.lbl_icon = QLabel(icon)
        self.lbl_icon.setStyleSheet("font-size: 12px;")
        row1.addWidget(self.lbl_icon)

        # Nom éditable
        self.txt_name = QLineEdit(self.track.name)
        self.txt_name.setStyleSheet("background: transparent; border: none; font-weight: bold; font-size: 11px; padding: 1px 2px;")
        self.txt_name.setCursorPosition(0)
        self.txt_name.setToolTip(self.track.name)
        self.txt_name.editingFinished.connect(self._on_name_changed)
        row1.addWidget(self.txt_name, stretch=1)

        # Bouton Mute
        self.btn_mute = QPushButton("M")
        self.btn_mute.setObjectName("btn_track_mute")
        self.btn_mute.setCheckable(True)
        self.btn_mute.setChecked(self.track.muted)
        set_button_glow(self.btn_mute, self.track.muted, "#f59e0b", blur_radius=14, alpha=220)
        self.btn_mute.setToolTip("Mute (Couper le son de cette piste)")
        self.btn_mute.setFixedSize(22, 22)
        self.btn_mute.toggled.connect(self._on_mute_toggled)
        row1.addWidget(self.btn_mute)

        # Bouton Solo
        self.btn_solo = QPushButton("S")
        self.btn_solo.setObjectName("btn_track_solo")
        self.btn_solo.setCheckable(True)
        self.btn_solo.setChecked(self.track.soloed)
        set_button_glow(self.btn_solo, self.track.soloed, "#facc15", blur_radius=14, alpha=220)
        self.btn_solo.setToolTip("Solo (Écouter cette piste uniquement)")
        self.btn_solo.setFixedSize(22, 22)
        self.btn_solo.toggled.connect(self._on_solo_toggled)
        row1.addWidget(self.btn_solo)

        # Bouton Record Arm (R)
        self.btn_rec = QPushButton("R")
        self.btn_rec.setObjectName("btn_track_rec")
        self.btn_rec.setCheckable(True)
        self.btn_rec.setChecked(self.track.armed)
        set_button_glow(self.btn_rec, self.track.armed, "#ff2244", blur_radius=16, alpha=235)
        self.btn_rec.setToolTip("Armer pour l'enregistrement (R) - Cliquez pour enregistrer sur cette piste")
        self.btn_rec.setFixedSize(22, 22)
        self.btn_rec.toggled.connect(self._on_rec_toggled)
        row1.addWidget(self.btn_rec)

        # Bouton Supprimer
        self.btn_delete = QPushButton("✕")
        self.btn_delete.setFixedSize(18, 18)
        self.btn_delete.setStyleSheet("background: transparent; border: none; color: #64748b; font-size: 11px; padding: 0px;")
        self.btn_delete.setToolTip("Supprimer cette piste")
        self.btn_delete.clicked.connect(lambda: self.track_deleted.emit(self.track.id))
        row1.addWidget(self.btn_delete)

        content_layout.addLayout(row1)

        # Ligne 2 : Fader de Volume & Panoramique avec saisie manuelle et double-clic réinitialisant
        self.row2_widget = QWidget()
        row2 = QHBoxLayout(self.row2_widget)
        row2.setContentsMargins(0, 0, 0, 0)
        row2.setSpacing(3)

        lbl_v = QLabel("VOL")
        lbl_v.setStyleSheet("font-size: 8px; color: #64748b; font-weight: bold;")
        row2.addWidget(lbl_v)

        vol_val = int(self.track.volume * 100)
        self.slider_vol = ResetableSlider(Qt.Horizontal, default_value=80)
        self.slider_vol.setRange(0, 150)
        self.slider_vol.setValue(vol_val)
        self.slider_vol.setFixedHeight(14)
        self.slider_vol.setMinimumWidth(30)
        self.slider_vol.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.slider_vol.setToolTip(f"Volume : {vol_val}% (Double-cliquer pour réinitialiser à 80%)")
        self.slider_vol.valueChanged.connect(self._on_vol_changed)
        row2.addWidget(self.slider_vol, stretch=2)

        self.txt_vol = CompactNumEdit(f"{vol_val}%")
        self.txt_vol.setFixedWidth(36)
        self.txt_vol.setFixedHeight(16)
        self.txt_vol.setToolTip("Saisir le volume (ex: 80, 100%, 0.8) - Entrée pour valider")
        self.txt_vol.editingFinished.connect(self._on_vol_text_edited)
        row2.addWidget(self.txt_vol)

        lbl_pan = QLabel("PAN")
        lbl_pan.setStyleSheet("font-size: 8px; color: #64748b; font-weight: bold;")
        row2.addWidget(lbl_pan)

        pan_val = int(self.track.pan * 100)
        self.slider_pan = ResetableSlider(Qt.Horizontal, default_value=0)
        self.slider_pan.setRange(-100, 100)
        self.slider_pan.setValue(pan_val)
        self.slider_pan.setFixedHeight(14)
        self.slider_pan.setMinimumWidth(25)
        self.slider_pan.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        pan_str = self._format_pan(pan_val)
        self.slider_pan.setToolTip(f"Pan : {pan_str} (Double-cliquer pour réinitialiser au centre)")
        self.slider_pan.valueChanged.connect(self._on_pan_changed)
        row2.addWidget(self.slider_pan, stretch=1)

        self.txt_pan = CompactNumEdit(pan_str)
        self.txt_pan.setFixedWidth(32)
        self.txt_pan.setFixedHeight(16)
        self.txt_pan.setToolTip("Saisir le panoramique (ex: C, L30, R40, -20) - Entrée pour valider")
        self.txt_pan.editingFinished.connect(self._on_pan_text_edited)
        row2.addWidget(self.txt_pan)

        content_layout.addWidget(self.row2_widget)

        # Ligne 3 (pour les pistes MIDI) : Périphérique de sortie / Instrument VST (style Cubase)
        if self.track.track_type == "midi":
            self.row3_widget = QWidget()
            row3 = QHBoxLayout(self.row3_widget)
            row3.setContentsMargins(0, 0, 0, 0)
            row3.setSpacing(4)

            lbl_out = QLabel("OUT")
            lbl_out.setStyleSheet("font-size: 8px; color: #38bdf8; font-weight: bold;")
            row3.addWidget(lbl_out)

            self.combo_plugin = QComboBox()
            self.combo_plugin.setFixedHeight(19)
            self.combo_plugin.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self.combo_plugin.setStyleSheet("""
                QComboBox {
                    background-color: #121318;
                    color: #e2e8f0;
                    border: 1px solid #282a36;
                    border-radius: 3px;
                    padding-left: 4px;
                    font-size: 10px;
                }
                QComboBox::drop-down { border: none; width: 14px; }
                QComboBox QAbstractItemView {
                    background-color: #1a1c24;
                    color: #ffffff;
                    selection-background-color: #0284c7;
                    font-size: 10px;
                }
            """)
            self._populate_plugin_combo()
            self.combo_plugin.currentIndexChanged.connect(self._on_plugin_changed)
            global_plugin_manager.scan_updated.connect(self._populate_plugin_combo)
            row3.addWidget(self.combo_plugin, stretch=1)

            # Bouton [e] d'édition d'instrument virtuel (style Cubase)
            self.btn_edit_plugin = QPushButton("e")
            self.btn_edit_plugin.setFixedSize(20, 19)
            self.btn_edit_plugin.setToolTip("Éditer l'instrument virtuel (ouvrir l'interface VST)")
            self.btn_edit_plugin.setStyleSheet("""
                QPushButton {
                    background-color: #1e2230;
                    color: #38bdf8;
                    font-weight: bold;
                    font-size: 11px;
                    border: 1px solid #38bdf8;
                    border-radius: 3px;
                    padding: 0px;
                }
                QPushButton:hover {
                    background-color: #38bdf8;
                    color: #0f172a;
                }
                QPushButton:disabled {
                    border-color: #282a36;
                    color: #475569;
                    background-color: transparent;
                }
            """)
            self.btn_edit_plugin.setEnabled(bool(self.track.plugin_path))
            self.btn_edit_plugin.clicked.connect(self._on_open_plugin_editor)
            global_plugin_manager.editor_opened.connect(self._on_editor_state_changed)
            global_plugin_manager.editor_closed.connect(self._on_editor_state_changed)
            row3.addWidget(self.btn_edit_plugin)

            content_layout.addWidget(self.row3_widget)
        else:
            content_layout.addStretch()

        main_layout.addLayout(content_layout)

    def set_track_height(self, h: int):
        h = max(46, min(320, int(h)))
        self.setFixedHeight(h)
        self.track.height = h
        if hasattr(self, "row2_widget"):
            self.row2_widget.setVisible(h >= 58)
        if hasattr(self, "row3_widget"):
            self.row3_widget.setVisible(h >= 76)

    def mouseMoveEvent(self, event):
        if self._resizing_height:
            dy = int(event.globalPosition().y() - self._drag_start_y)
            new_h = max(46, min(320, self._drag_start_h + dy))
            apply_all = bool(event.modifiers() & (Qt.ShiftModifier | Qt.AltModifier))
            self.set_track_height(new_h)
            self.track_height_changed.emit(self.track.id, new_h, apply_all)
            event.accept()
            return

        if event.position().y() >= self.height() - self.RESIZE_MARGIN:
            self.setCursor(Qt.SizeVerCursor)
        else:
            self.unsetCursor()
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and event.position().y() >= self.height() - self.RESIZE_MARGIN:
            self._resizing_height = True
            self._drag_start_y = event.globalPosition().y()
            self._drag_start_h = self.height()
            self.grabMouse()
            event.accept()
            return
        self.track_selected.emit(self.track.id)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self._resizing_height:
            self._resizing_height = False
            self.releaseMouse()
            self.unsetCursor()
            self.track.height = self.height()
            self.track_modified.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def set_selected(self, selected: bool):
        self.is_selected = selected
        self.update_style()

    def update_style(self):
        border_color = "#38bdf8" if self.is_selected else "#282a36"
        bg_color = "#20232e" if self.is_selected else "#181920"
        self.setStyleSheet(f"""
            QFrame#track_header {{
                background-color: {bg_color};
                border-bottom: 1px solid #252834;
                border-left: 2px solid {border_color};
            }}
        """)
        self.color_bar.setStyleSheet(f"background-color: {self.track.color};")

    def _on_name_changed(self):
        new_name = self.txt_name.text().strip()
        if new_name:
            self.track.name = new_name
            self.track_modified.emit()

    def _on_mute_toggled(self, checked: bool):
        self.track.muted = checked
        set_button_glow(self.btn_mute, checked, "#f59e0b", blur_radius=14, alpha=220)
        self.track_modified.emit()

    def _on_solo_toggled(self, checked: bool):
        self.track.soloed = checked
        set_button_glow(self.btn_solo, checked, "#facc15", blur_radius=14, alpha=220)
        self.track_modified.emit()

    def _on_rec_toggled(self, checked: bool):
        self.track.armed = checked
        set_button_glow(self.btn_rec, checked, "#ff2244", blur_radius=16, alpha=235)
        self.track_modified.emit()

    def update_arm_state(self, armed: bool):
        """Met à jour l'état visuel et logique de l'armement R avec son aura rouge."""
        self.track.armed = armed
        self.btn_rec.blockSignals(True)
        self.btn_rec.setChecked(armed)
        self.btn_rec.blockSignals(False)
        set_button_glow(self.btn_rec, armed, "#ff2244", blur_radius=16, alpha=235)

    def update_mute_state(self, muted: bool):
        """Met à jour l'état visuel et logique de Mute avec son aura ambrée."""
        self.track.muted = muted
        self.btn_mute.blockSignals(True)
        self.btn_mute.setChecked(muted)
        self.btn_mute.blockSignals(False)
        set_button_glow(self.btn_mute, muted, "#f59e0b", blur_radius=14, alpha=220)

    def update_solo_state(self, soloed: bool):
        """Met à jour l'état visuel et logique de Solo avec son aura dorée."""
        self.track.soloed = soloed
        self.btn_solo.blockSignals(True)
        self.btn_solo.setChecked(soloed)
        self.btn_solo.blockSignals(False)
        set_button_glow(self.btn_solo, soloed, "#facc15", blur_radius=14, alpha=220)

    def sync_controls_from_track(self):
        """Synchronise tous les contrôles (M, S, R, Volume, Pan, Nom) depuis la piste."""
        self.update_mute_state(self.track.muted)
        self.update_solo_state(self.track.soloed)
        self.update_arm_state(self.track.armed)
        if hasattr(self, "slider_vol"):
            self.slider_vol.blockSignals(True)
            self.slider_vol.setValue(int(round(self.track.volume * 100)))
            self.slider_vol.blockSignals(False)
        if hasattr(self, "txt_vol") and not self.txt_vol.hasFocus():
            self.txt_vol.setText(f"{int(round(self.track.volume * 100))}%")
        if hasattr(self, "slider_pan"):
            self.slider_pan.blockSignals(True)
            self.slider_pan.setValue(int(round(self.track.pan * 100)))
            self.slider_pan.blockSignals(False)
        if hasattr(self, "txt_pan") and not self.txt_pan.hasFocus():
            self.txt_pan.setText(self._format_pan(int(round(self.track.pan * 100))))
        if hasattr(self, "txt_name") and not self.txt_name.hasFocus():
            self.txt_name.setText(self.track.name)
        self._populate_plugin_combo()

    @staticmethod
    def _format_pan(value: int) -> str:
        if value == 0:
            return "C"
        return f"L{abs(value)}" if value < 0 else f"R{value}"

    @staticmethod
    def _parse_vol_text(text: str) -> Optional[int]:
        raw = text.strip().rstrip("%").strip()
        if not raw:
            return None
        try:
            val = float(raw)
            if 0.0 <= val <= 1.5 and "." in raw:
                val *= 100.0
            return max(0, min(150, int(round(val))))
        except ValueError:
            return None

    @staticmethod
    def _parse_pan_text(text: str) -> Optional[int]:
        raw = text.strip().upper()
        if not raw:
            return None
        if raw in ("C", "CENTER", "CENTRE", "MID", "0"):
            return 0
        if raw.startswith("L") or raw.startswith("G"):
            try:
                num_part = raw[1:].strip().rstrip("%")
                val = float(num_part) if num_part else 100.0
                if 0.0 <= val <= 1.0 and "." in num_part:
                    val *= 100.0
                return max(-100, min(0, -int(round(val))))
            except ValueError:
                return None
        if raw.startswith("R") or raw.startswith("D"):
            try:
                num_part = raw[1:].strip().rstrip("%")
                val = float(num_part) if num_part else 100.0
                if 0.0 <= val <= 1.0 and "." in num_part:
                    val *= 100.0
                return max(0, min(100, int(round(val))))
            except ValueError:
                return None
        try:
            val = float(raw)
            if -1.0 <= val <= 1.0 and "." in raw:
                val *= 100.0
            return max(-100, min(100, int(round(val))))
        except ValueError:
            return None

    def _on_vol_changed(self, value: int):
        self.track.volume = value / 100.0
        self.slider_vol.setToolTip(f"Volume : {value}% (Double-cliquer pour réinitialiser à 80%)")
        if hasattr(self, "txt_vol") and not self.txt_vol.hasFocus():
            self.txt_vol.setText(f"{value}%")
        self.track_modified.emit()

    def _on_vol_text_edited(self):
        parsed = self._parse_vol_text(self.txt_vol.text())
        if parsed is not None:
            self.slider_vol.setValue(parsed)
            self.txt_vol.setText(f"{parsed}%")
        else:
            self.txt_vol.setText(f"{self.slider_vol.value()}%")

    def _on_pan_changed(self, value: int):
        self.track.pan = value / 100.0
        pan_str = self._format_pan(value)
        self.slider_pan.setToolTip(f"Pan : {pan_str} (Double-cliquer pour réinitialiser au centre)")
        if hasattr(self, "txt_pan") and not self.txt_pan.hasFocus():
            self.txt_pan.setText(pan_str)
        self.track_modified.emit()

    def _on_pan_text_edited(self):
        parsed = self._parse_pan_text(self.txt_pan.text())
        if parsed is not None:
            self.slider_pan.setValue(parsed)
            self.txt_pan.setText(self._format_pan(parsed))
        else:
            self.txt_pan.setText(self._format_pan(self.slider_pan.value()))

    @property
    def project(self):
        if getattr(self, "_project_override", None):
            return self._project_override
        win = self.window()
        if win and win is not self and hasattr(win, "project") and getattr(win, "project", None):
            return win.project
        p = self.parent()
        while p:
            if hasattr(p, "project") and getattr(p, "project", None):
                return p.project
            p = p.parent()
        return getattr(global_plugin_manager, "current_project", None)

    def _populate_plugin_combo(self):
        """Remplit la liste déroulante des instruments (Synthé interne, Nova Drums, Rack du projet et VST3)"""
        if not hasattr(self, "combo_plugin"):
            return
        self.combo_plugin.blockSignals(True)
        self.combo_plugin.clear()

        # 1. Synthé interne
        self.combo_plugin.addItem("🎹 Synthé Interne", userData=None)

        # 2. NovaSynth (Synthétiseur Polyphonique Modulaire)
        self.combo_plugin.addItem("⚡ NovaSynth", userData="novadaw.synth")

        # 3. Nova Drums VSTi
        self.combo_plugin.addItem("🥁 Nova Drums VSTi", userData="novadaw.drum_machine")

        # 4. Instruments déjà chargés dans le Rack du projet
        rack_paths = set()
        proj = self.project
        if proj and hasattr(proj, "plugin_rack"):
            rack_insts = [p for p in proj.plugin_rack if p.get("plugin_type") == "instrument"]
            for idx, r in enumerate(rack_insts, 1):
                rack_paths.add(r.get("file_path"))
                self.combo_plugin.addItem(f"🎹 [Rack #{idx:02d}] {r['name']}", userData=r["file_path"])

        # 5. Tous les autres instruments compatibles scannés
        instruments = global_plugin_manager.get_compatible_instruments()
        for inst in instruments:
            if inst.file_path not in rack_paths and inst.file_path not in ("novadaw.drum_machine", "novadaw.synth"):
                self.combo_plugin.addItem(f"🎹 {inst.name}", userData=inst.file_path)

        # 6. Option pour ajouter au rack
        self.combo_plugin.addItem("➕ Assigner un instrument au Rack…", userData="__ADD_TO_RACK__")

        # Trouver l'élément sélectionné
        selected_idx = 0
        if self.track.plugin_path:
            for idx in range(self.combo_plugin.count()):
                if self.combo_plugin.itemData(idx) == self.track.plugin_path:
                    selected_idx = idx
                    break

        self.combo_plugin.setCurrentIndex(selected_idx)
        self.combo_plugin.blockSignals(False)
        if hasattr(self, "btn_edit_plugin"):
            self.btn_edit_plugin.setEnabled(bool(self.track.plugin_path))

    def _on_plugin_changed(self, index: int):
        file_path = self.combo_plugin.currentData()
        if file_path == "__ADD_TO_RACK__":
            win = self.window()
            if hasattr(win, "floating_vst_rack") and win.floating_vst_rack:
                win.floating_vst_rack.show_and_focus()
                win.floating_vst_rack.rack_widget.show_add_dialog()
            elif hasattr(win, "vst_rack") and win.vst_rack:
                win.vst_rack.show_add_dialog()
            self._populate_plugin_combo()
            return

        if file_path:
            self.track.plugin_path = file_path
            raw = self.combo_plugin.currentText().replace("🎹 ", "").replace("🥁 ", "").replace("⚡ ", "")
            if "[Rack #" in raw:
                raw = raw.split("]", 1)[-1].strip()
            self.track.plugin_name = raw.strip()
            self.btn_edit_plugin.setEnabled(True)

            # S'assurer que le plugin est bien ajouté au rack du projet
            proj = self.project
            if proj and hasattr(proj, "add_rack_plugin"):
                proj.add_rack_plugin(file_path, self.track.plugin_name, "instrument")

            # Si Nova Drums ou NovaSynth, s'assurer qu'il est instancié
            if file_path in ("novadaw.drum_machine", "novadaw.synth"):
                has_inst = any(getattr(p, "plugin_type_id", None) == file_path for p in getattr(self.track, "plugins", []))
                if not has_inst:
                    from plugins.registry import plugin_registry, ensure_plugins_loaded
                    ensure_plugins_loaded()
                    inst = plugin_registry.create_plugin(file_path)
                    if inst:
                        self.track.plugins.insert(0, inst)
        else:
            self.track.plugin_path = None
            self.track.plugin_name = None
            self.btn_edit_plugin.setEnabled(False)

        self.track_modified.emit()

    def _on_editor_state_changed(self, path=None):
        if not hasattr(self, "btn_edit_plugin"):
            return
        is_open = False
        if self.track.plugin_path in ("novadaw.synth", "novadaw.drum_machine"):
            from ui.plugin_dialogs import _open_native_editors
            for p in getattr(self.track, "plugins", []):
                if getattr(p, "plugin_type_id", None) == self.track.plugin_path:
                    inst_id = getattr(p, "instance_id", None)
                    if inst_id and inst_id in _open_native_editors:
                        is_open = _open_native_editors[inst_id].isVisible()
                    break
        elif self.track.plugin_path:
            is_open = global_plugin_manager.is_editor_open(f"track:{self.track.id}:instrument:{self.track.plugin_path}")
            if not is_open:
                is_open = global_plugin_manager.is_editor_open(self.track.plugin_path)

        if is_open:
            self.btn_edit_plugin.setStyleSheet("""
                QPushButton {
                    background-color: #0284c7;
                    color: #ffffff;
                    font-weight: bold;
                    border: 1px solid #38bdf8;
                    border-radius: 3px;
                }
                QPushButton:hover { background-color: #0369a1; }
            """)
            self.btn_edit_plugin.setToolTip("L'interface du plugin est ouverte (Cliquer pour fermer)")
        else:
            self.btn_edit_plugin.setStyleSheet("""
                QPushButton {
                    background-color: #1e2230;
                    color: #38bdf8;
                    font-weight: bold;
                    border: 1px solid #38bdf8;
                    border-radius: 3px;
                }
                QPushButton:hover {
                    background-color: #38bdf8;
                    color: #0f172a;
                }
                QPushButton:disabled {
                    border-color: #282a36;
                    color: #475569;
                    background-color: transparent;
                }
            """)
            self.btn_edit_plugin.setToolTip("Éditer l'instrument VST (Ouvrir l'interface)")

    def _on_open_plugin_editor(self):
        if self.track.plugin_path in ("novadaw.drum_machine", "novadaw.synth"):
            from ui.plugin_dialogs import open_native_plugin_editor
            from plugins.registry import plugin_registry, ensure_plugins_loaded
            native_plugin = next((p for p in getattr(self.track, "plugins", []) if getattr(p, "plugin_type_id", None) == self.track.plugin_path), None)
            if not native_plugin:
                ensure_plugins_loaded()
                native_plugin = plugin_registry.create_plugin(self.track.plugin_path)
                if native_plugin:
                    self.track.plugins.insert(0, native_plugin)
            if native_plugin:
                open_native_plugin_editor(native_plugin, self)
        elif self.track.plugin_path:
            from ui.plugin_dialogs import open_plugin_editor_gui
            open_plugin_editor_gui(self.track.plugin_path, self, f"track:{self.track.id}:instrument:{self.track.plugin_path}")
