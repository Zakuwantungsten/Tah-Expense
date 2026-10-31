"""Push Ready days of an import session into Master Expenses.

Each (source file, reconciled date) becomes one Browse → Uploads batch named
after the source file. Its upload id is derived from that name and date, so
pushing the same day again lands in the same batch and the server skips the
rows it already holds.
"""

from __future__ import annotations

import inspect
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from tahmeed.services.daily_import_service import (
    _ref_float_from_notes,
    commit_daily_to_master,
    list_daily_uploads_page,
    normalize_receipt,
)
from tahmeed.services.import_session_parser import StagedRow, parse_source_file_date
from tahmeed.services.import_session_service import ImportSession, save_session
from tahmeed.services.import_session_validation import (
    DAY_READY,
    RowResolution,
    SessionValidation,
)

_UPLOAD_NAMESPACE = uuid.UUID("5f0c2b8e-6a51-4d8e-9a7e-1c3f0d2b7a90")
_EXCEL_SUFFIX = re.compile(r"\.(xlsx|xlsm|xls)$", re.IGNORECASE)


def upload_name_for(source_file: str, batch_date: date) -> str:
    """Browse → Uploads name; always carries the reconciled date."""
    name = " ".join((source_file or "").split()) or "Import"
    if parse_source_file_date(name) == batch_date:
        return name
    return f"MATUMIZI YA {batch_date.strftime('%d-%m-%y')} ({name})"


def _name_key(name: str) -> str:
    return _EXCEL_SUFFIX.sub("", " ".join((name or "").split())).casefold()


def upload_id_for(upload_name: str, batch_date: date) -> str:
    return str(uuid.uuid5(_UPLOAD_NAMESPACE, f"{_name_key(upload_name)}|{batch_date.isoformat()}"))


def _midnight(value: date) -> datetime:
    return datetime(value.year, value.month, value.day)


def row_payload(
    row: StagedRow,
    res: RowResolution,
    *,
    upload_id: str,
    upload_name: str,
) -> dict:
    """``commit_daily_to_master`` row, shaped like ``staged_row_payload``."""
    return {
        "date": _midnight(row.excel_date or row.batch_date),
        "item": res.item,
        "description": row.description,
        "truck_number": res.truck,
        "memo": row.memo,
        "ref_float": _ref_float_from_notes(row.notes),
        "amount": row.amount,
        "currency": row.currency,
        "amount_usd": row.amount_usd,
        "receipt_status": normalize_receipt(row.receipt_status),
        "ownership": row.ownership,
        "approver": row.approver,
        "payee": res.payee,
        "category_id": res.category_id,
        "category_name": res.item,
        "lpo_do": row.lpo_do,
        "do_number": row.do_number,
        # Part of the server's per-row duplicate key: without an Excel serial,
        # two identical lines of one file must still differ.
        "serial": row.serial if row.serial is not None else f"XL{row.excel_row}",
        "daily_import_id": upload_id,
        "daily_import_source": upload_name,
        "date_discrepancy": False,
        "import_primary_date": _midnight(row.batch_date),
    }


@dataclass
class PushGroup:
    batch_date: date
    upload_name: str
    upload_id: str
    payloads: List[dict] = field(default_factory=list)

    @property
    def total_tzs(self) -> float:
        return sum(p["amount"] for p in self.payloads if p["currency"] == "TZS")

    @property
    def total_usd(self) -> float:
        return sum(p["amount_usd"] or 0.0 for p in self.payloads)


class DayNotReady(ValueError):
    def __init__(self, days: List[date]) -> None:
        self.days = days
        super().__init__("Not ready: " + ", ".join(d.isoformat() for d in days))


def build_push_groups(
    session: ImportSession,
    validation: SessionValidation,
    days: Iterable[date],
) -> List[PushGroup]:
    """One group per (day, source file), days in date order. Ignored rows are left out."""
    wanted = sorted(set(days))
    not_ready = [
        d for d in wanted
        if validation.day(d) is None or validation.day(d).status != DAY_READY
    ]
    if not_ready:
        raise DayNotReady(not_ready)

    groups: Dict[tuple, PushGroup] = {}
    for batch_date in wanted:
        for row in session.rows_for_day(batch_date):
            res = validation.rows[row.row_id]
            if res.ignored:
                continue
            if not res.ok:
                raise DayNotReady([batch_date])
            name = upload_name_for(row.source_file, batch_date)
            group = groups.get((batch_date, name))
            if group is None:
                group = PushGroup(batch_date, name, upload_id_for(name, batch_date))
                groups[(batch_date, name)] = group
            group.payloads.append(
                row_payload(row, res, upload_id=group.upload_id, upload_name=name)
            )
    return list(groups.values())


def _row_day(payload: dict) -> date:
    value = payload["date"]
    return value.date() if isinstance(value, datetime) else value


@dataclass
class DateOutlier:
    """Rows of one day whose Excel date is in another month (or year)."""

    batch_date: date
    row_date: date
    rows: int


def date_outliers(groups: Iterable[PushGroup]) -> List[DateOutlier]:
    """Master Expenses files rows by their own date, so these land in another month."""
    counts: Dict[tuple, int] = defaultdict(int)
    for group in groups:
        for payload in group.payloads:
            row_date = _row_day(payload)
            if (row_date.year, row_date.month) != (group.batch_date.year, group.batch_date.month):
                counts[(group.batch_date, row_date)] += 1
    return [DateOutlier(b, r, n) for (b, r), n in sorted(counts.items())]


def supplier_rows(groups: Iterable[PushGroup], is_supplier: Callable[[str], bool]) -> Dict[str, int]:
    """Rows per supplier item: pushed, but Master Expenses never lists them."""
    counts: Dict[str, int] = defaultdict(int)
    for group in groups:
        for payload in group.payloads:
            if is_supplier(payload["item"]):
                counts[payload["item"]] += 1
    return dict(counts)


def row_date_range(groups: Iterable[PushGroup]) -> Optional[Tuple[date, date]]:
    days = [_row_day(p) for g in groups for p in g.payloads]
    return (min(days), max(days)) if days else None


@dataclass
class PriorUpload:
    upload_id: str
    source_filename: str
    row_count: int
    primary_date: Optional[date]
    same_batch: bool  # pushed before from an import session (same upload id)


def _as_date(value) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value.strip().replace("Z", "+00:00")).date()
        except ValueError:
            return None
    return None


async def find_prior_uploads(
    groups: List[PushGroup],
    *,
    page_size: int = 500,
    max_pages: int = 20,
) -> Dict[date, List[PriorUpload]]:
    """Existing Browse → Uploads batches for the same day or the same file name."""
    by_id = {g.upload_id: g for g in groups}
    by_name = {(_name_key(g.upload_name), g.batch_date): g for g in groups}
    days = {g.batch_date for g in groups}
    found: Dict[date, List[PriorUpload]] = defaultdict(list)
    seen: set = set()
    skip = 0
    for _ in range(max_pages):
        page = await list_daily_uploads_page(limit=page_size, skip=skip)
        for up in page.items:
            uid = str(up.get("_id") or up.get("upload_id") or "").strip()
            if not uid or uid in seen:
                continue
            seen.add(uid)
            name = str(up.get("source_filename") or "")
            primary = _as_date(up.get("primary_date")) or _as_date(up.get("min_date"))
            group = by_id.get(uid)
            if group is None:
                group = by_name.get((_name_key(name), primary))
            if group is None and primary in days:
                day = primary
            elif group is not None:
                day = group.batch_date
            else:
                continue
            found[day].append(
                PriorUpload(
                    upload_id=uid,
                    source_filename=name,
                    row_count=int(up.get("count") or 0),
                    primary_date=primary,
                    same_batch=uid in by_id,
                )
            )
        skip += len(page.items)
        if not page.items or skip >= int(page.total or 0):
            break
    return dict(found)


@dataclass
class DayPushResult:
    batch_date: date
    rows: int
    inserted: int
    duplicates_skipped: int
    skipped_no_item: int
    upload_ids: List[str]


@dataclass
class PushSummary:
    days: List[DayPushResult] = field(default_factory=list)
    failed_day: Optional[date] = None
    error: str = ""
    cancelled: bool = False

    @property
    def inserted(self) -> int:
        return sum(d.inserted for d in self.days)

    @property
    def duplicates_skipped(self) -> int:
        return sum(d.duplicates_skipped for d in self.days)

    @property
    def skipped_no_item(self) -> int:
        return sum(d.skipped_no_item for d in self.days)


async def push_groups(
    session: ImportSession,
    groups: List[PushGroup],
    *,
    on_progress: Optional[Callable[[int, int, PushGroup], object]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> PushSummary:
    """Commit group by group; a day is marked pushed once all its groups are in.

    Stops at the first failure. Days finished before it stay pushed and saved;
    pushing the failed day again skips the rows that already went in.
    """
    summary = PushSummary()
    by_day: Dict[date, List[PushGroup]] = defaultdict(list)
    for group in groups:
        by_day[group.batch_date].append(group)
    total = len(groups)
    done = 0
    for batch_date in sorted(by_day):
        if should_cancel is not None and should_cancel():
            summary.cancelled = True
            break
        day = DayPushResult(batch_date, 0, 0, 0, 0, [])
        try:
            for group in by_day[batch_date]:
                done += 1
                if on_progress is not None:
                    update = on_progress(done, total, group)
                    if inspect.isawaitable(update):
                        await update
                result = await commit_daily_to_master(group.payloads)
                day.rows += len(group.payloads)
                day.inserted += int(result.get("inserted") or 0)
                day.duplicates_skipped += int(result.get("duplicates_skipped") or 0)
                day.skipped_no_item += int(result.get("skipped_no_item") or 0)
                day.upload_ids.append(str(result.get("upload_id") or group.upload_id))
        except Exception as exc:
            summary.failed_day = batch_date
            summary.error = str(exc) or exc.__class__.__name__
            break
        session.mark_day_pushed(batch_date, row_count=day.rows, upload_ids=day.upload_ids)
        save_session(session)
        summary.days.append(day)
    return summary
