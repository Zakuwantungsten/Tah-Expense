"""Local Daily Register draft outbox — crash / power-loss recovery.

Drafts live under ``%LOCALAPPDATA%\\meedexpe\\register_drafts\\`` and are
keyed by ``(user_id, register_date, merged)``. They store raw cell text so
incomplete rows survive without Mongo validation.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from bson import ObjectId

from tahmeed.config import APP_NAME

SCHEMA_VERSION = 3

# Per-row outbox sync state (stored in pending_meta / draft new_rows).
SYNC_PENDING = "PENDING"
SYNC_IN_FLIGHT = "IN_FLIGHT"
SYNC_FAILED = "FAILED"
# SYNCED is implied by removal from the draft / editable zone after ACK.


def new_client_row_id() -> str:
    """UUID for a register row that has not yet been acknowledged by the server."""
    return str(uuid.uuid4())


def normalize_sync_status(raw: Any) -> str:
    """Map stored status for drain. Unknown / mid-flight → PENDING (safe retry)."""
    status = str(raw or "").strip().upper()
    if status == SYNC_FAILED:
        return SYNC_FAILED
    if status == SYNC_IN_FLIGHT:
        # Crash / timeout mid-send: treat as pending so reconnect can retry.
        return SYNC_PENDING
    return SYNC_PENDING


def entry_sync_status(entry: dict | None) -> str:
    if not isinstance(entry, dict):
        return SYNC_PENDING
    meta = entry.get("pending_meta")
    if isinstance(meta, dict) and meta.get("sync_status"):
        return normalize_sync_status(meta.get("sync_status"))
    return normalize_sync_status(entry.get("sync_status"))


def entry_client_row_id(entry: dict | None) -> str:
    """Read ``client_row_id`` from a draft ``new_rows`` entry (top-level or meta)."""
    if not isinstance(entry, dict):
        return ""
    rid = entry.get("client_row_id")
    if rid:
        return str(rid).strip()
    meta = entry.get("pending_meta")
    if isinstance(meta, dict) and meta.get("client_row_id"):
        return str(meta["client_row_id"]).strip()
    return ""


def ensure_draft_client_row_ids(payload: dict | None) -> dict | None:
    """Stamp missing ``client_row_id`` + normalize sync_status on draft new_rows."""
    if not isinstance(payload, dict):
        return payload
    new_rows = list(payload.get("new_rows") or [])
    changed = False
    for entry in new_rows:
        if not isinstance(entry, dict):
            continue
        rid = entry_client_row_id(entry)
        if not rid:
            rid = new_client_row_id()
            changed = True
        if entry.get("client_row_id") != rid:
            entry["client_row_id"] = rid
            changed = True
        meta = entry.get("pending_meta")
        if not isinstance(meta, dict):
            meta = {}
            entry["pending_meta"] = meta
            changed = True
        else:
            meta = dict(meta)
            entry["pending_meta"] = meta
        if meta.get("client_row_id") != rid:
            meta["client_row_id"] = rid
            changed = True
        raw_sync = meta.get("sync_status") or entry.get("sync_status")
        if raw_sync:
            normalized = normalize_sync_status(raw_sync)
            if meta.get("sync_status") != normalized:
                meta["sync_status"] = normalized
                changed = True
            if entry.get("sync_status") != normalized:
                entry["sync_status"] = normalized
                changed = True
        else:
            # Leave ordinary unsaved rows without sync_status (manual Save).
            meta.pop("sync_status", None)
            entry.pop("sync_status", None)
    if changed or int(payload.get("schema_version") or 1) < SCHEMA_VERSION:
        payload["new_rows"] = new_rows
        payload["schema_version"] = SCHEMA_VERSION
    return payload


def drafts_root() -> Path:
    base = os.getenv("LOCALAPPDATA")
    if not base:
        base = str(Path.home() / "AppData" / "Local")
    return Path(base) / APP_NAME / "register_drafts"


def draft_path(user_id: ObjectId | str, register_date: date, *, merged: bool) -> Path:
    uid = str(user_id)
    mode = "merged" if merged else "my"
    return drafts_root() / f"{uid}_{register_date.isoformat()}_{mode}.json"


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def serialize_pending_meta(meta: Optional[dict]) -> Optional[dict]:
    if not meta:
        return None
    out: dict[str, Any] = {}
    for key, value in meta.items():
        if isinstance(value, ObjectId):
            out[key] = str(value)
        elif isinstance(value, datetime):
            out[key] = value.isoformat()
        elif isinstance(value, date):
            out[key] = value.isoformat()
        else:
            out[key] = value
    return out


def hydrate_pending_meta(meta: Optional[dict]) -> Optional[dict]:
    if not meta:
        return None
    out = dict(meta)
    cid = out.get("category_id")
    if isinstance(cid, str) and cid:
        try:
            out["category_id"] = ObjectId(cid)
        except Exception:
            out["category_id"] = None
    ipd = out.get("import_primary_date")
    if isinstance(ipd, str) and ipd:
        try:
            out["import_primary_date"] = datetime.fromisoformat(ipd)
        except ValueError:
            out["import_primary_date"] = None
    return out


def cells_for_json(values: dict[int, str]) -> dict[str, str]:
    return {str(int(col)): str(text) for col, text in values.items()}


def cells_from_json(raw: dict | None) -> dict[int, str]:
    if not isinstance(raw, dict):
        return {}
    out: dict[int, str] = {}
    for key, value in raw.items():
        try:
            out[int(key)] = "" if value is None else str(value)
        except (TypeError, ValueError):
            continue
    return out


def _money_key(value: float | int | None) -> str:
    try:
        return f"{float(value or 0.0):.2f}"
    except (TypeError, ValueError):
        return "0.00"


def expense_content_fingerprint(
    *,
    description: str,
    truck_number: str = "",
    item: str = "",
    tzs: float = 0.0,
    usd: float = 0.0,
    row_date_iso: str = "",
) -> tuple[str, str, str, str, str, str]:
    """Stable content key for Phase-0 restore / footer dedupe (pre-client UUID).

    Requires a non-empty description to be considered meaningful for matching.
    """
    return (
        (description or "").strip().upper(),
        (truck_number or "").strip().upper(),
        (item or "").strip().upper(),
        _money_key(tzs),
        _money_key(usd),
        (row_date_iso or "").strip(),
    )


def fingerprint_is_meaningful(fp: tuple[str, str, str, str, str, str] | None) -> bool:
    """True when the fingerprint has enough content to safely match."""
    return bool(fp and fp[0])


def claim_fingerprint(
    available: dict[tuple[str, str, str, str, str, str], int],
    fp: tuple[str, str, str, str, str, str] | None,
) -> bool:
    """Consume one occurrence of *fp* from a multiset. Return True if claimed."""
    if not fingerprint_is_meaningful(fp):
        return False
    left = available.get(fp, 0)
    if left <= 0:
        return False
    if left == 1:
        available.pop(fp, None)
    else:
        available[fp] = left - 1
    return True


def build_draft_payload(
    *,
    user_id: ObjectId | str,
    username: str,
    register_date: date,
    merged: bool,
    edit_mode: bool,
    dirty_saved: list[dict],
    new_rows: list[dict],
) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "user_id": str(user_id),
        "username": username or "",
        "register_date": register_date.isoformat(),
        "merged": bool(merged),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "edit_mode": bool(edit_mode),
        "dirty_saved": dirty_saved,
        "new_rows": new_rows,
    }


def draft_is_empty(payload: dict | None) -> bool:
    if not payload:
        return True
    return not payload.get("dirty_saved") and not payload.get("new_rows")


def apply_mapping_to_draft_payload(
    draft: dict,
    description_keys: set[str],
    *,
    payee: str,
    item_name: str,
    category_id: ObjectId | str | None = None,
    clear_category: bool = False,
    desc_col: int = 3,
    item_col: int = 2,
    payee_col: int = 12,
    normalize_description=None,
) -> int:
    """Stamp Payee + Item onto matching draft new/dirty rows. Mutates *draft*.

    Pass ``clear_category=True`` (Day Issues Undo) to drop ``category_id`` from
    pending meta while writing blank Item / Payee.
    """
    if not draft or not description_keys:
        return 0
    if normalize_description is None:
        from tahmeed.services.description_mapping_service import (
            normalize_description as _norm,
        )
        normalize_description = _norm

    updated = 0
    new_rows = list(draft.get("new_rows") or [])
    for entry in new_rows:
        cells = cells_from_json(entry.get("cells"))
        key = normalize_description((cells.get(desc_col) or "").strip())
        if not key or key not in description_keys:
            continue
        cells[item_col] = item_name
        cells[payee_col] = payee
        entry["cells"] = cells_for_json(cells)
        meta = hydrate_pending_meta(entry.get("pending_meta")) or {}
        if clear_category:
            meta.pop("category_id", None)
        elif category_id is not None:
            meta["category_id"] = category_id
        entry["pending_meta"] = serialize_pending_meta(meta)
        updated += 1

    dirty = list(draft.get("dirty_saved") or [])
    for entry in dirty:
        cells = cells_from_json(entry.get("cells"))
        key = normalize_description((cells.get(desc_col) or "").strip())
        if not key or key not in description_keys:
            continue
        cells[item_col] = item_name
        cells[payee_col] = payee
        entry["cells"] = cells_for_json(cells)
        updated += 1

    if updated:
        draft["new_rows"] = new_rows
        draft["dirty_saved"] = dirty
    return updated


def save_register_draft(payload: dict) -> Path:
    """Write or replace the draft file. Empty drafts delete the file."""
    user_id = payload["user_id"]
    register_date = date.fromisoformat(payload["register_date"])
    merged = bool(payload.get("merged"))
    path = draft_path(user_id, register_date, merged=merged)
    if draft_is_empty(payload):
        clear_register_draft(user_id, register_date, merged=merged)
        return path
    _atomic_json(path, payload)
    return path


def load_register_draft(
    user_id: ObjectId | str,
    register_date: date,
    *,
    merged: bool,
) -> Optional[dict]:
    path = draft_path(user_id, register_date, merged=merged)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    if draft_is_empty(raw):
        return None
    ensure_draft_client_row_ids(raw)
    return raw


def clear_register_draft(
    user_id: ObjectId | str,
    register_date: date,
    *,
    merged: bool,
) -> None:
    path = draft_path(user_id, register_date, merged=merged)
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def clear_register_drafts_for_date(
    user_id: ObjectId | str,
    register_date: date,
) -> None:
    """Remove My and Merged local recovery files for one register day."""
    clear_register_draft(user_id, register_date, merged=False)
    clear_register_draft(user_id, register_date, merged=True)
