"""Modal indeterminate progress dialog for file upload / Excel parse work."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QProgressDialog, QWidget


class UploadBusy:
    """Show a non-cancelable progress dialog while an upload is processed.

    Use as a context manager around parse / staging work so the user always
    sees that the system is busy before any preview or result appears.
    """

    def __init__(
        self,
        parent: Optional[QWidget],
        message: str = "Processing upload…",
        *,
        title: str = "Upload",
    ) -> None:
        self._dlg = QProgressDialog(message, None, 0, 0, parent)
        self._dlg.setWindowTitle(title)
        self._dlg.setWindowModality(Qt.WindowModal)
        self._dlg.setMinimumDuration(0)
        self._dlg.setCancelButton(None)
        self._dlg.setMinimumWidth(360)
        self._dlg.setAutoClose(False)
        self._dlg.setAutoReset(False)
        # Explicit light contrast — Windows dark mode otherwise paints white text.
        self._dlg.setStyleSheet(
            "QProgressDialog { background: #FFFFFF; color: #111827; }"
            "QProgressDialog QLabel { color: #111827; background: transparent; }"
            "QProgressBar {"
            "  background: #E5E7EB; border: none; border-radius: 4px;"
            "  text-align: center; color: #111827; min-height: 12px;"
            "}"
            "QProgressBar::chunk { background: #0077C5; border-radius: 4px; }"
        )

    def __enter__(self) -> "UploadBusy":
        self._dlg.show()
        QApplication.processEvents()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def update(self, message: str, *, value: Optional[int] = None, maximum: Optional[int] = None) -> None:
        if maximum is not None:
            self._dlg.setMaximum(max(0, maximum))
        if value is not None and self._dlg.maximum() > 0:
            self._dlg.setValue(max(0, value))
        self._dlg.setLabelText(message)
        QApplication.processEvents()

    def close(self) -> None:
        self._dlg.reset()
        self._dlg.hide()
        self._dlg.close()
