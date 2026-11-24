"""Responsive Excel exports — progress overlay, async-safe dialogs, threaded writes."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Awaitable, Callable, Optional, TypeVar

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QFileDialog, QWidget

from tahmeed.ui.dialog_theme import show_critical, show_info
from tahmeed.ui.async_utils import pause_background_polls
from tahmeed.ui.widgets.export_paging import EXPORT_PAGE_SIZE, fetch_all_pages
from tahmeed.ui.widgets.loading_overlay import LoadingOverlay

T = TypeVar("T")

FAST_STYLE_ROW_LIMIT = 200
PROGRESS_EVERY = 500

# Overall export bar: fetch then write (bar never jumps backwards).
_FETCH_SHARE = 0.55
_WRITE_SHARE = 0.45
_BAR_UNITS = 1000


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
    """Return an overlay on *widget* (reuse parent/accountant overlay when present).

    Always enables the determinate % progress bar for QuickBooks-style exports.
    """
    existing = getattr(widget, "_export_overlay", None)
    if isinstance(existing, LoadingOverlay):
        existing.enable_progress(True)
        return existing

    probe: Optional[QWidget] = widget
    while probe is not None:
        for attr in ("_loading_overlay", "_loading"):
            overlay = getattr(probe, attr, None)
            if isinstance(overlay, LoadingOverlay):
                overlay.enable_progress(True)
                widget._export_overlay = overlay  # type: ignore[attr-defined]
                return overlay
        probe = probe.parentWidget()

    overlay = LoadingOverlay(widget, "Exporting…", show_progress=True)
    overlay.enable_progress(True)
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
    overlay.enable_progress(True)
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


class ExportProgressTracker:
    """Two-phase 0–100% bar: batch fetch, then Excel write (never jumps back)."""

    def __init__(self, overlay: LoadingOverlay) -> None:
        self.overlay = overlay
        self.fetch_total = 0
        self.write_total = 0
        self._fetch_done = 0
        self._write_done = 0
        self._phase = "Exporting"

    def _overall_units(self) -> tuple[int, int]:
        fetch_frac = 0.0
        if self.fetch_total > 0:
            fetch_frac = min(1.0, self._fetch_done / self.fetch_total)
        elif self._fetch_done > 0:
            # Unknown total — ease toward end of fetch share as pages arrive.
            fetch_frac = min(0.92, 0.15 + (self._fetch_done / max(EXPORT_PAGE_SIZE, 1)) * 0.08)

        write_frac = 0.0
        if self.write_total > 0:
            write_frac = min(1.0, self._write_done / self.write_total)
        elif self._write_done > 0:
            write_frac = 0.5

        score = fetch_frac * _FETCH_SHARE + write_frac * _WRITE_SHARE
        return int(score * _BAR_UNITS), _BAR_UNITS

    def _message(self) -> str:
        pct_units, total_units = self._overall_units()
        pct = min(100, int(pct_units * 100 / total_units)) if total_units else 0
        if self._write_done or (self.write_total and self._fetch_done >= max(self.fetch_total, 1)):
            if self.write_total > 0:
                return (
                    f"{self._phase}… {self._write_done:,} / {self.write_total:,} rows"
                    f"  ·  {pct}%"
                )
            return f"{self._phase}… {self._write_done:,} rows  ·  {pct}%"
        if self.fetch_total > 0:
            return (
                f"{self._phase}… {self._fetch_done:,} / {self.fetch_total:,} loaded"
                f"  ·  {pct}%"
            )
        return f"{self._phase}… {self._fetch_done:,} loaded  ·  {pct}%"

    def _publish(self) -> None:
        done, total = self._overall_units()
        msg = self._message()
        _post_ui(lambda: self.overlay.show_loading(msg, value=done, maximum=total))

    def begin_fetch(self, total: int = 0, *, phase: str = "Loading records") -> None:
        self.fetch_total = max(0, int(total or 0))
        self._fetch_done = 0
        self._write_done = 0
        self.write_total = 0
        self._phase = phase
        self.overlay.enable_progress(True)
        self._publish()

    def on_fetch_batch(self, loaded: int) -> None:
        self._fetch_done = max(0, int(loaded))
        if self.fetch_total > 0:
            self._fetch_done = min(self._fetch_done, self.fetch_total)
        self._publish()

    def finish_fetch(self, loaded: int) -> None:
        self._fetch_done = max(0, int(loaded))
        if self.fetch_total <= 0:
            self.fetch_total = self._fetch_done
        else:
            self._fetch_done = self.fetch_total
        self._publish()

    def begin_write(self, total: int, *, phase: str = "Writing Excel") -> None:
        self.write_total = max(0, int(total or 0))
        self._write_done = 0
        if self.fetch_total > 0:
            self._fetch_done = self.fetch_total
        self._phase = phase
        self.overlay.enable_progress(True)
        self._publish()

    def on_write_progress(self, done: int, detail: str = "") -> None:
        self._write_done = max(0, int(done))
        if detail:
            self._phase = detail
        self._publish()

    def finish_write(self) -> None:
        if self.write_total > 0:
            self._write_done = self.write_total
        self._phase = "Saving file"
        self._publish()


def make_export_progress_callback(
    overlay: LoadingOverlay,
    total: int,
    phase: str = "Writing",
) -> Callable[[int, str], None]:
    """Thread-safe progress reporter for openpyxl work."""

    def _cb(done: int, detail: str = "") -> None:
        report_export_progress(overlay, done, total, detail or phase)

    return _cb


def make_tracker_write_callback(
    tracker: ExportProgressTracker,
) -> Callable[[int, str], None]:
    def _cb(done: int, detail: str = "") -> None:
        tracker.on_write_progress(done, detail)

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
    total: Optional[int] = None,
    count_fn: Optional[Callable[[], Awaitable[int]]] = None,
    tracker: Optional[ExportProgressTracker] = None,
) -> list:
    """Paginated batch fetch with a live % progress bar when total is known."""

    known = int(total) if total is not None and total > 0 else None
    if known is None and count_fn is not None:
        show_export_busy(overlay, f"{phase}… counting", maximum=0)
        try:
            known = int(await count_fn() or 0)
        except Exception:
            known = None
        if known is not None and known <= 0:
            known = None

    progress = tracker or ExportProgressTracker(overlay)
    progress.begin_fetch(known or 0, phase=phase)

    def _on_batch(count: int) -> None:
        progress.on_fetch_batch(count)

    rows = await fetch_all_pages(
        fetch, page_size=page_size, on_batch=_on_batch, total=known,
    )
    progress.finish_fetch(len(rows))
    return rows


async def run_export_write(
    overlay: LoadingOverlay,
    total: int,
    write_fn: Callable[[Callable[[int, str], None]], None],
    *,
    phase: str = "Writing",
    tracker: Optional[ExportProgressTracker] = None,
) -> None:
    """Run blocking openpyxl work off the GUI thread with determinate progress."""
    if tracker is not None:
        tracker.begin_write(total, phase=phase)
        progress = make_tracker_write_callback(tracker)

        def _worker() -> None:
            write_fn(progress)

        await asyncio.to_thread(_worker)
        tracker.finish_write()
        return

    progress = make_export_progress_callback(overlay, total, phase=phase)
    report_export_progress(overlay, 0, total, phase)

    def _worker() -> None:
        write_fn(progress)

    await asyncio.to_thread(_worker)
    report_export_progress(overlay, total, total, "Saving file")
