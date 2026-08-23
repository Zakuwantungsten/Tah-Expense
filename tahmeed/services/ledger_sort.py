"""Server-side ledger sort helpers (Mongo field whitelists + tie-breakers)."""

from __future__ import annotations

from typing import Iterable, List, Optional, Sequence, Set, Tuple

# Mongo .sort clauses: [(field, direction), ...]
SortClauses = List[Tuple[str, int]]

# Truck / reg / plate fields that use truck_sort_key then raw field.
TRUCK_SORT_FIELDS: Set[str] = frozenset({
    "truck_number",
    "truck_no",
    "vehicle_reg",
    "vehicle_no",
    "plate_num",
    "truck_reg",
    "reg_no",
    "truck",
})


def ledger_sort_clauses(
    sort_field: str,
    sort_asc: bool,
    allowed: Set[str],
    *,
    default: str = "date",
    tie_break: Sequence[Tuple[str, int]] = (("created_at", -1),),
) -> SortClauses:
    """Build a whitelisted Mongo sort list with stable tie-breakers."""
    field = sort_field if sort_field in allowed else default
    direction = 1 if sort_asc else -1
    clauses: SortClauses = []
    if field in TRUCK_SORT_FIELDS:
        clauses.append(("truck_sort_key", direction))
    clauses.append((field, direction))
    for tb_field, tb_dir in tie_break:
        if tb_field not in {c[0] for c in clauses}:
            clauses.append((tb_field, tb_dir))
    return clauses


def default_desc_clauses(
    sort_field: str,
    sort_asc: bool,
    allowed: Set[str],
    *,
    default: str,
    tie_break: Sequence[Tuple[str, int]],
) -> SortClauses:
    """Like ledger_sort_clauses but default sort is descending (newest first)."""
    field = sort_field if sort_field in allowed else default
    direction = 1 if sort_asc else -1
    clauses: SortClauses = []
    if field in TRUCK_SORT_FIELDS:
        clauses.append(("truck_sort_key", direction))
    clauses.append((field, direction))
    for tb_field, tb_dir in tie_break:
        if tb_field not in {c[0] for c in clauses}:
            clauses.append((tb_field, tb_dir))
    return clauses
