from datetime import datetime, timedelta, timezone

import pytest
from pymongo.errors import DuplicateKeyError

from app.cli.scheduler import (
    ScheduleSlot,
    claim_slot,
    daily_slot,
    due_slots,
    interval_slot,
    weekly_slot,
)
from app.config import Settings

UTC = timezone.utc


class FakeUpdateResult:
    def __init__(self, modified_count: int) -> None:
        self.modified_count = modified_count


class FakeScheduleCollection:
    def __init__(self) -> None:
        self.documents: dict[str, dict] = {}

    async def insert_one(self, document: dict) -> None:
        if document["_id"] in self.documents:
            raise DuplicateKeyError("duplicate schedule")
        self.documents[document["_id"]] = document.copy()

    async def update_one(self, query: dict, update: dict) -> FakeUpdateResult:
        document = self.documents.get(query["_id"])
        if document is None:
            return FakeUpdateResult(0)
        now = query["$or"][1]["expires_at"]["$lte"]
        recoverable = document["status"] == "failed" or (
            document["status"] == "running" and document["expires_at"] <= now
        )
        if not recoverable:
            return FakeUpdateResult(0)
        document.update(update["$set"])
        document["attempts"] += update["$inc"]["attempts"]
        document.pop("error", None)
        return FakeUpdateResult(1)


def test_due_slots_use_utc_boundaries() -> None:
    now = datetime(2026, 7, 19, 1, 30, tzinfo=UTC)  # Sunday

    assert daily_slot(now, "02:00").due_at == datetime(2026, 7, 18, 2, tzinfo=UTC)
    assert weekly_slot(now, 6, "03:00").due_at == datetime(2026, 7, 12, 3, tzinfo=UTC)
    assert interval_slot(now, 30).due_at == datetime(2026, 7, 19, 1, 30, tzinfo=UTC)


def test_slot_key_is_stable_for_same_due_time() -> None:
    due = datetime(2026, 7, 19, 2, tzinfo=UTC)

    assert ScheduleSlot("backup-daily", due, "daily").key == "backup-daily:20260719T0200Z"


def test_weekly_day_replaces_daily_and_old_slots_are_not_caught_up() -> None:
    settings = Settings(
        mongodb_uri="mongodb://localhost:27017",
        jwt_secret="a-test-secret-that-is-definitely-32-characters",
        backup_schedule_catchup_minutes=360,
    )
    sunday_before_weekly = due_slots(
        settings, datetime(2026, 7, 19, 2, 30, tzinfo=UTC)
    )
    assert [slot.task for slot in sunday_before_weekly] == ["upload-retry-prune"]

    sunday_after_weekly = due_slots(
        settings, datetime(2026, 7, 19, 3, 5, tzinfo=UTC)
    )
    assert [slot.task for slot in sunday_after_weekly] == [
        "backup-weekly",
        "upload-retry-prune",
    ]

    monday_after_daily = due_slots(
        settings, datetime(2026, 7, 20, 2, 5, tzinfo=UTC)
    )
    assert [slot.task for slot in monday_after_daily] == [
        "backup-daily",
        "upload-retry-prune",
    ]

    monday_too_late = due_slots(settings, datetime(2026, 7, 20, 15, 0, tzinfo=UTC))
    assert [slot.task for slot in monday_too_late] == ["upload-retry-prune"]


@pytest.mark.asyncio
async def test_claim_is_idempotent_and_recovers_only_stale_claims() -> None:
    collection = FakeScheduleCollection()
    now = datetime(2026, 7, 19, 2, tzinfo=UTC)
    slot = ScheduleSlot("backup-daily", now, "daily")
    stale_after = timedelta(minutes=120)

    assert await claim_slot(
        collection, slot, owner="first", now=now, stale_after=stale_after
    )
    assert not await claim_slot(
        collection,
        slot,
        owner="second",
        now=now + timedelta(minutes=30),
        stale_after=stale_after,
    )
    assert await claim_slot(
        collection,
        slot,
        owner="recovery",
        now=now + timedelta(minutes=121),
        stale_after=stale_after,
    )
    assert collection.documents[slot.key]["owner"] == "recovery"
    assert collection.documents[slot.key]["attempts"] == 2
