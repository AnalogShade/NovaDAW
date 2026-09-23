"""
ui/glow_effects.py - Utilitaires pour les auras lumineuses (Glow Effects)
et les retours visuels haute intensité pour les boutons de NovaDAW.
"""
from typing import Optional
from PySide6.QtWidgets import QPushButton, QGraphicsDropShadowEffect
from PySide6.QtGui import QColor


def apply_glow_effect(
    button: QPushButton,
    color_hex: str = "#ff2a4b",
    blur_radius: int = 16,
    alpha: int = 230
):
    """
    Applique une aura lumineuse néon à 360 degrés autour d'un bouton.
    Utilise QGraphicsDropShadowEffect sans décalage (offset 0,0) avec un flou prononcé.
    """
    glow = QGraphicsDropShadowEffect(button)
    qcolor = QColor(color_hex)
    qcolor.setAlpha(alpha)
    glow.setColor(qcolor)
    glow.setBlurRadius(blur_radius)
    glow.setOffset(0, 0)
    button.setGraphicsEffect(glow)


def remove_glow_effect(button: QPushButton):
    """Retire l'effet d'aura lumineuse."""
    button.setGraphicsEffect(None)


def set_button_glow(
    button: QPushButton,
    is_active: bool,
    color_hex: str,
    blur_radius: int = 16,
    alpha: int = 230
):
    """
    Active ou désactive l'effet de lueur / aura autour du bouton selon son état.
    """
    if is_active:
        apply_glow_effect(button, color_hex=color_hex, blur_radius=blur_radius, alpha=alpha)
    else:
        remove_glow_effect(button)
