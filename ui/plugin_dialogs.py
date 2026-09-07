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
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor

from core.plugin_manager import (
    global_plugin_manager,
    PluginScanWorker,
    PluginLoadWorker,
    PluginInfo,
)


def _watch_and_front_plugin_window(plugin_name: str, parent_hwnd: int, close_event: threading.Event):
    """
    Surveille l'apparition de la fenêtre native créée par le plugin (JUCE / Pedalboard),
    la renomme avec un titre explicite NovaDAW, la rattache comme fenêtre fille/possédée
    par NovaDAW pour qu'elle ne disparaisse JAMAIS derrière la fenêtre maximisée,
    et la force au premier plan avec sa barre de titre et son bouton [X].
    """
    if sys.platform != "win32":
        return

    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        target_hwnd = None
        pid = os.getpid()

        def enum_cb(hwnd, _):
            nonlocal target_hwnd
            if user32.IsWindowVisible(hwnd):
                cur_pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(cur_pid))
                if cur_pid.value == pid:
                    length = user32.GetWindowTextLengthW(hwnd)
                    buff = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buff, length + 1)
                    title = buff.value
                    if title == "Pedalboard" or "pedalboard" in title.lower() or plugin_name.lower() in title.lower():
                        target_hwnd = hwnd
                        return False
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

        # Attendre que la fenêtre apparaisse (jusqu'à 12 secondes, par pas de 100ms)
        for _ in range(120):
            if close_event.is_set():
                return
            user32.EnumWindows(WNDENUMPROC(enum_cb), 0)
            if target_hwnd:
                break
            time.sleep(0.1)

        if target_hwnd and not close_event.is_set():
            # 1. Renommer la fenêtre pour que l'utilisateur sache exactement quel plugin est ouvert
            user32.SetWindowTextW(target_hwnd, f"{plugin_name} — NovaDAW VST3")

            # 2. Rattacher la fenêtre à NovaDAW via GWLP_HWNDPARENT
            # En Win32, une fenêtre "owned" reste TOUJOURS au-dessus de son propriétaire,
            # minimise avec lui, et se ferme avec lui.
            if parent_hwnd:
                GWLP_HWNDPARENT = -8
                try:
                    user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
                    user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
                    user32.SetWindowLongPtrW(target_hwnd, GWLP_HWNDPARENT, parent_hwnd)
                except Exception:
                    pass

            # 3. Forcer la fenêtre au premier plan absolu
            SW_RESTORE = 9
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            SWP_SHOWWINDOW = 0x0040
            HWND_TOPMOST = -1
            HWND_NOTOPMOST = -2

            user32.ShowWindow(target_hwnd, SW_RESTORE)
            user32.SetWindowPos(target_hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
            user32.SetWindowPos(target_hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
            user32.BringWindowToTop(target_hwnd)
            user32.SetForegroundWindow(target_hwnd)

    except Exception as e:
        print(f"[PluginWatcher] Information fenêtre native : {e}")


class PluginLoadingDialog(QDialog):
    """
    Dialogue moderne affiché pendant le chargement en arrière-plan d'un plugin VST3 lourd.
    Empêche le gel de l'interface et informe clairement l'utilisateur.
    """
    def __init__(self, file_path: str, plugin_name: str, parent=None):
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

        # Pied avec statut et bouton Annuler
        footer = QHBoxLayout()
        self.status_lbl = QLabel("Veuillez patienter quelques secondes...")
        self.status_lbl.setStyleSheet("font-size: 11px; color: #64748b; font-style: italic;")
        footer.addWidget(self.status_lbl, stretch=1)

        self.btn_cancel = QPushButton("Annuler")
        self.btn_cancel.clicked.connect(self.reject)
        footer.addWidget(self.btn_cancel)
        layout.addLayout(footer)

        # Démarrer le worker asynchrone
        self.worker = PluginLoadWorker(self.file_path, self)
        self.worker.loaded.connect(self._on_loaded)
        self.worker.error.connect(self._on_error)
        self.worker.start()

    def _on_loaded(self, path: str, plugin: object):
        self.loaded_plugin = plugin
        self.accept()

    def _on_error(self, path: str, err: str):
        self.error_message = err
        self.reject()

    def closeEvent(self, event):
        if self.worker.isRunning():
            self.worker.quit()
        super().closeEvent(event)


def open_plugin_editor_gui(file_path: str, parent=None):
    """
    Ouvre l'interface graphique native du plugin VST3 avec :
    1. Retour visuel pendant le chargement initial (popup avec barre de progression).
    2. Garantie que la fenêtre native s'affiche TOUJOURS au premier plan devant NovaDAW.
    3. Titre de fenêtre clair avec le nom du plugin et NovaDAW.
    4. Contrôle complet de fermeture : bouton [X] natif ou re-clic sur [e].
    """
    if not file_path or not os.path.exists(file_path):
        QMessageBox.warning(parent, "Plugin introuvable", f"Le fichier du plugin n'existe pas :\n{file_path}")
        return

    # Si l'éditeur de ce plugin est déjà ouvert, re-cliquer ferme la fenêtre (toggle)
    if global_plugin_manager.is_editor_open(file_path):
        global_plugin_manager.close_editor(file_path)
        return

    # Nom du plugin
    plugin_name = os.path.splitext(os.path.basename(file_path))[0]
    for p in global_plugin_manager.plugins:
        if p.file_path == file_path:
            plugin_name = p.name
            break

    # 1. Vérifier si le plugin est déjà instancié en mémoire
    plugin = global_plugin_manager.active_instances.get(file_path)
    if plugin is None:
        # Afficher la boîte de dialogue de chargement en arrière-plan avec barre animée
        load_dlg = PluginLoadingDialog(file_path, plugin_name, parent)
        res = load_dlg.exec()
        if res != QDialog.Accepted or load_dlg.loaded_plugin is None:
            if load_dlg.error_message:
                QMessageBox.critical(parent, "Erreur de chargement", f"Impossible d'instancier {plugin_name} :\n{load_dlg.error_message}")
            return
        plugin = load_dlg.loaded_plugin
        global_plugin_manager.active_instances[file_path] = plugin

    # 2. Préparer l'événement de fermeture et l'ancrage au premier plan
    close_event = threading.Event()
    global_plugin_manager.open_editors[file_path] = close_event
    global_plugin_manager.editor_opened.emit(file_path)

    # Récupérer l'identifiant Win32 de NovaDAW
    parent_hwnd = 0
    if parent is not None:
        top_win = parent.window() if hasattr(parent, "window") else parent
        if hasattr(top_win, "winId"):
            try:
                parent_hwnd = int(top_win.winId())
            except Exception:
                pass

    # Démarrer le watcher qui renomme et force la fenêtre au premier plan
    watcher_thread = threading.Thread(
        target=_watch_and_front_plugin_window,
        args=(plugin_name, parent_hwnd, close_event),
        daemon=True
    )
    watcher_thread.start()

    # 3. Affichage de l'interface native du VST
    try:
        plugin.show_editor(close_event)
    except Exception as e:
        QMessageBox.critical(parent, "Erreur Plugin", f"Impossible d'afficher l'interface de {plugin_name} :\n{e}")
    finally:
        global_plugin_manager.open_editors.pop(file_path, None)
        global_plugin_manager.editor_closed.emit(file_path)


class PluginFolderManagerDialog(QDialog):
    """Dialogue pour visualiser et gérer les dossiers de scan VST3"""
    folders_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Dossiers de Plugins VST3")
        self.resize(600, 380)
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
        self.btn_add.clicked.connect(self._add_folder)
        self.btn_remove = QPushButton("✕ Supprimer le dossier")
        self.btn_remove.clicked.connect(self._remove_folder)
        self.btn_close = QPushButton("Fermer")
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
        self.resize(850, 520)
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
        self.btn_scan.clicked.connect(self.start_scan)
        top_bar.addWidget(self.btn_scan)

        self.btn_add_file = QPushButton("➕ Ajouter un .vst3...")
        self.btn_add_file.clicked.connect(self._add_single_file)
        top_bar.addWidget(self.btn_add_file)

        self.btn_folders = QPushButton("📁 Gérer dossiers...")
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
        self.table.setHorizontalHeaderLabels(["Nom du Plugin", "Type", "Compatibilité", "Paramètres", "Emplacement"])
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
        self.btn_open_gui.clicked.connect(self._open_selected_editor)
        bottom_bar.addWidget(self.btn_open_gui)

        self.btn_close = QPushButton("Fermer")
        self.btn_close.clicked.connect(self.accept)
        bottom_bar.addWidget(self.btn_close)

        layout.addLayout(bottom_bar)

    def _populate_table(self):
        plugins = global_plugin_manager.plugins
        self.table.setRowCount(len(plugins))

        inst_count = 0
        fx_count = 0
        comp_count = 0

        for row, p in enumerate(plugins):
            # Nom
            item_name = QTableWidgetItem(p.name)
            item_name.setData(Qt.UserRole, p.file_path)

            # Type
            if p.plugin_type == "instrument":
                type_str = "🎹 Instrument (VSTi)"
                type_color = "#38bdf8"
                inst_count += 1
            elif p.plugin_type == "effect":
                type_str = "🎛️ Effet (VST FX)"
                type_color = "#a855f7"
                fx_count += 1
            else:
                type_str = "❓ Inconnu"
                type_color = "#94a3b8"

            item_type = QTableWidgetItem(type_str)
            item_type.setForeground(QColor(type_color))

            # Compatibilité
            if p.is_compatible:
                comp_str = "Compatible ✅"
                comp_color = "#10b981"
                comp_count += 1
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

        self.lbl_stats.setText(
            f"Total : {len(plugins)} plugins ({inst_count} instruments, {fx_count} effets) | "
            f"{comp_count} compatibles"
        )
        self._filter_table(self.txt_search.text())

    def _filter_table(self, query: str):
        q = query.strip().lower()
        for row in range(self.table.rowCount()):
            name_item = self.table.item(row, 0)
            type_item = self.table.item(row, 1)
            text = (name_item.text() if name_item else "") + " " + (type_item.text() if type_item else "")
            self.table.setRowHidden(row, q not in text.lower())

    def start_scan(self):
        """Lance un scan asynchrone des répertoires standards et personnalisés"""
        dirs = global_plugin_manager.get_all_directories()
        if not dirs:
            QMessageBox.information(self, "Aucun dossier", "Aucun dossier de plugins configuré.")
            return

        self.btn_scan.setEnabled(False)
        self.progress_frame.setVisible(True)
        self.lbl_progress.setText("Recherche des fichiers VST3...")
        self.progress_bar.setValue(0)

        self.scan_worker = PluginScanWorker(dirs, self)
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
            info = global_plugin_manager.add_plugin_file(file_path)
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
            open_plugin_editor_gui(file_path, self)
        else:
            QMessageBox.information(self, "Sélection requise", "Veuillez sélectionner un plugin dans la liste.")

    def _on_table_double_clicked(self, index):
        row = index.row()
        name_item = self.table.item(row, 0)
        if name_item:
            file_path = name_item.data(Qt.UserRole)
            open_plugin_editor_gui(file_path, self)
