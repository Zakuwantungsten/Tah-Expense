"""One-time / idempotent backfill of truck_sort_key on Mongo collections.

Usage:
    python scripts/backfill_truck_sort_key.py
"""

from __future__ import annotations

import asyncio

from tahmeed.db.connection import get_db
from tahmeed.services.truck_format import stamp_truck_sort_key

_COLLECTIONS = ("transactions", "imported_feeds", "separate_expenses")
_BATCH = 500


async def _backfill_collection(name: str) -> int:
    db = get_db()
    coll = db[name]
    updated = 0
    cursor = coll.find({}, projection={"_id": 1, **{f: 1 for f in (
        "truck_number", "truck_no", "vehicle_reg", "vehicle_no",
        "plate_num", "truck_reg", "reg_no", "truck", "truck_sort_key",
    )}})
    batch: list = []
    async for doc in cursor:
        patch = dict(doc)
        stamp_truck_sort_key(patch)
        key = patch.get("truck_sort_key")
        if doc.get("truck_sort_key") == key:
            continue
        batch.append({"_id": doc["_id"], "truck_sort_key": key})
        if len(batch) >= _BATCH:
            for item in batch:
                await coll.update_one(
                    {"_id": item["_id"]},
                    {"$set": {"truck_sort_key": item["truck_sort_key"]}},
                )
            updated += len(batch)
            batch.clear()
    for item in batch:
        await coll.update_one(
            {"_id": item["_id"]},
            {"$set": {"truck_sort_key": item["truck_sort_key"]}},
        )
    updated += len(batch)
    return updated


async def main() -> None:
    for name in _COLLECTIONS:
        count = await _backfill_collection(name)
        print(f"{name}: updated {count} documents")


if __name__ == "__main__":
    asyncio.run(main())
