"""Description Maps — manage remembered description → item assignments."""

from __future__ import annotations

import asyncio
from typing import List, Optional

import qtawesome as qta
from PySide6.QtCore import Qt, QSize, QTimer
from PySide6.QtGui import QColor, QFont, QBrush
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QCompleter, QDialog, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from tahmeed.models.category import Category
from tahmeed.models.description_mapping import DescriptionMapping
from tahmeed.ui.accountant.separate_expenses import (
    _finish_table_row, _stripe_bg, _table_style, _ROW_H,
)
from tahmeed.ui.dialog_theme import show_critical, show_info, show_warning
from tahmeed.ui.widgets.column_persistence import bind_column_width_persistence
from tahmeed.ui.widgets.loading_overlay import LoadingOverlay

_WHITE = "#FFFFFF"
_BG = "#F4F6F8"
_BORDER = "#E5E7EB"
_BLUE = "#0077C5"
_BLUE_L = "#E8F4FD"
_RED = "#DC2626"
_T1 = "#111827"
_T2 = "#6B7280"
_TM = "#9CA3AF"

_COL_DEFAULTS = [52, 280, 220]
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
        ss = (
            f"QPushButton{{background:{_RED};color:#FFFFFF;border:none;border-radius:5px;"
            f"font-size:12px;font-weight:600;font-family:'Segoe UI';padding:0 14px;}}"
            f"QPushButton:hover{{background:#B91C1C;}}"
            f"QPushButton:disabled{{background:#FCA5A5;}}"
        )
    elif primary:
        ss = (
            f"QPushButton{{background:{_BLUE};color:#FFFFFF;border:none;border-radius:5px;"
            f"font-size:12px;font-weight:600;font-family:'Segoe UI';padding:0 14px;}}"
            f"QPushButton:hover{{background:#005EA3;}}"
            f"QPushButton:disabled{{background:#93C5FD;}}"
        )
    else:
        ss = (
            f"QPushButton{{background:{_WHITE};color:{_T1};border:1px solid {_BORDER};"
            f"border-radius:5px;font-size:12px;font-family:'Segoe UI';padding:0 14px;}}"
            f"QPushButton:hover{{background:{_BG};}}"
            f"QPushButton:disabled{{color:{_TM};}}"
        )
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


class _MappingEditorDialog(QDialog):
    """Create or edit a description → item map."""

    def __init__(
        self,
        categories: List[Category],
        parent: Optional[QWidget] = None,
        *,
        mapping: Optional[DescriptionMapping] = None,
    ) -> None:
        super().__init__(parent)
        self._categories = categories
        self._mapping = mapping
        self.result_description: Optional[str] = None
        self.result_category: Optional[Category] = None
        editing = mapping is not None
        self.setWindowTitle("Edit Mapping" if editing else "Add Mapping")
        self.setMinimumWidth(440)
        self.setStyleSheet(
            f"QDialog {{ background: {_WHITE}; color: {_T1}; }}"
            f"QLabel {{ color: {_T1}; background: transparent; border: none; }}"
        )
        self._build(editing)

    def _build(self, editing: bool) -> None:
        vl = QVBoxLayout(self)
        vl.setContentsMargins(24, 24, 24, 24)
        vl.setSpacing(12)

        vl.addWidget(_lbl(
            "Edit description → item" if editing else "Add description → item",
            size=15, weight=700,
        ))

        vl.addWidget(_lbl("Description *", size=12, color=_T2))
        self._desc = QLineEdit()
        self._desc.setPlaceholderText("e.g. TRIANGLE")
        self._desc.setFixedHeight(34)
        self._desc.setStyleSheet(
            f"QLineEdit{{border:1px solid {_BORDER};border-radius:5px;"
            f"background:{_WHITE};color:{_T1};font-size:13px;"
            "font-family:'Segoe UI';padding:0 8px;}}"
            f"QLineEdit:focus{{border-color:{_BLUE};}}"
            f"QLineEdit:disabled{{background:{_BG};color:{_T2};}}"
        )
        if self._mapping is not None:
            self._desc.setText(self._mapping.description)
            self._desc.setEnabled(False)
            self._desc.setToolTip(
                "Description key cannot be changed. Delete and re-add to rename."
            )
        vl.addWidget(self._desc)

        vl.addWidget(_lbl("Item *", size=12, color=_T2))
        self._combo = QComboBox()
        self._combo.setEditable(True)
        self._combo.setInsertPolicy(QComboBox.NoInsert)
        self._combo.setFixedHeight(34)
        self._combo.setStyleSheet(
            f"QComboBox{{border:1px solid {_BORDER};border-radius:5px;"
            f"background:{_WHITE};color:{_T1};font-size:12px;"
            "font-family:'Segoe UI';padding:0 8px;}}"
            f"QComboBox:focus{{border-color:{_BLUE};}}"
        )
        for cat in self._categories:
            self._combo.addItem(cat.name, cat._id)
        completer = QCompleter([c.name for c in self._categories], self)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        self._combo.setCompleter(completer)
        if self._mapping is not None:
            idx = self._combo.findText(self._mapping.category_name)
            if idx >= 0:
                self._combo.setCurrentIndex(idx)
            else:
                self._combo.setEditText(self._mapping.category_name)
        else:
            self._combo.setCurrentIndex(-1)
        vl.addWidget(self._combo)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel = _btn("Cancel", primary=False, height=32)
        cancel.clicked.connect(self.reject)
        btn_row.addWidget(cancel)
        save = _btn("Save", primary=True, height=32)
        save.clicked.connect(self._accept)
        btn_row.addWidget(save)
        vl.addLayout(btn_row)

        if self._mapping is None:
            self._desc.setFocus()
        else:
            self._combo.setFocus()

    def _accept(self) -> None:
        desc = self._desc.text().strip()
        if not desc:
            show_warning(self, "Validation", "Description is required.")
            return
        name = self._combo.currentText().strip()
        if not name:
            show_warning(self, "Validation", "Please choose an item.")
            return
        idx = self._combo.findText(name)
        cat_id = self._combo.itemData(idx) if idx >= 0 else None
        if cat_id is None:
            match = next((c for c in self._categories if c.name == name), None)
            if match is None:
                show_warning(
                    self,
                    "Validation",
                    "Please pick an existing item from the Items list.",
                )
                return
            cat_id = match._id
        self.result_description = desc
        self.result_category = Category(_id=cat_id, name=name)
        self.accept()


class DescriptionMapsWidget(QWidget):
    """Browse / add / edit / delete description → item mappings."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._rows: List[DescriptionMapping] = []
        self._page = 0
        self._total = 0
        self._loading = False
        self._categories: List[Category] = []
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
            icon_lbl.setPixmap(
                qta.icon("mdi.link-variant", color=_BLUE).pixmap(24, 24)
            )
            icon_lbl.setFixedSize(24, 24)
            icon_lbl.setStyleSheet("background:transparent;")
            hdr.addWidget(icon_lbl)
        except Exception:
            pass
        hdr.addWidget(_lbl("Description Maps", size=18, weight=700))

        self._count_chip = QLabel("—")
        self._count_chip.setStyleSheet(
            f"background:{_BLUE_L};color:{_BLUE};font-size:11px;font-weight:700;"
            "border-radius:10px;padding:2px 10px;font-family:'Segoe UI';"
        )
        hdr.addWidget(self._count_chip)
        hdr.addStretch()

        hint = _lbl(
            "Select a row to delete · right-click for Edit / Delete",
            size=11, color=_TM,
        )
        hdr.addWidget(hint)

        self._delete_btn = _btn("Delete Selected", icon="mdi.delete-outline", primary=False, danger=True, height=32)
        self._delete_btn.setEnabled(False)
        self._delete_btn.setToolTip("Delete the selected mapping(s)")
        self._delete_btn.clicked.connect(self._delete_selected)
        hdr.addWidget(self._delete_btn)

        clear_btn = _btn("Clear All", primary=False, height=32)
        clear_btn.setToolTip("Delete every description → item mapping")
        clear_btn.clicked.connect(self._confirm_clear_all)
        hdr.addWidget(clear_btn)

        add_btn = _btn("+ Add Mapping", primary=True, height=32)
        add_btn.clicked.connect(self._add_mapping)
        hdr.addWidget(add_btn)
        root.addLayout(hdr)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search by description or item…")
        self._search.setFixedHeight(32)
        self._search.setStyleSheet(
            f"QLineEdit{{border:1px solid {_BORDER};border-radius:5px;"
            f"background:{_WHITE};color:{_T1};font-size:12px;"
            "font-family:'Segoe UI';padding:0 8px;}}"
            f"QLineEdit:focus{{border-color:{_BLUE};}}"
        )
        self._search.textChanged.connect(self._on_search_changed)
        toolbar.addWidget(self._search, 1)
        root.addLayout(toolbar)

        self._table_host = QFrame()
        self._table_host.setStyleSheet(
            "QFrame { background: transparent; border: none; }"
        )
        table_vl = QVBoxLayout(self._table_host)
        table_vl.setContentsMargins(0, 0, 0, 0)
        table_vl.setSpacing(0)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["#", "Description", "Item"])
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._table.setAlternatingRowColors(False)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(_ROW_H)
        self._table.setStyleSheet(_table_style())
        self._table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._context_menu)
        self._table.doubleClicked.connect(self._on_double_click)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)

        hdr_view = self._table.horizontalHeader()
        hdr_view.setSectionsMovable(False)
        hdr_view.setStretchLastSection(True)
        for i, width in enumerate(_COL_DEFAULTS):
            self._table.setColumnWidth(i, width)
            hdr_view.setSectionResizeMode(i, QHeaderView.Interactive)
        bind_column_width_persistence(
            self._table, "description_maps", _COL_DEFAULTS,
        )
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

    def _on_search_changed(self) -> None:
        self._search_debounce.start()

    def _on_search_commit(self) -> None:
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

    async def _ensure_categories(self) -> bool:
        if self._categories:
            return True
        from tahmeed.services.category_service import get_all_categories

        try:
            self._categories = await get_all_categories()
        except Exception as exc:
            show_critical(self, "Error", f"Could not load items:\n{exc}")
            return False
        if not self._categories:
            show_warning(
                self,
                "No Items",
                "Import your Chart of Accounts into Items first\n"
                "(Manage → Items → Import Chart of Accounts).",
            )
            return False
        return True

    async def _load(self) -> None:
        if self._loading:
            return
        self._loading = True
        self._loading_overlay.show_loading("Loading mappings…")
        try:
            from tahmeed.services.description_mapping_service import (
                count_mappings, list_mappings,
            )

            search = self._search.text().strip()
            skip = self._page * _PAGE_SIZE
            rows, total = await asyncio.gather(
                list_mappings(search=search, limit=_PAGE_SIZE, skip=skip),
                count_mappings(search=search),
            )
            max_pg = max(0, (total - 1) // _PAGE_SIZE) if total else 0
            if self._page > max_pg:
                self._page = max_pg
                skip = self._page * _PAGE_SIZE
                rows = await list_mappings(
                    search=search, limit=_PAGE_SIZE, skip=skip,
                )
            self._rows = rows
            self._total = total
            self._populate_table()
            self._update_pager()
        except Exception as exc:
            show_critical(self, "Error", f"Could not load mappings:\n{exc}")
        finally:
            self._loading = False
            self._loading_overlay.hide_loading()

    def _populate_table(self) -> None:
        rows = self._rows
        skip = self._page * _PAGE_SIZE
        self._table.setRowCount(0)
        self._count_chip.setText(f"{self._total:,} maps")

        for i, row in enumerate(rows):
            self._table.insertRow(i)
            row_bg = _stripe_bg(i)
            self._table.setItem(
                i, 0,
                _cell(str(skip + i + 1), Qt.AlignCenter | Qt.AlignVCenter, bg=row_bg),
            )
            self._table.setItem(i, 1, _cell(row.description, bg=row_bg))
            self._table.setItem(i, 2, _cell(row.category_name, bg=row_bg))
            _finish_table_row(self._table, i, row_bg)

        shown = len(rows)
        self._footer.setText(
            f"{shown} on this page  ·  {self._total:,} total matching"
        )
        self._on_selection_changed()

    def _on_selection_changed(self) -> None:
        selected = self._selected_entries()
        n = len(selected)
        self._delete_btn.setEnabled(n > 0)
        if n <= 1:
            self._delete_btn.setText("  Delete Selected")
        else:
            self._delete_btn.setText(f"  Delete Selected ({n})")

    def _selected_entries(self) -> List[DescriptionMapping]:
        rows = sorted({idx.row() for idx in self._table.selectedIndexes()})
        return [
            self._rows[r] for r in rows
            if 0 <= r < len(self._rows)
        ]

    def _delete_selected(self) -> None:
        entries = self._selected_entries()
        if not entries:
            show_info(self, "Delete", "Select one or more mappings to delete.")
            return
        if len(entries) == 1:
            self._confirm_delete(entries[0])
            return
        box = QMessageBox(self)
        box.setWindowTitle("Delete Mappings")
        box.setIcon(QMessageBox.Question)
        box.setText(f"Delete {len(entries):,} selected mapping(s)?")
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.No)
        from tahmeed.ui.dialog_theme import style_message_box
        style_message_box(box)
        if box.exec() != QMessageBox.Yes:
            return
        asyncio.ensure_future(self._do_delete_many(entries))

    def _context_menu(self, pos) -> None:
        row = self._table.rowAt(pos.y())
        if row < 0 or row >= len(self._rows):
            return
        # Keep the clicked row in the selection so Delete Selected matches the menu.
        if not self._table.selectionModel().isRowSelected(row, self._table.rootIndex()):
            self._table.selectRow(row)
        entry = self._rows[row]
        menu = QMenu(self)
        edit_act = menu.addAction("Edit Item…")
        menu.addSeparator()
        selected = self._selected_entries()
        if len(selected) > 1:
            delete_act = menu.addAction(f"Delete Selected ({len(selected)})")
        else:
            delete_act = menu.addAction("Delete")
        try:
            delete_act.setIcon(qta.icon("mdi.delete-outline", color=_RED))
        except Exception:
            pass
        chosen = menu.exec(self._table.viewport().mapToGlobal(pos))
        if chosen == edit_act:
            if len(selected) == 1:
                asyncio.ensure_future(self._edit_mapping(entry))
            else:
                show_info(self, "Edit", "Select a single mapping to edit.")
        elif chosen == delete_act:
            self._delete_selected()

    def _on_double_click(self, index) -> None:
        row = index.row()
        if 0 <= row < len(self._rows):
            asyncio.ensure_future(self._edit_mapping(self._rows[row]))

    def _add_mapping(self) -> None:
        asyncio.ensure_future(self._do_add())

    async def _do_add(self) -> None:
        if not await self._ensure_categories():
            return
        dlg = _MappingEditorDialog(self._categories, parent=self)
        if dlg.exec() != QDialog.Accepted:
            return
        if not dlg.result_description or not dlg.result_category:
            return
        from tahmeed.services.description_mapping_service import save_mapping

        try:
            await save_mapping(
                dlg.result_description,
                dlg.result_category._id,
                dlg.result_category.name,
            )
        except Exception as exc:
            show_critical(self, "Error", f"Could not save mapping:\n{exc}")
            return
        await self._load()

    async def _edit_mapping(self, entry: DescriptionMapping) -> None:
        if not await self._ensure_categories():
            return
        dlg = _MappingEditorDialog(
            self._categories, parent=self, mapping=entry,
        )
        if dlg.exec() != QDialog.Accepted:
            return
        if not dlg.result_description or not dlg.result_category:
            return
        from tahmeed.services.description_mapping_service import save_mapping

        try:
            await save_mapping(
                dlg.result_description,
                dlg.result_category._id,
                dlg.result_category.name,
            )
        except Exception as exc:
            show_critical(self, "Error", f"Could not update mapping:\n{exc}")
            return
        await self._load()

    def _confirm_delete(self, entry: DescriptionMapping) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("Delete Mapping")
        box.setIcon(QMessageBox.Question)
        box.setText(
            f'Delete mapping for "{entry.description}"?\n\n'
            f"Item: {entry.category_name}"
        )
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.No)
        from tahmeed.ui.dialog_theme import style_message_box
        style_message_box(box)
        if box.exec() != QMessageBox.Yes:
            return
        asyncio.ensure_future(self._do_delete(entry))

    async def _do_delete(self, entry: DescriptionMapping) -> None:
        if not entry._id:
            show_warning(self, "Delete", "This mapping has no id and cannot be deleted.")
            return
        from tahmeed.services.description_mapping_service import delete_mapping

        try:
            await delete_mapping(entry._id)
        except Exception as exc:
            show_critical(self, "Error", f"Could not delete mapping:\n{exc}")
            return
        await self._load()

    async def _do_delete_many(self, entries: List[DescriptionMapping]) -> None:
        from tahmeed.services.description_mapping_service import delete_mapping

        failed = 0
        for entry in entries:
            if not entry._id:
                failed += 1
                continue
            try:
                await delete_mapping(entry._id)
            except Exception:
                failed += 1
        if failed:
            show_warning(
                self,
                "Delete",
                f"Deleted {len(entries) - failed:,} mapping(s); {failed:,} failed.",
            )
        await self._load()

    def _confirm_clear_all(self) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("Clear All Mappings")
        box.setIcon(QMessageBox.Warning)
        box.setText(
            "Delete ALL description → item mappings?\n\n"
            "Imports and Verify will ask you to map descriptions again.\n"
            "This cannot be undone."
        )
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.No)
        from tahmeed.ui.dialog_theme import style_message_box
        style_message_box(box)
        if box.exec() != QMessageBox.Yes:
            return
        asyncio.ensure_future(self._do_clear_all())

    async def _do_clear_all(self) -> None:
        from tahmeed.services.description_mapping_service import delete_all_mappings

        try:
            count = await delete_all_mappings()
        except Exception as exc:
            show_critical(self, "Error", f"Could not clear mappings:\n{exc}")
            return
        show_info(self, "Cleared", f"Deleted {count:,} mapping(s).")
        self._page = 0
        await self._load()
