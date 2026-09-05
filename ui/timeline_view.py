"""
ui/timeline_view.py - Règle temporelle, grille d'arrangement, clips et tête de lecture
"""
from typing import Optional, Tuple
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
    clip_double_clicked = Signal(Track, object)  # (track, clip)
    project_modified = Signal()
    seek_requested = Signal(float)

    TRACK_HEIGHT = 76

    def __init__(self, project: Project, parent=None):
        super().__init__(parent)
        self.project = project
        self.pixels_per_beat = 40.0
        self.playhead_beat = 0.0

        # État d'interaction
        self.selected_clip: Optional[Tuple[Track, ClipType]] = None
        self._dragging_clip = False
        self._resizing_clip = False
        self._drag_start_x = 0
        self._drag_start_beat = 0.0
        self._drag_start_len = 0.0

        self.setMouseTracking(True)
        self.update_dimensions()

    def set_zoom(self, ppb: float):
        self.pixels_per_beat = ppb
        self.update_dimensions()
        self.update()

    def set_playhead(self, beat: float):
        self.playhead_beat = beat
        self.update()

    def update_dimensions(self):
        total_tracks = max(1, len(self.project.tracks))
        h = total_tracks * self.TRACK_HEIGHT
        w = max(2400, int(128.0 * self.pixels_per_beat))
        self.setFixedSize(w, h)

    def _get_track_at_y(self, y: int) -> Tuple[Optional[Track], int]:
        index = y // self.TRACK_HEIGHT
        if 0 <= index < len(self.project.tracks):
            return self.project.tracks[index], index
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
                # Vérifier si on est sur les 8 pixels du bord droit pour le redimensionnement
                clip_pixel_end = clip_end * self.pixels_per_beat
                is_resize = abs(x - clip_pixel_end) <= 8
                return track, clip, is_resize
        return track, None, False

    def mousePressEvent(self, event: QMouseEvent):
        x = event.position().x()
        y = event.position().y()
        track, clip, is_resize = self._get_clip_at(x, y)

        if event.button() == Qt.LeftButton:
            if clip:
                self.selected_clip = (track, clip)
                self._drag_start_x = x
                self._drag_start_beat = clip.start_beat
                self._drag_start_len = clip.length_beats
                if is_resize:
                    self._resizing_clip = True
                else:
                    self._dragging_clip = True
            else:
                self.selected_clip = None
                # Déplacer la tête de lecture au clic
                beat = x / self.pixels_per_beat
                self.seek_requested.emit(beat)
            self.update()

        elif event.button() == Qt.RightButton:
            if clip:
                self._show_clip_context_menu(event.globalPosition().toPoint(), track, clip)

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        x = event.position().x()
        y = event.position().y()
        track, clip, _ = self._get_clip_at(x, y)

        if clip:
            # Ouvrir le Piano Roll ou l'Éditeur Audio !
            self.clip_double_clicked.emit(track, clip)
        elif track:
            # Double-clic sur piste vide : Créer un nouveau bloc/clip !
            # Alignement sur la mesure ou le temps le plus proche
            click_beat = x / self.pixels_per_beat
            snapped_beat = max(0.0, round(click_beat / 4.0) * 4.0)
            length = 4.0  # 1 mesure de 4 temps par défaut

            # Si l'intervalle de boucle englobe ce point, utiliser la boucle
            if self.project.loop_enabled and self.project.loop_start_beat <= click_beat <= self.project.loop_end_beat:
                snapped_beat = self.project.loop_start_beat
                length = max(1.0, self.project.loop_end_beat - self.project.loop_start_beat)

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
            self.project_modified.emit()
            self.update()

            # Ouvrir immédiatement l'éditeur sur ce nouveau clip créé !
            self.clip_double_clicked.emit(track, new_clip)

    def mouseMoveEvent(self, event: QMouseEvent):
        x = event.position().x()
        y = event.position().y()

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

        else:
            # Curseur adaptatif selon survol du bord de clip
            _, clip, is_resize = self._get_clip_at(x, y)
            if is_resize:
                self.setCursor(Qt.SizeHorCursor)
            elif clip:
                self.setCursor(Qt.ArrowCursor)
            else:
                self.setCursor(Qt.CrossCursor)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if self._dragging_clip or self._resizing_clip:
            self._dragging_clip = False
            self._resizing_clip = False
            self.project_modified.emit()
            self.update()

    def _show_clip_context_menu(self, pos, track: Track, clip: ClipType):
        menu = QMenu(self)
        action_edit = menu.addAction("✏ Éditer le bloc")
        action_rename = menu.addAction("Renommer...")
        menu.addSeparator()
        action_delete = menu.addAction("🗑 Supprimer le bloc")

        action = menu.exec(pos)
        if action == action_edit:
            self.clip_double_clicked.emit(track, clip)
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

        # Pistes & Lignes horizontales
        for i, track in enumerate(self.project.tracks):
            y = i * self.TRACK_HEIGHT
            # Ligne de fond alternée subtile
            bg_col = QColor("#171821") if i % 2 == 0 else QColor("#14151c")
            painter.fillRect(0, y, width, self.TRACK_HEIGHT, bg_col)

            # Ligne de séparation de piste
            painter.setPen(QPen(QColor("#232634"), 1))
            painter.drawLine(0, y + self.TRACK_HEIGHT, width, y + self.TRACK_HEIGHT)

        # Grille verticale (Mesures et temps)
        num_bars = int(width / (self.pixels_per_beat * 4)) + 4
        for bar in range(num_bars):
            bar_beat = bar * 4.0
            x = bar_beat * self.pixels_per_beat

            # Ligne de mesure (Barre franche)
            painter.setPen(QPen(QColor("#2a2e3f"), 1))
            painter.drawLine(int(x), 0, int(x), height)

            # Subdivisions (Temps)
            painter.setPen(QPen(QColor("#1b1d28"), 1))
            for beat in range(1, 4):
                bx = (bar_beat + beat) * self.pixels_per_beat
                painter.drawLine(int(bx), 0, int(bx), height)

        # Région de boucle en surbrillance verticale
        if self.project.loop_enabled:
            lx1 = self.project.loop_start_beat * self.pixels_per_beat
            lx2 = self.project.loop_end_beat * self.pixels_per_beat
            painter.fillRect(QRectF(lx1, 0, lx2 - lx1, height), QColor(245, 158, 11, 15))

        # Dessin des Clips / Blocs
        font_clip = QFont("Segoe UI", 9, QFont.Bold)
        painter.setFont(font_clip)

        for track_idx, track in enumerate(self.project.tracks):
            y_track = track_idx * self.TRACK_HEIGHT

            for clip in track.clips:
                cx = clip.start_beat * self.pixels_per_beat
                cw = clip.length_beats * self.pixels_per_beat
                cy = y_track + 6
                ch = self.TRACK_HEIGHT - 12

                clip_rect = QRectF(cx, cy, cw, ch)
                is_selected = self.selected_clip and self.selected_clip[1].id == clip.id

                # Couleur de base
                base_color = QColor(clip.color)
                body_color = QColor(base_color.red(), base_color.green(), base_color.blue(), 190)

                # Corps du bloc
                painter.setBrush(QBrush(body_color))
                border_color = QColor("#ffffff") if is_selected else base_color.lighter(130)
                border_width = 2 if is_selected else 1
                painter.setPen(QPen(border_color, border_width))
                painter.drawRoundedRect(clip_rect, 5, 5)

                # Barre d'en-tête du clip
                header_rect = QRectF(cx, cy, cw, 18)
                header_col = QColor(base_color.darker(140))
                painter.setBrush(QBrush(header_col))
                painter.drawRoundedRect(header_rect, 5, 5)
                # Remettre l'angle inférieur droit net
                painter.fillRect(QRectF(cx, cy + 12, cw, 6), header_col)

                # Titre du bloc
                painter.setPen(QColor("#ffffff"))
                painter.drawText(int(cx) + 8, int(cy) + 14, clip.name)

                # Aperçu du contenu à l'intérieur du clip
                if isinstance(clip, MidiClip):
                    # Mini représentation des notes MIDI
                    painter.setPen(Qt.NoPen)
                    painter.setBrush(QBrush(QColor("#ffffff")))
                    for note in clip.notes:
                        # Hauteur relative (pitch 36 à 84)
                        norm_pitch = max(0.0, min(1.0, (note.pitch - 36) / 48.0))
                        ny = cy + 22 + (1.0 - norm_pitch) * (ch - 28)
                        nx = cx + (note.start_beat * self.pixels_per_beat)
                        nw = max(3.0, note.duration * self.pixels_per_beat - 1)
                        painter.drawRoundedRect(QRectF(nx, ny, nw, 3), 1, 1)

                elif isinstance(clip, AudioClip):
                    # Mini représentation d'ondes audio
                    painter.setPen(QPen(QColor("#ffffff"), 1))
                    mid_y = cy + 18 + (ch - 18) / 2
                    painter.drawLine(int(cx) + 4, int(mid_y), int(cx + cw) - 4, int(mid_y))
                    # Ondes simulées ou réelles
                    step = 6
                    for sx in range(int(cx) + 6, int(cx + cw) - 6, step):
                        h_wave = 4 + (abs(sx % 17 - 8) * 1.5)
                        painter.drawLine(sx, int(mid_y - h_wave), sx, int(mid_y + h_wave))

                # Poignée de redimensionnement à droite
                painter.setPen(QPen(QColor(255, 255, 255, 100), 2))
                painter.drawLine(int(cx + cw) - 3, int(cy) + 8, int(cx + cw) - 3, int(cy + ch) - 8)

        # Tête de lecture (Ligne verticale)
        px = self.playhead_beat * self.pixels_per_beat
        painter.setPen(QPen(QColor("#38bdf8"), 2))
        painter.drawLine(int(px), 0, int(px), height)
