import re
from typing import List, Dict

from pymongo import UpdateOne

from tahmeed.db.connection import get_db


# ── Trucks ─────────────────────────────────────────────────────────────────────

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


async def search_fleet(prefix: str, limit: int = 10) -> List[str]:
    """Return truck *and* trailer numbers whose prefix matches (case-insensitive).

    Used by the cashier register's Truck No. column so both trucks and trailers
    from the accountant's registries appear as suggestions.
    """
    if not prefix.strip():
        return []
    db = get_db()
    pattern = f"^{re.escape(prefix.strip())}"
    query = {"number": {"$regex": pattern, "$options": "i"}, "active": True}
    proj = {"number": 1, "_id": 0}
    trucks = await db.trucks.find(query, proj).sort("number", 1).limit(limit).to_list(length=limit)
    trailers = await db.trailers.find(query, proj).sort("number", 1).limit(limit).to_list(length=limit)
    seen: set = set()
    out: List[str] = []
    for r in trucks + trailers:
        n = r["number"]
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out[:limit]


async def get_fleet_numbers(active_only: bool = True) -> set:
    """Return the set of all valid truck + trailer numbers (uppercased).

    Used to validate the cashier's Truck No. entries when the restrict-trucks
    setting is on.
    """
    db = get_db()
    query = {"active": True} if active_only else {}
    proj = {"number": 1, "_id": 0}
    trucks = await db.trucks.find(query, proj).to_list(length=None)
    trailers = await db.trailers.find(query, proj).to_list(length=None)
    return {r["number"].upper() for r in (trucks + trailers) if r.get("number")}


async def get_all_trucks(search: str = "", active_only: bool = False) -> List[Dict]:
    db = get_db()
    query: dict = {}
    if active_only:
        query["active"] = True
    if search.strip():
        query["number"] = {"$regex": re.escape(search.strip()), "$options": "i"}
    cursor = db.trucks.find(query, {"_id": 0, "number": 1, "active": 1}).sort("number", 1)
    return await cursor.to_list(length=None)


async def add_truck(number: str) -> None:
    db = get_db()
    number = number.strip().upper()
    await db.trucks.update_one(
        {"number": number},
        {"$set": {"number": number, "active": True}},
        upsert=True,
    )


async def remove_truck(number: str) -> None:
    db = get_db()
    await db.trucks.delete_one({"number": number.strip().upper()})


async def set_truck_active(number: str, active: bool) -> None:
    db = get_db()
    await db.trucks.update_one(
        {"number": number.strip().upper()},
        {"$set": {"active": active}},
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


# ── Trailers ───────────────────────────────────────────────────────────────────

async def get_all_trailers(search: str = "", active_only: bool = False) -> List[Dict]:
    db = get_db()
    query: dict = {}
    if active_only:
        query["active"] = True
    if search.strip():
        query["number"] = {"$regex": re.escape(search.strip()), "$options": "i"}
    cursor = db.trailers.find(query, {"_id": 0, "number": 1, "active": 1}).sort("number", 1)
    return await cursor.to_list(length=None)


async def add_trailer(number: str) -> None:
    db = get_db()
    number = number.strip().upper()
    await db.trailers.update_one(
        {"number": number},
        {"$set": {"number": number, "active": True}},
        upsert=True,
    )


async def remove_trailer(number: str) -> None:
    db = get_db()
    await db.trailers.delete_one({"number": number.strip().upper()})


async def set_trailer_active(number: str, active: bool) -> None:
    db = get_db()
    await db.trailers.update_one(
        {"number": number.strip().upper()},
        {"$set": {"active": active}},
    )


async def bulk_add_trailers(numbers: List[str]) -> int:
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
    result = await db.trailers.bulk_write(ops)
    return result.upserted_count
