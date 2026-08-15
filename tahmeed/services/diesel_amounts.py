"""Numeric helpers for diesel station imports (Infinity / Lake / GBP)."""

from __future__ import annotations

from typing import Any, Optional


def parse_diesel_number(value: Any) -> Optional[float]:
    """Parse a litres / rate / amount cell. Returns None when blank or invalid."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text or text.lower() in ("none", "n/a", "na", "-", "—"):
        return None
    text = text.replace(" ", "").replace("%", "")
    if "," in text and "." in text:
        text = text.replace(",", "")
    elif text.count(",") > 1:
        text = text.replace(",", "")
    elif "," in text:
        _left, _, right = text.partition(",")
        text = text.replace(",", ".") if len(right) != 3 else text.replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


def diesel_line_total(ltrs: Any, rate: Any) -> float:
    """Litres × rate. Missing or invalid sides count as 0."""
    litres = parse_diesel_number(ltrs)
    price = parse_diesel_number(rate)
    if litres is None or price is None:
        return 0.0
    return round(litres * price, 2)


def apply_diesel_computed_fields(rec: dict) -> None:
    """Drop Excel S/No and replace Excel amount with litres × rate (in place)."""
    rec["sn"] = ""
    litres = parse_diesel_number(rec.get("ltrs"))
    rate = parse_diesel_number(rec.get("price_per_ltr"))
    if litres is not None:
        rec["ltrs"] = litres
    if rate is not None:
        rec["price_per_ltr"] = rate
    rec["total_amount"] = diesel_line_total(litres, rate)
