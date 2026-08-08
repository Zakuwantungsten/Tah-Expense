"""Local Daily Register draft outbox — crash / power-loss recovery.

Drafts live under ``%LOCALAPPDATA%\\Tahmeed Expense\\register_drafts\\`` and are
keyed by ``(user_id, register_date, merged)``. They store raw cell text so
incomplete rows survive without Mongo validation.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from bson import ObjectId

from tahmeed.config import APP_NAME

SCHEMA_VERSION = 1


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
