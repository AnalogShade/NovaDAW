"""
plugins/vocal_glide/vocal_glide_gui.py - Interface graphique studio pour Nova Vocal Glide.

Harmonie visuelle avec le thème sombre NovaDAW (#11131a / #0b0d13, accents cyan #38bdf8 & émeraude #10b981) :
- Bannière artistique studio avec image personnalisée (assets/vocal_glide_header.png)
- Bouton d'aide intégré (?) ouvrant un guide interactif complet sur l'utilisation du plugin
- Oscilloscope temps réel défilant (Entrée, Sortie corrigée, Cible & Seuil déplaçables à la souris)
- Fader motorisé visuel 3D animé en temps réel dans sa plage Boost/Cut
- Triple VU-mètre studio (IN, GAIN, OUT)
- Contrôles complets : Target, Seuil d'activité, Agressivité, Vitesse, Max Boost, Max Cut, Output Trim,
  Sidechain Vocal, Comportement en pause, Write Live, Monitoring Micro Live (🎧)
- Barre de cristallisation en 1 clic vers la piste d'automatisation Volume native de NovaDAW.
"""
import os
from typing import Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSlider, QComboBox, QFrame, QGridLayout, QSizePolicy, QCheckBox,
    QDialog, QTextBrowser
)
from PySide6.QtCore import Qt, QTimer, QRectF
from PySide6.QtGui import (
    QPainter, QPen, QBrush, QColor, QFont, QPainterPath,
    QLinearGradient, QPixmap
)

from plugins.vocal_glide.vocal_glide_plugin import VocalGlidePlugin


def _asset_path(filename: str) -> str:
    return os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "assets", filename)
    )


class VocalGlideHelpDialog(QDialog):
    """Fenêtre d'aide intégrée expliquant l'utilisation complète de Nova Vocal Glide."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("❔ Guide d'utilisation — Nova Vocal Glide")
        self.resize(580, 520)
        self.setMinimumSize(460, 400)
        self.setStyleSheet("""
            QDialog {
                background-color: #11131a;
                color: #e2e8f0;
            }
            QTextBrowser {
                background-color: #0b0d13;
                color: #e2e8f0;
                border: 1px solid #1f2737;
                border-radius: 8px;
                padding: 12px;
                font-size: 12px;
            }
            QPushButton {
                background-color: #0369a1;
                color: #ffffff;
                border: 1px solid #38bdf8;
                border-radius: 5px;
                padding: 6px 18px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #0284c7;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        header = QLabel("🎤 COMMENT UTILISER NOVA VOCAL GLIDE")
        header.setStyleSheet("font-size: 14px; font-weight: bold; color: #38bdf8; letter-spacing: 0.6px;")
        layout.addWidget(header)

        browser = QTextBrowser(self)
        browser.setOpenExternalLinks(False)
        browser.setHtml("""
        <style>
            h3 { color: #38bdf8; margin-top: 12px; margin-bottom: 4px; font-size: 13px; }
            p, li { color: #cbd5e1; line-height: 1.45; }
            b { color: #f8fafc; }
            .tip { color: #34d399; font-weight: bold; }
            .warn { color: #fbbf24; font-weight: bold; }
        </style>
        <p><b>Nova Vocal Glide</b> maintient automatiquement le volume de votre voix à un niveau régulier en pilotant un fader virtuel fluide — idéal avant un compresseur ou pour remplacer une longue automatisation manuelle.</p>

        <h3>1. Réglages essentiels</h3>
        <ul>
            <li><b>Niveau Cible (Target)</b> : le niveau moyen visé (ligne bleue sur le graphe). Vous pouvez aussi <b>glisser directement cette ligne à la souris</b> sur l'oscilloscope.</li>
            <li><b>Seuil d'activité (Noise)</b> : ligne pointillée ambrée. En dessous de ce seuil (pauses, respirations, bruit de pièce), le fader <span class="warn">cesse de monter</span> pour éviter d'amplifier le bruit de fond.</li>
            <li><b>Agressivité / Sensibilité</b> : à 100%, corrige chaque écart vers la cible. Plus bas (ex. 50–70%), préserve davantage la dynamique naturelle.</li>
            <li><b>Vitesse (Slow ↔ Fast)</b> : lente pour des phrases chantées douces, rapide pour rattraper des mots isolés ou du rap.</li>
            <li><b>Boost Max / Cut Max</b> : définit l'amplitude maximale autorisée vers le haut (+dB) et vers le bas (-dB).</li>
            <li><b>SC Vocal</b> : active un filtre passe-bande (100 Hz – 6.5 kHz) sur le détecteur pour suivre l'énergie réelle de la voix sans réagir aux plosives ou sibilances.</li>
        </ul>

        <h3>2. Mode Live pendant que vous chantez</h3>
        <ul>
            <li>Cliquez sur <b>🎧 Live Micro</b> en haut à droite (ou sur <b>🎧 Mon Live</b> dans l'en-tête de la piste audio).</li>
            <li>Chantez dans votre micro : vous voyez en direct la courbe, le fader motorisé bouger et vous entendez l'égalisation automatique du volume, même lorsque la lecture est arrêtée.</li>
        </ul>

        <h3>3. Cristalliser en Piste d'Automatisation Volume</h3>
        <ul>
            <li>Une fois vos réglages satisfaisants sur une piste audio, choisissez la <b>Précision</b> (<i>Musical</i> recommandé) et la portée (<i>Toute la piste</i> ou <i>Zone de boucle</i>).</li>
            <li>Cliquez sur <span class="tip">✨ Cristalliser en Automatisation Volume</span> : NovaDAW analyse vos clips avec anticipation (lookahead) et crée automatiquement une courbe éditable sur la piste d'automatisation <b>Volume</b>.</li>
            <li>L'option <b>Auto-Bypass après cristallisation</b> désactive automatiquement le plugin ensuite afin de ne pas appliquer deux fois la correction.</li>
            <li>Vous pouvez également activer <b>Write Live</b> pour enregistrer les mouvements du fader en direct pendant la lecture.</li>
        </ul>
        """)
        layout.addWidget(browser, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_close = QPushButton("Compris")
        btn_close.clicked.connect(self.accept)
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)


class VocalGlideHeaderBanner(QFrame):
    """Bannière d'en-tête studio avec image artistique et fondu sombre intégré."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(64)
        self.setObjectName("vocal_glide_banner")
        self.setStyleSheet(
            "QFrame#vocal_glide_banner { background-color: #0b0d13; border: 1px solid #1f2737; border-radius: 8px; }"
        )
        banner_file = _asset_path("vocal_glide_header.png")
        self._pixmap = QPixmap(banner_file) if os.path.exists(banner_file) else QPixmap()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        rect = QRectF(1, 1, self.width() - 2, self.height() - 2)
        clip = QPainterPath()
        clip.addRoundedRect(rect, 7.0, 7.0)
        painter.setClipPath(clip)

        if not self._pixmap.isNull():
            scaled = self._pixmap.scaled(
                self.width(),
                self.height(),
                Qt.KeepAspectRatioByExpanding,
                Qt.SmoothTransformation,
            )
            sx = max(0, (scaled.width() - self.width()) // 2)
            sy = max(0, (scaled.height() - self.height()) // 2)
            painter.setOpacity(0.55)
            painter.drawPixmap(0, 0, scaled, sx, sy, self.width(), self.height())
            painter.setOpacity(1.0)

        grad = QLinearGradient(0, 0, self.width(), 0)
        grad.setColorAt(0.0, QColor(11, 13, 19, 235))
        grad.setColorAt(0.32, QColor(11, 13, 19, 135))
        grad.setColorAt(0.68, QColor(11, 13, 19, 135))
        grad.setColorAt(1.0, QColor(11, 13, 19, 235))
        painter.fillRect(rect, QBrush(grad))


class VocalGlideScopeWidget(QFrame):
    """
    Oscilloscope défilant en temps réel :
    - Trace grisée : niveau vocal d'entrée (dBFS)
    - Trace cyan/émeraude lumineuse : niveau corrigé (dBFS)
    - Ligne cyan : Target Level (déplaçable verticalement à la souris)
    - Ligne ambrée pointillée : Seuil d'activité / Noise Floor (déplaçable à la souris)
    """

    def __init__(self, plugin: VocalGlidePlugin, on_params_dragged=None, parent=None):
        super().__init__(parent)
        self.plugin = plugin
        self.on_params_dragged = on_params_dragged
        self.setMinimumHeight(175)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)
        self.setStyleSheet(
            "background-color: #090b10; border: 1px solid #1f2737; border-radius: 7px;"
        )
        self._dragging: Optional[str] = None
        self._hover_target: Optional[str] = None

    def _db_to_y(self, db: float, h: float) -> float:
        margin_y = 14.0
        usable = max(10.0, h - 2.0 * margin_y)
        clamped = max(-66.0, min(0.0, float(db)))
        norm = (clamped + 66.0) / 66.0
        return margin_y + (1.0 - norm) * usable

    def _y_to_db(self, y: float, h: float) -> float:
        margin_y = 14.0
        usable = max(10.0, h - 2.0 * margin_y)
        norm = 1.0 - max(0.0, min(1.0, (float(y) - margin_y) / usable))
        return -66.0 + norm * 66.0

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            h = float(self.height())
            y = float(event.position().y())
            yt = self._db_to_y(self.plugin.target_db, h)
            ys = self._db_to_y(self.plugin.activity_threshold_db, h)
            if abs(y - yt) <= 9.0:
                self._dragging = "target"
                event.accept()
                return
            if abs(y - ys) <= 9.0:
                self._dragging = "threshold"
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        h = float(self.height())
        y = float(event.position().y())
        if self._dragging == "target":
            db = round(max(-42.0, min(-3.0, self._y_to_db(y, h))), 1)
            self.plugin.target_db = db
            if self.on_params_dragged:
                self.on_params_dragged("target")
            self.update()
            return
        if self._dragging == "threshold":
            db = round(max(-72.0, min(-12.0, self._y_to_db(y, h))), 1)
            self.plugin.activity_threshold_db = db
            if self.on_params_dragged:
                self.on_params_dragged("threshold")
            self.update()
            return

        yt = self._db_to_y(self.plugin.target_db, h)
        ys = self._db_to_y(self.plugin.activity_threshold_db, h)
        if abs(y - yt) <= 9.0:
            self._hover_target = "target"
            self.setCursor(Qt.SizeVerCursor)
        elif abs(y - ys) <= 9.0:
            self._hover_target = "threshold"
            self.setCursor(Qt.SizeVerCursor)
        else:
            self._hover_target = None
            self.setCursor(Qt.ArrowCursor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._dragging is not None:
            self._dragging = None
            self.update()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = float(self.width())
        h = float(self.height())
        margin_l = 36.0
        margin_r = 10.0
        plot_w = max(20.0, w - margin_l - margin_r)

        y_thresh = self._db_to_y(self.plugin.activity_threshold_db, h)
        y_bottom = self._db_to_y(-66.0, h)
        if y_bottom > y_thresh:
            painter.fillRect(
                QRectF(margin_l, y_thresh, plot_w, y_bottom - y_thresh),
                QColor(245, 158, 11, 14),
            )

        painter.setFont(QFont("Segoe UI", 7))
        for db in [0, -12, -24, -36, -48, -60]:
            y = self._db_to_y(db, h)
            painter.setPen(QPen(QColor(26, 33, 46), 1, Qt.DotLine))
            painter.drawLine(int(margin_l), int(y), int(w - margin_r), int(y))
            painter.setPen(QPen(QColor(100, 116, 139), 1))
            painter.drawText(5, int(y + 3), f"{db}")

        hist = self.plugin.get_history_snapshot()
        if len(hist) >= 2:
            n = len(hist)
            max_pts = 220
            step_x = plot_w / float(max(1, max_pts - 1))
            start_x = (w - margin_r) - (n - 1) * step_x

            in_path = QPainterPath()
            out_path = QPainterPath()
            fill_path = QPainterPath()

            for i, (in_db, out_db, _g_db, _act) in enumerate(hist):
                px = max(margin_l, start_x + i * step_x)
                py_in = self._db_to_y(in_db, h)
                py_out = self._db_to_y(out_db, h)
                if i == 0:
                    in_path.moveTo(px, py_in)
                    out_path.moveTo(px, py_out)
                    fill_path.moveTo(px, y_bottom)
                    fill_path.lineTo(px, py_out)
                else:
                    in_path.lineTo(px, py_in)
                    out_path.lineTo(px, py_out)
                    fill_path.lineTo(px, py_out)

            last_x = max(margin_l, start_x + (n - 1) * step_x)
            fill_path.lineTo(last_x, y_bottom)
            fill_path.closeSubpath()

            area_grad = QLinearGradient(0, 14, 0, y_bottom)
            area_grad.setColorAt(0.0, QColor(16, 185, 129, 55))
            area_grad.setColorAt(0.6, QColor(56, 189, 248, 22))
            area_grad.setColorAt(1.0, QColor(56, 189, 248, 0))
            painter.fillPath(fill_path, QBrush(area_grad))

            painter.setPen(QPen(QColor(148, 163, 184, 130), 1.4))
            painter.drawPath(in_path)

            out_col = QColor(16, 185, 129) if self.plugin.enabled else QColor(100, 116, 139)
            painter.setPen(QPen(out_col, 2.2))
            painter.drawPath(out_path)

        y_tgt = self._db_to_y(self.plugin.target_db, h)
        tgt_w = 2.4 if (self._dragging == "target" or self._hover_target == "target") else 1.7
        painter.setPen(QPen(QColor(56, 189, 248, 230), tgt_w))
        painter.drawLine(int(margin_l), int(y_tgt), int(w - margin_r), int(y_tgt))
        painter.setFont(QFont("Segoe UI", 7, QFont.Bold))
        painter.setPen(QColor(56, 189, 248))
        painter.drawText(int(margin_l + 6), int(max(11, y_tgt - 4)), f"CIBLE {self.plugin.target_db:.1f} dB")

        thr_w = 2.2 if (self._dragging == "threshold" or self._hover_target == "threshold") else 1.4
        painter.setPen(QPen(QColor(245, 158, 11, 210), thr_w, Qt.DashLine))
        painter.drawLine(int(margin_l), int(y_thresh), int(w - margin_r), int(y_thresh))
        painter.setPen(QColor(245, 158, 11))
        painter.drawText(
            int(margin_l + 6),
            int(min(h - 4, y_thresh + 11)),
            f"SEUIL ACTIVITÉ {self.plugin.activity_threshold_db:.1f} dB",
        )

        painter.setFont(QFont("Segoe UI", 7, QFont.Bold))
        painter.setPen(QColor(148, 163, 184))
        painter.drawText(int(w - 135), 14, "─ IN")
        painter.setPen(QColor(16, 185, 129))
        painter.drawText(int(w - 92), 14, "─ OUT CORRIGÉ")


class MotorizedGlideFaderWidget(QFrame):
    """Fader motorisé hardware animé en temps réel dans la plage [-MaxCut .. +MaxBoost]."""

    def __init__(self, plugin: VocalGlidePlugin, parent=None):
        super().__init__(parent)
        self.plugin = plugin
        self.setFixedWidth(96)
        self.setMinimumHeight(175)
        self.setStyleSheet(
            "background-color: #090b10; border: 1px solid #1f2737; border-radius: 7px;"
        )

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = float(self.width())
        h = float(self.height())
        track_x = w * 0.54
        top_y = 22.0
        bot_y = h - 26.0
        rail_h = max(30.0, bot_y - top_y)

        def db_to_y(db: float) -> float:
            clamped = max(-15.0, min(15.0, float(db)))
            norm = (clamped + 15.0) / 30.0
            return top_y + (1.0 - norm) * rail_h

        painter.setFont(QFont("Segoe UI", 7, QFont.Bold))
        painter.setPen(QColor(148, 163, 184))
        painter.drawText(QRectF(0, 4, w, 14), Qt.AlignCenter, "GLIDE FADER")

        painter.setFont(QFont("Consolas", 7))
        for db in [15, 10, 5, 0, -5, -10, -15]:
            y = db_to_y(db)
            is_zero = (db == 0)
            painter.setPen(QPen(QColor(56, 189, 248, 150) if is_zero else QColor(51, 65, 85), 1))
            painter.drawLine(int(track_x - 16), int(y), int(track_x + 16), int(y))
            painter.setPen(QColor(226, 232, 240) if is_zero else QColor(100, 116, 139))
            lbl = f"+{db}" if db > 0 else f"{db}"
            painter.drawText(4, int(y + 3), lbl)

        painter.fillRect(QRectF(track_x - 3, top_y, 6, rail_h), QColor(18, 23, 34))

        y_max = db_to_y(self.plugin.max_boost_db)
        y_min = db_to_y(-self.plugin.max_cut_db)
        painter.fillRect(
            QRectF(track_x - 2, y_max, 4, max(2.0, y_min - y_max)),
            QColor(56, 189, 248, 110),
        )

        g_db = self.plugin.current_glide_gain_db if self.plugin.enabled else 0.0
        y_zero = db_to_y(0.0)
        y_cap = db_to_y(g_db)
        bar_col = QColor(16, 185, 129) if g_db >= 0.0 else QColor(245, 158, 11)
        if not self.plugin.enabled:
            bar_col = QColor(100, 116, 139)
        painter.fillRect(
            QRectF(track_x - 2, min(y_zero, y_cap), 4, abs(y_cap - y_zero)),
            bar_col,
        )

        cap_w, cap_h = 30.0, 15.0
        cap_rect = QRectF(track_x - cap_w * 0.5, y_cap - cap_h * 0.5, cap_w, cap_h)
        cap_grad = QLinearGradient(cap_rect.topLeft(), cap_rect.bottomLeft())
        cap_grad.setColorAt(0.0, QColor(71, 85, 105))
        cap_grad.setColorAt(0.5, QColor(30, 41, 59))
        cap_grad.setColorAt(1.0, QColor(15, 23, 42))
        painter.setBrush(QBrush(cap_grad))
        painter.setPen(QPen(QColor(148, 163, 184, 180), 1))
        painter.drawRoundedRect(cap_rect, 3.0, 3.0)

        led_col = (
            QColor(16, 185, 129) if (self.plugin.enabled and self.plugin.is_vocal_active)
            else QColor(56, 189, 248) if self.plugin.enabled
            else QColor(100, 116, 139)
        )
        painter.setPen(QPen(led_col, 2.0))
        painter.drawLine(
            int(cap_rect.left() + 4),
            int(y_cap),
            int(cap_rect.right() - 4),
            int(y_cap),
        )

        sign = "+" if g_db > 0.05 else ""
        painter.setFont(QFont("Consolas", 8, QFont.Bold))
        painter.setPen(bar_col)
        painter.drawText(QRectF(0, h - 20, w, 16), Qt.AlignCenter, f"{sign}{g_db:.1f} dB")


class VocalGlideVUMeters(QFrame):
    """Triple VU-mètre studio : IN (dBFS), GAIN (Glide dB), OUT (dBFS)."""

    def __init__(self, plugin: VocalGlidePlugin, parent=None):
        super().__init__(parent)
        self.plugin = plugin
        self.setFixedWidth(96)
        self.setMinimumHeight(175)
        self.setStyleSheet(
            "background-color: #090b10; border: 1px solid #1f2737; border-radius: 7px;"
        )

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        h = self.height()
        meter_y = 20
        meter_h = h - 30

        painter.setFont(QFont("Segoe UI", 7, QFont.Bold))
        painter.setPen(QColor(148, 163, 184))
        painter.drawText(8, 13, "IN")
        painter.setPen(QColor(56, 189, 248))
        painter.drawText(36, 13, "GAIN")
        painter.setPen(QColor(16, 185, 129))
        painter.drawText(69, 13, "OUT")

        in_db = max(-60.0, min(0.0, self.plugin.current_input_db))
        in_norm = (in_db + 60.0) / 60.0
        in_h = int(meter_h * in_norm)
        painter.fillRect(8, meter_y, 18, meter_h, QColor(18, 22, 32))
        if in_h > 0:
            grad_in = QLinearGradient(0, meter_y + meter_h, 0, meter_y)
            grad_in.setColorAt(0.0, QColor(34, 197, 94))
            grad_in.setColorAt(0.72, QColor(234, 179, 8))
            grad_in.setColorAt(1.0, QColor(239, 68, 68))
            painter.fillRect(8, meter_y + meter_h - in_h, 18, in_h, QBrush(grad_in))

        painter.fillRect(39, meter_y, 18, meter_h, QColor(18, 22, 32))
        mid_y = meter_y + meter_h // 2
        g_db = max(-15.0, min(15.0, self.plugin.current_glide_gain_db if self.plugin.enabled else 0.0))
        g_px = int((abs(g_db) / 15.0) * (meter_h * 0.5))
        if g_px > 0:
            if g_db >= 0.0:
                painter.fillRect(39, mid_y - g_px, 18, g_px, QColor(16, 185, 129))
            else:
                painter.fillRect(39, mid_y, 18, g_px, QColor(245, 158, 11))
        painter.setPen(QPen(QColor(148, 163, 184, 140), 1))
        painter.drawLine(37, mid_y, 59, mid_y)

        out_db = max(-60.0, min(0.0, self.plugin.current_output_db))
        out_norm = (out_db + 60.0) / 60.0
        out_h = int(meter_h * out_norm)
        painter.fillRect(70, meter_y, 18, meter_h, QColor(18, 22, 32))
        if out_h > 0:
            grad_out = QLinearGradient(0, meter_y + meter_h, 0, meter_y)
            grad_out.setColorAt(0.0, QColor(16, 185, 129))
            grad_out.setColorAt(0.72, QColor(56, 189, 248))
            grad_out.setColorAt(1.0, QColor(239, 68, 68))
            painter.fillRect(70, meter_y + meter_h - out_h, 18, out_h, QBrush(grad_out))


class VocalGlideWidget(QWidget):
    """Interface complète du plugin Nova Vocal Glide."""

    def __init__(self, plugin: VocalGlidePlugin, parent=None):
        super().__init__(parent)
        self.plugin = plugin
        self.setObjectName("vocal_glide_widget")
        self.setStyleSheet("""
            QWidget#vocal_glide_widget {
                background-color: #11131a;
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
                border-radius: 5px;
                padding: 4px 10px;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #2b3244;
                border-color: #38bdf8;
            }
            QPushButton:checked {
                background-color: #0369a1;
                border-color: #38bdf8;
                font-weight: bold;
                color: #ffffff;
            }
            QComboBox {
                background-color: #1a1e29;
                border: 1px solid #2d3748;
                border-radius: 4px;
                color: #f1f5f9;
                font-size: 10px;
                padding: 3px 8px;
            }
            QCheckBox {
                color: #cbd5e1;
                font-size: 10px;
                spacing: 6px;
            }
            QSlider::groove:horizontal {
                height: 4px;
                background: #1e2230;
                border-radius: 2px;
            }
            QSlider::sub-page:horizontal {
                background: #38bdf8;
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
        self._sync_live_monitor_button()

        self.anim_timer = QTimer(self)
        self.anim_timer.setInterval(33)
        self.anim_timer.timeout.connect(self._on_timer_tick)
        self.anim_timer.start()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 8, 10, 10)
        main_layout.setSpacing(8)

        # 1. Bannière Artistique Studio + Statut + Contrôles Rapides + Aide (?)
        banner = VocalGlideHeaderBanner(self)
        b_layout = QHBoxLayout(banner)
        b_layout.setContentsMargins(12, 8, 12, 8)
        b_layout.setSpacing(8)

        badge_path = _asset_path("vocal_glide_badge.png")
        if os.path.exists(badge_path):
            lbl_img = QLabel()
            lbl_img.setPixmap(
                QPixmap(badge_path).scaled(42, 42, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
            lbl_img.setStyleSheet("border: 1px solid #2a364c; border-radius: 6px; background: #090b10;")
            b_layout.addWidget(lbl_img)

        title_col = QVBoxLayout()
        title_col.setSpacing(1)
        lbl_title = QLabel("NOVA VOCAL GLIDE")
        lbl_title.setStyleSheet("font-size: 14px; font-weight: bold; color: #f8fafc; letter-spacing: 0.8px;")
        title_col.addWidget(lbl_title)
        lbl_sub = QLabel("Automatisation dynamique de voix & cristallisation en piste Volume")
        lbl_sub.setStyleSheet("font-size: 10px; color: #38bdf8;")
        title_col.addWidget(lbl_sub)
        b_layout.addLayout(title_col)

        b_layout.addStretch()

        self.lbl_status_pill = QLabel("● EN ATTENTE")
        self.lbl_status_pill.setFixedHeight(22)
        self.lbl_status_pill.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.lbl_status_pill.setStyleSheet(
            "background-color: #151b26; color: #94a3b8; border: 1px solid #2b3548; "
            "border-radius: 11px; padding: 2px 10px; font-size: 9px; font-weight: bold;"
        )
        b_layout.addWidget(self.lbl_status_pill)

        self.btn_live_monitor = QPushButton("🎧 Live Micro")
        self.btn_live_monitor.setCheckable(True)
        self.btn_live_monitor.setToolTip(
            "Active le monitoring audio en direct sur la piste hôte pour voir et entendre Nova Vocal Glide pendant que vous chantez"
        )
        self.btn_live_monitor.setStyleSheet("""
            QPushButton {
                background-color: #172030;
                border: 1px solid #2e405b;
                color: #cbd5e1;
                font-weight: bold;
            }
            QPushButton:checked {
                background-color: #065f46;
                border-color: #10b981;
                color: #ecfdf5;
            }
        """)
        self.btn_live_monitor.clicked.connect(self._toggle_live_monitor)
        b_layout.addWidget(self.btn_live_monitor)

        self.preset_container = QWidget()
        p_layout = QHBoxLayout(self.preset_container)
        p_layout.setContentsMargins(0, 0, 0, 0)
        p_layout.setSpacing(4)
        self.combo_presets = QComboBox()
        for name in self.plugin.get_factory_presets().keys():
            self.combo_presets.addItem(name)
        self.combo_presets.currentIndexChanged.connect(self._on_preset_selected)
        p_layout.addWidget(self.combo_presets)
        b_layout.addWidget(self.preset_container)

        self.btn_bypass = QPushButton("Bypass")
        self.btn_bypass.setCheckable(True)
        self.btn_bypass.setChecked(not self.plugin.enabled)
        self.btn_bypass.setText("Bypass Actif" if not self.plugin.enabled else "Bypass")
        self.btn_bypass.setStyleSheet("""
            QPushButton:checked {
                background-color: #7f1d1d;
                border-color: #ef4444;
                color: #ffffff;
            }
        """)
        self.btn_bypass.clicked.connect(self._toggle_bypass)
        b_layout.addWidget(self.btn_bypass)

        # Bouton d'aide (?) en haut à droite
        self.btn_help = QPushButton("?")
        self.btn_help.setObjectName("btn_vocal_glide_help")
        self.btn_help.setFixedSize(26, 26)
        self.btn_help.setCursor(Qt.PointingHandCursor)
        self.btn_help.setToolTip("Afficher le guide d'utilisation de Nova Vocal Glide")
        self.btn_help.setStyleSheet("""
            QPushButton#btn_vocal_glide_help {
                background-color: #182336;
                border: 1px solid #38bdf8;
                border-radius: 13px;
                color: #7dd3fc;
                font-size: 12px;
                font-weight: bold;
                padding: 0px;
            }
            QPushButton#btn_vocal_glide_help:hover {
                background-color: #0284c7;
                color: #ffffff;
            }
        """)
        self.btn_help.clicked.connect(self._show_help_dialog)
        b_layout.addWidget(self.btn_help)

        main_layout.addWidget(banner)

        # 2. Zone Visuelle Centrale : Scope + Fader Motorisé + Triple VU-mètre
        viz_row = QHBoxLayout()
        viz_row.setSpacing(8)

        self.scope = VocalGlideScopeWidget(self.plugin, on_params_dragged=self._on_scope_dragged, parent=self)
        viz_row.addWidget(self.scope, stretch=5)

        self.fader_widget = MotorizedGlideFaderWidget(self.plugin, self)
        viz_row.addWidget(self.fader_widget)

        self.meters = VocalGlideVUMeters(self.plugin, self)
        viz_row.addWidget(self.meters)

        main_layout.addLayout(viz_row, stretch=3)

        # 3. Zone des Paramètres Studio
        controls_frame = QFrame()
        controls_frame.setObjectName("vocal_glide_controls_frame")
        controls_frame.setStyleSheet(
            "QFrame#vocal_glide_controls_frame { background-color: #0d0f15; border: 1px solid #1f2432; border-radius: 7px; }"
        )
        grid = QGridLayout(controls_frame)
        grid.setContentsMargins(12, 10, 12, 10)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(7)

        self.slider_target, self.lbl_target_val = self._create_param_row(
            grid, 0, 0, "Niveau Cible (Target)", -420, -30,
            int(round(self.plugin.target_db * 10)), "dB", display_div=10.0,
            callback=self._on_target_changed,
            tooltip="Niveau moyen cible vers lequel Nova Vocal Glide ramène la voix."
        )
        self.slider_thresh, self.lbl_thresh_val = self._create_param_row(
            grid, 0, 2, "Seuil d'activité (Noise)", -720, -120,
            int(round(self.plugin.activity_threshold_db * 10)), "dB", display_div=10.0,
            callback=self._on_thresh_changed,
            tooltip="En dessous de ce seuil (pauses, respirations), le fader cesse de remonter le volume."
        )

        self.slider_aggr, self.lbl_aggr_val = self._create_param_row(
            grid, 1, 0, "Agressivité / Sensibilité", 0, 100,
            int(round(self.plugin.aggressiveness)), "%",
            callback=self._on_aggr_changed,
            tooltip="Contrôle la réactivité et la force de correction (0% neutre, 100% correction maximale)."
        )
        self.slider_speed, self.lbl_speed_val = self._create_param_row(
            grid, 1, 2, "Vitesse (Slow ↔ Fast)", 0, 100,
            int(round(self.plugin.speed)), "%",
            callback=self._on_speed_changed,
            tooltip="Vitesse de glissement du fader (Lent pour du naturel, Rapide pour mots isolés)."
        )

        self.slider_boost, self.lbl_boost_val = self._create_param_row(
            grid, 2, 0, "Boost Max (Remontée)", 0, 150,
            int(round(self.plugin.max_boost_db * 10)), "dB", display_div=10.0, prefix="+",
            callback=self._on_boost_changed,
            tooltip="Gain maximal ajouté lorsque le volume de la voix descend."
        )
        self.slider_cut, self.lbl_cut_val = self._create_param_row(
            grid, 2, 2, "Cut Max (Atténuation)", 0, 150,
            int(round(self.plugin.max_cut_db * 10)), "dB", display_div=10.0, prefix="-",
            callback=self._on_cut_changed,
            tooltip="Atténuation maximale appliquée lorsque la voix dépasse le niveau cible."
        )

        self.slider_trim, self.lbl_trim_val = self._create_param_row(
            grid, 3, 0, "Output Trim", -120, 120,
            int(round(self.plugin.output_trim_db * 10)), "dB", display_div=10.0,
            callback=self._on_trim_changed,
            tooltip="Ajustement manuel du gain final après Nova Vocal Glide."
        )

        opts_box = QHBoxLayout()
        opts_box.setSpacing(6)

        self.btn_sc_vocal = QPushButton("SC Vocal")
        self.btn_sc_vocal.setCheckable(True)
        self.btn_sc_vocal.setChecked(self.plugin.sidechain_vocal_filter)
        self.btn_sc_vocal.setToolTip("Filtre passe-bande 100 Hz - 6.5 kHz sur le détecteur pour ignorer les graves/sibilances")
        self.btn_sc_vocal.clicked.connect(self._toggle_sc_vocal)
        opts_box.addWidget(self.btn_sc_vocal)

        self.btn_idle = QPushButton(
            "Pause: Retour 0 dB" if self.plugin.idle_behavior == "return_zero" else "Pause: Maintenir"
        )
        self.btn_idle.setToolTip("Comportement du fader quand la voix descend sous le seuil d'activité")
        self.btn_idle.clicked.connect(self._toggle_idle_behavior)
        opts_box.addWidget(self.btn_idle)

        self.btn_write_live = QPushButton("Write Live")
        self.btn_write_live.setCheckable(True)
        self.btn_write_live.setChecked(self.plugin.write_automation_live)
        self.btn_write_live.setToolTip(
            "Écrit les mouvements de Nova Vocal Glide en direct dans la piste Volume pendant la lecture (mode W)"
        )
        self.btn_write_live.clicked.connect(self._toggle_write_live)
        opts_box.addWidget(self.btn_write_live)

        grid.addLayout(opts_box, 3, 2, 1, 2)
        main_layout.addWidget(controls_frame)

        # 4. Dock de Cristallisation en Piste d'Automatisation Volume
        cryst_frame = QFrame()
        cryst_frame.setObjectName("vocal_glide_cryst_frame")
        cryst_frame.setStyleSheet(
            "QFrame#vocal_glide_cryst_frame { "
            "background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0f1726, stop:1 #111b29); "
            "border: 1px solid #1e3a5f; border-radius: 7px; }"
        )
        cryst_layout = QVBoxLayout(cryst_frame)
        cryst_layout.setContentsMargins(12, 8, 12, 8)
        cryst_layout.setSpacing(6)

        c_top = QHBoxLayout()
        c_top.setSpacing(8)

        lbl_det = QLabel("Précision :")
        lbl_det.setStyleSheet("font-size: 10px; color: #94a3b8; font-weight: bold;")
        c_top.addWidget(lbl_det)

        self.combo_detail = QComboBox()
        self.combo_detail.addItem("Précis (détaillé)", "precise")
        self.combo_detail.addItem("Musical (équilibré)", "musical")
        self.combo_detail.addItem("Lissé (peu de points)", "smooth")
        idx_d = max(0, self.combo_detail.findData(self.plugin.crystallize_detail))
        self.combo_detail.setCurrentIndex(idx_d)
        self.combo_detail.currentIndexChanged.connect(self._on_detail_changed)
        c_top.addWidget(self.combo_detail)

        self.combo_scope = QComboBox()
        self.combo_scope.addItem("Toute la piste", "track")
        self.combo_scope.addItem("Zone de boucle", "loop")
        self.combo_scope.setToolTip("Portée temporelle analysée pour créer les points d'automatisation")
        c_top.addWidget(self.combo_scope)

        self.chk_auto_bypass = QCheckBox("Auto-Bypass après cristallisation")
        self.chk_auto_bypass.setChecked(self.plugin.crystallize_auto_bypass)
        self.chk_auto_bypass.setToolTip(
            "Désactive automatiquement le plugin après création de la piste Volume pour éviter le double traitement"
        )
        self.chk_auto_bypass.toggled.connect(self._on_auto_bypass_toggled)
        c_top.addWidget(self.chk_auto_bypass)

        c_top.addStretch()

        self.btn_crystallize = QPushButton("✨ Cristalliser en Automatisation Volume")
        self.btn_crystallize.setCursor(Qt.PointingHandCursor)
        self.btn_crystallize.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0284c7, stop:1 #059669);
                border: 1px solid #38bdf8;
                border-radius: 5px;
                color: #ffffff;
                font-size: 11px;
                font-weight: bold;
                padding: 6px 14px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0ea5e9, stop:1 #10b981);
                border-color: #7dd3fc;
            }
        """)
        self.btn_crystallize.clicked.connect(self._on_crystallize_clicked)
        c_top.addWidget(self.btn_crystallize)

        cryst_layout.addLayout(c_top)

        self.lbl_cryst_feedback = QLabel(
            "Ajustez les paramètres puis cliquez sur « Cristalliser » pour générer une courbe éditable sur la piste Volume."
        )
        self.lbl_cryst_feedback.setStyleSheet("font-size: 10px; color: #7dd3fc;")
        cryst_layout.addWidget(self.lbl_cryst_feedback)

        main_layout.addWidget(cryst_frame)

    def _show_help_dialog(self):
        dlg = VocalGlideHelpDialog(self)
        dlg.exec()

    def _create_param_row(
        self,
        grid: QGridLayout,
        row: int,
        col: int,
        name: str,
        min_val: int,
        max_val: int,
        curr_val: int,
        unit: str,
        display_div: float = 1.0,
        prefix: str = "",
        callback=None,
        tooltip: str = "",
    ):
        lbl_name = QLabel(f"{name} :")
        lbl_name.setStyleSheet("font-size: 10px; font-weight: bold; color: #cbd5e1;")
        if tooltip:
            lbl_name.setToolTip(tooltip)
        grid.addWidget(lbl_name, row, col)

        slider = QSlider(Qt.Horizontal)
        slider.setRange(min_val, max_val)
        slider.setValue(curr_val)
        slider.setFixedHeight(16)
        if tooltip:
            slider.setToolTip(tooltip)
        if callback:
            slider.valueChanged.connect(callback)
        grid.addWidget(slider, row, col + 1)

        val = curr_val / display_div if display_div != 1.0 else curr_val
        val_txt = f"{prefix}{val:.1f} {unit}" if display_div != 1.0 else f"{prefix}{val} {unit}"
        lbl_val = QLabel(val_txt)
        lbl_val.setFixedWidth(58)
        lbl_val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lbl_val.setStyleSheet("font-size: 10px; color: #38bdf8; font-weight: bold;")
        grid.addWidget(lbl_val, row, col + 1, Qt.AlignRight)

        return slider, lbl_val

    def _resolve_context(self):
        from ui.plugin_dialogs import _find_main_window, find_track_for_plugin
        main_win = _find_main_window(self.parent())
        track = None
        dlg = self.parent()
        if dlg is not None and getattr(dlg, "track", None) is not None:
            track = dlg.track
        if track is None:
            track = find_track_for_plugin(self.plugin, main_win or self.parent())
        return main_win, track

    def _notify_project_modified(self):
        main_win, _ = self._resolve_context()
        if main_win and hasattr(main_win, "set_dirty"):
            main_win.set_dirty(True)

    def _sync_live_monitor_button(self):
        _, track = self._resolve_context()
        if track is None or getattr(track, "track_type", "") != "audio":
            self.btn_live_monitor.setEnabled(track is not None)
            self.btn_live_monitor.setChecked(bool(getattr(track, "monitoring", False)) if track else False)
            return
        self.btn_live_monitor.setEnabled(True)
        self.btn_live_monitor.blockSignals(True)
        self.btn_live_monitor.setChecked(bool(getattr(track, "monitoring", False)))
        self.btn_live_monitor.blockSignals(False)

    def _toggle_live_monitor(self, checked: bool):
        main_win, track = self._resolve_context()
        if track is None:
            self.btn_live_monitor.setChecked(False)
            self.lbl_cryst_feedback.setText("Aucune piste audio associée détectée pour le monitoring.")
            return
        if getattr(track, "track_type", "") != "audio":
            self.btn_live_monitor.setChecked(False)
            self.lbl_cryst_feedback.setText("Le monitoring micro live est disponible sur les pistes Audio.")
            return
        track.monitoring = bool(checked)
        if main_win:
            if hasattr(main_win, "_on_track_mixer_changed"):
                main_win._on_track_mixer_changed()
            elif hasattr(main_win, "audio_engine") and main_win.audio_engine:
                main_win.audio_engine.sync_project_monitoring()

    def _on_scope_dragged(self, which: str):
        if which == "target":
            self.slider_target.blockSignals(True)
            self.slider_target.setValue(int(round(self.plugin.target_db * 10)))
            self.lbl_target_val.setText(f"{self.plugin.target_db:.1f} dB")
            self.slider_target.blockSignals(False)
        elif which == "threshold":
            self.slider_thresh.blockSignals(True)
            self.slider_thresh.setValue(int(round(self.plugin.activity_threshold_db * 10)))
            self.lbl_thresh_val.setText(f"{self.plugin.activity_threshold_db:.1f} dB")
            self.slider_thresh.blockSignals(False)
        self._notify_project_modified()

    def _on_target_changed(self, val: int):
        self.plugin.target_db = val / 10.0
        self.lbl_target_val.setText(f"{self.plugin.target_db:.1f} dB")
        self.scope.update()
        self._notify_project_modified()

    def _on_thresh_changed(self, val: int):
        self.plugin.activity_threshold_db = val / 10.0
        self.lbl_thresh_val.setText(f"{self.plugin.activity_threshold_db:.1f} dB")
        self.scope.update()
        self._notify_project_modified()

    def _on_aggr_changed(self, val: int):
        self.plugin.aggressiveness = float(val)
        self.lbl_aggr_val.setText(f"{val} %")
        self._notify_project_modified()

    def _on_speed_changed(self, val: int):
        self.plugin.speed = float(val)
        self.lbl_speed_val.setText(f"{val} %")
        self._notify_project_modified()

    def _on_boost_changed(self, val: int):
        self.plugin.max_boost_db = val / 10.0
        self.lbl_boost_val.setText(f"+{self.plugin.max_boost_db:.1f} dB")
        self.fader_widget.update()
        self._notify_project_modified()

    def _on_cut_changed(self, val: int):
        self.plugin.max_cut_db = val / 10.0
        self.lbl_cut_val.setText(f"-{self.plugin.max_cut_db:.1f} dB")
        self.fader_widget.update()
        self._notify_project_modified()

    def _on_trim_changed(self, val: int):
        self.plugin.output_trim_db = val / 10.0
        sign = "+" if self.plugin.output_trim_db > 0 else ""
        self.lbl_trim_val.setText(f"{sign}{self.plugin.output_trim_db:.1f} dB")
        self._notify_project_modified()

    def _toggle_sc_vocal(self, checked: bool):
        self.plugin.set_state({"sidechain_vocal_filter": checked})
        self._notify_project_modified()

    def _toggle_idle_behavior(self):
        new_mode = "hold" if self.plugin.idle_behavior == "return_zero" else "return_zero"
        self.plugin.idle_behavior = new_mode
        self.btn_idle.setText("Pause: Retour 0 dB" if new_mode == "return_zero" else "Pause: Maintenir")
        self._notify_project_modified()

    def _toggle_write_live(self, checked: bool):
        self.plugin.write_automation_live = bool(checked)
        if checked:
            main_win, track = self._resolve_context()
            if track is not None:
                lane = track.get_automation_lane("volume") or track.add_automation_lane("volume")
                lane.mode = "write"
                lane.enabled = True
                lane.visible = True
                track.automation_expanded = True
                if main_win and hasattr(main_win, "refresh_project_ui"):
                    main_win.refresh_project_ui()
                self.lbl_cryst_feedback.setText("Mode Write Live actif : lancez la lecture pour enregistrer les mouvements dans Volume.")
        self._notify_project_modified()

    def _toggle_bypass(self, checked: bool):
        self.plugin.enabled = not checked
        self.btn_bypass.setText("Bypass Actif" if checked else "Bypass")
        self._notify_project_modified()

    def _on_detail_changed(self, _idx: int):
        data = self.combo_detail.currentData()
        if data in ("precise", "musical", "smooth"):
            self.plugin.crystallize_detail = data
            self._notify_project_modified()

    def _on_auto_bypass_toggled(self, checked: bool):
        self.plugin.crystallize_auto_bypass = bool(checked)
        self._notify_project_modified()

    def _on_preset_selected(self, _idx: int):
        name = self.combo_presets.currentText()
        p = self.plugin.get_factory_presets().get(name)
        if p:
            self.plugin.set_state(p)
            self._sync_all_controls()

    def _sync_all_controls(self):
        sliders = [
            self.slider_target, self.slider_thresh, self.slider_aggr,
            self.slider_speed, self.slider_boost, self.slider_cut, self.slider_trim,
        ]
        for s in sliders:
            s.blockSignals(True)

        self.slider_target.setValue(int(round(self.plugin.target_db * 10)))
        self.slider_thresh.setValue(int(round(self.plugin.activity_threshold_db * 10)))
        self.slider_aggr.setValue(int(round(self.plugin.aggressiveness)))
        self.slider_speed.setValue(int(round(self.plugin.speed)))
        self.slider_boost.setValue(int(round(self.plugin.max_boost_db * 10)))
        self.slider_cut.setValue(int(round(self.plugin.max_cut_db * 10)))
        self.slider_trim.setValue(int(round(self.plugin.output_trim_db * 10)))

        self.lbl_target_val.setText(f"{self.plugin.target_db:.1f} dB")
        self.lbl_thresh_val.setText(f"{self.plugin.activity_threshold_db:.1f} dB")
        self.lbl_aggr_val.setText(f"{int(round(self.plugin.aggressiveness))} %")
        self.lbl_speed_val.setText(f"{int(round(self.plugin.speed))} %")
        self.lbl_boost_val.setText(f"+{self.plugin.max_boost_db:.1f} dB")
        self.lbl_cut_val.setText(f"-{self.plugin.max_cut_db:.1f} dB")
        sign = "+" if self.plugin.output_trim_db > 0 else ""
        self.lbl_trim_val.setText(f"{sign}{self.plugin.output_trim_db:.1f} dB")

        for w in (self.btn_sc_vocal, self.btn_write_live, self.btn_bypass, self.chk_auto_bypass):
            w.blockSignals(True)
        self.btn_sc_vocal.setChecked(self.plugin.sidechain_vocal_filter)
        self.btn_idle.setText(
            "Pause: Retour 0 dB" if self.plugin.idle_behavior == "return_zero" else "Pause: Maintenir"
        )
        self.btn_write_live.setChecked(self.plugin.write_automation_live)
        self.btn_bypass.setChecked(not self.plugin.enabled)
        self.btn_bypass.setText("Bypass Actif" if not self.plugin.enabled else "Bypass")
        self.chk_auto_bypass.setChecked(self.plugin.crystallize_auto_bypass)
        for w in (self.btn_sc_vocal, self.btn_write_live, self.btn_bypass, self.chk_auto_bypass):
            w.blockSignals(False)

        if hasattr(self, "combo_detail"):
            self.combo_detail.blockSignals(True)
            idx_d = self.combo_detail.findData(self.plugin.crystallize_detail)
            if idx_d >= 0:
                self.combo_detail.setCurrentIndex(idx_d)
            self.combo_detail.blockSignals(False)

        for s in sliders:
            s.blockSignals(False)

        self.scope.update()
        self.fader_widget.update()

    def _on_crystallize_clicked(self):
        """Analyse les clips audio de la piste et génère une piste d'automatisation Volume éditable."""
        main_win, track = self._resolve_context()
        if track is None:
            self.lbl_cryst_feedback.setText(
                "❌ Piste hôte introuvable : placez Nova Vocal Glide sur une piste audio pour cristalliser."
            )
            return

        project = getattr(main_win, "project", None) if main_win else None
        bpm = float(getattr(project, "bpm", 120.0)) if project else 120.0
        sr = int(getattr(getattr(main_win, "audio_engine", None), "sample_rate", 44100)) if main_win else 44100

        start_b, end_b = None, None
        if self.combo_scope.currentData() == "loop" and project and getattr(project, "loop_enabled", False):
            if project.loop_end_beat > project.loop_start_beat:
                start_b = float(project.loop_start_beat)
                end_b = float(project.loop_end_beat)

        pts, stats = self.plugin.analyze_track_to_automation_points(
            track=track,
            bpm=bpm,
            sample_rate=sr,
            start_beat=start_b,
            end_beat=end_b,
            detail_mode=self.combo_detail.currentData(),
        )

        if not pts:
            self.lbl_cryst_feedback.setText(
                "⚠️ Aucun clip audio détecté sur cette piste dans la plage sélectionnée."
            )
            return

        lane = track.add_automation_lane("volume", seed_initial_point=False)
        lane.enabled = True
        lane.visible = True
        lane.mode = "read"
        track.automation_expanded = True

        if start_b is not None and end_b is not None:
            lane.points = [p for p in lane.points if p.beat < start_b or p.beat > end_b]
        else:
            lane.points.clear()

        for beat, val in pts:
            lane.add_point(beat, val, curve_type="linear", merge_tol=1e-3)

        bypass_msg = ""
        if self.plugin.crystallize_auto_bypass:
            self.plugin.enabled = False
            self.btn_bypass.setChecked(True)
            self.btn_bypass.setText("Bypass Actif")
            bypass_msg = " — Plugin bypassé (anti-double correction)"

        if main_win:
            if hasattr(main_win, "audio_engine") and main_win.audio_engine:
                main_win.audio_engine.invalidate_track_cache(track.id)
            if hasattr(main_win, "refresh_project_ui"):
                main_win.refresh_project_ui()
            if hasattr(main_win, "set_dirty"):
                main_win.set_dirty(True)

        clamp_note = " (plafonné à 200%)" if stats.get("was_clamped") else ""
        self.lbl_cryst_feedback.setText(
            f"✅ {stats['point_count']} points créés sur « Volume » "
            f"({stats['min_gain_db']:+.1f} à {stats['max_gain_db']:+.1f} dB){clamp_note}{bypass_msg}."
        )

    def _on_timer_tick(self):
        self.plugin.decay_meters()
        self._sync_live_monitor_button()

        if not self.plugin.enabled:
            self.lbl_status_pill.setText("● BYPASS")
            self.lbl_status_pill.setStyleSheet(
                "background-color: #2a1418; color: #f87171; border: 1px solid #ef4444; "
                "border-radius: 11px; padding: 2px 10px; font-size: 9px; font-weight: bold;"
            )
        elif self.plugin.is_vocal_active:
            self.lbl_status_pill.setText("● VOIX ACTIVE")
            self.lbl_status_pill.setStyleSheet(
                "background-color: #064e3b; color: #34d399; border: 1px solid #10b981; "
                "border-radius: 11px; padding: 2px 10px; font-size: 9px; font-weight: bold;"
            )
        else:
            self.lbl_status_pill.setText("● SOUS SEUIL")
            self.lbl_status_pill.setStyleSheet(
                "background-color: #1e1b12; color: #fbbf24; border: 1px solid #d97706; "
                "border-radius: 11px; padding: 2px 10px; font-size: 9px; font-weight: bold;"
            )

        self.scope.update()
        self.fader_widget.update()
        self.meters.update()
