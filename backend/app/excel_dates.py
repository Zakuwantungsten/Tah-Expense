"""Parse Excel / spreadsheet date cells into Python datetimes (Phase 8.1).

Must stay in sync with ``tahmeed.services.excel_dates``. Feed imports store a
BSON datetime on ``transaction_date`` / ``expense_date`` for sort and filter;
display strings like ``"03 JUN 2026 12:12"`` must parse or those fields stay
empty and Mongo string-sorts by day-of-month.
"""

from __future__ import annotations

import calendar
import re
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
    "%d-%m-%Y %H:%M",
    "%d-%m-%Y",
    "%d.%m.%Y %H:%M:%S",
    "%d.%m.%Y %H:%M",
    "%d.%m.%Y",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M",
    "%m/%d/%Y",
    "%d %b %Y %H:%M:%S",
    "%d %b %Y %H:%M",
    "%d %b %Y",
    "%d %B %Y %H:%M:%S",
    "%d %B %Y %H:%M",
    "%d %B %Y",
    "%d %b %y %H:%M:%S",
    "%d %b %y %H:%M",
    "%d %b %y",
    "%d %B %y %H:%M:%S",
    "%d %B %y %H:%M",
    "%d %B %y",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y/%m/%d",
)

_FLEX_DATE = re.compile(
    r"""
    ^\s*
    (?P<day>\d{1,2})
    [/\-.\s]+
    (?P<month>[A-Za-z]{3,9}|\d{1,2})
    [/\-.\s]+
    (?P<year>\d{2,4})
    (?:[ T]+(?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?)?
    \s*$
    """,
    re.VERBOSE,
)

_MONTH_NAME = {
    name.lower(): index
    for index, name in enumerate(calendar.month_name)
    if name
}
_MONTH_ABBR = {
    name.lower(): index
    for index, name in enumerate(calendar.month_abbr)
    if name
}

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


def _month_number(token: str) -> Optional[int]:
    raw = token.strip().lower()
    if raw.isdigit():
        month = int(raw)
        return month if 1 <= month <= 12 else None
    if raw in _MONTH_ABBR:
        return _MONTH_ABBR[raw]
    if raw in _MONTH_NAME:
        return _MONTH_NAME[raw]
    if len(raw) >= 3 and raw[:3] in _MONTH_ABBR:
        return _MONTH_ABBR[raw[:3]]
    return None


def _year_number(token: str) -> Optional[int]:
    try:
        year = int(token)
    except ValueError:
        return None
    if year < 100:
        year += 2000 if year < 70 else 1900
    if 1990 <= year <= 2100:
        return year
    return None


def _parse_flex_date(text: str) -> Optional[datetime]:
    match = _FLEX_DATE.match(text)
    if not match:
        return None
    day = int(match.group("day"))
    month = _month_number(match.group("month"))
    year = _year_number(match.group("year"))
    if month is None or year is None:
        return None
    hour = int(match.group("hour") or 0)
    minute = int(match.group("minute") or 0)
    second = int(match.group("second") or 0)
    try:
        return datetime(year, month, day, hour, minute, second)
    except ValueError:
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

    flex = _parse_flex_date(s) or _parse_flex_date(titled)
    if flex is not None:
        return flex

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
    rewrite_display: bool = True,
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
    if (
        rewrite_display
        and source_key is not None
        and not isinstance(doc.get(source_key), datetime)
    ):
        doc[source_key] = format_excel_date(parsed, display_fmt)
    return parsed
