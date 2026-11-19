"""Light-mode QMenu / QToolTip styles — OS/Fusion dark popups hide dark text.

Unscoped ``background:`` on parent widgets also leaks into Qt tooltips; panels
should append these rules (or call ``apply_light_menu``) so white mode stays readable.
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QMenu

_WHITE = "#FFFFFF"
_BORDER = "#E5E7EB"
_TEXT = "#111827"
_MUTED_BORDER = "#D1D5DB"
_HOVER = "#EFF6FF"
_HOVER_FG = "#1D4ED8"

LIGHT_TOOLTIP_SS = (
    "QToolTip {"
    f" background: {_WHITE}; background-color: {_WHITE}; color: {_TEXT};"
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


# Child chrome often sets only ``background: transparent``, which replaces parent
# stylesheets and drops QToolTip rules — OS dark mode then paints black-on-black.
TRANSPARENT_LIGHT_POPUPS_SS = with_light_popups("background: transparent;")


def apply_light_tooltip_palette(app: QApplication) -> None:
    """Force readable tooltip colors under Windows / Fusion OS dark mode."""
    pal = app.palette()
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor(_WHITE))
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor(_TEXT))
    app.setPalette(pal)


# Applied directly on the floating tip widget (QTipLabel) — app/widget QToolTip
# rules alone often lose to Windows dark-mode native tip chrome.
TIP_WIDGET_SS = (
    "QWidget, QLabel {"
    f" background-color: {_WHITE}; color: {_TEXT};"
    f" border: 1px solid {_MUTED_BORDER}; border-radius: 4px;"
    " padding: 6px 10px; font-size: 12px; font-family: 'Segoe UI';"
    "}"
)


def _is_floating_tooltip(widget) -> bool:
    """True for Qt tip popups only — not ordinary ribbon/tool buttons."""
    from PySide6.QtCore import Qt

    # Internal tip label class used by QToolTip.showText / widget tips.
    if widget.__class__.__name__ == "QTipLabel":
        return True
    # WindowType.ToolTip is a composite flag; compare the masked window type.
    mask = getattr(Qt.WindowType, "WindowType_Mask", None)
    flags = widget.windowFlags()
    if mask is not None:
        return (flags & mask) == Qt.WindowType.ToolTip
    # Fallback: require ToolTip + Window together without matching random widgets.
    return (
        bool(flags & Qt.WindowType.ToolTip)
        and bool(flags & Qt.WindowType.Window)
        and bool(flags & Qt.WindowType.FramelessWindowHint)
        and widget.parent() is None
    )


def style_tooltip_widget(widget) -> None:
    """Paint a shown tip label light so dark OS tips stay readable."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QWidget

    if not isinstance(widget, QWidget) or not _is_floating_tooltip(widget):
        return

    if widget.styleSheet() != TIP_WIDGET_SS:
        widget.setStyleSheet(TIP_WIDGET_SS)
    pal = widget.palette()
    white = QColor(_WHITE)
    text = QColor(_TEXT)
    if (
        pal.color(QPalette.ColorRole.Window) != white
        or pal.color(QPalette.ColorRole.WindowText) != text
        or pal.color(QPalette.ColorRole.ToolTipBase) != white
        or pal.color(QPalette.ColorRole.ToolTipText) != text
    ):
        pal.setColor(QPalette.ColorRole.Window, white)
        pal.setColor(QPalette.ColorRole.WindowText, text)
        pal.setColor(QPalette.ColorRole.Base, white)
        pal.setColor(QPalette.ColorRole.Text, text)
        pal.setColor(QPalette.ColorRole.ToolTipBase, white)
        pal.setColor(QPalette.ColorRole.ToolTipText, text)
        widget.setPalette(pal)
    widget.setAutoFillBackground(True)
    widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)


def apply_light_menu(menu: QMenu) -> QMenu:
    """Force a readable light stylesheet on a popup menu."""
    menu.setStyleSheet(LIGHT_MENU_SS)
    return menu
