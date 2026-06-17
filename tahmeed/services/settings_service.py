from typing import Any
from tahmeed.db.connection import get_db

DEFAULTS = {
    "default_currency": "TZS",
    "confidence_threshold": 75,  # percent — below this goes to review queue
    # When True the cashier register's Item column only accepts existing items
    # (unknown entries prompt to add). When False the column accepts free text.
    "restrict_items": False,
    # When True the cashier register's Truck No. column only accepts numbers that
    # exist in the accountant's truck/trailer registries.
    "restrict_trucks": False,
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
