"""Validate an import session: Item + Payee per description, trucks per row.

Description → Item/Payee comes from the session's temporary mappings and/or
the global Description Maps (session options decide which wins). Trucks are
matched against the fleet's trucks and trailers after the session's truck
corrections are applied. A day is Ready when every non-ignored row resolves.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, Iterable, List, Optional, Set, Tuple

from bson import ObjectId

from tahmeed.models.description_mapping import DescriptionMapping
from tahmeed.services.description_mapping_service import normalize_description
from tahmeed.services.import_session_parser import StagedRow
from tahmeed.services.import_session_service import ImportSession, TempMapping, truck_key
from tahmeed.services.import_truck_check import is_blank_truck_value, resolve_truck_cell
from tahmeed.services.truck_format import DEFAULT_PLACE_LABELS, merge_allowed_labels

ISSUE_UNMAPPED = "unmapped"
ISSUE_BAD_ITEM = "bad_item"
ISSUE_BAD_PAYEE = "bad_payee"
ISSUE_INVALID_TRUCK = "invalid_truck"

DAY_READY = "Ready"
DAY_BLOCKED = "Blocked"
DAY_PUSHED = "Pushed"

KIND_UNMAPPED = "Unmapped"
KIND_BAD_MAP = "Bad map"
KIND_IGNORED = "Ignored"

SOURCE_TEMP = "temp"
SOURCE_GLOBAL = "global"


def _norm_name(value: str) -> str:
    return " ".join((value or "").strip().upper().split())


def compact_key(value: str) -> str:
    return "".join(ch for ch in (value or "").upper() if ch.isalnum())


def rank_name_matches(query: str, names: Iterable[str], limit: int = 50) -> List[str]:
    """Live-search ranking (best first).

    0 exact ignoring spaces · 1 starts with query · 2 every token starts a word ·
    3 all tokens anywhere · 4 compact contains · 5 same ``T###`` plate prefix.
    """
    pool = sorted(set(names), key=str.upper)
    needle = _norm_name(query)
    if not needle:
        return pool[:limit]
    compact = compact_key(needle)
    tokens = needle.split()
    plate = re.match(r"(T\d{2,4})", compact)
    plate_prefix = plate.group(1) if plate else ""

    scored: List[Tuple[int, int, str]] = []
    for name in pool:
        upper = name.upper()
        cand = compact_key(name)
        words = [w for w in re.split(r"[^\w]+", upper) if w]
        if compact and compact == cand:
            rank = 0
        elif upper.startswith(needle) or (compact and cand.startswith(compact)):
            rank = 1
        elif all(any(w.startswith(t) for w in words) for t in tokens):
            rank = 2
        elif all(t in upper for t in tokens):
            rank = 3
        elif compact and compact in cand:
            rank = 4
        elif plate_prefix and cand.startswith(plate_prefix):
            rank = 5
        else:
            continue
        pos = upper.find(tokens[0])
        scored.append((rank, pos if pos >= 0 else 9999, name))
    scored.sort(key=lambda item: (item[0], item[1], item[2].upper()))
    return [name for _, _, name in scored[:limit]]


def rank_truck_matches(query: str, names: Iterable[str], limit: int = 50) -> List[str]:
    """Like ``rank_name_matches``; ``641 EQC`` also finds plates typed without their T."""
    matches = rank_name_matches(query, names, limit)
    text = _norm_name(query)
    if text[:1].isdigit():
        matches += [
            name for name in rank_name_matches("T" + text, names, limit) if name not in matches
        ]
    return matches[:limit]


@dataclass(frozen=True)
class ItemRef:
    id: Optional[ObjectId]
    name: str


class ItemIndex:
    """Items (QuickBooks accounts) by full name, compact name and leaf name."""

    _PREFERRED_PARENT = "DIRECT COSTS:"

    def __init__(self, items: Iterable[ItemRef]) -> None:
        self._by_name: Dict[str, ItemRef] = {}
        self._by_compact: Dict[str, ItemRef] = {}
        self._by_id: Dict[str, ItemRef] = {}
        self._by_leaf: Dict[str, List[ItemRef]] = defaultdict(list)
        for item in items:
            self.add(item)

    def add(self, item: ItemRef) -> Optional[ItemRef]:
        """Index one item (e.g. just created); returns the canonical ref."""
        name = _norm_name(item.name)
        if not name:
            return None
        if name in self._by_name:
            existing = self._by_name[name]
            if item.id is not None:
                self._by_id.setdefault(str(item.id), ItemRef(item.id, name))
            return existing
        ref = ItemRef(item.id, name)
        self._by_name[name] = ref
        self._by_compact.setdefault(compact_key(name), ref)
        if item.id is not None:
            self._by_id[str(item.id)] = ref
        self._by_leaf[self._leaf(name)].append(ref)
        return ref

    @staticmethod
    def _leaf(name: str) -> str:
        return name.rsplit(":", 1)[-1].strip()

    def names(self) -> List[str]:
        return sorted(self._by_name)

    def by_id(self, item_id) -> Optional[ItemRef]:
        return self._by_id.get(str(item_id)) if item_id is not None else None

    def resolve(self, value: str) -> Optional[ItemRef]:
        name = _norm_name(value)
        if not name:
            return None
        hit = self._by_name.get(name) or self._by_compact.get(compact_key(name))
        if hit is not None:
            return hit
        leaf = self._leaf(name)
        if leaf in self._by_name:
            return self._by_name[leaf]
        candidates = self._by_leaf.get(leaf) or []
        if len(candidates) == 1:
            return candidates[0]
        suffix = [c for c in candidates if c.name.endswith(name)]
        if len(suffix) == 1:
            return suffix[0]
        preferred = [c for c in candidates if c.name.startswith(self._PREFERRED_PARENT)]
        if len(preferred) == 1:
            return preferred[0]
        return None


class NameIndex:
    """Canonical names matched exactly or ignoring spaces/punctuation."""

    def __init__(self, names: Iterable[str]) -> None:
        self._by_name: Dict[str, str] = {}
        self._by_compact: Dict[str, str] = {}
        for raw in names:
            self.add(raw)

    def add(self, raw: str) -> Optional[str]:
        name = _norm_name(raw)
        if not name:
            return None
        self._by_name.setdefault(name, name)
        self._by_compact.setdefault(compact_key(name), name)
        return self._by_name[name]

    def names(self) -> List[str]:
        return sorted(self._by_name)

    def resolve(self, value: str) -> Optional[str]:
        name = _norm_name(value)
        if not name:
            return None
        return self._by_name.get(name) or self._by_compact.get(compact_key(name))


@dataclass
class ImportReferences:
    items: ItemIndex
    payees: NameIndex
    fleet: Set[str]
    place_labels: List[str] = field(default_factory=lambda: list(DEFAULT_PLACE_LABELS))
    global_mappings: Dict[str, DescriptionMapping] = field(default_factory=dict)

    def fleet_names(self) -> List[str]:
        return sorted(self.fleet)


def _fleet_numbers(documents: object) -> List[str]:
    if not isinstance(documents, list):
        return []
    return [
        str(doc.get("number") or "").strip().upper()
        for doc in documents
        if isinstance(doc, dict) and doc.get("number")
    ]


def references_from_snapshot(
    snapshot: dict,
    global_mappings: Optional[Dict[str, DescriptionMapping]] = None,
) -> ImportReferences:
    """Build references from a catalog snapshot. Fleet = trucks, trailers, motorcycles & cars."""
    items = [
        ItemRef(doc.get("_id") or doc.get("id"), str(doc.get("name") or ""))
        for doc in snapshot.get("categories") or []
        if isinstance(doc, dict) and doc.get("name") and doc.get("active", True) is not False
    ]
    payees = [
        str(doc.get("name") or "") if isinstance(doc, dict) else str(doc or "")
        for doc in snapshot.get("payees") or []
    ]
    fleet = {
        number
        for kind in ("trucks", "trailers", "motor_vehicles")
        for number in _fleet_numbers(snapshot.get(kind))
    }
    settings = snapshot.get("settings") if isinstance(snapshot.get("settings"), dict) else {}
    extra_labels = snapshot.get("allowed_truck_labels") or settings.get("allowed_truck_labels") or []
    return ImportReferences(
        items=ItemIndex(items),
        payees=NameIndex(payees),
        fleet=fleet,
        place_labels=sorted(merge_allowed_labels(DEFAULT_PLACE_LABELS, extra_labels)),
        global_mappings=dict(global_mappings or {}),
    )


async def load_import_references(*, refresh: bool = False) -> ImportReferences:
    from tahmeed.services.api_models import desktop_document
    from tahmeed.services.catalog_service import fetch_catalog_snapshot
    from tahmeed.services.description_mapping_service import (
        ensure_description_mapping_cache,
    )

    snapshot = await fetch_catalog_snapshot(refresh=refresh)
    snapshot = dict(snapshot)
    snapshot["categories"] = [
        desktop_document(doc) for doc in snapshot.get("categories") or [] if isinstance(doc, dict)
    ]
    mappings = await ensure_description_mapping_cache()
    return references_from_snapshot(snapshot, mappings)


@dataclass
class RowResolution:
    item: str = ""
    category_id: Optional[ObjectId] = None
    payee: str = ""
    mapping_source: str = ""
    truck: str = ""
    truck_corrected: bool = False
    truck_cleared: bool = False
    ignored: bool = False
    issues: Tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.issues


@dataclass
class _MappingOutcome:
    item: str = ""
    category_id: Optional[ObjectId] = None
    payee: str = ""
    source: str = ""
    issues: Tuple[str, ...] = (ISSUE_UNMAPPED,)


def _temp_outcome(mapping: TempMapping, refs: ImportReferences) -> _MappingOutcome:
    item = refs.items.resolve(mapping.item)
    payee = refs.payees.resolve(mapping.payee)
    issues = []
    if item is None:
        issues.append(ISSUE_BAD_ITEM)
    if payee is None:
        issues.append(ISSUE_BAD_PAYEE)
    return _MappingOutcome(
        item=item.name if item else mapping.item,
        category_id=item.id if item else None,
        payee=payee or mapping.payee,
        source=SOURCE_TEMP,
        issues=tuple(issues),
    )


def _global_outcome(mapping: DescriptionMapping, refs: ImportReferences) -> _MappingOutcome:
    item = refs.items.by_id(mapping.category_id) or refs.items.resolve(mapping.category_name)
    payee = refs.payees.resolve(mapping.payee)
    issues = []
    if item is None:
        issues.append(ISSUE_BAD_ITEM)
    if payee is None:
        issues.append(ISSUE_BAD_PAYEE)
    return _MappingOutcome(
        item=item.name if item else _norm_name(mapping.category_name),
        category_id=item.id if item else mapping.category_id,
        payee=payee or _norm_name(mapping.payee),
        source=SOURCE_GLOBAL,
        issues=tuple(issues),
    )


def resolve_description(
    description: str,
    session: ImportSession,
    refs: ImportReferences,
) -> _MappingOutcome:
    """Preferred source first; fall back to the other when the first is a bad map."""
    key = normalize_description(description)
    temp = session.temp_mappings.get(key) if session.options.use_temp_mappings else None
    glob = refs.global_mappings.get(key)
    if session.options.prefer_temp_mappings:
        ordered = [(temp, _temp_outcome), (glob, _global_outcome)]
    else:
        ordered = [(glob, _global_outcome), (temp, _temp_outcome)]
    first_bad: Optional[_MappingOutcome] = None
    for mapping, evaluate in ordered:
        if mapping is None:
            continue
        outcome = evaluate(mapping, refs)
        if not outcome.issues:
            return outcome
        if first_bad is None:
            first_bad = outcome
    return first_bad or _MappingOutcome()


@dataclass(frozen=True)
class _TruckOutcome:
    truck: str
    corrected: bool
    cleared: bool
    invalid: bool


def resolve_truck(
    raw: str, refs: ImportReferences, fix: Optional[str] = None, *, allowed: bool = False
) -> _TruckOutcome:
    """The row's truck: its own fix when it has one, else the Excel value.

    ``allowed`` means the user chose to keep the fix even if it is not in the fleet.
    """
    corrected = fix is not None
    if corrected and not fix:
        return _TruckOutcome("", True, True, False)
    value = fix if corrected else raw
    if is_blank_truck_value(value):
        return _TruckOutcome("", corrected, False, False)
    status, resolved = resolve_truck_cell(value, refs.fleet, refs.place_labels)
    if status in ("ok", "place_label"):
        return _TruckOutcome(resolved, corrected, False, False)
    if status == "empty":
        return _TruckOutcome("", corrected, False, False)
    return _TruckOutcome(truck_key(value), corrected, False, not (corrected and allowed))


def is_fleet_truck(value: str, refs: ImportReferences) -> Optional[str]:
    """Canonical plate when ``value`` is one of our trucks / trailers (or a place label)."""
    status, resolved = resolve_truck_cell(value, refs.fleet, refs.place_labels)
    return resolved if status in ("ok", "place_label") and resolved else None


@dataclass
class DayResult:
    batch_date: date
    status: str
    row_count: int
    push_count: int
    total_tzs: float
    total_usd: float
    source_files: List[str]
    reasons: List[str] = field(default_factory=list)
    unmapped_count: int = 0
    bad_map_count: int = 0
    invalid_truck_count: int = 0
    ignored_count: int = 0

    @property
    def is_ready(self) -> bool:
        return self.status == DAY_READY


@dataclass
class DescriptionIssue:
    kind: str
    key: str
    description: str
    row_count: int
    day_count: int
    item: str = ""
    payee: str = ""
    detail: str = ""
    source: str = ""


ISSUE_LABELS: Dict[str, str] = {
    ISSUE_UNMAPPED: "Unmapped",
    ISSUE_BAD_ITEM: "Item not found",
    ISSUE_BAD_PAYEE: "Payee not found",
    ISSUE_INVALID_TRUCK: "Wrong truck",
}


def issue_text(resolution: "RowResolution") -> str:
    if resolution.ignored:
        return "Ignored"
    return ", ".join(ISSUE_LABELS.get(i, i) for i in resolution.issues)


@dataclass
class TruckRowIssue:
    """One Excel row whose truck is wrong, or that has its own truck fix."""

    row_id: str
    batch_date: date
    source_file: str
    excel_row: int
    description: str
    amount: float
    currency: str
    truck_raw: str
    fix: Optional[str]  # None = no fix; "" = no truck
    invalid: bool
    allowed: bool = False  # fix kept although it is not one of our trucks


@dataclass
class SessionValidation:
    rows: Dict[str, RowResolution]
    days: List[DayResult]
    _staged: List[StagedRow] = field(default_factory=list, repr=False)
    _refs: Optional[ImportReferences] = field(default=None, repr=False)

    def day(self, batch_date: date) -> Optional[DayResult]:
        for result in self.days:
            if result.batch_date == batch_date:
                return result
        return None

    @property
    def ready_count(self) -> int:
        return sum(1 for d in self.days if d.status == DAY_READY)

    @property
    def blocked_count(self) -> int:
        return sum(1 for d in self.days if d.status == DAY_BLOCKED)

    @property
    def pushed_count(self) -> int:
        return sum(1 for d in self.days if d.status == DAY_PUSHED)

    def ready_dates(self) -> List[date]:
        return [d.batch_date for d in self.days if d.status == DAY_READY]

    def _scoped(self, batch_date: Optional[date]) -> List[StagedRow]:
        if batch_date is None:
            return self._staged
        return [r for r in self._staged if r.batch_date == batch_date]

    def description_issues(self, batch_date: Optional[date] = None) -> List[DescriptionIssue]:
        """Unmapped first (most rows first), then bad maps, then ignored."""
        rows_by_key: Dict[str, int] = defaultdict(int)
        days_by_key: Dict[str, Set[date]] = defaultdict(set)
        sample: Dict[str, Tuple[StagedRow, RowResolution]] = {}
        for row in self._scoped(batch_date):
            res = self.rows[row.row_id]
            if not res.ignored and not ({ISSUE_UNMAPPED, ISSUE_BAD_ITEM, ISSUE_BAD_PAYEE} & set(res.issues)):
                continue
            key = normalize_description(row.description)
            rows_by_key[key] += 1
            days_by_key[key].add(row.batch_date)
            sample.setdefault(key, (row, res))

        out: List[DescriptionIssue] = []
        for key, (row, res) in sample.items():
            if res.ignored:
                kind, detail = KIND_IGNORED, "Not pushed"
            elif ISSUE_UNMAPPED in res.issues:
                kind, detail = KIND_UNMAPPED, ""
            else:
                problems = []
                if ISSUE_BAD_ITEM in res.issues:
                    problems.append(f"item not found: {res.item or '(blank)'}")
                if ISSUE_BAD_PAYEE in res.issues:
                    problems.append(f"payee not found: {res.payee or '(blank)'}")
                kind, detail = KIND_BAD_MAP, "; ".join(problems)
            out.append(
                DescriptionIssue(
                    kind=kind,
                    key=key,
                    description=row.description,
                    row_count=rows_by_key[key],
                    day_count=len(days_by_key[key]),
                    item="" if kind == KIND_UNMAPPED else res.item,
                    payee="" if kind == KIND_UNMAPPED else res.payee,
                    detail=detail,
                    source=res.mapping_source,
                )
            )
        order = {KIND_UNMAPPED: 0, KIND_BAD_MAP: 1, KIND_IGNORED: 2}
        out.sort(key=lambda i: (order[i.kind], -i.row_count, i.description))
        return out

    def truck_rows(
        self,
        batch_date: Optional[date] = None,
        fixes: Optional[Dict[str, str]] = None,
        allowed: Optional[Set[str]] = None,
    ) -> List[TruckRowIssue]:
        """Rows with a wrong truck first, then rows already fixed (file order)."""
        fixes = fixes or {}
        allowed = allowed or set()
        out: List[TruckRowIssue] = []
        for row in self._scoped(batch_date):
            res = self.rows[row.row_id]
            invalid = ISSUE_INVALID_TRUCK in res.issues
            fix = fixes.get(row.row_id)
            if res.ignored or not (invalid or fix is not None):
                continue
            out.append(
                TruckRowIssue(
                    row_id=row.row_id,
                    batch_date=row.batch_date,
                    source_file=row.source_file,
                    excel_row=row.excel_row,
                    description=row.description,
                    amount=row.amount,
                    currency=row.currency,
                    truck_raw=row.truck_raw,
                    fix=fix,
                    invalid=invalid,
                    allowed=bool(fix) and row.row_id in allowed,
                )
            )
        out.sort(key=lambda t: not t.invalid)
        return out


def validate_session(session: ImportSession, refs: ImportReferences) -> SessionValidation:
    mapping_cache: Dict[str, _MappingOutcome] = {}
    truck_cache: Dict[str, _TruckOutcome] = {}
    resolutions: Dict[str, RowResolution] = {}

    for row in session.rows:
        key = normalize_description(row.description)
        if key in session.ignored:
            resolutions[row.row_id] = RowResolution(ignored=True)
            continue
        mapping = mapping_cache.get(key)
        if mapping is None:
            mapping = resolve_description(row.description, session, refs)
            mapping_cache[key] = mapping
        fix = session.truck_fix_for(row.row_id)
        if fix is not None:
            truck = resolve_truck(
                row.truck_raw, refs, fix, allowed=session.truck_fix_allowed(row.row_id)
            )
        else:
            truck = truck_cache.get(row.truck_raw)
            if truck is None:
                truck = resolve_truck(row.truck_raw, refs)
                truck_cache[row.truck_raw] = truck
        issues = list(mapping.issues)
        if truck.invalid:
            issues.append(ISSUE_INVALID_TRUCK)
        resolutions[row.row_id] = RowResolution(
            item=mapping.item,
            category_id=mapping.category_id,
            payee=mapping.payee,
            mapping_source=mapping.source,
            truck=truck.truck,
            truck_corrected=truck.corrected,
            truck_cleared=truck.cleared,
            issues=tuple(issues),
        )

    days: List[DayResult] = []
    for batch_date, rows in session.days().items():
        days.append(_day_result(batch_date, rows, resolutions, session))
    return SessionValidation(rows=resolutions, days=days, _staged=list(session.rows), _refs=refs)


def _day_result(
    batch_date: date,
    rows: List[StagedRow],
    resolutions: Dict[str, RowResolution],
    session: ImportSession,
) -> DayResult:
    unmapped: Set[str] = set()
    bad_maps: Set[str] = set()
    wrong_trucks = 0
    ignored = 0
    for row in rows:
        res = resolutions[row.row_id]
        if res.ignored:
            ignored += 1
            continue
        key = normalize_description(row.description)
        if ISSUE_UNMAPPED in res.issues:
            unmapped.add(key)
        elif ISSUE_BAD_ITEM in res.issues or ISSUE_BAD_PAYEE in res.issues:
            bad_maps.add(key)
        if ISSUE_INVALID_TRUCK in res.issues:
            wrong_trucks += 1

    reasons: List[str] = []
    if unmapped:
        reasons.append(f"{len(unmapped)} unmapped description(s)")
    if bad_maps:
        reasons.append(f"{len(bad_maps)} bad mapping(s)")
    if wrong_trucks:
        reasons.append(f"{wrong_trucks} row(s) with a wrong truck")

    if session.is_day_pushed(batch_date):
        status = DAY_PUSHED
    else:
        status = DAY_BLOCKED if reasons else DAY_READY
    return DayResult(
        batch_date=batch_date,
        status=status,
        row_count=len(rows),
        push_count=len(rows) - ignored,
        total_tzs=sum(r.amount for r in rows if r.currency == "TZS"),
        total_usd=sum(r.amount_usd or 0.0 for r in rows),
        source_files=sorted({r.source_file for r in rows}),
        reasons=reasons,
        unmapped_count=len(unmapped),
        bad_map_count=len(bad_maps),
        invalid_truck_count=wrong_trucks,
        ignored_count=ignored,
    )
