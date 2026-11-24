"""Helpers for batch duplicate review during register save."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Literal, Optional, Sequence, Set, Tuple

from tahmeed.models.transaction import Transaction

# Soft-dup identity: same fields the DB check uses (truck, amount, item, description).
SoftDupKey = Tuple[str, float, str, str]

DupSourceKind = Literal["db", "table"]
DupSourceFilter = Literal["all", "db", "table"]

# Blank truck / item / description facet keys shown in the review dialog.
NO_TRUCK_LABEL = "No truck"
EMPTY_ITEM_LABEL = "(no item)"
EMPTY_DESC_LABEL = "(no description)"


@dataclass
class DuplicateReviewItem:
    """One new register row that matched an existing transaction."""

    row: int
    row_display: int
    description: str
    truck_number: str
    item: str
    amount: float
    amount_label: str
    existing: Transaction
    date_label: str = ""
    # Cream-row badge in the dialog — "Existing" (DB) or "Same table".
    existing_label: str = "Existing"


def dup_source_kind(item: DuplicateReviewItem) -> DupSourceKind:
    """Classify a review pair as a DB hit or a same-table twin."""
    if (item.existing_label or "").strip().casefold() == "same table":
        return "table"
    return "db"


def truck_facet_label(truck_number: str | None) -> str:
    text = (truck_number or "").strip()
    return text or NO_TRUCK_LABEL


def item_facet_label(item: str | None) -> str:
    text = (item or "").strip()
    if not text or text == "—":
        return EMPTY_ITEM_LABEL
    return text


def description_facet_label(description: str | None) -> str:
    text = (description or "").strip()
    if not text or text == "—":
        return EMPTY_DESC_LABEL
    return text


def group_counts(
    items: Sequence[DuplicateReviewItem],
) -> Dict[str, Counter]:
    """Counters for source / truck / item / description facets."""
    source = Counter()
    trucks = Counter()
    items_c = Counter()
    descs = Counter()
    for item in items:
        kind = dup_source_kind(item)
        source[kind] += 1
        trucks[truck_facet_label(item.truck_number)] += 1
        items_c[item_facet_label(item.item)] += 1
        descs[description_facet_label(item.description)] += 1
    return {
        "source": source,
        "truck": trucks,
        "item": items_c,
        "description": descs,
    }


def filter_duplicate_items(
    items: Sequence[DuplicateReviewItem],
    *,
    source: DupSourceFilter = "all",
    truck: str | None = None,
    item: str | None = None,
    description: str | None = None,
) -> List[DuplicateReviewItem]:
    """Filter review pairs by source kind and truck/item/description facets."""
    out: List[DuplicateReviewItem] = []
    for entry in items:
        if source != "all" and dup_source_kind(entry) != source:
            continue
        if truck is not None and truck_facet_label(entry.truck_number) != truck:
            continue
        if item is not None and item_facet_label(entry.item) != item:
            continue
        if (
            description is not None
            and description_facet_label(entry.description) != description
        ):
            continue
        out.append(entry)
    return out


LookbackOverrideKind = Literal["item", "description"]
LookbackOverrides = Dict[str, Dict[str, int]]


def clamp_lookback_days(days: int) -> int:
    return max(1, min(365, int(days or 1)))


def empty_lookback_overrides() -> LookbackOverrides:
    return {"item": {}, "description": {}}


def normalize_lookback_overrides(raw: object) -> LookbackOverrides:
    """Coerce settings / dialog payload into ``{item: {key: days}, description: {...}}``."""
    out = empty_lookback_overrides()
    if not isinstance(raw, dict):
        return out
    for kind in ("item", "description"):
        bucket = raw.get(kind)
        if not isinstance(bucket, dict):
            continue
        cleaned: Dict[str, int] = {}
        for label, days in bucket.items():
            key = str(label or "").strip().casefold()
            if not key:
                continue
            try:
                cleaned[key] = clamp_lookback_days(int(days))
            except (TypeError, ValueError):
                continue
        out[kind] = cleaned
    return out


def effective_lookback_days(
    *,
    item: str = "",
    description: str = "",
    default_days: int,
    overrides: object = None,
) -> int:
    """Resolve lookback for one candidate.

    Priority: description override → item override → global default.
    """
    default = clamp_lookback_days(default_days)
    maps = normalize_lookback_overrides(overrides)
    desc_key = description_facet_label(description).casefold()
    item_key = item_facet_label(item).casefold()
    if desc_key in maps["description"]:
        return maps["description"][desc_key]
    if item_key in maps["item"]:
        return maps["item"][item_key]
    return default


def lookback_days_from_unit(value: int, unit: str) -> int:
    """Convert dialog lookback (days|months) to API ``days`` (1–365)."""
    n = max(1, int(value or 1))
    u = (unit or "days").strip().casefold()
    if u.startswith("month"):
        return max(1, min(365, n * 30))
    return max(1, min(365, n))


def convert_lookback_value(value: int, *, from_unit: str, to_unit: str) -> int:
    """Re-express a lookback spin value when the user switches Days ↔ Months.

    Days → Months uses ceil(days/30) so a short window does not collapse to 0.
    Months → Days uses ×30 (capped at 365). Same unit returns the value unchanged
    (still clamped to the unit's spin range).
    """
    src = (from_unit or "days").strip().casefold()
    dst = (to_unit or "days").strip().casefold()
    src_months = src.startswith("month")
    dst_months = dst.startswith("month")
    n = max(1, int(value or 1))
    if src_months == dst_months:
        return max(1, min(12 if dst_months else 365, n))
    if dst_months:
        # days → months
        return max(1, min(12, (n + 29) // 30))
    # months → days
    return max(1, min(365, n * 30))


def format_lookback_label(days: int, *, unit: str = "days", value: int | None = None) -> str:
    """Human window label for titles — e.g. ``last 5 days`` / ``last 2 months``."""
    u = (unit or "days").strip().casefold()
    if u.startswith("month"):
        n = int(value) if value is not None else max(1, (int(days) + 29) // 30)
        return f"last {n} month" if n == 1 else f"last {n} months"
    n = int(value) if value is not None else int(days)
    return f"last {n} day" if n == 1 else f"last {n} days"


def soft_dup_key(
    *,
    truck_number: str = "",
    amount: float = 0.0,
    item: str = "",
    description: str = "",
) -> SoftDupKey:
    """Case-insensitive soft-dup fingerprint (truck + amount + item + description)."""
    try:
        amt = float(amount or 0.0)
    except (TypeError, ValueError):
        amt = 0.0
    return (
        (truck_number or "").strip().casefold(),
        amt,
        (item or "").strip().casefold(),
        (description or "").strip().casefold(),
    )


def soft_dup_key_for_tx(tx: Transaction) -> SoftDupKey:
    return soft_dup_key(
        truck_number=tx.truck_number or "",
        amount=tx.amount,
        item=tx.item or "",
        description=tx.description or "",
    )


def soft_dup_key_is_meaningful(key: SoftDupKey) -> bool:
    """Skip empty twin drafts — need description or item (or truck + amount)."""
    truck, amount, item, description = key
    if description or item:
        return True
    return bool(truck) and amount != 0.0


def within_table_duplicate_items(
    draft_inserts: Sequence[tuple[int, Transaction]],
    *,
    already_flagged_rows: Optional[Set[int]] = None,
) -> List[DuplicateReviewItem]:
    """Flag later draft rows that match an earlier draft in the same save.

    The first occurrence of each soft-dup key is treated as the original; later
    identical rows become review items (cream row = that earlier draft). Rows
    already flagged by the DB soft-dup scan are left alone.
    """
    flagged = set(already_flagged_rows or ())
    first_by_key: dict[SoftDupKey, tuple[int, Transaction]] = {}
    items: List[DuplicateReviewItem] = []

    for row, tx in draft_inserts:
        key = soft_dup_key_for_tx(tx)
        if not soft_dup_key_is_meaningful(key):
            continue
        prior = first_by_key.get(key)
        if prior is None:
            first_by_key[key] = (row, tx)
            continue
        if row in flagged:
            continue
        _prior_row, prior_tx = prior
        items.append(
            DuplicateReviewItem(
                row=row,
                row_display=row + 1,
                description=tx.description or "—",
                truck_number=tx.truck_number or "",
                item=tx.item or "",
                amount=float(tx.amount or 0.0),
                amount_label=format_amount_label(tx),
                existing=prior_tx,
                date_label=format_new_date(tx.date),
                existing_label="Same table",
            )
        )
    return items


def format_amount_label(tx: Transaction) -> str:
    """Human-readable amount for duplicate review rows."""
    tzs_show, usd_show = tx.money_parts()
    if tzs_show and usd_show:
        return f"TZS {tzs_show:,.0f} / USD {usd_show:,.2f}"
    if tzs_show:
        return f"TZS {tzs_show:,.0f}"
    if usd_show:
        return f"USD {usd_show:,.2f}"
    return "—"


def format_existing_date(dt: Optional[datetime]) -> str:
    if dt is None:
        return "—"
    try:
        return dt.strftime("%d %b %Y")
    except Exception:
        return str(dt)


def format_new_date(dt: Optional[datetime]) -> str:
    """Date label for the candidate (new) row in the duplicate dialog."""
    return format_existing_date(dt)


def rows_to_save_with_duplicate_flags(
    duplicate_items: List[DuplicateReviewItem],
    save_anyway_rows: set[int],
) -> dict[int, bool]:
    """Map grid row index → ``possible_duplicate`` for duplicate rows being saved."""
    return {
        item.row: item.row in save_anyway_rows
        for item in duplicate_items
        if item.row in save_anyway_rows
    }
