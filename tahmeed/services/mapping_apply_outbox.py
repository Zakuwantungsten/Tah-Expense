"""In-process outbox: stamp open register rows after Description Maps save.

Maps are durable on the server as soon as bulk-upsert succeeds. This outbox
applies Payee + Item onto matching open transactions in the background via one
``update_many`` call, with idempotent retries.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, List, Optional, Sequence

from bson import ObjectId

_log = logging.getLogger("tahmeed.mapping_apply_outbox")

_MAX_ATTEMPTS = 5
_BASE_BACKOFF_SEC = 0.6


@dataclass
class MappingApplyJob:
    job_id: str
    description_keys: List[str]
    payee: str
    item: str
    category_id: ObjectId
    dates: List[date] = field(default_factory=list)
    cashier_id: Optional[ObjectId] = None
    attempts: int = 0
    status: str = "pending"  # pending | in_flight | done | failed | cancelled


_jobs: List[MappingApplyJob] = []
_drain_lock = asyncio.Lock()
_drain_scheduled = False
_on_failed: Optional[Callable[[MappingApplyJob, BaseException], None]] = None


def set_failure_handler(
    handler: Optional[Callable[[MappingApplyJob, BaseException], None]],
) -> None:
    """Optional UI hook for hard failures after retries are exhausted."""
    global _on_failed
    _on_failed = handler


def pending_jobs() -> List[MappingApplyJob]:
    return [job for job in list(_jobs) if job.status in {"pending", "in_flight", "failed"}]


def cancel_pending_jobs_for_keys(description_keys: Sequence[str]) -> int:
    """Cancel pending/failed apply jobs that touch any of *description_keys*.

    ``in_flight`` jobs are left alone; callers should ``await wait_outbox_idle()``
    then clear stamps so a finishing apply cannot outrace Undo.
    """
    keys = {
        " ".join((key or "").strip().upper().split())
        for key in description_keys
        if " ".join((key or "").strip().upper().split())
    }
    if not keys:
        return 0
    cancelled = 0
    for job in _jobs:
        if job.status not in {"pending", "failed"}:
            continue
        if keys.intersection(job.description_keys):
            job.status = "cancelled"
            cancelled += 1
    return cancelled


async def wait_outbox_idle() -> None:
    """Block until any in-flight drain finishes (no-op if idle)."""
    async with _drain_lock:
        return


def enqueue_mapping_apply(
    *,
    description_keys: Sequence[str],
    payee: str,
    item: str,
    category_id: ObjectId,
    dates: Sequence[date] | None = None,
    cashier_id: ObjectId | None = None,
    job_id: str | None = None,
) -> MappingApplyJob:
    """Queue a bulk apply. Dedupes against an identical in-flight/pending job."""
    keys = sorted(
        {
            " ".join((key or "").strip().upper().split())
            for key in description_keys
            if " ".join((key or "").strip().upper().split())
        }
    )
    day_list = sorted({d for d in (dates or []) if isinstance(d, date)})
    for existing in _jobs:
        if existing.status not in {"pending", "in_flight"}:
            continue
        if (
            existing.description_keys == keys
            and existing.payee == " ".join((payee or "").strip().upper().split())
            and existing.item == (item or "").strip()
            and existing.category_id == category_id
            and existing.dates == day_list
            and existing.cashier_id == cashier_id
        ):
            return existing

    job = MappingApplyJob(
        job_id=job_id or str(uuid.uuid4()),
        description_keys=keys,
        payee=" ".join((payee or "").strip().upper().split()),
        item=(item or "").strip(),
        category_id=category_id,
        dates=day_list,
        cashier_id=cashier_id,
    )
    _jobs.append(job)
    _schedule_drain()
    return job


def _schedule_drain(delay_ms: int = 0) -> None:
    global _drain_scheduled
    if _drain_scheduled:
        return
    _drain_scheduled = True

    def _kick() -> None:
        global _drain_scheduled
        _drain_scheduled = False
        try:
            from tahmeed.ui.async_utils import schedule_coro

            schedule_coro(drain_mapping_apply_outbox())
        except Exception:
            _log.exception("Failed to schedule mapping-apply outbox drain")

    if delay_ms <= 0:
        try:
            from tahmeed.ui.async_utils import schedule_call

            schedule_call(_kick)
        except Exception:
            _kick()
    else:
        try:
            from PySide6.QtCore import QTimer

            QTimer.singleShot(max(0, int(delay_ms)), _kick)
        except Exception:
            _kick()


async def drain_mapping_apply_outbox() -> None:
    """Process pending apply jobs (one at a time, idempotent)."""
    if _drain_lock.locked():
        return
    async with _drain_lock:
        while True:
            job = next(
                (j for j in _jobs if j.status in {"pending", "failed"} and j.attempts < _MAX_ATTEMPTS),
                None,
            )
            if job is None:
                # Drop finished jobs to keep memory small.
                _jobs[:] = [
                    j for j in _jobs if j.status not in {"done", "cancelled"}
                ]
                return
            job.status = "in_flight"
            job.attempts += 1
            try:
                from tahmeed.services.cashier_service import apply_description_mapping_bulk

                await apply_description_mapping_bulk(
                    job.description_keys,
                    payee=job.payee,
                    category_name=job.item,
                    category_id=job.category_id,
                    dates=job.dates or None,
                    cashier_id=job.cashier_id,
                    client_job_id=job.job_id,
                )
                job.status = "done"
            except Exception as exc:
                _log.warning(
                    "mapping apply job %s failed (attempt %s): %s",
                    job.job_id,
                    job.attempts,
                    exc,
                )
                if job.attempts >= _MAX_ATTEMPTS:
                    job.status = "failed"
                    handler = _on_failed
                    if handler is not None:
                        try:
                            handler(job, exc)
                        except Exception:
                            _log.exception("mapping apply failure handler crashed")
                else:
                    job.status = "pending"
                    backoff_ms = int(
                        _BASE_BACKOFF_SEC * (2 ** (job.attempts - 1)) * 1000
                    )
                    _schedule_drain(delay_ms=backoff_ms)
                    return


def reset_outbox_for_tests() -> None:
    """Clear queue state between unit tests."""
    global _drain_scheduled, _on_failed
    _jobs.clear()
    _drain_scheduled = False
    _on_failed = None
