"""Paperwork → Records: company paper folders + upload (no expense row required).

Files stay in the shared ``receipt_files`` ledger with a ``record`` block and a
browse copy under ``records/<kind>/<slug>/YYYY/MM/``. Custom folders live in
``receipt_record_folders`` and are reused across uploads.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import uuid
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from bson import ObjectId

from .attachment_ops import attachments_root, safe_name
from .receipt_ops import (
    INBOX_APPROVED,
    ensure_receipt_workspace,
    quarantine_rejected_upload,
    resolve_receipt_file_path,
)
from .receipt_upload_security import UploadSecurityError, validate_receipt_upload
from .security import utcnow

_MAX_PACK_FILES = 500

UTC = timezone.utc

RECORD_KINDS = frozenset({"item", "supplier", "separate", "custom"})
KIND_DISK = {
    "item": "items",
    "supplier": "suppliers",
    "separate": "separate",
    "custom": "custom",
}

# Sidebar Separate Expenses keys (accountant). Labels for destination pickers.
SEPARATE_RECORD_DESTINATIONS: tuple[tuple[str, str], ...] = (
    ("toll_plaza", "Toll Plaza"),
    ("parking_congo", "Parking Congo"),
    ("congo_exp", "Congo Expenses"),
    ("ahmed_kimvi", "Ahmed Kimvi (Klesa)"),
    ("zambia_parking", "Zambia Parking"),
    ("afritrack", "Afritrack"),
    ("third_party", "Third Party Covers"),
    ("comesa", "COMESA Covers"),
    ("sm_burhani", "SM Burhani"),
    ("rahntech", "RahnTech"),
)

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_HEX24 = re.compile(r"^[0-9a-f]{24}$", re.IGNORECASE)


def record_slug(value: str, *, fallback: str = "untitled") -> str:
    text = (value or "").strip().lower()
    text = _SLUG_RE.sub("-", text).strip("-")
    return (text[:80] or fallback)


def _folder_segment(value: str, *, fallback: str = "unclassified") -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", (value or "").strip())
    text = text.strip(" .") or fallback
    return text[:80]


def records_root(base: Path) -> Path:
    return attachments_root(base) / "records"


def ensure_records_workspace(base: Path) -> dict[str, Path]:
    """Ensure receipt workspace plus ``records/`` kind roots."""
    workspace = ensure_receipt_workspace(base)
    root = records_root(base)
    paths = {
        **workspace,
        "records": root,
        "records_items": root / "items",
        "records_suppliers": root / "suppliers",
        "records_separate": root / "separate",
        "records_custom": root / "custom",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return paths


def _as_day_start(value: date | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    return datetime(value.year, value.month, value.day, tzinfo=UTC)


def _as_day_end(value: date | datetime | None) -> datetime | None:
    start = _as_day_start(value)
    if start is None:
        return None
    return start.replace(hour=23, minute=59, second=59, microsecond=999999)


def _serialize_folder(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(doc.get("_id")),
        "kind": doc.get("kind") or "custom",
        "label": doc.get("label") or "",
        "slug": doc.get("slug") or "",
        "ref_key": doc.get("ref_key") or "",
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
        "created_by": str(doc["created_by"])
        if doc.get("created_by") is not None
        else None,
    }


async def list_custom_folders(db: Any, *, include_deleted: bool = False) -> list[dict[str, Any]]:
    query: dict[str, Any] = {"kind": "custom"}
    if not include_deleted:
        query["deleted_at"] = None
    cursor = db.receipt_record_folders.find(query).sort([("label", 1)])
    docs = await cursor.to_list(length=500)
    return [_serialize_folder(doc) for doc in docs]


async def create_custom_folder(
    db: Any,
    *,
    label: str,
    actor_id: ObjectId | None = None,
) -> dict[str, Any]:
    clean = " ".join((label or "").split())
    if not clean:
        raise ValueError("Folder label is required")
    if len(clean) > 120:
        raise ValueError("Folder label is too long")
    slug = record_slug(clean)
    existing = await db.receipt_record_folders.find_one(
        {"kind": "custom", "slug": slug, "deleted_at": None}
    )
    if existing:
        raise ValueError(f"A custom folder named “{existing.get('label') or clean}” already exists")
    now = utcnow()
    doc = {
        "kind": "custom",
        "label": clean,
        "slug": slug,
        "ref_key": slug,
        "created_at": now,
        "updated_at": now,
        "created_by": actor_id,
        "deleted_at": None,
    }
    result = await db.receipt_record_folders.insert_one(doc)
    doc["_id"] = result.inserted_id
    return _serialize_folder(doc)


async def rename_custom_folder(
    db: Any,
    folder_id: ObjectId,
    *,
    label: str,
) -> dict[str, Any]:
    clean = " ".join((label or "").split())
    if not clean:
        raise ValueError("Folder label is required")
    if len(clean) > 120:
        raise ValueError("Folder label is too long")
    doc = await db.receipt_record_folders.find_one(
        {"_id": folder_id, "kind": "custom", "deleted_at": None}
    )
    if not doc:
        raise ValueError("Custom folder not found")
    new_slug = record_slug(clean)
    clash = await db.receipt_record_folders.find_one(
        {
            "kind": "custom",
            "slug": new_slug,
            "deleted_at": None,
            "_id": {"$ne": folder_id},
        }
    )
    if clash:
        raise ValueError(f"A custom folder named “{clash.get('label') or clean}” already exists")
    await db.receipt_record_folders.update_one(
        {"_id": folder_id},
        {
            "$set": {
                "label": clean,
                "slug": new_slug,
                "ref_key": new_slug,
                "updated_at": utcnow(),
            }
        },
    )
    # Keep file metadata labels in sync; disk slug path stays as stored at upload
    # (browse copies are not relocated on rename in v1).
    await db.receipt_files.update_many(
        {"record.folder_id": str(folder_id)},
        {"$set": {"record.label": clean, "record.slug": new_slug, "record.ref_key": new_slug}},
    )
    updated = await db.receipt_record_folders.find_one({"_id": folder_id})
    return _serialize_folder(updated or doc)


async def delete_custom_folder(db: Any, folder_id: ObjectId) -> dict[str, Any]:
    doc = await db.receipt_record_folders.find_one(
        {"_id": folder_id, "kind": "custom", "deleted_at": None}
    )
    if not doc:
        raise ValueError("Custom folder not found")
    in_use = await db.receipt_files.count_documents(
        {"record.folder_id": str(folder_id), "security_rejected": {"$ne": True}}
    )
    if in_use:
        raise ValueError(
            f"Cannot delete “{doc.get('label')}” — {in_use} file(s) are filed there. "
            "Move or keep the folder."
        )
    now = utcnow()
    await db.receipt_record_folders.update_one(
        {"_id": folder_id},
        {"$set": {"deleted_at": now, "updated_at": now}},
    )
    return {"id": str(folder_id), "deleted": True}


async def list_record_destinations(db: Any) -> dict[str, Any]:
    """Picker payload: items, suppliers, separate feeds, custom folders."""
    cats = await db.categories.find(
        {},
        {"name": 1, "sidebar_name": 1, "is_supplier": 1},
    ).sort([("name", 1)]).to_list(length=2000)

    items: list[dict[str, str]] = []
    suppliers: list[dict[str, str]] = []
    for cat in cats:
        name = str(cat.get("name") or "").strip()
        if not name:
            continue
        label = str(cat.get("sidebar_name") or name).strip() or name
        row = {"ref_key": name, "label": label, "slug": record_slug(name)}
        if cat.get("is_supplier") is True:
            suppliers.append(row)
        else:
            items.append(row)

    separate = [
        {"ref_key": key, "label": label, "slug": record_slug(key)}
        for key, label in SEPARATE_RECORD_DESTINATIONS
    ]
    customs = await list_custom_folders(db)
    return {
        "items": items,
        "suppliers": suppliers,
        "separate": separate,
        "custom": customs,
    }


async def resolve_record_destination(
    db: Any,
    *,
    kind: str,
    ref_key: str = "",
    folder_id: str = "",
) -> dict[str, Any]:
    """Normalize upload destination into kind/label/slug/folder_id/ref_key."""
    key = (kind or "").strip().lower()
    if key not in RECORD_KINDS:
        raise ValueError("kind must be item, supplier, separate, or custom")

    if key == "custom":
        fid_raw = (folder_id or "").strip()
        if not fid_raw:
            # Allow create-by-label via ref_key only when folder_id absent? No —
            # customs must be created first so reuse is explicit.
            raise ValueError("Custom destination requires folder_id")
        if not _HEX24.match(fid_raw):
            raise ValueError("Invalid custom folder id")
        try:
            oid = ObjectId(fid_raw)
        except Exception as exc:
            raise ValueError("Invalid custom folder id") from exc
        doc = await db.receipt_record_folders.find_one(
            {"_id": oid, "kind": "custom", "deleted_at": None}
        )
        if not doc:
            raise ValueError("Custom folder not found")
        return {
            "kind": "custom",
            "folder_id": str(doc["_id"]),
            "ref_key": doc.get("ref_key") or doc.get("slug") or "",
            "label": doc.get("label") or "",
            "slug": doc.get("slug") or record_slug(str(doc.get("label") or "custom")),
        }

    raw = " ".join((ref_key or "").split())
    if not raw:
        raise ValueError(f"{key} destination requires ref_key")

    if key == "separate":
        allowed = {k: lab for k, lab in SEPARATE_RECORD_DESTINATIONS}
        if raw not in allowed:
            # Accept label match too
            by_label = {lab.lower(): k for k, lab in SEPARATE_RECORD_DESTINATIONS}
            mapped = by_label.get(raw.lower())
            if not mapped:
                raise ValueError(f"Unknown separate expense key: {raw}")
            raw = mapped
        label = allowed[raw]
        return {
            "kind": "separate",
            "folder_id": None,
            "ref_key": raw,
            "label": label,
            "slug": record_slug(raw),
        }

    # item / supplier — match categories
    query: dict[str, Any] = {"name": raw}
    if key == "supplier":
        query["is_supplier"] = True
    else:
        query["is_supplier"] = {"$ne": True}
    cat = await db.categories.find_one(query)
    if not cat:
        # Case-insensitive fallback
        rx = {"$regex": f"^{re.escape(raw)}$", "$options": "i"}
        soft = {"name": rx}
        if key == "supplier":
            soft["is_supplier"] = True
        else:
            soft["is_supplier"] = {"$ne": True}
        cat = await db.categories.find_one(soft)
    if not cat:
        # Still allow filing under the typed key so accountants can file against
        # a label that is not (yet) in the catalog — Records is paper storage.
        label = raw
        name = raw
    else:
        name = str(cat.get("name") or raw).strip() or raw
        label = str(cat.get("sidebar_name") or name).strip() or name
    return {
        "kind": key,
        "folder_id": None,
        "ref_key": name,
        "label": label,
        "slug": record_slug(name),
    }


def _records_browse_rel(
    dest: dict[str, Any],
    *,
    stored_name: str,
    when: datetime | None = None,
) -> str:
    day = when or utcnow()
    if day.tzinfo is None:
        day = day.replace(tzinfo=UTC)
    else:
        day = day.astimezone(UTC)
    kind = dest["kind"]
    disk_kind = KIND_DISK[kind]
    slug = _folder_segment(dest.get("slug") or dest.get("ref_key") or "untitled")
    year, month = f"{day.year:04d}", f"{day.month:02d}"
    return f"records/{disk_kind}/{slug}/{year}/{month}/{safe_name(Path(stored_name).name)}"


async def store_record_file_bytes(
    db: Any,
    base: Path,
    *,
    filename: str,
    content: bytes,
    destination: dict[str, Any],
    actor_id: ObjectId | None = None,
    note: str = "",
    claimed_content_type: str | None = None,
) -> dict[str, Any]:
    """Validate, store under ``files/``, materialize under ``records/…``, index."""
    ensure_records_workspace(base)
    try:
        checked = validate_receipt_upload(
            filename, content, claimed_content_type=claimed_content_type
        )
    except UploadSecurityError as exc:
        await quarantine_rejected_upload(
            db,
            base,
            filename=filename,
            content=content or b"",
            reason=str(exc),
            source="records",
            actor_id=actor_id,
            code=getattr(exc, "code", "upload_rejected"),
        )
        raise ValueError(str(exc)) from exc

    original_name = checked["filename"]
    content = checked["content"]
    content_type = checked["content_type"]

    file_id = uuid.uuid4().hex
    stored_name = f"{file_id}_{safe_name(original_name)}"
    relative = f"files/{stored_name}"
    workspace = ensure_records_workspace(base)
    staging = workspace["staging"] / f"record_{file_id}.part"
    dest_file = workspace["files"] / stored_name
    checksum = hashlib.sha256(content).hexdigest()
    now = utcnow()
    browse_rel = _records_browse_rel(destination, stored_name=stored_name, when=now)
    browse_abs = attachments_root(base) / Path(*browse_rel.split("/"))

    try:
        staging.write_bytes(content)
        os.replace(staging, dest_file)
        browse_abs.parent.mkdir(parents=True, exist_ok=True)
        if browse_abs.resolve() != dest_file.resolve():
            shutil.copy2(dest_file, browse_abs)
    except OSError:
        try:
            staging.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    note_clean = " ".join((note or "").split())[:500]
    record_block = {
        "kind": destination["kind"],
        "folder_id": destination.get("folder_id"),
        "ref_key": destination.get("ref_key") or "",
        "label": destination.get("label") or "",
        "slug": destination.get("slug") or "",
        "path": browse_rel,
        "note": note_clean,
    }
    meta = {
        "id": file_id,
        "filename": original_name,
        "stored_name": relative,
        "stored_path": relative,
        "size": dest_file.stat().st_size,
        "sha256": checksum,
        "s3_key": "",
        "uploaded_at": now,
        "content_type": content_type,
        "record": record_block,
    }
    if actor_id is not None:
        meta["uploaded_by"] = actor_id

    doc = {
        "_id": file_id,
        "original_filename": original_name,
        "content_type": content_type,
        "size": meta["size"],
        "sha256": checksum,
        "stored_path": relative,
        "s3_key": "",
        "uploaded_at": now,
        "uploaded_by": actor_id,
        "source": "records",
        "inbox_status": INBOX_APPROVED,
        "request_id": None,
        "transaction_ids": [],
        "approved_at": now,
        "approved_by": actor_id,
        "rejected_at": None,
        "rejected_by": None,
        "rejection_reason": None,
        "security_rejected": False,
        "record": record_block,
        "materialized_paths": [browse_rel],
        "materialized_at": now,
    }
    await db.receipt_files.insert_one(doc)
    return meta


async def list_record_files(
    db: Any,
    *,
    kind: str = "",
    ref_key: str = "",
    folder_id: str = "",
    q: str = "",
    date_from: date | datetime | None = None,
    date_to: date | datetime | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    query: dict[str, Any] = {
        "record": {"$exists": True, "$ne": None},
        "security_rejected": {"$ne": True},
    }
    kind_key = (kind or "").strip().lower()
    if kind_key:
        if kind_key not in RECORD_KINDS:
            raise ValueError("kind must be item, supplier, separate, or custom")
        query["record.kind"] = kind_key
    fid = (folder_id or "").strip()
    if fid:
        query["record.folder_id"] = fid
    rk = " ".join((ref_key or "").split())
    if rk:
        query["record.ref_key"] = rk

    start = _as_day_start(date_from)
    end = _as_day_end(date_to)
    if start is not None or end is not None:
        span: dict[str, Any] = {}
        if start is not None:
            span["$gte"] = start
        if end is not None:
            span["$lte"] = end
        query["uploaded_at"] = span

    needle = (q or "").strip()
    if needle:
        query["original_filename"] = {"$regex": re.escape(needle), "$options": "i"}

    cap = max(1, min(int(limit), 500))
    docs = (
        await db.receipt_files.find(query)
        .sort("uploaded_at", -1)
        .limit(cap)
        .to_list(length=cap)
    )
    items: list[dict[str, Any]] = []
    for doc in docs:
        rec = doc.get("record") or {}
        items.append(
            {
                "id": str(doc.get("_id")),
                "original_filename": doc.get("original_filename") or "",
                "content_type": doc.get("content_type") or "",
                "size": int(doc.get("size") or 0),
                "sha256": doc.get("sha256") or "",
                "stored_path": doc.get("stored_path") or "",
                "uploaded_at": doc.get("uploaded_at"),
                "source": doc.get("source") or "records",
                "inbox_status": doc.get("inbox_status") or INBOX_APPROVED,
                "record": {
                    "kind": rec.get("kind") or "",
                    "folder_id": rec.get("folder_id"),
                    "ref_key": rec.get("ref_key") or "",
                    "label": rec.get("label") or "",
                    "slug": rec.get("slug") or "",
                    "path": rec.get("path") or "",
                    "note": rec.get("note") or "",
                },
            }
        )
    return items


def _pack_arcname(doc: dict[str, Any], *, used: set[str]) -> str:
    rec = doc.get("record") or {}
    preferred = str(rec.get("path") or "").replace("\\", "/").lstrip("/")
    if preferred.startswith("records/"):
        name = preferred
    else:
        kind = str(rec.get("kind") or "custom")
        disk = KIND_DISK.get(kind, "custom")
        slug = _folder_segment(str(rec.get("slug") or rec.get("ref_key") or "untitled"))
        original = safe_name(str(doc.get("original_filename") or "file"))
        name = f"records/{disk}/{slug}/{original}"
    base = name
    n = 1
    while name.lower() in used:
        stem = Path(base)
        name = f"{stem.with_suffix('')}__{n}{stem.suffix}"
        n += 1
    used.add(name.lower())
    return name


async def build_records_pack(
    db: Any,
    base: Path,
    *,
    kind: str = "",
    ref_key: str = "",
    folder_id: str = "",
    q: str = "",
    date_from: date | datetime | None = None,
    date_to: date | datetime | None = None,
) -> tuple[bytes, dict[str, Any]]:
    """ZIP Records filings for the active destination filters (Manager handoff)."""
    ensure_records_workspace(base)
    # Over-fetch one to detect cap overflow
    rows = await list_record_files(
        db,
        kind=kind,
        ref_key=ref_key,
        folder_id=folder_id,
        q=q,
        date_from=date_from,
        date_to=date_to,
        limit=_MAX_PACK_FILES + 1,
    )
    if len(rows) > _MAX_PACK_FILES:
        raise ValueError(
            f"More than {_MAX_PACK_FILES} files match. Narrow the destination or dates."
        )
    ids = [str(r.get("id") or "") for r in rows if r.get("id")]
    docs: list[dict[str, Any]] = []
    if ids:
        docs = await db.receipt_files.find({"_id": {"$in": ids}}).to_list(
            length=len(ids) + 10
        )
        order = {fid: i for i, fid in enumerate(ids)}
        docs.sort(key=lambda d: order.get(str(d.get("_id")), 10_000))

    buf = io.BytesIO()
    used: set[str] = set()
    copied = 0
    missing = 0
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for doc in docs:
            path = resolve_receipt_file_path(base, doc)
            if path is None or not path.is_file():
                missing += 1
                continue
            arc = _pack_arcname(doc, used=used)
            try:
                zf.write(path, arcname=arc)
                copied += 1
            except OSError:
                missing += 1
        readme = (
            "Tahmeed Paperwork → Records pack\n"
            f"kind={kind or 'any'} ref_key={ref_key or '-'} "
            f"folder_id={folder_id or '-'} q={q or '-'}\n"
            f"files_copied={copied} missing={missing}\n"
        )
        zf.writestr("README.txt", readme)

    payload = buf.getvalue()
    digest = hashlib.sha256(payload).hexdigest()
    stamp = utcnow().strftime("%Y%m%d")
    kind_part = record_slug(kind or "all", fallback="all")
    dest_part = record_slug(ref_key or folder_id or "records", fallback="records")
    filename = f"tahmeed-records_{kind_part}_{dest_part}_{stamp}.zip"
    summary = {
        "filename": filename,
        "sha256": digest,
        "file_count": copied,
        "missing": missing,
        "kind": kind or "",
        "ref_key": ref_key or "",
        "folder_id": folder_id or "",
        "q": q or "",
        "bytes": len(payload),
    }
    return payload, summary
