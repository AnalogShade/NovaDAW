"""
ui/piano_roll.py - Séquenceur MIDI / Piano Roll interactif avec clavier virtuel à gauche
"""
from typing import Optional, List, Tuple
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QScrollArea, QFrame, QSplitter
)
from PySide6.QtGui import (
    QPainter, QColor, QPen, QBrush, QFont, QMouseEvent, QWheelEvent
)
from PySide6.QtCore import Qt, Signal, QRectF, QPointF
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
    key_pressed = Signal(int)  # pitch

    def __init__(self, start_pitch=36, num_pitches=48, row_height=18, parent=None):
        super().__init__(parent)
        self.start_pitch = start_pitch  # C2
        self.num_pitches = num_pitches  # 4 octaves (48 touches)
        self.row_height = row_height
        self.setFixedWidth(64)
        self.setFixedHeight(self.num_pitches * self.row_height)
        self.active_pitch: Optional[int] = None

    def pitch_at_y(self, y: int) -> int:
        idx = y // self.row_height
        # Du haut (aigu) vers le bas (grave)
        pitch = (self.start_pitch + self.num_pitches - 1) - idx
        return pitch

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            pitch = self.pitch_at_y(event.position().y())
            self.active_pitch = pitch
            self.key_pressed.emit(pitch)
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent):
        self.active_pitch = None
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
    note_added_or_edited = Signal(int)  # pitch joué
    notes_changed = Signal()
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
        self._dragging_note = False
        self._resizing_note = False
        self._drag_start_x = 0
        self._drag_start_y = 0
        self._drag_start_beat = 0.0
        self._drag_start_pitch = 60
        self._drag_start_dur = 1.0

        self.setMouseTracking(True)
        self.update_dimensions()

    def set_playhead(self, beat: float):
        self.playhead_project_beat = beat
        self.update()

    def set_clip(self, clip: Optional[MidiClip]):
        self.clip = clip
        self.selected_note = None
        self.update_dimensions()
        self.update()

    def set_snap(self, snap: float):
        self.snap_beats = snap

    def update_dimensions(self):
        h = self.num_pitches * self.row_height
        beats = self.clip.length_beats if self.clip else 16.0
        w = max(1800, int((beats + 4.0) * self.pixels_per_beat))
        self.setFixedSize(w, h)

    def pitch_at_y(self, y: int) -> int:
        idx = y // self.row_height
        return (self.start_pitch + self.num_pitches - 1) - idx

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

    def mousePressEvent(self, event: QMouseEvent):
        if not self.clip:
            return

        x = event.position().x()
        y = event.position().y()

        # Shift + Clic gauche : Déplace le curseur de lecture directement à cet endroit !
        if event.button() == Qt.LeftButton and (event.modifiers() & Qt.ShiftModifier):
            rel_beat = max(0.0, x / self.pixels_per_beat)
            abs_beat = (self.clip.start_beat if self.clip else 0.0) + rel_beat
            self.seek_requested.emit(abs_beat)
            self.update()
            return

        note, is_resize = self._get_note_at(x, y)

        if event.button() == Qt.LeftButton:
            if note:
                self.selected_note = note
                self._drag_start_x = x
                self._drag_start_y = y
                self._drag_start_beat = note.start_beat
                self._drag_start_pitch = note.pitch
                self._drag_start_dur = note.duration

                if is_resize:
                    self._resizing_note = True
                else:
                    self._dragging_note = True
                    self.note_added_or_edited.emit(note.pitch)
            else:
                # Créer une nouvelle note au clic !
                pitch = self.pitch_at_y(y)
                raw_beat = x / self.pixels_per_beat
                # Magnétisme / Snap
                snapped_beat = round(raw_beat / self.snap_beats) * self.snap_beats
                # Durée par défaut = 1 temps ou valeur du snap
                duration = max(self.snap_beats, 1.0)

                new_note = MidiNote(pitch=pitch, start_beat=snapped_beat, duration=duration)
                self.clip.notes.append(new_note)
                self.selected_note = new_note
                self.note_added_or_edited.emit(pitch)
                self.notes_changed.emit()
            self.update()

        elif event.button() == Qt.RightButton:
            # Clic droit sur une note = Suppression immédiate !
            if note:
                self.clip.notes = [n for n in self.clip.notes if n != note]
                if self.selected_note == note:
                    self.selected_note = None
                self.notes_changed.emit()
                self.update()

    def mouseMoveEvent(self, event: QMouseEvent):
        x = event.position().x()
        y = event.position().y()

        if self._dragging_note and self.selected_note:
            delta_beat = (x - self._drag_start_x) / self.pixels_per_beat
            new_beat = max(0.0, round((self._drag_start_beat + delta_beat) / self.snap_beats) * self.snap_beats)
            new_pitch = self.pitch_at_y(y)

            if self.selected_note.pitch != new_pitch:
                self.note_added_or_edited.emit(new_pitch)

            self.selected_note.start_beat = new_beat
            self.selected_note.pitch = new_pitch
            self.notes_changed.emit()
            self.update()

        elif self._resizing_note and self.selected_note:
            delta_beat = (x - self._drag_start_x) / self.pixels_per_beat
            new_dur = max(self.snap_beats, round((self._drag_start_dur + delta_beat) / self.snap_beats) * self.snap_beats)
            self.selected_note.duration = new_dur
            self.notes_changed.emit()
            self.update()

        else:
            _, is_resize = self._get_note_at(x, y)
            if is_resize:
                self.setCursor(Qt.SizeHorCursor)
            else:
                self.setCursor(Qt.ArrowCursor)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if self._dragging_note or self._resizing_note:
            self._dragging_note = False
            self._resizing_note = False
            self.notes_changed.emit()
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

                is_sel = (self.selected_note == note)
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
        tb_layout.addWidget(self.lbl_title)

        # Sélecteur de quantification / Snap
        lbl_snap = QLabel("Grille (Snap) :")
        lbl_snap.setStyleSheet("color: #94a3b8; font-size: 11px;")
        tb_layout.addWidget(lbl_snap)

        self.combo_snap = QComboBox()
        self.combo_snap.addItems(["1/16 (Double-croche)", "1/8 (Croche)", "1/4 (Noire)", "1/2 (Blanche)", "1 Mesure"])
        self.combo_snap.currentIndexChanged.connect(self._on_snap_changed)
        tb_layout.addWidget(self.combo_snap)

        tb_layout.addStretch()

        # Bouton Vider
        self.btn_clear = QPushButton("🗑 Tout effacer")
        self.btn_clear.clicked.connect(self._clear_notes)
        tb_layout.addWidget(self.btn_clear)

        layout.addWidget(toolbar)

        # Zone de défilement pour le piano + grille synchronisés
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("QScrollArea { border: none; background-color: #121318; }")

        # Container regroupant les touches et la grille côte à côte
        content_widget = QWidget()
        content_layout = QHBoxLayout(content_widget)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        self.piano_keys = PianoKeysWidget(start_pitch=36, num_pitches=48, row_height=18)
        self.note_grid = NoteGridWidget(start_pitch=36, num_pitches=48, row_height=18)

        # Raccordements signaux audio & transport
        self.piano_keys.key_pressed.connect(self._play_sound)
        self.note_grid.note_added_or_edited.connect(self._play_sound)
        self.note_grid.notes_changed.connect(self.notes_updated.emit)
        self.note_grid.seek_requested.connect(self.seek_requested.emit)

        content_layout.addWidget(self.piano_keys)
        content_layout.addWidget(self.note_grid)
        self.scroll_area.setWidget(content_widget)

        layout.addWidget(self.scroll_area)

        # Centrer le défilement vertical vers le milieu (Do4 / C4)
        self.scroll_area.verticalScrollBar().setValue(200)

    def set_playhead(self, beat: float):
        self.note_grid.set_playhead(beat)

    def set_active_track(self, track: Optional[Track]):
        """Lie le Piano Roll et son clavier virtuel à la piste active sélectionnée"""
        self.current_track = track
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

        # Piste MIDI
        if track.clips and isinstance(track.clips[0], MidiClip):
            # Si le clip actuel fait déjà partie des clips de cette piste, on le conserve
            if self.current_clip not in track.clips:
                self.current_clip = track.clips[0]
            self.note_grid.set_clip(self.current_clip)
            self.lbl_title.setText(f"🎹 PIANO ROLL : Piste active [{track.name}] ➔ {self.current_clip.name} — Instrument : {plugin_label}")
        else:
            self.current_clip = None
            self.note_grid.set_clip(None)
            self.lbl_title.setText(f"🎹 PIANO ROLL : Piste active [{track.name}] — Instrument : {plugin_label} (Clavier actif)")

    def open_clip(self, track: Track, clip: MidiClip):
        self.current_track = track
        self.current_clip = clip
        plugin_label = track.plugin_name if track.plugin_name else "Synthé Interne"
        self.lbl_title.setText(f"🎹 PIANO ROLL : {track.name} ➔ {clip.name} — Instrument : {plugin_label}")
        self.note_grid.set_clip(clip)

    def _play_sound(self, pitch: int):
        if self.audio_engine:
            self.audio_engine.preview_note(pitch, duration_sec=0.35, velocity=100, track=self.current_track)

    def _on_snap_changed(self, idx: int):
        snaps = [0.25, 0.5, 1.0, 2.0, 4.0]
        if 0 <= idx < len(snaps):
            self.note_grid.set_snap(snaps[idx])

    def _clear_notes(self):
        if self.current_clip:
            self.current_clip.notes.clear()
            self.note_grid.update()
            self.notes_updated.emit()
