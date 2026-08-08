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
        await create_indexes(db)
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
