"""Per-transaction file attachments (receipts, scans) — API upload/download + local cache."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import List, Optional

from bson import ObjectId

from tahmeed.config import APP_NAME
from tahmeed.services.api_client import ApiError, api_client

logger = logging.getLogger("tahmeed.attachments")

_ALLOWED_EXT = {
    ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp",
    ".tif", ".tiff", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".txt",
}


def attachments_root() -> Path:
    base = os.getenv("LOCALAPPDATA")
    if not base:
        base = str(Path.home() / "AppData" / "Local")
    return Path(base) / APP_NAME / "attachments"


def staging_root() -> Path:
    return attachments_root() / ".staging"


def tx_dir(tx_id: ObjectId | str) -> Path:
    return attachments_root() / str(tx_id)


def _safe_name(name: str) -> str:
    keep = "".join(c if c.isalnum() or c in "._- " else "_" for c in name.strip())
    keep = keep.strip(" ._") or "file"
    return keep[:120]


def purge_stale_staging(*, max_age_hours: float = 24.0) -> int:
    """Remove abandoned staging files (sync; safe before Mongo is ready)."""
    root = staging_root()
    if not root.is_dir():
        return 0
    cutoff = time.time() - max(0.0, max_age_hours) * 3600.0
    removed = 0
    for path in root.iterdir():
        if not path.is_file():
            continue
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
                removed += 1
        except OSError:
            continue
    return removed


async def get_attachments(tx_id: ObjectId) -> List[dict]:
    payload = await api_client.request(
        "GET", f"v1/transactions/{tx_id}/attachments"
    )
    return list((payload or {}).get("items") or [])


def _seed_cache(tx_id: ObjectId | str, meta: dict, content: bytes) -> None:
    """Write uploaded bytes into the local open-cache so open works without re-download."""
    dest = _cache_path(tx_id, meta)
    if dest is None:
        return
    staging: Path | None = None
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        staging = dest.parent / f".{dest.name}.part"
        staging.write_bytes(content)
        os.replace(staging, dest)
        staging = None
    except OSError:
        if staging is not None:
            try:
                staging.unlink(missing_ok=True)
            except OSError:
                pass


def _clear_cache(tx_id: ObjectId | str, attachment_id: str, meta: dict | None = None) -> None:
    paths: list[Path] = []
    if meta:
        cached = _cache_path(tx_id, meta)
        if cached is not None:
            paths.append(cached)
    folder = tx_dir(tx_id)
    if folder.is_dir():
        for path in folder.iterdir():
            if path.is_file() and (
                path.name.startswith(f"{attachment_id}_")
                or path.name.startswith(f".{attachment_id}_")
            ):
                paths.append(path)
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


async def add_attachment(
    tx_id: ObjectId,
    source_path: str | Path,
    *,
    actor_id: ObjectId | None = None,
) -> dict:
    """Upload a file to the API and seed the local open-cache.

    ``actor_id`` is kept for call-site compatibility; the API records the
    authenticated user as uploader.
    """
    import httpx

    _ = actor_id  # server derives actor from the bearer token
    src = Path(source_path)
    if not src.is_file():
        raise FileNotFoundError(f"File not found: {src}")

    ext = src.suffix.lower()
    if ext and ext not in _ALLOWED_EXT:
        raise ValueError(
            f"Unsupported file type '{ext}'. "
            "Allowed: PDF, images, Office docs, CSV, TXT."
        )

    content = src.read_bytes()
    try:
        meta = await api_client.request(
            "POST",
            f"v1/transactions/{tx_id}/attachments",
            files={"file": (src.name, content, "application/octet-stream")},
            timeout=httpx.Timeout(120.0, connect=5.0),
        )
    except ApiError as exc:
        if exc.status_code == 404:
            raise ValueError("Transaction not found — attachment not saved.") from exc
        if exc.status_code == 422:
            raise ValueError(str(exc)) from exc
        raise

    if not isinstance(meta, dict):
        raise ValueError("Invalid attachment response from API.")
    _seed_cache(tx_id, meta, content)
    return meta


async def remove_attachment(
    tx_id: ObjectId,
    attachment_id: str,
    *,
    actor_id: ObjectId | None = None,
) -> bool:
    """Delete an attachment via the API and clear any local cache copy."""
    _ = actor_id
    attachment_id = str(attachment_id or "").strip()
    if not attachment_id:
        return False

    try:
        await api_client.request(
            "DELETE",
            f"v1/transactions/{tx_id}/attachments/{attachment_id}",
        )
    except ApiError as exc:
        if exc.status_code == 404:
            _clear_cache(tx_id, attachment_id)
            return False
        raise

    _clear_cache(tx_id, attachment_id)
    return True


def _cache_path(tx_id: ObjectId | str, meta: dict) -> Optional[Path]:
    att_id = str(meta.get("id") or "").strip()
    stored = str(meta.get("stored_name") or "").strip()
    filename = str(meta.get("filename") or "file")
    name = Path(stored).name if stored else None
    if not name:
        if not att_id:
            return None
        name = f"{att_id}_{_safe_name(filename)}"
    return tx_dir(tx_id) / name


def resolve_attachment_path(tx_id: ObjectId | str, meta: dict) -> Optional[Path]:
    """Return a locally cached path if present (no network). Prefer ``ensure_attachment_local``."""
    path = _cache_path(tx_id, meta)
    if path is None:
        return None
    expected = meta.get("size")
    if path.is_file() and (
        expected is None or int(expected) == path.stat().st_size
    ):
        return path
    return None


async def ensure_attachment_local(
    tx_id: ObjectId | str, meta: dict
) -> Optional[Path]:
    """Download attachment to a local cache (if needed) and return the path for open."""
    cached = resolve_attachment_path(tx_id, meta)
    if cached is not None:
        return cached

    att_id = str(meta.get("id") or "").strip()
    dest = _cache_path(tx_id, meta)
    if not att_id or dest is None:
        return None

    content = await api_client.request_bytes(
        "GET", f"v1/transactions/{tx_id}/attachments/{att_id}/file"
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    staging = dest.parent / f".{dest.name}.part"
    try:
        staging.write_bytes(content)
        os.replace(staging, dest)
    except OSError:
        try:
            staging.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return dest if dest.is_file() else None


def _count_stale_staging(*, max_age_hours: float = 24.0) -> int:
    root = staging_root()
    if not root.is_dir():
        return 0
    cutoff = time.time() - max(0.0, max_age_hours) * 3600.0
    return sum(
        1
        for path in root.iterdir()
        if path.is_file() and path.stat().st_mtime < cutoff
    )


async def cleanup_attachment_orphans(
    *,
    max_age_hours: float = 24.0,
    dry_run: bool = False,
) -> dict:
    """Purge local staging, then ask the API to reconcile server attachment storage.

    Server cleanup is manager-only; cashiers quietly skip that part after a 403.
    """
    local_staging = (
        _count_stale_staging(max_age_hours=max_age_hours)
        if dry_run
        else purge_stale_staging(max_age_hours=max_age_hours)
    )
    result = {
        "staging_removed": local_staging,
        "orphan_files_removed": 0,
        "empty_dirs_removed": 0,
        "server_skipped": False,
    }
    try:
        server = await api_client.request(
            "POST",
            "v1/transactions/attachments/cleanup-orphans",
            params={
                "max_age_hours": max_age_hours,
                "dry_run": dry_run,
            },
        )
        if isinstance(server, dict):
            result["staging_removed"] = int(server.get("staging_removed") or 0) + local_staging
            result["orphan_files_removed"] = int(server.get("orphan_files_removed") or 0)
            result["empty_dirs_removed"] = int(server.get("empty_dirs_removed") or 0)
    except ApiError as exc:
        if exc.status_code in (401, 403):
            result["server_skipped"] = True
            logger.info(
                "server attachment cleanup skipped (status=%s)",
                exc.status_code,
            )
        else:
            logger.warning("server attachment cleanup failed: %s", exc)
            raise

    logger.info(
        "attachment orphan cleanup: staging=%s orphans=%s empty_dirs=%s "
        "dry_run=%s server_skipped=%s",
        result["staging_removed"],
        result["orphan_files_removed"],
        result["empty_dirs_removed"],
        dry_run,
        result["server_skipped"],
    )
    return result
