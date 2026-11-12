"""Light-mode QMenu / QToolTip styles — OS/Fusion dark popups hide dark text.

Unscoped ``background:`` on parent widgets also leaks into Qt tooltips; panels
should append these rules (or call ``apply_light_menu``) so white mode stays readable.
"""

from __future__ import annotations

from PySide6.QtWidgets import QMenu

_WHITE = "#FFFFFF"
_BORDER = "#E5E7EB"
_TEXT = "#111827"
_MUTED_BORDER = "#D1D5DB"
_HOVER = "#EFF6FF"
_HOVER_FG = "#1D4ED8"

LIGHT_TOOLTIP_SS = (
    "QToolTip {"
    f" background-color: {_WHITE}; color: {_TEXT};"
    f" border: 1px solid {_MUTED_BORDER}; border-radius: 4px;"
    " padding: 6px 10px; font-size: 12px; font-family: 'Segoe UI';"
    "}"
)

LIGHT_MENU_SS = (
    f"QMenu {{ background-color: {_WHITE}; color: {_TEXT};"
    f" border: 1px solid {_MUTED_BORDER}; border-radius: 6px; padding: 4px; }}"
    f"QMenu::item {{ padding: 6px 16px; border-radius: 4px;"
    f" font-size: 12px; font-family: 'Segoe UI'; color: {_TEXT}; }}"
    f"QMenu::item:selected {{ background-color: {_HOVER}; color: {_HOVER_FG}; }}"
    f"QMenu::item:disabled {{ color: #9CA3AF; }}"
    f"QMenu::separator {{ height: 1px; background: {_BORDER}; margin: 4px 8px; }}"
)

LIGHT_POPUP_SS = f"{LIGHT_TOOLTIP_SS}\n{LIGHT_MENU_SS}"


def with_light_popups(stylesheet: str) -> str:
    """Append light tooltip/menu rules if not already present."""
    base = (stylesheet or "").rstrip()
    parts = [base] if base else []
    if "QToolTip" not in base:
        parts.append(LIGHT_TOOLTIP_SS.strip())
    if "QMenu {" not in base and "QMenu{" not in base:
        parts.append(LIGHT_MENU_SS.strip())
    return "\n".join(p for p in parts if p) + "\n"


def apply_light_menu(menu: QMenu) -> QMenu:
    """Force a readable light stylesheet on a popup menu."""
    menu.setStyleSheet(LIGHT_MENU_SS)
    return menu
