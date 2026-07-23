"""People Registry — names for Ownership / APR BY autocomplete.

Cashiers can still type any free-text value; this registry only powers
suggestions and inline preview (same interaction as Items).
"""

from __future__ import annotations

import asyncio
from typing import List, Optional

import qtawesome as qta
from PySide6.QtCore import Qt, QSize, QTimer
from PySide6.QtGui import QColor, QFont, QBrush
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from tahmeed.ui.accountant.separate_expenses import (
    _finish_table_row, _stripe_bg, _table_style, _ROW_H,
)
from tahmeed.ui.widgets.column_persistence import bind_column_width_persistence
from tahmeed.ui.widgets.loading_overlay import LoadingOverlay

_WHITE   = "#FFFFFF"
_BG      = "#F4F6F8"
_BORDER  = "#E5E7EB"
_BLUE    = "#0077C5"
_BLUE_L  = "#E8F4FD"
_GREEN   = "#16A34A"
_GREEN_L = "#DCFCE7"
_RED     = "#DC2626"
_RED_L   = "#FEE2E2"
_T1      = "#111827"
_T2      = "#6B7280"
_TM      = "#9CA3AF"
_HDR_BG  = "#F1F5F9"

_COL_DEFAULTS = [52, 280, 100]
_PAGE_SIZE = 100


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
          bg: str = "") -> QTableWidgetItem:
    item = QTableWidgetItem(str(text) if text is not None else "—")
    item.setTextAlignment(align)
    item.setFont(QFont("Segoe UI", 11))
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


class _AddPersonDialog(QDialog):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add Person")
        self.setMinimumWidth(360)
        self.setStyleSheet("background:#FFFFFF;")
        self.result_name: Optional[str] = None

        vl = QVBoxLayout(self)
        vl.setContentsMargins(24, 24, 24, 24)
        vl.setSpacing(12)

        vl.addWidget(_lbl("Add Person", size=15, weight=700))
        vl.addWidget(_lbl(
            "Used for Ownership and APR BY suggestions in the register.",
            size=12, color=_T2,
        ))

        vl.addWidget(_lbl("Name *", size=12, color=_T2))
        self._inp = QLineEdit()
        self._inp.setPlaceholderText("e.g. JOHN DOE")
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
        val = " ".join(self._inp.text().upper().split())
        if not val:
            QMessageBox.warning(self, "Validation", "Name is required.")
            return
        self.result_name = val
        self._inp.setText(val)
        self.accept()


class PeopleRegistryWidget(QWidget):
    """Manage people names for Ownership / APR BY autocomplete."""

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

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(16)

        hdr = QHBoxLayout()
        hdr.setSpacing(10)
        try:
            icon_lbl = QLabel()
            icon_lbl.setPixmap(qta.icon("mdi.account-outline", color=_BLUE).pixmap(24, 24))
            icon_lbl.setFixedSize(24, 24)
            icon_lbl.setStyleSheet("background:transparent;")
            hdr.addWidget(icon_lbl)
        except Exception:
            pass
        hdr.addWidget(_lbl("People Registry", size=18, weight=700))

        self._count_chip = QLabel("—")
        self._count_chip.setStyleSheet(
            f"background:{_BLUE_L};color:{_BLUE};font-size:11px;font-weight:700;"
            "border-radius:10px;padding:2px 10px;font-family:'Segoe UI';"
        )
        hdr.addWidget(self._count_chip)
        hdr.addStretch()

        hint = _lbl("Suggestions only — cashiers may still type any name", size=11, color=_TM)
        hdr.addWidget(hint)

        add_btn = _btn("+ Add Person", primary=True, height=32)
        add_btn.clicked.connect(self._add_person)
        hdr.addWidget(add_btn)
        root.addLayout(hdr)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search by name…")
        self._search.setFixedHeight(32)
        self._search.setStyleSheet(
            f"QLineEdit{{border:1px solid {_BORDER};border-radius:5px;"
            f"background:{_WHITE};color:{_T1};font-size:12px;"
            "font-family:'Segoe UI';padding:0 8px;}}"
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
            "font-family:'Segoe UI';padding:0 8px;min-width:120px;}}"
            f"QComboBox:focus{{border-color:{_BLUE};}}"
            "QComboBox::drop-down{border:none;width:20px;}"
        )
        self._filter_cb.currentIndexChanged.connect(self._on_filter_changed)
        toolbar.addWidget(self._filter_cb)
        root.addLayout(toolbar)

        self._table_host = QFrame()
        self._table_host.setStyleSheet("QFrame { background: transparent; border: none; }")
        table_vl = QVBoxLayout(self._table_host)
        table_vl.setContentsMargins(0, 0, 0, 0)
        table_vl.setSpacing(0)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["#", "Name", "Status"])
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
        for i, width in enumerate(_COL_DEFAULTS):
            self._table.setColumnWidth(i, width)
            hdr_view.setSectionResizeMode(i, QHeaderView.Interactive)
        bind_column_width_persistence(self._table, "people_registry", _COL_DEFAULTS)
        table_vl.addWidget(self._table)
        root.addWidget(self._table_host, 1)

        self._loading_overlay = LoadingOverlay(self._table_host, "Loading…")

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

        self._footer = _lbl("", size=11, color=_TM)
        root.addWidget(self._footer)

    def refresh(self) -> None:
        asyncio.ensure_future(self._load())

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
        self._loading_overlay.show_loading("Loading people…")
        try:
            from tahmeed.services.people_service import list_people, count_people

            search = self._search.text().strip()
            active_filter = self._active_filter()
            skip = self._page * _PAGE_SIZE
            rows, total = await asyncio.gather(
                list_people(
                    search=search,
                    active_filter=active_filter,
                    limit=_PAGE_SIZE,
                    skip=skip,
                ),
                count_people(search=search, active_filter=active_filter),
            )
            max_pg = max(0, (total - 1) // _PAGE_SIZE) if total else 0
            if self._page > max_pg:
                self._page = max_pg
                skip = self._page * _PAGE_SIZE
                rows = await list_people(
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
            QMessageBox.critical(self, "Error", f"Could not load people:\n{exc}")
        finally:
            self._loading = False
            self._loading_overlay.hide_loading()

    def _populate_table(self) -> None:
        rows = self._rows
        skip = self._page * _PAGE_SIZE
        self._table.setRowCount(0)
        self._count_chip.setText(f"{self._total:,} people")

        for i, row in enumerate(rows):
            self._table.insertRow(i)
            active = row.get("active", True)
            row_bg = _stripe_bg(i)
            self._table.setItem(
                i, 0,
                _cell(str(skip + i + 1), Qt.AlignCenter | Qt.AlignVCenter, bg=row_bg),
            )
            self._table.setItem(i, 1, _cell(row.get("name", ""), bg=row_bg))
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
        if row < 0 or row >= len(self._rows):
            return
        entry = self._rows[row]
        active = entry.get("active", True)
        name = entry.get("name", "")

        menu = QMenu(self)
        toggle_act = menu.addAction("Deactivate" if active else "Activate")
        menu.addSeparator()
        delete_act = menu.addAction("Delete")
        delete_act.setIcon(qta.icon("mdi.delete-outline", color=_RED))

        chosen = menu.exec(self._table.viewport().mapToGlobal(pos))
        if chosen == toggle_act:
            asyncio.ensure_future(self._toggle_active(name, not active))
        elif chosen == delete_act:
            self._confirm_delete(name)

    async def _toggle_active(self, name: str, active: bool) -> None:
        from tahmeed.services.people_service import set_person_active

        try:
            await set_person_active(name, active)
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))
            return
        await self._load()

    def _confirm_delete(self, name: str) -> None:
        if QMessageBox.question(
            self, "Delete Person",
            f'Delete "{name}" permanently?\nThis cannot be undone.',
        ) != QMessageBox.Yes:
            return
        asyncio.ensure_future(self._do_delete(name))

    async def _do_delete(self, name: str) -> None:
        from tahmeed.services.people_service import remove_person

        try:
            await remove_person(name)
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))
            return
        await self._load()

    def _add_person(self) -> None:
        dlg = _AddPersonDialog(parent=self)
        if dlg.exec() == QDialog.Accepted and dlg.result_name:
            asyncio.ensure_future(self._do_add(dlg.result_name))

    async def _do_add(self, name: str) -> None:
        from tahmeed.services.people_service import add_person

        try:
            await add_person(name)
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))
            return
        await self._load()
