"""Force readable light contrast on system dialogs under Windows dark mode.

Fusion inherits the OS dark palette (light WindowText). QMessageBox /
QInputDialog / QProgressDialog bodies stay light in this app, so text and
buttons go white-on-white without an explicit stylesheet.

Parent widgets with their own ``setStyleSheet`` can also block the app-level
rules from reaching a child QMessageBox — use ``style_message_box`` / ``show_*``
before ``exec()`` whenever the parent may be styled.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

# Kept light to match the rest of the desktop UI (verify, master, cashier).
_DIALOG_CONTRAST_SS = """
QMessageBox, QInputDialog, QProgressDialog {
    background-color: #FFFFFF;
    color: #111827;
}
QMessageBox QLabel, QInputDialog QLabel, QProgressDialog QLabel {
    color: #111827;
    background-color: transparent;
}
QMessageBox QPushButton, QInputDialog QPushButton, QProgressDialog QPushButton {
    background-color: #FFFFFF;
    color: #111827;
    border: 1px solid #D1D5DB;
    border-radius: 5px;
    padding: 6px 16px;
    min-width: 72px;
    min-height: 28px;
    font-size: 13px;
}
QMessageBox QPushButton:hover, QInputDialog QPushButton:hover,
QProgressDialog QPushButton:hover {
    background-color: #F3F4F6;
    color: #111827;
}
QMessageBox QPushButton:default, QInputDialog QPushButton:default {
    background-color: #0077C5;
    color: #FFFFFF;
    border-color: #0077C5;
}
QInputDialog QLineEdit, QInputDialog QTextEdit, QInputDialog QPlainTextEdit,
QInputDialog QSpinBox, QInputDialog QComboBox {
    background-color: #FFFFFF;
    color: #111827;
    border: 1px solid #D1D5DB;
    border-radius: 5px;
    padding: 4px 8px;
    selection-background-color: #0077C5;
    selection-color: #FFFFFF;
}
QProgressDialog QProgressBar {
    background-color: #E5E7EB;
    border: none;
    border-radius: 4px;
    text-align: center;
    color: #111827;
    min-height: 12px;
}
QProgressDialog QProgressBar::chunk {
    background-color: #0077C5;
    border-radius: 4px;
}
"""


def apply_readable_dialog_styles(app: QApplication) -> None:
    """Append contrast rules so confirm / input / progress dialogs stay readable."""
    existing = app.styleSheet() or ""
    if "QMessageBox QLabel" in existing:
        return
    app.setStyleSheet(existing + "\n" + _DIALOG_CONTRAST_SS)


def style_message_box(box: QMessageBox) -> QMessageBox:
    """Apply contrast rules directly on *box* (beats parent stylesheet blocking)."""
    box.setStyleSheet(_DIALOG_CONTRAST_SS)
    return box


def show_message(
    parent: Optional[QWidget],
    title: str,
    text: str,
    *,
    icon: QMessageBox.Icon = QMessageBox.Information,
    buttons: QMessageBox.StandardButton = QMessageBox.Ok,
) -> int:
    """Show a contrast-safe message box and return the clicked button."""
    box = QMessageBox(icon, title, text, buttons, parent)
    style_message_box(box)
    return box.exec()


def show_warning(parent: Optional[QWidget], title: str, text: str) -> int:
    return show_message(parent, title, text, icon=QMessageBox.Warning)


def show_info(parent: Optional[QWidget], title: str, text: str) -> int:
    return show_message(parent, title, text, icon=QMessageBox.Information)


def show_critical(parent: Optional[QWidget], title: str, text: str) -> int:
    return show_message(parent, title, text, icon=QMessageBox.Critical)
