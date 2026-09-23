"""
ui/timeline_view.py - Règle temporelle, grille d'arrangement, clips et tête de lecture
"""
from typing import Optional, Tuple
import copy
import uuid
from PySide6.QtWidgets import (
    QWidget, QScrollArea, QVBoxLayout, QHBoxLayout, QFrame, QMenu, QInputDialog
)
from PySide6.QtGui import (
    QPainter, QColor, QPen, QBrush, QFont, QMouseEvent, QWheelEvent, QPolygonF
)
from PySide6.QtCore import Qt, Signal, QPointF, QRectF
from core.project import Project, Track, MidiClip, AudioClip, MidiNote, ClipType


class TimelineRuler(QWidget):
    """Règle temporelle affichant les numéros de mesures et la région de boucle"""
    seek_requested = Signal(float)
    loop_changed = Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(30)
        self.pixels_per_beat = 40.0
        self.total_beats = 128.0
        self.playhead_beat = 0.0
        self.loop_start_beat = 0.0
        self.loop_end_beat = 16.0
        self.loop_enabled = True

        self._dragging_loop = False
        self._drag_anchor_beat = 0.0

    def set_zoom(self, ppb: float):
        self.pixels_per_beat = ppb
        self.update()

    def set_playhead(self, beat: float):
        self.playhead_beat = beat
        self.update()

    def set_loop(self, enabled: bool, start_b: float, end_b: float):
        self.loop_enabled = enabled
        self.loop_start_beat = start_b
        self.loop_end_beat = end_b
        self.update()

    def mousePressEvent(self, event: QMouseEvent):
        beat = max(0.0, event.position().x() / self.pixels_per_beat)
        if event.button() == Qt.LeftButton:
            # Shift ou Clic-droit sur la règle pour définir la boucle
            if event.modifiers() & Qt.ShiftModifier:
                self._dragging_loop = True
                self._drag_anchor_beat = round(beat)
                self.loop_start_beat = self._drag_anchor_beat
                self.loop_end_beat = self._drag_anchor_beat + 4.0
            else:
                self.seek_requested.emit(beat)
            self.update()

    def mouseMoveEvent(self, event: QMouseEvent):
        beat = max(0.0, event.position().x() / self.pixels_per_beat)
        if self._dragging_loop:
            s = min(self._drag_anchor_beat, round(beat))
            e = max(self._drag_anchor_beat, round(beat))
            if e <= s:
                e = s + 1.0
            self.loop_start_beat = s
            self.loop_end_beat = e
            self.loop_changed.emit(s, e)
            self.update()
        elif event.buttons() & Qt.LeftButton:
            self.seek_requested.emit(beat)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if self._dragging_loop:
            self._dragging_loop = False
            self.loop_changed.emit(self.loop_start_beat, self.loop_end_beat)
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        width = self.width()
        height = self.height()

        # Fond
        painter.fillRect(0, 0, width, height, QColor("#14151c"))

        # Zone de boucle en surbrillance ambre
        if self.loop_enabled:
            x_start = self.loop_start_beat * self.pixels_per_beat
            x_end = self.loop_end_beat * self.pixels_per_beat
            loop_rect = QRectF(x_start, 0, x_end - x_start, height)
            painter.fillRect(loop_rect, QColor(245, 158, 11, 40))

            # Barre supérieure de boucle
            painter.setPen(QPen(QColor("#f59e0b"), 2))
            painter.drawLine(int(x_start), 2, int(x_end), 2)

            # Marqueurs L et R
            painter.setPen(QColor("#fbbf24"))
            font_small = QFont("Segoe UI", 8, QFont.Bold)
            painter.setFont(font_small)
            painter.drawText(int(x_start) + 3, 14, "▼ L")
            painter.drawText(int(x_end) - 18, 14, "R ▼")

        # Graduation des mesures (4 temps par mesure en 4/4)
        num_bars = int(width / (self.pixels_per_beat * 4)) + 4
        font = QFont("Consolas", 9, QFont.Bold)
        painter.setFont(font)

        for bar in range(num_bars):
            bar_beat = bar * 4.0
            x = bar_beat * self.pixels_per_beat

            # Ligne de mesure principale
            painter.setPen(QPen(QColor("#475069"), 1))
            painter.drawLine(int(x), 0, int(x), height)

            # Numéro de mesure
            painter.setPen(QColor("#94a3b8"))
            painter.drawText(int(x) + 5, 20, str(bar + 1))

            # Graduations de temps
            painter.setPen(QPen(QColor("#252834"), 1))
            for beat in range(1, 4):
                bx = (bar_beat + beat) * self.pixels_per_beat
                painter.drawLine(int(bx), 16, int(bx), height)

        # Ligne de séparation inférieure
        painter.setPen(QPen(QColor("#282a36"), 1))
        painter.drawLine(0, height - 1, width, height - 1)

        # Tête de lecture (Triangle)
        px = self.playhead_beat * self.pixels_per_beat
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

    DEFAULT_TRACK_HEIGHT = 76
    RESIZE_MARGIN = 5

    def __init__(self, project: Project, parent=None):
        super().__init__(parent)
        self.project = project
        self.pixels_per_beat = 40.0
        self.playhead_beat = 0.0

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

        self.setMouseTracking(True)
        self.update_dimensions()

    def set_zoom(self, ppb: float):
        self.pixels_per_beat = ppb
        self.update_dimensions()
        self.update()

    def set_playhead(self, beat: float):
        self.playhead_beat = beat
        self.update()

    def get_track_layout(self) -> list:
        """Retourne la liste des tuples (track, top_y, height) pour chaque piste"""
        layout = []
        curr_y = 0
        for track in self.project.tracks:
            h = getattr(track, "height", self.DEFAULT_TRACK_HEIGHT)
            layout.append((track, curr_y, h))
            curr_y += h
        return layout

    def update_dimensions(self):
        total_tracks_h = sum(getattr(t, "height", self.DEFAULT_TRACK_HEIGHT) for t in self.project.tracks)
        h = max(400, total_tracks_h)
        w = max(2400, int(128.0 * self.pixels_per_beat))
        self.setFixedSize(w, h)

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
            if clip and track:
                self.selected_clip = (track, clip)
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
                # Clic sur une zone vide : initie la sélection temporelle
                self.selected_clip = None
                self._is_selecting_range = True
                click_beat = max(0.0, x / self.pixels_per_beat)
                self._range_anchor_beat = click_beat
                self.time_selection = (click_beat, click_beat)
                self.seek_requested.emit(click_beat)
            self.update()

        elif event.button() == Qt.RightButton:
            if clip and track:
                self.selected_clip = (track, clip)
                self.clip_selected.emit(track, clip)
                self.update()
                self._show_clip_context_menu(event.globalPosition().toPoint(), track, clip)

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        x = event.position().x()
        y = event.position().y()
        track, clip, _ = self._get_clip_at(x, y)

        if clip and track:
            self.selected_clip = (track, clip)
            self.clip_selected.emit(track, clip)
            self.clip_double_clicked.emit(track, clip)
        elif track:
            click_beat = max(0.0, x / self.pixels_per_beat)

            # Si une zone temporelle était sélectionnée, le bloc s'adapte exactement dessus !
            if self.time_selection and abs(self.time_selection[1] - self.time_selection[0]) >= 0.5:
                s, e = self.time_selection
                snapped_beat = max(0.0, round(s))
                length = max(1.0, round(e - s))
            elif self.project.loop_enabled and self.project.loop_start_beat <= click_beat <= self.project.loop_end_beat:
                snapped_beat = self.project.loop_start_beat
                length = max(1.0, self.project.loop_end_beat - self.project.loop_start_beat)
            else:
                snapped_beat = max(0.0, round(click_beat / 4.0) * 4.0)
                length = 4.0

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

        if self._dragging_clip and self.selected_clip:
            _, clip = self.selected_clip
            delta_beats = (x - self._drag_start_x) / self.pixels_per_beat
            new_beat = max(0.0, round(self._drag_start_beat + delta_beats))
            clip.start_beat = new_beat
            self.project_modified.emit()
            self.update()

        elif self._resizing_clip and self.selected_clip:
            _, clip = self.selected_clip
            delta_beats = (x - self._drag_start_x) / self.pixels_per_beat
            new_len = max(1.0, round(self._drag_start_len + delta_beats))
            clip.length_beats = new_len
            self.project_modified.emit()
            self.update()

        elif self._is_selecting_range:
            cur_beat = max(0.0, x / self.pixels_per_beat)
            s = min(self._range_anchor_beat, cur_beat)
            e = max(self._range_anchor_beat, cur_beat)
            self.time_selection = (s, e)
            self.time_range_selected.emit(s, e)
            self.update()

        else:
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
        # Touche Suppr ou Backspace pour supprimer le bloc sélectionné
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            if self.selected_clip:
                track, clip = self.selected_clip
                track.clips = [c for c in track.clips if c.id != clip.id]
                self.selected_clip = None
                self.project_modified.emit()
                self.update()
                event.accept()
                return

        # Ctrl + D : Dupliquer le bloc sélectionné immédiatement à la suite
        elif event.key() == Qt.Key_D and (event.modifiers() & Qt.ControlModifier):
            if self.selected_clip:
                track, clip = self.selected_clip
                new_clip = copy.deepcopy(clip)
                new_clip.id = str(uuid.uuid4())[:8]
                new_clip.name = f"{clip.name} (Copie)"
                new_clip.start_beat = clip.start_beat + clip.length_beats
                track.clips.append(new_clip)
                self.selected_clip = (track, new_clip)
                self.project_modified.emit()
                self.update()
                self.clip_selected.emit(track, new_clip)
                event.accept()
                return

        super().keyPressEvent(event)

    def _show_clip_context_menu(self, pos, track: Track, clip: ClipType):
        menu = QMenu(self)
        action_edit = menu.addAction("✏ Éditer le bloc")
        action_dup = menu.addAction("📑 Dupliquer (Ctrl+D)")
        action_rename = menu.addAction("Renommer...")
        menu.addSeparator()
        action_delete = menu.addAction("🗑 Supprimer le bloc (Suppr)")

        action = menu.exec(pos)
        if action == action_edit:
            self.clip_double_clicked.emit(track, clip)
        elif action == action_dup:
            new_clip = copy.deepcopy(clip)
            new_clip.id = str(uuid.uuid4())[:8]
            new_clip.name = f"{clip.name} (Copie)"
            new_clip.start_beat = clip.start_beat + clip.length_beats
            track.clips.append(new_clip)
            self.selected_clip = (track, new_clip)
            self.project_modified.emit()
            self.update()
            self.clip_selected.emit(track, new_clip)
        elif action == action_rename:
            new_name, ok = QInputDialog.getText(self, "Renommer le bloc", "Nom du bloc :", text=clip.name)
            if ok and new_name.strip():
                clip.name = new_name.strip()
                self.project_modified.emit()
                self.update()
        elif action == action_delete:
            track.clips = [c for c in track.clips if c.id != clip.id]
            if self.selected_clip and self.selected_clip[1].id == clip.id:
                self.selected_clip = None
            self.project_modified.emit()
            self.update()

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
            bg_col = QColor("#171821") if i % 2 == 0 else QColor("#14151c")
            painter.fillRect(0, top_y, width, track_h, bg_col)
            painter.setPen(QPen(QColor("#232634"), 1))
            painter.drawLine(0, top_y + track_h, width, top_y + track_h)

        # Grille verticale (Mesures et temps)
        num_bars = int(width / (self.pixels_per_beat * 4)) + 4
        for bar in range(num_bars):
            bar_beat = bar * 4.0
            x = bar_beat * self.pixels_per_beat

            painter.setPen(QPen(QColor("#2a2e3f"), 1))
            painter.drawLine(int(x), 0, int(x), height)

            painter.setPen(QPen(QColor("#1b1d28"), 1))
            for beat in range(1, 4):
                bx = (bar_beat + beat) * self.pixels_per_beat
                painter.drawLine(int(bx), 0, int(bx), height)

        # Région de boucle en surbrillance verticale
        if self.project.loop_enabled:
            lx1 = self.project.loop_start_beat * self.pixels_per_beat
            lx2 = self.project.loop_end_beat * self.pixels_per_beat
            painter.fillRect(QRectF(lx1, 0, lx2 - lx1, height), QColor(245, 158, 11, 15))

        # Zone temporelle sélectionnée par glisser sur la grille
        if self.time_selection:
            s, e = self.time_selection
            sx = s * self.pixels_per_beat
            sw = max(2.0, (e - s) * self.pixels_per_beat)
            sel_rect = QRectF(sx, 0, sw, height)
            painter.fillRect(sel_rect, QColor(56, 189, 248, 35))
            painter.setPen(QPen(QColor("#38bdf8"), 1, Qt.DashLine))
            painter.drawRect(sel_rect)

        # Dessin des Clips / Blocs
        font_clip = QFont("Segoe UI", 9, QFont.Bold)
        painter.setFont(font_clip)

        for track, top_y, track_h in track_layout:
            for clip in track.clips:
                cx = clip.start_beat * self.pixels_per_beat
                cw = clip.length_beats * self.pixels_per_beat
                cy = top_y + 4
                ch = max(18, track_h - 8)

                clip_rect = QRectF(cx, cy, cw, ch)
                is_selected = self.selected_clip and self.selected_clip[1].id == clip.id

                base_color = QColor(clip.color)
                alpha = 240 if is_selected else 180
                body_color = QColor(base_color.red(), base_color.green(), base_color.blue(), alpha)

                # Si sélectionné : Halo d'illumination ambre / or éclatant
                if is_selected:
                    painter.setBrush(Qt.NoBrush)
                    painter.setPen(QPen(QColor("#fbbf24"), 3))
                    painter.drawRoundedRect(clip_rect.adjusted(-2, -2, 2, 2), 6, 6)

                # Corps du bloc
                painter.setBrush(QBrush(body_color))
                border_color = QColor("#ffffff") if is_selected else base_color.lighter(130)
                border_width = 2 if is_selected else 1
                painter.setPen(QPen(border_color, border_width))
                painter.drawRoundedRect(clip_rect, 5, 5)

                # Barre d'en-tête du clip
                h_header = min(18, int(ch * 0.35))
                header_rect = QRectF(cx, cy, cw, h_header)
                header_col = QColor(base_color.darker(140))
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
                        elided_name = fm.elidedText(clip.name, Qt.ElideRight, avail_w)
                        painter.drawText(int(cx) + 8, int(cy) + min(14, max(10, h_header - 2)), elided_name)

                # Badge ou étoile si sélectionné
                if is_selected and cw > 40 and ch >= 26:
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
                        painter.setPen(QPen(QColor("#ffffff"), 1))
                        mid_y = cy + h_header + (ch - h_header) / 2
                        painter.drawLine(int(cx) + 4, int(mid_y), int(cx + cw) - 4, int(mid_y))
                        step = 6
                        max_wave = max(2, (ch - h_header) * 0.35)
                        for sx in range(int(cx) + 6, int(cx + cw) - 6, step):
                            h_wave = min(max_wave, 3 + (abs(sx % 17 - 8) * 1.5))
                            painter.drawLine(sx, int(mid_y - h_wave), sx, int(mid_y + h_wave))

                # Poignée de redimensionnement à droite
                painter.setPen(QPen(QColor(255, 255, 255, 100), 2))
                painter.drawLine(int(cx + cw) - 3, int(cy) + 6, int(cx + cw) - 3, int(cy + ch) - 6)

        # Tête de lecture (Ligne verticale)
        px = self.playhead_beat * self.pixels_per_beat
        painter.setPen(QPen(QColor("#38bdf8"), 2))
        painter.drawLine(int(px), 0, int(px), height)
