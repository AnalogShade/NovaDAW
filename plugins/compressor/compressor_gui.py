"""
plugins/compressor/compressor_gui.py - Interface graphique moderne pour le Compresseur Dynamique.

Comprend :
- Graphique de la courbe de transfert dynamique avec Soft-Knee et seuil interactif
- VU-mètre animé de Réduction de Gain (GR Meter 0 à -24 dB)
- VU-mètres de crête Entrée / Sortie
- Sliders précis pour Threshold, Ratio, Attack, Release, Knee, Makeup Gain et Mix (Dry/Wet)
- Liste de presets de studio prêts à l'emploi
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
    """Tracé de la courbe de transfert du compresseur (Entrée dB vs Sortie dB)"""

    def __init__(self, comp: CompressorPlugin, parent=None):
        super().__init__(parent)
        self.comp = comp
        self.setMinimumHeight(140)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet("background-color: #0d0f14; border: 1px solid #1f2430; border-radius: 6px;")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = float(self.width())
        h = float(self.height())
        margin = 30.0

        # Grille (-60 dB à 0 dB sur X et Y)
        grid_pen = QPen(QColor(30, 36, 48), 1, Qt.DotLine)
        painter.setPen(grid_pen)

        font_lbl = QFont("Segoe UI", 7)
        painter.setFont(font_lbl)

        for db in [-60, -48, -36, -24, -12, 0]:
            # Ligne X
            norm_x = (db + 60.0) / 60.0
            x = margin + norm_x * (w - 2 * margin)
            painter.drawLine(int(x), int(margin), int(x), int(h - margin))
            painter.setPen(QPen(QColor(100, 116, 139), 1))
            painter.drawText(int(x - 8), int(h - margin + 14), f"{db}")

            # Ligne Y
            norm_y = (db + 60.0) / 60.0
            y = margin + (1.0 - norm_y) * (h - 2 * margin)
            painter.setPen(grid_pen)
            painter.drawLine(int(margin), int(y), int(w - margin), int(y))
            painter.setPen(QPen(QColor(100, 116, 139), 1))
            painter.drawText(6, int(y + 3), f"{db}")

        # Ligne de référence 1:1 (diagonale sans compression)
        ref_pen = QPen(QColor(71, 85, 105, 120), 1, Qt.DashLine)
        painter.setPen(ref_pen)
        p1 = QPointF(margin, h - margin)
        p2 = QPointF(w - margin, margin)
        painter.drawLine(p1, p2)

        # Calcul de la courbe de compression avec soft-knee
        t = self.comp.threshold_db
        r = max(1.0, self.comp.ratio)
        knee = max(0.0, self.comp.knee_db)

        xs_db = np.linspace(-60.0, 0.0, 100)
        ys_db = np.zeros_like(xs_db)

        for idx, x_val in enumerate(xs_db):
            diff = x_val - t
            if knee > 0.1:
                if 2.0 * diff < -knee:
                    ys_db[idx] = x_val
                elif abs(2.0 * diff) <= knee:
                    ys_db[idx] = x_val + (1.0 / r - 1.0) * ((diff + knee / 2.0) ** 2) / (2.0 * knee)
                else:
                    ys_db[idx] = t + diff / r
            else:
                ys_db[idx] = x_val if diff <= 0 else t + diff / r

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

        curve_pen = QPen(QColor(168, 85, 247), 2.0)
        painter.setPen(curve_pen)
        painter.drawPath(path)

        # Point de seuil (Threshold)
        nt = (t + 60.0) / 60.0
        yt = t if knee < 0.1 else t
        nyt = (yt + 60.0) / 60.0
        pt_x = margin + nt * (w - 2 * margin)
        pt_y = margin + (1.0 - nyt) * (h - 2 * margin)

        painter.setBrush(QBrush(QColor(250, 204, 21)))
        painter.setPen(QPen(QColor(15, 23, 42), 1.5))
        painter.drawEllipse(QPointF(pt_x, pt_y), 4.0, 4.0)


class GainReductionMeter(QFrame):
    """Afficheur bargraph vertical/horizontal de la Réduction de Gain (0 à -24 dB)"""

    def __init__(self, comp: CompressorPlugin, parent=None):
        super().__init__(parent)
        self.comp = comp
        self.setFixedWidth(28)
        self.setMinimumHeight(140)
        self.setStyleSheet("background-color: #0b0d12; border: 1px solid #1f2430; border-radius: 4px;")

    def paintEvent(self, event):
        painter = QPainter(self)
        w = self.width()
        h = self.height()

        gr_db = abs(min(0.0, self.comp.current_gain_reduction_db))
        # gr_db : 0 (aucune réduction) à 24 (forte réduction)
        max_meter_db = 24.0
        norm = min(1.0, gr_db / max_meter_db)

        # Tracé du bargraph de haut en bas (comme les compresseurs professionnels)
        meter_h = int((h - 8) * norm)
        if meter_h > 0:
            gradient = QLinearGradient(0, 4, 0, 4 + meter_h)
            gradient.setColorAt(0.0, QColor(239, 68, 68))    # Rouge vif
            gradient.setColorAt(0.5, QColor(245, 158, 11))   # Ambre
            gradient.setColorAt(1.0, QColor(250, 204, 21))   # Jaune
            painter.fillRect(4, 4, w - 8, meter_h, QBrush(gradient))

        # Lignes repères (0, -3, -6, -12, -18, -24)
        painter.setFont(QFont("Segoe UI", 6))
        painter.setPen(QPen(QColor(148, 163, 184), 1))
        for db in [0, 6, 12, 18, 24]:
            y_pos = int(4 + (db / max_meter_db) * (h - 8))
            painter.drawLine(w - 6, y_pos, w - 2, y_pos)


class CompressorPluginWidget(QWidget):
    """Interface utilisateur principale du Compresseur"""

    PRESETS = {
        "Défaut (Transparent)": {"threshold_db": -18.0, "ratio": 4.0, "attack_ms": 15.0, "release_ms": 120.0, "knee_db": 4.0, "makeup_gain_db": 0.0, "mix": 1.0},
        "Voix Douce (Opto)": {"threshold_db": -22.0, "ratio": 3.0, "attack_ms": 30.0, "release_ms": 250.0, "knee_db": 6.0, "makeup_gain_db": 3.0, "mix": 1.0},
        "Batterie Punch (VCA)": {"threshold_db": -16.0, "ratio": 6.0, "attack_ms": 5.0, "release_ms": 60.0, "knee_db": 2.0, "makeup_gain_db": 2.5, "mix": 0.85},
        "Basse Électrique": {"threshold_db": -20.0, "ratio": 5.0, "attack_ms": 10.0, "release_ms": 150.0, "knee_db": 4.0, "makeup_gain_db": 2.0, "mix": 1.0},
        "Master Bus Glue": {"threshold_db": -12.0, "ratio": 2.0, "attack_ms": 40.0, "release_ms": 300.0, "knee_db": 5.0, "makeup_gain_db": 1.0, "mix": 1.0},
        "Compression Parallèle NY": {"threshold_db": -28.0, "ratio": 12.0, "attack_ms": 2.0, "release_ms": 80.0, "knee_db": 1.0, "makeup_gain_db": 6.0, "mix": 0.40},
    }

    def __init__(self, comp: CompressorPlugin, parent=None):
        super().__init__(parent)
        self.comp = comp
        self.setObjectName("compressor_widget")
        self.setStyleSheet("""
            QWidget#compressor_widget {
                background-color: #12141a;
                color: #e2e8f0;
            }
            QLabel {
                font-size: 11px;
                color: #94a3b8;
            }
            QLabel#title {
                font-weight: bold;
                font-size: 12px;
                color: #a855f7;
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
                border-color: #a855f7;
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
                background: #f1f5f9;
                width: 12px;
                margin: -4px 0;
                border-radius: 6px;
            }
        """)

        self._init_ui()

        # Timer pour l'animation des compteurs et de la réduction de gain (30 FPS)
        self.anim_timer = QTimer(self)
        self.anim_timer.setInterval(33)
        self.anim_timer.timeout.connect(self._on_timer_tick)
        self.anim_timer.start()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 10, 12, 10)
        main_layout.setSpacing(10)

        # 1. Barre supérieure : Titre + Presets + Bypass
        top_bar = QHBoxLayout()
        lbl_title = QLabel("🗜️ COMPRESSEUR DYNAMIQUE MODULAIRE")
        lbl_title.setObjectName("title")
        top_bar.addWidget(lbl_title)

        top_bar.addStretch()

        # Menu déroulant de Presets
        lbl_p = QLabel("Preset :")
        top_bar.addWidget(lbl_p)

        self.combo_presets = QComboBox()
        for name in self.PRESETS.keys():
            self.combo_presets.addItem(name)
        self.combo_presets.currentIndexChanged.connect(self._on_preset_selected)
        top_bar.addWidget(self.combo_presets)

        # Bouton Bypass
        self.btn_bypass = QPushButton("Bypass")
        self.btn_bypass.setCheckable(True)
        self.btn_bypass.setChecked(not self.comp.enabled)
        self.btn_bypass.clicked.connect(self._toggle_bypass)
        top_bar.addWidget(self.btn_bypass)

        main_layout.addLayout(top_bar)

        # 2. Zone Visuelle : Graphique de Courbe de Transfert + VU-Mètre GR
        viz_row = QHBoxLayout()
        viz_row.setSpacing(10)

        self.transfer_curve = CompressorTransferCurve(self.comp, self)
        viz_row.addWidget(self.transfer_curve, stretch=4)

        # Conteneur GR Meter avec label
        gr_box = QVBoxLayout()
        gr_box.setSpacing(3)
        lbl_gr = QLabel("GR (dB)")
        lbl_gr.setAlignment(Qt.AlignCenter)
        lbl_gr.setStyleSheet("font-size: 9px; font-weight: bold; color: #f87171;")
        gr_box.addWidget(lbl_gr)

        self.gr_meter = GainReductionMeter(self.comp, self)
        gr_box.addWidget(self.gr_meter, alignment=Qt.AlignCenter)

        self.lbl_gr_val = QLabel("0.0 dB")
        self.lbl_gr_val.setAlignment(Qt.AlignCenter)
        self.lbl_gr_val.setStyleSheet("font-size: 8px; color: #94a3b8;")
        gr_box.addWidget(self.lbl_gr_val)

        viz_row.addLayout(gr_box)
        main_layout.addLayout(viz_row, stretch=2)

        # 3. Zone Contrôles : Grille de Potentiomètres / Sliders
        controls_frame = QFrame()
        controls_frame.setStyleSheet("background: #0f1117; border: 1px solid #1f2430; border-radius: 6px; padding: 6px;")
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
        lbl_val.setStyleSheet("font-size: 10px; color: #a855f7; font-weight: bold;")
        grid.addWidget(lbl_val, row, col + 1, Qt.AlignRight)

        return slider, lbl_val

    def _on_thresh_changed(self, val: int):
        self.comp.threshold_db = float(val)
        self.lbl_thresh_val.setText(f"{val} dB")
        self.transfer_curve.update()

    def _on_ratio_changed(self, val: int):
        r = val / 10.0
        self.comp.ratio = r
        self.lbl_ratio_val.setText(f"{r:.1f}:1")
        self.transfer_curve.update()

    def _on_attack_changed(self, val: int):
        self.comp.attack_ms = float(val)
        self.lbl_attack_val.setText(f"{val} ms")

    def _on_release_changed(self, val: int):
        self.comp.release_ms = float(val)
        self.lbl_release_val.setText(f"{val} ms")

    def _on_knee_changed(self, val: int):
        self.comp.knee_db = float(val)
        self.lbl_knee_val.setText(f"{val} dB")
        self.transfer_curve.update()

    def _on_makeup_changed(self, val: int):
        self.comp.makeup_gain_db = float(val)
        self.lbl_makeup_val.setText(f"+{val} dB")

    def _on_mix_changed(self, val: int):
        self.comp.mix = val / 100.0
        self.lbl_mix_val.setText(f"{val}%")

    def _on_preset_selected(self, idx: int):
        name = self.combo_presets.currentText()
        p = self.PRESETS.get(name)
        if not p:
            return

        self.slider_thresh.setValue(int(p["threshold_db"]))
        self.slider_ratio.setValue(int(p["ratio"] * 10))
        self.slider_attack.setValue(int(p["attack_ms"]))
        self.slider_release.setValue(int(p["release_ms"]))
        self.slider_knee.setValue(int(p["knee_db"]))
        self.slider_makeup.setValue(int(p["makeup_gain_db"]))
        self.slider_mix.setValue(int(p["mix"] * 100))

    def _toggle_bypass(self, checked: bool):
        self.comp.enabled = not checked
        self.btn_bypass.setText("Bypass Actif" if checked else "Bypass")

    def _on_timer_tick(self):
        """Met à jour l'animation du VU-mètre de réduction de gain"""
        gr = self.comp.current_gain_reduction_db
        self.lbl_gr_val.setText(f"{gr:.1f} dB")
        self.gr_meter.update()
