"""Per-transaction file attachments (receipts, scans) — two-phase disk + Mongo."""

from __future__ import annotations

import logging
import os
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from bson import ObjectId

from tahmeed.config import APP_NAME
from tahmeed.db.connection import get_db

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
    db = get_db()
    doc = await db.transactions.find_one({"_id": tx_id}, {"attachments": 1})
    if not doc:
        return []
    return list(doc.get("attachments") or [])


async def add_attachment(
    tx_id: ObjectId,
    source_path: str | Path,
    *,
    actor_id: ObjectId | None = None,
) -> dict:
    """Two-phase attach: stage file → Mongo $push → finalize with os.replace."""
    src = Path(source_path)
    if not src.is_file():
        raise FileNotFoundError(f"File not found: {src}")

    ext = src.suffix.lower()
    if ext and ext not in _ALLOWED_EXT:
        raise ValueError(
            f"Unsupported file type '{ext}'. "
            "Allowed: PDF, images, Office docs, CSV, TXT."
        )

    att_id = uuid.uuid4().hex
    stored_name = f"{att_id}_{_safe_name(src.name)}"
    stage_dir = staging_root()
    stage_dir.mkdir(parents=True, exist_ok=True)
    staging = stage_dir / f"{tx_id}_{att_id}.part"
    dest_dir = tx_dir(tx_id)
    dest = dest_dir / stored_name

    try:
        shutil.copy2(src, staging)
    except OSError:
        try:
            staging.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    meta = {
        "id": att_id,
        "filename": src.name,
        "stored_name": stored_name,
        "size": staging.stat().st_size,
        "uploaded_at": datetime.utcnow(),
    }
    if actor_id is not None:
        meta["uploaded_by"] = actor_id

    db = get_db()
    try:
        result = await db.transactions.update_one(
            {"_id": tx_id},
            {"$push": {"attachments": meta}},
        )
        if result.matched_count == 0:
            raise ValueError("Transaction not found — attachment not saved.")
        dest_dir.mkdir(parents=True, exist_ok=True)
        os.replace(staging, dest)
    except Exception:
        try:
            staging.unlink(missing_ok=True)
        except OSError:
            pass
        # If Mongo wrote meta but finalize failed, pull meta so disk/meta stay aligned.
        try:
            await db.transactions.update_one(
                {"_id": tx_id},
                {"$pull": {"attachments": {"id": att_id}}},
            )
        except Exception:
            pass
        raise

    try:
        from tahmeed.services.audit_service import record_event

        await record_event(
            "attachment.add",
            actor_id=actor_id,
            entity_type="attachment",
            entity_ids=[tx_id],
            details={"attachment_id": att_id, "filename": src.name},
        )
    except Exception:
        pass
    return meta


async def remove_attachment(
    tx_id: ObjectId,
    attachment_id: str,
    *,
    actor_id: ObjectId | None = None,
) -> bool:
    db = get_db()
    doc = await db.transactions.find_one({"_id": tx_id}, {"attachments": 1})
    if not doc:
        return False
    attachments = list(doc.get("attachments") or [])
    match = next((a for a in attachments if a.get("id") == attachment_id), None)
    if not match:
        return False

    stored = match.get("stored_name") or ""
    path = tx_dir(tx_id) / stored if stored else None
    staging = None
    if path is not None and path.is_file():
        stage_dir = staging_root()
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

    try:
        from tahmeed.services.audit_service import record_event

        await record_event(
            "attachment.remove",
            actor_id=actor_id,
            entity_type="attachment",
            entity_ids=[tx_id],
            details={
                "attachment_id": attachment_id,
                "filename": match.get("filename"),
            },
        )
    except Exception:
        pass
    return True


def resolve_attachment_path(tx_id: ObjectId | str, meta: dict) -> Optional[Path]:
    stored = meta.get("stored_name") or ""
    if not stored:
        return None
    path = tx_dir(tx_id) / stored
    return path if path.is_file() else None


async def cleanup_attachment_orphans(
    *,
    max_age_hours: float = 24.0,
    dry_run: bool = False,
) -> dict:
    """Reconcile disk attachments with Mongo metadata; purge stale staging."""
    staging_removed = purge_stale_staging(max_age_hours=max_age_hours) if not dry_run else 0
    if dry_run:
        staging_removed = 0
        root = staging_root()
        if root.is_dir():
            cutoff = time.time() - max(0.0, max_age_hours) * 3600.0
            staging_removed = sum(
                1
                for p in root.iterdir()
                if p.is_file() and p.stat().st_mtime < cutoff
            )

    orphan_files = 0
    empty_dirs = 0
    root = attachments_root()
    if not root.is_dir():
        return {
            "staging_removed": staging_removed,
            "orphan_files_removed": 0,
            "empty_dirs_removed": 0,
        }

    db = get_db()
    for folder in list(root.iterdir()):
        if not folder.is_dir() or folder.name.startswith("."):
            continue
        tx_key = folder.name
        try:
            tx_oid = ObjectId(tx_key)
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
        elif not known and not any(folder.iterdir()):
            empty_dirs += 1

    logger.info(
        "attachment orphan cleanup: staging=%s orphans=%s empty_dirs=%s dry_run=%s",
        staging_removed,
        orphan_files,
        empty_dirs,
        dry_run,
    )
    return {
        "staging_removed": staging_removed,
        "orphan_files_removed": orphan_files,
        "empty_dirs_removed": empty_dirs,
    }
