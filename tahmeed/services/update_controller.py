"""Qt coordinator for conservative, non-blocking desktop update checks."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QApplication

from tahmeed.config import UPDATE_MANIFEST_URL
from tahmeed.services.update_service import check_for_update
from tahmeed.ui.dialogs.update_dialog import UpdateDialog


class UpdateController(QObject):
    STARTUP_DELAY_MS = 12_000
    CHECK_INTERVAL_MS = 6 * 60 * 60 * 1000

    def __init__(self, request_install: Callable[[str], None], parent=None):
        super().__init__(parent)
        self._request_install = request_install
        self._checking = False
        self._dialog: UpdateDialog | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(self.CHECK_INTERVAL_MS)
        self._timer.timeout.connect(self.check_now)

    def start(self) -> None:
        if not UPDATE_MANIFEST_URL:
            return
        QTimer.singleShot(self.STARTUP_DELAY_MS, self.check_now)
        self._timer.start()

    def check_now(self) -> None:
        if self._checking or self._dialog is not None:
            return
        self._checking = True
        asyncio.ensure_future(self._check())

    async def _check(self) -> None:
        try:
            info = await check_for_update()
        except Exception:
            # Background checks are intentionally quiet. The next periodic check
            # retries; no untrusted network text is shown to the user.
            return
        finally:
            self._checking = False
        if info is None:
            return
        app = QApplication.instance()
        parent = app.activeWindow() if app is not None else None
        self._dialog = UpdateDialog(info, parent)
        self._dialog.restart_requested.connect(self._request_install)
        self._dialog.finished.connect(self._dialog_finished)
        self._dialog.show()

    def _dialog_finished(self) -> None:
        if self._dialog is not None:
            self._dialog.deleteLater()
            self._dialog = None
