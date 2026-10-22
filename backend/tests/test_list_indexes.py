"""Phase 3: list indexes and the register_status backfill."""

from __future__ import annotations

from datetime import datetime

import pytest
from bson import ObjectId
from pymongo import AsyncMongoClient

from app.accountant_ops import inbox_base_query
from app.cli.migrate import backfill_register_status, create_indexes
from app.config import get_settings
from app.transaction_ops import DRAFT_OR_PARKED_MATCH, register_day_clause
from tests.test_transactions_api import FakeTransactions, _matches


class _Indexed:
    def __init__(self) -> None:
        self.created: list[tuple[list, dict]] = []

    async def create_index(self, fields, **kwargs) -> None:
        self.created.append((list(fields), kwargs))

    async def create_indexes(self, _indexes) -> None:
        return None

    async def index_information(self) -> dict:
        return {
            kwargs.get("name") or "unnamed": {"key": fields}
            for fields, kwargs in self.created
        }


class _Database:
    def __init__(self) -> None:
        self.transactions = _Indexed()
        self.auth_sessions = _Indexed()
        self.backup_jobs = _Indexed()
        self.backup_schedules = _Indexed()
        self.imported_feeds = _Indexed()
        self.separate_expenses = _Indexed()
        self.operation_events = _Indexed()
        self.collections = {
            "users": _Indexed(),
            "categories": _Indexed(),
            "category_subtables": _Indexed(),
            "description_mappings": _Indexed(),
            "trucks": _Indexed(),
            "trailers": _Indexed(),
            "motor_vehicles": _Indexed(),
            "people": _Indexed(),
            "payees": _Indexed(),
            "system_settings": _Indexed(),
        }

    def __getitem__(self, name: str) -> _Indexed:
        return self.collections.setdefault(name, _Indexed())


@pytest.mark.asyncio
async def test_migrate_creates_the_three_list_indexes() -> None:
    database = _Database()

    await create_indexes(database)
    info = await database.transactions.index_information()

    assert info["inbox_submitted"]["key"] == [("date", -1), ("day_order", 1), ("_id", 1)]
    assert info["register_by_day"]["key"] == [
        ("cashier_id", 1),
        ("date", 1),
        ("day_order", 1),
    ]
    assert info["drafts_by_status"]["key"] == [("register_status", 1), ("date", 1)]
    partials = {kwargs.get("name"): kwargs.get("partialFilterExpression") for _fields, kwargs in database.transactions.created}
    assert partials["inbox_submitted"] == {
        "verified": False,
        "register_status": "submitted",
    }
    assert partials["drafts_by_status"] == {"register_status": {"$in": ["draft", "parked"]}}
    assert partials["register_by_day"] is None


@pytest.mark.asyncio
async def test_missing_register_status_backfills_into_inbox_not_drafts() -> None:
    legacy = {
        "_id": ObjectId(),
        "verified": False,
        "rejected": False,
        "trashed": False,
        "deletion_requested": False,
        "date": datetime(2026, 10, 22, 8, 0, 0),
    }
    draft = {
        "_id": ObjectId(),
        "verified": False,
        "rejected": False,
        "register_status": "draft",
        "date": datetime(2026, 10, 22, 9, 0, 0),
    }
    collection = FakeTransactions([legacy, draft])

    found = await backfill_register_status(type("DB", (), {"transactions": collection})())

    assert found == 1
    assert legacy["register_status"] == "submitted"
    assert draft["register_status"] == "draft"
    assert _matches(legacy, inbox_base_query())
    assert not _matches(legacy, dict(DRAFT_OR_PARKED_MATCH))
    assert _matches(draft, dict(DRAFT_OR_PARKED_MATCH))
    assert not _matches(draft, inbox_base_query())


def _plan_stages(explain: dict) -> list[str]:
    found: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            stage = node.get("stage")
            if isinstance(stage, str):
                found.append(stage)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(explain.get("queryPlanner", {}).get("winningPlan", {}))
    return found


@pytest.mark.asyncio
async def test_explain_inbox_and_by_date_use_index_scans() -> None:
    settings = get_settings()
    client = AsyncMongoClient(
        settings.mongodb_uri,
        serverSelectionTimeoutMS=800,
        tz_aware=True,
    )
    try:
        await client.admin.command("ping")
    except Exception:
        await client.close()
        pytest.skip("MongoDB is not running")

    coll = client[settings.db_name]["_phase3_explain"]
    cashier = ObjectId()
    day = datetime(2026, 10, 22, 10, 0, 0)
    try:
        await coll.drop()
        await coll.create_index(
            [("date", -1), ("day_order", 1), ("_id", 1)],
            name="inbox_submitted",
            partialFilterExpression={
                "verified": False,
                "register_status": "submitted",
            },
        )
        await coll.create_index(
            [("cashier_id", 1), ("date", 1), ("day_order", 1)],
            name="register_by_day",
        )
        await coll.insert_many(
            [
                {
                    "_id": ObjectId(),
                    "verified": False,
                    "register_status": "submitted",
                    "rejected": False,
                    "trashed": False,
                    "deletion_requested": False,
                    "date": day,
                    "day_order": 1,
                    "cashier_id": cashier,
                },
                {
                    "_id": ObjectId(),
                    "verified": False,
                    "register_status": "draft",
                    "date": day,
                    "day_order": 0,
                    "cashier_id": cashier,
                    "import_primary_date": None,
                    "rejected": False,
                    "trashed": False,
                    "deletion_requested": False,
                },
            ]
        )
        inbox_explain = await coll.find(inbox_base_query()).sort(
            [("date", -1), ("day_order", 1), ("_id", 1)]
        ).limit(25).explain()
        by_date = {
            **register_day_clause(day.date()),
            "cashier_id": cashier,
            "rejected": {"$ne": True},
            "deletion_requested": {"$ne": True},
            "trashed": {"$ne": True},
        }
        by_date_explain = await coll.find(by_date).sort(
            [("day_order", 1), ("_id", 1)]
        ).explain()
    finally:
        await coll.drop()
        await client.close()

    inbox_stages = _plan_stages(inbox_explain)
    by_date_stages = _plan_stages(by_date_explain)
    assert "IXSCAN" in inbox_stages
    assert "COLLSCAN" not in inbox_stages
    assert "IXSCAN" in by_date_stages
    assert "COLLSCAN" not in by_date_stages
