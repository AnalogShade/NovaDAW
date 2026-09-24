"""
main.py - Point d'entrée de NovaDAW (Digital Audio Workstation Open-Source)
"""
import sys
import os
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon
from ui.main_window import MainWindow


def load_stylesheet(app: QApplication):
    qss_path = os.path.join(os.path.dirname(__file__), "assets", "style.qss")
    if os.path.exists(qss_path):
        with open(qss_path, "r", encoding="utf-8") as f:
            app.setStyleSheet(f.read())


def main():
    # Définition de l'AppUserModelID pour que la barre des tâches Windows affiche l'icône personnalisée
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("NovaDAW.AudioWorkstation.App.1.1")
        except Exception:
            pass

    app = QApplication(sys.argv)
    app.setApplicationName("NovaDAW")
    app.setOrganizationName("NovaDAW Open Source")

    # Définition de l'icône officielle Supernova
    icon_path = os.path.join(os.path.dirname(__file__), "assets", "nova_icon.png")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    # Charger le thème sombre moderne style Cubase
    load_stylesheet(app)

    window = MainWindow()
    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
        try:
            from core.serializer import load_project
            window.audio_engine.stop()
            window.project = load_project(sys.argv[1])
            window.audio_engine.set_project(window.project)
            window.refresh_project_ui()
        except Exception as e:
            print(f"Erreur chargement projet CLI {sys.argv[1]}: {e}")
    window.showMaximized()

    sys.exit(app.exec())


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    main()
