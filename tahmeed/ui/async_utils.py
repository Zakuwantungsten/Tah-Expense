"""Helpers for scheduling asyncio work from Qt UI code (Python 3.14-safe)."""

from __future__ import annotations

import asyncio
import sys
from contextlib import contextmanager
from typing import Any, Callable, Coroutine, Iterator, Optional

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QWidget


def in_running_task() -> bool:
    try:
        return asyncio.current_task() is not None
    except RuntimeError:
        return False


def safe_process_events() -> None:
    """``QApplication.processEvents`` only when no asyncio Task is current."""
    if in_running_task():
        return
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is not None:
        app.processEvents()


def schedule_call(fn: Callable[[], Any]) -> None:
    """Run *fn* now, or on the next Qt tick if an asyncio Task is current."""
    if in_running_task():
        QTimer.singleShot(0, fn)
    else:
        fn()


def create_task(coro: Coroutine[Any, Any, Any]) -> asyncio.Task:
    """Create a Task without eager-start (safe to call from inside another Task).

    Python 3.14 + Qt ``processEvents`` will otherwise try to enter the new task
    while the parent is still current and raise RuntimeError.
    """
    loop = asyncio.get_running_loop()
    if sys.version_info >= (3, 12):
        try:
            return loop.create_task(coro, eager_start=False)
        except TypeError:
            pass
    return loop.create_task(coro)


def schedule_coro(coro: Coroutine[Any, Any, Any]) -> None:
    """Schedule *coro* on the running asyncio loop without nesting into a task.

    Python 3.14 raises ``RuntimeError: Cannot enter into task ... while another
    task ... is being executed`` when a new Task is started (or stepped via
    ``QApplication.processEvents``) while another Task is current. Deferring to
    the next Qt tick exits the current Task before the new one runs.
    """

    def _kick() -> None:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            coro.close()
            return
        # Timer may fire while still inside another task's nested Qt loop
        # (QMessageBox / QFileDialog). Defer again instead of colliding.
        if in_running_task():
            QTimer.singleShot(50, _kick)
            return
        create_task(coro)

    if in_running_task():
        QTimer.singleShot(0, _kick)
    else:
        _kick()


def find_poll_pausable(widget: Optional[QWidget]) -> Optional[Any]:
    """Walk parents for an object with ``pause_notification_polling``."""
    w = widget
    while w is not None:
        if hasattr(w, "pause_notification_polling") and hasattr(
            w, "resume_notification_polling"
        ):
            return w
        w = w.parent() if hasattr(w, "parent") else None
    return None


# Nested Qt loops (import gate, export save dialogs, QMessageBox) must not
# resume background polls until the outermost caller finishes.
_poll_pause_depth = 0


@contextmanager
def pause_background_polls(widget: Optional[QWidget]) -> Iterator[None]:
    """Pause badge / connectivity polls during nested Qt event loops.

    Modal dialogs (QFileDialog, QMessageBox, progress) pump Qt events while an
    asyncio Task is still current. In-flight connectivity and notification
    tasks then wake via ``task_wakeup`` and Python 3.14 raises
    ``Cannot enter into task … while another task … is being executed``.

    Always pause the process-wide connectivity monitor. Also pause accountant
    notification polling when *widget* sits under AccountantDashboard.

    Re-entrant: only the outermost caller resumes polling.
    """
    global _poll_pause_depth
    dash = find_poll_pausable(widget)
    from tahmeed.services.connectivity_service import connectivity_monitor

    if _poll_pause_depth == 0:
        if dash is not None:
            dash.pause_notification_polling()
        else:
            connectivity_monitor.pause()
    _poll_pause_depth += 1
    try:
        yield
    finally:
        _poll_pause_depth = max(0, _poll_pause_depth - 1)
        if _poll_pause_depth == 0:
            if dash is not None:
                dash.resume_notification_polling()
            else:
                connectivity_monitor.resume()
