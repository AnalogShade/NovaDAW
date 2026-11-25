"""Façade et commandes de l'éditeur Nova Guitar Amp."""
from math import cos, radians, sin
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSignalBlocker, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QDial, QFrame, QGridLayout, QHBoxLayout, QLabel, QProgressBar, QPushButton,
    QVBoxLayout, QWidget,
)

from plugins.guitar_amp.amp_plugin import NovaGuitarAmpPlugin


class AmpGrille(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        path = Path(__file__).resolve().parents[2] / "assets" / "nova_guitar_amp_grille.jpg"
        self._photo = QPixmap(str(path))
        self.setMinimumHeight(192)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 20, 26, 20)
        layout.setSpacing(0)
        layout.addStretch()
        brand = QLabel("NOVA")
        brand.setObjectName("ampBrand")
        layout.addWidget(brand, alignment=Qt.AlignLeft)
        title = QLabel("GUITAR AMP")
        title.setObjectName("ampTitle")
        layout.addWidget(title, alignment=Qt.AlignLeft)
        layout.addStretch()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#171719"))
        if self._photo.isNull():
            return
        photo = self._photo.scaled(
            self.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation,
        )
        painter.drawPixmap((self.width() - photo.width()) // 2,
                           (self.height() - photo.height()) // 2, photo)


class AmpDial(QDial):
    """Potentiomètre gradué à glissement vertical, avec le clavier natif de QDial."""

    def __init__(self, label):
        super().__init__()
        self.setFixedSize(86, 86)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.SizeVerCursor)
        self.setAccessibleName(label)
        self.setToolTip(f"{label}\nGlisser vers le haut ou le bas, ou utiliser la molette.")
        self._drag_y = 0.0
        self._drag_value = 0

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            event.ignore()
            return
        self.setFocus(Qt.MouseFocusReason)
        self._drag_y = event.globalPosition().y()
        self._drag_value = self.value()
        self.setSliderDown(True)
        event.accept()

    def mouseMoveEvent(self, event):
        if not self.isSliderDown():
            event.ignore()
            return
        distance = self._drag_y - event.globalPosition().y()
        change = distance * (self.maximum() - self.minimum()) / 160
        self.setValue(self._drag_value + round(change))
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton or not self.isSliderDown():
            event.ignore()
            return
        self.setSliderDown(False)
        event.accept()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.translate(self.width() / 2, self.height() / 2)
        progress = (self.value() - self.minimum()) / (self.maximum() - self.minimum())
        angle = 225 - progress * 270

        # La graduation et l'index partagent une course de 270 degrés.
        for tick in range(11):
            tick_angle = radians(225 - tick * 27)
            direction = QPointF(cos(tick_angle), -sin(tick_angle))
            color = "#f18f69" if tick / 10 <= progress else "#737176"
            painter.setPen(QPen(QColor(color), 1.6))
            painter.drawLine(direction * (33 if tick % 5 else 31), direction * 37)

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#0c0c0e"))
        painter.drawEllipse(QRectF(-28, -26, 56, 57))

        metal = QLinearGradient(0, -28, 0, 28)
        metal.setColorAt(0, QColor("#b1ada9"))
        metal.setColorAt(0.36, QColor("#575559"))
        metal.setColorAt(0.78, QColor("#2f2d31"))
        metal.setColorAt(1, QColor("#787377"))
        painter.setPen(QPen(QColor("#171619"), 1.2))
        painter.setBrush(metal)
        painter.drawEllipse(QRectF(-28, -28, 56, 56))

        for flute in range(36):
            a = radians(flute * 10)
            direction = QPointF(cos(a), sin(a))
            painter.setPen(QPen(QColor(15, 14, 16, 135), 1.3))
            painter.drawLine(direction * 23, direction * 27)

        cap = QLinearGradient(-16, -21, 13, 21)
        cap.setColorAt(0, QColor("#5d5a5c"))
        cap.setColorAt(0.38, QColor("#39373a"))
        cap.setColorAt(1, QColor("#1c1b1e"))
        painter.setBrush(cap)
        painter.setPen(QPen(QColor("#888287"), 0.8))
        painter.drawEllipse(QRectF(-23, -23, 46, 46))

        painter.save()
        painter.rotate(-angle)
        painter.setPen(QPen(QColor("#130e0e"), 5.5, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(11, 0, 20, 0)
        painter.setPen(QPen(QColor("#ffad85"), 2.8, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(11, 0, 20, 0)
        painter.restore()

        if self.hasFocus():
            painter.setPen(QPen(QColor("#ffc3a5"), 1.2, Qt.DashLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(QRectF(-41, -41, 82, 82))


class AmpPowerSwitch(QPushButton):
    """Interrupteur à bascule dont l'état coché correspond au bypass."""

    def __init__(self):
        super().__init__()
        self.setCheckable(True)
        self.setFixedSize(61, 60)
        self.setCursor(Qt.PointingHandCursor)
        self.setAccessibleName("Bypass de l’amplificateur")
        self.setToolTip("Couper ou réactiver le traitement de l’amplificateur.")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.translate(self.width() / 2, 0)
        powered = not self.isChecked()

        painter.setPen(QPen(QColor("#ffba97" if self.hasFocus() else "#6a6264"), 1))
        painter.setBrush(QColor("#0a090b"))
        painter.drawRoundedRect(QRectF(-24, 2, 48, 56), 6, 6)

        face = QLinearGradient(0, 6, 0, 54)
        if powered:
            face.setColorAt(0, QColor("#4a1319"))
            face.setColorAt(0.52, QColor("#80252a"))
            face.setColorAt(1, QColor("#d54d48"))
        else:
            face.setColorAt(0, QColor("#535056"))
            face.setColorAt(0.52, QColor("#302c31"))
            face.setColorAt(1, QColor("#18171a"))
        outline = "#d08273" if self.underMouse() else "#1c171a"
        painter.setPen(QPen(QColor(outline), 1))
        painter.setBrush(face)
        offset = 1 if self.isDown() else 0
        painter.drawRoundedRect(QRectF(-19, 6 + offset, 38, 48), 4, 4)

        painter.setPen(QPen(QColor(0, 0, 0, 110), 1))
        painter.drawLine(-17, 30 + offset, 17, 30 + offset)
        font = QFont("Segoe UI", 10, QFont.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor("#fff0e4" if powered else "#b2a9ad"))
        painter.drawText(QRectF(-16, 7 + offset, 32, 22), Qt.AlignCenter, "I")
        painter.setPen(QColor("#f5ddda" if powered else "#e6e0e1"))
        painter.drawText(QRectF(-16, 31 + offset, 32, 21), Qt.AlignCenter, "O")


class NovaGuitarAmpEditorWidget(QWidget):
    """Éditeur de tête d'ampli avec niveaux d'entrée et de sortie en temps réel."""

    def __init__(self, plugin: NovaGuitarAmpPlugin, parent=None):
        super().__init__(parent)
        self.plugin = plugin
        self.setMinimumWidth(690)
        self.setStyleSheet("""
            QFrame#ampChassis {
                background: #101012; border: 1px solid #484246; border-radius: 8px;
            }
            QFrame#ampFace {
                background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                    stop:0 #383539, stop:0.07 #282629, stop:1 #1b191d);
                border-top: 1px solid #6e5b57; border-bottom: 1px solid #0a090b;
            }
            QFrame#ampEQ {
                background: #232124; border: 1px solid #4e474b; border-radius: 5px;
            }
            QLabel {
                background: transparent; color: #eae4df; font-family: "Segoe UI";
            }
            QLabel#ampBrand {
                color: #f49c79; font-family: "Segoe UI"; font-size: 15px; font-weight: 700;
            }
            QLabel#ampTitle {
                color: #f5efeb; font-family: "Bahnschrift"; font-size: 46px; font-weight: 700;
            }
            QLabel#ampLegend, QLabel#ampMeterLabel, QLabel#ampEQTitle {
                font-size: 11px; font-weight: 700; color: #e2d9d4;
            }
            QLabel#ampEQTitle { color: #c3aaa0; }
            QLabel#ampValue, QLabel#ampMeterValue, QLabel#ampStatus {
                font-family: "Consolas"; font-size: 11px; color: #ffc1a1;
            }
            QLabel#ampValue {
                background: #141316; border: 1px solid #383036; border-radius: 3px;
                padding: 3px 5px;
            }
            QPushButton#ampCabSwitch {
                background: #161518; border: 1px solid #635b60; border-radius: 4px;
                color: #dfd7d4; font: 600 11px "Consolas"; padding: 4px 8px;
            }
            QPushButton#ampCabSwitch:checked { background: #40272a; color: #ffb998; }
            QPushButton#ampCabSwitch:hover, QPushButton#ampCabSwitch:focus {
                border-color: #ffb998;
            }
            QProgressBar {
                background: #080809; border: 1px solid #51474a; border-radius: 2px;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                    stop:0 #ffc59c, stop:0.35 #e98052, stop:1 #9f3b32);
                border-radius: 1px;
            }
        """)
        self._dials = {}
        self._init_ui()
        self._sync_all()

        self._meter_timer = QTimer(self, interval=33)
        self._meter_timer.timeout.connect(self._update_meters)
        self._meter_timer.start()
        self._sync_timer = QTimer(self, interval=150)
        self._sync_timer.timeout.connect(self._sync_all)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        chassis = QFrame()
        chassis.setObjectName("ampChassis")
        amp = QVBoxLayout(chassis)
        amp.setContentsMargins(6, 6, 6, 6)
        amp.setSpacing(0)
        layout.addWidget(chassis)
        amp.addWidget(AmpGrille(), 1)

        face = QFrame()
        face.setObjectName("ampFace")
        face_layout = QHBoxLayout(face)
        face_layout.setContentsMargins(17, 12, 17, 12)
        face_layout.setSpacing(14)
        amp.addWidget(face)

        self._add_dial(face_layout, "distortion", "GAIN", "Distorsion / gain",
                       0, 100, 100, lambda v: f"{v} %").setContentsMargins(0, 0, 0, 9)

        eq = QFrame()
        eq.setObjectName("ampEQ")
        eq_layout = QVBoxLayout(eq)
        eq_layout.setContentsMargins(6, 6, 6, 8)
        eq_layout.setSpacing(5)
        eq_layout.addWidget(self._label("ÉGALISATION", "ampEQTitle"))
        eq_row = QHBoxLayout()
        eq_row.setSpacing(5)
        eq_layout.addLayout(eq_row)
        for key, label, name in (
            ("low_db", "GRAVES", "Graves"),
            ("mid_db", "MÉDIUMS", "Médiums"),
            ("treble_db", "AIGUS", "Aigus"),
        ):
            self._add_dial(eq_row, key, label, name, -120, 120, 10,
                           lambda v: f"{v / 10:+.1f} dB")
        face_layout.addWidget(eq, 3)

        self._add_dial(face_layout, "volume", "MASTER", "Volume master",
                       0, 200, 100, lambda v: f"{v} %").setContentsMargins(0, 0, 0, 9)

        switches = QVBoxLayout()
        switches.setSpacing(4)
        switches.addWidget(self._label("BYPASS", "ampLegend"))
        self.btn_bypass = AmpPowerSwitch()
        self.btn_bypass.toggled.connect(self._toggle_bypass)
        switches.addWidget(self.btn_bypass, alignment=Qt.AlignHCenter)
        self._bypass_status = self._label("", "ampStatus")
        switches.addWidget(self._bypass_status)
        self.chk_cab = QPushButton()
        self.chk_cab.setObjectName("ampCabSwitch")
        self.chk_cab.setCheckable(True)
        self.chk_cab.setCursor(Qt.PointingHandCursor)
        self.chk_cab.setAccessibleName("Simulateur de baffle")
        self.chk_cab.setToolTip("Activer ou désactiver le simulateur de baffle.")
        self.chk_cab.toggled.connect(self._on_cab_toggled)
        switches.addSpacing(5)
        switches.addWidget(self.chk_cab)
        face_layout.addLayout(switches)

        meters = QGridLayout()
        meters.setContentsMargins(17, 12, 17, 10)
        meters.setHorizontalSpacing(9)
        meters.setColumnMinimumWidth(3, 16)
        self.bar_in, self._input_value = self._add_meter(meters, 0, "ENTRÉE")
        self.bar_out, self._output_value = self._add_meter(meters, 4, "SORTIE")
        amp.addLayout(meters)

    @staticmethod
    def _label(text, name):
        label = QLabel(text)
        label.setObjectName(name)
        label.setAlignment(Qt.AlignCenter)
        return label

    def _add_dial(self, row, attr, label, tooltip, minimum, maximum, scale, format_value):
        column = QVBoxLayout()
        column.setSpacing(3)
        column.addStretch()
        column.addWidget(self._label(label, "ampLegend"))
        dial = AmpDial(tooltip)
        dial.setRange(minimum, maximum)
        column.addWidget(dial, alignment=Qt.AlignHCenter)
        value = self._label("", "ampValue")
        value.setMinimumWidth(66)
        column.addWidget(value, alignment=Qt.AlignHCenter)
        row.addLayout(column, 1)

        def on_changed(position):
            setattr(self.plugin, attr, position / scale)
            value.setText(format_value(position))

        dial.valueChanged.connect(on_changed)
        self._dials[attr] = dial, value, scale, format_value
        return column

    def _add_meter(self, layout, column, name):
        layout.addWidget(self._label(name, "ampMeterLabel"), 0, column)
        meter = QProgressBar()
        meter.setAccessibleName(f"Niveau {name.lower()}")
        meter.setRange(-60, 0)
        meter.setValue(-60)
        meter.setTextVisible(False)
        meter.setFixedHeight(9)
        layout.addWidget(meter, 0, column + 1)
        layout.setColumnStretch(column + 1, 1)
        value = self._label("-60 dBFS", "ampMeterValue")
        value.setMinimumWidth(57)
        value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(value, 0, column + 2)
        return meter, value

    def _update_switch_labels(self):
        self._bypass_status.setText("OFF · BYPASS" if self.btn_bypass.isChecked() else "ON · AMPLI")
        self.chk_cab.setText("BAFFLE ON" if self.chk_cab.isChecked() else "BAFFLE OFF")

    def _on_cab_toggled(self, checked):
        self.plugin.cab_enabled = checked
        self._update_switch_labels()

    def _toggle_bypass(self, checked):
        self.plugin.enabled = not checked
        self._update_switch_labels()

    def _update_meters(self):
        for peak, meter, label in (
            (self.plugin.current_input_peak_db, self.bar_in, self._input_value),
            (self.plugin.current_output_peak_db, self.bar_out, self._output_value),
        ):
            level = max(-60, min(0, round(peak)))
            meter.setValue(level)
            label.setText(f"{level} dBFS")
        self.plugin.decay_meters()

    def _sync_all(self):
        state = self.plugin.get_state()
        with QSignalBlocker(self.btn_bypass), QSignalBlocker(self.chk_cab):
            self.btn_bypass.setChecked(not state["enabled"])
            self.chk_cab.setChecked(state["cab_enabled"])
        self._update_switch_labels()

        for attr, (dial, label, scale, format_value) in self._dials.items():
            with QSignalBlocker(dial):
                dial.setValue(round(state[attr] * scale))
            label.setText(format_value(dial.value()))

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_all()
        self._sync_timer.start()

    def hideEvent(self, event):
        self._sync_timer.stop()
        super().hideEvent(event)

    def closeEvent(self, event):
        self._meter_timer.stop()
        super().closeEvent(event)
