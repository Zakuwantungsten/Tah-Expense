"""Truck / trailer registration number format helpers.

Canonical form: T{digits} {SUFFIX}  e.g. ``T688 EAF``.
Place labels (YARD, GARAGE, …) are free-text truck-column values that are
allowed without belonging to the fleet registry.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Literal, Optional, Set

# Canonical: T + digits + single space + letter suffix
_CANONICAL = re.compile(r"^T(\d+) ([A-Z]+)$")
# Compact without space: T688EAF
_COMPACT = re.compile(r"^T(\d+)([A-Z]+)$")
# Digits + suffix, optional T / spaces: 688EAF, 688 EAF, T 688 EAF
_LOOSE = re.compile(r"^T?\s*(\d+)\s*([A-Z]+)$")
# Letters / spaces only — candidate place label (YARD, GARAGE, …)
_PLACE_LABEL = re.compile(r"^[A-Z][A-Z0-9]*(?:[ \-][A-Z0-9]+)*$")

NormalizeStatus = Literal["empty", "ok", "normalized", "invalid", "place_label"]

DEFAULT_PLACE_LABELS: tuple[str, ...] = ("YARD", "GARAGE")


@dataclass(frozen=True)
class TruckNormalizeResult:
    status: NormalizeStatus
    value: str = ""          # canonical / label value when resolved
    raw: str = ""            # original input (stripped)


def _strip_excel_junk(text: str) -> str:
    """Remove leading/trailing punctuation Excel often leaves on cells."""
    s = (text or "").strip()
    s = re.sub(r"^[\s\-–—._,;:'\"`]+", "", s)
    s = re.sub(r"[\s\-–—._,;:'\"`]+$", "", s)
    return s


def normalize_place_label(raw: str) -> str:
    return " ".join((raw or "").strip().upper().split())


def is_place_label_candidate(raw: str) -> bool:
    """True when text looks like a place word (no truck digit+suffix shape)."""
    key = normalize_place_label(_strip_excel_junk(raw))
    if not key or _CANONICAL.match(key) or _COMPACT.match(key.replace(" ", "")):
        return False
    # Reject if it still looks like a truck (has digit run + letters)
    if re.search(r"\d", key) and re.search(r"[A-Z]", key):
        return False
    return bool(_PLACE_LABEL.match(key))


def is_allowed_place_label(raw: str, allowed: Iterable[str]) -> bool:
    key = normalize_place_label(raw)
    if not key:
        return False
    allowed_set = {normalize_place_label(a) for a in allowed if a}
    return key in allowed_set


def normalize_truck_number(
    raw: str,
    *,
    allowed_labels: Optional[Iterable[str]] = None,
) -> TruckNormalizeResult:
    """Uppercase / space-collapse into ``T{digits} {SUFFIX}`` when possible.

    Also recognizes remembered place labels (YARD, GARAGE, …) when
    ``allowed_labels`` is provided.
    """
    original = (raw or "").strip()
    if not original:
        return TruckNormalizeResult(status="empty", value="", raw="")

    cleaned = _strip_excel_junk(original)
    if not cleaned:
        return TruckNormalizeResult(status="empty", value="", raw=original)

    collapsed = " ".join(cleaned.upper().split())

    labels = list(allowed_labels) if allowed_labels is not None else list(DEFAULT_PLACE_LABELS)
    if is_allowed_place_label(collapsed, labels):
        return TruckNormalizeResult(status="place_label", value=collapsed, raw=original)

    if _CANONICAL.match(collapsed):
        return TruckNormalizeResult(status="ok", value=collapsed, raw=original)

    compact_src = collapsed.replace(" ", "")
    m = _COMPACT.match(compact_src)
    if m:
        value = f"T{m.group(1)} {m.group(2)}"
        status: NormalizeStatus = "ok" if value == collapsed else "normalized"
        return TruckNormalizeResult(status=status, value=value, raw=original)

    # Allow loose forms that clearly have digits + letter suffix
    m = _LOOSE.match(collapsed)
    if m:
        value = f"T{m.group(1)} {m.group(2)}"
        status = "ok" if value == collapsed else "normalized"
        return TruckNormalizeResult(status=status, value=value, raw=original)

    return TruckNormalizeResult(status="invalid", value=collapsed, raw=original)


def is_canonical_truck_number(value: str) -> bool:
    return bool(_CANONICAL.match((value or "").strip().upper()))


def try_match_fleet(number: str, fleet: set[str]) -> Optional[str]:
    """Return a fleet member matching ``number`` (exact or after normalize), else None.

    Prefers the canonical ``T{digits} {SUFFIX}`` form when the input can be normalized.
    Does not match place labels — callers should check those separately.
    """
    if not number:
        return None
    result = normalize_truck_number(number, allowed_labels=())
    if result.status == "place_label":
        return None
    candidates: list[str] = []
    if result.status in ("ok", "normalized"):
        candidates.append(result.value)
        candidates.append(result.value.replace(" ", ""))
    upper = number.strip().upper()
    collapsed = " ".join(upper.split())
    for extra in (upper, collapsed, collapsed.replace(" ", "")):
        if extra and extra not in candidates:
            candidates.append(extra)

    for candidate in candidates:
        if candidate in fleet:
            return result.value if result.status in ("ok", "normalized") else candidate

    # Slow path: normalize each fleet entry (handles legacy compact registry values)
    compact_candidates = {c.replace(" ", "") for c in candidates}
    for member in fleet:
        m = normalize_truck_number(member, allowed_labels=())
        if m.status in ("ok", "normalized"):
            if m.value in candidates or m.value.replace(" ", "") in compact_candidates:
                return m.value
        elif member.replace(" ", "") in compact_candidates:
            return member
    return None


def merge_allowed_labels(*groups: Iterable[str]) -> Set[str]:
    out: Set[str] = set()
    for group in groups:
        for item in group or ():
            key = normalize_place_label(item)
            if key:
                out.add(key)
    return out
