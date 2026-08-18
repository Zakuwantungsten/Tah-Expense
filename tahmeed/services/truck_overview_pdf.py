"""Fleet Expense Report PDF for Truck Overview.

Matches the Tahmeed Transporters report layout in A4 landscape so every
on-screen column fits: logo header, KPI cards, spend-by-source bars,
transaction detail, signatures, confidential footer.
"""

from __future__ import annotations

import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import fitz

# A4 landscape (points)
_PAGE_W = 841.8897705078125
_PAGE_H = 595.2755737304688

_ML = 28.0
_MR = 28.0
_CONTENT_R = _PAGE_W - _MR
_CONTENT_W = _CONTENT_R - _ML

_ORANGE = (232 / 255, 96 / 255, 5 / 255)
_BLACK = (17 / 255, 17 / 255, 17 / 255)
_DARK = (26 / 255, 26 / 255, 26 / 255)
_LABEL = (138 / 255, 138 / 255, 147 / 255)
_MUTED = (85 / 255, 85 / 255, 92 / 255)
_ROW_ALT = (248 / 255, 248 / 255, 250 / 255)
_CARD_BG = (250 / 255, 250 / 255, 250 / 255)
_CARD_EDGE = (236 / 255, 236 / 255, 236 / 255)
_META_BG = (228 / 255, 228 / 255, 231 / 255)
_BAR_TRACK = (238 / 255, 238 / 255, 238 / 255)
_WHITE = (1, 1, 1)
_RULE = (216 / 255, 216 / 255, 219 / 255)

# Same columns as Truck Overview table / Excel export.
_COL_SPECS: list[tuple[str, str, float]] = [
    ("date", "DATE", 50),
    ("source", "SOURCE", 68),
    ("desc", "DESCRIPTION", 108),
    ("ref", "REFERENCE", 66),
    ("truck", "TRUCK FIELD", 54),
    ("tzs", "TZS", 56),
    ("usd", "USD", 48),
    ("zmw", "ZMW", 48),
    ("ltrs", "LTRS", 34),
    ("rate", "RATE", 38),
    ("station", "STATION / OWNER", 68),
    ("receipt", "RECEIPT", 44),
]
_WRAP_KEYS = {"source", "desc", "ref", "station"}
_RIGHT_KEYS = {"tzs", "usd", "zmw", "ltrs", "rate"}


def _build_cols() -> dict[str, tuple[float, float]]:
    pad = 3.0
    usable = _CONTENT_W - 2 * pad
    total = sum(w for _, _, w in _COL_SPECS)
    scale = usable / total if total else 1.0
    cols: dict[str, tuple[float, float]] = {}
    x = _ML + pad
    for key, _, width in _COL_SPECS:
        w = width * scale
        cols[key] = (x, w)
        x += w
    return cols


_COL = _build_cols()

_DASH = "—"
_FONT_REG = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "arial.ttf"
_FONT_BOLD = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "arialbd.ttf"
_fonts: dict[str, fitz.Font] = {}


def _project_root() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent.parent.parent


def _logo_path() -> Optional[Path]:
    root = _project_root()
    candidates = [
        root / "logo.png",
        Path(__file__).resolve().parent.parent.parent / "logo.png",
        Path(os.path.dirname(os.path.abspath(__file__))).parents[1] / "logo.png",
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None


def _font_file(bold: bool = False) -> str:
    path = _FONT_BOLD if bold else _FONT_REG
    return str(path) if path.is_file() else ""


def _font(bold: bool = False) -> Optional[fitz.Font]:
    key = "b" if bold else "r"
    if key in _fonts:
        return _fonts[key]
    path = _font_file(bold)
    if not path:
        return None
    font = fitz.Font(fontfile=path)
    _fonts[key] = font
    return font


def _text_len(text: str, size: float, *, bold: bool = False) -> float:
    font = _font(bold)
    if font is not None:
        return font.text_length(text, fontsize=size)
    return fitz.get_text_length(
        text, fontname="hebo" if bold else "helv", fontsize=size
    )


def _ensure_page_fonts(page: fitz.Page) -> tuple[str, str]:
    """Register Arial on the page once; return (regular_name, bold_name)."""
    cached = getattr(page, "_tahmeed_fonts", None)
    if cached:
        return cached
    reg = _font_file(False)
    bold = _font_file(True)
    if reg and bold:
        page.insert_font(fontname="Tahmeed", fontfile=reg)
        page.insert_font(fontname="TahmeedB", fontfile=bold)
        names = ("Tahmeed", "TahmeedB")
    else:
        names = ("helv", "hebo")
    try:
        page._tahmeed_fonts = names  # type: ignore[attr-defined]
    except Exception:
        pass
    return names


def _dash(value) -> str:
    if value is None:
        return _DASH
    text = str(value).strip()
    return text if text else _DASH


def _fmt_usd(value: Optional[float], *, money: bool = False) -> str:
    if value is None:
        return _DASH
    try:
        val = float(value)
    except (TypeError, ValueError):
        return _DASH
    if money:
        if abs(val - round(val)) < 1e-9:
            return f"${val:,.0f}"
        return f"${val:,.2f}"
    if abs(val - round(val)) < 1e-9:
        return f"{val:,.0f}"
    return f"{val:,.2f}"


def _fmt_rate(value) -> str:
    if value in (None, "", 0, 0.0):
        return _DASH
    try:
        val = float(value)
    except (TypeError, ValueError):
        return _DASH
    if abs(val) < 1e-12:
        return _DASH
    text = f"{val:.4f}".rstrip("0").rstrip(".")
    return text or _DASH


def _fmt_date(value) -> str:
    if value is None:
        return _DASH
    if hasattr(value, "strftime"):
        if getattr(value, "year", 1) <= 1:
            return _DASH
        return value.strftime("%d/%m/%Y")
    return _dash(value)


def _normalize_currency(currency: str) -> str:
    cur = (currency or "").strip().upper()
    if cur in ("TZS", "TSH", "TZ"):
        return "TZS"
    if cur == "USD":
        return "USD"
    if cur in ("ZMW", "ZMB", "ZK"):
        return "ZMW"
    return cur


def _usd_amount(row: dict) -> Optional[float]:
    amount = row.get("amount")
    if amount is None or amount == "":
        return None
    try:
        val = float(amount)
    except (TypeError, ValueError):
        return None
    if _normalize_currency(row.get("currency") or "") != "USD":
        return None
    return val


def _split_amounts(row: dict) -> tuple[Optional[float], Optional[float], Optional[float]]:
    amount = row.get("amount")
    if amount is None or amount == "":
        return None, None, None
    try:
        val = float(amount)
    except (TypeError, ValueError):
        return None, None, None
    cur = _normalize_currency(row.get("currency") or "")
    if cur == "TZS":
        return val, None, None
    if cur == "USD":
        return None, val, None
    if cur == "ZMW":
        return None, None, val
    return None, None, None


def _fmt_amount(value: Optional[float], *, decimals: int) -> str:
    if value is None:
        return _DASH
    try:
        val = float(value)
    except (TypeError, ValueError):
        return _DASH
    return f"{val:,.{decimals}f}"


def _fmt_liters(value) -> str:
    if value in (None, "", 0, 0.0):
        return _DASH
    try:
        val = float(value)
    except (TypeError, ValueError):
        return _DASH
    if abs(val) < 1e-12:
        return _DASH
    return f"{val:,.0f}"


def _wrap(text: str, size: float, max_width: float, *, bold: bool = False) -> list[str]:
    text = (text or "").replace("\n", " ").strip()
    if not text:
        return [_DASH]
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        trial = word if not current else f"{current} {word}"
        if _text_len(trial, size, bold=bold) <= max_width:
            current = trial
        else:
            if current:
                lines.append(current)
            if _text_len(word, size, bold=bold) <= max_width:
                current = word
            else:
                chunk = ""
                for ch in word:
                    trial_ch = chunk + ch
                    if _text_len(trial_ch, size, bold=bold) <= max_width:
                        chunk = trial_ch
                    else:
                        if chunk:
                            lines.append(chunk)
                        chunk = ch
                current = chunk
    if current:
        lines.append(current)
    return lines or [_DASH]


def _draw_text(
    page: fitz.Page,
    x: float,
    y: float,
    text: str,
    *,
    size: float,
    color: tuple[float, float, float],
    bold: bool = False,
) -> None:
    regular, bold_name = _ensure_page_fonts(page)
    page.insert_text(
        (x, y),
        text,
        fontsize=size,
        fontname=bold_name if bold else regular,
        color=color,
    )


def _draw_right(
    page: fitz.Page,
    right: float,
    y: float,
    text: str,
    *,
    size: float,
    color: tuple[float, float, float],
    bold: bool = False,
) -> None:
    width = _text_len(text, size, bold=bold)
    _draw_text(page, right - width, y, text, size=size, color=color, bold=bold)


def _fill_rect(page: fitz.Page, rect: fitz.Rect, color: tuple[float, float, float]) -> None:
    page.draw_rect(rect, color=None, fill=color, width=0)


def _header(page: fitz.Page, logo_path: Optional[Path]) -> float:
    """Draw branded header; return y just below orange rule."""
    if logo_path and logo_path.is_file():
        page.insert_image(
            fitz.Rect(_ML, 16.0, _ML + 48.0, 44.0),
            filename=str(logo_path),
            keep_proportion=True,
        )

    company = "TAHMEED TRANSPORTERS"
    company_size = 11.0
    company_w = _text_len(company, company_size, bold=True)
    _draw_text(
        page,
        _CONTENT_R - company_w,
        20.0 + company_size * 0.75,
        company,
        size=company_size,
        color=_BLACK,
        bold=True,
    )

    subtitle = "FLEET EXPENSE REPORT  |  WWW.TAHMEEDCOACH.CO.KE"
    sub_size = 7.0
    sub_w = _text_len(subtitle, sub_size)
    _draw_text(
        page,
        _CONTENT_R - sub_w,
        35.5 + sub_size * 0.75,
        subtitle,
        size=sub_size,
        color=_LABEL,
    )

    y = 50.0
    page.draw_line(
        fitz.Point(_ML, y),
        fitz.Point(_CONTENT_R, y),
        color=_ORANGE,
        width=2.25,
    )
    return y + 14.0


def _footer(page: fitz.Page, page_no: int, page_count: int) -> None:
    y = _PAGE_H - 22.0
    page.draw_line(
        fitz.Point(_ML, y - 10),
        fitz.Point(_CONTENT_R, y - 10),
        color=_RULE,
        width=0.6,
    )
    _draw_text(
        page,
        _ML,
        y + 5,
        "CONFIDENTIAL — INTERNAL USE ONLY",
        size=7.0,
        color=_LABEL,
        bold=True,
    )
    label = f"Page {page_no} of {page_count}"
    _draw_right(page, _CONTENT_R, y + 5, label, size=7.0, color=_LABEL)


def _draw_kpi_card(
    page: fitz.Page,
    x: float,
    y: float,
    w: float,
    h: float,
    label: str,
    value: str,
    *,
    value_color: tuple[float, float, float] = _BLACK,
) -> None:
    rect = fitz.Rect(x, y, x + w, y + h)
    _fill_rect(page, rect, _CARD_BG)
    # subtle edge
    page.draw_rect(rect, color=_CARD_EDGE, width=0.4)
    # orange top accent
    _fill_rect(page, fitz.Rect(x, y, x + w, y + 2.4), _ORANGE)
    _draw_text(page, x + 10.0, y + 16.5, label, size=6.8, color=_LABEL, bold=True)
    _draw_text(page, x + 10.0, y + 34.5, value, size=13.0, color=value_color, bold=True)


def _draw_meta_block(
    page: fitz.Page,
    y: float,
    truck: str,
    period: str,
    sources: str,
    generated: str,
) -> float:
    h = 48.0
    rect = fitz.Rect(_ML, y, _CONTENT_R, y + h)
    _fill_rect(page, rect, _META_BG)

    col_w = _CONTENT_W / 4
    cols = [
        (_ML, "VEHICLE", truck),
        (_ML + col_w, "PERIOD COVERED", period),
        (_ML + 2 * col_w, "SOURCES", sources),
        (_ML + 3 * col_w, "GENERATED", generated),
    ]
    wrap_w = col_w - 14.0
    for x, label, value in cols:
        _draw_text(page, x + 8.0, y + 14.5, label, size=7.0, color=_LABEL, bold=True)
        lines = _wrap(value, 9.0, wrap_w, bold=True)
        yy = y + 27.5
        for line in lines[:2]:
            _draw_text(page, x + 8.0, yy, line, size=9.0, color=_DARK, bold=True)
            yy += 12.5
    return y + h + 12.0


def _spend_by_source(rows: list[dict]) -> list[dict]:
    buckets: dict[str, dict[str, Any]] = defaultdict(lambda: {"records": 0, "usd": 0.0})
    for row in rows:
        source = (row.get("source") or "Unknown").strip() or "Unknown"
        buckets[source]["records"] += 1
        usd = _usd_amount(row)
        if usd is not None:
            buckets[source]["usd"] += usd
    items = [
        {"source": name, "records": data["records"], "usd": data["usd"]}
        for name, data in buckets.items()
    ]
    items.sort(key=lambda item: (-item["usd"], -item["records"], item["source"].lower()))
    total_usd = sum(item["usd"] for item in items) or 0.0
    for item in items:
        item["share"] = (item["usd"] / total_usd * 100.0) if total_usd else 0.0
    return items


def _row_height(row: dict) -> float:
    max_lines = 1
    for key in _WRAP_KEYS:
        x, w = _COL[key]
        text = _cell_text(row, key)
        size = 7.2 if key in ("source", "desc") else 7.0
        max_lines = max(max_lines, len(_wrap(text, size, w - 2)))
    if max_lines <= 1:
        return 16.5
    return 10.0 + max_lines * 9.5


def _cell_text(row: dict, key: str) -> str:
    tzs, usd, zmw = _split_amounts(row)
    if key == "date":
        return _fmt_date(row.get("date"))
    if key == "source":
        return _dash(row.get("source"))
    if key == "desc":
        return _dash(row.get("description"))
    if key == "ref":
        return _dash(row.get("reference"))
    if key == "truck":
        return _dash(row.get("truck_value"))
    if key == "tzs":
        return _fmt_amount(tzs, decimals=0)
    if key == "usd":
        return _fmt_amount(usd, decimals=2)
    if key == "zmw":
        return _fmt_amount(zmw, decimals=0)
    if key == "ltrs":
        return _fmt_liters(row.get("liters"))
    if key == "rate":
        return _fmt_rate(row.get("rate"))
    if key == "station":
        return _dash(row.get("station"))
    if key == "receipt":
        receipt = (row.get("receipt_status") or "").strip()
        return receipt.title() if receipt and receipt != _DASH else _DASH
    return _DASH


def _draw_table_header(page: fitz.Page, y: float) -> float:
    h = 22.0
    _fill_rect(page, fitz.Rect(_ML, y, _CONTENT_R, y + h), _DARK)
    for key, label, _ in _COL_SPECS:
        x, w = _COL[key]
        if key in _RIGHT_KEYS:
            _draw_right(page, x + w - 2, y + 14.0, label, size=6.2, color=_WHITE, bold=True)
        elif key == "receipt":
            _draw_right(page, x + w - 2, y + 14.0, label, size=6.2, color=_WHITE, bold=True)
        elif key == "station":
            _draw_text(page, x, y + 9.0, "STATION /", size=5.8, color=_WHITE, bold=True)
            _draw_text(page, x, y + 17.5, "OWNER", size=5.8, color=_WHITE, bold=True)
        else:
            _draw_text(page, x, y + 14.0, label, size=6.2, color=_WHITE, bold=True)
    return y + h


def _draw_detail_row(page: fitz.Page, y: float, row: dict, *, alt: bool) -> float:
    h = _row_height(row)
    if alt:
        _fill_rect(page, fitz.Rect(_ML, y, _CONTENT_R, y + h), _ROW_ALT)

    baseline = y + 11.0
    for key, _, _ in _COL_SPECS:
        x, w = _COL[key]
        text = _cell_text(row, key)
        size = 7.2 if key in ("source", "desc", "tzs", "usd", "zmw") else 7.0
        color = _MUTED if key == "date" else _DARK
        if key == "ref":
            color = _LABEL
        bold = key in _RIGHT_KEYS

        if key in _WRAP_KEYS:
            lines = _wrap(text, size, w - 2)
            yy = baseline
            for line in lines:
                _draw_text(page, x, yy, line, size=size, color=color)
                yy += 9.5
        elif key in _RIGHT_KEYS:
            _draw_right(page, x + w - 2, baseline, text, size=size, color=_DARK, bold=bold)
        elif key == "receipt":
            _draw_right(page, x + w - 2, baseline, text, size=7.0, color=_DARK)
        else:
            line = _wrap(text, size, w - 2)[0]
            _draw_text(page, x, baseline, line, size=size, color=color)
    return y + h


def _draw_total_row(page: fitz.Page, y: float, summary: dict) -> float:
    page.draw_line(
        fitz.Point(_ML, y),
        fitz.Point(_CONTENT_R, y),
        color=_DARK,
        width=1.125,
    )
    y += 12.0
    _draw_text(page, _ML + 2, y, "Total", size=8.0, color=_DARK, bold=True)
    totals = {
        "tzs": _fmt_amount(summary.get("tzs_total"), decimals=0),
        "usd": _fmt_amount(summary.get("usd_total"), decimals=2),
        "zmw": _fmt_amount(summary.get("zmw_total"), decimals=0),
        "ltrs": _fmt_liters(summary.get("liters_total")),
    }
    for key, text in totals.items():
        x, w = _COL[key]
        _draw_right(page, x + w - 2, y, text, size=8.0, color=_DARK, bold=True)
    return y + 14.0


def _draw_signatures(page: fitz.Page, y: float) -> None:
    y = max(y + 22.0, _PAGE_H - 70.0)
    mid = (_ML + _CONTENT_R) / 2
    gap = 28.0
    left_x0, left_x1 = _ML, mid - gap
    right_x0, right_x1 = mid + gap, _CONTENT_R
    page.draw_line(fitz.Point(left_x0, y), fitz.Point(left_x1, y), color=_LABEL, width=0.8)
    page.draw_line(fitz.Point(right_x0, y), fitz.Point(right_x1, y), color=_LABEL, width=0.8)
    _draw_text(
        page,
        left_x0,
        y + 12,
        "PREPARED BY — DATA OPERATIONS",
        size=7.0,
        color=_LABEL,
    )
    _draw_text(
        page,
        right_x0,
        y + 12,
        "REVIEWED / APPROVED BY",
        size=7.0,
        color=_LABEL,
    )


def _period_label(date_from: Optional[datetime], date_to: Optional[datetime], rows: list[dict]) -> str:
    if date_from and date_to:
        return f"{date_from.strftime('%d %b %Y')} – {date_to.strftime('%d %b %Y')}"
    dates = [row.get("date") for row in rows if hasattr(row.get("date"), "strftime")]
    dates = [d for d in dates if getattr(d, "year", 1) > 1]
    if not dates:
        return "All dates"
    return f"{min(dates).strftime('%d %b %Y')} – {max(dates).strftime('%d %b %Y')}"


def _sources_label(rows: list[dict], source_filter_label: str) -> str:
    if source_filter_label and source_filter_label.lower() not in ("all sources", "all"):
        return source_filter_label
    names = sorted({(row.get("source") or "").strip() for row in rows if (row.get("source") or "").strip()})
    return ", ".join(names) if names else "—"


def export_truck_overview_pdf(
    path: str,
    *,
    truck: str,
    rows: list[dict],
    summary: dict,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    source_label: str = "All Sources",
    generated_at: Optional[datetime] = None,
) -> None:
    """Write a landscape Fleet Expense Report PDF for the given truck overview rows."""
    generated_at = generated_at or datetime.now()
    logo = _logo_path()
    spend_items = _spend_by_source(rows)
    usd_total = float(summary.get("usd_total") or 0.0)
    record_count = int(summary.get("record_count") or len(rows))
    source_count = int(summary.get("source_count") or len(spend_items))
    avg = (usd_total / record_count) if record_count else 0.0
    period = _period_label(date_from, date_to, rows)
    sources = _sources_label(rows, source_label)

    doc = fitz.open()

    def new_page() -> fitz.Page:
        return doc.new_page(width=_PAGE_W, height=_PAGE_H)

    # ── Page 1: cover summary + start of detail ──────────────────────────
    page = new_page()
    y = _header(page, logo)

    # Title block
    _draw_text(page, _ML, y + 6, "FLEET EXPENSE REPORT", size=7.5, color=_ORANGE, bold=True)
    y += 20.0
    title = f"Truck Overview — {truck}"
    _draw_text(page, _ML, y + 6, title, size=18.0, color=_BLACK, bold=True)
    chip = f"{record_count} line items"
    chip_w = _text_len(chip, 8.0, bold=True)
    _draw_text(page, _CONTENT_R - chip_w, y + 4, chip, size=8.0, color=_LABEL, bold=True)
    y += 26.0
    _draw_text(
        page,
        _ML,
        y,
        "Consolidated expense record across all logged sources",
        size=9.5,
        color=_MUTED,
    )
    y += 16.0

    y = _draw_meta_block(
        page,
        y,
        truck=truck,
        period=period,
        sources=sources,
        generated=generated_at.strftime("%d %b %Y"),
    )

    # KPI cards
    card_h = 46.0
    gap = 8.0
    card_w = (_CONTENT_W - 4 * gap) / 5
    labels_vals = [
        ("TOTAL RECORDS", f"{record_count:,}", _BLACK),
        ("DATA SOURCES", f"{source_count:,}", _BLACK),
        ("TOTAL USD SPEND", _fmt_usd(usd_total, money=True), _ORANGE),
        ("AVG. PER RECORD", f"${avg:,.2f}" if record_count else "—", _BLACK),
        ("LINE ITEMS LISTED", f"{record_count:,}", _BLACK),
    ]
    for i, (lab, val, col) in enumerate(labels_vals):
        x = _ML + i * (card_w + gap)
        _draw_kpi_card(page, x, y, card_w, card_h, lab, val, value_color=col)
    y += card_h + 14.0
    bottom_limit = _PAGE_H - 55.0

    def start_spend_header(current_page: fitz.Page, current_y: float) -> float:
        _draw_text(current_page, _ML, current_y + 6, "Spend by Source", size=11.0, color=_BLACK, bold=True)
        current_y += 18.0
        spend_cols = [
            (_ML + 6, "SOURCE"),
            (_ML + 210.0, "RECORDS"),
            (_ML + 290.0, "USD TOTAL"),
            (_ML + 390.0, "SHARE"),
            (_ML + 450.0, "DISTRIBUTION"),
        ]
        for x, lab in spend_cols:
            _draw_text(current_page, x, current_y + 12, lab, size=7.0, color=_LABEL, bold=True)
        current_page.draw_line(
            fitz.Point(_ML, current_y + 18.0),
            fitz.Point(_CONTENT_R, current_y + 18.0),
            color=_RULE,
            width=0.75,
        )
        return current_y + 18.0

    y = start_spend_header(page, y)

    bar_x0 = _ML + 450.0
    bar_w = _CONTENT_R - bar_x0 - 6.0
    for item in spend_items:
        row_h = 22.0
        if y + row_h > bottom_limit:
            page = new_page()
            y = start_spend_header(page, _header(page, logo))
        _draw_text(page, _ML + 6, y + 13.5, item["source"], size=8.5, color=_DARK)
        _draw_right(page, _ML + 255.0, y + 13.5, f"{item['records']}", size=8.5, color=_DARK)
        _draw_right(
            page,
            _ML + 365.0,
            y + 13.5,
            _fmt_usd(item["usd"], money=True),
            size=8.5,
            color=_DARK,
        )
        share_txt = f"{item['share']:.0f}%"
        _draw_right(page, _ML + 430.0, y + 13.5, share_txt, size=8.5, color=_DARK)

        track = fitz.Rect(bar_x0, y + 8.0, bar_x0 + bar_w, y + 15.5)
        _fill_rect(page, track, _BAR_TRACK)
        fill_w = bar_w * max(0.0, min(1.0, item["share"] / 100.0))
        if fill_w > 0.5:
            _fill_rect(page, fitz.Rect(bar_x0, y + 8.0, bar_x0 + fill_w, y + 15.5), _ORANGE)

        page.draw_line(
            fitz.Point(_ML, y + row_h),
            fitz.Point(_CONTENT_R, y + row_h),
            color=(238 / 255, 238 / 255, 239 / 255),
            width=0.75,
        )
        y += row_h

    # Total spend row
    page.draw_line(
        fitz.Point(_ML, y),
        fitz.Point(_CONTENT_R, y),
        color=_DARK,
        width=1.125,
    )
    y += 14.5
    _draw_text(page, _ML + 6, y, "Total", size=9.0, color=_DARK, bold=True)
    _draw_right(page, _ML + 255.0, y, f"{record_count}", size=8.5, color=_DARK, bold=True)
    _draw_right(
        page,
        _ML + 365.0,
        y,
        _fmt_usd(usd_total, money=True),
        size=8.5,
        color=_DARK,
        bold=True,
    )
    _draw_right(page, _ML + 430.0, y, "100%", size=8.5, color=_DARK, bold=True)
    y += 18.0

    if y + 70 > bottom_limit:
        page = new_page()
        y = _header(page, logo)

    # Transaction Detail heading
    _draw_text(page, _ML, y + 6, "Transaction Detail", size=11.0, color=_BLACK, bold=True)
    chip = f"{record_count} line items"
    chip_w = _text_len(chip, 8.0, bold=True)
    _draw_text(page, _CONTENT_R - chip_w, y + 6, chip, size=8.0, color=_LABEL, bold=True)
    y += 18.0

    y = _draw_table_header(page, y)

    row_index = 0
    alt = False

    while row_index < len(rows):
        row = rows[row_index]
        needed = _row_height(row)
        if y + needed > bottom_limit:
            _draw_total_row(page, y + 2, summary)
            page = new_page()
            y = _header(page, logo)
            y = _draw_table_header(page, y + 2)
            alt = False
            continue

        y = _draw_detail_row(page, y, row, alt=alt)
        alt = not alt
        row_index += 1

    if y + 40 > bottom_limit:
        _draw_total_row(page, y + 2, summary)
        page = new_page()
        y = _header(page, logo)
        y = _draw_table_header(page, y + 2)
    y = _draw_total_row(page, y + 2, summary)
    _draw_signatures(page, y)

    # Stamp footers with final page count
    page_count = doc.page_count
    for i in range(page_count):
        _footer(doc[i], i + 1, page_count)

    # Ensure parent dir exists
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    doc.close()
