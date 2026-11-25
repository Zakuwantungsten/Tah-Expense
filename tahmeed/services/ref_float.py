"""Canonical Ref_Float / refund-to-float recognition (desktop + import).

Keep in sync with ``backend.app.ref_float`` (backend cannot import tahmeed).
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

_CANONICAL = "REFUND TO FLOAT"
_CANONICAL_LOW = "refund to float"
_CANONICAL_COMPACT = "refundtofloat"

# Typo tolerance vs the canonical phrase / compact form.
_PHRASE_RATIO = 0.78
_COMPACT_RATIO = 0.85


def is_refund_float(text: str) -> bool:
    """True for canonical, colloquial, and near-miss Ref_Float refund phrases."""
    low = (text or "").strip().lower()
    if not low:
        return False
    if low == _CANONICAL_LOW:
        return True
    if "refund" in low and "float" in low:
        return True
    if SequenceMatcher(None, low, _CANONICAL_LOW).ratio() >= _PHRASE_RATIO:
        return True
    compact = re.sub(r"[^a-z]", "", low)
    if (
        compact
        and SequenceMatcher(None, compact, _CANONICAL_COMPACT).ratio()
        >= _COMPACT_RATIO
    ):
        return True
    return False


def canonical_ref_float(text: str) -> str:
    """Normalize to ``REFUND TO FLOAT`` when matched; else stripped uppercase."""
    raw = (text or "").strip()
    if not raw:
        return ""
    if is_refund_float(raw):
        return _CANONICAL
    return raw.upper()
