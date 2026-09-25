"""
plugins/synth/synth_gui.py - Interface graphique moderne, séduisante et cyberpunk pour NovaSynth.

Caractéristiques visuelles :
- Motifs de background futuristes haute-technologie (grille hexagonale isométrique, circuits néon et nodes luminescents).
- Oscilloscope temps réel avec analyseur spectral réactif.
- Visualisateur interactif de courbe d'enveloppe ADSR (Attack, Decay, Sustain, Release).
- Visualisateur de courbe de réponse en fréquence de filtre (Bode plot).
- Gestionnaire d'empilement de couches sonores (Sound Stacking) avec Mute/Solo et couleur personnalisée.
- Sélecteur de matrice de routage pour les 10 sorties stéréo indépendantes.
- Clavier d'écoute temps réel ultra-faible latence (<8ms).
"""
import math
import threading
from typing import Optional, List, Dict, Any
import numpy as np

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QPushButton, QSlider, QComboBox, QFrame, QSizePolicy,
    QGroupBox, QCheckBox, QScrollArea, QStackedLayout, QLineEdit,
    QInputDialog, QMessageBox, QMenu
)
from PySide6.QtCore import Qt, QTimer, QRectF, QPointF, Signal
from PySide6.QtGui import (
    QPainter, QColor, QPen, QBrush, QFont, QLinearGradient,
    QRadialGradient, QPainterPath, QPolygonF
)

from plugins.synth.synth_plugin import NovaSynthPlugin, SynthLayer
from plugins.synth.synth_dsp import pitch_to_freq, generate_oscillator


# Définition universelle des formes d'onde supportées par NovaSynth
WAVEFORM_ITEMS_SHORT = [
    ("saw", "Saw", "⊿"),
    ("square", "Square", "⎍"),
    ("sine", "Sine", "∿"),
    ("triangle", "Tri", "⋀"),
    ("supersaw", "Super", "⚡"),
    ("fm", "FM", "📻"),
    ("noise", "Noise", "💨"),
]

WAVEFORM_ITEMS_FULL = [
    ("saw", "Saw (Dents de scie)", "⊿"),
    ("square", "Square (Carrée / Pulse)", "⎍"),
    ("sine", "Sine (Sinusoïde pure)", "∿"),
    ("triangle", "Triangle", "⋀"),
    ("supersaw", "SuperSaw (7 Voix JP-8000)", "⚡"),
    ("fm", "FM (Modulation Fréquence)", "📻"),
    ("noise", "Noise (Bruit Blanc Analogique)", "💨"),
]


class LowLatencySynthAudition:
    """Lecteur audio temps réel persistant ultra-faible latence pour l'écoute interactive dans l'interface"""
    _instance: Optional['LowLatencySynthAudition'] = None

    @classmethod
    def get_instance(cls) -> 'LowLatencySynthAudition':
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self, sample_rate: int = 44100, block_size: int = 256):
        self.sample_rate = sample_rate
        self.block_size = block_size
        self._lock = threading.Lock()
        self._voices: List[Dict[str, Any]] = []
        self._stream: Optional[Any] = None
        self._stream_failed = False

    def _ensure_stream(self) -> bool:
        if self._stream is not None and getattr(self._stream, "active", False):
            return True
        if self._stream_failed:
            return False
        try:
            import sounddevice as sd
            self._stream = sd.OutputStream(
                samplerate=self.sample_rate,
                blocksize=self.block_size,
                channels=2,
                dtype="float32",
                latency="low",
                callback=self._audio_callback
            )
            self._stream.start()
            return True
        except Exception:
            self._stream = None
            self._stream_failed = True
            return False

    def play(self, buffer: np.ndarray):
        if buffer is None or len(buffer) == 0:
            return
        if self._ensure_stream():
            with self._lock:
                self._voices.append({
                    "buffer": np.ascontiguousarray(buffer, dtype=np.float32),
                    "cursor": 0
                })
            return

        if not self._stream_failed:
            try:
                import sounddevice as sd
                sd.play(buffer, self.sample_rate)
            except Exception:
                self._stream_failed = True

    def _audio_callback(self, outdata, frames, time_info, status):
        outdata.fill(0.0)
        with self._lock:
            if not self._voices:
                return
            surviving = []
            for v in self._voices:
                buf = v["buffer"]
                cur = v["cursor"]
                rem = len(buf) - cur
                if rem <= 0:
                    continue
                chunk_len = min(frames, rem)
                outdata[:chunk_len] += buf[cur:cur + chunk_len]
                v["cursor"] += chunk_len
                if v["cursor"] < len(buf):
                    surviving.append(v)
            self._voices = surviving
            # Soft limiting
            np.clip(outdata, -1.0, 1.0, out=outdata)


class CyberBackgroundWidget(QWidget):
    """
    Fond visuel haut de gamme orné d'une grille hexagonale vectorielle,
    de pistes de circuit imprimé et d'éclats néon cybernétiques.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, False)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        h = self.height()

        # 1. Dégradé sombre d'ambiance
        grad = QLinearGradient(0, 0, w, h)
        grad.setColorAt(0.0, QColor("#080a12"))
        grad.setColorAt(0.5, QColor("#0d111e"))
        grad.setColorAt(1.0, QColor("#12172a"))
        painter.fillRect(0, 0, w, h, grad)

        # 2. Motif géométrique hexagonal cybernétique
        hex_size = 32.0
        h_spacing = hex_size * 1.732
        v_spacing = hex_size * 1.5

        pen_grid = QPen(QColor(0, 240, 255, 14))
        pen_grid.setWidthF(0.8)
        painter.setPen(pen_grid)
        painter.setBrush(Qt.NoBrush)

        cols = int(w / h_spacing) + 2
        rows = int(h / v_spacing) + 2

        for r in range(rows):
            y_c = r * v_spacing
            x_offset = (h_spacing * 0.5) if (r % 2 == 1) else 0.0
            for c in range(cols):
                x_c = c * h_spacing + x_offset
                path = QPainterPath()
                for i in range(6):
                    angle = math.radians(60 * i + 30)
                    px = x_c + hex_size * 0.5 * math.cos(angle)
                    py = y_c + hex_size * 0.5 * math.sin(angle)
                    if i == 0:
                        path.moveTo(px, py)
                    else:
                        path.lineTo(px, py)
                path.closeSubpath()
                painter.drawPath(path)

        # 3. Tracés de circuits néon stylisés
        pen_circuit = QPen(QColor(255, 0, 127, 22))
        pen_circuit.setWidthF(1.2)
        painter.setPen(pen_circuit)

        # Circuit 1
        c_path1 = QPainterPath()
        c_path1.moveTo(0, 60)
        c_path1.lineTo(120, 60)
        c_path1.lineTo(180, 120)
        c_path1.lineTo(350, 120)
        painter.drawPath(c_path1)
        painter.setBrush(QBrush(QColor(0, 240, 255, 60)))
        painter.drawEllipse(QPointF(350, 120), 3.0, 3.0)

        # Circuit 2
        c_path2 = QPainterPath()
        c_path2.moveTo(w, 80)
        c_path2.lineTo(w - 140, 80)
        c_path2.lineTo(w - 200, 140)
        c_path2.lineTo(w - 400, 140)
        painter.drawPath(c_path2)
        painter.drawEllipse(QPointF(w - 400, 140), 3.0, 3.0)

        # 4. Vignettage sombre sur les bords pour accentuer la profondeur
        vignette = QRadialGradient(w * 0.5, h * 0.5, max(w, h) * 0.7)
        vignette.setColorAt(0.0, QColor(0, 0, 0, 0))
        vignette.setColorAt(0.7, QColor(0, 0, 0, 40))
        vignette.setColorAt(1.0, QColor(0, 0, 0, 160))
        painter.fillRect(0, 0, w, h, vignette)


class CyberOscilloscope(QWidget):
    """Oscilloscope temps réel et visualisateur d'ondes avec effet de néon réactif"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(80)
        self.setMinimumWidth(260)
        self._samples: np.ndarray = np.zeros(256, dtype=np.float32)
        self._pending_audio: Optional[np.ndarray] = None
        self._pending_lock = threading.Lock()
        self._phase = 0.0
        self._glow_intensity = 0.5

        # Animation à 30 FPS pour un mouvement continu fluide
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_timer)
        self._timer.start(33)

    def set_audio_data(self, audio: np.ndarray):
        """Reçoit les blocs audio du moteur. Non-bloquant pour le thread audio temps réel (aucun appel Qt update direct)."""
        if audio is None or len(audio) == 0:
            return
        if audio.ndim == 2:
            mono = (audio[:, 0] + audio[:, 1]) * 0.5
        else:
            mono = audio

        # Extraction et synchronisation rapide
        sliced = mono
        limit = min(120, len(mono) - 1)
        for z_i in range(limit):
            if mono[z_i] <= 0.0 and mono[z_i + 1] > 0.0:
                sliced = mono[z_i:]
                break

        step = max(1, len(sliced) // 256)
        sub = sliced[::step][:256]
        padded = np.zeros(256, dtype=np.float32)
        take = min(256, len(sub))
        padded[:take] = sub[:take]
        self._samples = padded
        self._glow_intensity = 1.0

    def _on_timer(self):
        self._phase = (self._phase + 0.08) % (2.0 * math.pi)
        self._glow_intensity = max(0.3, self._glow_intensity * 0.94)
        # Décroissance douce des échantillons réels pour éviter tout gel statique
        if np.max(np.abs(self._samples)) > 0.005:
            self._samples *= 0.90
            if np.max(np.abs(self._samples)) <= 0.005:
                self._samples.fill(0.0)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        h = self.height()

        # Fond d'écran de l'oscilloscope
        bg_grad = QLinearGradient(0, 0, 0, h)
        bg_grad.setColorAt(0.0, QColor("#080d1a"))
        bg_grad.setColorAt(1.0, QColor("#03060c"))
        painter.fillRect(0, 0, w, h, bg_grad)

        # Lignes de grille
        pen_grid = QPen(QColor(0, 240, 255, 25))
        pen_grid.setWidthF(0.8)
        painter.setPen(pen_grid)
        mid_y = h * 0.5
        painter.drawLine(0, int(mid_y), w, int(mid_y))
        for x_g in range(0, w, 40):
            painter.drawLine(x_g, 0, x_g, h)

        # Tracé de la forme d'onde
        has_real_audio = np.max(np.abs(self._samples)) > 0.01

        path = QPainterPath()
        points = 128
        dx = w / (points - 1)

        for i in range(points):
            x = i * dx
            if has_real_audio:
                s_idx = int((i / points) * len(self._samples))
                val = float(self._samples[s_idx])
            else:
                # Onde d'ambiance cosmique animée en veille
                val = 0.15 * math.sin(i * 0.15 + self._phase) + 0.08 * math.cos(i * 0.35 - self._phase * 1.5)

            y = mid_y - (val * (h * 0.42))
            y = max(4.0, min(h - 4.0, y))
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)

        # Halo néon
        pen_glow = QPen(QColor(0, 240, 255, int(70 * self._glow_intensity)))
        pen_glow.setWidthF(5.0)
        painter.setPen(pen_glow)
        painter.drawPath(path)

        # Tracé principal brillant (cyan vers magenta)
        grad_line = QLinearGradient(0, 0, w, 0)
        grad_line.setColorAt(0.0, QColor("#00f0ff"))
        grad_line.setColorAt(0.5, QColor("#7000ff"))
        grad_line.setColorAt(1.0, QColor("#ff007f"))
        pen_line = QPen(QBrush(grad_line), 2.0)
        painter.setPen(pen_line)
        painter.drawPath(path)

        # Bordure de l'écran avec reflets
        painter.setPen(QPen(QColor(0, 240, 255, 60), 1.0))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(0.5, 0.5, w - 1, h - 1, 4, 4)

        # Badge d'état
        painter.setFont(QFont("Segoe UI", 8, QFont.Bold))
        painter.setPen(QColor(0, 240, 255, 180))
        painter.drawText(8, 14, "OSCILLOSCOPE FP32 // DSP LIVE")


class InteractiveADSRWidget(QWidget):
    """
    Visualisateur et éditeur 2D interactif de la courbe d'enveloppe ADSR.
    Permet de saisir directement à la souris et déplacer les points de contrôle dans l'espace 2D.
    """
    adsr_changed = Signal(float, float, float, float)  # attack (s), decay (s), sustain (0-1), release (s)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(105)
        self.setMinimumWidth(220)
        self.attack = 0.010
        self.decay = 0.250
        self.sustain = 0.70
        self.release = 0.350

        self.setMouseTracking(True)
        self.active_node: Optional[int] = None
        self.hover_node: Optional[int] = None

    def set_adsr(self, a: float, d: float, s: float, r: float):
        self.attack = max(0.001, min(2.0, float(a)))
        self.decay = max(0.005, min(3.0, float(d)))
        self.sustain = max(0.0, min(1.0, float(s)))
        self.release = max(0.005, min(4.0, float(r)))
        self.update()

    def _get_geometry(self):
        w = self.width()
        h = self.height()
        pad_x = 16
        pad_y = 16
        draw_w = w - 2 * pad_x
        draw_h = h - 2 * pad_y
        baseline_y = h - pad_y

        total_time = self.attack + self.decay + 0.35 + self.release
        x_att = pad_x + draw_w * (self.attack / total_time)
        x_dec = x_att + draw_w * (self.decay / total_time)
        x_sus = x_dec + draw_w * (0.35 / total_time)
        x_rel = pad_x + draw_w

        y_top = pad_y
        y_sus = baseline_y - draw_h * self.sustain

        p0 = QPointF(x_att, y_top)       # Sommet Attack
        p1 = QPointF(x_dec, y_sus)       # Point Decay / début Sustain
        p2 = QPointF(x_sus, y_sus)       # Fin Sustain / début Release
        p3 = QPointF(x_rel, baseline_y)  # Fin Release

        return (pad_x, pad_y, draw_w, draw_h, baseline_y, [p0, p1, p2, p3])

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            pos = event.position()
            _, _, _, _, _, nodes = self._get_geometry()
            closest = None
            min_dist = 16.0
            for i, p in enumerate(nodes):
                dist = math.hypot(pos.x() - p.x(), pos.y() - p.y())
                if dist < min_dist:
                    min_dist = dist
                    closest = i

            if closest is not None:
                self.active_node = closest
                self.setCursor(Qt.ClosedHandCursor)
                self.update()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        pos = event.position()
        pad_x, pad_y, draw_w, draw_h, baseline_y, nodes = self._get_geometry()

        if self.active_node is not None:
            x = pos.x()
            y = pos.y()

            if self.active_node == 0:
                rel_x = max(0.005, min(0.65, (x - pad_x) / draw_w))
                self.attack = max(0.001, min(2.0, rel_x * 2.5))
            elif self.active_node == 1:
                x_att = nodes[0].x()
                rel_x = max(0.005, min(0.70, (x - x_att) / draw_w))
                self.decay = max(0.005, min(3.0, rel_x * 3.5))
                self.sustain = max(0.0, min(1.0, (baseline_y - y) / draw_h))
            elif self.active_node == 2:
                self.sustain = max(0.0, min(1.0, (baseline_y - y) / draw_h))
            elif self.active_node == 3:
                x_sus = nodes[2].x()
                rel_x = max(0.005, min(0.85, (x - x_sus) / draw_w))
                self.release = max(0.005, min(4.0, rel_x * 4.5))

            self.adsr_changed.emit(self.attack, self.decay, self.sustain, self.release)
            self.update()
        else:
            closest = None
            min_dist = 16.0
            for i, p in enumerate(nodes):
                dist = math.hypot(pos.x() - p.x(), pos.y() - p.y())
                if dist < min_dist:
                    min_dist = dist
                    closest = i

            if closest != self.hover_node:
                self.hover_node = closest
                self.setCursor(Qt.PointingHandCursor if closest is not None else Qt.ArrowCursor)
                self.update()

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.active_node is not None:
            self.active_node = None
            self.setCursor(Qt.PointingHandCursor if self.hover_node is not None else Qt.ArrowCursor)
            self.update()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        h = self.height()
        pad_x, pad_y, draw_w, draw_h, baseline_y, nodes = self._get_geometry()
        p0, p1, p2, p3 = nodes

        painter.fillRect(0, 0, w, h, QColor("#0a0f1d"))
        painter.setPen(QPen(QColor("#1e293b"), 1.0))
        painter.drawRoundedRect(0.5, 0.5, w - 1, h - 1, 4, 4)

        pen_grid = QPen(QColor(0, 240, 255, 15), 0.8, Qt.DotLine)
        painter.setPen(pen_grid)
        for pct in [0.25, 0.50, 0.75]:
            gy = baseline_y - draw_h * pct
            painter.drawLine(pad_x, int(gy), w - pad_x, int(gy))

        path = QPainterPath()
        path.moveTo(pad_x, baseline_y)
        path.lineTo(p0)
        path.lineTo(p1)
        path.lineTo(p2)
        path.lineTo(p3)

        fill_path = QPainterPath(path)
        fill_path.lineTo(pad_x, baseline_y)
        fill_path.closeSubpath()

        grad_fill = QLinearGradient(0, pad_y, 0, baseline_y)
        grad_fill.setColorAt(0.0, QColor(0, 240, 255, 95))
        grad_fill.setColorAt(1.0, QColor(0, 240, 255, 5))
        painter.fillPath(fill_path, grad_fill)

        pen_curve = QPen(QColor("#00f0ff"), 2.2)
        painter.setPen(pen_curve)
        painter.drawPath(path)

        for i, pt in enumerate(nodes):
            is_active = (i == self.active_node)
            is_hover = (i == self.hover_node)

            if is_active or is_hover:
                painter.setBrush(QBrush(QColor(0, 240, 255, 80)))
                painter.setPen(QPen(QColor("#00f0ff"), 1.5))
                painter.drawEllipse(pt, 8.0, 8.0)

            painter.setBrush(QBrush(QColor("#ffffff") if (is_active or is_hover) else QColor("#f8fafc")))
            painter.setPen(QPen(QColor("#ff007f") if i in (0, 3) else QColor("#0284c7"), 2.0))
            painter.drawEllipse(pt, 4.2 if (is_active or is_hover) else 3.5, 4.2 if (is_active or is_hover) else 3.5)

        painter.setFont(QFont("Segoe UI", 7, QFont.Bold))
        painter.setPen(QColor("#94a3b8"))
        painter.drawText(pad_x, pad_y - 2, "ENVELOPPE ADSR (2D INTERACTIF)")

        painter.setPen(QColor("#38bdf8"))
        vals_txt = f"A:{int(self.attack * 1000)}ms | D:{int(self.decay * 1000)}ms | S:{int(self.sustain * 100)}% | R:{int(self.release * 1000)}ms"
        painter.drawText(w - pad_x - 170, pad_y - 2, vals_txt)


class FilterCurveWidget(QWidget):
    """
    Visualisateur et contrôleur 2D interactif de la réponse fréquentielle du filtre (Bode Plot).
    Permet de déplacer le point de coupure (Cutoff horizontal) et la résonance (Resonance vertical) à la souris.
    """
    filter_changed = Signal(float, float)  # cutoff (Hz), resonance (Q)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(105)
        self.setMinimumWidth(220)
        self.filter_type = "lowpass"
        self.cutoff = 3500.0
        self.resonance = 1.2

        self.setMouseTracking(True)
        self.is_dragging = False
        self.is_hovered = False

    def set_filter(self, ftype: str, cutoff: float, res: float):
        self.filter_type = ftype
        self.cutoff = max(20.0, min(20000.0, float(cutoff)))
        self.resonance = max(0.2, min(10.0, float(res)))
        self.update()

    def _get_node_pos(self):
        w = self.width()
        h = self.height()
        pad_x = 14
        pad_y = 16
        draw_w = w - 2 * pad_x
        draw_h = h - 2 * pad_y
        mid_y = pad_y + draw_h * 0.55

        log_min = math.log10(20.0)
        log_max = math.log10(20000.0)
        c_log = (math.log10(self.cutoff) - log_min) / (log_max - log_min)
        x_node = pad_x + draw_w * max(0.0, min(1.0, c_log))

        res_bump = min(1.8, (self.resonance - 0.5) * 0.35)
        y_node = mid_y - res_bump * (draw_h * 0.4)
        return (pad_x, pad_y, draw_w, draw_h, mid_y, x_node, y_node, log_min, log_max)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            pad_x, pad_y, draw_w, draw_h, mid_y, x_node, y_node, _, _ = self._get_node_pos()
            pos = event.position()
            dist = math.hypot(pos.x() - x_node, pos.y() - y_node)
            if dist <= 24.0 or pad_x <= pos.x() <= pad_x + draw_w:
                self.is_dragging = True
                self.setCursor(Qt.ClosedHandCursor)
                self._update_from_mouse(pos)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        pos = event.position()
        pad_x, pad_y, draw_w, draw_h, mid_y, x_node, y_node, _, _ = self._get_node_pos()

        if self.is_dragging:
            self._update_from_mouse(pos)
        else:
            dist = math.hypot(pos.x() - x_node, pos.y() - y_node)
            hover = (dist <= 16.0)
            if hover != self.is_hovered:
                self.is_hovered = hover
                self.setCursor(Qt.PointingHandCursor if hover else Qt.ArrowCursor)
                self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.is_dragging:
            self.is_dragging = False
            self.setCursor(Qt.PointingHandCursor if self.is_hovered else Qt.ArrowCursor)
            self.update()
        super().mouseReleaseEvent(event)

    def _update_from_mouse(self, pos):
        pad_x, pad_y, draw_w, draw_h, mid_y, _, _, log_min, log_max = self._get_node_pos()

        norm_x = max(0.0, min(1.0, (pos.x() - pad_x) / draw_w))
        self.cutoff = 10.0 ** (log_min + norm_x * (log_max - log_min))
        self.cutoff = max(20.0, min(20000.0, self.cutoff))

        norm_y = (mid_y - pos.y()) / (draw_h * 0.4)
        self.resonance = max(0.2, min(10.0, 0.5 + (norm_y * 1.5)))

        self.filter_changed.emit(self.cutoff, self.resonance)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        h = self.height()
        pad_x, pad_y, draw_w, draw_h, mid_y, x_node, y_node, log_min, log_max = self._get_node_pos()

        painter.fillRect(0, 0, w, h, QColor("#0a0f1d"))
        painter.setPen(QPen(QColor("#1e293b"), 1.0))
        painter.drawRoundedRect(0.5, 0.5, w - 1, h - 1, 4, 4)

        pen_grid = QPen(QColor(255, 0, 127, 20), 0.8, Qt.DotLine)
        painter.setPen(pen_grid)
        for f_grid in [100.0, 1000.0, 10000.0]:
            x_g = pad_x + draw_w * ((math.log10(f_grid) - log_min) / (log_max - log_min))
            painter.drawLine(int(x_g), pad_y, int(x_g), h - pad_y)

        path = QPainterPath()
        points = 80
        ftype = self.filter_type.lower()
        res_bump = min(1.8, (self.resonance - 0.5) * 0.35)
        c_log = (math.log10(self.cutoff) - log_min) / (log_max - log_min)

        for i in range(points):
            f_norm = i / (points - 1)
            x = pad_x + draw_w * f_norm
            dist = f_norm - c_log

            if ftype in ("lowpass", "lp"):
                if dist < 0:
                    resp = math.exp(dist * 2.0) * res_bump
                else:
                    resp = res_bump * math.exp(-dist * 12.0) - (dist * 4.0)
            elif ftype in ("highpass", "hp"):
                if dist > 0:
                    resp = math.exp(-dist * 2.0) * res_bump
                else:
                    resp = res_bump * math.exp(dist * 12.0) + (dist * 4.0)
            elif ftype in ("bandpass", "bp"):
                resp = math.exp(-abs(dist) * 10.0) * (1.0 + res_bump) - 1.0
            else:  # notch
                resp = -math.exp(-abs(dist) * 16.0) * 1.5

            y = mid_y - resp * (draw_h * 0.4)
            y = max(pad_y + 2.0, min(h - pad_y - 2.0, y))
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)

        fill_path = QPainterPath(path)
        fill_path.lineTo(w - pad_x, h - pad_y)
        fill_path.lineTo(pad_x, h - pad_y)
        fill_path.closeSubpath()

        grad = QLinearGradient(0, pad_y, 0, h - pad_y)
        grad.setColorAt(0.0, QColor(255, 0, 127, 85))
        grad.setColorAt(1.0, QColor(255, 0, 127, 5))
        painter.fillPath(fill_path, grad)

        pen = QPen(QColor("#ff007f"), 2.2)
        painter.setPen(pen)
        painter.drawPath(path)

        painter.setPen(QPen(QColor(0, 240, 255, 130), 1.0, Qt.DashLine))
        painter.drawLine(int(x_node), pad_y, int(x_node), h - pad_y)

        pt_node = QPointF(x_node, y_node)
        if self.is_dragging or self.is_hovered:
            painter.setBrush(QBrush(QColor(255, 0, 127, 80)))
            painter.setPen(QPen(QColor("#ff007f"), 1.5))
            painter.drawEllipse(pt_node, 9.0, 9.0)

        painter.setBrush(QBrush(QColor("#ffffff") if (self.is_dragging or self.is_hovered) else QColor("#00f0ff")))
        painter.setPen(QPen(QColor("#ff007f"), 2.0))
        painter.drawEllipse(pt_node, 4.5 if (self.is_dragging or self.is_hovered) else 3.5, 4.5 if (self.is_dragging or self.is_hovered) else 3.5)

        painter.setFont(QFont("Segoe UI", 7, QFont.Bold))
        painter.setPen(QColor("#94a3b8"))
        painter.drawText(pad_x, pad_y - 2, f"FILTRE {ftype.upper()} (2D INTERACTIF)")

        painter.setPen(QColor("#f43f5e"))
        painter.drawText(w - pad_x - 140, pad_y - 2, f"{int(self.cutoff)} Hz | Q: {self.resonance:.2f}")


class LayerCardWidget(QFrame):
    """Carte graphique représentant une couche sonore empilée dans la liste des layers"""
    def __init__(self, layer: SynthLayer, on_select, on_mute, on_solo, on_delete, on_bus_change=None, on_wave_change=None, parent=None):
        super().__init__(parent)
        self.layer = layer
        self.on_select = on_select
        self.on_mute = on_mute
        self.on_solo = on_solo
        self.on_delete = on_delete
        self.on_bus_change = on_bus_change
        self.on_wave_change = on_wave_change
        self.is_selected = False

        self.setFixedHeight(48)
        self.setCursor(Qt.PointingHandCursor)
        self._init_ui()
        self.update_style()

    def _init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(6)

        # Pastille couleur
        self.lbl_color = QLabel("●")
        self.lbl_color.setStyleSheet(f"color: {self.layer.color}; font-size: 14px;")
        layout.addWidget(self.lbl_color)

        # Nom de la couche
        self.lbl_info = QLabel(self.layer.name)
        self.lbl_info.setStyleSheet("color: #f1f5f9; font-weight: bold; font-size: 11px;")
        layout.addWidget(self.lbl_info, stretch=1)

        # Sélecteur interactif de forme d'onde directement sur la couche
        self.combo_wave = QComboBox()
        self.combo_wave.setFixedSize(86, 22)
        self.combo_wave.setStyleSheet("""
            QComboBox {
                background: #1a2238;
                color: #38bdf8;
                border: 1px solid #0284c7;
                border-radius: 3px;
                padding: 1px 4px;
                font-size: 10px;
                font-weight: bold;
            }
            QComboBox:hover {
                border-color: #00f0ff;
                background-color: #1e2942;
            }
            QComboBox::drop-down { border: none; width: 10px; }
            QComboBox QAbstractItemView {
                background: #0f172a;
                color: #e2e8f0;
                selection-background-color: #0284c7;
                selection-color: #ffffff;
            }
        """)
        for wave_id, wave_name, wave_icon in WAVEFORM_ITEMS_SHORT:
            self.combo_wave.addItem(f"{wave_icon} {wave_name}", userData=wave_id)

        self._sync_combo_wave()
        self.combo_wave.setToolTip("Forme d'onde de l'oscillateur pour cette couche sonore")
        self.combo_wave.currentIndexChanged.connect(self._on_wave_combo_changed)
        layout.addWidget(self.combo_wave)

        # Sélecteur de Sortie Stéréo (OUT 1 à OUT 10)
        self.combo_bus = QComboBox()
        self.combo_bus.setFixedSize(68, 22)
        self.combo_bus.setStyleSheet("""
            QComboBox {
                background: #0284c7;
                color: #ffffff;
                border: 1px solid #0369a1;
                border-radius: 3px;
                padding: 1px 4px;
                font-size: 9px;
                font-weight: bold;
            }
            QComboBox::drop-down { border: none; width: 12px; }
            QComboBox QAbstractItemView {
                background: #0f172a;
                color: #e2e8f0;
                selection-background-color: #0284c7;
            }
        """)
        for b_i in range(10):
            self.combo_bus.addItem(f"OUT {b_i + 1}", userData=b_i)
        self.combo_bus.setCurrentIndex(self.layer.output_bus)
        self.combo_bus.setToolTip("Routage vers l'une des 10 sorties stéréo indépendantes (Bus 1 à 10)")
        self.combo_bus.currentIndexChanged.connect(self._on_bus_combo_changed)
        layout.addWidget(self.combo_bus)

        # Bouton Mute
        self.btn_m = QPushButton("M")
        self.btn_m.setFixedSize(22, 22)
        self.btn_m.setCheckable(True)
        self.btn_m.setChecked(self.layer.muted)
        self.btn_m.setToolTip("MUTE : Couper temporairement le son de cette couche")
        self.btn_m.clicked.connect(lambda: self.on_mute(self.layer))
        layout.addWidget(self.btn_m)

        # Bouton Solo
        self.btn_s = QPushButton("S")
        self.btn_s.setFixedSize(22, 22)
        self.btn_s.setCheckable(True)
        self.btn_s.setChecked(self.layer.soloed)
        self.btn_s.setToolTip("SOLO : Écouter exclusivement cette couche sonore")
        self.btn_s.clicked.connect(lambda: self.on_solo(self.layer))
        layout.addWidget(self.btn_s)

        # Bouton Supprimer
        self.btn_del = QPushButton("🗑")
        self.btn_del.setFixedSize(22, 22)
        self.btn_del.setToolTip("SUPPRIMER : Retirer cette couche de l'empilement (Sound Stacking)")
        self.btn_del.setStyleSheet("""
            QPushButton {
                background: #2a1520;
                color: #ef4444;
                border: 1px solid #7f1d1d;
                border-radius: 3px;
                font-size: 11px;
            }
            QPushButton:hover {
                background: #dc2626;
                color: #ffffff;
            }
        """)
        self.btn_del.clicked.connect(lambda: self.on_delete(self.layer))
        layout.addWidget(self.btn_del)

    def _sync_combo_wave(self):
        self.combo_wave.blockSignals(True)
        cur_w = (self.layer.waveform or "saw").lower()
        for i in range(self.combo_wave.count()):
            if self.combo_wave.itemData(i) == cur_w:
                self.combo_wave.setCurrentIndex(i)
                break
        self.combo_wave.blockSignals(False)

    def _on_wave_combo_changed(self, idx: int):
        new_wave = self.combo_wave.itemData(idx)
        if new_wave and self.layer.waveform != new_wave:
            self.layer.waveform = new_wave
            if self.on_wave_change:
                self.on_wave_change(self.layer, new_wave)

    def _on_bus_combo_changed(self, idx: int):
        self.layer.output_bus = idx
        if self.on_bus_change:
            self.on_bus_change(self.layer, idx)

    def mousePressEvent(self, event):
        self.on_select(self.layer)
        super().mousePressEvent(event)

    def set_selected(self, sel: bool):
        self.is_selected = sel
        self.update_style()

    def update_style(self):
        border_col = self.layer.color if self.is_selected else "#222738"
        bg_col = "#1b2238" if self.is_selected else "#111625"
        self.setStyleSheet(f"""
            LayerCardWidget {{
                background-color: {bg_col};
                border: 1px solid {border_col};
                border-radius: 6px;
            }}
            QPushButton {{
                background: #232a3f;
                color: #94a3b8;
                border: 1px solid #334155;
                border-radius: 3px;
                font-size: 10px;
                font-weight: bold;
            }}
            QPushButton:checked {{
                background: #f59e0b;
                color: #000000;
            }}
        """)
        self._sync_combo_wave()
        self.combo_bus.blockSignals(True)
        self.combo_bus.setCurrentIndex(self.layer.output_bus)
        self.combo_bus.blockSignals(False)
        self.lbl_info.setText(self.layer.name)
        self.btn_m.setChecked(self.layer.muted)
        self.btn_s.setChecked(self.layer.soloed)


class MiniAuditionKeyboard(QWidget):
    """Clavier visuel interactif au bas de la fenêtre pour tester les sons instantanément"""
    def __init__(self, on_note_trigger, parent=None):
        super().__init__(parent)
        self.on_note_trigger = on_note_trigger
        self.setFixedHeight(50)
        self.setMinimumWidth(380)

        # Notes de C3 (48) à B4 (71) -> 24 touches
        self.base_pitch = 48
        self.num_keys = 24
        self.active_pitch: Optional[int] = None

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        h = self.height()

        white_key_indices = [i for i in range(self.num_keys) if (i % 12) not in (1, 3, 6, 8, 10)]
        num_whites = len(white_key_indices)
        kw = w / num_whites

        # Touches blanches
        w_idx = 0
        white_rects = {}
        for i in range(self.num_keys):
            is_black = (i % 12) in (1, 3, 6, 8, 10)
            if not is_black:
                x = w_idx * kw
                pitch = self.base_pitch + i
                is_active = (self.active_pitch == pitch)
                rect = QRectF(x, 0, kw - 1.0, h)
                white_rects[i] = rect

                grad = QLinearGradient(x, 0, x, h)
                if is_active:
                    grad.setColorAt(0.0, QColor("#00f0ff"))
                    grad.setColorAt(1.0, QColor("#0284c7"))
                else:
                    grad.setColorAt(0.0, QColor("#e2e8f0"))
                    grad.setColorAt(1.0, QColor("#cbd5e1"))

                painter.fillRect(rect, grad)
                painter.setPen(QColor("#475569"))
                painter.drawRect(rect)
                w_idx += 1

        # Touches noires
        bw = kw * 0.65
        bh = h * 0.60
        w_idx = 0
        for i in range(self.num_keys):
            is_black = (i % 12) in (1, 3, 6, 8, 10)
            if is_black:
                prev_white_x = (w_idx - 1) * kw
                x = prev_white_x + kw * 0.68
                pitch = self.base_pitch + i
                is_active = (self.active_pitch == pitch)
                rect = QRectF(x, 0, bw, bh)

                if is_active:
                    painter.fillRect(rect, QColor("#ff007f"))
                else:
                    painter.fillRect(rect, QColor("#0f172a"))

                painter.setPen(QColor("#020617"))
                painter.drawRect(rect)
            else:
                w_idx += 1

    def mousePressEvent(self, event):
        x = event.position().x()
        y = event.position().y()

        w = self.width()
        h = self.height()
        white_key_indices = [i for i in range(self.num_keys) if (i % 12) not in (1, 3, 6, 8, 10)]
        kw = w / len(white_key_indices)
        bw = kw * 0.65
        bh = h * 0.60

        clicked_pitch = None

        # Priorité aux touches noires si le clic est en haut
        if y <= bh:
            w_idx = 0
            for i in range(self.num_keys):
                is_black = (i % 12) in (1, 3, 6, 8, 10)
                if is_black:
                    prev_white_x = (w_idx - 1) * kw
                    bx = prev_white_x + kw * 0.68
                    if bx <= x <= bx + bw:
                        clicked_pitch = self.base_pitch + i
                        break
                else:
                    w_idx += 1

        if clicked_pitch is None:
            # Touche blanche
            w_idx = int(x // kw)
            if 0 <= w_idx < len(white_key_indices):
                clicked_pitch = self.base_pitch + white_key_indices[w_idx]

        if clicked_pitch:
            self.active_pitch = clicked_pitch
            self.update()
            self.on_note_trigger(clicked_pitch)
            QTimer.singleShot(200, self._release_note)

    def _release_note(self):
        self.active_pitch = None
        self.update()


class NovaSynthGUI(QWidget):
    """
    Interface Graphique Principale de NovaSynth.
    Regroupe le visualiseur cybernétique, la gestion de l'empilement de couches,
    la matrice des 10 sorties stéréo, le sound sculpting et le clavier d'écoute.
    """
    def __init__(self, plugin: NovaSynthPlugin, parent=None):
        super().__init__(parent)
        self.plugin = plugin
        self.selected_layer: Optional[SynthLayer] = self.plugin.layers[0] if self.plugin.layers else None

        self.setWindowTitle(f"NovaSynth — {self.plugin.preset_name}")
        self.resize(1080, 720)
        self.setMinimumSize(980, 640)

        # Cache d'audition ultra-rapide (<1ms) pour une réactivité clavier instantanée
        self._note_cache: Dict[int, np.ndarray] = {}
        self._cache_lock = threading.Lock()
        self._cache_generation = 0
        self._prewarm_timer = QTimer(self)
        self._prewarm_timer.setSingleShot(True)
        self._prewarm_timer.setInterval(200)
        self._prewarm_timer.timeout.connect(self._do_prewarm_worker)
        self._is_prewarming = False

        self._init_layout()
        self._sync_all()

        # Connecter l'oscilloscope au flux audio temps réel du synthétiseur
        self.plugin.live_audio_callback = self.scope.set_audio_data

        # Démarrer le préchauffage en arrière-plan du cache pour les notes du clavier
        self._trigger_cache_prewarm()

    def _init_layout(self):
        # Widget de fond cybernétique personnalisé
        bg = CyberBackgroundWidget(self)
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.addWidget(bg)

        main_layout = QVBoxLayout(bg)
        main_layout.setContentsMargins(14, 12, 14, 12)
        main_layout.setSpacing(10)

        # -------------------------------------------------------------
        # 1. TOP HEADER : Titre Néon, Presets & Oscilloscope
        # -------------------------------------------------------------
        header_frame = QFrame()
        header_frame.setStyleSheet("""
            QFrame {
                background: rgba(15, 20, 35, 0.75);
                border: 1px solid #1e293b;
                border-radius: 8px;
            }
        """)
        h_layout = QHBoxLayout(header_frame)
        h_layout.setContentsMargins(12, 8, 12, 8)
        h_layout.setSpacing(12)

        # Logo & Titre
        v_title = QVBoxLayout()
        lbl_title = QLabel("⚡ N O V A S Y N T H")
        lbl_title.setStyleSheet("color: #00f0ff; font-family: 'Segoe UI', sans-serif; font-size: 16px; font-weight: bold; letter-spacing: 2px;")
        lbl_sub = QLabel("Modular Multi-Layer Polyphonic Synthesizer // 10-Bus Multi-Out")
        lbl_sub.setStyleSheet("color: #64748b; font-size: 9px; font-weight: bold;")
        v_title.addWidget(lbl_title)
        v_title.addWidget(lbl_sub)
        h_layout.addLayout(v_title)

        # Sélecteur de Presets
        v_preset = QVBoxLayout()
        v_preset.addWidget(QLabel("Preset Sonore :"))
        h_preset_bar = QHBoxLayout()
        h_preset_bar.setSpacing(4)

        self.combo_presets = QComboBox()
        self.combo_presets.setStyleSheet("""
            QComboBox {
                background-color: #1e293b;
                color: #f8fafc;
                border: 1px solid #334155;
                border-radius: 4px;
                padding: 4px 10px;
                min-width: 170px;
                font-weight: bold;
            }
        """)
        self.btn_save_preset = QPushButton("💾")
        self.btn_save_preset.setToolTip("Enregistrer le timbre actuel sous forme de preset")
        self.btn_save_preset.setFixedWidth(32)
        self.btn_save_preset.setStyleSheet("""
            QPushButton {
                background-color: #0f172a;
                color: #38bdf8;
                border: 1px solid #0284c7;
                border-radius: 4px;
                font-size: 13px;
                padding: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #0284c7;
                color: white;
            }
        """)
        self.btn_save_preset.clicked.connect(self._save_current_preset)

        h_preset_bar.addWidget(self.combo_presets, stretch=1)
        h_preset_bar.addWidget(self.btn_save_preset)
        v_preset.addLayout(h_preset_bar)
        h_layout.addLayout(v_preset)

        self._populate_presets_combo()
        self.combo_presets.currentIndexChanged.connect(self._on_preset_selected)

        # Oscilloscope cybernétique
        self.scope = CyberOscilloscope()
        h_layout.addWidget(self.scope, stretch=1)

        main_layout.addWidget(header_frame)

        # -------------------------------------------------------------
        # 2. CORPS CENTRAL : Liste des Couches (Gauche) & Sculpting (Droite)
        # -------------------------------------------------------------
        center_row = QHBoxLayout()
        center_row.setSpacing(10)

        # --- Colonne Gauche : Stacking de Couches Sonores (Layers) ---
        group_layers = QGroupBox("EMPILEMENT DE COUCHES (SOUND STACKING)")
        group_layers.setStyleSheet(self._group_style("#00f0ff"))
        v_layers = QVBoxLayout(group_layers)
        v_layers.setContentsMargins(8, 10, 8, 8)
        v_layers.setSpacing(5)

        # Sous-titre explicatif sur l'empilement sonore
        lbl_stack_info = QLabel("Superposition d'oscillateurs indépendants (Lead, Sub, etc.)")
        lbl_stack_info.setStyleSheet("color: #38bdf8; font-size: 8px; font-weight: normal; margin-left: 2px;")
        v_layers.addWidget(lbl_stack_info)

        # En-tête des colonnes du tableau des couches
        hdr_widget = QWidget()
        h_hdr = QHBoxLayout(hdr_widget)
        h_hdr.setContentsMargins(8, 2, 8, 2)
        h_hdr.setSpacing(6)
        lbl_h1 = QLabel("COUCHE / TIMBRE")
        lbl_h1.setStyleSheet("color: #64748b; font-size: 8px; font-weight: bold;")
        lbl_h_wave = QLabel("ONDE")
        lbl_h_wave.setStyleSheet("color: #64748b; font-size: 8px; font-weight: bold; min-width: 86px; text-align: center;")
        lbl_h2 = QLabel("SORTIE")
        lbl_h2.setStyleSheet("color: #64748b; font-size: 8px; font-weight: bold; min-width: 68px; text-align: center;")
        lbl_h3 = QLabel("M")
        lbl_h3.setToolTip("Mute : Couper le son de cette couche")
        lbl_h3.setStyleSheet("color: #64748b; font-size: 8px; font-weight: bold; width: 22px; text-align: center;")
        lbl_h4 = QLabel("S")
        lbl_h4.setToolTip("Solo : Écouter uniquement cette couche")
        lbl_h4.setStyleSheet("color: #64748b; font-size: 8px; font-weight: bold; width: 22px; text-align: center;")
        lbl_h5 = QLabel("🗑")
        lbl_h5.setToolTip("Supprimer cette couche sonore")
        lbl_h5.setStyleSheet("color: #64748b; font-size: 8px; font-weight: bold; width: 22px; text-align: center;")
        h_hdr.addWidget(lbl_h1, stretch=1)
        h_hdr.addWidget(lbl_h_wave)
        h_hdr.addWidget(lbl_h2)
        h_hdr.addWidget(lbl_h3)
        h_hdr.addWidget(lbl_h4)
        h_hdr.addWidget(lbl_h5)
        v_layers.addWidget(hdr_widget)

        self.scroll_layers = QScrollArea()
        self.scroll_layers.setWidgetResizable(True)
        self.scroll_layers.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.layer_container = QWidget()
        self.layer_vbox = QVBoxLayout(self.layer_container)
        self.layer_vbox.setContentsMargins(0, 0, 0, 0)
        self.layer_vbox.setSpacing(4)
        self.scroll_layers.setWidget(self.layer_container)
        v_layers.addWidget(self.scroll_layers, stretch=1)

        # Boutons d'action Couches
        h_layer_btns = QHBoxLayout()
        self.btn_add_layer = QPushButton("➕ Ajouter Couche")
        self.btn_add_layer.clicked.connect(self._on_add_layer_clicked)

        # Menu déroulant rapide pour choisir directement la forme d'onde à l'ajout
        self.btn_add_wave = QPushButton("▾")
        self.btn_add_wave.setFixedWidth(24)
        self.btn_add_wave.setToolTip("Ajouter une couche sonore avec une forme d'onde spécifique...")
        self.menu_add_wave = QMenu(self)
        self.menu_add_wave.setStyleSheet("""
            QMenu {
                background-color: #0f172a;
                color: #f1f5f9;
                border: 1px solid #00f0ff;
                border-radius: 4px;
                padding: 4px;
                font-weight: bold;
                font-size: 11px;
            }
            QMenu::item {
                padding: 6px 14px;
                border-radius: 3px;
            }
            QMenu::item:selected {
                background-color: #0284c7;
                color: #ffffff;
            }
        """)
        for w_id, w_name, w_ico in WAVEFORM_ITEMS_FULL:
            act = self.menu_add_wave.addAction(f"{w_ico}  Ajouter Couche {w_name}")
            act.triggered.connect(lambda checked=False, wid=w_id: self._on_add_layer_with_waveform(wid))
        self.btn_add_wave.setMenu(self.menu_add_wave)

        self.btn_dup_layer = QPushButton("📋 Dupliquer")
        self.btn_dup_layer.clicked.connect(self._on_dup_layer_clicked)
        h_layer_btns.addWidget(self.btn_add_layer)
        h_layer_btns.addWidget(self.btn_add_wave)
        h_layer_btns.addWidget(self.btn_dup_layer)
        v_layers.addLayout(h_layer_btns)

        center_row.addWidget(group_layers, stretch=4)

        # --- Colonne Droite : Sound Sculpting de la couche sélectionnée ---
        group_sculpt = QGroupBox("SCULPTURE SONORE (COUCHE SÉLECTIONNÉE)")
        group_sculpt.setStyleSheet(self._group_style("#ff007f"))
        v_sculpt = QVBoxLayout(group_sculpt)
        v_sculpt.setContentsMargins(12, 12, 12, 10)
        v_sculpt.setSpacing(10)

        # -------------------------------------------------------------
        # En-tête Couche Active & Sélecteur Forme d'Onde en haut à droite
        # -------------------------------------------------------------
        header_layer_bar = QFrame()
        header_layer_bar.setObjectName("header_layer_bar")
        header_layer_bar.setFixedHeight(38)
        header_layer_bar.setStyleSheet("""
            QFrame#header_layer_bar {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 rgba(15, 23, 42, 0.95), stop:1 rgba(30, 41, 59, 0.75));
                border: 1px solid #334155;
                border-radius: 6px;
            }
            QLabel {
                border: none;
                background: transparent;
            }
        """)
        h_layer_bar = QHBoxLayout(header_layer_bar)
        h_layer_bar.setContentsMargins(10, 4, 10, 4)
        h_layer_bar.setSpacing(10)

        self.lbl_selected_layer_badge = QLabel("● COUCHE ACTIVE :")
        self.lbl_selected_layer_badge.setStyleSheet("color: #00f0ff; font-weight: bold; font-size: 11px; border: none; background: transparent;")
        self.lbl_selected_layer_name = QLabel("Layer 1")
        self.lbl_selected_layer_name.setStyleSheet("color: #f8fafc; font-weight: bold; font-size: 13px; border: none; background: transparent;")
        h_layer_bar.addWidget(self.lbl_selected_layer_badge)
        h_layer_bar.addWidget(self.lbl_selected_layer_name)

        h_layer_bar.addStretch(1)

        # En haut à droite : Sélecteur de Forme d'Onde
        lbl_wave_prompt = QLabel("FORME D'ONDE :")
        lbl_wave_prompt.setStyleSheet("color: #00f0ff; font-weight: bold; font-size: 11px; letter-spacing: 0.5px;")
        h_layer_bar.addWidget(lbl_wave_prompt)

        self.combo_wave = QComboBox()
        self.combo_wave.setFixedSize(210, 26)
        self.combo_wave.setStyleSheet("""
            QComboBox {
                background-color: #0f172a;
                color: #38bdf8;
                border: 1.5px solid #0284c7;
                border-radius: 4px;
                padding: 2px 8px;
                font-size: 11px;
                font-weight: bold;
            }
            QComboBox:hover {
                border-color: #00f0ff;
                background-color: #162036;
            }
            QComboBox::drop-down {
                border: none;
                width: 14px;
            }
            QComboBox QAbstractItemView {
                background-color: #0f172a;
                color: #f1f5f9;
                selection-background-color: #0284c7;
                selection-color: #ffffff;
                border: 1px solid #00f0ff;
                padding: 4px;
            }
        """)
        for wave_id, wave_name, wave_icon in WAVEFORM_ITEMS_FULL:
            self.combo_wave.addItem(f"{wave_icon} {wave_name}", userData=wave_id)

        self.combo_wave.setToolTip("Choisir la forme d'onde sonore de l'oscillateur pour cette couche")
        self.combo_wave.currentIndexChanged.connect(self._on_wave_combo_changed)
        h_layer_bar.addWidget(self.combo_wave)

        v_sculpt.addWidget(header_layer_bar)

        # -------------------------------------------------------------
        # Ligne Paramètres Oscillateur : Octave, Demi-tons, Volume, Pan, Sortie
        # -------------------------------------------------------------
        row_osc = QHBoxLayout()
        row_osc.setSpacing(10)

        # Octave
        v_oct = QVBoxLayout()
        v_oct.setSpacing(3)
        lbl_oct = QLabel("OCTAVE :")
        lbl_oct.setStyleSheet("color: #94a3b8; font-size: 9px; font-weight: bold;")
        v_oct.addWidget(lbl_oct, alignment=Qt.AlignHCenter)
        self.combo_oct = QComboBox()
        self.combo_oct.setFixedHeight(24)
        for o in [-2, -1, 0, 1, 2]:
            self.combo_oct.addItem(f"{o:+d}" if o != 0 else "0", userData=o)
        self.combo_oct.currentIndexChanged.connect(self._on_oct_changed)
        v_oct.addWidget(self.combo_oct)
        lbl_oct_sub = QLabel("hauteur")
        lbl_oct_sub.setStyleSheet("color: #64748b; font-size: 9px;")
        v_oct.addWidget(lbl_oct_sub, alignment=Qt.AlignHCenter)
        row_osc.addLayout(v_oct)

        # Demi-tons
        v_semi = QVBoxLayout()
        v_semi.setSpacing(3)
        lbl_semi = QLabel("DEMI-TONS :")
        lbl_semi.setStyleSheet("color: #94a3b8; font-size: 9px; font-weight: bold;")
        v_semi.addWidget(lbl_semi, alignment=Qt.AlignHCenter)
        self.slider_semi = QSlider(Qt.Horizontal)
        self.slider_semi.setFixedHeight(24)
        self.slider_semi.setRange(-12, 12)
        self.slider_semi.valueChanged.connect(self._on_semi_changed)
        v_semi.addWidget(self.slider_semi)
        self.lbl_semi_val = QLabel("0 st")
        self.lbl_semi_val.setStyleSheet("color: #f1f5f9; font-size: 10px; font-weight: bold;")
        v_semi.addWidget(self.lbl_semi_val, alignment=Qt.AlignHCenter)
        row_osc.addLayout(v_semi)

        # Volume Couche
        v_lvol = QVBoxLayout()
        v_lvol.setSpacing(3)
        lbl_vol = QLabel("VOLUME :")
        lbl_vol.setStyleSheet("color: #94a3b8; font-size: 9px; font-weight: bold;")
        v_lvol.addWidget(lbl_vol, alignment=Qt.AlignHCenter)
        self.slider_lvol = QSlider(Qt.Horizontal)
        self.slider_lvol.setFixedHeight(24)
        self.slider_lvol.setRange(0, 150)
        self.slider_lvol.valueChanged.connect(self._on_lvol_changed)
        v_lvol.addWidget(self.slider_lvol)
        self.lbl_lvol_val = QLabel("80%")
        self.lbl_lvol_val.setStyleSheet("color: #f1f5f9; font-size: 10px; font-weight: bold;")
        v_lvol.addWidget(self.lbl_lvol_val, alignment=Qt.AlignHCenter)
        row_osc.addLayout(v_lvol)

        # Panoramique
        v_lpan = QVBoxLayout()
        v_lpan.setSpacing(3)
        lbl_pan = QLabel("PANORAMIQUE :")
        lbl_pan.setStyleSheet("color: #94a3b8; font-size: 9px; font-weight: bold;")
        v_lpan.addWidget(lbl_pan, alignment=Qt.AlignHCenter)
        self.slider_lpan = QSlider(Qt.Horizontal)
        self.slider_lpan.setFixedHeight(24)
        self.slider_lpan.setRange(-100, 100)
        self.slider_lpan.valueChanged.connect(self._on_lpan_changed)
        v_lpan.addWidget(self.slider_lpan)
        self.lbl_lpan_val = QLabel("C")
        self.lbl_lpan_val.setStyleSheet("color: #f1f5f9; font-size: 10px; font-weight: bold;")
        v_lpan.addWidget(self.lbl_lpan_val, alignment=Qt.AlignHCenter)
        row_osc.addLayout(v_lpan)

        # Sortie Stéréo (1 à 10)
        v_bus = QVBoxLayout()
        v_bus.setSpacing(3)
        lbl_bus = QLabel("SORTIE STÉRÉO :")
        lbl_bus.setStyleSheet("color: #94a3b8; font-size: 9px; font-weight: bold;")
        v_bus.addWidget(lbl_bus, alignment=Qt.AlignHCenter)
        self.combo_bus = QComboBox()
        self.combo_bus.setFixedHeight(24)
        for b_i in range(10):
            self.combo_bus.addItem(f"Sortie {b_i + 1} (Bus {b_i})", userData=b_i)
        self.combo_bus.currentIndexChanged.connect(self._on_bus_changed)
        v_bus.addWidget(self.combo_bus)
        lbl_bus_sub = QLabel("routage")
        lbl_bus_sub.setStyleSheet("color: #64748b; font-size: 9px;")
        v_bus.addWidget(lbl_bus_sub, alignment=Qt.AlignHCenter)
        row_osc.addLayout(v_bus)

        v_sculpt.addLayout(row_osc)

        # Ligne 2 : Filtre et Enveloppe ADSR (Visualisateurs & Sliders)
        row_dsp = QHBoxLayout()
        row_dsp.setSpacing(10)

        # Section Filtre
        v_filt_col = QVBoxLayout()
        self.filter_view = FilterCurveWidget()
        self.filter_view.filter_changed.connect(self._on_filter_view_dragged)
        v_filt_col.addWidget(self.filter_view)

        h_filt_ctrls = QHBoxLayout()
        self.combo_ftype = QComboBox()
        self.combo_ftype.addItems(["Lowpass", "Highpass", "Bandpass", "Notch"])
        self.combo_ftype.currentTextChanged.connect(self._on_filter_type_changed)
        h_filt_ctrls.addWidget(self.combo_ftype)

        # Cutoff
        self.slider_cutoff = QSlider(Qt.Horizontal)
        self.slider_cutoff.setRange(20, 15000)
        self.slider_cutoff.valueChanged.connect(self._on_cutoff_changed)
        h_filt_ctrls.addWidget(QLabel("Cutoff:"))
        h_filt_ctrls.addWidget(self.slider_cutoff)

        # Resonance
        self.slider_res = QSlider(Qt.Horizontal)
        self.slider_res.setRange(2, 50)
        self.slider_res.valueChanged.connect(self._on_res_changed)
        h_filt_ctrls.addWidget(QLabel("Res:"))
        h_filt_ctrls.addWidget(self.slider_res)

        v_filt_col.addLayout(h_filt_ctrls)
        row_dsp.addLayout(v_filt_col, stretch=1)

        # Section ADSR
        v_adsr_col = QVBoxLayout()
        self.adsr_view = InteractiveADSRWidget()
        self.adsr_view.adsr_changed.connect(self._on_adsr_view_dragged)
        v_adsr_col.addWidget(self.adsr_view)

        h_adsr_ctrls = QHBoxLayout()
        # Attack
        self.slider_att = QSlider(Qt.Horizontal)
        self.slider_att.setRange(1, 2000)  # ms
        self.slider_att.valueChanged.connect(self._on_adsr_changed)
        h_adsr_ctrls.addWidget(QLabel("A:"))
        h_adsr_ctrls.addWidget(self.slider_att)

        # Decay
        self.slider_dec = QSlider(Qt.Horizontal)
        self.slider_dec.setRange(5, 3000)
        self.slider_dec.valueChanged.connect(self._on_adsr_changed)
        h_adsr_ctrls.addWidget(QLabel("D:"))
        h_adsr_ctrls.addWidget(self.slider_dec)

        # Sustain
        self.slider_sus = QSlider(Qt.Horizontal)
        self.slider_sus.setRange(0, 100)
        self.slider_sus.valueChanged.connect(self._on_adsr_changed)
        h_adsr_ctrls.addWidget(QLabel("S:"))
        h_adsr_ctrls.addWidget(self.slider_sus)

        # Release
        self.slider_rel = QSlider(Qt.Horizontal)
        self.slider_rel.setRange(5, 4000)
        self.slider_rel.valueChanged.connect(self._on_adsr_changed)
        h_adsr_ctrls.addWidget(QLabel("R:"))
        h_adsr_ctrls.addWidget(self.slider_rel)

        v_adsr_col.addLayout(h_adsr_ctrls)
        row_dsp.addLayout(v_adsr_col, stretch=1)

        v_sculpt.addLayout(row_dsp)
        center_row.addWidget(group_sculpt, stretch=6)

        main_layout.addLayout(center_row, stretch=1)

        # -------------------------------------------------------------
        # 3. BOTTOM RACK : Master FX (Délai & Réverb) et Clavier d'écoute
        # -------------------------------------------------------------
        bottom_frame = QFrame()
        bottom_frame.setStyleSheet("""
            QFrame {
                background: rgba(15, 20, 35, 0.75);
                border: 1px solid #1e293b;
                border-radius: 8px;
            }
        """)
        bot_layout = QHBoxLayout(bottom_frame)
        bot_layout.setContentsMargins(10, 6, 10, 6)
        bot_layout.setSpacing(12)

        # Délai Stéréo
        self.chk_delay = QCheckBox("Ping-Pong Delay")
        self.chk_delay.setChecked(self.plugin.delay.enabled)
        self.chk_delay.toggled.connect(self._on_delay_toggled)
        bot_layout.addWidget(self.chk_delay)

        bot_layout.addWidget(QLabel("Delay Mix:"))
        self.slider_del_mix = QSlider(Qt.Horizontal)
        self.slider_del_mix.setRange(0, 100)
        self.slider_del_mix.setValue(int(self.plugin.delay.mix * 100))
        self.slider_del_mix.valueChanged.connect(self._on_delay_mix_changed)
        bot_layout.addWidget(self.slider_del_mix)

        # Réverbération
        self.chk_reverb = QCheckBox("Studio Reverb")
        self.chk_reverb.setChecked(self.plugin.reverb.enabled)
        self.chk_reverb.toggled.connect(self._on_reverb_toggled)
        bot_layout.addWidget(self.chk_reverb)

        bot_layout.addWidget(QLabel("Reverb Mix:"))
        self.slider_rev_mix = QSlider(Qt.Horizontal)
        self.slider_rev_mix.setRange(0, 100)
        self.slider_rev_mix.setValue(int(self.plugin.reverb.mix * 100))
        self.slider_rev_mix.valueChanged.connect(self._on_reverb_mix_changed)
        bot_layout.addWidget(self.slider_rev_mix)

        # Master Vol
        bot_layout.addWidget(QLabel("Master Vol:"))
        self.slider_master = QSlider(Qt.Horizontal)
        self.slider_master.setRange(0, 150)
        self.slider_master.setValue(int(self.plugin.master_volume * 100))
        self.slider_master.valueChanged.connect(self._on_master_vol_changed)
        bot_layout.addWidget(self.slider_master)

        main_layout.addWidget(bottom_frame)

        # Clavier d'écoute temps réel
        self.audition_keys = MiniAuditionKeyboard(self._on_audition_note)
        main_layout.addWidget(self.audition_keys)

    def _group_style(self, accent_color: str) -> str:
        return f"""
            QGroupBox {{
                background-color: rgba(15, 22, 38, 0.70);
                border: 1px solid {accent_color}44;
                border-radius: 8px;
                margin-top: 18px;
                font-weight: bold;
                color: #e2e8f0;
                font-size: 11px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
                color: {accent_color};
            }}
            QLabel {{
                color: #94a3b8;
                font-size: 10px;
                font-weight: bold;
            }}
            QSlider::groove:horizontal {{
                background: #1e293b;
                height: 5px;
                border-radius: 2px;
            }}
            QSlider::sub-page:horizontal {{
                background: {accent_color};
                border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                background: #f8fafc;
                border: 1px solid {accent_color};
                width: 12px;
                margin-top: -4px;
                margin-bottom: -4px;
                border-radius: 6px;
            }}
            QPushButton {{
                background: #1e293b;
                color: #f1f5f9;
                border: 1px solid #334155;
                border-radius: 4px;
                padding: 5px 10px;
                font-size: 11px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background: #334155;
                border-color: {accent_color};
            }}
            QComboBox {{
                background-color: #1e293b;
                color: #f8fafc;
                border: 1px solid #334155;
                border-radius: 4px;
                padding: 3px 8px;
                font-size: 10px;
                font-weight: bold;
            }}
            QCheckBox {{
                color: #f1f5f9;
                font-weight: bold;
                font-size: 11px;
            }}
        """

    def _sync_all(self):
        # 1. Mettre à jour la liste des couches sonores
        while self.layer_vbox.count():
            item = self.layer_vbox.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self.layer_cards = []
        for l in self.plugin.layers:
            card = LayerCardWidget(
                layer=l,
                on_select=self._select_layer,
                on_mute=self._toggle_mute,
                on_solo=self._toggle_solo,
                on_delete=self._delete_layer,
                on_bus_change=self._on_layer_bus_card_changed,
                on_wave_change=self._on_layer_wave_card_changed,
                parent=self.layer_container
            )
            card.set_selected(l == self.selected_layer)
            self.layer_cards.append(card)
            self.layer_vbox.addWidget(card)

        self.layer_vbox.addStretch(1)

        # 2. Synchroniser les contrôles de la couche sélectionnée
        if self.selected_layer:
            l = self.selected_layer
            self.lbl_selected_layer_badge.setStyleSheet(f"color: {l.color}; font-weight: bold; font-size: 11px;")
            self.lbl_selected_layer_name.setText(l.name)

            self.combo_wave.blockSignals(True)
            cur_w = (l.waveform or "saw").lower()
            for idx in range(self.combo_wave.count()):
                if self.combo_wave.itemData(idx) == cur_w:
                    self.combo_wave.setCurrentIndex(idx)
                    break
            self.combo_wave.blockSignals(False)

            self.combo_oct.blockSignals(True)
            for idx in range(self.combo_oct.count()):
                if self.combo_oct.itemData(idx) == l.octave:
                    self.combo_oct.setCurrentIndex(idx)
                    break
            self.combo_oct.blockSignals(False)

            self.slider_semi.blockSignals(True)
            self.slider_semi.setValue(l.semitone)
            self.lbl_semi_val.setText(f"{l.semitone:+d} st")
            self.slider_semi.blockSignals(False)

            self.slider_lvol.blockSignals(True)
            self.slider_lvol.setValue(int(l.volume * 100))
            self.lbl_lvol_val.setText(f"{int(l.volume * 100)}%")
            self.slider_lvol.blockSignals(False)

            self.slider_lpan.blockSignals(True)
            self.slider_lpan.setValue(int(l.pan * 100))
            self.lbl_lpan_val.setText("C" if l.pan == 0 else (f"G{abs(int(l.pan * 100))}" if l.pan < 0 else f"D{int(l.pan * 100)}"))
            self.slider_lpan.blockSignals(False)

            self.combo_bus.blockSignals(True)
            self.combo_bus.setCurrentIndex(l.output_bus)
            self.combo_bus.blockSignals(False)

            # Filtre
            self.combo_ftype.blockSignals(True)
            self.combo_ftype.setCurrentText(l.filter_type.capitalize())
            self.combo_ftype.blockSignals(False)

            self.slider_cutoff.blockSignals(True)
            self.slider_cutoff.setValue(int(l.cutoff))
            self.slider_cutoff.blockSignals(False)

            self.slider_res.blockSignals(True)
            self.slider_res.setValue(int(l.resonance * 10))
            self.slider_res.blockSignals(False)

            self.filter_view.set_filter(l.filter_type, l.cutoff, l.resonance)

            # ADSR
            self.slider_att.blockSignals(True)
            self.slider_att.setValue(int(l.attack * 1000))
            self.slider_att.blockSignals(False)

            self.slider_dec.blockSignals(True)
            self.slider_dec.setValue(int(l.decay * 1000))
            self.slider_dec.blockSignals(False)

            self.slider_sus.blockSignals(True)
            self.slider_sus.setValue(int(l.sustain * 100))
            self.slider_sus.blockSignals(False)

            self.slider_rel.blockSignals(True)
            self.slider_rel.setValue(int(l.release * 1000))
            self.slider_rel.blockSignals(False)

            self.adsr_view.set_adsr(l.attack, l.decay, l.sustain, l.release)

        # 3. Synchroniser la sélection du combo des presets
        self.combo_presets.blockSignals(True)
        cur_name = self.plugin.preset_name
        for i in range(self.combo_presets.count()):
            if self.combo_presets.itemData(i) == cur_name:
                self.combo_presets.setCurrentIndex(i)
                break
        self.combo_presets.blockSignals(False)

    def _invalidate_note_cache(self):
        """Invalide le cache des notes pré-calculées quand un paramètre est modifié."""
        self._cache_generation += 1
        with self._cache_lock:
            self._note_cache.clear()
        # Debounce : évite de recalculer des notes lourdes pendant que l'utilisateur déplace un curseur ou un point 2D
        self._prewarm_timer.start(180)

    def _trigger_cache_prewarm(self):
        """Déclenche la pré-génération debouncée des notes de pré-écoute."""
        self._prewarm_timer.start(60)

    def _do_prewarm_worker(self):
        """Pré-génère en tâche de fond les notes principales sans monopoliser le processeur."""
        if self._is_prewarming:
            return

        # Si le moteur audio est en cours de lecture, ne pas exécuter de pré-rendu en tâche de fond
        try:
            from core.audio_engine import get_global_audio_engine
            engine = get_global_audio_engine()
            if engine and getattr(engine, "is_playing", False):
                return
        except Exception:
            pass

        self._is_prewarming = True
        gen = self._cache_generation

        def worker():
            try:
                # Octave centrale C4-C5 en priorité
                key_notes = [60, 64, 67, 72, 62, 65, 69, 71]
                for p in key_notes:
                    if gen != self._cache_generation:
                        return
                    with self._cache_lock:
                        already = (p in self._note_cache)
                    if not already:
                        w = self.plugin.render_note(p, duration_sec=0.35, sample_rate=44100, velocity=105)
                        with self._cache_lock:
                            if gen == self._cache_generation:
                                self._note_cache[p] = w
                    # Pause pour laisser respirer le processeur
                    time.sleep(0.01)
            except Exception:
                pass
            finally:
                self._is_prewarming = False

        t = threading.Thread(target=worker, daemon=True)
        t.start()

    def _select_layer(self, layer: SynthLayer):
        self.selected_layer = layer
        self._sync_all()

    def _on_layer_bus_card_changed(self, layer: SynthLayer, bus_idx: int):
        layer.output_bus = bus_idx
        if self.selected_layer == layer:
            self.combo_bus.blockSignals(True)
            self.combo_bus.setCurrentIndex(bus_idx)
            self.combo_bus.blockSignals(False)
        self._invalidate_note_cache()

    def _toggle_mute(self, layer: SynthLayer):
        layer.muted = not layer.muted
        self._invalidate_note_cache()
        self._sync_all()

    def _toggle_solo(self, layer: SynthLayer):
        layer.soloed = not layer.soloed
        self._invalidate_note_cache()
        self._sync_all()

    def _delete_layer(self, layer: SynthLayer):
        if len(self.plugin.layers) <= 1:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(
                self,
                "Empilement de couches",
                "Le synthétiseur doit conserver au moins 1 couche d'oscillateur active."
            )
            return
        self.plugin.remove_layer(layer.layer_id)
        if self.selected_layer == layer:
            self.selected_layer = self.plugin.layers[0]
        self._invalidate_note_cache()
        self._sync_all()

    def _on_add_layer_clicked(self):
        next_bus = min(9, len(self.plugin.layers))
        wave = self.selected_layer.waveform if self.selected_layer else "saw"
        new_l = self.plugin.add_layer(name=f"Layer {len(self.plugin.layers) + 1}", waveform=wave, output_bus=next_bus)
        self.selected_layer = new_l
        self._invalidate_note_cache()
        self._sync_all()

    def _on_add_layer_with_waveform(self, waveform: str):
        next_bus = min(9, len(self.plugin.layers))
        wave_clean = waveform.capitalize()
        new_l = self.plugin.add_layer(name=f"Layer {len(self.plugin.layers) + 1} ({wave_clean})", waveform=waveform, output_bus=next_bus)
        self.selected_layer = new_l
        self._invalidate_note_cache()
        self._sync_all()

    def _on_dup_layer_clicked(self):
        if self.selected_layer:
            dup = self.plugin.duplicate_layer(self.selected_layer.layer_id)
            if dup:
                self.selected_layer = dup
                self._invalidate_note_cache()
                self._sync_all()

    def _on_wave_combo_changed(self, idx: int):
        if self.selected_layer:
            wave = self.combo_wave.itemData(idx)
            if wave and self.selected_layer.waveform != wave:
                self.selected_layer.waveform = wave
                self._invalidate_note_cache()
                for card in self.layer_cards:
                    if card.layer == self.selected_layer:
                        card._sync_combo_wave()
                        break

    def _on_layer_wave_card_changed(self, layer: SynthLayer, wave: str):
        layer.waveform = wave
        if self.selected_layer != layer:
            self._select_layer(layer)
        else:
            self.combo_wave.blockSignals(True)
            for idx in range(self.combo_wave.count()):
                if self.combo_wave.itemData(idx) == wave.lower():
                    self.combo_wave.setCurrentIndex(idx)
                    break
            self.combo_wave.blockSignals(False)
            self._invalidate_note_cache()

    def _on_wave_changed(self, text: str):
        if self.selected_layer:
            w = text.lower().strip()
            for token in ["supersaw", "saw", "square", "sine", "triangle", "noise", "fm"]:
                if token in w:
                    w = token
                    break
            self.selected_layer.waveform = w
            self._invalidate_note_cache()
            self._sync_all()

    def _on_oct_changed(self, index: int):
        if self.selected_layer:
            self.selected_layer.octave = int(self.combo_oct.itemData(index))
            self._invalidate_note_cache()
            self._sync_all()

    def _on_semi_changed(self, val: int):
        if self.selected_layer:
            self.selected_layer.semitone = val
            self.lbl_semi_val.setText(f"{val:+d} st")
            self._invalidate_note_cache()

    def _on_lvol_changed(self, val: int):
        if self.selected_layer:
            self.selected_layer.volume = val / 100.0
            self.lbl_lvol_val.setText(f"{val}%")
            self._invalidate_note_cache()

    def _on_lpan_changed(self, val: int):
        if self.selected_layer:
            self.selected_layer.pan = val / 100.0
            self.lbl_lpan_val.setText("C" if val == 0 else (f"G{abs(val)}" if val < 0 else f"D{val}"))
            self._invalidate_note_cache()

    def _on_bus_changed(self, index: int):
        if self.selected_layer:
            self.selected_layer.output_bus = index
            self._invalidate_note_cache()
            self._sync_all()

    def _on_filter_type_changed(self, text: str):
        if self.selected_layer:
            self.selected_layer.filter_type = text.lower()
            self.filter_view.set_filter(self.selected_layer.filter_type, self.selected_layer.cutoff, self.selected_layer.resonance)
            self._invalidate_note_cache()

    def _on_cutoff_changed(self, val: int):
        if self.selected_layer:
            self.selected_layer.cutoff = float(val)
            self.filter_view.set_filter(self.selected_layer.filter_type, self.selected_layer.cutoff, self.selected_layer.resonance)
            self._invalidate_note_cache()

    def _on_res_changed(self, val: int):
        if self.selected_layer:
            self.selected_layer.resonance = val / 10.0
            self.filter_view.set_filter(self.selected_layer.filter_type, self.selected_layer.cutoff, self.selected_layer.resonance)
            self._invalidate_note_cache()

    def _on_filter_view_dragged(self, cutoff: float, res: float):
        """Mise à jour en temps réel bidirectionnelle lors du glisser-déplacer 2D sur le filtre."""
        if self.selected_layer:
            self.selected_layer.cutoff = cutoff
            self.selected_layer.resonance = res
            self.slider_cutoff.blockSignals(True)
            self.slider_cutoff.setValue(int(cutoff))
            self.slider_cutoff.blockSignals(False)

            self.slider_res.blockSignals(True)
            self.slider_res.setValue(int(res * 10))
            self.slider_res.blockSignals(False)
            self._invalidate_note_cache()

    def _on_adsr_changed(self):
        if self.selected_layer:
            self.selected_layer.attack = self.slider_att.value() / 1000.0
            self.selected_layer.decay = self.slider_dec.value() / 1000.0
            self.selected_layer.sustain = self.slider_sus.value() / 100.0
            self.selected_layer.release = self.slider_rel.value() / 1000.0
            self.adsr_view.set_adsr(
                self.selected_layer.attack,
                self.selected_layer.decay,
                self.selected_layer.sustain,
                self.selected_layer.release
            )
            self._invalidate_note_cache()

    def _on_adsr_view_dragged(self, a: float, d: float, s: float, r: float):
        """Mise à jour en temps réel bidirectionnelle lors du glisser-déplacer 2D sur l'enveloppe ADSR."""
        if self.selected_layer:
            self.selected_layer.attack = a
            self.selected_layer.decay = d
            self.selected_layer.sustain = s
            self.selected_layer.release = r

            self.slider_att.blockSignals(True)
            self.slider_att.setValue(int(a * 1000))
            self.slider_att.blockSignals(False)

            self.slider_dec.blockSignals(True)
            self.slider_dec.setValue(int(d * 1000))
            self.slider_dec.blockSignals(False)

            self.slider_sus.blockSignals(True)
            self.slider_sus.setValue(int(s * 100))
            self.slider_sus.blockSignals(False)

            self.slider_rel.blockSignals(True)
            self.slider_rel.setValue(int(r * 1000))
            self.slider_rel.blockSignals(False)

            self._invalidate_note_cache()

    def _populate_presets_combo(self):
        self.combo_presets.blockSignals(True)
        self.combo_presets.clear()
        try:
            from core.preset_manager import global_preset_manager
            presets = global_preset_manager.list_presets(self.plugin.plugin_type_id, self.plugin)
            for p in presets:
                prefix = "⚡ " if p.is_factory else "💾 "
                self.combo_presets.addItem(f"{prefix}{p.name}", p.name)
        except Exception:
            for pname in self.plugin.get_factory_presets().keys():
                self.combo_presets.addItem(f"⚡ {pname}", pname)

        cur_name = self.plugin.preset_name
        for i in range(self.combo_presets.count()):
            if self.combo_presets.itemData(i) == cur_name:
                self.combo_presets.setCurrentIndex(i)
                break
        self.combo_presets.blockSignals(False)

    def _save_current_preset(self):
        name, ok = QInputDialog.getText(self, "Enregistrer Preset", "Nom du preset synthétiseur :")
        if ok and name.strip():
            pname = name.strip()
            try:
                from core.preset_manager import global_preset_manager
                global_preset_manager.save_preset(self.plugin.plugin_type_id, pname, self.plugin.to_dict())
                self.plugin.preset_name = pname
                self._populate_presets_combo()
            except Exception as e:
                QMessageBox.warning(self, "Erreur", f"Impossible d'enregistrer le preset : {e}")

    def _on_preset_selected(self, index_or_name: Any):
        if isinstance(index_or_name, int):
            if index_or_name < 0:
                return
            pname = self.combo_presets.itemData(index_or_name)
            if not pname:
                pname = self.combo_presets.currentText()
        else:
            pname = str(index_or_name)

        if pname and (pname.startswith("⚡ ") or pname.startswith("💾 ")):
            pname = pname[2:]

        if not pname:
            return

        self.plugin.apply_preset(pname)
        if self.plugin.layers:
            self.selected_layer = self.plugin.layers[0]
        self._invalidate_note_cache()
        self._sync_all()

    def _on_delay_toggled(self, checked: bool):
        self.plugin.delay.enabled = checked
        self._invalidate_note_cache()

    def _on_delay_mix_changed(self, val: int):
        self.plugin.delay.mix = val / 100.0
        self._invalidate_note_cache()

    def _on_reverb_toggled(self, checked: bool):
        self.plugin.reverb.enabled = checked
        self._invalidate_note_cache()

    def _on_reverb_mix_changed(self, val: int):
        self.plugin.reverb.mix = val / 100.0
        self._invalidate_note_cache()

    def _on_master_vol_changed(self, val: int):
        self.plugin.master_volume = val / 100.0
        self._invalidate_note_cache()

    def _on_audition_note(self, pitch: int):
        """Déclenche immédiatement l'audio d'une note (réponse instantanée < 1ms via cache) et anime l'oscilloscope."""
        with self._cache_lock:
            wave = self._note_cache.get(pitch)

        if wave is None:
            wave = self.plugin.render_note(pitch, duration_sec=0.35, sample_rate=44100, velocity=105)
            with self._cache_lock:
                self._note_cache[pitch] = wave

        self.scope.set_audio_data(wave)

        # 1. Utiliser le moteur audio en direct du DAW si actif (latence ultra-faible < 5ms)
        from core.audio_engine import get_global_audio_engine
        engine = get_global_audio_engine()
        if engine and hasattr(engine, "play_preview_buffer"):
            track = engine.get_track_for_plugin(self.plugin) if hasattr(engine, "get_track_for_plugin") else None
            if engine.play_preview_buffer(wave, choke_group=0, track=track):
                return

        # 2. Repli sur le lecteur audio dédié
        LowLatencySynthAudition.get_instance().play(wave)
