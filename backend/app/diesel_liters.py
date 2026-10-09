"""Parse litre quantities from diesel-cash description text."""

from __future__ import annotations

import re
from typing import Optional

_LITRE_UNIT = r"(?:ltrs?|lts|litres?|liters?)"
_WITH_UNIT = re.compile(
    rf"(?<![A-Za-z0-9])(\d+(?:[.,]\d+)?)\s*{_LITRE_UNIT}\b",
    re.IGNORECASE,
)
_STANDALONE = re.compile(r"(?<![A-Za-z0-9.,])(\d+(?:[.,]\d+)?)(?![A-Za-z0-9])")
_YEAR = re.compile(r"^(?:19|20)\d{2}$")


def parse_liters_from_description(description: str) -> Optional[float]:
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
