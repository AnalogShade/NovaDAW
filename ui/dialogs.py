"""
ui/dialogs.py - Boîtes de dialogue pour le DAW (Ajout de piste, etc.)
"""
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QRadioButton, QButtonGroup, QColorDialog, QFrame, QComboBox
)
from PySide6.QtGui import QColor
from PySide6.QtCore import Qt
from core.plugin_manager import global_plugin_manager


class AddTrackDialog(QDialog):
    """Dialogue de création d'une nouvelle piste (MIDI ou Audio)"""
    COLOR_PRESETS = [
        "#38bdf8",  # Bleu ciel
        "#a855f7",  # Violet
        "#10b981",  # Vert émeraude
        "#f59e0b",  # Ambre
        "#ef4444",  # Rouge
        "#ec4899",  # Rose
        "#06b6d4",  # Cyan
        "#84cc16",  # Lime
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Ajouter une nouvelle piste")
        self.setMinimumWidth(440)
        self.resize(460, 420)
        self.setSizeGripEnabled(True)
        self.setStyleSheet("""
            QDialog {
                background-color: #1a1c24;
                color: #e2e8f0;
            }
            QLabel {
                color: #94a3b8;
                font-size: 12px;
            }
            QRadioButton {
                color: #f1f5f9;
                font-size: 12px;
                spacing: 8px;
            }
            QLineEdit {
                background-color: #121318;
                color: #ffffff;
                border: 1px solid #282a36;
                border-radius: 4px;
                padding: 6px 10px;
                font-size: 12px;
            }
            QLineEdit:focus {
                border-color: #38bdf8;
            }
            QPushButton {
                background-color: #262936;
                color: #e2e8f0;
                border: 1px solid #3b3f52;
                border-radius: 4px;
                padding: 6px 16px;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #333748;
                border-color: #38bdf8;
            }
            QPushButton#btn_add_track {
                background-color: #0284c7;
                color: #ffffff;
                font-weight: bold;
                border: none;
            }
            QPushButton#btn_add_track:hover {
                background-color: #0369a1;
            }
        """)

        self.selected_color = self.COLOR_PRESETS[0]

        layout = QVBoxLayout(self)
        layout.setSpacing(14)

        # 1. Sélection du type de piste
        type_label = QLabel("Type de piste :")
        type_label.setStyleSheet("font-weight: bold; color: #94a3b8;")
        layout.addWidget(type_label)

        type_layout = QHBoxLayout()
        self.rb_midi = QRadioButton("🎹 Piste MIDI (Instrument Virtuel)")
        self.rb_midi.setChecked(True)
        self.rb_audio = QRadioButton("🔊 Piste Audio (Enregistrement / Samples)")

        self.type_group = QButtonGroup(self)
        self.type_group.addButton(self.rb_midi)
        self.type_group.addButton(self.rb_audio)

        type_layout.addWidget(self.rb_midi)
        layout.addLayout(type_layout)
        layout.addWidget(self.rb_audio)

        # 2. Sélecteur d'instrument (pour piste MIDI)
        self.lbl_inst = QLabel("Instrument de sortie :")
        self.lbl_inst.setStyleSheet("font-weight: bold; color: #94a3b8;")
        layout.addWidget(self.lbl_inst)

        self.combo_inst = QComboBox()
        self.combo_inst.setStyleSheet("""
            QComboBox {
                background-color: #121318;
                color: #ffffff;
                border: 1px solid #282a36;
                border-radius: 4px;
                padding: 6px;
                font-size: 12px;
            }
            QComboBox QAbstractItemView {
                background-color: #1a1c24;
                color: #ffffff;
                selection-background-color: #0284c7;
            }
        """)
        self.combo_inst.addItem("🎹 Synthé Polyphonique NovaDAW (Défaut)", userData=None)
        self.combo_inst.addItem("🥁 Nova Drums VSTi (Batterie IA)", userData="novadaw.drum_machine")
        for inst in global_plugin_manager.get_compatible_instruments():
            is_multibus = any(k.lower() in inst.name.lower() for k in ["kontakt", "sampletank"])
            tag = " (Sampler Multi-bus) ⚠️" if is_multibus else " (VST3 Direct) ✅"
            self.combo_inst.addItem(f"🎹 {inst.name}{tag}", userData=inst.file_path)
        self.combo_inst.currentIndexChanged.connect(self._on_instrument_selected)
        layout.addWidget(self.combo_inst)

        # 3. Nom de la piste
        name_label = QLabel("Nom de la piste :")
        name_label.setStyleSheet("font-weight: bold; color: #94a3b8;")
        layout.addWidget(name_label)

        self.txt_name = QLineEdit()
        self.txt_name.setText("Synth Lead")
        layout.addWidget(self.txt_name)

        # Changer automatiquement le nom par défaut si on change de type
        self.rb_midi.toggled.connect(self._on_type_changed)

        # 3. Choix de la couleur
        color_label = QLabel("Couleur :")
        color_label.setStyleSheet("font-weight: bold; color: #94a3b8;")
        layout.addWidget(color_label)

        colors_layout = QHBoxLayout()
        self.color_buttons = []
        for color_hex in self.COLOR_PRESETS:
            btn = QPushButton()
            btn.setFixedSize(26, 26)
            btn.setStyleSheet(f"background-color: {color_hex}; border-radius: 13px; border: 2px solid transparent;")
            btn.clicked.connect(lambda _, c=color_hex, b=btn: self._select_color(c, b))
            colors_layout.addWidget(btn)
            self.color_buttons.append((btn, color_hex))
        layout.addLayout(colors_layout)

        # Sélectionner la première couleur visuellement
        self._select_color(self.COLOR_PRESETS[0], self.color_buttons[0][0])

        # Ligne de séparation
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color: #2b2e3b;")
        layout.addWidget(sep)

        # Boutons Valider / Annuler
        btn_layout = QHBoxLayout()
        self.btn_cancel = QPushButton("Annuler")
        self.btn_cancel.clicked.connect(self.reject)

        self.btn_ok = QPushButton("Créer la piste")
        self.btn_ok.setObjectName("btn_add_track")
        self.btn_ok.clicked.connect(self.accept)

        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_cancel)
        btn_layout.addWidget(self.btn_ok)
        layout.addLayout(btn_layout)

    def _on_type_changed(self, is_midi: bool):
        self.lbl_inst.setVisible(is_midi)
        self.combo_inst.setVisible(is_midi)
        if is_midi:
            if not self.txt_name.text() or "Audio" in self.txt_name.text():
                self.txt_name.setText("Synth Lead")
        else:
            if not self.txt_name.text() or "Synth" in self.txt_name.text():
                self.txt_name.setText("Piste Audio")

    def _on_instrument_selected(self, index: int):
        file_path = self.combo_inst.currentData()
        if file_path:
            inst_name = self.combo_inst.currentText().replace("🎹 ", "").replace("🥁 ", "")
            for badge in [" (Sampler Multi-bus) ⚠️", " (VST3 Direct) ✅", " (VST3)"]:
                inst_name = inst_name.replace(badge, "")
            inst_name = inst_name.strip()
            if not self.txt_name.text() or self.txt_name.text() in ["Synth Lead", "Piste MIDI"]:
                self.txt_name.setText(inst_name)

    def _select_color(self, hex_code: str, target_btn: QPushButton):
        self.selected_color = hex_code
        for btn, _ in self.color_buttons:
            btn.setStyleSheet(f"background-color: {btn.palette().button().color().name()}; border-radius: 13px; border: 2px solid transparent;")
        target_btn.setStyleSheet(f"background-color: {hex_code}; border-radius: 13px; border: 2px solid #ffffff;")

    def get_track_data(self) -> dict:
        track_type = "midi" if self.rb_midi.isChecked() else "audio"
        name = self.txt_name.text().strip() or ("Piste MIDI" if track_type == "midi" else "Piste Audio")
        plugin_path = self.combo_inst.currentData() if track_type == "midi" else None
        plugin_name = None
        if track_type == "midi" and plugin_path:
            raw = self.combo_inst.currentText().replace("🎹 ", "").replace("🥁 ", "")
            for badge in [" (Sampler Multi-bus) ⚠️", " (VST3 Direct) ✅", " (VST3)"]:
                raw = raw.replace(badge, "")
            plugin_name = raw.strip()

        return {
            "name": name,
            "track_type": track_type,
            "color": self.selected_color,
            "plugin_path": plugin_path,
            "plugin_name": plugin_name,
        }
