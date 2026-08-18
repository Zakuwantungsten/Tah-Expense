"""Live reachability probes for the desktop app (API + direct MongoDB)."""

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
    """Snapshot of desktop reachability to the API and MongoDB."""

    api_ok: bool
    mongo_ok: bool
    checked_at: Optional[datetime] = None
    api_detail: str = ""
    mongo_detail: str = ""

    @property
    def online(self) -> bool:
        return self.api_ok and self.mongo_ok

    @property
    def degraded(self) -> bool:
        return (self.api_ok or self.mongo_ok) and not self.online

    def short_label(self) -> str:
        if self.online:
            return "Connected"
        if not self.api_ok and not self.mongo_ok:
            return "Offline"
        if not self.api_ok:
            return "API unreachable"
        return "Database unreachable"

    def banner_message(self) -> str:
        if self.online:
            return ""
        if not self.api_ok and not self.mongo_ok:
            return (
                "No connection — cannot reach the server or database. "
                "Connect to the internet to use the app."
            )
        if not self.api_ok:
            return (
                "Cannot reach the Tahmeed API. "
                "Check your internet connection and try again."
            )
        return (
            "Cannot reach the database. "
            "Check your internet connection and try again."
        )

    def status_bar_text(self, *, mode: str = "") -> str:
        checked = ""
        if self.checked_at is not None:
            checked = f"     |     Checked {self.checked_at.strftime('%H:%M:%S')}"
        mode_part = f"     |     {mode}" if mode else ""
        return (
            f"{self.short_label()} · MongoDB Atlas"
            f"{checked}{mode_part}"
            f"     |     v{APP_VERSION}"
        )

    def dot_color(self) -> str:
        if self.online:
            return "#16A34A"
        if self.degraded:
            return "#D97706"
        return "#DC2626"


_PROBE_TIMEOUT_S = 5.0


async def probe_connectivity(*, timeout: float = _PROBE_TIMEOUT_S) -> ConnectivityStatus:
    """Ping API /health/ready and MongoDB; never raises."""
    api_ok, api_detail = await _probe_api(timeout)
    mongo_ok, mongo_detail = await _probe_mongo(timeout)
    return ConnectivityStatus(
        api_ok=api_ok,
        mongo_ok=mongo_ok,
        checked_at=datetime.now(),
        api_detail=api_detail,
        mongo_detail=mongo_detail,
    )


async def _probe_api(timeout: float) -> tuple[bool, str]:
    try:
        from tahmeed.services.api_client import ApiConnectionError, ApiError, api_client

        await asyncio.wait_for(
            api_client.request("GET", "health/ready", auth=False),
            timeout=timeout,
        )
        return True, ""
    except asyncio.TimeoutError:
        return False, "timeout"
    except ApiConnectionError as exc:
        return False, str(exc) or "connection_error"
    except ApiError as exc:
        # 503 database_unavailable still means the API process answered.
        if exc.status_code == 503 and exc.code == "database_unavailable":
            return True, "api_up_db_not_ready"
        return False, str(exc) or (exc.code or "api_error")
    except Exception as exc:
        return False, str(exc) or "api_error"


async def _probe_mongo(timeout: float) -> tuple[bool, str]:
    try:
        from tahmeed.db.connection import get_client

        client = get_client()
        await asyncio.wait_for(client.admin.command("ping"), timeout=timeout)
        return True, ""
    except asyncio.TimeoutError:
        return False, "timeout"
    except Exception as exc:
        return False, str(exc) or "mongo_error"


class ConnectivityMonitor(QObject):
    """Periodic connectivity probe that publishes via app_signals."""

    INTERVAL_MS = 8_000

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setInterval(self.INTERVAL_MS)
        self._timer.timeout.connect(self._on_tick)
        self._in_flight = False
        self._task: asyncio.Task | None = None
        self._paused = False
        self._status: ConnectivityStatus | None = None
        self._started = False

    @property
    def status(self) -> ConnectivityStatus | None:
        return self._status

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._paused = False
        self._timer.start()
        self._on_tick()

    def stop(self) -> None:
        self._started = False
        self._timer.stop()
        task = self._task
        if task is not None and not task.done():
            task.cancel()
        self._task = None
        self._in_flight = False

    def pause(self) -> None:
        """Stop probes during nested Qt dialogs / long UI tasks (Py3.14)."""
        self._paused = True
        self._timer.stop()
        task = self._task
        if task is not None and not task.done():
            task.cancel()
        self._task = None
        self._in_flight = False

    def resume(self) -> None:
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
            status = await probe_connectivity()
            prev = self._status
            self._status = status
            if (
                prev is None
                or prev.api_ok != status.api_ok
                or prev.mongo_ok != status.mongo_ok
                or prev.online != status.online
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
