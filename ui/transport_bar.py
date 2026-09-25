"""
ui/transport_bar.py - Barre de transport inférieure et centrée avec navigation complète
"""
import math
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QPushButton, QLabel, QDoubleSpinBox,
    QSlider, QFrame, QSpacerItem, QSizePolicy
)
from PySide6.QtCore import Qt, Signal, QTimer, QRectF
from PySide6.QtGui import QPainter, QColor, QLinearGradient, QPen, QBrush, QFont
from ui.track_header import ResetableSlider
from ui.glow_effects import set_button_glow


class MasterMeterWidget(QWidget):
    """
    VU-mètre Master stéréo avec barres L/R et indicateur d'écrêtage / distorsion (> 0 dB).
    """
    clip_reset = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(92, 30)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.level_l = 0.0
        self.level_r = 0.0
        self.peak_hold_l = 0.0
        self.peak_hold_r = 0.0
        self.is_clipped = False
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("VU-mètre Master stéréo. Indicateur CLIP : distorsion > 0 dB (Cliquer pour réinitialiser).")

    def set_levels(self, lvl_l: float, lvl_r: float, clipped: bool):
        self.level_l = float(lvl_l)
        self.level_r = float(lvl_r)
        self.peak_hold_l = max(self.level_l, self.peak_hold_l * 0.94)
        self.peak_hold_r = max(self.level_r, self.peak_hold_r * 0.94)
        if clipped or lvl_l > 1.0 or lvl_r > 1.0:
            self.is_clipped = True
        elif not clipped:
            self.is_clipped = False

        # Tooltip dynamique en dB
        db_l = 20.0 * math.log10(max(1e-4, self.level_l))
        db_r = 20.0 * math.log10(max(1e-4, self.level_r))
        if self.is_clipped or self.level_l > 1.0 or self.level_r > 1.0:
            max_db = max(db_l, db_r)
            self.setToolTip(f"⚠️ DISTORSION / ÉCRÊTAGE DÉTECTÉ (+{max_db:.1f} dB > 0 dB) !\nCliquer pour réinitialiser.")
        else:
            self.setToolTip(f"Niveau Master : L {db_l:.1f} dB | R {db_r:.1f} dB (OK)")
        self.update()

    def mousePressEvent(self, event):
        self.is_clipped = False
        self.clip_reset.emit()
        self.update()
        super().mousePressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w = self.width()
        h = self.height()

        # Fond vitré sombre
        painter.fillRect(0, 0, w, h, QColor("#11131a"))
        painter.setPen(QPen(QColor("#242838"), 1.0))
        painter.drawRoundedRect(0.5, 0.5, w - 1, h - 1, 3, 3)

        # Dimensions des barres L et R
        meter_x = 16
        clip_w = 30
        meter_w = max(10, w - meter_x - clip_w - 5)
        bar_h = 7
        y_l = 5
        y_r = 16

        # Labels "L" et "R"
        painter.setFont(QFont("Consolas", 7, QFont.Bold))
        painter.setPen(QColor("#64748b"))
        painter.drawText(3, y_l + bar_h - 1, "L")
        painter.drawText(3, y_r + bar_h - 1, "R")

        def draw_channel_bar(y_pos: int, lvl: float, pk_hold: float):
            painter.fillRect(meter_x, y_pos, meter_w, bar_h, QColor("#1e2230"))
            norm = min(1.2, lvl) / 1.2
            fill_w = int(meter_w * norm)
            if fill_w > 0:
                grad = QLinearGradient(meter_x, 0, meter_x + meter_w, 0)
                grad.setColorAt(0.0, QColor("#10b981"))
                grad.setColorAt(0.70, QColor("#10b981"))
                grad.setColorAt(0.83, QColor("#f59e0b"))
                grad.setColorAt(1.0, QColor("#ef4444"))
                painter.fillRect(meter_x, y_pos, fill_w, bar_h, grad)

            # Ligne de crête maintenue (Peak hold)
            if pk_hold > 0.01:
                pk_norm = min(1.2, pk_hold) / 1.2
                pk_x = int(meter_x + meter_w * pk_norm)
                pk_col = QColor("#ef4444") if pk_hold > 1.0 else QColor("#ffffff")
                painter.setPen(QPen(pk_col, 1.2))
                painter.drawLine(pk_x, y_pos, pk_x, y_pos + bar_h)

            # Repère 0 dB
            zero_x = int(meter_x + meter_w * (1.0 / 1.2))
            painter.setPen(QPen(QColor(255, 255, 255, 70), 0.8, Qt.DotLine))
            painter.drawLine(zero_x, y_pos, zero_x, y_pos + bar_h)

        draw_channel_bar(y_l, self.level_l, self.peak_hold_l)
        draw_channel_bar(y_r, self.level_r, self.peak_hold_r)

        # Indicateur LED de distorsion / clipping
        clip_rect = QRectF(w - clip_w - 3, 4, clip_w, 20)
        if self.is_clipped:
            painter.setBrush(QBrush(QColor("#dc2626")))
            painter.setPen(QPen(QColor("#fca5a5"), 1.2))
            painter.drawRoundedRect(clip_rect, 3, 3)
            painter.setFont(QFont("Segoe UI", 7, QFont.Bold))
            painter.setPen(QColor("#ffffff"))
            painter.drawText(clip_rect, Qt.AlignCenter, "CLIP")
        else:
            painter.setBrush(QBrush(QColor("#181b24")))
            painter.setPen(QPen(QColor("#2d3345"), 1.0))
            painter.drawRoundedRect(clip_rect, 3, 3)
            painter.setFont(QFont("Segoe UI", 7, QFont.Bold))
            painter.setPen(QColor("#475569"))
            painter.drawText(clip_rect, Qt.AlignCenter, "0dB")


class TransportBar(QWidget):
    play_toggled = Signal(bool)
    stop_clicked = Signal()
    record_toggled = Signal(bool)
    loop_toggled = Signal(bool)
    bpm_changed = Signal(float)
    master_volume_changed = Signal(float)
    master_clip_reset = Signal()
    goto_start_clicked = Signal()
    goto_end_clicked = Signal()
    step_rewind = Signal(float)    # Déplacement en beats (négatif)
    step_forward = Signal(float)   # Déplacement en beats (positif)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(50)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("""
            TransportBar {
                background-color: #171822;
                border-top: 1px solid #282a38;
                border-bottom: 1px solid #282a38;
            }
        """)

        # Timers pour le maintien enfoncé de Reculer / Avancer
        self._rewind_timer = QTimer(self)
        self._rewind_timer.setInterval(70)
        self._rewind_timer.timeout.connect(lambda: self.step_rewind.emit(-1.0))

        self._forward_timer = QTimer(self)
        self._forward_timer.setInterval(70)
        self._forward_timer.timeout.connect(lambda: self.step_forward.emit(1.0))

        self._init_ui()

    def _init_ui(self):
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(16, 4, 16, 4)
        main_layout.setSpacing(12)
        self.main_layout = main_layout

        # Centrage : Stretch à gauche
        main_layout.addStretch(1)

        # --- GROUPE NAVIGATION (Aller au début, Reculer, Stop, Play, Avancer, Aller à la fin) ---
        nav_container = QWidget()
        nav_container.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        nav_layout = QHBoxLayout(nav_container)
        nav_layout.setContentsMargins(0, 0, 0, 0)
        nav_layout.setSpacing(5)

        # 1. Aller au tout début
        self.btn_start = QPushButton("⏮")
        self.btn_start.setObjectName("btn_nav")
        self.btn_start.setFixedSize(34, 32)
        self.btn_start.setToolTip("Aller au tout début du morceau (Mesure 1)")
        self.btn_start.clicked.connect(self.goto_start_clicked.emit)
        nav_layout.addWidget(self.btn_start)

        # 2. Reculer (Rewind) avec maintien
        self.btn_rewind = QPushButton("⏪")
        self.btn_rewind.setObjectName("btn_nav")
        self.btn_rewind.setFixedSize(34, 32)
        self.btn_rewind.setToolTip("Reculer d'une mesure (Maintenir pour rembobiner)")
        self.btn_rewind.pressed.connect(self._start_rewind)
        self.btn_rewind.released.connect(self._stop_rewind)
        nav_layout.addWidget(self.btn_rewind)

        # 3. Stop
        self.btn_stop = QPushButton("■")
        self.btn_stop.setObjectName("btn_stop")
        self.btn_stop.setFixedSize(36, 32)
        self.btn_stop.setToolTip("Stop / Arrêt (Espace ou 0)")
        self.btn_stop.clicked.connect(self._on_stop)
        nav_layout.addWidget(self.btn_stop)

        # 4. Play / Pause
        self.btn_play = QPushButton("▶")
        self.btn_play.setObjectName("btn_play")
        self.btn_play.setCheckable(True)
        self.btn_play.setFixedSize(48, 32)
        self.btn_play.setToolTip("Lecture / Pause (Espace)")
        self.btn_play.clicked.connect(self._on_play)
        nav_layout.addWidget(self.btn_play)

        # 5. Avancer (Fast-Forward) avec maintien
        self.btn_forward = QPushButton("⏩")
        self.btn_forward.setObjectName("btn_nav")
        self.btn_forward.setFixedSize(34, 32)
        self.btn_forward.setToolTip("Avancer d'une mesure (Maintenir pour avancer)")
        self.btn_forward.pressed.connect(self._start_forward)
        self.btn_forward.released.connect(self._stop_forward)
        nav_layout.addWidget(self.btn_forward)

        # 6. Aller à la toute fin
        self.btn_end = QPushButton("⏭")
        self.btn_end.setObjectName("btn_nav")
        self.btn_end.setFixedSize(34, 32)
        self.btn_end.setToolTip("Aller à la fin du morceau (après le dernier bloc)")
        self.btn_end.clicked.connect(self.goto_end_clicked.emit)
        nav_layout.addWidget(self.btn_end)

        # 7. Record
        self.btn_record = QPushButton("●")
        self.btn_record.setObjectName("btn_record")
        self.btn_record.setCheckable(True)
        self.btn_record.setFixedSize(36, 32)
        self.btn_record.setToolTip("Enregistrer (Record) - Démarre la lecture et l'enregistrement sur les pistes armées (R)")
        self.btn_record.clicked.connect(self._on_record)
        nav_layout.addWidget(self.btn_record)

        # 8. Boucle
        self.btn_loop = QPushButton("🔁 Boucle")
        self.btn_loop.setObjectName("btn_loop")
        self.btn_loop.setCheckable(True)
        self.btn_loop.setChecked(True)
        set_button_glow(self.btn_loop, True, "#d97706", blur_radius=15, alpha=210)
        self.btn_loop.setFixedHeight(32)
        self.btn_loop.setMinimumWidth(84)
        self.btn_loop.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        self.btn_loop.setToolTip("Activer / Désactiver la lecture en boucle (Raccourci: L)")
        self.btn_loop.toggled.connect(self._on_loop)
        nav_layout.addWidget(self.btn_loop)

        main_layout.addWidget(nav_container)

        self._add_separator(main_layout)

        # --- COMPTEURS DIGITAUX ---
        time_container = QWidget()
        time_layout = QHBoxLayout(time_container)
        time_layout.setContentsMargins(0, 0, 0, 0)
        time_layout.setSpacing(6)

        lbl_bars = QLabel("MESURE")
        lbl_bars.setStyleSheet("font-size: 8px; font-weight: bold; color: #64748b;")
        time_layout.addWidget(lbl_bars)

        self.lbl_bars_display = QLabel("001 . 01 . 00")
        self.lbl_bars_display.setObjectName("digital_display")
        time_layout.addWidget(self.lbl_bars_display)

        self.lbl_time_display = QLabel("00:00:00.00")
        self.lbl_time_display.setObjectName("digital_time")
        time_layout.addWidget(self.lbl_time_display)

        main_layout.addWidget(time_container)

        self._add_separator(main_layout)

        # --- TEMPO BPM & SIGNATURE ---
        bpm_container = QWidget()
        bpm_layout = QHBoxLayout(bpm_container)
        bpm_layout.setContentsMargins(0, 0, 0, 0)
        bpm_layout.setSpacing(6)

        lbl_bpm = QLabel("BPM")
        lbl_bpm.setStyleSheet("font-size: 9px; font-weight: bold; color: #64748b;")
        bpm_layout.addWidget(lbl_bpm)

        self.spin_bpm = QDoubleSpinBox()
        self.spin_bpm.setRange(20.0, 300.0)
        self.spin_bpm.setValue(120.0)
        self.spin_bpm.setDecimals(1)
        self.spin_bpm.setFixedWidth(74)
        self.spin_bpm.setFixedHeight(30)
        self.spin_bpm.valueChanged.connect(self.bpm_changed.emit)
        bpm_layout.addWidget(self.spin_bpm)

        lbl_sig = QLabel("4 / 4")
        lbl_sig.setStyleSheet("font-family: Consolas; font-weight: bold; color: #cbd5e1; padding: 2px 4px;")
        bpm_layout.addWidget(lbl_sig)

        main_layout.addWidget(bpm_container)

        self._add_separator(main_layout)

        # --- VOLUME MASTER ---
        vol_container = QWidget()
        vol_layout = QHBoxLayout(vol_container)
        vol_layout.setContentsMargins(0, 0, 0, 0)
        vol_layout.setSpacing(6)

        lbl_vol = QLabel("🔊 MASTER")
        lbl_vol.setStyleSheet("font-size: 9px; font-weight: bold; color: #94a3b8;")
        vol_layout.addWidget(lbl_vol)

        self.slider_master = ResetableSlider(Qt.Horizontal, default_value=90)
        self.slider_master.setRange(0, 120)
        self.slider_master.setValue(90)
        self.slider_master.setFixedWidth(80)
        self.slider_master.setToolTip("Volume Master : 90% (Double-cliquer pour réinitialiser à 90%)")
        self.slider_master.valueChanged.connect(self._on_master_slider_changed)
        vol_layout.addWidget(self.slider_master)

        # VU-mètre Master Stéréo avec indicateur de distorsion / clipping (> 0 dB)
        self.master_meter = MasterMeterWidget(self)
        self.master_meter.clip_reset.connect(self.master_clip_reset.emit)
        vol_layout.addWidget(self.master_meter)

        main_layout.addWidget(vol_container)

        # Centrage : Stretch à droite
        main_layout.addStretch(1)

    def _add_separator(self, layout):
        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setStyleSheet("color: #2b2e3b; margin: 4px 2px;")
        layout.addWidget(sep)

    def _start_rewind(self):
        # 1 pas immédiat d'une mesure (4 temps)
        self.step_rewind.emit(-4.0)
        # Démarre le défilement continu rapide si maintenu
        self._rewind_timer.start()

    def _stop_rewind(self):
        self._rewind_timer.stop()

    def _start_forward(self):
        # 1 pas immédiat d'une mesure (4 temps)
        self.step_forward.emit(4.0)
        # Démarre le défilement continu rapide si maintenu
        self._forward_timer.start()

    def _stop_forward(self):
        self._forward_timer.stop()

    def _on_play(self, checked: bool):
        set_button_glow(self.btn_play, checked, "#10b981", blur_radius=20, alpha=240)
        self.play_toggled.emit(checked)

    def _on_record(self, checked: bool):
        set_button_glow(self.btn_record, checked, "#ff2244", blur_radius=20, alpha=240)
        self.record_toggled.emit(checked)

    def _on_loop(self, checked: bool):
        set_button_glow(self.btn_loop, checked, "#d97706", blur_radius=15, alpha=210)
        self.loop_toggled.emit(checked)

    def _on_stop(self):
        self.btn_play.blockSignals(True)
        self.btn_play.setChecked(False)
        self.btn_play.blockSignals(False)
        set_button_glow(self.btn_play, False, "#10b981")

        self.btn_record.blockSignals(True)
        self.btn_record.setChecked(False)
        self.btn_record.blockSignals(False)
        set_button_glow(self.btn_record, False, "#ff2244")

        self.stop_clicked.emit()

    def _on_master_slider_changed(self, val: int):
        self.slider_master.setToolTip(f"Volume Master : {val}% (Double-cliquer pour réinitialiser à 90%)")
        self.master_volume_changed.emit(val / 100.0)

    def update_position(self, beat: float, bpm: float):
        bar = int(beat // 4) + 1
        beat_in_bar = int(beat % 4) + 1
        sub_beat = int((beat % 1) * 100)
        self.lbl_bars_display.setText(f"{bar:03d} . {beat_in_bar:02d} . {sub_beat:02d}")

        sec = beat * (60.0 / bpm)
        mins = int(sec // 60)
        secs = int(sec % 60)
        millis = int((sec % 1) * 100)
        self.lbl_time_display.setText(f"00:{mins:02d}:{secs:02d}.{millis:02d}")

    def set_playing_state(self, is_playing: bool):
        self.btn_play.blockSignals(True)
        self.btn_play.setChecked(is_playing)
        self.btn_play.blockSignals(False)
        set_button_glow(self.btn_play, is_playing, "#10b981", blur_radius=20, alpha=240)

    def set_recording_state(self, is_recording: bool):
        self.btn_record.blockSignals(True)
        self.btn_record.setChecked(is_recording)
        self.btn_record.blockSignals(False)
        set_button_glow(self.btn_record, is_recording, "#ff2244", blur_radius=20, alpha=240)

    def update_master_meter(self, peak_l: float, peak_r: float, clipped: bool):
        if hasattr(self, "master_meter"):
            self.master_meter.set_levels(peak_l, peak_r, clipped)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "main_layout") and hasattr(self, "slider_master"):
            is_compact = self.width() < 1120
            self.main_layout.setContentsMargins(8 if is_compact else 16, 4, 8 if is_compact else 16, 4)
            self.main_layout.setSpacing(6 if is_compact else 12)
            self.slider_master.setFixedWidth(60 if is_compact else 80)
            if hasattr(self, "master_meter"):
                self.master_meter.setFixedSize(76 if is_compact else 92, 30)
