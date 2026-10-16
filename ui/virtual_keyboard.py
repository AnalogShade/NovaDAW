"""
ui/virtual_keyboard.py - Clavier Musical Virtuel (Musical Typing) type Logic Pro / GarageBand / Ableton
Permet de jouer des notes MIDI en temps réel directement avec les touches du clavier d'ordinateur
(lettres A-K, W-O, etc.) avec visualisation en direct, réglage d'octave et de vélocité.
Activé et désactivé par le raccourci global Ctrl+K.
"""
import sys
from typing import Optional, Dict, Set
from PySide6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QSizePolicy, QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox
)
from PySide6.QtCore import Qt, Signal, QObject, QEvent, QRectF
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QFont

from core.audio_engine import AudioEngine
from core.project import Track


# Noms des notes pour affichage
NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def pitch_to_name(pitch: int) -> str:
    octave = (pitch // 12) - 1
    name = NOTE_NAMES[pitch % 12]
    return f"{name}{octave}"


# Mappage des touches du clavier d'ordinateur vers l'offset en demi-tons par rapport à la note de base (C)
# Supporte à la fois les claviers QWERTY et AZERTY (ex: A/Q, W/Z)
KEY_NOTE_MAP: Dict[int, tuple[int, str]] = {
    # Octave inférieure (Home row)
    Qt.Key_A: (0, "A"),          # Do / C
    Qt.Key_Q: (0, "Q/A"),        # Do / C (AZERTY)
    Qt.Key_W: (1, "W"),          # Do# / C#
    Qt.Key_Z: (1, "Z/W"),        # Do# / C# (AZERTY)
    Qt.Key_S: (2, "S"),          # Ré / D
    Qt.Key_E: (3, "E"),          # Ré# / D#
    Qt.Key_D: (4, "D"),          # Mi / E
    Qt.Key_F: (5, "F"),          # Fa / F
    Qt.Key_T: (6, "T"),          # Fa# / F#
    Qt.Key_G: (7, "G"),          # Sol / G
    Qt.Key_Y: (8, "Y"),          # Sol# / G#
    Qt.Key_H: (9, "H"),          # La / A
    Qt.Key_U: (10, "U"),         # La# / A#
    Qt.Key_J: (11, "J"),         # Si / B
    # Octave supérieure
    Qt.Key_K: (12, "K"),         # Do / C (+1 oct)
    Qt.Key_O: (13, "O"),         # Do# / C# (+1 oct)
    Qt.Key_L: (14, "L"),         # Ré / D (+1 oct)
    Qt.Key_P: (15, "P"),         # Ré# / D# (+1 oct)
    Qt.Key_Semicolon: (16, ";"), # Mi / E (+1 oct)
    Qt.Key_M: (16, "M"),         # Mi / E (+1 oct AZERTY)
    Qt.Key_Colon: (16, ":"),
    Qt.Key_Apostrophe: (17, "'"),# Fa / F (+1 oct)
}


class VirtualPianoVisualWidget(QWidget):
    """
    Widget visuel interactif affichant le clavier de piano avec les lettres du clavier d'ordinateur.
    S'illumine en direct lorsqu'une touche est pressée et permet également de cliquer à la souris.
    """
    note_pressed = Signal(int)   # pitch
    note_released = Signal(int)  # pitch

    # Définition des touches affichées (17 touches : 10 blanches, 7 noires)
    # [(semitone_offset, is_black, key_label)]
    WHITE_KEYS_DEF = [
        (0, "A"),   # C
        (2, "S"),   # D
        (4, "D"),   # E
        (5, "F"),   # F
        (7, "G"),   # G
        (9, "H"),   # A
        (11, "J"),  # B
        (12, "K"),  # C+1
        (14, "L"),  # D+1
        (16, ";/M"),# E+1
    ]

    BLACK_KEYS_DEF = [
        (1, "W", 0),    # C# (après touche blanche index 0)
        (3, "E", 1),    # D# (après touche blanche index 1)
        (6, "T", 3),    # F# (après touche blanche index 3)
        (8, "Y", 4),    # G# (après touche blanche index 4)
        (10, "U", 5),   # A# (après touche blanche index 5)
        (13, "O", 7),   # C#+1 (après touche blanche index 7)
        (15, "P", 8),   # D#+1 (après touche blanche index 8)
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.base_pitch = 48  # C3 par défaut
        self.active_pitches: Set[int] = set()
        self.mouse_active_pitch: Optional[int] = None
        self.setFixedHeight(140)
        self.setMinimumWidth(560)
        self.setMouseTracking(True)

    def set_base_pitch(self, pitch: int):
        self.base_pitch = int(pitch)
        self.update()

    def set_pitch_active(self, pitch: int, is_active: bool):
        if is_active:
            self.active_pitches.add(pitch)
        else:
            self.active_pitches.discard(pitch)
        self.update()

    def clear_active_pitches(self):
        self.active_pitches.clear()
        self.update()

    def _get_key_geometries(self):
        w = self.width()
        h = self.height()
        num_white = len(self.WHITE_KEYS_DEF)
        white_w = w / num_white
        black_w = white_w * 0.62
        black_h = h * 0.60

        white_geoms = []
        for i, (offset, key_lbl) in enumerate(self.WHITE_KEYS_DEF):
            rx = i * white_w
            rect = QRectF(rx, 0, white_w, h)
            white_geoms.append((self.base_pitch + offset, key_lbl, rect))

        black_geoms = []
        for offset, key_lbl, w_idx in self.BLACK_KEYS_DEF:
            center_x = (w_idx + 1) * white_w
            rx = center_x - (black_w / 2.0)
            rect = QRectF(rx, 0, black_w, black_h)
            black_geoms.append((self.base_pitch + offset, key_lbl, rect))

        return white_geoms, black_geoms

    def _pitch_at_pos(self, x: float, y: float) -> Optional[int]:
        white_geoms, black_geoms = self._get_key_geometries()
        # Vérifier d'abord les touches noires (qui sont au premier plan)
        for pitch, _, rect in black_geoms:
            if rect.contains(x, y):
                return pitch
        for pitch, _, rect in white_geoms:
            if rect.contains(x, y):
                return pitch
        return None

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            p = self._pitch_at_pos(event.position().x(), event.position().y())
            if p is not None:
                self.mouse_active_pitch = p
                self.set_pitch_active(p, True)
                self.note_pressed.emit(p)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton:
            p = self._pitch_at_pos(event.position().x(), event.position().y())
            if p != self.mouse_active_pitch:
                if self.mouse_active_pitch is not None:
                    self.set_pitch_active(self.mouse_active_pitch, False)
                    self.note_released.emit(self.mouse_active_pitch)
                self.mouse_active_pitch = p
                if p is not None:
                    self.set_pitch_active(p, True)
                    self.note_pressed.emit(p)

    def mouseReleaseEvent(self, event):
        if self.mouse_active_pitch is not None:
            p = self.mouse_active_pitch
            self.mouse_active_pitch = None
            self.set_pitch_active(p, False)
            self.note_released.emit(p)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        white_geoms, black_geoms = self._get_key_geometries()

        # 1. Dessin des touches blanches
        for pitch, key_lbl, rect in white_geoms:
            is_active = pitch in self.active_pitches
            if is_active:
                bg = QColor("#38bdf8")
                border = QColor("#0284c7")
                txt_main = QColor("#082f49")
                txt_note = QColor("#ffffff")
            else:
                bg = QColor("#f1f5f9")
                border = QColor("#cbd5e1")
                txt_main = QColor("#0f172a")
                txt_note = QColor("#64748b")

            painter.setBrush(QBrush(bg))
            painter.setPen(QPen(border, 1.5))
            painter.drawRoundedRect(rect.adjusted(1, 0, -1, -1), 4, 4)

            # Lettre du clavier d'ordinateur (au bas de la touche)
            painter.setFont(QFont("Segoe UI", 11, QFont.Bold))
            painter.setPen(txt_main)
            painter.drawText(
                QRectF(rect.x(), rect.bottom() - 44, rect.width(), 22),
                Qt.AlignCenter,
                key_lbl
            )

            # Nom de la note musicale (ex: C3, D3)
            painter.setFont(QFont("Segoe UI", 8, QFont.Bold))
            painter.setPen(txt_note)
            painter.drawText(
                QRectF(rect.x(), rect.bottom() - 20, rect.width(), 16),
                Qt.AlignCenter,
                pitch_to_name(pitch)
            )

        # 2. Dessin des touches noires (au premier plan)
        for pitch, key_lbl, rect in black_geoms:
            is_active = pitch in self.active_pitches
            if is_active:
                bg = QColor("#00f0ff")
                border = QColor("#38bdf8")
                txt_main = QColor("#082f49")
                txt_note = QColor("#082f49")
            else:
                bg = QColor("#1e2230")
                border = QColor("#0f1118")
                txt_main = QColor("#38bdf8")
                txt_note = QColor("#94a3b8")

            painter.setBrush(QBrush(bg))
            painter.setPen(QPen(border, 1.2))
            painter.drawRoundedRect(rect, 3, 3)

            # Lettre du clavier d'ordinateur (au milieu bas de la touche noire)
            painter.setFont(QFont("Segoe UI", 9, QFont.Bold))
            painter.setPen(txt_main)
            painter.drawText(
                QRectF(rect.x(), rect.bottom() - 32, rect.width(), 16),
                Qt.AlignCenter,
                key_lbl
            )

            # Nom de la note (ex: C#3)
            painter.setFont(QFont("Segoe UI", 7, QFont.Bold))
            painter.setPen(txt_note)
            painter.drawText(
                QRectF(rect.x(), rect.bottom() - 15, rect.width(), 14),
                Qt.AlignCenter,
                pitch_to_name(pitch)
            )


class MusicalTypingEventFilter(QObject):
    """
    Filtre d'événements global pour capturer les frappes du clavier d'ordinateur
    lorsque le Clavier Musical Virtuel est actif, tout en laissant passer les saisies
    dans les champs texte (QLineEdit, etc.) et les raccourcis système (Ctrl+K).
    """
    def __init__(self, dialog: 'VirtualKeyboardDialog'):
        super().__init__()
        self.dialog = dialog
        self.active = False

    def is_musical_typing_key(self, key_code: int) -> bool:
        """Indique si la touche est une touche de note musicale ou de contrôle (octave, vélocité)."""
        if key_code in KEY_NOTE_MAP:
            return True
        if key_code in (
            Qt.Key_Z, Qt.Key_X, Qt.Key_C, Qt.Key_V,
            Qt.Key_BracketLeft, Qt.Key_BracketRight,
            Qt.Key_Up, Qt.Key_Down
        ):
            return True
        return False

    def eventFilter(self, watched, event):
        try:
            if not self.active or self.dialog is None or not self.dialog.isVisible():
                return False
        except (RuntimeError, ReferenceError):
            return False

        # Ne pas intercepter la saisie si l'utilisateur est dans un champ texte éditable
        from PySide6.QtWidgets import QApplication
        focus_w = QApplication.focusWidget()
        if isinstance(watched, (QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox)) or \
           isinstance(focus_w, (QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox)):
            return False

        evt_type = event.type()
        if evt_type == QEvent.ShortcutOverride:
            # Laisser passer les combinaisons avec Ctrl ou Alt (ex: Ctrl+K pour basculer le mode)
            if event.modifiers() & (Qt.ControlModifier | Qt.AltModifier):
                return False
            # Si c'est une touche de note ou de contrôle musical, intercepter l'override pour que Qt
            # n'exécute pas les raccourcis d'action (comme 'J' pour Snap, 'F' pour Follow, etc.)
            if self.is_musical_typing_key(event.key()):
                event.accept()
                return True
            return False

        elif evt_type == QEvent.KeyPress:
            # Laisser passer les combinaisons avec Ctrl ou Alt (ex: Ctrl+K pour désactiver)
            if event.modifiers() & (Qt.ControlModifier | Qt.AltModifier):
                return False
            # Ignorer la répétition automatique des touches sous Windows
            if event.isAutoRepeat():
                return True
            return self.dialog.handle_key_press(event.key())

        elif evt_type == QEvent.KeyRelease:
            if event.modifiers() & (Qt.ControlModifier | Qt.AltModifier):
                return False
            if event.isAutoRepeat():
                return True
            return self.dialog.handle_key_release(event.key())

        return False


class VirtualKeyboardDialog(QDialog):
    """
    Fenêtre flottante non-bloquante du Clavier Musical Virtuel (Musical Typing).
    Reste au premier plan, affiche la correspondance exacte des touches et permet
    de jouer en direct avec le clavier de l'ordinateur.
    """
    visibility_toggled = Signal(bool)

    def __init__(self, audio_engine: AudioEngine, parent=None):
        super().__init__(parent)
        self.audio_engine = audio_engine
        self.current_track: Optional[Track] = None

        self.base_pitch = 48  # C3 par défaut
        self.velocity = 100   # Vélocité par défaut
        self.held_keys_pitches: Dict[int, int] = {}  # key_code -> pitch

        self.setWindowTitle("🎹 Clavier Musical Virtuel (Musical Typing) — [Ctrl+K]")
        self.setWindowFlags(Qt.Tool | Qt.WindowStaysOnTopHint | Qt.WindowTitleHint | Qt.WindowCloseButtonHint)
        self.resize(680, 240)
        self.setMinimumSize(580, 220)

        self.setStyleSheet("""
            QDialog {
                background-color: #12141c;
                border: 1px solid #232736;
                border-radius: 8px;
            }
            QLabel {
                color: #e2e8f0;
                font-family: 'Segoe UI', sans-serif;
            }
            QPushButton {
                background-color: #1e2230;
                color: #e2e8f0;
                border: 1px solid #333a4e;
                border-radius: 4px;
                padding: 4px 10px;
                font-size: 11px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #282e42;
                border-color: #38bdf8;
                color: #ffffff;
            }
            QPushButton#btn_accent {
                background-color: #0284c7;
                color: #ffffff;
                border-color: #38bdf8;
            }
            QPushButton#btn_accent:hover {
                background-color: #0369a1;
            }
        """)

        self._init_ui()

        # Filtre d'événement global (activé dynamiquement lors de l'affichage)
        self.event_filter = MusicalTypingEventFilter(self)
        self._filter_installed = False

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        # 1. Barre supérieure : Statut & Info Piste
        top_bar = QHBoxLayout()
        top_bar.setSpacing(10)

        self.lbl_title = QLabel("🎹 CLAVIER MUSICAL VIRTUEL")
        self.lbl_title.setStyleSheet("font-size: 13px; font-weight: bold; color: #38bdf8; letter-spacing: 0.5px;")
        top_bar.addWidget(self.lbl_title)

        self.lbl_track = QLabel("Piste : Aucune piste sélectionnée")
        self.lbl_track.setStyleSheet("font-size: 11px; color: #94a3b8; font-weight: bold;")
        top_bar.addWidget(self.lbl_track, stretch=1)

        self.lbl_hint = QLabel("💡 Astuce : [Ctrl+K] pour activer/masquer")
        self.lbl_hint.setStyleSheet("font-size: 10px; color: #64748b; font-style: italic;")
        top_bar.addWidget(self.lbl_hint)

        layout.addLayout(top_bar)

        # 2. Clavier visuel interactif
        self.piano_widget = VirtualPianoVisualWidget(self)
        self.piano_widget.set_base_pitch(self.base_pitch)
        self.piano_widget.note_pressed.connect(self._play_note)
        self.piano_widget.note_released.connect(self._stop_note)
        layout.addWidget(self.piano_widget)

        # 3. Barre inférieure : Commandes d'Octave & Vélocité
        bottom_bar = QHBoxLayout()
        bottom_bar.setSpacing(8)

        # Boutons Octave
        lbl_oct = QLabel("Octave :")
        lbl_oct.setStyleSheet("font-size: 11px; color: #94a3b8;")
        bottom_bar.addWidget(lbl_oct)

        self.btn_oct_down = QPushButton("◀ Octave - [Z]")
        self.btn_oct_down.clicked.connect(self.octave_down)
        bottom_bar.addWidget(self.btn_oct_down)

        self.lbl_oct_val = QLabel(f"C{self.base_pitch // 12 - 1} ({self.base_pitch})")
        self.lbl_oct_val.setStyleSheet("font-size: 12px; font-weight: bold; color: #00f0ff; min-width: 65px; text-align: center;")
        bottom_bar.addWidget(self.lbl_oct_val)

        self.btn_oct_up = QPushButton("Octave + [X] ▶")
        self.btn_oct_up.clicked.connect(self.octave_up)
        bottom_bar.addWidget(self.btn_oct_up)

        bottom_bar.addSpacing(16)

        # Boutons Vélocité
        lbl_vel = QLabel("Vélocité :")
        lbl_vel.setStyleSheet("font-size: 11px; color: #94a3b8;")
        bottom_bar.addWidget(lbl_vel)

        self.btn_vel_down = QPushButton("◀ Vélocité - [C]")
        self.btn_vel_down.clicked.connect(self.velocity_down)
        bottom_bar.addWidget(self.btn_vel_down)

        self.lbl_vel_val = QLabel(str(self.velocity))
        self.lbl_vel_val.setStyleSheet("font-size: 12px; font-weight: bold; color: #f59e0b; min-width: 35px; text-align: center;")
        bottom_bar.addWidget(self.lbl_vel_val)

        self.btn_vel_up = QPushButton("Vélocité + [V] ▶")
        self.btn_vel_up.clicked.connect(self.velocity_up)
        bottom_bar.addWidget(self.btn_vel_up)

        bottom_bar.addStretch()

        self.btn_close = QPushButton("✕ Fermer [Ctrl+K]")
        self.btn_close.clicked.connect(self.hide)
        bottom_bar.addWidget(self.btn_close)

        layout.addLayout(bottom_bar)

    def set_active_track(self, track: Optional[Track]):
        self.current_track = track
        if track:
            plugin_name = track.plugin_name or ("Synthé Interne" if track.track_type == "midi" else "Piste Audio")
            self.lbl_track.setText(f"Piste : [{track.name}] ➔ {plugin_name} ✅")
            self.lbl_track.setStyleSheet("font-size: 11px; color: #38bdf8; font-weight: bold;")
        else:
            self.lbl_track.setText("Piste : Aucune piste sélectionnée")
            self.lbl_track.setStyleSheet("font-size: 11px; color: #94a3b8; font-weight: bold;")

    def octave_down(self):
        if self.base_pitch > 12:
            self._release_all_held_notes()
            self.base_pitch -= 12
            self.piano_widget.set_base_pitch(self.base_pitch)
            self.lbl_oct_val.setText(f"C{self.base_pitch // 12 - 1} ({self.base_pitch})")

    def octave_up(self):
        if self.base_pitch < 96:
            self._release_all_held_notes()
            self.base_pitch += 12
            self.piano_widget.set_base_pitch(self.base_pitch)
            self.lbl_oct_val.setText(f"C{self.base_pitch // 12 - 1} ({self.base_pitch})")

    def velocity_down(self):
        self.velocity = max(20, self.velocity - 15)
        self.lbl_vel_val.setText(str(self.velocity))

    def velocity_up(self):
        self.velocity = min(127, self.velocity + 15)
        self.lbl_vel_val.setText(str(self.velocity))

    def _play_note(self, pitch: int):
        if self.audio_engine:
            self.audio_engine.start_live_note(pitch, velocity=self.velocity, track=self.current_track)

    def _stop_note(self, pitch: int):
        if self.audio_engine:
            self.audio_engine.stop_live_note(pitch, track=self.current_track)

    def _release_all_held_notes(self):
        for key, pitch in list(self.held_keys_pitches.items()):
            self._stop_note(pitch)
            self.piano_widget.set_pitch_active(pitch, False)
        self.held_keys_pitches.clear()
        self.piano_widget.clear_active_pitches()

    def handle_key_press(self, key_code: int) -> bool:
        # Contrôle d'octave
        if key_code == Qt.Key_Z or key_code == Qt.Key_BracketLeft or key_code == Qt.Key_Down:
            self.octave_down()
            return True
        elif key_code == Qt.Key_X or key_code == Qt.Key_BracketRight or key_code == Qt.Key_Up:
            self.octave_up()
            return True
        # Contrôle de vélocité
        elif key_code == Qt.Key_C:
            self.velocity_down()
            return True
        elif key_code == Qt.Key_V:
            self.velocity_up()
            return True

        # Notes musicales
        if key_code in KEY_NOTE_MAP:
            offset, _ = KEY_NOTE_MAP[key_code]
            pitch = self.base_pitch + offset
            if key_code not in self.held_keys_pitches:
                self.held_keys_pitches[key_code] = pitch
                self.piano_widget.set_pitch_active(pitch, True)
                self._play_note(pitch)
            return True

        return False

    def handle_key_release(self, key_code: int) -> bool:
        if key_code in self.held_keys_pitches:
            pitch = self.held_keys_pitches.pop(key_code)
            self.piano_widget.set_pitch_active(pitch, False)
            self._stop_note(pitch)
            return True
        return False

    def showEvent(self, event):
        super().showEvent(event)
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app and not self._filter_installed:
            app.installEventFilter(self.event_filter)
            self._filter_installed = True
        self.event_filter.active = True
        self.visibility_toggled.emit(True)

    def hideEvent(self, event):
        self._release_all_held_notes()
        self.event_filter.active = False
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app and self._filter_installed:
            try:
                app.removeEventFilter(self.event_filter)
            except Exception:
                pass
            self._filter_installed = False
        self.visibility_toggled.emit(False)
        super().hideEvent(event)

    def closeEvent(self, event):
        self._release_all_held_notes()
        self.event_filter.active = False
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app and self._filter_installed:
            try:
                app.removeEventFilter(self.event_filter)
            except Exception:
                pass
            self._filter_installed = False
        self.visibility_toggled.emit(False)
        super().closeEvent(event)
