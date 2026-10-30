"""Centre d'aide hors ligne, consultable pendant l'édition du projet."""
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPushButton, QSplitter, QTextBrowser, QVBoxLayout,
)
from ui.help_content import TOPIC_BY_ID, render_topic, search_topics


class HelpDialog(QDialog):
    """Le manuel ne déclenche aucune action projet ou audio."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Aide — Manuel Nova DAW")
        self.resize(1000, 720)
        self.setMinimumSize(480, 360)
        self.setSizeGripEnabled(True)
        self.current_topic_id = None
        self._history = []
        self._history_index = -1
        self.setStyleSheet("""
            QDialog { background: #14151c; color: #e2e8f0; }
            QLabel { color: #cbd5e1; }
            QLineEdit, QListWidget, QTextBrowser, QComboBox {
                background: #171822; color: #e2e8f0; border: 1px solid #34384b;
                border-radius: 4px; padding: 8px; font-size: 13px;
            }
            QListWidget::item { padding: 8px 4px; }
            QListWidget::item:selected { background: #164e63; color: #ffffff; }
            QPushButton { background: #252834; color: #e2e8f0;
                border: 1px solid #475569; border-radius: 4px; padding: 6px 10px; }
            QPushButton:hover { background: #164e63; }
            QPushButton:disabled { color: #64748b; }
            QPushButton:focus, QLineEdit:focus, QListWidget:focus, QTextBrowser:focus {
                border: 2px solid #38bdf8;
            }
        """)
        layout = QVBoxLayout(self)
        title = QLabel("Nova DAW — Apprendre et créer")
        title.setWordWrap(True)
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #38bdf8;")
        layout.addWidget(title)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Rechercher dans tout le manuel… (Ctrl+F)")
        self.search.setAccessibleName("Rechercher dans tout le manuel")
        self.search.setClearButtonEnabled(True)
        layout.addWidget(self.search)
        self.compact_topics = QComboBox()
        self.compact_topics.setAccessibleName("Choisir un chapitre ou un résultat")
        self.compact_topics.setMinimumWidth(0)
        self.compact_topics.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.compact_topics.setMinimumContentsLength(12)
        self.compact_topics.hide()
        layout.addWidget(self.compact_topics)
        self.splitter = QSplitter(Qt.Horizontal)
        self.topics = QListWidget()
        self.topics.setAccessibleName("Chapitres et résultats de recherche")
        self.topics.setWordWrap(True)
        self.topics.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.topics.setMinimumWidth(130)
        self.browser = QTextBrowser()
        self.browser.setAccessibleName("Contenu du manuel et table des matières")
        self.browser.setOpenLinks(False)
        self.browser.setOpenExternalLinks(False)
        self.browser.setMinimumWidth(220)
        self.splitter.addWidget(self.topics)
        self.splitter.addWidget(self.browser)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([260, 700])
        self.splitter.setChildrenCollapsible(False)
        layout.addWidget(self.splitter, 1)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setAccessibleName("État de la recherche")
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        self.back_button = QPushButton("← Précédent")
        self.forward_button = QPushButton("Suivant →")
        self.home_button = QPushButton("Accueil")
        self.close_button = QPushButton("Fermer")
        for button in (self.back_button, self.forward_button, self.home_button, self.close_button):
            button.setAutoDefault(False)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.search.textChanged.connect(self._filter)
        self.search.returnPressed.connect(self._open_first_result)
        self.topics.currentItemChanged.connect(self._selected)
        self.compact_topics.activated.connect(
            lambda index: self.show_topic(self.compact_topics.itemData(index)))
        self.browser.anchorClicked.connect(self._link_clicked)
        self.back_button.clicked.connect(lambda: self._move_history(-1))
        self.forward_button.clicked.connect(lambda: self._move_history(1))
        self.home_button.clicked.connect(lambda: self.show_topic("welcome"))
        self.close_button.clicked.connect(self.close)
        for key, callback in (("Ctrl+F", self.search.setFocus),
                              ("Alt+Left", lambda: self._move_history(-1)),
                              ("Alt+Right", lambda: self._move_history(1))):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(callback)
        self._filter("")
        self.show_topic("welcome")

    def _filter(self, query):
        results = search_topics(query)
        self.topics.blockSignals(True)
        self.topics.clear()
        self.compact_topics.clear()
        last_category = None
        for topic in results:
            self.compact_topics.addItem(f"{topic.category} — {topic.title}", topic.id)
            if topic.category != last_category:
                header = QListWidgetItem(topic.category.upper())
                header.setFlags(Qt.NoItemFlags)
                self.topics.addItem(header)
                last_category = topic.category
            item = QListWidgetItem(topic.title)
            item.setData(Qt.UserRole, topic.id)
            self.topics.addItem(item)
            if topic.id == self.current_topic_id:
                self.topics.setCurrentItem(item)
        self.topics.blockSignals(False)
        self.compact_topics.setCurrentIndex(self.compact_topics.findData(self.current_topic_id))
        self.status.setText(
            f"{len(results)} chapitre(s) — Entrée ouvre le premier résultat."
            if results else "Aucun résultat. Essayez moins de mots, ou effacez la recherche.")

    def _open_first_result(self):
        for index in range(self.topics.count()):
            item = self.topics.item(index)
            if item.data(Qt.UserRole):
                self.topics.setCurrentItem(item)
                self.show_topic(item.data(Qt.UserRole))
                self.browser.setFocus()
                return

    def _selected(self, item, previous):
        if item and item.data(Qt.UserRole):
            self.show_topic(item.data(Qt.UserRole))

    def show_topic(self, topic_id, *, remember=True):
        if topic_id not in TOPIC_BY_ID:
            return
        if remember and topic_id != self.current_topic_id:
            self._history = self._history[:self._history_index + 1]
            self._history.append(topic_id)
            self._history_index = len(self._history) - 1
        self.current_topic_id = topic_id
        self.browser.setHtml(render_topic(TOPIC_BY_ID[topic_id]))
        self.topics.blockSignals(True)
        self.topics.setCurrentRow(-1)
        for index in range(self.topics.count()):
            if self.topics.item(index).data(Qt.UserRole) == topic_id:
                self.topics.setCurrentRow(index)
                break
        self.topics.blockSignals(False)
        self.compact_topics.setCurrentIndex(self.compact_topics.findData(topic_id))
        self.back_button.setEnabled(self._history_index > 0)
        self.forward_button.setEnabled(self._history_index + 1 < len(self._history))

    def _move_history(self, delta):
        index = self._history_index + delta
        if 0 <= index < len(self._history):
            self._history_index = index
            self.show_topic(self._history[index], remember=False)

    def _link_clicked(self, url: QUrl):
        if url.scheme() == "help":
            self.show_topic(url.path())
        elif not url.scheme() and url.fragment():
            self.browser.scrollToAnchor(url.fragment())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        compact = self.width() < 700
        self.topics.setVisible(not compact)
        self.compact_topics.setVisible(compact)
