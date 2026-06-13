"""AccountantDashboard — Separate Expenses widgets (ASK 8).

Covers all nine views under the SEPARATE EXPENSES sidebar section:
  TollPlazaWidget      — import from Dot Com Zambia xlsx/csv, dedup by Receipt No
  ParkingCongoWidget   — import from Congo transporter ledger, dedup by Serial
  CongoExpensesWidget  — manual entry (Date·LPO No·Truck·Desc·USD·Approved By)
  AhmedKimviWidget     — visit-sheet pagination, advance + itemised rows + balance
  ZambiaParkingWidget  — weekly statement import, opening-balance row handling
  HarrisonExpensesWidget — manual entry, USD + Kwacha columns
  AfritrackWidget      — placeholder stub
  ThirdPartyWidget     — placeholder stub
  ComesaWidget         — placeholder stub
"""

from __future__ import annotations

import asyncio
import csv
import io
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import qtawesome as qta

from PySide6.QtCore import Qt, QSize, QTimer, Signal
from PySide6.QtGui import QColor, QDragEnterEvent, QDropEvent, QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMenu, QMessageBox, QPushButton, QSizePolicy,
    QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget,
)

# ── openpyxl (optional — fallback to csv-only if missing) ──────────────────────
try:
    import openpyxl
    _HAS_OPENPYXL = True
except ImportError:
    _HAS_OPENPYXL = False

# ── Design tokens (match dashboard palette) ────────────────────────────────────
_WHITE   = "#FFFFFF"
_BG      = "#F4F6F8"
_BORDER  = "#E5E7EB"
_BLUE    = "#0077C5"
_BLUE_L  = "#E8F4FD"
_GREEN   = "#16A34A"
_GREEN_L = "#DCFCE7"
_AMBER   = "#D97706"
_AMBER_L = "#FEF3C7"
_RED     = "#DC2626"
_RED_L   = "#FEE2E2"
_T1      = "#111827"
_T2      = "#6B7280"
_TM      = "#9CA3AF"
_HDR_BG  = "#F1F5F9"
_ALT_ROW = "#F9FAFB"
_NAVY    = "#1B2B4B"

_PAGE_SIZES = [25, 50, 100]
_ROW_H      = 36

# ── Afritrack / QuickBooks-inspired palette (used only for AfritrackWidget) ───
_QB_HDR_BG   = "#EFF6FF"   # very light blue header
_QB_HDR_FG   = "#1E3A5F"   # dark navy header text
_QB_FORM_BG  = "#EFF6FF"   # formula-cell background
_QB_FORM_FG  = "#1D4ED8"   # formula-cell text (blue)
_QB_VNEG_BG  = "#FEF2F2"   # negative variance background
_QB_VNEG_FG  = "#DC2626"   # negative variance text
_QB_VPOS_FG  = "#15803D"   # positive variance text
_QB_VZRO_FG  = "#9CA3AF"   # zero variance text
_QB_SEL_BG   = "#DBEAFE"   # selection highlight
_QB_SEL_FG   = "#1E3A5F"   # selection text
_QB_FOOT_BG  = "#F8FAFC"   # footer background
_QB_BOLD_FG  = "#0F172A"   # bold row text
_QB_RED_DARK = "#B91C1C"   # red totals text
_QB_RED_BG   = "#FEF2F2"   # red totals background


# ═══════════════════════════════════════════════════════════════════════════════
#  Primitive helpers
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
         danger: bool = False, height: int = 34) -> QPushButton:
    b = QPushButton(f"  {text}" if icon else text)
    b.setCursor(Qt.PointingHandCursor)
    b.setFixedHeight(height)
    if icon:
        try:
            b.setIcon(qta.icon(icon, color="#FFFFFF" if (primary or danger) else _T1))
            b.setIconSize(QSize(16, 16))
        except Exception:
            pass
    if danger:
        ss = (f"QPushButton{{background:{_RED};color:#FFF;border:none;border-radius:5px;"
              f"font-size:12px;font-weight:600;font-family:'Segoe UI',sans-serif;padding:0 14px;}}"
              f"QPushButton:hover{{background:#B91C1C;}}"
              f"QPushButton:disabled{{background:#FCA5A5;}}")
    elif primary:
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


def _card(widget: QWidget) -> QFrame:
    f = QFrame()
    f.setStyleSheet(
        f"QFrame{{background:{_WHITE};border:1px solid {_BORDER};"
        "border-radius:6px;}}"
    )
    vl = QVBoxLayout(f)
    vl.setContentsMargins(0, 0, 0, 0)
    vl.setSpacing(0)
    vl.addWidget(widget)
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
    t.setAlternatingRowColors(True)
    t.verticalHeader().setVisible(False)
    t.verticalHeader().setDefaultSectionSize(_ROW_H)
    t.horizontalHeader().setStretchLastSection(True)
    t.setStyleSheet(_table_style())
    t.setAlternatingRowColors(True)
    t.setStyleSheet(
        _table_style()
        + f"QTableWidget{{alternate-background-color:{_ALT_ROW};}}"
    )
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


def _fmt_date(dt) -> str:
    if isinstance(dt, datetime):
        return dt.strftime("%d %b %y")
    return str(dt) if dt else "—"


def _fmt_num(v, prefix: str = "", decimals: int = 2) -> str:
    if v is None:
        return "—"
    try:
        return f"{prefix}{float(v):,.{decimals}f}"
    except Exception:
        return str(v)


# ═══════════════════════════════════════════════════════════════════════════════
#  Shared page title + toolbar wrapper
# ═══════════════════════════════════════════════════════════════════════════════

class _PageHeader(QWidget):
    """Title row with optional right-aligned buttons."""

    def __init__(self, title: str, icon_name: str = "mdi.cash-multiple",
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

        title_lbl = _lbl(title, size=18, weight=700)
        hl.addWidget(title_lbl)
        hl.addStretch()
        self._right = hl

    def add_right(self, widget: QWidget) -> None:
        self._right.addWidget(widget)


# ═══════════════════════════════════════════════════════════════════════════════
#  Pagination footer
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

    def _on_size_change(self, idx: int) -> None:
        self._page = 1
        self._page_size = self._size_cb.currentData()
        self.size_changed.emit(self._page_size)


# ═══════════════════════════════════════════════════════════════════════════════
#  Footer totals bar
# ═══════════════════════════════════════════════════════════════════════════════

class _TotalsBar(QFrame):
    def __init__(self, labels: List[Tuple[str, str]], parent: QWidget | None = None) -> None:
        """labels = [(key, display_prefix), ...]  e.g. [("usd", "USD"), ("kwacha", "ZMW")]"""
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
#  Import Dialog — shared by TollPlaza / ParkingCongo / ZambiaParking
# ═══════════════════════════════════════════════════════════════════════════════

class _DropZone(QFrame):
    """Drag-and-drop file drop target."""
    file_dropped = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setMinimumHeight(100)
        self.setStyleSheet(
            f"QFrame{{border:2px dashed {_BORDER};border-radius:8px;"
            f"background:{_BG};}}"
            f"QFrame:hover{{border-color:{_BLUE};background:{_BLUE_L};}}"
        )
        vl = QVBoxLayout(self)
        vl.setAlignment(Qt.AlignCenter)
        try:
            icon_lbl = QLabel()
            icon_lbl.setPixmap(qta.icon("mdi.upload-outline", color=_TM).pixmap(32, 32))
            icon_lbl.setAlignment(Qt.AlignCenter)
            icon_lbl.setStyleSheet("background:transparent;border:none;")
            vl.addWidget(icon_lbl)
        except Exception:
            pass
        vl.addWidget(_lbl("Drag & drop .xlsx or .csv here", size=13, color=_T2))
        self._path_lbl = _lbl("", size=11, color=_TM)
        self._path_lbl.setAlignment(Qt.AlignCenter)
        vl.addWidget(self._path_lbl)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.setStyleSheet(
                f"QFrame{{border:2px dashed {_BLUE};border-radius:8px;"
                f"background:{_BLUE_L};}}"
            )

    def dragLeaveEvent(self, event) -> None:
        self.setStyleSheet(
            f"QFrame{{border:2px dashed {_BORDER};border-radius:8px;"
            f"background:{_BG};}}"
            f"QFrame:hover{{border-color:{_BLUE};background:{_BLUE_L};}}"
        )

    def dropEvent(self, event: QDropEvent) -> None:
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            self._path_lbl.setText(path)
            self.file_dropped.emit(path)
        self.dragLeaveEvent(event)

    def set_path(self, path: str) -> None:
        self._path_lbl.setText(Path(path).name)


def _read_file_rows(path: str) -> Tuple[List[str], List[List[Any]]]:
    """Return (headers, data_rows) from an xlsx or csv file."""
    p = Path(path)
    if p.suffix.lower() in (".xlsx", ".xls") and _HAS_OPENPYXL:
        wb = openpyxl.load_workbook(path, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return [], []
        headers = [str(c) if c is not None else "" for c in rows[0]]
        data = [[str(c) if c is not None else "" for c in r] for r in rows[1:] if any(c is not None for c in r)]
        return headers, data
    else:
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            rows = list(reader)
        if not rows:
            return [], []
        return rows[0], rows[1:]


class ImportDialog(QDialog):
    """
    Generic file-import dialog.

    Parameters
    ----------
    feed_type  : str          "toll_plaza" | "parking_congo" | "zambia_parking"
    dedup_key  : str          column key used for duplicate detection
    preview_headers : list    column display names for the preview table
    col_map    : dict         maps expected_key → list of candidate header names
                              (case-insensitive, first match wins)
    save_fn    : coroutine    async fn(records: list[dict]) → int (saved count)
    exist_fn   : coroutine    async fn(keys: list[str]) → set[str] (existing keys)
    """

    imported = Signal(int)

    def __init__(
        self,
        feed_type: str,
        dedup_key: str,
        preview_headers: List[str],
        col_map: Dict[str, List[str]],
        save_fn,
        exist_fn,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._feed_type  = feed_type
        self._dedup_key  = dedup_key
        self._preview_headers = preview_headers
        self._col_map    = col_map
        self._save_fn    = save_fn
        self._exist_fn   = exist_fn

        self._raw_headers: List[str] = []
        self._all_rows:    List[dict] = []
        self._new_rows:    List[dict] = []

        self.setWindowTitle(f"Import — {feed_type.replace('_', ' ').title()}")
        self.setMinimumWidth(680)
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

        # Browse button
        browse_row = QWidget()
        browse_row.setStyleSheet("background:transparent;")
        brl = QHBoxLayout(browse_row)
        brl.setContentsMargins(0, 0, 0, 0)
        brl.setSpacing(8)
        browse_btn = _btn("Browse File", "mdi.folder-open-outline", primary=False)
        browse_btn.clicked.connect(self._browse)
        brl.addStretch()
        brl.addWidget(browse_btn)
        vl.addWidget(browse_row)

        # Stats row
        self._stats_lbl = _lbl("No file loaded.", size=12, color=_T2)
        vl.addWidget(self._stats_lbl)

        vl.addWidget(_hsep())

        # Preview table
        preview_title = _lbl("Preview (first 10 rows)", size=12, weight=600)
        vl.addWidget(preview_title)

        self._preview_tbl = _make_table(self._preview_headers)
        self._preview_tbl.setMinimumHeight(200)
        vl.addWidget(self._preview_tbl)

        vl.addWidget(_hsep())

        # Buttons
        btn_row = QWidget()
        btn_row.setStyleSheet("background:transparent;")
        bbl = QHBoxLayout(btn_row)
        bbl.setContentsMargins(0, 0, 0, 0)
        bbl.setSpacing(8)
        bbl.addStretch()

        cancel_btn = _btn("Cancel", primary=False)
        cancel_btn.clicked.connect(self.reject)
        bbl.addWidget(cancel_btn)

        self._import_btn = _btn("Import Records", "mdi.check-circle-outline")
        self._import_btn.setEnabled(False)
        self._import_btn.clicked.connect(self._do_import)
        bbl.addWidget(self._import_btn)

        vl.addWidget(btn_row)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open File", "", "Excel / CSV (*.xlsx *.xls *.csv)"
        )
        if path:
            self._drop.set_path(path)
            self._on_file(path)

    def _on_file(self, path: str) -> None:
        self._stats_lbl.setText("Reading file…")
        try:
            headers, rows = _read_file_rows(path)
        except Exception as exc:
            self._stats_lbl.setText(f"Error reading file: {exc}")
            return

        self._raw_headers = headers
        # Map columns
        hdr_lower = {h.strip().lower(): i for i, h in enumerate(headers)}

        def _find(candidates: List[str]) -> Optional[int]:
            for c in candidates:
                idx = hdr_lower.get(c.lower())
                if idx is not None:
                    return idx
            return None

        field_idxs: Dict[str, Optional[int]] = {
            key: _find(cands) for key, cands in self._col_map.items()
        }

        records: List[dict] = []
        for row in rows:
            rec: dict = {"_raw": row, "feed_type": self._feed_type}
            for key, idx in field_idxs.items():
                rec[key] = row[idx].strip() if (idx is not None and idx < len(row)) else ""
            records.append(rec)

        self._all_rows = records
        dedup_vals = [r.get(self._dedup_key, "") for r in records if r.get(self._dedup_key)]
        asyncio.ensure_future(self._check_dupes(records, dedup_vals))

    async def _check_dupes(self, records: List[dict], keys: List[str]) -> None:
        try:
            existing: set = await self._exist_fn(keys)
        except Exception:
            existing = set()

        self._new_rows = [r for r in records
                          if r.get(self._dedup_key) not in existing]
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

    def _do_import(self) -> None:
        self._import_btn.setEnabled(False)
        self._import_btn.setText("Importing…")
        asyncio.ensure_future(self._async_import())

    async def _async_import(self) -> None:
        try:
            saved = await self._save_fn(self._new_rows)
            self.imported.emit(saved)
            self.accept()
        except Exception as exc:
            QMessageBox.critical(self, "Import Error", str(exc))
            self._import_btn.setEnabled(True)
            self._import_btn.setText(f"Import {len(self._new_rows):,} Records")


# ═══════════════════════════════════════════════════════════════════════════════
#  1. TollPlazaWidget
# ═══════════════════════════════════════════════════════════════════════════════

_TOLL_HEADERS = [
    "TOLL DATE", "TOLL PLAZA", "VEHICLE REG", "CLASS", "TENDER",
    "RECEIPT NO", "DEVICE", "LANE", "CASHIER",
]
_TOLL_COL_MAP = {
    "toll_date":    ["toll date", "date", "transaction date"],
    "toll_plaza":   ["toll plaza", "plaza", "station"],
    "vehicle_reg":  ["vehicle reg", "vehicle", "vehicle registration", "plate"],
    "vehicle_class":["class", "vehicle class"],
    "tender_amount":["tender", "amount", "tender amount", "zmw"],
    "receipt_no":   ["receipt no", "receipt", "receipt number", "receipt no."],
    "device":       ["device", "device id"],
    "lane":         ["lane"],
    "cashier_name": ["cashier", "cashier name", "operator"],
}


class TollPlazaWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._page = 1
        self._page_size = 25
        self._total = 0
        self._records: List[dict] = []
        self._search = ""
        self._plaza_filter = ""
        self._build()
        self.refresh()

    def _build(self) -> None:
        self.setStyleSheet(f"background:{_BG};")
        vl = QVBoxLayout(self)
        vl.setContentsMargins(20, 20, 20, 16)
        vl.setSpacing(12)

        header = _PageHeader("Toll Plaza", "mdi.boom-gate")
        self._import_btn = _btn("Import from Dot Com Zambia", "mdi.upload-outline")
        self._import_btn.clicked.connect(self._open_import)
        header.add_right(self._import_btn)
        vl.addWidget(header)
        vl.addWidget(_hsep())

        # Toolbar
        tb = QWidget()
        tb.setStyleSheet("background:transparent;")
        tbl = QHBoxLayout(tb)
        tbl.setContentsMargins(0, 0, 0, 0)
        tbl.setSpacing(8)

        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Search vehicle, plaza, receipt…")
        self._search_edit.setFixedWidth(260)
        self._search_edit.setStyleSheet(_input_ss())
        self._search_edit.textChanged.connect(self._on_search)
        tbl.addWidget(self._search_edit)

        self._plaza_cb = QComboBox()
        self._plaza_cb.addItem("All Plazas", "")
        self._plaza_cb.setFixedWidth(160)
        self._plaza_cb.setStyleSheet(_input_ss())
        self._plaza_cb.currentIndexChanged.connect(self._on_filter)
        tbl.addWidget(self._plaza_cb)
        tbl.addStretch()

        export_btn = _btn("Export", "mdi.download-outline", primary=False)
        tbl.addWidget(export_btn)
        vl.addWidget(tb)

        # Table
        self._table = _make_table(_TOLL_HEADERS)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setStretchLastSection(True)
        vl.addWidget(self._table, 1)

        # Totals + pagination
        self._totals = _TotalsBar([("zmw", "ZMW "), ("count", "Records: ")])
        vl.addWidget(self._totals)

        self._pager = _PaginationBar()
        self._pager.page_changed.connect(self._go_page)
        self._pager.size_changed.connect(self._on_page_size)
        vl.addWidget(self._pager)

    def refresh(self) -> None:
        asyncio.ensure_future(self._load())

    async def _load(self) -> None:
        from tahmeed.services import accountant_service as svc
        skip = (self._page - 1) * self._page_size
        recs, total = await asyncio.gather(
            svc.get_imported_feed("toll_plaza", self._search, self._plaza_filter,
                                  self._page_size, skip),
            svc.count_imported_feed("toll_plaza", self._search, self._plaza_filter),
        )
        self._records = recs
        self._total = total
        self._fill_table(recs)
        self._pager.set_total(total, self._page_size, self._page)
        totals = await svc.get_imported_feed_totals("toll_plaza")
        zmw = totals.get("tender_amount", 0.0)
        self._totals.set_total("zmw", zmw, "ZMW ")
        self._totals.set_total("count", total, "")

    def _fill_table(self, recs: List[dict]) -> None:
        t = self._table
        t.setRowCount(0)
        for rec in recs:
            r = t.rowCount()
            t.insertRow(r)
            t.setItem(r, 0, _cell(rec.get("toll_date", "")))
            t.setItem(r, 1, _cell(rec.get("toll_plaza", "")))
            t.setItem(r, 2, _cell(rec.get("vehicle_reg", "")))
            t.setItem(r, 3, _cell(rec.get("vehicle_class", "")))
            t.setItem(r, 4, _cell(_fmt_num(rec.get("tender_amount"), "ZMW ", 0), mono=True))
            t.setItem(r, 5, _cell(rec.get("receipt_no", "")))
            t.setItem(r, 6, _cell(rec.get("device", "")))
            t.setItem(r, 7, _cell(rec.get("lane", "")))
            t.setItem(r, 8, _cell(rec.get("cashier_name", "")))

    def _open_import(self) -> None:
        from tahmeed.services import accountant_service as svc
        dlg = ImportDialog(
            feed_type="toll_plaza",
            dedup_key="receipt_no",
            preview_headers=_TOLL_HEADERS,
            col_map=_TOLL_COL_MAP,
            save_fn=svc.save_imported_feed,
            exist_fn=svc.get_existing_feed_keys,
            parent=self,
        )
        dlg.imported.connect(lambda n: (
            QMessageBox.information(self, "Import Complete", f"Imported {n:,} new records."),
            self.refresh(),
        ))
        dlg.exec()

    def _on_search(self, text: str) -> None:
        self._search = text
        self._page = 1
        asyncio.ensure_future(self._load())

    def _on_filter(self) -> None:
        self._plaza_filter = self._plaza_cb.currentData() or ""
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
#  2. ParkingCongoWidget
# ═══════════════════════════════════════════════════════════════════════════════

_PCONGO_HEADERS = [
    "SN", "TRANSACTION DATE", "TYPE", "SERIAL", "VEHICLE #",
    "AMOUNT", "BALANCE", "GATE IN", "GATE OUT",
]
_PCONGO_COL_MAP = {
    "sn":               ["sn", "s/n", "no", "#"],
    "transaction_date": ["transaction date", "date", "trans date"],
    "transaction_type": ["type", "transaction type", "trans type"],
    "serial":           ["serial", "serial no", "serial number"],
    "vehicle_no":       ["vehicle #", "vehicle no", "vehicle", "plate"],
    "amount":           ["amount", "amt"],
    "balance":          ["balance", "bal"],
    "gate_in":          ["gate in", "in"],
    "gate_out":         ["gate out", "out"],
}


class ParkingCongoWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._page = 1
        self._page_size = 25
        self._total = 0
        self._search = ""
        self._build()
        self.refresh()

    def _build(self) -> None:
        self.setStyleSheet(f"background:{_BG};")
        vl = QVBoxLayout(self)
        vl.setContentsMargins(20, 20, 20, 16)
        vl.setSpacing(12)

        header = _PageHeader("Parking Congo", "mdi.parking")
        self._import_btn = _btn("Import from Congo Ledger", "mdi.upload-outline")
        self._import_btn.clicked.connect(self._open_import)
        header.add_right(self._import_btn)
        vl.addWidget(header)
        vl.addWidget(_hsep())

        tb = QWidget()
        tb.setStyleSheet("background:transparent;")
        tbl = QHBoxLayout(tb)
        tbl.setContentsMargins(0, 0, 0, 0)
        tbl.setSpacing(8)

        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Search vehicle, serial…")
        self._search_edit.setFixedWidth(240)
        self._search_edit.setStyleSheet(_input_ss())
        self._search_edit.textChanged.connect(self._on_search)
        tbl.addWidget(self._search_edit)
        tbl.addStretch()

        export_btn = _btn("Export", "mdi.download-outline", primary=False)
        tbl.addWidget(export_btn)
        vl.addWidget(tb)

        self._table = _make_table(_PCONGO_HEADERS)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setStretchLastSection(True)
        vl.addWidget(self._table, 1)

        self._totals = _TotalsBar([("amount", "$ ")])
        vl.addWidget(self._totals)

        self._pager = _PaginationBar()
        self._pager.page_changed.connect(self._go_page)
        self._pager.size_changed.connect(self._on_page_size)
        vl.addWidget(self._pager)

    def refresh(self) -> None:
        asyncio.ensure_future(self._load())

    async def _load(self) -> None:
        from tahmeed.services import accountant_service as svc
        skip = (self._page - 1) * self._page_size
        recs, total = await asyncio.gather(
            svc.get_imported_feed("parking_congo", self._search, "",
                                  self._page_size, skip),
            svc.count_imported_feed("parking_congo", self._search, ""),
        )
        self._fill_table(recs)
        self._pager.set_total(total, self._page_size, self._page)
        totals = await svc.get_imported_feed_totals("parking_congo")
        self._totals.set_total("amount", totals.get("amount", 0.0), "$ ")

    def _fill_table(self, recs: List[dict]) -> None:
        t = self._table
        t.setRowCount(0)
        for rec in recs:
            r = t.rowCount()
            t.insertRow(r)
            t.setItem(r, 0, _cell(rec.get("sn", "")))
            t.setItem(r, 1, _cell(rec.get("transaction_date", "")))
            t.setItem(r, 2, _cell(rec.get("transaction_type", "")))
            t.setItem(r, 3, _cell(rec.get("serial", "")))
            t.setItem(r, 4, _cell(rec.get("vehicle_no", "")))
            t.setItem(r, 5, _cell(_fmt_num(rec.get("amount"), "$ "), mono=True))
            t.setItem(r, 6, _cell(_fmt_num(rec.get("balance"), "$ "), mono=True))
            t.setItem(r, 7, _cell(rec.get("gate_in", "")))
            t.setItem(r, 8, _cell(rec.get("gate_out", "")))

    def _open_import(self) -> None:
        from tahmeed.services import accountant_service as svc
        dlg = ImportDialog(
            feed_type="parking_congo",
            dedup_key="serial",
            preview_headers=_PCONGO_HEADERS,
            col_map=_PCONGO_COL_MAP,
            save_fn=svc.save_imported_feed,
            exist_fn=svc.get_existing_feed_keys,
            parent=self,
        )
        dlg.imported.connect(lambda n: (
            QMessageBox.information(self, "Import Complete", f"Imported {n:,} new records."),
            self.refresh(),
        ))
        dlg.exec()

    def _on_search(self, text: str) -> None:
        self._search = text
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
#  3. CongoExpensesWidget  — manual entry
# ═══════════════════════════════════════════════════════════════════════════════

_CONGO_EXP_HEADERS = [
    "S/NO", "DATE", "LPO NO", "TRUCK NO", "DESCRIPTION", "AMOUNT (USD)", "APPROVED BY",
]


class _CongoEntryDialog(QDialog):
    saved = Signal(dict)

    def __init__(self, record: dict | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._record = record
        self.setWindowTitle("New Congo Expense" if not record else "Edit Congo Expense")
        self.setMinimumWidth(400)
        self.setStyleSheet(f"background:{_WHITE};")
        self._build()

    def _build(self) -> None:
        vl = QVBoxLayout(self)
        vl.setSpacing(12)
        vl.setContentsMargins(20, 20, 20, 20)

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        def _field(placeholder: str = "", fixed_w: int = 0) -> QLineEdit:
            e = QLineEdit()
            e.setPlaceholderText(placeholder)
            e.setStyleSheet(_input_ss())
            if fixed_w:
                e.setFixedWidth(fixed_w)
            return e

        self._date_edit  = _field("dd mmm yyyy, e.g. 01 Jan 2025")
        self._lpo_edit   = _field("C001")
        self._truck_edit = _field("T700 DXY")
        self._desc_edit  = _field("Seal Facilitation")
        self._amt_edit   = _field("0.00", fixed_w=120)
        self._appr_edit  = _field("Name")

        form.addRow("Date *", self._date_edit)
        form.addRow("LPO No.", self._lpo_edit)
        form.addRow("Truck No.", self._truck_edit)
        form.addRow("Description *", self._desc_edit)
        form.addRow("Amount (USD) *", self._amt_edit)
        form.addRow("Approved By", self._appr_edit)
        vl.addLayout(form)

        if self._record:
            self._date_edit.setText(self._record.get("date_str", ""))
            self._lpo_edit.setText(self._record.get("lpo_no", ""))
            self._truck_edit.setText(self._record.get("truck_no", ""))
            self._desc_edit.setText(self._record.get("description", ""))
            self._amt_edit.setText(str(self._record.get("amount_usd", "")))
            self._appr_edit.setText(self._record.get("approved_by", ""))

        vl.addWidget(_hsep())

        btn_row = QWidget()
        btn_row.setStyleSheet("background:transparent;")
        brl = QHBoxLayout(btn_row)
        brl.setContentsMargins(0, 0, 0, 0)
        brl.addStretch()
        cancel = _btn("Cancel", primary=False)
        cancel.clicked.connect(self.reject)
        brl.addWidget(cancel)
        save = _btn("Save Entry", "mdi.content-save-outline")
        save.clicked.connect(self._save)
        brl.addWidget(save)
        vl.addWidget(btn_row)

    def _save(self) -> None:
        desc = self._desc_edit.text().strip()
        amt_text = self._amt_edit.text().strip()
        if not desc or not amt_text:
            QMessageBox.warning(self, "Validation", "Description and Amount are required.")
            return
        try:
            amt = float(amt_text)
        except ValueError:
            QMessageBox.warning(self, "Validation", "Amount must be a number.")
            return
        self.saved.emit({
            "date_str":    self._date_edit.text().strip(),
            "lpo_no":      self._lpo_edit.text().strip(),
            "truck_no":    self._truck_edit.text().strip(),
            "description": desc,
            "amount_usd":  amt,
            "approved_by": self._appr_edit.text().strip(),
        })
        self.accept()


class CongoExpensesWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._page = 1
        self._page_size = 25
        self._total = 0
        self._search = ""
        self._truck_filter = ""
        self._build()
        self.refresh()

    def _build(self) -> None:
        self.setStyleSheet(f"background:{_BG};")
        vl = QVBoxLayout(self)
        vl.setContentsMargins(20, 20, 20, 16)
        vl.setSpacing(12)

        header = _PageHeader("Congo Expenses", "mdi.map-marker")
        new_btn = _btn("New Entry", "mdi.plus-circle-outline")
        new_btn.clicked.connect(self._new_entry)
        header.add_right(new_btn)
        vl.addWidget(header)
        vl.addWidget(_hsep())

        tb = QWidget()
        tb.setStyleSheet("background:transparent;")
        tbl = QHBoxLayout(tb)
        tbl.setContentsMargins(0, 0, 0, 0)
        tbl.setSpacing(8)

        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Search description, truck…")
        self._search_edit.setFixedWidth(240)
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

        export_btn = _btn("Export", "mdi.download-outline", primary=False)
        tbl.addWidget(export_btn)
        vl.addWidget(tb)

        self._table = _make_table(_CONGO_EXP_HEADERS)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setStretchLastSection(True)
        vl.addWidget(self._table, 1)

        self._totals = _TotalsBar([("usd", "USD ")])
        vl.addWidget(self._totals)

        self._pager = _PaginationBar()
        self._pager.page_changed.connect(self._go_page)
        self._pager.size_changed.connect(self._on_page_size)
        vl.addWidget(self._pager)

    def refresh(self) -> None:
        asyncio.ensure_future(self._load())

    async def _load(self) -> None:
        from tahmeed.services import accountant_service as svc
        skip = (self._page - 1) * self._page_size
        recs, total = await asyncio.gather(
            svc.get_separate_expenses_list("congo_expenses", self._search,
                                           self._truck_filter, self._page_size, skip),
            svc.count_separate_expenses("congo_expenses", self._search, self._truck_filter),
        )
        self._total = total
        self._fill_table(recs)
        self._pager.set_total(total, self._page_size, self._page)
        totals = await svc.get_separate_expense_totals("congo_expenses")
        self._totals.set_total("usd", totals.get("amount_usd", 0.0), "USD ")

    def _fill_table(self, recs: List[dict]) -> None:
        t = self._table
        t.setRowCount(0)
        for i, rec in enumerate(recs, 1):
            r = t.rowCount()
            t.insertRow(r)
            t.setItem(r, 0, _cell(str(i)))
            t.setItem(r, 1, _cell(rec.get("date_str", "")))
            t.setItem(r, 2, _cell(rec.get("lpo_no", "")))
            t.setItem(r, 3, _cell(rec.get("truck_no", "")))
            t.setItem(r, 4, _cell(rec.get("description", "")))
            t.setItem(r, 5, _cell(_fmt_num(rec.get("amount_usd"), "$ "), mono=True))
            t.setItem(r, 6, _cell(rec.get("approved_by", "")))

    def _new_entry(self) -> None:
        dlg = _CongoEntryDialog(parent=self)
        dlg.saved.connect(self._on_saved)
        dlg.exec()

    def _on_saved(self, data: dict) -> None:
        asyncio.ensure_future(self._save_entry(data))

    async def _save_entry(self, data: dict) -> None:
        from tahmeed.services import accountant_service as svc
        await svc.save_separate_expense("congo_expenses", data)
        self.refresh()

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
#  4. AhmedKimviWidget  — visit-sheet pagination
# ═══════════════════════════════════════════════════════════════════════════════

_KIMVI_HEADERS = ["S/NO", "DATE", "TRUCK NO", "PARTICULARS", "AMOUNT (USD)"]


class _NewKimviSheetDialog(QDialog):
    created = Signal(str, str, float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("New Visit Sheet")
        self.setMinimumWidth(360)
        self.setStyleSheet(f"background:{_WHITE};")
        vl = QVBoxLayout(self)
        vl.setSpacing(12)
        vl.setContentsMargins(20, 20, 20, 20)

        form = QFormLayout()
        form.setSpacing(10)

        self._label_edit = QLineEdit()
        self._label_edit.setPlaceholderText("e.g. 02.06 (visit date)")
        self._label_edit.setStyleSheet(_input_ss())

        self._date_edit = QLineEdit()
        self._date_edit.setPlaceholderText("02 Jun 2026")
        self._date_edit.setStyleSheet(_input_ss())

        self._advance_edit = QLineEdit()
        self._advance_edit.setPlaceholderText("1500.00")
        self._advance_edit.setStyleSheet(_input_ss())

        form.addRow("Sheet Label *", self._label_edit)
        form.addRow("Visit Date *", self._date_edit)
        form.addRow("Cash Advance (USD) *", self._advance_edit)
        vl.addLayout(form)
        vl.addWidget(_hsep())

        btn_row = QWidget()
        btn_row.setStyleSheet("background:transparent;")
        brl = QHBoxLayout(btn_row)
        brl.setContentsMargins(0, 0, 0, 0)
        brl.addStretch()
        cancel = _btn("Cancel", primary=False)
        cancel.clicked.connect(self.reject)
        brl.addWidget(cancel)
        create = _btn("Create Sheet", "mdi.plus-circle-outline")
        create.clicked.connect(self._create)
        brl.addWidget(create)
        vl.addWidget(btn_row)

    def _create(self) -> None:
        label = self._label_edit.text().strip()
        date_str = self._date_edit.text().strip()
        try:
            advance = float(self._advance_edit.text().strip())
        except ValueError:
            QMessageBox.warning(self, "Validation", "Cash Advance must be a number.")
            return
        if not label or not date_str:
            QMessageBox.warning(self, "Validation", "Label and Date are required.")
            return
        self.created.emit(label, date_str, advance)
        self.accept()


class _KimviRowDialog(QDialog):
    saved = Signal(dict)

    def __init__(self, sheet_label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._sheet_label = sheet_label
        self.setWindowTitle("Add Item")
        self.setMinimumWidth(360)
        self.setStyleSheet(f"background:{_WHITE};")
        vl = QVBoxLayout(self)
        vl.setSpacing(12)
        vl.setContentsMargins(20, 20, 20, 20)

        form = QFormLayout()
        form.setSpacing(10)

        self._date_edit  = QLineEdit()
        self._date_edit.setPlaceholderText("02 Jun 2026")
        self._date_edit.setStyleSheet(_input_ss())

        self._truck_edit = QLineEdit()
        self._truck_edit.setPlaceholderText("T587 DTB")
        self._truck_edit.setStyleSheet(_input_ss())

        self._part_edit  = QLineEdit()
        self._part_edit.setPlaceholderText("Entry Card Renewal")
        self._part_edit.setStyleSheet(_input_ss())

        self._amt_edit   = QLineEdit()
        self._amt_edit.setPlaceholderText("20.00")
        self._amt_edit.setStyleSheet(_input_ss())
        self._amt_edit.setFixedWidth(120)

        form.addRow("Date", self._date_edit)
        form.addRow("Truck No.", self._truck_edit)
        form.addRow("Particulars *", self._part_edit)
        form.addRow("Amount (USD) *", self._amt_edit)
        vl.addLayout(form)
        vl.addWidget(_hsep())

        btn_row = QWidget()
        btn_row.setStyleSheet("background:transparent;")
        brl = QHBoxLayout(btn_row)
        brl.setContentsMargins(0, 0, 0, 0)
        brl.addStretch()
        cancel = _btn("Cancel", primary=False)
        cancel.clicked.connect(self.reject)
        brl.addWidget(cancel)
        save = _btn("Add Item", "mdi.plus")
        save.clicked.connect(self._save)
        brl.addWidget(save)
        vl.addWidget(btn_row)

    def _save(self) -> None:
        part = self._part_edit.text().strip()
        try:
            amt = float(self._amt_edit.text().strip())
        except ValueError:
            QMessageBox.warning(self, "Validation", "Amount must be a number.")
            return
        if not part:
            QMessageBox.warning(self, "Validation", "Particulars are required.")
            return
        self.saved.emit({
            "sheet_label": self._sheet_label,
            "date_str":    self._date_edit.text().strip(),
            "truck_no":    self._truck_edit.text().strip(),
            "description": part,
            "amount_usd":  amt,
            "is_advance":  False,
        })
        self.accept()


class AhmedKimviWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._sheets: List[str] = []
        self._current_idx: int = 0
        self._entries: List[dict] = []
        self._build()
        self.refresh()

    def _build(self) -> None:
        self.setStyleSheet(f"background:{_BG};")
        vl = QVBoxLayout(self)
        vl.setContentsMargins(20, 20, 20, 16)
        vl.setSpacing(12)

        header = _PageHeader("Ahmed Kimvi (Klesa)", "mdi.account-cash")
        new_sheet_btn = _btn("New Sheet", "mdi.plus-circle-outline", primary=False)
        new_sheet_btn.clicked.connect(self._new_sheet)
        add_item_btn = _btn("Add Item", "mdi.plus")
        add_item_btn.clicked.connect(self._add_item)
        header.add_right(new_sheet_btn)
        header.add_right(add_item_btn)
        vl.addWidget(header)
        vl.addWidget(_hsep())

        # Sheet navigation bar
        nav = QWidget()
        nav.setStyleSheet("background:transparent;")
        navl = QHBoxLayout(nav)
        navl.setContentsMargins(0, 0, 0, 0)
        navl.setSpacing(8)

        self._prev_sheet = _btn("◀", primary=False, height=30)
        self._prev_sheet.setFixedWidth(36)
        self._prev_sheet.clicked.connect(self._prev)
        navl.addWidget(self._prev_sheet)

        self._sheet_lbl = _lbl("Sheet —", size=13, weight=600)
        navl.addWidget(self._sheet_lbl)

        self._next_sheet = _btn("▶", primary=False, height=30)
        self._next_sheet.setFixedWidth(36)
        self._next_sheet.clicked.connect(self._next)
        navl.addWidget(self._next_sheet)
        navl.addStretch()

        export_btn = _btn("Export All", "mdi.download-outline", primary=False)
        navl.addWidget(export_btn)
        vl.addWidget(nav)

        # Summary card
        self._summary = QFrame()
        self._summary.setStyleSheet(
            f"QFrame{{background:{_BLUE_L};border:1px solid #BFDBFE;"
            "border-radius:6px;padding:8px;}}"
        )
        sl = QHBoxLayout(self._summary)
        sl.setContentsMargins(12, 8, 12, 8)
        sl.setSpacing(24)

        self._adv_lbl   = _lbl("Cash Advance: —", size=12, weight=600)
        self._bal_lbl   = _lbl("Running Balance: —", size=12, weight=600, color=_GREEN)
        self._spent_lbl = _lbl("Spent: —", size=12, color=_T2)
        sl.addWidget(self._adv_lbl)
        sl.addWidget(self._spent_lbl)
        sl.addWidget(self._bal_lbl)
        sl.addStretch()
        vl.addWidget(self._summary)

        # Table
        self._table = _make_table(_KIMVI_HEADERS)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setStretchLastSection(True)
        vl.addWidget(self._table, 1)

    def refresh(self) -> None:
        asyncio.ensure_future(self._load_sheets())

    async def _load_sheets(self) -> None:
        from tahmeed.services import accountant_service as svc
        self._sheets = await svc.get_kimvi_sheets()
        if self._sheets and self._current_idx >= len(self._sheets):
            self._current_idx = len(self._sheets) - 1
        await self._load_current()

    async def _load_current(self) -> None:
        from tahmeed.services import accountant_service as svc
        if not self._sheets:
            self._sheet_lbl.setText("No sheets yet")
            self._table.setRowCount(0)
            self._prev_sheet.setEnabled(False)
            self._next_sheet.setEnabled(False)
            self._adv_lbl.setText("Cash Advance: —")
            self._bal_lbl.setText("Running Balance: —")
            self._spent_lbl.setText("Spent: —")
            return

        label = self._sheets[self._current_idx]
        self._sheet_lbl.setText(f"Sheet {self._current_idx + 1} of {len(self._sheets)}: {label}")
        self._prev_sheet.setEnabled(self._current_idx > 0)
        self._next_sheet.setEnabled(self._current_idx < len(self._sheets) - 1)

        entries = await svc.get_kimvi_sheet_entries(label)
        self._entries = entries
        self._fill_table(entries)
        self._update_summary(entries)

    def _fill_table(self, entries: List[dict]) -> None:
        t = self._table
        t.setRowCount(0)
        item_no = 0
        for entry in entries:
            r = t.rowCount()
            t.insertRow(r)
            is_advance = entry.get("is_advance", False)
            if is_advance:
                t.setItem(r, 0, _cell("—"))
            else:
                item_no += 1
                t.setItem(r, 0, _cell(str(item_no)))
            t.setItem(r, 1, _cell(entry.get("date_str", "")))
            t.setItem(r, 2, _cell(entry.get("truck_no", "")))
            t.setItem(r, 3, _cell(entry.get("description", "")))
            amt = entry.get("amount_usd", 0.0)
            color = _RED if (not is_advance and amt > 0) else _GREEN
            t.setItem(r, 4, _cell(
                f"({'.' if is_advance else ''}{_fmt_num(abs(amt), '$ ')})",
                mono=True, color=color,
            ))
            if is_advance:
                for col in range(t.columnCount()):
                    item = t.item(r, col)
                    if item:
                        item.setBackground(QColor(_AMBER_L))

    def _update_summary(self, entries: List[dict]) -> None:
        advance = sum(e.get("amount_usd", 0) for e in entries if e.get("is_advance"))
        spent   = sum(e.get("amount_usd", 0) for e in entries if not e.get("is_advance"))
        balance = advance - spent
        self._adv_lbl.setText(f"Cash Advance: USD {_fmt_num(advance)}")
        self._spent_lbl.setText(f"Spent: USD {_fmt_num(spent)}")
        self._bal_lbl.setText(f"Running Balance: USD {_fmt_num(balance)}")
        self._bal_lbl.setStyleSheet(
            f"color:{'#DC2626' if balance < 0 else '#16A34A'};"
            "font-size:12px;font-weight:600;font-family:'Segoe UI',sans-serif;"
            "background:transparent;"
        )

    def _new_sheet(self) -> None:
        dlg = _NewKimviSheetDialog(parent=self)
        dlg.created.connect(self._on_sheet_created)
        dlg.exec()

    def _on_sheet_created(self, label: str, date_str: str, advance: float) -> None:
        asyncio.ensure_future(self._create_sheet(label, date_str, advance))

    async def _create_sheet(self, label: str, date_str: str, advance: float) -> None:
        from tahmeed.services import accountant_service as svc
        await svc.create_kimvi_sheet(label, date_str, advance)
        await self._load_sheets()
        # Navigate to the new sheet (last)
        if self._sheets:
            self._current_idx = len(self._sheets) - 1
            await self._load_current()

    def _add_item(self) -> None:
        if not self._sheets:
            QMessageBox.information(self, "No Sheet", "Create a visit sheet first.")
            return
        label = self._sheets[self._current_idx]
        dlg = _KimviRowDialog(sheet_label=label, parent=self)
        dlg.saved.connect(self._on_item_saved)
        dlg.exec()

    def _on_item_saved(self, data: dict) -> None:
        asyncio.ensure_future(self._save_item(data))

    async def _save_item(self, data: dict) -> None:
        from tahmeed.services import accountant_service as svc
        await svc.save_separate_expense("ahmed_kimvi", data)
        await self._load_current()

    def _prev(self) -> None:
        if self._current_idx > 0:
            self._current_idx -= 1
            asyncio.ensure_future(self._load_current())

    def _next(self) -> None:
        if self._current_idx < len(self._sheets) - 1:
            self._current_idx += 1
            asyncio.ensure_future(self._load_current())


# ═══════════════════════════════════════════════════════════════════════════════
#  5. ZambiaParkingWidget  — weekly statement import
# ═══════════════════════════════════════════════════════════════════════════════

_ZAMBIA_PARK_HEADERS = [
    "DATE", "TYPE", "PLATE NUM.", "TICKET NO.", "DEBIT", "CREDIT", "BALANCE", "HEADING TO",
]
_ZAMBIA_PARK_COL_MAP = {
    "date":       ["date", "transaction date"],
    "type":       ["type", "transaction type"],
    "plate_num":  ["plate num.", "plate num", "plate", "vehicle", "plate number"],
    "ticket_no":  ["ticket no.", "ticket no", "ticket", "ticket number"],
    "debit":      ["debit", "dr"],
    "credit":     ["credit", "cr"],
    "balance":    ["balance", "bal"],
    "heading_to": ["heading to", "heading", "destination"],
}


class ZambiaParkingWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._page = 1
        self._page_size = 25
        self._total = 0
        self._search = ""
        self._build()
        self.refresh()

    def _build(self) -> None:
        self.setStyleSheet(f"background:{_BG};")
        vl = QVBoxLayout(self)
        vl.setContentsMargins(20, 20, 20, 16)
        vl.setSpacing(12)

        header = _PageHeader("Zambia Parking", "mdi.map")
        self._import_btn = _btn("Import Weekly Statement", "mdi.upload-outline")
        self._import_btn.clicked.connect(self._open_import)
        header.add_right(self._import_btn)
        vl.addWidget(header)
        vl.addWidget(_hsep())

        # Info bar
        self._info_bar = QFrame()
        self._info_bar.setStyleSheet(
            f"QFrame{{background:{_BLUE_L};border:1px solid #BFDBFE;border-radius:6px;}}"
        )
        ibl = QHBoxLayout(self._info_bar)
        ibl.setContentsMargins(12, 6, 12, 6)
        ibl.setSpacing(24)
        self._balance_lbl   = _lbl("Current Balance: —", size=12, weight=600, color=_BLUE)
        self._statement_lbl = _lbl("Last Statement: —", size=11, color=_T2)
        ibl.addWidget(self._balance_lbl)
        ibl.addWidget(self._statement_lbl)
        ibl.addStretch()
        vl.addWidget(self._info_bar)

        tb = QWidget()
        tb.setStyleSheet("background:transparent;")
        tbl = QHBoxLayout(tb)
        tbl.setContentsMargins(0, 0, 0, 0)
        tbl.setSpacing(8)
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Search plate, ticket, destination…")
        self._search_edit.setFixedWidth(260)
        self._search_edit.setStyleSheet(_input_ss())
        self._search_edit.textChanged.connect(self._on_search)
        tbl.addWidget(self._search_edit)
        tbl.addStretch()
        export_btn = _btn("Export", "mdi.download-outline", primary=False)
        tbl.addWidget(export_btn)
        vl.addWidget(tb)

        self._table = _make_table(_ZAMBIA_PARK_HEADERS)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setStretchLastSection(True)
        vl.addWidget(self._table, 1)

        self._totals = _TotalsBar([("debit", "ZMW "), ("credit", "CR: ZMW ")])
        vl.addWidget(self._totals)

        self._pager = _PaginationBar()
        self._pager.page_changed.connect(self._go_page)
        self._pager.size_changed.connect(self._on_page_size)
        vl.addWidget(self._pager)

    def refresh(self) -> None:
        asyncio.ensure_future(self._load())

    async def _load(self) -> None:
        from tahmeed.services import accountant_service as svc
        skip = (self._page - 1) * self._page_size
        recs, total = await asyncio.gather(
            svc.get_imported_feed("zambia_parking", self._search, "",
                                  self._page_size, skip),
            svc.count_imported_feed("zambia_parking", self._search, ""),
        )
        self._total = total
        self._fill_table(recs)
        self._pager.set_total(total, self._page_size, self._page)
        totals = await svc.get_imported_feed_totals("zambia_parking")
        self._totals.set_total("debit", totals.get("debit", 0.0), "ZMW ")
        self._totals.set_total("credit", totals.get("credit", 0.0), "CR: ZMW ")
        # Show current balance from last row
        if recs:
            last_bal = recs[-1].get("balance", "")
            self._balance_lbl.setText(f"Current Balance: ZMW {last_bal}" if last_bal else "Current Balance: —")
        self._statement_lbl.setText(f"Total records: {total:,}")

    def _fill_table(self, recs: List[dict]) -> None:
        t = self._table
        t.setRowCount(0)
        for rec in recs:
            r = t.rowCount()
            t.insertRow(r)
            is_opening = rec.get("type", "").upper() in ("OPENING", "OPENING BALANCE", "OB")
            t.setItem(r, 0, _cell(rec.get("date", "")))
            t.setItem(r, 1, _cell(rec.get("type", "")))
            t.setItem(r, 2, _cell(rec.get("plate_num", "")))
            t.setItem(r, 3, _cell(rec.get("ticket_no", "")))
            t.setItem(r, 4, _cell(_fmt_num(rec.get("debit"), "ZMW ", 0), mono=True))
            credit = rec.get("credit")
            t.setItem(r, 5, _cell(_fmt_num(credit, "ZMW ", 0) if credit else "—", mono=True,
                                  color=_GREEN if credit else ""))
            t.setItem(r, 6, _cell(_fmt_num(rec.get("balance"), "ZMW ", 0), mono=True))
            t.setItem(r, 7, _cell(rec.get("heading_to", "")))
            if is_opening:
                for col in range(t.columnCount()):
                    item = t.item(r, col)
                    if item:
                        item.setBackground(QColor(_BLUE_L))

    def _open_import(self) -> None:
        from tahmeed.services import accountant_service as svc
        dlg = ImportDialog(
            feed_type="zambia_parking",
            dedup_key="ticket_no",
            preview_headers=_ZAMBIA_PARK_HEADERS,
            col_map=_ZAMBIA_PARK_COL_MAP,
            save_fn=svc.save_imported_feed,
            exist_fn=svc.get_existing_feed_keys,
            parent=self,
        )
        dlg.imported.connect(lambda n: (
            QMessageBox.information(self, "Import Complete", f"Imported {n:,} records."),
            self.refresh(),
        ))
        dlg.exec()

    def _on_search(self, text: str) -> None:
        self._search = text
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
#  6. HarrisonExpensesWidget  — manual entry, USD + Kwacha
# ═══════════════════════════════════════════════════════════════════════════════

_HARRISON_HEADERS = [
    "S/NO", "DATE", "TRUCK NO", "TRAILER NO", "DESCRIPTION", "USD", "KWACHA",
]


class _HarrisonEntryDialog(QDialog):
    saved = Signal(dict)

    def __init__(self, record: dict | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._record = record
        self.setWindowTitle("New Harrison Expense" if not record else "Edit Harrison Expense")
        self.setMinimumWidth(400)
        self.setStyleSheet(f"background:{_WHITE};")
        vl = QVBoxLayout(self)
        vl.setSpacing(12)
        vl.setContentsMargins(20, 20, 20, 20)

        form = QFormLayout()
        form.setSpacing(10)

        def _f(placeholder: str = "", fw: int = 0) -> QLineEdit:
            e = QLineEdit()
            e.setPlaceholderText(placeholder)
            e.setStyleSheet(_input_ss())
            if fw:
                e.setFixedWidth(fw)
            return e

        self._date_edit    = _f("dd mmm yyyy")
        self._truck_edit   = _f("T700 DXY")
        self._trailer_edit = _f("T966 DYY")
        self._desc_edit    = _f("Description")
        self._usd_edit     = _f("0.00", fw=120)
        self._kwacha_edit  = _f("0.00", fw=120)

        form.addRow("Date *",        self._date_edit)
        form.addRow("Truck No.",     self._truck_edit)
        form.addRow("Trailer No.",   self._trailer_edit)
        form.addRow("Description *", self._desc_edit)
        form.addRow("USD",           self._usd_edit)
        form.addRow("Kwacha",        self._kwacha_edit)
        vl.addLayout(form)

        if self._record:
            self._date_edit.setText(self._record.get("date_str", ""))
            self._truck_edit.setText(self._record.get("truck_no", ""))
            self._trailer_edit.setText(self._record.get("trailer_no", ""))
            self._desc_edit.setText(self._record.get("description", ""))
            self._usd_edit.setText(str(self._record.get("amount_usd", "")))
            self._kwacha_edit.setText(str(self._record.get("amount_kwacha", "")))

        vl.addWidget(_hsep())

        btn_row = QWidget()
        btn_row.setStyleSheet("background:transparent;")
        brl = QHBoxLayout(btn_row)
        brl.setContentsMargins(0, 0, 0, 0)
        brl.addStretch()
        cancel = _btn("Cancel", primary=False)
        cancel.clicked.connect(self.reject)
        brl.addWidget(cancel)
        save = _btn("Save Entry", "mdi.content-save-outline")
        save.clicked.connect(self._save)
        brl.addWidget(save)
        vl.addWidget(btn_row)

    def _save(self) -> None:
        desc = self._desc_edit.text().strip()
        if not desc:
            QMessageBox.warning(self, "Validation", "Description is required.")
            return
        try:
            usd    = float(self._usd_edit.text().strip() or "0")
            kwacha = float(self._kwacha_edit.text().strip() or "0")
        except ValueError:
            QMessageBox.warning(self, "Validation", "USD and Kwacha must be numbers.")
            return
        self.saved.emit({
            "date_str":      self._date_edit.text().strip(),
            "truck_no":      self._truck_edit.text().strip(),
            "trailer_no":    self._trailer_edit.text().strip(),
            "description":   desc,
            "amount_usd":    usd,
            "amount_kwacha": kwacha,
        })
        self.accept()


class HarrisonExpensesWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._page = 1
        self._page_size = 25
        self._total = 0
        self._search = ""
        self._build()
        self.refresh()

    def _build(self) -> None:
        self.setStyleSheet(f"background:{_BG};")
        vl = QVBoxLayout(self)
        vl.setContentsMargins(20, 20, 20, 16)
        vl.setSpacing(12)

        header = _PageHeader("Harrison Expenses", "mdi.account-tie")
        new_btn = _btn("New Entry", "mdi.plus-circle-outline")
        new_btn.clicked.connect(self._new_entry)
        header.add_right(new_btn)
        vl.addWidget(header)
        vl.addWidget(_hsep())

        tb = QWidget()
        tb.setStyleSheet("background:transparent;")
        tbl = QHBoxLayout(tb)
        tbl.setContentsMargins(0, 0, 0, 0)
        tbl.setSpacing(8)
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Search description, truck…")
        self._search_edit.setFixedWidth(240)
        self._search_edit.setStyleSheet(_input_ss())
        self._search_edit.textChanged.connect(self._on_search)
        tbl.addWidget(self._search_edit)
        tbl.addStretch()
        export_btn = _btn("Export", "mdi.download-outline", primary=False)
        tbl.addWidget(export_btn)
        vl.addWidget(tb)

        self._table = _make_table(_HARRISON_HEADERS)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setStretchLastSection(True)
        vl.addWidget(self._table, 1)

        self._totals = _TotalsBar([("usd", "USD "), ("kwacha", "ZMW ")])
        vl.addWidget(self._totals)

        self._pager = _PaginationBar()
        self._pager.page_changed.connect(self._go_page)
        self._pager.size_changed.connect(self._on_page_size)
        vl.addWidget(self._pager)

    def refresh(self) -> None:
        asyncio.ensure_future(self._load())

    async def _load(self) -> None:
        from tahmeed.services import accountant_service as svc
        skip = (self._page - 1) * self._page_size
        recs, total = await asyncio.gather(
            svc.get_separate_expenses_list("harrison", self._search, "",
                                           self._page_size, skip),
            svc.count_separate_expenses("harrison", self._search, ""),
        )
        self._total = total
        self._fill_table(recs)
        self._pager.set_total(total, self._page_size, self._page)
        totals = await svc.get_separate_expense_totals("harrison")
        self._totals.set_total("usd", totals.get("amount_usd", 0.0), "USD ")
        self._totals.set_total("kwacha", totals.get("amount_kwacha", 0.0), "ZMW ")

    def _fill_table(self, recs: List[dict]) -> None:
        t = self._table
        t.setRowCount(0)
        for i, rec in enumerate(recs, 1):
            r = t.rowCount()
            t.insertRow(r)
            t.setItem(r, 0, _cell(str(i)))
            t.setItem(r, 1, _cell(rec.get("date_str", "")))
            t.setItem(r, 2, _cell(rec.get("truck_no", "")))
            t.setItem(r, 3, _cell(rec.get("trailer_no", "")))
            t.setItem(r, 4, _cell(rec.get("description", "")))
            t.setItem(r, 5, _cell(_fmt_num(rec.get("amount_usd"), "$ "), mono=True))
            t.setItem(r, 6, _cell(_fmt_num(rec.get("amount_kwacha"), "ZMW ", 0), mono=True))

    def _new_entry(self) -> None:
        dlg = _HarrisonEntryDialog(parent=self)
        dlg.saved.connect(self._on_saved)
        dlg.exec()

    def _on_saved(self, data: dict) -> None:
        asyncio.ensure_future(self._save_entry(data))

    async def _save_entry(self, data: dict) -> None:
        from tahmeed.services import accountant_service as svc
        await svc.save_separate_expense("harrison", data)
        self.refresh()

    def _on_search(self, text: str) -> None:
        self._search = text
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
#  Placeholder widget factory  (Afritrack, Third Party, COMESA)
# ═══════════════════════════════════════════════════════════════════════════════

class _PlaceholderExpenseWidget(QWidget):
    def __init__(
        self,
        title: str,
        icon_name: str,
        hint: str = "Import functionality coming soon.",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setStyleSheet(f"background:{_BG};")
        vl = QVBoxLayout(self)
        vl.setContentsMargins(20, 20, 20, 16)
        vl.setSpacing(12)

        header = _PageHeader(title, icon_name)
        import_btn = _btn("Import", "mdi.upload-outline")
        import_btn.setEnabled(False)
        header.add_right(import_btn)
        vl.addWidget(header)
        vl.addWidget(_hsep())

        # Coming Soon card
        card = QFrame()
        card.setStyleSheet(
            f"QFrame{{background:{_WHITE};border:1px solid {_BORDER};"
            "border-radius:8px;}}"
        )
        card_vl = QVBoxLayout(card)
        card_vl.setContentsMargins(0, 60, 0, 60)
        card_vl.setAlignment(Qt.AlignCenter)

        try:
            icon_lbl = QLabel()
            icon_lbl.setPixmap(qta.icon(icon_name, color="#D1D5DB").pixmap(56, 56))
            icon_lbl.setAlignment(Qt.AlignCenter)
            icon_lbl.setStyleSheet("background:transparent;border:none;")
            card_vl.addWidget(icon_lbl)
        except Exception:
            pass

        card_vl.addSpacing(16)
        t_lbl = _lbl("Coming Soon", size=16, weight=700, color=_TM)
        t_lbl.setAlignment(Qt.AlignCenter)
        card_vl.addWidget(t_lbl)

        card_vl.addSpacing(6)
        h_lbl = _lbl(hint, size=12, color=_T2)
        h_lbl.setAlignment(Qt.AlignCenter)
        card_vl.addWidget(h_lbl)

        vl.addWidget(card, 1)

    def refresh(self) -> None:
        pass


# ═══════════════════════════════════════════════════════════════════════════════
#  Afritrack — Schedule of Differences  (Excel-style interactive grid)
# ═══════════════════════════════════════════════════════════════════════════════

_AF_HEADERS = [
    "S/NO", "TRUCKS", "NO OF DAYS", "NON-TRANS DAYS",
    "TRANS DAYS", "RATE / DAY", "TOTAL (TAHMEED)",
    "TOTAL (INVOICE)", "VARIANCE", "REMARKS",
]
_AF_NCOLS       = len(_AF_HEADERS)
_AF_COL_SNO     = 0
_AF_COL_TRUCK   = 1
_AF_COL_DAYS    = 2
_AF_COL_NTRANS  = 3
_AF_COL_TRANS   = 4   # formula: DAYS − NTRANS
_AF_COL_RATE    = 5
_AF_COL_TOTAL_T = 6   # formula: TRANS × RATE
_AF_COL_TOTAL_I = 7
_AF_COL_VAR     = 8   # formula: TOTAL_T − TOTAL_I
_AF_COL_REMARKS = 9
_AF_FORMULA_COLS = frozenset({_AF_COL_TRANS, _AF_COL_TOTAL_T, _AF_COL_VAR})
_AF_NUM_COLS     = frozenset(range(2, 9))    # cols 2-8 are numeric

_AF_FOOTER_DEFS: List[Tuple[str, bool, bool, bool, bool]] = [
    # (key,        bold,   red,    show_i, show_var)
    ("sub",        False,  False,  True,   True),
    ("inst",       False,  False,  True,   True),
    ("sub2",       False,  False,  True,   True),
    ("vat",        False,  False,  True,   True),
    ("sub3",       False,  False,  True,   True),
    ("wht",        False,  False,  True,   True),
    ("payable",    True,   False,  True,   True),
    ("bal",        False,  True,   False,  False),
    ("total",      True,   True,   False,  False),
]


# ── tiny helpers ───────────────────────────────────────────────────────────────

def _af_flt(text: str) -> float:
    if not text or text in ("-", "—"):
        return 0.0
    try:
        return float(text.replace(",", ""))
    except Exception:
        return 0.0


def _af_fmt(val: float, decimals: int = 2) -> str:
    if val == 0.0:
        return "-"
    return f"{val:,.{decimals}f}"


# ── Excel-style reader ─────────────────────────────────────────────────────────

def _read_afritrack_file(path: str) -> Tuple[List[List[str]], float, float, float]:
    """Parse Afritrack xlsx.  Returns (data_rows, inst_t, inst_i, bal_mar)."""
    if not _HAS_OPENPYXL:
        raise RuntimeError("openpyxl is required to import Afritrack files.")
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.active
    raw = list(ws.iter_rows(values_only=True))
    if not raw:
        return [], 0.0, 0.0, 0.0

    data_rows: List[List[str]] = []
    inst_t = inst_i = bal_mar = 0.0

    def _v(row, idx, fallback=0.0):
        try:
            v = row[idx] if len(row) > idx else None
            return float(v) if v is not None else fallback
        except Exception:
            return fallback

    def _s(val, dec=2):
        if val is None or val == 0.0:
            return "-"
        try:
            f = float(val)
            return f"{f:,.{dec}f}" if f != 0 else "-"
        except Exception:
            return str(val).strip()

    for row in raw[1:]:           # skip header row
        sno = row[0] if row else None
        if sno is None:
            label = " ".join(str(c or "").lower() for c in row[:9])
            if "installation fee" in label:
                inst_t = _v(row, 6)
                inst_i = _v(row, 7)
            elif "bal mar" in label or "balance mar" in label:
                bal_mar = _v(row, 6)
            continue
        try:
            sno = int(sno)
        except (TypeError, ValueError):
            continue

        days    = _v(row, 2)
        ntrans  = _v(row, 3)
        trans   = _v(row, 4) if (len(row) > 4 and row[4] is not None) else days - ntrans
        rate    = _v(row, 5)
        total_t = _v(row, 6) if (len(row) > 6 and row[6] is not None) else trans * rate
        total_i = _v(row, 7)
        remarks = str(row[9] or "").strip() if len(row) > 9 else ""

        def _int_or_dec(f):
            return _s(f, 0) if f == int(f) else _s(f)

        data_rows.append([
            str(sno),
            str(row[1] or "").strip(),
            _int_or_dec(days),
            _int_or_dec(ntrans),
            _int_or_dec(trans),
            _s(rate, 6),
            _s(total_t),
            _s(total_i),
            "",             # variance — auto-computed
            remarks,
        ])

    return data_rows, inst_t, inst_i, bal_mar


# ── Excel exporter ─────────────────────────────────────────────────────────────

def _export_afritrack_xlsx(
    path: str,
    grid: "_AfritrackGrid",
    inst_t: float,
    inst_i: float,
    bal_mar: float,
    vat_rate: float,
    period: str,
) -> None:
    if not _HAS_OPENPYXL:
        raise RuntimeError("openpyxl is required for Excel export.")
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    wb = Workbook()
    ws = wb.active
    ws.title = f"Afritrack {period}"

    thin  = Side(border_style="thin", color="E5E7EB")
    bdr   = Border(left=thin, right=thin, top=thin, bottom=thin)
    r_aln = Alignment(horizontal="right",  vertical="center")
    l_aln = Alignment(horizontal="left",   vertical="center")
    c_aln = Alignment(horizontal="center", vertical="center")
    w_aln = Alignment(horizontal="center", vertical="center", wrap_text=True)

    hdr_fill = PatternFill("solid", fgColor="EFF6FF")
    hdr_font = Font(name="Calibri", bold=True, size=11, color="1E3A5F")
    frm_fill = PatternFill("solid", fgColor="EFF6FF")
    frm_font = Font(name="Calibri", size=11, color="1D4ED8")
    alt_fill = PatternFill("solid", fgColor="F9FAFB")
    red_fill = PatternFill("solid", fgColor="FEF2F2")
    red_font = Font(name="Calibri", bold=True, size=11, color="B91C1C")
    bld_font = Font(name="Calibri", bold=True, size=11)
    nrm_font = Font(name="Calibri", size=11)

    col_widths_ch = [6, 16, 10, 12, 10, 10, 15, 15, 10, 20]
    for c, w in enumerate(col_widths_ch, 1):
        ws.column_dimensions[ws.cell(1, c).column_letter].width = w

    # header row
    ws.row_dimensions[1].height = 34
    col_labels = [
        "S/NO", "TRUCKS", f"NO OF DAYS\n{period.upper()}",
        "NON-TRANS\nDAYS", "TRANS\nDAYS", "RATE/DAY",
        "TOTAL AS PER\nTAHMEED", "TOTAL AS PER\nINVOICE", "VARIANCE", "REMARKS",
    ]
    for c, label in enumerate(col_labels, 1):
        cell = ws.cell(row=1, column=c, value=label)
        cell.font = hdr_font; cell.fill = hdr_fill
        cell.border = bdr;   cell.alignment = w_aln

    # data rows
    data = grid.get_all_data()
    for ri, row in enumerate(data, 2):
        ws.row_dimensions[ri].height = 18
        is_alt = (ri % 2 == 0)
        for ci, val in enumerate(row, 1):
            is_formula = (ci - 1) in _AF_FORMULA_COLS
            num = None
            if 3 <= ci <= 9 and val not in ("", "-", "—"):
                try:
                    num = float(val.replace(",", ""))
                except Exception:
                    pass
            cell = ws.cell(row=ri, column=ci,
                           value=num if num is not None else (val or None))
            cell.border    = bdr
            cell.alignment = r_aln if 3 <= ci <= 9 else (c_aln if ci == 1 else l_aln)
            if is_formula:
                cell.font = frm_font; cell.fill = frm_fill
            elif is_alt:
                cell.font = nrm_font; cell.fill = alt_fill
            else:
                cell.font = nrm_font

    # footer rows
    st_t, st_i, _ = grid.get_col_totals()
    it = inst_t; ii = inst_i; bm = bal_mar; vr = vat_rate
    s2t  = st_t + it;     s2i = st_i + ii
    vat_t = s2t * vr / 100; vat_i = s2i * vr / 100
    s3t  = s2t + vat_t;   s3i = s2i + vat_i
    wt   = s3t * 0.05;    wi  = s3i * 0.05
    pt   = s3t - wt;      pi  = s3i - wi
    tot  = pt + bm

    fr = len(data) + 2
    footer_rows = [
        (fr,   "",                                    st_t,  st_i,  st_t-st_i, False, False),
        (fr+1, "Installation Fees",                   it,    ii,    it-ii,     False, False),
        (fr+2, "",                                    s2t,   s2i,   s2t-s2i,   False, False),
        (fr+3, f"VAT @ {vr:.0f}%",                   vat_t, vat_i, vat_t-vat_i, False, False),
        (fr+4, "",                                    s3t,   s3i,   s3t-s3i,   False, False),
        (fr+5, "Less WHT @ 5%",                      wt,    wi,    wt-wi,     False, False),
        (fr+6, "Amount Payable",                      pt,    pi,    pt-pi,     True,  False),
        (fr+7, "Add Bal. Mar (WHT Calculations)",     bm,    None,  None,      False, True),
        (fr+8, f"Total Payable {period} Account",     tot,   None,  None,      True,  True),
    ]
    for frow, label, vt_, vi_, vvar, bold, red in footer_rows:
        ws.row_dimensions[frow].height = 18
        if label:
            lc = ws.cell(row=frow, column=5, value=label)
            lc.alignment = r_aln
            lc.font = red_font if red else (bld_font if bold else nrm_font)
            if red:
                lc.fill = red_fill
        for xc, xv in [(_AF_COL_TOTAL_T+1, vt_),
                        (_AF_COL_TOTAL_I+1, vi_),
                        (_AF_COL_VAR+1,     vvar)]:
            if xv is not None:
                cell = ws.cell(row=frow, column=xc, value=xv)
                cell.alignment = r_aln; cell.border = bdr
                cell.font = red_font if red else (bld_font if bold else nrm_font)
                if red:
                    cell.fill = red_fill

    wb.save(path)


# ── Editable grid ─────────────────────────────────────────────────────────────

class _AfritrackGrid(QTableWidget):
    """
    Editable Excel-like grid.  Formula cols (TRANS DAYS, TOTAL TAHMEED,
    VARIANCE) auto-compute and are read-only.  All other cols are editable.
    Supports Ctrl+C/X/V, Ctrl+Z/Y, Delete, multi-select, right-click menu.
    """
    data_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(0, _AF_NCOLS, parent)
        self._block          = False
        self._undo_stack: List[List[List[str]]] = []
        self._redo_stack: List[List[List[str]]] = []
        self._pre_edit_snap: Optional[List[List[str]]] = None
        self._setup()

    # ── initialisation ────────────────────────────────────────────────────────

    def _setup(self) -> None:
        self.setHorizontalHeaderLabels(_AF_HEADERS)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setEditTriggers(
            QAbstractItemView.DoubleClicked | QAbstractItemView.AnyKeyPressed
        )
        self.setAlternatingRowColors(True)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setMinimumSectionSize(18)
        self.verticalHeader().setDefaultSectionSize(22)
        self.setShowGrid(True)
        self.setSortingEnabled(False)

        hdr = self.horizontalHeader()
        hdr.setHighlightSections(False)
        hdr.setMinimumSectionSize(36)
        hdr.setStretchLastSection(True)
        col_widths = [38, 110, 60, 76, 60, 62, 96, 96, 66, 120]
        for c, w in enumerate(col_widths):
            self.setColumnWidth(c, w)

        self.setStyleSheet(self._grid_ss())
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._ctx_menu)
        self.currentItemChanged.connect(self._capture_pre_edit)
        self.itemChanged.connect(self._on_item_changed)

    def _grid_ss(self) -> str:
        return (
            f"QTableWidget{{background:{_WHITE};gridline-color:{_BORDER};"
            "border:none;font-size:12px;font-family:'Segoe UI',sans-serif;}}"
            f"QTableWidget::item{{padding:0 6px;color:{_T1};}}"
            f"QTableWidget::item:selected{{background:{_QB_SEL_BG};color:{_QB_SEL_FG};}}"
            f"QTableWidget::item:alternate{{background:#F9FAFB;}}"
            f"QHeaderView::section{{background:{_QB_HDR_BG};color:{_QB_HDR_FG};"
            "font-size:11px;font-weight:700;font-family:'Segoe UI',sans-serif;"
            f"border:none;border-right:1px solid {_BORDER};"
            f"border-bottom:2px solid {_BLUE};padding:0 6px;height:32px;}}"
            "QScrollBar:vertical{width:8px;background:transparent;}"
            f"QScrollBar::handle:vertical{{background:#D1D5DB;border-radius:4px;}}"
            "QScrollBar:horizontal{height:8px;background:transparent;}"
            f"QScrollBar::handle:horizontal{{background:#D1D5DB;border-radius:4px;}}"
        )

    # ── keyboard ──────────────────────────────────────────────────────────────

    def keyPressEvent(self, event) -> None:
        mod = event.modifiers()
        key = event.key()
        if mod == Qt.ControlModifier:
            if key == Qt.Key_C: self._do_copy();  return
            if key == Qt.Key_X: self._do_cut();   return
            if key == Qt.Key_V: self._do_paste();  return
            if key == Qt.Key_Z: self._do_undo();   return
            if key == Qt.Key_Y: self._do_redo();   return
        # Tab on last column → wrap to TRUCKS col of next row (add row if needed)
        if key == Qt.Key_Tab and not (mod & Qt.ShiftModifier):
            row, col = self.currentRow(), self.currentColumn()
            if col == _AF_NCOLS - 1:
                if row >= self.rowCount() - 1:
                    self.add_row()
                self.setCurrentCell(min(row + 1, self.rowCount() - 1), _AF_COL_TRUCK)
                return
        if key == Qt.Key_Delete:
            self.delete_selected_rows(); return
        if key == Qt.Key_Backspace:
            self._do_clear(); return
        super().keyPressEvent(event)

    def closeEditor(self, editor, hint) -> None:
        """Tab while editing the last column → commit + move to TRUCKS of next row."""
        from PySide6.QtWidgets import QAbstractItemDelegate
        if hint == QAbstractItemDelegate.EditNextItem:
            col = self.currentColumn()
            row = self.currentRow()
            if col == _AF_NCOLS - 1:
                super().closeEditor(editor, QAbstractItemDelegate.SubmitModelCache)
                if row >= self.rowCount() - 1:
                    self.add_row()
                self.setCurrentCell(min(row + 1, self.rowCount() - 1), _AF_COL_TRUCK)
                return
        super().closeEditor(editor, hint)

    # ── copy / cut / paste ────────────────────────────────────────────────────

    def _sel_rows_cols(self) -> Tuple[List[int], List[int]]:
        idxs = self.selectedIndexes()
        if not idxs:
            return [], []
        return (
            sorted({i.row() for i in idxs}),
            sorted({i.column() for i in idxs}),
        )

    def _do_copy(self) -> None:
        rows, cols = self._sel_rows_cols()
        if not rows:
            return
        lines = []
        for r in rows:
            lines.append("\t".join(
                (self.item(r, c).text() if self.item(r, c) else "") for c in cols
            ))
        QApplication.clipboard().setText("\n".join(lines))

    def _do_cut(self) -> None:
        self._do_copy()
        self._do_clear()

    def _do_paste(self) -> None:
        text = QApplication.clipboard().text()
        if not text:
            return
        paste_rows = [line.split("\t") for line in text.splitlines()]
        while paste_rows and all(c == "" for c in paste_rows[-1]):
            paste_rows.pop()
        sel_rows, sel_cols = self._sel_rows_cols()
        r0 = sel_rows[0] if sel_rows else max(self.currentRow(), 0)
        c0 = sel_cols[0] if sel_cols else max(self.currentColumn(), 0)
        self._push_undo()
        self._block = True
        for dr, row_data in enumerate(paste_rows):
            r = r0 + dr
            if r >= self.rowCount():
                self._insert_blank_row(r)
            for dc, val in enumerate(row_data):
                c = c0 + dc
                if c >= self.columnCount() or c in _AF_FORMULA_COLS:
                    continue
                item = self.item(r, c) or QTableWidgetItem()
                item.setText(val.strip())
                self._style_item(item, r, c)
                self.setItem(r, c, item)
        self._block = False
        self._recalc_all()
        self.data_changed.emit()

    def _do_clear(self) -> None:
        self._push_undo()
        self._block = True
        for item in self.selectedItems():
            if item.column() not in (_AF_FORMULA_COLS | {_AF_COL_SNO}):
                item.setText("")
        self._block = False
        self._recalc_all()
        self.data_changed.emit()

    # ── undo / redo ───────────────────────────────────────────────────────────

    def _snapshot(self) -> List[List[str]]:
        return [
            [(self.item(r, c).text() if self.item(r, c) else "")
             for c in range(self.columnCount())]
            for r in range(self.rowCount())
        ]

    def _push_undo(self) -> None:
        self._undo_stack.append(self._snapshot())
        if len(self._undo_stack) > 50:
            self._undo_stack.pop(0)
        self._redo_stack.clear()

    def _restore(self, snap: List[List[str]]) -> None:
        self._block = True
        self.setRowCount(len(snap))
        for r, row_data in enumerate(snap):
            for c, text in enumerate(row_data):
                item = self.item(r, c) or QTableWidgetItem()
                item.setText(text)
                self._style_item(item, r, c)
                self.setItem(r, c, item)
        self._block = False
        self._recalc_all()
        self.data_changed.emit()

    def _do_undo(self) -> None:
        if not self._undo_stack:
            return
        self._redo_stack.append(self._snapshot())
        self._restore(self._undo_stack.pop())

    def _do_redo(self) -> None:
        if not self._redo_stack:
            return
        self._push_undo()
        self._restore(self._redo_stack.pop())

    # ── formula engine ────────────────────────────────────────────────────────

    def _capture_pre_edit(self, _curr, _prev) -> None:
        if not self._block:
            self._pre_edit_snap = self._snapshot()

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._block:
            return
        col = item.column()
        if col not in _AF_FORMULA_COLS:
            if self._pre_edit_snap is not None:
                self._undo_stack.append(self._pre_edit_snap)
                if len(self._undo_stack) > 50:
                    self._undo_stack.pop(0)
                self._redo_stack.clear()
                self._pre_edit_snap = None
        self._recalc_row(item.row())
        self.data_changed.emit()

    def _recalc_all(self) -> None:
        for r in range(self.rowCount()):
            self._recalc_row(r, renumber=False)
        self._renumber()

    def _recalc_row(self, row: int, renumber: bool = True) -> None:
        self._block = True

        def _txt(c):
            it = self.item(row, c)
            return it.text() if it else ""

        days    = _af_flt(_txt(_AF_COL_DAYS))
        ntrans  = _af_flt(_txt(_AF_COL_NTRANS))
        rate    = _af_flt(_txt(_AF_COL_RATE))
        total_i = _af_flt(_txt(_AF_COL_TOTAL_I))
        trans   = days - ntrans
        total_t = trans * rate
        var     = total_t - total_i

        self._set_fcell(row, _AF_COL_TRANS,   trans,  is_var=False)
        self._set_fcell(row, _AF_COL_TOTAL_T, total_t, is_var=False)
        self._set_fcell(row, _AF_COL_VAR,     var,    is_var=True)

        self._block = False
        if renumber:
            self._renumber()

    def _set_fcell(self, row: int, col: int, val: float,
                   is_var: bool = False) -> None:
        item = self.item(row, col)
        if item is None:
            item = QTableWidgetItem()
            self.setItem(row, col, item)
        item.setText(_af_fmt(val))
        item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        item.setFlags(
            (item.flags() & ~Qt.ItemIsEditable) | Qt.ItemIsSelectable | Qt.ItemIsEnabled
        )
        if is_var:
            if val < -0.005:
                item.setForeground(QColor(_QB_VNEG_FG))
                item.setBackground(QColor(_QB_VNEG_BG))
            elif val > 0.005:
                item.setForeground(QColor(_QB_VPOS_FG))
                item.setBackground(QColor(_QB_FORM_BG))
            else:
                item.setForeground(QColor(_QB_VZRO_FG))
                item.setBackground(QColor(_QB_FORM_BG))
        else:
            item.setForeground(QColor(_QB_FORM_FG))
            item.setBackground(QColor(_QB_FORM_BG))

    def _style_item(self, item: QTableWidgetItem, row: int, col: int) -> None:
        if col in _AF_FORMULA_COLS:
            item.setBackground(QColor(_QB_FORM_BG))
            item.setForeground(QColor(_QB_FORM_FG))
            item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        elif col in _AF_NUM_COLS:
            item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        elif col == _AF_COL_SNO:
            item.setTextAlignment(Qt.AlignCenter | Qt.AlignVCenter)
            item.setForeground(QColor(_T2))

    def _renumber(self) -> None:
        self._block = True
        for r in range(self.rowCount()):
            item = self.item(r, _AF_COL_SNO)
            if item is None:
                item = QTableWidgetItem()
                self.setItem(r, _AF_COL_SNO, item)
            item.setText(str(r + 1))
            item.setForeground(QColor(_T2))
            item.setTextAlignment(Qt.AlignCenter | Qt.AlignVCenter)
            item.setFlags(
                (item.flags() & ~Qt.ItemIsEditable) | Qt.ItemIsSelectable | Qt.ItemIsEnabled
            )
        self._block = False

    # ── row operations ────────────────────────────────────────────────────────

    def _insert_blank_row(self, r: int) -> None:
        self.insertRow(r)
        self._block = True
        for c in range(_AF_NCOLS):
            item = QTableWidgetItem("")
            self._style_item(item, r, c)
            self.setItem(r, c, item)
        self._block = False

    def add_row(self, values: List[str] = None) -> None:
        r = self.rowCount()
        self._insert_blank_row(r)
        if values:
            self._block = True
            for c in range(min(len(values), _AF_NCOLS)):
                if c in (_AF_FORMULA_COLS | {_AF_COL_SNO}):
                    continue
                item = self.item(r, c) or QTableWidgetItem()
                item.setText(str(values[c]) if values[c] is not None else "")
                self._style_item(item, r, c)
                self.setItem(r, c, item)
            self._block = False
        self._recalc_row(r)
        self.scrollToBottom()

    def insert_row_above(self) -> None:
        sel_rows, _ = self._sel_rows_cols()
        r = sel_rows[0] if sel_rows else self.rowCount()
        self._push_undo()
        self._insert_blank_row(r)
        self._recalc_row(r)
        self._renumber()
        self.data_changed.emit()

    def delete_selected_rows(self) -> None:
        rows = sorted({i.row() for i in self.selectedIndexes()}, reverse=True)
        if not rows:
            return
        self._push_undo()
        for r in rows:
            self.removeRow(r)
        self._renumber()
        self.data_changed.emit()

    # ── data access ───────────────────────────────────────────────────────────

    def get_all_data(self) -> List[List[str]]:
        return [
            [(self.item(r, c).text() if self.item(r, c) else "")
             for c in range(self.columnCount())]
            for r in range(self.rowCount())
        ]

    def get_col_totals(self) -> Tuple[float, float, float]:
        def _sum(col):
            return sum(
                _af_flt(self.item(r, col).text() if self.item(r, col) else "")
                for r in range(self.rowCount())
                if not self.isRowHidden(r)
            )
        t = _sum(_AF_COL_TOTAL_T)
        i = _sum(_AF_COL_TOTAL_I)
        return t, i, t - i

    def filter_rows(self, text: str) -> None:
        text = text.lower().strip()
        for r in range(self.rowCount()):
            match = any(
                text in (self.item(r, c).text().lower() if self.item(r, c) else "")
                for c in range(self.columnCount())
            )
            self.setRowHidden(r, bool(text) and not match)

    # ── context menu ──────────────────────────────────────────────────────────

    def _ctx_menu(self, pos) -> None:
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu{{background:{_WHITE};border:1px solid {_BORDER};"
            f"border-radius:6px;padding:4px;"
            f"font-size:12px;font-family:'Segoe UI',sans-serif;}}"
            f"QMenu::item{{padding:6px 20px;border-radius:4px;color:{_T1};}}"
            f"QMenu::item:selected{{background:{_BLUE_L};}}"
            f"QMenu::separator{{height:1px;background:{_BORDER};margin:4px 8px;}}"
        )
        menu.addAction("Copy\t\tCtrl+C",        self._do_copy)
        menu.addAction("Cut\t\tCtrl+X",         self._do_cut)
        menu.addAction("Paste\t\tCtrl+V",        self._do_paste)
        menu.addAction("Clear Cells\tBackspace",  self._do_clear)
        menu.addSeparator()
        menu.addAction("Insert Row Above",              self.insert_row_above)
        menu.addAction("Add Row at Bottom\tTab",        self.add_row)
        menu.addAction("Delete Row(s)\t\tDel",          self.delete_selected_rows)
        menu.addSeparator()
        menu.addAction("Undo\t\tCtrl+Z",  self._do_undo)
        menu.addAction("Redo\t\tCtrl+Y",  self._do_redo)
        menu.exec(self.viewport().mapToGlobal(pos))


# ── Pinned footer ─────────────────────────────────────────────────────────────

class _AfritrackFooter(QWidget):
    """
    Non-scrolling totals section that mirrors the Excel footer layout:
    Subtotal → Installation Fees → Sub2 → VAT → Total+VAT →
    Less WHT @5% → Amount Payable → Add Bal. Mar → Total Payable Account
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._inst_t   = 0.0
        self._inst_i   = 0.0
        self._bal_mar  = 0.0
        self._vat_rate = 15.0
        self._build()

    def _build(self) -> None:
        vl = QVBoxLayout(self)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(0)

        sep = QFrame()
        sep.setFixedHeight(2)
        sep.setStyleSheet(f"background:{_BLUE};border:none;")
        vl.addWidget(sep)

        self._table = QTableWidget(len(_AF_FOOTER_DEFS), _AF_NCOLS)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.horizontalHeader().setVisible(False)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(24)
        self._table.setShowGrid(True)
        self._table.setStyleSheet(self._foot_ss())
        self._table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._table.setFocusPolicy(Qt.NoFocus)

        for ri in range(len(_AF_FOOTER_DEFS)):
            self._table.setSpan(ri, 0, 1, 6)   # cols 0-5 → merged label cell

        self._fix_height()
        vl.addWidget(self._table)

    def _foot_ss(self) -> str:
        return (
            f"QTableWidget{{background:{_QB_FOOT_BG};gridline-color:{_BORDER};"
            "border:none;font-size:12px;font-family:'Segoe UI',sans-serif;}}"
            f"QTableWidget::item{{padding:0 8px;color:{_T1};}}"
        )

    def _fix_height(self) -> None:
        h = sum(self._table.rowHeight(r) for r in range(len(_AF_FOOTER_DEFS))) + 2
        self._table.setFixedHeight(h)

    def sync_columns(self, grid: "_AfritrackGrid") -> None:
        for c in range(_AF_NCOLS):
            self._table.setColumnWidth(c, grid.columnWidth(c))

    def set_params(self, inst_t: float, inst_i: float,
                   bal_mar: float, vat_rate: float) -> None:
        self._inst_t   = inst_t
        self._inst_i   = inst_i
        self._bal_mar  = bal_mar
        self._vat_rate = vat_rate

    def refresh(self, grid: "_AfritrackGrid", month: str) -> None:
        sum_t, sum_i, _ = grid.get_col_totals()
        it   = self._inst_t;  ii  = self._inst_i
        s2t  = sum_t + it;    s2i = sum_i + ii
        vt   = s2t * self._vat_rate / 100
        vi   = s2i * self._vat_rate / 100
        s3t  = s2t + vt;      s3i = s2i + vi
        wt   = s3t * 0.05;    wi  = s3i * 0.05
        pt   = s3t - wt;      pi  = s3i - wi
        tot  = pt + self._bal_mar

        vals: Dict[str, Tuple[str, float, Optional[float], Optional[float]]] = {
            "sub":     ("",                                    sum_t,          sum_i,    sum_t - sum_i),
            "inst":    ("Installation Fees",                   it,             ii,       it - ii),
            "sub2":    ("",                                    s2t,            s2i,      s2t - s2i),
            "vat":     (f"VAT @ {self._vat_rate:.0f}%",       vt,             vi,       vt - vi),
            "sub3":    ("",                                    s3t,            s3i,      s3t - s3i),
            "wht":     ("Less WHT @ 5%",                      wt,             wi,       wt - wi),
            "payable": ("Amount Payable",                      pt,             pi,       pt - pi),
            "bal":     (f"Add Bal. Mar (WHT Calculations)",    self._bal_mar,  None,     None),
            "total":   (f"Total Payable {month} Account",      tot,            None,     None),
        }

        t = self._table
        for ri, (key, bold, red, show_i, show_var) in enumerate(_AF_FOOTER_DEFS):
            label, vt_, vi_, vvar = vals[key]

            # label cell (merged cols 0-5)
            lbl = t.item(ri, 0) or QTableWidgetItem()
            lbl.setText(label)
            lbl.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            fn = lbl.font(); fn.setBold(bold); lbl.setFont(fn)
            if red:
                lbl.setForeground(QColor(_QB_RED_DARK))
                lbl.setBackground(QColor(_QB_RED_BG))
            elif bold:
                lbl.setForeground(QColor(_QB_BOLD_FG))
                lbl.setBackground(QColor(_QB_FOOT_BG))
            else:
                lbl.setForeground(QColor(_T2))
                lbl.setBackground(QColor(_QB_FOOT_BG))
            t.setItem(ri, 0, lbl)

            def _put(col: int, val: Optional[float]) -> None:
                cell = t.item(ri, col) or QTableWidgetItem()
                if val is None:
                    cell.setText(""); cell.setBackground(QColor(_QB_FOOT_BG))
                    t.setItem(ri, col, cell); return
                cell.setText(_af_fmt(val))
                cell.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                fn2 = cell.font(); fn2.setBold(bold); cell.setFont(fn2)
                if red:
                    cell.setForeground(QColor(_QB_RED_DARK))
                    cell.setBackground(QColor(_QB_RED_BG))
                elif col == _AF_COL_VAR and val < -0.005:
                    cell.setForeground(QColor(_QB_VNEG_FG))
                    cell.setBackground(QColor(_QB_VNEG_BG))
                elif col == _AF_COL_VAR and val > 0.005:
                    cell.setForeground(QColor(_QB_VPOS_FG))
                    cell.setBackground(QColor(_QB_FOOT_BG))
                else:
                    cell.setForeground(QColor(_QB_BOLD_FG if bold else _T1))
                    cell.setBackground(QColor(_QB_FOOT_BG))
                t.setItem(ri, col, cell)

            _put(_AF_COL_TOTAL_T, vt_)
            _put(_AF_COL_TOTAL_I, vi_  if show_i   else None)
            _put(_AF_COL_VAR,     vvar if show_var  else None)

        self._fix_height()

    def get_export_data(self) -> Dict[str, float]:
        return {
            "inst_t":   self._inst_t,
            "inst_i":   self._inst_i,
            "bal_mar":  self._bal_mar,
            "vat_rate": self._vat_rate,
        }


# ── QB Bill-card field helpers ────────────────────────────────────────────────

def _qb_field_widget(label_text: str, input_widget: QWidget) -> QWidget:
    """Small ALL-CAPS gray label above an input — matches QB Bill field style."""
    w = QWidget()
    w.setStyleSheet("background:transparent;")
    vl = QVBoxLayout(w)
    vl.setContentsMargins(0, 0, 0, 0)
    vl.setSpacing(3)
    lbl = QLabel(label_text)
    lbl.setStyleSheet(
        f"color:{_T2};font-size:9px;font-weight:600;letter-spacing:0.8px;"
        f"font-family:'Segoe UI',sans-serif;background:transparent;"
    )
    vl.addWidget(lbl)
    vl.addWidget(input_widget)
    return w


def _qb_amount_widget(
    label_text: str,
    badge: str = "USD",
    red: bool = False,
) -> Tuple[QWidget, QLabel]:
    """
    QB-style amount display: currency badge on left + large bold value on right.
    Returns (container_widget, value_label).
    """
    w = QWidget()
    w.setStyleSheet("background:transparent;")
    vl = QVBoxLayout(w)
    vl.setContentsMargins(0, 0, 0, 0)
    vl.setSpacing(3)

    lbl = QLabel(label_text)
    lbl.setStyleSheet(
        f"color:{_T2};font-size:9px;font-weight:600;letter-spacing:0.8px;"
        f"font-family:'Segoe UI',sans-serif;background:transparent;"
    )
    vl.addWidget(lbl)

    box = QFrame()
    box_bg = _QB_RED_BG if red else _BG
    box.setStyleSheet(
        f"QFrame{{background:{box_bg};border:1px solid {_BORDER};border-radius:4px;}}"
    )
    box.setFixedHeight(34)
    hl = QHBoxLayout(box)
    hl.setContentsMargins(0, 0, 0, 0)
    hl.setSpacing(0)

    badge_lbl = QLabel(badge)
    badge_lbl.setFixedWidth(40)
    badge_lbl.setAlignment(Qt.AlignCenter)
    badge_bg = _QB_RED_BG if red else _QB_HDR_BG
    badge_fg = _QB_RED_DARK if red else _QB_HDR_FG
    badge_lbl.setStyleSheet(
        f"QLabel{{background:{badge_bg};border:none;"
        f"border-right:1px solid {_BORDER};"
        f"border-top-left-radius:4px;border-bottom-left-radius:4px;"
        f"color:{badge_fg};font-size:10px;font-weight:700;"
        f"font-family:'Segoe UI',sans-serif;}}"
    )
    hl.addWidget(badge_lbl)

    val_lbl = QLabel("0.00")
    val_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    val_fg = _QB_RED_DARK if red else _QB_HDR_FG
    val_lbl.setStyleSheet(
        f"QLabel{{color:{val_fg};font-size:14px;font-weight:700;"
        f"font-family:'Segoe UI',sans-serif;background:transparent;padding:0 10px;}}"
    )
    hl.addWidget(val_lbl, 1)
    vl.addWidget(box)
    return w, val_lbl


# ── Main widget ───────────────────────────────────────────────────────────────

class AfritrackWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._period = "Apr 2026"
        self._build()

    def _build(self) -> None:
        self.setStyleSheet(f"background:{_BG};")
        vl = QVBoxLayout(self)
        vl.setContentsMargins(20, 16, 20, 12)
        vl.setSpacing(8)

        # ── QB Bill-style form card (all metadata above the table) ────────────
        vl.addWidget(self._build_bill_card())

        # ── slim action toolbar ───────────────────────────────────────────────
        tb = QWidget()
        tb.setStyleSheet("background:transparent;")
        tbl = QHBoxLayout(tb)
        tbl.setContentsMargins(0, 0, 0, 0)
        tbl.setSpacing(8)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search trucks, remarks…")
        self._search.setFixedWidth(220)
        self._search.setStyleSheet(_input_ss())
        self._search.textChanged.connect(lambda t: self._grid.filter_rows(t))
        tbl.addWidget(self._search)
        tbl.addStretch()

        self._import_btn = _btn("Import", "mdi.upload-outline", height=32)
        self._export_btn = _btn("Export", "mdi.download-outline",
                                primary=False, height=32)
        recalc_btn = _btn("Recalculate", "mdi.refresh", primary=False, height=32)
        del_btn    = _btn("Delete Row",  "mdi.delete-outline",
                          primary=False, height=32)

        self._import_btn.clicked.connect(self._do_import)
        self._export_btn.clicked.connect(self._do_export)
        recalc_btn.clicked.connect(self._recalculate)
        del_btn.clicked.connect(lambda: self._grid.delete_selected_rows())

        tbl.addWidget(self._import_btn)
        tbl.addWidget(self._export_btn)
        tbl.addWidget(recalc_btn)
        tbl.addWidget(del_btn)
        vl.addWidget(tb)

        # ── grid card — takes all remaining space ─────────────────────────────
        card = QFrame()
        card.setStyleSheet(
            f"QFrame{{background:{_WHITE};border:1px solid {_BORDER};"
            "border-radius:6px;}}"
        )
        cl = QVBoxLayout(card)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)

        self._grid = _AfritrackGrid()
        self._grid.data_changed.connect(self._on_data_changed)
        cl.addWidget(self._grid, 1)
        vl.addWidget(card, 1)

        # ── status bar ────────────────────────────────────────────────────────
        self._status = _lbl(
            "0 trucks  ·  Import an Excel file or add rows manually",
            11, 400, _T2,
        )
        vl.addWidget(self._status)

        self._grid.add_row()
        self._on_data_changed()

    def _build_bill_card(self) -> QFrame:
        """
        QuickBooks Bill-style form card.
        Left  : SUPPLIER · [PERIOD | VAT%]
        Right : row 0 — INST. FEES TAHMEED | INST. FEES INVOICE
                row 1 — BAL MAR | LESS WHT | TOTAL INVOICE | AMOUNT DUE | TOTAL PAYABLE
        """
        card = QFrame()
        card.setObjectName("af_bill_card")
        card.setStyleSheet(
            "QFrame#af_bill_card{"
            f"background:{_WHITE};border:1px solid {_BORDER};border-radius:8px;}}"
        )

        root = QVBoxLayout(card)
        root.setContentsMargins(20, 14, 20, 16)
        root.setSpacing(10)

        # title row
        title_lbl = QLabel("Afritrack Schedule")
        title_lbl.setStyleSheet(
            f"color:{_QB_HDR_FG};font-size:20px;font-weight:700;"
            f"font-family:'Segoe UI',sans-serif;background:transparent;"
        )
        root.addWidget(title_lbl)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background:{_BORDER};")
        root.addWidget(sep)

        # ── body: left | divider | right ──────────────────────────────────────
        body = QWidget()
        body.setStyleSheet("background:transparent;")
        fl = QHBoxLayout(body)
        fl.setContentsMargins(0, 4, 0, 4)
        fl.setSpacing(0)

        # ── LEFT ──────────────────────────────────────────────────────────────
        left = QWidget()
        left.setStyleSheet("background:transparent;")
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 24, 0)
        ll.setSpacing(10)

        supp_lbl = QLineEdit("AFRITRACK")
        supp_lbl.setReadOnly(True)
        supp_lbl.setFixedWidth(180)
        supp_lbl.setStyleSheet(
            f"QLineEdit{{background:{_BG};border:1px solid {_BORDER};"
            "border-radius:4px;color:#111827;font-size:12px;font-weight:600;"
            "font-family:'Segoe UI',sans-serif;padding:0 10px;"
            "min-height:32px;max-height:32px;}}"
        )
        ll.addWidget(_qb_field_widget("SUPPLIER", supp_lbl))

        # PERIOD + VAT% on the same row
        pv_row = QWidget(); pv_row.setStyleSheet("background:transparent;")
        pvl = QHBoxLayout(pv_row)
        pvl.setContentsMargins(0, 0, 0, 0)
        pvl.setSpacing(14)

        self._period_cb = QComboBox()
        self._period_cb.setFixedWidth(130)
        self._period_cb.setStyleSheet(_input_ss())
        _months = ["Jan","Feb","Mar","Apr","May","Jun",
                   "Jul","Aug","Sep","Oct","Nov","Dec"]
        for yr in ["2024", "2025", "2026", "2027"]:
            for mo in _months:
                self._period_cb.addItem(f"{mo} {yr}")
        self._period_cb.setCurrentText("Apr 2026")
        self._period_cb.currentTextChanged.connect(self._on_period)
        pvl.addWidget(_qb_field_widget("PERIOD", self._period_cb))

        self._vat_edit = QLineEdit("15")
        self._vat_edit.setFixedWidth(60)
        self._vat_edit.setStyleSheet(_input_ss())
        self._vat_edit.textChanged.connect(self._on_params)
        pvl.addWidget(_qb_field_widget("VAT %", self._vat_edit))
        pvl.addStretch()

        ll.addWidget(pv_row)
        ll.addStretch()
        fl.addWidget(left)

        # vertical divider
        vdiv = QFrame()
        vdiv.setFrameShape(QFrame.VLine)
        vdiv.setFixedWidth(1)
        vdiv.setStyleSheet(f"background:{_BORDER};")
        fl.addWidget(vdiv)

        # ── RIGHT ─────────────────────────────────────────────────────────────
        right = QWidget()
        right.setStyleSheet("background:transparent;")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(24, 0, 0, 0)
        rl.setSpacing(10)

        # row 0: installation fees
        inst_row = QWidget(); inst_row.setStyleSheet("background:transparent;")
        il = QHBoxLayout(inst_row)
        il.setContentsMargins(0, 0, 0, 0)
        il.setSpacing(20)

        self._inst_t = QLineEdit("0")
        self._inst_t.setStyleSheet(_input_ss())
        self._inst_t.textChanged.connect(self._on_params)
        il.addWidget(_qb_field_widget("INST. FEES — TAHMEED", self._inst_t))

        self._inst_i = QLineEdit("0")
        self._inst_i.setStyleSheet(_input_ss())
        self._inst_i.textChanged.connect(self._on_params)
        il.addWidget(_qb_field_widget("INST. FEES — INVOICE", self._inst_i))
        il.addStretch()
        rl.addWidget(inst_row)

        # row 1: BAL MAR | LESS WHT | TOTAL INVOICE | AMOUNT DUE | TOTAL PAYABLE
        totals_row = QWidget(); totals_row.setStyleSheet("background:transparent;")
        tl = QHBoxLayout(totals_row)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(20)

        self._bal_mar = QLineEdit("0")
        self._bal_mar.setFixedWidth(110)
        self._bal_mar.setStyleSheet(_input_ss())
        self._bal_mar.textChanged.connect(self._on_params)
        tl.addWidget(_qb_field_widget("BALANCE MAR (WHT)", self._bal_mar))

        less_wht_w, self._less_wht_val = _qb_amount_widget("LESS WHT", "USD", red=False)
        tl.addWidget(less_wht_w)

        total_inv_w, self._total_inv_val = _qb_amount_widget("TOTAL INVOICE", "USD", red=False)
        tl.addWidget(total_inv_w)

        amt_w, self._amount_due_val = _qb_amount_widget("AMOUNT DUE", "USD", red=False)
        tl.addWidget(amt_w)

        tot_w, self._total_pay_val = _qb_amount_widget("TOTAL PAYABLE", "USD", red=True)
        tl.addWidget(tot_w)
        tl.addStretch()
        rl.addWidget(totals_row)

        fl.addWidget(right, 1)
        root.addWidget(body)
        return card

    # ── slots ─────────────────────────────────────────────────────────────────

    def _on_period(self, text: str) -> None:
        self._period = text
        self._on_data_changed()

    def _on_params(self) -> None:
        self._on_data_changed()

    def _on_data_changed(self) -> None:
        try:
            it = _af_flt(self._inst_t.text())
            ii = _af_flt(self._inst_i.text())
            bm = _af_flt(self._bal_mar.text())
            vr = float(self._vat_edit.text() or "15")
        except Exception:
            it = ii = bm = 0.0; vr = 15.0

        # ── live bill-card totals ──────────────────────────────────────────────
        tt, ti, _ = self._grid.get_col_totals()
        # Tahmeed side
        s2t   = tt + it
        vat_t = s2t * vr / 100
        s3t   = s2t + vat_t
        wht_t = s3t * 0.05
        pay_t = s3t - wht_t
        total = pay_t + bm
        # Invoice side (for TOTAL INVOICE display)
        s2i   = ti + ii
        vat_i = s2i * vr / 100
        s3i   = s2i + vat_i
        pay_i = s3i * 0.95

        self._less_wht_val.setText(f"{wht_t:,.2f}")
        self._total_inv_val.setText(f"{pay_i:,.2f}")
        self._amount_due_val.setText(f"{pay_t:,.2f}")
        self._total_pay_val.setText(f"{total:,.2f}")

        n = sum(1 for r in range(self._grid.rowCount())
                if not self._grid.isRowHidden(r))
        self._status.setText(
            f"{n:,} truck{'s' if n != 1 else ''}  ·  "
            f"Tahmeed: {_af_fmt(tt)}  ·  "
            f"Invoice: {_af_fmt(ti)}  ·  "
            f"Variance: {_af_fmt(tt - ti)}"
        )

    def _recalculate(self) -> None:
        self._grid._recalc_all()
        self._on_data_changed()

    def _do_import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Afritrack Schedule", "",
            "Excel Files (*.xlsx *.xls);;All Files (*)",
        )
        if not path:
            return
        try:
            rows, inst_t, inst_i, bal_mar = _read_afritrack_file(path)
        except Exception as exc:
            QMessageBox.critical(self, "Import Error", str(exc))
            return
        self._grid.setRowCount(0)
        for rd in rows:
            self._grid.add_row(rd)
        if inst_t or inst_i:
            self._inst_t.setText(f"{inst_t:.2f}")
            self._inst_i.setText(f"{inst_i:.2f}")
        if bal_mar:
            self._bal_mar.setText(f"{bal_mar:.2f}")
        self._on_data_changed()
        QMessageBox.information(
            self, "Import Complete",
            f"Loaded {self._grid.rowCount():,} truck rows.",
        )

    def _do_export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Afritrack Schedule",
            f"Afritrack_{self._period.replace(' ', '_')}.xlsx",
            "Excel Files (*.xlsx)",
        )
        if not path:
            return
        try:
            it = _af_flt(self._inst_t.text())
            ii = _af_flt(self._inst_i.text())
            bm = _af_flt(self._bal_mar.text())
            vr = float(self._vat_edit.text() or "15")
            _export_afritrack_xlsx(path, self._grid, it, ii, bm, vr, self._period)
            QMessageBox.information(self, "Export Complete", f"Saved:\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "Export Error", str(exc))


# ═══════════════════════════════════════════════════════════════════════════════
#  Insurance helpers — QB-style table, file readers, import dialog, exporters
# ═══════════════════════════════════════════════════════════════════════════════

_INS_MONTHS = [
    "All Months", "JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY",
    "JUNE", "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER",
]


def _ins_table_style() -> str:
    return (
        f"QTableWidget{{background:{_WHITE};gridline-color:{_BORDER};"
        "border:none;font-size:12px;font-family:'Segoe UI',sans-serif;}}"
        f"QTableWidget::item{{padding:0 6px;color:{_T1};}}"
        f"QTableWidget::item:selected{{background:{_QB_SEL_BG};color:{_QB_SEL_FG};}}"
        f"QHeaderView::section{{background:{_QB_HDR_BG};color:{_QB_HDR_FG};"
        "font-size:11px;font-weight:700;font-family:'Segoe UI',sans-serif;"
        f"border:none;border-bottom:2px solid #BFDBFE;"
        "padding:0 8px;height:32px;}}"
        "QScrollBar:vertical{width:8px;background:transparent;}"
        f"QScrollBar::handle:vertical{{background:#D1D5DB;border-radius:4px;}}"
        f"QTableWidget{{alternate-background-color:{_ALT_ROW};}}"
    )


def _ins_make_table(headers: List[str]) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setAlternatingRowColors(True)
    t.verticalHeader().setVisible(False)
    t.verticalHeader().setDefaultSectionSize(_ROW_H)
    t.horizontalHeader().setStretchLastSection(True)
    t.setStyleSheet(_ins_table_style())
    return t


class _InsTotalsBar(QFrame):
    """Flexible totals footer bar for insurance widgets."""

    def __init__(
        self,
        labels: List[Tuple[str, str, str]],   # (key, display_label, num_prefix)
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setFixedHeight(38)
        self.setStyleSheet(
            f"QFrame{{background:{_QB_HDR_BG};border-top:2px solid #BFDBFE;"
            "border-bottom-left-radius:6px;border-bottom-right-radius:6px;}}"
        )
        hl = QHBoxLayout(self)
        hl.setContentsMargins(16, 0, 16, 0)
        hl.setSpacing(32)

        self._map: Dict[str, Tuple[QLabel, str]] = {}
        for key, display_label, num_prefix in labels:
            sub = QWidget()
            sub.setStyleSheet("background:transparent;")
            sub_hl = QHBoxLayout(sub)
            sub_hl.setContentsMargins(0, 0, 0, 0)
            sub_hl.setSpacing(4)
            sub_hl.addWidget(_lbl(display_label + ":", size=11, weight=600,
                                   color=_QB_HDR_FG))
            val = _lbl("—", size=12, weight=700, color=_QB_BOLD_FG)
            sub_hl.addWidget(val)
            self._map[key] = (val, num_prefix)
            hl.addWidget(sub)
        hl.addStretch()

    def set_value(self, key: str, value: float, decimals: int = 0) -> None:
        if key in self._map:
            lbl, prefix = self._map[key]
            lbl.setText(_fmt_num(value, prefix=prefix, decimals=decimals))

    def set_text(self, key: str, text: str) -> None:
        if key in self._map:
            self._map[key][0].setText(text)


# ── COMESA file reader ─────────────────────────────────────────────────────────

def _read_comesa_rows(path: str) -> List[dict]:
    if not _HAS_OPENPYXL:
        raise RuntimeError("openpyxl is required to import COMESA files.")
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = next(
        (wb[n] for n in wb.sheetnames if "comesa" in n.lower()),
        wb.active,
    )
    records: List[dict] = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or row[0] is None:
            continue
        try:
            int(row[0])
        except (TypeError, ValueError):
            continue

        card_no = str(row[2] or "").strip() if len(row) > 2 else ""
        if not card_no:
            continue

        name      = str(row[1] or "").strip() if len(row) > 1 else ""
        vf        = _fmt_date(row[3]) if (len(row) > 3 and row[3]) else ""
        vt        = _fmt_date(row[4]) if (len(row) > 4 and row[4]) else ""
        truck_reg = str(row[5] or "").strip() if len(row) > 5 else ""
        raw_p     = row[6] if len(row) > 6 else None
        try:
            premium = float(raw_p) if raw_p is not None else 0.0
        except (TypeError, ValueError):
            premium = 0.0
        month = str(row[7] or "").strip().upper() if len(row) > 7 else ""

        records.append({
            "feed_type":  "comesa",
            "name":       name,
            "card_no":    card_no,
            "valid_from": vf,
            "valid_to":   vt,
            "truck_reg":  truck_reg,
            "premium":    premium,
            "month":      month,
            "dedup_id":   card_no,
        })
    return records


# ── Third Party file reader ────────────────────────────────────────────────────

def _read_third_party_rows(path: str) -> List[dict]:
    if not _HAS_OPENPYXL:
        raise RuntimeError("openpyxl is required to import Third Party files.")
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = next(
        (wb[n] for n in wb.sheetnames
         if "third" in n.lower() or "party" in n.lower()),
        wb.active,
    )
    records: List[dict] = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row:
            continue

        # ── PAID block: cols A(0)–H(7) ──────────────────────────────────────
        if row[0] is not None:
            try:
                int(row[0])
            except (TypeError, ValueError):
                pass
            else:
                reg_no = str(row[3] or row[2] or "").strip() if len(row) > 3 else ""
                name   = str(row[1] or "").strip() if len(row) > 1 else ""
                raw_p  = row[4] if len(row) > 4 else None
                month  = str(row[7] or "").strip().upper() if len(row) > 7 else ""
                if reg_no:
                    try:
                        prem = float(raw_p) if raw_p is not None else 0.0
                    except (TypeError, ValueError):
                        prem = 0.0
                    vat   = round(prem * 0.18, 2)
                    total = round(prem + vat, 2)
                    records.append({
                        "feed_type":     "third_party",
                        "name":          name,
                        "reg_no":        reg_no,
                        "premium":       prem,
                        "vat":           vat,
                        "total_premium": total,
                        "month":         month,
                        "status":        "PAID",
                        "dedup_id":      f"{reg_no}|{month}|PAID",
                    })

        # ── UNPAID block: cols L(11)–Q(16) ──────────────────────────────────
        if len(row) > 11 and row[11] is not None:
            name_u  = str(row[11] or "").strip()
            reg_u   = str(row[12] or "").strip() if len(row) > 12 else ""
            raw_pu  = row[13] if len(row) > 13 else None
            month_u = str(row[16] or "").strip().upper() if len(row) > 16 else ""
            if reg_u:
                try:
                    prem_u = float(raw_pu) if raw_pu is not None else 0.0
                except (TypeError, ValueError):
                    prem_u = 0.0
                vat_u   = round(prem_u * 0.18, 2)
                total_u = round(prem_u + vat_u, 2)
                records.append({
                    "feed_type":     "third_party",
                    "name":          name_u,
                    "reg_no":        reg_u,
                    "premium":       prem_u,
                    "vat":           vat_u,
                    "total_premium": total_u,
                    "month":         month_u,
                    "status":        "UNPAID",
                    "dedup_id":      f"{reg_u}|{month_u}|UNPAID",
                })
    return records


# ── Shared insurance import dialog ────────────────────────────────────────────

class _InsuranceImportDialog(QDialog):
    imported = Signal(int)

    def __init__(
        self,
        feed_type: str,
        reader_fn,
        preview_headers: List[str],
        preview_keys: List[str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._feed_type      = feed_type
        self._reader_fn      = reader_fn
        self._preview_hdrs   = preview_headers
        self._preview_keys   = preview_keys
        self._new_rows: List[dict] = []
        self.setWindowTitle(f"Import — {feed_type.replace('_', ' ').title()}")
        self.setMinimumWidth(760)
        self.setStyleSheet(f"background:{_WHITE};")
        self._build()

    def _build(self) -> None:
        vl = QVBoxLayout(self)
        vl.setSpacing(12)
        vl.setContentsMargins(20, 20, 20, 20)

        self._drop = _DropZone()
        self._drop.file_dropped.connect(self._on_file)
        vl.addWidget(self._drop)

        browse_row = QWidget()
        browse_row.setStyleSheet("background:transparent;")
        brl = QHBoxLayout(browse_row)
        brl.setContentsMargins(0, 0, 0, 0)
        brl.setSpacing(8)
        browse_btn = _btn("Browse File", "mdi.folder-open-outline", primary=False)
        browse_btn.clicked.connect(self._browse)
        brl.addStretch()
        brl.addWidget(browse_btn)
        vl.addWidget(browse_row)

        self._stats_lbl = _lbl("No file loaded.", size=12, color=_T2)
        vl.addWidget(self._stats_lbl)

        vl.addWidget(_hsep())
        vl.addWidget(_lbl("Preview (first 10 rows)", size=12, weight=600))

        self._preview_tbl = _ins_make_table(self._preview_hdrs)
        self._preview_tbl.setMinimumHeight(200)
        self._preview_tbl.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents
        )
        vl.addWidget(self._preview_tbl)

        vl.addWidget(_hsep())

        btn_row = QWidget()
        btn_row.setStyleSheet("background:transparent;")
        bbl = QHBoxLayout(btn_row)
        bbl.setContentsMargins(0, 0, 0, 0)
        bbl.addStretch()
        cancel_btn = _btn("Cancel", primary=False)
        cancel_btn.clicked.connect(self.reject)
        bbl.addWidget(cancel_btn)
        self._import_btn = _btn("Import Records", "mdi.check-circle-outline")
        self._import_btn.setEnabled(False)
        self._import_btn.clicked.connect(self._do_import)
        bbl.addWidget(self._import_btn)
        vl.addWidget(btn_row)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open File", "", "Excel (*.xlsx *.xls)"
        )
        if path:
            self._drop.set_path(path)
            self._on_file(path)

    def _on_file(self, path: str) -> None:
        self._stats_lbl.setText("Reading file…")
        try:
            records = self._reader_fn(path)
        except Exception as exc:
            self._stats_lbl.setText(f"Error reading file: {exc}")
            return
        keys = [r["dedup_id"] for r in records if r.get("dedup_id")]
        asyncio.ensure_future(self._check_dupes(records, keys))

    async def _check_dupes(self, records: List[dict], keys: List[str]) -> None:
        from tahmeed.services import accountant_service as svc
        try:
            existing = await svc.get_existing_insurance_keys(self._feed_type, keys)
        except Exception:
            existing = set()
        self._new_rows = [r for r in records if r.get("dedup_id") not in existing]
        dupes = len(records) - len(self._new_rows)
        self._stats_lbl.setText(
            f"New records: {len(self._new_rows):,}  ·  Duplicates (skipped): {dupes:,}"
        )
        self._import_btn.setEnabled(bool(self._new_rows))
        self._import_btn.setText(f"Import {len(self._new_rows):,} Records")
        self._fill_preview(self._new_rows[:10])

    def _fill_preview(self, rows: List[dict]) -> None:
        t = self._preview_tbl
        t.setRowCount(0)
        for row in rows:
            r = t.rowCount()
            t.insertRow(r)
            for c, key in enumerate(self._preview_keys):
                if c >= t.columnCount():
                    break
                val = row.get(key, "")
                if isinstance(val, float):
                    val = f"{val:,.0f}" if val else ""
                t.setItem(r, c, _cell(str(val)))

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


# ── QB-style info card shared by both insurance widgets ───────────────────────

def _ins_info_card(title: str, icon_name: str) -> Tuple[QFrame, QLabel]:
    card = QFrame()
    card.setObjectName("ins_info_card")
    card.setStyleSheet(
        f"QFrame#ins_info_card{{background:{_QB_HDR_BG};"
        "border:1px solid #BFDBFE;border-radius:8px;}}"
    )
    hl = QHBoxLayout(card)
    hl.setContentsMargins(20, 14, 20, 14)
    hl.setSpacing(14)

    try:
        icon_lbl = QLabel()
        icon_lbl.setPixmap(qta.icon(icon_name, color=_QB_HDR_FG).pixmap(32, 32))
        icon_lbl.setFixedSize(32, 32)
        icon_lbl.setStyleSheet("background:transparent;")
        hl.addWidget(icon_lbl)
    except Exception:
        pass

    vl2 = QVBoxLayout()
    vl2.setSpacing(2)
    t_lbl = QLabel(title)
    t_lbl.setStyleSheet(
        f"color:{_QB_HDR_FG};font-size:18px;font-weight:700;"
        "font-family:'Segoe UI',sans-serif;background:transparent;"
    )
    vl2.addWidget(t_lbl)
    s_lbl = _lbl("Import records to get started", size=11, color=_T2)
    vl2.addWidget(s_lbl)
    hl.addLayout(vl2)
    hl.addStretch()
    return card, s_lbl


# ── COMESA Excel exporter ──────────────────────────────────────────────────────

def _export_comesa_xlsx(path: str, recs: List[dict]) -> None:
    if not _HAS_OPENPYXL:
        raise RuntimeError("openpyxl is required for Excel export.")
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    wb = Workbook()
    ws = wb.active
    ws.title = "COMESA Covers"

    thin     = Side(border_style="thin", color="E5E7EB")
    bdr      = Border(left=thin, right=thin, top=thin, bottom=thin)
    hdr_fill = PatternFill("solid", fgColor="EFF6FF")
    hdr_font = Font(name="Calibri", bold=True, size=11, color="1E3A5F")
    alt_fill = PatternFill("solid", fgColor="F9FAFB")
    nrm_font = Font(name="Calibri", size=11)

    col_widths = [6, 30, 14, 14, 16, 16, 14, 12]
    for c, w in enumerate(col_widths, 1):
        ws.column_dimensions[ws.cell(1, c).column_letter].width = w

    ws.row_dimensions[1].height = 30
    hdrs = ["S/NO", "NAME", "CARD NO.", "VALID FROM", "VALID TO",
            "TRUCK REG", "PREMIUM", "MONTH"]
    for c, label in enumerate(hdrs, 1):
        cell = ws.cell(row=1, column=c, value=label)
        cell.font = hdr_font
        cell.fill = hdr_fill
        cell.border = bdr
        cell.alignment = Alignment(horizontal="center", vertical="center")

    for ri, rec in enumerate(recs, 2):
        ws.row_dimensions[ri].height = 18
        data = [
            ri - 1,
            rec.get("name", ""),
            rec.get("card_no", ""),
            rec.get("valid_from", ""),
            rec.get("valid_to", ""),
            rec.get("truck_reg", ""),
            rec.get("premium") or None,
            rec.get("month", ""),
        ]
        aligns = ["center", "left", "center", "center", "center",
                  "center", "right", "center"]
        for ci, (val, aln) in enumerate(zip(data, aligns), 1):
            cell = ws.cell(row=ri, column=ci, value=val)
            cell.font = nrm_font
            cell.border = bdr
            cell.alignment = Alignment(horizontal=aln, vertical="center")
            if ri % 2 == 0:
                cell.fill = alt_fill

    wb.save(path)


# ── Third Party Excel exporter ────────────────────────────────────────────────

def _export_third_party_xlsx(path: str, recs: List[dict]) -> None:
    if not _HAS_OPENPYXL:
        raise RuntimeError("openpyxl is required for Excel export.")
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    wb = Workbook()
    ws = wb.active
    ws.title = "Third Party Covers"

    thin      = Side(border_style="thin", color="E5E7EB")
    bdr       = Border(left=thin, right=thin, top=thin, bottom=thin)
    hdr_fill  = PatternFill("solid", fgColor="EFF6FF")
    hdr_font  = Font(name="Calibri", bold=True, size=11, color="1E3A5F")
    alt_fill  = PatternFill("solid", fgColor="F9FAFB")
    paid_fill = PatternFill("solid", fgColor="DCFCE7")
    upd_fill  = PatternFill("solid", fgColor="FEF3C7")
    nrm_font  = Font(name="Calibri", size=11)

    col_widths = [6, 30, 16, 14, 14, 16, 12, 10]
    for c, w in enumerate(col_widths, 1):
        ws.column_dimensions[ws.cell(1, c).column_letter].width = w

    ws.row_dimensions[1].height = 30
    hdrs = ["S/NO", "NAME", "REG. NO.", "PREMIUM", "VAT 18%",
            "TOTAL PREMIUM", "MONTH", "STATUS"]
    for c, label in enumerate(hdrs, 1):
        cell = ws.cell(row=1, column=c, value=label)
        cell.font = hdr_font
        cell.fill = hdr_fill
        cell.border = bdr
        cell.alignment = Alignment(horizontal="center", vertical="center")

    for ri, rec in enumerate(recs, 2):
        ws.row_dimensions[ri].height = 18
        status = rec.get("status", "")
        row_fill = (
            paid_fill if status == "PAID" else
            upd_fill  if status == "UNPAID" else
            (alt_fill if ri % 2 == 0 else None)
        )
        data = [
            ri - 1,
            rec.get("name", ""),
            rec.get("reg_no", ""),
            rec.get("premium") or None,
            rec.get("vat") or None,
            rec.get("total_premium") or None,
            rec.get("month", ""),
            status,
        ]
        aligns = ["center", "left", "center", "right", "right",
                  "right", "center", "center"]
        for ci, (val, aln) in enumerate(zip(data, aligns), 1):
            cell = ws.cell(row=ri, column=ci, value=val)
            cell.font = nrm_font
            cell.border = bdr
            cell.alignment = Alignment(horizontal=aln, vertical="center")
            if row_fill:
                cell.fill = row_fill

    wb.save(path)


# ═══════════════════════════════════════════════════════════════════════════════
#  COMESA Covers
# ═══════════════════════════════════════════════════════════════════════════════

_COMESA_HEADERS         = ["S/NO", "NAME", "CARD NO.", "VALID FROM",
                            "VALID TO", "TRUCK REG", "PREMIUM", "MONTH"]
_COMESA_PREVIEW_HEADERS = ["NAME", "CARD NO.", "VALID FROM", "VALID TO",
                            "TRUCK REG", "PREMIUM", "MONTH"]
_COMESA_PREVIEW_KEYS    = ["name", "card_no", "valid_from", "valid_to",
                            "truck_reg", "premium", "month"]


class ComesaWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._page      = 1
        self._page_size = 50
        self._total     = 0
        self._search    = ""
        self._month     = ""
        self._build()
        self.refresh()

    def _build(self) -> None:
        self.setStyleSheet(f"background:{_BG};")
        vl = QVBoxLayout(self)
        vl.setContentsMargins(20, 16, 20, 12)
        vl.setSpacing(8)

        # QB info card
        self._info_card, self._summary_lbl = _ins_info_card(
            "COMESA Covers", "mdi.certificate"
        )
        vl.addWidget(self._info_card)

        # Toolbar
        tb  = QWidget()
        tb.setStyleSheet("background:transparent;")
        tbl = QHBoxLayout(tb)
        tbl.setContentsMargins(0, 0, 0, 0)
        tbl.setSpacing(8)

        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Search card no., truck, name…")
        self._search_edit.setFixedWidth(250)
        self._search_edit.setStyleSheet(_input_ss())
        self._search_edit.textChanged.connect(self._on_search)
        tbl.addWidget(self._search_edit)

        self._month_cb = QComboBox()
        for m in _INS_MONTHS:
            self._month_cb.addItem(m, "" if m == "All Months" else m)
        self._month_cb.setFixedWidth(155)
        self._month_cb.setStyleSheet(_input_ss())
        self._month_cb.currentIndexChanged.connect(self._on_month)
        tbl.addWidget(self._month_cb)
        tbl.addStretch()

        self._import_btn = _btn("Import", "mdi.upload-outline", height=32)
        self._export_btn = _btn("Export", "mdi.download-outline",
                                primary=False, height=32)
        self._import_btn.clicked.connect(self._do_import)
        self._export_btn.clicked.connect(self._do_export)
        tbl.addWidget(self._import_btn)
        tbl.addWidget(self._export_btn)
        vl.addWidget(tb)

        # Table card
        card = QFrame()
        card.setStyleSheet(
            f"QFrame{{background:{_WHITE};border:1px solid {_BORDER};"
            "border-radius:6px;}}"
        )
        cl = QVBoxLayout(card)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)

        self._table = _ins_make_table(_COMESA_HEADERS)
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents
        )
        self._table.horizontalHeader().setStretchLastSection(True)
        cl.addWidget(self._table, 1)

        self._totals_bar = _InsTotalsBar([
            ("premium", "TOTAL PREMIUM", "TSh "),
            ("records", "RECORDS", ""),
        ])
        cl.addWidget(self._totals_bar)
        vl.addWidget(card, 1)

        self._pager = _PaginationBar()
        self._pager.page_changed.connect(self._go_page)
        self._pager.size_changed.connect(self._on_page_size)
        vl.addWidget(self._pager)

    def refresh(self) -> None:
        asyncio.ensure_future(self._load())

    async def _load(self) -> None:
        from tahmeed.services import accountant_service as svc
        skip = (self._page - 1) * self._page_size
        recs, total, totals = await asyncio.gather(
            svc.get_insurance_feed(
                "comesa", self._search, self._month, "",
                self._page_size, skip,
            ),
            svc.count_insurance_feed("comesa", self._search, self._month),
            svc.get_insurance_totals("comesa", self._month),
        )
        self._total = total
        self._fill_table(recs)
        self._pager.set_total(total, self._page_size, self._page)

        prem = totals.get("premium", 0.0)
        self._totals_bar.set_value("premium", prem)
        self._totals_bar.set_text("records", f"{total:,}")
        self._summary_lbl.setText(
            f"{total:,} records  ·  Total Premium: TSh {prem:,.0f}"
        )

    def _fill_table(self, recs: List[dict]) -> None:
        t   = self._table
        off = (self._page - 1) * self._page_size
        t.setRowCount(0)
        for idx, rec in enumerate(recs, off + 1):
            r = t.rowCount()
            t.insertRow(r)
            t.setItem(r, 0, _cell(str(idx),
                                   Qt.AlignCenter | Qt.AlignVCenter))
            t.setItem(r, 1, _cell(rec.get("name", "")))
            t.setItem(r, 2, _cell(rec.get("card_no", ""), mono=True))
            t.setItem(r, 3, _cell(rec.get("valid_from", "")))
            t.setItem(r, 4, _cell(rec.get("valid_to", "")))
            t.setItem(r, 5, _cell(rec.get("truck_reg", ""), mono=True))
            prem = rec.get("premium", 0.0)
            t.setItem(r, 6, _cell(
                _fmt_num(prem, decimals=0) if prem else "—",
                Qt.AlignRight | Qt.AlignVCenter, mono=True,
            ))
            t.setItem(r, 7, _cell(rec.get("month", ""),
                                   Qt.AlignCenter | Qt.AlignVCenter))

    def _do_import(self) -> None:
        dlg = _InsuranceImportDialog(
            "comesa", _read_comesa_rows,
            _COMESA_PREVIEW_HEADERS, _COMESA_PREVIEW_KEYS,
            parent=self,
        )
        dlg.imported.connect(lambda n: (
            QMessageBox.information(
                self, "Import Complete", f"Imported {n:,} new records."
            ),
            self.refresh(),
        ))
        dlg.exec()

    def _do_export(self) -> None:
        if self._total == 0:
            QMessageBox.information(self, "Export", "No records to export.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export COMESA Covers", "COMESA_Covers.xlsx",
            "Excel (*.xlsx)",
        )
        if path:
            asyncio.ensure_future(self._async_export(path))

    async def _async_export(self, path: str) -> None:
        from tahmeed.services import accountant_service as svc
        try:
            recs = await svc.get_insurance_feed(
                "comesa", self._search, self._month, "", 10000, 0
            )
            _export_comesa_xlsx(path, recs)
            QMessageBox.information(self, "Export Complete", f"Saved:\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "Export Error", str(exc))

    def _on_search(self, text: str) -> None:
        self._search = text
        self._page   = 1
        asyncio.ensure_future(self._load())

    def _on_month(self) -> None:
        self._month = self._month_cb.currentData() or ""
        self._page  = 1
        asyncio.ensure_future(self._load())

    def _go_page(self, page: int) -> None:
        self._page = page
        asyncio.ensure_future(self._load())

    def _on_page_size(self, size: int) -> None:
        self._page_size = size
        self._page      = 1
        asyncio.ensure_future(self._load())


# ═══════════════════════════════════════════════════════════════════════════════
#  Third Party Covers
# ═══════════════════════════════════════════════════════════════════════════════

_TP_HEADERS         = ["S/NO", "NAME", "REG. NO.", "PREMIUM",
                        "VAT 18%", "TOTAL PREMIUM", "MONTH", "STATUS"]
_TP_PREVIEW_HEADERS = ["NAME", "REG. NO.", "PREMIUM", "VAT 18%",
                        "TOTAL PREMIUM", "MONTH", "STATUS"]
_TP_PREVIEW_KEYS    = ["name", "reg_no", "premium", "vat",
                        "total_premium", "month", "status"]

_STATUS_COLORS = {
    "PAID":   (_GREEN, _GREEN_L),
    "UNPAID": (_AMBER, _AMBER_L),
}


class ThirdPartyWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._page      = 1
        self._page_size = 50
        self._total     = 0
        self._search    = ""
        self._month     = ""
        self._status    = ""
        self._build()
        self.refresh()

    def _build(self) -> None:
        self.setStyleSheet(f"background:{_BG};")
        vl = QVBoxLayout(self)
        vl.setContentsMargins(20, 16, 20, 12)
        vl.setSpacing(8)

        # QB info card
        self._info_card, self._summary_lbl = _ins_info_card(
            "Third Party Covers", "mdi.shield-account"
        )
        vl.addWidget(self._info_card)

        # Toolbar
        tb  = QWidget()
        tb.setStyleSheet("background:transparent;")
        tbl = QHBoxLayout(tb)
        tbl.setContentsMargins(0, 0, 0, 0)
        tbl.setSpacing(8)

        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Search reg. no., name…")
        self._search_edit.setFixedWidth(220)
        self._search_edit.setStyleSheet(_input_ss())
        self._search_edit.textChanged.connect(self._on_search)
        tbl.addWidget(self._search_edit)

        self._month_cb = QComboBox()
        for m in _INS_MONTHS:
            self._month_cb.addItem(m, "" if m == "All Months" else m)
        self._month_cb.setFixedWidth(155)
        self._month_cb.setStyleSheet(_input_ss())
        self._month_cb.currentIndexChanged.connect(self._on_month)
        tbl.addWidget(self._month_cb)

        self._status_cb = QComboBox()
        for label, val in [("All Status", ""), ("PAID", "PAID"),
                            ("UNPAID", "UNPAID")]:
            self._status_cb.addItem(label, val)
        self._status_cb.setFixedWidth(120)
        self._status_cb.setStyleSheet(_input_ss())
        self._status_cb.currentIndexChanged.connect(self._on_status)
        tbl.addWidget(self._status_cb)
        tbl.addStretch()

        self._import_btn = _btn("Import", "mdi.upload-outline", height=32)
        self._export_btn = _btn("Export", "mdi.download-outline",
                                primary=False, height=32)
        self._import_btn.clicked.connect(self._do_import)
        self._export_btn.clicked.connect(self._do_export)
        tbl.addWidget(self._import_btn)
        tbl.addWidget(self._export_btn)
        vl.addWidget(tb)

        # Table card
        card = QFrame()
        card.setStyleSheet(
            f"QFrame{{background:{_WHITE};border:1px solid {_BORDER};"
            "border-radius:6px;}}"
        )
        cl = QVBoxLayout(card)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)

        self._table = _ins_make_table(_TP_HEADERS)
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents
        )
        self._table.horizontalHeader().setStretchLastSection(True)
        cl.addWidget(self._table, 1)

        self._totals_bar = _InsTotalsBar([
            ("premium", "PREMIUM",      "TSh "),
            ("vat",     "VAT 18%",      "TSh "),
            ("total",   "TOTAL",        "TSh "),
            ("records", "RECORDS",      ""),
        ])
        cl.addWidget(self._totals_bar)
        vl.addWidget(card, 1)

        self._pager = _PaginationBar()
        self._pager.page_changed.connect(self._go_page)
        self._pager.size_changed.connect(self._on_page_size)
        vl.addWidget(self._pager)

    def refresh(self) -> None:
        asyncio.ensure_future(self._load())

    async def _load(self) -> None:
        from tahmeed.services import accountant_service as svc
        skip = (self._page - 1) * self._page_size
        recs, total, totals = await asyncio.gather(
            svc.get_insurance_feed(
                "third_party", self._search, self._month, self._status,
                self._page_size, skip,
            ),
            svc.count_insurance_feed(
                "third_party", self._search, self._month, self._status
            ),
            svc.get_insurance_totals(
                "third_party", self._month, self._status
            ),
        )
        self._total = total
        self._fill_table(recs)
        self._pager.set_total(total, self._page_size, self._page)

        prem = totals.get("premium", 0.0)
        vat  = totals.get("vat",     0.0)
        tot  = totals.get("total_premium", 0.0)
        self._totals_bar.set_value("premium", prem)
        self._totals_bar.set_value("vat",     vat)
        self._totals_bar.set_value("total",   tot)
        self._totals_bar.set_text("records",  f"{total:,}")
        self._summary_lbl.setText(
            f"{total:,} records  ·  Premium: TSh {prem:,.0f}"
            f"  ·  Total inc. VAT: TSh {tot:,.0f}"
        )

    def _fill_table(self, recs: List[dict]) -> None:
        t   = self._table
        off = (self._page - 1) * self._page_size
        t.setRowCount(0)
        for idx, rec in enumerate(recs, off + 1):
            r = t.rowCount()
            t.insertRow(r)
            t.setItem(r, 0, _cell(str(idx),
                                   Qt.AlignCenter | Qt.AlignVCenter))
            t.setItem(r, 1, _cell(rec.get("name", "")))
            t.setItem(r, 2, _cell(rec.get("reg_no", ""), mono=True))
            prem = rec.get("premium", 0.0)
            vat  = rec.get("vat",     0.0)
            tot  = rec.get("total_premium", 0.0)
            t.setItem(r, 3, _cell(
                _fmt_num(prem, decimals=0) if prem else "—",
                Qt.AlignRight | Qt.AlignVCenter, mono=True,
            ))
            t.setItem(r, 4, _cell(
                _fmt_num(vat, decimals=0) if vat else "—",
                Qt.AlignRight | Qt.AlignVCenter, mono=True,
            ))
            t.setItem(r, 5, _cell(
                _fmt_num(tot, decimals=0) if tot else "—",
                Qt.AlignRight | Qt.AlignVCenter, mono=True,
            ))
            t.setItem(r, 6, _cell(rec.get("month", ""),
                                   Qt.AlignCenter | Qt.AlignVCenter))

            status = rec.get("status", "")
            s_item = _cell(status, Qt.AlignCenter | Qt.AlignVCenter)
            if status in _STATUS_COLORS:
                fg, bg = _STATUS_COLORS[status]
                s_item.setForeground(QColor(fg))
                s_item.setBackground(QColor(bg))
            t.setItem(r, 7, s_item)

    def _do_import(self) -> None:
        dlg = _InsuranceImportDialog(
            "third_party", _read_third_party_rows,
            _TP_PREVIEW_HEADERS, _TP_PREVIEW_KEYS,
            parent=self,
        )
        dlg.imported.connect(lambda n: (
            QMessageBox.information(
                self, "Import Complete", f"Imported {n:,} new records."
            ),
            self.refresh(),
        ))
        dlg.exec()

    def _do_export(self) -> None:
        if self._total == 0:
            QMessageBox.information(self, "Export", "No records to export.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Third Party Covers", "ThirdParty_Covers.xlsx",
            "Excel (*.xlsx)",
        )
        if path:
            asyncio.ensure_future(self._async_export(path))

    async def _async_export(self, path: str) -> None:
        from tahmeed.services import accountant_service as svc
        try:
            recs = await svc.get_insurance_feed(
                "third_party", self._search, self._month, self._status,
                10000, 0,
            )
            _export_third_party_xlsx(path, recs)
            QMessageBox.information(self, "Export Complete", f"Saved:\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "Export Error", str(exc))

    def _on_search(self, text: str) -> None:
        self._search = text
        self._page   = 1
        asyncio.ensure_future(self._load())

    def _on_month(self) -> None:
        self._month = self._month_cb.currentData() or ""
        self._page  = 1
        asyncio.ensure_future(self._load())

    def _on_status(self) -> None:
        self._status = self._status_cb.currentData() or ""
        self._page   = 1
        asyncio.ensure_future(self._load())

    def _go_page(self, page: int) -> None:
        self._page = page
        asyncio.ensure_future(self._load())

    def _on_page_size(self, size: int) -> None:
        self._page_size = size
        self._page      = 1
        asyncio.ensure_future(self._load())


# ═══════════════════════════════════════════════════════════════════════════════
#  RahnTech — Transacted Devices import
# ═══════════════════════════════════════════════════════════════════════════════

_RAHNTECH_HEADERS = [
    "S/N", "SALES DATE", "TRIP NUMBER", "DEVICE NUMBER",
    "TRUCK NUMBER", "DRIVER NAME", "DO",
]
_RAHNTECH_COL_MAP = {
    "sn":            ["s/n", "sn", "#"],
    "sales_date":    ["sales date", "date", "transaction date"],
    "trip_number":   ["trip number", "trip no", "trip"],
    "device_number": ["device number", "device no", "device"],
    "truck_number":  ["truck number", "truck no", "truck"],
    "driver_name":   ["driver name", "driver"],
    "do_number":     ["do", "do number", "delivery order"],
}


def _read_rahntech_rows(path: str) -> Tuple[List[str], List[List[Any]]]:
    """RahnTech xlsx: row 0 is a title banner; row 1 holds the real headers."""
    p = Path(path)
    if p.suffix.lower() in (".xlsx", ".xls") and _HAS_OPENPYXL:
        wb = openpyxl.load_workbook(path, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        if len(rows) < 2:
            return [], []
        headers = [str(c) if c is not None else "" for c in rows[1]]
        data = [
            [str(c) if c is not None else "" for c in r]
            for r in rows[2:]
            if any(c is not None for c in r)
        ]
        return headers, data
    return _read_file_rows(path)


class _RahnTechImportDialog(ImportDialog):
    """ImportDialog that uses the RahnTech-specific row reader (skips title row)."""

    def _on_file(self, path: str) -> None:
        self._stats_lbl.setText("Reading file…")
        try:
            headers, rows = _read_rahntech_rows(path)
        except Exception as exc:
            self._stats_lbl.setText(f"Error reading file: {exc}")
            return

        self._raw_headers = headers
        hdr_lower = {h.strip().lower(): i for i, h in enumerate(headers)}

        def _find(candidates: List[str]) -> Optional[int]:
            for c in candidates:
                idx = hdr_lower.get(c.lower())
                if idx is not None:
                    return idx
            return None

        field_idxs: Dict[str, Optional[int]] = {
            key: _find(cands) for key, cands in self._col_map.items()
        }

        records: List[dict] = []
        for row in rows:
            rec: dict = {"_raw": row, "feed_type": self._feed_type}
            for key, idx in field_idxs.items():
                rec[key] = str(row[idx]).strip() if (idx is not None and idx < len(row)) else ""
            records.append(rec)

        self._all_rows = records
        dedup_vals = [r.get(self._dedup_key, "") for r in records if r.get(self._dedup_key)]
        asyncio.ensure_future(self._check_dupes(records, dedup_vals))


class RahnTechWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._page = 1
        self._page_size = 25
        self._total = 0
        self._search = ""
        self._build()
        self.refresh()

    def _build(self) -> None:
        self.setStyleSheet(f"background:{_BG};")
        vl = QVBoxLayout(self)
        vl.setContentsMargins(20, 20, 20, 16)
        vl.setSpacing(12)

        header = _PageHeader("RahnTech", "mdi.devices")
        self._import_btn = _btn("Import Transacted Devices", "mdi.upload-outline")
        self._import_btn.clicked.connect(self._open_import)
        header.add_right(self._import_btn)
        vl.addWidget(header)
        vl.addWidget(_hsep())

        tb = QWidget()
        tb.setStyleSheet("background:transparent;")
        tbl = QHBoxLayout(tb)
        tbl.setContentsMargins(0, 0, 0, 0)
        tbl.setSpacing(8)

        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Search truck, driver, trip, device…")
        self._search_edit.setFixedWidth(280)
        self._search_edit.setStyleSheet(_input_ss())
        self._search_edit.textChanged.connect(self._on_search)
        tbl.addWidget(self._search_edit)
        tbl.addStretch()

        export_btn = _btn("Export", "mdi.download-outline", primary=False)
        tbl.addWidget(export_btn)
        vl.addWidget(tb)

        self._table = _make_table(_RAHNTECH_HEADERS)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setStretchLastSection(True)
        vl.addWidget(self._table, 1)

        self._totals = _TotalsBar([("count", "Records: ")])
        vl.addWidget(self._totals)

        self._pager = _PaginationBar()
        self._pager.page_changed.connect(self._go_page)
        self._pager.size_changed.connect(self._on_page_size)
        vl.addWidget(self._pager)

    def refresh(self) -> None:
        asyncio.ensure_future(self._load())

    async def _load(self) -> None:
        from tahmeed.services import accountant_service as svc
        skip = (self._page - 1) * self._page_size
        recs, total = await asyncio.gather(
            svc.get_imported_feed("rahntech", self._search, "", self._page_size, skip),
            svc.count_imported_feed("rahntech", self._search, ""),
        )
        self._fill_table(recs)
        self._total = total
        self._pager.set_total(total, self._page_size, self._page)
        self._totals.set_total("count", total, "")

    def _fill_table(self, recs: List[dict]) -> None:
        t = self._table
        t.setRowCount(0)
        for rec in recs:
            r = t.rowCount()
            t.insertRow(r)
            t.setItem(r, 0, _cell(rec.get("sn", "")))
            t.setItem(r, 1, _cell(rec.get("sales_date", "")))
            t.setItem(r, 2, _cell(rec.get("trip_number", ""), mono=True))
            t.setItem(r, 3, _cell(rec.get("device_number", ""), mono=True))
            t.setItem(r, 4, _cell(rec.get("truck_number", "")))
            t.setItem(r, 5, _cell(rec.get("driver_name", "")))
            t.setItem(r, 6, _cell(rec.get("do_number", "")))

    def _open_import(self) -> None:
        from tahmeed.services import accountant_service as svc
        dlg = _RahnTechImportDialog(
            feed_type="rahntech",
            dedup_key="trip_number",
            preview_headers=_RAHNTECH_HEADERS,
            col_map=_RAHNTECH_COL_MAP,
            save_fn=svc.save_imported_feed,
            exist_fn=svc.get_existing_feed_keys,
            parent=self,
        )
        dlg.imported.connect(lambda n: (
            QMessageBox.information(self, "Import Complete", f"Imported {n:,} new records."),
            self.refresh(),
        ))
        dlg.exec()

    def _on_search(self, text: str) -> None:
        self._search = text
        self._page = 1
        asyncio.ensure_future(self._load())

    def _go_page(self, page: int) -> None:
        self._page = page
        asyncio.ensure_future(self._load())

    def _on_page_size(self, size: int) -> None:
        self._page_size = size
        self._page = 1
        asyncio.ensure_future(self._load())
