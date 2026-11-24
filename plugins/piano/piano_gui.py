"""Native Nova Piano editor."""
from pathlib import Path

from PySide6.QtCore import QSignalBlocker, Qt, QTimer
from PySide6.QtGui import QFont, QPainter, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget,
)

from plugins.base import play_plugin_audition
from plugins.piano.piano_dsp import ROOMS


class PianoArtwork(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        image = Path(__file__).resolve().parents[2] / "assets" / "nova_piano.jpg"
        self._photo = QPixmap(str(image))
        self.setMinimumHeight(265)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 22, 0, 24)
        layout.setSpacing(4)
        layout.addStretch()
        title = QLabel("Nova Piano")
        title.setFont(QFont("Georgia", 33))
        title.setStyleSheet("color: #fff4e2; background: transparent;")
        layout.addWidget(title, alignment=Qt.AlignLeft)
        detail = QLabel("Piano à queue · Synthèse modale")
        detail.setStyleSheet("color: #dbc4a8; font-size: 13px; background: transparent;")
        layout.addWidget(detail, alignment=Qt.AlignLeft)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), "#191310")
        if not self._photo.isNull():
            scaled = self._photo.scaled(
                self.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
            )
            painter.drawPixmap(0, (self.height() - scaled.height()) // 2, scaled)


class NovaPianoEditor(QWidget):
    def __init__(self, plugin, parent=None):
        super().__init__(parent)
        self.plugin = plugin
        self.setMinimumWidth(615)
        self.setStyleSheet("""
            QFrame#pianoControls { background: #1e1917; border: 1px solid #4a3829; }
            QLabel { color: #f1deca; background: transparent; }
            QComboBox, QPushButton {
                border: 1px solid #74573c; border-radius: 5px;
                padding: 6px 9px; color: #ffecd4; background: #32251d;
            }
            QComboBox:hover, QPushButton:hover { background: #503724; }
            QComboBox QAbstractItemView { background: #2a201a; color: #ffecd4; }
            QSlider::groove:horizontal { background: #4a382c; height: 4px; border-radius: 2px; }
            QSlider::sub-page:horizontal { background: #c0915f; border-radius: 2px; }
            QSlider::handle:horizontal {
                background: #f2cf9e; width: 12px; margin: -5px 0; border-radius: 6px;
            }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(PianoArtwork(self), 1)

        controls = QFrame(self)
        controls.setObjectName("pianoControls")
        control_layout = QVBoxLayout(controls)
        control_layout.setContentsMargins(18, 13, 18, 13)
        control_layout.setSpacing(12)
        voicing = QHBoxLayout()
        voicing.setSpacing(19)
        ambience = QHBoxLayout()
        ambience.setSpacing(19)
        control_layout.addLayout(voicing)
        control_layout.addLayout(ambience)

        self.sliders = {}
        self._add_slider(
            voicing, "hammer_hardness", "Dureté du marteau", 100,
            "Ajuste le mordant de l’attaque et la brillance des notes fortes.",
        )
        self._add_slider(
            voicing, "soundboard_resonance", "Résonance de la table", 100,
            "Ajuste la vibration de la table d’harmonie et la tenue des cordes.",
        )
        self.room = QComboBox()
        self.room.setAccessibleName("Espace de réverbération")
        self.room.setMinimumWidth(144)
        for key, (label, *_) in ROOMS.items():
            self.room.addItem(label, key)
        room_column = QVBoxLayout()
        room_column.addWidget(QLabel("Espace de réverbération"))
        room_column.addWidget(self.room)
        ambience.addLayout(room_column)

        self._add_slider(ambience, "reverb_mix", "Dosage", 100)
        self._add_slider(voicing, "master_volume", "Niveau", 200)
        self.audition = QPushButton("▶ Écouter")
        self.audition.setToolTip("Écouter un accord avec les réglages actuels")
        ambience.addWidget(self.audition)
        layout.addWidget(controls)

        self.room.currentIndexChanged.connect(lambda: self._change("reverb_room", self.room.currentData()))
        self.audition.clicked.connect(self._audition)
        self._load_from_plugin()
        self._refresh_timer = QTimer(self, interval=150)
        self._refresh_timer.timeout.connect(self._load_from_plugin)
        self._refresh_timer.start()

    def _add_slider(self, row, key, label, maximum, tooltip=""):
        column = QVBoxLayout()
        heading = QHBoxLayout()
        heading.addWidget(QLabel(label))
        value = QLabel()
        heading.addWidget(value, alignment=Qt.AlignRight)
        column.addLayout(heading)
        slider = QSlider(Qt.Horizontal)
        slider.setAccessibleName(label)
        slider.setToolTip(tooltip)
        slider.setRange(0, maximum)
        column.addWidget(slider)
        row.addLayout(column, 1)
        self.sliders[key] = (slider, value)
        slider.valueChanged.connect(lambda position: self._change(key, position / 100))

    def _load_from_plugin(self):
        state = self.plugin.get_state()
        with QSignalBlocker(self.room):
            self.room.setCurrentIndex(self.room.findData(state["reverb_room"]))
        for key, (slider, value) in self.sliders.items():
            with QSignalBlocker(slider):
                slider.setValue(round(state[key] * 100))
            value.setText(f"{slider.value()} %")
        self.audition.setEnabled(state["enabled"])

    def _sync_controls(self):
        self._load_from_plugin()
        self._notify_edit()

    def _change(self, key, value):
        self.plugin.set_state({key: value})
        self._sync_controls()

    def _notify_edit(self):
        from core.audio_engine import get_global_audio_engine

        engine = get_global_audio_engine()
        if engine is not None:
            engine.notify_plugin_modified(self.plugin)
        window = self.window()
        host = window._get_main_window() if hasattr(window, "_get_main_window") else None
        if host is not None:
            host.set_dirty(True)

    def _audition(self):
        from types import SimpleNamespace
        from core.audio_engine import get_global_audio_engine

        engine = get_global_audio_engine()
        sample_rate = engine.sample_rate if engine is not None else 44100
        notes = [
            SimpleNamespace(pitch=pitch, velocity=velocity, start_beat=0.0, duration=0.65)
            for pitch, velocity in ((48, 91), (55, 77), (64, 85), (71, 69))
        ]
        frames = round((0.8 + self.plugin.get_release_tail_sec()) * sample_rate)
        wave = self.plugin.render_in_context(
            notes, 0.0, 1.0, frames, sample_rate, self.plugin.create_isolated_context(sample_rate)
        )
        play_plugin_audition(self.plugin, wave, self)
