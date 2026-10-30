"""
ui/piano_roll.py - Séquenceur MIDI / Piano Roll interactif avec clavier virtuel à gauche
"""
from typing import Optional, List, Tuple
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QScrollArea, QFrame, QSplitter, QSizePolicy, QSpinBox
)
from PySide6.QtGui import (
    QPainter, QColor, QPen, QBrush, QFont, QMouseEvent, QWheelEvent, QPolygonF
)
from PySide6.QtCore import Qt, Signal, QRectF, QPointF, QEvent
from core.project import MidiClip, MidiNote, Track
from core.audio_engine import AudioEngine


NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
IS_BLACK_KEY = [False, True, False, True, False, False, True, False, True, False, True, False]


def pitch_to_name(pitch: int) -> str:
    octave = (pitch // 12) - 1
    name = NOTE_NAMES[pitch % 12]
    return f"{name}{octave}"


class PianoKeysWidget(QWidget):
    """Clavier de piano vertical interactif à gauche"""
    key_pressed = Signal(int)   # pitch (Note-On)
    key_released = Signal(int)  # pitch (Note-Off)

    def __init__(self, start_pitch=36, num_pitches=48, row_height=18, parent=None):
        super().__init__(parent)
        self.start_pitch = start_pitch  # C2
        self.num_pitches = num_pitches  # 4 octaves (48 touches)
        self.row_height = row_height
        self.setFixedWidth(64)
        self.setFixedHeight(self.num_pitches * self.row_height)
        self.active_pitch: Optional[int] = None

    def pitch_at_y(self, y: int) -> int:
        idx = int(y) // self.row_height
        idx = max(0, min(self.num_pitches - 1, idx))
        # Du haut (aigu) vers le bas (grave)
        pitch = (self.start_pitch + self.num_pitches - 1) - idx
        return pitch

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            pitch = self.pitch_at_y(event.position().y())
            if self.active_pitch is not None and self.active_pitch != pitch:
                self.key_released.emit(self.active_pitch)
            self.active_pitch = pitch
            self.key_pressed.emit(pitch)
            self.update()

    def mouseMoveEvent(self, event: QMouseEvent):
        if event.buttons() & Qt.LeftButton:
            pitch = self.pitch_at_y(event.position().y())
            if pitch != self.active_pitch:
                if self.active_pitch is not None:
                    self.key_released.emit(self.active_pitch)
                self.active_pitch = pitch
                self.key_pressed.emit(pitch)
                self.update()

    def mouseReleaseEvent(self, event: QMouseEvent):
        if self.active_pitch is not None:
            released_pitch = self.active_pitch
            self.active_pitch = None
            self.key_released.emit(released_pitch)
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        width = self.width()
        font = QFont("Segoe UI", 8, QFont.Bold)
        painter.setFont(font)

        for i in range(self.num_pitches):
            pitch = (self.start_pitch + self.num_pitches - 1) - i
            y = i * self.row_height
            is_black = IS_BLACK_KEY[pitch % 12]
            is_active = (self.active_pitch == pitch)

            if is_active:
                bg = QColor("#38bdf8")
            elif is_black:
                bg = QColor("#1e2028")
            else:
                bg = QColor("#cbd5e1")

            painter.fillRect(0, y, width, self.row_height, bg)

            # Ligne de séparation
            painter.setPen(QPen(QColor("#0f1015"), 1))
            painter.drawLine(0, y + self.row_height - 1, width, y + self.row_height - 1)

            # Étiquette de note (affichée surtout pour les notes C ou blanches)
            text_col = QColor("#0f172a") if not is_black and not is_active else QColor("#94a3b8")
            if is_active:
                text_col = QColor("#ffffff")

            if (pitch % 12 == 0) or is_active:  # C notes
                painter.setPen(text_col)
                note_str = pitch_to_name(pitch)
                painter.drawText(6, y + self.row_height - 4, note_str)


class NoteGridWidget(QWidget):
    """Grille matricielle d'édition des notes MIDI"""
    note_added_or_edited = Signal(int)  # pitch joué (Note-On)
    note_released = Signal(int)         # pitch relâché (Note-Off)
    notes_changed = Signal()
    selection_changed = Signal()
    seek_requested = Signal(float)       # déplacement curseur

    def __init__(self, start_pitch=36, num_pitches=48, row_height=18, parent=None):
        super().__init__(parent)
        self.start_pitch = start_pitch
        self.num_pitches = num_pitches
        self.row_height = row_height

        self.clip: Optional[MidiClip] = None
        self.pixels_per_beat = 60.0
        self.snap_beats = 0.25  # 1/16 de mesure par défaut
        self.playhead_project_beat = 0.0

        # Interaction notes
        self.selected_note: Optional[MidiNote] = None
        self.selected_notes = []
        self.default_velocity = 100
        self._undo = []
        self._redo = []
        self._known_state = None
        self._gesture_before = None
        self._drag_notes = []
        self._marquee = None
        self._dragging_note = False
        self._resizing_note = False
        self._active_preview_pitch: Optional[int] = None
        self._drag_start_x = 0
        self._drag_start_y = 0
        self._drag_start_beat = 0.0
        self._drag_start_pitch = 60
        self._drag_start_dur = 1.0

        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setToolTip("Ctrl+clic : sélection multiple · Ctrl+glisser : rectangle · "
                        "Flèches : déplacer/transposer · Shift+↑/↓ : octave · Q : quantifier · "
                        "Ctrl+Z / Ctrl+Y : annuler/rétablir")
        self.update_dimensions()

    def set_playhead(self, beat: float):
        offset = self.clip.start_beat if self.clip else 0.0
        old_px = int((self.playhead_project_beat - offset) * self.pixels_per_beat)
        new_px = int((beat - offset) * self.pixels_per_beat)
        self.playhead_project_beat = beat
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

    def set_clip(self, clip: Optional[MidiClip]):
        if self.clip is clip:
            self._sync_history()
            self.update_dimensions()
            self.update()
            return
        self._stop_preview()
        self.clip = clip
        self.selected_note = None
        self.selected_notes = []
        self._undo.clear()
        self._redo.clear()
        self._gesture_before = None
        self._dragging_note = self._resizing_note = False
        self._marquee = None
        self._known_state = self._snapshot()
        self.selection_changed.emit()
        self.update_dimensions()
        self.update()

    def set_snap(self, snap: float):
        self.snap_beats = snap

    def set_zoom(self, ppb: float):
        self.pixels_per_beat = max(15.0, min(300.0, float(ppb)))
        self.update_dimensions()
        self.update()

    def zoom_in(self):
        self.set_zoom(self.pixels_per_beat * 1.25)

    def zoom_out(self):
        self.set_zoom(self.pixels_per_beat / 1.25)

    def zoom_reset(self):
        self.set_zoom(60.0)

    def wheelEvent(self, event: QWheelEvent):
        """Ctrl + Molette pour zoomer / dézoomer la grille du piano roll"""
        if event.modifiers() & Qt.ControlModifier:
            delta = event.angleDelta().y()
            if delta > 0:
                self.zoom_in()
            elif delta < 0:
                self.zoom_out()
            event.accept()
            return
        super().wheelEvent(event)

    def update_dimensions(self):
        h = self.num_pitches * self.row_height
        beats = self.clip.length_beats if self.clip else 16.0
        w = max(1800, int((beats + 4.0) * self.pixels_per_beat))
        self.setFixedSize(w, h)

    def pitch_at_y(self, y: int) -> int:
        idx = int(y) // self.row_height
        return max(self.start_pitch, min(self.start_pitch + self.num_pitches - 1,
                                        self.start_pitch + self.num_pitches - 1 - idx))

    def y_for_pitch(self, pitch: int) -> int:
        idx = (self.start_pitch + self.num_pitches - 1) - pitch
        return idx * self.row_height

    def _get_note_at(self, x: int, y: int) -> Tuple[Optional[MidiNote], bool]:
        """Retourne (note, is_resize_handle)"""
        if not self.clip:
            return None, False

        pitch = self.pitch_at_y(y)
        beat = x / self.pixels_per_beat

        for note in self.clip.notes:
            if note.pitch == pitch:
                note_end = note.start_beat + note.duration
                if note.start_beat <= beat <= note_end:
                    # Bord droit pour resize (6 pixels)
                    pixel_end = note_end * self.pixels_per_beat
                    is_resize = abs(x - pixel_end) <= 6
                    return note, is_resize
        return None, False

    def _snapshot(self):
        if self.clip is None:
            return None
        return (self.clip.length_beats,
                tuple((n.pitch, n.start_beat, n.duration, n.velocity) for n in self.clip.notes))

    def _sync_history(self):
        # External edits (MCP, recording) must never be overwritten by stale undo.
        state = self._snapshot()
        if state != self._known_state:
            self._undo.clear()
            self._redo.clear()
            self._known_state = state
            self._gesture_before = None
            self._dragging_note = self._resizing_note = False
            self._marquee = None
            self._stop_preview()
        if self.clip:
            self.selected_notes = [n for n in self.selected_notes
                                   if any(n is live for live in self.clip.notes)]
            if not any(self.selected_note is n for n in self.clip.notes):
                self.selected_note = None

    def _selection(self):
        self._sync_history()
        return self.selected_notes or ([self.selected_note] if self.selected_note else [])

    def _select(self, notes):
        self.selected_notes = list(notes)
        self.selected_note = self.selected_notes[-1] if self.selected_notes else None
        self.selection_changed.emit()
        self.update()

    def _changed(self):
        self._known_state = self._snapshot()
        self.update_dimensions()
        self.notes_changed.emit()
        self.update()

    def _commit(self, before):
        if before == self._snapshot():
            return
        self._undo.append(before)
        del self._undo[:-100]
        self._redo.clear()
        self._changed()

    def _restore(self, state):
        self._stop_preview()
        self.clip.length_beats, notes = state
        self.clip.notes = [MidiNote(*values) for values in notes]
        self._select([])
        self._changed()

    def undo(self):
        self._sync_history()
        if self.clip and self._undo:
            self._redo.append(self._snapshot())
            self._restore(self._undo.pop())

    def redo(self):
        self._sync_history()
        if self.clip and self._redo:
            self._undo.append(self._snapshot())
            self._restore(self._redo.pop())

    def select_all(self):
        if self.clip:
            self._select(self.clip.notes)

    def delete_selected(self):
        notes = self._selection()
        if not notes:
            return
        before = self._snapshot()
        ids = {id(n) for n in notes}
        self.clip.notes = [n for n in self.clip.notes if id(n) not in ids]
        self._select([])
        self._commit(before)

    def clear_notes(self):
        if self.clip:
            self.select_all()
            self.delete_selected()

    def copy_selected(self):
        notes = self._selection()
        if notes:
            origin = min(n.start_beat for n in notes)
            self._clipboard = [(n.pitch, n.start_beat - origin, n.duration, n.velocity)
                               for n in notes]

    def cut_selected(self):
        if self._selection():
            self.copy_selected()
            self.delete_selected()

    def _insert_notes(self, values, beat):
        if not self.clip or not values:
            return
        self._sync_history()
        before = self._snapshot()
        notes = [MidiNote(p, max(0.0, beat) + s, d, v) for p, s, d, v in values]
        self.clip.notes.extend(notes)
        self.clip.length_beats = max(self.clip.length_beats,
                                     max(n.start_beat + n.duration for n in notes))
        self._select(notes)
        self._commit(before)

    def paste_notes(self):
        beat = max(0.0, self.playhead_project_beat - self.clip.start_beat) if self.clip else 0.0
        beat = round(beat / self.snap_beats) * self.snap_beats
        self._insert_notes(getattr(self, '_clipboard', []), beat)

    def duplicate_selected(self):
        notes = self._selection()
        if notes:
            origin = min(n.start_beat for n in notes)
            end = max(n.start_beat + n.duration for n in notes)
            values = [(n.pitch, n.start_beat - origin, n.duration, n.velocity) for n in notes]
            self._insert_notes(values, end)

    def quantize_selected(self):
        notes = self._selection()
        before = self._snapshot()
        for n in notes:
            n.start_beat = max(0.0, round(n.start_beat / self.snap_beats) * self.snap_beats)
        if notes:
            self.clip.length_beats = max(self.clip.length_beats,
                                         max(n.start_beat + n.duration for n in notes))
        self._commit(before)

    def move_selected(self, beats=0.0, semitones=0):
        notes = self._selection()
        if not notes:
            return
        before = self._snapshot()
        beats = max(-min(n.start_beat for n in notes), beats)
        semitones = max(-min(n.pitch for n in notes),
                        min(127 - max(n.pitch for n in notes), semitones))
        for n in notes:
            n.start_beat += beats
            n.pitch += semitones
        self.clip.length_beats = max(self.clip.length_beats,
                                     max(n.start_beat + n.duration for n in notes))
        self._commit(before)

    def set_velocity(self, value):
        self.default_velocity = max(1, min(127, int(value)))
        notes = self._selection()
        before = self._snapshot()
        for n in notes:
            n.velocity = self.default_velocity
        self._commit(before)

    def _stop_preview(self):
        if self._active_preview_pitch is not None:
            pitch = self._active_preview_pitch
            self._active_preview_pitch = None
            self.note_released.emit(pitch)

    def event(self, event):
        # Claim editing shortcuts before window-wide clip actions can consume them.
        if event.type() == QEvent.ShortcutOverride:
            modifiers = event.modifiers()
            ctrl = bool(modifiers & Qt.ControlModifier)
            if modifiers & (Qt.AltModifier | Qt.MetaModifier):
                return super().event(event)
            if (ctrl and event.key() in (Qt.Key_A, Qt.Key_C, Qt.Key_X, Qt.Key_V,
                                         Qt.Key_D, Qt.Key_Z, Qt.Key_Y)) or (not ctrl and event.key() in (
                    Qt.Key_Delete, Qt.Key_Backspace, Qt.Key_Left, Qt.Key_Right,
                    Qt.Key_Up, Qt.Key_Down, Qt.Key_Q, Qt.Key_Escape)):
                event.accept()
                return True
        return super().event(event)

    def keyPressEvent(self, event):
        if event.modifiers() & (Qt.AltModifier | Qt.MetaModifier):
            super().keyPressEvent(event)
            return
        ctrl = bool(event.modifiers() & Qt.ControlModifier)
        shift = bool(event.modifiers() & Qt.ShiftModifier)
        key = event.key()
        actions = {Qt.Key_A: self.select_all, Qt.Key_C: self.copy_selected,
                   Qt.Key_X: self.cut_selected, Qt.Key_V: self.paste_notes,
                   Qt.Key_D: self.duplicate_selected, Qt.Key_Y: self.redo,
                   Qt.Key_Z: self.redo if shift else self.undo}
        if ctrl and key in actions:
            actions[key]()
        elif key in (Qt.Key_Delete, Qt.Key_Backspace) and not ctrl:
            self.delete_selected()
        elif key == Qt.Key_Q and not ctrl:
            self.quantize_selected()
        elif key in (Qt.Key_Up, Qt.Key_Down) and not ctrl:
            self.move_selected(semitones=(12 if shift else 1) * (1 if key == Qt.Key_Up else -1))
        elif key in (Qt.Key_Left, Qt.Key_Right) and not ctrl:
            self.move_selected(beats=self.snap_beats * (1 if key == Qt.Key_Right else -1))
        elif key == Qt.Key_Escape and not ctrl:
            self._select([])
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    def mousePressEvent(self, event: QMouseEvent):
        if not self.clip:
            return
        self.setFocus(Qt.MouseFocusReason)
        self._sync_history()
        x, y = event.position().x(), event.position().y()
        if event.button() == Qt.LeftButton and event.modifiers() & Qt.ShiftModifier:
            self.seek_requested.emit(self.clip.start_beat + max(0.0, x / self.pixels_per_beat))
            return
        note, is_resize = self._get_note_at(x, y)
        if event.button() == Qt.RightButton:
            if note:
                before = self._snapshot()
                self.clip.notes = [n for n in self.clip.notes if n is not note]
                self._select(n for n in self.selected_notes if n is not note)
                self._commit(before)
            return
        if event.button() != Qt.LeftButton:
            return
        if event.modifiers() & Qt.ControlModifier:
            if note:
                if any(n is note for n in self.selected_notes):
                    self._select(n for n in self.selected_notes if n is not note)
                else:
                    self._select([*self.selected_notes, note])
            else:
                self._select([])
                self._marquee = QRectF(QPointF(x, y), QPointF(x, y))
                self._drag_start_x, self._drag_start_y = x, y
            return
        self._gesture_before = self._snapshot()
        if note:
            if not any(n is note for n in self.selected_notes):
                self._select([note])
            self.selected_note = note
            self._drag_start_x, self._drag_start_y = x, y
            self._drag_start_pitch = self.pitch_at_y(y)
            self._drag_start_beat = note.start_beat
            self._drag_start_dur = note.duration
            self._drag_notes = [(n, n.start_beat, n.pitch, n.duration) for n in self.selected_notes]
            self._resizing_note = is_resize
            self._dragging_note = not is_resize
            if not is_resize:
                self._active_preview_pitch = note.pitch
                self.note_added_or_edited.emit(note.pitch)
        else:
            beat = max(0.0, round(x / self.pixels_per_beat / self.snap_beats) * self.snap_beats)
            # Clicking beyond the clip grows it, so added notes are always audible.
            note = MidiNote(self.pitch_at_y(y), beat, max(self.snap_beats, 1.0), self.default_velocity)
            self.clip.notes.append(note)
            self.clip.length_beats = max(self.clip.length_beats, beat + note.duration)
            self._select([note])
            self._active_preview_pitch = note.pitch
            self.note_added_or_edited.emit(note.pitch)
            self._changed()
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent):
        x, y = event.position().x(), event.position().y()
        if self._marquee is not None:
            self._marquee = QRectF(QPointF(self._drag_start_x, self._drag_start_y),
                                   QPointF(x, y)).normalized()
            self._select(n for n in self.clip.notes if self._marquee.intersects(QRectF(
                n.start_beat * self.pixels_per_beat, self.y_for_pitch(n.pitch),
                n.duration * self.pixels_per_beat, self.row_height)))
        elif (self._dragging_note or self._resizing_note) and self.selected_note:
            before = self._snapshot()
            delta = (x - self._drag_start_x) / self.pixels_per_beat
            if self._dragging_note:
                delta = round((self._drag_start_beat + delta) / self.snap_beats) * self.snap_beats - self._drag_start_beat
                delta = max(-min(s for _, s, _, _ in self._drag_notes), delta)
                pitch_delta = self.pitch_at_y(y) - self._drag_start_pitch
                pitch_delta = max(-min(p for _, _, p, _ in self._drag_notes),
                                  min(127 - max(p for _, _, p, _ in self._drag_notes), pitch_delta))
                for n, start, pitch, _ in self._drag_notes:
                    n.start_beat, n.pitch = start + delta, pitch + pitch_delta
                if self.selected_note.pitch != self._active_preview_pitch:
                    self._stop_preview()
                    self._active_preview_pitch = self.selected_note.pitch
                    self.note_added_or_edited.emit(self.selected_note.pitch)
            else:
                delta = round((self._drag_start_dur + delta) / self.snap_beats) * self.snap_beats - self._drag_start_dur
                delta = max(self.snap_beats - min(d for _, _, _, d in self._drag_notes), delta)
                for n, _, _, duration in self._drag_notes:
                    n.duration = duration + delta
            self.clip.length_beats = max(self._gesture_before[0],
                                         max(n.start_beat + n.duration for n in self.clip.notes))
            if before != self._snapshot():
                self._changed()
        else:
            _, resize = self._get_note_at(x, y)
            self.setCursor(Qt.SizeHorCursor if resize else Qt.ArrowCursor)

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._stop_preview()
        self._dragging_note = self._resizing_note = False
        self._marquee = None
        if self._gesture_before is not None:
            # One drag is one undo operation, even when many mouse moves occurred.
            before, self._gesture_before = self._gesture_before, None
            self._commit(before)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        width = self.width()
        height = self.height()

        # 1. Lignes horizontales (Notes)
        for i in range(self.num_pitches):
            pitch = (self.start_pitch + self.num_pitches - 1) - i
            y = i * self.row_height
            is_black = IS_BLACK_KEY[pitch % 12]

            bg = QColor("#121318") if is_black else QColor("#1a1c24")
            painter.fillRect(0, y, width, self.row_height, bg)

            # Ligne de séparation subtile
            painter.setPen(QPen(QColor("#242733"), 1))
            painter.drawLine(0, y + self.row_height, width, y + self.row_height)

        # 2. Lignes verticales (Mesures et temps)
        clip_beats = self.clip.length_beats if self.clip else 16.0
        num_beats = int(width / self.pixels_per_beat) + 2

        for beat in range(num_beats):
            x = beat * self.pixels_per_beat
            is_bar = (beat % 4 == 0)

            pen_color = QColor("#3f4559") if is_bar else QColor("#222533")
            pen_width = 2 if is_bar else 1
            painter.setPen(QPen(pen_color, pen_width))
            painter.drawLine(int(x), 0, int(x), height)

            # Subdivisions (1/16 ou 1/4)
            painter.setPen(QPen(QColor("#181a24"), 1))
            for sub in range(1, 4):
                sx = (beat + sub * 0.25) * self.pixels_per_beat
                painter.drawLine(int(sx), 0, int(sx), height)

        # 3. Ombrage hors des limites du clip
        if self.clip:
            clip_end_x = clip_beats * self.pixels_per_beat
            if width > clip_end_x:
                painter.fillRect(QRectF(clip_end_x, 0, width - clip_end_x, height), QColor(0, 0, 0, 120))

        # 4. Dessin des Notes
        if self.clip:
            for note in self.clip.notes:
                y = self.y_for_pitch(note.pitch) + 1
                x = note.start_beat * self.pixels_per_beat
                w = max(4.0, note.duration * self.pixels_per_beat - 2)
                h = self.row_height - 2

                is_sel = any(note is n for n in self.selected_notes) or self.selected_note is note
                note_rect = QRectF(x, y, w, h)

                base_col = QColor("#38bdf8")
                if is_sel:
                    painter.setBrush(QBrush(QColor("#7dd3fc")))
                    painter.setPen(QPen(QColor("#ffffff"), 2))
                else:
                    painter.setBrush(QBrush(base_col))
                    painter.setPen(QPen(base_col.darker(130), 1))

                painter.drawRoundedRect(note_rect, 3, 3)

                # Nom de note à l'intérieur
                if w > 24:
                    painter.setPen(QColor("#082f49"))
                    font = QFont("Segoe UI", 8, QFont.Bold)
                    painter.setFont(font)
                    painter.drawText(int(x) + 4, int(y) + self.row_height - 5, pitch_to_name(note.pitch))

        if self._marquee is not None:
            painter.setPen(QPen(QColor("#38bdf8"), 1, Qt.DashLine))
            painter.setBrush(QColor(56, 189, 248, 40))
            painter.drawRect(self._marquee)

        # 5. Tête de lecture / Curseur synchronisé avec la timeline principale
        if self.clip:
            rel_beat = self.playhead_project_beat - self.clip.start_beat
            if 0.0 <= rel_beat <= (clip_beats + 4.0):
                px = rel_beat * self.pixels_per_beat
                painter.setPen(QPen(QColor("#38bdf8"), 2))
                painter.drawLine(int(px), 0, int(px), height)

                # Curseur triangulaire en haut
                painter.setBrush(QBrush(QColor("#38bdf8")))
                painter.setPen(Qt.NoPen)
                tri = QPolygonF([
                    QPointF(px - 6, 0),
                    QPointF(px + 6, 0),
                    QPointF(px, 12)
                ])
                painter.drawPolygon(tri)


class PianoRoll(QWidget):
    """Panneau complet du Piano Roll (Zone inférieure)"""
    notes_updated = Signal()
    seek_requested = Signal(float)

    def __init__(self, audio_engine: AudioEngine, parent=None):
        super().__init__(parent)
        self.audio_engine = audio_engine
        self.current_track: Optional[Track] = None
        self.current_clip: Optional[MidiClip] = None

        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Barre d'outils supérieure du Piano Roll
        toolbar = QFrame()
        toolbar.setFixedHeight(36)
        toolbar.setStyleSheet("background-color: #1a1c24; border-bottom: 1px solid #282a36; padding: 2px 8px;")
        tb_layout = QHBoxLayout(toolbar)
        tb_layout.setContentsMargins(8, 2, 8, 2)
        tb_layout.setSpacing(12)

        self.lbl_title = QLabel("🎹 PIANO ROLL : Aucun bloc sélectionné")
        self.lbl_title.setStyleSheet("font-weight: bold; color: #38bdf8; font-size: 12px;")
        self.lbl_title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.lbl_title.setMinimumWidth(160)
        tb_layout.addWidget(self.lbl_title, 1)

        # Sélecteur de quantification / Snap
        lbl_snap = QLabel("Grille (Snap) :")
        lbl_snap.setStyleSheet("color: #94a3b8; font-size: 11px;")
        tb_layout.addWidget(lbl_snap)

        self.combo_snap = QComboBox()
        self.combo_snap.addItems(["1/16 (Double-croche)", "1/8 (Croche)", "1/4 (Noire)", "1/2 (Blanche)", "1 Mesure"])
        self.combo_snap.currentIndexChanged.connect(self._on_snap_changed)
        tb_layout.addWidget(self.combo_snap)

        # Contrôles de Zoom
        self.btn_zoom_out = QPushButton("🔍-")
        self.btn_zoom_out.setFixedSize(32, 26)
        self.btn_zoom_out.setToolTip("Dézoomer (Ctrl + Molette bas)")
        self.btn_zoom_out.clicked.connect(self.zoom_out)
        tb_layout.addWidget(self.btn_zoom_out)

        self.btn_zoom_reset = QPushButton("100%")
        self.btn_zoom_reset.setFixedSize(45, 26)
        self.btn_zoom_reset.setToolTip("Réinitialiser le zoom (Ctrl + 0)")
        self.btn_zoom_reset.clicked.connect(self.zoom_reset)
        tb_layout.addWidget(self.btn_zoom_reset)

        self.btn_zoom_in = QPushButton("🔍+")
        self.btn_zoom_in.setFixedSize(32, 26)
        self.btn_zoom_in.setToolTip("Zoomer (Ctrl + Molette haut)")
        self.btn_zoom_in.clicked.connect(self.zoom_in)
        tb_layout.addWidget(self.btn_zoom_in)

        # Bouton Ouvrir Interface Plugin
        self.btn_open_instrument = QPushButton("⚡ Éditer Instrument [e]")
        self.btn_open_instrument.setEnabled(False)
        self.btn_open_instrument.setStyleSheet("""
            QPushButton {
                background-color: #1a1e2e;
                color: #00f0ff;
                font-weight: bold;
                border: 1px solid #00f0ff;
                border-radius: 4px;
                padding: 4px 10px;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #0284c7;
                color: #ffffff;
            }
            QPushButton:disabled {
                border-color: #282a36;
                color: #475569;
                background-color: transparent;
            }
        """)
        self.btn_open_instrument.clicked.connect(self._on_open_instrument_editor)
        tb_layout.addWidget(self.btn_open_instrument)

        # Bouton Vider
        self.btn_clear = QPushButton("🗑 Tout effacer")
        self.btn_clear.setMinimumWidth(110)
        self.btn_clear.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.btn_clear.clicked.connect(self._clear_notes)
        tb_layout.addWidget(self.btn_clear)

        layout.addWidget(toolbar)

        edit_bar = QFrame()
        edit_layout = QHBoxLayout(edit_bar)
        edit_layout.setContentsMargins(8, 2, 8, 2)
        edit_layout.setSpacing(6)
        for label, tip, method in [
            ("Annuler", "Annuler dans le clip ouvert (Ctrl+Z)", "undo"),
            ("Rétablir", "Rétablir (Ctrl+Y / Ctrl+Shift+Z)", "redo"),
            ("Tout sélectionner", "Sélectionner toutes les notes (Ctrl+A)", "select_all"),
            ("Quantifier", "Aligner les notes sélectionnées sur la grille (Q)", "quantize_selected"),
            ("− Octave", "Transposer la sélection (Shift+↓)", None),
            ("+ Octave", "Transposer la sélection (Shift+↑)", None),
        ]:
            button = QPushButton(label)
            button.setToolTip(tip)
            button.setFocusPolicy(Qt.NoFocus)
            if method:
                button.clicked.connect(lambda checked=False, m=method: getattr(self.note_grid, m)())
            else:
                shift = -12 if label.startswith("−") else 12
                button.clicked.connect(lambda checked=False, s=shift: self.note_grid.move_selected(semitones=s))
            edit_layout.addWidget(button)
        edit_layout.addWidget(QLabel("Vélocité :"))
        self.spin_velocity = QSpinBox()
        self.spin_velocity.setMinimumWidth(72)
        self.spin_velocity.setRange(1, 127)
        self.spin_velocity.setValue(100)
        self.spin_velocity.setToolTip("Vélocité des notes sélectionnées et des nouvelles notes")
        self.spin_velocity.valueChanged.connect(lambda value: self.note_grid.set_velocity(value))
        edit_layout.addWidget(self.spin_velocity)
        edit_layout.addStretch()
        layout.addWidget(edit_bar)

        # Zone de défilement pour le piano + grille synchronisés
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("QScrollArea { border: none; background-color: #121318; }")
        self.scroll_area.installEventFilter(self)
        self.scroll_area.viewport().installEventFilter(self)

        # Container regroupant les touches et la grille côte à côte
        content_widget = QWidget()
        content_layout = QHBoxLayout(content_widget)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        self.piano_keys = PianoKeysWidget(start_pitch=0, num_pitches=128, row_height=18)
        self.note_grid = NoteGridWidget(start_pitch=0, num_pitches=128, row_height=18)

        # Raccordements signaux audio & transport
        self.piano_keys.key_pressed.connect(self._play_sound)
        self.piano_keys.key_released.connect(self._stop_sound)
        self.note_grid.note_added_or_edited.connect(self._play_sound)
        self.note_grid.note_released.connect(self._stop_sound)
        self.note_grid.notes_changed.connect(self.notes_updated.emit)
        self.note_grid.selection_changed.connect(self._sync_velocity)
        self.note_grid.seek_requested.connect(self.seek_requested.emit)

        content_layout.addWidget(self.piano_keys)
        content_layout.addWidget(self.note_grid)
        self.scroll_area.setWidget(content_widget)

        layout.addWidget(self.scroll_area)

        # Centrer le défilement vertical vers le milieu (Do4 / C4)
        self.scroll_area.verticalScrollBar().setValue(self.note_grid.y_for_pitch(72))

    def eventFilter(self, watched, event):
        """Intercepte Ctrl + Molette partout dans la zone de défilement du Piano Roll pour zoomer"""
        if event.type() == QEvent.Wheel and (event.modifiers() & Qt.ControlModifier):
            delta = event.angleDelta().y()
            if delta > 0:
                self.zoom_in()
            elif delta < 0:
                self.zoom_out()
            return True
        return super().eventFilter(watched, event)

    def zoom_in(self):
        self.note_grid.zoom_in()

    def zoom_out(self):
        self.note_grid.zoom_out()

    def zoom_reset(self):
        self.note_grid.zoom_reset()

    def set_playhead(self, beat: float):
        self.note_grid.set_playhead(beat)

    def set_active_track(self, track: Optional[Track]):
        """Lie le Piano Roll et son clavier virtuel à la piste active sélectionnée"""
        if self.current_track is not track:
            self._release_preview_notes()
        self.current_track = track
        self.btn_open_instrument.setEnabled(bool(track and track.track_type == "midi" and track.plugin_path))
        if not track:
            self.current_clip = None
            self.lbl_title.setText("🎹 PIANO ROLL : Aucune piste sélectionnée")
            self.note_grid.set_clip(None)
            return

        plugin_label = track.plugin_name if track.plugin_name else "Synthé Interne"

        if track.track_type == "audio":
            self.current_clip = None
            self.note_grid.set_clip(None)
            self.lbl_title.setText(f"🎹 PIANO ROLL : Piste audio [{track.name}] — Ouvrez l'Éditeur Audio pour cette piste")
            return

        is_multibus = any(k.lower() in (track.plugin_name or "").lower() for k in ["kontakt", "sampletank"])
        if track.plugin_path == "novadaw.synth" or "synth" in (track.plugin_name or "").lower():
            inst_tag = f"⚡ {track.plugin_name} (Synthé Polyphonique Multi-Couches) ✅"
        elif track.plugin_path == "novadaw.drum_machine" or "drums" in (track.plugin_name or "").lower():
            inst_tag = f"🥁 {track.plugin_name} (Échantillonneur FP32) ✅"
        elif track.plugin_path and not is_multibus:
            fmt_label = "VST2 Direct" if str(track.plugin_path).lower().endswith(".dll") else "VST3 Direct"
            inst_tag = f"{fmt_label} : {track.plugin_name} ✅"
        elif is_multibus:
            inst_tag = f"{track.plugin_name} (Sampler Multi-bus ➔ Synthé secours) ⚠️"
        else:
            inst_tag = "Synthé Interne NovaDAW"

        has_plugin = bool(track and track.plugin_path)
        self.btn_open_instrument.setEnabled(has_plugin)
        if has_plugin:
            icon = "⚡" if track.plugin_path == "novadaw.synth" else ("🥁" if track.plugin_path == "novadaw.drum_machine" else "🎹")
            self.btn_open_instrument.setText(f"{icon} Éditer {track.plugin_name or 'Instrument'} [e]")
        else:
            self.btn_open_instrument.setText("⚡ Éditer Instrument [e]")

        if track.clips and isinstance(track.clips[0], MidiClip):
            # Si le clip actuel fait déjà partie des clips de cette piste, on le conserve
            if self.current_clip not in track.clips:
                self.current_clip = track.clips[0]
            self.note_grid.set_clip(self.current_clip)
            self.lbl_title.setText(f"🎹 PIANO ROLL : [{track.name}] ➔ {self.current_clip.name} — {inst_tag}")
        else:
            self.current_clip = None
            self.note_grid.set_clip(None)
            self.lbl_title.setText(f"🎹 PIANO ROLL : [{track.name}] — {inst_tag} (Clavier actif)")

    def open_clip(self, track: Optional[Track], clip: Optional[MidiClip]):
        if not track or not clip:
            self.set_active_track(None)
            return

        if self.current_track is not track:
            self._release_preview_notes()
        self.current_track = track
        self.current_clip = clip

        has_plugin = bool(track and track.plugin_path)
        self.btn_open_instrument.setEnabled(has_plugin)
        if has_plugin:
            icon = "⚡" if track.plugin_path == "novadaw.synth" else ("🥁" if track.plugin_path == "novadaw.drum_machine" else "🎹")
            self.btn_open_instrument.setText(f"{icon} Éditer {track.plugin_name or 'Instrument'} [e]")
        else:
            self.btn_open_instrument.setText("⚡ Éditer Instrument [e]")

        is_multibus = any(k.lower() in (track.plugin_name or "").lower() for k in ["kontakt", "sampletank"])
        if track.plugin_path == "novadaw.synth" or "synth" in (track.plugin_name or "").lower():
            inst_tag = f"⚡ {track.plugin_name} (Synthé Polyphonique Multi-Couches) ✅"
        elif track.plugin_path == "novadaw.drum_machine" or "drums" in (track.plugin_name or "").lower():
            inst_tag = f"🥁 {track.plugin_name} (Échantillonneur FP32) ✅"
        elif track.plugin_path and not is_multibus:
            fmt_label = "VST2 Direct" if str(track.plugin_path).lower().endswith(".dll") else "VST3 Direct"
            inst_tag = f"{fmt_label} : {track.plugin_name} ✅"
        elif is_multibus:
            inst_tag = f"{track.plugin_name} (Sampler Multi-bus ➔ Synthé secours) ⚠️"
        else:
            inst_tag = "Synthé Interne NovaDAW"
        self.lbl_title.setText(f"🎹 PIANO ROLL : [{track.name}] ➔ {clip.name} — {inst_tag}")
        self.note_grid.set_clip(clip)

    def _on_open_instrument_editor(self):
        if not self.current_track or not self.current_track.plugin_path:
            return
        if self.current_track.plugin_path in ("novadaw.synth", "novadaw.drum_machine"):
            from ui.plugin_dialogs import open_native_plugin_editor
            from plugins.registry import plugin_registry, ensure_plugins_loaded
            native_plugin = next((p for p in getattr(self.current_track, "plugins", []) if getattr(p, "plugin_type_id", None) == self.current_track.plugin_path), None)
            if not native_plugin:
                ensure_plugins_loaded()
                native_plugin = plugin_registry.create_plugin(self.current_track.plugin_path)
                if native_plugin:
                    self.current_track.plugins.insert(0, native_plugin)
            if native_plugin:
                open_native_plugin_editor(native_plugin, self)
        else:
            from ui.plugin_dialogs import open_plugin_editor_gui
            open_plugin_editor_gui(self.current_track.plugin_path, self, f"track:{self.current_track.id}:instrument:{self.current_track.plugin_path}")

    def clear(self):
        """Réinitialise le piano roll à un état vide."""
        self.set_active_track(None)

    def _play_sound(self, pitch: int):
        velocity = self.note_grid.default_velocity
        if self.note_grid.selected_note is not None:
            velocity = self.note_grid.selected_note.velocity
        if self.audio_engine:
            if hasattr(self.audio_engine, "start_live_note"):
                self.audio_engine.start_live_note(pitch, velocity=velocity, track=self.current_track)
            else:
                self.audio_engine.preview_note(pitch, duration_sec=0.35, velocity=velocity, track=self.current_track)

    def _release_preview_notes(self):
        self.note_grid._stop_preview()
        if self.piano_keys.active_pitch is not None:
            self._stop_sound(self.piano_keys.active_pitch)
            self.piano_keys.active_pitch = None
            self.piano_keys.update()

    def hideEvent(self, event):
        self._release_preview_notes()
        super().hideEvent(event)

    def _stop_sound(self, pitch: int):
        if self.audio_engine and hasattr(self.audio_engine, "stop_live_note"):
            self.audio_engine.stop_live_note(pitch, track=self.current_track)

    def _on_snap_changed(self, idx: int):
        snaps = [0.25, 0.5, 1.0, 2.0, 4.0]
        if 0 <= idx < len(snaps):
            self.note_grid.set_snap(snaps[idx])

    def _sync_velocity(self):
        note = self.note_grid.selected_note
        value = note.velocity if note else self.note_grid.default_velocity
        self.spin_velocity.blockSignals(True)
        self.spin_velocity.setValue(value)
        self.spin_velocity.blockSignals(False)

    def _clear_notes(self):
        self.note_grid.setFocus(Qt.OtherFocusReason)
        self.note_grid.clear_notes()
