"""Second copy of attachment bytes in the backup bucket.

The file on ``ATTACHMENTS_DIRECTORY`` is the primary copy. Upload runs only
after that file has been renamed into place. A failed upload leaves ``s3_key``
empty so a later retry can try again, and it never deletes the local file.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from bson import ObjectId

from .attachment_ops import resolve_attachment_path
from .cli.backup import s3_client, sha256
from .config import Settings
from .receipt_storage import resolve_attachments_directory

logger = logging.getLogger("tahmeed.receipt_backup")

# Each scheduler pass holds the backup lock. Cap the pass so a receipt backlog
# cannot block mongodump. ``tahmeed-backup retry`` drains without this cap.
_SCHEDULER_BATCH = 40

_PENDING_S3 = {
    "attachments": {
        "$elemMatch": {
            "$or": [
                {"s3_key": {"$exists": False}},
                {"s3_key": None},
                {"s3_key": ""},
            ]
        }
    }
}


def receipt_object_key(settings: Settings, file_id: str) -> str:
    prefix = settings.receipt_s3_prefix.strip("/")
    return f"{prefix}/{file_id}" if prefix else file_id


def _needs_backup(meta: dict[str, Any]) -> bool:
    key = meta.get("s3_key")
    return not (isinstance(key, str) and key.strip())


async def upload_receipt_file(
    settings: Settings,
    *,
    path: Path,
    file_id: str,
    checksum: str,
    client: Any | None = None,
) -> str:
    """Upload one file and verify size plus SHA-256 metadata. Returns the key."""
    if not settings.backup_s3_bucket:
        return ""
    key = receipt_object_key(settings, file_id)
    s3 = client or s3_client(settings)
    size = path.stat().st_size
    await asyncio.to_thread(
        s3.upload_file,
        str(path),
        settings.backup_s3_bucket,
        key,
        ExtraArgs={
            "ContentType": "application/octet-stream",
            "Metadata": {"sha256": checksum},
        },
    )
    head = await asyncio.to_thread(
        s3.head_object,
        Bucket=settings.backup_s3_bucket,
        Key=key,
    )
    metadata = head.get("Metadata") or {}
    if head.get("ContentLength") != size or metadata.get("sha256") != checksum:
        raise RuntimeError("S3 receipt verification failed: size or SHA-256 metadata differs")
    return key


async def _remember_backup(
    db: Any,
    tx_id: ObjectId,
    attachment_id: str,
    *,
    s3_key: str,
    checksum: str,
) -> bool:
    result = await db.transactions.update_one(
        {"_id": tx_id, "attachments.id": attachment_id},
        {"$set": {"attachments.$.s3_key": s3_key, "attachments.$.sha256": checksum}},
    )
    return getattr(result, "matched_count", 0) == 1


async def _upload_one(
    settings: Settings,
    db: Any,
    tx_id: ObjectId,
    base: Path,
    meta: dict[str, Any],
    *,
    client: Any | None = None,
) -> str:
    """Upload one attachment. Returns the object key, or "" when nothing was recorded."""
    if not settings.backup_s3_bucket:
        return ""
    attachment_id = str(meta.get("id") or "")
    if not attachment_id:
        return ""
    path = resolve_attachment_path(base, tx_id, meta)
    if path is None:
        return ""
    checksum = await asyncio.to_thread(sha256, path)
    recorded = str(meta.get("sha256") or "")
    if recorded and recorded != checksum:
        logger.warning(
            "receipt sha256 on disk differs from metadata for %s; using the file",
            attachment_id,
        )
    try:
        key = await upload_receipt_file(
            settings,
            path=path,
            file_id=attachment_id,
            checksum=checksum,
            client=client,
        )
    except Exception:
        logger.warning("receipt backup failed for %s", attachment_id, exc_info=True)
        return ""
    if not key:
        return ""
    try:
        saved = await _remember_backup(
            db, tx_id, attachment_id, s3_key=key, checksum=checksum
        )
    except Exception:
        logger.warning(
            "receipt backup uploaded but s3_key was not saved for %s",
            attachment_id,
            exc_info=True,
        )
        return ""
    if not saved:
        logger.warning(
            "receipt backup uploaded but attachment %s was no longer on the row",
            attachment_id,
        )
        return ""
    meta["sha256"] = checksum
    meta["s3_key"] = key
    return key


async def mirror_attachment(
    settings: Settings,
    db: Any,
    tx_id: ObjectId,
    base: Path,
    meta: dict[str, Any],
    *,
    client: Any | None = None,
) -> str:
    """Upload a file that was just renamed into place. Never raises."""
    try:
        return await _upload_one(settings, db, tx_id, base, meta, client=client)
    except Exception:
        logger.warning(
            "receipt backup failed for %s",
            meta.get("id"),
            exc_info=True,
        )
        return ""


async def retry_pending_receipt_backups(
    settings: Settings,
    db: Any,
    *,
    client: Any | None = None,
    limit: int | None = _SCHEDULER_BATCH,
) -> int:
    """Upload attachments that still have no verified ``s3_key``.

    ``limit`` bounds one pass. ``None`` drains the backlog. Missing local files
    are skipped. The local file is left in place when the upload fails.
    """
    if not settings.backup_s3_bucket:
        return 0
    base = await resolve_attachments_directory(settings, db)
    uploaded = 0
    considered = 0
    cursor = db.transactions.find(_PENDING_S3, {"attachments": 1})
    async for doc in cursor:
        tx_id = doc.get("_id")
        if not isinstance(tx_id, ObjectId):
            continue
        for meta in doc.get("attachments") or []:
            if not isinstance(meta, dict) or not _needs_backup(meta):
                continue
            # A missing file is not a failed upload. Do not let it consume the
            # batch or the pass never reaches a file that is still on disk.
            if resolve_attachment_path(base, tx_id, meta) is None:
                continue
            if limit is not None and considered >= limit:
                logger.info(
                    "receipt backup retry stopped after %s file(s); more remain",
                    considered,
                )
                return uploaded
            considered += 1
            if await _upload_one(settings, db, tx_id, base, meta, client=client):
                uploaded += 1
    return uploaded
