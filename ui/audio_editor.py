"""
ui/audio_editor.py - Éditeur et inspecteur de clip audio avec affichage de forme d'onde
"""
import os
from typing import Optional
import numpy as np
import soundfile as sf
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFileDialog, QSlider, QFrame, QSizePolicy
)
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QFont, QPaintEvent
from PySide6.QtCore import Qt, Signal, QRectF
from core.project import AudioClip, Track
from core.audio_engine import AudioEngine
from core.audio_importer import load_audio_file, QT_FILE_DIALOG_FILTER


class WaveformWidget(QWidget):
    """Visualiseur de forme d'onde audio haute résolution"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.audio_data: Optional[np.ndarray] = None
        self.sample_rate = 44100
        self.setFixedHeight(120)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_audio_data(self, data: Optional[np.ndarray], sr: int = 44100):
        self.audio_data = data
        self.sample_rate = sr
        self.update()

    def paintEvent(self, event: QPaintEvent):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        h = self.height()

        # Fond
        painter.fillRect(0, 0, w, h, QColor("#121318"))
        mid_y = h / 2.0

        # Ligne de centre
        painter.setPen(QPen(QColor("#242733"), 1))
        painter.drawLine(0, int(mid_y), w, int(mid_y))

        if self.audio_data is None or len(self.audio_data) == 0:
            painter.setPen(QColor("#64748b"))
            painter.setFont(QFont("Segoe UI", 11))
            painter.drawText(self.rect(), Qt.AlignCenter, "Aucun échantillon audio chargé - Cliquez sur 'Importer WAV'")
            return

        # Calculer les crêtes (peaks) par colonne de pixels pour un affichage instantané
        data = self.audio_data
        if data.ndim > 1:
            data = data[:, 0]  # Canal gauche pour la vue d'ensemble

        total_samples = len(data)
        samples_per_pixel = max(1, total_samples // w)

        painter.setPen(QPen(QColor("#10b981"), 1))

        for x in range(w):
            start_idx = x * samples_per_pixel
            end_idx = min(total_samples, (x + 1) * samples_per_pixel)
            if start_idx >= total_samples:
                break
            chunk = data[start_idx:end_idx]
            if len(chunk) == 0:
                continue

            min_val = float(np.min(chunk))
            max_val = float(np.max(chunk))

            y1 = int(mid_y - (max_val * (h * 0.45)))
            y2 = int(mid_y - (min_val * (h * 0.45)))
            painter.drawLine(x, y1, x, y2)


class AudioEditor(QWidget):
    """Panneau d'inspection et édition de clip audio"""
    clip_modified = Signal()

    def __init__(self, audio_engine: AudioEngine, parent=None):
        super().__init__(parent)
        self.audio_engine = audio_engine
        self.current_track: Optional[Track] = None
        self.current_clip: Optional[AudioClip] = None
        self.setObjectName("audio_editor")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("""
            QWidget#audio_editor {
                background-color: #14151b;
                color: #e2e8f0;
            }
        """)

        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(10)

        # Barre d'infos
        top_bar = QHBoxLayout()
        self.lbl_title = QLabel("🔊 ÉDITEUR AUDIO : Aucun clip sélectionné")
        self.lbl_title.setStyleSheet("font-weight: bold; color: #10b981; font-size: 13px;")
        top_bar.addWidget(self.lbl_title)

        top_bar.addStretch()

        self.btn_load_wav = QPushButton("📥 Importer Fichier Audio...")
        self.btn_load_wav.setToolTip("Importer n'importe quel fichier audio (WAV, MP3, FLAC, OGG, AIFF, M4A, etc.)")
        self.btn_load_wav.setMinimumWidth(180)
        self.btn_load_wav.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.btn_load_wav.clicked.connect(self._import_wav)
        top_bar.addWidget(self.btn_load_wav)

        layout.addLayout(top_bar)

        # Afficheur de forme d'onde
        self.waveform = WaveformWidget()
        layout.addWidget(self.waveform)

        # Contrôles inférieurs (Gain, durée, infos)
        ctrl_layout = QHBoxLayout()
        ctrl_layout.setSpacing(12)

        self.lbl_info = QLabel("Format : - | Durée : 0.0s")
        self.lbl_info.setStyleSheet("color: #94a3b8; font-size: 11px;")
        ctrl_layout.addWidget(self.lbl_info)

        ctrl_layout.addStretch()

        lbl_gain = QLabel("Gain :")
        lbl_gain.setStyleSheet("color: #cbd5e1; font-weight: bold;")
        ctrl_layout.addWidget(lbl_gain)

        self.slider_gain = QSlider(Qt.Horizontal)
        self.slider_gain.setRange(0, 200)
        self.slider_gain.setValue(100)
        self.slider_gain.setMinimumWidth(100)
        self.slider_gain.setMaximumWidth(160)
        self.slider_gain.valueChanged.connect(self._on_gain_changed)
        ctrl_layout.addWidget(self.slider_gain)

        self.lbl_gain_val = QLabel("100%")
        self.lbl_gain_val.setStyleSheet("font-family: Consolas; color: #38bdf8;")
        ctrl_layout.addWidget(self.lbl_gain_val)

        layout.addLayout(ctrl_layout)

    def open_clip(self, track: Optional[Track], clip: Optional[AudioClip]):
        self.current_track = track
        self.current_clip = clip

        if track is None or clip is None:
            self.lbl_title.setText("🔊 ÉDITEUR AUDIO : Aucun clip sélectionné")
            self.slider_gain.setValue(100)
            self.lbl_gain_val.setText("100%")
            self.waveform.set_audio_data(None)
            self.lbl_info.setText("Sélectionnez ou double-cliquez sur un clip audio pour l'éditer.")
            return

        self.lbl_title.setText(f"🔊 ÉDITEUR AUDIO : {track.name} ➔ {clip.name}")

        self.slider_gain.setValue(int(clip.gain * 100))
        self.lbl_gain_val.setText(f"{int(clip.gain * 100)}%")

        if clip.audio_data is not None:
            self.waveform.set_audio_data(clip.audio_data, clip.sample_rate)
            dur_sec = len(clip.audio_data) / clip.sample_rate
            fmt = os.path.splitext(clip.file_path or "")[1].upper().replace(".", "") or "AUDIO"
            self.lbl_info.setText(f"Format : {fmt} | Échantillonnage : {clip.sample_rate} Hz | Durée : {dur_sec:.2f}s | Fichier : {os.path.basename(clip.file_path or '')}")
        else:
            self.waveform.set_audio_data(None)
            self.lbl_info.setText("Aucun fichier audio chargé")

    def clear(self):
        """Réinitialise l'éditeur audio à un état vide."""
        self.open_clip(None, None)

    def _import_wav(self):
        if not self.current_clip:
            return

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Sélectionner un fichier audio (WAV, MP3, FLAC, OGG, AIFF, M4A...)",
            "",
            QT_FILE_DIALOG_FILTER
        )
        if file_path:
            try:
                target_sr = self.audio_engine.sample_rate if hasattr(self.audio_engine, "sample_rate") else 44100
                data, sr, dur_sec = load_audio_file(file_path, target_sr=target_sr)
                self.current_clip.audio_data = data
                self.current_clip.sample_rate = sr
                self.current_clip.file_path = file_path
                self.current_clip.name = os.path.splitext(os.path.basename(file_path))[0]

                # Calculer la longueur en temps
                bpm = 120.0
                if hasattr(self.current_track, "bpm"):
                    bpm = self.current_track.bpm
                beats = (dur_sec / 60.0) * bpm
                self.current_clip.length_beats = max(1.0, round(beats, 2))

                self.open_clip(self.current_track, self.current_clip)
                self.clip_modified.emit()
            except Exception as e:
                print(f"[AudioEditor] Erreur chargement audio: {e}")

    def _on_gain_changed(self, val: int):
        if self.current_clip:
            self.current_clip.gain = val / 100.0
            self.lbl_gain_val.setText(f"{val}%")
            self.clip_modified.emit()
