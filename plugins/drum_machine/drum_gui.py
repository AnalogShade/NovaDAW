"""
plugins/drum_machine/drum_gui.py - Interface graphique moderne pour le plugin Nova Drums VSTi.
Design inspiré de Native Instruments Battery et Cubase Groove Agent.
"""
from typing import Optional, Dict
import numpy as np
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QPushButton, QSlider, QComboBox, QFrame, QSizePolicy,
    QGroupBox, QCheckBox
)
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QFont, QLinearGradient

from plugins.drum_machine.drum_plugin import DrumMachinePlugin, DrumPad


class WaveformDisplay(QWidget):
    """Affiche la forme d'onde audio du pad sélectionné"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(70)
        self.audio_data: Optional[np.ndarray] = None
        self.setStyleSheet("background-color: #0b0d14; border-radius: 4px; border: 1px solid #1e2230;")

    def set_audio_data(self, data):
        self.audio_data = data
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        h = self.height()
        mid_y = h / 2.0

        # Fond
        painter.fillRect(0, 0, w, h, QColor("#0c0e17"))

        # Ligne médiane
        painter.setPen(QPen(QColor("#1e2436"), 1, Qt.DashLine))
        painter.drawLine(0, int(mid_y), w, int(mid_y))

        if self.audio_data is None or len(self.audio_data) == 0:
            painter.setPen(QColor("#475569"))
            painter.drawText(self.rect(), Qt.AlignCenter, "Aucun échantillon audio")
            return

        # Tracé de la forme d'onde
        step = max(1, len(self.audio_data) // w)
        mono = self.audio_data[:, 0] if self.audio_data.ndim > 1 else self.audio_data
        
        pen = QPen(QColor("#38bdf8"), 1.5)
        painter.setPen(pen)

        for x in range(w):
            idx = x * step
            if idx >= len(mono):
                break
            chunk = mono[idx:idx + step]
            if len(chunk) == 0:
                continue
            max_v = float(np.max(chunk))
            min_v = float(np.min(chunk))
            y_top = mid_y - (max_v * (mid_y * 0.9))
            y_bot = mid_y - (min_v * (mid_y * 0.9))
            painter.drawLine(x, int(y_top), x, int(y_bot))


class PadButton(QPushButton):
    """Bouton de pad interactif avec LED d'animation lors des frappes"""
    def __init__(self, pad: DrumPad, parent=None):
        super().__init__(parent)
        self.pad = pad
        self.is_flashing = False
        self.setMinimumSize(85, 75)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setCursor(Qt.PointingHandCursor)

        self.flash_timer = QTimer(self)
        self.flash_timer.setSingleShot(True)
        self.flash_timer.setInterval(120)
        self.flash_timer.timeout.connect(self._stop_flash)

        self._update_style()

    def flash(self):
        self.is_flashing = True
        self._update_style()
        self.flash_timer.start()

    def _stop_flash(self):
        self.is_flashing = False
        self._update_style()

    def _update_style(self):
        if self.is_flashing:
            border = "#38bdf8"
            bg = "#0369a1"
            txt = "#ffffff"
            glow = "0 0 10px #38bdf8"
        elif self.pad.muted:
            border = "#ef4444"
            bg = "#1f1418"
            txt = "#94a3b8"
            glow = "none"
        elif self.pad.soloed:
            border = "#eab308"
            bg = "#292212"
            txt = "#fef08a"
            glow = "none"
        else:
            border = "#2b3245"
            bg = "#161924"
            txt = "#f1f5f9"
            glow = "none"

        note_str = f"GM {self.pad.midi_pitches[0]}" if self.pad.midi_pitches else ""

        self.setStyleSheet(f"""
            QPushButton {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {bg}, stop:1 #10121b);
                color: {txt};
                border: 2px solid {border};
                border-radius: 6px;
                padding: 4px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                border-color: #38bdf8;
                background: #1e2436;
            }}
        """)
        self.setText(f"{self.pad.name}\n({note_str})")


class DrumMachineWidget(QWidget):
    """Interface graphique principale pour Nova Drums VSTi"""
    def __init__(self, plugin: DrumMachinePlugin, parent=None):
        super().__init__(parent)
        self.plugin = plugin
        self.selected_pad = self.plugin.pads[0] if self.plugin.pads else None

        self.setWindowTitle(f"{self.plugin.name} - Studio Drum Sampler")
        self.resize(780, 620)
        self.setStyleSheet("""
            QWidget {
                background-color: #0e1017;
                color: #e2e8f0;
                font-family: 'Segoe UI', sans-serif;
            }
            QLabel {
                font-size: 11px;
                color: #94a3b8;
            }
            QLabel#title {
                font-size: 16px;
                font-weight: bold;
                color: #38bdf8;
                letter-spacing: 1px;
            }
            QLabel#subtitle {
                font-size: 10px;
                color: #64748b;
            }
            QGroupBox {
                border: 1px solid #232738;
                border-radius: 6px;
                margin-top: 14px;
                padding-top: 10px;
                background-color: #131622;
                font-weight: bold;
                font-size: 11px;
                color: #38bdf8;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
            }
            QComboBox {
                background-color: #1a1e2b;
                color: #f1f5f9;
                border: 1px solid #2d3748;
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 11px;
            }
            QComboBox QAbstractItemView {
                background-color: #1a1e2b;
                color: #f1f5f9;
                selection-background-color: #0284c7;
            }
            QSlider::groove:horizontal {
                height: 4px;
                background: #232738;
                border-radius: 2px;
            }
            QSlider::sub-page:horizontal {
                background: #38bdf8;
                border-radius: 2px;
            }
            QSlider::handle:horizontal {
                background: #ffffff;
                width: 12px;
                margin: -4px 0;
                border-radius: 6px;
            }
            QPushButton#btn_action {
                background-color: #1a2233;
                color: #38bdf8;
                border: 1px solid #0284c7;
                border-radius: 4px;
                padding: 4px 10px;
                font-weight: bold;
                font-size: 11px;
            }
            QPushButton#btn_action:hover {
                background-color: #0284c7;
                color: #ffffff;
            }
        """)

        self._init_ui()
        self._sync_all_controls()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(14, 14, 14, 14)
        main_layout.setSpacing(12)

        # 1. En-tête Header
        header = QHBoxLayout()
        v_titles = QVBoxLayout()
        lbl_t = QLabel("🥁 NOVA DRUMS VSTi")
        lbl_t.setObjectName("title")
        lbl_sub = QLabel("Échantillonneur de Percussions Haute Fidélité FP32 • Réverbération Intégrée")
        lbl_sub.setObjectName("subtitle")
        v_titles.addWidget(lbl_t)
        v_titles.addWidget(lbl_sub)
        header.addLayout(v_titles)

        header.addStretch()

        # Sélecteur de preset
        header.addWidget(QLabel("Preset :"))
        self.combo_preset = QComboBox()
        self.combo_preset.addItems([
            "Studio Acoustic",
            "Punchy Rock",
            "Trap / Modern",
            "Big Hall Ambience",
            "Tight & Dry"
        ])
        self.combo_preset.setCurrentText(self.plugin.preset_name)
        self.combo_preset.currentTextChanged.connect(self._on_preset_changed)
        header.addWidget(self.combo_preset)

        # Volume Master
        header.addSpacing(16)
        header.addWidget(QLabel("Master :"))
        self.slider_master = QSlider(Qt.Horizontal)
        self.slider_master.setRange(0, 150)
        self.slider_master.setValue(int(self.plugin.master_volume * 100))
        self.slider_master.setFixedWidth(80)
        self.slider_master.valueChanged.connect(self._on_master_vol_changed)
        header.addWidget(self.slider_master)
        self.lbl_master_val = QLabel(f"{int(self.plugin.master_volume * 100)}%")
        header.addWidget(self.lbl_master_val)

        main_layout.addLayout(header)

        # 2. Centre : Grille des Pads (gauche) + Détails Pad (droite)
        center_row = QHBoxLayout()
        center_row.setSpacing(12)

        # Grille 3x3 de Pads (disposition finger drumming classique)
        group_pads = QGroupBox("PADS DE BATTERIE GM")
        grid_layout = QGridLayout(group_pads)
        grid_layout.setSpacing(8)

        self.pad_buttons: Dict[str, PadButton] = {}

        # Ordre visuel ergonomique :
        # Ligne 1 : High Tom, Crash, Ride
        # Ligne 2 : Low Tom, Mid Tom, Open HH
        # Ligne 3 : Kick, Snare, Closed HH
        matrix_order = [
            ("tom_h", 0, 0), ("crash", 0, 1), ("ride", 0, 2),
            ("tom_l", 1, 0), ("tom_m", 1, 1), ("hihat_o", 1, 2),
            ("kick", 2, 0),  ("snare", 2, 1),  ("hihat_c", 2, 2)
        ]

        pad_lookup = {p.pad_id: p for p in self.plugin.pads}
        for pad_id, r, c in matrix_order:
            if pad_id in pad_lookup:
                pad = pad_lookup[pad_id]
                btn = PadButton(pad)
                btn.clicked.connect(lambda _, p=pad, b=btn: self._on_pad_clicked(p, b))
                self.pad_buttons[pad_id] = btn
                grid_layout.addWidget(btn, r, c)

        center_row.addWidget(group_pads, stretch=5)

        # Panneau de réglages du pad sélectionné
        group_detail = QGroupBox("RÉGLAGES DU PAD SÉLECTIONNÉ")
        detail_layout = QVBoxLayout(group_detail)
        detail_layout.setSpacing(8)

        self.lbl_sel_name = QLabel("Pad : Kick (C2)")
        self.lbl_sel_name.setStyleSheet("font-weight: bold; font-size: 13px; color: #38bdf8;")
        detail_layout.addWidget(self.lbl_sel_name)

        # Waveform
        self.waveform_view = WaveformDisplay()
        detail_layout.addWidget(self.waveform_view)

        # Slider Gain du pad
        row_g = QHBoxLayout()
        row_g.addWidget(QLabel("Volume"))
        self.slider_pad_vol = QSlider(Qt.Horizontal)
        self.slider_pad_vol.setRange(0, 200)
        self.slider_pad_vol.valueChanged.connect(self._on_pad_vol_changed)
        row_g.addWidget(self.slider_pad_vol)
        self.lbl_pad_vol = QLabel("100%")
        self.lbl_pad_vol.setFixedWidth(36)
        row_g.addWidget(self.lbl_pad_vol)
        detail_layout.addLayout(row_g)

        # Slider Panoramique
        row_p = QHBoxLayout()
        row_p.addWidget(QLabel("Pan"))
        self.slider_pad_pan = QSlider(Qt.Horizontal)
        self.slider_pad_pan.setRange(-100, 100)
        self.slider_pad_pan.valueChanged.connect(self._on_pad_pan_changed)
        row_p.addWidget(self.slider_pad_pan)
        self.lbl_pad_pan = QLabel("C")
        self.lbl_pad_pan.setFixedWidth(36)
        row_p.addWidget(self.lbl_pad_pan)
        detail_layout.addLayout(row_p)

        # Slider Tune (Pitch)
        row_t = QHBoxLayout()
        row_t.addWidget(QLabel("Accordage"))
        self.slider_pad_tune = QSlider(Qt.Horizontal)
        self.slider_pad_tune.setRange(-12, 12)
        self.slider_pad_tune.valueChanged.connect(self._on_pad_tune_changed)
        row_t.addWidget(self.slider_pad_tune)
        self.lbl_pad_tune = QLabel("0 st")
        self.lbl_pad_tune.setFixedWidth(36)
        row_t.addWidget(self.lbl_pad_tune)
        detail_layout.addLayout(row_t)

        # Slider Reverb Send
        row_r = QHBoxLayout()
        row_r.addWidget(QLabel("Send Reverb"))
        self.slider_pad_rev = QSlider(Qt.Horizontal)
        self.slider_pad_rev.setRange(0, 100)
        self.slider_pad_rev.valueChanged.connect(self._on_pad_rev_changed)
        row_r.addWidget(self.slider_pad_rev)
        self.lbl_pad_rev = QLabel("25%")
        self.lbl_pad_rev.setFixedWidth(36)
        row_r.addWidget(self.lbl_pad_rev)
        detail_layout.addLayout(row_r)

        # Boutons Mute / Solo / Test
        row_actions = QHBoxLayout()
        self.btn_mute = QPushButton("Mute")
        self.btn_mute.setCheckable(True)
        self.btn_mute.toggled.connect(self._on_pad_mute_toggled)
        row_actions.addWidget(self.btn_mute)

        self.btn_solo = QPushButton("Solo")
        self.btn_solo.setCheckable(True)
        self.btn_solo.toggled.connect(self._on_pad_solo_toggled)
        row_actions.addWidget(self.btn_solo)

        self.btn_test_pad = QPushButton("▶ Écouter")
        self.btn_test_pad.setObjectName("btn_action")
        self.btn_test_pad.clicked.connect(self._on_test_pad_clicked)
        row_actions.addWidget(self.btn_test_pad)

        detail_layout.addLayout(row_actions)
        center_row.addWidget(group_detail, stretch=4)

        main_layout.addLayout(center_row)

        # 3. Section Réverbération Stéréo
        group_reverb = QGroupBox("RÉVERBÉRATION STÉRÉO INTÉGRÉE (FREEVERB)")
        rev_layout = QHBoxLayout(group_reverb)
        rev_layout.setSpacing(16)

        self.chk_reverb = QCheckBox("Activer")
        self.chk_reverb.setChecked(self.plugin.reverb.enabled)
        self.chk_reverb.toggled.connect(self._on_reverb_toggled)
        rev_layout.addWidget(self.chk_reverb)

        # Room size
        v_rs = QVBoxLayout()
        v_rs.addWidget(QLabel("Taille de Pièce"))
        self.slider_room = QSlider(Qt.Horizontal)
        self.slider_room.setRange(0, 100)
        self.slider_room.setValue(int(self.plugin.reverb.room_size * 100))
        self.slider_room.valueChanged.connect(self._on_room_changed)
        v_rs.addWidget(self.slider_room)
        rev_layout.addLayout(v_rs)

        # Damping
        v_damp = QVBoxLayout()
        v_damp.addWidget(QLabel("Amortissement"))
        self.slider_damp = QSlider(Qt.Horizontal)
        self.slider_damp.setRange(0, 100)
        self.slider_damp.setValue(int(self.plugin.reverb.damping * 100))
        self.slider_damp.valueChanged.connect(self._on_damp_changed)
        v_damp.addWidget(self.slider_damp)
        rev_layout.addLayout(v_damp)

        # Largeur Stéréo
        v_w = QVBoxLayout()
        v_w.addWidget(QLabel("Largeur Stéréo"))
        self.slider_width = QSlider(Qt.Horizontal)
        self.slider_width.setRange(0, 100)
        self.slider_width.setValue(int(self.plugin.reverb.width * 100))
        self.slider_width.valueChanged.connect(self._on_width_changed)
        v_w.addWidget(self.slider_width)
        rev_layout.addLayout(v_w)

        # Mix Wet/Dry
        v_mix = QVBoxLayout()
        v_mix.addWidget(QLabel("Mix Réverb (Wet)"))
        self.slider_wet = QSlider(Qt.Horizontal)
        self.slider_wet.setRange(0, 100)
        self.slider_wet.setValue(int(self.plugin.reverb.wet_mix * 100))
        self.slider_wet.valueChanged.connect(self._on_wet_changed)
        v_mix.addWidget(self.slider_wet)
        rev_layout.addLayout(v_mix)

        main_layout.addWidget(group_reverb)

    def _sync_all_controls(self):
        if not self.selected_pad:
            return
        p = self.selected_pad
        self.lbl_sel_name.setText(f"Pad : {p.name} (GM {p.midi_pitches[0]})")
        self.waveform_view.set_audio_data(p.get_audio_data())

        self.slider_pad_vol.blockSignals(True)
        self.slider_pad_vol.setValue(int(p.volume * 100))
        self.lbl_pad_vol.setText(f"{int(p.volume * 100)}%")
        self.slider_pad_vol.blockSignals(False)

        self.slider_pad_pan.blockSignals(True)
        self.slider_pad_pan.setValue(int(p.pan * 100))
        self.lbl_pad_pan.setText("C" if p.pan == 0 else (f"G{abs(int(p.pan * 100))}" if p.pan < 0 else f"D{int(p.pan * 100)}"))
        self.slider_pad_pan.blockSignals(False)

        self.slider_pad_tune.blockSignals(True)
        self.slider_pad_tune.setValue(int(p.tune))
        self.lbl_pad_tune.setText(f"{int(p.tune):+d} st")
        self.slider_pad_tune.blockSignals(False)

        self.slider_pad_rev.blockSignals(True)
        self.slider_pad_rev.setValue(int(p.reverb_send * 100))
        self.lbl_pad_rev.setText(f"{int(p.reverb_send * 100)}%")
        self.slider_pad_rev.blockSignals(False)

        self.btn_mute.blockSignals(True)
        self.btn_mute.setChecked(p.muted)
        self.btn_mute.blockSignals(False)

        self.btn_solo.blockSignals(True)
        self.btn_solo.setChecked(p.soloed)
        self.btn_solo.blockSignals(False)

    def _on_pad_clicked(self, pad: DrumPad, btn: PadButton):
        self.selected_pad = pad
        btn.flash()
        self._sync_all_controls()
        self._play_pad(pad)

    def _play_pad(self, pad: DrumPad):
        """Joue immédiatement l'échantillon via sounddevice pour pré-écoute fluide"""
        try:
            import sounddevice as sd
            wave = self.plugin.render_note(pad.midi_pitches[0], velocity=115)
            sd.play(wave, 44100)
        except Exception:
            pass

    def _on_test_pad_clicked(self):
        if self.selected_pad:
            btn = self.pad_buttons.get(self.selected_pad.pad_id)
            if btn:
                btn.flash()
            self._play_pad(self.selected_pad)

    def _on_preset_changed(self, name: str):
        self.plugin.apply_preset(name)
        self.slider_room.setValue(int(self.plugin.reverb.room_size * 100))
        self.slider_damp.setValue(int(self.plugin.reverb.damping * 100))
        self.slider_wet.setValue(int(self.plugin.reverb.wet_mix * 100))
        self._sync_all_controls()

    def _on_master_vol_changed(self, val: int):
        self.plugin.master_volume = val / 100.0
        self.lbl_master_val.setText(f"{val}%")

    def _on_pad_vol_changed(self, val: int):
        if self.selected_pad:
            self.selected_pad.volume = val / 100.0
            self.lbl_pad_vol.setText(f"{val}%")

    def _on_pad_pan_changed(self, val: int):
        if self.selected_pad:
            self.selected_pad.pan = val / 100.0
            self.lbl_pad_pan.setText("C" if val == 0 else (f"G{abs(val)}" if val < 0 else f"D{val}"))

    def _on_pad_tune_changed(self, val: int):
        if self.selected_pad:
            self.selected_pad.tune = float(val)
            self.lbl_pad_tune.setText(f"{val:+d} st")
            self.waveform_view.set_audio_data(self.selected_pad.get_audio_data())

    def _on_pad_rev_changed(self, val: int):
        if self.selected_pad:
            self.selected_pad.reverb_send = val / 100.0
            self.lbl_pad_rev.setText(f"{val}%")

    def _on_pad_mute_toggled(self, checked: bool):
        if self.selected_pad:
            self.selected_pad.muted = checked
            btn = self.pad_buttons.get(self.selected_pad.pad_id)
            if btn:
                btn._update_style()

    def _on_pad_solo_toggled(self, checked: bool):
        if self.selected_pad:
            self.selected_pad.soloed = checked
            btn = self.pad_buttons.get(self.selected_pad.pad_id)
            if btn:
                btn._update_style()

    def _on_reverb_toggled(self, checked: bool):
        self.plugin.reverb.enabled = checked

    def _on_room_changed(self, val: int):
        self.plugin.reverb.room_size = val / 100.0

    def _on_damp_changed(self, val: int):
        self.plugin.reverb.damping = val / 100.0

    def _on_width_changed(self, val: int):
        self.plugin.reverb.width = val / 100.0

    def _on_wet_changed(self, val: int):
        self.plugin.reverb.wet_mix = val / 100.0
