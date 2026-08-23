"""Responsive Excel exports — progress overlay, async-safe dialogs, threaded writes."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Callable, Optional, TypeVar

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QFileDialog, QWidget

from tahmeed.ui.dialog_theme import show_critical, show_info
from tahmeed.ui.async_utils import pause_background_polls
from tahmeed.ui.widgets.export_paging import EXPORT_PAGE_SIZE, fetch_all_pages
from tahmeed.ui.widgets.loading_overlay import LoadingOverlay

T = TypeVar("T")

FAST_STYLE_ROW_LIMIT = 200
PROGRESS_EVERY = 500


def normalize_xlsx_path(path: str) -> str:
    """Return an absolute ``.xlsx`` path and ensure the parent folder exists."""
    p = Path(path).expanduser()
    if p.suffix.lower() != ".xlsx":
        p = p.with_suffix(".xlsx")
    p.parent.mkdir(parents=True, exist_ok=True)
    return str(p.resolve())


def export_file_ready(path: str) -> bool:
    p = Path(path)
    return p.is_file() and p.stat().st_size > 0


def attach_export_overlay(widget: QWidget) -> LoadingOverlay:
    """Return an overlay on *widget* (reuse parent/accountant overlay when present)."""
    existing = getattr(widget, "_export_overlay", None)
    if isinstance(existing, LoadingOverlay):
        return existing

    probe: Optional[QWidget] = widget
    while probe is not None:
        for attr in ("_loading_overlay", "_loading"):
            overlay = getattr(probe, attr, None)
            if isinstance(overlay, LoadingOverlay):
                widget._export_overlay = overlay  # type: ignore[attr-defined]
                return overlay
        probe = probe.parentWidget()

    overlay = LoadingOverlay(widget, "Exporting…")
    widget._export_overlay = overlay  # type: ignore[attr-defined]
    return overlay


def _restore_cursors() -> None:
    while QApplication.overrideCursor() is not None:
        QApplication.restoreOverrideCursor()


def hide_export_busy(widget: QWidget) -> None:
    """Hide every export overlay we may have shown and reset the wait cursor."""
    _restore_cursors()
    seen: set[int] = set()
    probe: Optional[QWidget] = widget
    while probe is not None:
        oid = id(probe)
        if oid not in seen:
            seen.add(oid)
            for attr in ("_export_overlay", "_loading_overlay", "_loading"):
                overlay = getattr(probe, attr, None)
                if isinstance(overlay, LoadingOverlay):
                    overlay.hide_loading()
        probe = probe.parentWidget()


def show_export_busy(
    overlay: LoadingOverlay,
    message: str,
    *,
    value: Optional[int] = None,
    maximum: Optional[int] = None,
) -> None:
    overlay.show_loading(message, value=value, maximum=maximum)


def _post_ui(fn: Callable[[], None]) -> None:
    QTimer.singleShot(0, fn)


def report_export_count(overlay: LoadingOverlay, message: str) -> None:
    """Update an indeterminate progress bar with a live row count."""
    _post_ui(lambda: overlay.show_loading(message, maximum=0))


def report_export_progress(
    overlay: LoadingOverlay,
    done: int,
    total: int,
    phase: str,
) -> None:
    if total <= 0:
        _post_ui(lambda: overlay.show_loading(f"{phase}… {done:,}", maximum=0))
        return
    pct = min(100, int(done * 100 / total))
    _post_ui(lambda: overlay.show_loading(
        f"{phase}… {done:,} / {total:,} ({pct}%)",
        value=done,
        maximum=total,
    ))


def make_export_progress_callback(
    overlay: LoadingOverlay,
    total: int,
    phase: str = "Writing",
) -> Callable[[int, str], None]:
    """Thread-safe progress reporter for openpyxl work."""

    def _cb(done: int, detail: str = "") -> None:
        report_export_progress(overlay, done, total, detail or phase)

    return _cb


async def await_ui(fn: Callable[[], T]) -> T:
    """Run a blocking Qt dialog while the current asyncio task is suspended."""
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[T] = loop.create_future()

    def _run() -> None:
        if fut.done():
            return
        try:
            fut.set_result(fn())
        except Exception as exc:
            fut.set_exception(exc)

    QTimer.singleShot(0, _run)
    return await fut


async def pick_export_path(
    widget: QWidget,
    title: str,
    default_name: str,
) -> str:
    """Async-safe save dialog; returns ``""`` when cancelled."""
    with pause_background_polls(widget):
        path, _ = await await_ui(lambda: QFileDialog.getSaveFileName(
            widget, title, default_name, "Excel Files (*.xlsx)",
        ))
    return path or ""


async def notify_export_info(widget: QWidget, title: str, message: str) -> None:
    with pause_background_polls(widget):
        await await_ui(lambda: show_info(widget, title, message))


async def notify_export_error(widget: QWidget, title: str, message: str) -> None:
    with pause_background_polls(widget):
        await await_ui(lambda: show_critical(widget, title, message))


async def fetch_records_with_progress(
    overlay: LoadingOverlay,
    fetch,
    *,
    phase: str = "Loading records",
    page_size: int = EXPORT_PAGE_SIZE,
) -> list:
    """Paginated fetch with a live loaded-row count on the overlay."""

    def _on_batch(count: int) -> None:
        report_export_count(overlay, f"{phase}… {count:,} loaded")

    return await fetch_all_pages(fetch, page_size=page_size, on_batch=_on_batch)


async def run_export_write(
    overlay: LoadingOverlay,
    total: int,
    write_fn: Callable[[Callable[[int, str], None]], None],
    *,
    phase: str = "Writing",
) -> None:
    """Run blocking openpyxl work off the GUI thread with determinate progress."""
    progress = make_export_progress_callback(overlay, total, phase=phase)
    report_export_progress(overlay, 0, total, phase)

    def _worker() -> None:
        write_fn(progress)

    await asyncio.to_thread(_worker)
    report_export_progress(overlay, total, total, "Saving file")
