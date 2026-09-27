"""
plugins/mixer/mixer_gui.py - Console de mixage multipiste professionnelle pour NovaDAW.

Affiche :
- Tranches de console pour chaque piste du projet (Volume, Pan, Mute, Solo, Rec)
- Tranche Master dédiée à droite avec fader général, commutateurs Mono et Dim
- VU-mètres stéréo de crête animés en temps réel
- Accès rapide aux plugins chargés sur chaque piste
"""
from typing import Optional, Dict
import numpy as np

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSlider, QFrame, QScrollArea, QSizePolicy
)
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPainter, QBrush, QColor, QFont, QLinearGradient, QPen

from plugins.mixer.mixer_plugin import MixerPlugin
from core.project import Project, Track
from ui.track_header import ResetableSlider


class VuMeterBar(QFrame):
    """Bargraph vertical stéréo (Vert -> Jaune -> Rouge)"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(14)
        self.setMinimumHeight(120)
        self.peak_left_db = -60.0
        self.peak_right_db = -60.0
        self.setStyleSheet("background-color: #0b0d12; border: 1px solid #1f2430; border-radius: 2px;")

    def set_levels(self, l_db: float, r_db: float):
        self.peak_left_db = l_db
        self.peak_right_db = r_db
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        w = self.width()
        h = self.height()
        bar_w = (w - 4) // 2

        for ch_idx, db in enumerate([self.peak_left_db, self.peak_right_db]):
            # Échelle de -48 dB à +3 dB
            norm = max(0.0, min(1.0, (db + 48.0) / 51.0))
            bar_h = int((h - 4) * norm)
            x_pos = 2 + ch_idx * (bar_w + 1)
            y_pos = h - 2 - bar_h

            if bar_h > 0:
                gradient = QLinearGradient(0, h - 2, 0, 2)
                gradient.setColorAt(0.0, QColor(34, 197, 94))   # Vert
                gradient.setColorAt(0.7, QColor(234, 179, 8))   # Jaune (-6 dB)
                gradient.setColorAt(1.0, QColor(239, 68, 68))   # Rouge (+0 dB)
                painter.fillRect(x_pos, y_pos, bar_w, bar_h, QBrush(gradient))

        # Ligne de repère 0 dB (à environ 94% de la hauteur)
        y_zero = int(h - 2 - (h - 4) * (48.0 / 51.0))
        painter.setPen(QPen(QColor(248, 113, 113), 1))
        painter.drawLine(1, y_zero, w - 2, y_zero)


class MixerChannelStrip(QFrame):
    """Tranche de console pour une piste individuelle"""
    track_modified = Signal()
    track_selected = Signal(str)

    def __init__(self, track: Track, parent=None):
        super().__init__(parent)
        self.track = track
        self.is_selected = False
        self.setFixedWidth(92)
        self._apply_style(False)
        self._init_ui()

    def _apply_style(self, selected: bool):
        border = "#38bdf8" if selected else "#232736"
        bg = "#1b2030" if selected else "#151822"
        self.setStyleSheet(f"""
            MixerChannelStrip {{
                background-color: {bg};
                border: 1px solid {border};
                border-radius: 5px;
            }}
            QLabel {{
                color: #e2e8f0;
                font-size: 10px;
            }}
            QSlider::groove:vertical {{
                width: 4px;
                background: #0f1118;
                border-radius: 2px;
            }}
            QSlider::sub-page:vertical {{
                background: #0f1118;
            }}
            QSlider::add-page:vertical {{
                background: #38bdf8;
            }}
            QSlider::handle:vertical {{
                background: #ffffff;
                height: 14px;
                margin: 0 -5px;
                border-radius: 2px;
            }}
        """)

    def set_selected(self, selected: bool):
        if self.is_selected != selected:
            self.is_selected = selected
            self._apply_style(selected)

    def mousePressEvent(self, event):
        self.track_selected.emit(self.track.id)
        super().mousePressEvent(event)

    def _get_track_icon(self) -> str:
        p_name = (self.track.plugin_name or "").lower()
        p_path = (self.track.plugin_path or "").lower()
        t_name = self.track.name.lower()
        if "drum" in p_name or "batterie" in t_name or p_path == "novadaw.drum_machine":
            return "🥁"
        elif "synth" in p_name or p_path == "novadaw.synth":
            return "⚡"
        elif self.track.track_type == "midi":
            return "🎹"
        return "🔊"

    def _update_name_label(self):
        icon = self._get_track_icon()
        self.lbl_name.setToolTip(self.track.name)
        clean = self.track.name.replace("🎸 ", "").replace("🎹 ", "").replace("🔊 ", "").replace("🥁 ", "").replace("⚡ ", "")
        fm = self.lbl_name.fontMetrics()
        elided = fm.elidedText(clean, Qt.ElideRight, 78)
        self.lbl_name.setText(f"{icon} {elided}")

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 6, 5, 6)
        layout.setSpacing(4)
        layout.setAlignment(Qt.AlignCenter)

        # 1. Badge couleur & Nom
        self.color_tag = QFrame()
        self.color_tag.setFixedHeight(4)
        self.color_tag.setStyleSheet(f"background-color: {self.track.color}; border-radius: 2px;")
        layout.addWidget(self.color_tag)

        self.lbl_name = QLabel()
        self.lbl_name.setAlignment(Qt.AlignCenter)
        self.lbl_name.setStyleSheet("font-size: 10px; font-weight: bold; color: #f1f5f9; padding: 1px;")
        self._update_name_label()
        layout.addWidget(self.lbl_name)

        # 2. Panoramique
        lbl_pan = QLabel("PAN")
        lbl_pan.setStyleSheet("font-size: 8px; color: #64748b; font-weight: bold;")
        lbl_pan.setAlignment(Qt.AlignCenter)
        layout.addWidget(lbl_pan)

        pan_val = int(round(self.track.pan * 100))
        self.slider_pan = ResetableSlider(Qt.Horizontal, default_value=0)
        self.slider_pan.setRange(-100, 100)
        self.slider_pan.setValue(pan_val)
        self.slider_pan.setFixedHeight(12)
        self._update_pan_tooltip(pan_val)
        self.slider_pan.valueChanged.connect(self._on_pan_changed)
        layout.addWidget(self.slider_pan)

        # 3. Boutons M / S / R
        row_msr = QHBoxLayout()
        row_msr.setSpacing(3)

        self.btn_m = QPushButton("M")
        self.btn_m.setFixedSize(24, 20)
        self.btn_m.setCheckable(True)
        self.btn_m.setChecked(self.track.muted)
        self.btn_m.setToolTip("Mute (Couper le son)")
        self._update_mute_style(self.track.muted)
        self.btn_m.toggled.connect(self._on_mute_toggled)
        row_msr.addWidget(self.btn_m)

        self.btn_s = QPushButton("S")
        self.btn_s.setFixedSize(24, 20)
        self.btn_s.setCheckable(True)
        self.btn_s.setChecked(self.track.soloed)
        self.btn_s.setToolTip("Solo (Écouter seul)")
        self._update_solo_style(self.track.soloed)
        self.btn_s.toggled.connect(self._on_solo_toggled)
        row_msr.addWidget(self.btn_s)

        self.btn_r = QPushButton("R")
        self.btn_r.setFixedSize(24, 20)
        self.btn_r.setCheckable(True)
        self.btn_r.setChecked(self.track.armed)
        self.btn_r.setToolTip("Armer pour l'enregistrement (R)")
        self._update_rec_style(self.track.armed)
        self.btn_r.toggled.connect(self._on_rec_toggled)
        row_msr.addWidget(self.btn_r)

        layout.addLayout(row_msr)

        # 4. Section Fader + VU-Mètre
        fader_row = QHBoxLayout()
        fader_row.setSpacing(4)

        vol_val = int(round(self.track.volume * 100))
        self.slider_vol = ResetableSlider(Qt.Vertical, default_value=80)
        self.slider_vol.setRange(0, 150)
        self.slider_vol.setValue(vol_val)
        self.slider_vol.setFixedHeight(130)
        self.slider_vol.setToolTip(f"Volume : {vol_val}% (Double-clic: 80%)")
        self.slider_vol.valueChanged.connect(self._on_vol_changed)
        fader_row.addWidget(self.slider_vol)

        self.vu_meter = VuMeterBar(self)
        self.vu_meter.setFixedHeight(130)
        fader_row.addWidget(self.vu_meter)

        layout.addLayout(fader_row)

        # 5. Label Valeur dB / %
        self.lbl_vol_db = QLabel(f"{vol_val}%")
        self.lbl_vol_db.setAlignment(Qt.AlignCenter)
        self.lbl_vol_db.setStyleSheet("font-size: 9px; font-weight: bold; color: #38bdf8;")
        layout.addWidget(self.lbl_vol_db)

    def _update_pan_tooltip(self, val: int):
        pan_str = "C" if val == 0 else (f"L{abs(val)}" if val < 0 else f"R{val}")
        self.slider_pan.setToolTip(f"Pan : {pan_str} (Double-clic: C)")

    def _update_mute_style(self, chk: bool):
        bg = "#d97706" if chk else "#20232f"
        col = "#ffffff" if chk else "#94a3b8"
        border = "#f59e0b" if chk else "#33384a"
        self.btn_m.setStyleSheet(f"background-color: {bg}; color: {col}; border: 1px solid {border}; font-weight: bold; font-size: 10px; padding: 0px;")

    def _update_solo_style(self, chk: bool):
        bg = "#eab308" if chk else "#20232f"
        col = "#1e293b" if chk else "#94a3b8"
        border = "#facc15" if chk else "#33384a"
        self.btn_s.setStyleSheet(f"background-color: {bg}; color: {col}; border: 1px solid {border}; font-weight: bold; font-size: 10px; padding: 0px;")

    def _update_rec_style(self, chk: bool):
        bg = "#dc2626" if chk else "#20232f"
        col = "#ffffff" if chk else "#94a3b8"
        border = "#ef4444" if chk else "#33384a"
        self.btn_r.setStyleSheet(f"background-color: {bg}; color: {col}; border: 1px solid {border}; font-weight: bold; font-size: 10px; padding: 0px;")

    def _on_pan_changed(self, val: int):
        self.track.pan = val / 100.0
        self._update_pan_tooltip(val)
        self.track_modified.emit()

    def _on_vol_changed(self, val: int):
        self.track.volume = val / 100.0
        self.lbl_vol_db.setText(f"{val}%")
        self.slider_vol.setToolTip(f"Volume : {val}% (Double-clic: 80%)")
        self.track_modified.emit()

    def _on_mute_toggled(self, chk: bool):
        self.track.muted = chk
        self._update_mute_style(chk)
        self.track_modified.emit()

    def _on_solo_toggled(self, chk: bool):
        self.track.soloed = chk
        self._update_solo_style(chk)
        self.track_modified.emit()

    def _on_rec_toggled(self, chk: bool):
        self.track.armed = chk
        self._update_rec_style(chk)
        self.track_modified.emit()

    def sync_controls_from_track(self):
        """Synchronise l'ensemble des contrôles de la tranche depuis l'objet Track."""
        self.btn_m.blockSignals(True)
        self.btn_m.setChecked(self.track.muted)
        self.btn_m.blockSignals(False)
        self._update_mute_style(self.track.muted)

        self.btn_s.blockSignals(True)
        self.btn_s.setChecked(self.track.soloed)
        self.btn_s.blockSignals(False)
        self._update_solo_style(self.track.soloed)

        if hasattr(self, "btn_r"):
            self.btn_r.blockSignals(True)
            self.btn_r.setChecked(self.track.armed)
            self.btn_r.blockSignals(False)
            self._update_rec_style(self.track.armed)

        vol_val = int(round(self.track.volume * 100))
        self.slider_vol.blockSignals(True)
        self.slider_vol.setValue(vol_val)
        self.slider_vol.blockSignals(False)
        self.lbl_vol_db.setText(f"{vol_val}%")
        self.slider_vol.setToolTip(f"Volume : {vol_val}% (Double-clic: 80%)")

        pan_val = int(round(self.track.pan * 100))
        self.slider_pan.blockSignals(True)
        self.slider_pan.setValue(pan_val)
        self.slider_pan.blockSignals(False)
        self._update_pan_tooltip(pan_val)

        self._update_name_label()
        self.color_tag.setStyleSheet(f"background-color: {self.track.color}; border-radius: 2px;")


class MixerConsoleWidget(QWidget):
    """Console de mixage principale regroupant toutes les tranches et le Master"""
    track_mixer_changed = Signal()
    track_selected = Signal(str)

    def __init__(self, mixer: MixerPlugin, parent=None):
        super().__init__(parent)
        self.mixer = mixer
        self._selected_track_id: Optional[str] = None
        self.setObjectName("mixer_console")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("""
            QWidget#mixer_console {
                background-color: #0f1117;
                color: #e2e8f0;
            }
            QLabel#header {
                font-weight: bold;
                font-size: 13px;
                color: #38bdf8;
            }
        """)

        self.strips: Dict[str, MixerChannelStrip] = {}
        self._init_ui()

        # Timer pour rafraîchissement des VU-mètres (30 FPS)
        self.meter_timer = QTimer(self)
        self.meter_timer.setInterval(33)
        self.meter_timer.timeout.connect(self._update_meters)
        self.meter_timer.start()

    def _on_strip_track_modified(self):
        self.track_mixer_changed.emit()

    def _on_strip_track_selected(self, track_id: str):
        self.set_selected_track(track_id)
        self.track_selected.emit(track_id)

    def set_selected_track(self, track_id: Optional[str]):
        self._selected_track_id = track_id
        for tid, strip in self.strips.items():
            strip.set_selected(tid == track_id)

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 8, 10, 8)
        main_layout.setSpacing(8)

        # En-tête
        top_bar = QHBoxLayout()
        lbl_title = QLabel("🎛️ CONSOLE DE MIXAGE (Mixeur NovaDAW)")
        lbl_title.setObjectName("header")
        top_bar.addWidget(lbl_title)
        top_bar.addStretch()

        self.btn_mono = QPushButton("Mono")
        self.btn_mono.setCheckable(True)
        self.btn_mono.setMinimumWidth(64)
        self.btn_mono.setChecked(self.mixer.mono_switch)
        self.btn_mono.toggled.connect(lambda c: setattr(self.mixer, "mono_switch", c))
        top_bar.addWidget(self.btn_mono)

        self.btn_dim = QPushButton("Dim (-12dB)")
        self.btn_dim.setCheckable(True)
        self.btn_dim.setMinimumWidth(92)
        self.btn_dim.setChecked(self.mixer.dim_switch)
        self.btn_dim.toggled.connect(lambda c: setattr(self.mixer, "dim_switch", c))
        top_bar.addWidget(self.btn_dim)

        main_layout.addLayout(top_bar)

        # Zone centrale : Pistes défilantes + Tranche Master fixe à droite
        center_layout = QHBoxLayout()
        center_layout.setSpacing(10)

        # Zone de défilement des pistes
        scroll_tracks = QScrollArea()
        scroll_tracks.setWidgetResizable(True)
        scroll_tracks.setStyleSheet("border: none; background: transparent;")

        self.tracks_container = QWidget()
        self.tracks_layout = QHBoxLayout(self.tracks_container)
        self.tracks_layout.setContentsMargins(0, 0, 0, 0)
        self.tracks_layout.setSpacing(8)
        self.tracks_layout.addStretch()
        scroll_tracks.setWidget(self.tracks_container)

        center_layout.addWidget(scroll_tracks, stretch=1)

        # Tranche Master à droite
        self.master_strip = self._create_master_strip()
        center_layout.addWidget(self.master_strip)

        main_layout.addLayout(center_layout)

        # Construire les tranches des pistes actuelles
        self.refresh_tracks()

    def _create_master_strip(self) -> QFrame:
        frame = QFrame()
        frame.setFixedWidth(95)
        frame.setStyleSheet("""
            QFrame {
                background-color: #1a1622;
                border: 1px solid #ef4444;
                border-radius: 6px;
            }
            QLabel {
                color: #fca5a5;
                font-size: 10px;
                font-weight: bold;
            }
            QSlider::groove:vertical {
                width: 5px;
                background: #0f1118;
                border-radius: 2px;
            }
            QSlider::add-page:vertical {
                background: #ef4444;
            }
            QSlider::handle:vertical {
                background: #ffffff;
                height: 16px;
                margin: 0 -5px;
                border-radius: 3px;
            }
        """)

        layout = QVBoxLayout(frame)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)
        layout.setAlignment(Qt.AlignCenter)

        lbl_master = QLabel("MASTER")
        lbl_master.setAlignment(Qt.AlignCenter)
        lbl_master.setStyleSheet("color: #ef4444; font-size: 11px; font-weight: bold; letter-spacing: 1px;")
        layout.addWidget(lbl_master)

        # Fader Master + VU-Mètre Stéréo
        fader_row = QHBoxLayout()
        fader_row.setSpacing(4)

        master_vol_val = int(round(self.mixer.master_volume * 100))
        self.slider_master = ResetableSlider(Qt.Vertical, default_value=100)
        self.slider_master.setRange(0, 150)
        self.slider_master.setValue(master_vol_val)
        self.slider_master.setFixedHeight(140)
        self.slider_master.setToolTip(f"Volume Master : {master_vol_val}% (Double-clic: 100%)")
        self.slider_master.valueChanged.connect(self._on_master_fader_changed)
        fader_row.addWidget(self.slider_master)

        self.master_vu = VuMeterBar(self)
        self.master_vu.setFixedHeight(140)
        self.master_vu.setFixedWidth(18)
        fader_row.addWidget(self.master_vu)

        layout.addLayout(fader_row)

        self.lbl_master_db = QLabel(f"{master_vol_val}%")
        self.lbl_master_db.setAlignment(Qt.AlignCenter)
        self.lbl_master_db.setStyleSheet("font-size: 10px; font-weight: bold; color: #ef4444;")
        layout.addWidget(self.lbl_master_db)

        return frame

    def _on_master_fader_changed(self, val: int):
        self.mixer.master_volume = val / 100.0
        self.lbl_master_db.setText(f"{val}%")
        self.slider_master.setToolTip(f"Volume Master : {val}% (Double-clic: 100%)")

    def refresh_tracks(self):
        """Reconstruit les tranches selon les pistes du projet"""
        while self.tracks_layout.count() > 1:
            child = self.tracks_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        self.strips.clear()

        if self.mixer.project and hasattr(self.mixer.project, "tracks"):
            for track in self.mixer.project.tracks:
                strip = MixerChannelStrip(track, self)
                strip.track_modified.connect(self._on_strip_track_modified)
                strip.track_selected.connect(self._on_strip_track_selected)
                if self._selected_track_id:
                    strip.set_selected(track.id == self._selected_track_id)
                self.strips[track.id] = strip
                self.tracks_layout.insertWidget(self.tracks_layout.count() - 1, strip)

    def sync_controls_from_tracks(self):
        """Synchronise l'ensemble des tranches avec le projet actif."""
        if not self.mixer.project or not hasattr(self.mixer.project, "tracks"):
            return

        proj_track_ids = [t.id for t in self.mixer.project.tracks]
        current_strip_ids = list(self.strips.keys())

        if proj_track_ids != current_strip_ids:
            self.refresh_tracks()
            return

        for track in self.mixer.project.tracks:
            strip = self.strips.get(track.id)
            if strip:
                strip.sync_controls_from_track()

        # Synchroniser la tranche Master
        if hasattr(self, "slider_master"):
            m_val = int(round(self.mixer.master_volume * 100))
            self.slider_master.blockSignals(True)
            self.slider_master.setValue(m_val)
            self.slider_master.blockSignals(False)
            self.lbl_master_db.setText(f"{m_val}%")
            self.slider_master.setToolTip(f"Volume Master : {m_val}% (Double-clic: 100%)")
        if hasattr(self, "btn_mono"):
            self.btn_mono.blockSignals(True)
            self.btn_mono.setChecked(self.mixer.mono_switch)
            self.btn_mono.blockSignals(False)
        if hasattr(self, "btn_dim"):
            self.btn_dim.blockSignals(True)
            self.btn_dim.setChecked(self.mixer.dim_switch)
            self.btn_dim.blockSignals(False)

    def _update_meters(self):
        """Mise à jour périodique des VU-mètres Master et Pistes"""
        # Master VU-Meter
        self.master_vu.set_levels(self.mixer.peak_left_db, self.mixer.peak_right_db)

        # Décroissance douce des niveaux
        self.mixer.peak_left_db = max(-60.0, self.mixer.peak_left_db - 1.5)
        self.mixer.peak_right_db = max(-60.0, self.mixer.peak_right_db - 1.5)

        # Mettre à jour les pistes individuelles si données disponibles
        for track_id, (pk_l, pk_r) in list(self.mixer.track_peaks.items()):
            strip = self.strips.get(track_id)
            if strip:
                strip.vu_meter.set_levels(pk_l, pk_r)
