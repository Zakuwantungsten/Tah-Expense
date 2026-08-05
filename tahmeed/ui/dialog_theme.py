"""Force readable light contrast on system dialogs under Windows dark mode.

Fusion inherits the OS dark palette (light WindowText). QMessageBox /
QInputDialog / QProgressDialog bodies stay light in this app, so text and
buttons go white-on-white without an explicit stylesheet.
"""

from __future__ import annotations

from PySide6.QtWidgets import QApplication

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
