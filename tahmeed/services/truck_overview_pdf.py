"""Fleet Expense Report PDF for Truck Overview.

Matches the Tahmeed Transporters portrait report layout:
logo header, KPI cards, spend-by-source with distribution bars,
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

# A4 portrait (points)
_PAGE_W = 595.2755737304688
_PAGE_H = 841.8897705078125

_ML = 39.7
_MR = 39.7
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

# Transaction table column x starts / widths (content area)
_COL = {
    "date": (_ML + 4.5, 50),
    "source": (_ML + 56.2, 72),
    "desc": (_ML + 134.3, 148),
    "ref": (_ML + 287.7, 88),
    "rate": (_ML + 380.6, 34),
    "station": (_ML + 409.3, 82),
    "usd": (_ML + 491.3, 24.6),
}

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
        # Match reference placement ~53x31 pt
        page.insert_image(
            fitz.Rect(_ML, 22.7, _ML + 52.9, 53.9),
            filename=str(logo_path),
            keep_proportion=True,
        )

    company = "TAHMEED TRANSPORTERS"
    company_size = 11.0
    company_w = _text_len(company, company_size, bold=True)
    _draw_text(
        page,
        _CONTENT_R - company_w,
        28.1 + company_size * 0.75,
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
        44.9 + sub_size * 0.75,
        subtitle,
        size=sub_size,
        color=_LABEL,
    )

    # Orange accent rule
    y = 62.0
    page.draw_line(
        fitz.Point(_ML, y),
        fitz.Point(_CONTENT_R, y),
        color=_ORANGE,
        width=2.25,
    )
    return y + 18.0


def _footer(page: fitz.Page, page_no: int, page_count: int) -> None:
    y = 805.8
    page.draw_line(
        fitz.Point(_ML, y - 10),
        fitz.Point(_CONTENT_R, y - 10),
        color=_RULE,
        width=0.6,
    )
    _draw_text(
        page,
        _ML,
        y + 7,
        "CONFIDENTIAL — INTERNAL USE ONLY",
        size=7.0,
        color=_LABEL,
        bold=True,
    )
    label = f"Page {page_no} of {page_count}"
    _draw_right(page, _CONTENT_R, y + 7, label, size=7.0, color=_LABEL)


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
    _draw_text(page, x + 9.7, y + 18.5, label, size=6.8, color=_LABEL, bold=True)
    _draw_text(page, x + 9.7, y + 36.5, value, size=14.0, color=value_color, bold=True)


def _draw_meta_block(
    page: fitz.Page,
    y: float,
    truck: str,
    period: str,
    sources: str,
    generated: str,
) -> float:
    h = 56.4
    rect = fitz.Rect(_ML, y, _CONTENT_R, y + h)
    _fill_rect(page, rect, _META_BG)

    cols = [
        (_ML, "VEHICLE", truck),
        (_ML + 129.0, "PERIOD COVERED", period),
        (_ML + 257.9, "SOURCES", sources),
        (_ML + 386.9, "GENERATED", generated),
    ]
    for x, label, value in cols:
        _draw_text(page, x, y + 16.5, label, size=7.0, color=_LABEL, bold=True)
        lines = _wrap(value, 9.5, 120, bold=True)
        yy = y + 29.5
        for line in lines[:2]:
            _draw_text(page, x, yy, line, size=9.5, color=_DARK, bold=True)
            yy += 13.8
    return y + h + 15.0


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
    ref_lines = _wrap(_dash(row.get("reference")), 7.8, _COL["ref"][1])
    desc_lines = _wrap(_dash(row.get("description")), 8.3, _COL["desc"][1])
    source_lines = _wrap(_dash(row.get("source")), 8.3, _COL["source"][1])
    lines = max(len(ref_lines), len(desc_lines), len(source_lines), 1)
    if lines <= 1:
        return 20.6
    return 12.0 + lines * 11.3


def _draw_table_header(page: fitz.Page, y: float) -> float:
    h = 28.0
    _fill_rect(page, fitz.Rect(_ML, y, _CONTENT_R, y + h), _DARK)
    headers = [
        (_COL["date"][0], "DATE", False),
        (_COL["source"][0], "SOURCE", False),
        (_COL["desc"][0], "DESCRIPTION", False),
        (_COL["ref"][0], "REFERENCE", False),
        (_COL["rate"][0], "RATE", False),
        (_COL["station"][0], "STATION /", True),
        (_COL["usd"][0] + _COL["usd"][1], "USD", False),
    ]
    for x, label, stacked in headers:
        if stacked:
            _draw_text(page, x, y + 11.5, "STATION /", size=6.9, color=_WHITE, bold=True)
            _draw_text(page, x, y + 21.5, "OWNER", size=6.9, color=_WHITE, bold=True)
        elif label == "USD":
            _draw_right(page, x, y + 17.5, "USD", size=6.9, color=_WHITE, bold=True)
        else:
            _draw_text(page, x, y + 17.5, label, size=6.9, color=_WHITE, bold=True)
    return y + h


def _draw_detail_row(page: fitz.Page, y: float, row: dict, *, alt: bool) -> float:
    h = _row_height(row)
    if alt:
        _fill_rect(page, fitz.Rect(_ML, y, _CONTENT_R, y + h), _ROW_ALT)

    baseline = y + 12.5
    date_txt = _fmt_date(row.get("date"))
    _draw_text(page, _COL["date"][0], baseline, date_txt, size=8.3, color=_MUTED)

    source_lines = _wrap(_dash(row.get("source")), 8.3, _COL["source"][1])
    desc_lines = _wrap(_dash(row.get("description")), 8.3, _COL["desc"][1])
    ref_lines = _wrap(_dash(row.get("reference")), 7.8, _COL["ref"][1])
    yy = baseline
    for line in source_lines:
        _draw_text(page, _COL["source"][0], yy, line, size=8.3, color=_DARK)
        yy += 11.3

    yy = baseline
    for line in desc_lines:
        _draw_text(page, _COL["desc"][0], yy, line, size=8.3, color=_DARK)
        yy += 11.3

    yy = baseline
    for line in ref_lines:
        _draw_text(page, _COL["ref"][0], yy, line, size=7.8, color=_LABEL)
        yy += 11.3

    rate_txt = _fmt_rate(row.get("rate"))
    rate_w = _text_len(rate_txt, 8.3, bold=True)
    # right-ish within rate column
    _draw_text(
        page,
        _COL["rate"][0] + max(0, _COL["rate"][1] - rate_w),
        baseline,
        rate_txt,
        size=8.3,
        color=_DARK,
        bold=True,
    )

    station = _dash(row.get("station"))
    _draw_text(page, _COL["station"][0], baseline, station[:18], size=8.3, color=_DARK)


    usd = _usd_amount(row)
    usd_txt = _fmt_usd(usd)
    _draw_right(
        page,
        _CONTENT_R - 4,
        baseline,
        usd_txt,
        size=8.3,
        color=_DARK,
        bold=True,
    )
    return y + h


def _draw_total_row(page: fitz.Page, y: float, usd_total: float) -> float:
    page.draw_line(
        fitz.Point(_ML, y),
        fitz.Point(_CONTENT_R, y),
        color=_DARK,
        width=1.125,
    )
    y += 14.0
    _draw_text(page, _ML + 0.7, y, "Total", size=8.6, color=_DARK, bold=True)
    _draw_right(
        page,
        _CONTENT_R - 4,
        y,
        _fmt_usd(usd_total, money=True),
        size=8.6,
        color=_DARK,
        bold=True,
    )
    return y + 16.0


def _draw_signatures(page: fitz.Page, y: float) -> None:
    y = max(y + 36.0, 700.0)
    mid = (_ML + _CONTENT_R) / 2
    gap = 24.0
    left_x0, left_x1 = _ML, mid - gap
    right_x0, right_x1 = mid + gap, _CONTENT_R
    page.draw_line(fitz.Point(left_x0, y), fitz.Point(left_x1, y), color=_LABEL, width=0.8)
    page.draw_line(fitz.Point(right_x0, y), fitz.Point(right_x1, y), color=_LABEL, width=0.8)
    _draw_text(
        page,
        left_x0,
        y + 14,
        "PREPARED BY — DATA OPERATIONS",
        size=7.5,
        color=_LABEL,
    )
    _draw_text(
        page,
        right_x0,
        y + 14,
        "REVIEWED / APPROVED BY",
        size=7.5,
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
    """Write a Fleet Expense Report PDF for the given truck overview rows."""
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
    _draw_text(page, _ML, y + 8, "FLEET EXPENSE REPORT", size=8.0, color=_ORANGE, bold=True)
    y += 26.0
    title = f"Truck Overview — {truck}"
    _draw_text(page, _ML, y + 8, title, size=22.0, color=_BLACK, bold=True)
    # line-items chip top-right near title area
    chip = f"{record_count} line items"
    chip_w = _text_len(chip, 8.0, bold=True)
    _draw_text(page, _CONTENT_R - chip_w, y + 4, chip, size=8.0, color=_LABEL, bold=True)
    y += 32.0
    _draw_text(
        page,
        _ML,
        y,
        "Consolidated expense record across all logged sources",
        size=10.5,
        color=_MUTED,
    )
    y += 22.0

    y = _draw_meta_block(
        page,
        y,
        truck=truck,
        period=period,
        sources=sources,
        generated=generated_at.strftime("%d %b %Y"),
    )

    # KPI cards
    card_w = 105.6
    card_h = 54.9
    gap = 6.0
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
    y += card_h + 18.5

    # Spend by Source
    _draw_text(page, _ML, y + 8, "Spend by Source", size=11.5, color=_BLACK, bold=True)
    y += 22.0

    spend_cols = [
        (_ML + 6, "SOURCE"),
        (_ML + 130.8, "RECORDS"),
        (_ML + 197.7, "USD TOTAL"),
        (_ML + 276.4, "SHARE"),
        (_ML + 315.5, "DISTRIBUTION"),
    ]
    for x, lab in spend_cols:
        _draw_text(page, x, y + 14, lab, size=7.2, color=_LABEL, bold=True)
    # header underline
    page.draw_line(
        fitz.Point(_ML, y + 22.7),
        fitz.Point(_CONTENT_R, y + 22.7),
        color=_RULE,
        width=0.75,
    )
    y += 22.7

    bar_x0 = _ML + 315.5
    bar_w = 194.4
    for item in spend_items:
        row_h = 24.3
        _draw_text(page, _ML + 6, y + 14.5, item["source"], size=9.0, color=_DARK)
        _draw_right(page, _ML + 175.5, y + 14.5, f"{item['records']}", size=9.0, color=_DARK)
        _draw_right(
            page,
            _ML + 267.5,
            y + 14.5,
            _fmt_usd(item["usd"], money=True),
            size=9.0,
            color=_DARK,
        )
        share_txt = f"{item['share']:.0f}%"
        _draw_right(page, _ML + 309.5, y + 14.5, share_txt, size=9.0, color=_DARK)

        # distribution bar
        track = fitz.Rect(bar_x0, y + 8.5, bar_x0 + bar_w, y + 16.5)
        _fill_rect(page, track, _BAR_TRACK)
        fill_w = bar_w * max(0.0, min(1.0, item["share"] / 100.0))
        if fill_w > 0.5:
            _fill_rect(page, fitz.Rect(bar_x0, y + 8.5, bar_x0 + fill_w, y + 16.5), _ORANGE)

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
    _draw_right(page, _ML + 175.5, y, f"{record_count}", size=9.0, color=_DARK, bold=True)
    _draw_right(
        page,
        _ML + 267.5,
        y,
        _fmt_usd(usd_total, money=True),
        size=9.0,
        color=_DARK,
        bold=True,
    )
    _draw_right(page, _ML + 309.5, y, "100%", size=9.0, color=_DARK, bold=True)
    y += 22.0

    # Transaction Detail heading
    _draw_text(page, _ML, y + 8, "Transaction Detail", size=11.5, color=_BLACK, bold=True)
    # right chip again
    chip = f"{record_count} line items"
    chip_w = _text_len(chip, 8.0, bold=True)
    _draw_text(page, _CONTENT_R - chip_w, y + 6, chip, size=8.0, color=_LABEL, bold=True)
    y += 20.0

    y = _draw_table_header(page, y)

    row_index = 0
    alt = False
    # Leave room for total row (+ ~30) above the footer
    bottom_limit = 750.0

    while row_index < len(rows):
        row = rows[row_index]
        needed = _row_height(row)
        if y + needed > bottom_limit:
            _draw_total_row(page, y + 2, usd_total)
            page = new_page()
            y = _header(page, logo)
            y = _draw_table_header(page, y + 4)
            alt = False
            continue

        y = _draw_detail_row(page, y, row, alt=alt)
        alt = not alt
        row_index += 1

    # Total + signatures on the last page
    if y + 30 > bottom_limit:
        _draw_total_row(page, y + 2, usd_total)
        page = new_page()
        y = _header(page, logo)
        y = _draw_table_header(page, y + 4)
    y = _draw_total_row(page, y + 2, usd_total)
    _draw_signatures(page, y)

    # Stamp footers with final page count
    page_count = doc.page_count
    for i in range(page_count):
        _footer(doc[i], i + 1, page_count)

    # Ensure parent dir exists
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    doc.close()
