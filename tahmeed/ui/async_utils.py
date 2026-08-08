"""Helpers for scheduling asyncio work from Qt UI code (Python 3.14-safe)."""

from __future__ import annotations

import asyncio
import sys
from typing import Any, Callable, Coroutine

from PySide6.QtCore import QTimer


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
