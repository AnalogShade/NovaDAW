"""
ui/inspector.py - Inspecteur de piste latéral inspiré de Cubase Pro
Affiche les réglages complets de la piste sélectionnée :
- Sortie Instrument VSTi et bouton [e] pour les pistes MIDI
- Chaîne d'effets d'insert et boutons [e] pour les pistes Audio
- Faders Volume, Panoramique, Mute, Solo, Record
"""
import os
from typing import Optional, Any
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSlider, QComboBox, QFrame, QScrollArea,
    QFileDialog, QMessageBox, QSizePolicy, QMenu
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor

from core.project import Track, Project
from core.plugin_manager import global_plugin_manager
from ui.plugin_dialogs import analyze_plugin_file, open_plugin_editor_gui, open_native_plugin_editor
from plugins.registry import plugin_registry, ensure_plugins_loaded
from ui.track_header import ResetableSlider, CompactNumEdit, TrackHeaderWidget
from ui.glow_effects import set_button_glow


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

        self.setMinimumWidth(200)
        self.setMaximumWidth(550)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
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
            QComboBox::drop-down {
                border: none;
                width: 16px;
            }
            QPushButton#btn_edit_plugin {
                background-color: #1b2230;
                color: #38bdf8;
                border: 1px solid #0284c7;
                border-radius: 4px;
                padding: 5px 8px;
                font-weight: bold;
                font-size: 11px;
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
        global_plugin_manager.editor_opened.connect(self._on_editor_state_changed)
        global_plugin_manager.editor_closed.connect(self._on_editor_state_changed)

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
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
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

        lbl_inst_title = QLabel("SORTIE MIDI — INSTRUMENT")
        lbl_inst_title.setObjectName("section_title")
        fm_layout.addWidget(lbl_inst_title)

        lbl_inst_desc = QLabel("Jouer cette piste avec :")
        fm_layout.addWidget(lbl_inst_desc)

        self.combo_instrument = QComboBox()
        self.combo_instrument.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.combo_instrument.setMinimumContentsLength(10)
        self.combo_instrument.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.combo_instrument.currentIndexChanged.connect(self._on_instrument_changed)
        fm_layout.addWidget(self.combo_instrument)

        self.btn_edit_instrument = QPushButton("🎹 Ouvrir Interface [e]")
        self.btn_edit_instrument.setObjectName("btn_edit_plugin")
        self.btn_edit_instrument.setToolTip("Ouvrir l'interface graphique de l'instrument virtuel [e]")
        self.btn_edit_instrument.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.btn_edit_instrument.clicked.connect(self._on_open_instrument_editor)
        fm_layout.addWidget(self.btn_edit_instrument)

        self.lbl_inst_status = QLabel()
        self.lbl_inst_status.setWordWrap(True)
        self.lbl_inst_status.setVisible(False)
        fm_layout.addWidget(self.lbl_inst_status)

        self.content_layout.addWidget(self.frame_midi)

        # 3. SECTION PILE DE PLUGINS & EFFETS (Pour toutes les pistes : MIDI, Audio, Master)
        self.frame_plugins = QFrame()
        self.frame_plugins.setStyleSheet("background-color: #181a24; border: 1px solid #232634; border-radius: 4px;")
        fp_layout = QVBoxLayout(self.frame_plugins)
        fp_layout.setContentsMargins(8, 8, 8, 8)
        fp_layout.setSpacing(6)

        lbl_fx_title = QLabel("PILE DE PLUGINS & EFFETS")
        lbl_fx_title.setObjectName("section_title")
        fp_layout.addWidget(lbl_fx_title)

        lbl_fx_desc = QLabel("Chaîne de traitement sonore :")
        fp_layout.addWidget(lbl_fx_desc)

        self.plugins_container = QVBoxLayout()
        self.plugins_container.setSpacing(4)
        fp_layout.addLayout(self.plugins_container)

        self.btn_add_plugin = QPushButton("+ Ajouter un Plugin...")
        self.btn_add_plugin.setStyleSheet("""
            QPushButton {
                background-color: #1a2233;
                color: #38bdf8;
                border: 1px dashed #38bdf8;
                border-radius: 3px;
                padding: 6px;
                font-size: 11px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #243048; color: #ffffff; }
        """)
        self.btn_add_plugin.clicked.connect(self._show_add_plugin_menu)
        fp_layout.addWidget(self.btn_add_plugin)

        self.content_layout.addWidget(self.frame_plugins)

        # 4. SECTION MIXEUR (Volume / Pan / Mute / Solo)
        self.frame_mix = QFrame()
        self.frame_mix.setObjectName("frame_mix")
        self.frame_mix.setStyleSheet("QFrame#frame_mix { background-color: #181a24; border: 1px solid #232634; border-radius: 4px; }")
        fmix_layout = QVBoxLayout(self.frame_mix)
        fmix_layout.setContentsMargins(8, 8, 8, 8)
        fmix_layout.setSpacing(6)

        lbl_mix_title = QLabel("CONTRÔLES DE PISTE")
        lbl_mix_title.setObjectName("section_title")
        fmix_layout.addWidget(lbl_mix_title)

        # Ligne M / S / R
        row_msr = QHBoxLayout()
        row_msr.setSpacing(6)

        self.btn_mute = QPushButton("M")
        self.btn_mute.setObjectName("btn_track_mute")
        self.btn_mute.setCheckable(True)
        self.btn_mute.setFixedSize(26, 24)
        self.btn_mute.setToolTip("Mute (Couper le son)")
        self.btn_mute.toggled.connect(self._on_mute_toggled)
        row_msr.addWidget(self.btn_mute)

        self.btn_solo = QPushButton("S")
        self.btn_solo.setObjectName("btn_track_solo")
        self.btn_solo.setCheckable(True)
        self.btn_solo.setFixedSize(26, 24)
        self.btn_solo.setToolTip("Solo (Écouter cette piste uniquement)")
        self.btn_solo.toggled.connect(self._on_solo_toggled)
        row_msr.addWidget(self.btn_solo)

        self.btn_rec = QPushButton("R")
        self.btn_rec.setObjectName("btn_track_rec")
        self.btn_rec.setCheckable(True)
        self.btn_rec.setFixedSize(26, 24)
        self.btn_rec.setToolTip("Armer pour l'enregistrement (R)")
        self.btn_rec.toggled.connect(self._on_rec_toggled)
        row_msr.addWidget(self.btn_rec)

        fmix_layout.addLayout(row_msr)

        # Volume
        row_v = QHBoxLayout()
        row_v.addWidget(QLabel("Volume"))
        self.txt_vol = CompactNumEdit("80%")
        self.txt_vol.setFixedWidth(46)
        self.txt_vol.setFixedHeight(18)
        self.txt_vol.setToolTip("Volume manuel (ex: 80, 100%, 0.8) - Entrée pour valider")
        self.txt_vol.editingFinished.connect(self._on_vol_text_edited)
        self.lbl_vol_val = self.txt_vol
        row_v.addWidget(self.txt_vol, alignment=Qt.AlignRight)
        fmix_layout.addLayout(row_v)

        self.slider_vol = ResetableSlider(Qt.Horizontal, default_value=80)
        self.slider_vol.setRange(0, 150)
        self.slider_vol.setToolTip("Volume (Double-cliquer pour réinitialiser à 80%)")
        self.slider_vol.valueChanged.connect(self._on_vol_changed)
        fmix_layout.addWidget(self.slider_vol)

        # Panoramique
        row_p = QHBoxLayout()
        row_p.addWidget(QLabel("Pan"))
        self.txt_pan = CompactNumEdit("Centre")
        self.txt_pan.setFixedWidth(46)
        self.txt_pan.setFixedHeight(18)
        self.txt_pan.setToolTip("Panoramique manuel (ex: C, L30, R40, -20) - Entrée pour valider")
        self.txt_pan.editingFinished.connect(self._on_pan_text_edited)
        self.lbl_pan_val = self.txt_pan
        row_p.addWidget(self.txt_pan, alignment=Qt.AlignRight)
        fmix_layout.addLayout(row_p)

        self.slider_pan = ResetableSlider(Qt.Horizontal, default_value=0)
        self.slider_pan.setRange(-100, 100)
        self.slider_pan.setToolTip("Panoramique (Double-cliquer pour réinitialiser au centre)")
        self.slider_pan.valueChanged.connect(self._on_pan_changed)
        fmix_layout.addWidget(self.slider_pan)

        self.content_layout.addWidget(self.frame_mix)
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
            self.frame_plugins.setVisible(False)
            if hasattr(self, "frame_mix"):
                self.frame_mix.setVisible(False)
            self.lbl_no_track.setVisible(True)
            return

        self.lbl_no_track.setVisible(False)
        self.card_info.setVisible(True)
        if hasattr(self, "frame_mix"):
            self.frame_mix.setVisible(True)

        # 1. Infos générales
        if track.track_type == "midi":
            icon = "🎹"
            type_desc = "Piste MIDI (Instrument Virtuel)"
            color_code = "#38bdf8;"
        elif track.track_type == "master":
            icon = "🎛️"
            type_desc = "Piste Master (Bus Stéréo)"
            color_code = "#ef4444;"
        else:
            icon = "🔊"
            type_desc = "Piste Audio (Enregistrement/Samples)"
            color_code = "#10b981;"

        self.lbl_icon.setText(icon)
        self.txt_name.setText(track.name)
        self.lbl_type.setText(type_desc)
        self.lbl_type.setStyleSheet("font-size: 10px; color: " + color_code)

        # 2. Affichage conditionnel MIDI vs Plugins
        self.frame_plugins.setVisible(True)
        if track.track_type == "midi":
            self.frame_midi.setVisible(True)
            self._populate_instrument_combo()
        else:
            self.frame_midi.setVisible(False)

        self._rebuild_plugin_stack()

        # 3. Contrôles de mixage
        self.sync_controls_from_track()

    def sync_controls_from_track(self):
        """Synchronise l'ensemble des boutons et faders avec l'objet Track courant."""
        if not self.current_track:
            return
        track = self.current_track
        self.btn_mute.blockSignals(True)
        self.btn_mute.setChecked(track.muted)
        self.btn_mute.blockSignals(False)
        set_button_glow(self.btn_mute, track.muted, "#f59e0b", blur_radius=14, alpha=220)

        self.btn_solo.blockSignals(True)
        self.btn_solo.setChecked(track.soloed)
        self.btn_solo.blockSignals(False)
        set_button_glow(self.btn_solo, track.soloed, "#facc15", blur_radius=14, alpha=220)

        self.btn_rec.blockSignals(True)
        self.btn_rec.setChecked(track.armed)
        self.btn_rec.blockSignals(False)
        set_button_glow(self.btn_rec, track.armed, "#ff2244", blur_radius=16, alpha=235)

        if hasattr(self, "slider_vol"):
            self.slider_vol.blockSignals(True)
            self.slider_vol.setValue(int(round(track.volume * 100)))
            self.slider_vol.blockSignals(False)
        if hasattr(self, "lbl_vol_val") and hasattr(self.lbl_vol_val, "hasFocus") and not self.lbl_vol_val.hasFocus():
            self.lbl_vol_val.setText(f"{int(round(track.volume * 100))}%")
        if hasattr(self, "slider_pan"):
            self.slider_pan.blockSignals(True)
            self.slider_pan.setValue(int(round(track.pan * 100)))
            self.slider_pan.blockSignals(False)
        if hasattr(self, "lbl_pan_val") and hasattr(self.lbl_pan_val, "hasFocus") and not self.lbl_pan_val.hasFocus():
            pan_val = int(round(track.pan * 100))
            self.lbl_pan_val.setText("Centre" if pan_val == 0 else (f"G{abs(pan_val)}" if pan_val < 0 else f"D{pan_val}"))
        if hasattr(self, "txt_name") and not self.txt_name.hasFocus():
            self.txt_name.setText(track.name)

    def _update_instrument_status_label(self):
        """Affiche un badge explicatif transparent sur le mode de rendu audio de l'instrument sélectionné"""
        if not self.current_track or self.current_track.track_type != "midi":
            self.lbl_inst_status.setVisible(False)
            return

        plugin_path = self.current_track.plugin_path
        plugin_name = self.current_track.plugin_name or ""
        is_multibus = any(k.lower() in plugin_name.lower() or k.lower() in (plugin_path or "").lower() for k in ["kontakt", "sampletank"]) if plugin_path else False

        if not plugin_path:
            self.lbl_inst_status.setStyleSheet("""
                QLabel {
                    background-color: #1a1e29;
                    color: #94a3b8;
                    border: 1px solid #2d3748;
                    border-radius: 4px;
                    padding: 6px;
                    font-size: 11px;
                }
            """)
            self.lbl_inst_status.setText(
                "ℹ️ <b>Synthétiseur Interne NovaDAW</b><br>"
                "Synthèse polyphonique intégrée avec enveloppe ADSR."
            )
            self.lbl_inst_status.setVisible(True)
        elif plugin_path == "novadaw.synth":
            self.lbl_inst_status.setStyleSheet("""
                QLabel {
                    background-color: #1a0f2e;
                    color: #00f0ff;
                    border: 1px solid #a855f7;
                    border-radius: 4px;
                    padding: 6px;
                    font-size: 11px;
                }
            """)
            self.lbl_inst_status.setText(
                "⚡ <b>NovaSynth Actif</b><br>"
                "Synthétiseur polyphonique modulaire multi-couches avec 10 sorties stéréo."
            )
            self.lbl_inst_status.setVisible(True)
        elif plugin_path == "novadaw.drum_machine":
            self.lbl_inst_status.setStyleSheet("""
                QLabel {
                    background-color: #0f291e;
                    color: #4ade80;
                    border: 1px solid #16a34a;
                    border-radius: 4px;
                    padding: 6px;
                    font-size: 11px;
                }
            """)
            self.lbl_inst_status.setText(
                "🥁 <b>Nova Drums VSTi Actif</b><br>"
                "Échantillonneur de batterie FP32 avec réverbération stéréo intégrée."
            )
            self.lbl_inst_status.setVisible(True)
        else:
            self.lbl_inst_status.setText(
                "Instrument du projet. Ouvrez son interface pour choisir un son ou une banque. "
                "Un plugin indisponible reste silencieux."
            )
            self.lbl_inst_status.setVisible(True)

    def _populate_instrument_combo(self):
        if not self.current_track or self.current_track.track_type != "midi":
            return

        self.combo_instrument.blockSignals(True)
        self.combo_instrument.clear()

        # Option 1 : Synthé interne
        self.combo_instrument.addItem("🎹 Synthé Interne NovaDAW", userData=None)

        # Option 2 : NovaSynth
        self.combo_instrument.addItem("⚡ NovaSynth (Synthétiseur Polyphonique)", userData="novadaw.synth")

        # Option 3 : Nova Drums VSTi
        self.combo_instrument.addItem("🥁 Nova Drums VSTi (Batterie IA)", userData="novadaw.drum_machine")

        def format_inst_label(name: str) -> str:
            return f"🎹 {name}"

        # Option 4 : Instruments du Rack du projet
        rack_insts = [p for p in self.project.plugin_rack if p.get("plugin_type") == "instrument"]
        if rack_insts:
            for r in rack_insts:
                if r.get("file_path") not in ("novadaw.drum_machine", "novadaw.synth"):
                    self.combo_instrument.addItem(format_inst_label(r['name']) + " (Rack)", userData=r["file_path"])

        # Option 5 : Tous les instruments compatibles scannés
        all_insts = global_plugin_manager.get_compatible_instruments()
        for inst in all_insts:
            # Éviter doublons déjà dans le rack
            if not any(r.get("file_path") == inst.file_path for r in rack_insts) and inst.file_path not in ("novadaw.drum_machine", "novadaw.synth"):
                self.combo_instrument.addItem(format_inst_label(inst.name), userData=inst.file_path)

        # Option 6 : Charger manuellement un .vst3
        self.combo_instrument.addItem("➕ Charger un fichier .vst3...", userData="__ADD_FILE__")

        # Sélectionner le plugin actuel de la piste
        selected_idx = 0
        if self.current_track.plugin_path:
            for idx in range(self.combo_instrument.count()):
                if self.combo_instrument.itemData(idx) == self.current_track.plugin_path:
                    selected_idx = idx
                    break

        if self.current_track.plugin_path and selected_idx == 0:
            self.combo_instrument.addItem(f"{self.current_track.plugin_name or self.current_track.plugin_path} — indisponible",
                                          self.current_track.plugin_path)
            selected_idx = self.combo_instrument.count() - 1
        self.combo_instrument.setCurrentIndex(selected_idx)
        self.combo_instrument.blockSignals(False)
        self.btn_edit_instrument.setEnabled(bool(self.current_track.plugin_path))
        self._update_instrument_status_label()

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
                info = analyze_plugin_file(file_path, self)
                if info.is_compatible and info.plugin_type == "instrument":
                    self.current_track.plugins = [p for p in self.current_track.plugins if not getattr(p, "is_instrument", False)]
                    self.project.add_rack_plugin(file_path, info.name, "instrument")
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

        if data and not data.startswith("novadaw."):
            from ui.plugin_dialogs import ensure_plugin_loaded
            key = f"track:{self.current_track.id}:instrument:{data}"
            if not ensure_plugin_loaded(data, self, key):
                self._populate_instrument_combo()
                return
        if data != self.current_track.plugin_path:
            self.current_track.plugins = [p for p in self.current_track.plugins
                                          if not getattr(p, "is_instrument", False)]
        if data == "novadaw.synth":
            self.current_track.plugin_path = "novadaw.synth"
            self.current_track.plugin_name = "NovaSynth"
            # S'assurer que le plugin est dans track.plugins
            has_synth = any(getattr(p, "plugin_type_id", None) == "novadaw.synth" for p in self.current_track.plugins)
            if not has_synth:
                ensure_plugins_loaded()
                sp = plugin_registry.create_plugin("novadaw.synth")
                if sp:
                    template = self.project.get_rack_native_plugin(data)
                    if template:
                        sp.set_state(template.get_state())
                    self.current_track.plugins.insert(0, sp)
            self.btn_edit_instrument.setEnabled(True)
            self._rebuild_plugin_stack()
        elif data == "novadaw.drum_machine":
            self.current_track.plugin_path = "novadaw.drum_machine"
            self.current_track.plugin_name = "Nova Drums VSTi"
            # S'assurer que le plugin est dans track.plugins
            has_drums = any(getattr(p, "plugin_type_id", None) == "novadaw.drum_machine" for p in self.current_track.plugins)
            if not has_drums:
                ensure_plugins_loaded()
                dp = plugin_registry.create_plugin("novadaw.drum_machine")
                if dp:
                    template = self.project.get_rack_native_plugin(data)
                    if template:
                        dp.set_state(template.get_state())
                    self.current_track.plugins.insert(0, dp)
            self.btn_edit_instrument.setEnabled(True)
            self._rebuild_plugin_stack()
        elif data:
            self.current_track.plugin_path = data
            raw_text = self.combo_instrument.currentText()
            cleaned = raw_text.replace("🎹 ", "").replace("⚡ ", "").replace("🥁 ", "").replace(" (Rack)", "")
            for badge in [" (Sampler Multi-bus) ⚠️", " (VST3 Direct) ✅"]:
                cleaned = cleaned.replace(badge, "")
            self.current_track.plugin_name = cleaned.strip()
            self.btn_edit_instrument.setEnabled(True)
        else:
            self.current_track.plugin_path = None
            self.current_track.plugin_name = None
            self.btn_edit_instrument.setEnabled(False)

        if self.current_track.plugin_path:
            self.project.add_rack_plugin(self.current_track.plugin_path,
                                         self.current_track.plugin_name, "instrument")
        self._rebuild_plugin_stack()
        self._update_instrument_status_label()
        self.track_modified.emit()

    def _on_editor_state_changed(self, file_path: str = ""):
        """Met à jour l'apparence des boutons d'édition selon si la fenêtre est ouverte ou fermée"""
        if self.current_track and self.current_track.track_type == "midi":
            is_open = False
            if self.current_track.plugin_path in ("novadaw.synth", "novadaw.drum_machine"):
                from ui.plugin_dialogs import _open_native_editors
                for p in self.current_track.plugins:
                    if getattr(p, "plugin_type_id", None) == self.current_track.plugin_path:
                        inst_id = getattr(p, "instance_id", None)
                        if inst_id and inst_id in _open_native_editors:
                            is_open = _open_native_editors[inst_id].isVisible()
                        break
            elif self.current_track.plugin_path:
                is_open = global_plugin_manager.is_editor_open(f"track:{self.current_track.id}:instrument:{self.current_track.plugin_path}")
            
            if is_open:
                self.btn_edit_instrument.setText("🎹 Fermer Interface Plugin [e]")
                self.btn_edit_instrument.setStyleSheet("""
                    QPushButton {
                        background-color: #0284c7;
                        color: #ffffff;
                        font-weight: bold;
                        border: 1px solid #38bdf8;
                        border-radius: 4px;
                        padding: 6px;
                    }
                    QPushButton:hover { background-color: #0369a1; }
                """)
                self.btn_edit_instrument.setToolTip("L'interface du plugin est ouverte (Cliquer pour fermer)")
            else:
                self.btn_edit_instrument.setText("🎹 Ouvrir Interface Plugin [e]")
                self.btn_edit_instrument.setStyleSheet("")
                self.btn_edit_instrument.setToolTip("Éditer l'instrument (Ouvrir l'interface)")
        else:
            self._rebuild_plugin_stack()

    def _on_open_instrument_editor(self):
        if not self.current_track:
            return
        if self.current_track.plugin_path in ("novadaw.drum_machine", "novadaw.synth"):
            native_id = self.current_track.plugin_path
            native_plugin = None
            for p in self.current_track.plugins:
                if getattr(p, "plugin_type_id", None) == native_id:
                    native_plugin = p
                    break
            if not native_plugin:
                ensure_plugins_loaded()
                native_plugin = plugin_registry.create_plugin(native_id)
                if native_plugin:
                    self.current_track.plugins.insert(0, native_plugin)
            if native_plugin:
                open_native_plugin_editor(native_plugin, self)
        elif self.current_track.plugin_path:
            open_plugin_editor_gui(self.current_track.plugin_path, self, f"track:{self.current_track.id}:instrument:{self.current_track.plugin_path}")

    def _show_add_plugin_menu(self):
        """Affiche le menu de sélection de plugin à empiler sur la piste"""
        if not self.current_track:
            return
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #1a1e29;
                color: #f1f5f9;
                border: 1px solid #2d3748;
                font-size: 11px;
                padding: 4px;
            }
            QMenu::item {
                padding: 6px 20px 6px 10px;
                border-radius: 3px;
            }
            QMenu::item:selected {
                background-color: #0284c7;
                color: #ffffff;
            }
            QMenu::separator {
                height: 1px;
                background-color: #2d3748;
                margin: 4px 6px;
            }
        """)

        # 1. Plugins NovaDAW intégrés
        act_synth = menu.addAction("⚡ NovaSynth (Synthétiseur Polyphonique)")
        act_synth.triggered.connect(self._add_synth_plugin)

        act_drums = menu.addAction("🥁 Nova Drums (Instrument VSTi Batterie)")
        act_drums.triggered.connect(self._add_drum_plugin)

        act_eq = menu.addAction("📊 Égaliseur Paramétrique (3/10/12/24 bandes)")
        act_eq.triggered.connect(self._add_equalizer_plugin)

        act_comp = menu.addAction("🗜️ Compresseur Dynamique (Studio)")
        act_comp.triggered.connect(self._add_compressor_plugin)

        act_mix = menu.addAction("🎛️ Mixeur de Pistes")
        act_mix.triggered.connect(self._add_mixer_plugin)

        menu.addSeparator()

        # 2. Plugins VST3 externes scannés
        effects = global_plugin_manager.get_compatible_effects()
        if effects:
            menu_vst = menu.addMenu("📁 Effets VST3 externes...")
            menu_vst.setStyleSheet(menu.styleSheet())
            for fx in effects:
                act_fx = menu_vst.addAction(f"🎛️ {fx.name}")
                act_fx.triggered.connect(lambda _, path=fx.file_path: self._add_vst_plugin(path))

        act_browse = menu.addAction("➕ Parcourir un fichier .vst3...")
        act_browse.triggered.connect(self._browse_vst_insert)

        menu.exec(self.btn_add_plugin.mapToGlobal(self.btn_add_plugin.rect().bottomLeft()))

    def _add_synth_plugin(self):
        if not self.current_track:
            return
        ensure_plugins_loaded()
        synth = plugin_registry.create_plugin("novadaw.synth")
        if synth:
            self.current_track.add_plugin(synth)
            if self.current_track.track_type == "midi":
                self.current_track.plugin_path = "novadaw.synth"
                self.current_track.plugin_name = "NovaSynth"
                self._populate_instrument_combo()
            self._rebuild_plugin_stack()
            self.track_modified.emit()
            open_native_plugin_editor(synth, self)

    def _add_drum_plugin(self):
        if not self.current_track:
            return
        ensure_plugins_loaded()
        drums = plugin_registry.create_plugin("novadaw.drum_machine")
        if drums:
            self.current_track.add_plugin(drums)
            if self.current_track.track_type == "midi":
                self.current_track.plugin_path = "novadaw.drum_machine"
                self.current_track.plugin_name = "Nova Drums VSTi"
                self._populate_instrument_combo()
            self._rebuild_plugin_stack()
            self.track_modified.emit()
            open_native_plugin_editor(drums, self)

    def _add_equalizer_plugin(self):
        if not self.current_track:
            return
        ensure_plugins_loaded()
        eq = plugin_registry.create_plugin("novadaw.equalizer")
        if eq:
            self.current_track.add_plugin(eq)
            self._rebuild_plugin_stack()
            self.track_modified.emit()
            open_native_plugin_editor(eq, self)

    def _add_compressor_plugin(self):
        if not self.current_track:
            return
        ensure_plugins_loaded()
        comp = plugin_registry.create_plugin("novadaw.compressor")
        if comp:
            self.current_track.add_plugin(comp)
            self._rebuild_plugin_stack()
            self.track_modified.emit()
            open_native_plugin_editor(comp, self)

    def _add_mixer_plugin(self):
        if not self.current_track:
            return
        ensure_plugins_loaded()
        mixer = plugin_registry.create_plugin("novadaw.mixer")
        if mixer:
            if hasattr(mixer, "set_project"):
                mixer.set_project(self.project)
            self.current_track.add_plugin(mixer)
            self._rebuild_plugin_stack()
            self.track_modified.emit()
            open_native_plugin_editor(mixer, self)

    def _add_vst_plugin(self, file_path: str):
        if not self.current_track:
            return
        if not hasattr(self.current_track, "insert_effects"):
            self.current_track.insert_effects = []
        if file_path in self.current_track.insert_effects:
            return
        from ui.plugin_dialogs import ensure_plugin_loaded
        if not ensure_plugin_loaded(file_path, self, f"track:{self.current_track.id}:effect:{file_path}"):
            return
        self.current_track.insert_effects.append(file_path)
        info = next((p for p in global_plugin_manager.plugins if p.file_path == file_path), None)
        self.project.add_rack_plugin(file_path, info.name if info else os.path.basename(file_path), "effect")
        self._rebuild_plugin_stack()
        self.track_modified.emit()

    def _browse_vst_insert(self):
        if not self.current_track:
            return
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Sélectionner un effet VST3",
            "",
            "Plugins VST3 (*.vst3);;Tous les fichiers (*.*)"
        )
        if file_path:
            info = analyze_plugin_file(file_path, self)
            if info.is_compatible and info.plugin_type == "effect":
                self._add_vst_plugin(file_path)
            else:
                QMessageBox.warning(self, "Incompatible", f"Le plugin n'a pas pu être chargé :\n{info.error_message}")

    def _rebuild_plugin_stack(self):
        """Reconstruit visuellement la pile d'effets/plugins de la piste active"""
        while self.plugins_container.count() > 0:
            item = self.plugins_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self.current_track:
            return

        if not hasattr(self.current_track, "plugins"):
            self.current_track.plugins = []

        total_plugins = len(self.current_track.plugins)

        # 1. Rendu des plugins natifs empilés
        for p_idx, plugin in enumerate(self.current_track.plugins):
            card = QFrame()
            card.setStyleSheet("""
                QFrame {
                    background-color: #1a1d29;
                    border: 1px solid #2b3044;
                    border-radius: 4px;
                }
                QFrame:hover {
                    border-color: #38bdf8;
                }
            """)
            card_layout = QHBoxLayout(card)
            card_layout.setContentsMargins(5, 3, 5, 3)
            card_layout.setSpacing(3)

            # Numéro et Nom du plugin
            lbl_title = QLabel(f"{p_idx + 1}. {plugin.icon} {plugin.name}")
            lbl_title.setStyleSheet("font-size: 10px; font-weight: bold; color: #f1f5f9;")
            lbl_title.setToolTip(getattr(plugin, "description", plugin.name))
            card_layout.addWidget(lbl_title, stretch=1)

            # Bouton On / Bypass
            btn_bypass = QPushButton("On" if plugin.enabled else "Bypass")
            btn_bypass.setCheckable(True)
            btn_bypass.setChecked(plugin.enabled)
            btn_bypass.setFixedSize(36, 18)
            btn_bypass.setStyleSheet("""
                QPushButton {
                    background-color: #064e3b;
                    color: #34d399;
                    font-size: 9px;
                    font-weight: bold;
                    border: 1px solid #059669;
                    border-radius: 2px;
                }
                QPushButton:!checked {
                    background-color: #262626;
                    color: #737373;
                    border-color: #404040;
                }
            """)
            btn_bypass.toggled.connect(lambda chk, p=plugin, b=btn_bypass: self._on_plugin_bypass_toggled(chk, p, b))
            card_layout.addWidget(btn_bypass)

            # Bouton [e] d'ouverture d'interface graphique
            btn_e = QPushButton("e")
            btn_e.setFixedSize(18, 18)
            btn_e.setToolTip("Ouvrir l'interface graphique [e]")
            btn_e.setStyleSheet("""
                QPushButton {
                    background-color: #1e293b;
                    color: #38bdf8;
                    font-weight: bold;
                    font-size: 11px;
                    border: 1px solid #0284c7;
                    border-radius: 3px;
                }
                QPushButton:hover {
                    background-color: #0284c7;
                    color: #ffffff;
                }
            """)
            btn_e.clicked.connect(lambda _, p=plugin: open_native_plugin_editor(p, self))
            card_layout.addWidget(btn_e)

            # Bouton Monter [▲]
            btn_up = QPushButton("▲")
            btn_up.setFixedSize(18, 18)
            btn_up.setEnabled(p_idx > 0)
            btn_up.setStyleSheet("background: transparent; border: none; color: #94a3b8; font-size: 9px; padding: 0px;")
            btn_up.clicked.connect(lambda _, idx=p_idx: self._move_plugin_up(idx))
            card_layout.addWidget(btn_up)

            # Bouton Descendre [▼]
            btn_down = QPushButton("▼")
            btn_down.setFixedSize(18, 18)
            btn_down.setEnabled(p_idx < total_plugins - 1)
            btn_down.setStyleSheet("background: transparent; border: none; color: #94a3b8; font-size: 9px; padding: 0px;")
            btn_down.clicked.connect(lambda _, idx=p_idx: self._move_plugin_down(idx))
            card_layout.addWidget(btn_down)

            # Bouton Supprimer [✕]
            btn_del = QPushButton("✕")
            btn_del.setFixedSize(18, 18)
            btn_del.setStyleSheet("background: transparent; border: none; color: #f87171; font-size: 11px; padding: 0px;")
            btn_del.setToolTip("Supprimer ce plugin de la pile")
            btn_del.clicked.connect(lambda _, p=plugin: self._remove_plugin(p))
            card_layout.addWidget(btn_del)

            self.plugins_container.addWidget(card)

        # 2. Rendu des effets VST3 de la piste
        if hasattr(self.current_track, "insert_effects") and self.current_track.insert_effects:
            for s_idx, fx_path in enumerate(self.current_track.insert_effects):
                fx_name = os.path.splitext(os.path.basename(fx_path))[0]
                card = QFrame()
                card.setStyleSheet("background-color: #161822; border: 1px solid #3b3054; border-radius: 4px;")
                row = QHBoxLayout(card)
                row.setContentsMargins(5, 3, 5, 3)
                row.setSpacing(3)

                lbl = QLabel(f"VST: 🎛️ {fx_name}")
                lbl.setStyleSheet("font-size: 10px; color: #c084fc; font-weight: bold;")
                row.addWidget(lbl, stretch=1)

                btn_e = QPushButton("e")
                btn_e.setFixedSize(18, 18)
                btn_e.setStyleSheet("background-color: #2e1065; color: #c084fc; font-weight: bold; border: 1px solid #7e22ce; border-radius: 3px;")
                btn_e.clicked.connect(lambda _, p=fx_path, key=f"track:{self.current_track.id}:effect:{fx_path}": open_plugin_editor_gui(p, self, key))
                row.addWidget(btn_e)

                btn_del = QPushButton("✕")
                btn_del.setFixedSize(16, 18)
                btn_del.setStyleSheet("background: transparent; border: none; color: #64748b; font-size: 10px;")
                btn_del.clicked.connect(lambda _, idx=s_idx: self._remove_vst_slot(idx))
                row.addWidget(btn_del)

                self.plugins_container.addWidget(card)

        if total_plugins == 0 and (not hasattr(self.current_track, "insert_effects") or not self.current_track.insert_effects):
            lbl_empty = QLabel("Aucun plugin dans la pile.\nCliquez sur '+ Ajouter un Plugin...'")
            lbl_empty.setAlignment(Qt.AlignCenter)
            lbl_empty.setStyleSheet("color: #64748b; font-size: 10px; font-style: italic; padding: 10px 0;")
            self.plugins_container.addWidget(lbl_empty)

    def _on_plugin_bypass_toggled(self, chk: bool, plugin: Any, btn: QPushButton):
        plugin.enabled = chk
        btn.setText("On" if chk else "Bypass")
        self.track_modified.emit()

    def _move_plugin_up(self, idx: int):
        if self.current_track and self.current_track.move_plugin(idx, idx - 1):
            self._rebuild_plugin_stack()
            self.track_modified.emit()

    def _move_plugin_down(self, idx: int):
        if self.current_track and self.current_track.move_plugin(idx, idx + 1):
            self._rebuild_plugin_stack()
            self.track_modified.emit()

    def _remove_plugin(self, plugin: Any):
        if self.current_track:
            self.current_track.remove_plugin(plugin.instance_id)
            self._rebuild_plugin_stack()
            self.track_modified.emit()

    def _remove_vst_slot(self, slot_idx: int):
        if self.current_track and hasattr(self.current_track, "insert_effects"):
            if 0 <= slot_idx < len(self.current_track.insert_effects):
                self.current_track.insert_effects.pop(slot_idx)
                self._rebuild_plugin_stack()
                self.track_modified.emit()

    def _on_name_changed(self):
        if self.current_track:
            name = self.txt_name.text().strip()
            if name:
                self.current_track.name = name
                self.track_modified.emit()

    def _on_mute_toggled(self, checked: bool):
        set_button_glow(self.btn_mute, checked, "#f59e0b", blur_radius=14, alpha=220)
        if self.current_track:
            self.current_track.muted = checked
            self.track_modified.emit()

    def _on_solo_toggled(self, checked: bool):
        set_button_glow(self.btn_solo, checked, "#facc15", blur_radius=14, alpha=220)
        if self.current_track:
            self.current_track.soloed = checked
            self.track_modified.emit()

    def _on_rec_toggled(self, checked: bool):
        set_button_glow(self.btn_rec, checked, "#ff2244", blur_radius=16, alpha=235)
        if self.current_track:
            self.current_track.armed = checked
            self.track_modified.emit()

    def _on_vol_changed(self, val: int):
        if self.current_track:
            self.current_track.volume = val / 100.0
            self.slider_vol.setToolTip(f"Volume : {val}% (Double-cliquer pour réinitialiser à 80%)")
            if hasattr(self, "txt_vol") and not self.txt_vol.hasFocus():
                self.txt_vol.setText(f"{val}%")
            self.track_modified.emit()

    def _on_vol_text_edited(self):
        if self.current_track and hasattr(self, "txt_vol"):
            parsed = TrackHeaderWidget._parse_vol_text(self.txt_vol.text())
            if parsed is not None:
                self.slider_vol.setValue(parsed)
                self.txt_vol.setText(f"{parsed}%")
            else:
                self.txt_vol.setText(f"{self.slider_vol.value()}%")

    def _on_pan_changed(self, val: int):
        if self.current_track:
            self.current_track.pan = val / 100.0
            pan_str = "Centre" if val == 0 else (f"L{abs(val)}" if val < 0 else f"R{val}")
            self.slider_pan.setToolTip(f"Pan : {pan_str} (Double-cliquer pour réinitialiser au centre)")
            if hasattr(self, "txt_pan") and not self.txt_pan.hasFocus():
                self.txt_pan.setText(pan_str)
            self.track_modified.emit()

    def _on_pan_text_edited(self):
        if self.current_track and hasattr(self, "txt_pan"):
            parsed = TrackHeaderWidget._parse_pan_text(self.txt_pan.text())
            if parsed is not None:
                self.slider_pan.setValue(parsed)
                pan_str = "Centre" if parsed == 0 else (f"L{abs(parsed)}" if parsed < 0 else f"R{parsed}")
                self.txt_pan.setText(pan_str)
            else:
                v = self.slider_pan.value()
                pan_str = "Centre" if v == 0 else (f"L{abs(v)}" if v < 0 else f"R{v}")
                self.txt_pan.setText(pan_str)

    def _refresh_plugin_lists(self):
        if self.current_track:
            self.set_track(self.current_track)
