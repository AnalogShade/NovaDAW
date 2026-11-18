"""
ui/automation_lane_renderer.py - Rendu graphique et géométrie des courbes d'automatisation
"""
from typing import List, Optional, Tuple, Set
import math
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QFont, QPainterPath, QPolygonF
from PySide6.QtCore import Qt, QPointF, QRectF
from core.project import Track, AutomationLane, AutomationPoint, get_automation_param_spec


LANE_PAD_Y = 6.0


def value_to_lane_y(lane: AutomationLane, value: float, top_y: float, height: float) -> float:
    """Convertit une valeur de paramètre en coordonnée Y dans la sous-piste."""
    min_v, max_v = lane.get_bounds()
    usable_h = max(8.0, float(height) - 2.0 * LANE_PAD_Y)
    v = max(min_v, min(max_v, float(value)))
    norm = (v - min_v) / max(1e-9, max_v - min_v)
    if lane.target_param == "pan":
        # Haut = Gauche (-1.0), Centre = 0.0, Bas = Droite (+1.0)
        return float(top_y) + LANE_PAD_Y + norm * usable_h
    # Haut = Max, Bas = Min (ex: Volume 150% en haut, 0% en bas)
    return float(top_y) + LANE_PAD_Y + (1.0 - norm) * usable_h


def lane_y_to_value(lane: AutomationLane, y: float, top_y: float, height: float) -> float:
    """Convertit une coordonnée Y dans la sous-piste en valeur bornée du paramètre."""
    min_v, max_v = lane.get_bounds()
    usable_h = max(8.0, float(height) - 2.0 * LANE_PAD_Y)
    rel = (float(y) - (float(top_y) + LANE_PAD_Y)) / usable_h
    rel = max(0.0, min(1.0, rel))
    if lane.target_param == "pan":
        val = min_v + rel * (max_v - min_v)
        # Aimantation douce vers le centre C (0.0) si très proche
        if abs(val) < 0.03:
            val = 0.0
        return val
    return max_v - rel * (max_v - min_v)


def _point_line_distance(pt: Tuple[float, float], a: Tuple[float, float], b: Tuple[float, float], x_scale: float) -> float:
    """Distance perpendiculaire pondérée entre un point (beat, val) et le segment a->b."""
    ax, ay = a[0] * x_scale, a[1]
    bx, by = b[0] * x_scale, b[1]
    px, py = pt[0] * x_scale, pt[1]
    dx = bx - ax
    dy = by - ay
    denom = dx * dx + dy * dy
    if denom <= 1e-12:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / denom))
    proj_x = ax + t * dx
    proj_y = ay + t * dy
    return math.hypot(px - proj_x, py - proj_y)


def _rdp_simplify(pts: List[Tuple[float, float]], eps: float, x_scale: float) -> List[Tuple[float, float]]:
    if len(pts) <= 2:
        return pts
    max_dist = -1.0
    max_idx = 0
    first, last = pts[0], pts[-1]
    for i in range(1, len(pts) - 1):
        d = _point_line_distance(pts[i], first, last, x_scale)
        if d > max_dist:
            max_dist = d
            max_idx = i
    if max_dist > eps:
        left = _rdp_simplify(pts[: max_idx + 1], eps, x_scale)
        right = _rdp_simplify(pts[max_idx:], eps, x_scale)
        return left[:-1] + right
    return [first, last]


def simplify_freehand_points(
    raw_points: List[Tuple[float, float]],
    grid_step: float = 0.0,
    snap_enabled: bool = False,
    value_span: float = 1.5,
) -> List[Tuple[float, float]]:
    """
    Nettoie un tracé à main levée (outil Crayon) pour produire des points propres et éditables.
    """
    if not raw_points:
        return []

    # Trier par beat croissant et dédupliquer les beats quasi identiques (garder le dernier tracé)
    sorted_pts = sorted((max(0.0, float(b)), float(v)) for b, v in raw_points)
    dedup: List[Tuple[float, float]] = []
    for b, v in sorted_pts:
        if dedup and abs(b - dedup[-1][0]) < 1e-3:
            dedup[-1] = (b, v)
        else:
            dedup.append((b, v))

    if snap_enabled and grid_step > 0.0:
        snapped: List[Tuple[float, float]] = []
        for b, v in dedup:
            sb = max(0.0, round(b / grid_step) * grid_step)
            if snapped and abs(sb - snapped[-1][0]) < 1e-4:
                snapped[-1] = (sb, v)
            else:
                snapped.append((sb, v))
        return snapped

    if len(dedup) <= 2:
        return dedup

    # Simplification Ramer-Douglas-Peucker normalisée sur la plage de valeurs
    eps = max(0.012, value_span * 0.018)
    x_scale = max(0.05, value_span * 0.25)
    return _rdp_simplify(dedup, eps=eps, x_scale=x_scale)


def get_segment_midpoint_handle(
    lane: AutomationLane,
    seg_idx: int,
    top_y: float,
    height: float,
    pixels_per_beat: float,
) -> Optional[Tuple[float, float]]:
    """Retourne les coordonnées (x, y) de la poignée de courbure au milieu du segment `seg_idx`."""
    if seg_idx < 0 or seg_idx >= len(lane.points) - 1:
        return None
    p0 = lane.points[seg_idx]
    p1 = lane.points[seg_idx + 1]
    if p0.curve_type == "step" or (p1.beat - p0.beat) * pixels_per_beat < 18.0:
        return None
    mid_beat = 0.5 * (p0.beat + p1.beat)
    mid_val = lane.evaluate(mid_beat)
    return mid_beat * pixels_per_beat, value_to_lane_y(lane, mid_val, top_y, height)


def _fallback_for_lane(track: Track, lane: AutomationLane) -> float:
    if lane.target_param == "volume":
        return float(track.volume)
    if lane.target_param == "pan":
        return float(track.pan)
    return float(get_automation_param_spec(lane.target_param)["default_val"])


def paint_automation_lane(
    painter: QPainter,
    track: Track,
    lane: AutomationLane,
    top_y: float,
    height: float,
    width: int,
    pixels_per_beat: float,
    selected_point_ids: Set[str],
    hovered_point_id: Optional[str] = None,
    hovered_segment_idx: Optional[int] = None,
    draw_preview: Optional[List[Tuple[float, float]]] = None,
    draw_background: bool = True,
) -> None:
    """Dessine une sous-piste d'automatisation complète dans TimelineGrid."""
    spec = get_automation_param_spec(lane.target_param)
    base_hex = lane.color or spec["color"]
    lane_col = QColor(base_hex)
    if not lane.enabled:
        lane_col = QColor("#64748b")

    # 1. Fond de la sous-piste et liseré gauche
    if draw_background:
        bg_col = QColor("#0e1017") if lane.enabled else QColor("#0b0c12")
        painter.fillRect(QRectF(0, top_y, width, height), bg_col)

    t_col = QColor(getattr(track, "color", "#38bdf8") or "#38bdf8")
    painter.fillRect(QRectF(0, top_y, 3, height), QColor(t_col.red(), t_col.green(), t_col.blue(), 110))

    # 2. Lignes de référence (ligne centrale pour Pan, 100% pour Volume)
    if lane.target_param == "pan":
        center_y = value_to_lane_y(lane, 0.0, top_y, height)
        # Teinte très subtile au-dessus (Gauche) et en dessous (Droite)
        painter.fillRect(QRectF(0, top_y, width, max(0.0, center_y - top_y)), QColor(56, 189, 248, 8))
        painter.fillRect(QRectF(0, center_y, width, max(0.0, top_y + height - center_y)), QColor(168, 85, 247, 8))

        painter.setPen(QPen(QColor(148, 163, 184, 135), 1, Qt.DashLine))
        painter.drawLine(0, int(center_y), width, int(center_y))

        # Repères L / C / R discrets à gauche
        painter.setFont(QFont("Segoe UI", 7, QFont.Bold))
        painter.setPen(QColor(148, 163, 184, 160))
        painter.drawText(8, int(top_y + 11), "L")
        painter.drawText(8, int(center_y - 2), "C")
        painter.drawText(8, int(top_y + height - 4), "R")
        baseline_y = center_y
    else:
        if lane.target_param == "volume":
            y_100 = value_to_lane_y(lane, 1.0, top_y, height)
            painter.setPen(QPen(QColor(148, 163, 184, 75), 1, Qt.DashLine))
            painter.drawLine(0, int(y_100), width, int(y_100))
            painter.setFont(QFont("Segoe UI", 7))
            painter.setPen(QColor(148, 163, 184, 130))
            painter.drawText(8, int(y_100 - 2), "100%")
        baseline_y = value_to_lane_y(lane, lane.get_bounds()[0], top_y, height)

    # 3. Construction du tracé de la courbe
    pts = lane.points
    fallback_v = _fallback_for_lane(track, lane)

    stroke_path = QPainterPath()
    fill_path = QPainterPath()

    if not pts:
        y0 = value_to_lane_y(lane, fallback_v, top_y, height)
        stroke_path.moveTo(0.0, y0)
        stroke_path.lineTo(float(width), y0)

        fill_path.moveTo(0.0, baseline_y)
        fill_path.lineTo(0.0, y0)
        fill_path.lineTo(float(width), y0)
        fill_path.lineTo(float(width), baseline_y)
        fill_path.closeSubpath()
    else:
        first_x = pts[0].beat * pixels_per_beat
        first_y = value_to_lane_y(lane, pts[0].value, top_y, height)

        stroke_path.moveTo(0.0, first_y)
        fill_path.moveTo(0.0, baseline_y)
        fill_path.lineTo(0.0, first_y)
        if first_x > 0.0:
            stroke_path.lineTo(first_x, first_y)
            fill_path.lineTo(first_x, first_y)

        for idx in range(len(pts) - 1):
            p0 = pts[idx]
            p1 = pts[idx + 1]
            x0 = p0.beat * pixels_per_beat
            x1 = p1.beat * pixels_per_beat
            y0 = value_to_lane_y(lane, p0.value, top_y, height)
            y1 = value_to_lane_y(lane, p1.value, top_y, height)

            if p0.curve_type == "step":
                stroke_path.lineTo(x1, y0)
                stroke_path.lineTo(x1, y1)
                fill_path.lineTo(x1, y0)
                fill_path.lineTo(x1, y1)
            elif p0.curve_type == "curve":
                steps = max(8, min(40, int((x1 - x0) / 6.0)))
                for s in range(1, steps + 1):
                    t = s / float(steps)
                    b = p0.beat + t * (p1.beat - p0.beat)
                    v = lane.evaluate(b)
                    sx = b * pixels_per_beat
                    sy = value_to_lane_y(lane, v, top_y, height)
                    stroke_path.lineTo(sx, sy)
                    fill_path.lineTo(sx, sy)
            else:
                stroke_path.lineTo(x1, y1)
                fill_path.lineTo(x1, y1)

        last_x = pts[-1].beat * pixels_per_beat
        last_y = value_to_lane_y(lane, pts[-1].value, top_y, height)
        if last_x < width:
            stroke_path.lineTo(float(width), last_y)
            fill_path.lineTo(float(width), last_y)
        fill_path.lineTo(float(width), baseline_y)
        fill_path.closeSubpath()

    # Remplissage translucide
    fill_alpha = 42 if lane.enabled else 16
    painter.setPen(Qt.NoPen)
    painter.setBrush(QBrush(QColor(lane_col.red(), lane_col.green(), lane_col.blue(), fill_alpha)))
    painter.drawPath(fill_path)

    # Ligne de courbe
    line_alpha = 235 if lane.enabled else 120
    pen_style = Qt.SolidLine if pts else Qt.DashLine
    painter.setBrush(Qt.NoBrush)
    painter.setPen(QPen(QColor(lane_col.red(), lane_col.green(), lane_col.blue(), line_alpha), 2.0, pen_style))
    painter.drawPath(stroke_path)

    # 4. Poignée de courbure sur le segment survolé
    if hovered_segment_idx is not None and 0 <= hovered_segment_idx < len(pts) - 1:
        handle = get_segment_midpoint_handle(lane, hovered_segment_idx, top_y, height, pixels_per_beat)
        if handle is not None:
            hx, hy = handle
            diamond = QPolygonF([
                QPointF(hx, hy - 5.0),
                QPointF(hx + 5.0, hy),
                QPointF(hx, hy + 5.0),
                QPointF(hx - 5.0, hy),
            ])
            painter.setBrush(QBrush(QColor("#fbbf24")))
            painter.setPen(QPen(QColor("#0f172a"), 1.2))
            painter.drawPolygon(diamond)

    # 5. Points de contrôle
    for pt in pts:
        px = pt.beat * pixels_per_beat
        if px < -12 or px > width + 12:
            continue
        py = value_to_lane_y(lane, pt.value, top_y, height)
        is_sel = pt.id in selected_point_ids
        is_hov = pt.id == hovered_point_id
        r = 5.5 if (is_sel or is_hov) else 4.2

        if is_sel:
            painter.setBrush(QBrush(QColor("#fbbf24")))
            painter.setPen(QPen(QColor("#ffffff"), 1.6))
        elif is_hov:
            painter.setBrush(QBrush(QColor("#ffffff")))
            painter.setPen(QPen(lane_col, 2.0))
        else:
            painter.setBrush(QBrush(QColor("#0f172a")))
            painter.setPen(QPen(lane_col, 1.8))

        if pt.curve_type == "step":
            painter.drawRect(QRectF(px - r, py - r, 2 * r, 2 * r))
        else:
            painter.drawEllipse(QPointF(px, py), r, r)

    # 6. Aperçu du tracé à main levée (Outil Crayon)
    if draw_preview and len(draw_preview) >= 2:
        prev_path = QPainterPath()
        b0, v0 = draw_preview[0]
        prev_path.moveTo(b0 * pixels_per_beat, value_to_lane_y(lane, v0, top_y, height))
        for b, v in draw_preview[1:]:
            prev_path.lineTo(b * pixels_per_beat, value_to_lane_y(lane, v, top_y, height))
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor("#fbbf24"), 2.0, Qt.DotLine))
        painter.drawPath(prev_path)

    # 7. Séparateur inférieur de la sous-piste
    painter.setPen(QPen(QColor("#232634"), 1))
    painter.drawLine(0, int(top_y + height), width, int(top_y + height))
