"""Date-range evidence pack: ZIP folders + index.csv + export audit record."""

from __future__ import annotations

import csv
import hashlib
import io
import re
import zipfile
from datetime import date, datetime
from pathlib import Path
from typing import Any

from bson import ObjectId

from .attachment_ops import resolve_attachment_path
from .receipt_ops import normalize_person_name, resolve_receipt_file_path
from .security import utcnow

_INDEX_COLUMNS = [
    "pack_path",
    "original_filename",
    "sha256",
    "size",
    "transaction_id",
    "date",
    "amount",
    "currency",
    "item",
    "payee",
    "ownership",
    "approver",
    "receipt_status",
    "request_status",
    "waiver_reason",
    "waived_by",
    "row_has_no_file",
]

_MAX_ROWS = 8000
_SAFE_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


def _as_datetime(value: date | datetime) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    return datetime(value.year, value.month, value.day)


def _safe_segment(value: str, *, fallback: str = "unknown") -> str:
    text = _SAFE_RE.sub("_", (value or "").strip())
    text = text.strip(" .") or fallback
    return text[:80]


def _tx_item(tx: dict[str, Any]) -> str:
    return str(tx.get("item") or tx.get("category_name") or "unclassified").strip()


def _fmt_date(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "")
    return text[:10] if text else ""


def _pack_filename(tx: dict[str, Any], file_id: str, original: str) -> str:
    day = _fmt_date(tx.get("date")) or "undated"
    amount = tx.get("amount")
    amount_s = (
        str(int(amount))
        if isinstance(amount, (int, float)) and float(amount).is_integer()
        else str(amount if amount is not None else "0")
    )
    ownership = _safe_segment(str(tx.get("ownership") or "unknown"), fallback="unknown")
    ext = Path(original or "").suffix.lower()
    if not ext or len(ext) > 8:
        ext = ".bin"
    short_id = _safe_segment(file_id, fallback="file")[:24]
    return f"{day}_{amount_s}_{ownership}_{short_id}{ext}"


def _pack_relpath(tx: dict[str, Any], filename: str) -> str:
    day = tx.get("date")
    if isinstance(day, datetime):
        year, month = f"{day.year:04d}", f"{day.month:02d}"
    elif isinstance(day, date):
        year, month = f"{day.year:04d}", f"{day.month:02d}"
    else:
        text = _fmt_date(day)
        year = text[:4] if len(text) >= 4 else "0000"
        month = text[5:7] if len(text) >= 7 else "00"
    item = _safe_segment(_tx_item(tx), fallback="unclassified")
    return f"{year}/{month}/{item}/{filename}"


async def _request_map(
    db: Any, tx_ids: list[ObjectId]
) -> dict[ObjectId, dict[str, Any]]:
    if not tx_ids:
        return {}
    docs = await db.receipt_requests.find(
        {"transaction_id": {"$in": tx_ids}},
        {
            "transaction_id": 1,
            "status": 1,
            "waiver_reason": 1,
            "waived_by_name": 1,
            "waived_by": 1,
        },
    ).to_list(length=None)
    out: dict[ObjectId, dict[str, Any]] = {}
    for doc in docs or []:
        tid = doc.get("transaction_id")
        if isinstance(tid, ObjectId):
            out[tid] = doc
    return out


def _attachment_metas(tx: dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    for raw in tx.get("attachments") or []:
        if isinstance(raw, dict) and (
            raw.get("id") or raw.get("file_id") or raw.get("stored_name") or raw.get("stored_path")
        ):
            items.append(raw)
    return items


def _normalize_item_filters(item: str = "", items: list[str] | None = None) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for raw in list(items or []) + ([item] if item else []):
        text = (raw or "").strip()
        if not text or text.lower() in seen:
            continue
        seen.add(text.lower())
        found.append(text)
    # Also split comma-separated single field (desk convenience)
    expanded: list[str] = []
    seen2: set[str] = set()
    for text in found:
        parts = [p.strip() for p in text.split(",")]
        for part in parts:
            if not part or part.lower() in seen2:
                continue
            seen2.add(part.lower())
            expanded.append(part)
    return expanded


async def _approved_file_ids(db: Any, file_ids: list[str]) -> set[str]:
    if not file_ids:
        return set()
    docs = await db.receipt_files.find(
        {"_id": {"$in": file_ids}, "inbox_status": "approved"},
        {"_id": 1},
    ).to_list(length=len(file_ids) + 50)
    return {str(doc.get("_id")) for doc in docs or []}


async def build_evidence_pack(
    db: Any,
    attachments_dir: Path,
    *,
    date_from: date | datetime,
    date_to: date | datetime,
    item: str = "",
    items: list[str] | None = None,
    payee: str = "",
    ownership: str = "",
    approved_only: bool = False,
) -> tuple[bytes, dict[str, Any]]:
    """Build an in-memory ZIP. Returns ``(zip_bytes, summary)``."""
    start = _as_datetime(date_from)
    end = _as_datetime(date_to)
    if end.hour == 0 and end.minute == 0 and end.second == 0 and end.microsecond == 0:
        end = end.replace(hour=23, minute=59, second=59, microsecond=999999)
    if end < start:
        raise ValueError("date_to must be on or after date_from")

    query: dict[str, Any] = {
        "date": {"$gte": start, "$lte": end},
        "trashed": {"$ne": True},
        "discarded": {"$ne": True},
    }
    item_filters = _normalize_item_filters(item, items)
    if item_filters:
        query["$or"] = [
            {"item": {"$in": item_filters}},
            {"category_name": {"$in": item_filters}},
        ]
    payee_q = " ".join((payee or "").strip().upper().split())
    if payee_q:
        query["payee"] = payee_q
    ownership_q = normalize_person_name(ownership)
    if ownership_q:
        query["ownership"] = ownership_q

    cursor = db.transactions.find(query).sort([("date", 1), ("_id", 1)]).limit(_MAX_ROWS + 1)
    txs = await cursor.to_list(length=_MAX_ROWS + 1)
    if len(txs) > _MAX_ROWS:
        raise ValueError(
            f"More than {_MAX_ROWS} rows match this range. Narrow the filters."
        )

    tx_ids = [tx["_id"] for tx in txs if isinstance(tx.get("_id"), ObjectId)]
    requests = await _request_map(db, tx_ids)

    all_file_ids: list[str] = []
    if approved_only:
        for tx in txs:
            for meta in _attachment_metas(tx):
                fid = str(meta.get("id") or meta.get("file_id") or "")
                if fid:
                    all_file_ids.append(fid)
    approved_ids = await _approved_file_ids(db, all_file_ids) if approved_only else set()

    index_rows: list[dict[str, str]] = []
    file_bytes: dict[str, tuple[str, bytes]] = {}  # pack_path -> (sha256, content)
    with_file = 0
    without_file = 0
    waived = 0
    missing_on_disk = 0
    skipped_unapproved = 0

    for tx in txs:
        tid = tx.get("_id")
        req = requests.get(tid) if isinstance(tid, ObjectId) else None
        req_status = str((req or {}).get("status") or "")
        if req_status == "waived":
            waived += 1
        metas = _attachment_metas(tx)
        if approved_only and metas:
            kept = []
            for meta in metas:
                fid = str(meta.get("id") or meta.get("file_id") or "")
                if fid and fid in approved_ids:
                    kept.append(meta)
                else:
                    skipped_unapproved += 1
            metas = kept
        base_fields = {
            "transaction_id": str(tid or ""),
            "date": _fmt_date(tx.get("date")),
            "amount": "" if tx.get("amount") is None else str(tx.get("amount")),
            "currency": str(tx.get("currency") or "TZS"),
            "item": _tx_item(tx),
            "payee": str(tx.get("payee") or ""),
            "ownership": str(tx.get("ownership") or ""),
            "approver": str(tx.get("approver") or ""),
            "receipt_status": str(tx.get("receipt_status") or ""),
            "request_status": req_status,
            "waiver_reason": str((req or {}).get("waiver_reason") or ""),
            "waived_by": str(
                (req or {}).get("waived_by_name")
                or (req or {}).get("waived_by")
                or ""
            ),
        }
        if not metas:
            without_file += 1
            index_rows.append(
                {
                    **base_fields,
                    "pack_path": "",
                    "original_filename": "",
                    "sha256": "",
                    "size": "",
                    "row_has_no_file": "yes",
                }
            )
            continue

        with_file += 1
        wrote_any = False
        for meta in metas:
            file_id = str(meta.get("id") or meta.get("file_id") or "")
            original = str(
                meta.get("filename")
                or meta.get("original_filename")
                or meta.get("stored_name")
                or "file"
            )
            path = resolve_attachment_path(attachments_dir, tid, meta)
            if path is None or not path.is_file():
                missing_on_disk += 1
                index_rows.append(
                    {
                        **base_fields,
                        "pack_path": "",
                        "original_filename": original,
                        "sha256": str(meta.get("sha256") or ""),
                        "size": str(meta.get("size") or ""),
                        "row_has_no_file": "yes",
                    }
                )
                continue
            content = path.read_bytes()
            digest = str(meta.get("sha256") or "") or hashlib.sha256(content).hexdigest()
            filename = _pack_filename(tx, file_id or digest[:12], original)
            rel = _pack_relpath(tx, filename)
            # Same path + same digest: one zip member. Different digest: uniquify.
            existing = file_bytes.get(rel)
            if existing is None:
                file_bytes[rel] = (digest, content)
            elif existing[0] != digest:
                stem = Path(filename).stem
                ext = Path(filename).suffix
                rel = _pack_relpath(tx, f"{stem}_{digest[:8]}{ext}")
                file_bytes[rel] = (digest, content)
            index_rows.append(
                {
                    **base_fields,
                    "pack_path": rel,
                    "original_filename": original,
                    "sha256": digest,
                    "size": str(len(content)),
                    "row_has_no_file": "no",
                }
            )
            wrote_any = True
        if not wrote_any and metas:
            # Counted as with_file above but nothing on disk — correct the tallies.
            with_file -= 1
            without_file += 1

    item_label = ", ".join(item_filters) if item_filters else "(all)"
    built_at = utcnow()
    readme = (
        "Tahmeed evidence pack\n"
        "=====================\n"
        f"Built at (UTC): {built_at.isoformat()}\n"
        f"Date from: {start.date().isoformat()}\n"
        f"Date to: {end.date().isoformat()}\n"
        f"Item filter: {item_label}\n"
        f"Payee filter: {payee_q or '(all)'}\n"
        f"Ownership filter: {ownership_q or '(all)'}\n"
        f"Approved only: {'yes' if approved_only else 'no'}\n"
        f"Rows: {len(txs)}\n"
        f"Rows with file: {with_file}\n"
        f"Rows without file: {without_file}\n"
        f"Waived requests: {waived}\n"
        f"Files in zip: {len(file_bytes)}\n"
        f"Missing on disk: {missing_on_disk}\n"
        f"Skipped unapproved attachments: {skipped_unapproved}\n"
        "\n"
        "index.csv is the authority. Folder paths are for browsing only.\n"
        "Disk browse copies live under approved/year/month/<item>/ "
        "(canonical files/ unchanged).\n"
        "This zip is a snapshot; regenerating from live links may differ.\n"
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("README.txt", readme)
        csv_buf = io.StringIO()
        writer = csv.DictWriter(csv_buf, fieldnames=_INDEX_COLUMNS, lineterminator="\n")
        writer.writeheader()
        for row in index_rows:
            writer.writerow({col: row.get(col, "") for col in _INDEX_COLUMNS})
        zf.writestr("index.csv", csv_buf.getvalue())
        for rel, (_digest, content) in sorted(file_bytes.items()):
            zf.writestr(rel, content)

    payload = buf.getvalue()
    pack_sha = hashlib.sha256(payload).hexdigest()
    summary = {
        "sha256": pack_sha,
        "size": len(payload),
        "row_count": len(txs),
        "with_file": with_file,
        "without_file": without_file,
        "waived": waived,
        "files_in_zip": len(file_bytes),
        "missing_on_disk": missing_on_disk,
        "skipped_unapproved": skipped_unapproved,
        "built_at": built_at,
        "date_from": start,
        "date_to": end,
        "item": item_filters[0] if len(item_filters) == 1 else "",
        "items": item_filters,
        "payee": payee_q,
        "ownership": ownership_q,
        "approved_only": bool(approved_only),
        "kind": "evidence",
        "filename": (
            f"tahmeed-evidence_{start.date().isoformat()}_"
            f"{end.date().isoformat()}.zip"
        ),
    }
    return payload, summary


async def build_consolidate_pack(
    db: Any,
    attachments_dir: Path,
    *,
    date_from: date | datetime,
    date_to: date | datetime,
    item: str = "",
    items: list[str] | None = None,
    payee: str = "",
    ownership: str = "",
    approved_only: bool = True,
) -> tuple[bytes, dict[str, Any]]:
    """Files-only ZIP for a filtered range (no index.csv). Unique files, stable paths."""
    start = _as_datetime(date_from)
    end = _as_datetime(date_to)
    if end.hour == 0 and end.minute == 0 and end.second == 0 and end.microsecond == 0:
        end = end.replace(hour=23, minute=59, second=59, microsecond=999999)
    if end < start:
        raise ValueError("date_to must be on or after date_from")

    query: dict[str, Any] = {
        "date": {"$gte": start, "$lte": end},
        "trashed": {"$ne": True},
        "discarded": {"$ne": True},
    }
    item_filters = _normalize_item_filters(item, items)
    if item_filters:
        query["$or"] = [
            {"item": {"$in": item_filters}},
            {"category_name": {"$in": item_filters}},
        ]
    payee_q = " ".join((payee or "").strip().upper().split())
    if payee_q:
        query["payee"] = payee_q
    ownership_q = normalize_person_name(ownership)
    if ownership_q:
        query["ownership"] = ownership_q

    cursor = db.transactions.find(query).sort([("date", 1), ("_id", 1)]).limit(_MAX_ROWS + 1)
    txs = await cursor.to_list(length=_MAX_ROWS + 1)
    if len(txs) > _MAX_ROWS:
        raise ValueError(
            f"More than {_MAX_ROWS} rows match this range. Narrow the filters."
        )

    all_file_ids: list[str] = []
    for tx in txs:
        for meta in _attachment_metas(tx):
            fid = str(meta.get("id") or meta.get("file_id") or "")
            if fid:
                all_file_ids.append(fid)
    approved_ids = await _approved_file_ids(db, all_file_ids) if approved_only else set()

    file_docs: dict[str, dict[str, Any]] = {}
    if all_file_ids:
        uniq = list(dict.fromkeys(all_file_ids))
        docs = await db.receipt_files.find({"_id": {"$in": uniq}}).to_list(
            length=len(uniq) + 20
        )
        file_docs = {str(d.get("_id")): d for d in docs or []}

    # digest -> (rel_path, content)
    file_bytes: dict[str, tuple[str, bytes]] = {}
    copied = 0
    missing = 0
    skipped_unapproved = 0
    rows_with_file = 0
    rows_without_file = 0
    seen_file_ids: set[str] = set()

    for tx in txs:
        tid = tx.get("_id")
        metas = _attachment_metas(tx)
        if approved_only and metas:
            kept = []
            for meta in metas:
                fid = str(meta.get("id") or meta.get("file_id") or "")
                if fid and fid in approved_ids:
                    kept.append(meta)
                else:
                    skipped_unapproved += 1
            metas = kept
        if not metas:
            rows_without_file += 1
            continue
        rows_with_file += 1
        for meta in metas:
            file_id = str(meta.get("id") or meta.get("file_id") or "")
            if file_id and file_id in seen_file_ids:
                continue
            original = str(
                meta.get("filename")
                or meta.get("original_filename")
                or meta.get("stored_name")
                or "file"
            )
            path = resolve_attachment_path(attachments_dir, tid, meta)
            if (path is None or not path.is_file()) and file_id:
                doc = file_docs.get(file_id)
                if doc:
                    path = resolve_receipt_file_path(attachments_dir, doc)
                    original = str(doc.get("original_filename") or original)
            if path is None or not path.is_file():
                missing += 1
                continue
            content = path.read_bytes()
            digest = str(meta.get("sha256") or "") or hashlib.sha256(content).hexdigest()
            if digest in file_bytes:
                if file_id:
                    seen_file_ids.add(file_id)
                continue
            filename = _pack_filename(tx, file_id or digest[:12], original)
            rel = _pack_relpath(tx, filename)
            # Avoid collisions inside the zip
            if any(existing_rel == rel for existing_rel, _ in file_bytes.values()):
                stem = Path(filename).stem
                ext = Path(filename).suffix
                rel = _pack_relpath(tx, f"{stem}_{digest[:8]}{ext}")
            file_bytes[digest] = (rel, content)
            if file_id:
                seen_file_ids.add(file_id)
            copied += 1

    item_label = ", ".join(item_filters) if item_filters else "(all)"
    built_at = utcnow()
    summary_txt = (
        "Tahmeed receipt consolidate\n"
        "===========================\n"
        f"Built at (UTC): {built_at.isoformat()}\n"
        f"Date from: {start.date().isoformat()}\n"
        f"Date to: {end.date().isoformat()}\n"
        f"Item filter: {item_label}\n"
        f"Payee filter: {payee_q or '(all)'}\n"
        f"Ownership filter: {ownership_q or '(all)'}\n"
        f"Approved only: {'yes' if approved_only else 'no'}\n"
        f"Transaction rows: {len(txs)}\n"
        f"Rows with file: {rows_with_file}\n"
        f"Rows without file: {rows_without_file}\n"
        f"Files copied: {copied}\n"
        f"Missing on disk: {missing}\n"
        f"Skipped unapproved: {skipped_unapproved}\n"
        "\n"
        "Layout: year/month/<item>/<stable-name>\n"
        "Canonical storage remains under files/ on the receipt disk.\n"
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("SUMMARY.txt", summary_txt)
        for _digest, (rel, content) in sorted(
            file_bytes.items(), key=lambda kv: kv[1][0]
        ):
            zf.writestr(rel, content)

    payload = buf.getvalue()
    pack_sha = hashlib.sha256(payload).hexdigest()
    summary = {
        "sha256": pack_sha,
        "size": len(payload),
        "row_count": len(txs),
        "with_file": rows_with_file,
        "without_file": rows_without_file,
        "files_in_zip": copied,
        "missing_on_disk": missing,
        "skipped_unapproved": skipped_unapproved,
        "built_at": built_at,
        "date_from": start,
        "date_to": end,
        "item": item_filters[0] if len(item_filters) == 1 else "",
        "items": item_filters,
        "payee": payee_q,
        "ownership": ownership_q,
        "approved_only": bool(approved_only),
        "kind": "consolidate",
        "filename": (
            f"tahmeed-receipts_{start.date().isoformat()}_"
            f"{end.date().isoformat()}.zip"
        ),
    }
    return payload, summary


async def record_evidence_export(
    db: Any,
    *,
    summary: dict[str, Any],
    actor: dict[str, Any] | None,
) -> ObjectId:
    """Persist who / when / range / pack sha256 for later 'what did we give them'."""
    now = utcnow()
    doc = {
        "created_at": now,
        "updated_at": now,
        "created_by": (actor or {}).get("_id"),
        "created_by_username": (actor or {}).get("username") or "",
        "created_by_role": (actor or {}).get("role") or "",
        "date_from": summary.get("date_from"),
        "date_to": summary.get("date_to"),
        "item": summary.get("item") or "",
        "items": list(summary.get("items") or []),
        "payee": summary.get("payee") or "",
        "ownership": summary.get("ownership") or "",
        "approved_only": bool(summary.get("approved_only")),
        "sha256": summary.get("sha256") or "",
        "size": int(summary.get("size") or 0),
        "row_count": int(summary.get("row_count") or 0),
        "with_file": int(summary.get("with_file") or 0),
        "without_file": int(summary.get("without_file") or 0),
        "waived": int(summary.get("waived") or 0),
        "files_in_zip": int(summary.get("files_in_zip") or 0),
        "filename": summary.get("filename") or "",
        "kind": str(summary.get("kind") or "evidence"),
    }
    result = await db.receipt_exports.insert_one(doc)
    return result.inserted_id


async def list_evidence_exports(db: Any, *, limit: int = 50) -> list[dict[str, Any]]:
    cursor = db.receipt_exports.find({}).sort("created_at", -1).limit(limit)
    docs = await cursor.to_list(length=limit)
    out: list[dict[str, Any]] = []
    for doc in docs:
        out.append(
            {
                "id": str(doc.get("_id")),
                "created_at": doc.get("created_at"),
                "created_by_username": doc.get("created_by_username") or "",
                "date_from": doc.get("date_from"),
                "date_to": doc.get("date_to"),
                "item": doc.get("item") or "",
                "payee": doc.get("payee") or "",
                "ownership": doc.get("ownership") or "",
                "sha256": doc.get("sha256") or "",
                "size": int(doc.get("size") or 0),
                "row_count": int(doc.get("row_count") or 0),
                "with_file": int(doc.get("with_file") or 0),
                "without_file": int(doc.get("without_file") or 0),
                "waived": int(doc.get("waived") or 0),
                "files_in_zip": int(doc.get("files_in_zip") or 0),
                "filename": doc.get("filename") or "",
                "kind": str(doc.get("kind") or "evidence"),
            }
        )
    return out
