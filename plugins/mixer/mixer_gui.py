"""
plugins/mixer/mixer_gui.py - Console de mixage multipiste professionnelle pour NovaDAW.

Affiche :
- Tranches de console pour chaque piste du projet (Volume, Pan, Mute, Solo, Rec)
- Tranche Master dédiée à droite avec fader général, commutateurs Mono et Dim
- VU-mètres stéréo de crête animés en temps réel
- Accès rapide aux plugins chargés sur chaque piste
"""
import math
import os
from typing import Optional, Dict, Tuple
import numpy as np

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSlider, QFrame, QScrollArea, QSizePolicy, QMenu
)
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPainter, QBrush, QColor, QFont, QLinearGradient, QPen, QPixmap

from plugins.mixer.mixer_plugin import MixerPlugin
from core.project import Project, Track
from ui.track_header import ResetableSlider


class VuMeterBar(QWidget):
    """Bargraph vertical stéréo (Vert -> Jaune -> Rouge) sans QSS ni allocations par trame"""

    _BG_COLOR = QColor("#0b0d12")
    _BORDER_PEN = QPen(QColor("#1f2430"), 1)
    _ZERO_PEN = QPen(QColor(248, 113, 113), 1)
    _C_GREEN = QColor(34, 197, 94)
    _C_YELLOW = QColor(234, 179, 8)
    _C_RED = QColor(239, 68, 68)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.setFixedWidth(14)
        self.setMinimumHeight(120)
        self.peak_left_db = -60.0
        self.peak_right_db = -60.0
        self._last_bars: Optional[Tuple[int, int]] = None
        self._bar_pixmap: Optional[QPixmap] = None
        self._bar_pixmap_size: Optional[Tuple[int, int]] = None

    def resizeEvent(self, event):
        self._last_bars = None
        super().resizeEvent(event)

    def _ensure_bar_pixmap(self, bar_w: int, usable_h: int) -> QPixmap:
        key = (bar_w, usable_h)
        if self._bar_pixmap is None or self._bar_pixmap_size != key:
            pix = QPixmap(bar_w, usable_h)
            p = QPainter(pix)
            gradient = QLinearGradient(0, usable_h, 0, 0)
            gradient.setColorAt(0.0, self._C_GREEN)   # Vert
            gradient.setColorAt(0.7, self._C_YELLOW)  # Jaune (-6 dB)
            gradient.setColorAt(1.0, self._C_RED)     # Rouge (+0 dB)
            p.fillRect(0, 0, bar_w, usable_h, QBrush(gradient))
            p.end()
            self._bar_pixmap = pix
            self._bar_pixmap_size = key
        return self._bar_pixmap

    @staticmethod
    def _norm_db(db: float) -> float:
        if not math.isfinite(db):
            return 1.0 if db > 0.0 else 0.0
        if db <= -48.0:
            return 0.0
        if db >= 3.0:
            return 1.0
        return (db + 48.0) / 51.0

    def set_levels(self, l_db: float, r_db: float):
        l_val = -60.0 if math.isnan(l_db) else float(l_db)
        r_val = -60.0 if math.isnan(r_db) else float(r_db)
        self.peak_left_db = l_val
        self.peak_right_db = r_val
        usable_h = max(0, self.height() - 4)
        nl = self._norm_db(l_val)
        nr = self._norm_db(r_val)
        bars = (int(usable_h * nl), int(usable_h * nr))
        if bars != self._last_bars:
            self._last_bars = bars
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        w = self.width()
        h = self.height()
        painter.fillRect(0, 0, w, h, self._BG_COLOR)
        painter.setPen(self._BORDER_PEN)
        painter.drawRect(0, 0, w - 1, h - 1)

        usable_h = h - 4
        bar_w = (w - 4) // 2
        if usable_h > 0 and bar_w > 0:
            pix = self._ensure_bar_pixmap(bar_w, usable_h)
            for ch_idx, db in enumerate((self.peak_left_db, self.peak_right_db)):
                # Échelle de -48 dB à +3 dB
                norm = self._norm_db(db)
                bar_h = int(usable_h * norm)
                if bar_h > 0:
                    x_pos = 2 + ch_idx * (bar_w + 1)
                    y_pos = h - 2 - bar_h
                    painter.drawPixmap(x_pos, y_pos, pix, 0, usable_h - bar_h, bar_w, bar_h)

        # Ligne de repère 0 dB (à environ 94% de la hauteur)
        y_zero = int(h - 2 - usable_h * (48.0 / 51.0))
        painter.setPen(self._ZERO_PEN)
        painter.drawLine(1, y_zero, w - 2, y_zero)


class PeakDbLabel(QWidget):
    """Afficheur de crête en dB sans QSS ni recalcul de mise en page à 30 Hz"""

    _C_SILENT = QColor("#64748b")
    _C_CLIP = QColor("#ef4444")
    _C_WARN = QColor("#f59e0b")
    _C_NORM_STRIP = QColor("#38bdf8")
    _C_NORM_MASTER = QColor("#fbbf24")

    def __init__(
        self,
        text: str = "-∞ dB",
        font_px: int = 9,
        normal_color: str = "#38bdf8",
        bg_normal: str = "#151822",
        bg_selected: str = "#1b2030",
        parent=None,
    ):
        super().__init__(parent)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self._text = text
        self._alignment = Qt.AlignRight | Qt.AlignVCenter
        self._font = QFont()
        self._font.setPixelSize(font_px)
        self._font.setBold(True)
        self._color_norm = self._C_NORM_MASTER if normal_color == "#fbbf24" else (
            self._C_NORM_STRIP if normal_color == "#38bdf8" else QColor(normal_color)
        )
        self._color = self._C_SILENT
        self._bg_normal = QColor(bg_normal)
        self._bg_selected = QColor(bg_selected)
        self._is_selected = False
        self.setFixedHeight(14 if font_px <= 9 else 15)
        self.setMinimumWidth(44)

    def text(self) -> str:
        return self._text

    def setText(self, text: str):
        if text != self._text:
            self._text = text
            self.update()

    def setAlignment(self, alignment):
        if self._alignment != alignment:
            self._alignment = alignment
            self.update()

    def set_background_selected(self, selected: bool):
        if self._is_selected != selected:
            self._is_selected = selected
            self.update()

    def set_level_db(self, pk_max: float):
        if math.isnan(pk_max) or pk_max <= -50.0:
            new_text = "-∞ dB"
            new_color = self._C_SILENT
        else:
            if pk_max > 0.0:
                new_text = f"{pk_max:+.1f} dB"
                new_color = self._C_CLIP
            elif pk_max > -6.0:
                new_text = f"{pk_max:.1f} dB"
                new_color = self._C_WARN
            else:
                new_text = f"{pk_max:.1f} dB"
                new_color = self._color_norm

        if new_text != self._text or new_color is not self._color:
            self._text = new_text
            self._color = new_color
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        bg = self._bg_selected if self._is_selected else self._bg_normal
        painter.fillRect(0, 0, self.width(), self.height(), bg)
        painter.setFont(self._font)
        painter.setPen(self._color)
        painter.drawText(self.rect(), self._alignment, self._text)


class MixerChannelStrip(QFrame):
    """Tranche de console pour une piste individuelle"""
    track_modified = Signal()
    track_selected = Signal(str)
    track_deleted = Signal(str)

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
            if hasattr(self, "lbl_peak_db"):
                self.lbl_peak_db.set_background_selected(selected)

    def mousePressEvent(self, event):
        self.track_selected.emit(self.track.id)
        super().mousePressEvent(event)

    def contextMenuEvent(self, event):
        self.track_selected.emit(self.track.id)
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #181b26;
                color: #f1f5f9;
                border: 1px solid #38bdf8;
                border-radius: 4px;
                padding: 4px;
                font-size: 11px;
            }
            QMenu::item {
                padding: 5px 18px 5px 10px;
                border-radius: 3px;
            }
            QMenu::item:selected {
                background-color: #0284c7;
                color: #ffffff;
                font-weight: bold;
            }
            QMenu::item:disabled {
                color: #64748b;
                font-weight: bold;
            }
        """)
        act_hdr = menu.addAction(f"🎵 Piste : « {self.track.name} »")
        act_hdr.setEnabled(False)
        menu.addSeparator()
        act_del = menu.addAction(f"🗑️ Supprimer la piste « {self.track.name} » (Delete)")
        act_del.triggered.connect(lambda: self.track_deleted.emit(self.track.id))
        menu.exec(event.globalPos())
        event.accept()

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
        self._last_tag_color = self.track.color
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

        # 5. Label Valeur Volume Fader (%) & Niveau de crête dynamique (dB avec 1 décimale)
        meter_info_layout = QHBoxLayout()
        meter_info_layout.setContentsMargins(0, 0, 0, 0)
        meter_info_layout.setSpacing(2)

        self.lbl_vol_db = QLabel(f"{vol_val}%")
        self.lbl_vol_db.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.lbl_vol_db.setStyleSheet("font-size: 9px; font-weight: bold; color: #38bdf8;")
        self.lbl_vol_db.setToolTip(f"Volume Fader : {vol_val}%")
        meter_info_layout.addWidget(self.lbl_vol_db)

        self.lbl_peak_db = PeakDbLabel(
            "-∞ dB",
            font_px=9,
            normal_color="#38bdf8",
            bg_normal="#151822",
            bg_selected="#1b2030",
            parent=self,
        )
        self.lbl_peak_db.setToolTip("Niveau de crête réel en direct (dB)")
        meter_info_layout.addWidget(self.lbl_peak_db)

        layout.addLayout(meter_info_layout)

    def set_levels(self, l_db: float, r_db: float):
        """Met à jour le VU-mètre et l'indicateur décibels (dB) en direct avec 1 décimale."""
        l_val = -60.0 if math.isnan(l_db) else l_db
        r_val = -60.0 if math.isnan(r_db) else r_db
        self.vu_meter.set_levels(l_val, r_val)
        if hasattr(self, "lbl_peak_db"):
            self.lbl_peak_db.set_level_db(max(l_val, r_val))

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
        if self.slider_vol.value() != vol_val:
            self.slider_vol.blockSignals(True)
            self.slider_vol.setValue(vol_val)
            self.slider_vol.blockSignals(False)
        self.lbl_vol_db.setText(f"{vol_val}%")
        self.slider_vol.setToolTip(f"Volume : {vol_val}% (Double-clic: 80%)")

        pan_val = int(round(self.track.pan * 100))
        if self.slider_pan.value() != pan_val:
            self.slider_pan.blockSignals(True)
            self.slider_pan.setValue(pan_val)
            self.slider_pan.blockSignals(False)
        self._update_pan_tooltip(pan_val)

        self._update_name_label()
        if getattr(self, "_last_tag_color", None) != self.track.color:
            self._last_tag_color = self.track.color
            self.color_tag.setStyleSheet(f"background-color: {self.track.color}; border-radius: 2px;")


class MixerConsoleWidget(QWidget):
    """Console de mixage principale regroupant toutes les tranches et le Master"""
    track_mixer_changed = Signal()
    track_selected = Signal(str)
    track_deleted = Signal(str)
    master_volume_changed = Signal(float)
    master_fx_requested = Signal()

    def __init__(self, mixer: MixerPlugin, parent=None):
        super().__init__(parent)
        self.mixer = mixer
        self._selected_track_id: Optional[str] = None
        self._master_selected: Optional[bool] = None
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
        if hasattr(self, "master_frame") and self.mixer.project:
            master_t = self.mixer.project.ensure_master_track()
            is_master = bool(master_t and track_id == master_t.id)
            self._apply_master_style(is_master)

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

    def _get_master_frame_style(self, selected: bool) -> str:
        border_col = "#fbbf24" if selected else "#d97706"
        border_px = "2px" if selected else "1px"
        bg_col = "#241e17" if selected else "#171a22"
        return f"""
            QFrame#master_strip_frame {{
                background-color: {bg_col};
                border: {border_px} solid {border_col};
                border-radius: 6px;
            }}
            QLabel {{
                color: #fde68a;
                font-size: 10px;
                font-weight: bold;
            }}
            QSlider::groove:vertical {{
                width: 5px;
                background: #0b0d13;
                border-radius: 2px;
            }}
            QSlider::sub-page:vertical {{
                background: #0b0d13;
            }}
            QSlider::add-page:vertical {{
                background: #f59e0b;
            }}
            QSlider::handle:vertical {{
                background: #ffffff;
                height: 16px;
                margin: 0 -5px;
                border-radius: 3px;
            }}
        """

    def _apply_master_style(self, selected: bool):
        if self._master_selected == selected and hasattr(self, "master_frame"):
            return
        self._master_selected = selected
        if hasattr(self, "master_frame"):
            self.master_frame.setStyleSheet(self._get_master_frame_style(selected))
        if hasattr(self, "lbl_master_peak_db"):
            self.lbl_master_peak_db.set_background_selected(selected)

    def _on_master_strip_clicked(self, event):
        if self.mixer.project:
            master_t = self.mixer.project.ensure_master_track()
            if master_t:
                self.set_selected_track(master_t.id)
                self.track_selected.emit(master_t.id)

    def _create_master_strip(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("master_strip_frame")
        frame.setFixedWidth(98)
        self.master_frame = frame
        self._apply_master_style(False)
        frame.mousePressEvent = self._on_master_strip_clicked

        layout = QVBoxLayout(frame)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)
        layout.setAlignment(Qt.AlignCenter)

        # En-tête Master Gold avec badge
        lbl_master = QLabel("👑 MASTER")
        lbl_master.setAlignment(Qt.AlignCenter)
        lbl_master.setStyleSheet("color: #f59e0b; font-size: 11px; font-weight: bold; letter-spacing: 1px;")
        layout.addWidget(lbl_master)

        # Bouton Effets / FX sur la piste Master
        self.btn_master_fx = QPushButton("⚡ FX")
        self.btn_master_fx.setToolTip("Effets & Plugins sur le bus Master (Clic: Onglet Effets Master F6 | Clic-droit: Menu rapide)")
        self.btn_master_fx.setFixedHeight(22)
        self.btn_master_fx.setStyleSheet("""
            QPushButton {
                background-color: #1e2230;
                color: #f59e0b;
                border: 1px solid #d97706;
                border-radius: 3px;
                font-size: 10px;
                font-weight: bold;
                padding: 1px 4px;
            }
            QPushButton:hover {
                background-color: #d97706;
                color: #ffffff;
            }
        """)
        self.btn_master_fx.clicked.connect(self._on_master_fx_clicked)
        self.btn_master_fx.setContextMenuPolicy(Qt.CustomContextMenu)
        self.btn_master_fx.customContextMenuRequested.connect(lambda pos: self._show_master_fx_menu())
        layout.addWidget(self.btn_master_fx)

        # Fader Master + VU-Mètre Stéréo
        fader_row = QHBoxLayout()
        fader_row.setSpacing(4)

        master_vol_val = int(round(self.mixer.master_volume * 100))
        self.slider_master = ResetableSlider(Qt.Vertical, default_value=100)
        self.slider_master.setRange(0, 150)
        self.slider_master.setValue(master_vol_val)
        self.slider_master.setFixedHeight(130)
        self.slider_master.setToolTip(f"Volume Master : {master_vol_val}% (Double-clic: 100%)")
        self.slider_master.valueChanged.connect(self._on_master_fader_changed)
        fader_row.addWidget(self.slider_master)

        self.master_vu = VuMeterBar(self)
        self.master_vu.setFixedHeight(130)
        self.master_vu.setFixedWidth(18)
        fader_row.addWidget(self.master_vu)

        layout.addLayout(fader_row)

        # Labels Master Volume & Crête en dB avec 1 décimale
        master_info_layout = QHBoxLayout()
        master_info_layout.setContentsMargins(0, 0, 0, 0)
        master_info_layout.setSpacing(2)

        self.lbl_master_db = QLabel(f"{master_vol_val}%")
        self.lbl_master_db.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.lbl_master_db.setStyleSheet("font-size: 10px; font-weight: bold; color: #fbbf24;")
        self.lbl_master_db.setToolTip(f"Volume Master : {master_vol_val}%")
        master_info_layout.addWidget(self.lbl_master_db)

        self.lbl_master_peak_db = PeakDbLabel(
            "-∞ dB",
            font_px=10,
            normal_color="#fbbf24",
            bg_normal="#171a22",
            bg_selected="#241e17",
            parent=self,
        )
        self.lbl_master_peak_db.setToolTip("Niveau Master réel en direct (dB)")
        master_info_layout.addWidget(self.lbl_master_peak_db)

        layout.addLayout(master_info_layout)

        self._update_master_fx_badges()
        return frame

    def _on_master_fx_clicked(self):
        """Bascule immédiatement vers l'onglet dédié Effets Master (Master FX Rack F6)."""
        self.master_fx_requested.emit()

    def _show_master_fx_menu(self):
        """Affiche le menu contextuel rapide pour les plugins sur la piste Master."""
        master_t = self.mixer.project.ensure_master_track() if self.mixer.project else None
        if not master_t:
            return
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

        # Option principale : Basculer vers l'onglet Effets Master
        act_tab = menu.addAction("👑 Ouvrir le Rack Effets Master (F6)...")
        act_tab.triggered.connect(self.master_fx_requested.emit)
        menu.addSeparator()

        # 1. Ouvrir / Éditer les plugins existants sur le Master (en excluant le mixeur lui-même)
        from ui.plugin_dialogs import open_native_plugin_editor
        plugins_found = False
        if hasattr(master_t, "plugins") and master_t.plugins:
            for p in master_t.plugins:
                if getattr(p, "plugin_type_id", None) == "novadaw.mixer":
                    continue
                p_name = getattr(p, "name", "Plugin")
                p_icon = getattr(p, "icon", "🎛️")
                act_open = menu.addAction(f"{p_icon} Ouvrir {p_name}")
                act_open.triggered.connect(lambda _, plug=p: open_native_plugin_editor(plug, self))
                plugins_found = True

        if plugins_found:
            menu.addSeparator()

        # 2. Ajouter un effet sur le Master
        act_add_eq = menu.addAction("📊 Ajouter Égaliseur Paramétrique...")
        act_add_eq.triggered.connect(self._add_master_equalizer)

        act_add_comp = menu.addAction("🗜️ Ajouter Compresseur Dynamique...")
        act_add_comp.triggered.connect(self._add_master_compressor)

        menu.addSeparator()

        # 3. Ouvrir l'inspecteur pour la piste Master
        act_insp = menu.addAction("🔍 Inspecter Piste Master (Inspecteur complet)...")
        act_insp.triggered.connect(lambda: self.track_selected.emit(master_t.id))

        menu.exec(self.btn_master_fx.mapToGlobal(self.btn_master_fx.rect().bottomLeft()))

    def _add_master_equalizer(self):
        master_t = self.mixer.project.ensure_master_track() if self.mixer.project else None
        if master_t:
            from plugins.registry import plugin_registry, ensure_plugins_loaded
            from ui.plugin_dialogs import open_native_plugin_editor
            ensure_plugins_loaded()
            eq = plugin_registry.create_plugin("novadaw.equalizer")
            if eq:
                master_t.add_plugin(eq)
                self.track_mixer_changed.emit()
                self._update_master_fx_badges()
                open_native_plugin_editor(eq, self)

    def _add_master_compressor(self):
        master_t = self.mixer.project.ensure_master_track() if self.mixer.project else None
        if master_t:
            from plugins.registry import plugin_registry, ensure_plugins_loaded
            from ui.plugin_dialogs import open_native_plugin_editor
            ensure_plugins_loaded()
            comp = plugin_registry.create_plugin("novadaw.compressor")
            if comp:
                master_t.add_plugin(comp)
                self.track_mixer_changed.emit()
                self._update_master_fx_badges()
                open_native_plugin_editor(comp, self)

    def _update_master_fx_badges(self):
        if not hasattr(self, "btn_master_fx"):
            return
        master_t = self.mixer.project.ensure_master_track() if self.mixer.project else None
        if master_t:
            # Exclure le plugin mixeur lui-même du décompte d'effets insérés
            fx_list = [p for p in getattr(master_t, "plugins", []) if getattr(p, "plugin_type_id", "") != "novadaw.mixer"]
            vst_list = getattr(master_t, "insert_effects", [])
            count = len(fx_list) + len(vst_list)
            if count > 0:
                self.btn_master_fx.setText(f"⚡ FX ({count})")
                names = [getattr(p, "name", "Effet") for p in fx_list]
                names += [os.path.basename(v) for v in vst_list]
                names_str = ", ".join(names)
                self.btn_master_fx.setToolTip(f"Effets Master actifs ({count}) : {names_str}\nClic : Ouvrir l'onglet Effets Master (F6)\nClic-droit : Menu rapide")
                self.btn_master_fx.setStyleSheet("""
                    QPushButton {
                        background-color: #d97706;
                        color: #ffffff;
                        border: 1px solid #fbbf24;
                        border-radius: 3px;
                        font-size: 10px;
                        font-weight: bold;
                        padding: 1px 4px;
                    }
                    QPushButton:hover {
                        background-color: #f59e0b;
                    }
                """)
            else:
                self.btn_master_fx.setText("⚡ FX")
                self.btn_master_fx.setToolTip("Effets Master : Aucun effet inséré\nClic : Ouvrir l'onglet Effets Master (F6)\nClic-droit : Menu rapide")
                self.btn_master_fx.setStyleSheet("""
                    QPushButton {
                        background-color: #1e2230;
                        color: #f59e0b;
                        border: 1px solid #d97706;
                        border-radius: 3px;
                        font-size: 10px;
                        font-weight: bold;
                        padding: 1px 4px;
                    }
                    QPushButton:hover {
                        background-color: #d97706;
                        color: #ffffff;
                    }
                """)
        else:
            self.btn_master_fx.setText("⚡ FX")

    def _on_master_fader_changed(self, val: int):
        vol = val / 100.0
        self.mixer.master_volume = vol
        if self.mixer.project and hasattr(self.mixer.project, "master_track") and self.mixer.project.master_track:
            self.mixer.project.master_track.volume = vol
        self.lbl_master_db.setText(f"{val}%")
        self.slider_master.setToolTip(f"Volume Master : {val}% (Double-clic: 100%)")
        self.master_volume_changed.emit(vol)
        self.track_mixer_changed.emit()

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
                strip.track_deleted.connect(self.track_deleted.emit)
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
            master_t = self.mixer.project.ensure_master_track() if self.mixer.project else None
            vol = master_t.volume if master_t else self.mixer.master_volume
            self.mixer.master_volume = vol
            m_val = int(round(vol * 100))
            self.slider_master.blockSignals(True)
            self.slider_master.setValue(m_val)
            self.slider_master.blockSignals(False)
            self.lbl_master_db.setText(f"{m_val}%")
            self.slider_master.setToolTip(f"Volume Master : {m_val}% (Double-clic: 100%)")
            self._update_master_fx_badges()
        if hasattr(self, "btn_mono"):
            self.btn_mono.blockSignals(True)
            self.btn_mono.setChecked(self.mixer.mono_switch)
            self.btn_mono.blockSignals(False)
        if hasattr(self, "btn_dim"):
            self.btn_dim.blockSignals(True)
            self.btn_dim.setChecked(self.mixer.dim_switch)
            self.btn_dim.blockSignals(False)

    def _update_meters(self):
        """Mise à jour périodique des VU-mètres Master et Pistes (30 FPS)"""
        # Master VU-Meter et affichage dynamique dB avec décimales
        pk_ml = -60.0 if math.isnan(self.mixer.peak_left_db) else self.mixer.peak_left_db
        pk_mr = -60.0 if math.isnan(self.mixer.peak_right_db) else self.mixer.peak_right_db
        self.master_vu.set_levels(pk_ml, pk_mr)
        if hasattr(self, "lbl_master_peak_db"):
            self.lbl_master_peak_db.set_level_db(max(pk_ml, pk_mr))

        # Décroissance douce des niveaux Master (~55 dB/s)
        self.mixer.peak_left_db = max(-60.0, pk_ml - 1.8)
        self.mixer.peak_right_db = max(-60.0, pk_mr - 1.8)

        # Mettre à jour toutes les tranches individuelles
        for track_id, strip in self.strips.items():
            if track_id in self.mixer.track_peaks:
                pk_l, pk_r = self.mixer.track_peaks[track_id]
                pk_l_val = -60.0 if math.isnan(pk_l) else pk_l
                pk_r_val = -60.0 if math.isnan(pk_r) else pk_r
                strip.set_levels(pk_l_val, pk_r_val)
                # Décroissance douce pour la piste
                new_l = max(-60.0, pk_l_val - 1.8)
                new_r = max(-60.0, pk_r_val - 1.8)
                self.mixer.track_peaks[track_id] = (new_l, new_r)
            else:
                strip.set_levels(-60.0, -60.0)
