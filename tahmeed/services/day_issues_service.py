"""Day Issues — unmapped / bad description→payee+item maps for a register day."""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Callable, List, Optional, Sequence

from bson import ObjectId

from tahmeed.services.cashier_service import get_transactions_by_date
from tahmeed.services.description_mapping_service import (
    get_mappings_for_descriptions,
    normalize_description,
)
from tahmeed.services.payee_service import get_payee_names


def merge_transactions_for_day_issues(
    saved: Sequence,
    extras: Sequence,
) -> list:
    """Overlay *extras* onto *saved*.

    - Extra rows with ``_id`` replace the matching saved row (live cell edits).
    - Extra rows without ``_id`` are appended (unsaved / staged / draft rows).
    """
    by_id: dict[str, object] = {}
    ephemeral: list = []
    for tx in extras:
        tid = getattr(tx, "_id", None)
        if tid is None:
            ephemeral.append(tx)
        else:
            by_id[str(tid)] = tx

    out: list = []
    seen: set[str] = set()
    for tx in saved:
        tid = getattr(tx, "_id", None)
        key = str(tid) if tid is not None else ""
        if key and key in by_id:
            out.append(by_id[key])
            seen.add(key)
        else:
            out.append(tx)
    for key, tx in by_id.items():
        if key not in seen:
            out.append(tx)
    out.extend(ephemeral)
    return out


@dataclass(frozen=True)
class DayIssue:
    """One unique description that blocks (or warns on) submit."""

    kind: str  # "Unmapped" | "Bad map"
    description: str
    description_key: str
    count: int
    payee: str = ""
    item: str = ""
    detail: str = ""


@dataclass
class DayIssuesSnapshot:
    target_date: date
    issues: List[DayIssue] = field(default_factory=list)
    scope: str = "day"  # "day" | "all"
    # description_key -> register days that contributed (All days scope).
    issue_dates: dict = field(default_factory=dict)
    days_scanned: List[date] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.issues)

    @property
    def unmapped_count(self) -> int:
        return sum(1 for issue in self.issues if issue.kind == "Unmapped")

    @property
    def bad_map_count(self) -> int:
        return sum(1 for issue in self.issues if issue.kind == "Bad map")


def merge_day_issue_snapshots(
    snaps: Sequence[DayIssuesSnapshot],
    *,
    anchor_date: date,
) -> DayIssuesSnapshot:
    """Merge per-day snapshots into one All-days view (by description key)."""
    by_key: dict[str, DayIssue] = {}
    dates_by_key: dict[str, set[date]] = {}
    days_scanned: list[date] = []

    for snap in snaps:
        days_scanned.append(snap.target_date)
        for issue in snap.issues:
            key = issue.description_key
            dates_by_key.setdefault(key, set()).add(snap.target_date)
            existing = by_key.get(key)
            if existing is None:
                by_key[key] = issue
                continue
            # Prefer Bad map over Unmapped when either day has a bad map.
            kind = existing.kind
            if issue.kind == "Bad map" or existing.kind == "Bad map":
                kind = "Bad map"
            payee = issue.payee or existing.payee
            item = issue.item or existing.item
            detail = issue.detail if issue.kind == "Bad map" else existing.detail
            if existing.kind == "Bad map" and issue.kind != "Bad map":
                detail = existing.detail
            by_key[key] = DayIssue(
                kind=kind,
                description=existing.description or issue.description,
                description_key=key,
                count=existing.count + issue.count,
                payee=payee,
                item=item,
                detail=detail or existing.detail,
            )

    issues = sorted(
        by_key.values(),
        key=lambda i: (-i.count, i.description_key),
    )
    return DayIssuesSnapshot(
        target_date=anchor_date,
        issues=issues,
        scope="all",
        issue_dates={k: sorted(v) for k, v in dates_by_key.items()},
        days_scanned=sorted(set(days_scanned)),
    )


def _row_description(tx) -> str:
    return (getattr(tx, "description", None) or "").strip()


def _row_item(tx) -> str:
    return (
        (getattr(tx, "item", None) or "").strip()
        or (getattr(tx, "category_name", None) or "").strip()
    )


def _row_payee(tx) -> str:
    return (getattr(tx, "payee", None) or "").strip()


def _is_open_row(tx) -> bool:
    """Issues only matter for draft / not-yet-verified rows."""
    if getattr(tx, "trashed", False) or getattr(tx, "discarded", False):
        return False
    if getattr(tx, "verified", False):
        return False
    return True


def _cashier_id_matches(tx, cashier_id: Optional[ObjectId]) -> bool:
    """When *cashier_id* is set, keep only that cashier's rows."""
    if cashier_id is None:
        return True
    cid = getattr(tx, "cashier_id", None)
    if cid is None:
        return False
    return str(cid) == str(cashier_id)


def filter_rows_for_cashier_scope(
    rows: Sequence,
    cashier_id: Optional[ObjectId],
) -> list:
    if cashier_id is None:
        return list(rows)
    return [tx for tx in rows if _cashier_id_matches(tx, cashier_id)]


async def collect_day_issues(
    target: date,
    *,
    transactions: Optional[Sequence] = None,
    extra_transactions: Optional[Sequence] = None,
    payee_names: Optional[Sequence[str]] = None,
    cashier_id: Optional[ObjectId] = None,
) -> DayIssuesSnapshot:
    """Build Day Issues for *target* from open (unverified) register rows.

    Pass ``transactions`` to fully replace the DB load (e.g. live Table snapshot
    for the open register day). Pass ``extra_transactions`` to overlay dirty
    saved rows and append unsaved / draft rows onto the DB result.

    When ``cashier_id`` is set (cashier UI), only that cashier's rows count.
    Accountant UI passes ``cashier_id=None`` to include everyone.
    """
    open_rows = await _load_open_rows(
        target,
        transactions=transactions,
        extra_transactions=extra_transactions,
        cashier_id=cashier_id,
    )
    descriptions = _open_row_descriptions(open_rows)
    if not descriptions:
        return DayIssuesSnapshot(target_date=target, issues=[])
    mappings = await get_mappings_for_descriptions(descriptions)
    payee_set = await _resolve_payee_set(payee_names)
    return _build_day_snapshot(target, open_rows, mappings, payee_set)


async def _load_open_rows(
    target: date,
    *,
    transactions: Optional[Sequence] = None,
    extra_transactions: Optional[Sequence] = None,
    cashier_id: Optional[ObjectId] = None,
) -> list:
    if transactions is not None:
        rows = list(transactions)
    elif cashier_id is not None:
        rows = await get_transactions_by_date(
            target, merged=False, cashier_id=cashier_id
        )
    else:
        rows = await get_transactions_by_date(target, merged=True)
    if extra_transactions:
        rows = merge_transactions_for_day_issues(rows, extra_transactions)
    rows = filter_rows_for_cashier_scope(rows, cashier_id)
    return [tx for tx in rows if _is_open_row(tx)]


def _open_row_descriptions(open_rows: Sequence) -> list[str]:
    """One display description per normalized key."""
    display_for: dict[str, str] = {}
    for tx in open_rows:
        raw = _row_description(tx)
        key = normalize_description(raw)
        if key:
            display_for.setdefault(key, raw)
    return list(display_for.values())


async def _resolve_payee_set(payee_names: Optional[Sequence[str]]) -> set[str]:
    if payee_names is None:
        try:
            return {n.upper() for n in await get_payee_names(active_only=True)}
        except Exception:
            return set()
    return {str(n).strip().upper() for n in payee_names if str(n).strip()}


def _build_day_snapshot(
    target: date,
    open_rows: Sequence,
    mappings: dict,
    payee_set: set[str],
) -> DayIssuesSnapshot:
    desc_counts: Counter[str] = Counter()
    display_for: dict[str, str] = {}
    for tx in open_rows:
        raw = _row_description(tx)
        key = normalize_description(raw)
        if not key:
            continue
        desc_counts[key] += 1
        display_for.setdefault(key, raw)

    if not desc_counts:
        return DayIssuesSnapshot(target_date=target, issues=[])

    # Per-description: if every open row already has item+payee, not an issue.
    rows_by_key: dict[str, list] = {}
    for tx in open_rows:
        key = normalize_description(_row_description(tx))
        if key:
            rows_by_key.setdefault(key, []).append(tx)

    issues: list[DayIssue] = []
    for key, count in sorted(desc_counts.items(), key=lambda item: (-item[1], item[0])):
        display = display_for[key]
        group = rows_by_key.get(key) or []
        all_complete = all(_row_item(tx) and _row_payee(tx) for tx in group)
        if all_complete:
            continue

        mapping = mappings.get(key)
        if mapping is None:
            issues.append(
                DayIssue(
                    kind="Unmapped",
                    description=display,
                    description_key=key,
                    count=count,
                    detail="No payee + item mapping",
                )
            )
            continue

        payee = (mapping.payee or "").strip().upper()
        item = (mapping.category_name or "").strip()
        problems: list[str] = []
        if not payee:
            problems.append("missing payee")
        elif payee_set and payee not in payee_set:
            problems.append("payee not in list")
        if not item:
            problems.append("missing item")
        if problems:
            issues.append(
                DayIssue(
                    kind="Bad map",
                    description=display,
                    description_key=key,
                    count=count,
                    payee=payee,
                    item=item,
                    detail="; ".join(problems),
                )
            )
            continue

        # Mapping OK but some rows still blank — treat as Unmapped for apply.
        issues.append(
            DayIssue(
                kind="Unmapped",
                description=display,
                description_key=key,
                count=count,
                payee=payee,
                item=item,
                detail="Mapped but rows still need payee/item applied",
            )
        )

    return DayIssuesSnapshot(target_date=target, issues=issues)


async def count_day_issues(
    target: date,
    *,
    transactions: Optional[Sequence] = None,
    extra_transactions: Optional[Sequence] = None,
    cashier_id: Optional[ObjectId] = None,
) -> int:
    snap = await collect_day_issues(
        target,
        transactions=transactions,
        extra_transactions=extra_transactions,
        cashier_id=cashier_id,
    )
    return snap.total


# Parallel day loads for All days; small enough not to swamp the API tunnel.
_ALL_DAYS_CONCURRENCY = 4
# Mapping lookups pass keys in the query string; batch to keep URLs short.
_MAPPING_BATCH = 150


async def _mappings_in_batches(descriptions: Sequence[str]) -> dict:
    batches = [
        list(descriptions[i:i + _MAPPING_BATCH])
        for i in range(0, len(descriptions), _MAPPING_BATCH)
    ]
    results = await asyncio.gather(
        *(get_mappings_for_descriptions(batch) for batch in batches)
    )
    merged: dict = {}
    for part in results:
        merged.update(part)
    return merged


async def collect_day_issues_all_days(
    *,
    cashier_id: Optional[ObjectId] = None,
    anchor_date: Optional[date] = None,
    live_target: Optional[date] = None,
    live_transactions: Optional[Sequence] = None,
    live_extras: Optional[Sequence] = None,
    limit_days: int = 120,
    day_cache: Optional[dict] = None,
    payee_names: Optional[Sequence[str]] = None,
    only_days: Optional[Sequence[date]] = None,
    progress: Optional[Callable[[int, int], None]] = None,
) -> DayIssuesSnapshot:
    """Build Day Issues across open register days (incomplete rows only).

    When *day_cache* is provided, previously scanned days are reused unless they
    are the live register day (always refreshed from live rows). Successful
    scans are written back into *day_cache*. *only_days* skips the open-day
    lookup and scans just those register days.
    """
    from tahmeed.services.cashier_service import get_open_day_summaries

    anchor = anchor_date or date.today()
    days: list[date] = []
    if only_days:
        days = [d for d in only_days if isinstance(d, date)]
    else:
        try:
            summaries = await get_open_day_summaries(
                cashier_id,
                all_cashiers=cashier_id is None,
                limit=limit_days,
            )
        except Exception:
            summaries = []
        for row in summaries:
            d = row.get("date")
            if isinstance(d, date):
                days.append(d)
    if live_target is not None and live_target not in days:
        days.append(live_target)
    if not days and live_transactions is None and live_extras is None:
        return DayIssuesSnapshot(
            target_date=anchor, issues=[], scope="all", days_scanned=[]
        )

    by_day: dict[date, DayIssuesSnapshot] = {}
    to_scan: list[date] = []
    for d in sorted(set(days)):
        is_live = live_target is not None and d == live_target
        cached = None if day_cache is None else day_cache.get(d)
        if cached is not None and not is_live:
            by_day[d] = cached
        else:
            to_scan.append(d)

    gate = asyncio.Semaphore(_ALL_DAYS_CONCURRENCY)
    done = 0
    if progress is not None and to_scan:
        progress(0, len(to_scan))

    async def _load(d: date) -> list:
        nonlocal done
        kwargs: dict = {"cashier_id": cashier_id}
        if live_target is not None and d == live_target:
            if live_transactions is not None:
                kwargs["transactions"] = live_transactions
            if live_extras is not None:
                kwargs["extra_transactions"] = live_extras
        async with gate:
            rows = await _load_open_rows(d, **kwargs)
        done += 1
        if progress is not None:
            progress(done, len(to_scan))
        return rows

    loaded = await asyncio.gather(*(_load(d) for d in to_scan))
    descriptions = _open_row_descriptions([tx for rows in loaded for tx in rows])
    mappings: dict = {}
    payee_set: set[str] = set()
    if descriptions:
        mappings = await _mappings_in_batches(descriptions)
        payee_set = await _resolve_payee_set(payee_names)
    for d, rows in zip(to_scan, loaded):
        snap = _build_day_snapshot(d, rows, mappings, payee_set)
        if day_cache is not None:
            day_cache[d] = snap
        by_day[d] = snap

    snaps = [by_day[d] for d in sorted(by_day)]
    if not snaps:
        return DayIssuesSnapshot(
            target_date=anchor, issues=[], scope="all", days_scanned=[]
        )
    return merge_day_issue_snapshots(snaps, anchor_date=anchor)


def as_of_label(target: date) -> str:
    if isinstance(target, datetime):
        target = target.date()
    return target.strftime("%d %b %Y")
