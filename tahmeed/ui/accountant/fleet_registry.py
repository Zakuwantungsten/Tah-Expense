"""Fleet Registry — Trucks, Trailers, and Motorcycles & Cars management tables.

Widgets share a common _FleetRegistryBase that provides search, table,
add-dialog, and Excel import. Data is stored in the `trucks` / `trailers` /
`motor_vehicles` MongoDB collections.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable, Coroutine, List, Optional

import qtawesome as qta
from PySide6.QtCore import Qt, QSize, QTimer
from PySide6.QtGui import QColor, QFont, QBrush
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QFileDialog, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu, QMessageBox,
    QPushButton, QSizePolicy, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from tahmeed.ui.accountant.separate_expenses import (
    _finish_table_row, _stripe_bg, _table_style, _ROW_H,
)
from tahmeed.ui.widgets.column_persistence import bind_column_width_persistence
from tahmeed.ui.widgets.loading_overlay import LoadingOverlay

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

_FLEET_COL_DEFAULTS = [52, 220, 100]
_PAGE_SIZE = 100

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


def _cell(text: str, align: Qt.AlignmentFlag = Qt.AlignLeft | Qt.AlignVCenter,
          mono: bool = False, bg: str = "") -> QTableWidgetItem:
    item = QTableWidgetItem(str(text) if text is not None else "—")
    item.setTextAlignment(align)
    if mono:
        item.setFont(QFont("Cascadia Code", 11))
    if bg:
        item.setBackground(QBrush(QColor(bg)))
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
    def __init__(
        self,
        kind: str,
        parent: Optional[QWidget] = None,
        *,
        require_plate_format: bool = True,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Add {kind}")
        self.setMinimumWidth(360)
        self.setStyleSheet("background:#FFFFFF;")
        self.result_number: Optional[str] = None
        self._require_plate_format = require_plate_format

        vl = QVBoxLayout(self)
        vl.setContentsMargins(24, 24, 24, 24)
        vl.setSpacing(12)

        vl.addWidget(_lbl(f"Add {kind}", size=15, weight=700))

        vl.addWidget(_lbl("Registration Number *", size=12, color=_T2))
        self._inp = QLineEdit()
        self._inp.setPlaceholderText(
            "e.g. T880 CUL" if require_plate_format else "e.g. MC 123 ABC"
        )
        self._inp.setStyleSheet(
            f"QLineEdit{{border:1px solid {_BORDER};border-radius:5px;"
            f"background:{_WHITE};color:{_T1};font-size:13px;"
            f"font-family:'Segoe UI';padding:0 8px;"
            f"min-height:34px;max-height:34px;}}"
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
        from tahmeed.services.truck_format import normalize_truck_number

        val = self._inp.text().strip()
        if not val:
            QMessageBox.warning(self, "Validation", "Registration number is required.")
            return
        if self._require_plate_format:
            result = normalize_truck_number(val)
            if result.status not in ("ok", "normalized"):
                QMessageBox.warning(
                    self, "Validation",
                    f'"{val}" is not a valid registration number.\n\n'
                    "Use format T + number + space + suffix, e.g. T880 CUL.\n"
                    "Compact forms like T880CUL are auto-corrected.",
                )
                return
            self.result_number = result.value
            self._inp.setText(result.value)
        else:
            number = " ".join(val.upper().split())
            if len(number) < 2:
                QMessageBox.warning(
                    self, "Validation", "Registration number is required."
                )
                return
            self.result_number = number
            self._inp.setText(number)
        self.accept()


# ── Base registry widget ───────────────────────────────────────────────────────

class _FleetRegistryBase(QWidget):
    """
    Shared base for fleet registry pages (trucks, trailers, motor vehicles).
    Subclasses supply: _kind, _kind_plural, _icon, _excel_section,
    _fn_list, _fn_count, _fn_add, _fn_remove, _fn_set_active, _fn_bulk_add.
    """

    _kind: str = "Vehicle"
    _kind_plural: str = "vehicles"
    _icon: str = "mdi.truck"
    _excel_section: str = "TRUCKS"   # "TRUCKS" / "TRAILERS" / "MOTOR VEHICLES"
    _col_prefs_key: str = "fleet_registry"
    # Trucks/trailers use T### XXX; motorcycles & cars accept free-form plates.
    _require_plate_format: bool = True

    # async callables — set by subclass
    _fn_list: Callable
    _fn_count: Callable
    _fn_add: Callable
    _fn_remove: Callable
    _fn_set_active: Callable
    _fn_bulk_add: Callable

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._rows: List[dict] = []
        self._page = 0
        self._total = 0
        self._loading = False
        self._search_debounce = QTimer(self)
        self._search_debounce.setSingleShot(True)
        self._search_debounce.setInterval(350)
        self._search_debounce.timeout.connect(self._on_search_commit)
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
            "exist in the fleet registries (trucks, trailers, motorcycles & cars)."
        )
        try:
            self._restrict_btn.setIcon(qta.icon("mdi.lock-outline", color=_T2))
            self._restrict_btn.setIconSize(QSize(15, 15))
        except Exception:
            pass
        self._restrict_btn.setStyleSheet(
            f"QPushButton{{background:{_WHITE};color:{_T2};border:1px solid {_BORDER};"
            f"border-radius:5px;font-size:12px;font-family:'Segoe UI';padding:0 12px;}}"
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
            f"font-family:'Segoe UI';padding:0 8px;}}"
            f"QLineEdit:focus{{border-color:{_BLUE};}}"
        )
        self._search.textChanged.connect(self._on_search_changed)
        toolbar.addWidget(self._search, 1)

        self._filter_cb = QComboBox()
        self._filter_cb.addItems(["All", "Active only", "Inactive only"])
        self._filter_cb.setFixedHeight(32)
        self._filter_cb.setStyleSheet(
            f"QComboBox{{border:1px solid {_BORDER};border-radius:5px;"
            f"background:{_WHITE};color:{_T1};font-size:12px;"
            f"font-family:'Segoe UI';padding:0 8px;min-width:120px;}}"
            f"QComboBox:focus{{border-color:{_BLUE};}}"
            "QComboBox::drop-down{border:none;width:20px;}"
        )
        self._filter_cb.currentIndexChanged.connect(self._on_filter_changed)
        toolbar.addWidget(self._filter_cb)

        root.addLayout(toolbar)

        # ── Table ────────────────────────────────────────────────────────────
        self._table_host = QFrame()
        self._table_host.setStyleSheet("QFrame { background: transparent; border: none; }")
        table_vl = QVBoxLayout(self._table_host)
        table_vl.setContentsMargins(0, 0, 0, 0)
        table_vl.setSpacing(0)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["#", "Registration No.", "Status"])
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setAlternatingRowColors(False)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(_ROW_H)
        self._table.setStyleSheet(_table_style())
        self._table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._context_menu)

        hdr_view = self._table.horizontalHeader()
        hdr_view.setSectionsMovable(False)
        hdr_view.setStretchLastSection(True)
        for i, width in enumerate(_FLEET_COL_DEFAULTS):
            self._table.setColumnWidth(i, width)
            hdr_view.setSectionResizeMode(i, QHeaderView.Interactive)
        bind_column_width_persistence(
            self._table, self._col_prefs_key, _FLEET_COL_DEFAULTS,
        )

        table_vl.addWidget(self._table)
        root.addWidget(self._table_host, 1)

        self._loading_overlay = LoadingOverlay(self._table_host, "Loading…")

        # ── Pagination ───────────────────────────────────────────────────────
        pager = QFrame()
        pager.setFixedHeight(44)
        pager.setStyleSheet(
            f"QFrame{{background:{_WHITE};border:1px solid {_BORDER};border-radius:6px;}}"
        )
        pl = QHBoxLayout(pager)
        pl.setContentsMargins(12, 0, 12, 0)
        pl.setSpacing(10)

        self._page_info = _lbl("—", size=12, color=_T2)
        pl.addWidget(self._page_info)
        pl.addStretch()

        self._prev_btn = _btn("← Prev", primary=False, height=30)
        self._prev_btn.setFixedWidth(88)
        self._prev_btn.clicked.connect(self._on_prev_page)
        pl.addWidget(self._prev_btn)

        self._next_btn = _btn("Next →", primary=False, height=30)
        self._next_btn.setFixedWidth(88)
        self._next_btn.clicked.connect(self._on_next_page)
        pl.addWidget(self._next_btn)

        root.addWidget(pager)

        # ── Footer ───────────────────────────────────────────────────────────
        self._footer = _lbl("", size=11, color=_TM)
        root.addWidget(self._footer)

    # ── Public API ─────────────────────────────────────────────────────────────

    def refresh(self) -> None:
        asyncio.ensure_future(self._load())
        asyncio.ensure_future(self._load_restrict_setting())

    # ── Restrict-in-cashier toggle ──────────────────────────────────────────────

    async def _load_restrict_setting(self) -> None:
        from tahmeed.services.settings_service import get_setting, set_setting
        # Restriction is always on — keep DB setting aligned and lock the toggle.
        try:
            on = bool(await get_setting("restrict_trucks"))
            if not on:
                await set_setting("restrict_trucks", True)
        except Exception:
            pass
        self._restrict_btn.blockSignals(True)
        self._restrict_btn.setChecked(True)
        self._restrict_btn.setEnabled(False)
        self._restrict_btn.setText("  Restrict in cashier: On (required)")
        self._restrict_btn.setToolTip(
            "Cashiers may only enter fleet numbers that exist in the registries "
            "(trucks, trailers, motorcycles & cars)."
        )
        self._restrict_btn.blockSignals(False)

    def _on_restrict_toggled(self, on: bool) -> None:
        # Restriction is always required — ignore attempts to turn it off.
        self._restrict_btn.blockSignals(True)
        self._restrict_btn.setChecked(True)
        self._restrict_btn.setText("  Restrict in cashier: On (required)")
        self._restrict_btn.blockSignals(False)
        asyncio.ensure_future(self._save_restrict_setting(True))

    async def _save_restrict_setting(self, on: bool) -> None:
        from tahmeed.services.settings_service import set_setting
        try:
            await set_setting("restrict_trucks", True)
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to save setting:\n{exc}")

    # ── Internal ───────────────────────────────────────────────────────────────

    def _active_filter(self) -> str:
        idx = self._filter_cb.currentIndex()
        if idx == 1:
            return "active"
        if idx == 2:
            return "inactive"
        return "all"

    def _on_search_changed(self) -> None:
        self._search_debounce.start()

    def _on_search_commit(self) -> None:
        self._page = 0
        asyncio.ensure_future(self._load())

    def _on_filter_changed(self) -> None:
        self._page = 0
        asyncio.ensure_future(self._load())

    def _on_prev_page(self) -> None:
        if self._page > 0:
            self._page -= 1
            asyncio.ensure_future(self._load())

    def _on_next_page(self) -> None:
        max_pg = max(0, (self._total - 1) // _PAGE_SIZE) if self._total else 0
        if self._page < max_pg:
            self._page += 1
            asyncio.ensure_future(self._load())

    def _update_pager(self) -> None:
        total = self._total
        size = _PAGE_SIZE
        page = self._page
        max_pg = max(0, (total - 1) // size) if total else 0
        start = page * size + 1 if total else 0
        end = min((page + 1) * size, total)
        self._page_info.setText(
            f"Showing {start:,}–{end:,} of {total:,}  ·  Page {page + 1} of {max_pg + 1}"
        )
        self._prev_btn.setEnabled(page > 0)
        self._next_btn.setEnabled(page < max_pg)

    async def _load(self) -> None:
        if self._loading:
            return
        self._loading = True
        self._loading_overlay.show_loading(f"Loading {self._kind_plural}…")
        try:
            search = self._search.text().strip()
            active_filter = self._active_filter()
            skip = self._page * _PAGE_SIZE
            rows, total = await asyncio.gather(
                self._fn_list(
                    search=search,
                    active_filter=active_filter,
                    limit=_PAGE_SIZE,
                    skip=skip,
                ),
                self._fn_count(search=search, active_filter=active_filter),
            )
            max_pg = max(0, (total - 1) // _PAGE_SIZE) if total else 0
            if self._page > max_pg:
                self._page = max_pg
                skip = self._page * _PAGE_SIZE
                rows = await self._fn_list(
                    search=search,
                    active_filter=active_filter,
                    limit=_PAGE_SIZE,
                    skip=skip,
                )
            self._rows = rows
            self._total = total
            self._populate_table()
            self._update_pager()
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Could not load {self._kind_plural}:\n{exc}")
        finally:
            self._loading = False
            self._loading_overlay.hide_loading()

    def _populate_table(self) -> None:
        rows = self._rows
        skip = self._page * _PAGE_SIZE
        self._table.setRowCount(0)
        self._count_chip.setText(f"{self._total:,} {self._kind_plural}")

        for i, row in enumerate(rows):
            self._table.insertRow(i)
            active = row.get("active", True)
            row_bg = _stripe_bg(i)

            self._table.setItem(
                i, 0,
                _cell(str(skip + i + 1), Qt.AlignCenter | Qt.AlignVCenter, bg=row_bg),
            )
            self._table.setItem(
                i, 1,
                _cell(row["number"], mono=True, bg=row_bg),
            )

            chip = _status_chip(active)
            chip_container = QWidget()
            chip_container.setStyleSheet(f"background: {row_bg};")
            cl = QHBoxLayout(chip_container)
            cl.setContentsMargins(6, 2, 6, 2)
            cl.addWidget(chip)
            cl.addStretch()
            self._table.setCellWidget(i, 2, chip_container)
            _finish_table_row(self._table, i, row_bg)

        shown = len(rows)
        self._footer.setText(
            f"{shown} on this page  ·  {self._total:,} total matching"
        )

    def _context_menu(self, pos) -> None:
        row = self._table.rowAt(pos.y())
        if row < 0:
            return
        if row >= len(self._rows):
            return
        entry = self._rows[row]
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
        dlg = _AddVehicleDialog(
            self._kind,
            parent=self,
            require_plate_format=self._require_plate_format,
        )
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
        self._loading_overlay.show_loading(f"Importing {self._kind_plural}…")
        try:
            import openpyxl
        except ImportError:
            self._loading_overlay.hide_loading()
            QMessageBox.critical(self, "Missing library",
                                 "openpyxl is required. Run: pip install openpyxl")
            return

        try:
            wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
            sheet = wb.active
            rows = list(sheet.iter_rows(values_only=True))
        except Exception as exc:
            self._loading_overlay.hide_loading()
            QMessageBox.critical(self, "Import Error", f"Could not read file:\n{exc}")
            return

        numbers = self._parse_numbers(rows)
        if not numbers:
            self._loading_overlay.hide_loading()
            fname = Path(path).name
            section = self._excel_section
            QMessageBox.warning(
                self, "Nothing Found",
                f'No {self._kind_plural} registration numbers found in\n'
                f'"{fname}".\n\n'
                f'Expected a section headed "{section}".'
            )
            return

        try:
            count = await self._fn_bulk_add(numbers)
        except Exception as exc:
            self._loading_overlay.hide_loading()
            QMessageBox.critical(self, "Import Error", str(exc))
            return

        self._loading_overlay.hide_loading()
        QMessageBox.information(
            self, "Import Complete",
            f"Parsed {len(numbers)} {self._kind_plural} from the file.\n"
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
    _kind_plural   = "trucks"
    _icon          = "mdi.truck"
    _excel_section = "TRUCKS"
    _col_prefs_key = "fleet_trucks"

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        from tahmeed.services.truck_service import (
            list_trucks, count_trucks, add_truck, remove_truck,
            set_truck_active, bulk_add_trucks,
        )
        self._fn_list       = list_trucks
        self._fn_count      = count_trucks
        self._fn_add        = add_truck
        self._fn_remove     = remove_truck
        self._fn_set_active = set_truck_active
        self._fn_bulk_add   = bulk_add_trucks
        super().__init__(parent)


# ── Trailers registry ──────────────────────────────────────────────────────────

class TrailersRegistryWidget(_FleetRegistryBase):
    _kind          = "Trailer"
    _kind_plural   = "trailers"
    _icon          = "mdi.truck-trailer"
    _excel_section = "TRAILERS"
    _col_prefs_key = "fleet_trailers"

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        from tahmeed.services.truck_service import (
            list_trailers, count_trailers, add_trailer, remove_trailer,
            set_trailer_active, bulk_add_trailers,
        )
        self._fn_list       = list_trailers
        self._fn_count      = count_trailers
        self._fn_add        = add_trailer
        self._fn_remove     = remove_trailer
        self._fn_set_active = set_trailer_active
        self._fn_bulk_add   = bulk_add_trailers
        super().__init__(parent)


# ── Motorcycles & Cars registry ────────────────────────────────────────────────

class MotorVehiclesRegistryWidget(_FleetRegistryBase):
    _kind          = "Motorcycles & Cars"
    _kind_plural   = "motorcycles & cars"
    _icon          = "mdi.car"
    _excel_section = "MOTOR VEHICLES"
    _col_prefs_key = "fleet_motor_vehicles"
    _require_plate_format = False

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        from tahmeed.services.truck_service import (
            list_motor_vehicles, count_motor_vehicles, add_motor_vehicle,
            remove_motor_vehicle, set_motor_vehicle_active, bulk_add_motor_vehicles,
        )
        self._fn_list       = list_motor_vehicles
        self._fn_count      = count_motor_vehicles
        self._fn_add        = add_motor_vehicle
        self._fn_remove     = remove_motor_vehicle
        self._fn_set_active = set_motor_vehicle_active
        self._fn_bulk_add   = bulk_add_motor_vehicles
        super().__init__(parent)
