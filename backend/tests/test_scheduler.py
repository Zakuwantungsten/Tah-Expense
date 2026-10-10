from datetime import datetime, timedelta, timezone

import pytest
from pymongo.errors import DuplicateKeyError

from app.cli.scheduler import (
    ScheduleSlot,
    claim_slot,
    daily_slot,
    due_slots,
    hourly_slot,
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
    assert hourly_slot(now).due_at == datetime(2026, 7, 19, 1, 0, tzinfo=UTC)


def test_slot_key_is_stable_for_same_due_time() -> None:
    due = datetime(2026, 7, 19, 2, tzinfo=UTC)

    assert ScheduleSlot("backup-daily", due, "daily").key == "backup-daily:20260719T0200Z"
    assert ScheduleSlot("backup-hourly", due, "hourly").key == "backup-hourly:20260719T0200Z"


def test_hourly_mode_dumps_within_catchup_window() -> None:
    settings = Settings(
        mongodb_uri="mongodb://localhost:27017",
        jwt_secret="a-test-secret-that-is-definitely-32-characters",
        backup_schedule_mode="hourly",
        backup_schedule_catchup_minutes=30,
    )
    just_after_hour = due_slots(settings, datetime(2026, 7, 20, 2, 5, tzinfo=UTC))
    assert [slot.task for slot in just_after_hour] == [
        "backup-hourly",
        "upload-retry-prune",
    ]

    # Current hour slot is already older than the catchup window.
    too_late = due_slots(settings, datetime(2026, 7, 20, 2, 45, tzinfo=UTC))
    assert [slot.task for slot in too_late] == ["upload-retry-prune"]


def test_daily_mode_uses_configured_time() -> None:
    settings = Settings(
        mongodb_uri="mongodb://localhost:27017",
        jwt_secret="a-test-secret-that-is-definitely-32-characters",
        backup_schedule_mode="daily",
        backup_daily_time_utc="00:00",
        backup_schedule_catchup_minutes=90,
    )
    after_midnight = due_slots(
        settings,
        datetime(2026, 7, 20, 0, 10, tzinfo=UTC),
        schedule_mode="daily",
        daily_time_utc="00:00",
    )
    assert [slot.task for slot in after_midnight] == [
        "backup-daily",
        "upload-retry-prune",
    ]


def test_disabled_mode_only_runs_maintenance() -> None:
    settings = Settings(
        mongodb_uri="mongodb://localhost:27017",
        jwt_secret="a-test-secret-that-is-definitely-32-characters",
        backup_schedule_mode="disabled",
    )
    slots = due_slots(settings, datetime(2026, 7, 20, 2, 5, tzinfo=UTC))
    assert [slot.task for slot in slots] == ["upload-retry-prune"]


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
