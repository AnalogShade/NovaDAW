"""
plugins/guitar/guitar_gui.py - Interface graphique de Nova Guitar (Digital Waveguide).

Caractéristiques :
- Sélecteur de modèle physique (Acoustique Acier, Acoustique Nylon, Électrique DI, Basse)
- Contrôles physiques : Tension/Rigidité, Position d'attaque (Pluck), Attaque médiator,
  Brillance, Palm Mute, Sustain, Relâchement, Résonance Caisse/Micro, Position Micro, Stéréo, Réverbe et Volume
- Visualisation temps réel de la corde vibrante
- Boutons d'audition immédiate (note grave, accord Em, arpège) réutilisant LowLatencySynthAudition
- Bouton Bypass et synchronisation automatique avec NativePluginDialog et PresetManager
"""
import math
from typing import Optional
import numpy as np

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QPushButton, QSlider, QComboBox, QFrame, QCheckBox,
    QGroupBox, QSizePolicy
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPainter, QPen, QColor, QPainterPath

from plugins.guitar.guitar_plugin import NovaGuitarPlugin


GUITAR_MODES = [
    ("acoustic_steel", "Acoustique Acier (Folk)"),
    ("acoustic_nylon", "Acoustique Nylon (Classique)"),
    ("electric_clean", "Électrique Clean (DI Micro)"),
    ("electric_bass", "Basse Électrique (DI)"),
]


class StringVisualizerWidget(QFrame):
    """Visualisateur stylisé des 6 cordes et de l'enveloppe de corde selon les paramètres physiques."""

    def __init__(self, plugin: NovaGuitarPlugin, parent=None):
        super().__init__(parent)
        self.plugin = plugin
        self.setMinimumHeight(88)
        self.setMaximumHeight(104)
        self.setStyleSheet("background-color: #0b0e14; border: 1px solid #1e293b; border-radius: 6px;")
        self._phase = 0.0
        self._energy = 0.0

    def trigger_pluck(self):
        self._energy = 1.0
        self.update()

    def tick(self):
        if self._energy > 0.005:
            self._phase += 0.45
            self._energy *= 0.90
            self.update()
        elif self._energy > 0.0:
            self._energy = 0.0
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = float(self.width())
        h = float(self.height())
        margin_x = 18.0
        span_w = max(20.0, w - 2.0 * margin_x)

        # Marqueur de position du micro / rosace et du point d'attaque (pluck_position)
        pluck_x = margin_x + span_w * (1.0 - self.plugin.pluck_position)
        pickup_x = margin_x + span_w * (1.0 - self.plugin.pickup_position)

        if self.plugin.guitar_mode in ("electric_clean", "electric_bass"):
            painter.setPen(QPen(QColor(56, 189, 248, 90), 2, Qt.DashLine))
            painter.drawLine(int(pickup_x), 8, int(pickup_x), int(h - 8))
        else:
            # Rosace acoustique
            painter.setPen(QPen(QColor(217, 119, 6, 95), 2))
            painter.drawEllipse(int(margin_x + span_w * 0.62 - 26), int(h * 0.5 - 26), 52, 52)

        # 6 cordes
        for s_idx in range(6):
            y = 14.0 + s_idx * ((h - 28.0) / 5.0)
            thickness = 1.0 + (5 - s_idx) * 0.28
            amp = self._energy * (4.5 - s_idx * 0.35) * (1.15 - 0.35 * self.plugin.string_tension)

            if not self.plugin.enabled:
                col = QColor(71, 85, 105)
            elif self.plugin.guitar_mode == "acoustic_nylon" and s_idx < 3:
                col = QColor(226, 232, 240)
            elif "acoustic" in self.plugin.guitar_mode:
                col = QColor(245, 158, 11)
            else:
                col = QColor(148, 163, 184)

            painter.setPen(QPen(col, thickness))
            if amp < 0.15:
                painter.drawLine(int(margin_x), int(y), int(w - margin_x), int(y))
            else:
                path = QPainterPath()
                steps = 40
                for k in range(steps + 1):
                    u = k / float(steps)
                    px = margin_x + u * span_w
                    env = math.sin(math.pi * u)
                    vib = math.sin(self._phase * (1.0 + s_idx * 0.17) + u * math.pi * 2.0)
                    py = y + env * amp * vib
                    if k == 0:
                        path.moveTo(px, py)
                    else:
                        path.lineTo(px, py)
                painter.drawPath(path)

        # Marqueur du point d'attaque du médiator/doigt
        painter.setPen(QPen(QColor(251, 191, 36, 170), 1.5))
        painter.drawLine(int(pluck_x), 6, int(pluck_x), int(h - 6))


class NovaGuitarEditorWidget(QWidget):
    """Interface d'édition complète pour le plugin Nova Guitar."""

    def __init__(self, plugin: NovaGuitarPlugin, parent=None):
        super().__init__(parent)
        self.plugin = plugin
        self.setMinimumSize(640, 480)
        self.setStyleSheet("""
            QWidget {
                background-color: #12151e;
                color: #e2e8f0;
                font-family: 'Segoe UI', sans-serif;
            }
            QGroupBox {
                background-color: #171b26;
                border: 1px solid #262d3d;
                border-radius: 6px;
                margin-top: 10px;
                padding-top: 12px;
                font-weight: bold;
                font-size: 11px;
                color: #fbbf24;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
            }
            QSlider::groove:horizontal {
                height: 6px;
                background: #0b0e14;
                border-radius: 3px;
                border: 1px solid #262d3d;
            }
            QSlider::handle:horizontal {
                background: #f59e0b;
                border: 1px solid #fbbf24;
                width: 14px;
                height: 14px;
                margin: -5px 0;
                border-radius: 7px;
            }
            QSlider::sub-page:horizontal {
                background: #d97706;
                border-radius: 3px;
            }
            QComboBox {
                background-color: #1e2433;
                border: 1px solid #334155;
                border-radius: 4px;
                padding: 4px 8px;
                color: #f8fafc;
            }
            QPushButton {
                background-color: #1e2433;
                border: 1px solid #334155;
                border-radius: 4px;
                padding: 5px 10px;
                color: #e2e8f0;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #293247;
                border-color: #f59e0b;
            }
        """)

        self._sliders = {}
        self._init_ui()
        self._sync_controls()

        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self.visualizer.tick)
        self._anim_timer.start(33)

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(14, 12, 14, 12)
        main_layout.setSpacing(10)

        # 1. En-tête : Titre + Sélecteur Mode + Bypass
        top_bar = QHBoxLayout()
        lbl_title = QLabel("🎸 NOVA GUITAR — Modélisation Physique Digital Waveguide")
        lbl_title.setStyleSheet("font-size: 13px; font-weight: bold; color: #fbbf24;")
        top_bar.addWidget(lbl_title)

        top_bar.addStretch()

        top_bar.addWidget(QLabel("Type :"))
        self.combo_mode = QComboBox()
        for mode_id, mode_lbl in GUITAR_MODES:
            self.combo_mode.addItem(mode_lbl, userData=mode_id)
        self.combo_mode.currentIndexChanged.connect(self._on_mode_changed)
        top_bar.addWidget(self.combo_mode)

        self.btn_bypass = QPushButton("Bypass")
        self.btn_bypass.setCheckable(True)
        self.btn_bypass.clicked.connect(self._toggle_bypass)
        self.btn_bypass.setStyleSheet("""
            QPushButton:checked {
                background-color: #7f1d1d;
                border-color: #ef4444;
                color: #ffffff;
                font-weight: bold;
            }
        """)
        top_bar.addWidget(self.btn_bypass)
        main_layout.addLayout(top_bar)

        # 2. Visualiseur de cordes
        self.visualizer = StringVisualizerWidget(self.plugin, self)
        main_layout.addWidget(self.visualizer)

        # 3. Groupes de paramètres physiques
        cols = QHBoxLayout()
        cols.setSpacing(10)

        # Groupe Gauche : Physique de la Corde & Attaque
        grp_string = QGroupBox("Physique de la Corde & Médiator")
        g1 = QGridLayout(grp_string)
        g1.setSpacing(8)
        self._add_slider(g1, 0, "string_tension", "Tension / Rigidité", 0, 100, lambda v: f"{v}%")
        self._add_slider(g1, 1, "pluck_position", "Position d'attaque", 4, 50, lambda v: f"{v}% (Chev↔Manche)")
        self._add_slider(g1, 2, "pick_attack", "Dureté Médiator/Doigt", 0, 100, lambda v: f"{v}%")
        self._add_slider(g1, 3, "brightness", "Brillance corde", 5, 98, lambda v: f"{v}%")
        self._add_slider(g1, 4, "palm_mute", "Palm Mute", 0, 100, lambda v: f"{v}%")

        self.chk_dual_pol = QCheckBox("Double polarisation (battement naturel)")
        self.chk_dual_pol.toggled.connect(self._on_dual_pol_toggled)
        g1.addWidget(self.chk_dual_pol, 5, 0, 1, 3)
        cols.addWidget(grp_string)

        # Groupe Droite : Sustain, Caisse / Micro & Espace
        grp_body = QGroupBox("Caisse / Micro, Enveloppe & Espace")
        g2 = QGridLayout(grp_body)
        g2.setSpacing(8)
        self._add_slider(g2, 0, "sustain_sec", "Sustain corde", 5, 100, lambda v: f"{v / 10.0:.1f} s", scale=10.0)
        self._add_slider(g2, 1, "release_sec", "Relâchement (Note-Off)", 4, 150, lambda v: f"{v / 100.0:.2f} s", scale=100.0)
        self._add_slider(g2, 2, "body_resonance", "Résonance Caisse/Micro", 0, 130, lambda v: f"{v}%")
        self._add_slider(g2, 3, "pickup_position", "Position Micro (Élec.)", 8, 42, lambda v: f"{v}%")
        self._add_slider(g2, 4, "stereo_width", "Largeur Stéréo", 0, 100, lambda v: f"{v}%")
        self._add_slider(g2, 5, "reverb_mix", "Ambiance / Réverbe", 0, 55, lambda v: f"{v}%")
        self._add_slider(g2, 6, "master_volume", "Volume Sortie", 0, 180, lambda v: f"{v}%")
        cols.addWidget(grp_body)

        main_layout.addLayout(cols)

        # 4. Barre d'audition rapide
        aud_bar = QHBoxLayout()
        lbl_aud = QLabel("Audition directe :")
        lbl_aud.setStyleSheet("color: #94a3b8; font-size: 11px;")
        aud_bar.addWidget(lbl_aud)

        for label, pitches in [
            ("🎸 E2 (Mi grave)", [40]),
            ("🎸 A3 (La)", [57]),
            ("🎸 Accord Em", [40, 47, 52, 55, 59, 64]),
            ("🎸 Accord Am", [45, 52, 57, 60, 64]),
        ]:
            btn = QPushButton(label)
            btn.clicked.connect(lambda _chk=False, ps=pitches: self._audition_notes(ps))
            aud_bar.addWidget(btn)

        aud_bar.addStretch()
        main_layout.addLayout(aud_bar)

    def _add_slider(self, grid: QGridLayout, row: int, attr: str, label: str, min_v: int, max_v: int, fmt_fn, scale: float = 100.0):
        lbl = QLabel(label)
        lbl.setStyleSheet("font-size: 11px; color: #cbd5e1;")
        slider = QSlider(Qt.Horizontal)
        slider.setRange(min_v, max_v)
        val_lbl = QLabel("")
        val_lbl.setMinimumWidth(92)
        val_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        val_lbl.setStyleSheet("font-size: 11px; color: #fbbf24; font-family: Consolas, monospace;")

        def on_changed(val: int):
            setattr(self.plugin, attr, float(val) / scale)
            if attr in ("reverb_mix", "stereo_width"):
                self.plugin._sync_reverb()
            val_lbl.setText(fmt_fn(val))
            self.visualizer.update()
            self.plugin.notify_modified()

        slider.valueChanged.connect(on_changed)
        grid.addWidget(lbl, row, 0)
        grid.addWidget(slider, row, 1)
        grid.addWidget(val_lbl, row, 2)
        self._sliders[attr] = (slider, val_lbl, fmt_fn, scale)

    def _on_mode_changed(self, idx: int):
        mode_id = self.combo_mode.itemData(idx)
        if mode_id:
            self.plugin.guitar_mode = mode_id
            self.visualizer.update()
            self.plugin.notify_modified()

    def _on_dual_pol_toggled(self, checked: bool):
        self.plugin.dual_polarization = bool(checked)
        self.plugin.notify_modified()

    def _toggle_bypass(self, checked: bool):
        self.plugin.enabled = not checked
        self.btn_bypass.setText("Bypass Actif" if checked else "Bypass")
        self.visualizer.update()
        self.plugin.notify_modified()

    def _sync_controls(self):
        self.btn_bypass.blockSignals(True)
        self.btn_bypass.setChecked(not self.plugin.enabled)
        self.btn_bypass.setText("Bypass Actif" if not self.plugin.enabled else "Bypass")
        self.btn_bypass.blockSignals(False)

        self.combo_mode.blockSignals(True)
        for idx in range(self.combo_mode.count()):
            if self.combo_mode.itemData(idx) == self.plugin.guitar_mode:
                self.combo_mode.setCurrentIndex(idx)
                break
        self.combo_mode.blockSignals(False)

        self.chk_dual_pol.blockSignals(True)
        self.chk_dual_pol.setChecked(bool(self.plugin.dual_polarization))
        self.chk_dual_pol.blockSignals(False)

        for attr, (slider, val_lbl, fmt_fn, scale) in self._sliders.items():
            slider.blockSignals(True)
            v_int = int(round(float(getattr(self.plugin, attr)) * scale))
            v_int = max(slider.minimum(), min(slider.maximum(), v_int))
            slider.setValue(v_int)
            val_lbl.setText(fmt_fn(v_int))
            slider.blockSignals(False)

        self.visualizer.update()

    def _sync_all(self):
        self._sync_controls()

    def _audition_notes(self, pitches):
        """Joue immédiatement une note ou un accord gratté (strum) via le moteur audio ou LowLatencySynthAudition."""
        self.visualizer.trigger_pluck()
        try:
            sr = 44100
            if len(pitches) == 1:
                wave = self.plugin.render_note(pitches[0], duration_sec=0.85, sample_rate=sr, velocity=102)
            else:
                # Petit décalage de strum réaliste (~18 ms entre cordes)
                notes = []
                for idx, p in enumerate(pitches):
                    notes.append(
                        type(
                            "StrumNote",
                            (),
                            {
                                "pitch": p,
                                "start_beat": idx * 0.036,
                                "duration": 1.5,
                                "velocity": 96 - idx * 2,
                            },
                        )()
                    )
                strum_span_sec = max(0, len(pitches) - 1) * 0.018
                total_sec = 0.75 + strum_span_sec + self.plugin.get_release_tail_sec()
                frames = int(total_sec * sr)
                iso_ctx = self.plugin.create_isolated_context(sr)
                wave = self.plugin._render_slice_internal(
                    notes=notes,
                    start_b=0.0,
                    end_b=total_sec * 2.0,
                    beats_per_sec=2.0,
                    frames=frames,
                    sample_rate=sr,
                    isolated_ctx=iso_ctx,
                    use_live_state=False,
                )
                fade_n = min(len(wave), max(1, int(0.012 * sr)))
                if fade_n > 1:
                    wave[-fade_n:] *= np.linspace(1.0, 0.0, fade_n, dtype=np.float32)[:, None]

            from plugins.base import play_plugin_audition
            from plugins.synth.synth_gui import LowLatencySynthAudition
            play_plugin_audition(
                self.plugin,
                wave,
                widget=self,
                choke_group=0,
                fallback_player=LowLatencySynthAudition.get_instance().play,
            )
        except Exception as e:
            print(f"[NovaGuitarGUI] Erreur audition : {e}")

    def closeEvent(self, event):
        if hasattr(self, "_anim_timer"):
            self._anim_timer.stop()
        super().closeEvent(event)
