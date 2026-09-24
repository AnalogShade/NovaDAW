"""
ui/editing_toolbar.py - Palette et barre d'outils d'édition, grille musicale et presse-papier pour NovaDAW
"""
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QPushButton, QLabel, QComboBox,
    QButtonGroup, QFrame, QSizePolicy
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont


class EditingToolbar(QWidget):
    """
    Barre d'outils d'édition complète :
    - Palette d'outils exclusifs : Pointeur [1], Ciseaux / Scission [2], Gomme [3]
    - Découpe instantanée : Scinder à la tête de lecture [Ctrl+K / S]
    - Presse-papier : Copier [Ctrl+C], Couper [Ctrl+X], Coller [Ctrl+V], Dupliquer [Ctrl+D], Supprimer [Suppr]
    - Grille BPM & Aimantage : Aimantage Snap (On/Off), Sélecteur de résolution (1 Mesure à 1/32, Off)
    - Zoom de l'arrangement : Dézoomer, Zoomer, Zoom 100%
    """

    tool_changed = Signal(str)                   # "select", "split", "erase"
    split_playhead_requested = Signal()
    copy_requested = Signal()
    cut_requested = Signal()
    paste_requested = Signal()
    duplicate_requested = Signal()
    delete_requested = Signal()
    snap_toggled = Signal(bool)
    grid_resolution_changed = Signal(float)       # Valeur en temps (ex: 4.0, 1.0, 0.25)
    zoom_in_requested = Signal()
    zoom_out_requested = Signal()
    zoom_reset_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(36)
        self.setObjectName("editing_toolbar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("""
            QWidget#editing_toolbar {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1a1c27, stop:1 #13151f);
                border-bottom: 1px solid #282d3e;
            }
            QPushButton.tool_btn {
                background-color: #1e2230;
                color: #cbd5e1;
                border: 1px solid #2f354a;
                border-radius: 4px;
                padding: 2px 9px;
                font-size: 11px;
                font-weight: 500;
            }
            QPushButton.tool_btn:hover {
                background-color: #2b3145;
                color: #ffffff;
                border-color: #4b5563;
            }
            QPushButton.tool_btn:checked {
                background-color: #0369a1;
                color: #ffffff;
                border: 1px solid #38bdf8;
                font-weight: bold;
            }
            QPushButton.action_btn {
                background-color: #1e2230;
                color: #cbd5e1;
                border: 1px solid #2f354a;
                border-radius: 4px;
                padding: 2px 8px;
                font-size: 11px;
            }
            QPushButton.action_btn:hover {
                background-color: #282f44;
                color: #38bdf8;
                border-color: #38bdf8;
            }
            QPushButton.snap_btn {
                background-color: #1e2230;
                color: #94a3b8;
                border: 1px solid #2f354a;
                border-radius: 4px;
                padding: 2px 8px;
                font-size: 11px;
            }
            QPushButton.snap_btn:checked {
                background-color: #064e3b;
                color: #34d399;
                border: 1px solid #10b981;
                font-weight: bold;
            }
            QComboBox {
                background-color: #1a1e2b;
                color: #e2e8f0;
                border: 1px solid #2e354a;
                border-radius: 4px;
                padding: 2px 6px;
                font-size: 11px;
                min-width: 120px;
            }
            QComboBox::drop-down {
                border: none;
                width: 16px;
            }
            QComboBox QAbstractItemView {
                background-color: #161822;
                color: #e2e8f0;
                selection-background-color: #0284c7;
                selection-color: #ffffff;
                border: 1px solid #2e354a;
            }
            QLabel {
                color: #94a3b8;
                font-size: 11px;
            }
        """)

        self._init_ui()

    def _create_sep(self) -> QFrame:
        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setStyleSheet("color: #2b3044; max-height: 18px;")
        return sep

    def _init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 3, 10, 3)
        layout.setSpacing(6)

        # --- 1. PALETTE D'OUTILS (EXCLUSIFS) ---
        self.tool_group = QButtonGroup(self)
        self.tool_group.setExclusive(True)

        self.btn_select = QPushButton("🖱️ Pointeur (1)")
        self.btn_select.setToolTip("Outil Pointeur [1] : Sélectionner, déplacer et redimensionner des blocs")
        self.btn_select.setProperty("class", "tool_btn")
        self.btn_select.setCheckable(True)
        self.btn_select.setChecked(True)
        self.tool_group.addButton(self.btn_select)
        layout.addWidget(self.btn_select)

        self.btn_split = QPushButton("✂️ Ciseaux (2)")
        self.btn_split.setToolTip("Outil Ciseaux [2] : Cliquer sur un bloc pour le scinder en deux selon la grille")
        self.btn_split.setProperty("class", "tool_btn")
        self.btn_split.setCheckable(True)
        self.tool_group.addButton(self.btn_split)
        layout.addWidget(self.btn_split)

        self.btn_erase = QPushButton("🗑️ Gomme (3)")
        self.btn_erase.setToolTip("Outil Gomme [3] : Cliquer sur un bloc pour le supprimer instantanément")
        self.btn_erase.setProperty("class", "tool_btn")
        self.btn_erase.setCheckable(True)
        self.tool_group.addButton(self.btn_erase)
        layout.addWidget(self.btn_erase)

        self.tool_group.buttonClicked.connect(self._on_tool_clicked)

        layout.addWidget(self._create_sep())

        # --- 2. ACTION SCISSION DIRECTE À LA TÊTE ---
        self.btn_split_playhead = QPushButton("✂️ Scinder Tête (Ctrl+K)")
        self.btn_split_playhead.setToolTip("Scinder le bloc sélectionné (ou sous la tête) à la position de lecture actuelle [Ctrl+K ou S]")
        self.btn_split_playhead.setProperty("class", "action_btn")
        self.btn_split_playhead.clicked.connect(self.split_playhead_requested.emit)
        layout.addWidget(self.btn_split_playhead)

        layout.addWidget(self._create_sep())

        # --- 3. PRESSE-PAPIER (COPIER, COUPER, COLLER, DUPLIQUER, SUPPRIMER) ---
        self.btn_copy = QPushButton("📋 Copier")
        self.btn_copy.setToolTip("Copier le bloc sélectionné [Ctrl+C]")
        self.btn_copy.setProperty("class", "action_btn")
        self.btn_copy.clicked.connect(self.copy_requested.emit)
        layout.addWidget(self.btn_copy)

        self.btn_cut = QPushButton("✂️ Couper")
        self.btn_cut.setToolTip("Couper le bloc sélectionné [Ctrl+X]")
        self.btn_cut.setProperty("class", "action_btn")
        self.btn_cut.clicked.connect(self.cut_requested.emit)
        layout.addWidget(self.btn_cut)

        self.btn_paste = QPushButton("📥 Coller à la Tête")
        self.btn_paste.setToolTip("Coller le bloc à la position actuelle de la tête de lecture [Ctrl+V]")
        self.btn_paste.setProperty("class", "action_btn")
        self.btn_paste.clicked.connect(self.paste_requested.emit)
        layout.addWidget(self.btn_paste)

        self.btn_dup = QPushButton("📑 Dupliquer")
        self.btn_dup.setToolTip("Dupliquer le bloc sélectionné juste à la suite [Ctrl+D]")
        self.btn_dup.setProperty("class", "action_btn")
        self.btn_dup.clicked.connect(self.duplicate_requested.emit)
        layout.addWidget(self.btn_dup)

        self.btn_del = QPushButton("🗑️")
        self.btn_del.setToolTip("Supprimer le bloc sélectionné [Suppr]")
        self.btn_del.setProperty("class", "action_btn")
        self.btn_del.setFixedWidth(28)
        self.btn_del.clicked.connect(self.delete_requested.emit)
        layout.addWidget(self.btn_del)

        layout.addWidget(self._create_sep())

        # --- 4. GRILLE & AIMANTAGE (SNAP) ---
        self.btn_snap = QPushButton("🧲 Snap")
        self.btn_snap.setToolTip("Activer / Désactiver l'aimantage à la grille pour le déplacement, découpe et rognage")
        self.btn_snap.setProperty("class", "snap_btn")
        self.btn_snap.setCheckable(True)
        self.btn_snap.setChecked(True)
        self.btn_snap.toggled.connect(self.snap_toggled.emit)
        layout.addWidget(self.btn_snap)

        lbl_grid = QLabel("Grille :")
        layout.addWidget(lbl_grid)

        self.combo_grid = QComboBox()
        self.combo_grid.setToolTip("Résolution temporelle de la grille musicale selon le tempo BPM")
        self.combo_grid.addItem("1 Mesure (1 Bar)", 4.0)
        self.combo_grid.addItem("1/2 Mesure", 2.0)
        self.combo_grid.addItem("1/4 (Temps / Noire)", 1.0)
        self.combo_grid.addItem("1/8 (Croche)", 0.5)
        self.combo_grid.addItem("1/16 (Double-croche)", 0.25)
        self.combo_grid.addItem("1/32 (Triple-croche)", 0.125)
        self.combo_grid.addItem("Off / Libre", 0.0)
        self.combo_grid.setCurrentIndex(2)  # 1/4 par défaut (1 temps)
        self.combo_grid.currentIndexChanged.connect(self._on_grid_combo_changed)
        layout.addWidget(self.combo_grid)

        layout.addStretch()

        # --- 5. ZOOM DE LA TIMELINE ---
        layout.addWidget(self._create_sep())
        lbl_zoom = QLabel("Zoom :")
        layout.addWidget(lbl_zoom)

        self.btn_zoom_out = QPushButton("−")
        self.btn_zoom_out.setToolTip("Réduire le zoom horizontal de la timeline")
        self.btn_zoom_out.setProperty("class", "action_btn")
        self.btn_zoom_out.setFixedWidth(26)
        self.btn_zoom_out.clicked.connect(self.zoom_out_requested.emit)
        layout.addWidget(self.btn_zoom_out)

        self.btn_zoom_in = QPushButton("+")
        self.btn_zoom_in.setToolTip("Agrandir le zoom horizontal de la timeline")
        self.btn_zoom_in.setProperty("class", "action_btn")
        self.btn_zoom_in.setFixedWidth(26)
        self.btn_zoom_in.clicked.connect(self.zoom_in_requested.emit)
        layout.addWidget(self.btn_zoom_in)

        self.btn_zoom_reset = QPushButton("100%")
        self.btn_zoom_reset.setToolTip("Rétablir le zoom normal de la timeline")
        self.btn_zoom_reset.setProperty("class", "action_btn")
        self.btn_zoom_reset.clicked.connect(self.zoom_reset_requested.emit)
        layout.addWidget(self.btn_zoom_reset)

    def _on_tool_clicked(self, button):
        if button == self.btn_select:
            self.tool_changed.emit("select")
        elif button == self.btn_split:
            self.tool_changed.emit("split")
        elif button == self.btn_erase:
            self.tool_changed.emit("erase")

    def _on_grid_combo_changed(self, index: int):
        val = float(self.combo_grid.currentData())
        self.grid_resolution_changed.emit(val)

    def set_active_tool(self, tool_name: str):
        """Définit l'outil actif programmatiquement ('select', 'split', 'erase')"""
        if tool_name == "select":
            self.btn_select.setChecked(True)
        elif tool_name == "split":
            self.btn_split.setChecked(True)
        elif tool_name == "erase":
            self.btn_erase.setChecked(True)
        self.tool_changed.emit(tool_name)

    def set_grid_resolution(self, val_beats: float):
        for i in range(self.combo_grid.count()):
            if abs(float(self.combo_grid.itemData(i)) - float(val_beats)) < 1e-4:
                self.combo_grid.setCurrentIndex(i)
                break

    def set_snap_enabled(self, enabled: bool):
        self.btn_snap.setChecked(bool(enabled))
