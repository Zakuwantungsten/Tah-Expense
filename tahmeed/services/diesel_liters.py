"""Pull a litre quantity out of a diesel-cash description string.

Used at display time (and Excel export). Existing transactions are not rewritten.
"""

from __future__ import annotations

import re
from typing import Optional

_LITRE_UNIT = r"(?:ltrs?|lts|litres?|liters?)"
_WITH_UNIT = re.compile(
    rf"(?<![A-Za-z0-9])(\d+(?:[.,]\d+)?)\s*{_LITRE_UNIT}\b",
    re.IGNORECASE,
)
# Standalone number: not glued to a plate like T615, and not a 4-digit year.
_STANDALONE = re.compile(r"(?<![A-Za-z0-9.,])(\d+(?:[.,]\d+)?)(?![A-Za-z0-9])")
_YEAR = re.compile(r"^(?:19|20)\d{2}$")


def parse_liters_from_description(description: str) -> Optional[float]:
    """Return litres found in *description*, or ``None`` if there are none.

    Prefers a number next to LTRS / LTR / LITRE(S) (any case, optional space).
    If no unit is present, falls back to the first standalone number that is
    not a year. Rows like ``DIESEL NAKONDE`` stay empty.
    """
    text = (description or "").strip()
    if not text:
        return None
    match = _WITH_UNIT.search(text)
    if match:
        return _to_liters(match.group(1))
    for found in _STANDALONE.finditer(text):
        raw = found.group(1)
        if _YEAR.fullmatch(raw):
            continue
        return _to_liters(raw)
    return None


def format_liters(value: Optional[float]) -> str:
    """Display string for a parsed litre value; ``—`` when missing."""
    if value is None:
        return "—"
    if float(value).is_integer():
        return f"{int(value)}"
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text or "—"


def _to_liters(raw: str) -> Optional[float]:
    s = (raw or "").strip()
    if not s:
        return None
    if "," in s and "." not in s:
        left, _, right = s.partition(",")
        s = s.replace(",", ".") if len(right) != 3 else s.replace(",", "")
    else:
        s = s.replace(",", "")
    try:
        value = float(s)
    except ValueError:
        return None
    if value <= 0:
        return None
    return value
