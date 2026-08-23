"""Split Export QToolButton — Filtered on click, All in dropdown menu."""

from __future__ import annotations

import qtawesome as qta
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QToolButton

_WHITE = "#FFFFFF"
_BG = "#F4F6F8"
_BORDER = "#E5E7EB"
_T1 = "#111827"
_T2 = "#6B7280"


def export_menu_btn_stylesheet(*, height: int = 32) -> str:
    """Styles for MenuButtonPopup on Windows light mode (menu-button subcontrol)."""
    return (
        f"QToolButton {{"
        f" background-color: {_WHITE}; color: {_T1};"
        f" border: 1px solid {_BORDER}; border-radius: 5px;"
        f" font-size: 12px; font-family: 'Segoe UI'; padding: 0 8px 0 12px;"
        f" min-height: {height}px; max-height: {height}px;"
        f"}}"
        f"QToolButton:hover {{ background-color: {_BG}; color: {_T1}; }}"
        f"QToolButton:pressed {{ background-color: #EEF2F6; color: {_T1}; }}"
        f"QToolButton:open {{ background-color: {_BG}; color: {_T1}; }}"
        f"QToolButton::menu-button {{"
        f" background-color: {_WHITE}; color: {_T1};"
        f" border: none; border-left: 1px solid {_BORDER};"
        f" border-top-right-radius: 5px; border-bottom-right-radius: 5px;"
        f" width: 22px; padding: 0; margin: 0;"
        f"}}"
        f"QToolButton::menu-button:hover {{ background-color: {_BG}; color: {_T1}; }}"
        f"QToolButton::menu-button:pressed {{ background-color: #EEF2F6; color: {_T1}; }}"
        f"QToolButton::menu-arrow {{"
        f" image: none;"
        f" border-left: 4px solid transparent;"
        f" border-right: 4px solid transparent;"
        f" border-top: 5px solid {_T2};"
        f" width: 0; height: 0; margin-right: 6px;"
        f"}}"
    )


def export_dropdown_menu_stylesheet() -> str:
    return (
        f"QMenu {{ background-color: {_WHITE}; color: {_T1};"
        f" border: 1px solid {_BORDER}; padding: 4px 0; }}"
        f"QMenu::item {{ padding: 6px 24px 6px 16px; color: {_T1}; }}"
        f"QMenu::item:selected {{ background-color: {_BG}; color: {_T1}; }}"
    )


def make_export_menu_btn(
    on_filtered,
    on_all,
    *,
    parent=None,
    height: int = 32,
    btn_tip: str = "",
    filtered_tip: str = "Export rows matching the current filters and sort order.",
    all_tip: str = "Export every record in the current scope (ignores search and date range).",
) -> QToolButton:
    btn = QToolButton(parent)
    btn.setText("  Export")
    btn.setCursor(Qt.PointingHandCursor)
    btn.setFixedHeight(height)
    btn.setAutoRaise(False)
    btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
    btn.setPopupMode(QToolButton.MenuButtonPopup)
    try:
        btn.setIcon(qta.icon("mdi.microsoft-excel", color=_T2))
        btn.setIconSize(QSize(15, 15))
    except Exception:
        pass
    btn.setStyleSheet(export_menu_btn_stylesheet(height=height))
    if btn_tip:
        btn.setToolTip(btn_tip)

    act_filtered = QAction("Export Filtered", btn)
    act_filtered.setToolTip(filtered_tip)
    act_filtered.triggered.connect(on_filtered)
    act_all = QAction("Export All", btn)
    act_all.setToolTip(all_tip)
    act_all.triggered.connect(on_all)

    menu = QMenu(btn)
    menu.setStyleSheet(export_dropdown_menu_stylesheet())
    menu.addAction(act_filtered)
    menu.addAction(act_all)
    btn.setMenu(menu)
    btn.clicked.connect(on_filtered)
    return btn
