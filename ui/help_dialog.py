"""
ui/help_dialog.py - Boîte de dialogue d'aide et documentation intégrée pour NovaDAW
"""
from PySide6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QTabWidget, QLabel,
    QPushButton, QTextBrowser, QTableWidget, QTableWidgetItem,
    QHeaderView, QFrame
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QColor


class HelpDialog(QDialog):
    """Fenêtre d'aide contenant les raccourcis clavier et le manuel d'utilisation"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Documentation & Raccourcis Clavier - NovaDAW")
        self.resize(760, 560)
        self.setMinimumSize(640, 480)
        self.setStyleSheet("""
            QDialog {
                background-color: #14151c;
            }
            QTabWidget::pane {
                border: 1px solid #282a38;
                background-color: #171822;
                border-radius: 4px;
            }
            QTabBar::tab {
                background: #1e202c;
                border: 1px solid #282a38;
                border-bottom: none;
                padding: 8px 18px;
                margin-right: 3px;
                border-top-left-radius: 5px;
                border-top-right-radius: 5px;
                color: #94a3b8;
                font-weight: 600;
            }
            QTabBar::tab:selected {
                background: #252834;
                color: #38bdf8;
                border-bottom: 2px solid #38bdf8;
            }
            QTableWidget {
                background-color: #171822;
                border: none;
                gridline-color: #262838;
                color: #e2e8f0;
            }
            QTableWidget::item {
                padding: 6px 12px;
                border-bottom: 1px solid #202230;
            }
            QTableWidget::item:selected {
                background-color: #0284c7;
                color: #ffffff;
            }
            QHeaderView::section {
                background-color: #1c1e2a;
                color: #38bdf8;
                font-weight: bold;
                padding: 6px 10px;
                border: none;
                border-bottom: 1px solid #2d3042;
            }
            QTextBrowser {
                background-color: #171822;
                border: none;
                color: #e2e8f0;
                padding: 16px;
                font-size: 13px;
                line-height: 1.6;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # En-tête
        header_layout = QHBoxLayout()
        lbl_title = QLabel("🎵 NovaDAW — Centre d'Aide & Documentation")
        lbl_title.setStyleSheet("font-size: 16px; font-weight: bold; color: #38bdf8;")
        header_layout.addWidget(lbl_title)
        header_layout.addStretch()
        layout.addLayout(header_layout)

        # Onglets
        tabs = QTabWidget()

        # Onglet 1 : Raccourcis Clavier
        tabs.addTab(self._create_shortcuts_tab(), "⌨️ Raccourcis Clavier & Souris")

        # Onglet 2 : Guide des Fonctionnalités
        tabs.addTab(self._create_guide_tab(), "📖 Guide des Fonctionnalités")

        # Onglet 3 : À propos
        tabs.addTab(self._create_about_tab(), "ℹ️ À Propos")

        layout.addWidget(tabs)

        # Bouton Fermer
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_close = QPushButton("Fermer")
        btn_close.setFixedWidth(100)
        btn_close.setStyleSheet("""
            QPushButton {
                background-color: #0284c7;
                color: #ffffff;
                border: none;
                border-radius: 4px;
                padding: 6px 16px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #0369a1;
            }
        """)
        btn_close.clicked.connect(self.accept)
        btn_layout.addWidget(btn_close)
        layout.addLayout(btn_layout)

    def _create_shortcuts_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(8, 8, 8, 8)

        shortcuts = [
            # Catégorie Transport
            ("Espace", "Lecture / Pause du morceau", "Transport"),
            ("0 ou Num 0", "Arrêt (Stop) et retour au point de départ", "Transport"),
            ("Home / Début", "Déplacer la tête de lecture à la Mesure 1 (début absolu)", "Transport"),
            ("End / Fin", "Déplacer la tête de lecture à la fin du dernier bloc", "Transport"),
            ("Bouton ⏪ (Maintien)", "Rembobiner en continu dans le morceau", "Transport"),
            ("Bouton ⏩ (Maintien)", "Avancer rapidement en continu dans le morceau", "Transport"),
            ("L", "Activer / Désactiver la lecture en boucle (Loop)", "Transport"),
            
            # Catégorie Projet & Fichiers
            ("Ctrl + N", "Créer un Nouveau Projet", "Projet"),
            ("Ctrl + O", "Ouvrir un Projet existant (.ndaw)", "Projet"),
            ("Ctrl + S", "Enregistrer le Projet actuel", "Projet"),
            ("Ctrl + Shift + S", "Enregistrer le Projet sous un nouveau nom", "Projet"),
            ("Ctrl + E", "⚡ Exporter le mixage audio complet en fichier WAV", "Projet"),
            ("Ctrl + Q", "Quitter l'application", "Projet"),
            
            # Catégorie Pistes & Timeline
            ("Ctrl + T", "Ajouter une nouvelle piste (MIDI ou Audio)", "Pistes & Timeline"),
            ("Double-clic sur piste vide", "Créer automatiquement un nouveau bloc/clip sur la mesure ou sur la boucle", "Pistes & Timeline"),
            ("Double-clic sur un bloc", "Ouvrir l'éditeur correspondant (Piano Roll pour MIDI, Inspecteur pour Audio)", "Pistes & Timeline"),
            ("Glisser un bloc", "Déplacer le bloc temporellement sur la timeline", "Pistes & Timeline"),
            ("Glisser le bord droit d'un bloc", "Redimensionner la longueur du bloc", "Pistes & Timeline"),
            ("Clic droit sur un bloc", "Menu contextuel : renommer, supprimer", "Pistes & Timeline"),
            ("Shift + Glisser sur la règle", "Définir rapidement la région de boucle [L / R]", "Pistes & Timeline"),
            
            # Catégorie Piano Roll (Séquenceur MIDI)
            ("Clic sur les touches du piano", "Jouer et prévisualiser immédiatement la note du synthétiseur", "Piano Roll"),
            ("Clic gauche sur la grille", "Poser une note MIDI (joue un son test en direct)", "Piano Roll"),
            ("Glisser une note", "Changer la hauteur de note ou le temps de déclenchement", "Piano Roll"),
            ("Glisser bord droit d'une note", "Ajuster la durée de la note", "Piano Roll"),
            ("Clic droit sur une note", "Supprimer immédiatement la note", "Piano Roll"),
            
            # Catégorie Affichage
            ("F1", "Ouvrir cette fenêtre d'aide et documentation", "Interface"),
            ("F3 ou Bouton ▼/▲", "Minimiser la zone d'édition (ne voir que les onglets) / Agrandir", "Interface"),
        ]

        table = QTableWidget(len(shortcuts), 3)
        table.setHorizontalHeaderLabels(["Raccourci / Action", "Description", "Section"])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectRows)

        for row, (key, desc, cat) in enumerate(shortcuts):
            item_key = QTableWidgetItem(key)
            item_key.setFont(QFont("Consolas", 10, QFont.Bold))
            item_key.setForeground(QColor("#38bdf8"))

            item_desc = QTableWidgetItem(desc)
            item_cat = QTableWidgetItem(cat)
            item_cat.setForeground(QColor("#94a3b8"))

            table.setItem(row, 0, item_key)
            table.setItem(row, 1, item_desc)
            table.setItem(row, 2, item_cat)

        layout.addWidget(table)
        return widget

    def _create_guide_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(8, 8, 8, 8)

        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        html_content = """
        <h2 style="color: #38bdf8; margin-top: 0;">Guide d'Utilisation de NovaDAW</h2>
        
        <p>Bienvenue dans <b>NovaDAW</b>, votre station de travail audio numérique (DAW) open-source conçue pour la création et la composition musicale intuitive.</p>
        
        <hr style="border: 0; border-top: 1px solid #282a38; margin: 16px 0;">

        <h3 style="color: #34d399;">1. Les Pistes (Tracks)</h3>
        <ul>
            <li><b>Piste MIDI (🎹) :</b> Destinée aux instruments virtuels et à la mélodie. Elle utilise le synthétiseur polyphonique interne haute qualité pour jouer vos notes sans latence.</li>
            <li><b>Piste Audio (🔊) :</b> Destinée aux enregistrements de microphones, guitares ou importations d'échantillons (fichiers WAV, etc.).</li>
            <li><b>Contrôles de piste :</b>
                <ul>
                    <li><b>M (Mute) :</b> Coupe temporairement le son de la piste.</li>
                    <li><b>S (Solo) :</b> Isole la piste pour être la seule entendue dans le mixage.</li>
                    <li><b>R (Arm) :</b> Arme la piste pour l'enregistrement.</li>
                    <li><b>Curseurs VOL & PAN :</b> Ajustent le volume et la répartition panoramique (Gauche / Droite).</li>
                </ul>
            </li>
        </ul>

        <h3 style="color: #34d399;">2. La Timeline & Création de Blocs</h3>
        <ul>
            <li><b>Créer un bloc / clip :</b> <i>Double-cliquez</i> simplement sur n'importe quel endroit vide d'une piste. Un bloc est instantanément créé, calé sur la mesure ou sur votre zone de boucle !</li>
            <li><b>Éditer un bloc :</b> <i>Double-cliquez</i> sur un bloc pour l'ouvrir immédiatement dans la zone inférieure.</li>
            <li><b>Déplacer & Redimensionner :</b> Cliquez et glissez le corps du bloc pour le bouger dans le temps. Attrapez son bord droit pour l'allonger ou le raccourcir.</li>
            <li><b>Région de boucle (Loop) :</b> La règle en haut affiche des marqueurs ambre <b>[L]</b> et <b>[R]</b>. Maintenez <i>Shift + Glisser</i> sur la règle pour ajuster la boucle à la souris.</li>
        </ul>

        <h3 style="color: #34d399;">3. Le Séquenceur MIDI (Piano Roll)</h3>
        <ul>
            <li><b>Clavier virtuel interactif :</b> Sur le côté gauche, le clavier vous permet d'entendre immédiatement le son de chaque touche en cliquant dessus.</li>
            <li><b>Ajouter des notes :</b> Cliquez dans la grille pour déposer une note. Le son est joué en temps réel pour vérifier la tonalité.</li>
            <li><b>Modifier / Supprimer :</b> Glissez une note pour la changer de hauteur ou de temps. <i>Clic droit</i> sur une note pour la supprimer.</li>
            <li><b>Magnétisme (Snap) :</b> Utilisez le menu déroulant en haut du Piano Roll pour caler vos notes à la double-croche (1/16), à la croche (1/8), à la noire (1/4), etc.</li>
        </ul>

        <h3 style="color: #34d399;">4. L'Éditeur Audio</h3>
        <ul>
            <li>Double-cliquez sur un bloc Audio pour afficher sa forme d'onde en temps réel.</li>
            <li>Cliquez sur <b>📁 Importer fichier WAV...</b> pour charger un échantillon, une prise de son ou une piste d'accompagnement. La durée du bloc s'ajuste automatiquement au fichier audio.</li>
            <li>Ajustez le fader de gain pour équilibrer le volume du sample.</li>
        </ul>

        <h3 style="color: #34d399;">5. La Barre de Transport Inférieure</h3>
        <ul>
            <li>Située en bas au centre pour un accès naturel :
                <ul>
                    <li><b>⏮ Début :</b> Retourne directement au tout début (Mesure 1).</li>
                    <li><b>⏪ Reculer :</b> Recule d'une mesure, ou rembobine en continu si maintenu.</li>
                    <li><b>■ Stop :</b> Arrête la lecture et replace la tête de lecture.</li>
                    <li><b>▶ Play :</b> Lance la lecture (raccourci <i>Espace</i>).</li>
                    <li><b>⏩ Avancer :</b> Avance d'une mesure, ou avance rapidement en continu si maintenu.</li>
                    <li><b>⏭ Fin :</b> Se positionne après le dernier bloc du projet.</li>
                    <li><b>🔁 Boucle :</b> Boucle en continu sur l'intervalle sélectionné.</li>
                </ul>
            </li>
            <li><b>Minimisation (▼ / ▲) :</b> Cliquez sur la flèche en haut à droite des onglets (ou appuyez sur <b>F3</b>) pour replier la zone inférieure et ne garder que la vue de vos pistes.</li>
        </ul>

        <h3 style="color: #34d399;">6. Sauvegarde et Exportation</h3>
        <ul>
            <li><b>Format propriétaire (.ndaw) :</b> Enregistrez votre projet avec <i>Ctrl + S</i> pour conserver toutes vos pistes, réglages, volumes et notes MIDI.</li>
            <li><b>Export Audio (WAV) :</b> Utilisez le menu <b>Fichier ➔ Exporter Mixage Audio (WAV)...</b> (ou <i>Ctrl + E</i>) pour générer un fichier audio stéréo 44.1 kHz de votre morceau complet !</li>
        </ul>
        """
        browser.setHtml(html_content)
        layout.addWidget(browser)
        return widget

    def _create_about_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        lbl_app = QLabel("NovaDAW")
        lbl_app.setStyleSheet("font-size: 24px; font-weight: bold; color: #38bdf8;")
        layout.addWidget(lbl_app)

        lbl_sub = QLabel("Version 1.0 — Digital Audio Workstation Open-Source")
        lbl_sub.setStyleSheet("font-size: 13px; color: #94a3b8; font-weight: 600;")
        layout.addWidget(lbl_sub)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color: #282a38;")
        layout.addWidget(sep)

        about_text = QLabel(
            "NovaDAW est une station de travail audio numérique moderne, libre et gratuite, "
            "développée en Python avec PySide6 (Qt 6), numpy, scipy et sounddevice.\n\n"
            "Caractéristiques techniques :\n"
            " • Moteur audio temps réel avec synthétiseur polyphonique à modélisation analogique\n"
            " • Mixeur multi-pistes stéréo avec contrôle de panoramique, volume, mute et solo\n"
            " • Séquenceur MIDI interactif avec quantification automatique\n"
            " • Format de projet structuré .ndaw (NovaDAW Project)\n"
            " • Rendu hors-ligne vers fichier WAV stéréo 44.1 kHz haute fidélité\n"
            " • Prêt pour l'accueil de plugins VST3 via pedalboard\n\n"
            "Créé pour rendre la production musicale accessible, créative et ouverte à tous !"
        )
        about_text.setStyleSheet("color: #cbd5e1; font-size: 12px; line-height: 1.5;")
        about_text.setWordWrap(True)
        layout.addWidget(about_text)

        layout.addStretch()
        return widget
