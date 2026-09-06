"""
ui/inspector.py - Inspecteur de piste latéral inspiré de Cubase Pro
Affiche les réglages complets de la piste sélectionnée :
- Sortie Instrument VSTi et bouton [e] pour les pistes MIDI
- Chaîne d'effets d'insert et boutons [e] pour les pistes Audio
- Faders Volume, Panoramique, Mute, Solo, Record
"""
from typing import Optional
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSlider, QComboBox, QFrame, QScrollArea,
    QFileDialog, QMessageBox
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor

from core.project import Track, Project
from core.plugin_manager import global_plugin_manager
from ui.plugin_dialogs import open_plugin_editor_gui


class TrackInspector(QFrame):
    """
    Panneau inspecteur de piste (style Cubase).
    Se met à jour dès qu'une piste est sélectionnée.
    """
    track_modified = Signal()

    def __init__(self, project: Project, parent=None):
        super().__init__(parent)
        self.project = project
        self.current_track: Optional[Track] = None

        self.setFixedWidth(230)
        self.setObjectName("track_inspector")
        self.setStyleSheet("""
            QFrame#track_inspector {
                background-color: #14161f;
                border-right: 1px solid #232634;
            }
            QLabel {
                color: #94a3b8;
                font-size: 11px;
            }
            QLabel#section_title {
                color: #38bdf8;
                font-size: 10px;
                font-weight: bold;
                letter-spacing: 1px;
            }
            QComboBox {
                background-color: #1a1c26;
                color: #f1f5f9;
                border: 1px solid #2b2e3e;
                border-radius: 3px;
                padding: 4px 6px;
                font-size: 11px;
            }
            QComboBox:hover {
                border-color: #38bdf8;
            }
            QComboBox QAbstractItemView {
                background-color: #1a1c26;
                color: #f1f5f9;
                selection-background-color: #0284c7;
                font-size: 11px;
            }
            QPushButton#btn_edit_plugin {
                background-color: #1c2638;
                color: #38bdf8;
                font-weight: bold;
                font-size: 11px;
                border: 1px solid #38bdf8;
                border-radius: 3px;
                padding: 5px;
            }
            QPushButton#btn_edit_plugin:hover {
                background-color: #0284c7;
                color: #ffffff;
            }
            QPushButton#btn_edit_plugin:disabled {
                border-color: #2b2e3e;
                color: #475569;
                background-color: transparent;
            }
            QSlider::groove:horizontal {
                height: 4px;
                background: #252836;
                border-radius: 2px;
            }
            QSlider::sub-page:horizontal {
                background: #38bdf8;
                border-radius: 2px;
            }
            QSlider::handle:horizontal {
                background: #ffffff;
                width: 12px;
                margin: -4px 0;
                border-radius: 6px;
            }
        """)

        self._init_ui()
        self.set_track(None)

        # Mettre à jour les listes déroulantes quand des plugins sont scannés
        global_plugin_manager.scan_updated.connect(self._refresh_plugin_lists)

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(8)

        # 1. En-tête Inspecteur
        top_header = QHBoxLayout()
        lbl_top = QLabel("INSPECTEUR")
        lbl_top.setStyleSheet("font-size: 10px; font-weight: bold; color: #64748b; letter-spacing: 1px;")
        top_header.addWidget(lbl_top)
        top_header.addStretch()
        main_layout.addLayout(top_header)

        # Zone scrollable
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("border: none; background: transparent;")
        self.content_widget = QWidget()
        self.content_layout = QVBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(10)
        scroll.setWidget(self.content_widget)
        main_layout.addWidget(scroll)

        # Carte d'identité de piste
        self.card_info = QFrame()
        self.card_info.setStyleSheet("background-color: #1a1c26; border-radius: 4px; padding: 6px;")
        ci_layout = QVBoxLayout(self.card_info)
        ci_layout.setContentsMargins(6, 6, 6, 6)
        ci_layout.setSpacing(6)

        row_name = QHBoxLayout()
        self.lbl_icon = QLabel("🎹")
        self.lbl_icon.setStyleSheet("font-size: 14px;")
        row_name.addWidget(self.lbl_icon)

        self.txt_name = QLineEdit("Synth Lead")
        self.txt_name.setStyleSheet("background: transparent; border: none; font-weight: bold; font-size: 12px; color: #ffffff;")
        self.txt_name.editingFinished.connect(self._on_name_changed)
        row_name.addWidget(self.txt_name, stretch=1)
        ci_layout.addLayout(row_name)

        self.lbl_type = QLabel("Piste MIDI (Instrument Virtuel)")
        self.lbl_type.setStyleSheet("font-size: 10px; color: #38bdf8;")
        ci_layout.addWidget(self.lbl_type)

        self.content_layout.addWidget(self.card_info)

        # 2. SECTION INSTRUMENT (Pour pistes MIDI)
        self.frame_midi = QFrame()
        self.frame_midi.setStyleSheet("background-color: #181a24; border: 1px solid #232634; border-radius: 4px;")
        fm_layout = QVBoxLayout(self.frame_midi)
        fm_layout.setContentsMargins(8, 8, 8, 8)
        fm_layout.setSpacing(6)

        lbl_inst_title = QLabel("SORTIE INSTRUMENT VST")
        lbl_inst_title.setObjectName("section_title")
        fm_layout.addWidget(lbl_inst_title)

        lbl_inst_desc = QLabel("Périphérique de synthèse :")
        fm_layout.addWidget(lbl_inst_desc)

        self.combo_instrument = QComboBox()
        self.combo_instrument.currentIndexChanged.connect(self._on_instrument_changed)
        fm_layout.addWidget(self.combo_instrument)

        self.btn_edit_instrument = QPushButton("🎹 Ouvrir Interface Plugin [e]")
        self.btn_edit_instrument.setObjectName("btn_edit_plugin")
        self.btn_edit_instrument.clicked.connect(self._on_open_instrument_editor)
        fm_layout.addWidget(self.btn_edit_instrument)

        self.content_layout.addWidget(self.frame_midi)

        # 3. SECTION INSERTS (Pour pistes Audio)
        self.frame_audio = QFrame()
        self.frame_audio.setStyleSheet("background-color: #181a24; border: 1px solid #232634; border-radius: 4px;")
        fa_layout = QVBoxLayout(self.frame_audio)
        fa_layout.setContentsMargins(8, 8, 8, 8)
        fa_layout.setSpacing(6)

        lbl_fx_title = QLabel("EFFETS D'INSERT (AUDIO)")
        lbl_fx_title.setObjectName("section_title")
        fa_layout.addWidget(lbl_fx_title)

        lbl_fx_desc = QLabel("Chaîne de traitement sonore :")
        fa_layout.addWidget(lbl_fx_desc)

        self.inserts_container = QVBoxLayout()
        self.inserts_container.setSpacing(4)
        fa_layout.addLayout(self.inserts_container)

        self.btn_add_insert = QPushButton("+ Ajouter un effet d'insert")
        self.btn_add_insert.setStyleSheet("""
            QPushButton {
                background-color: #202330;
                color: #a855f7;
                border: 1px dashed #a855f7;
                border-radius: 3px;
                padding: 4px;
                font-size: 11px;
            }
            QPushButton:hover { background-color: #2b3044; }
        """)
        self.btn_add_insert.clicked.connect(self._add_insert_slot)
        fa_layout.addWidget(self.btn_add_insert)

        self.content_layout.addWidget(self.frame_audio)

        # 4. SECTION MIXEUR (Volume / Pan / Mute / Solo)
        frame_mix = QFrame()
        frame_mix.setStyleSheet("background-color: #181a24; border: 1px solid #232634; border-radius: 4px;")
        fmix_layout = QVBoxLayout(frame_mix)
        fmix_layout.setContentsMargins(8, 8, 8, 8)
        fmix_layout.setSpacing(6)

        lbl_mix_title = QLabel("CONTRÔLES DE PISTE")
        lbl_mix_title.setObjectName("section_title")
        fmix_layout.addWidget(lbl_mix_title)

        # Ligne M / S / R
        row_msr = QHBoxLayout()
        row_msr.setSpacing(6)

        self.btn_mute = QPushButton("M")
        self.btn_mute.setCheckable(True)
        self.btn_mute.setFixedSize(24, 24)
        self.btn_mute.setStyleSheet("font-weight: bold;")
        self.btn_mute.toggled.connect(self._on_mute_toggled)
        row_msr.addWidget(self.btn_mute)

        self.btn_solo = QPushButton("S")
        self.btn_solo.setCheckable(True)
        self.btn_solo.setFixedSize(24, 24)
        self.btn_solo.setStyleSheet("font-weight: bold;")
        self.btn_solo.toggled.connect(self._on_solo_toggled)
        row_msr.addWidget(self.btn_solo)

        self.btn_rec = QPushButton("R")
        self.btn_rec.setCheckable(True)
        self.btn_rec.setFixedSize(24, 24)
        self.btn_rec.setStyleSheet("font-weight: bold;")
        self.btn_rec.toggled.connect(self._on_rec_toggled)
        row_msr.addWidget(self.btn_rec)

        fmix_layout.addLayout(row_msr)

        # Volume
        row_v = QHBoxLayout()
        row_v.addWidget(QLabel("Volume"))
        self.lbl_vol_val = QLabel("80%")
        self.lbl_vol_val.setAlignment(Qt.AlignRight)
        row_v.addWidget(self.lbl_vol_val)
        fmix_layout.addLayout(row_v)

        self.slider_vol = QSlider(Qt.Horizontal)
        self.slider_vol.setRange(0, 150)
        self.slider_vol.valueChanged.connect(self._on_vol_changed)
        fmix_layout.addWidget(self.slider_vol)

        # Panoramique
        row_p = QHBoxLayout()
        row_p.addWidget(QLabel("Pan"))
        self.lbl_pan_val = QLabel("Centre")
        self.lbl_pan_val.setAlignment(Qt.AlignRight)
        row_p.addWidget(self.lbl_pan_val)
        fmix_layout.addLayout(row_p)

        self.slider_pan = QSlider(Qt.Horizontal)
        self.slider_pan.setRange(-100, 100)
        self.slider_pan.valueChanged.connect(self._on_pan_changed)
        fmix_layout.addWidget(self.slider_pan)

        self.content_layout.addWidget(frame_mix)
        self.content_layout.addStretch()

        # Message aucun piste sélectionnée
        self.lbl_no_track = QLabel("Sélectionnez une piste pour inspecter ses réglages.")
        self.lbl_no_track.setAlignment(Qt.AlignCenter)
        self.lbl_no_track.setStyleSheet("color: #64748b; font-style: italic; padding: 20px 10px;")
        self.content_layout.addWidget(self.lbl_no_track)

    def set_track(self, track: Optional[Track]):
        self.current_track = track

        if track is None:
            self.card_info.setVisible(False)
            self.frame_midi.setVisible(False)
            self.frame_audio.setVisible(False)
            self.lbl_no_track.setVisible(True)
            return

        self.lbl_no_track.setVisible(False)
        self.card_info.setVisible(True)

        # 1. Infos générales
        self.lbl_icon.setText("🎹" if track.track_type == "midi" else "🔊")
        self.txt_name.setText(track.name)
        type_desc = "Piste MIDI (Instrument Virtuel)" if track.track_type == "midi" else "Piste Audio (Enregistrement/Samples)"
        self.lbl_type.setText(type_desc)
        self.lbl_type.setStyleSheet("font-size: 10px; color: " + ("#38bdf8;" if track.track_type == "midi" else "#10b981;"))

        # 2. Affichage conditionnel MIDI vs Audio
        if track.track_type == "midi":
            self.frame_midi.setVisible(True)
            self.frame_audio.setVisible(False)
            self._populate_instrument_combo()
        else:
            self.frame_midi.setVisible(False)
            self.frame_audio.setVisible(True)
            self._rebuild_insert_slots()

        # 3. Contrôles de mixage
        self.btn_mute.setChecked(track.muted)
        self.btn_solo.setChecked(track.soloed)
        self.btn_rec.setChecked(track.armed)

        self.slider_vol.setValue(int(track.volume * 100))
        self.lbl_vol_val.setText(f"{int(track.volume * 100)}%")

        self.slider_pan.setValue(int(track.pan * 100))
        pan_val = int(track.pan * 100)
        self.lbl_pan_val.setText("Centre" if pan_val == 0 else (f"G{abs(pan_val)}" if pan_val < 0 else f"D{pan_val}"))

    def _populate_instrument_combo(self):
        if not self.current_track or self.current_track.track_type != "midi":
            return

        self.combo_instrument.blockSignals(True)
        self.combo_instrument.clear()

        # Option 1 : Synthé interne
        self.combo_instrument.addItem("🎹 Synthé Interne NovaDAW", userData=None)

        # Option 2 : Instruments du Rack du projet
        rack_insts = [p for p in self.project.plugin_rack if p.get("plugin_type") == "instrument"]
        if rack_insts:
            for r in rack_insts:
                self.combo_instrument.addItem(f"🎹 {r['name']} (Rack)", userData=r["file_path"])

        # Option 3 : Tous les instruments compatibles scannés
        all_insts = global_plugin_manager.get_compatible_instruments()
        for inst in all_insts:
            # Éviter doublons déjà dans le rack
            if not any(r.get("file_path") == inst.file_path for r in rack_insts):
                self.combo_instrument.addItem(f"🎹 {inst.name}", userData=inst.file_path)

        # Option 4 : Charger manuellement un .vst3
        self.combo_instrument.addItem("➕ Charger un fichier .vst3...", userData="__ADD_FILE__")

        # Sélectionner le plugin actuel de la piste
        selected_idx = 0
        if self.current_track.plugin_path:
            for idx in range(self.combo_instrument.count()):
                if self.combo_instrument.itemData(idx) == self.current_track.plugin_path:
                    selected_idx = idx
                    break

        self.combo_instrument.setCurrentIndex(selected_idx)
        self.combo_instrument.blockSignals(False)
        self.btn_edit_instrument.setEnabled(bool(self.current_track.plugin_path))

    def _on_instrument_changed(self, index: int):
        if not self.current_track:
            return
        data = self.combo_instrument.currentData()

        if data == "__ADD_FILE__":
            file_path, _ = QFileDialog.getOpenFileName(
                self,
                "Sélectionner un instrument VST3",
                "",
                "Plugins VST3 (*.vst3);;Tous les fichiers (*.*)"
            )
            if file_path:
                info = global_plugin_manager.add_plugin_file(file_path)
                if info.is_compatible:
                    self.current_track.plugin_path = file_path
                    self.current_track.plugin_name = info.name
                    self._populate_instrument_combo()
                    self.track_modified.emit()
                else:
                    QMessageBox.warning(self, "Incompatible", f"Le plugin n'a pas pu être chargé :\n{info.error_message}")
                    self._populate_instrument_combo()
            else:
                self._populate_instrument_combo()
            return

        if data:
            self.current_track.plugin_path = data
            self.current_track.plugin_name = self.combo_instrument.currentText().replace("🎹 ", "").replace(" (Rack)", "")
            self.btn_edit_instrument.setEnabled(True)
        else:
            self.current_track.plugin_path = None
            self.current_track.plugin_name = None
            self.btn_edit_instrument.setEnabled(False)

        self.track_modified.emit()

    def _on_open_instrument_editor(self):
        if self.current_track and self.current_track.plugin_path:
            open_plugin_editor_gui(self.current_track.plugin_path, self)

    def _rebuild_insert_slots(self):
        """Reconstruit la liste des slots d'effets d'insert pour la piste audio"""
        while self.inserts_container.count() > 0:
            item = self.inserts_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self.current_track or self.current_track.track_type != "audio":
            return

        effects = global_plugin_manager.get_compatible_effects()
        if not hasattr(self.current_track, "insert_effects"):
            self.current_track.insert_effects = []

        # Afficher chaque slot configuré + 1 slot vide si la liste est vide
        slots_to_show = list(self.current_track.insert_effects)
        if len(slots_to_show) == 0:
            slots_to_show.append(None)

        for slot_idx, fx_path in enumerate(slots_to_show):
            row = QHBoxLayout()
            row.setSpacing(4)

            combo = QComboBox()
            combo.setFixedHeight(22)
            combo.addItem(f"Slot {slot_idx + 1} : [ Aucun effet ]", userData=None)
            selected_idx = 0
            for idx, fx in enumerate(effects, start=1):
                combo.addItem(f"🎛️ {fx.name}", userData=fx.file_path)
                if fx_path and fx.file_path == fx_path:
                    selected_idx = idx

            combo.setCurrentIndex(selected_idx)
            combo.currentIndexChanged.connect(lambda idx, s=slot_idx, c=combo: self._on_insert_changed(s, c))
            row.addWidget(combo, stretch=1)

            # Bouton [e] pour éditer l'effet
            btn_e = QPushButton("e")
            btn_e.setFixedSize(20, 20)
            btn_e.setStyleSheet("""
                QPushButton {
                    background-color: #1e2230;
                    color: #a855f7;
                    font-weight: bold;
                    border: 1px solid #a855f7;
                    border-radius: 3px;
                }
                QPushButton:hover { background-color: #a855f7; color: #ffffff; }
                QPushButton:disabled { border-color: #2b2e3e; color: #475569; background: transparent; }
            """)
            btn_e.setEnabled(bool(fx_path))
            if fx_path:
                btn_e.clicked.connect(lambda _, p=fx_path: open_plugin_editor_gui(p, self))
            row.addWidget(btn_e)

            # Bouton [✕] pour vider le slot
            btn_x = QPushButton("✕")
            btn_x.setFixedSize(18, 18)
            btn_x.setStyleSheet("background: transparent; border: none; color: #64748b; font-size: 10px;")
            btn_x.clicked.connect(lambda _, s=slot_idx: self._remove_insert_slot(s))
            row.addWidget(btn_x)

            slot_frame = QFrame()
            slot_frame.setLayout(row)
            self.inserts_container.addWidget(slot_frame)

    def _add_insert_slot(self):
        if self.current_track:
            if not hasattr(self.current_track, "insert_effects"):
                self.current_track.insert_effects = []
            self.current_track.insert_effects.append("")
            self._rebuild_insert_slots()

    def _on_insert_changed(self, slot_idx: int, combo: QComboBox):
        if not self.current_track:
            return
        path = combo.currentData()
        if not hasattr(self.current_track, "insert_effects"):
            self.current_track.insert_effects = []

        while len(self.current_track.insert_effects) <= slot_idx:
            self.current_track.insert_effects.append("")

        if path:
            self.current_track.insert_effects[slot_idx] = path
        else:
            self.current_track.insert_effects[slot_idx] = ""

        # Nettoyer les slots vides à la fin
        self.current_track.insert_effects = [p for p in self.current_track.insert_effects if p]
        self._rebuild_insert_slots()
        self.track_modified.emit()

    def _remove_insert_slot(self, slot_idx: int):
        if self.current_track and hasattr(self.current_track, "insert_effects"):
            if 0 <= slot_idx < len(self.current_track.insert_effects):
                self.current_track.insert_effects.pop(slot_idx)
                self._rebuild_insert_slots()
                self.track_modified.emit()

    def _on_name_changed(self):
        if self.current_track:
            name = self.txt_name.text().strip()
            if name:
                self.current_track.name = name
                self.track_modified.emit()

    def _on_mute_toggled(self, checked: bool):
        if self.current_track:
            self.current_track.muted = checked
            self.track_modified.emit()

    def _on_solo_toggled(self, checked: bool):
        if self.current_track:
            self.current_track.soloed = checked
            self.track_modified.emit()

    def _on_rec_toggled(self, checked: bool):
        if self.current_track:
            self.current_track.armed = checked
            self.track_modified.emit()

    def _on_vol_changed(self, val: int):
        if self.current_track:
            self.current_track.volume = val / 100.0
            self.lbl_vol_val.setText(f"{val}%")
            self.track_modified.emit()

    def _on_pan_changed(self, val: int):
        if self.current_track:
            self.current_track.pan = val / 100.0
            self.lbl_pan_val.setText("Centre" if val == 0 else (f"G{abs(val)}" if val < 0 else f"D{val}"))
            self.track_modified.emit()

    def _refresh_plugin_lists(self):
        if self.current_track:
            self.set_track(self.current_track)
