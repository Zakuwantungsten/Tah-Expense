from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from pymongo import ASCENDING, DESCENDING, AsyncMongoClient, IndexModel

from ..config import get_settings
from ..serialization import json_safe


@dataclass(frozen=True)
class UniqueKey:
    collection: str
    fields: tuple[str, ...]


UNIQUE_KEYS = (
    UniqueKey("users", ("username",)),
    UniqueKey("categories", ("name",)),
    UniqueKey("category_subtables", ("parent_key", "name")),
    UniqueKey("description_mappings", ("description_key",)),
    UniqueKey("trucks", ("number",)),
    UniqueKey("trailers", ("number",)),
    UniqueKey("motor_vehicles", ("number",)),
    UniqueKey("people", ("name",)),
    UniqueKey("payees", ("name",)),
    UniqueKey("system_settings", ("key",)),
)


async def find_conflicts(db: Any) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    for key in UNIQUE_KEYS:
        group_id = {field: f"${field}" for field in key.fields}
        pipeline = [
            {"$match": {field: {"$exists": True, "$ne": None} for field in key.fields}},
            {"$group": {"_id": group_id, "count": {"$sum": 1}, "ids": {"$push": "$_id"}}},
            {"$match": {"count": {"$gt": 1}}},
        ]
        async for conflict in await db[key.collection].aggregate(pipeline):
            conflicts.append(
                {
                    "collection": key.collection,
                    "fields": key.fields,
                    "values": conflict["_id"],
                    "count": conflict["count"],
                    "ids": conflict["ids"],
                }
            )
    # Register client_row_id idempotency (cashier-scoped).
    client_row_pipeline = [
        {"$match": {"client_row_id": {"$type": "string"}, "cashier_id": {"$exists": True}}},
        {
            "$group": {
                "_id": {
                    "cashier_id": "$cashier_id",
                    "client_row_id": "$client_row_id",
                },
                "count": {"$sum": 1},
                "ids": {"$push": "$_id"},
            }
        },
        {"$match": {"count": {"$gt": 1}}},
    ]
    async for conflict in await db.transactions.aggregate(client_row_pipeline):
        conflicts.append(
            {
                "collection": "transactions",
                "fields": ("cashier_id", "client_row_id"),
                "values": conflict["_id"],
                "count": conflict["count"],
                "ids": conflict["ids"],
            }
        )
    return conflicts


async def create_indexes(db: Any) -> None:
    for key in UNIQUE_KEYS:
        name = "uniq_" + "_".join(key.fields)
        await db[key.collection].create_index(
            [(field, ASCENDING) for field in key.fields],
            name=name,
            unique=True,
            partialFilterExpression={field: {"$type": "string"} for field in key.fields},
        )
    await db.auth_sessions.create_indexes(
        [
            IndexModel([("expires_at", ASCENDING)], expireAfterSeconds=0, name="ttl_expires"),
            IndexModel([("user_id", ASCENDING), ("revoked_at", ASCENDING)], name="user_active"),
        ]
    )
    await db.backup_jobs.create_indexes(
        [
            IndexModel([("created_at", DESCENDING)], name="created_desc"),
            IndexModel([("status", ASCENDING), ("created_at", ASCENDING)], name="status_created"),
            IndexModel([("filename", ASCENDING)], unique=True, name="uniq_filename"),
            IndexModel(
                [("schedule_id", ASCENDING)],
                unique=True,
                sparse=True,
                name="uniq_schedule_id",
            ),
        ]
    )
    await db.backup_schedules.create_indexes(
        [
            IndexModel([("status", ASCENDING), ("due_at", DESCENDING)], name="status_due"),
            IndexModel([("completed_at", DESCENDING)], name="completed_desc"),
        ]
    )
    await db.transactions.create_index(
        [("verified", ASCENDING), ("rejected", ASCENDING)],
        name="pending_verification",
    )
    # Idempotent import keys (partial unique — ignore docs without the fields).
    await db.transactions.create_index(
        [("daily_import_id", ASCENDING), ("import_row_key", ASCENDING)],
        name="uniq_daily_import_row",
        unique=True,
        partialFilterExpression={
            "daily_import_id": {"$type": "string"},
            "import_row_key": {"$type": "string"},
        },
    )
    await db.transactions.create_index(
        [("master_import_source", ASCENDING), ("master_serial", ASCENDING)],
        name="uniq_master_import_serial",
        unique=True,
        partialFilterExpression={
            "master_import_source": {"$type": "string"},
            "master_serial": {"$exists": True},
        },
    )
    # Idempotent register creates (Phase 2 — sparse on client_row_id).
    await db.transactions.create_index(
        [("cashier_id", ASCENDING), ("client_row_id", ASCENDING)],
        name="uniq_cashier_client_row",
        unique=True,
        partialFilterExpression={
            "client_row_id": {"$type": "string"},
            "cashier_id": {"$exists": True},
        },
    )
    # List indexes. Partial filters are equality only so the planner can use them.
    # register_status is backfilled before these are built (see migrate()).
    await db.transactions.create_index(
        [("date", DESCENDING), ("day_order", ASCENDING), ("_id", ASCENDING)],
        name="inbox_submitted",
        partialFilterExpression={
            "verified": False,
            "register_status": "submitted",
        },
    )
    await db.transactions.create_index(
        [("cashier_id", ASCENDING), ("date", ASCENDING), ("day_order", ASCENDING)],
        name="register_by_day",
    )
    await db.transactions.create_index(
        [("register_status", ASCENDING), ("date", ASCENDING)],
        name="drafts_by_status",
        partialFilterExpression={"register_status": {"$in": ["draft", "parked"]}},
    )
    await db.imported_feeds.create_index(
        [("skipped_row_id", ASCENDING)],
        name="uniq_feed_skipped_row_id",
        unique=True,
        sparse=True,
    )
    await db.separate_expenses.create_index(
        [("skipped_row_id", ASCENDING)],
        name="uniq_sep_skipped_row_id",
        unique=True,
        sparse=True,
    )
    await db.operation_events.create_indexes(
        [
            IndexModel([("ts", DESCENDING)], name="ts_desc"),
            IndexModel(
                [("actor_id", ASCENDING), ("ts", DESCENDING)],
                name="actor_ts",
            ),
            IndexModel(
                [("action", ASCENDING), ("ts", DESCENDING)],
                name="action_ts",
            ),
            IndexModel([("entity_ids", ASCENDING)], name="entity_ids"),
        ]
    )


async def backfill_register_status(db: Any) -> int:
    """Legacy rows with no register_status were treated as the verify inbox.

    Drafts and parked rows already store that status. Missing means submitted,
    so the row lands in the inbox and not in the drafts list.
    """
    missing = {"register_status": {"$exists": False}}
    found = int(await db.transactions.count_documents(missing))
    if found:
        await db.transactions.update_many(
            missing,
            {"$set": {"register_status": "submitted"}},
        )
    print(f"Transactions missing register_status: {found}. Backfilled to submitted.")
    return found


# Display string → BSON datetime targets for imported feed / separate expense rows.
_FEED_DATE_BACKFILLS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("toll_plaza", ("toll_date",), "transaction_date"),
    ("parking_congo", ("payment_date",), "transaction_date"),
    ("parking_congo", ("gate_in",), "gate_in_dt"),
    ("rahntech", ("sales_date",), "transaction_date"),
    ("zambia_parking", ("date", "transaction_date"), "transaction_date"),
    ("comesa", ("valid_from",), "valid_from_dt"),
    ("comesa", ("valid_to",), "valid_to_dt"),
    ("diesel_infinity", ("date",), "transaction_date"),
    ("diesel_lake_zambia", ("date",), "transaction_date"),
    ("diesel_lake_tunduma", ("date",), "transaction_date"),
    ("diesel_gbp", ("date",), "transaction_date"),
)

_SEPARATE_DATE_BACKFILLS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("ahmed_kimvi", ("date_str", "expense_date"), "expense_date"),
    ("congo_expenses", ("date_str", "expense_date"), "expense_date"),
)


async def backfill_feed_datetimes(db: Any) -> int:
    """Parse display date strings into BSON datetimes used for sort/filter.

    Rows imported before ``HH:MM`` (no seconds) parsing worked often have empty
    ``transaction_date`` / ``expense_date``. Sorting then falls through to the
    display string and groups by day-of-month (all 3rds, then all 4ths, …).
    """
    from ..excel_dates import normalize_date_fields

    updated = 0
    feeds = db.imported_feeds
    for feed_type, sources, store_as in _FEED_DATE_BACKFILLS:
        query = {
            "feed_type": feed_type,
            "$or": [
                {store_as: {"$exists": False}},
                {store_as: None},
                {store_as: {"$type": "string"}},
            ],
        }
        cursor = feeds.find(query, projection={src: 1 for src in sources} | {"_id": 1})
        async for doc in cursor:
            patch = {src: doc.get(src) for src in sources if src in doc}
            if not patch:
                continue
            # gate_in keeps its DD/MM/YYYY display text.
            rewrite = store_as != "gate_in_dt"
            display_fmt = (
                "%d %b %y"
                if store_as in {"valid_from_dt", "valid_to_dt"}
                else "%d %b %Y"
            )
            parsed = normalize_date_fields(
                patch,
                *sources,
                store_as=store_as,
                display_fmt=display_fmt,
                rewrite_display=rewrite,
            )
            if not isinstance(parsed, datetime):
                continue
            set_doc = {store_as: parsed}
            if rewrite:
                for src in sources:
                    if src in patch and isinstance(patch.get(src), str):
                        set_doc[src] = patch[src]
            await feeds.update_one({"_id": doc["_id"]}, {"$set": set_doc})
            updated += 1

    separate = db.separate_expenses
    for expense_type, sources, store_as in _SEPARATE_DATE_BACKFILLS:
        query = {
            "expense_type": expense_type,
            "$or": [
                {store_as: {"$exists": False}},
                {store_as: None},
                {store_as: {"$type": "string"}},
            ],
        }
        cursor = separate.find(
            query, projection={src: 1 for src in sources} | {"_id": 1}
        )
        async for doc in cursor:
            patch = {src: doc.get(src) for src in sources if src in doc}
            if not patch:
                continue
            parsed = normalize_date_fields(
                patch, *sources, store_as=store_as, display_fmt="%d %b %Y"
            )
            if not isinstance(parsed, datetime):
                continue
            set_doc = {store_as: parsed}
            if isinstance(patch.get("date_str"), str):
                set_doc["date_str"] = patch["date_str"]
            await separate.update_one({"_id": doc["_id"]}, {"$set": set_doc})
            updated += 1

    print(f"Feed/separate expense datetime backfill updated {updated} row(s).")
    await db.schema_migrations.update_one(
        {"_id": "0005_feed_datetime_backfill"},
        {
            "$set": {
                "description": (
                    "Backfill transaction_date/expense_date/gate_in_dt/"
                    "valid_*_dt from display strings for chronological sort"
                ),
                "applied_at": datetime.now(timezone.utc),
                "updated_rows": updated,
            }
        },
        upsert=True,
    )
    return updated


async def migrate(check_only: bool) -> int:
    settings = get_settings()
    client = AsyncMongoClient(settings.mongodb_uri, appname="tahmeed-migrate")
    try:
        db = client[settings.db_name]
        await db.command("ping")
        conflicts = await find_conflicts(db)
        print(json.dumps({"conflicts": json_safe(conflicts)}, indent=2))
        if conflicts:
            print("Indexes were not changed. Resolve all conflicts and run again.")
            return 2
        if check_only:
            print("No conflicts found; check-only mode made no changes.")
            return 0
        await backfill_register_status(db)
        await backfill_feed_datetimes(db)
        await create_indexes(db)
        from ..badge_counters import repair_badge_counters

        await repair_badge_counters(db)
        await db.schema_migrations.update_one(
            {"_id": "0003_operation_events_indexes"},
            {
                "$set": {
                    "description": "Indexes for append-only operation_events audit trail",
                    "applied_at": datetime.now(timezone.utc),
                }
            },
            upsert=True,
        )
        await db.schema_migrations.update_one(
            {"_id": "0004_client_row_id_idempotency"},
            {
                "$set": {
                    "description": (
                        "Unique (cashier_id, client_row_id) for idempotent "
                        "register creates / timeout retries"
                    ),
                    "applied_at": datetime.now(timezone.utc),
                }
            },
            upsert=True,
        )
        await db.schema_migrations.update_one(
            {"_id": "0002_import_idempotency_indexes"},
            {
                "$set": {
                    "description": (
                        "Unique indexes for daily/master import idempotency "
                        "and skipped-row reupload"
                    ),
                    "applied_at": datetime.now(timezone.utc),
                }
            },
            upsert=True,
        )
        await db.schema_migrations.update_one(
            {"_id": "0001_api_foundation_indexes"},
            {
                "$set": {
                    "description": "API foundation unique and query indexes",
                    "applied_at": datetime.now(timezone.utc),
                }
            },
            upsert=True,
        )
        print("Indexes are current.")
        return 0
    finally:
        await client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate data and create API indexes.")
    parser.add_argument("--check", action="store_true", help="report only; create no indexes")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(migrate(args.check)))


if __name__ == "__main__":
    main()
