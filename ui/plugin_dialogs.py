"""
ui/plugin_dialogs.py - Boîtes de dialogue pour la gestion des plugins VST3
Gestionnaire de plugins, scan de dossiers avec progression en direct,
sélection de répertoires personnalisés et ouverture de l'interface native du VST.
"""
import os
import sys
import time
import threading
from typing import Optional

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QLineEdit,
    QProgressBar, QMessageBox, QFileDialog, QFrame, QAbstractItemView
)
from PySide6.QtCore import Qt, Signal, QTimer, QThread
from PySide6.QtGui import QColor

from core.plugin_manager import (
    global_plugin_manager,
    PluginScanWorker,
    PluginLoadWorker,
    PluginInfo,
)


class PluginLoadingDialog(QDialog):
    """
    Dialogue moderne affiché pendant le chargement en arrière-plan d'un plugin VST3 lourd.
    Empêche le gel de l'interface et informe clairement l'utilisateur.
    """
    def __init__(self, file_path: str, plugin_name: str, parent=None, instance_key=None):
        super().__init__(parent)
        self.file_path = file_path
        self.plugin_name = plugin_name
        self.loaded_plugin = None
        self.error_message: Optional[str] = None

        self.setWindowTitle(f"Chargement : {plugin_name}")
        self.setFixedSize(460, 180)
        self.setWindowFlags(Qt.Dialog | Qt.CustomizeWindowHint | Qt.WindowTitleHint | Qt.WindowCloseButtonHint)
        self.setModal(True)

        self.setStyleSheet("""
            QDialog {
                background-color: #161822;
                border: 1px solid #2e3447;
                border-radius: 8px;
            }
            QLabel {
                color: #e2e8f0;
                font-family: 'Segoe UI', sans-serif;
            }
            QPushButton {
                background-color: #242838;
                color: #94a3b8;
                border: 1px solid #3b4259;
                border-radius: 4px;
                padding: 6px 18px;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #2f354a;
                color: #ffffff;
                border-color: #38bdf8;
            }
            QProgressBar {
                background-color: #0f1118;
                border: 1px solid #232736;
                border-radius: 4px;
                height: 10px;
                text-align: center;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0284c7, stop:1 #38bdf8);
                border-radius: 4px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        # En-tête avec icône et titre stylé
        header = QHBoxLayout()
        is_inst = any(w in plugin_name.lower() for w in ["tank", "kontakt", "synth", "piano", "organ", "drum"])
        icon_lbl = QLabel("🎹" if is_inst else "🎛️")
        icon_lbl.setStyleSheet("font-size: 28px;")
        header.addWidget(icon_lbl)

        vbox = QVBoxLayout()
        title_lbl = QLabel(f"Chargement de {self.plugin_name}")
        title_lbl.setStyleSheet("font-size: 15px; font-weight: bold; color: #38bdf8;")
        sub_lbl = QLabel("Initialisation du plugin VST3 et des banques de sons...")
        sub_lbl.setStyleSheet("font-size: 12px; color: #94a3b8;")
        vbox.addWidget(title_lbl)
        vbox.addWidget(sub_lbl)
        header.addLayout(vbox, stretch=1)
        layout.addLayout(header)

        # Barre de progression indéterminée animée
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        layout.addWidget(self.progress_bar)

        # Pied avec statut
        footer = QHBoxLayout()
        self.status_lbl = QLabel("Veuillez patienter quelques secondes...")
        self.status_lbl.setStyleSheet("font-size: 11px; color: #64748b; font-style: italic;")
        footer.addWidget(self.status_lbl, stretch=1)
        layout.addLayout(footer)

        # Centrer sur la fenêtre parente si disponible
        if parent:
            top_w = parent.window() if hasattr(parent, "window") else parent
            if hasattr(top_w, "geometry"):
                geo = top_w.geometry()
                self.move(geo.center().x() - self.width() // 2, geo.center().y() - self.height() // 2)

        self.worker = PluginLoadWorker(file_path, self, instance_key)
        self.worker.loaded.connect(self._loaded)
        self.worker.error.connect(self._failed)
        self.worker.start()

    def _loaded(self, path, plugin):
        self.worker.wait()
        self.loaded_plugin = plugin
        self.accept()

    def _failed(self, path, message):
        self.worker.wait()
        self.error_message = message
        self.reject()

    def reject(self):
        if self.worker.isRunning():
            return
        super().reject()


def ensure_plugin_loaded(path, parent=None, instance_key=None):
    key = instance_key or path
    plugin = global_plugin_manager.active_instances.get(key)
    if plugin is not None and not getattr(plugin, "_failure", None):
        return True
    if plugin is not None:
        plugin.dispose()
        global_plugin_manager.active_instances.pop(key, None)
    global_plugin_manager.load_errors.pop(key, None)
    dialog = PluginLoadingDialog(path, os.path.basename(path), parent, key)
    if dialog.exec() == QDialog.Accepted:
        return True
    QMessageBox.warning(parent, "Plugin indisponible", dialog.error_message or "Chargement impossible")
    return False


def analyze_plugin_file(path, parent=None):
    """Load and classify a selected file without blocking the Qt event loop."""
    if not ensure_plugin_loaded(path, parent):
        return PluginInfo(os.path.basename(path), path, "unknown", False,
                          global_plugin_manager.load_errors.get(path, "Chargement impossible"))
    plugin = global_plugin_manager.active_instances[path]
    info = PluginInfo(plugin.name, path, "instrument" if plugin.is_instrument else "effect",
                      True, parameters_count=plugin.parameters_count)
    global_plugin_manager.plugins = [p for p in global_plugin_manager.plugins if p.file_path != path] + [info]
    global_plugin_manager.save_cache()
    global_plugin_manager.scan_updated.emit()
    return info


class EditorStatusWorker(QThread):
    checked = Signal(object)

    def __init__(self, plugin, close_event, parent):
        super().__init__(parent)
        self.plugin = plugin
        self.close_event = close_event

    def run(self):
        try:
            if self.close_event.is_set():
                self.plugin.request("close_editor", timeout=15)
            result = self.plugin.request("editor_status", timeout=15)
        except Exception as exc:
            result = {"open": False, "error": str(exc)}
        self.checked.emit(result)


def open_plugin_editor_gui(file_path: str, parent=None, instance_key=None):
    """Open the editor in its isolated host, leaving Qt responsive."""
    if not file_path or not os.path.exists(file_path):
        QMessageBox.warning(parent, "Plugin introuvable", f"Plugin introuvable :\n{file_path}")
        return
    info = next((p for p in global_plugin_manager.plugins if p.file_path == file_path), None)
    if info and not info.is_compatible:
        QMessageBox.information(
            parent,
            f"Plugin non chargeable : {info.name}",
            f"Impossible d'ouvrir l'interface de '{info.name}' :\n\n"
            f"Format : {getattr(info, 'format', 'inconnu').upper()}\n"
            f"Diagnostic : {info.error_message or 'Format incompatible avec le moteur VST3 natif.'}"
        )
        return
    key = instance_key or file_path
    if global_plugin_manager.is_editor_open(key):
        global_plugin_manager.close_editor(key)
        return
    if not ensure_plugin_loaded(file_path, parent, key):
        return
    plugin = global_plugin_manager.active_instances[key]
    try:
        global_plugin_manager.saved_states[key] = plugin.request("editor")
    except Exception as exc:
        QMessageBox.warning(parent, "Erreur du plugin", str(exc))
        return
    event = threading.Event()
    global_plugin_manager.open_editors[key] = event
    global_plugin_manager.editor_opened.emit(file_path)
    timer = QTimer(global_plugin_manager)
    timer.setInterval(400)

    worker = EditorStatusWorker(plugin, event, global_plugin_manager)

    def checked(status):
        worker.wait()
        if status["open"]:
            return
        timer.stop()
        timer.deleteLater()
        worker.deleteLater()
        global_plugin_manager.open_editors.pop(key, None)
        global_plugin_manager.editor_closed.emit(file_path)
        if status["error"] and not event.is_set():
            QMessageBox.warning(parent, "Erreur du plugin", status["error"])

    worker.checked.connect(checked)
    timer.timeout.connect(lambda: worker.start() if not worker.isRunning() else None)
    timer.start()


# Fenêtres d'éditeurs natifs ouvertes (instance_id -> NativePluginDialog)
_open_native_editors: dict = {}


class NativePluginDialog(QDialog):
    """Fenêtre autonome pour héberger l'interface graphique d'un plugin natif NovaDAW"""
    def __init__(self, plugin, parent=None):
        super().__init__(parent)
        self.plugin = plugin
        self.setWindowTitle(f"{plugin.icon} {plugin.name} - NovaDAW")
        
        # Dimensions par défaut adaptées au type de plugin
        if getattr(plugin, "plugin_type_id", "") == "novadaw.mixer":
            self.resize(880, 480)
        elif getattr(plugin, "plugin_type_id", "") == "novadaw.equalizer":
            self.resize(760, 480)
        elif getattr(plugin, "plugin_type_id", "") == "novadaw.compressor":
            self.resize(680, 460)
        elif getattr(plugin, "plugin_type_id", "") == "novadaw.drum_machine":
            self.resize(800, 640)
        elif getattr(plugin, "plugin_type_id", "") == "novadaw.synth":
            self.resize(920, 680)
        else:
            self.resize(650, 450)

        self.setStyleSheet("""
            QDialog {
                background-color: #12141a;
                color: #e2e8f0;
            }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        editor = plugin.create_editor(self)
        if editor:
            layout.addWidget(editor)

    def closeEvent(self, event):
        _open_native_editors.pop(self.plugin.instance_id, None)
        super().closeEvent(event)


def open_native_plugin_editor(plugin, parent=None):
    """Ouvre ou bascule l'interface d'un plugin natif NovaDAW (Égaliseur, Compresseur, Mixeur)"""
    if not plugin:
        return None
    instance_id = getattr(plugin, "instance_id", None)
    if not instance_id:
        return None

    existing = _open_native_editors.get(instance_id)
    if existing:
        if existing.isVisible() and existing.isActiveWindow():
            existing.close()
            return None
        else:
            existing.show()
            existing.raise_()
            existing.activateWindow()
            return existing

    dlg = NativePluginDialog(plugin, parent)
    _open_native_editors[instance_id] = dlg
    dlg.show()
    return dlg


class PluginFolderManagerDialog(QDialog):
    """Dialogue pour visualiser et gérer les dossiers de scan VST3"""
    folders_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Dossiers de Plugins VST3")
        self.setMinimumSize(540, 320)
        self.resize(600, 380)
        self.setSizeGripEnabled(True)
        self.setStyleSheet("""
            QDialog { background-color: #1a1c24; color: #e2e8f0; }
            QLabel { color: #94a3b8; font-size: 12px; }
            QPushButton {
                background-color: #262936;
                color: #e2e8f0;
                border: 1px solid #3b3f52;
                border-radius: 4px;
                padding: 6px 14px;
                font-size: 12px;
            }
            QPushButton:hover { background-color: #333748; border-color: #38bdf8; }
            QTableWidget {
                background-color: #121318;
                border: 1px solid #282a36;
                gridline-color: #1e2029;
                color: #f8fafc;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        lbl_desc = QLabel(
            "NovaDAW analyse automatiquement les dossiers standards de votre système "
            "(Windows VST3, Steinberg/Cubase) ainsi que vos dossiers personnalisés."
        )
        lbl_desc.setWordWrap(True)
        layout.addWidget(lbl_desc)

        self.table = QTableWidget()
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels(["Dossier de recherche", "Type"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        layout.addWidget(self.table)

        btn_layout = QHBoxLayout()
        self.btn_add = QPushButton("📁 Ajouter un dossier...")
        self.btn_add.setMinimumWidth(150)
        self.btn_add.clicked.connect(self._add_folder)
        self.btn_remove = QPushButton("✕ Supprimer le dossier")
        self.btn_remove.setMinimumWidth(150)
        self.btn_remove.clicked.connect(self._remove_folder)
        self.btn_close = QPushButton("Fermer")
        self.btn_close.setMinimumWidth(80)
        self.btn_close.clicked.connect(self.accept)

        btn_layout.addWidget(self.btn_add)
        btn_layout.addWidget(self.btn_remove)
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_close)
        layout.addLayout(btn_layout)

        self._refresh_table()

    def _refresh_table(self):
        all_dirs = global_plugin_manager.get_all_directories()
        customs = set(global_plugin_manager.custom_directories)
        self.table.setRowCount(len(all_dirs))

        for row, path in enumerate(all_dirs):
            item_path = QTableWidgetItem(path)
            item_path.setToolTip(path)
            is_custom = path in customs
            item_type = QTableWidgetItem("Personnalisé (Cubase/User)" if is_custom else "Système Standard")
            if not is_custom:
                item_path.setForeground(QColor("#94a3b8"))
                item_type.setForeground(QColor("#64748b"))
            else:
                item_path.setForeground(QColor("#38bdf8"))
                item_type.setForeground(QColor("#38bdf8"))

            self.table.setItem(row, 0, item_path)
            self.table.setItem(row, 1, item_type)

    def _add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Sélectionner un dossier de plugins VST3")
        if folder:
            if global_plugin_manager.add_custom_directory(folder):
                self._refresh_table()
                self.folders_changed.emit()
            else:
                QMessageBox.information(self, "Information", "Ce dossier est déjà présent dans la liste.")

    def _remove_folder(self):
        row = self.table.currentRow()
        if row >= 0:
            path = self.table.item(row, 0).text()
            if path in global_plugin_manager.custom_directories:
                global_plugin_manager.remove_custom_directory(path)
                self._refresh_table()
                self.folders_changed.emit()
            else:
                QMessageBox.warning(self, "Action impossible", "Les dossiers système standards ne peuvent pas être supprimés.")


class PluginManagerDialog(QDialog):
    """
    Gestionnaire complet des plugins VST3 inspiré de Cubase.
    Permet de scanner, filtrer, visualiser les instruments et effets et tester leur interface.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Gestionnaire de Plugins VST3 - NovaDAW")
        self.setMinimumSize(780, 420)
        self.resize(850, 520)
        self.setSizeGripEnabled(True)
        self.setStyleSheet("""
            QDialog { background-color: #161820; color: #f1f5f9; }
            QLabel { color: #94a3b8; font-size: 12px; }
            QLineEdit {
                background-color: #1f222e;
                color: #ffffff;
                border: 1px solid #33384a;
                border-radius: 4px;
                padding: 6px 10px;
                font-size: 12px;
            }
            QLineEdit:focus { border-color: #38bdf8; }
            QPushButton {
                background-color: #242838;
                color: #e2e8f0;
                border: 1px solid #383d54;
                border-radius: 4px;
                padding: 6px 14px;
                font-size: 12px;
            }
            QPushButton:hover { background-color: #30354a; border-color: #38bdf8; }
            QPushButton#btn_accent {
                background-color: #0284c7;
                color: #ffffff;
                font-weight: bold;
                border: none;
            }
            QPushButton#btn_accent:hover { background-color: #0369a1; }
            QTableWidget {
                background-color: #121318;
                border: 1px solid #282a36;
                gridline-color: #1e2029;
                color: #f8fafc;
                font-size: 12px;
            }
            QHeaderView::section {
                background-color: #1b1d26;
                color: #94a3b8;
                padding: 6px;
                border: 1px solid #252834;
                font-weight: bold;
            }
            QProgressBar {
                background-color: #121318;
                border: 1px solid #282a36;
                border-radius: 4px;
                text-align: center;
                color: #ffffff;
                height: 16px;
            }
            QProgressBar::chunk { background-color: #38bdf8; border-radius: 3px; }
        """)

        self.scan_worker: Optional[PluginScanWorker] = None
        self._init_ui()
        self._populate_table()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # 1. Barre d'outils supérieure
        top_bar = QHBoxLayout()
        top_bar.setSpacing(8)

        self.txt_search = QLineEdit()
        self.txt_search.setPlaceholderText("🔍 Filtrer les plugins par nom...")
        self.txt_search.textChanged.connect(self._filter_table)
        top_bar.addWidget(self.txt_search, stretch=2)

        self.btn_scan = QPushButton("🔍 Scanner les dossiers")
        self.btn_scan.setObjectName("btn_accent")
        self.btn_scan.setMinimumWidth(150)
        self.btn_scan.clicked.connect(lambda: self.start_scan(deep_scan=False))
        top_bar.addWidget(self.btn_scan)

        self.btn_deep_scan = QPushButton("🌐 Scanner tout le PC")
        self.btn_deep_scan.setMinimumWidth(140)
        self.btn_deep_scan.setToolTip("Recherche en profondeur tous les plugins VST3 et VST2 sur l'ordinateur")
        self.btn_deep_scan.clicked.connect(lambda: self.start_scan(deep_scan=True))
        top_bar.addWidget(self.btn_deep_scan)

        self.btn_add_file = QPushButton("➕ Ajouter un .vst3...")
        self.btn_add_file.setMinimumWidth(130)
        self.btn_add_file.clicked.connect(self._add_single_file)
        top_bar.addWidget(self.btn_add_file)

        self.btn_folders = QPushButton("📁 Gérer dossiers...")
        self.btn_folders.setMinimumWidth(125)
        self.btn_folders.clicked.connect(self._open_folders_dialog)
        top_bar.addWidget(self.btn_folders)

        layout.addLayout(top_bar)

        # 2. Zone de progression (masquée par défaut)
        self.progress_frame = QFrame()
        self.progress_frame.setVisible(False)
        pf_layout = QVBoxLayout(self.progress_frame)
        pf_layout.setContentsMargins(0, 4, 0, 4)
        pf_layout.setSpacing(4)

        self.lbl_progress = QLabel("Analyse des plugins...")
        pf_layout.addWidget(self.lbl_progress)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        pf_layout.addWidget(self.progress_bar)
        layout.addWidget(self.progress_frame)

        # 3. Tableau des plugins
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["Nom du Plugin", "Type & Format", "Compatibilité", "Paramètres", "Emplacement"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.doubleClicked.connect(self._on_table_double_clicked)
        layout.addWidget(self.table)

        # 4. Barre d'action inférieure
        bottom_bar = QHBoxLayout()
        self.lbl_stats = QLabel("")
        bottom_bar.addWidget(self.lbl_stats)

        bottom_bar.addStretch()

        self.btn_open_gui = QPushButton("🎹 Ouvrir l'interface du plugin [e]")
        self.btn_open_gui.setMinimumWidth(215)
        self.btn_open_gui.clicked.connect(self._open_selected_editor)
        bottom_bar.addWidget(self.btn_open_gui)

        self.btn_close = QPushButton("Fermer")
        self.btn_close.setMinimumWidth(80)
        self.btn_close.clicked.connect(self.accept)
        bottom_bar.addWidget(self.btn_close)

        layout.addLayout(bottom_bar)

    def _populate_table(self):
        plugins = global_plugin_manager.plugins
        self.table.setRowCount(len(plugins))

        inst_count = 0
        fx_count = 0
        comp_count = 0
        vst2_count = 0

        for row, p in enumerate(plugins):
            # Nom
            item_name = QTableWidgetItem(p.name)
            item_name.setData(Qt.UserRole, p.file_path)

            # Type & Format
            fmt_badge = f" [{p.format.upper()}]" if hasattr(p, "format") and p.format else " [VST3]"
            if p.format == "vst2":
                vst2_count += 1

            if p.plugin_type == "instrument":
                type_str = f"🎹 Instrument{fmt_badge}"
                type_color = "#38bdf8"
                inst_count += 1
            elif p.plugin_type == "effect":
                type_str = f"🎛️ Effet{fmt_badge}"
                type_color = "#a855f7"
                fx_count += 1
            else:
                type_str = f"❓ Inconnu{fmt_badge}"
                type_color = "#94a3b8"

            item_type = QTableWidgetItem(type_str)
            item_type.setForeground(QColor(type_color))

            # Compatibilité
            if p.is_compatible:
                comp_str = "Compatible ✅"
                comp_color = "#10b981"
                comp_count += 1
            elif p.format == "vst2":
                comp_str = "Hérité VST 2.4 ⚠️ (MAJ requise)"
                comp_color = "#f59e0b"
            else:
                comp_str = "Incompatible ❌"
                comp_color = "#ef4444"

            item_comp = QTableWidgetItem(comp_str)
            item_comp.setForeground(QColor(comp_color))
            if p.error_message:
                item_comp.setToolTip(f"Détail : {p.error_message}")

            # Paramètres
            item_params = QTableWidgetItem(str(p.parameters_count))
            item_params.setTextAlignment(Qt.AlignCenter)

            # Chemin
            item_path = QTableWidgetItem(p.file_path)
            item_path.setToolTip(p.file_path)
            item_path.setForeground(QColor("#64748b"))

            self.table.setItem(row, 0, item_name)
            self.table.setItem(row, 1, item_type)
            self.table.setItem(row, 2, item_comp)
            self.table.setItem(row, 3, item_params)
            self.table.setItem(row, 4, item_path)

        vst2_info = f" ({vst2_count} VST2 hérités)" if vst2_count > 0 else ""
        self.lbl_stats.setText(
            f"Total : {len(plugins)} plugins ({inst_count} instruments, {fx_count} effets) | "
            f"{comp_count} compatibles{vst2_info}"
        )
        self._filter_table(self.txt_search.text())

    def _filter_table(self, query: str):
        q = query.strip().lower()
        for row in range(self.table.rowCount()):
            name_item = self.table.item(row, 0)
            type_item = self.table.item(row, 1)
            path_item = self.table.item(row, 4)
            text = (name_item.text() if name_item else "") + " " + (type_item.text() if type_item else "") + " " + (path_item.text() if path_item else "")
            self.table.setRowHidden(row, q not in text.lower())

    def reject(self):
        if getattr(self, "scan_worker", None) and self.scan_worker.isRunning():
            self.scan_worker.requestInterruption()
            self.lbl_progress.setText("Arrêt après le plugin en cours…")
            return
        super().reject()

    def start_scan(self, deep_scan: bool = False):
        """Lance un scan asynchrone des répertoires standards et personnalisés"""
        if getattr(self, "scan_worker", None) and self.scan_worker.isRunning():
            return
        dirs = global_plugin_manager.get_all_directories()
        if not dirs:
            QMessageBox.information(self, "Aucun dossier", "Aucun dossier de plugins configuré.")
            return

        self.btn_scan.setEnabled(False)
        self.btn_deep_scan.setEnabled(False)
        self.progress_frame.setVisible(True)
        self.lbl_progress.setText("Recherche approfondie des plugins VST3 et VST2 sur l'ordinateur..." if deep_scan else "Recherche des plugins VST3 et VST2...")
        self.progress_bar.setValue(0)

        cache_map = {p.file_path: p for p in global_plugin_manager.plugins}
        self.scan_worker = PluginScanWorker(
            directories=dirs,
            parent=self,
            incremental=True,
            existing_cache=cache_map,
            deep_scan=deep_scan
        )
        self.scan_worker.progress.connect(self._on_scan_progress)
        self.scan_worker.finished_scan.connect(self._on_scan_finished)
        self.scan_worker.start()

    def _on_scan_progress(self, current: int, total: int, name: str):
        pct = int((current / max(1, total)) * 100)
        self.progress_bar.setValue(pct)
        self.lbl_progress.setText(f"Analyse ({current}/{total}) : {name}")

    def _on_scan_finished(self, results: list):
        self.progress_frame.setVisible(False)
        self.btn_scan.setEnabled(True)
        self.btn_deep_scan.setEnabled(True)
        self.scan_worker.wait()
        if self.scan_worker.isInterruptionRequested():
            found = {p.file_path: p for p in global_plugin_manager.plugins}
            found.update({p.file_path: p for p in results})
            results = list(found.values())
        global_plugin_manager.plugins = results
        global_plugin_manager.save_cache()
        global_plugin_manager.scan_updated.emit()
        self._populate_table()
        QMessageBox.information(
            self,
            "Scan terminé",
            f"Analyse terminée avec succès !\n{len(results)} plugins trouvés et validés."
        )

    def _add_single_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Ajouter un plugin VST3",
            "",
            "Plugins VST3 (*.vst3);;Tous les fichiers (*.*)"
        )
        if file_path:
            info = analyze_plugin_file(file_path, self)
            self._populate_table()
            status = "compatible et prêt à l'emploi ✅" if info.is_compatible else f"incompatible ❌ ({info.error_message})"
            QMessageBox.information(
                self,
                "Plugin ajouté",
                f"Le plugin '{info.name}' a été analysé :\n{status}"
            )

    def _open_folders_dialog(self):
        dlg = PluginFolderManagerDialog(self)
        dlg.folders_changed.connect(self.start_scan)
        dlg.exec()

    def _open_selected_editor(self):
        row = self.table.currentRow()
        if row >= 0:
            name_item = self.table.item(row, 0)
            file_path = name_item.data(Qt.UserRole)
            info = next((p for p in global_plugin_manager.plugins if p.file_path == file_path), None)
            if info and not info.is_compatible:
                QMessageBox.information(
                    self,
                    f"Informations sur {info.name}",
                    f"Plugin : {info.name}\n"
                    f"Format : {getattr(info, 'format', 'inconnu').upper()}\n"
                    f"Fichier : {info.file_path}\n\n"
                    f"Diagnostic :\n{info.error_message or 'Plugin incompatible ou non chargeable directement.'}"
                )
                return
            open_plugin_editor_gui(file_path, self)
        else:
            QMessageBox.information(self, "Sélection requise", "Veuillez sélectionner un plugin dans la liste.")

    def _on_table_double_clicked(self, index):
        self._open_selected_editor()
