"""People Registry — names for Ownership / APR BY autocomplete.

Cashiers can still type any free-text value; this registry only powers
suggestions and inline preview (same interaction as Items).

Also stores receipt identity: linked login (user_id) and whether the person
receives mobile receipt requests — used by chase / early park / person dossier.
"""

from __future__ import annotations

import asyncio
from typing import Any, List, Optional

import qtawesome as qta
from PySide6.QtCore import Qt, QSize, QTimer
from PySide6.QtGui import QColor, QBrush
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from tahmeed.ui.accountant.master_ledger_table import _TABLE_SS as _MASTER_TABLE_SS
from tahmeed.ui.accountant.separate_expenses import (
    _finish_table_row, _make_table, _stripe_bg, _table_cell_font,
)
from tahmeed.ui.widgets.column_persistence import bind_column_width_persistence
from tahmeed.ui.widgets.loading_overlay import LoadingOverlay
from tahmeed.ui.widgets.popup_styles import apply_light_menu

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

_COL_DEFAULTS = [52, 220, 180, 100, 100]
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
    item.setFont(_table_cell_font())
    if bg:
        item.setBackground(QBrush(QColor(bg)))
    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    return item


def _status_chip(active: bool) -> QLabel:
    lbl = QLabel("Active" if active else "Inactive")
    lbl.setAlignment(Qt.AlignCenter)
    lbl.setFixedHeight(18)
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


class _PersonEditorDialog(QDialog):
    """Add / edit a people row including receipt identity fields."""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        person: Optional[dict[str, Any]] = None,
        users: Optional[List[Any]] = None,
    ) -> None:
        super().__init__(parent)
        self._editing = bool(person and person.get("name"))
        self.setWindowTitle("Edit Person" if self._editing else "Add Person")
        self.setMinimumWidth(420)
        self.setStyleSheet("background:#FFFFFF;")
        self.result: Optional[dict[str, Any]] = None
        self._users = list(users or [])

        field_ss = (
            f"QLineEdit,QComboBox{{border:1px solid {_BORDER};border-radius:5px;"
            f"background:{_WHITE};color:{_T1};font-size:13px;"
            "font-family:'Segoe UI';padding:0 8px;"
            "min-height:34px;max-height:34px;}"
            f"QLineEdit:focus,QComboBox:focus{{border-color:{_BLUE};}}"
            "QComboBox::drop-down{border:none;width:20px;}"
        )

        vl = QVBoxLayout(self)
        vl.setContentsMargins(24, 24, 24, 24)
        vl.setSpacing(10)

        vl.addWidget(
            _lbl(
                "Edit Person" if self._editing else "Add Person",
                size=15,
                weight=700,
            )
        )
        vl.addWidget(
            _lbl(
                "Ownership / APR BY suggestions, plus mobile receipt identity "
                "(linked login + receives requests).",
                size=12,
                color=_T2,
            )
        )

        vl.addWidget(_lbl("Name *", size=12, color=_T2))
        self._inp = QLineEdit()
        self._inp.setPlaceholderText("e.g. JOHN DOE")
        self._inp.setStyleSheet(field_ss)
        if self._editing:
            self._inp.setText(str(person.get("name") or ""))
            self._inp.setReadOnly(True)
            self._inp.setToolTip("Rename is not supported — create a new person instead.")
        else:
            self._inp.returnPressed.connect(self._accept)
        vl.addWidget(self._inp)

        vl.addWidget(_lbl("Phone", size=12, color=_T2))
        self._phone = QLineEdit()
        self._phone.setPlaceholderText("Optional")
        self._phone.setStyleSheet(field_ss)
        if person:
            self._phone.setText(str(person.get("phone") or ""))
        vl.addWidget(self._phone)

        vl.addWidget(_lbl("Linked login", size=12, color=_T2))
        from tahmeed.ui.widgets.combo_styles import with_combo_popup

        self._user_cb = QComboBox()
        self._user_cb.setStyleSheet(with_combo_popup(field_ss))
        self._user_cb.addItem("(none)", None)
        current_uid = str(person.get("user_id") or "") if person else ""
        selected_idx = 0
        for i, user in enumerate(self._users, start=1):
            uid = str(getattr(user, "_id", "") or "")
            full = str(getattr(user, "full_name", "") or "").strip()
            uname = str(getattr(user, "username", "") or "").strip()
            role = str(getattr(user, "role", "") or "").strip()
            label = full or uname or uid
            if uname and full and uname.upper() != full.upper():
                label = f"{full} ({uname})"
            if role:
                label = f"{label} · {role}"
            self._user_cb.addItem(label, uid or None)
            if current_uid and uid == current_uid:
                selected_idx = i
        self._user_cb.setCurrentIndex(selected_idx)
        vl.addWidget(self._user_cb)

        self._receives = QCheckBox("Receives receipt requests (mobile chase)")
        self._receives.setChecked(
            True
            if not person
            else bool(person.get("receives_receipt_requests", True))
        )
        self._receives.setStyleSheet(
            f"QCheckBox{{color:{_T1};font:12px 'Segoe UI';background:transparent;}}"
        )
        vl.addWidget(self._receives)

        if self._editing:
            self._active = QCheckBox("Active")
            self._active.setChecked(bool(person.get("active", True)))
            self._active.setStyleSheet(
                f"QCheckBox{{color:{_T1};font:12px 'Segoe UI';background:transparent;}}"
            )
            vl.addWidget(self._active)
        else:
            self._active = None

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel = _btn("Cancel", primary=False, height=32)
        cancel.clicked.connect(self.reject)
        btn_row.addWidget(cancel)
        save = _btn("Save" if self._editing else "Add", primary=True, height=32)
        save.clicked.connect(self._accept)
        btn_row.addWidget(save)
        vl.addLayout(btn_row)

    def _accept(self) -> None:
        val = " ".join(self._inp.text().upper().split())
        if not val:
            QMessageBox.warning(self, "Validation", "Name is required.")
            return
        self.result = {
            "name": val,
            "phone": self._phone.text().strip(),
            "user_id": self._user_cb.currentData(),
            "receives_receipt_requests": self._receives.isChecked(),
            "active": True if self._active is None else self._active.isChecked(),
        }
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

        hint = _lbl(
            "Suggestions + receipt identity — cashiers may still type any name",
            size=11,
            color=_TM,
        )
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
            "font-family:'Segoe UI';padding:0 8px;}"
            f"QLineEdit:focus{{border-color:{_BLUE};}}"
        )
        self._search.textChanged.connect(self._on_search_changed)
        toolbar.addWidget(self._search, 1)

        self._filter_cb = QComboBox()
        self._filter_cb.addItems(["All", "Active only", "Inactive only"])
        self._filter_cb.setFixedHeight(32)
        from tahmeed.ui.widgets.combo_styles import with_combo_popup
        self._filter_cb.setStyleSheet(with_combo_popup(
            f"QComboBox{{border:1px solid {_BORDER};border-radius:5px;"
            f"background:{_WHITE};color:{_T1};font-size:12px;"
            "font-family:'Segoe UI';padding:0 8px;min-width:120px;}"
            f"QComboBox:focus{{border-color:{_BLUE};}}"
            "QComboBox::drop-down{border:none;width:20px;}"
        ))
        self._filter_cb.currentIndexChanged.connect(self._on_filter_changed)
        toolbar.addWidget(self._filter_cb)
        root.addLayout(toolbar)

        self._table_host = QFrame()
        self._table_host.setStyleSheet("QFrame { background: transparent; border: none; }")
        table_vl = QVBoxLayout(self._table_host)
        table_vl.setContentsMargins(0, 0, 0, 0)
        table_vl.setSpacing(0)

        self._table = _make_table(["#", "Name", "Linked login", "Receives", "Status"])
        self._table.setStyleSheet(_MASTER_TABLE_SS)
        self._table.setShowGrid(True)
        self._table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._context_menu)
        self._table.cellDoubleClicked.connect(self._on_row_double_clicked)

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
            linked = "Linked" if row.get("user_id") else "—"
            self._table.setItem(i, 2, _cell(linked, bg=row_bg))
            receives = row.get("receives_receipt_requests", True)
            self._table.setItem(
                i,
                3,
                _cell("Yes" if receives else "No", bg=row_bg),
            )
            chip = _status_chip(active)
            chip_container = QWidget()
            chip_container.setStyleSheet(f"background: {row_bg};")
            cl = QHBoxLayout(chip_container)
            cl.setContentsMargins(6, 1, 6, 1)
            cl.addWidget(chip)
            cl.addStretch()
            self._table.setCellWidget(i, 4, chip_container)
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

        menu = apply_light_menu(QMenu(self))
        edit_act = menu.addAction("Edit identity…")
        edit_act.setIcon(qta.icon("mdi.account-edit-outline", color=_T1))
        toggle_act = menu.addAction("Deactivate" if active else "Activate")
        menu.addSeparator()
        delete_act = menu.addAction("Delete")
        delete_act.setIcon(qta.icon("mdi.delete-outline", color=_RED))

        chosen = menu.exec(self._table.viewport().mapToGlobal(pos))
        if chosen == edit_act:
            asyncio.ensure_future(self._edit_person(entry))
        elif chosen == toggle_act:
            asyncio.ensure_future(self._toggle_active(name, not active))
        elif chosen == delete_act:
            self._confirm_delete(name)

    def _on_row_double_clicked(self, row: int, _col: int) -> None:
        if row < 0 or row >= len(self._rows):
            return
        asyncio.ensure_future(self._edit_person(self._rows[row]))

    async def _load_users_for_editor(self) -> list:
        try:
            from tahmeed.services.user_service import get_all_users

            users = await get_all_users()
            return [
                u
                for u in users
                if getattr(u, "active", True)
                and not getattr(u, "banned", False)
                and not getattr(u, "deleted_at", None)
            ]
        except Exception:
            return []

    async def _edit_person(self, entry: dict) -> None:
        users = await self._load_users_for_editor()
        dlg = _PersonEditorDialog(parent=self, person=entry, users=users)
        if dlg.exec() != QDialog.Accepted or not dlg.result:
            return
        await self._do_upsert(dlg.result)

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
        asyncio.ensure_future(self._add_person_async())

    async def _add_person_async(self) -> None:
        users = await self._load_users_for_editor()
        dlg = _PersonEditorDialog(parent=self, users=users)
        if dlg.exec() != QDialog.Accepted or not dlg.result:
            return
        await self._do_upsert(dlg.result)

    async def _do_upsert(self, payload: dict[str, Any]) -> None:
        from tahmeed.services.people_service import upsert_person

        try:
            await upsert_person(
                str(payload.get("name") or ""),
                active=bool(payload.get("active", True)),
                phone=str(payload.get("phone") or ""),
                receives_receipt_requests=bool(
                    payload.get("receives_receipt_requests", True)
                ),
                user_id=payload.get("user_id"),
            )
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))
            return
        await self._load()
