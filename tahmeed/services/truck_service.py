import re
from typing import List

from pymongo import UpdateOne

from tahmeed.db.connection import get_db


async def search_trucks(prefix: str, limit: int = 10) -> List[str]:
    """Return truck numbers whose prefix matches (case-insensitive)."""
    if not prefix.strip():
        return []
    db = get_db()
    pattern = f"^{re.escape(prefix.strip())}"
    cursor = (
        db.trucks.find(
            {"number": {"$regex": pattern, "$options": "i"}, "active": True},
            {"number": 1, "_id": 0},
        )
        .sort("number", 1)
        .limit(limit)
    )
    results = await cursor.to_list(length=limit)
    return [r["number"] for r in results]


async def add_truck(number: str) -> None:
    db = get_db()
    number = number.strip().upper()
    await db.trucks.update_one(
        {"number": number},
        {"$set": {"number": number, "active": True}},
        upsert=True,
    )


async def bulk_add_trucks(numbers: List[str]) -> int:
    """Upsert a list of truck numbers; returns the count of newly inserted."""
    db = get_db()
    ops = [
        UpdateOne(
            {"number": n.strip().upper()},
            {"$set": {"number": n.strip().upper(), "active": True}},
            upsert=True,
        )
        for n in numbers
        if n.strip()
    ]
    if not ops:
        return 0
    result = await db.trucks.bulk_write(ops)
    return result.upserted_count
