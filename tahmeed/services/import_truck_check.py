"""Fleet validation gate for accountant Excel/CSV imports.

Scans rows for truck fields, normalizes known ``T### XXX`` shapes against the
fleet registry, and returns issues for the correction dialog. Odd feed-specific
formats (e.g. ``t999hej/number``) stay ``invalid_format`` until per-feed
normalizers are added later.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

from tahmeed.services.truck_format import (
    DEFAULT_PLACE_LABELS,
    normalize_truck_number,
    try_match_fleet,
)
from tahmeed.ui.dialogs.truck_correction_dialog import TruckIssue

# feed_type / expense_type → truck column on the import record
FEED_TRUCK_FIELDS: Dict[str, str] = {
    "daily_register": "truck_number",
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
    "rpa_schedule": "truck_and_trailer",
    "bonds": "truck_and_trailer",
    "sm_burhani": "truck_and_trailer",
}

# How re-uploaded skipped rows should be persisted
FEED_SAVE_TARGET: Dict[str, str] = {
    "congo_expenses": "separate_expenses",
    "ahmed_kimvi": "separate_expenses",
    "rpa_schedule": "reconciliation",
    "bonds": "reconciliation",
    "sm_burhani": "reconciliation",
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


_COMBO_SPLIT = re.compile(r"\s*(?:[/,&|]|\bAND\b)\s*", re.IGNORECASE)


def split_truck_combo_cell(raw: str) -> Optional[List[str]]:
    """Split ``T688 EAF / T123 TRA`` into parts; None if it is a single plate.

    Requires every part to look like a registration (letters and digits) so
    values like ``weird/99`` stay one invalid token, not a combo.
    """
    text = str(raw or "").strip()
    if not text:
        return None
    parts = [p.strip() for p in _COMBO_SPLIT.split(text) if str(p).strip()]
    if len(parts) < 2:
        return None
    if not all(re.search(r"\d", p) and re.search(r"[A-Za-z]", p) for p in parts):
        return None
    return parts


def split_leading_truck(raw: str) -> Optional[Tuple[str, str]]:
    """Split SM Burhani ``T469EKZ/T689ELK`` into ``(truck, suffix)``.

    Suffix keeps the original separator and trailer (``/T689ELK``). Returns
    None when the cell is not a truck/trailer combo.
    """
    text = str(raw or "").strip()
    if not text:
        return None
    match = _COMBO_SPLIT.search(text)
    if not match:
        return None
    truck = text[: match.start()].strip()
    rest = text[match.end() :].strip()
    if not truck or not rest:
        return None
    if not (re.search(r"\d", truck) and re.search(r"[A-Za-z]", truck)):
        return None
    if not (re.search(r"\d", rest) and re.search(r"[A-Za-z]", rest)):
        return None
    return truck, text[match.start() :]


def _plate_flex_body(compact: str) -> str:
    """Allow optional spaces between each character of a compact plate."""
    return r"\s*".join(re.escape(ch) for ch in compact)


def truck_and_trailer_search_regex(truck: str) -> Optional[str]:
    """Regex that matches a truck at the start of a ``truck/trailer`` cell.

    ``T469 EKZ`` matches ``T469EKZ/T689ELK``, ``T469 EKZ / T689 ELK``, and
    a plain ``T469 EKZ`` cell. Spaces in the stored plate are ignored.
    """
    return truck_exact_match_regex(truck)


def truck_exact_match_regex(value: str) -> Optional[str]:
    """Space-insensitive exact plate match, optionally followed by ``/trailer``.

    ``T103 DVL`` matches ``T103DVL``, ``T103 DVL``, and ``T103DVL/T689ELK``.
    It does not match ``T102 DVL`` or ``T1030 DVL``.
    """
    raw = str(value or "").strip()
    if not raw:
        return None
    compact = re.sub(r"\s+", "", raw.upper())
    if not compact:
        return None
    norm = normalize_truck_number(raw, allowed_labels=())
    if norm.status in ("ok", "normalized"):
        compact = norm.value.replace(" ", "")
    return rf"^{_plate_flex_body(compact)}(?=\s*/|$)"


def truck_field_search_regex(query: str) -> Optional[str]:
    """Plate-shaped search regex, or ``None`` so callers can use text search.

    Full plates (``T103 DVL``) match only that plate. A ``T103`` / digits-only
    query matches that number with an optional suffix, not ``T1030``.
    """
    raw = str(query or "").strip()
    if not raw:
        return None
    norm = normalize_truck_number(raw, allowed_labels=())
    if norm.status in ("ok", "normalized"):
        return truck_exact_match_regex(norm.value)
    digits = re.match(r"^T?\s*(\d+)$", raw, re.I)
    if digits:
        body = _plate_flex_body(digits.group(1))
        return rf"^T?\s*{body}(?=\s*[A-Z]|\s*/|$)"
    return None


def _match_one_truck(
    raw: str,
    fleet: Set[str],
    labels: List[str],
) -> Tuple[str, str]:
    """Return ``(status, value)`` for one plate/label token."""
    if is_blank_truck_value(raw):
        return "empty", ""
    norm = normalize_truck_number(raw, allowed_labels=labels)
    if norm.status == "empty":
        return "empty", ""
    if norm.status == "place_label":
        return "place_label", norm.value
    if norm.status in ("ok", "normalized"):
        matched = try_match_fleet(norm.value, fleet)
        if matched is not None:
            return "ok", matched
        return "not_in_registry", raw
    matched = try_match_fleet(raw, fleet)
    if matched is not None:
        return "ok", matched
    return "invalid_format", raw


def join_combo_parts(parts: List[str]) -> str:
    """Join canonical plates as ``T843 EKT/T691 ELK`` (Excel-style slash)."""
    return "/".join(p.strip() for p in parts if str(p).strip())


def attach_combo_suffix(truck: str, suffix: str) -> str:
    """Reattach ``/T691ELK`` (or two-trailer rest) after editing the truck."""
    truck = str(truck or "").strip()
    suffix = str(suffix or "")
    if not suffix:
        return truck
    return f"{truck}{suffix}"


def combo_suffix_of(raw: str) -> str:
    """Trailer rest of a combo cell, including the separator; else ``""``."""
    split = split_leading_truck(raw)
    return split[1] if split else ""


def leading_truck_of(raw: str) -> str:
    """First plate of a ``truck/trailer`` cell, or the whole cell."""
    split = split_leading_truck(raw)
    return split[0] if split else str(raw or "").strip()


def is_two_trailer_cell(raw: str) -> bool:
    """True for ``truck/trailer/trailer`` (or ``truck/trailer & trailer``) cells."""
    parts = split_truck_combo_cell(raw)
    return bool(parts) and len(parts) >= 3


def _pretty_combo_part(
    part: str,
    fleet: Set[str],
    labels: List[str],
) -> str:
    """Canonical plate when known; otherwise format-normalize without gating."""
    status, value = _match_one_truck(part, fleet, labels)
    if status in ("ok", "place_label"):
        return value
    norm = normalize_truck_number(part, allowed_labels=labels)
    if norm.status in ("ok", "normalized", "place_label"):
        return norm.value
    return part


def resolve_combo_parts(
    raw: str,
    fleet: Set[str],
    labels: Optional[Iterable[str]] = None,
) -> Tuple[str, str, List[str]]:
    """Check the leading truck only. Returns ``(status, value, parts)``.

    One unknown trailer does not fail the gate. Two or more trailers
    (``T724CPQ/T631DZX/T632DZX``) are flagged as ``invalid_format`` so the
    importer can confirm the truck. ``parts`` is the original split list
    (empty when the cell is not a combo).
    """
    allowed = list(labels) if labels is not None else list(DEFAULT_PLACE_LABELS)
    text = str(raw or "").strip()
    parts = split_truck_combo_cell(text)
    if not parts:
        status, value = _match_one_truck(text, fleet, allowed)
        return status, value, []
    truck_status, truck_value = _match_one_truck(parts[0], fleet, allowed)
    rest = [_pretty_combo_part(p, fleet, allowed) for p in parts[1:]]
    if is_two_trailer_cell(text):
        if truck_status not in ("ok", "place_label"):
            return truck_status, text, parts
        return "invalid_format", text, parts
    if truck_status in ("ok", "place_label"):
        return "ok", join_combo_parts([truck_value] + rest), parts
    return truck_status, text, parts


def resolve_truck_cell(
    raw: str,
    fleet: Set[str],
    labels: Optional[Iterable[str]] = None,
) -> Tuple[str, str]:
    """Match a truck cell against the fleet, including ``truck/trailer`` combos."""
    text = str(raw or "").strip()
    if is_blank_truck_value(text):
        return "empty", ""
    status, value, _parts = resolve_combo_parts(text, fleet, labels)
    return status, value


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

    ``TRUCK & TRAILER`` cells (``T469EKZ/T689ELK``) are split; only the
    leading truck is gated. One missing trailer does not flag the row.
    Two or more trailers are flagged as irregular for review.

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
        status, value, _parts = resolve_combo_parts(raw, fleet, labels)
        if status == "empty":
            row[truck_field] = ""
            result.empty_count += 1
            continue
        if status in ("ok", "place_label"):
            row[truck_field] = value
            result.ok_count += 1
            continue
        result.issues.append(TruckIssue(
            row=i,
            original=raw,
            kind=status if status in ("invalid_format", "not_in_registry") else "not_in_registry",
            combo_suffix=combo_suffix_of(raw),
        ))
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
            # 1-based position among data rows in the uploaded file
            "source_row": int(iss.row) + 1,
            "reason": iss.kind,
            "original_truck": iss.original,
            "save_target": save_target_for(feed_key),
            "record": payload,
        })
    return docs
