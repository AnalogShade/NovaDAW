"""
plugins/guitar_amp/amp_gui.py - Interface graphique de l'amplificateur Nova Guitar Amp.

Comprend :
- Bouton Bypass temps réel
- Contrôles principaux : Distorsion (Gain), Low, Mid, Treble, Volume
- Commutateur Simulateur de Baffle (Cab Sim On/Off)
- VU-mètres Entrée / Sortie temps réel
- Synchronisation complète avec NativePluginDialog et PresetManager
"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QPushButton, QSlider, QFrame, QCheckBox, QGroupBox, QProgressBar
)
from PySide6.QtCore import Qt, QTimer

from plugins.guitar_amp.amp_plugin import NovaGuitarAmpPlugin


class NovaGuitarAmpEditorWidget(QWidget):
    """Interface d'édition type tête d'amplificateur pour Nova Guitar Amp."""

    def __init__(self, plugin: NovaGuitarAmpPlugin, parent=None):
        super().__init__(parent)
        self.plugin = plugin
        self.setMinimumSize(540, 340)
        self.setStyleSheet("""
            QWidget {
                background-color: #121318;
                color: #e2e8f0;
                font-family: 'Segoe UI', sans-serif;
            }
            QGroupBox {
                background-color: #181a22;
                border: 1px solid #2c3140;
                border-radius: 6px;
                margin-top: 10px;
                padding-top: 12px;
                font-weight: bold;
                font-size: 11px;
                color: #f87171;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
            }
            QSlider::groove:horizontal {
                height: 6px;
                background: #0b0d12;
                border-radius: 3px;
                border: 1px solid #2c3140;
            }
            QSlider::handle:horizontal {
                background: #ef4444;
                border: 1px solid #fca5a5;
                width: 14px;
                height: 14px;
                margin: -5px 0;
                border-radius: 7px;
            }
            QSlider::sub-page:horizontal {
                background: #dc2626;
                border-radius: 3px;
            }
            QPushButton {
                background-color: #1f2430;
                border: 1px solid #374151;
                border-radius: 4px;
                padding: 5px 12px;
                color: #e2e8f0;
                font-size: 11px;
            }
            QPushButton:hover {
                border-color: #ef4444;
            }
            QProgressBar {
                background-color: #0b0d12;
                border: 1px solid #262b38;
                border-radius: 3px;
                height: 10px;
                text-align: center;
                font-size: 9px;
            }
            QProgressBar::chunk {
                background-color: #22c55e;
                border-radius: 2px;
            }
        """)

        self._sliders = {}
        self._init_ui()
        self._sync_controls()

        self._meter_timer = QTimer(self)
        self._meter_timer.timeout.connect(self._update_meters)
        self._meter_timer.start(33)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        # 1. En-tête : Titre + Cab Sim + Bypass
        top_bar = QHBoxLayout()
        lbl_title = QLabel("🔊 NOVA GUITAR AMP — Préampli, Tone Stack & Baffle")
        lbl_title.setStyleSheet("font-size: 13px; font-weight: bold; color: #f87171;")
        top_bar.addWidget(lbl_title)
        top_bar.addStretch()

        self.chk_cab = QCheckBox("Baffle (Cab Sim)")
        self.chk_cab.toggled.connect(self._on_cab_toggled)
        top_bar.addWidget(self.chk_cab)

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
        layout.addLayout(top_bar)

        # 2. Contrôles de l'ampli
        grp_amp = QGroupBox("Réglages Amplificateur")
        grid = QGridLayout(grp_amp)
        grid.setSpacing(10)

        self._add_slider(grid, 0, "distortion", "Distorsion / Gain", 0, 100, lambda v: f"{v}%", scale=100.0)
        self._add_slider(grid, 1, "low_db", "Low (Graves)", -120, 120, lambda v: f"{v / 10.0:+.1f} dB", scale=10.0)
        self._add_slider(grid, 2, "mid_db", "Mid (Médiums)", -120, 120, lambda v: f"{v / 10.0:+.1f} dB", scale=10.0)
        self._add_slider(grid, 3, "treble_db", "Treble (Aigus)", -120, 120, lambda v: f"{v / 10.0:+.1f} dB", scale=10.0)
        self._add_slider(grid, 4, "volume", "Volume Master", 0, 200, lambda v: f"{v}%", scale=100.0)

        layout.addWidget(grp_amp)

        # 3. VU-mètres Entrée / Sortie
        grp_meters = QGroupBox("Niveaux Temps Réel")
        m_layout = QGridLayout(grp_meters)
        m_layout.setSpacing(6)

        m_layout.addWidget(QLabel("Entrée :"), 0, 0)
        self.bar_in = QProgressBar()
        self.bar_in.setRange(-60, 0)
        self.bar_in.setValue(-60)
        self.bar_in.setFormat("%v dBFS")
        m_layout.addWidget(self.bar_in, 0, 1)

        m_layout.addWidget(QLabel("Sortie :"), 1, 0)
        self.bar_out = QProgressBar()
        self.bar_out.setRange(-60, 0)
        self.bar_out.setValue(-60)
        self.bar_out.setFormat("%v dBFS")
        m_layout.addWidget(self.bar_out, 1, 1)

        layout.addWidget(grp_meters)

    def _add_slider(self, grid: QGridLayout, row: int, attr: str, label: str, min_v: int, max_v: int, fmt_fn, scale: float):
        lbl = QLabel(label)
        lbl.setStyleSheet("font-size: 11px; color: #cbd5e1;")
        slider = QSlider(Qt.Horizontal)
        slider.setRange(min_v, max_v)
        val_lbl = QLabel("")
        val_lbl.setMinimumWidth(78)
        val_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        val_lbl.setStyleSheet("font-size: 11px; color: #fca5a5; font-family: Consolas, monospace;")

        def on_changed(val: int):
            setattr(self.plugin, attr, float(val) / scale)
            val_lbl.setText(fmt_fn(val))

        slider.valueChanged.connect(on_changed)
        grid.addWidget(lbl, row, 0)
        grid.addWidget(slider, row, 1)
        grid.addWidget(val_lbl, row, 2)
        self._sliders[attr] = (slider, val_lbl, fmt_fn, scale)

    def _on_cab_toggled(self, checked: bool):
        self.plugin.cab_enabled = bool(checked)

    def _toggle_bypass(self, checked: bool):
        self.plugin.enabled = not checked
        self.btn_bypass.setText("Bypass Actif" if checked else "Bypass")

    def _update_meters(self):
        self.bar_in.setValue(int(max(-60, min(0, round(self.plugin.current_input_peak_db)))))
        self.bar_out.setValue(int(max(-60, min(0, round(self.plugin.current_output_peak_db)))))
        self.plugin.decay_meters()

    def _sync_controls(self):
        self.btn_bypass.blockSignals(True)
        self.btn_bypass.setChecked(not self.plugin.enabled)
        self.btn_bypass.setText("Bypass Actif" if not self.plugin.enabled else "Bypass")
        self.btn_bypass.blockSignals(False)

        self.chk_cab.blockSignals(True)
        self.chk_cab.setChecked(bool(self.plugin.cab_enabled))
        self.chk_cab.blockSignals(False)

        for attr, (slider, val_lbl, fmt_fn, scale) in self._sliders.items():
            slider.blockSignals(True)
            v_int = int(round(float(getattr(self.plugin, attr)) * scale))
            v_int = max(slider.minimum(), min(slider.maximum(), v_int))
            slider.setValue(v_int)
            val_lbl.setText(fmt_fn(v_int))
            slider.blockSignals(False)

    def _sync_all(self):
        self._sync_controls()

    def closeEvent(self, event):
        if hasattr(self, "_meter_timer"):
            self._meter_timer.stop()
        super().closeEvent(event)
