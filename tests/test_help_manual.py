"""Contrats du manuel : liens, recherche, clavier, adaptation et accès réel."""
import ast
from html.parser import HTMLParser
from pathlib import Path

import pytest
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QAction, QFontDatabase, QKeySequence
from PySide6.QtTest import QTest

from ui.help_content import (
    SHORTCUTS, TOPICS, TOPIC_BY_ID, render_topic, search_topics, sections_for,
)
from ui.help_dialog import HelpDialog


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links, self.anchors = [], set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "href" in attrs:
            self.links.append(attrs["href"])
        self.anchors.update(attrs[key] for key in ("id", "name") if key in attrs)


def test_all_chapters_and_links_are_local_and_resolve():
    assert len(TOPICS) == len(TOPIC_BY_ID)
    for topic in TOPICS:
        parser = Links()
        parser.feed(render_topic(topic))
        assert sections_for(topic)
        for link in parser.links:
            if link.startswith("#"):
                assert link[1:] in parser.anchors
            else:
                assert link.startswith("help:")
                assert link[5:] in TOPIC_BY_ID


@pytest.mark.parametrize("query, expected", [
    ("VELOCITE", "piano"), ("pas de son", "troubleshooting"),
    ("ctrl+shift+delete", "shortcuts"), ("MCP", "mcp"),
    ("reverb", "synth"), ("ndaw", "projects"),
])
def test_search_full_content_and_shortcuts(query, expected):
    assert expected in {topic.id for topic in search_topics(query)}


def test_empty_and_unknown_search():
    assert search_topics("  ") == list(TOPICS)
    assert search_topics("zxy-inexistant-9301") == []


def documented_keys():
    # Décomposer les groupes « Ctrl+C / Ctrl+X » et traduire via Qt.
    return {QKeySequence(key.strip()).toString(QKeySequence.PortableText)
            for group, _, _ in SHORTCUTS for key in group.split(" / ")}


def test_every_declared_window_shortcut_is_documented():
    """Détecter l'ajout d'un raccourci de menu oublié dans le manuel."""
    root = Path(__file__).resolve().parents[1]
    tree = ast.parse((root / "ui/main_window.py").read_text(encoding="utf-8"))
    keys = documented_keys()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "setShortcut" and node.args):
            value = node.args[0]
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                assert QKeySequence(value.value).toString(QKeySequence.PortableText) in keys


@pytest.fixture
def manual(qapp):
    # La plateforme Qt offscreen de Windows ne découvre pas les polices système.
    # Charger la police réelle pour tester les retours à la ligne, pas des carrés vides.
    if not QFontDatabase.families():
        font_path = Path("C:/Windows/Fonts/segoeui.ttf")
        if font_path.exists():
            QFontDatabase.addApplicationFont(str(font_path))
    dialog = HelpDialog()
    dialog.show()
    qapp.processEvents()
    yield dialog
    dialog.close()


def test_search_navigation_and_history(manual, qapp):
    assert manual.current_topic_id == "welcome"
    manual.search.setText("quantification")
    QTest.keyClick(manual.search, Qt.Key_Return)
    assert manual.current_topic_id == search_topics("quantification")[0].id
    manual._link_clicked(QUrl("help:piano"))
    manual._link_clicked(QUrl("help:projects"))
    manual.back_button.click()
    assert manual.current_topic_id == "piano"
    manual.forward_button.click()
    assert manual.current_topic_id == "projects"
    manual.back_button.click()
    manual.show_topic("synth")
    assert not manual.forward_button.isEnabled()
    manual._link_clicked(QUrl("help:missing"))
    manual._link_clicked(QUrl("https://example.invalid"))
    assert manual.current_topic_id == "synth"
    manual.search.setText("zxy-inexistant-9301")
    assert manual.topics.count() == 0
    assert "Aucun résultat" in manual.status.text()
    manual.search.clear()
    assert manual.topics.count() > len(TOPICS)  # inclut les catégories


def test_table_of_contents_scrolls(manual, qapp):
    manual.show_topic("shortcuts")
    qapp.processEvents()
    manual._link_clicked(QUrl("#section-12"))
    later_position = manual.browser.verticalScrollBar().value()
    assert later_position > 0
    manual._link_clicked(QUrl("#section-0"))
    assert manual.browser.verticalScrollBar().value() < later_position


@pytest.mark.parametrize("size", [(480, 360), (640, 480), (1000, 720), (1440, 900)])
def test_responsive_navigation_and_document_width(manual, qapp, size):
    manual.resize(*size)
    manual.show_topic("shortcuts")
    qapp.processEvents()
    assert manual.size().width() == size[0]
    assert manual.compact_topics.isVisible() == (size[0] < 700)
    assert manual.topics.isVisible() == (size[0] >= 700)
    assert manual.browser.width() >= 220
    assert manual.close_button.geometry().right() < manual.width()
    # Chaque chapitre doit rester lisible sans défilement horizontal du texte.
    for topic in TOPICS:
        manual.show_topic(topic.id)
        qapp.processEvents()
        assert manual.browser.horizontalScrollBar().maximum() == 0, topic.id


def test_help_keyboard_and_compact_selector(manual, qapp):
    manual.resize(480, 360)
    qapp.processEvents()
    index = manual.compact_topics.findData("piano")
    manual.compact_topics.activated.emit(index)
    assert manual.current_topic_id == "piano"
    manual.browser.setFocus()
    QTest.keyClick(manual, Qt.Key_F, Qt.ControlModifier)
    assert manual.search.hasFocus()
    QTest.keyClick(manual, Qt.Key_Escape)
    assert not manual.isVisible()


def test_main_window_help_entries_reuse_nonmodal_dialog(qapp, monkeypatch):
    from ui.main_window import MainWindow
    # Le menu et la barre sont construits réellement ; éviter uniquement le service IPC.
    from core.ipc.ipc_server import NovaIpcServer
    monkeypatch.setattr(NovaIpcServer, "start", lambda self: False)
    window = MainWindow()
    try:
        before = window._capture_project_snapshot()
        actions = window.findChildren(QAction)
        help_action = next(action for action in actions if action.shortcut() == QKeySequence("F1"))
        window.show()
        window.activateWindow()
        qapp.processEvents()
        QTest.keyClick(window, Qt.Key_F1)
        qapp.processEvents()
        dialog = window._help_dialog
        assert dialog.isVisible() and not dialog.isModal()
        assert dialog.current_topic_id == "welcome"
        next(action for action in actions if action.text() == "Mon premier morceau…").trigger()
        assert dialog.current_topic_id == "quickstart"
        next(action for action in actions if action.text() == "Raccourcis clavier et gestes…").trigger()
        assert dialog.current_topic_id == "shortcuts"
        window.show_about_dialog()
        assert dialog.current_topic_id == "about"
        dialog.close()
        window.btn_quick_help.click()
        assert window._help_dialog is dialog and dialog.isVisible()
        assert window._capture_project_snapshot() == before
        keys = documented_keys()
        for action in actions:
            if not action.shortcut().isEmpty():
                assert action.shortcut().toString(QKeySequence.PortableText) in keys
    finally:
        window._help_dialog.close()
        window.audio_engine.close()
        window.close()
