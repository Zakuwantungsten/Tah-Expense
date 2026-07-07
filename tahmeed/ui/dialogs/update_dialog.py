from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from tahmeed.config import APP_VERSION
from tahmeed.services.update_service import UpdateInfo

_BTN_STYLE = """
    QPushButton {
        background-color: #E85D04;
        color: white;
        border: none;
        border-radius: 6px;
        font-size: 13px;
        font-weight: bold;
        padding: 8px 16px;
    }
    QPushButton:hover { background-color: #F48C06; }
    QPushButton:pressed { background-color: #DC2F02; }
"""


class UpdateDialog(QDialog):
    def __init__(self, info: UpdateInfo, parent=None):
        super().__init__(parent)
        self._info = info
        self.setWindowTitle("Update Available")
        self.setMinimumWidth(420)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        title = QLabel(f"A new version of Tahmeed Expense is available.")
        title.setWordWrap(True)
        title.setStyleSheet("font-size: 15px; font-weight: 600;")
        layout.addWidget(title)

        versions = QLabel(
            f"You have <b>{APP_VERSION}</b> — latest is <b>{self._info.version}</b>."
        )
        versions.setWordWrap(True)
        layout.addWidget(versions)

        if self._info.release_notes:
            notes = QTextEdit()
            notes.setReadOnly(True)
            notes.setPlainText(self._info.release_notes)
            notes.setFixedHeight(120)
            layout.addWidget(notes)

        hint = QLabel(
            "Download the update, close this app, replace the old folder with the "
            "new one, then open Tahmeed Expense again."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #666; font-size: 12px;")
        layout.addWidget(hint)

        buttons = QHBoxLayout()
        buttons.addStretch()

        later_btn = QPushButton("Later")
        later_btn.clicked.connect(self.reject)
        buttons.addWidget(later_btn)

        download_btn = QPushButton("Download Update")
        download_btn.setStyleSheet(_BTN_STYLE)
        download_btn.clicked.connect(self._download)
        download_btn.setEnabled(bool(self._info.download_url))
        buttons.addWidget(download_btn)

        layout.addLayout(buttons)

    def _download(self) -> None:
        if self._info.download_url:
            QDesktopServices.openUrl(QUrl(self._info.download_url))
        self.accept()
