"""One cached catalog document for the register.

The JSON lives on this process. ``catalog_version`` lives in ``system_counters``
and increases when a catalog write commits, so a matching ``If-None-Match``
can return 304 without reading items, payees, or fleet.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from pymongo import ReturnDocument

CATALOG_ID = "catalog"

# Settings the cashier register used to read one key at a time.
REGISTER_SETTING_KEYS = (
    "restrict_items",
    "restrict_payees",
    "restrict_trucks",
    "defer_item_to_verify",
    "auto_fill_from_description_maps",
    "duplicate_check_days",
    "export_restrict_surfaces",
    "allowed_truck_labels",
)


def _cache(app: Any) -> dict[str, Any]:
    slot = getattr(app.state, "catalog_cache", None)
    if not isinstance(slot, dict):
        slot = {"version": None, "body": None}
        app.state.catalog_cache = slot
    return slot


def clear_catalog_cache(app: Any) -> None:
    slot = _cache(app)
    slot["version"] = None
    slot["body"] = None


def etag_for(version: int) -> str:
    return f'"{int(version)}"'


def etag_matches(header: str | None, version: int) -> bool:
    if not header:
        return False
    token = header.split(",", 1)[0].strip()
    if token.startswith("W/"):
        token = token[2:].strip()
    if len(token) >= 2 and token[0] == '"' and token[-1] == '"':
        token = token[1:-1]
    return token == str(int(version))


async def current_catalog_version(db: Any) -> int:
    counters = getattr(db, "system_counters", None)
    find_one = getattr(counters, "find_one", None)
    if find_one is None:
        return 0
    doc = await find_one({"_id": CATALOG_ID})
    if not doc:
        return 0
    try:
        return int(doc.get("catalog_version") or 0)
    except (TypeError, ValueError):
        return 0


async def bump_catalog_version(request: Request) -> None:
    """Drop the process cache and advance the stored version after a catalog write."""
    clear_catalog_cache(request.app)
    db = request.app.state.database.db
    counters = getattr(db, "system_counters", None)
    update = getattr(counters, "find_one_and_update", None)
    if update is None:
        return
    try:
        await update(
            {"_id": CATALOG_ID},
            {"$inc": {"catalog_version": 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    except Exception:
        return


async def _active_docs(collection: Any, query: dict, sort: list[tuple[str, int]]) -> list[dict]:
    cursor = collection.find(query)
    cursor = cursor.sort(sort)
    docs = await cursor.to_list(length=None)
    return list(docs or [])


async def build_catalog_snapshot(db: Any, version: int) -> dict[str, Any]:
    from .api import merged_settings
    from .serialization import json_safe

    categories = await _active_docs(
        db.categories,
        {"active": {"$ne": False}},
        [("name", 1), ("_id", 1)],
    )
    payees = await _active_docs(
        db.payees,
        {"active": True},
        [("name", 1), ("_id", 1)],
    )
    trucks = await _active_docs(
        db.trucks,
        {"active": True},
        [("number", 1), ("_id", 1)],
    )
    trailers = await _active_docs(
        db.trailers,
        {"active": True},
        [("number", 1), ("_id", 1)],
    )
    motor_vehicles = await _active_docs(
        db.motor_vehicles,
        {"active": True},
        [("number", 1), ("_id", 1)],
    )
    stored = await merged_settings(db)
    settings = {key: stored.get(key) for key in REGISTER_SETTING_KEYS}
    return {
        "catalog_version": int(version),
        "categories": json_safe(categories),
        "payees": json_safe(payees),
        "trucks": json_safe(trucks),
        "trailers": json_safe(trailers),
        "motor_vehicles": json_safe(motor_vehicles),
        "allowed_truck_labels": settings.get("allowed_truck_labels") or [],
        "settings": json_safe(settings),
    }


async def catalog_snapshot_body(request: Request) -> tuple[int, dict[str, Any] | None, bool]:
    """Return ``(version, body, not_modified)``.

    A matching ``If-None-Match`` is ``not_modified`` and has no body.
    """
    db = request.app.state.database.db
    version = await current_catalog_version(db)
    if etag_matches(request.headers.get("if-none-match"), version):
        return version, None, True
    slot = _cache(request.app)
    if slot.get("version") == version and isinstance(slot.get("body"), dict):
        return version, slot["body"], False
    body = await build_catalog_snapshot(db, version)
    slot["version"] = version
    slot["body"] = body
    return version, body, False
