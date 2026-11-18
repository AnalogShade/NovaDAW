"""
ui/timeline_view.py - Règle temporelle, grille d'arrangement, clips et tête de lecture
"""
from typing import Optional, Tuple, List, Set
import copy
import uuid
from PySide6.QtWidgets import (
    QWidget, QScrollArea, QVBoxLayout, QHBoxLayout, QFrame, QMenu, QInputDialog, QToolTip
)
from PySide6.QtGui import (
    QPainter, QColor, QPen, QBrush, QFont, QFontMetrics, QMouseEvent, QWheelEvent,
    QPolygonF, QCursor, QPixmap, QPainterPath
)
from PySide6.QtCore import Qt, Signal, QPointF, QRectF
from core.project import (
    Project, Track, MidiClip, AudioClip, MidiNote, ClipType,
    AutomationLane, AutomationPoint, format_automation_value
)
from ui.automation_lane_renderer import (
    paint_automation_lane, value_to_lane_y, lane_y_to_value,
    simplify_freehand_points, get_segment_midpoint_handle
)


def create_scissors_cursor() -> QCursor:
    """Génère un curseur de souris représentant une véritable paire de ciseaux professionnelle."""
    size = 32
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)

    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)

    # 1. Tracé de l'ombre / contour noir pour un contraste absolu sur tout type de bloc (clair ou sombre)
    path_blade1 = QPainterPath()
    path_blade1.moveTo(2, 2)
    path_blade1.lineTo(13, 13)
    path_blade1.lineTo(24, 24)

    path_h1 = QPainterPath()
    path_h1.addEllipse(QPointF(23, 20), 5.5, 4.5)

    path_h2 = QPainterPath()
    path_h2.addEllipse(QPointF(19, 24), 4.5, 5.5)

    path_blade2 = QPainterPath()
    path_blade2.moveTo(2, 11)
    path_blade2.lineTo(13, 13)
    path_blade2.lineTo(17, 16)

    p.setPen(QPen(QColor(0, 0, 0, 240), 3.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.drawPath(path_h1)
    p.drawPath(path_h2)
    p.drawPath(path_blade1)
    p.drawPath(path_blade2)

    # 2. Anneaux ergonomiques de la poignée en cyan Nova
    p.setPen(QPen(QColor("#38bdf8"), 1.8))
    p.drawEllipse(QPointF(23, 20), 4.5, 3.5)
    p.drawEllipse(QPointF(19, 24), 3.5, 4.5)

    # 3. Lames acérées argentées / blanches contrastées
    p.setPen(QPen(QColor("#ffffff"), 2.0, Qt.SolidLine, Qt.RoundCap))
    p.drawLine(QPointF(2, 2), QPointF(13, 13))
    p.drawLine(QPointF(2, 11), QPointF(13, 13))
    p.drawLine(QPointF(13, 13), QPointF(22, 22))

    # 4. Rivet central / vis de pivot en laiton doré
    p.setPen(QPen(QColor("#0f172a"), 1))
    p.setBrush(QBrush(QColor("#facc15")))
    p.drawEllipse(QPointF(13, 13), 2.2, 2.2)

    p.end()

    # Le point actif (hotspot) est à la pointe exacte de découpe de la lame supérieure : (2, 2)
    return QCursor(pix, 2, 2)



class TimelineRuler(QWidget):
    """Règle temporelle affichant les numéros de mesures et la région de boucle avec poignées d'ajustement interactives"""
    seek_requested = Signal(float)
    loop_changed = Signal(float, float)
    status_hint = Signal(str)
    zoom_in_requested = Signal()
    zoom_out_requested = Signal()

    HANDLE_MARGIN = 10  # Zone de détection en pixels pour les poignées gauche et droite

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(30)
        self.pixels_per_beat = 40.0
        self.total_beats = 128.0
        self.playhead_beat = 0.0
        self.loop_start_beat = 0.0
        self.loop_end_beat = 16.0
        self.loop_enabled = False  # Désactivé par défaut au démarrage
        self.scroll_offset = 0.0

        # Aimantage & Grille temporelle
        self.snap_enabled = True
        self.grid_resolution = 1.0  # en temps (1.0 = noire / quart de mesure)

        self._active_drag = None  # None, "start", "end", "new", "seek"
        self._drag_anchor_beat = 0.0
        self._hovered_handle = None  # None, "start", "end", "region"
        self.setMouseTracking(True)

    def wheelEvent(self, event: QWheelEvent):
        """Ctrl + Molette pour zoomer / dézoomer la règle et la timeline"""
        if event.modifiers() & Qt.ControlModifier:
            delta = event.angleDelta().y()
            if delta > 0:
                self.zoom_in_requested.emit()
            elif delta < 0:
                self.zoom_out_requested.emit()
            event.accept()
            return
        super().wheelEvent(event)

    def set_zoom(self, ppb: float):
        self.pixels_per_beat = ppb
        self.update()

    def set_playhead(self, beat: float):
        old_px = int(self.playhead_beat * self.pixels_per_beat)
        new_px = int(beat * self.pixels_per_beat)
        self.playhead_beat = beat
        if old_px == new_px:
            return
        h = self.height()
        min_x = min(old_px, new_px) - 8
        max_x = max(old_px, new_px) + 8
        if max_x - min_x < 60:
            self.update(min_x, 0, max_x - min_x + 1, h)
        else:
            self.update(old_px - 8, 0, 17, h)
            self.update(new_px - 8, 0, 17, h)

    def set_grid_resolution(self, res_beats: float):
        """Définit la résolution de la grille en temps musicaux"""
        self.grid_resolution = float(res_beats)
        self.update()

    def set_snap_enabled(self, enabled: bool):
        """Active ou désactive l'aimantage à la grille"""
        self.snap_enabled = bool(enabled)
        self.update()

    def snap_beat(self, beat: float) -> float:
        """Aligne un temps sur la grille active selon la résolution choisie si snap est actif"""
        if not self.snap_enabled or self.grid_resolution <= 0.0:
            return max(0.0, float(beat))
        step = float(self.grid_resolution)
        return max(0.0, round(beat / step) * step)

    def set_loop(self, enabled: bool, start_b: float, end_b: float):
        self.loop_enabled = enabled
        self.loop_start_beat = start_b
        self.loop_end_beat = end_b
        self.update()

    def set_scroll_offset(self, offset: float):
        if self.scroll_offset != float(offset):
            self.scroll_offset = float(offset)
            self.update()

    def _get_handle_at(self, x: float) -> Optional[str]:
        sx = self.loop_start_beat * self.pixels_per_beat - self.scroll_offset
        ex = self.loop_end_beat * self.pixels_per_beat - self.scroll_offset

        near_s = abs(x - sx) <= self.HANDLE_MARGIN
        near_e = abs(x - ex) <= self.HANDLE_MARGIN

        if near_s and near_e:
            return "start" if x < (sx + ex) / 2.0 else "end"
        if near_s:
            return "start"
        if near_e:
            return "end"
        if sx < x < ex:
            return "region"
        return None

    def leaveEvent(self, event):
        self._hovered_handle = None
        self.unsetCursor()
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            x = event.position().x()
            beat = max(0.0, (x + self.scroll_offset) / self.pixels_per_beat)

            # Shift + Clic pour redéfinir entièrement la zone de boucle
            if event.modifiers() & Qt.ShiftModifier:
                self._active_drag = "new"
                self._drag_anchor_beat = round(beat)
                self.loop_start_beat = self._drag_anchor_beat
                self.loop_end_beat = self._drag_anchor_beat + 4.0
                self.loop_enabled = True
                self.loop_changed.emit(self.loop_start_beat, self.loop_end_beat)
                self.update()
                return

            handle = self._get_handle_at(x)
            if handle == "start":
                self._active_drag = "start"
                self.loop_enabled = True
                self.update()
            elif handle == "end":
                self._active_drag = "end"
                self.loop_enabled = True
                self.update()
            else:
                self._active_drag = "seek"
                snapped_beat = self.snap_beat(beat)
                self.seek_requested.emit(snapped_beat)
                self.update()

    def mouseMoveEvent(self, event: QMouseEvent):
        x = event.position().x()
        beat = max(0.0, (x + self.scroll_offset) / self.pixels_per_beat)

        if self._active_drag == "start":
            step = 0.25 if (event.modifiers() & Qt.ShiftModifier) else (self.grid_resolution if (self.snap_enabled and self.grid_resolution > 0.0) else 1.0)
            new_start = round(beat / step) * step
            new_start = max(0.0, new_start)
            if new_start >= self.loop_end_beat:
                new_start = max(0.0, self.loop_end_beat - step)
            if new_start != self.loop_start_beat:
                self.loop_start_beat = new_start
                self.loop_changed.emit(self.loop_start_beat, self.loop_end_beat)
            self.update()
        elif self._active_drag == "end":
            step = 0.25 if (event.modifiers() & Qt.ShiftModifier) else (self.grid_resolution if (self.snap_enabled and self.grid_resolution > 0.0) else 1.0)
            new_end = round(beat / step) * step
            if new_end <= self.loop_start_beat:
                new_end = self.loop_start_beat + step
            if new_end != self.loop_end_beat:
                self.loop_end_beat = new_end
                self.loop_changed.emit(self.loop_start_beat, self.loop_end_beat)
            self.update()
        elif self._active_drag == "new":
            step = self.grid_resolution if (self.snap_enabled and self.grid_resolution > 0.0) else 1.0
            snapped = round(beat / step) * step
            s = min(self._drag_anchor_beat, snapped)
            e = max(self._drag_anchor_beat, snapped)
            if e <= s:
                e = s + step
            self.loop_start_beat = s
            self.loop_end_beat = e
            self.loop_changed.emit(s, e)
            self.update()
        elif self._active_drag == "seek" or (event.buttons() & Qt.LeftButton):
            snapped_beat = self.snap_beat(beat)
            self.seek_requested.emit(snapped_beat)
        else:
            old_handle = self._hovered_handle
            self._hovered_handle = self._get_handle_at(x)

            if self._hovered_handle in ("start", "end"):
                self.setCursor(Qt.SizeHorCursor)
                if self._hovered_handle == "start":
                    tip = (
                        f"◀ Début de la boucle (Temps {self.loop_start_beat:.1f})\n"
                        "↔ Glisser pour ajuster le début\n"
                        "💡 Maj + Clic-Glisser pour redéfinir la boucle\n"
                        "Touche 'L' pour activer/désactiver"
                    )
                    self.status_hint.emit("Boucle : Glisser pour ajuster le début | Maj + Clic-Glisser pour redéfinir")
                else:
                    tip = (
                        f"Fin de la boucle ▶ (Temps {self.loop_end_beat:.1f})\n"
                        "↔ Glisser pour ajuster la fin\n"
                        "💡 Maj + Clic-Glisser pour redéfinir la boucle\n"
                        "Touche 'L' pour activer/désactiver"
                    )
                    self.status_hint.emit("Boucle : Glisser pour ajuster la fin | Maj + Clic-Glisser pour redéfinir")
                self.setToolTip(tip)
            else:
                self.unsetCursor()
                if self._hovered_handle == "region":
                    self.setToolTip(
                        f"Région de boucle : {self.loop_start_beat:.1f} → {self.loop_end_beat:.1f} ({self.loop_end_beat - self.loop_start_beat:.1f} temps)\n"
                        "💡 Maj + Clic-Glisser pour redéfinir la boucle\n"
                        "Touche 'L' pour activer/désactiver"
                    )
                else:
                    self.setToolTip("Règle temporelle : Clic pour déplacer la tête de lecture\n• Maj + Clic-Glisser pour définir une boucle")

            if old_handle != self._hovered_handle:
                self.update()

    def mouseReleaseEvent(self, event: QMouseEvent):
        if self._active_drag in ("start", "end", "new"):
            self.loop_changed.emit(self.loop_start_beat, self.loop_end_beat)
        self._active_drag = None
        x = event.position().x()
        self._hovered_handle = self._get_handle_at(x)
        if self._hovered_handle in ("start", "end"):
            self.setCursor(Qt.SizeHorCursor)
        else:
            self.unsetCursor()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        width = self.width()
        height = self.height()

        # Fond
        painter.fillRect(0, 0, width, height, QColor("#14151c"))

        # Positions à l'écran de la boucle
        x_start = self.loop_start_beat * self.pixels_per_beat - self.scroll_offset
        x_end = self.loop_end_beat * self.pixels_per_beat - self.scroll_offset

        # 1. Graduation des mesures (4 temps par mesure en 4/4)
        first_bar = max(0, int(self.scroll_offset / (self.pixels_per_beat * 4)))
        num_bars = int(width / (self.pixels_per_beat * 4)) + 4
        font = QFont("Consolas", 9, QFont.Bold)
        painter.setFont(font)

        for bar in range(first_bar, first_bar + num_bars):
            bar_beat = bar * 4.0
            x = bar_beat * self.pixels_per_beat - self.scroll_offset
            if x < -50 or x > width + 50:
                continue

            # Ligne de mesure principale
            painter.setPen(QPen(QColor("#475069"), 1))
            painter.drawLine(int(x), 0, int(x), height)

            # Numéro de mesure
            painter.setPen(QColor("#94a3b8"))
            painter.drawText(int(x) + 5, 20, str(bar + 1))

            # Graduations de temps
            painter.setPen(QPen(QColor("#252834"), 1))
            for beat in range(1, 4):
                bx = (bar_beat + beat) * self.pixels_per_beat - self.scroll_offset
                if 0 <= bx <= width:
                    painter.drawLine(int(bx), 16, int(bx), height)

            # Subdivisions fines dans la règle si zoom suffisant et snap actif
            step = self.grid_resolution if (self.snap_enabled and self.grid_resolution > 0.0) else 0.0
            if 0.0 < step < 1.0 and (step * self.pixels_per_beat) >= 6.0:
                painter.setPen(QPen(QColor("#1b1e2a"), 1))
                num_sub = int(round(4.0 / step))
                for s_idx in range(1, num_sub):
                    cur_b = bar_beat + s_idx * step
                    if abs(cur_b - round(cur_b)) > 1e-4:
                        sub_x = cur_b * self.pixels_per_beat - self.scroll_offset
                        if 0 <= sub_x <= width:
                            painter.drawLine(int(sub_x), 22, int(sub_x), height)

        # 2. Zone de boucle
        is_loop_on = self.loop_enabled
        loop_color = QColor(245, 158, 11) if is_loop_on else QColor(148, 163, 184)
        bg_color = QColor(245, 158, 11, 40) if is_loop_on else QColor(148, 163, 184, 15)

        loop_rect = QRectF(x_start, 0, max(2.0, x_end - x_start), height)
        painter.fillRect(loop_rect, bg_color)

        # Barre supérieure de boucle
        top_pen = QPen(loop_color, 2 if is_loop_on else 1.5)
        if not is_loop_on:
            top_pen.setStyle(Qt.DashLine)
        painter.setPen(top_pen)
        painter.drawLine(int(x_start), 2, int(x_end), 2)

        # 3. Poignées d'ajustement gauche et droite (Loop Handles)
        # Poignée gauche (Début)
        is_s_hover = (self._hovered_handle == "start" or self._active_drag == "start")
        handle_s_color = QColor("#fbbf24") if (is_s_hover or is_loop_on) else QColor("#94a3b8")

        # Trait vertical
        painter.setPen(QPen(handle_s_color, 2 if is_s_hover else 1.5))
        painter.drawLine(int(x_start), 0, int(x_start), height)

        # Onglet poignée gauche
        tab_s_rect = QRectF(x_start, 1, 18, 16)
        tab_s_bg = QColor(245, 158, 11, 220) if is_s_hover else (QColor(245, 158, 11, 140) if is_loop_on else QColor(51, 65, 85, 160))
        painter.fillRect(tab_s_rect, tab_s_bg)
        painter.setPen(QPen(handle_s_color, 1))
        painter.drawRect(tab_s_rect)

        # Texte / Icône poignée gauche
        painter.setFont(QFont("Segoe UI", 7, QFont.Bold))
        painter.setPen(QColor("#ffffff") if is_s_hover else handle_s_color)
        painter.drawText(tab_s_rect, Qt.AlignCenter, "◀L")

        # Poignée droite (Fin)
        is_e_hover = (self._hovered_handle == "end" or self._active_drag == "end")
        handle_e_color = QColor("#fbbf24") if (is_e_hover or is_loop_on) else QColor("#94a3b8")

        # Trait vertical
        painter.setPen(QPen(handle_e_color, 2 if is_e_hover else 1.5))
        painter.drawLine(int(x_end), 0, int(x_end), height)

        # Onglet poignée droite
        tab_e_rect = QRectF(x_end - 18, 1, 18, 16)
        tab_e_bg = QColor(245, 158, 11, 220) if is_e_hover else (QColor(245, 158, 11, 140) if is_loop_on else QColor(51, 65, 85, 160))
        painter.fillRect(tab_e_rect, tab_e_bg)
        painter.setPen(QPen(handle_e_color, 1))
        painter.drawRect(tab_e_rect)

        # Texte / Icône poignée droite
        painter.setFont(QFont("Segoe UI", 7, QFont.Bold))
        painter.setPen(QColor("#ffffff") if is_e_hover else handle_e_color)
        painter.drawText(tab_e_rect, Qt.AlignCenter, "R▶")

        # 4. Petit label interactif d'aide survol (HUD hint badge)
        if (self._hovered_handle in ("start", "end") or self._active_drag in ("start", "end")) and width > 300:
            if self._hovered_handle == "start" or self._active_drag == "start":
                hud_text = f"↔ Début ({self.loop_start_beat:.1f}) | Maj+Clic pour redéfinir"
                ideal_x = x_start + 22
            else:
                hud_text = f"↔ Fin ({self.loop_end_beat:.1f}) | Maj+Clic pour redéfinir"
                ideal_x = x_end - 220

            font_hud = QFont("Segoe UI", 8, QFont.Bold)
            fm = QFontMetrics(font_hud)
            tw = fm.horizontalAdvance(hud_text)
            hud_w = tw + 14
            hud_h = 19
            hud_x = max(6.0, min(float(width - hud_w - 6), ideal_x))
            hud_y = 5.0

            hud_rect = QRectF(hud_x, hud_y, hud_w, hud_h)
            painter.fillRect(hud_rect, QColor(15, 23, 42, 235))
            painter.setPen(QPen(QColor("#f59e0b" if is_loop_on else "#94a3b8"), 1))
            painter.drawRoundedRect(hud_rect, 4, 4)

            painter.setFont(font_hud)
            painter.setPen(QColor("#fef08a" if is_loop_on else "#e2e8f0"))
            painter.drawText(hud_rect, Qt.AlignCenter, hud_text)

        # Ligne de séparation inférieure
        painter.setPen(QPen(QColor("#282a36"), 1))
        painter.drawLine(0, height - 1, width, height - 1)

        # 5. Tête de lecture (Triangle cyan)
        px = self.playhead_beat * self.pixels_per_beat - self.scroll_offset
        if -10 <= px <= width + 10:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(QColor("#38bdf8")))
            tri = QPolygonF([
                QPointF(px - 6, 0),
                QPointF(px + 6, 0),
                QPointF(px, 12)
            ])
            painter.drawPolygon(tri)


class TimelineGrid(QWidget):
    """Canvas principal de la timeline contenant les pistes, clips et la tête de lecture"""
    clip_selected = Signal(Track, object)       # (track, clip)
    clip_double_clicked = Signal(Track, object)  # (track, clip)
    project_modified = Signal()
    seek_requested = Signal(float)
    time_range_selected = Signal(float, float)
    track_height_changed = Signal(str, int, bool)  # (track_id, height, apply_all)
    track_selected = Signal(str)    # track_id
    track_deleted = Signal(str)     # track_id
    track_moved = Signal(str, int)  # (track_id, delta)
    track_duplicated = Signal(str)  # track_id
    automation_lane_height_changed = Signal(str, str, int)  # (track_id, lane_id, height)
    automation_modified = Signal()
    status_message = Signal(str, int)  # (message, duration_ms)
    zoom_in_requested = Signal()
    zoom_out_requested = Signal()

    DEFAULT_TRACK_HEIGHT = 76
    RESIZE_MARGIN = 5

    def __init__(self, project: Project, parent=None):
        super().__init__(parent)
        self.project = project
        self.pixels_per_beat = 40.0
        self.playhead_beat = 0.0
        self.selected_track_id: Optional[str] = None
        self._last_track_context_menu: Optional[QMenu] = None

        # Outils d'édition & Grille
        self.active_tool: str = "select"  # "select", "split", "erase", "draw"
        self.snap_enabled: bool = True
        self.grid_resolution: float = 1.0  # en temps (1.0 = noire / quart de mesure)
        self._clip_clipboard: Optional[ClipType] = None

        # État d'édition des pistes d'automatisation
        self.selected_automation_points: Set[str] = set()
        self._hover_auto_point: Optional[Tuple[str, str]] = None      # (lane_id, point_id)
        self._hover_auto_segment: Optional[Tuple[str, int]] = None    # (lane_id, seg_idx)
        self._dragging_auto_point: Optional[Tuple[Track, AutomationLane, AutomationPoint, float, float]] = None
        self._dragging_auto_curve: Optional[Tuple[Track, AutomationLane, AutomationPoint, float, float, float]] = None
        self._drawing_auto_freehand: Optional[Tuple[Track, AutomationLane, float, float]] = None
        self._draw_auto_raw_points: List[Tuple[float, float]] = []
        self._erasing_auto: bool = False
        self._resizing_lane: Optional[Tuple[Track, AutomationLane]] = None
        self._drag_start_lane_h = 56
        self._drag_start_lane_y = 0

        # Guide visuel pour découpe (ciseaux)
        self._hover_split_beat: Optional[float] = None
        self._hover_clip: Optional[Tuple[Track, ClipType]] = None

        # Permet de recevoir les touches du clavier (Suppr, Ctrl+D, etc.)
        self.setFocusPolicy(Qt.StrongFocus)

        # État de sélection de bloc et zone temporelle
        self.selected_clip: Optional[Tuple[Track, ClipType]] = None
        self.time_selection: Optional[Tuple[float, float]] = None

        self._dragging_clip = False
        self._resizing_clip = False
        self._is_selecting_range = False
        self._range_anchor_beat = 0.0
        self._drag_start_x = 0
        self._drag_start_beat = 0.0
        self._drag_start_len = 0.0

        # Redimensionnement de piste à la souris
        self._resizing_track_height = False
        self._resizing_track: Optional[Track] = None
        self._drag_start_track_h = 76
        self._drag_start_track_y = 0

        self._scissors_cursor: Optional[QCursor] = None

        self.setMouseTracking(True)
        self.update_dimensions()

    def set_selected_track_id(self, track_id: Optional[str]):
        if self.selected_track_id != track_id:
            self.selected_track_id = track_id
            self.update()

    def get_scissors_cursor(self) -> QCursor:
        """Retourne le curseur personnalisé en forme de ciseaux (mis en cache)."""
        if self._scissors_cursor is None:
            self._scissors_cursor = create_scissors_cursor()
        return self._scissors_cursor

    def enterEvent(self, event):
        if self.active_tool == "split":
            self.setCursor(self.get_scissors_cursor())
        elif self.active_tool == "erase":
            self.setCursor(Qt.PointingHandCursor)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover_split_beat = None
        self._hover_clip = None
        self.update()
        super().leaveEvent(event)

    def snap_beat(self, beat: float) -> float:
        """Aligne un temps sur la grille active selon la résolution choisie si snap est actif"""
        if not self.snap_enabled or self.grid_resolution <= 0.0:
            return max(0.0, float(beat))
        step = float(self.grid_resolution)
        return max(0.0, round(beat / step) * step)

    def set_active_tool(self, tool_name: str):
        """Active l'outil demandé ('select', 'split', 'erase', 'draw')"""
        self.active_tool = tool_name
        self._hover_split_beat = None
        self._hover_clip = None
        if tool_name == "split":
            self.setCursor(self.get_scissors_cursor())
            self.status_message.emit("✂️ Outil Ciseaux actif : Cliquez sur un clip pour le scinder à l'endroit précis (Raccourci: 2 ou C)", 3500)
        elif tool_name == "erase":
            self.setCursor(Qt.PointingHandCursor)
            self.status_message.emit("🗑️ Outil Gomme actif : Cliquez sur un clip ou un point d'automatisation pour le supprimer (Raccourci: 3)", 3500)
        elif tool_name == "draw":
            self.setCursor(Qt.CrossCursor)
            self.status_message.emit("✏️ Outil Crayon actif : Dessinez librement dans une piste d'automatisation (Raccourci: 4 ou B)", 3500)
        else:
            self.unsetCursor()
            self.status_message.emit("🖱️ Outil Pointeur actif : Sélectionner, déplacer et redimensionner des blocs ou points (Raccourci: 1 ou V)", 2500)
        self.update()

    def set_grid_resolution(self, res_beats: float):
        """Définit la résolution de la grille en temps musicaux"""
        self.grid_resolution = float(res_beats)
        self.update()

    def set_snap_enabled(self, enabled: bool):
        """Active ou désactive l'aimantage à la grille"""
        self.snap_enabled = bool(enabled)
        self.update()

    def copy_selected_clip(self) -> bool:
        """Copie le bloc sélectionné dans le presse-papier interne"""
        if self.selected_clip:
            track, clip = self.selected_clip
            self._clip_clipboard = copy.deepcopy(clip)
            self.status_message.emit(f"Bloc '{clip.name}' copié dans le presse-papier", 3000)
            return True
        self.status_message.emit("Aucun bloc sélectionné à copier", 2500)
        return False

    def cut_selected_clip(self) -> bool:
        """Coupe le bloc sélectionné (copie et supprime de la piste)"""
        if self.selected_clip:
            track, clip = self.selected_clip
            self._clip_clipboard = copy.deepcopy(clip)
            track.clips = [c for c in track.clips if c.id != clip.id]
            self.selected_clip = None
            self.project_modified.emit()
            self.update()
            self.status_message.emit(f"Bloc '{clip.name}' coupé", 3000)
            return True
        self.status_message.emit("Aucun bloc sélectionné à couper", 2500)
        return False

    def paste_clip_at_playhead(self, target_track: Optional[Track] = None) -> Optional[ClipType]:
        """Colle le bloc copié à la position actuelle de la tête de lecture sur la piste cible"""
        if not self._clip_clipboard:
            self.status_message.emit("Presse-papier vide. Utilisez Copier d'abord.", 3000)
            return None

        track = target_track
        if not track and self.selected_clip:
            track = self.selected_clip[0]
        if not track and hasattr(self.parent(), "selected_track_id"):
            track = self.project.get_track(self.parent().selected_track_id)
        if not track and self.project.tracks:
            track = self.project.tracks[0]

        if not track:
            self.status_message.emit("Aucune piste disponible pour coller le bloc", 3000)
            return None

        paste_beat = self.snap_beat(self.playhead_beat)
        new_clip = copy.deepcopy(self._clip_clipboard)
        new_clip.id = str(uuid.uuid4())[:8]
        new_clip.start_beat = paste_beat
        new_clip.color = track.color or new_clip.color

        track.clips.append(new_clip)
        self.selected_clip = (track, new_clip)
        self.project_modified.emit()
        self.update()
        self.clip_selected.emit(track, new_clip)
        self.status_message.emit(f"Bloc '{new_clip.name}' collé à {paste_beat:.2f} temps sur {track.name}", 3500)
        return new_clip

    def duplicate_selected_clip(self) -> Optional[ClipType]:
        """Duplique le bloc sélectionné immédiatement à la fin de celui-ci"""
        if not self.selected_clip:
            self.status_message.emit("Aucun bloc sélectionné à dupliquer", 2500)
            return None
        track, clip = self.selected_clip
        new_clip = copy.deepcopy(clip)
        new_clip.id = str(uuid.uuid4())[:8]
        new_clip.name = f"{clip.name} (Copie)"
        new_clip.start_beat = self.snap_beat(clip.start_beat + clip.length_beats)
        track.clips.append(new_clip)
        self.selected_clip = (track, new_clip)
        self.project_modified.emit()
        self.update()
        self.clip_selected.emit(track, new_clip)
        self.status_message.emit(f"Bloc dupliqué : {new_clip.name}", 3000)
        return new_clip

    def delete_selected_clip(self) -> bool:
        """Supprime le bloc actuellement sélectionné"""
        if self.selected_clip:
            track, clip = self.selected_clip
            track.clips = [c for c in track.clips if c.id != clip.id]
            self.selected_clip = None
            self.project_modified.emit()
            self.update()
            self.status_message.emit(f"Bloc '{clip.name}' supprimé", 2500)
            return True
        return False

    def split_clip_at(self, track: Track, clip: ClipType, split_beat: float) -> Optional[Tuple[ClipType, ClipType]]:
        """Scinde un bloc à une position temporelle absolue en deux clips indépendants"""
        if not (clip.start_beat < split_beat < clip.start_beat + clip.length_beats):
            return None

        if isinstance(clip, AudioClip):
            p1, p2 = clip.split(split_beat, bpm=self.project.bpm)
        elif isinstance(clip, MidiClip):
            p1, p2 = clip.split(split_beat)
        else:
            return None

        try:
            idx = track.clips.index(clip)
            track.clips[idx] = p1
            track.clips.insert(idx + 1, p2)
        except ValueError:
            track.clips = [c for c in track.clips if c.id != clip.id] + [p1, p2]

        self.selected_clip = (track, p2)
        self.clip_selected.emit(track, p2)
        self.project_modified.emit()
        self.update()
        self.status_message.emit(f"Bloc scindé à {split_beat:.2f} temps en deux parties", 3500)
        return p1, p2

    def split_at_playhead(self) -> bool:
        """Scinde le bloc sélectionné (ou le bloc sous la tête de lecture) à la position de la tête"""
        target_beat = self.snap_beat(self.playhead_beat) if self.snap_enabled else self.playhead_beat
        
        # 1. Vérifier si le bloc sélectionné contient target_beat
        if self.selected_clip:
            track, clip = self.selected_clip
            if clip.start_beat < target_beat < clip.start_beat + clip.length_beats:
                res = self.split_clip_at(track, clip, target_beat)
                return res is not None

        # 2. Sinon, chercher sur la piste sélectionnée
        active_track = None
        if hasattr(self.parent(), "selected_track_id"):
            active_track = self.project.get_track(self.parent().selected_track_id)
        if not active_track and self.project.tracks:
            active_track = self.project.tracks[0]

        if active_track:
            for clip in active_track.clips:
                if clip.start_beat < target_beat < clip.start_beat + clip.length_beats:
                    res = self.split_clip_at(active_track, clip, target_beat)
                    return res is not None

        self.status_message.emit("Aucun bloc sous la tête de lecture à scinder", 2500)
        return False

    def set_zoom(self, ppb: float):
        self.pixels_per_beat = ppb
        self.update_dimensions()
        self.update()

    def get_total_project_beats(self) -> float:
        """Calcule la durée totale en temps (beats) du projet avec marge de fin dynamique"""
        max_beat = 128.0  # Au minimum 32 mesures
        if hasattr(self.project, "loop_end_beat") and self.project.loop_end_beat:
            max_beat = max(max_beat, float(self.project.loop_end_beat))
        if hasattr(self, "playhead_beat") and self.playhead_beat:
            max_beat = max(max_beat, float(self.playhead_beat))
        for track in self.project.tracks:
            for clip in track.clips:
                clip_end = float(clip.start_beat) + float(clip.length_beats)
                if clip_end > max_beat:
                    max_beat = clip_end
        # Marge de fin d'au moins 16 mesures (64 temps) pour respirer et éditer librement
        return max_beat + 64.0

    def wheelEvent(self, event: QWheelEvent):
        """Ctrl + Molette pour zoomer / dézoomer la timeline"""
        if event.modifiers() & Qt.ControlModifier:
            delta = event.angleDelta().y()
            if delta > 0:
                self.zoom_in_requested.emit()
            elif delta < 0:
                self.zoom_out_requested.emit()
            event.accept()
            return
        super().wheelEvent(event)

    def set_playhead(self, beat: float):
        old_px = int(self.playhead_beat * self.pixels_per_beat)
        new_px = int(beat * self.pixels_per_beat)
        self.playhead_beat = beat
        if (new_px + 200) > self.width():
            self.update_dimensions()
        if old_px == new_px:
            return
        h = self.height()
        min_x = min(old_px, new_px) - 4
        max_x = max(old_px, new_px) + 4
        if max_x - min_x < 60:
            self.update(min_x, 0, max_x - min_x + 1, h)
        else:
            self.update(old_px - 4, 0, 9, h)
            self.update(new_px - 4, 0, 9, h)

    def get_track_layout(self) -> list:
        """Retourne la liste des tuples (track, top_y, height) pour chaque piste principale."""
        layout = []
        curr_y = 0
        for track in self.project.tracks:
            h = getattr(track, "height", self.DEFAULT_TRACK_HEIGHT)
            layout.append((track, curr_y, h))
            curr_y += h
            if getattr(track, "automation_expanded", True):
                for lane in getattr(track, "automation_lanes", []):
                    if getattr(lane, "visible", True):
                        curr_y += max(40, int(getattr(lane, "height", 56)))
        return layout

    def get_automation_layout(self) -> list:
        """Retourne la liste des tuples (track, lane, top_y, height) pour chaque sous-piste d'automatisation visible."""
        lanes_layout = []
        curr_y = 0
        for track in self.project.tracks:
            h = getattr(track, "height", self.DEFAULT_TRACK_HEIGHT)
            curr_y += h
            if getattr(track, "automation_expanded", True):
                for lane in getattr(track, "automation_lanes", []):
                    if getattr(lane, "visible", True):
                        lh = max(40, int(getattr(lane, "height", 56)))
                        lanes_layout.append((track, lane, curr_y, lh))
                        curr_y += lh
        return lanes_layout

    def update_dimensions(self):
        total_tracks_h = 0
        for t in self.project.tracks:
            total_tracks_h += getattr(t, "height", self.DEFAULT_TRACK_HEIGHT)
            if getattr(t, "automation_expanded", True):
                for lane in getattr(t, "automation_lanes", []):
                    if getattr(lane, "visible", True):
                        total_tracks_h += max(40, int(getattr(lane, "height", 56)))
        h = max(400, total_tracks_h)
        total_beats = self.get_total_project_beats()
        w = max(2400, int(total_beats * self.pixels_per_beat))
        self.setFixedSize(w, h)

    def _get_automation_lane_at_y(self, y: float) -> Tuple[Optional[Track], Optional[AutomationLane], float, float]:
        for track, lane, top_y, lh in self.get_automation_layout():
            if top_y <= y < top_y + lh:
                return track, lane, float(top_y), float(lh)
        return None, None, 0.0, 0.0

    def _get_lane_separator_at_y(self, y: float) -> Tuple[Optional[Track], Optional[AutomationLane]]:
        for track, lane, top_y, lh in self.get_automation_layout():
            if abs(y - (top_y + lh)) <= self.RESIZE_MARGIN:
                return track, lane
        return None, None

    def _get_auto_point_at(
        self, lane: AutomationLane, x: float, y: float, top_y: float, lh: float, radius: float = 8.0
    ) -> Optional[AutomationPoint]:
        best_pt = None
        best_d = radius + 1.0
        for pt in lane.points:
            px = pt.beat * self.pixels_per_beat
            py = value_to_lane_y(lane, pt.value, top_y, lh)
            d = ((x - px) ** 2 + (y - py) ** 2) ** 0.5
            if d <= radius and d < best_d:
                best_d = d
                best_pt = pt
        return best_pt

    def _get_auto_segment_handle_at(
        self, lane: AutomationLane, x: float, y: float, top_y: float, lh: float, radius: float = 8.0
    ) -> Optional[int]:
        for idx in range(len(lane.points) - 1):
            handle = get_segment_midpoint_handle(lane, idx, top_y, lh, self.pixels_per_beat)
            if handle is not None:
                hx, hy = handle
                if ((x - hx) ** 2 + (y - hy) ** 2) ** 0.5 <= radius:
                    return idx
        return None

    def _get_auto_segment_at(self, lane: AutomationLane, beat: float) -> Optional[int]:
        for idx in range(len(lane.points) - 1):
            if lane.points[idx].beat <= beat <= lane.points[idx + 1].beat:
                return idx
        return None

    def _ensure_lane_anchor(self, track: Track, lane: AutomationLane) -> None:
        """Si la courbe est encore vide, pose un point initial à beat=0 avec la valeur actuelle de la piste."""
        if lane.points:
            return
        if lane.target_param == "volume":
            init_val = float(track.volume)
        elif lane.target_param == "pan":
            init_val = float(track.pan)
        else:
            init_val = lane.evaluate(0.0)
        lane.add_point(0.0, init_val, curve_type="linear")

    def delete_selected_automation_points(self) -> bool:
        if not self.selected_automation_points:
            return False
        removed = False
        for track in self.project.tracks:
            for lane in getattr(track, "automation_lanes", []):
                before = len(lane.points)
                lane.points = [p for p in lane.points if p.id not in self.selected_automation_points]
                if len(lane.points) < before:
                    removed = True
        self.selected_automation_points.clear()
        if removed:
            self.project_modified.emit()
            self.automation_modified.emit()
            self.update()
        return removed

    def _get_track_at_y(self, y: int) -> Tuple[Optional[Track], int]:
        for index, (track, top_y, h) in enumerate(self.get_track_layout()):
            if top_y <= y < top_y + h:
                return track, index
        return None, -1

    def _get_track_separator_at_y(self, y: int) -> Tuple[Optional[Track], int]:
        """Détecte si la souris survole la ligne de séparation inférieure d'une piste"""
        for index, (track, top_y, h) in enumerate(self.get_track_layout()):
            bottom_y = top_y + h
            if abs(y - bottom_y) <= self.RESIZE_MARGIN:
                return track, index
        return None, -1

    def _get_clip_at(self, x: int, y: int) -> Tuple[Optional[Track], Optional[ClipType], bool]:
        """Retourne (track, clip, is_resize_handle)"""
        track, index = self._get_track_at_y(y)
        if not track:
            return None, None, False

        beat = x / self.pixels_per_beat
        for clip in track.clips:
            clip_end = clip.start_beat + clip.length_beats
            if clip.start_beat <= beat <= clip_end:
                # Bord droit pour le redimensionnement (8 pixels)
                clip_pixel_end = clip_end * self.pixels_per_beat
                is_resize = abs(x - clip_pixel_end) <= 8
                return track, clip, is_resize
        return track, None, False

    def mousePressEvent(self, event: QMouseEvent):
        self.setFocus()
        x = event.position().x()
        y = event.position().y()

        # 0. Vérifier si l'événement se situe dans une sous-piste d'automatisation
        auto_track, auto_lane, lane_top, lane_h = self._get_automation_lane_at_y(y)
        if auto_track and auto_lane:
            self.selected_track_id = auto_track.id
            self.track_selected.emit(auto_track.id)

            if event.button() == Qt.LeftButton:
                # Bord inférieur de la sous-piste : redimensionner sa hauteur
                sep_t, sep_l = self._get_lane_separator_at_y(y)
                if sep_t and sep_l:
                    self._resizing_lane = (sep_t, sep_l)
                    self._drag_start_lane_h = sep_l.height
                    self._drag_start_lane_y = event.globalPosition().y()
                    self.grabMouse()
                    event.accept()
                    return

                raw_beat = max(0.0, x / self.pixels_per_beat)
                use_snap = self.snap_enabled and self.grid_resolution > 0.0 and not (event.modifiers() & Qt.AltModifier)
                beat = self.snap_beat(raw_beat) if use_snap else raw_beat
                val = lane_y_to_value(auto_lane, y, lane_top, lane_h)

                # Outil Crayon : démarrage du dessin à main levée
                if self.active_tool == "draw":
                    self._ensure_lane_anchor(auto_track, auto_lane)
                    self._drawing_auto_freehand = (auto_track, auto_lane, lane_top, lane_h)
                    self._draw_auto_raw_points = [(raw_beat, val)]
                    QToolTip.showText(
                        event.globalPosition().toPoint(),
                        f"✏️ {auto_lane.name}: {format_automation_value(auto_lane.target_param, val)}",
                        self,
                    )
                    self.update()
                    event.accept()
                    return

                # Outil Gomme : supprimer le point cliqué
                if self.active_tool == "erase":
                    self._erasing_auto = True
                    hit_pt = self._get_auto_point_at(auto_lane, x, y, lane_top, lane_h)
                    if hit_pt:
                        auto_lane.remove_point(hit_pt.id)
                        self.selected_automation_points.discard(hit_pt.id)
                        self.project_modified.emit()
                        self.automation_modified.emit()
                        self.update()
                    event.accept()
                    return

                # Outil Ciseaux : insérer un point d'ancrage / coupure nette
                if self.active_tool == "split":
                    self._ensure_lane_anchor(auto_track, auto_lane)
                    cur_v = auto_lane.evaluate(beat, fallback_value=val)
                    pt = auto_lane.add_point(beat, cur_v, curve_type="step")
                    self.selected_automation_points = {pt.id}
                    self.project_modified.emit()
                    self.automation_modified.emit()
                    self.update()
                    self.status_message.emit(
                        f"✂️ Point d'ancrage posé à {beat:.2f} t ({format_automation_value(auto_lane.target_param, cur_v)})",
                        2500,
                    )
                    event.accept()
                    return

                # Outil Pointeur : poignée de courbure ou point existant ou création
                seg_handle_idx = self._get_auto_segment_handle_at(auto_lane, x, y, lane_top, lane_h)
                if seg_handle_idx is not None or ((event.modifiers() & Qt.AltModifier) and self._get_auto_segment_at(auto_lane, raw_beat) is not None):
                    seg_idx = seg_handle_idx if seg_handle_idx is not None else self._get_auto_segment_at(auto_lane, raw_beat)
                    if seg_idx is not None and 0 <= seg_idx < len(auto_lane.points) - 1:
                        p0 = auto_lane.points[seg_idx]
                        p0.curve_type = "curve"
                        self._dragging_auto_curve = (auto_track, auto_lane, p0, y, p0.curve_tension, lane_h)
                        self.update()
                        event.accept()
                        return

                hit_pt = self._get_auto_point_at(auto_lane, x, y, lane_top, lane_h)
                if hit_pt:
                    if event.modifiers() & Qt.ControlModifier:
                        if hit_pt.id in self.selected_automation_points:
                            self.selected_automation_points.discard(hit_pt.id)
                        else:
                            self.selected_automation_points.add(hit_pt.id)
                    else:
                        if hit_pt.id not in self.selected_automation_points:
                            self.selected_automation_points = {hit_pt.id}
                    self._dragging_auto_point = (auto_track, auto_lane, hit_pt, lane_top, lane_h)
                    QToolTip.showText(
                        event.globalPosition().toPoint(),
                        f"{auto_lane.name}: {format_automation_value(auto_lane.target_param, hit_pt.value)} @ {hit_pt.beat:.2f} t",
                        self,
                    )
                    self.update()
                    event.accept()
                    return

                # Clic dans la zone vide de la sous-piste : ajoute un point directement déplaçable
                self._ensure_lane_anchor(auto_track, auto_lane)
                new_pt = auto_lane.add_point(beat, val, curve_type="linear")
                self.selected_automation_points = {new_pt.id}
                self._dragging_auto_point = (auto_track, auto_lane, new_pt, lane_top, lane_h)
                self.project_modified.emit()
                self.automation_modified.emit()
                QToolTip.showText(
                    event.globalPosition().toPoint(),
                    f"{auto_lane.name}: {format_automation_value(auto_lane.target_param, new_pt.value)} @ {new_pt.beat:.2f} t",
                    self,
                )
                self.update()
                event.accept()
                return

            elif event.button() == Qt.RightButton:
                self._show_automation_context_menu(
                    event.globalPosition().toPoint(), auto_track, auto_lane, x, y, lane_top, lane_h
                )
                event.accept()
                return

        # 1. Vérifier si on clique sur la ligne de séparation d'une piste pour redimensionner sa hauteur
        sep_track, sep_idx = self._get_track_separator_at_y(y)
        if event.button() == Qt.LeftButton and sep_track:
            track, clip, is_resize = self._get_clip_at(x, y)
            if not clip or is_resize:
                self._resizing_track_height = True
                self._resizing_track = sep_track
                self._drag_start_track_h = getattr(sep_track, "height", self.DEFAULT_TRACK_HEIGHT)
                self._drag_start_track_y = event.globalPosition().y()
                self.grabMouse()
                event.accept()
                return

        track, clip, is_resize = self._get_clip_at(x, y)

        if event.button() == Qt.LeftButton:
            # 2. Outil Ciseaux / Découpe (Split Tool)
            if self.active_tool == "split":
                if clip and track:
                    raw_beat = x / self.pixels_per_beat
                    split_beat = self.snap_beat(raw_beat) if (self.snap_enabled and self.grid_resolution > 0) else raw_beat
                    # Vérifier que le point de coupe est à l'intérieur du bloc
                    target_split = None
                    if clip.start_beat + 0.02 < split_beat < clip.start_beat + clip.length_beats - 0.02:
                        target_split = split_beat
                    elif clip.start_beat + 0.02 < raw_beat < clip.start_beat + clip.length_beats - 0.02:
                        target_split = raw_beat

                    if target_split is not None:
                        p1, p2 = self.split_clip_at(track, clip, target_split)
                        self._hover_split_beat = target_split
                        self._hover_clip = (track, p2)
                        self.status_message.emit(f"✂️ Bloc '{clip.name}' scindé à {target_split:.2f} temps en deux parties", 3500)
                        self.update()
                    else:
                        self.status_message.emit("Point de coupe trop proche des extrémités du bloc", 2000)
                return

            # 3. Outil Gomme (Erase Tool)
            if self.active_tool == "erase":
                if clip and track:
                    track.clips = [c for c in track.clips if c.id != clip.id]
                    if self.selected_clip and self.selected_clip[1].id == clip.id:
                        self.selected_clip = None
                    self.project_modified.emit()
                    self.update()
                    self.status_message.emit(f"Bloc '{clip.name}' supprimé", 2500)
                return

            # 4. Outil Pointeur normal (Sélection, déplacement, redimensionnement)
            if clip and track:
                self.selected_clip = (track, clip)
                self.selected_track_id = track.id
                self.time_selection = None
                self._drag_start_x = x
                self._drag_start_beat = clip.start_beat
                self._drag_start_len = clip.length_beats
                if is_resize:
                    self._resizing_clip = True
                else:
                    self._dragging_clip = True
                self.clip_selected.emit(track, clip)
            else:
                # Clic sur une zone vide : sélectionne la piste sous le curseur et initie la sélection temporelle
                self.selected_clip = None
                if track:
                    self.selected_track_id = track.id
                    self.track_selected.emit(track.id)
                self._is_selecting_range = True
                click_beat = max(0.0, x / self.pixels_per_beat)
                snapped_click = self.snap_beat(click_beat)
                self._range_anchor_beat = snapped_click
                self.time_selection = (snapped_click, snapped_click)
                self.seek_requested.emit(snapped_click)
            self.update()

        elif event.button() == Qt.RightButton:
            if clip and track:
                self.selected_clip = (track, clip)
                self.selected_track_id = track.id
                self.clip_selected.emit(track, clip)
                self.update()
                self._show_clip_context_menu(event.globalPosition().toPoint(), track, clip)
            elif track:
                self.selected_track_id = track.id
                self.track_selected.emit(track.id)
                self.update()
                self._show_track_lane_context_menu(event.globalPosition().toPoint(), track, x)

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        x = event.position().x()
        y = event.position().y()

        auto_track, auto_lane, lane_top, lane_h = self._get_automation_lane_at_y(y)
        if auto_track and auto_lane:
            seg_idx = self._get_auto_segment_handle_at(auto_lane, x, y, lane_top, lane_h)
            if seg_idx is not None and 0 <= seg_idx < len(auto_lane.points) - 1:
                p0 = auto_lane.points[seg_idx]
                p0.curve_type = "linear"
                p0.curve_tension = 0.0
                self.project_modified.emit()
                self.automation_modified.emit()
                self.update()
                event.accept()
                return
            hit_pt = self._get_auto_point_at(auto_lane, x, y, lane_top, lane_h)
            if hit_pt:
                auto_lane.remove_point(hit_pt.id)
                self.selected_automation_points.discard(hit_pt.id)
                self.project_modified.emit()
                self.automation_modified.emit()
                self.update()
                event.accept()
                return
            event.accept()
            return

        track, clip, _ = self._get_clip_at(x, y)

        if clip and track:
            self.selected_clip = (track, clip)
            self.clip_selected.emit(track, clip)
            self.clip_double_clicked.emit(track, clip)
        elif track:
            click_beat = max(0.0, x / self.pixels_per_beat)

            # Si une zone temporelle était sélectionnée, le bloc s'adapte exactement dessus !
            if self.time_selection and abs(self.time_selection[1] - self.time_selection[0]) >= 0.25:
                s, e = self.time_selection
                snapped_beat = self.snap_beat(min(s, e))
                length = max(self.grid_resolution if (self.snap_enabled and self.grid_resolution > 0) else 0.5, abs(e - s))
            elif self.project.loop_enabled and self.project.loop_start_beat <= click_beat <= self.project.loop_end_beat:
                snapped_beat = self.project.loop_start_beat
                length = max(1.0, self.project.loop_end_beat - self.project.loop_start_beat)
            else:
                snapped_beat = self.snap_beat(click_beat)
                length = max(1.0, 4.0 if self.grid_resolution <= 1.0 else self.grid_resolution)

            if track.track_type == "midi":
                new_clip = MidiClip(
                    name=f"{track.name} Clip",
                    start_beat=snapped_beat,
                    length_beats=length,
                    color=track.color
                )
            else:
                new_clip = AudioClip(
                    name=f"{track.name} Audio",
                    start_beat=snapped_beat,
                    length_beats=length,
                    color=track.color
                )

            track.clips.append(new_clip)
            self.selected_clip = (track, new_clip)
            self.time_selection = None
            self.project_modified.emit()
            self.update()

            self.clip_selected.emit(track, new_clip)
            self.clip_double_clicked.emit(track, new_clip)

    def mouseMoveEvent(self, event: QMouseEvent):
        x = event.position().x()
        y = event.position().y()

        if self._resizing_lane:
            r_track, r_lane = self._resizing_lane
            dy = int(event.globalPosition().y() - self._drag_start_lane_y)
            new_h = max(40, min(220, self._drag_start_lane_h + dy))
            r_lane.height = new_h
            self.update_dimensions()
            self.update()
            self.automation_lane_height_changed.emit(r_track.id, r_lane.id, new_h)
            event.accept()
            return

        if self._drawing_auto_freehand:
            _, d_lane, d_top, d_h = self._drawing_auto_freehand
            raw_beat = max(0.0, x / self.pixels_per_beat)
            val = lane_y_to_value(d_lane, y, d_top, d_h)
            self._draw_auto_raw_points.append((raw_beat, val))
            QToolTip.showText(
                event.globalPosition().toPoint(),
                f"✏️ {d_lane.name}: {format_automation_value(d_lane.target_param, val)}",
                self,
            )
            self.update()
            event.accept()
            return

        if self._dragging_auto_curve:
            _, c_lane, p0, start_y, start_tension, c_h = self._dragging_auto_curve
            dy = (y - start_y) / max(32.0, c_h * 0.75)
            # Inverser si nécessaire pour que monter la souris bombe vers le haut
            p1_idx = c_lane.points.index(p0) + 1 if p0 in c_lane.points else -1
            sign = 1.0
            if 0 <= p1_idx < len(c_lane.points):
                if c_lane.target_param == "pan":
                    sign = -1.0 if c_lane.points[p1_idx].value >= p0.value else 1.0
                else:
                    sign = 1.0 if c_lane.points[p1_idx].value >= p0.value else -1.0
            p0.curve_type = "curve"
            p0.curve_tension = max(-1.0, min(1.0, start_tension + sign * dy))
            self.project_modified.emit()
            self.automation_modified.emit()
            QToolTip.showText(
                event.globalPosition().toPoint(),
                f"〰️ Courbure: {p0.curve_tension:+.2f}",
                self,
            )
            self.update()
            event.accept()
            return

        if self._dragging_auto_point:
            _, p_lane, pt, p_top, p_h = self._dragging_auto_point
            raw_beat = max(0.0, x / self.pixels_per_beat)
            use_snap = self.snap_enabled and self.grid_resolution > 0.0 and not (event.modifiers() & Qt.AltModifier)
            new_beat = self.snap_beat(raw_beat) if use_snap else raw_beat
            new_val = lane_y_to_value(p_lane, y, p_top, p_h)
            if event.modifiers() & Qt.ShiftModifier:
                # Ajustement fin vertical (ralentit d'un facteur 4 autour de la valeur précédente)
                new_val = p_lane.clamp_value(pt.value + (new_val - pt.value) * 0.25)
            pt.beat = new_beat
            pt.value = p_lane.clamp_value(new_val)
            p_lane.sort_points()
            self.project_modified.emit()
            self.automation_modified.emit()
            QToolTip.showText(
                event.globalPosition().toPoint(),
                f"{p_lane.name}: {format_automation_value(p_lane.target_param, pt.value)} @ {pt.beat:.2f} t",
                self,
            )
            self.update()
            event.accept()
            return

        if self._erasing_auto and self.active_tool == "erase":
            _, e_lane, e_top, e_h = self._get_automation_lane_at_y(y)
            if e_lane:
                hit_pt = self._get_auto_point_at(e_lane, x, y, e_top, e_h, radius=10.0)
                if hit_pt:
                    e_lane.remove_point(hit_pt.id)
                    self.selected_automation_points.discard(hit_pt.id)
                    self.project_modified.emit()
                    self.automation_modified.emit()
                    self.update()
            event.accept()
            return

        if self._resizing_track_height and self._resizing_track:
            dy = int(event.globalPosition().y() - self._drag_start_track_y)
            new_h = max(46, min(320, self._drag_start_track_h + dy))
            apply_all = bool(event.modifiers() & (Qt.ShiftModifier | Qt.AltModifier))
            if apply_all:
                for t in self.project.tracks:
                    t.height = new_h
            else:
                self._resizing_track.height = new_h
            self.update_dimensions()
            self.update()
            self.track_height_changed.emit(self._resizing_track.id, new_h, apply_all)
            event.accept()
            return

        # Survol avec outil Ciseaux : conserve le curseur ciseaux et affiche la ligne repère de coupe aimantée
        if self.active_tool == "split":
            self.setCursor(self.get_scissors_cursor())
            track, clip, _ = self._get_clip_at(x, y)
            raw_beat = x / self.pixels_per_beat
            snapped = self.snap_beat(raw_beat) if (self.snap_enabled and self.grid_resolution > 0) else raw_beat
            if track and clip:
                if clip.start_beat <= snapped <= clip.start_beat + clip.length_beats:
                    self._hover_split_beat = snapped
                else:
                    self._hover_split_beat = raw_beat
                self._hover_clip = (track, clip)
            else:
                self._hover_split_beat = None
                self._hover_clip = None
            self.update()
            return

        # Survol avec outil Gomme
        if self.active_tool == "erase":
            track, clip, _ = self._get_clip_at(x, y)
            if clip:
                self.setCursor(Qt.PointingHandCursor)
            else:
                self.setCursor(Qt.ArrowCursor)
            self._hover_split_beat = None
            self._hover_clip = None
            return

        # Survol avec outil Crayon
        if self.active_tool == "draw":
            auto_t, auto_l, _, _ = self._get_automation_lane_at_y(y)
            if auto_t and auto_l:
                self.setCursor(Qt.CrossCursor)
            else:
                self.setCursor(Qt.ArrowCursor)
            return

        if self._dragging_clip and self.selected_clip:
            _, clip = self.selected_clip
            delta_beats = (x - self._drag_start_x) / self.pixels_per_beat
            target_beat = self._drag_start_beat + delta_beats
            new_beat = self.snap_beat(target_beat)
            clip.start_beat = max(0.0, new_beat)
            self.project_modified.emit()
            self.update()

        elif self._resizing_clip and self.selected_clip:
            _, clip = self.selected_clip
            delta_beats = (x - self._drag_start_x) / self.pixels_per_beat
            target_end = self._drag_start_beat + self._drag_start_len + delta_beats
            snapped_end = self.snap_beat(target_end)
            min_len = self.grid_resolution if (self.snap_enabled and self.grid_resolution > 0) else 0.25
            new_len = max(min_len, snapped_end - clip.start_beat)

            # Empêcher le dépassement de la longueur audio réelle
            if isinstance(clip, AudioClip):
                max_len = clip.get_max_allowed_length_beats(self.project.bpm)
                if max_len > 0:
                    new_len = min(new_len, max_len)

            clip.length_beats = new_len
            self.project_modified.emit()
            self.update()

        elif self._is_selecting_range:
            cur_beat = max(0.0, x / self.pixels_per_beat)
            snapped_cur = self.snap_beat(cur_beat)
            s = min(self._range_anchor_beat, snapped_cur)
            e = max(self._range_anchor_beat, snapped_cur)
            self.time_selection = (s, e)
            self.time_range_selected.emit(s, e)
            self.update()

        else:
            auto_t, auto_l, l_top, l_h = self._get_automation_lane_at_y(y)
            if auto_t and auto_l:
                sep_t, sep_l = self._get_lane_separator_at_y(y)
                hit_pt = self._get_auto_point_at(auto_l, x, y, l_top, l_h)
                seg_h_idx = self._get_auto_segment_handle_at(auto_l, x, y, l_top, l_h)
                new_hov_pt = (auto_l.id, hit_pt.id) if hit_pt else None
                new_hov_seg = (auto_l.id, seg_h_idx) if seg_h_idx is not None else (
                    (auto_l.id, self._get_auto_segment_at(auto_l, max(0.0, x / self.pixels_per_beat)))
                    if (not hit_pt and self._get_auto_segment_at(auto_l, max(0.0, x / self.pixels_per_beat)) is not None)
                    else None
                )
                if new_hov_pt != self._hover_auto_point or new_hov_seg != self._hover_auto_segment:
                    self._hover_auto_point = new_hov_pt
                    self._hover_auto_segment = new_hov_seg
                    self.update()
                if sep_t and sep_l:
                    self.setCursor(Qt.SizeVerCursor)
                elif seg_h_idx is not None:
                    self.setCursor(Qt.SizeVerCursor)
                elif hit_pt:
                    self.setCursor(Qt.PointingHandCursor)
                else:
                    self.setCursor(Qt.CrossCursor)
                return

            if self._hover_auto_point is not None or self._hover_auto_segment is not None:
                self._hover_auto_point = None
                self._hover_auto_segment = None
                self.update()

            sep_track, _ = self._get_track_separator_at_y(y)
            _, clip, is_resize = self._get_clip_at(x, y)
            if sep_track and (not clip or is_resize):
                self.setCursor(Qt.SizeVerCursor)
            elif is_resize:
                self.setCursor(Qt.SizeHorCursor)
            elif clip:
                self.setCursor(Qt.PointingHandCursor)
            else:
                self.setCursor(Qt.CrossCursor)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if self._resizing_lane:
            self._resizing_lane = None
            self.releaseMouse()
            self.unsetCursor()
            self.project_modified.emit()
            self.update()
            event.accept()
            return

        if self._drawing_auto_freehand:
            _, d_lane, _, _ = self._drawing_auto_freehand
            raw_pts = self._draw_auto_raw_points
            self._drawing_auto_freehand = None
            self._draw_auto_raw_points = []
            if raw_pts:
                min_v, max_v = d_lane.get_bounds()
                simplified = simplify_freehand_points(
                    raw_pts,
                    grid_step=self.grid_resolution,
                    snap_enabled=self.snap_enabled and not (event.modifiers() & Qt.AltModifier),
                    value_span=(max_v - min_v),
                )
                if simplified:
                    b_start = min(b for b, _ in simplified)
                    b_end = max(b for b, _ in simplified)
                    # Remplacer proprement les points existants dans l'intervalle redessiné
                    d_lane.points = [
                        p for p in d_lane.points
                        if p.beat < b_start - 1e-3 or p.beat > b_end + 1e-3
                    ]
                    new_ids = set()
                    for b, v in simplified:
                        pt = d_lane.add_point(b, v, curve_type="linear")
                        new_ids.add(pt.id)
                    self.selected_automation_points = new_ids
                    self.project_modified.emit()
                    self.automation_modified.emit()
            self.update()
            event.accept()
            return

        if self._dragging_auto_curve:
            self._dragging_auto_curve = None
            self.project_modified.emit()
            self.automation_modified.emit()
            self.update()
            event.accept()
            return

        if self._dragging_auto_point:
            self._dragging_auto_point = None
            self.project_modified.emit()
            self.automation_modified.emit()
            self.update()
            event.accept()
            return

        if self._erasing_auto:
            self._erasing_auto = False
            event.accept()
            return

        if self._resizing_track_height:
            self._resizing_track_height = False
            self._resizing_track = None
            self.releaseMouse()
            self.unsetCursor()
            self.project_modified.emit()
            self.update()
            event.accept()
            return

        if self._dragging_clip or self._resizing_clip:
            self._dragging_clip = False
            self._resizing_clip = False
            self.project_modified.emit()
            self.update()

        if self._is_selecting_range:
            self._is_selecting_range = False
            if self.time_selection:
                s, e = self.time_selection
                if abs(e - s) < 0.25:
                    self.time_selection = None
            self.update()

    def keyPressEvent(self, event):
        # Touche Échap : Revenir immédiatement à l'outil Pointeur
        if event.key() == Qt.Key_Escape:
            if self.active_tool != "select":
                self.set_active_tool("select")
                if hasattr(self.parent(), "editing_toolbar"):
                    self.parent().editing_toolbar.set_active_tool("select")
                event.accept()
                return

        # Outils rapides clavier : 1 ou V = Pointeur, 2 ou C = Ciseaux, 3 = Gomme
        if event.key() in (Qt.Key_1, Qt.Key_V) and not (event.modifiers() & (Qt.ControlModifier | Qt.AltModifier)):
            self.set_active_tool("select")
            if hasattr(self.parent(), "editing_toolbar"):
                self.parent().editing_toolbar.set_active_tool("select")
            event.accept()
            return
        elif event.key() in (Qt.Key_2, Qt.Key_C) and not (event.modifiers() & (Qt.ControlModifier | Qt.AltModifier)):
            self.set_active_tool("split")
            if hasattr(self.parent(), "editing_toolbar"):
                self.parent().editing_toolbar.set_active_tool("split")
            event.accept()
            return
        elif event.key() == Qt.Key_3 and not (event.modifiers() & (Qt.ControlModifier | Qt.AltModifier)):
            self.set_active_tool("erase")
            if hasattr(self.parent(), "editing_toolbar"):
                self.parent().editing_toolbar.set_active_tool("erase")
            event.accept()
            return
        elif event.key() in (Qt.Key_4, Qt.Key_B) and not (event.modifiers() & (Qt.ControlModifier | Qt.AltModifier)):
            self.set_active_tool("draw")
            if hasattr(self.parent(), "editing_toolbar"):
                self.parent().editing_toolbar.set_active_tool("draw")
            event.accept()
            return

        # Touche Suppr ou Backspace pour supprimer les points d'automatisation ou le bloc sélectionné
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            if self.delete_selected_automation_points():
                event.accept()
                return
            if self.delete_selected_clip():
                event.accept()
                return

        # Ctrl + C : Copier
        elif event.key() == Qt.Key_C and (event.modifiers() & Qt.ControlModifier):
            if self.copy_selected_clip():
                event.accept()
                return

        # Ctrl + X : Couper
        elif event.key() == Qt.Key_X and (event.modifiers() & Qt.ControlModifier):
            if self.cut_selected_clip():
                event.accept()
                return

        # Ctrl + V : Coller à la tête de lecture
        elif event.key() == Qt.Key_V and (event.modifiers() & Qt.ControlModifier):
            if self.paste_clip_at_playhead():
                event.accept()
                return

        # Ctrl + K ou S : Scinder à la tête de lecture
        elif (event.key() == Qt.Key_K and (event.modifiers() & Qt.ControlModifier)) or \
             (event.key() == Qt.Key_S and not (event.modifiers() & (Qt.ControlModifier | Qt.AltModifier))):
            if self.split_at_playhead():
                event.accept()
                return

        # Ctrl + D : Dupliquer le bloc sélectionné immédiatement à la suite
        elif event.key() == Qt.Key_D and (event.modifiers() & Qt.ControlModifier):
            if self.duplicate_selected_clip():
                event.accept()
                return

        super().keyPressEvent(event)

    def _show_clip_context_menu(self, pos, track: Track, clip: ClipType):
        menu = QMenu(self)
        action_edit = menu.addAction("✏ Éditer le bloc")
        action_tool_split = menu.addAction("✂️ Activer l'outil Ciseaux (2)")
        action_split_playhead = menu.addAction("⚡ Scinder à la tête de lecture (Ctrl+K)")
        menu.addSeparator()
        action_copy = menu.addAction("📋 Copier (Ctrl+C)")
        action_cut = menu.addAction("✂️ Couper Bloc (Ctrl+X)")
        action_dup = menu.addAction("📑 Dupliquer (Ctrl+D)")
        action_rename = menu.addAction("Renommer...")
        menu.addSeparator()
        action_delete = menu.addAction("🗑 Supprimer le bloc (Suppr)")
        menu.addSeparator()
        action_delete_track = menu.addAction(f"🗑️ Supprimer la piste « {track.name} »")

        action = menu.exec(pos)
        if action == action_edit:
            self.clip_double_clicked.emit(track, clip)
        elif action == action_tool_split:
            self.set_active_tool("split")
            if hasattr(self.parent(), "editing_toolbar"):
                self.parent().editing_toolbar.set_active_tool("split")
        elif action == action_split_playhead:
            self.split_at_playhead()
        elif action == action_copy:
            self.copy_selected_clip()
        elif action == action_cut:
            self.cut_selected_clip()
        elif action == action_dup:
            self.duplicate_selected_clip()
        elif action == action_rename:
            new_name, ok = QInputDialog.getText(self, "Renommer le bloc", "Nom du bloc :", text=clip.name)
            if ok and new_name.strip():
                clip.name = new_name.strip()
                self.project_modified.emit()
                self.update()
        elif action == action_delete:
            self.delete_selected_clip()
        elif action == action_delete_track:
            self.track_deleted.emit(track.id)

    def _create_clip_on_track_at(self, track: Track, x: float):
        click_beat = max(0.0, x / self.pixels_per_beat)
        snapped_beat = self.snap_beat(click_beat)
        length = max(1.0, 4.0 if self.grid_resolution <= 1.0 else self.grid_resolution)
        if track.track_type == "midi":
            new_clip = MidiClip(
                name=f"{track.name} Clip",
                start_beat=snapped_beat,
                length_beats=length,
                color=track.color,
            )
        else:
            new_clip = AudioClip(
                name=f"{track.name} Audio",
                start_beat=snapped_beat,
                length_beats=length,
                color=track.color,
            )
        track.clips.append(new_clip)
        self.selected_clip = (track, new_clip)
        self.selected_track_id = track.id
        self.project_modified.emit()
        self.update()
        self.clip_selected.emit(track, new_clip)

    def _build_track_lane_context_menu(self, track: Track, x: float) -> QMenu:
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
            QMenu::separator {
                height: 1px;
                background-color: #2b3042;
                margin: 4px 6px;
            }
        """)

        act_header = menu.addAction(f"🎵 Piste : « {track.name} »")
        act_header.setEnabled(False)
        menu.addSeparator()

        act_create_clip = menu.addAction("➕ Créer un nouveau bloc ici")
        act_create_clip.triggered.connect(lambda: self._create_clip_on_track_at(track, x))

        act_paste = menu.addAction("📥 Coller le bloc à la tête de lecture (Ctrl+V)")
        act_paste.setEnabled(self._clip_clipboard is not None)
        act_paste.triggered.connect(lambda: self.paste_clip_at_playhead(track))

        menu.addSeparator()

        track_idx = next((i for i, t in enumerate(self.project.tracks) if t.id == track.id), -1)
        num_tracks = len(self.project.tracks)

        act_up = menu.addAction("⬆️ Monter la piste (Déplacer vers le haut)")
        act_up.setEnabled(track_idx > 0)
        act_up.triggered.connect(lambda: self.track_moved.emit(track.id, -1))

        act_down = menu.addAction("⬇️ Descendre la piste (Déplacer vers le bas)")
        act_down.setEnabled(0 <= track_idx < num_tracks - 1)
        act_down.triggered.connect(lambda: self.track_moved.emit(track.id, 1))

        act_dup = menu.addAction(f"📑 Dupliquer la piste « {track.name} »")
        act_dup.triggered.connect(lambda: self.track_duplicated.emit(track.id))

        menu.addSeparator()

        act_del = menu.addAction(f"🗑️ Supprimer la piste « {track.name} » (Delete)")
        act_del.setObjectName("action_delete_track")
        act_del.triggered.connect(lambda: self.track_deleted.emit(track.id))

        return menu

    def _show_track_lane_context_menu(self, pos, track: Track, x: float):
        menu = self._build_track_lane_context_menu(track, x)
        self._last_track_context_menu = menu
        menu.exec(pos)

    def _show_automation_context_menu(
        self, pos, track: Track, lane: AutomationLane, x: float, y: float, lane_top: float, lane_h: float
    ):
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
            QMenu::item { padding: 5px 16px 5px 10px; border-radius: 3px; }
            QMenu::item:selected { background-color: #0284c7; color: #ffffff; font-weight: bold; }
            QMenu::item:disabled { color: #64748b; font-weight: bold; }
            QMenu::separator { height: 1px; background-color: #2b3042; margin: 4px 6px; }
        """)
        hdr = menu.addAction(f"📈 {track.name} — {lane.name}")
        hdr.setEnabled(False)
        menu.addSeparator()

        hit_pt = self._get_auto_point_at(lane, x, y, lane_top, lane_h)
        raw_beat = max(0.0, x / self.pixels_per_beat)
        seg_idx = self._get_auto_segment_at(lane, raw_beat)
        target_pt: Optional[AutomationPoint] = hit_pt
        if target_pt is None and seg_idx is not None and 0 <= seg_idx < len(lane.points):
            target_pt = lane.points[seg_idx]

        if target_pt is not None:
            act_lin = menu.addAction("📈 Segment : Linéaire (Ligne droite)")
            act_lin.setCheckable(True)
            act_lin.setChecked(target_pt.curve_type == "linear")
            act_lin.triggered.connect(lambda: self._set_point_curve(target_pt, "linear", 0.0))

            act_curve = menu.addAction("〰️ Segment : Courbe lissée")
            act_curve.setCheckable(True)
            act_curve.setChecked(target_pt.curve_type == "curve")
            act_curve.triggered.connect(lambda: self._set_point_curve(target_pt, "curve", target_pt.curve_tension))

            act_step = menu.addAction("📐 Segment : Angle droit (Palier)")
            act_step.setCheckable(True)
            act_step.setChecked(target_pt.curve_type == "step")
            act_step.triggered.connect(lambda: self._set_point_curve(target_pt, "step", 0.0))

            if target_pt.curve_type == "curve" and abs(target_pt.curve_tension) > 1e-4:
                act_reset_c = menu.addAction("⚖️ Réinitialiser la tension de courbe")
                act_reset_c.triggered.connect(lambda: self._set_point_curve(target_pt, "curve", 0.0))

            menu.addSeparator()

        if hit_pt is not None:
            act_set_val = menu.addAction(
                f"🎯 Définir une valeur exacte ({format_automation_value(lane.target_param, hit_pt.value)})..."
            )
            act_set_val.triggered.connect(lambda: self._prompt_point_value(lane, hit_pt))

            act_del_pt = menu.addAction("🗑️ Supprimer ce point")
            act_del_pt.triggered.connect(lambda: self._delete_automation_point(lane, hit_pt.id))
        else:
            act_add_pt = menu.addAction("➕ Ajouter un point ici")
            snap_b = self.snap_beat(raw_beat) if (self.snap_enabled and self.grid_resolution > 0) else raw_beat
            val_y = lane_y_to_value(lane, y, lane_top, lane_h)
            act_add_pt.triggered.connect(lambda: self._add_automation_point_action(track, lane, snap_b, val_y))

        menu.addSeparator()
        act_draw = menu.addAction("✏️ Activer l'outil Crayon (4)")
        act_draw.triggered.connect(lambda: self._activate_tool_from_menu("draw"))

        act_clear = menu.addAction("🧹 Effacer tous les points de cette courbe")
        act_clear.setEnabled(len(lane.points) > 0)
        act_clear.triggered.connect(lambda: self._clear_automation_lane(lane))

        menu.exec(pos)

    def _set_point_curve(self, pt: AutomationPoint, curve_type: str, tension: float):
        pt.curve_type = curve_type
        pt.curve_tension = max(-1.0, min(1.0, float(tension)))
        self.project_modified.emit()
        self.automation_modified.emit()
        self.update()

    def _delete_automation_point(self, lane: AutomationLane, point_id: str):
        lane.remove_point(point_id)
        self.selected_automation_points.discard(point_id)
        self.project_modified.emit()
        self.automation_modified.emit()
        self.update()

    def _add_automation_point_action(self, track: Track, lane: AutomationLane, beat: float, val: float):
        self._ensure_lane_anchor(track, lane)
        pt = lane.add_point(beat, val, curve_type="linear")
        self.selected_automation_points = {pt.id}
        self.project_modified.emit()
        self.automation_modified.emit()
        self.update()

    def _prompt_point_value(self, lane: AutomationLane, pt: AutomationPoint):
        if lane.target_param == "volume":
            val_pct, ok = QInputDialog.getInt(
                self, "Valeur du point (Volume)", "Volume en % (0 à 150) :",
                int(round(pt.value * 100.0)), 0, 150, 1
            )
            if ok:
                pt.value = lane.clamp_value(val_pct / 100.0)
        elif lane.target_param == "pan":
            val_pct, ok = QInputDialog.getInt(
                self, "Valeur du point (Panoramique)", "Panoramique (-100 = L, 0 = C, +100 = R) :",
                int(round(pt.value * 100.0)), -100, 100, 1
            )
            if ok:
                pt.value = lane.clamp_value(val_pct / 100.0)
        else:
            min_v, max_v = lane.get_bounds()
            val_f, ok = QInputDialog.getDouble(
                self, "Valeur du point", "Valeur :", pt.value, min_v, max_v, 3
            )
            if ok:
                pt.value = lane.clamp_value(val_f)
        self.project_modified.emit()
        self.automation_modified.emit()
        self.update()

    def _clear_automation_lane(self, lane: AutomationLane):
        lane.points.clear()
        self.selected_automation_points.clear()
        self.project_modified.emit()
        self.automation_modified.emit()
        self.update()

    def _activate_tool_from_menu(self, tool_name: str):
        self.set_active_tool(tool_name)
        if hasattr(self.parent(), "editing_toolbar"):
            self.parent().editing_toolbar.set_active_tool(tool_name)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        width = self.width()
        height = self.height()

        # Fond
        painter.fillRect(0, 0, width, height, QColor("#121318"))

        # Pistes & Lignes horizontales adaptées à chaque piste
        track_layout = self.get_track_layout()
        if not track_layout:
            painter.setPen(QColor("#475569"))
            painter.setFont(QFont("Segoe UI", 11))
            painter.drawText(
                QRectF(24, 24, 650, 36),
                Qt.AlignLeft | Qt.AlignVCenter,
                "Projet vide — Cliquez sur '+ Piste' ou [Ctrl+T] pour ajouter votre première piste"
            )
        for i, (track, top_y, track_h) in enumerate(track_layout):
            is_sel_track = (self.selected_track_id and track.id == self.selected_track_id)
            if is_sel_track:
                bg_col = QColor("#1c2233")
            else:
                bg_col = QColor("#171821") if i % 2 == 0 else QColor("#14151c")
            painter.fillRect(0, top_y, width, track_h, bg_col)
            if is_sel_track:
                painter.setPen(QPen(QColor(56, 189, 248, 90), 1))
                painter.drawLine(0, top_y, width, top_y)
                painter.drawLine(0, top_y + track_h - 1, width, top_y + track_h - 1)
            painter.setPen(QPen(QColor("#232634"), 1))
            painter.drawLine(0, top_y + track_h, width, top_y + track_h)

        # Fond des sous-pistes d'automatisation (avant la grille verticale musicale)
        auto_layout = self.get_automation_layout()
        for track, lane, ly, lh in auto_layout:
            bg_col = QColor("#0e1017") if lane.enabled else QColor("#0b0c12")
            painter.fillRect(0, ly, width, lh, bg_col)

        # Grille verticale musicale dynamique (Mesures, temps et subdivisions)
        num_bars = int(width / (self.pixels_per_beat * 4)) + 4
        step = self.grid_resolution if (self.snap_enabled and self.grid_resolution > 0.0) else 1.0
        px_per_step = step * self.pixels_per_beat

        for bar in range(num_bars):
            bar_beat = bar * 4.0
            x = bar_beat * self.pixels_per_beat

            # Ligne de mesure principale (forte)
            painter.setPen(QPen(QColor("#384055"), 1))
            painter.drawLine(int(x), 0, int(x), height)

            # Lignes de temps et subdivisions
            if step <= 0.0 or not self.snap_enabled or step == 1.0:
                painter.setPen(QPen(QColor("#222533"), 1))
                for beat in range(1, 4):
                    bx = (bar_beat + beat) * self.pixels_per_beat
                    painter.drawLine(int(bx), 0, int(bx), height)
            elif step >= 4.0:
                pass  # Seulement les barres de mesure
            elif step == 2.0:
                painter.setPen(QPen(QColor("#222533"), 1))
                bx = (bar_beat + 2.0) * self.pixels_per_beat
                painter.drawLine(int(bx), 0, int(bx), height)
            else:
                # Subdivisions fines (ex: 0.5 = croches, 0.25 = doubles-croches)
                num_subdivs = int(round(4.0 / step))
                for sub in range(1, num_subdivs):
                    cur_beat = bar_beat + sub * step
                    bx = cur_beat * self.pixels_per_beat
                    is_integer_beat = abs(cur_beat - round(cur_beat)) < 1e-4
                    if is_integer_beat:
                        painter.setPen(QPen(QColor("#222533"), 1))
                        painter.drawLine(int(bx), 0, int(bx), height)
                    elif px_per_step >= 6.0:  # Empêche l'effet de moiré si trop dézoomé
                        painter.setPen(QPen(QColor("#181a24"), 1))
                        painter.drawLine(int(bx), 0, int(bx), height)

        # Région de boucle en surbrillance verticale
        if self.project.loop_enabled:
            lx1 = self.project.loop_start_beat * self.pixels_per_beat
            lx2 = self.project.loop_end_beat * self.pixels_per_beat
            painter.fillRect(QRectF(lx1, 0, lx2 - lx1, height), QColor(245, 158, 11, 15))

        # Zone temporelle sélectionnée par glisser sur la grille
        if self.time_selection:
            s, e = self.time_selection
            sx = min(s, e) * self.pixels_per_beat
            sw = max(2.0, abs(e - s) * self.pixels_per_beat)
            sel_rect = QRectF(sx, 0, sw, height)
            painter.fillRect(sel_rect, QColor(56, 189, 248, 35))
            painter.setPen(QPen(QColor("#38bdf8"), 1, Qt.DashLine))
            painter.drawRect(sel_rect)

        # Dessin des Clips / Blocs
        font_clip = QFont("Segoe UI", 9, QFont.Bold)
        painter.setFont(font_clip)

        for track, top_y, track_h in track_layout:
            for clip in track.clips:
                is_recording = getattr(clip, "_is_recording", False)
                cx = clip.start_beat * self.pixels_per_beat
                if is_recording:
                    cw = max(8.0, (self.playhead_beat - clip.start_beat) * self.pixels_per_beat)
                else:
                    cw = clip.length_beats * self.pixels_per_beat
                cy = top_y + 4
                ch = max(18, track_h - 8)

                clip_rect = QRectF(cx, cy, cw, ch)
                is_selected = (self.selected_clip and self.selected_clip[1].id == clip.id) and not is_recording

                if is_recording:
                    base_color = QColor("#ef4444")
                    body_color = QColor(220, 38, 38, 200)
                    border_color = QColor("#fca5a5")
                    border_width = 2
                    # Halo rougeoyant d'enregistrement actif
                    painter.setBrush(Qt.NoBrush)
                    painter.setPen(QPen(QColor(239, 68, 68, 140), 2))
                    painter.drawRoundedRect(clip_rect.adjusted(-2, -2, 2, 2), 6, 6)
                else:
                    base_color = QColor(clip.color)
                    alpha = 240 if is_selected else 180
                    body_color = QColor(base_color.red(), base_color.green(), base_color.blue(), alpha)
                    border_color = QColor("#ffffff") if is_selected else base_color.lighter(130)
                    border_width = 2 if is_selected else 1
                    # Si sélectionné : Halo d'illumination ambre / or éclatant
                    if is_selected:
                        painter.setBrush(Qt.NoBrush)
                        painter.setPen(QPen(QColor("#fbbf24"), 3))
                        painter.drawRoundedRect(clip_rect.adjusted(-2, -2, 2, 2), 6, 6)

                # Corps du bloc
                painter.setBrush(QBrush(body_color))
                painter.setPen(QPen(border_color, border_width))
                painter.drawRoundedRect(clip_rect, 5, 5)

                # Barre d'en-tête du clip
                h_header = min(18, int(ch * 0.35))
                header_rect = QRectF(cx, cy, cw, h_header)
                header_col = QColor(153, 27, 27) if is_recording else QColor(base_color.darker(140))
                painter.setBrush(QBrush(header_col))
                painter.drawRoundedRect(header_rect, 4, 4)
                if h_header >= 10:
                    painter.fillRect(QRectF(cx, cy + h_header - 4, cw, 4), header_col)

                # Titre du bloc (si assez d'espace vertical)
                if ch >= 26:
                    painter.setPen(QColor("#ffffff"))
                    fm = painter.fontMetrics()
                    avail_w = int(cw - 16)
                    if avail_w > 10:
                        if is_recording:
                            dur_sec = (max(0.05, self.playhead_beat - clip.start_beat) * 60.0) / max(20.0, float(self.project.bpm))
                            disp_name = f"🔴 REC [{dur_sec:.1f}s]"
                        else:
                            disp_name = clip.name
                        elided_name = fm.elidedText(disp_name, Qt.ElideRight, avail_w)
                        painter.drawText(int(cx) + 8, int(cy) + min(14, max(10, h_header - 2)), elided_name)

                # Badge ou étoile si sélectionné ou recording
                if cw > 40 and ch >= 26:
                    if is_recording:
                        painter.setPen(QColor("#fca5a5"))
                        painter.drawText(int(cx + cw - 20), int(cy) + min(14, max(10, h_header - 2)), "●")
                    elif is_selected:
                        painter.setPen(QColor("#fbbf24"))
                        painter.drawText(int(cx + cw - 18), int(cy) + min(14, max(10, h_header - 2)), "★")

                # Aperçu du contenu à l'intérieur du clip
                if ch >= 36:
                    if isinstance(clip, MidiClip):
                        painter.setPen(Qt.NoPen)
                        painter.setBrush(QBrush(QColor("#ffffff")))
                        content_top = cy + h_header + 4
                        content_h = ch - h_header - 8
                        for note in clip.notes:
                            norm_pitch = max(0.0, min(1.0, (note.pitch - 36) / 48.0))
                            ny = content_top + (1.0 - norm_pitch) * max(4, content_h - 4)
                            nx = cx + (note.start_beat * self.pixels_per_beat)
                            nw = max(3.0, note.duration * self.pixels_per_beat - 1)
                            painter.drawRoundedRect(QRectF(nx, ny, nw, 3), 1, 1)

                    elif isinstance(clip, AudioClip):
                        content_top = cy + h_header
                        content_h = ch - h_header
                        mid_y = content_top + content_h / 2.0
                        half_h = max(2.0, (content_h / 2.0) * 0.88)

                        # Ligne de zéro (centre)
                        painter.setPen(QPen(QColor(255, 255, 255, 30), 1))
                        painter.drawLine(int(cx) + 3, int(mid_y), int(cx + cw) - 3, int(mid_y))

                        clip._ensure_audio_loaded()
                        if clip.audio_data is not None and len(clip.audio_data) > 0:
                            bpm = max(20.0, float(self.project.bpm))
                            samples_per_beat = (60.0 / bpm) * clip.sample_rate
                            offset_b = float(getattr(clip, "source_offset_beats", 0.0))
                            
                            start_sample = int(offset_b * samples_per_beat)
                            dur_samples = int(clip.length_beats * samples_per_beat)
                            total_audio_samples = len(clip.audio_data)

                            avail_samples = max(0, min(dur_samples, total_audio_samples - start_sample))
                            if avail_samples > 0 and samples_per_beat > 0:
                                avail_beats = avail_samples / samples_per_beat
                                avail_pixels = max(1, int(avail_beats * self.pixels_per_beat))
                                max_pixels = max(1, int(cw - 8))
                                slice_pixels = min(max_pixels, avail_pixels)

                                mins, maxs = clip.get_slice_waveform_peaks(
                                    start_sample,
                                    start_sample + avail_samples,
                                    slice_pixels
                                )

                                if len(mins) > 0:
                                    wave_pen = QPen(QColor(255, 255, 255, 230), 1)
                                    painter.setPen(wave_pen)
                                    nb_peaks = len(mins)
                                    for i in range(nb_peaks):
                                        sx = int(cx + 4 + i)
                                        y1 = int(mid_y - maxs[i] * half_h)
                                        y2 = int(mid_y - mins[i] * half_h)
                                        if y1 == y2:
                                            y2 = y1 + 1
                                        painter.drawLine(sx, y1, sx, y2)
                        else:
                            painter.setPen(QPen(QColor(255, 255, 255, 80), 1, Qt.DashLine))
                            painter.drawLine(int(cx) + 6, int(mid_y), int(cx + cw) - 6, int(mid_y))

                # Tête d'enregistrement ou poignée de redimensionnement à droite
                if is_recording:
                    painter.setPen(QPen(QColor("#ffffff"), 2))
                    painter.drawLine(int(cx + cw), int(cy), int(cx + cw), int(cy + ch))
                else:
                    painter.setPen(QPen(QColor(255, 255, 255, 100), 2))
                    painter.drawLine(int(cx + cw) - 3, int(cy) + 6, int(cx + cw) - 3, int(cy + ch) - 6)

        # Dessin des courbes et points des sous-pistes d'automatisation
        for track, lane, ly, lh in auto_layout:
            hov_pt_id = (
                self._hover_auto_point[1]
                if (self._hover_auto_point and self._hover_auto_point[0] == lane.id)
                else None
            )
            hov_seg_idx = (
                self._hover_auto_segment[1]
                if (self._hover_auto_segment and self._hover_auto_segment[0] == lane.id)
                else None
            )
            preview = (
                self._draw_auto_raw_points
                if (self._drawing_auto_freehand and self._drawing_auto_freehand[1].id == lane.id)
                else None
            )
            paint_automation_lane(
                painter=painter,
                track=track,
                lane=lane,
                top_y=ly,
                height=lh,
                width=width,
                pixels_per_beat=self.pixels_per_beat,
                selected_point_ids=self.selected_automation_points,
                hovered_point_id=hov_pt_id,
                hovered_segment_idx=hov_seg_idx,
                draw_preview=preview,
                draw_background=False,
            )

        # Guide visuel de découpe ciseaux si outil split actif
        if self.active_tool == "split" and self._hover_clip and self._hover_split_beat is not None:
            h_track, h_clip = self._hover_clip
            cut_x = int(self._hover_split_beat * self.pixels_per_beat)
            for t, ty, th in track_layout:
                if t.id == h_track.id:
                    guide_y1 = ty + 2
                    guide_y2 = ty + th - 2
                    painter.setPen(QPen(QColor("#38bdf8"), 2, Qt.DashLine))
                    painter.drawLine(cut_x, guide_y1, cut_x, guide_y2)
                    painter.setBrush(QBrush(QColor("#38bdf8")))
                    painter.setPen(Qt.NoPen)

                    # Triangle supérieur
                    tri_top = QPolygonF([
                        QPointF(cut_x - 5, guide_y1),
                        QPointF(cut_x + 5, guide_y1),
                        QPointF(cut_x, guide_y1 + 7)
                    ])
                    painter.drawPolygon(tri_top)

                    # Triangle inférieur
                    tri_bot = QPolygonF([
                        QPointF(cut_x - 5, guide_y2),
                        QPointF(cut_x + 5, guide_y2),
                        QPointF(cut_x, guide_y2 - 7)
                    ])
                    painter.drawPolygon(tri_bot)

                    # Badge textuel avec position exacte en temps
                    tag_text = f"✂ {self._hover_split_beat:.2f} t"
                    painter.setFont(QFont("Consolas", 8, QFont.Bold))
                    fm = QFontMetrics(painter.font())
                    tw = fm.horizontalAdvance(tag_text) + 8
                    th_box = 16
                    tag_x = max(0, cut_x - tw // 2)
                    tag_y = max(0, guide_y1 - th_box - 2)
                    painter.setBrush(QBrush(QColor("#0284c7")))
                    painter.setPen(QPen(QColor("#38bdf8"), 1))
                    painter.drawRoundedRect(tag_x, tag_y, tw, th_box, 3, 3)
                    painter.setPen(QColor("#ffffff"))
                    painter.drawText(tag_x, tag_y, tw, th_box, Qt.AlignCenter, tag_text)
                    break

        # Tête de lecture (Ligne verticale)
        px = self.playhead_beat * self.pixels_per_beat
        painter.setPen(QPen(QColor("#38bdf8"), 2))
        painter.drawLine(int(px), 0, int(px), height)
