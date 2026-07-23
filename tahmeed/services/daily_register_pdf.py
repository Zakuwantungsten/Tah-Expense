"""Daily Register PDF for the cashier table view.

Matches the Tahmeed Transporters report style used by Truck Overview
(logo header, orange accent, KPI cards, dark table header, signatures,
confidential footer) in A4 landscape so all register columns fit.
"""

from __future__ import annotations

import os
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional, Sequence

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
_WHITE = (1, 1, 1)
_RULE = (216 / 255, 216 / 255, 219 / 255)

_DASH = "—"
_FONT_REG = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "arial.ttf"
_FONT_BOLD = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "arialbd.ttf"
_fonts: dict[str, fitz.Font] = {}

# Export columns — same order as DailyRegister._EXPORT_COLS / HEADERS
EXPORT_HEADERS = [
    "Date",
    "Reported Date",
    "Item",
    "Description",
    "Truck No.",
    "Memo",
    "Ref_Float",
    "TZS",
    "Receipt",
    "Ownership",
    "APR BY",
    "Payee",
    "Cheque",
]

# Relative widths (sum ≈ content width after padding). S/N is PDF-only.
_COL_SPECS: list[tuple[str, str, float]] = [
    ("sno", "S/N", 26),
    ("date", "DATE", 46),
    ("reported", "REPORTED", 48),
    ("item", "ITEM", 62),
    ("desc", "DESCRIPTION", 118),
    ("truck", "TRUCK NO.", 50),
    ("memo", "MEMO", 50),
    ("ref", "REF_FLOAT", 50),
    ("tzs", "TZS", 56),
    ("receipt", "RECEIPT", 42),
    ("own", "OWNERSHIP", 48),
    ("apr", "APR BY", 40),
    ("payee", "PAYEE", 48),
    ("cheque", "CHEQUE", 40),
]


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


def _parse_tzs(value) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text or text == _DASH:
        return None
    cleaned = re.sub(r"[^\d.\-]", "", text.replace(",", ""))
    if not cleaned or cleaned in (".", "-", "-."):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _fmt_tzs(value: Optional[float], *, money: bool = False) -> str:
    if value is None:
        return _DASH
    if money:
        if abs(value - round(value)) < 1e-9:
            return f"{value:,.0f}"
        return f"{value:,.2f}"
    if abs(value - round(value)) < 1e-9:
        return f"{value:,.0f}"
    return f"{value:,.2f}"


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

    subtitle = "DAILY REGISTER REPORT  |  WWW.TAHMEEDCOACH.CO.KE"
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
    page.draw_rect(rect, color=_CARD_EDGE, width=0.4)
    _fill_rect(page, fitz.Rect(x, y, x + w, y + 2.4), _ORANGE)
    _draw_text(page, x + 10.0, y + 16.5, label, size=6.8, color=_LABEL, bold=True)
    _draw_text(page, x + 10.0, y + 34.5, value, size=13.0, color=value_color, bold=True)


def _row_cells(row: Sequence[str] | dict) -> list[str]:
    if isinstance(row, dict):
        return [_dash(row.get(h)) for h in EXPORT_HEADERS]
    cells = list(row)
    if len(cells) < len(EXPORT_HEADERS):
        cells.extend([""] * (len(EXPORT_HEADERS) - len(cells)))
    return [_dash(c) for c in cells[: len(EXPORT_HEADERS)]]


def _row_height(cells: list[str]) -> float:
    """``cells`` are data columns only (EXPORT_HEADERS order), not including S/N."""
    wrap_keys = ("item", "desc", "memo", "ref", "payee")
    sizes = {"item": 7.2, "desc": 7.2, "memo": 7.0, "ref": 7.0, "payee": 7.0}
    data_keys = [spec[0] for spec in _COL_SPECS if spec[0] != "sno"]
    key_to_idx = {key: i for i, key in enumerate(data_keys)}
    max_lines = 1
    for key in wrap_keys:
        idx = key_to_idx[key]
        lines = _wrap(cells[idx], sizes[key], _COL[key][1] - 2)
        max_lines = max(max_lines, len(lines))
    if max_lines <= 1:
        return 16.5
    return 10.0 + max_lines * 9.5


def _draw_table_header(page: fitz.Page, y: float) -> float:
    h = 22.0
    _fill_rect(page, fitz.Rect(_ML, y, _CONTENT_R, y + h), _DARK)
    for key, label, _ in _COL_SPECS:
        x, w = _COL[key]
        if key == "tzs":
            _draw_right(
                page, x + w - 2, y + 14.0, label, size=6.2, color=_WHITE, bold=True
            )
        else:
            _draw_text(page, x, y + 14.0, label, size=6.2, color=_WHITE, bold=True)
    return y + h


def _draw_detail_row(
    page: fitz.Page,
    y: float,
    cells: list[str],
    *,
    sn: int,
    alt: bool,
) -> float:
    h = _row_height(cells)
    if alt:
        _fill_rect(page, fitz.Rect(_ML, y, _CONTENT_R, y + h), _ROW_ALT)

    baseline = y + 11.0
    wrap_keys = {"item", "desc", "memo", "ref", "payee"}
    right_keys = {"tzs"}
    data_keys = [spec[0] for spec in _COL_SPECS if spec[0] != "sno"]

    for key, _, _ in _COL_SPECS:
        x, w = _COL[key]
        if key == "sno":
            _draw_text(page, x, baseline, str(sn), size=7.0, color=_MUTED, bold=True)
            continue

        text = cells[data_keys.index(key)]
        size = 7.2 if key in ("item", "desc") else 7.0
        color = _MUTED if key in ("date", "reported") else _DARK
        if key == "ref":
            color = _LABEL

        if key in wrap_keys:
            lines = _wrap(text, size, w - 2)
            yy = baseline
            for line in lines:
                _draw_text(page, x, yy, line, size=size, color=color)
                yy += 9.5
        elif key in right_keys:
            tzs = _parse_tzs(text if text != _DASH else None)
            display = _fmt_tzs(tzs) if tzs is not None else text
            _draw_right(
                page,
                x + w - 2,
                baseline,
                display,
                size=7.2,
                color=_DARK,
                bold=True,
            )
        else:
            line = _wrap(text, size, w - 2)[0]
            _draw_text(page, x, baseline, line, size=size, color=color)
    return y + h


def _draw_total_row(page: fitz.Page, y: float, tzs_total: float, count: int) -> float:
    page.draw_line(
        fitz.Point(_ML, y),
        fitz.Point(_CONTENT_R, y),
        color=_DARK,
        width=1.125,
    )
    y += 12.0
    _draw_text(page, _ML + 2, y, f"Total ({count} rows)", size=8.0, color=_DARK, bold=True)
    x, w = _COL["tzs"]
    _draw_right(
        page,
        x + w - 2,
        y,
        _fmt_tzs(tzs_total, money=True),
        size=8.0,
        color=_DARK,
        bold=True,
    )
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
        "PREPARED BY — CASHIER / DATA OPERATIONS",
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


def _fmt_register_date(value: Optional[date | datetime | str]) -> str:
    if value is None:
        return "Current view"
    if hasattr(value, "strftime"):
        return value.strftime("%d %b %Y")
    return _dash(value)


def _is_refund(ref_float: str) -> bool:
    return "refund" in (ref_float or "").lower()


def export_daily_register_pdf(
    path: str,
    *,
    rows: Sequence[Sequence[str] | dict],
    register_date: Optional[date | datetime | str] = None,
    generated_at: Optional[datetime] = None,
) -> None:
    """Write a landscape Daily Register PDF with all export columns plus S/N."""
    generated_at = generated_at or datetime.now()
    logo = _logo_path()
    cell_rows = [_row_cells(row) for row in rows]
    record_count = len(cell_rows)
    tzs_idx = EXPORT_HEADERS.index("TZS")
    ref_idx = EXPORT_HEADERS.index("Ref_Float")
    receipt_idx = EXPORT_HEADERS.index("Receipt")

    tzs_total = 0.0
    refund_total = 0.0
    for cells in cell_rows:
        val = _parse_tzs(cells[tzs_idx] if cells[tzs_idx] != _DASH else None)
        if val is None:
            continue
        tzs_total += val
        if _is_refund(cells[ref_idx]):
            refund_total += val

    with_receipt = sum(
        1
        for cells in cell_rows
        if cells[receipt_idx] not in (_DASH, "", "No", "NO", "no")
        and "missing" not in cells[receipt_idx].lower()
        and cells[receipt_idx].lower() not in ("none", "n/a")
    )

    date_label = _fmt_register_date(register_date)
    generated_label = generated_at.strftime("%d %b %Y")

    doc = fitz.open()

    def new_page() -> fitz.Page:
        return doc.new_page(width=_PAGE_W, height=_PAGE_H)

    page = new_page()
    y = _header(page, logo)

    _draw_text(page, _ML, y + 6, "DAILY REGISTER REPORT", size=7.5, color=_ORANGE, bold=True)
    y += 20.0
    title = "Cashier Table — Transaction Export"
    _draw_text(page, _ML, y + 6, title, size=18.0, color=_BLACK, bold=True)
    y += 28.0

    card_h = 46.0
    gap = 8.0
    card_w = (_CONTENT_W - 3 * gap) / 4
    labels_vals = [
        ("TOTAL RECORDS", f"{record_count:,}", _BLACK),
        ("TZS TOTAL", _fmt_tzs(tzs_total, money=True), _ORANGE),
        ("REFUND TOTAL", _fmt_tzs(refund_total, money=True), _BLACK),
        ("WITH RECEIPT", f"{with_receipt:,}", _BLACK),
    ]
    for i, (lab, val, col) in enumerate(labels_vals):
        x = _ML + i * (card_w + gap)
        _draw_kpi_card(page, x, y, card_w, card_h, lab, val, value_color=col)
    y += card_h + 14.0

    _draw_text(page, _ML, y + 6, "Transaction Detail", size=11.0, color=_BLACK, bold=True)
    detail_meta = f"{date_label}  ·  Generated {generated_label}  ·  {record_count} rows"
    meta_w = _text_len(detail_meta, 7.5, bold=True)
    _draw_text(
        page,
        _CONTENT_R - meta_w,
        y + 6,
        detail_meta,
        size=7.5,
        color=_LABEL,
        bold=True,
    )
    y += 16.0

    y = _draw_table_header(page, y)

    row_index = 0
    alt = False
    bottom_limit = _PAGE_H - 55.0

    while row_index < len(cell_rows):
        cells = cell_rows[row_index]
        needed = _row_height(cells)
        if y + needed > bottom_limit:
            page = new_page()
            y = _header(page, logo)
            y = _draw_table_header(page, y + 2)
            alt = False
            continue

        y = _draw_detail_row(page, y, cells, sn=row_index + 1, alt=alt)
        alt = not alt
        row_index += 1

    if y + 40 > bottom_limit:
        page = new_page()
        y = _header(page, logo)
        y = _draw_table_header(page, y + 2)

    y = _draw_total_row(page, y + 2, tzs_total, record_count)
    _draw_signatures(page, y)

    page_count = doc.page_count
    for i in range(page_count):
        _footer(doc[i], i + 1, page_count)

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    doc.close()
