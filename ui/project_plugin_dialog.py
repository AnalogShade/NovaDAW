"""One entry point for adding instruments and effects to the project."""
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QPushButton)
from PySide6.QtCore import Qt
from core.plugin_manager import global_plugin_manager


class ProjectPluginDialog(QDialog):
    def __init__(self, rack):
        super().__init__(rack)
        self.rack = rack
        self.setWindowTitle("Ajouter un plugin au projet")
        self.resize(600, 440)
        layout = QVBoxLayout(self)
        description = QLabel("1. Ajoutez un instrument ou un effet au projet.\n"
                             "2. Sur une piste MIDI, choisissez sa Sortie MIDI — Instrument.\n"
                             "Chaque piste conserve ses propres réglages. Les effets vont dans sa pile d’effets.")
        description.setWordWrap(True)
        layout.addWidget(description)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Rechercher un plugin…")
        layout.addWidget(self.search)
        self.list = QListWidget()
        layout.addWidget(self.list)
        self.search.textChanged.connect(self.filter)
        buttons = QHBoxLayout()
        scan = QPushButton("Rechercher les plugins installés…")
        scan.setAutoDefault(False)
        scan.clicked.connect(self.scan)
        buttons.addWidget(scan)
        browse = QPushButton("Parcourir .vst3…")
        browse.setAutoDefault(False)
        browse.clicked.connect(rack._browse_vst_file)
        buttons.addWidget(browse)
        layout.addLayout(buttons)
        self.add = QPushButton("Ajouter au projet")
        self.add.setDefault(True)
        self.add.setEnabled(False)
        self.add.clicked.connect(self.add_selected)
        self.list.itemSelectionChanged.connect(lambda: self.add.setEnabled(bool(self.list.currentItem())))
        self.list.itemDoubleClicked.connect(self.add_selected)
        layout.addWidget(self.add)
        global_plugin_manager.scan_updated.connect(self.populate)
        self.populate()

    def populate(self):
        self.list.clear()
        entries = [
            ("Nova Drums VSTi (Batterie IA)", "novadaw.drum_machine", "instrument"),
            ("⚡ NovaSynth (Synthétiseur Polyphonique)", "novadaw.synth", "instrument"),
            ("Égaliseur Paramétrique", "novadaw.equalizer", "effect"),
            ("Compresseur Dynamique", "novadaw.compressor", "effect"),
            ("Mixeur de Pistes", "novadaw.mixer", "effect"),
        ]
        entries += [(p.name, p.file_path, p.plugin_type) for p in global_plugin_manager.plugins
                    if p.is_compatible and p.plugin_type in ("instrument", "effect")]
        # Inclure également les plugins VST2 détectés (ex: Omnisphere) pour que l'utilisateur les voie
        for p in global_plugin_manager.plugins:
            if not p.is_compatible and getattr(p, "format", "") == "vst2":
                entries.append((f"{p.name} [Hérité VST 2.4]", p.file_path, f"{p.plugin_type}_vst2"))

        for name, path, kind in entries:
            present = any(p["file_path"] == path for p in self.rack.project.plugin_rack)
            type_label = "Instrument" if "instrument" in kind else "Effet"
            vst2_tag = " ⚠️ (VST2)" if "_vst2" in kind else ""
            item = QListWidgetItem(f"{type_label} · {name}{vst2_tag}"
                                   + (" — déjà dans le projet" if present else ""))
            item.setData(Qt.UserRole, (path, name.replace(" [Hérité VST 2.4]", ""), kind))
            item.setToolTip(path)
            self.list.addItem(item)
        self.filter(self.search.text())

    def filter(self, query):
        first_visible = None
        for i in range(self.list.count()):
            item = self.list.item(i)
            item.setHidden(query.casefold() not in item.text().casefold())
            if not item.isHidden() and first_visible is None:
                first_visible = item
        if first_visible is not None:
            self.list.setCurrentItem(first_visible)
        else:
            self.list.setCurrentRow(-1)
        self.add.setEnabled(first_visible is not None)

    def scan(self):
        self.rack._trigger_scan()
        self.populate()

    def add_selected(self, *_):
        item = self.list.currentItem()
        if item:
            path, name, kind = item.data(Qt.UserRole)
            if kind.endswith("_vst2"):
                from PySide6.QtWidgets import QMessageBox
                info = next((p for p in global_plugin_manager.plugins if p.file_path == path), None)
                diag = info.error_message if info else "Format VST 2.4 (.dll) hérité."
                QMessageBox.information(
                    self,
                    f"Plugin détecté : {name}",
                    f"Le plugin '{name}' a bien été détecté sur votre système !\n\n"
                    f"Cependant, il est au format VST 2.4 (.dll) hérité.\n"
                    f"NovaDAW utilise l'architecture standard VST3 64-bit moderne.\n\n"
                    f"Pour l'utiliser directement dans le Rack et vos pistes MIDI :\n"
                    f"• Installez la mise à jour officielle VST3 de l'éditeur (ex: Omnisphere.vst3)\n"
                    f"• Ou utilisez un adaptateur / wrapper VST3 vers VST2.\n\n"
                    f"Emplacement détecté :\n{path}"
                )
                return
            clean_kind = "instrument" if "instrument" in kind else "effect"
            if self.rack._add_plugin(path, name, clean_kind):
                self.accept()
