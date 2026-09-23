"""
plugins/equalizer/equalizer_gui.py - Interface graphique moderne pour l'Égaliseur Paramétrique.

Comprend :
- Visualiseur de courbe de réponse en fréquence interactive avec repères logarithmiques
- Nœuds déplaçables à la souris pour régler la fréquence et le gain directement sur le graphe
- Sélecteur de mode 3 bandes, 10 bandes, 12 bandes ou 24 bandes
- Panneau de faders précis pour chaque bande
- Boutons Reset Flat et Bypass global
"""
from typing import Optional, List
import numpy as np

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSlider, QComboBox, QFrame, QScrollArea, QDoubleSpinBox,
    QButtonGroup, QSizePolicy
)
from PySide6.QtCore import Qt, QPointF, Signal, QRectF
from PySide6.QtGui import (
    QPainter, QPen, QBrush, QColor, QFont, QPainterPath,
    QLinearGradient
)

from plugins.equalizer.equalizer_plugin import EqualizerPlugin, EqualizerBand


class FrequencyResponseCurve(QFrame):
    """Widget de tracé graphique de la réponse en fréquence (20 Hz - 20 kHz, -24 à +24 dB)"""
    band_changed = Signal()

    def __init__(self, eq: EqualizerPlugin, parent=None):
        super().__init__(parent)
        self.eq = eq
        self.setMinimumHeight(180)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet("background-color: #0d0f14; border: 1px solid #1f2430; border-radius: 6px;")
        self.setMouseTracking(True)

        self.dragging_band_idx: Optional[int] = None
        self.hover_band_idx: Optional[int] = None

    def freq_to_x(self, freq: float, width: float) -> float:
        """Convertit une fréquence en coordonnée X (échelle logarithmique de 20 Hz à 20 000 Hz)"""
        min_f, max_f = 20.0, 20000.0
        log_min, log_max = np.log10(min_f), np.log10(max_f)
        freq_clamped = max(min_f, min(max_f, freq))
        norm = (np.log10(freq_clamped) - log_min) / (log_max - log_min)
        margin = 40.0
        return margin + norm * (width - 2 * margin)

    def x_to_freq(self, x: float, width: float) -> float:
        margin = 40.0
        w = max(1.0, width - 2 * margin)
        norm = max(0.0, min(1.0, (x - margin) / w))
        min_f, max_f = 20.0, 20000.0
        log_min, log_max = np.log10(min_f), np.log10(max_f)
        return float(10.0 ** (log_min + norm * (log_max - log_min)))

    def db_to_y(self, db: float, height: float) -> float:
        """Convertit des dB (-24 à +24) en coordonnée Y"""
        min_db, max_db = -24.0, 24.0
        margin = 25.0
        h = max(1.0, height - 2 * margin)
        norm = (db - min_db) / (max_db - min_db)
        # norm 1 = +24dB (en haut), norm 0 = -24dB (en bas)
        return margin + (1.0 - norm) * h

    def y_to_db(self, y: float, height: float) -> float:
        margin = 25.0
        h = max(1.0, height - 2 * margin)
        norm = 1.0 - max(0.0, min(1.0, (y - margin) / h))
        return float(-24.0 + norm * 48.0)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = float(self.width())
        h = float(self.height())
        margin_x = 40.0
        margin_y = 25.0

        # 1. Grille d'arrière-plan
        grid_pen = QPen(QColor(30, 36, 48), 1, Qt.DotLine)
        painter.setPen(grid_pen)

        # Lignes horizontales de gain (+18, +12, +6, 0, -6, -12, -18 dB)
        font_axis = QFont("Segoe UI", 8)
        painter.setFont(font_axis)
        for db in [18, 12, 6, 0, -6, -12, -18]:
            y = self.db_to_y(db, h)
            if db == 0:
                painter.setPen(QPen(QColor(56, 189, 248, 90), 1, Qt.SolidLine))
            else:
                painter.setPen(grid_pen)
            painter.drawLine(int(margin_x), int(y), int(w - margin_x), int(y))

            # Texte dB à gauche
            painter.setPen(QPen(QColor(100, 116, 139), 1))
            painter.drawText(6, int(y + 3), f"{'+' if db > 0 else ''}{db}dB")

        # Lignes verticales de fréquence (30, 60, 125, 250, 500, 1k, 2k, 4k, 8k, 16k)
        freq_grid = [
            (30, "30"), (60, "60"), (125, "125"), (250, "250"), (500, "500"),
            (1000, "1k"), (2000, "2k"), (4000, "4k"), (8000, "8k"), (16000, "16k")
        ]
        for f, lbl in freq_grid:
            x = self.freq_to_x(f, w)
            painter.setPen(grid_pen)
            painter.drawLine(int(x), int(margin_y), int(x), int(h - margin_y))
            painter.setPen(QPen(QColor(100, 116, 139), 1))
            painter.drawText(int(x - 10), int(h - 8), lbl)

        # 2. Calcul de la courbe de réponse
        n_points = max(100, int(w - 2 * margin_x))
        freqs = np.geomspace(20.0, 20000.0, n_points)
        curve_dbs = self.eq.compute_magnitude_response(freqs)

        # Remplissage sous la courbe (gradient cyan / violet)
        path = QPainterPath()
        first_x = self.freq_to_x(freqs[0], w)
        first_y = self.db_to_y(curve_dbs[0], h)
        path.moveTo(first_x, first_y)

        zero_y = self.db_to_y(0.0, h)

        for i in range(1, n_points):
            px = self.freq_to_x(freqs[i], w)
            py = self.db_to_y(curve_dbs[i], h)
            path.lineTo(px, py)

        # Fermer le chemin pour le dégradé
        fill_path = QPainterPath(path)
        fill_path.lineTo(self.freq_to_x(freqs[-1], w), zero_y)
        fill_path.lineTo(first_x, zero_y)
        fill_path.closeSubpath()

        gradient = QLinearGradient(0, int(margin_y), 0, int(h - margin_y))
        gradient.setColorAt(0.0, QColor(56, 189, 248, 50))
        gradient.setColorAt(0.5, QColor(56, 189, 248, 20))
        gradient.setColorAt(1.0, QColor(168, 85, 247, 50))
        painter.fillPath(fill_path, QBrush(gradient))

        # Tracé de la ligne de courbe
        curve_pen = QPen(QColor(56, 189, 248), 2.0)
        painter.setPen(curve_pen)
        painter.drawPath(path)

        # 3. Tracé des nœuds de réglage de chaque bande
        for idx, band in enumerate(self.eq.bands):
            bx = self.freq_to_x(band.frequency, w)
            by = self.db_to_y(band.gain_db, h)

            is_active = (idx == self.dragging_band_idx or idx == self.hover_band_idx)
            radius = 6.0 if is_active else 4.5

            # Couleur selon état
            if not band.enabled:
                node_color = QColor(100, 116, 139)
            elif is_active:
                node_color = QColor(250, 204, 21)  # Jaune vif
            else:
                node_color = QColor(56, 189, 248)

            painter.setBrush(QBrush(node_color))
            painter.setPen(QPen(QColor(15, 23, 42), 1.5))
            painter.drawEllipse(QPointF(bx, by), radius, radius)

            # Numéro de la bande
            painter.setFont(QFont("Segoe UI", 7, QFont.Bold))
            painter.setPen(QPen(QColor(241, 245, 249), 1))
            painter.drawText(int(bx - 3), int(by - 7), str(band.band_id))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            w = float(self.width())
            h = float(self.height())
            pos = event.position()
            # Chercher le nœud le plus proche
            closest_idx = None
            closest_dist = 20.0  # Rayon de sélection 20px
            for idx, band in enumerate(self.eq.bands):
                bx = self.freq_to_x(band.frequency, w)
                by = self.db_to_y(band.gain_db, h)
                dist = np.hypot(pos.x() - bx, pos.y() - by)
                if dist < closest_dist:
                    closest_dist = dist
                    closest_idx = idx

            if closest_idx is not None:
                self.dragging_band_idx = closest_idx
                self.update()

    def mouseMoveEvent(self, event):
        w = float(self.width())
        h = float(self.height())
        pos = event.position()

        if self.dragging_band_idx is not None and 0 <= self.dragging_band_idx < len(self.eq.bands):
            band = self.eq.bands[self.dragging_band_idx]
            new_f = self.x_to_freq(pos.x(), w)
            new_gain = self.y_to_db(pos.y(), h)
            band.frequency = round(new_f, 1)
            band.gain_db = round(new_gain, 1)
            band.invalidate_cache()
            self.band_changed.emit()
            self.update()
        else:
            # Détection de survol
            closest_idx = None
            closest_dist = 15.0
            for idx, band in enumerate(self.eq.bands):
                bx = self.freq_to_x(band.frequency, w)
                by = self.db_to_y(band.gain_db, h)
                dist = np.hypot(pos.x() - bx, pos.y() - by)
                if dist < closest_dist:
                    closest_dist = dist
                    closest_idx = idx

            if closest_idx != self.hover_band_idx:
                self.hover_band_idx = closest_idx
                self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.dragging_band_idx = None
            self.update()

    def wheelEvent(self, event):
        """Molette de la souris pour ajuster le facteur Q de la bande survolée"""
        if self.hover_band_idx is not None and 0 <= self.hover_band_idx < len(self.eq.bands):
            band = self.eq.bands[self.hover_band_idx]
            delta = event.angleDelta().y() / 120.0
            new_q = max(0.2, min(10.0, band.q + delta * 0.1))
            band.q = round(new_q, 2)
            band.invalidate_cache()
            self.band_changed.emit()
            self.update()
            event.accept()
        else:
            super().wheelEvent(event)


class EqualizerPluginWidget(QWidget):
    """Widget complet de contrôle de l'égaliseur paramétrique"""

    def __init__(self, eq: EqualizerPlugin, parent=None):
        super().__init__(parent)
        self.eq = eq
        self.setObjectName("equalizer_widget")
        self.setStyleSheet("""
            QWidget#equalizer_widget {
                background-color: #12141a;
                color: #e2e8f0;
            }
            QLabel {
                font-size: 11px;
                color: #94a3b8;
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
                border-color: #38bdf8;
            }
            QPushButton:checked {
                background-color: #0284c7;
                border-color: #38bdf8;
                font-weight: bold;
                color: #ffffff;
            }
            QSlider::groove:vertical {
                width: 5px;
                background: #1e2230;
                border-radius: 2px;
            }
            QSlider::sub-page:vertical {
                background: #1e2230;
            }
            QSlider::add-page:vertical {
                background: #38bdf8;
            }
            QSlider::handle:vertical {
                background: #f1f5f9;
                height: 12px;
                margin: 0 -4px;
                border-radius: 6px;
            }
            QComboBox {
                background-color: #1a1e29;
                border: 1px solid #2d3748;
                border-radius: 3px;
                color: #f1f5f9;
                font-size: 10px;
                padding: 2px 4px;
            }
        """)

        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)

        # 1. Barre supérieure : Titre + Sélecteur de Bandes + Bypass + Reset
        top_bar = QHBoxLayout()
        top_bar.setSpacing(8)

        lbl_title = QLabel("📊 ÉGALISEUR PARAMÉTRIQUE MODULAIRE")
        lbl_title.setStyleSheet("font-weight: bold; font-size: 12px; color: #38bdf8;")
        top_bar.addWidget(lbl_title)

        top_bar.addStretch()

        # Sélecteur rapide du nombre de bandes (3, 10, 12, 24)
        lbl_bands = QLabel("Mode :")
        top_bar.addWidget(lbl_bands)

        self.btn_group_bands = QButtonGroup(self)
        self.band_buttons = {}

        for count in [3, 10, 12, 24]:
            btn = QPushButton(f"{count} Bandes")
            btn.setCheckable(True)
            if len(self.eq.bands) == count:
                btn.setChecked(True)
            btn.clicked.connect(lambda _, c=count: self._change_band_count(c))
            self.btn_group_bands.addButton(btn)
            self.band_buttons[count] = btn
            top_bar.addWidget(btn)

        # Séparateur
        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setStyleSheet("color: #2d3748;")
        top_bar.addWidget(sep)

        # Bouton Reset Flat
        btn_flat = QPushButton("Reset Flat")
        btn_flat.clicked.connect(self._reset_flat)
        top_bar.addWidget(btn_flat)

        # Bouton Bypass
        self.btn_bypass = QPushButton("Bypass")
        self.btn_bypass.setCheckable(True)
        self.btn_bypass.setChecked(not self.eq.enabled)
        self.btn_bypass.clicked.connect(self._toggle_bypass)
        top_bar.addWidget(self.btn_bypass)

        layout.addLayout(top_bar)

        # 2. Zone Graphique de Courbe
        self.curve_widget = FrequencyResponseCurve(self.eq, self)
        self.curve_widget.band_changed.connect(self._update_controls_from_bands)
        layout.addWidget(self.curve_widget, stretch=3)

        # 3. Zone Inférieure : Faders de chaque bande (dans un QScrollArea)
        self.scroll_bands = QScrollArea()
        self.scroll_bands.setWidgetResizable(True)
        self.scroll_bands.setFixedHeight(180)
        self.scroll_bands.setStyleSheet("border: 1px solid #1f2430; background: #0f1117; border-radius: 4px;")

        self.bands_container = QWidget()
        self.bands_layout = QHBoxLayout(self.bands_container)
        self.bands_layout.setContentsMargins(8, 6, 8, 6)
        self.bands_layout.setSpacing(8)
        self.scroll_bands.setWidget(self.bands_container)

        layout.addWidget(self.scroll_bands, stretch=2)

        # Reconstruire les bandes
        self._rebuild_band_controls()

    def _change_band_count(self, count: int):
        self.eq.set_band_count(count)
        for c, btn in self.band_buttons.items():
            btn.setChecked(c == count)
        self._rebuild_band_controls()
        self.curve_widget.update()

    def _reset_flat(self):
        """Réinitialise tous les gains à 0 dB"""
        for band in self.eq.bands:
            band.gain_db = 0.0
            band.invalidate_cache()
        self.eq.reset()
        self._update_controls_from_bands()
        self.curve_widget.update()

    def _toggle_bypass(self, checked: bool):
        self.eq.enabled = not checked
        self.btn_bypass.setText("Bypass Actif" if checked else "Bypass")
        self.curve_widget.update()

    def _rebuild_band_controls(self):
        """Reconstruit les tranches de faders de bandes"""
        while self.bands_layout.count() > 0:
            child = self.bands_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        self.band_widgets = []

        for idx, band in enumerate(self.eq.bands):
            col = QFrame()
            col.setFixedWidth(64 if len(self.eq.bands) <= 12 else 52)
            col.setStyleSheet("background: #141721; border-radius: 4px; padding: 2px;")
            col_l = QVBoxLayout(col)
            col_l.setContentsMargins(2, 4, 2, 4)
            col_l.setSpacing(3)
            col_l.setAlignment(Qt.AlignCenter)

            # En-tête bande (Numéro + Fréquence)
            freq_str = f"{int(band.frequency)}Hz" if band.frequency < 1000 else f"{band.frequency/1000:.1f}k"
            lbl_b = QLabel(f"B{band.band_id}\n{freq_str}")
            lbl_b.setAlignment(Qt.AlignCenter)
            lbl_b.setStyleSheet("font-size: 9px; font-weight: bold; color: #38bdf8;")
            col_l.addWidget(lbl_b)

            # Gain Slider vertical
            slider = QSlider(Qt.Vertical)
            slider.setRange(-24, 24)
            slider.setValue(int(band.gain_db))
            slider.setFixedHeight(75)
            slider.setToolTip(f"Gain : {band.gain_db:.1f} dB")
            slider.valueChanged.connect(lambda val, b=band: self._on_slider_gain_changed(val, b))
            col_l.addWidget(slider, alignment=Qt.AlignCenter)

            # Label de valeur dB
            lbl_val = QLabel(f"{band.gain_db:+.1f}")
            lbl_val.setAlignment(Qt.AlignCenter)
            lbl_val.setStyleSheet("font-size: 8px; color: #94a3b8;")
            col_l.addWidget(lbl_val)

            # Bouton On/Off de la bande
            btn_on = QPushButton("On")
            btn_on.setCheckable(True)
            btn_on.setChecked(band.enabled)
            btn_on.setFixedHeight(16)
            btn_on.setStyleSheet("font-size: 8px; padding: 0px;")
            btn_on.toggled.connect(lambda chk, b=band: self._on_band_toggled(chk, b))
            col_l.addWidget(btn_on)

            self.bands_layout.addWidget(col)
            self.band_widgets.append({
                "slider": slider,
                "lbl_val": lbl_val,
                "lbl_b": lbl_b,
                "btn_on": btn_on,
                "band": band
            })

        self.bands_layout.addStretch()

    def _on_slider_gain_changed(self, val: int, band: EqualizerBand):
        band.gain_db = float(val)
        band.invalidate_cache()
        for item in self.band_widgets:
            if item["band"] == band:
                item["lbl_val"].setText(f"{val:+.1f}")
                break
        self.curve_widget.update()

    def _on_band_toggled(self, checked: bool, band: EqualizerBand):
        band.enabled = checked
        band.invalidate_cache()
        self.curve_widget.update()

    def _update_controls_from_bands(self):
        """Met à jour les curseurs lorsque l'utilisateur déplace un nœud à la souris"""
        for item in self.band_widgets:
            band = item["band"]
            item["slider"].blockSignals(True)
            item["slider"].setValue(int(band.gain_db))
            item["slider"].blockSignals(False)
            item["lbl_val"].setText(f"{band.gain_db:+.1f}")
            freq_str = f"{int(band.frequency)}Hz" if band.frequency < 1000 else f"{band.frequency/1000:.1f}k"
            item["lbl_b"].setText(f"B{band.band_id}\n{freq_str}")
