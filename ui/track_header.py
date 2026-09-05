"""
ui/track_header.py - En-tête de piste individuel (Mute, Solo, Arm, Volume, Pan, Couleur)
"""
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
    QSlider, QLineEdit, QFrame, QMenu
)
from PySide6.QtCore import Qt, Signal
from core.project import Track


class TrackHeaderWidget(QFrame):
    track_modified = Signal()
    track_selected = Signal(str)  # track_id
    track_deleted = Signal(str)   # track_id

    def __init__(self, track: Track, parent=None):
        super().__init__(parent)
        self.track = track
        self.is_selected = False

        self.setFixedHeight(76)
        self.setObjectName("track_header")
        self.setProperty("class", "track_header")
        self.setAttribute(Qt.WA_StyledBackground, True)

        self._init_ui()
        self.update_style()

    def _init_ui(self):
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 8, 0)
        main_layout.setSpacing(6)

        # 1. Bande latérale de couleur
        self.color_bar = QFrame()
        self.color_bar.setFixedWidth(6)
        self.color_bar.setStyleSheet(f"background-color: {self.track.color}; border-top-left-radius: 4px; border-bottom-left-radius: 4px;")
        main_layout.addWidget(self.color_bar)

        # Contenu principal
        content_layout = QVBoxLayout()
        content_layout.setContentsMargins(6, 6, 4, 6)
        content_layout.setSpacing(4)

        # Ligne 1 : Icône + Nom + Boutons M/S/R + Bouton Supprimer
        row1 = QHBoxLayout()
        row1.setSpacing(4)

        # Icône du type
        icon = "🎹" if self.track.track_type == "midi" else "🔊"
        self.lbl_icon = QLabel(icon)
        self.lbl_icon.setStyleSheet("font-size: 13px;")
        row1.addWidget(self.lbl_icon)

        # Nom éditable
        self.txt_name = QLineEdit(self.track.name)
        self.txt_name.setStyleSheet("background: transparent; border: none; font-weight: bold; font-size: 12px;")
        self.txt_name.editingFinished.connect(self._on_name_changed)
        row1.addWidget(self.txt_name, stretch=1)

        # Bouton Mute
        self.btn_mute = QPushButton("M")
        self.btn_mute.setProperty("class", "btn_mute")
        self.btn_mute.setCheckable(True)
        self.btn_mute.setChecked(self.track.muted)
        self.btn_mute.setToolTip("Mute (Couper le son)")
        self.btn_mute.toggled.connect(self._on_mute_toggled)
        row1.addWidget(self.btn_mute)

        # Bouton Solo
        self.btn_solo = QPushButton("S")
        self.btn_solo.setProperty("class", "btn_solo")
        self.btn_solo.setCheckable(True)
        self.btn_solo.setChecked(self.track.soloed)
        self.btn_solo.setToolTip("Solo (Écouter cette piste uniquement)")
        self.btn_solo.toggled.connect(self._on_solo_toggled)
        row1.addWidget(self.btn_solo)

        # Bouton Record Arm
        self.btn_rec = QPushButton("R")
        self.btn_rec.setProperty("class", "btn_rec")
        self.btn_rec.setCheckable(True)
        self.btn_rec.setChecked(self.track.armed)
        self.btn_rec.setToolTip("Armer pour l'enregistrement")
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

        # Ligne 2 : Fader de Volume & Panoramique
        row2 = QHBoxLayout()
        row2.setSpacing(6)

        lbl_v = QLabel("VOL")
        lbl_v.setStyleSheet("font-size: 8px; color: #64748b; font-weight: bold;")
        row2.addWidget(lbl_v)

        self.slider_vol = QSlider(Qt.Horizontal)
        self.slider_vol.setRange(0, 150)
        self.slider_vol.setValue(int(self.track.volume * 100))
        self.slider_vol.setFixedHeight(18)
        self.slider_vol.setToolTip(f"Volume : {int(self.track.volume * 100)}%")
        self.slider_vol.valueChanged.connect(self._on_vol_changed)
        row2.addWidget(self.slider_vol, stretch=2)

        lbl_pan = QLabel("PAN")
        lbl_pan.setStyleSheet("font-size: 8px; color: #64748b; font-weight: bold;")
        row2.addWidget(lbl_pan)

        self.slider_pan = QSlider(Qt.Horizontal)
        self.slider_pan.setRange(-100, 100)
        self.slider_pan.setValue(int(self.track.pan * 100))
        self.slider_pan.setFixedHeight(18)
        self.slider_pan.setToolTip("Panoramique (Gauche / Centre / Droite)")
        self.slider_pan.valueChanged.connect(self._on_pan_changed)
        row2.addWidget(self.slider_pan, stretch=1)

        content_layout.addLayout(row2)
        main_layout.addLayout(content_layout)

    def mousePressEvent(self, event):
        self.track_selected.emit(self.track.id)
        super().mousePressEvent(event)

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
        self.track_modified.emit()

    def _on_solo_toggled(self, checked: bool):
        self.track.soloed = checked
        self.track_modified.emit()

    def _on_rec_toggled(self, checked: bool):
        self.track.armed = checked
        self.track_modified.emit()

    def _on_vol_changed(self, value: int):
        self.track.volume = value / 100.0
        self.slider_vol.setToolTip(f"Volume : {value}%")
        self.track_modified.emit()

    def _on_pan_changed(self, value: int):
        self.track.pan = value / 100.0
        pan_str = "C" if value == 0 else (f"L{abs(value)}" if value < 0 else f"R{value}")
        self.slider_pan.setToolTip(f"Pan : {pan_str}")
        self.track_modified.emit()
