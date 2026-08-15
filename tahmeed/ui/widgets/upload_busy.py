"""Modal progress dialog for file upload / Excel parse work."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Coroutine
from typing import Any, Optional, TypeVar

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QProgressDialog, QWidget

_T = TypeVar("_T")


class UploadCancelled(Exception):
    """User cancelled an UploadBusy operation."""


class UploadBusy:
    """Show a progress dialog while an upload is processed.

    Use as a context manager around parse / staging work so the user always
    sees that the system is busy before any preview or result appears.

    Pass ``cancellable=True`` for a Cancel button. ``should_cancel`` is safe
    to call from a worker thread; ``raise_if_cancelled`` / ``await_or_cancel``
    belong on the UI / asyncio thread.
    """

    def __init__(
        self,
        parent: Optional[QWidget],
        message: str = "Processing upload…",
        *,
        title: str = "Upload",
        cancellable: bool = False,
    ) -> None:
        self._cancellable = cancellable
        self._cancel_event = threading.Event()
        self._closing = False
        cancel_text = "Cancel" if cancellable else None
        self._dlg = QProgressDialog(message, cancel_text, 0, 0, parent)
        self._dlg.setWindowTitle(title)
        self._dlg.setWindowModality(Qt.WindowModal)
        self._dlg.setMinimumDuration(0)
        if not cancellable:
            self._dlg.setCancelButton(None)
        else:
            self._dlg.setCancelButtonText("Cancel")
            self._dlg.canceled.connect(self._on_canceled)
        self._dlg.setMinimumWidth(360)
        self._dlg.setAutoClose(False)
        self._dlg.setAutoReset(False)
        # Explicit light contrast — Windows dark mode otherwise paints white text.
        self._dlg.setStyleSheet(
            "QProgressDialog { background: #FFFFFF; color: #111827; }"
            "QProgressDialog QLabel { color: #111827; background: transparent; }"
            "QProgressDialog QPushButton {"
            "  background: #FFFFFF; color: #111827; border: 1px solid #D1D5DB;"
            "  border-radius: 5px; padding: 6px 16px; min-width: 72px;"
            "  min-height: 28px; font-size: 13px;"
            "}"
            "QProgressDialog QPushButton:hover { background: #F3F4F6; }"
            "QProgressBar {"
            "  background: #E5E7EB; border: none; border-radius: 4px;"
            "  text-align: center; color: #111827; min-height: 12px;"
            "}"
            "QProgressBar::chunk { background: #0077C5; border-radius: 4px; }"
        )

    def _on_canceled(self) -> None:
        if self._closing:
            return
        self._cancel_event.set()
        self._dlg.setLabelText("Cancelling…")
        self._dlg.setCancelButton(None)

    def should_cancel(self) -> bool:
        """Thread-safe: True after the user clicks Cancel."""
        return self._cancel_event.is_set()

    def raise_if_cancelled(self) -> None:
        if self.should_cancel() or (
            self._cancellable and not self._closing and self._dlg.wasCanceled()
        ):
            self._cancel_event.set()
            raise UploadCancelled()

    async def tick(self) -> None:
        """Yield to Qt so the overlay can paint; raise if cancelled."""
        self.raise_if_cancelled()
        await asyncio.sleep(0)
        self.raise_if_cancelled()

    async def await_or_cancel(self, coro: Coroutine[Any, Any, _T]) -> _T:
        """Await *coro*, aborting soon after Cancel is clicked."""
        from tahmeed.ui.async_utils import create_task

        try:
            self.raise_if_cancelled()
        except UploadCancelled:
            coro.close()
            raise
        task = create_task(coro)
        try:
            while True:
                if self.should_cancel():
                    task.cancel()
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):
                        pass
                    raise UploadCancelled()
                done, _ = await asyncio.wait({task}, timeout=0.12)
                if task in done:
                    return task.result()
        except UploadCancelled:
            raise
        except asyncio.CancelledError:
            if not task.done():
                task.cancel()
            raise

    def __enter__(self) -> "UploadBusy":
        self._dlg.show()
        from tahmeed.ui.async_utils import safe_process_events
        safe_process_events()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def update(self, message: str, *, value: Optional[int] = None, maximum: Optional[int] = None) -> None:
        if self.should_cancel():
            return
        if maximum is not None:
            self._dlg.setMaximum(max(0, maximum))
        if value is not None and self._dlg.maximum() > 0:
            self._dlg.setValue(max(0, value))
        self._dlg.setLabelText(message)
        from tahmeed.ui.async_utils import safe_process_events
        safe_process_events()

    def close(self) -> None:
        self._closing = True
        if self._cancellable:
            try:
                self._dlg.canceled.disconnect(self._on_canceled)
            except (RuntimeError, TypeError):
                pass
        self._dlg.reset()
        self._dlg.hide()
        self._dlg.close()
