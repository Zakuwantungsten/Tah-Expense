"""Fleet Registry — Trucks and Trailers management tables.

Two widgets (TrucksRegistryWidget / TrailersRegistryWidget) share a common
_FleetRegistryBase that provides search, table, add-dialog, and Excel import.
Data is stored in the `trucks` / `trailers` MongoDB collections.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable, Coroutine, List, Optional

import qtawesome as qta
from PySide6.QtCore import Qt, QSize, QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QFileDialog, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu, QMessageBox,
    QPushButton, QSizePolicy, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

# ── Design tokens (match dashboard palette) ────────────────────────────────────
_WHITE   = "#FFFFFF"
_BG      = "#F4F6F8"
_BORDER  = "#E5E7EB"
_BLUE    = "#0077C5"
_BLUE_L  = "#E8F4FD"
_GREEN   = "#16A34A"
_GREEN_L = "#DCFCE7"
_RED     = "#DC2626"
_RED_L   = "#FEE2E2"
_AMBER   = "#D97706"
_AMBER_L = "#FEF3C7"
_T1      = "#111827"
_T2      = "#6B7280"
_TM      = "#9CA3AF"
_HDR_BG  = "#F1F5F9"
_ALT_ROW = "#F9FAFB"
_NAVY    = "#1B2B4B"

_ROW_H = 36

# Default Excel path (pre-fills the file picker)
_DEFAULT_EXCEL = str(
    Path(__file__).parents[3] / "INSURANCE LIST.xlsx"
)


# ── Primitive helpers ──────────────────────────────────────────────────────────

def _lbl(text: str = "", size: int = 13, weight: int = 400,
         color: str = _T1) -> QLabel:
    w = QLabel(text)
    w.setStyleSheet(
        f"color:{color};font-size:{size}px;font-weight:{weight};"
        "font-family:'Segoe UI';background:transparent;"
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
              f"font-size:12px;font-weight:600;font-family:'Segoe UI';padding:0 14px;}}"
              f"QPushButton:hover{{background:#B91C1C;}}"
              f"QPushButton:disabled{{background:#FCA5A5;}}")
    elif primary:
        ss = (f"QPushButton{{background:{_BLUE};color:#FFF;border:none;border-radius:5px;"
              f"font-size:12px;font-weight:600;font-family:'Segoe UI';padding:0 14px;}}"
              f"QPushButton:hover{{background:#005EA3;}}"
              f"QPushButton:disabled{{background:#93C5FD;}}")
    else:
        ss = (f"QPushButton{{background:{_WHITE};color:{_T1};border:1px solid {_BORDER};"
              f"border-radius:5px;font-size:12px;font-family:'Segoe UI';padding:0 14px;}}"
              f"QPushButton:hover{{background:{_BG};}}"
              f"QPushButton:disabled{{color:{_TM};}}")
    b.setStyleSheet(ss)
    return b


def _table_style() -> str:
    return (
        f"QTableWidget{{background:{_WHITE};gridline-color:{_BORDER};"
        "border:none;font-size:12px;font-family:'Segoe UI';}}"
        f"QTableWidget::item{{padding:0 6px;color:{_T1};}}"
        f"QTableWidget::item:selected{{background:{_BLUE_L};color:{_T1};}}"
        f"QHeaderView::section{{background:{_HDR_BG};color:{_T2};"
        "font-size:11px;font-weight:600;font-family:'Segoe UI';"
        f"border:none;border-bottom:1px solid {_BORDER};"
        "padding:0 6px;height:30px;}}"
        "QScrollBar:vertical{width:8px;background:transparent;}"
        f"QScrollBar::handle:vertical{{background:#D1D5DB;border-radius:4px;}}"
        f"QTableWidget{{alternate-background-color:{_ALT_ROW};}}"
    )


def _cell(text: str, align: Qt.AlignmentFlag = Qt.AlignLeft | Qt.AlignVCenter,
          mono: bool = False) -> QTableWidgetItem:
    item = QTableWidgetItem(str(text) if text is not None else "—")
    item.setTextAlignment(align)
    if mono:
        item.setFont(QFont("Cascadia Code", 11))
    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    return item


def _status_chip(active: bool) -> QLabel:
    lbl = QLabel("Active" if active else "Inactive")
    lbl.setAlignment(Qt.AlignCenter)
    lbl.setFixedHeight(22)
    lbl.setMinimumWidth(60)
    if active:
        lbl.setStyleSheet(
            f"background:{_GREEN_L};color:{_GREEN};font-size:10px;font-weight:700;"
            "border-radius:11px;padding:0 8px;font-family:'Segoe UI';"
        )
    else:
        lbl.setStyleSheet(
            f"background:{_RED_L};color:{_RED};font-size:10px;font-weight:700;"
            "border-radius:11px;padding:0 8px;font-family:'Segoe UI';"
        )
    return lbl


# ── Add vehicle dialog ─────────────────────────────────────────────────────────

class _AddVehicleDialog(QDialog):
    def __init__(self, kind: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Add {kind}")
        self.setMinimumWidth(360)
        self.setStyleSheet("background:#FFFFFF;")
        self.result_number: Optional[str] = None

        vl = QVBoxLayout(self)
        vl.setContentsMargins(24, 24, 24, 24)
        vl.setSpacing(12)

        vl.addWidget(_lbl(f"Add {kind}", size=15, weight=700))

        vl.addWidget(_lbl("Registration Number *", size=12, color=_T2))
        self._inp = QLineEdit()
        self._inp.setPlaceholderText(f"e.g. T880 CUL")
        self._inp.setStyleSheet(
            f"QLineEdit{{border:1px solid {_BORDER};border-radius:5px;"
            f"background:{_WHITE};color:{_T1};font-size:13px;"
            "font-family:'Segoe UI';padding:0 8px;"
            "min-height:34px;max-height:34px;}}"
            f"QLineEdit:focus{{border-color:{_BLUE};}}"
        )
        self._inp.returnPressed.connect(self._accept)
        vl.addWidget(self._inp)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel = _btn("Cancel", primary=False, height=32)
        cancel.clicked.connect(self.reject)
        btn_row.addWidget(cancel)
        save = _btn("Add", primary=True, height=32)
        save.clicked.connect(self._accept)
        btn_row.addWidget(save)
        vl.addLayout(btn_row)

    def _accept(self) -> None:
        val = self._inp.text().strip().upper()
        if not val:
            QMessageBox.warning(self, "Validation", "Registration number is required.")
            return
        self.result_number = val
        self.accept()


# ── Base registry widget ───────────────────────────────────────────────────────

class _FleetRegistryBase(QWidget):
    """
    Shared base for Trucks and Trailers registry pages.
    Subclasses supply: _kind, _icon, _excel_sheet, _excel_section,
    _fn_get_all, _fn_add, _fn_remove, _fn_set_active, _fn_bulk_add.
    """

    _kind: str = "Vehicle"
    _icon: str = "mdi.truck"
    _excel_section: str = "TRUCKS"   # "TRUCKS" or "TRAILERS"

    # async callables — set by subclass
    _fn_get_all: Callable
    _fn_add: Callable
    _fn_remove: Callable
    _fn_set_active: Callable
    _fn_bulk_add: Callable

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._rows: List[dict] = []
        self._build()

    # ── Build ──────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(16)

        # ── Header ──────────────────────────────────────────────────────────
        hdr = QHBoxLayout()
        hdr.setSpacing(10)

        try:
            icon_lbl = QLabel()
            icon_lbl.setPixmap(qta.icon(self._icon, color=_BLUE).pixmap(24, 24))
            icon_lbl.setFixedSize(24, 24)
            icon_lbl.setStyleSheet("background:transparent;")
            hdr.addWidget(icon_lbl)
        except Exception:
            pass

        hdr.addWidget(_lbl(f"{self._kind} Registry", size=18, weight=700))

        self._count_chip = QLabel("—")
        self._count_chip.setStyleSheet(
            f"background:{_BLUE_L};color:{_BLUE};font-size:11px;font-weight:700;"
            "border-radius:10px;padding:2px 10px;"
            "font-family:'Segoe UI';"
        )
        hdr.addWidget(self._count_chip)
        hdr.addStretch()

        self._restrict_btn = QPushButton("  Restrict in cashier: Off")
        self._restrict_btn.setFixedHeight(32)
        self._restrict_btn.setCheckable(True)
        self._restrict_btn.setCursor(Qt.PointingHandCursor)
        self._restrict_btn.setToolTip(
            "When on, the cashier's Truck No. column only accepts numbers that\n"
            "exist in the truck/trailer registries. Applies to both registries."
        )
        try:
            self._restrict_btn.setIcon(qta.icon("mdi.lock-outline", color=_T2))
            self._restrict_btn.setIconSize(QSize(15, 15))
        except Exception:
            pass
        self._restrict_btn.setStyleSheet(
            f"QPushButton{{background:{_WHITE};color:{_T2};border:1px solid {_BORDER};"
            "border-radius:5px;font-size:12px;font-family:'Segoe UI';padding:0 12px;}}"
            f"QPushButton:checked{{background:{_GREEN_L};color:{_GREEN};border-color:{_GREEN};}}"
            f"QPushButton:hover:!checked{{background:{_BG};}}"
        )
        self._restrict_btn.toggled.connect(self._on_restrict_toggled)
        hdr.addWidget(self._restrict_btn)

        import_btn = _btn("Import from Excel", icon="mdi.microsoft-excel", primary=False, height=32)
        import_btn.clicked.connect(self._import_excel)
        hdr.addWidget(import_btn)

        add_btn = _btn(f"+ Add {self._kind}", primary=True, height=32)
        add_btn.clicked.connect(self._add_vehicle)
        hdr.addWidget(add_btn)

        root.addLayout(hdr)

        # ── Toolbar ─────────────────────────────────────────────────────────
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        self._search = QLineEdit()
        self._search.setPlaceholderText(f"Search by registration number…")
        self._search.setFixedHeight(32)
        self._search.setStyleSheet(
            f"QLineEdit{{border:1px solid {_BORDER};border-radius:5px;"
            f"background:{_WHITE};color:{_T1};font-size:12px;"
            "font-family:'Segoe UI';padding:0 8px;}}"
            f"QLineEdit:focus{{border-color:{_BLUE};}}"
        )
        self._search.textChanged.connect(self._on_search)
        toolbar.addWidget(self._search, 1)

        self._filter_cb = QComboBox()
        self._filter_cb.addItems(["All", "Active only", "Inactive only"])
        self._filter_cb.setFixedHeight(32)
        self._filter_cb.setStyleSheet(
            f"QComboBox{{border:1px solid {_BORDER};border-radius:5px;"
            f"background:{_WHITE};color:{_T1};font-size:12px;"
            "font-family:'Segoe UI';padding:0 8px;min-width:120px;}}"
            f"QComboBox:focus{{border-color:{_BLUE};}}"
            "QComboBox::drop-down{border:none;width:20px;}"
        )
        self._filter_cb.currentIndexChanged.connect(self._populate_table)
        toolbar.addWidget(self._filter_cb)

        root.addLayout(toolbar)

        # ── Table ────────────────────────────────────────────────────────────
        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["#", "Registration No.", "Status"])
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(_ROW_H)
        self._table.setStyleSheet(_table_style())
        self._table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._context_menu)

        hdr_view = self._table.horizontalHeader()
        hdr_view.setSectionResizeMode(0, QHeaderView.Fixed)
        self._table.setColumnWidth(0, 50)
        hdr_view.setSectionResizeMode(1, QHeaderView.Stretch)
        hdr_view.setSectionResizeMode(2, QHeaderView.Fixed)
        self._table.setColumnWidth(2, 100)

        root.addWidget(self._table, 1)

        # ── Footer ───────────────────────────────────────────────────────────
        self._footer = _lbl("", size=11, color=_TM)
        root.addWidget(self._footer)

    # ── Public API ─────────────────────────────────────────────────────────────

    def refresh(self) -> None:
        asyncio.ensure_future(self._load())
        asyncio.ensure_future(self._load_restrict_setting())

    # ── Restrict-in-cashier toggle ──────────────────────────────────────────────

    async def _load_restrict_setting(self) -> None:
        from tahmeed.services.settings_service import get_setting
        try:
            on = bool(await get_setting("restrict_trucks"))
        except Exception:
            on = False
        self._restrict_btn.blockSignals(True)
        self._restrict_btn.setChecked(on)
        self._restrict_btn.setText(
            "  Restrict in cashier: On" if on else "  Restrict in cashier: Off"
        )
        self._restrict_btn.blockSignals(False)

    def _on_restrict_toggled(self, on: bool) -> None:
        self._restrict_btn.setText(
            "  Restrict in cashier: On" if on else "  Restrict in cashier: Off"
        )
        asyncio.ensure_future(self._save_restrict_setting(on))

    async def _save_restrict_setting(self, on: bool) -> None:
        from tahmeed.services.settings_service import set_setting
        try:
            await set_setting("restrict_trucks", on)
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to save setting:\n{exc}")

    # ── Internal ───────────────────────────────────────────────────────────────

    async def _load(self) -> None:
        try:
            self._rows = await self._fn_get_all()
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Could not load {self._kind.lower()}s:\n{exc}")
            return
        self._populate_table()

    def _visible_rows(self) -> List[dict]:
        search = self._search.text().strip().lower()
        flt = self._filter_cb.currentIndex()  # 0=All, 1=Active, 2=Inactive
        result = []
        for row in self._rows:
            if search and search not in row["number"].lower():
                continue
            active = row.get("active", True)
            if flt == 1 and not active:
                continue
            if flt == 2 and active:
                continue
            result.append(row)
        return result

    def _populate_table(self) -> None:
        rows = self._visible_rows()
        self._table.setRowCount(0)
        self._count_chip.setText(f"{len(self._rows)} {self._kind.lower()}s")

        for i, row in enumerate(rows):
            self._table.insertRow(i)
            active = row.get("active", True)

            num_item = _cell(str(i + 1), Qt.AlignCenter | Qt.AlignVCenter)
            self._table.setItem(i, 0, num_item)

            reg_item = _cell(row["number"], mono=True)
            self._table.setItem(i, 1, reg_item)

            chip = _status_chip(active)
            chip_container = QWidget()
            chip_container.setStyleSheet("background:transparent;")
            cl = QHBoxLayout(chip_container)
            cl.setContentsMargins(6, 4, 6, 4)
            cl.addWidget(chip)
            cl.addStretch()
            self._table.setCellWidget(i, 2, chip_container)

        total = len(self._rows)
        shown = len(rows)
        self._footer.setText(
            f"Showing {shown} of {total} {self._kind.lower()}s"
        )

    def _on_search(self) -> None:
        self._populate_table()

    def _context_menu(self, pos) -> None:
        row = self._table.rowAt(pos.y())
        if row < 0:
            return
        visible = self._visible_rows()
        if row >= len(visible):
            return
        entry = visible[row]
        active = entry.get("active", True)

        menu = QMenu(self)
        toggle_act = menu.addAction(
            "Deactivate" if active else "Activate"
        )
        menu.addSeparator()
        delete_act = menu.addAction("Delete")
        delete_act.setIcon(qta.icon("mdi.delete-outline", color=_RED))

        chosen = menu.exec(self._table.viewport().mapToGlobal(pos))
        if chosen == toggle_act:
            asyncio.ensure_future(self._toggle_active(entry["number"], not active))
        elif chosen == delete_act:
            self._confirm_delete(entry["number"])

    async def _toggle_active(self, number: str, active: bool) -> None:
        try:
            await self._fn_set_active(number, active)
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))
            return
        await self._load()

    def _confirm_delete(self, number: str) -> None:
        if QMessageBox.question(
            self, f"Delete {self._kind}",
            f'Delete "{number}" permanently?\nThis cannot be undone.',
        ) != QMessageBox.Yes:
            return
        asyncio.ensure_future(self._do_delete(number))

    async def _do_delete(self, number: str) -> None:
        try:
            await self._fn_remove(number)
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))
            return
        await self._load()

    def _add_vehicle(self) -> None:
        dlg = _AddVehicleDialog(self._kind, parent=self)
        if dlg.exec() == QDialog.Accepted and dlg.result_number:
            asyncio.ensure_future(self._do_add(dlg.result_number))

    async def _do_add(self, number: str) -> None:
        try:
            await self._fn_add(number)
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))
            return
        await self._load()

    def _import_excel(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            f"Select Excel File for {self._kind} Import",
            _DEFAULT_EXCEL,
            "Excel Files (*.xlsx *.xls)",
        )
        if not path:
            return
        asyncio.ensure_future(self._do_import(path))

    async def _do_import(self, path: str) -> None:
        try:
            import openpyxl
        except ImportError:
            QMessageBox.critical(self, "Missing library",
                                 "openpyxl is required. Run: pip install openpyxl")
            return

        try:
            wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
            sheet = wb.active
            rows = list(sheet.iter_rows(values_only=True))
        except Exception as exc:
            QMessageBox.critical(self, "Import Error", f"Could not read file:\n{exc}")
            return

        numbers = self._parse_numbers(rows)
        if not numbers:
            fname = Path(path).name
            section = self._excel_section
            kind = self._kind.lower()
            QMessageBox.warning(
                self, "Nothing Found",
                f'No {kind} registration numbers found in\n'
                f'"{fname}".\n\n'
                f'Expected a section headed "{section}".'
            )
            return

        try:
            count = await self._fn_bulk_add(numbers)
        except Exception as exc:
            QMessageBox.critical(self, "Import Error", str(exc))
            return

        QMessageBox.information(
            self, "Import Complete",
            f"Parsed {len(numbers)} {self._kind.lower()}s from the file.\n"
            f"{count} new entr{'y' if count == 1 else 'ies'} added to the database."
        )
        await self._load()

    def _parse_numbers(self, rows: list) -> List[str]:
        """Extract registration numbers from the relevant section of the Excel."""
        in_section = False
        numbers: List[str] = []
        for row in rows:
            first = row[0]
            second = row[1] if len(row) > 1 else None

            # Detect section header row
            if isinstance(first, str) and first.strip().upper() == self._excel_section:
                in_section = True
                continue

            # Detect start of a different section — stop
            if in_section and isinstance(first, str) and first.strip() and \
               first.strip().upper() != self._excel_section and \
               not str(first).strip().isdigit() and first.strip().upper() not in ("SN",):
                if len(first.strip()) > 2:
                    break

            if in_section and isinstance(first, (int, float)) and second:
                reg = str(second).strip()
                if reg:
                    numbers.append(reg)

        return numbers


# ── Trucks registry ────────────────────────────────────────────────────────────

class TrucksRegistryWidget(_FleetRegistryBase):
    _kind          = "Truck"
    _icon          = "mdi.truck"
    _excel_section = "TRUCKS"

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        from tahmeed.services.truck_service import (
            get_all_trucks, add_truck, remove_truck,
            set_truck_active, bulk_add_trucks,
        )
        self._fn_get_all   = get_all_trucks
        self._fn_add       = add_truck
        self._fn_remove    = remove_truck
        self._fn_set_active = set_truck_active
        self._fn_bulk_add  = bulk_add_trucks
        super().__init__(parent)


# ── Trailers registry ──────────────────────────────────────────────────────────

class TrailersRegistryWidget(_FleetRegistryBase):
    _kind          = "Trailer"
    _icon          = "mdi.truck-trailer"
    _excel_section = "TRAILERS"

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        from tahmeed.services.truck_service import (
            get_all_trailers, add_trailer, remove_trailer,
            set_trailer_active, bulk_add_trailers,
        )
        self._fn_get_all    = get_all_trailers
        self._fn_add        = add_trailer
        self._fn_remove     = remove_trailer
        self._fn_set_active = set_trailer_active
        self._fn_bulk_add   = bulk_add_trailers
        super().__init__(parent)
