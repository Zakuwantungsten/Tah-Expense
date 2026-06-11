"""Fuel Consumption widgets — Infinity, Lake Zambia, Lake Tunduma, GBP Diesel.

Each widget mirrors the Excel sheet columns from 'infinity,lake tunduma.xlsx':
  DIESEL INFINITY    → InfinityWidget     (Date·LPO·DO/SDO·Diesel@·Client·Dest·Truck·Ltrs·Price·Total)
  DIESEL LAKE ZAMBIA → LakeZambiaWidget   (same + S/No · Lake US$ · Remark)
  DIESEL LAKE TUNDUMA→ LakeTundumaWidget  (same structure as Infinity)
  DIESEL GBP         → GBPDieselWidget    (S/No + Infinity columns)

Design: QuickBooks-inspired — metric cards, clean table, import with sheet selector.
"""

from __future__ import annotations

import asyncio
import csv
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import qtawesome as qta

from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtGui import QColor, QDragEnterEvent, QDropEvent, QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QFileDialog, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
    QPushButton, QSizePolicy, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

try:
    import openpyxl
    _HAS_OPENPYXL = True
except ImportError:
    _HAS_OPENPYXL = False

# ── Design tokens ──────────────────────────────────────────────────────────────
_WHITE   = "#FFFFFF"
_BG      = "#F4F6F8"
_BORDER  = "#E5E7EB"
_BLUE    = "#0077C5"
_BLUE_L  = "#E8F4FD"
_T1      = "#111827"
_T2      = "#6B7280"
_TM      = "#9CA3AF"
_HDR_BG  = "#F1F5F9"
_ALT_ROW = "#F9FAFB"

_PAGE_SIZES = [25, 50, 100]
_ROW_H      = 36


# ── Column definitions ─────────────────────────────────────────────────────────

_DIESEL_HEADERS = [
    "DATE", "LPO NO.", "DO/SDO NO.", "DIESEL @",
    "CLIENT", "DESTINATIONS", "TRUCK NO.", "LTRS", "PRICE/LTR", "TOTAL (TZS)",
]
_DIESEL_COL_MAP: Dict[str, List[str]] = {
    "date":          ["date"],
    "lpo_no":        ["lpo no.", "lpo no", "lpo"],
    "do_sdo_no":     ["do/sdo no.", "do/sdo no", "do no.", "sdo no."],
    "diesel_at":     ["diesel @", "diesel at", "station"],
    "clients_name":  ["clients name", "client name", "client"],
    "destinations":  ["destinations", "destination"],
    "truck_no":      ["truck no.", "truck no", "truck"],
    "ltrs":          ["ltrs", "litres", "liters", "qty", "quantity"],
    "price_per_ltr": ["price per ltr", "price/ltr", "unit price", "rate"],
    "total_amount":  ["total amount", "total", "amount"],
}

_ZAMBIA_HEADERS = [
    "S/NO", "DATE", "LPO NO.", "DO/SDO NO.", "DIESEL @",
    "CLIENT", "DESTINATIONS", "TRUCK NO.", "LTRS", "PRICE/LTR",
    "TOTAL (TZS)", "LAKE US$", "REMARK",
]
_ZAMBIA_COL_MAP: Dict[str, List[str]] = {
    "sn":            ["s/no.", "s/n", "sn", "no.", "s/no"],
    "date":          ["date"],
    "lpo_no":        ["lpo no.", "lpo no", "lpo"],
    "do_sdo_no":     ["do/sdo no.", "do/sdo no", "do no.", "sdo no."],
    "diesel_at":     ["diesel @", "diesel at", "station"],
    "clients_name":  ["clients name", "client name", "client"],
    "destinations":  ["destinations", "destination"],
    "truck_no":      ["truck no.", "truck no", "truck"],
    "ltrs":          ["ltrs", "litres", "liters", "qty", "quantity"],
    "price_per_ltr": ["price per ltr", "price/ltr", "unit price", "rate"],
    "total_amount":  ["total amount", "total", "amount"],
    "lake_usd":      ["lake us $", "lake usd", "usd", "us $"],
    "remark":        ["remark", "remarks", "notes"],
}

_GBP_HEADERS = [
    "S/NO", "DATE", "LPO NO.", "DO/SDO NO.", "DIESEL @",
    "CLIENT", "DESTINATIONS", "TRUCK NO.", "LTRS", "PRICE/LTR", "TOTAL (TZS)",
]
_GBP_COL_MAP: Dict[str, List[str]] = {
    "sn":            ["s/no.", "s/n", "sn", "no.", "s/no"],
    "date":          ["date"],
    "lpo_no":        ["lpo no.", "lpo no", "lpo"],
    "do_sdo_no":     ["do/sdo no.", "do/sdo no", "do no.", "sdo no."],
    "diesel_at":     ["diesel @", "diesel at", "station"],
    "clients_name":  ["clients name", "client name", "client"],
    "destinations":  ["destinations", "destination"],
    "truck_no":      ["truck no.", "truck no", "truck"],
    "ltrs":          ["ltrs", "litres", "liters", "qty", "quantity"],
    "price_per_ltr": ["price per ltr", "price/ltr", "unit price", "rate"],
    "total_amount":  ["total amount", "total", "amount"],
}

# Auto-select hint: feed_type → expected sheet name substring
_SHEET_HINTS: Dict[str, str] = {
    "diesel_infinity":    "DIESEL INFINITY",
    "diesel_lake_zambia": "DIESEL LAKE ZAMBIA",
    "diesel_lake_tunduma":"DIESEL LAKE TUNDUMA",
    "diesel_gbp":         "DIESEL GBP",
}


# ═══════════════════════════════════════════════════════════════════════════════
#  UI helpers (self-contained — mirrors separate_expenses.py palette)
# ═══════════════════════════════════════════════════════════════════════════════

def _lbl(text: str = "", size: int = 13, weight: int = 400,
         color: str = _T1) -> QLabel:
    w = QLabel(text)
    w.setStyleSheet(
        f"color:{color};font-size:{size}px;font-weight:{weight};"
        "font-family:'Segoe UI',sans-serif;background:transparent;"
    )
    return w


def _btn(text: str, icon: str = "", primary: bool = True,
         height: int = 34) -> QPushButton:
    b = QPushButton(f"  {text}" if icon else text)
    b.setCursor(Qt.PointingHandCursor)
    b.setFixedHeight(height)
    if icon:
        try:
            b.setIcon(qta.icon(icon, color="#FFFFFF" if primary else _T1))
            b.setIconSize(QSize(16, 16))
        except Exception:
            pass
    if primary:
        ss = (f"QPushButton{{background:{_BLUE};color:#FFF;border:none;border-radius:5px;"
              f"font-size:12px;font-weight:600;font-family:'Segoe UI',sans-serif;padding:0 14px;}}"
              f"QPushButton:hover{{background:#005EA3;}}"
              f"QPushButton:disabled{{background:#93C5FD;}}")
    else:
        ss = (f"QPushButton{{background:{_WHITE};color:{_T1};border:1px solid {_BORDER};"
              f"border-radius:5px;font-size:12px;font-family:'Segoe UI',sans-serif;padding:0 14px;}}"
              f"QPushButton:hover{{background:{_BG};}}"
              f"QPushButton:disabled{{color:{_TM};}}")
    b.setStyleSheet(ss)
    return b


def _input_ss() -> str:
    return (
        f"QLineEdit,QComboBox{{border:1px solid {_BORDER};border-radius:5px;"
        f"background:{_WHITE};color:{_T1};font-size:12px;"
        f"font-family:'Segoe UI',sans-serif;padding:0 8px;"
        f"min-height:32px;max-height:32px;}}"
        f"QLineEdit:focus,QComboBox:focus{{border-color:{_BLUE};}}"
        "QComboBox::drop-down{border:none;width:20px;}"
    )


def _hsep() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.HLine)
    f.setFixedHeight(1)
    f.setStyleSheet(f"background:{_BORDER};")
    return f


def _table_style() -> str:
    return (
        f"QTableWidget{{background:{_WHITE};gridline-color:{_BORDER};"
        "border:none;font-size:12px;font-family:'Segoe UI',sans-serif;}}"
        f"QTableWidget::item{{padding:0 6px;color:{_T1};}}"
        f"QTableWidget::item:selected{{background:{_BLUE_L};color:{_T1};}}"
        f"QHeaderView::section{{background:{_HDR_BG};color:{_T2};"
        "font-size:11px;font-weight:600;font-family:'Segoe UI',sans-serif;"
        f"border:none;border-bottom:1px solid {_BORDER};"
        "padding:0 6px;height:30px;}}"
        "QScrollBar:vertical{width:8px;background:transparent;}"
        f"QScrollBar::handle:vertical{{background:#D1D5DB;border-radius:4px;}}"
    )


def _make_table(headers: List[str]) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.verticalHeader().setVisible(False)
    t.verticalHeader().setDefaultSectionSize(_ROW_H)
    t.horizontalHeader().setStretchLastSection(True)
    t.setStyleSheet(
        _table_style()
        + f"QTableWidget{{alternate-background-color:{_ALT_ROW};}}"
    )
    t.setAlternatingRowColors(True)
    return t


def _cell(text: str, align: Qt.AlignmentFlag = Qt.AlignLeft | Qt.AlignVCenter,
          mono: bool = False, color: str = "") -> QTableWidgetItem:
    item = QTableWidgetItem(str(text) if text is not None else "—")
    item.setTextAlignment(align)
    if mono:
        item.setFont(QFont("Cascadia Code", 11))
    if color:
        item.setForeground(QColor(color))
    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    return item


def _fmt_num(v: Any, prefix: str = "", decimals: int = 2) -> str:
    if v is None or v == "" or v == "None":
        return "—"
    try:
        return f"{prefix}{float(v):,.{decimals}f}"
    except Exception:
        return str(v)


def _fmt_date_str(val: Any) -> str:
    if not val or str(val) in ("None", ""):
        return "—"
    if isinstance(val, datetime):
        return val.strftime("%d %b %y")
    return str(val)


# ═══════════════════════════════════════════════════════════════════════════════
#  Page header
# ═══════════════════════════════════════════════════════════════════════════════

class _PageHeader(QWidget):
    def __init__(self, title: str, icon_name: str = "mdi.gas-station",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet("background:transparent;")
        hl = QHBoxLayout(self)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(10)
        try:
            icon_lbl = QLabel()
            icon_lbl.setPixmap(qta.icon(icon_name, color=_BLUE).pixmap(22, 22))
            icon_lbl.setFixedSize(22, 22)
            icon_lbl.setStyleSheet("background:transparent;")
            hl.addWidget(icon_lbl)
        except Exception:
            pass
        hl.addWidget(_lbl(title, size=18, weight=700))
        hl.addStretch()
        self._right = hl

    def add_right(self, widget: QWidget) -> None:
        self._right.addWidget(widget)


# ═══════════════════════════════════════════════════════════════════════════════
#  Pagination bar
# ═══════════════════════════════════════════════════════════════════════════════

class _PaginationBar(QWidget):
    page_changed = Signal(int)
    size_changed = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet("background:transparent;")
        self._page = 1
        self._total = 0
        self._page_size = 25

        hl = QHBoxLayout(self)
        hl.setContentsMargins(0, 4, 0, 4)
        hl.setSpacing(8)

        self._size_cb = QComboBox()
        self._size_cb.setFixedWidth(80)
        for s in _PAGE_SIZES:
            self._size_cb.addItem(f"Show {s}", s)
        self._size_cb.setStyleSheet(_input_ss())
        self._size_cb.currentIndexChanged.connect(self._on_size_change)
        hl.addWidget(self._size_cb)

        self._info_lbl = _lbl("", size=11, color=_T2)
        hl.addWidget(self._info_lbl)
        hl.addStretch()

        self._prev_btn = _btn("← Prev", primary=False, height=30)
        self._prev_btn.clicked.connect(self._prev)
        hl.addWidget(self._prev_btn)

        self._page_lbl = _lbl("Page 1 of 1", size=11, color=_T2)
        hl.addWidget(self._page_lbl)

        self._next_btn = _btn("Next →", primary=False, height=30)
        self._next_btn.clicked.connect(self._next)
        hl.addWidget(self._next_btn)

    def set_total(self, total: int, page_size: int, current_page: int) -> None:
        self._total = total
        self._page_size = page_size
        self._page = current_page
        pages = max(1, (total + page_size - 1) // page_size)
        self._page_lbl.setText(f"Page {current_page} of {pages}")
        self._info_lbl.setText(f"{total:,} records")
        self._prev_btn.setEnabled(current_page > 1)
        self._next_btn.setEnabled(current_page < pages)

    def _prev(self) -> None:
        if self._page > 1:
            self._page -= 1
            self.page_changed.emit(self._page)

    def _next(self) -> None:
        pages = max(1, (self._total + self._page_size - 1) // self._page_size)
        if self._page < pages:
            self._page += 1
            self.page_changed.emit(self._page)

    def _on_size_change(self) -> None:
        self._page = 1
        self._page_size = self._size_cb.currentData()
        self.size_changed.emit(self._page_size)


# ═══════════════════════════════════════════════════════════════════════════════
#  Totals bar
# ═══════════════════════════════════════════════════════════════════════════════

class _TotalsBar(QFrame):
    def __init__(self, labels: List[Tuple[str, str]],
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(38)
        self.setStyleSheet(
            f"QFrame{{background:{_HDR_BG};border-top:1px solid {_BORDER};"
            "border-bottom-left-radius:6px;border-bottom-right-radius:6px;}}"
        )
        hl = QHBoxLayout(self)
        hl.setContentsMargins(16, 0, 16, 0)
        hl.setSpacing(32)

        self._lbl_map: Dict[str, QLabel] = {}
        for key, prefix in labels:
            sub = QWidget()
            sub.setStyleSheet("background:transparent;")
            sub_hl = QHBoxLayout(sub)
            sub_hl.setContentsMargins(0, 0, 0, 0)
            sub_hl.setSpacing(4)
            sub_hl.addWidget(_lbl("TOTAL:", size=11, weight=600, color=_T2))
            val = _lbl("—", size=12, weight=700, color=_T1)
            sub_hl.addWidget(val)
            self._lbl_map[key] = val
            hl.addWidget(sub)
        hl.addStretch()

    def set_total(self, key: str, value: float, prefix: str = "") -> None:
        if key in self._lbl_map:
            self._lbl_map[key].setText(_fmt_num(value, prefix=prefix))


# ═══════════════════════════════════════════════════════════════════════════════
#  Summary bar  (QuickBooks horizontal KPI strip)
# ═══════════════════════════════════════════════════════════════════════════════

class _SummaryBar(QFrame):
    """
    Single white card divided by vertical rules — one cell per KPI.
    Each cell: UPPERCASE label · currency/unit badge · bold value.
    Mirrors the QuickBooks bill/report summary bar aesthetic.
    """

    # Palette kept intentionally neutral so every station looks consistent.
    _BADGE_BG   = "#EEF2F7"   # soft slate — matches QB field label chips
    _BADGE_FG   = "#475569"
    _LABEL_CLR  = "#94A3B8"   # muted slate
    _VAL_CLR    = "#0F172A"   # near-black
    _DIV_CLR    = "#E2E8F0"   # light divider

    def __init__(
        self,
        metrics: List[Tuple[str, str, str]],   # (key, label, badge)
        parent: QWidget | None = None,
    ) -> None:
        """
        metrics: list of (key, display_label, unit_badge)
            badge="" for plain count, "TZS"/"USD"/"L" for units.
        """
        super().__init__(parent)
        self.setFixedHeight(82)
        self.setObjectName("summaryBar")
        self.setStyleSheet(
            "QFrame#summaryBar{"
            f"  background:{_WHITE};"
            "  border:1px solid #D1D9E6;"
            "  border-radius:8px;"
            "}"
        )

        hl = QHBoxLayout(self)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(0)

        self._val_map: Dict[str, QLabel] = {}

        for i, (key, label, badge) in enumerate(metrics):
            # Divider between cells
            if i > 0:
                div = QFrame()
                div.setFrameShape(QFrame.VLine)
                div.setFixedWidth(1)
                div.setStyleSheet(f"background:{self._DIV_CLR};")
                hl.addWidget(div)

            cell = QWidget()
            cell.setStyleSheet("background:transparent;")
            cvl = QVBoxLayout(cell)
            cvl.setContentsMargins(24, 14, 24, 12)
            cvl.setSpacing(5)

            # ── label row ────────────────────────────────────────────────
            lbl_w = QLabel(label.upper())
            lbl_w.setStyleSheet(
                f"color:{self._LABEL_CLR};"
                "font-size:10px;font-weight:600;letter-spacing:0.5px;"
                "font-family:'Segoe UI',sans-serif;background:transparent;"
            )
            cvl.addWidget(lbl_w)

            # ── value row: [badge] [number] ───────────────────────────────
            val_row = QWidget()
            val_row.setStyleSheet("background:transparent;")
            vhl = QHBoxLayout(val_row)
            vhl.setContentsMargins(0, 0, 0, 0)
            vhl.setSpacing(7)
            vhl.setAlignment(Qt.AlignVCenter)

            if badge:
                bl = QLabel(badge)
                bl.setStyleSheet(
                    f"background:{self._BADGE_BG};color:{self._BADGE_FG};"
                    "font-size:10px;font-weight:700;letter-spacing:0.3px;"
                    "border-radius:4px;padding:1px 7px;"
                    "font-family:'Segoe UI',sans-serif;"
                )
                bl.setFixedHeight(20)
                vhl.addWidget(bl)

            val_lbl = QLabel("—")
            val_lbl.setStyleSheet(
                f"color:{self._VAL_CLR};"
                "font-size:20px;font-weight:700;"
                "font-family:'Segoe UI',sans-serif;background:transparent;"
            )
            self._val_map[key] = val_lbl
            vhl.addWidget(val_lbl)
            vhl.addStretch()

            cvl.addWidget(val_row)
            hl.addWidget(cell, 1)

    def set_value(self, key: str, text: str) -> None:
        if key in self._val_map:
            self._val_map[key].setText(text)


# ═══════════════════════════════════════════════════════════════════════════════
#  Drop zone
# ═══════════════════════════════════════════════════════════════════════════════

class _DropZone(QFrame):
    file_dropped = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setMinimumHeight(90)
        self._normal_ss = (
            f"QFrame{{border:2px dashed {_BORDER};border-radius:8px;background:{_BG};}}"
            f"QFrame:hover{{border-color:{_BLUE};background:{_BLUE_L};}}"
        )
        self._hover_ss = (
            f"QFrame{{border:2px dashed {_BLUE};border-radius:8px;background:{_BLUE_L};}}"
        )
        self.setStyleSheet(self._normal_ss)

        vl = QVBoxLayout(self)
        vl.setAlignment(Qt.AlignCenter)
        vl.setSpacing(4)
        try:
            ic = QLabel()
            ic.setPixmap(qta.icon("mdi.upload-outline", color=_TM).pixmap(28, 28))
            ic.setAlignment(Qt.AlignCenter)
            ic.setStyleSheet("background:transparent;border:none;")
            vl.addWidget(ic)
        except Exception:
            pass
        vl.addWidget(_lbl("Drag & drop .xlsx or .csv here", size=12, color=_T2))
        self._path_lbl = _lbl("", size=11, color=_TM)
        self._path_lbl.setAlignment(Qt.AlignCenter)
        vl.addWidget(self._path_lbl)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.setStyleSheet(self._hover_ss)

    def dragLeaveEvent(self, event) -> None:
        self.setStyleSheet(self._normal_ss)

    def dropEvent(self, event: QDropEvent) -> None:
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            self._path_lbl.setText(Path(path).name)
            self.file_dropped.emit(path)
        self.setStyleSheet(self._normal_ss)

    def set_path(self, path: str) -> None:
        self._path_lbl.setText(Path(path).name)


# ═══════════════════════════════════════════════════════════════════════════════
#  Fuel Import Dialog  (sheet-aware — auto-selects the matching sheet)
# ═══════════════════════════════════════════════════════════════════════════════

class _FuelImportDialog(QDialog):
    """
    Import dialog for diesel feed data.
    Supports sheet selection for multi-sheet workbooks so the user can pick
    the correct sheet (e.g. DIESEL INFINITY) without having to re-save the file.
    """

    imported = Signal(int)

    def __init__(
        self,
        feed_type: str,
        title: str,
        col_map: Dict[str, List[str]],
        preview_headers: List[str],
        dedup_key: str = "lpo_no",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._feed_type       = feed_type
        self._col_map         = col_map
        self._preview_headers = preview_headers
        self._dedup_key       = dedup_key

        self._wb          = None
        self._new_rows:   List[dict] = []

        self.setWindowTitle(f"Import — {title}")
        self.setMinimumWidth(720)
        self.setStyleSheet(f"background:{_WHITE};")
        self._build()

    def _build(self) -> None:
        vl = QVBoxLayout(self)
        vl.setSpacing(12)
        vl.setContentsMargins(20, 20, 20, 20)

        # Drop zone
        self._drop = _DropZone()
        self._drop.file_dropped.connect(self._on_file)
        vl.addWidget(self._drop)

        # Browse + sheet selector
        ctrl = QWidget()
        ctrl.setStyleSheet("background:transparent;")
        ctrl_hl = QHBoxLayout(ctrl)
        ctrl_hl.setContentsMargins(0, 0, 0, 0)
        ctrl_hl.setSpacing(8)

        browse = _btn("Browse File", "mdi.folder-open-outline", primary=False)
        browse.clicked.connect(self._browse)
        ctrl_hl.addWidget(browse)

        ctrl_hl.addSpacing(12)
        ctrl_hl.addWidget(_lbl("Sheet:", size=12, color=_T2))

        self._sheet_cb = QComboBox()
        self._sheet_cb.setFixedWidth(220)
        self._sheet_cb.setStyleSheet(_input_ss())
        self._sheet_cb.setEnabled(False)
        self._sheet_cb.currentIndexChanged.connect(self._on_sheet_changed)
        ctrl_hl.addWidget(self._sheet_cb)
        ctrl_hl.addStretch()
        vl.addWidget(ctrl)

        # Stats row
        self._stats_lbl = _lbl("No file loaded.", size=12, color=_T2)
        vl.addWidget(self._stats_lbl)

        vl.addWidget(_hsep())

        vl.addWidget(_lbl("Preview — first 10 new rows", size=12, weight=600))

        self._preview_tbl = _make_table(self._preview_headers)
        self._preview_tbl.setMinimumHeight(180)
        vl.addWidget(self._preview_tbl)

        vl.addWidget(_hsep())

        btn_row = QWidget()
        btn_row.setStyleSheet("background:transparent;")
        bbl = QHBoxLayout(btn_row)
        bbl.setContentsMargins(0, 0, 0, 0)
        bbl.setSpacing(8)
        bbl.addStretch()

        cancel = _btn("Cancel", primary=False)
        cancel.clicked.connect(self.reject)
        bbl.addWidget(cancel)

        self._import_btn = _btn("Import Records", "mdi.check-circle-outline")
        self._import_btn.setEnabled(False)
        self._import_btn.clicked.connect(self._do_import)
        bbl.addWidget(self._import_btn)

        vl.addWidget(btn_row)

    # ── file handling ──────────────────────────────────────────────────────────

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open File", "", "Excel / CSV (*.xlsx *.xls *.csv)"
        )
        if path:
            self._drop.set_path(path)
            self._on_file(path)

    def _on_file(self, path: str) -> None:
        self._stats_lbl.setText("Reading file…")
        self._sheet_cb.blockSignals(True)
        self._sheet_cb.clear()
        self._sheet_cb.setEnabled(False)
        self._sheet_cb.blockSignals(False)
        self._import_btn.setEnabled(False)

        p = Path(path)
        if p.suffix.lower() in (".xlsx", ".xls") and _HAS_OPENPYXL:
            try:
                self._wb = openpyxl.load_workbook(path, data_only=True)
            except Exception as exc:
                self._stats_lbl.setText(f"Error opening file: {exc}")
                return

            names = self._wb.sheetnames
            self._sheet_cb.blockSignals(True)
            for name in names:
                self._sheet_cb.addItem(name)
            self._sheet_cb.setEnabled(True)
            self._sheet_cb.blockSignals(False)

            # Auto-select the sheet matching this feed type
            hint = _SHEET_HINTS.get(self._feed_type, "").upper()
            best_idx = 0
            for i, name in enumerate(names):
                if hint and hint in name.upper():
                    best_idx = i
                    break
            self._sheet_cb.setCurrentIndex(best_idx)
            self._on_sheet_changed()

        else:
            self._wb = None
            try:
                with open(path, newline="", encoding="utf-8-sig") as f:
                    rows = list(csv.reader(f))
            except Exception as exc:
                self._stats_lbl.setText(f"Error reading CSV: {exc}")
                return
            if not rows:
                self._stats_lbl.setText("File is empty.")
                return
            data = [
                [str(c) for c in r]
                for r in rows[1:]
                if any(c for c in r)
            ]
            self._parse(rows[0], data)

    def _on_sheet_changed(self, _idx: int = 0) -> None:
        if self._wb is None:
            return
        name = self._sheet_cb.currentText()
        try:
            ws = self._wb[name]
        except Exception:
            return
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            self._stats_lbl.setText("Sheet is empty.")
            return
        headers = [str(c) if c is not None else "" for c in rows[0]]
        data = [
            [str(c) if c is not None else "" for c in r]
            for r in rows[1:]
            if any(c is not None for c in r)
        ]
        self._parse(headers, data)

    # ── column mapping + dedup ─────────────────────────────────────────────────

    def _parse(self, headers: List[str], rows: List[List[str]]) -> None:
        hdr_lower = {h.strip().lower(): i for i, h in enumerate(headers)}

        def _find(cands: List[str]) -> Optional[int]:
            for c in cands:
                idx = hdr_lower.get(c.lower())
                if idx is not None:
                    return idx
            return None

        idxs = {key: _find(cands) for key, cands in self._col_map.items()}

        records: List[dict] = []
        for row in rows:
            rec: dict = {"feed_type": self._feed_type}
            for key, idx in idxs.items():
                rec[key] = (
                    row[idx].strip()
                    if idx is not None and idx < len(row) and row[idx] is not None
                    else ""
                )
            records.append(rec)

        dedup_vals = [r.get(self._dedup_key, "") for r in records if r.get(self._dedup_key)]
        asyncio.ensure_future(self._check_dupes(records, dedup_vals))

    async def _check_dupes(self, records: List[dict], keys: List[str]) -> None:
        from tahmeed.services import accountant_service as svc
        try:
            existing = await svc.get_existing_feed_keys(keys)
        except Exception:
            existing = set()

        self._new_rows = [r for r in records if r.get(self._dedup_key) not in existing]
        dupe_count = len(records) - len(self._new_rows)

        self._stats_lbl.setText(
            f"New records: {len(self._new_rows):,}     "
            f"Duplicates (skipped): {dupe_count:,}"
        )
        self._import_btn.setEnabled(bool(self._new_rows))
        self._import_btn.setText(f"Import {len(self._new_rows):,} Records")
        self._fill_preview(self._new_rows[:10])

    def _fill_preview(self, rows: List[dict]) -> None:
        t = self._preview_tbl
        t.setRowCount(0)
        keys = list(self._col_map.keys())
        for row in rows:
            r = t.rowCount()
            t.insertRow(r)
            for c, key in enumerate(keys):
                if c < t.columnCount():
                    t.setItem(r, c, _cell(row.get(key, "")))

    # ── import ─────────────────────────────────────────────────────────────────

    def _do_import(self) -> None:
        self._import_btn.setEnabled(False)
        self._import_btn.setText("Importing…")
        asyncio.ensure_future(self._async_import())

    async def _async_import(self) -> None:
        from tahmeed.services import accountant_service as svc
        try:
            saved = await svc.save_imported_feed(self._new_rows)
            self.imported.emit(saved)
            self.accept()
        except Exception as exc:
            QMessageBox.critical(self, "Import Error", str(exc))
            self._import_btn.setEnabled(True)
            self._import_btn.setText(f"Import {len(self._new_rows):,} Records")


# ═══════════════════════════════════════════════════════════════════════════════
#  Base diesel widget
# ═══════════════════════════════════════════════════════════════════════════════

class _BaseDieselWidget(QWidget):
    """
    Shared scaffold for all four fuel consumption pages.

    Subclasses set class attributes:
        _FEED_TYPE, _TITLE, _ICON, _HEADERS, _COL_MAP, _DEDUP_KEY
    and override _fill_row() if their column order differs from the base.
    LakeZambiaWidget also overrides _make_summary(), _totals_defs(), _load().
    """

    _FEED_TYPE: str              = ""
    _TITLE:     str              = ""
    _ICON:      str              = "mdi.gas-station"
    _HEADERS:   List[str]        = []
    _COL_MAP:   Dict[str, List[str]] = {}
    _DEDUP_KEY: str              = "lpo_no"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._page       = 1
        self._page_size  = 25
        self._total      = 0
        self._search     = ""
        self._truck_filter = ""
        self._build()
        self.refresh()

    # ── Build ──────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        self.setStyleSheet(f"background:{_BG};")
        vl = QVBoxLayout(self)
        vl.setContentsMargins(20, 20, 20, 16)
        vl.setSpacing(12)

        # Header row
        header = _PageHeader(self._TITLE, self._ICON)
        self._import_btn = _btn("Import from Excel", "mdi.upload-outline")
        self._import_btn.clicked.connect(self._open_import)
        header.add_right(self._import_btn)
        export_btn = _btn("Export", "mdi.download-outline", primary=False)
        header.add_right(export_btn)
        vl.addWidget(header)
        vl.addWidget(_hsep())

        # Summary metrics strip
        self._summary = self._make_summary()
        vl.addWidget(self._summary)

        # Toolbar: search + truck filter
        tb = QWidget()
        tb.setStyleSheet("background:transparent;")
        tbl = QHBoxLayout(tb)
        tbl.setContentsMargins(0, 0, 0, 0)
        tbl.setSpacing(8)

        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Search truck, LPO, client, destination…")
        self._search_edit.setFixedWidth(280)
        self._search_edit.setStyleSheet(_input_ss())
        self._search_edit.textChanged.connect(self._on_search)
        tbl.addWidget(self._search_edit)

        self._truck_cb = QComboBox()
        self._truck_cb.addItem("All Trucks", "")
        self._truck_cb.setFixedWidth(140)
        self._truck_cb.setStyleSheet(_input_ss())
        self._truck_cb.currentIndexChanged.connect(self._on_filter)
        tbl.addWidget(self._truck_cb)
        tbl.addStretch()
        vl.addWidget(tb)

        # Table
        self._table = _make_table(self._HEADERS)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setStretchLastSection(True)
        vl.addWidget(self._table, 1)

        # Totals footer
        self._totals = _TotalsBar(self._totals_defs())
        vl.addWidget(self._totals)

        # Pagination
        self._pager = _PaginationBar()
        self._pager.page_changed.connect(self._go_page)
        self._pager.size_changed.connect(self._on_page_size)
        vl.addWidget(self._pager)

    def _make_summary(self) -> _SummaryBar:
        return _SummaryBar([
            ("records", "Total Records",  ""),
            ("ltrs",    "Total Litres",   "L"),
            ("amount",  "Total Amount",   "TZS"),
        ])

    def _totals_defs(self) -> List[Tuple[str, str]]:
        return [("ltrs", "Ltrs "), ("amount", "TZS ")]

    # ── Import ─────────────────────────────────────────────────────────────────

    def _open_import(self) -> None:
        dlg = _FuelImportDialog(
            feed_type=self._FEED_TYPE,
            title=self._TITLE,
            col_map=self._COL_MAP,
            preview_headers=self._HEADERS,
            dedup_key=self._DEDUP_KEY,
            parent=self,
        )
        dlg.imported.connect(lambda n: (
            QMessageBox.information(self, "Import Complete",
                                    f"Imported {n:,} new records."),
            self.refresh(),
        ))
        dlg.exec()

    # ── Data ───────────────────────────────────────────────────────────────────

    def refresh(self) -> None:
        asyncio.ensure_future(self._load())

    async def _load(self) -> None:
        from tahmeed.services import accountant_service as svc
        skip = (self._page - 1) * self._page_size
        recs, total = await asyncio.gather(
            svc.get_diesel_feed(
                self._FEED_TYPE, self._search, self._truck_filter,
                self._page_size, skip,
            ),
            svc.count_diesel_feed(
                self._FEED_TYPE, self._search, self._truck_filter,
            ),
        )
        self._total = total
        self._fill_table(recs)
        self._pager.set_total(total, self._page_size, self._page)

        totals = await svc.get_diesel_totals(self._FEED_TYPE)
        ltrs   = totals.get("ltrs", 0.0)
        amount = totals.get("total_amount", 0.0)

        self._summary.set_value("records", f"{total:,}")
        self._summary.set_value("ltrs",    _fmt_num(ltrs,   decimals=0))
        self._summary.set_value("amount",  _fmt_num(amount, "TZS ", 0))

        self._totals.set_total("ltrs",   ltrs,   "Ltrs ")
        self._totals.set_total("amount", amount, "TZS ")

    def _fill_table(self, recs: List[dict]) -> None:
        t = self._table
        t.setRowCount(0)
        for rec in recs:
            r = t.rowCount()
            t.insertRow(r)
            self._fill_row(t, r, rec)

    def _fill_row(self, t: QTableWidget, r: int, rec: dict) -> None:
        """Base row layout: Date LPO DO/SDO Diesel@ Client Dest Truck Ltrs Price Total."""
        t.setItem(r, 0, _cell(_fmt_date_str(rec.get("date", ""))))
        t.setItem(r, 1, _cell(rec.get("lpo_no", "")))
        t.setItem(r, 2, _cell(rec.get("do_sdo_no", "")))
        t.setItem(r, 3, _cell(rec.get("diesel_at", "")))
        t.setItem(r, 4, _cell(rec.get("clients_name", "")))
        t.setItem(r, 5, _cell(rec.get("destinations", "")))
        t.setItem(r, 6, _cell(rec.get("truck_no", "")))
        t.setItem(r, 7, _cell(_fmt_num(rec.get("ltrs"), decimals=2),
                              Qt.AlignRight | Qt.AlignVCenter, mono=True))
        t.setItem(r, 8, _cell(_fmt_num(rec.get("price_per_ltr"), decimals=2),
                              Qt.AlignRight | Qt.AlignVCenter, mono=True))
        t.setItem(r, 9, _cell(_fmt_num(rec.get("total_amount"), "TZS ", 0),
                              Qt.AlignRight | Qt.AlignVCenter, mono=True))

    # ── Controls ───────────────────────────────────────────────────────────────

    def _on_search(self, text: str) -> None:
        self._search = text
        self._page = 1
        asyncio.ensure_future(self._load())

    def _on_filter(self) -> None:
        self._truck_filter = self._truck_cb.currentData() or ""
        self._page = 1
        asyncio.ensure_future(self._load())

    def _go_page(self, page: int) -> None:
        self._page = page
        asyncio.ensure_future(self._load())

    def _on_page_size(self, size: int) -> None:
        self._page_size = size
        self._page = 1
        asyncio.ensure_future(self._load())


# ═══════════════════════════════════════════════════════════════════════════════
#  Concrete widgets
# ═══════════════════════════════════════════════════════════════════════════════

class InfinityWidget(_BaseDieselWidget):
    """DIESEL INFINITY sheet — no S/No., no USD column."""
    _FEED_TYPE = "diesel_infinity"
    _TITLE     = "Infinity Diesel"
    _ICON      = "mdi.gas-station"
    _HEADERS   = _DIESEL_HEADERS
    _COL_MAP   = _DIESEL_COL_MAP


class LakeTundumaWidget(_BaseDieselWidget):
    """DIESEL LAKE TUNDUMA sheet — same column structure as Infinity."""
    _FEED_TYPE = "diesel_lake_tunduma"
    _TITLE     = "Lake Tunduma Diesel"
    _ICON      = "mdi.water-pump"
    _HEADERS   = _DIESEL_HEADERS
    _COL_MAP   = _DIESEL_COL_MAP


class GBPDieselWidget(_BaseDieselWidget):
    """DIESEL GBP sheet — adds S/No. as first column."""
    _FEED_TYPE = "diesel_gbp"
    _TITLE     = "GBP Diesel"
    _ICON      = "mdi.fuel"
    _HEADERS   = _GBP_HEADERS
    _COL_MAP   = _GBP_COL_MAP

    def _fill_row(self, t: QTableWidget, r: int, rec: dict) -> None:
        t.setItem(r, 0,  _cell(rec.get("sn", "")))
        t.setItem(r, 1,  _cell(_fmt_date_str(rec.get("date", ""))))
        t.setItem(r, 2,  _cell(rec.get("lpo_no", "")))
        t.setItem(r, 3,  _cell(rec.get("do_sdo_no", "")))
        t.setItem(r, 4,  _cell(rec.get("diesel_at", "")))
        t.setItem(r, 5,  _cell(rec.get("clients_name", "")))
        t.setItem(r, 6,  _cell(rec.get("destinations", "")))
        t.setItem(r, 7,  _cell(rec.get("truck_no", "")))
        t.setItem(r, 8,  _cell(_fmt_num(rec.get("ltrs"), decimals=2),
                               Qt.AlignRight | Qt.AlignVCenter, mono=True))
        t.setItem(r, 9,  _cell(_fmt_num(rec.get("price_per_ltr"), decimals=2),
                               Qt.AlignRight | Qt.AlignVCenter, mono=True))
        t.setItem(r, 10, _cell(_fmt_num(rec.get("total_amount"), "TZS ", 0),
                               Qt.AlignRight | Qt.AlignVCenter, mono=True))


class LakeZambiaWidget(_BaseDieselWidget):
    """DIESEL LAKE ZAMBIA sheet — extra Lake US$ and Remark columns."""
    _FEED_TYPE = "diesel_lake_zambia"
    _TITLE     = "Lake Zambia Diesel"
    _ICON      = "mdi.water-pump"
    _HEADERS   = _ZAMBIA_HEADERS
    _COL_MAP   = _ZAMBIA_COL_MAP

    def _make_summary(self) -> _SummaryBar:
        return _SummaryBar([
            ("records",  "Total Records", ""),
            ("ltrs",     "Total Litres",  "L"),
            ("amount",   "Total Amount",  "TZS"),
            ("lake_usd", "Lake US$",      "USD"),
        ])

    def _totals_defs(self) -> List[Tuple[str, str]]:
        return [("ltrs", "Ltrs "), ("amount", "TZS "), ("lake_usd", "USD ")]

    async def _load(self) -> None:
        from tahmeed.services import accountant_service as svc
        skip = (self._page - 1) * self._page_size
        recs, total = await asyncio.gather(
            svc.get_diesel_feed(
                self._FEED_TYPE, self._search, self._truck_filter,
                self._page_size, skip,
            ),
            svc.count_diesel_feed(
                self._FEED_TYPE, self._search, self._truck_filter,
            ),
        )
        self._total = total
        self._fill_table(recs)
        self._pager.set_total(total, self._page_size, self._page)

        totals   = await svc.get_diesel_totals(self._FEED_TYPE)
        ltrs     = totals.get("ltrs", 0.0)
        amount   = totals.get("total_amount", 0.0)
        lake_usd = totals.get("lake_usd", 0.0)

        self._summary.set_value("records",  f"{total:,}")
        self._summary.set_value("ltrs",     _fmt_num(ltrs,     decimals=0))
        self._summary.set_value("amount",   _fmt_num(amount,   "TZS ", 0))
        self._summary.set_value("lake_usd", _fmt_num(lake_usd, "$ ", 2))

        self._totals.set_total("ltrs",     ltrs,     "Ltrs ")
        self._totals.set_total("amount",   amount,   "TZS ")
        self._totals.set_total("lake_usd", lake_usd, "USD ")

    def _fill_row(self, t: QTableWidget, r: int, rec: dict) -> None:
        t.setItem(r, 0,  _cell(rec.get("sn", "")))
        t.setItem(r, 1,  _cell(_fmt_date_str(rec.get("date", ""))))
        t.setItem(r, 2,  _cell(rec.get("lpo_no", "")))
        t.setItem(r, 3,  _cell(rec.get("do_sdo_no", "")))
        t.setItem(r, 4,  _cell(rec.get("diesel_at", "")))
        t.setItem(r, 5,  _cell(rec.get("clients_name", "")))
        t.setItem(r, 6,  _cell(rec.get("destinations", "")))
        t.setItem(r, 7,  _cell(rec.get("truck_no", "")))
        t.setItem(r, 8,  _cell(_fmt_num(rec.get("ltrs"), decimals=2),
                               Qt.AlignRight | Qt.AlignVCenter, mono=True))
        t.setItem(r, 9,  _cell(_fmt_num(rec.get("price_per_ltr"), decimals=2),
                               Qt.AlignRight | Qt.AlignVCenter, mono=True))
        t.setItem(r, 10, _cell(_fmt_num(rec.get("total_amount"), "TZS ", 0),
                               Qt.AlignRight | Qt.AlignVCenter, mono=True))
        t.setItem(r, 11, _cell(_fmt_num(rec.get("lake_usd"), "$ ", 2),
                               Qt.AlignRight | Qt.AlignVCenter, mono=True))
        t.setItem(r, 12, _cell(rec.get("remark", "")))
