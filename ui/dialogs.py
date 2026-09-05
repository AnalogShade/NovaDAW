"""
ui/dialogs.py - Boîtes de dialogue pour le DAW (Ajout de piste, etc.)
"""
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QRadioButton, QButtonGroup, QColorDialog, QFrame
)
from PySide6.QtGui import QColor
from PySide6.QtCore import Qt


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
        self.setFixedWidth(380)
        self.setStyleSheet("background-color: #1a1c24;")

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

        # 2. Nom de la piste
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
        if is_midi:
            if not self.txt_name.text() or "Audio" in self.txt_name.text():
                self.txt_name.setText("Synth Lead")
        else:
            if not self.txt_name.text() or "Synth" in self.txt_name.text():
                self.txt_name.setText("Piste Audio")

    def _select_color(self, hex_code: str, target_btn: QPushButton):
        self.selected_color = hex_code
        for btn, _ in self.color_buttons:
            btn.setStyleSheet(f"background-color: {btn.palette().button().color().name()}; border-radius: 13px; border: 2px solid transparent;")
        target_btn.setStyleSheet(f"background-color: {hex_code}; border-radius: 13px; border: 2px solid #ffffff;")

    def get_track_data(self) -> dict:
        track_type = "midi" if self.rb_midi.isChecked() else "audio"
        name = self.txt_name.text().strip() or ("Piste MIDI" if track_type == "midi" else "Piste Audio")
        return {
            "name": name,
            "track_type": track_type,
            "color": self.selected_color
        }
