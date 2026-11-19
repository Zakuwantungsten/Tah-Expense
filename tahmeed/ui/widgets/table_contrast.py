"""Force readable light-UI table text under Windows dark-mode palettes."""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QTableWidget, QTableWidgetItem, QWidget

_FG = QColor("#111827")
_BG = QColor("#FFFFFF")
_ALT = QColor("#F9FAFB")
_SEL = QColor("#DBEAFE")
_HEADER_BG = "#F3F4F6"
_HEADER_FG = "#374151"
_GRID = "#E5E7EB"

_TABLE_SS = (
    f"QTableWidget{{background:{_BG.name()};alternate-background-color:{_ALT.name()};"
    f"color:{_FG.name()};gridline-color:{_GRID};border:1px solid {_GRID};"
    f"selection-background-color:{_SEL.name()};selection-color:{_FG.name()};}}"
    f"QTableWidget::item{{color:{_FG.name()};}}"
    f"QTableWidget::item:selected{{background:{_SEL.name()};color:{_FG.name()};}}"
    f"QHeaderView::section{{background:{_HEADER_BG};color:{_HEADER_FG};"
    f"border:none;border-bottom:1px solid {_GRID};padding:6px;}}"
)


def apply_light_table_contrast(table: QTableWidget) -> None:
    """Stylesheet + palette so item text stays dark on a light row background."""
    existing = table.styleSheet().strip()
    if existing and "QTableWidget::item" in existing:
        # Keep richer local styles (e.g. backups) but still fix the palette.
        pass
    else:
        table.setStyleSheet(_TABLE_SS if not existing else f"{existing}\n{_TABLE_SS}")
    pal = table.palette()
    pal.setColor(QPalette.ColorRole.Base, _BG)
    pal.setColor(QPalette.ColorRole.AlternateBase, _ALT)
    pal.setColor(QPalette.ColorRole.Text, _FG)
    pal.setColor(QPalette.ColorRole.Button, _BG)
    pal.setColor(QPalette.ColorRole.ButtonText, _FG)
    pal.setColor(QPalette.ColorRole.Highlight, _SEL)
    pal.setColor(QPalette.ColorRole.HighlightedText, _FG)
    table.setPalette(pal)


def apply_light_tables_under(root: QWidget) -> None:
    for table in root.findChildren(QTableWidget):
        apply_light_table_contrast(table)


def light_table_item(text: str = "") -> QTableWidgetItem:
    item = QTableWidgetItem("" if text is None else str(text))
    item.setForeground(_FG)
    return item
