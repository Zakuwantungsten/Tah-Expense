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
from .mongo_async import aggregate_list
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


def parse_record_file_date(value: date | datetime | str | None) -> date:
    """Normalize optional filing date (backlog month/day). Defaults to today UTC."""
    if value is None or value == "":
        return utcnow().date()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC).date()
        return value.astimezone(UTC).date()
    if isinstance(value, date):
        return value
    text = str(value).strip()[:10]
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("file_date must be YYYY-MM-DD") from exc


def _parent_id_str(doc: dict[str, Any] | None) -> str | None:
    if not doc:
        return None
    raw = doc.get("parent_id")
    if raw in (None, ""):
        return None
    return str(raw)


def _serialize_folder(
    doc: dict[str, Any],
    *,
    path_labels: list[str] | None = None,
    path_slugs: list[str] | None = None,
    depth: int | None = None,
) -> dict[str, Any]:
    labels = list(path_labels or [])
    slugs = list(path_slugs or [])
    label = str(doc.get("label") or "")
    slug = str(doc.get("slug") or "")
    if not labels and label:
        labels = [label]
    if not slugs and slug:
        slugs = [slug]
    display = " / ".join(labels) if labels else label
    return {
        "id": str(doc.get("_id")),
        "kind": doc.get("kind") or "custom",
        "label": label,
        "slug": slug,
        "ref_key": doc.get("ref_key") or slug,
        "parent_id": _parent_id_str(doc),
        "path_labels": labels,
        "path_slugs": slugs,
        "display_label": display,
        "depth": int(depth if depth is not None else max(0, len(labels) - 1)),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
        "created_by": str(doc["created_by"])
        if doc.get("created_by") is not None
        else None,
    }


async def _folder_ancestry(db: Any, doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Root → leaf chain for a custom folder (max depth 8)."""
    chain = [doc]
    seen = {doc.get("_id")}
    parent_raw = doc.get("parent_id")
    for _ in range(8):
        if parent_raw in (None, ""):
            break
        try:
            oid = parent_raw if isinstance(parent_raw, ObjectId) else ObjectId(str(parent_raw))
        except Exception:
            break
        if oid in seen:
            break
        parent = await db.receipt_record_folders.find_one(
            {"_id": oid, "kind": "custom", "deleted_at": None}
        )
        if not parent:
            break
        chain.insert(0, parent)
        seen.add(oid)
        parent_raw = parent.get("parent_id")
    return chain


async def _serialize_folder_with_path(db: Any, doc: dict[str, Any]) -> dict[str, Any]:
    chain = await _folder_ancestry(db, doc)
    labels = [str(d.get("label") or "").strip() or "Folder" for d in chain]
    slugs = [
        str(d.get("slug") or record_slug(str(d.get("label") or "folder"))) for d in chain
    ]
    return _serialize_folder(
        doc, path_labels=labels, path_slugs=slugs, depth=max(0, len(chain) - 1)
    )


def _split_folder_path(label: str) -> list[str]:
    """Split ``Parent/Child`` or ``Parent / Child`` into segments."""
    text = (label or "").replace("\\", "/")
    parts = [ " ".join(p.split()) for p in text.split("/") ]
    return [p for p in parts if p]


async def list_custom_folders(db: Any, *, include_deleted: bool = False) -> list[dict[str, Any]]:
    query: dict[str, Any] = {"kind": "custom"}
    if not include_deleted:
        query["deleted_at"] = None
    cursor = db.receipt_record_folders.find(query).sort([("label", 1)])
    docs = await cursor.to_list(length=500)
    rows = [await _serialize_folder_with_path(db, doc) for doc in docs]
    # Depth-first: parents before children, stable by display path
    rows.sort(
        key=lambda r: (
            "/".join(str(s).lower() for s in (r.get("path_slugs") or [r.get("slug") or ""])),
            str(r.get("label") or "").lower(),
        )
    )
    return rows


async def create_custom_folder(
    db: Any,
    *,
    label: str,
    actor_id: ObjectId | None = None,
    parent_id: str | ObjectId | None = None,
) -> dict[str, Any]:
    clean = " ".join((label or "").split())
    if not clean:
        raise ValueError("Folder label is required")
    if len(clean) > 120:
        raise ValueError("Folder label is too long")
    if "/" in clean or "\\" in clean:
        raise ValueError(
            "Folder name cannot contain /. Use Parent/Child to create a nested path."
        )

    parent_oid: ObjectId | None = None
    parent_doc: dict[str, Any] | None = None
    if parent_id not in (None, ""):
        try:
            parent_oid = (
                parent_id if isinstance(parent_id, ObjectId) else ObjectId(str(parent_id))
            )
        except Exception as exc:
            raise ValueError("Invalid parent folder id") from exc
        parent_doc = await db.receipt_record_folders.find_one(
            {"_id": parent_oid, "kind": "custom", "deleted_at": None}
        )
        if not parent_doc:
            raise ValueError("Parent custom folder not found")
        ancestry = await _folder_ancestry(db, parent_doc)
        if len(ancestry) >= 8:
            raise ValueError("Custom folder nesting is limited to 8 levels")

    slug = record_slug(clean)
    clash_query: dict[str, Any] = {
        "kind": "custom",
        "slug": slug,
        "deleted_at": None,
    }
    if parent_oid is None:
        clash_query["$or"] = [
            {"parent_id": None},
            {"parent_id": {"$exists": False}},
            {"parent_id": ""},
        ]
    else:
        clash_query["parent_id"] = parent_oid
    existing = await db.receipt_record_folders.find_one(clash_query)
    if existing:
        raise ValueError(
            f"A custom folder named “{existing.get('label') or clean}” already exists here"
        )
    now = utcnow()
    doc = {
        "kind": "custom",
        "label": clean,
        "slug": slug,
        "ref_key": slug,
        "parent_id": parent_oid,
        "created_at": now,
        "updated_at": now,
        "created_by": actor_id,
        "deleted_at": None,
    }
    result = await db.receipt_record_folders.insert_one(doc)
    doc["_id"] = result.inserted_id
    return await _serialize_folder_with_path(db, doc)


async def ensure_custom_folder_path(
    db: Any,
    *,
    path: str,
    actor_id: ObjectId | None = None,
) -> dict[str, Any]:
    """Create or reuse a nested custom folder from ``Parent/Child`` path text."""
    segments = _split_folder_path(path)
    if not segments:
        raise ValueError("Folder path is required")
    if len(segments) > 8:
        raise ValueError("Custom folder nesting is limited to 8 levels")
    parent_id: ObjectId | None = None
    current: dict[str, Any] | None = None
    for segment in segments:
        slug = record_slug(segment)
        query: dict[str, Any] = {
            "kind": "custom",
            "slug": slug,
            "deleted_at": None,
        }
        if parent_id is None:
            query["$or"] = [
                {"parent_id": None},
                {"parent_id": {"$exists": False}},
                {"parent_id": ""},
            ]
        else:
            query["parent_id"] = parent_id
        found = await db.receipt_record_folders.find_one(query)
        if found:
            current = found
            parent_id = found["_id"]
            continue
        created = await create_custom_folder(
            db, label=segment, actor_id=actor_id, parent_id=parent_id
        )
        parent_id = ObjectId(created["id"])
        current = await db.receipt_record_folders.find_one({"_id": parent_id})
    if not current:
        raise ValueError("Could not resolve custom folder path")
    return await _serialize_folder_with_path(db, current)


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
    if "/" in clean or "\\" in clean:
        raise ValueError("Folder name cannot contain /")
    doc = await db.receipt_record_folders.find_one(
        {"_id": folder_id, "kind": "custom", "deleted_at": None}
    )
    if not doc:
        raise ValueError("Custom folder not found")
    new_slug = record_slug(clean)
    parent_raw = doc.get("parent_id")
    clash_query: dict[str, Any] = {
        "kind": "custom",
        "slug": new_slug,
        "deleted_at": None,
        "_id": {"$ne": folder_id},
    }
    if parent_raw in (None, ""):
        clash_query["$or"] = [
            {"parent_id": None},
            {"parent_id": {"$exists": False}},
            {"parent_id": ""},
        ]
    else:
        clash_query["parent_id"] = parent_raw
    clash = await db.receipt_record_folders.find_one(clash_query)
    if clash:
        raise ValueError(f"A custom folder named “{clash.get('label') or clean}” already exists here")
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
    return await _serialize_folder_with_path(db, updated or doc)


async def delete_custom_folder(
    db: Any,
    folder_id: ObjectId,
    *,
    cascade: bool = False,
    actor_id: ObjectId | None = None,
) -> dict[str, Any]:
    """Soft-delete a custom folder. With ``cascade``, trash filed files + subfolders."""
    doc = await db.receipt_record_folders.find_one(
        {"_id": folder_id, "kind": "custom", "deleted_at": None}
    )
    if not doc:
        raise ValueError("Custom folder not found")

    if not cascade:
        child = await db.receipt_record_folders.find_one(
            {"kind": "custom", "parent_id": folder_id, "deleted_at": None}
        )
        if child:
            raise ValueError(
                f"Cannot delete “{doc.get('label')}” — it still has subfolders. "
                "Delete or move children first, or use cascade trash."
            )
        in_use = await db.receipt_files.count_documents(
            {
                "record.folder_id": str(folder_id),
                "security_rejected": {"$ne": True},
                "trashed": {"$ne": True},
            }
        )
        if in_use:
            raise ValueError(
                f"Cannot delete “{doc.get('label')}” — {in_use} file(s) are filed there. "
                "Trash the files first, or use cascade trash."
            )
        now = utcnow()
        await db.receipt_record_folders.update_one(
            {"_id": folder_id},
            {"$set": {"deleted_at": now, "updated_at": now}},
        )
        return {
            "id": str(folder_id),
            "deleted": True,
            "cascade": False,
            "trashed_files": 0,
            "deleted_folders": 1,
        }

    # Cascade: collect this folder + all descendants, trash files, soft-delete folders.
    all_docs = await db.receipt_record_folders.find(
        {"kind": "custom", "deleted_at": None}
    ).to_list(length=5000)
    by_parent: dict[str, list[dict[str, Any]]] = {}
    for row in all_docs:
        pid = str(row.get("parent_id") or "")
        by_parent.setdefault(pid, []).append(row)

    subtree: list[dict[str, Any]] = []

    def _walk(oid: ObjectId) -> None:
        key = str(oid)
        for child in by_parent.get(key, []):
            _walk(child["_id"])
        # Find self
        for row in all_docs:
            if row.get("_id") == oid:
                subtree.append(row)
                break

    _walk(folder_id)
    if not subtree:
        subtree = [doc]

    folder_ids = [str(r.get("_id")) for r in subtree if r.get("_id") is not None]
    file_docs = await db.receipt_files.find(
        {
            "record.folder_id": {"$in": folder_ids},
            "security_rejected": {"$ne": True},
            "trashed": {"$ne": True},
        },
        {"_id": 1},
    ).to_list(length=5000)
    file_ids = [str(d.get("_id") or "") for d in file_docs if d.get("_id")]

    from .receipt_ops import trash_receipt_files

    trashed = await trash_receipt_files(db, file_ids=file_ids, actor_id=actor_id)
    now = utcnow()
    deleted_folders = 0
    for row in subtree:
        oid = row.get("_id")
        if oid is None:
            continue
        result = await db.receipt_record_folders.update_one(
            {"_id": oid, "deleted_at": None},
            {"$set": {"deleted_at": now, "updated_at": now}},
        )
        if int(getattr(result, "modified_count", 0) or 0) > 0:
            deleted_folders += 1

    return {
        "id": str(folder_id),
        "deleted": True,
        "cascade": True,
        "trashed_files": int(trashed.get("trashed") or 0),
        "deleted_folders": deleted_folders,
        # Sample for audit accountability (who cascade-trashed which files).
        "file_ids": file_ids[:40],
        "folder_ids": folder_ids[:40],
    }


async def _record_file_stats(
    db: Any,
) -> tuple[
    dict[tuple[str, str], dict[str, int]],
    dict[str, dict[str, int]],
]:
    """Counts + byte totals of Records files by (kind, ref_key) and custom folder_id."""
    pipeline = [
        {
            "$match": {
                "record": {"$exists": True, "$ne": None},
                "security_rejected": {"$ne": True},
                "trashed": {"$ne": True},
            }
        },
        {
            "$group": {
                "_id": {
                    "kind": "$record.kind",
                    "ref_key": "$record.ref_key",
                    "folder_id": "$record.folder_id",
                },
                "count": {"$sum": 1},
                "total_size": {"$sum": {"$ifNull": ["$size", 0]}},
            }
        },
    ]
    by_ref: dict[tuple[str, str], dict[str, int]] = {}
    by_folder: dict[str, dict[str, int]] = {}
    # PyMongo AsyncMongoClient: aggregate() is a coroutine (see mongo_async).
    rows = await aggregate_list(db.receipt_files, pipeline, length=None)
    for row in rows:
        key = row.get("_id") or {}
        kind = str(key.get("kind") or "").strip().lower()
        count = int(row.get("count") or 0)
        total_size = int(row.get("total_size") or 0)
        if count <= 0 or not kind:
            continue
        folder_id = key.get("folder_id")
        if folder_id not in (None, ""):
            fid = str(folder_id).strip()
            if fid:
                prev = by_folder.get(fid) or {"count": 0, "total_size": 0}
                prev["count"] += count
                prev["total_size"] += total_size
                by_folder[fid] = prev
        ref_key = " ".join(str(key.get("ref_key") or "").split())
        if ref_key:
            pair = (kind, ref_key)
            prev = by_ref.get(pair) or {"count": 0, "total_size": 0}
            prev["count"] += count
            prev["total_size"] += total_size
            by_ref[pair] = prev
    return by_ref, by_folder


async def _record_file_counts(
    db: Any,
) -> tuple[dict[tuple[str, str], int], dict[str, int]]:
    """Counts of indexed Records files by (kind, ref_key) and by custom folder_id."""
    by_ref_stats, by_folder_stats = await _record_file_stats(db)
    by_ref = {k: int(v.get("count") or 0) for k, v in by_ref_stats.items()}
    by_folder = {k: int(v.get("count") or 0) for k, v in by_folder_stats.items()}
    return by_ref, by_folder


async def list_record_destinations(db: Any) -> dict[str, Any]:
    """Picker payload: items, suppliers, separate feeds, custom folders.

    Each row includes ``file_count`` and ``total_size`` (bytes of indexed Records
    files for that destination).
    """
    by_ref, by_folder = await _record_file_stats(db)
    cats = await db.categories.find(
        {},
        {"name": 1, "sidebar_name": 1, "is_supplier": 1},
    ).sort([("name", 1)]).to_list(length=2000)

    items: list[dict[str, Any]] = []
    suppliers: list[dict[str, Any]] = []
    for cat in cats:
        name = str(cat.get("name") or "").strip()
        if not name:
            continue
        label = str(cat.get("sidebar_name") or name).strip() or name
        kind = "supplier" if cat.get("is_supplier") is True else "item"
        stats = by_ref.get((kind, name)) or {}
        row = {
            "ref_key": name,
            "label": label,
            "slug": record_slug(name),
            "file_count": int(stats.get("count") or 0),
            "total_size": int(stats.get("total_size") or 0),
        }
        if kind == "supplier":
            suppliers.append(row)
        else:
            items.append(row)

    separate = []
    for key, label in SEPARATE_RECORD_DESTINATIONS:
        stats = by_ref.get(("separate", key)) or {}
        separate.append(
            {
                "ref_key": key,
                "label": label,
                "slug": record_slug(key),
                "file_count": int(stats.get("count") or 0),
                "total_size": int(stats.get("total_size") or 0),
            }
        )
    customs = await list_custom_folders(db)
    for row in customs:
        fid = str(row.get("id") or "").strip()
        stats = by_folder.get(fid) if fid else None
        row["file_count"] = int((stats or {}).get("count") or 0)
        row["total_size"] = int((stats or {}).get("total_size") or 0)
    return {
        "items": items,
        "suppliers": suppliers,
        "separate": separate,
        "custom": customs,
    }


async def discard_record_upload_batch(
    db: Any,
    base: Path,
    file_ids: list[str],
) -> int:
    """Hard-delete Records files from a failed / cancelled multi-upload batch.

    Unlike park discard, this intentionally removes ``inbox_status=approved``
    record filings (they are company paper, not chase inbox).
    """
    discarded = 0
    root = attachments_root(base)
    for raw in file_ids:
        fid = (raw or "").strip()
        if not fid:
            continue
        doc = await db.receipt_files.find_one({"_id": fid})
        if not doc:
            continue
        if not isinstance(doc.get("record"), dict):
            continue
        if doc.get("transaction_ids"):
            # Linked to an expense — never roll back via upload batch discard.
            continue
        paths: list[Path] = []
        primary = resolve_receipt_file_path(base, doc)
        if primary is not None:
            paths.append(primary)
        for rel in doc.get("materialized_paths") or []:
            text = str(rel or "").replace("\\", "/").lstrip("/")
            if not text:
                continue
            paths.append(root / Path(*text.split("/")))
        rec_path = str((doc.get("record") or {}).get("path") or "").replace("\\", "/")
        if rec_path:
            paths.append(root / Path(*rec_path.lstrip("/").split("/")))
        await db.receipt_files.delete_one({"_id": fid})
        seen: set[str] = set()
        for path in paths:
            try:
                key = str(path.resolve())
            except OSError:
                key = str(path)
            if key in seen:
                continue
            seen.add(key)
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        discarded += 1
    return discarded


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
        serialized = await _serialize_folder_with_path(db, doc)
        return {
            "kind": "custom",
            "folder_id": serialized["id"],
            "ref_key": serialized.get("ref_key") or serialized.get("slug") or "",
            "label": serialized.get("display_label") or serialized.get("label") or "",
            "slug": serialized.get("slug") or record_slug(str(doc.get("label") or "custom")),
            "path_slugs": list(serialized.get("path_slugs") or []),
            "path_labels": list(serialized.get("path_labels") or []),
            "parent_id": serialized.get("parent_id"),
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
    path_slugs = [
        _folder_segment(str(s))
        for s in (dest.get("path_slugs") or [])
        if str(s or "").strip()
    ]
    if not path_slugs:
        path_slugs = [
            _folder_segment(dest.get("slug") or dest.get("ref_key") or "untitled")
        ]
    slug_path = "/".join(path_slugs)
    year, month = f"{day.year:04d}", f"{day.month:02d}"
    return (
        f"records/{disk_kind}/{slug_path}/{year}/{month}/"
        f"{safe_name(Path(stored_name).name)}"
    )


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
    file_date: date | datetime | str | None = None,
) -> dict[str, Any]:
    """Validate, store under ``files/``, materialize under ``records/…``, index.

    ``file_date`` controls the browse ``YYYY/MM`` folder (backlog). ``uploaded_at``
    remains the real upload timestamp.
    """
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
    filed_on = parse_record_file_date(file_date)
    filed_at = datetime(filed_on.year, filed_on.month, filed_on.day, tzinfo=UTC)
    browse_rel = _records_browse_rel(
        destination, stored_name=stored_name, when=filed_at
    )
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
    path_slugs = [
        str(s) for s in (destination.get("path_slugs") or []) if str(s or "").strip()
    ]
    file_date_iso = filed_on.isoformat()
    record_block = {
        "kind": destination["kind"],
        "folder_id": destination.get("folder_id"),
        "ref_key": destination.get("ref_key") or "",
        "label": destination.get("label") or "",
        "slug": destination.get("slug") or "",
        "path_slugs": path_slugs,
        "path": browse_rel,
        "note": note_clean,
        "file_date": file_date_iso,
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


async def move_record_files(
    db: Any,
    base: Path,
    *,
    file_ids: list[str],
    destination: dict[str, Any],
) -> dict[str, Any]:
    """Move Records files into another destination (Cut → Paste).

    Updates ``record`` metadata and rematerializes the browse copy under the
    new ``records/…`` path. Primary ``files/`` bytes stay put.
    """
    ensure_records_workspace(base)
    root = attachments_root(base)
    moved = 0
    skipped = 0
    for raw in file_ids:
        fid = (raw or "").strip()
        if not fid:
            continue
        doc = await db.receipt_files.find_one({"_id": fid})
        if not doc or doc.get("trashed") is True:
            skipped += 1
            continue
        rec = doc.get("record") if isinstance(doc.get("record"), dict) else None
        if not rec:
            skipped += 1
            continue

        filed = _effective_record_day(doc) or utcnow()
        stored_name = Path(str(doc.get("stored_path") or "")).name
        if not stored_name:
            skipped += 1
            continue
        new_rel = _records_browse_rel(
            destination, stored_name=stored_name, when=filed
        )
        new_abs = root / Path(*new_rel.split("/"))
        old_rel = str(rec.get("path") or "").replace("\\", "/").lstrip("/")
        primary = resolve_receipt_file_path(base, doc)

        try:
            new_abs.parent.mkdir(parents=True, exist_ok=True)
            src = primary if primary is not None and primary.is_file() else None
            if src is None and old_rel:
                candidate = root / Path(*old_rel.split("/"))
                if candidate.is_file():
                    src = candidate
            if src is None:
                skipped += 1
                continue
            if new_abs.resolve() != src.resolve():
                shutil.copy2(src, new_abs)
        except OSError:
            skipped += 1
            continue

        path_slugs = [
            str(s)
            for s in (destination.get("path_slugs") or [])
            if str(s or "").strip()
        ]
        note_clean = str(rec.get("note") or "")
        file_date_iso = str(rec.get("file_date") or "") or (
            filed.date().isoformat() if isinstance(filed, datetime) else ""
        )
        new_record = {
            "kind": destination["kind"],
            "folder_id": destination.get("folder_id"),
            "ref_key": destination.get("ref_key") or "",
            "label": destination.get("label") or "",
            "slug": destination.get("slug") or "",
            "path_slugs": path_slugs,
            "path": new_rel,
            "note": note_clean,
            "file_date": file_date_iso,
        }
        # Preserve bridge flags if present
        if rec.get("from_approved"):
            new_record["from_approved"] = True

        mats = [
            str(p).replace("\\", "/")
            for p in (doc.get("materialized_paths") or [])
            if str(p or "").strip()
        ]
        mats = [p for p in mats if p != old_rel and p != new_rel]
        mats.append(new_rel)

        await db.receipt_files.update_one(
            {"_id": fid},
            {
                "$set": {
                    "record": new_record,
                    "materialized_paths": mats,
                    "materialized_at": utcnow(),
                }
            },
        )

        if old_rel and old_rel != new_rel:
            try:
                (root / Path(*old_rel.split("/"))).unlink(missing_ok=True)
            except OSError:
                pass
        moved += 1

    return {
        "moved": moved,
        "skipped": skipped,
        "requested": len(file_ids),
        "destination": {
            "kind": destination.get("kind"),
            "ref_key": destination.get("ref_key"),
            "folder_id": destination.get("folder_id"),
            "label": destination.get("label"),
        },
    }


def _effective_record_day(doc: dict[str, Any]) -> datetime | None:
    """Filing day for filters: record.file_date, else uploaded_at."""
    rec = doc.get("record") or {}
    raw = rec.get("file_date")
    if raw not in (None, ""):
        try:
            day = parse_record_file_date(raw)
            return datetime(day.year, day.month, day.day, tzinfo=UTC)
        except ValueError:
            pass
    uploaded = doc.get("uploaded_at")
    if isinstance(uploaded, datetime):
        if uploaded.tzinfo is None:
            return uploaded.replace(tzinfo=UTC)
        return uploaded.astimezone(UTC)
    if isinstance(uploaded, date):
        return datetime(uploaded.year, uploaded.month, uploaded.day, tzinfo=UTC)
    return None


def _serialize_record_file_row(
    doc: dict[str, Any], *, synthetic_record: dict[str, Any] | None = None
) -> dict[str, Any]:
    rec = synthetic_record if synthetic_record is not None else (doc.get("record") or {})
    return {
        "id": str(doc.get("_id")),
        "original_filename": doc.get("original_filename") or "",
        "content_type": doc.get("content_type") or "",
        "size": int(doc.get("size") or 0),
        "sha256": doc.get("sha256") or "",
        "stored_path": doc.get("stored_path") or "",
        "uploaded_at": doc.get("uploaded_at"),
        "source": doc.get("source") or ("approved" if synthetic_record else "records"),
        "inbox_status": doc.get("inbox_status") or INBOX_APPROVED,
        "record": {
            "kind": rec.get("kind") or "",
            "folder_id": rec.get("folder_id"),
            "ref_key": rec.get("ref_key") or "",
            "label": rec.get("label") or "",
            "slug": rec.get("slug") or "",
            "path_slugs": list(rec.get("path_slugs") or []),
            "path": rec.get("path") or "",
            "note": rec.get("note") or "",
            "file_date": rec.get("file_date") or None,
            "from_approved": bool(rec.get("from_approved")),
        },
    }


async def _approved_bridge_files(
    db: Any,
    *,
    kind: str,
    ref_key: str,
    q: str = "",
    date_from: date | datetime | None = None,
    date_to: date | datetime | None = None,
    limit: int = 200,
    exclude_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Approved chase receipts that belong under an Item/Supplier Records folder.

    Matching: linked transaction ``item`` (item folders) or ``payee`` (supplier).
    """
    key = (kind or "").strip().lower()
    needle_key = " ".join((ref_key or "").split())
    if key not in {"item", "supplier"} or not needle_key:
        return []

    query: dict[str, Any] = {
        "inbox_status": INBOX_APPROVED,
        "security_rejected": {"$ne": True},
        "trashed": {"$ne": True},
        "$or": [
            {"record": {"$exists": False}},
            {"record": None},
        ],
    }
    start = _as_day_start(date_from)
    end = _as_day_end(date_to)
    if start is not None or end is not None:
        span: dict[str, Any] = {}
        if start is not None:
            span["$gte"] = start
        if end is not None:
            span["$lte"] = end
        query["uploaded_at"] = span
    name_q = (q or "").strip()
    if name_q:
        query["original_filename"] = {"$regex": re.escape(name_q), "$options": "i"}

    fetch_n = min(800, max(limit * 4, limit))
    docs = (
        await db.receipt_files.find(query)
        .sort("uploaded_at", -1)
        .limit(fetch_n)
        .to_list(length=fetch_n)
    )
    if not docs:
        return []

    from .receipt_ops import _linked_entry_summaries_for_files

    linked = await _linked_entry_summaries_for_files(db, docs)
    target = needle_key.casefold()
    slug = record_slug(needle_key)
    skip = exclude_ids or set()
    out: list[dict[str, Any]] = []
    for doc in docs:
        fid = str(doc.get("_id") or "")
        if not fid or fid in skip:
            continue
        entries = list(linked.get(fid) or [])
        matched = False
        for entry in entries:
            if key == "item":
                value = str(entry.get("item") or "").strip()
            else:
                value = str(entry.get("payee") or "").strip()
            if value and value.casefold() == target:
                matched = True
                break
        if not matched:
            # Fallback: approved browse path contains the item folder segment
            for mp in doc.get("materialized_paths") or []:
                text = str(mp or "").replace("\\", "/")
                if f"/{_folder_segment(needle_key)}/" in f"/{text}/":
                    matched = True
                    break
        if not matched:
            continue
        path = ""
        for mp in doc.get("materialized_paths") or []:
            text = str(mp or "").replace("\\", "/").lstrip("/")
            if text.startswith("approved/"):
                path = text
                break
            if text and not path:
                path = f"approved/{text}" if not text.startswith("approved/") else text
        out.append(
            _serialize_record_file_row(
                doc,
                synthetic_record={
                    "kind": key,
                    "folder_id": None,
                    "ref_key": needle_key,
                    "label": needle_key,
                    "slug": slug,
                    "path_slugs": [slug],
                    "path": path,
                    "note": "",
                    "from_approved": True,
                },
            )
        )
        if len(out) >= limit:
            break
    return out


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
    include_approved: bool = True,
) -> list[dict[str, Any]]:
    query: dict[str, Any] = {
        "record": {"$exists": True, "$ne": None},
        "security_rejected": {"$ne": True},
        "trashed": {"$ne": True},
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
    # Date filters use record.file_date when set (backlog filing month), else
    # uploaded_at — applied after fetch so mixed rows stay correct.
    needle = (q or "").strip()
    cap = max(1, min(int(limit), 500))
    fetch_n = min(500, cap * 3 if (start is not None or end is not None) else cap)
    if needle:
        from .receipt_ops import find_receipt_files_matching_name

        docs = await find_receipt_files_matching_name(
            db, query, needle, limit=fetch_n
        )
    else:
        docs = (
            await db.receipt_files.find(query)
            .sort("uploaded_at", -1)
            .limit(fetch_n)
            .to_list(length=fetch_n)
        )
    if start is not None or end is not None:
        filtered: list[dict[str, Any]] = []
        for doc in docs:
            effective = _effective_record_day(doc)
            if effective is None:
                continue
            if start is not None and effective < start:
                continue
            if end is not None and effective > end:
                continue
            filtered.append(doc)
        docs = filtered[:cap]
    else:
        docs = docs[:cap]
    items: list[dict[str, Any]] = [_serialize_record_file_row(doc) for doc in docs]
    seen = {str(row.get("id") or "") for row in items}

    if (
        include_approved
        and kind_key in {"item", "supplier"}
        and rk
        and not fid
        and len(items) < cap
    ):
        bridged = await _approved_bridge_files(
            db,
            kind=kind_key,
            ref_key=rk,
            q=needle,
            date_from=date_from,
            date_to=date_to,
            limit=cap - len(items),
            exclude_ids=seen,
        )
        items.extend(bridged)
        items.sort(
            key=lambda row: row.get("uploaded_at") or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )
        items = items[:cap]
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
