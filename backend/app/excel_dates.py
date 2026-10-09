"""Parse Excel / spreadsheet date cells into Python datetimes (Phase 8.1)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional, Union

_EXCEL_EPOCH = datetime(1899, 12, 30)
_MIN_SERIAL = 20_000
_MAX_SERIAL = 80_000

_DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%d/%m/%Y",
    "%d-%m-%Y %H:%M:%S",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y",
    "%d %b %Y %H:%M:%S",
    "%d %b %Y",
    "%d %B %Y",
    "%d %b %y",
    "%Y/%m/%d",
)

ScalarDate = Union[None, datetime, date, int, float, str]


def excel_serial_to_datetime(serial: float) -> Optional[datetime]:
    try:
        n = float(serial)
    except (TypeError, ValueError):
        return None
    if n < _MIN_SERIAL or n > _MAX_SERIAL:
        return None
    try:
        return _EXCEL_EPOCH + timedelta(days=n)
    except (OverflowError, ValueError, OSError):
        return None


def parse_excel_date(val: ScalarDate) -> Optional[datetime]:
    if val is None or val == "":
        return None
    if isinstance(val, bool):
        return None
    if isinstance(val, datetime):
        return val
    if isinstance(val, date):
        return datetime(val.year, val.month, val.day)
    if isinstance(val, (int, float)):
        return excel_serial_to_datetime(val)

    s = str(val).strip()
    if not s or s.lower() in {"none", "n/a", "na", "-", "—", "."}:
        return None

    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue

    titled = s.title()
    if titled != s:
        for fmt in _DATE_FORMATS:
            try:
                return datetime.strptime(titled, fmt)
            except ValueError:
                continue

    try:
        num = float(s.replace(",", ""))
    except ValueError:
        return None
    return excel_serial_to_datetime(num)


def format_excel_date(
    val: ScalarDate,
    fmt: str = "%d %b %Y",
    *,
    fallback: str = "",
) -> str:
    parsed = parse_excel_date(val)
    if parsed is None:
        if val is None or val == "":
            return fallback
        s = str(val).strip()
        return s if s else fallback
    if parsed.hour or parsed.minute or parsed.second:
        if "%H" not in fmt and "%I" not in fmt:
            return parsed.strftime(f"{fmt} %H:%M")
    return parsed.strftime(fmt)


def normalize_date_fields(
    doc: dict,
    *field_names: str,
    display_fmt: str = "%d %b %Y",
    store_as: str = "transaction_date",
) -> Optional[datetime]:
    parsed: Optional[datetime] = None
    source_key: Optional[str] = None
    for key in field_names:
        if key not in doc:
            continue
        raw = doc.get(key)
        if raw is None or raw == "":
            continue
        candidate = parse_excel_date(raw)
        if candidate is not None:
            parsed = candidate
            source_key = key
            break
        if isinstance(raw, datetime):
            parsed = raw
            source_key = key
            break

    if parsed is None:
        existing = doc.get(store_as)
        if isinstance(existing, datetime):
            return existing
        return parse_excel_date(existing)

    doc[store_as] = parsed
    if source_key is not None and not isinstance(doc.get(source_key), datetime):
        doc[source_key] = format_excel_date(parsed, display_fmt)
    return parsed
