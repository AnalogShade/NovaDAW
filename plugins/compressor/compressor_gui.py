"""
plugins/compressor/compressor_gui.py - Interface graphique moderne pour le Compresseur Dynamique de Studio.

Comprend :
- Graphique de la courbe de transfert dynamique avec Soft-Knee, seuil interactif et bille dynamique temps réel
- Afficheur triple VU-mètre studio : Crête Entrée (In dBFS), Réduction de Gain (GR dB) et Crête Sortie (Out dBFS)
- Sliders précis pour Threshold, Ratio, Attack, Release, Knee, Makeup Gain et Mix (Dry/Wet)
- Sélecteurs pour le Mode de Détection (Peak vs RMS), Filtre Sidechain HPF 80 Hz, et Auto-Makeup
- Bouton Bypass temps réel à basculement instantané
- Synchronisation complète avec le gestionnaire de presets universel de NovaDAW
"""
from typing import Optional
import numpy as np

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSlider, QComboBox, QFrame, QGridLayout, QSizePolicy
)
from PySide6.QtCore import Qt, QTimer, QRectF, QPointF
from PySide6.QtGui import (
    QPainter, QPen, QBrush, QColor, QFont, QPainterPath,
    QLinearGradient
)

from plugins.compressor.compressor_plugin import CompressorPlugin


class CompressorTransferCurve(QFrame):
    """Tracé de la courbe de transfert du compresseur (Entrée dB vs Sortie dB) avec point actif temps réel"""

    def __init__(self, comp: CompressorPlugin, parent=None):
        super().__init__(parent)
        self.comp = comp
        self.setMinimumHeight(150)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet("background-color: #0b0d13; border: 1px solid #1f2432; border-radius: 6px;")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = float(self.width())
        h = float(self.height())
        margin = 32.0

        # Grille (-60 dB à 0 dB sur X et Y)
        grid_pen = QPen(QColor(28, 34, 46), 1, Qt.DotLine)
        painter.setPen(grid_pen)

        font_lbl = QFont("Segoe UI", 7)
        painter.setFont(font_lbl)

        for db in [-60, -48, -36, -24, -12, 0]:
            # Ligne X
            norm_x = (db + 60.0) / 60.0
            x = margin + norm_x * (w - 2 * margin)
            painter.drawLine(int(x), int(margin), int(x), int(h - margin))
            painter.setPen(QPen(QColor(100, 116, 139), 1))
            painter.drawText(int(x - 9), int(h - margin + 14), f"{db}")

            # Ligne Y
            norm_y = (db + 60.0) / 60.0
            y = margin + (1.0 - norm_y) * (h - 2 * margin)
            painter.setPen(grid_pen)
            painter.drawLine(int(margin), int(y), int(w - margin), int(y))
            painter.setPen(QPen(QColor(100, 116, 139), 1))
            painter.drawText(4, int(y + 3), f"{db}")

        # Ligne de référence 1:1 (diagonale sans compression)
        ref_pen = QPen(QColor(71, 85, 105, 100), 1, Qt.DashLine)
        painter.setPen(ref_pen)
        p1 = QPointF(margin, h - margin)
        p2 = QPointF(w - margin, margin)
        painter.drawLine(p1, p2)

        # Calcul de la courbe de compression avec soft-knee
        t = self.comp.threshold_db
        r = max(1.0, self.comp.ratio)
        knee = max(0.0, self.comp.knee_db)

        # Makeup gain additionnel pour le tracé
        auto_db = -(t * (1.0 - 1.0 / r)) * 0.5 if self.comp.auto_makeup else 0.0
        total_make = self.comp.makeup_gain_db + auto_db

        xs_db = np.linspace(-60.0, 0.0, 120)
        ys_db = np.zeros_like(xs_db)

        half_k = knee * 0.5
        two_k = 2.0 * knee if knee > 0.0 else 1.0
        slope = (1.0 / r) - 1.0

        for idx, x_val in enumerate(xs_db):
            diff = x_val - t
            if knee > 0.1:
                if diff <= -half_k:
                    gr = 0.0
                elif diff >= half_k:
                    gr = slope * diff
                else:
                    d_k = diff + half_k
                    gr = slope * (d_k * d_k) / two_k
            else:
                gr = slope * diff if diff > 0.0 else 0.0
            ys_db[idx] = min(0.0, max(-60.0, x_val + gr + total_make))

        # Tracé de la courbe
        path = QPainterPath()
        for idx in range(len(xs_db)):
            nx = (xs_db[idx] + 60.0) / 60.0
            ny = (ys_db[idx] + 60.0) / 60.0
            px = margin + nx * (w - 2 * margin)
            py = margin + (1.0 - ny) * (h - 2 * margin)
            if idx == 0:
                path.moveTo(px, py)
            else:
                path.lineTo(px, py)

        curve_pen = QPen(QColor(168, 85, 247), 2.2)
        painter.setPen(curve_pen)
        painter.drawPath(path)

        # Point de seuil (Threshold)
        nt = (t + 60.0) / 60.0
        yt = min(0.0, max(-60.0, t + total_make))
        nyt = (yt + 60.0) / 60.0
        pt_x = margin + nt * (w - 2 * margin)
        pt_y = margin + (1.0 - nyt) * (h - 2 * margin)

        painter.setBrush(QBrush(QColor(250, 204, 21)))
        painter.setPen(QPen(QColor(15, 23, 42), 1.5))
        painter.drawEllipse(QPointF(pt_x, pt_y), 4.5, 4.5)

        # Bille dynamique temps réel montrant le niveau du signal audio
        in_db = max(-60.0, min(0.0, self.comp.current_input_peak_db))
        out_db = max(-60.0, min(0.0, self.comp.current_output_peak_db))
        if in_db > -58.0:
            ball_nx = (in_db + 60.0) / 60.0
            ball_ny = (out_db + 60.0) / 60.0
            ball_x = margin + ball_nx * (w - 2 * margin)
            ball_y = margin + (1.0 - ball_ny) * (h - 2 * margin)

            # Lueur cyan
            glow_pen = QPen(QColor(56, 189, 248, 120), 8)
            painter.setPen(glow_pen)
            painter.drawPoint(QPointF(ball_x, ball_y))

            # Point central vif
            painter.setPen(QPen(QColor(255, 255, 255), 1))
            painter.setBrush(QBrush(QColor(56, 189, 248)))
            painter.drawEllipse(QPointF(ball_x, ball_y), 4.0, 4.0)


class StudioVUMeters(QFrame):
    """
    Module triple VU-mètre professionnel :
    - IN : Crête Entrée (-60 à 0 dBFS)
    - GR : Réduction de Gain (0 à -24 dB)
    - OUT : Crête Sortie (-60 à 0 dBFS)
    """

    def __init__(self, comp: CompressorPlugin, parent=None):
        super().__init__(parent)
        self.comp = comp
        self.setFixedWidth(96)
        self.setMinimumHeight(150)
        self.setStyleSheet("background-color: #0b0d13; border: 1px solid #1f2432; border-radius: 6px;")
        self._gr_peak = 0.0

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        h = self.height()
        meter_y = 18
        meter_h = h - 28

        # 1. En-têtes des barres
        painter.setFont(QFont("Segoe UI", 7, QFont.Bold))
        painter.setPen(QPen(QColor(148, 163, 184), 1))
        painter.drawText(6, 12, "IN")
        painter.setPen(QPen(QColor(248, 113, 113), 1))
        painter.drawText(38, 12, "GR")
        painter.setPen(QPen(QColor(56, 189, 248), 1))
        painter.drawText(70, 12, "OUT")

        # 2. Barres de crête Entrée (0 à -60 dBFS)
        in_db = max(-60.0, min(0.0, self.comp.current_input_peak_db))
        in_norm = (in_db + 60.0) / 60.0
        in_bar_h = int(meter_h * in_norm)
        # Fond
        painter.fillRect(6, meter_y, 18, meter_h, QBrush(QColor(18, 22, 32)))
        if in_bar_h > 0:
            grad_in = QLinearGradient(0, meter_y + meter_h, 0, meter_y)
            grad_in.setColorAt(0.0, QColor(34, 197, 94))   # Vert
            grad_in.setColorAt(0.7, QColor(234, 179, 8))   # Jaune
            grad_in.setColorAt(1.0, QColor(239, 68, 68))   # Rouge
            painter.fillRect(6, meter_y + meter_h - in_bar_h, 18, in_bar_h, QBrush(grad_in))

        # 3. Barre Réduction de Gain (0 à -24 dB) vers le bas
        gr_db = abs(min(0.0, self.comp.current_gain_reduction_db))
        self._gr_peak = max(self._gr_peak * 0.94, gr_db)
        gr_norm = min(1.0, gr_db / 24.0)
        gr_bar_h = int(meter_h * gr_norm)

        # Fond
        painter.fillRect(38, meter_y, 20, meter_h, QBrush(QColor(18, 22, 32)))
        if gr_bar_h > 0:
            grad_gr = QLinearGradient(0, meter_y, 0, meter_y + gr_bar_h)
            grad_gr.setColorAt(0.0, QColor(239, 68, 68))   # Rouge vif
            grad_gr.setColorAt(0.6, QColor(245, 158, 11))  # Ambre
            grad_gr.setColorAt(1.0, QColor(250, 204, 21))  # Jaune
            painter.fillRect(38, meter_y, 20, gr_bar_h, QBrush(grad_gr))

        # Ligne de crête de GR
        if self._gr_peak > 0.5:
            pk_norm = min(1.0, self._gr_peak / 24.0)
            pk_y = meter_y + int(meter_h * pk_norm)
            painter.setPen(QPen(QColor(255, 255, 255), 1.5))
            painter.drawLine(38, pk_y, 58, pk_y)

        # 4. Barre de crête Sortie (0 à -60 dBFS)
        out_db = max(-60.0, min(0.0, self.comp.current_output_peak_db))
        out_norm = (out_db + 60.0) / 60.0
        out_bar_h = int(meter_h * out_norm)
        painter.fillRect(70, meter_y, 18, meter_h, QBrush(QColor(18, 22, 32)))
        if out_bar_h > 0:
            grad_out = QLinearGradient(0, meter_y + meter_h, 0, meter_y)
            grad_out.setColorAt(0.0, QColor(34, 197, 94))
            grad_out.setColorAt(0.7, QColor(234, 179, 8))
            grad_out.setColorAt(1.0, QColor(239, 68, 68))
            painter.fillRect(70, meter_y + meter_h - out_bar_h, 18, out_bar_h, QBrush(grad_out))


class CompressorPluginWidget(QWidget):
    """Interface utilisateur moderne et complète du Compresseur Dynamique Studio"""

    def __init__(self, comp: CompressorPlugin, parent=None):
        super().__init__(parent)
        self.comp = comp
        self.setObjectName("compressor_widget")
        self.setStyleSheet("""
            QWidget#compressor_widget {
                background-color: #11131a;
                color: #e2e8f0;
            }
            QLabel {
                font-size: 11px;
                color: #94a3b8;
            }
            QLabel#title {
                font-weight: bold;
                font-size: 12px;
                color: #c084fc;
            }
            QPushButton {
                background-color: #1e2230;
                color: #f1f5f9;
                border: 1px solid #2d3748;
                border-radius: 4px;
                padding: 4px 10px;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #2b3244;
                border-color: #a855f7;
            }
            QPushButton:checked {
                background-color: #7e22ce;
                border-color: #c084fc;
                font-weight: bold;
                color: #ffffff;
            }
            QComboBox {
                background-color: #1a1e29;
                border: 1px solid #2d3748;
                border-radius: 3px;
                color: #f1f5f9;
                font-size: 10px;
                padding: 2px 6px;
            }
            QSlider::groove:horizontal {
                height: 4px;
                background: #1e2230;
                border-radius: 2px;
            }
            QSlider::sub-page:horizontal {
                background: #a855f7;
                border-radius: 2px;
            }
            QSlider::handle:horizontal {
                background: #f8fafc;
                width: 12px;
                margin: -4px 0;
                border-radius: 6px;
            }
        """)

        self._init_ui()

        # Timer pour l'animation haute réactivité (30 FPS)
        self.anim_timer = QTimer(self)
        self.anim_timer.setInterval(33)
        self.anim_timer.timeout.connect(self._on_timer_tick)
        self.anim_timer.start()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 10, 12, 10)
        main_layout.setSpacing(10)

        # 1. Barre supérieure : Titre + Presets + Options rapides + Bypass
        top_bar = QHBoxLayout()
        lbl_title = QLabel("🗜️ COMPRESSEUR DYNAMIQUE STUDIO")
        lbl_title.setObjectName("title")
        top_bar.addWidget(lbl_title)

        top_bar.addStretch()

        # Menu déroulant de Presets intégrés
        self.preset_container = QWidget()
        p_layout = QHBoxLayout(self.preset_container)
        p_layout.setContentsMargins(0, 0, 0, 0)
        p_layout.setSpacing(6)
        lbl_p = QLabel("Preset :")
        p_layout.addWidget(lbl_p)

        self.combo_presets = QComboBox()
        for name in self.comp.get_factory_presets().keys():
            self.combo_presets.addItem(name)
        self.combo_presets.currentIndexChanged.connect(self._on_preset_selected)
        p_layout.addWidget(self.combo_presets)
        top_bar.addWidget(self.preset_container)

        # Bouton Mode Détection : Peak vs RMS
        self.btn_mode = QPushButton("Mode: Peak" if self.comp.detection_mode == "peak" else "Mode: RMS")
        self.btn_mode.setToolTip("Basculer entre détection Peak (transitoires rapides) et RMS (musical/analogique)")
        self.btn_mode.clicked.connect(self._toggle_detection_mode)
        top_bar.addWidget(self.btn_mode)

        # Bouton Sidechain HPF (Low-cut 80 Hz)
        self.btn_hpf = QPushButton("SC HPF 80Hz")
        self.btn_hpf.setCheckable(True)
        self.btn_hpf.setChecked(self.comp.sidechain_hpf_hz >= 20.0)
        self.btn_hpf.setToolTip("Filtre passe-haut 80 Hz sur le sidechain pour éviter le pompage sur la basse")
        self.btn_hpf.clicked.connect(self._toggle_hpf)
        top_bar.addWidget(self.btn_hpf)

        # Bouton Auto-Makeup Gain
        self.btn_auto_makeup = QPushButton("Auto-Gain")
        self.btn_auto_makeup.setCheckable(True)
        self.btn_auto_makeup.setChecked(self.comp.auto_makeup)
        self.btn_auto_makeup.setToolTip("Compensation automatique du gain de sortie")
        self.btn_auto_makeup.clicked.connect(self._toggle_auto_makeup)
        top_bar.addWidget(self.btn_auto_makeup)

        # Bouton Bypass temps réel
        self.btn_bypass = QPushButton("Bypass")
        self.btn_bypass.setCheckable(True)
        self.btn_bypass.setChecked(not self.comp.enabled)
        self.btn_bypass.clicked.connect(self._toggle_bypass)
        top_bar.addWidget(self.btn_bypass)

        main_layout.addLayout(top_bar)

        # 2. Zone Visuelle : Courbe de Transfert + VU-Mètres Studio
        viz_row = QHBoxLayout()
        viz_row.setSpacing(10)

        self.transfer_curve = CompressorTransferCurve(self.comp, self)
        viz_row.addWidget(self.transfer_curve, stretch=4)

        # VU-mètres Studio (In, GR, Out)
        meter_box = QVBoxLayout()
        meter_box.setSpacing(3)
        self.meters = StudioVUMeters(self.comp, self)
        meter_box.addWidget(self.meters, alignment=Qt.AlignCenter)

        self.lbl_gr_val = QLabel("GR: 0.0 dB")
        self.lbl_gr_val.setAlignment(Qt.AlignCenter)
        self.lbl_gr_val.setStyleSheet("font-size: 9px; color: #f87171; font-weight: bold;")
        meter_box.addWidget(self.lbl_gr_val)

        viz_row.addLayout(meter_box)
        main_layout.addLayout(viz_row, stretch=2)

        # 3. Zone Contrôles : Grille de Paramètres
        controls_frame = QFrame()
        controls_frame.setStyleSheet("background: #0d0f15; border: 1px solid #1f2432; border-radius: 6px; padding: 6px;")
        grid = QGridLayout(controls_frame)
        grid.setContentsMargins(8, 8, 8, 8)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(8)

        # A. Threshold
        self.slider_thresh, self.lbl_thresh_val = self._create_param_row(
            grid, row=0, col=0, name="Threshold", min_val=-60, max_val=0,
            curr_val=int(self.comp.threshold_db), unit="dB",
            callback=self._on_thresh_changed
        )

        # B. Ratio
        self.slider_ratio, self.lbl_ratio_val = self._create_param_row(
            grid, row=0, col=2, name="Ratio", min_val=10, max_val=200,
            curr_val=int(self.comp.ratio * 10), unit=":1",
            display_div=10.0, callback=self._on_ratio_changed
        )

        # C. Attack
        self.slider_attack, self.lbl_attack_val = self._create_param_row(
            grid, row=1, col=0, name="Attack", min_val=1, max_val=200,
            curr_val=int(self.comp.attack_ms), unit="ms",
            callback=self._on_attack_changed
        )

        # D. Release
        self.slider_release, self.lbl_release_val = self._create_param_row(
            grid, row=1, col=2, name="Release", min_val=10, max_val=1000,
            curr_val=int(self.comp.release_ms), unit="ms",
            callback=self._on_release_changed
        )

        # E. Soft-Knee
        self.slider_knee, self.lbl_knee_val = self._create_param_row(
            grid, row=2, col=0, name="Knee", min_val=0, max_val=12,
            curr_val=int(self.comp.knee_db), unit="dB",
            callback=self._on_knee_changed
        )

        # F. Makeup Gain
        self.slider_makeup, self.lbl_makeup_val = self._create_param_row(
            grid, row=2, col=2, name="Makeup Gain", min_val=0, max_val=24,
            curr_val=int(self.comp.makeup_gain_db), unit="dB",
            callback=self._on_makeup_changed
        )

        # G. Mix (Dry / Wet)
        self.slider_mix, self.lbl_mix_val = self._create_param_row(
            grid, row=3, col=0, name="Mix (Dry/Wet)", min_val=0, max_val=100,
            curr_val=int(self.comp.mix * 100), unit="%",
            callback=self._on_mix_changed
        )

        main_layout.addWidget(controls_frame, stretch=2)

    def _create_param_row(self, grid: QGridLayout, row: int, col: int, name: str,
                          min_val: int, max_val: int, curr_val: int, unit: str,
                          display_div: float = 1.0, callback=None):
        lbl_name = QLabel(f"{name} :")
        lbl_name.setStyleSheet("font-size: 10px; font-weight: bold; color: #cbd5e1;")
        grid.addWidget(lbl_name, row, col)

        slider = QSlider(Qt.Horizontal)
        slider.setRange(min_val, max_val)
        slider.setValue(curr_val)
        slider.setFixedHeight(16)
        if callback:
            slider.valueChanged.connect(callback)
        grid.addWidget(slider, row, col + 1)

        val_str = f"{curr_val / display_div:.1f} {unit}" if display_div != 1.0 else f"{curr_val} {unit}"
        lbl_val = QLabel(val_str)
        lbl_val.setFixedWidth(55)
        lbl_val.setStyleSheet("font-size: 10px; color: #c084fc; font-weight: bold;")
        grid.addWidget(lbl_val, row, col + 1, Qt.AlignRight)

        return slider, lbl_val

    def _notify_project_modified(self):
        """Notifie l'arborescence parente qu'une modification a eu lieu."""
        p = self.parent()
        while p:
            if hasattr(p, "set_dirty"):
                p.set_dirty(True)
                break
            if hasattr(p, "project") and hasattr(p, "timeline_grid"):
                if hasattr(p, "set_dirty"):
                    p.set_dirty(True)
                break
            p = p.parent() if hasattr(p, "parent") else None

    def _on_thresh_changed(self, val: int):
        self.comp.threshold_db = float(val)
        self.lbl_thresh_val.setText(f"{val} dB")
        self.transfer_curve.update()
        self._notify_project_modified()

    def _on_ratio_changed(self, val: int):
        r = val / 10.0
        self.comp.ratio = r
        self.lbl_ratio_val.setText(f"{r:.1f}:1")
        self.transfer_curve.update()
        self._notify_project_modified()

    def _on_attack_changed(self, val: int):
        self.comp.attack_ms = float(val)
        self.lbl_attack_val.setText(f"{val} ms")
        self._notify_project_modified()

    def _on_release_changed(self, val: int):
        self.comp.release_ms = float(val)
        self.lbl_release_val.setText(f"{val} ms")
        self._notify_project_modified()

    def _on_knee_changed(self, val: int):
        self.comp.knee_db = float(val)
        self.lbl_knee_val.setText(f"{val} dB")
        self.transfer_curve.update()
        self._notify_project_modified()

    def _on_makeup_changed(self, val: int):
        self.comp.makeup_gain_db = float(val)
        self.lbl_makeup_val.setText(f"+{val} dB")
        self.transfer_curve.update()
        self._notify_project_modified()

    def _on_mix_changed(self, val: int):
        self.comp.mix = val / 100.0
        self.lbl_mix_val.setText(f"{val}%")
        self._notify_project_modified()

    def _toggle_detection_mode(self):
        new_mode = "rms" if self.comp.detection_mode == "peak" else "peak"
        self.comp.detection_mode = new_mode
        self.btn_mode.setText(f"Mode: {new_mode.upper()}")
        self._notify_project_modified()

    def _toggle_hpf(self, checked: bool):
        self.comp.sidechain_hpf_hz = 80.0 if checked else 0.0
        self._notify_project_modified()

    def _toggle_auto_makeup(self, checked: bool):
        self.comp.auto_makeup = checked
        self.transfer_curve.update()
        self._notify_project_modified()

    def _toggle_bypass(self, checked: bool):
        self.comp.enabled = not checked
        self.btn_bypass.setText("Bypass Actif" if checked else "Bypass")
        self._notify_project_modified()

    def _on_preset_selected(self, idx: int):
        name = self.combo_presets.currentText()
        presets = self.comp.get_factory_presets()
        p = presets.get(name)
        if not p:
            return
        self.comp.set_state(p)
        self._sync_all_controls()

    def _sync_all_controls(self):
        """Met à jour l'ensemble des contrôles graphiques avec l'état réel du plugin."""
        self.slider_thresh.blockSignals(True)
        self.slider_ratio.blockSignals(True)
        self.slider_attack.blockSignals(True)
        self.slider_release.blockSignals(True)
        self.slider_knee.blockSignals(True)
        self.slider_makeup.blockSignals(True)
        self.slider_mix.blockSignals(True)

        self.slider_thresh.setValue(int(self.comp.threshold_db))
        self.slider_ratio.setValue(int(round(self.comp.ratio * 10)))
        self.slider_attack.setValue(int(self.comp.attack_ms))
        self.slider_release.setValue(int(self.comp.release_ms))
        self.slider_knee.setValue(int(self.comp.knee_db))
        self.slider_makeup.setValue(int(self.comp.makeup_gain_db))
        self.slider_mix.setValue(int(round(self.comp.mix * 100)))

        self.lbl_thresh_val.setText(f"{int(self.comp.threshold_db)} dB")
        self.lbl_ratio_val.setText(f"{self.comp.ratio:.1f}:1")
        self.lbl_attack_val.setText(f"{int(self.comp.attack_ms)} ms")
        self.lbl_release_val.setText(f"{int(self.comp.release_ms)} ms")
        self.lbl_knee_val.setText(f"{int(self.comp.knee_db)} dB")
        self.lbl_makeup_val.setText(f"+{int(self.comp.makeup_gain_db)} dB")
        self.lbl_mix_val.setText(f"{int(round(self.comp.mix * 100))}%")

        self.btn_mode.setText(f"Mode: {self.comp.detection_mode.upper()}")
        self.btn_hpf.setChecked(self.comp.sidechain_hpf_hz >= 20.0)
        self.btn_auto_makeup.setChecked(self.comp.auto_makeup)
        self.btn_bypass.setChecked(not self.comp.enabled)
        self.btn_bypass.setText("Bypass Actif" if not self.comp.enabled else "Bypass")

        self.slider_thresh.blockSignals(False)
        self.slider_ratio.blockSignals(False)
        self.slider_attack.blockSignals(False)
        self.slider_release.blockSignals(False)
        self.slider_knee.blockSignals(False)
        self.slider_makeup.blockSignals(False)
        self.slider_mix.blockSignals(False)

        self.transfer_curve.update()

    def _on_timer_tick(self):
        """Met à jour l'animation fluide temps réel (Courbe, VU-Mètres, GR)"""
        gr = self.comp.current_gain_reduction_db
        self.lbl_gr_val.setText(f"GR: {gr:.1f} dB")
        self.meters.update()
        self.transfer_curve.update()
