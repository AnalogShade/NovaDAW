"""
plugins/drum_machine/drum_gui.py - Interface graphique moderne pour le plugin Nova Drums VSTi.
Design inspiré de Native Instruments Battery et Cubase Groove Agent.
"""
import os
import threading
from pathlib import Path
from typing import Optional, Dict, List, Any
import numpy as np
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QPushButton, QSlider, QComboBox, QFrame, QSizePolicy,
    QGroupBox, QCheckBox, QMenu, QFileDialog
)
from PySide6.QtCore import Qt, QTimer, Signal, QPoint
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QFont, QLinearGradient, QAction

from plugins.drum_machine.drum_plugin import DrumMachinePlugin, DrumPad


class LowLatencySoundPlayer:
    """
    Lecteur audio temps réel persistant ultra-faible latence (< 8ms) avec polyphonie complète.
    Maintient un flux de sortie actif permanent, éliminant le délai de 200ms de sd.play().
    Prend en charge le mélange simultané de plusieurs pads (cymbales + caisse claire) et le choke group.
    """
    _instance: Optional['LowLatencySoundPlayer'] = None

    @classmethod
    def get_instance(cls) -> 'LowLatencySoundPlayer':
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

    def play(self, buffer: np.ndarray, choke_group: int = 0):
        if buffer is None or len(buffer) == 0:
            return

        if self._ensure_stream():
            with self._lock:
                if choke_group > 0:
                    for v in self._voices:
                        if v.get("choke_group") == choke_group:
                            buf = v["buffer"]
                            cur = v["cursor"]
                            rem = len(buf) - cur
                            if rem > 0:
                                fade_len = min(rem, int(0.005 * self.sample_rate))
                                buf[cur:cur + fade_len] *= np.linspace(1.0, 0.0, fade_len)[:, None]
                                v["buffer"] = buf[:cur + fade_len]

                self._voices.append({
                    "buffer": np.ascontiguousarray(buffer, dtype=np.float32),
                    "cursor": 0,
                    "choke_group": choke_group
                })
            return

        # Secours
        try:
            import sounddevice as sd
            sd.play(buffer, self.sample_rate)
        except Exception:
            pass

    def _audio_callback(self, outdata, frames, time_info, status):
        outdata.fill(0.0)
        with self._lock:
            if not self._voices:
                return
            surviving = []
            for v in self._voices:
                buf = v["buffer"]
                cur = v["cursor"]
                avail = len(buf) - cur
                to_copy = min(frames, avail)
                if to_copy > 0:
                    outdata[:to_copy] += buf[cur:cur + to_copy]
                    v["cursor"] += to_copy
                if v["cursor"] < len(buf):
                    surviving.append(v)
            self._voices = surviving

    def close(self):
        with self._lock:
            self._voices.clear()
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None



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
    """Bouton de pad interactif avec LED d'animation lors des frappes et menu déroulant d'échantillons"""
    menu_requested = Signal(object, object)  # (DrumPad, QWidget)

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

        # Bouton flèche vers le bas discret pour ouvrir le menu des samples
        self.btn_arrow = QPushButton("▾", self)
        self.btn_arrow.setFixedSize(18, 18)
        self.btn_arrow.setCursor(Qt.PointingHandCursor)
        self.btn_arrow.setToolTip(f"Changer l'échantillon pour '{self.pad.name}'...")
        self.btn_arrow.setStyleSheet("""
            QPushButton {
                background-color: rgba(30, 41, 59, 0.85);
                color: #94a3b8;
                border: 1px solid rgba(148, 163, 184, 0.35);
                border-radius: 3px;
                font-size: 11px;
                font-weight: bold;
                padding: 0px;
                line-height: 18px;
            }
            QPushButton:hover {
                background-color: #0284c7;
                color: #ffffff;
                border-color: #38bdf8;
            }
        """)
        self.btn_arrow.clicked.connect(self._on_arrow_clicked)

        self._update_style()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Positionnement dans le coin supérieur droit du pad
        margin = 4
        btn_w, btn_h = self.btn_arrow.width(), self.btn_arrow.height()
        self.btn_arrow.move(self.width() - btn_w - margin, margin)

    def _on_arrow_clicked(self):
        self.menu_requested.emit(self.pad, self.btn_arrow)

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
        elif self.pad.muted:
            border = "#ef4444"
            bg = "#1f1418"
            txt = "#94a3b8"
        elif self.pad.soloed:
            border = "#eab308"
            bg = "#292212"
            txt = "#fef08a"
        else:
            border = "#2b3245"
            bg = "#161924"
            txt = "#f1f5f9"

        note_str = f"GM {self.pad.midi_pitches[0]}" if self.pad.midi_pitches else ""
        sample_name = Path(self.pad.sample_filename).stem if self.pad.sample_filename else ""

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
        self.setToolTip(f"Pad {self.pad.name} ({note_str})\nÉchantillon actif : {self.pad.sample_filename}\nCliquez sur ▾ pour changer d'échantillon")


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
            QMenu {
                background-color: #161924;
                color: #f1f5f9;
                border: 1px solid #2d3748;
                border-radius: 6px;
                padding: 4px;
            }
            QMenu::item {
                padding: 6px 24px 6px 20px;
                border-radius: 4px;
                font-size: 11px;
            }
            QMenu::item:selected {
                background-color: #0284c7;
                color: #ffffff;
            }
            QMenu::separator {
                height: 1px;
                background: #2d3748;
                margin: 4px 8px;
            }
        """)

        self._init_ui()
        self._sync_all_controls()

        # Surveillance de la mise en tampon RAM
        self.cache_timer = QTimer(self)
        self.cache_timer.setInterval(200)
        self.cache_timer.timeout.connect(self._update_cache_badge)
        self.cache_timer.start()
        self._update_cache_badge()

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

        header.addSpacing(16)
        # Badge indicateur du cache RAM
        self.lbl_cache_badge = QLabel("⚡ Cache RAM : 9/9 Prêts (0 ms)")
        self.lbl_cache_badge.setStyleSheet("""
            QLabel {
                background-color: #064e3b;
                color: #34d399;
                border: 1px solid #059669;
                border-radius: 10px;
                padding: 3px 10px;
                font-size: 11px;
                font-weight: bold;
            }
        """)
        header.addWidget(self.lbl_cache_badge)

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
                btn.menu_requested.connect(self._show_pad_sample_menu)
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

        # Sélecteur d'échantillon (Sample)
        row_sample = QHBoxLayout()
        lbl_s = QLabel("Sample")
        lbl_s.setFixedWidth(45)
        row_sample.addWidget(lbl_s)

        self.combo_pad_sample = QComboBox()
        self.combo_pad_sample.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.combo_pad_sample.currentIndexChanged.connect(self._on_combo_sample_changed)
        row_sample.addWidget(self.combo_pad_sample)

        self.btn_browse_sample = QPushButton("📂")
        self.btn_browse_sample.setToolTip("Charger un fichier audio WAV personnalisé...")
        self.btn_browse_sample.setFixedSize(28, 26)
        self.btn_browse_sample.setObjectName("btn_action")
        self.btn_browse_sample.clicked.connect(self._on_browse_selected_pad_sample)
        row_sample.addWidget(self.btn_browse_sample)

        detail_layout.addLayout(row_sample)

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

        # Synchroniser la liste des samples du pad
        if hasattr(self, "combo_pad_sample"):
            self.combo_pad_sample.blockSignals(True)
            self.combo_pad_sample.clear()
            samples = self.plugin.get_available_samples_for_pad(p.pad_id)
            active_idx = 0
            curr_fn = p.sample_filename or ""
            curr_stem = Path(curr_fn).stem.lower()
            for idx, s in enumerate(samples):
                self.combo_pad_sample.addItem(s["label"], s["filename"])
                s_fn = s["filename"]
                if curr_fn == s_fn or curr_stem == Path(s_fn).stem.lower():
                    active_idx = idx
            self.combo_pad_sample.setCurrentIndex(active_idx)
            self.combo_pad_sample.blockSignals(False)

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
        """
        Déclenche immédiatement l'échantillon pré-mis en tampon en RAM (< 5ms de latence globale).
        Assure une restitution audio instantanée dès le clic.
        """
        # Récupère l'audio pré-généré directement du cache en mémoire vive (0 ms de calcul CPU)
        wave = self.plugin.get_cached_pad_audio(pad.pad_id, velocity=115)

        # 1. Tenter la lecture via le moteur audio maître NovaDAW (latence minimale et device ASIO/WASAPI)
        try:
            from core.audio_engine import get_global_audio_engine
            engine = get_global_audio_engine()
            if engine and getattr(engine, "_stream", None) and getattr(engine._stream, "active", False):
                if engine.play_preview_buffer(wave, choke_group=pad.choke_group):
                    return
        except Exception:
            pass

        # 2. Repli vers le lecteur permanent dédié à ultra-faible latence (< 8ms)
        try:
            player = LowLatencySoundPlayer.get_instance()
            player.play(wave, choke_group=pad.choke_group)
        except Exception:
            try:
                import sounddevice as sd
                sd.play(wave, 44100)
            except Exception:
                pass

    def _update_cache_badge(self):
        if not hasattr(self, "lbl_cache_badge"):
            return
        ready, total = self.plugin.cache_status
        if ready == total:
            self.lbl_cache_badge.setText(f"⚡ Cache RAM : {ready}/{total} Prêts (0 ms)")
            self.lbl_cache_badge.setStyleSheet("""
                QLabel {
                    background-color: #064e3b;
                    color: #34d399;
                    border: 1px solid #059669;
                    border-radius: 10px;
                    padding: 3px 10px;
                    font-size: 11px;
                    font-weight: bold;
                }
            """)
        else:
            self.lbl_cache_badge.setText(f"⏳ Tamponnage RAM : {ready}/{total}...")
            self.lbl_cache_badge.setStyleSheet("""
                QLabel {
                    background-color: #78350f;
                    color: #fde047;
                    border: 1px solid #d97706;
                    border-radius: 10px;
                    padding: 3px 10px;
                    font-size: 11px;
                    font-weight: bold;
                }
            """)

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
        self._update_cache_badge()

    def _on_master_vol_changed(self, val: int):
        self.plugin.master_volume = val / 100.0
        self.lbl_master_val.setText(f"{val}%")
        self.plugin.invalidate_cache()
        self._update_cache_badge()

    def _on_pad_vol_changed(self, val: int):
        if self.selected_pad:
            self.selected_pad.volume = val / 100.0
            self.lbl_pad_vol.setText(f"{val}%")
            self.plugin.invalidate_cache(self.selected_pad.pad_id)
            self._update_cache_badge()

    def _on_pad_pan_changed(self, val: int):
        if self.selected_pad:
            self.selected_pad.pan = val / 100.0
            self.lbl_pad_pan.setText("C" if val == 0 else (f"G{abs(val)}" if val < 0 else f"D{val}"))
            self.plugin.invalidate_cache(self.selected_pad.pad_id)
            self._update_cache_badge()

    def _on_pad_tune_changed(self, val: int):
        if self.selected_pad:
            self.selected_pad.tune = float(val)
            self.lbl_pad_tune.setText(f"{val:+d} st")
            self.waveform_view.set_audio_data(self.selected_pad.get_audio_data())
            self.plugin.invalidate_cache(self.selected_pad.pad_id)
            self._update_cache_badge()

    def _on_pad_rev_changed(self, val: int):
        if self.selected_pad:
            self.selected_pad.reverb_send = val / 100.0
            self.lbl_pad_rev.setText(f"{val}%")
            self.plugin.invalidate_cache(self.selected_pad.pad_id)
            self._update_cache_badge()

    def _on_pad_mute_toggled(self, checked: bool):
        if self.selected_pad:
            self.selected_pad.muted = checked
            self.plugin.invalidate_cache(self.selected_pad.pad_id)
            self._update_cache_badge()
            btn = self.pad_buttons.get(self.selected_pad.pad_id)
            if btn:
                btn._update_style()

    def _on_pad_solo_toggled(self, checked: bool):
        if self.selected_pad:
            self.selected_pad.soloed = checked
            self.plugin.invalidate_cache()
            self._update_cache_badge()
            btn = self.pad_buttons.get(self.selected_pad.pad_id)
            if btn:
                btn._update_style()

    def _on_reverb_toggled(self, checked: bool):
        self.plugin.reverb.enabled = checked
        self.plugin.invalidate_cache()
        self._update_cache_badge()

    def _on_room_changed(self, val: int):
        self.plugin.reverb.room_size = val / 100.0
        self.plugin.invalidate_cache()
        self._update_cache_badge()

    def _on_damp_changed(self, val: int):
        self.plugin.reverb.damping = val / 100.0
        self.plugin.invalidate_cache()
        self._update_cache_badge()

    def _on_width_changed(self, val: int):
        self.plugin.reverb.width = val / 100.0
        self.plugin.invalidate_cache()
        self._update_cache_badge()

    def _on_wet_changed(self, val: int):
        self.plugin.reverb.wet_mix = val / 100.0
        self.plugin.invalidate_cache()
        self._update_cache_badge()

    def _show_pad_sample_menu(self, pad: DrumPad, anchor_btn: QWidget):
        """Affiche le menu contextuel déroulant de sélection d'échantillons sous le bouton flèche"""
        menu = QMenu(self)
        samples = self.plugin.get_available_samples_for_pad(pad.pad_id)
        current_fn = pad.sample_filename or ""
        current_stem = Path(current_fn).stem.lower()

        title_action = menu.addAction(f"🥁 Samples : {pad.name}")
        title_action.setEnabled(False)
        menu.addSeparator()

        for s in samples:
            action = menu.addAction(s["label"])
            action.setCheckable(True)
            s_fn = s["filename"]
            is_active = (current_fn == s_fn or current_stem == Path(s_fn).stem.lower())
            action.setChecked(is_active)
            action.triggered.connect(lambda checked=False, p=pad, f=s_fn: self._apply_pad_sample(p, f))

        menu.addSeparator()
        browse_action = menu.addAction("📂 Choisir un fichier WAV personnalisé...")
        browse_action.triggered.connect(lambda: self._browse_and_set_sample(pad))

        pos = anchor_btn.mapToGlobal(anchor_btn.rect().bottomLeft())
        menu.exec(pos)

    def _apply_pad_sample(self, pad: DrumPad, sample_filename_or_path: str):
        """Applique un nouvel échantillon au pad, rafraîchit l'UI et déclenche l'écoute immédiate"""
        success = self.plugin.set_pad_sample(pad.pad_id, sample_filename_or_path)
        if success:
            self.selected_pad = pad
            btn = self.pad_buttons.get(pad.pad_id)
            if btn:
                btn._update_style()
                btn.flash()
            self._sync_all_controls()
            self._update_cache_badge()
            self._play_pad(pad)

    def _browse_and_set_sample(self, pad: DrumPad):
        """Ouvre un sélecteur de fichier WAV pour charger un sample externe"""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            f"Choisir un échantillon audio pour {pad.name}",
            str(self.plugin.get_sample_dirs()[0]),
            "Fichiers Audio (*.wav)"
        )
        if file_path:
            self._apply_pad_sample(pad, file_path)

    def _on_combo_sample_changed(self, idx: int):
        """Appelé lors du changement de sélection dans la combobox de l'échantillon"""
        if idx < 0 or not self.selected_pad or not hasattr(self, "combo_pad_sample"):
            return
        filename = self.combo_pad_sample.itemData(idx)
        if filename and filename != self.selected_pad.sample_filename:
            self._apply_pad_sample(self.selected_pad, filename)

    def _on_browse_selected_pad_sample(self):
        """Ouvre l'explorateur pour le pad actuellement sélectionné"""
        if self.selected_pad:
            self._browse_and_set_sample(self.selected_pad)

    def keyPressEvent(self, event):
        key = event.key()
        # Mapping clavier ergonomique pour jouer les 9 pads (Pave numerique ou lettres)
        key_pad_map = {
            Qt.Key_7: "tom_h", Qt.Key_8: "crash", Qt.Key_9: "ride",
            Qt.Key_4: "tom_l", Qt.Key_5: "tom_m", Qt.Key_6: "hihat_o",
            Qt.Key_1: "kick",  Qt.Key_2: "snare",  Qt.Key_3: "hihat_c",
            Qt.Key_A: "tom_h", Qt.Key_Z: "crash", Qt.Key_E: "ride",
            Qt.Key_Q: "tom_l", Qt.Key_S: "tom_m", Qt.Key_D: "hihat_o",
            Qt.Key_W: "kick",  Qt.Key_X: "snare",  Qt.Key_C: "hihat_c",
            Qt.Key_Space: getattr(self.selected_pad, "pad_id", None)
        }
        pad_id = key_pad_map.get(key)
        if pad_id:
            pad = next((p for p in self.plugin.pads if p.pad_id == pad_id), None)
            if pad:
                btn = self.pad_buttons.get(pad.pad_id)
                if btn:
                    btn.flash()
                self.selected_pad = pad
                self._sync_all_controls()
                self._play_pad(pad)
                event.accept()
                return
        super().keyPressEvent(event)

