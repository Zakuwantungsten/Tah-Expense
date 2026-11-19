"""Receipt-disk health: detect unmounted / missing storage and alert clients.

The API may still see a writable empty directory at the configured mount path
when the real disk is absent. We combine a write probe, a mount-hint check for
``/mnt`` / ``/media`` paths, and a volume marker file that only exists on the
real volume.
"""

from __future__ import annotations

import logging
import platform
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .receipt_storage import (
    probe_directory,
    resolve_attachments_directory,
)
from .config import Settings

logger = logging.getLogger(__name__)

UTC = timezone.utc
HEALTH_ID = "receipt_storage_health"
VOLUME_TOKEN_KEY = "receipt_storage_volume_token"
VOLUME_MARKER = ".tahmeed-volume"
_CACHE_SECONDS = 15.0
_cache_mono: float = 0.0
_cache_payload: dict[str, Any] | None = None

_REASON_MESSAGES = {
    "path_missing": "Receipt disk path is missing or not a folder.",
    "not_writable": "Receipt disk is not writable.",
    "mount_missing": "Receipt disk mount looks unmounted or detached.",
    "volume_marker_missing": (
        "Receipt disk volume marker is missing — the drive may be unmounted."
    ),
    "probe_error": "Receipt disk probe failed.",
}


class StorageUnavailableError(RuntimeError):
    """Raised when receipt storage must not accept new writes."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


def _utcnow() -> datetime:
    return datetime.now(UTC)


def public_health_message(reason: str | None) -> str:
    if not reason:
        return "Receipt disk is available."
    return _REASON_MESSAGES.get(reason, "Receipt disk is unavailable.")


def mobile_fallback_hint() -> str:
    return (
        "We are experiencing receipt storage issues. "
        "Please send receipts the usual way through WhatsApp until this is fixed."
    )


def _mount_looks_detached(path: Path, mount_hint: str | None) -> bool:
    """True when a /mnt or /media path appears to sit on the root filesystem."""
    if platform.system() == "Windows":
        drive = path.drive
        if not drive:
            return False
        try:
            return not Path(f"{drive}\\").exists()
        except OSError:
            return True

    posix = path.as_posix()
    if not (posix.startswith("/mnt/") or posix.startswith("/media/")):
        return False
    hint = (mount_hint or "").rstrip("/") or "/"
    # Dedicated mount should report itself (or a parent under /mnt|/media), not "/".
    if hint == "/":
        return True
    if posix.startswith("/mnt/") and not hint.startswith("/mnt"):
        return True
    if posix.startswith("/media/") and not hint.startswith("/media"):
        return True
    return False


def _read_marker(path: Path) -> str | None:
    marker = path / VOLUME_MARKER
    try:
        if not marker.is_file():
            return None
        text = marker.read_text(encoding="utf-8", errors="replace").strip()
        return text or None
    except OSError:
        return None


def _write_marker(path: Path, token: str) -> None:
    marker = path / VOLUME_MARKER
    marker.write_text(token, encoding="utf-8")
    try:
        marker.chmod(0o644)
    except OSError:
        pass


async def _load_volume_token(db: Any) -> str | None:
    settings = getattr(db, "system_settings", None)
    if settings is None or not hasattr(settings, "find_one"):
        return None
    try:
        doc = await settings.find_one({"key": VOLUME_TOKEN_KEY})
    except Exception:
        return None
    if not doc:
        return None
    text = str(doc.get("value") or "").strip()
    return text or None


async def _save_volume_token(db: Any, token: str) -> None:
    settings = getattr(db, "system_settings", None)
    if settings is None or not hasattr(settings, "update_one"):
        return
    await settings.update_one(
        {"key": VOLUME_TOKEN_KEY},
        {"$set": {"key": VOLUME_TOKEN_KEY, "value": token}},
        upsert=True,
    )


async def _clear_volume_token(db: Any) -> None:
    settings = getattr(db, "system_settings", None)
    if settings is None or not hasattr(settings, "delete_one"):
        return
    try:
        await settings.delete_one({"key": VOLUME_TOKEN_KEY})
    except Exception:
        pass


async def bootstrap_volume_marker(db: Any, path: Path) -> str:
    """Create / refresh the on-disk marker after a path is confirmed writable."""
    token = uuid.uuid4().hex
    _write_marker(path, token)
    await _save_volume_token(db, token)
    return token


def evaluate_probe_health(
    path: Path,
    probe: dict[str, Any],
    *,
    expected_token: str | None,
) -> tuple[bool, str | None]:
    """Return ``(healthy, reason)`` from a probe + optional volume token."""
    if probe.get("error") and not probe.get("exists"):
        return False, "path_missing"
    if not probe.get("exists") or not probe.get("is_dir"):
        return False, "path_missing"
    if not probe.get("write_ok"):
        return False, "not_writable"
    if _mount_looks_detached(path, probe.get("mount_hint")):
        return False, "mount_missing"
    if expected_token:
        actual = _read_marker(path)
        if actual != expected_token:
            return False, "volume_marker_missing"
    return True, None


def _invalidate_cache() -> None:
    global _cache_mono, _cache_payload
    _cache_mono = 0.0
    _cache_payload = None


async def read_stored_health(db: Any) -> dict[str, Any]:
    counters = getattr(db, "system_counters", None)
    if counters is None or not hasattr(counters, "find_one"):
        return {"healthy": True, "degraded": False, "reason": None}
    doc = await counters.find_one({"_id": HEALTH_ID})
    if not doc:
        return {"healthy": True, "degraded": False, "reason": None}
    healthy = bool(doc.get("healthy", True))
    reason = doc.get("reason")
    return {
        "healthy": healthy,
        "degraded": not healthy,
        "reason": reason,
        "message": public_health_message(reason if not healthy else None),
        "path": doc.get("path"),
        "checked_at": doc.get("checked_at"),
        "host": doc.get("host"),
        "whatsapp_fallback": not healthy,
        "whatsapp_hint": mobile_fallback_hint() if not healthy else None,
    }


async def _persist_health(db: Any, payload: dict[str, Any], *, previous_healthy: bool | None) -> None:
    counters = getattr(db, "system_counters", None)
    if counters is None or not hasattr(counters, "update_one"):
        return
    await counters.update_one(
        {"_id": HEALTH_ID},
        {
            "$set": {
                "healthy": payload["healthy"],
                "reason": payload.get("reason"),
                "path": payload.get("path"),
                "checked_at": payload.get("checked_at"),
                "host": payload.get("host"),
                "updated_at": _utcnow(),
            }
        },
        upsert=True,
    )
    flipped = previous_healthy is not None and previous_healthy != payload["healthy"]
    if flipped or previous_healthy is None:
        # Bump badge stamp so accountant long-poll wakes and re-merges health.
        try:
            from .badge_counters import BADGE_ID, _wake_badge_waiters

            await counters.update_one(
                {"_id": BADGE_ID},
                {"$set": {"updated_at": _utcnow()}},
                upsert=True,
            )
            _wake_badge_waiters()
        except Exception:
            logger.exception("failed to wake badge waiters after storage health change")


async def refresh_storage_health(
    settings: Settings,
    db: Any,
    *,
    force: bool = False,
    bootstrap_marker: bool = True,
) -> dict[str, Any]:
    """Probe the effective receipt disk and persist the health document."""
    global _cache_mono, _cache_payload

    now_mono = time.monotonic()
    if (
        not force
        and _cache_payload is not None
        and (now_mono - _cache_mono) < _CACHE_SECONDS
    ):
        return dict(_cache_payload)

    previous = await read_stored_health(db)
    previous_healthy = previous.get("healthy")
    if "checked_at" not in previous or previous.get("path") is None:
        # No prior row — treat as unknown so first persist still wakes clients.
        previous_healthy = None

    try:
        effective = await resolve_attachments_directory(settings, db)
    except Exception as exc:
        payload = {
            "healthy": False,
            "degraded": True,
            "reason": "probe_error",
            "message": public_health_message("probe_error"),
            "path": None,
            "checked_at": _utcnow(),
            "host": platform.node(),
            "whatsapp_fallback": True,
            "whatsapp_hint": mobile_fallback_hint(),
            "error": str(exc),
        }
        await _persist_health(db, payload, previous_healthy=previous_healthy)
        _cache_mono = now_mono
        _cache_payload = payload
        return dict(payload)

    probe = probe_directory(effective, create=False)
    token = await _load_volume_token(db)
    healthy, reason = evaluate_probe_health(effective, probe, expected_token=token)

    if healthy and bootstrap_marker and not token and probe.get("write_ok"):
        try:
            await bootstrap_volume_marker(db, effective)
        except OSError as exc:
            healthy = False
            reason = "not_writable"
            probe = {**probe, "write_ok": False, "error": str(exc)}

    payload = {
        "healthy": healthy,
        "degraded": not healthy,
        "reason": reason,
        "message": public_health_message(reason if not healthy else None),
        "path": str(effective),
        "checked_at": _utcnow(),
        "host": platform.node(),
        "whatsapp_fallback": not healthy,
        "whatsapp_hint": mobile_fallback_hint() if not healthy else None,
        "probe": {
            "exists": probe.get("exists"),
            "is_dir": probe.get("is_dir"),
            "writable": probe.get("writable"),
            "write_ok": probe.get("write_ok"),
            "mount_hint": probe.get("mount_hint"),
            "drive": probe.get("drive"),
            "free_human": probe.get("free_human"),
            "error": probe.get("error"),
        },
    }
    await _persist_health(db, payload, previous_healthy=previous_healthy)
    _cache_mono = now_mono
    _cache_payload = payload
    if not healthy:
        logger.warning(
            "receipt storage unhealthy reason=%s path=%s",
            reason,
            effective,
        )
    return dict(payload)


async def get_storage_health(
    settings: Settings,
    db: Any,
    *,
    force: bool = False,
) -> dict[str, Any]:
    return await refresh_storage_health(settings, db, force=force)


async def assert_receipt_storage_ready(settings: Settings, db: Any) -> None:
    """Raise :class:`StorageUnavailableError` when new receipt writes must stop."""
    health = await get_storage_health(settings, db, force=False)
    if health.get("healthy"):
        return
    raise StorageUnavailableError(
        str(health.get("reason") or "probe_error"),
        str(health.get("message") or public_health_message("probe_error")),
    )


def storage_unavailable_error_payload(exc: StorageUnavailableError) -> dict[str, Any]:
    return {
        "reason": exc.reason,
        "whatsapp_fallback": True,
        "whatsapp_hint": mobile_fallback_hint(),
    }


async def on_storage_path_changed(db: Any, path: Path) -> None:
    """Reset volume identity after an admin/accountant applies a new path."""
    _invalidate_cache()
    await _clear_volume_token(db)
    try:
        await bootstrap_volume_marker(db, path)
    except OSError:
        logger.exception("could not write volume marker at %s", path)
