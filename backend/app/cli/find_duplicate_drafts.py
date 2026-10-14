"""Find (and optionally soft-trash) inflated register draft duplicates.

Groups draft rows by cashier + business day + content fingerprint. Extra
copies beyond the oldest keep-one are reported. With ``--apply --yes``, extras
are soft-trashed (``trashed=True``) so they leave register totals — drafts only,
never verified/submitted rows.

Usage (from backend/):

  python -m app.cli.find_duplicate_drafts --day 2026-08-08
  python -m app.cli.find_duplicate_drafts --day 2026-08-08 --cashier-id <oid>
  python -m app.cli.find_duplicate_drafts --day 2026-08-08 --apply --yes
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import defaultdict
from datetime import date, datetime, time, timezone
from typing import Any

from bson import ObjectId
from pymongo import AsyncMongoClient

from ..config import get_settings
from ..serialization import json_safe


def _money_key(value: Any) -> str:
    try:
        return f"{float(value or 0.0):.2f}"
    except (TypeError, ValueError):
        return "0.00"


def _money_parts(doc: dict[str, Any]) -> tuple[float, float]:
    cur = str(doc.get("currency") or "TZS").upper()
    amt = float(doc.get("amount") or 0.0)
    amount_usd = doc.get("amount_usd")
    if amount_usd is not None:
        usd = float(amount_usd or 0.0)
        if cur == "USD":
            if abs(amt - usd) < 1e-9:
                return 0.0, usd
            return amt, usd
        return amt, usd
    if cur == "USD":
        return 0.0, amt
    return amt, 0.0


def _row_date_iso(doc: dict[str, Any]) -> str:
    raw = doc.get("date")
    if isinstance(raw, datetime):
        return raw.date().isoformat()
    if isinstance(raw, date):
        return raw.isoformat()
    return ""


def content_fingerprint(doc: dict[str, Any]) -> tuple[str, str, str, str, str, str]:
    tzs, usd = _money_parts(doc)
    return (
        str(doc.get("description") or "").strip().upper(),
        str(doc.get("truck_number") or "").strip().upper(),
        str(doc.get("item") or "").strip().upper(),
        _money_key(tzs),
        _money_key(usd),
        _row_date_iso(doc),
    )


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min)
    end = datetime.combine(day, time.max)
    return start, end


def _sort_key(doc: dict[str, Any]) -> tuple:
    created = doc.get("created_at") or doc.get("date") or datetime.min
    if isinstance(created, datetime) and created.tzinfo is not None:
        created = created.replace(tzinfo=None)
    return (created, str(doc.get("_id")))


async def find_groups(
    db: Any,
    *,
    day: date | None,
    cashier_id: ObjectId | None,
    limit_days: int,
) -> list[dict[str, Any]]:
    match: dict[str, Any] = {
        "register_status": "draft",
        "trashed": {"$ne": True},
        "discarded": {"$ne": True},
        "verified": {"$ne": True},
    }
    if cashier_id is not None:
        match["cashier_id"] = cashier_id
    if day is not None:
        start, end = _day_bounds(day)
        match["date"] = {"$gte": start, "$lte": end}
    else:
        # Default: look back *limit_days* calendar days from UTC today.
        today = datetime.now(timezone.utc).date()
        start, _ = _day_bounds(date.fromordinal(today.toordinal() - max(0, limit_days - 1)))
        match["date"] = {"$gte": start}

    cursor = db.transactions.find(match)
    by_key: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    async for doc in cursor:
        fp = content_fingerprint(doc)
        if not fp[0]:
            continue
        cashier = doc.get("cashier_id")
        key = (str(cashier) if cashier is not None else "", fp)
        by_key[key].append(doc)

    groups: list[dict[str, Any]] = []
    for (cashier_key, fp), docs in by_key.items():
        if len(docs) < 2:
            continue
        ordered = sorted(docs, key=_sort_key)
        keep = ordered[0]
        extras = ordered[1:]
        groups.append(
            {
                "cashier_id": cashier_key,
                "fingerprint": {
                    "description": fp[0],
                    "truck_number": fp[1],
                    "item": fp[2],
                    "tzs": fp[3],
                    "usd": fp[4],
                    "row_date": fp[5],
                },
                "count": len(ordered),
                "keep_id": str(keep["_id"]),
                "extra_ids": [str(d["_id"]) for d in extras],
                "client_row_ids": [
                    d.get("client_row_id") for d in ordered if d.get("client_row_id")
                ],
            }
        )
    groups.sort(key=lambda g: (-g["count"], g["cashier_id"], g["keep_id"]))
    return groups


async def apply_soft_trash(db: Any, groups: list[dict[str, Any]]) -> int:
    """Soft-trash extras; return number of documents modified."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    ids: list[ObjectId] = []
    for group in groups:
        for raw in group["extra_ids"]:
            ids.append(ObjectId(raw))
    if not ids:
        return 0
    result = await db.transactions.update_many(
        {
            "_id": {"$in": ids},
            "register_status": "draft",
            "verified": {"$ne": True},
            "trashed": {"$ne": True},
        },
        {
            "$set": {
                "trashed": True,
                "trashed_at": now,
                "trashed_by": None,
                "dedupe_repair": True,
                "dedupe_repair_at": now,
            }
        },
    )
    return int(result.modified_count)


async def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    client = AsyncMongoClient(settings.mongodb_uri, appname="tahmeed-find-dup-drafts")
    try:
        db = client[settings.db_name]
        await db.command("ping")
        cashier_id = ObjectId(args.cashier_id) if args.cashier_id else None
        day = date.fromisoformat(args.day) if args.day else None
        groups = await find_groups(
            db,
            day=day,
            cashier_id=cashier_id,
            limit_days=args.days,
        )
        report = {
            "groups": len(groups),
            "extra_rows": sum(len(g["extra_ids"]) for g in groups),
            "items": json_safe(groups),
        }
        print(json.dumps(report, indent=2, default=str))
        if not groups:
            print("No duplicate draft groups found.")
            return 0
        if not args.apply:
            print(
                f"Found {report['groups']} group(s), {report['extra_rows']} extra "
                "row(s). Re-run with --apply --yes to soft-trash extras "
                "(drafts only; keeps oldest)."
            )
            return 0
        if not args.yes:
            print("Refusing --apply without --yes (accountant-approved repair).")
            return 2
        modified = await apply_soft_trash(db, groups)
        print(f"Soft-trashed {modified} extra draft row(s).")
        return 0
    finally:
        await client.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Find draft register rows that share the same cashier/day content "
            "fingerprint (possible inflation from pre-idempotency retries)."
        )
    )
    parser.add_argument(
        "--day",
        help="Business day YYYY-MM-DD (filters on transaction date)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="When --day is omitted, look back this many days (default 30)",
    )
    parser.add_argument(
        "--cashier-id",
        help="Limit to one cashier ObjectId hex",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Soft-trash extras (keep oldest); requires --yes",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Confirm destructive apply",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
