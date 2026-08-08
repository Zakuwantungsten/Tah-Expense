from typing import Any
from tahmeed.db.connection import get_db

DEFAULTS = {
    "default_currency": "TZS",
    "confidence_threshold": 75,  # percent — below this goes to review queue
    # When True the cashier register's Item column flags unknown entries
    # (text is kept; save still requires a known item). When False free text is allowed.
    "restrict_items": False,
    # When True cashiers may save table rows with description only (no item).
    # The accountant assigns items on verify; mappings are remembered.
    "defer_item_to_verify": False,
    # When True the cashier register's Truck No. column only accepts numbers that
    # exist in the accountant's fleet registries (trucks, trailers, motor vehicles).
    # Default On — always intended.
    "restrict_trucks": True,
    # How many days back to scan when checking for duplicate transactions.
    "duplicate_check_days": 5,
    # Free-text Truck No. place labels (YARD, GARAGE, …) accepted without fleet match.
    "allowed_truck_labels": ["YARD", "GARAGE"],
}


async def get_setting(key: str) -> Any:
    db = get_db()
    doc = await db.system_settings.find_one({"key": key})
    return doc["value"] if doc else DEFAULTS.get(key)


async def set_setting(key: str, value: Any) -> None:
    db = get_db()
    await db.system_settings.update_one(
        {"key": key},
        {"$set": {"key": key, "value": value}},
        upsert=True,
    )


async def get_all_settings() -> dict:
    db = get_db()
    cursor = db.system_settings.find({})
    docs = await cursor.to_list(length=None)
    result = dict(DEFAULTS)
    result.update({d["key"]: d["value"] for d in docs})
    return result
