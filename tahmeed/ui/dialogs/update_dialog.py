import threading

from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from tahmeed.config import APP_VERSION
from tahmeed.services.update_service import (
    DownloadCancelled,
    UpdateInfo,
    download_update,
    recover_ready_update,
    set_install_on_exit,
)

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


class _DownloadWorker(QObject):
    progress = Signal(int, int)
    completed = Signal(str)
    failed = Signal(str)

    def __init__(self, info: UpdateInfo, cancelled: threading.Event):
        super().__init__()
        self._info = info
        self._cancelled = cancelled

    @Slot()
    def run(self) -> None:
        try:
            path = download_update(
                self._info,
                progress=lambda current, total: self.progress.emit(current, total),
                cancel=self._cancelled,
            )
            self.completed.emit(str(path))
        except DownloadCancelled:
            self.failed.emit("")
        except Exception as exc:
            self.failed.emit(str(exc))


class UpdateDialog(QDialog):
    restart_requested = Signal(str)

    def __init__(self, info: UpdateInfo, parent=None):
        super().__init__(parent)
        self._info = info
        self._thread: QThread | None = None
        self._worker: _DownloadWorker | None = None
        self._cancelled = threading.Event()
        self._ready_path = recover_ready_update()
        self.setWindowTitle("Update Available")
        self.setMinimumWidth(420)
        self.setModal(info.required)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        title = QLabel("A new version of Tahmeed Expense is available.")
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

        self.hint = QLabel(
            "The signed installer will download in the background and be verified "
            "before it can run."
        )
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color: #666; font-size: 12px;")
        layout.addWidget(self.hint)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.hide()
        layout.addWidget(self.progress)

        buttons = QHBoxLayout()
        buttons.addStretch()

        self.later_btn = QPushButton("Later")
        self.later_btn.clicked.connect(self.reject)
        self.later_btn.setEnabled(not self._info.required)
        buttons.addWidget(self.later_btn)

        self.cancel_btn = QPushButton("Cancel Download")
        self.cancel_btn.clicked.connect(self._cancel_download)
        self.cancel_btn.hide()
        buttons.addWidget(self.cancel_btn)

        self.install_exit_btn = QPushButton("Install on Exit")
        self.install_exit_btn.clicked.connect(self._install_on_exit)
        self.install_exit_btn.hide()
        buttons.addWidget(self.install_exit_btn)

        self.action_btn = QPushButton("Download Update")
        self.action_btn.setStyleSheet(_BTN_STYLE)
        self.action_btn.clicked.connect(self._action)
        buttons.addWidget(self.action_btn)

        layout.addLayout(buttons)
        if self._ready_path and self._ready_path.name == self._info.artifact.name:
            self._show_ready(str(self._ready_path))

    def _action(self) -> None:
        if self._ready_path:
            self.restart_requested.emit(str(self._ready_path))
            return
        self._start_download()

    def _start_download(self) -> None:
        if self._thread is not None:
            return
        self._cancelled.clear()
        self.progress.setValue(0)
        self.progress.show()
        self.action_btn.setEnabled(False)
        self.later_btn.setEnabled(False)
        self.cancel_btn.show()
        self.hint.setText("Downloading signed installer…")
        self._thread = QThread(self)
        self._worker = _DownloadWorker(self._info, self._cancelled)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.completed.connect(self._show_ready)
        self._worker.failed.connect(self._download_failed)
        self._worker.completed.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._thread_finished)
        self._thread.start()

    @Slot(int, int)
    def _on_progress(self, received: int, total: int) -> None:
        self.progress.setValue(int(received * 100 / total))
        self.progress.setFormat(
            f"{received / 1024 / 1024:.1f} / {total / 1024 / 1024:.1f} MiB"
        )

    @Slot(str)
    def _show_ready(self, path: str) -> None:
        from pathlib import Path

        self._ready_path = Path(path)
        self.progress.setValue(100)
        self.progress.setFormat("Verified and ready")
        self.progress.show()
        self.cancel_btn.hide()
        self.install_exit_btn.show()
        self.action_btn.setText("Restart and Update")
        self.action_btn.setEnabled(True)
        self.later_btn.setEnabled(not self._info.required)
        self.hint.setText(
            "The installer passed its signed size and SHA-256 checks. Save any "
            "work before restarting."
        )

    @Slot(str)
    def _download_failed(self, message: str) -> None:
        self.progress.hide()
        self.cancel_btn.hide()
        self.action_btn.setEnabled(True)
        self.later_btn.setEnabled(not self._info.required)
        self.hint.setText("The update was not staged. You can try again.")
        if message:
            QMessageBox.warning(self, "Update Failed", message)

    @Slot()
    def _thread_finished(self) -> None:
        self._worker = None
        self._thread = None

    def _cancel_download(self) -> None:
        self._cancelled.set()
        self.cancel_btn.setEnabled(False)
        self.hint.setText("Cancelling download…")

    def _install_on_exit(self) -> None:
        set_install_on_exit(True)
        self.hint.setText("The verified update will install when you exit the app.")
        self.install_exit_btn.setEnabled(False)
        self.install_exit_btn.setText("Will Install on Exit")
        # Dismiss like VS Code / Cursor — the staged installer runs on quit.
        if not self._info.required:
            self.accept()

    def reject(self) -> None:
        if self._thread is not None:
            self._cancel_download()
            return
        if self._info.required:
            self.hint.setText(
                "This version is no longer supported. Download and install the "
                "verified update to continue."
            )
            return
        super().reject()
