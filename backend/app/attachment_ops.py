"""Server-side attachment storage helpers (Phase 5)."""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from typing import Any

from bson import ObjectId

from .security import utcnow

_ALLOWED_EXT = {
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".bmp",
    ".tif",
    ".tiff",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".csv",
    ".txt",
}

# Soft cap to avoid unbounded multipart uploads filling the API host disk.
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024


def attachments_root(base: Path) -> Path:
    return Path(base)


def staging_root(base: Path) -> Path:
    return attachments_root(base) / ".staging"


def tx_dir(base: Path, tx_id: ObjectId | str) -> Path:
    return attachments_root(base) / str(tx_id)


def safe_name(name: str) -> str:
    keep = "".join(c if c.isalnum() or c in "._- " else "_" for c in name.strip())
    keep = keep.strip(" ._") or "file"
    return keep[:120]


def allowed_extension(filename: str) -> bool:
    ext = Path(filename).suffix.lower()
    return not ext or ext in _ALLOWED_EXT


def find_attachment_meta(doc: dict[str, Any] | None, attachment_id: str) -> dict | None:
    if not doc:
        return None
    for item in doc.get("attachments") or []:
        if item.get("id") == attachment_id:
            return item
    return None


def resolve_attachment_path(
    base: Path, tx_id: ObjectId | str, meta: dict[str, Any]
) -> Path | None:
    stored = meta.get("stored_name") or ""
    if not stored:
        return None
    path = tx_dir(base, tx_id) / stored
    # Reject path traversal via stored_name.
    try:
        path.resolve().relative_to(tx_dir(base, tx_id).resolve())
    except ValueError:
        return None
    return path if path.is_file() else None


def purge_stale_staging(base: Path, *, max_age_hours: float = 24.0) -> int:
    root = staging_root(base)
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


def _count_stale_staging(base: Path, *, max_age_hours: float = 24.0) -> int:
    root = staging_root(base)
    if not root.is_dir():
        return 0
    cutoff = time.time() - max(0.0, max_age_hours) * 3600.0
    return sum(
        1
        for path in root.iterdir()
        if path.is_file() and path.stat().st_mtime < cutoff
    )


async def cleanup_attachment_orphans(
    db: Any,
    base: Path,
    *,
    max_age_hours: float = 24.0,
    dry_run: bool = False,
) -> dict[str, int]:
    """Reconcile disk attachments with Mongo metadata; purge stale staging."""
    if dry_run:
        staging_removed = _count_stale_staging(base, max_age_hours=max_age_hours)
    else:
        staging_removed = purge_stale_staging(base, max_age_hours=max_age_hours)

    orphan_files = 0
    empty_dirs = 0
    root = attachments_root(base)
    if not root.is_dir():
        return {
            "staging_removed": staging_removed,
            "orphan_files_removed": 0,
            "empty_dirs_removed": 0,
        }

    for folder in list(root.iterdir()):
        if not folder.is_dir() or folder.name.startswith("."):
            continue
        try:
            tx_oid = ObjectId(folder.name)
        except Exception:
            continue
        doc = await db.transactions.find_one({"_id": tx_oid}, {"attachments": 1})
        known = {
            str(a.get("stored_name") or "")
            for a in (doc or {}).get("attachments") or []
            if a.get("stored_name")
        }
        for path in list(folder.iterdir()):
            if not path.is_file():
                continue
            if path.name in known:
                continue
            orphan_files += 1
            if not dry_run:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
        if not dry_run:
            try:
                if not any(folder.iterdir()):
                    folder.rmdir()
                    empty_dirs += 1
            except OSError:
                pass
        elif not known and not any(p.is_file() for p in folder.iterdir()):
            empty_dirs += 1

    return {
        "staging_removed": staging_removed,
        "orphan_files_removed": orphan_files,
        "empty_dirs_removed": empty_dirs,
    }


async def add_attachment_bytes(
    db: Any,
    base: Path,
    tx_id: ObjectId,
    *,
    filename: str,
    content: bytes,
    actor_id: ObjectId | None = None,
) -> dict[str, Any]:
    """Two-phase attach: stage bytes → Mongo $push → finalize with os.replace."""
    original_name = Path(filename).name or "file"
    if not allowed_extension(original_name):
        raise ValueError(
            f"Unsupported file type '{Path(original_name).suffix.lower()}'. "
            "Allowed: PDF, images, Office docs, CSV, TXT."
        )
    if len(content) > MAX_ATTACHMENT_BYTES:
        raise ValueError(
            f"File exceeds maximum size of {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB."
        )

    att_id = uuid.uuid4().hex
    stored_name = f"{att_id}_{safe_name(original_name)}"
    stage_dir = staging_root(base)
    stage_dir.mkdir(parents=True, exist_ok=True)
    staging = stage_dir / f"{tx_id}_{att_id}.part"
    dest_dir = tx_dir(base, tx_id)
    dest = dest_dir / stored_name

    try:
        staging.write_bytes(content)
    except OSError:
        try:
            staging.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    meta: dict[str, Any] = {
        "id": att_id,
        "filename": original_name,
        "stored_name": stored_name,
        "size": staging.stat().st_size,
        "uploaded_at": utcnow(),
    }
    if actor_id is not None:
        meta["uploaded_by"] = actor_id

    try:
        result = await db.transactions.update_one(
            {"_id": tx_id},
            {"$push": {"attachments": meta}},
        )
        if result.matched_count == 0:
            raise LookupError("Transaction not found — attachment not saved.")
        dest_dir.mkdir(parents=True, exist_ok=True)
        os.replace(staging, dest)
    except Exception:
        try:
            staging.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            await db.transactions.update_one(
                {"_id": tx_id},
                {"$pull": {"attachments": {"id": att_id}}},
            )
        except Exception:
            pass
        raise

    return meta


async def remove_attachment(
    db: Any,
    base: Path,
    tx_id: ObjectId,
    attachment_id: str,
) -> dict[str, Any] | None:
    """Pull metadata and delete the on-disk file. Returns removed meta or None."""
    doc = await db.transactions.find_one({"_id": tx_id}, {"attachments": 1})
    if not doc:
        return None
    match = find_attachment_meta(doc, attachment_id)
    if not match:
        return None

    stored = match.get("stored_name") or ""
    path = tx_dir(base, tx_id) / stored if stored else None
    staging = None
    if path is not None and path.is_file():
        # Guard against stored_name escaping the tx folder.
        try:
            path.resolve().relative_to(tx_dir(base, tx_id).resolve())
        except ValueError:
            path = None
        if path is not None:
            stage_dir = staging_root(base)
            stage_dir.mkdir(parents=True, exist_ok=True)
            staging = stage_dir / f"{tx_id}_{attachment_id}.deleted"
            try:
                os.replace(path, staging)
            except OSError:
                staging = None

    await db.transactions.update_one(
        {"_id": tx_id},
        {"$pull": {"attachments": {"id": attachment_id}}},
    )

    if staging is not None:
        try:
            staging.unlink(missing_ok=True)
        except OSError:
            pass
    elif path is not None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    return match
