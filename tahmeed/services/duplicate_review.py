"""Helpers for batch duplicate review during register save."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from tahmeed.models.transaction import Transaction


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
