from __future__ import annotations

import argparse
import asyncio
import os
import socket
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from pymongo import AsyncMongoClient
from pymongo.errors import DuplicateKeyError

from ..config import Settings, get_settings
from .backup import (
    create_local_backup,
    distributed_lease,
    exclusive_lock,
    prune,
    upload_pending,
)

UTC = timezone.utc
SUCCESSFUL_BACKUP_STATUSES = {"pending_upload", "uploading", "uploaded", "pruned"}


@dataclass(frozen=True)
class ScheduleSlot:
    task: str
    due_at: datetime
    cadence: str | None = None

    @property
    def key(self) -> str:
        stamp = self.due_at.astimezone(UTC).strftime("%Y%m%dT%H%MZ")
        return f"{self.task}:{stamp}"


def _parse_time(value: str) -> tuple[int, int]:
    hour, minute = value.split(":")
    return int(hour), int(minute)


def daily_slot(now: datetime, value: str) -> ScheduleSlot:
    now = now.astimezone(UTC)
    hour, minute = _parse_time(value)
    due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if due > now:
        due -= timedelta(days=1)
    return ScheduleSlot("backup-daily", due, "daily")


def hourly_slot(now: datetime) -> ScheduleSlot:
    now = now.astimezone(UTC)
    due = now.replace(minute=0, second=0, microsecond=0)
    return ScheduleSlot("backup-hourly", due, "hourly")


def weekly_slot(now: datetime, weekday: int, value: str) -> ScheduleSlot:
    """Legacy helper retained for tests and manual tooling."""
    now = now.astimezone(UTC)
    hour, minute = _parse_time(value)
    due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    due -= timedelta(days=(due.weekday() - weekday) % 7)
    if due > now:
        due -= timedelta(days=7)
    return ScheduleSlot("backup-weekly", due, "weekly")


def interval_slot(now: datetime, minutes: int) -> ScheduleSlot:
    now = now.astimezone(UTC)
    interval_seconds = minutes * 60
    timestamp = int(now.timestamp()) // interval_seconds * interval_seconds
    return ScheduleSlot("upload-retry-prune", datetime.fromtimestamp(timestamp, UTC))


async def load_schedule_overrides(db: Any, settings: Settings) -> tuple[str, str]:
    mode = settings.backup_schedule_mode
    daily_time = settings.backup_daily_time_utc
    try:
        docs = await db.system_settings.find(
            {"key": {"$in": ["backup_schedule_mode", "backup_daily_time_utc"]}}
        ).to_list(length=None)
    except Exception:
        return mode, daily_time
    for doc in docs or []:
        key = doc.get("key")
        value = doc.get("value")
        if key == "backup_schedule_mode" and value in {"hourly", "daily", "disabled"}:
            mode = str(value)
        if key == "backup_daily_time_utc" and isinstance(value, str) and ":" in value:
            daily_time = value
    return mode, daily_time


def due_slots(
    settings: Settings,
    now: datetime,
    *,
    schedule_mode: str | None = None,
    daily_time_utc: str | None = None,
) -> tuple[ScheduleSlot, ...]:
    """Return due dump + maintenance work for the configured schedule mode."""
    now = now.astimezone(UTC)
    mode = (schedule_mode or settings.backup_schedule_mode or "hourly").strip().lower()
    daily_time = daily_time_utc or settings.backup_daily_time_utc
    maximum_age = timedelta(minutes=settings.backup_schedule_catchup_minutes)
    slots: list[ScheduleSlot] = []

    backup_slot: ScheduleSlot | None = None
    if mode == "hourly":
        backup_slot = hourly_slot(now)
    elif mode == "daily":
        backup_slot = daily_slot(now, daily_time)
    # mode == "disabled" → no dump slot

    if backup_slot is not None and timedelta(0) <= now - backup_slot.due_at <= maximum_age:
        slots.append(backup_slot)
    slots.append(interval_slot(now, settings.backup_maintenance_interval_minutes))
    return tuple(slots)


async def claim_slot(
    collection: Any,
    slot: ScheduleSlot,
    *,
    owner: str,
    now: datetime,
    stale_after: timedelta,
) -> bool:
    """Atomically claim a new slot or recover an abandoned/failed claim."""
    document = {
        "_id": slot.key,
        "task": slot.task,
        "cadence": slot.cadence,
        "due_at": slot.due_at,
        "status": "running",
        "owner": owner,
        "started_at": now,
        "updated_at": now,
        "expires_at": now + stale_after,
        "attempts": 1,
    }
    try:
        await collection.insert_one(document)
        return True
    except DuplicateKeyError:
        pass

    result = await collection.update_one(
        {
            "_id": slot.key,
            "$or": [
                {"status": "failed"},
                {"status": "running", "expires_at": {"$lte": now}},
            ],
        },
        {
            "$set": {
                "status": "running",
                "owner": owner,
                "started_at": now,
                "updated_at": now,
                "expires_at": now + stale_after,
            },
            "$inc": {"attempts": 1},
            "$unset": {"error": ""},
        },
    )
    return result.modified_count == 1


async def finish_slot(collection: Any, slot: ScheduleSlot, status: str, error: str = "") -> None:
    now = datetime.now(UTC)
    update: dict[str, Any] = {
        "$set": {"status": status, "updated_at": now, "completed_at": now},
        "$unset": {"expires_at": ""},
    }
    if error:
        update["$set"]["error"] = error[:2000]
    else:
        update["$unset"]["error"] = ""
    await collection.update_one({"_id": slot.key}, update)


async def execute_slot(settings: Settings, db: Any, slot: ScheduleSlot) -> None:
    schedules = db.backup_schedules
    if slot.cadence:
        existing = await db.backup_jobs.find_one({"schedule_id": slot.key})
        if existing:
            if existing.get("status") in SUCCESSFUL_BACKUP_STATUSES:
                await finish_slot(schedules, slot, "completed")
                return
            raise RuntimeError(
                f"Schedule {slot.key} already has backup job status "
                f"{existing.get('status', 'unknown')}; refusing a duplicate dump"
            )
        await create_local_backup(settings, db, slot.cadence, schedule_id=slot.key)
    else:
        await upload_pending(settings, db)
        await prune(settings, db)
    await finish_slot(schedules, slot, "completed")


async def scheduler_iteration(settings: Settings, db: Any, now: datetime) -> int:
    owner = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex}"
    claimed = 0
    mode, daily_time = await load_schedule_overrides(db, settings)
    for slot in due_slots(
        settings, now, schedule_mode=mode, daily_time_utc=daily_time
    ):
        if not await claim_slot(
            db.backup_schedules,
            slot,
            owner=owner,
            now=now,
            stale_after=timedelta(minutes=settings.backup_schedule_stale_minutes),
        ):
            continue
        claimed += 1
        try:
            with exclusive_lock(settings.backup_lock_file):
                async with distributed_lease(settings, db):
                    await execute_slot(settings, db, slot)
        except Exception as exc:
            await finish_slot(db.backup_schedules, slot, "failed", str(exc))
            print(f"Scheduled task {slot.key} failed: {exc}", flush=True)
    return claimed


async def run_scheduler(*, once: bool = False) -> None:
    settings = get_settings()
    backup_uri = settings.backup_mongodb_uri or settings.mongodb_uri
    client = AsyncMongoClient(backup_uri, appname="tahmeed-backup-scheduler", tz_aware=True)
    try:
        db = client[settings.db_name]
        await db.command("ping")
        while True:
            await scheduler_iteration(settings, db, datetime.now(UTC))
            if once:
                return
            await asyncio.sleep(settings.backup_scheduler_poll_seconds)
    finally:
        await client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run restart-safe scheduled backup tasks.")
    parser.add_argument("--once", action="store_true", help="process current due slots and exit")
    args = parser.parse_args()
    asyncio.run(run_scheduler(once=args.once))


if __name__ == "__main__":
    main()
