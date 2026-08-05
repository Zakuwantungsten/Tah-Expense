"""Fleet validation gate for accountant Excel/CSV imports.

Scans rows for truck fields, normalizes known ``T### XXX`` shapes against the
fleet registry, and returns issues for the correction dialog. Odd feed-specific
formats (e.g. ``t999hej/number``) stay ``invalid_format`` until per-feed
normalizers are added later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Set

from tahmeed.services.truck_format import (
    DEFAULT_PLACE_LABELS,
    normalize_truck_number,
    try_match_fleet,
)
from tahmeed.ui.dialogs.truck_correction_dialog import TruckIssue

# feed_type / expense_type → truck column on the import record
FEED_TRUCK_FIELDS: Dict[str, str] = {
    "toll_plaza": "vehicle_reg",
    "parking_congo": "vehicle_no",
    "congo_expenses": "truck_no",
    "ahmed_kimvi": "truck_no",
    "zambia_parking": "plate_num",
    "afritrack": "truck",
    "comesa": "truck_reg",
    "third_party": "reg_no",
    "rahntech": "truck_number",
    "diesel_infinity": "truck_no",
    "diesel_lake_zambia": "truck_no",
    "diesel_lake_tunduma": "truck_no",
    "diesel_gbp": "truck_no",
}

# How re-uploaded skipped rows should be persisted
FEED_SAVE_TARGET: Dict[str, str] = {
    "congo_expenses": "separate_expenses",
    "ahmed_kimvi": "separate_expenses",
}

# Ledger placeholders that are not real truck numbers (Parking Congo deposits, etc.)
_BLANK_TRUCK_TOKENS = frozenset({
    "-", "–", "—", "−", ".", "n/a", "na", "none", "nil", "null", "n.a.",
})


@dataclass
class ImportTruckScanResult:
    """Outcome of scanning a batch before save."""

    issues: List[TruckIssue] = field(default_factory=list)
    ok_count: int = 0
    empty_count: int = 0
    deposit_count: int = 0


def truck_field_for(feed_key: str) -> Optional[str]:
    return FEED_TRUCK_FIELDS.get(feed_key)


def save_target_for(feed_key: str) -> str:
    return FEED_SAVE_TARGET.get(feed_key, "imported_feeds")


def is_deposit_transaction(transaction_type: object) -> bool:
    """True when Type/transaction_type is a Parking Congo (or similar) deposit."""
    return str(transaction_type or "").strip().lower() == "deposit"


def is_blank_truck_value(raw: object) -> bool:
    """True for empty cells and ledger placeholders like ``-`` / ``N/A``."""
    s = str(raw or "").strip()
    if not s:
        return True
    return s.lower() in _BLANK_TRUCK_TOKENS


def mark_parking_congo_deposit(row: dict, truck_field: str = "vehicle_no") -> None:
    """Tag a deposit row and clear placeholder vehicle / direction cells."""
    row["is_deposit"] = True
    if is_blank_truck_value(row.get(truck_field)):
        row[truck_field] = ""
    for key in ("direction", "gate_in"):
        if is_blank_truck_value(row.get(key)):
            row[key] = ""


def scan_import_trucks(
    rows: List[dict],
    truck_field: str,
    fleet: Set[str],
    *,
    allowed_labels: Optional[Iterable[str]] = None,
    progress: Optional[Callable[[int, int], None]] = None,
) -> ImportTruckScanResult:
    """Normalize / match trucks in place; collect issues for unknowns.

    Rows that match the fleet (or an allowed place label) are rewritten with the
    canonical value. Empty truck cells are left alone and not flagged.

    Parking Congo **Deposit** rows (and blank/placeholder plates like ``-``) are
    never flagged as not-in-registry — deposits are account credits, not trucks.
    """
    labels = list(allowed_labels) if allowed_labels is not None else list(DEFAULT_PLACE_LABELS)
    result = ImportTruckScanResult()
    total = len(rows)
    for i, row in enumerate(rows):
        if progress is not None and (i % 20 == 0 or i + 1 == total):
            progress(i + 1, total)

        # Deposits have no real vehicle — import without fleet gate noise
        if is_deposit_transaction(row.get("transaction_type")):
            mark_parking_congo_deposit(row, truck_field)
            result.deposit_count += 1
            result.empty_count += 1
            continue

        raw = str(row.get(truck_field, "") or "").strip()
        if is_blank_truck_value(raw):
            row[truck_field] = ""
            result.empty_count += 1
            continue
        norm = normalize_truck_number(raw, allowed_labels=labels)
        if norm.status == "empty":
            row[truck_field] = ""
            result.empty_count += 1
            continue
        if norm.status == "place_label":
            row[truck_field] = norm.value
            result.ok_count += 1
            continue
        if norm.status in ("ok", "normalized"):
            matched = try_match_fleet(norm.value, fleet)
            if matched is not None:
                row[truck_field] = matched
                result.ok_count += 1
                continue
            result.issues.append(
                TruckIssue(row=i, original=raw, kind="not_in_registry")
            )
            continue
        # Invalid / unrecognized format — flag for Skip / Allow anyway
        result.issues.append(
            TruckIssue(row=i, original=raw, kind="invalid_format")
        )
    return result


def apply_truck_resolutions(
    rows: List[dict],
    truck_field: str,
    issues: List[TruckIssue],
) -> tuple[List[dict], List[tuple[dict, TruckIssue]]]:
    """Split rows into (to_save, skipped_pairs).

    Each skipped pair is ``(row_dict, issue)``. Callers persist them with the
    original import ``upload_id`` so a later re-upload joins that batch.
    """
    by_row = {iss.row: iss for iss in issues}
    to_save: List[dict] = []
    skipped: List[tuple[dict, TruckIssue]] = []

    for i, row in enumerate(rows):
        iss = by_row.get(i)
        if iss is None:
            to_save.append(row)
            continue
        if iss.omit_row or (iss.skip and not iss.corrected):
            value = (iss.corrected or iss.original or "").strip()
            if value:
                row[truck_field] = value
            skipped.append((row, iss))
            continue
        if iss.corrected:
            row[truck_field] = iss.corrected
        if iss.allow_anyway:
            row["fleet_override"] = True
        to_save.append(row)

    return to_save, skipped


def skipped_docs_from_pairs(
    pairs: List[tuple[dict, TruckIssue]],
    *,
    feed_key: str,
    truck_field: str,
    target_upload_id: str,
    source_filename: str = "",
    sheet_label: str = "",
) -> List[dict]:
    """Build documents for the skipped_import_rows collection."""
    docs: List[dict] = []
    for row, iss in pairs:
        payload = dict(row)
        payload.pop("_raw", None)
        payload["upload_id"] = target_upload_id
        if source_filename and not payload.get("source_filename"):
            payload["source_filename"] = source_filename
        if sheet_label and not payload.get("sheet_label"):
            payload["sheet_label"] = sheet_label
        docs.append({
            "feed_key": feed_key,
            "truck_field": truck_field,
            "truck_value": str(payload.get(truck_field, "") or ""),
            "target_upload_id": target_upload_id,
            "source_filename": source_filename or payload.get("source_filename", ""),
            "sheet_label": sheet_label or payload.get("sheet_label", ""),
            "reason": iss.kind,
            "original_truck": iss.original,
            "save_target": save_target_for(feed_key),
            "record": payload,
        })
    return docs
