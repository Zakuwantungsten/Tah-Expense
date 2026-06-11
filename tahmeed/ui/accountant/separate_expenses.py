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
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QFrame, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMessageBox, QPushButton, QSizePolicy,
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
            rec: dict = {"_raw": row}
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


class AfritrackWidget(_PlaceholderExpenseWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            "Afritrack",
            "mdi.satellite-variant",
            "Connect to the Afritrack tracking system to import trip data.",
            parent,
        )


class ThirdPartyWidget(_PlaceholderExpenseWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            "Third Party Covers",
            "mdi.shield-account",
            "Import third-party cover records when column schema is confirmed.",
            parent,
        )


class ComesaWidget(_PlaceholderExpenseWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            "COMESA Covers",
            "mdi.certificate",
            "Import COMESA cover records when column schema is confirmed.",
            parent,
        )
