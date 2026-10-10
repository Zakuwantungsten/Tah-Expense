"""Day Issues — unmapped / bad description→payee+item maps for a register day."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import List, Optional, Sequence

from tahmeed.services.cashier_service import get_transactions_by_date
from tahmeed.services.description_mapping_service import (
    get_mappings_for_descriptions,
    normalize_description,
)
from tahmeed.services.payee_service import get_payee_names


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

    @property
    def total(self) -> int:
        return len(self.issues)

    @property
    def unmapped_count(self) -> int:
        return sum(1 for issue in self.issues if issue.kind == "Unmapped")

    @property
    def bad_map_count(self) -> int:
        return sum(1 for issue in self.issues if issue.kind == "Bad map")


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


async def collect_day_issues(
    target: date,
    *,
    transactions: Optional[Sequence] = None,
    payee_names: Optional[Sequence[str]] = None,
) -> DayIssuesSnapshot:
    """Build Day Issues for *target* from open (unverified) register rows."""
    if transactions is not None:
        rows = list(transactions)
    else:
        rows = await get_transactions_by_date(target, merged=True)
    open_rows = [tx for tx in rows if _is_open_row(tx)]

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

    mappings = await get_mappings_for_descriptions(list(display_for.values()))
    if payee_names is None:
        try:
            payee_set = {n.upper() for n in await get_payee_names(active_only=True)}
        except Exception:
            payee_set = set()
    else:
        payee_set = {str(n).strip().upper() for n in payee_names if str(n).strip()}

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


async def count_day_issues(target: date) -> int:
    snap = await collect_day_issues(target)
    return snap.total


def as_of_label(target: date) -> str:
    if isinstance(target, datetime):
        target = target.date()
    return target.strftime("%d %b %Y")
