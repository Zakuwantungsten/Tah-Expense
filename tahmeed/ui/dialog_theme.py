"""Readable system dialogs on Windows light and dark mode.

Fusion inherits the OS palette. Under dark mode, ``QMessageBox`` can end up with
a dark body but low-contrast text when a styled parent blocks app-level rules.

This module forces a consistent light dialog chrome (matching the app UI) via:

* app-level stylesheet
* per-dialog stylesheet + explicit palette on show
* patched ``QMessageBox.*`` static helpers
"""

from __future__ import annotations

from typing import Optional, Tuple, TypeVar

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QInputDialog,
    QMessageBox,
    QProgressDialog,
    QWidget,
)

_T = TypeVar("_T", bound=QWidget)

# Light dialog chrome — matches verify / master / cashier screens.
_BG = "#FFFFFF"
_FG = "#111827"
_MUTED = "#6B7280"
_BORDER = "#D1D5DB"
_HOVER = "#F3F4F6"
_PRIMARY = "#0077C5"

_DIALOG_CONTRAST_SS = f"""
QMessageBox, QInputDialog, QProgressDialog, QFileDialog {{
    background-color: {_BG};
    color: {_FG};
}}
QMessageBox QWidget, QInputDialog QWidget, QProgressDialog QWidget {{
    background-color: {_BG};
    color: {_FG};
}}
QMessageBox QLabel, QInputDialog QLabel, QProgressDialog QLabel,
QFileDialog QLabel {{
    color: {_FG};
    background-color: transparent;
}}
QMessageBox QPushButton, QInputDialog QPushButton, QProgressDialog QPushButton,
QFileDialog QPushButton {{
    background-color: {_BG};
    color: {_FG};
    border: 1px solid {_BORDER};
    border-radius: 5px;
    padding: 6px 16px;
    min-width: 72px;
    min-height: 28px;
    font-size: 13px;
}}
QMessageBox QPushButton:hover, QInputDialog QPushButton:hover,
QProgressDialog QPushButton:hover, QFileDialog QPushButton:hover {{
    background-color: {_HOVER};
    color: {_FG};
}}
QMessageBox QPushButton:default, QInputDialog QPushButton:default,
QFileDialog QPushButton:default {{
    background-color: {_PRIMARY};
    color: #FFFFFF;
    border-color: {_PRIMARY};
}}
QInputDialog QLineEdit, QInputDialog QTextEdit, QInputDialog QPlainTextEdit,
QInputDialog QSpinBox, QInputDialog QComboBox,
QFileDialog QLineEdit, QFileDialog QComboBox, QFileDialog QTreeView,
QFileDialog QListView {{
    background-color: {_BG};
    color: {_FG};
    border: 1px solid {_BORDER};
    border-radius: 5px;
    padding: 4px 8px;
    selection-background-color: {_PRIMARY};
    selection-color: #FFFFFF;
}}
QFileDialog QHeaderView::section {{
    background-color: #F1F5F9;
    color: {_MUTED};
    border: none;
    border-bottom: 1px solid {_BORDER};
    padding: 4px 8px;
}}
QProgressDialog QProgressBar {{
    background-color: #E5E7EB;
    border: none;
    border-radius: 4px;
    text-align: center;
    color: {_FG};
    min-height: 12px;
}}
QProgressDialog QProgressBar::chunk {{
    background-color: {_PRIMARY};
    border-radius: 4px;
}}
"""

_CONTRAST_DIALOG_TYPES = (
    QMessageBox,
    QInputDialog,
    QProgressDialog,
)

_hooks_installed = False


def _apply_light_dialog_palette(widget: QWidget) -> None:
    """Set an explicit light palette so OS dark mode cannot invert text/background."""
    pal = widget.palette()
    pal.setColor(QPalette.ColorRole.Window, QColor(_BG))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(_FG))
    pal.setColor(QPalette.ColorRole.Base, QColor(_BG))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(_HOVER))
    pal.setColor(QPalette.ColorRole.Text, QColor(_FG))
    pal.setColor(QPalette.ColorRole.Button, QColor(_BG))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(_FG))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(_PRIMARY))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
    widget.setPalette(pal)
    widget.setAutoFillBackground(True)
    widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)


def _style_dialog_surface(widget: _T) -> _T:
    widget.setStyleSheet(_DIALOG_CONTRAST_SS)
    _apply_light_dialog_palette(widget)
    return widget


class _DialogContrastFilter(QObject):
    """Apply contrast styling when standard dialogs are shown."""

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.Show and isinstance(watched, _CONTRAST_DIALOG_TYPES):
            _style_dialog_surface(watched)
        return False


def apply_readable_dialog_styles(app: QApplication) -> None:
    """Append contrast rules so confirm / input / progress dialogs stay readable."""
    existing = app.styleSheet() or ""
    if "QMessageBox QLabel" in existing:
        return
    app.setStyleSheet(existing + "\n" + _DIALOG_CONTRAST_SS)


def _patch_message_box_statics() -> None:
    """Ensure ``QMessageBox.information`` and friends always style the box."""

    def _information(
        parent: Optional[QWidget],
        title: str,
        text: str,
        buttons: QMessageBox.StandardButton = QMessageBox.StandardButton.Ok,
        defaultButton: QMessageBox.StandardButton = QMessageBox.StandardButton.NoButton,
    ) -> int:
        box = QMessageBox(QMessageBox.Icon.Information, title, text, buttons, parent)
        if defaultButton != QMessageBox.StandardButton.NoButton:
            box.setDefaultButton(defaultButton)
        return _style_dialog_surface(box).exec()

    def _warning(
        parent: Optional[QWidget],
        title: str,
        text: str,
        buttons: QMessageBox.StandardButton = QMessageBox.StandardButton.Ok,
        defaultButton: QMessageBox.StandardButton = QMessageBox.StandardButton.NoButton,
    ) -> int:
        box = QMessageBox(QMessageBox.Icon.Warning, title, text, buttons, parent)
        if defaultButton != QMessageBox.StandardButton.NoButton:
            box.setDefaultButton(defaultButton)
        return _style_dialog_surface(box).exec()

    def _critical(
        parent: Optional[QWidget],
        title: str,
        text: str,
        buttons: QMessageBox.StandardButton = QMessageBox.StandardButton.Ok,
        defaultButton: QMessageBox.StandardButton = QMessageBox.StandardButton.NoButton,
    ) -> int:
        box = QMessageBox(QMessageBox.Icon.Critical, title, text, buttons, parent)
        if defaultButton != QMessageBox.StandardButton.NoButton:
            box.setDefaultButton(defaultButton)
        return _style_dialog_surface(box).exec()

    def _question(
        parent: Optional[QWidget],
        title: str,
        text: str,
        buttons: QMessageBox.StandardButton = (
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ),
        defaultButton: QMessageBox.StandardButton = QMessageBox.StandardButton.NoButton,
    ) -> int:
        box = QMessageBox(QMessageBox.Icon.Question, title, text, buttons, parent)
        if defaultButton != QMessageBox.StandardButton.NoButton:
            box.setDefaultButton(defaultButton)
        return _style_dialog_surface(box).exec()

    QMessageBox.information = staticmethod(_information)  # type: ignore[method-assign]
    QMessageBox.warning = staticmethod(_warning)  # type: ignore[method-assign]
    QMessageBox.critical = staticmethod(_critical)  # type: ignore[method-assign]
    QMessageBox.question = staticmethod(_question)  # type: ignore[method-assign]


def install_dialog_theme(app: QApplication) -> None:
    """Install global hooks so every standard dialog stays readable."""
    global _hooks_installed
    if not _hooks_installed:
        _patch_message_box_statics()
        app.installEventFilter(_DialogContrastFilter(app))
        _hooks_installed = True


def style_message_box(box: QMessageBox) -> QMessageBox:
    """Apply contrast rules directly on *box* (beats parent stylesheet blocking)."""
    return _style_dialog_surface(box)


def style_input_dialog(dlg: QInputDialog) -> QInputDialog:
    """Apply contrast rules directly on an input dialog."""
    return _style_dialog_surface(dlg)


def style_progress_dialog(dlg: QProgressDialog) -> QProgressDialog:
    """Apply contrast rules directly on a progress dialog."""
    return _style_dialog_surface(dlg)


def show_message(
    parent: Optional[QWidget],
    title: str,
    text: str,
    *,
    icon: QMessageBox.Icon = QMessageBox.Icon.Information,
    buttons: QMessageBox.StandardButton = QMessageBox.StandardButton.Ok,
    default_button: Optional[QMessageBox.StandardButton] = None,
) -> int:
    """Show a contrast-safe message box and return the clicked button."""
    box = QMessageBox(icon, title, text, buttons, parent)
    if default_button is not None:
        box.setDefaultButton(default_button)
    return _style_dialog_surface(box).exec()


def show_warning(parent: Optional[QWidget], title: str, text: str) -> int:
    return show_message(parent, title, text, icon=QMessageBox.Icon.Warning)


def show_info(parent: Optional[QWidget], title: str, text: str) -> int:
    return show_message(parent, title, text, icon=QMessageBox.Icon.Information)


def show_critical(parent: Optional[QWidget], title: str, text: str) -> int:
    return show_message(parent, title, text, icon=QMessageBox.Icon.Critical)


def show_question(
    parent: Optional[QWidget],
    title: str,
    text: str,
    *,
    default_no: bool = True,
) -> int:
    """Contrast-safe Yes/No question; returns the clicked standard button."""
    return show_message(
        parent,
        title,
        text,
        icon=QMessageBox.Icon.Question,
        buttons=QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        default_button=(
            QMessageBox.StandardButton.No if default_no else QMessageBox.StandardButton.Yes
        ),
    )


def get_text(
    parent: Optional[QWidget],
    title: str,
    label: str,
    text: str = "",
) -> Tuple[str, bool]:
    """Contrast-safe single-line input; returns ``(value, ok)``."""
    dlg = QInputDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setLabelText(label)
    dlg.setTextValue(text)
    dlg.setInputMode(QInputDialog.InputMode.TextInput)
    ok = _style_dialog_surface(dlg).exec() == QDialog.DialogCode.Accepted
    return (dlg.textValue() if ok else text), ok
