"""Live reachability probe for the desktop app (API /health/live only).

``/health/ready`` still pings Mongo for operators. The status light must not
use it: a slow import on the same process would look like a lost connection.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from PySide6.QtCore import QObject, QTimer

from tahmeed.config import APP_VERSION
from tahmeed.ui.async_utils import create_task, in_running_task


@dataclass(frozen=True)
class ConnectivityStatus:
    """Snapshot of desktop reachability to the Tahmeed API."""

    api_ok: bool
    checked_at: Optional[datetime] = None
    api_detail: str = ""

    @property
    def online(self) -> bool:
        return self.api_ok

    @property
    def degraded(self) -> bool:
        """Amber while a batch is still running and a probe only timed out."""
        return self.api_detail == "busy"

    def short_label(self) -> str:
        if self.online:
            return "Connected"
        if self.api_detail == "database_unavailable":
            return "Server unavailable"
        if self.api_detail == "busy":
            return "Working"
        return "Offline"

    def banner_message(self) -> str:
        if self.online:
            return ""
        if self.api_detail == "database_unavailable":
            return (
                "The server is up but the database is not ready. "
                "Try again in a moment."
            )
        if self.api_detail == "busy":
            return "The app is still working. Connection was not lost."
        if _loopback_api():
            if self.api_detail == "timeout":
                return "The Tahmeed API did not answer in time."
            return "The Tahmeed API is not running."
        return (
            "Cannot reach the Tahmeed API. "
            "Check your internet connection and try again."
        )

    def status_bar_text(self, *, mode: str = "") -> str:
        checked = ""
        if self.checked_at is not None:
            checked = f"     |     Checked {self.checked_at.strftime('%H:%M:%S')}"
        mode_part = f"     |     {mode}" if mode else ""
        return (
            f"{self.short_label()} · API"
            f"{checked}{mode_part}"
            f"     |     v{APP_VERSION}"
        )

    def dot_color(self) -> str:
        if self.online:
            return "#16A34A"
        if self.api_detail == "busy":
            return "#B45309"
        return "#DC2626"


# A cold TLS handshake through Cloudflare alone can take ~2s from East Africa.
_PROBE_TIMEOUT_S = 6.0
# One slow probe must not flash the red banner; it recovers on the next tick.
_FAILURES_BEFORE_OFFLINE = 2


def _loopback_api() -> bool:
    """True when the desktop is aimed at this machine, not a remote host."""
    from tahmeed.config import API_BASE_URL

    host = (API_BASE_URL or "").lower()
    return "127.0.0.1" in host or "localhost" in host or "[::1]" in host


def _status_while_busy(status: ConnectivityStatus, busy: bool) -> ConnectivityStatus:
    """A timeout during save/import/verify means the worker is busy."""
    if busy and not status.api_ok and status.api_detail == "timeout":
        return ConnectivityStatus(
            api_ok=False,
            checked_at=status.checked_at,
            api_detail="busy",
        )
    return status


async def probe_connectivity(*, timeout: float = _PROBE_TIMEOUT_S) -> ConnectivityStatus:
    """Ping API /health/live; never raises. Does not touch Mongo."""
    api_ok, api_detail = await _probe_api(timeout)
    return ConnectivityStatus(
        api_ok=api_ok,
        checked_at=datetime.now(),
        api_detail=api_detail,
    )


async def _probe_api(timeout: float) -> tuple[bool, str]:
    try:
        from tahmeed.services.api_client import ApiConnectionError, ApiError, api_client

        await asyncio.wait_for(
            api_client.request("GET", "health/live", auth=False),
            timeout=timeout,
        )
        return True, ""
    except asyncio.TimeoutError:
        return False, "timeout"
    except ApiConnectionError as exc:
        if exc.code == "timeout":
            return False, "timeout"
        return False, "connection_error"
    except ApiError as exc:
        # Desktop cannot work without the DB behind the API.
        if exc.status_code == 503 and exc.code == "database_unavailable":
            return False, "database_unavailable"
        return False, str(exc) or (exc.code or "api_error")
    except Exception as exc:
        return False, str(exc) or "api_error"


class ConnectivityMonitor(QObject):
    """Periodic connectivity probe that publishes via app_signals."""

    INTERVAL_MS = 8_000
    # start() runs while the dashboard is still building and loading data.
    FIRST_PROBE_DELAY_MS = 3_000

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setInterval(self.INTERVAL_MS)
        self._timer.timeout.connect(self._on_tick)
        self._in_flight = False
        self._task: asyncio.Task | None = None
        self._paused = False
        self._busy_depth = 0
        self._status: ConnectivityStatus | None = None
        self._started = False
        self._failures = 0

    @property
    def status(self) -> ConnectivityStatus | None:
        return self._status

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        if self._busy_depth > 0:
            self._paused = True
            return
        self._paused = False
        self._timer.start()
        QTimer.singleShot(self.FIRST_PROBE_DELAY_MS, self._on_tick)

    def stop(self) -> None:
        self._started = False
        self._failures = 0
        self._timer.stop()
        task = self._task
        if task is not None and not task.done():
            task.cancel()
        self._task = None
        self._in_flight = False

    def pause(self) -> None:
        """Stop probes for a dialog or a save/import/verify batch.

        Nested callers each pair pause with resume. Cancelling the in-flight
        probe does not publish a new status, so the light stays Connected.
        """
        self._busy_depth += 1
        self._paused = True
        self._timer.stop()
        task = self._task
        if task is not None and not task.done():
            task.cancel()
        self._task = None
        self._in_flight = False

    def resume(self) -> None:
        if self._busy_depth > 0:
            self._busy_depth -= 1
        if self._busy_depth > 0:
            return
        if not self._started:
            return
        self._paused = False
        if not self._timer.isActive():
            self._timer.start()
        self._on_tick()

    def _on_tick(self) -> None:
        if self._paused or not self._started or not self._timer.isActive():
            return
        if self._in_flight:
            return
        if in_running_task():
            return
        prev = self._task
        if prev is not None and not prev.done():
            return
        self._task = create_task(self._check_async())

    async def _check_async(self) -> None:
        if self._in_flight or self._paused or not self._started:
            return
        self._in_flight = True
        try:
            status = _status_while_busy(
                await probe_connectivity(),
                self._busy_depth > 0,
            )
            if self._paused and status.api_ok:
                return
            if status.api_ok:
                self._failures = 0
            else:
                self._failures += 1
                if self._failures < _FAILURES_BEFORE_OFFLINE:
                    return
            prev = self._status
            self._status = status
            if (
                prev is None
                or prev.api_ok != status.api_ok
                or prev.online != status.online
                or prev.api_detail != status.api_detail
            ):
                from tahmeed.signals import app_signals

                app_signals.connectivity_changed.emit(status)
            else:
                # Still refresh "Checked HH:MM:SS" on the status bar.
                from tahmeed.signals import app_signals

                app_signals.connectivity_changed.emit(status)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        finally:
            self._in_flight = False


# Process-wide monitor (started after login, stopped on logout/exit).
connectivity_monitor = ConnectivityMonitor()
