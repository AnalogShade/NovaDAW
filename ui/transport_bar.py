"""
ui/transport_bar.py - Barre de transport inférieure et centrée avec navigation complète
"""
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QPushButton, QLabel, QDoubleSpinBox,
    QSlider, QFrame, QSpacerItem, QSizePolicy
)
from PySide6.QtCore import Qt, Signal, QTimer


class TransportBar(QWidget):
    play_toggled = Signal(bool)
    stop_clicked = Signal()
    record_toggled = Signal(bool)
    loop_toggled = Signal(bool)
    bpm_changed = Signal(float)
    master_volume_changed = Signal(float)
    goto_start_clicked = Signal()
    goto_end_clicked = Signal()
    step_rewind = Signal(float)    # Déplacement en beats (négatif)
    step_forward = Signal(float)   # Déplacement en beats (positif)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(50)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("""
            QWidget {
                background-color: #171822;
                border-top: 1px solid #282a38;
                border-bottom: 1px solid #282a38;
            }
        """)

        # Timers pour le maintien enfoncé de Reculer / Avancer
        self._rewind_timer = QTimer(self)
        self._rewind_timer.setInterval(70)
        self._rewind_timer.timeout.connect(lambda: self.step_rewind.emit(-1.0))

        self._forward_timer = QTimer(self)
        self._forward_timer.setInterval(70)
        self._forward_timer.timeout.connect(lambda: self.step_forward.emit(1.0))

        self._init_ui()

    def _init_ui(self):
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(16, 4, 16, 4)
        main_layout.setSpacing(12)

        # Centrage : Stretch à gauche
        main_layout.addStretch(1)

        # --- GROUPE NAVIGATION (Aller au début, Reculer, Stop, Play, Avancer, Aller à la fin) ---
        nav_container = QWidget()
        nav_layout = QHBoxLayout(nav_container)
        nav_layout.setContentsMargins(0, 0, 0, 0)
        nav_layout.setSpacing(5)

        # 1. Aller au tout début
        self.btn_start = QPushButton("⏮")
        self.btn_start.setObjectName("btn_nav")
        self.btn_start.setFixedSize(34, 32)
        self.btn_start.setToolTip("Aller au tout début du morceau (Mesure 1)")
        self.btn_start.clicked.connect(self.goto_start_clicked.emit)
        nav_layout.addWidget(self.btn_start)

        # 2. Reculer (Rewind) avec maintien
        self.btn_rewind = QPushButton("⏪")
        self.btn_rewind.setObjectName("btn_nav")
        self.btn_rewind.setFixedSize(34, 32)
        self.btn_rewind.setToolTip("Reculer d'une mesure (Maintenir pour rembobiner)")
        self.btn_rewind.pressed.connect(self._start_rewind)
        self.btn_rewind.released.connect(self._stop_rewind)
        nav_layout.addWidget(self.btn_rewind)

        # 3. Stop
        self.btn_stop = QPushButton("■")
        self.btn_stop.setObjectName("btn_stop")
        self.btn_stop.setFixedSize(36, 32)
        self.btn_stop.setToolTip("Stop / Arrêt (Espace ou 0)")
        self.btn_stop.clicked.connect(self._on_stop)
        nav_layout.addWidget(self.btn_stop)

        # 4. Play / Pause
        self.btn_play = QPushButton("▶")
        self.btn_play.setObjectName("btn_play")
        self.btn_play.setCheckable(True)
        self.btn_play.setFixedSize(48, 32)
        self.btn_play.setToolTip("Lecture / Pause (Espace)")
        self.btn_play.clicked.connect(self._on_play)
        nav_layout.addWidget(self.btn_play)

        # 5. Avancer (Fast-Forward) avec maintien
        self.btn_forward = QPushButton("⏩")
        self.btn_forward.setObjectName("btn_nav")
        self.btn_forward.setFixedSize(34, 32)
        self.btn_forward.setToolTip("Avancer d'une mesure (Maintenir pour avancer)")
        self.btn_forward.pressed.connect(self._start_forward)
        self.btn_forward.released.connect(self._stop_forward)
        nav_layout.addWidget(self.btn_forward)

        # 6. Aller à la toute fin
        self.btn_end = QPushButton("⏭")
        self.btn_end.setObjectName("btn_nav")
        self.btn_end.setFixedSize(34, 32)
        self.btn_end.setToolTip("Aller à la fin du morceau (après le dernier bloc)")
        self.btn_end.clicked.connect(self.goto_end_clicked.emit)
        nav_layout.addWidget(self.btn_end)

        # 7. Record
        self.btn_record = QPushButton("●")
        self.btn_record.setObjectName("btn_record")
        self.btn_record.setCheckable(True)
        self.btn_record.setFixedSize(34, 32)
        self.btn_record.setToolTip("Armer l'enregistrement audio")
        self.btn_record.clicked.connect(self.record_toggled.emit)
        nav_layout.addWidget(self.btn_record)

        # 8. Boucle
        self.btn_loop = QPushButton("🔁 Boucle")
        self.btn_loop.setObjectName("btn_loop")
        self.btn_loop.setCheckable(True)
        self.btn_loop.setChecked(True)
        self.btn_loop.setFixedHeight(32)
        self.btn_loop.setToolTip("Activer / Désactiver la lecture en boucle (Raccourci: L)")
        self.btn_loop.clicked.connect(self.loop_toggled.emit)
        nav_layout.addWidget(self.btn_loop)

        main_layout.addWidget(nav_container)

        self._add_separator(main_layout)

        # --- COMPTEURS DIGITAUX ---
        time_container = QWidget()
        time_layout = QHBoxLayout(time_container)
        time_layout.setContentsMargins(0, 0, 0, 0)
        time_layout.setSpacing(6)

        lbl_bars = QLabel("MESURE")
        lbl_bars.setStyleSheet("font-size: 8px; font-weight: bold; color: #64748b;")
        time_layout.addWidget(lbl_bars)

        self.lbl_bars_display = QLabel("001 . 01 . 00")
        self.lbl_bars_display.setObjectName("digital_display")
        time_layout.addWidget(self.lbl_bars_display)

        self.lbl_time_display = QLabel("00:00:00.00")
        self.lbl_time_display.setObjectName("digital_time")
        time_layout.addWidget(self.lbl_time_display)

        main_layout.addWidget(time_container)

        self._add_separator(main_layout)

        # --- TEMPO BPM & SIGNATURE ---
        bpm_container = QWidget()
        bpm_layout = QHBoxLayout(bpm_container)
        bpm_layout.setContentsMargins(0, 0, 0, 0)
        bpm_layout.setSpacing(6)

        lbl_bpm = QLabel("BPM")
        lbl_bpm.setStyleSheet("font-size: 9px; font-weight: bold; color: #64748b;")
        bpm_layout.addWidget(lbl_bpm)

        self.spin_bpm = QDoubleSpinBox()
        self.spin_bpm.setRange(20.0, 300.0)
        self.spin_bpm.setValue(120.0)
        self.spin_bpm.setDecimals(1)
        self.spin_bpm.setFixedWidth(74)
        self.spin_bpm.setFixedHeight(30)
        self.spin_bpm.valueChanged.connect(self.bpm_changed.emit)
        bpm_layout.addWidget(self.spin_bpm)

        lbl_sig = QLabel("4 / 4")
        lbl_sig.setStyleSheet("font-family: Consolas; font-weight: bold; color: #cbd5e1; padding: 2px 4px;")
        bpm_layout.addWidget(lbl_sig)

        main_layout.addWidget(bpm_container)

        self._add_separator(main_layout)

        # --- VOLUME MASTER ---
        vol_container = QWidget()
        vol_layout = QHBoxLayout(vol_container)
        vol_layout.setContentsMargins(0, 0, 0, 0)
        vol_layout.setSpacing(6)

        lbl_vol = QLabel("🔊 MASTER")
        lbl_vol.setStyleSheet("font-size: 9px; font-weight: bold; color: #94a3b8;")
        vol_layout.addWidget(lbl_vol)

        self.slider_master = QSlider(Qt.Horizontal)
        self.slider_master.setRange(0, 120)
        self.slider_master.setValue(90)
        self.slider_master.setFixedWidth(90)
        self.slider_master.valueChanged.connect(lambda val: self.master_volume_changed.emit(val / 100.0))
        vol_layout.addWidget(self.slider_master)

        main_layout.addWidget(vol_container)

        # Centrage : Stretch à droite
        main_layout.addStretch(1)

    def _add_separator(self, layout):
        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setStyleSheet("color: #2b2e3b; margin: 4px 2px;")
        layout.addWidget(sep)

    def _start_rewind(self):
        # 1 pas immédiat d'une mesure (4 temps)
        self.step_rewind.emit(-4.0)
        # Démarre le défilement continu rapide si maintenu
        self._rewind_timer.start()

    def _stop_rewind(self):
        self._rewind_timer.stop()

    def _start_forward(self):
        # 1 pas immédiat d'une mesure (4 temps)
        self.step_forward.emit(4.0)
        # Démarre le défilement continu rapide si maintenu
        self._forward_timer.start()

    def _stop_forward(self):
        self._forward_timer.stop()

    def _on_play(self, checked: bool):
        self.play_toggled.emit(checked)

    def _on_stop(self):
        self.btn_play.setChecked(False)
        self.stop_clicked.emit()

    def update_position(self, beat: float, bpm: float):
        bar = int(beat // 4) + 1
        beat_in_bar = int(beat % 4) + 1
        sub_beat = int((beat % 1) * 100)
        self.lbl_bars_display.setText(f"{bar:03d} . {beat_in_bar:02d} . {sub_beat:02d}")

        sec = beat * (60.0 / bpm)
        mins = int(sec // 60)
        secs = int(sec % 60)
        millis = int((sec % 1) * 100)
        self.lbl_time_display.setText(f"00:{mins:02d}:{secs:02d}.{millis:02d}")

    def set_playing_state(self, is_playing: bool):
        self.btn_play.setChecked(is_playing)
