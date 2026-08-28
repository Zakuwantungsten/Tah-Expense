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
from tahmeed.services.mapping_assignment_service import MappingAssignment
from tahmeed.ui.accountant.separate_expenses import (
    _finish_table_row, _stripe_bg, _table_style, _ROW_H,
)
from tahmeed.ui.dialog_theme import show_critical, show_info, show_warning
from tahmeed.ui.dialogs.item_dialog import ItemDialog
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
_SCROLL_CHUNK = 50


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
    """Add, edit, or bulk re-assign description → item maps.

    Pick an existing item, or open the same Add New Item dialog used on
    the Items tab.
    """

    def __init__(
        self,
        categories: List[Category],
        parent: Optional[QWidget] = None,
        *,
        mapping: Optional[DescriptionMapping] = None,
        mappings: Optional[List[DescriptionMapping]] = None,
    ) -> None:
        super().__init__(parent)
        self._categories = categories
        entries = list(mappings or [])
        if mapping is not None and not entries:
            entries = [mapping]
        self._entries = entries
        self._mapping = entries[0] if len(entries) == 1 else mapping
        self.result_description: Optional[str] = None
        self.result_category: Optional[Category] = None
        self.result_assignment: Optional[MappingAssignment] = None
        bulk = len(entries) > 1
        editing = bool(entries) and not bulk
        if bulk:
            title = f"Re-assign {len(entries):,} Mappings"
        elif editing:
            title = "Edit Mapping"
        else:
            title = "Add Mapping"
        self.setWindowTitle(title)
        self.setMinimumWidth(480)
        self.setStyleSheet(
            f"QDialog {{ background: {_WHITE}; color: {_T1}; }}"
            f"QLabel {{ color: {_T1}; background: transparent; border: none; }}"
        )
        self._build(editing=editing, bulk=bulk)

    def _build(self, *, editing: bool, bulk: bool) -> None:
        vl = QVBoxLayout(self)
        vl.setContentsMargins(24, 24, 24, 24)
        vl.setSpacing(12)

        if bulk:
            heading = f"Re-assign {len(self._entries):,} description maps"
        elif editing:
            heading = "Edit description → item"
        else:
            heading = "Add description → item"
        vl.addWidget(_lbl(heading, size=15, weight=700))

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
        if bulk:
            preview = [e.description for e in self._entries[:6]]
            extra = len(self._entries) - len(preview)
            lines = "\n".join(f"• {name}" for name in preview)
            if extra > 0:
                lines += f"\n• … and {extra:,} more"
            summary = _lbl(lines, size=12, color=_T2)
            summary.setWordWrap(True)
            vl.addWidget(_lbl(
                "These descriptions will all point at the same item:",
                size=12, color=_T2,
            ))
            vl.addWidget(summary)
            self._desc.hide()
        else:
            vl.addWidget(_lbl("Description *", size=12, color=_T2))
            if self._mapping is not None:
                self._desc.setText(self._mapping.description)
                self._desc.setEnabled(False)
                self._desc.setToolTip(
                    "Description key cannot be changed. Delete and re-add to rename."
                )
            vl.addWidget(self._desc)

        vl.addWidget(_lbl("Item / Supplier *", size=12, color=_T2))
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
        if self._mapping is not None and not bulk:
            idx = self._combo.findText(self._mapping.category_name)
            if idx >= 0:
                self._combo.setCurrentIndex(idx)
            else:
                self._combo.setEditText(self._mapping.category_name)
        else:
            self._combo.setCurrentIndex(-1)
        vl.addWidget(self._combo)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        cancel = _btn("Cancel", primary=False, height=32)
        cancel.setAutoDefault(False)
        cancel.setDefault(False)
        cancel.clicked.connect(self.reject)
        btn_row.addWidget(cancel)
        btn_row.addStretch()
        new_item = _btn("Assign to a New Item…", primary=False, height=32)
        new_item.setAutoDefault(False)
        new_item.setDefault(False)
        new_item.setToolTip(
            "Open the same Add New Item form used on the Items tab, "
            "then map the selected description(s) to it."
        )
        new_item.clicked.connect(self._on_assign_new)
        btn_row.addWidget(new_item)
        new_supplier = _btn("Assign to a New Supplier…", primary=False, height=32)
        new_supplier.setAutoDefault(False)
        new_supplier.setDefault(False)
        new_supplier.setToolTip(
            "Create a supplier payment target, then map the selected description(s) to it."
        )
        new_supplier.clicked.connect(self._on_assign_new_supplier)
        btn_row.addWidget(new_supplier)
        save_label = "Re-assign" if bulk else "Save"
        save = _btn(save_label, primary=True, height=32)
        save.setAutoDefault(True)
        save.setDefault(True)
        save.clicked.connect(self._accept)
        btn_row.addWidget(save)
        vl.addLayout(btn_row)

        if self._mapping is None and not bulk:
            self._desc.setFocus()
        else:
            self._combo.setFocus()

    def _descriptions(self) -> List[str]:
        if self._entries:
            return [e.description for e in self._entries]
        desc = self._desc.text().strip()
        return [desc] if desc else []

    def _prefill_item_name(self) -> str:
        if len(self._entries) == 1:
            return self._entries[0].description
        if not self._entries:
            return self._desc.text().strip()
        return ""

    def _on_assign_new(self) -> None:
        if not self._entries:
            desc = self._desc.text().strip()
            if not desc:
                show_warning(self, "Validation", "Description is required.")
                return
        dlg = ItemDialog(parent=self, prefill_name=self._prefill_item_name())
        if dlg.exec() != QDialog.Accepted:
            return
        data = dict(dlg.result_data or {})
        name = (data.get("name") or "").strip()
        if not name:
            show_warning(self, "New Item", "Item name is required.")
            return
        existing = next(
            (c for c in self._categories if c.name.strip().lower() == name.lower()),
            None,
        )
        descriptions = self._descriptions()
        self.result_description = descriptions[0] if descriptions else name
        self.result_category = existing or Category(name=name)
        self.result_assignment = MappingAssignment(
            action="assign",
            description=self.result_description or "",
            category=self.result_category,
            create_new=existing is None,
            new_item_name=name,
            new_item_fields=data,
        )
        self.accept()

    def _on_assign_new_supplier(self) -> None:
        if not self._entries:
            desc = self._desc.text().strip()
            if not desc:
                show_warning(self, "Validation", "Description is required.")
                return
        from tahmeed.ui.accountant.manage_suppliers import _SupplierDialog

        dlg = _SupplierDialog(parent=self)
        if dlg.exec() != QDialog.Accepted:
            return
        data = dict(dlg.result_data or {})
        name = (data.get("name") or "").strip()
        if not name:
            show_warning(self, "New Supplier", "Supplier name is required.")
            return
        existing = next(
            (c for c in self._categories if c.name.strip().lower() == name.lower()),
            None,
        )
        descriptions = self._descriptions()
        self.result_description = descriptions[0] if descriptions else name
        self.result_category = existing or Category(name=name, is_supplier=True)
        self.result_assignment = MappingAssignment(
            action="assign",
            description=self.result_description or "",
            category=self.result_category,
            create_new=existing is None,
            new_item_name=name,
            new_item_fields=data,
        )
        self.accept()

    def _accept(self) -> None:
        descriptions = self._descriptions()
        if not descriptions:
            show_warning(self, "Validation", "Description is required.")
            return
        name = self._combo.currentText().strip()
        if not name:
            show_warning(self, "Validation", "Please choose an item.")
            return
        idx = self._combo.findText(name)
        cat_id = self._combo.itemData(idx) if idx >= 0 else None
        match = next((c for c in self._categories if c.name == name), None)
        if match is None:
            match = next(
                (c for c in self._categories
                 if c.name.strip().lower() == name.lower()),
                None,
            )
        if cat_id is None and match is None:
            show_warning(
                self,
                "Validation",
                "Please pick an existing item from the Items list, "
                "or click Assign to a New Item.",
            )
            return
        category = match or Category(_id=cat_id, name=name)
        self.result_description = descriptions[0]
        self.result_category = category
        self.result_assignment = MappingAssignment(
            action="assign",
            description=descriptions[0],
            category=category,
            create_new=False,
        )
        self.accept()


class DescriptionMapsWidget(QWidget):
    """Browse / add / edit / delete description → item mappings."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._rows: List[DescriptionMapping] = []
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
            "Select rows to re-assign or delete · right-click for more",
            size=11, color=_TM,
        )
        hdr.addWidget(hint)
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

        self._reassign_btn = _btn(
            "Re-assign Selected", icon="mdi.swap-horizontal", primary=False, height=32,
        )
        self._reassign_btn.setEnabled(False)
        self._reassign_btn.setToolTip(
            "Map the selected description(s) to an existing item or a new item"
        )
        self._reassign_btn.clicked.connect(self._reassign_selected)
        toolbar.addWidget(self._reassign_btn)

        self._delete_btn = _btn(
            "Delete Selected", icon="mdi.delete-outline", primary=False, danger=True, height=32,
        )
        self._delete_btn.setEnabled(False)
        self._delete_btn.setToolTip("Delete the selected mapping(s)")
        self._delete_btn.clicked.connect(self._delete_selected)
        toolbar.addWidget(self._delete_btn)

        clear_btn = _btn("Clear All", primary=False, height=32)
        clear_btn.setToolTip("Delete every description → item mapping")
        clear_btn.clicked.connect(self._confirm_clear_all)
        toolbar.addWidget(clear_btn)

        add_btn = _btn("+ Add Mapping", primary=True, height=32)
        add_btn.clicked.connect(self._add_mapping)
        toolbar.addWidget(add_btn)
        root.addLayout(toolbar)

        self._table_host = QFrame()
        self._table_host.setStyleSheet(
            "QFrame { background: transparent; border: none; }"
        )
        table_vl = QVBoxLayout(self._table_host)
        table_vl.setContentsMargins(0, 0, 0, 0)
        table_vl.setSpacing(0)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["#", "Description", "Item / Supplier"])
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
        self._table.verticalScrollBar().valueChanged.connect(self._on_scroll)

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

        self._footer = _lbl("", size=11, color=_TM)
        self._footer.setAlignment(Qt.AlignCenter)
        root.addWidget(self._footer)

    def refresh(self) -> None:
        self._reset_and_load()

    def _on_search_changed(self) -> None:
        self._search_debounce.start()

    def _on_search_commit(self) -> None:
        self._reset_and_load()

    def _reset_and_load(self) -> None:
        asyncio.ensure_future(self._load_initial())

    def _update_status(self) -> None:
        loaded = len(self._rows)
        if self._loading:
            suffix = "  •  Loading…"
        elif loaded >= self._total:
            suffix = ""
        else:
            suffix = "  •  Scroll down for more"
        self._footer.setText(f"Showing {loaded:,} of {self._total:,}{suffix}")
        self._count_chip.setText(f"{self._total:,} maps")

    def _on_scroll(self, value: int) -> None:
        bar = self._table.verticalScrollBar()
        if bar.maximum() <= 0:
            return
        if value >= bar.maximum() - 24:
            asyncio.ensure_future(self._load_more())

    def _fill_if_needed(self) -> None:
        bar = self._table.verticalScrollBar()
        if not self._loading and len(self._rows) < self._total and bar.maximum() <= 0:
            asyncio.ensure_future(self._load_more())

    async def _ensure_categories(self, *, refresh: bool = False) -> bool:
        if self._categories and not refresh:
            return True
        from tahmeed.services.category_service import get_payment_target_categories

        try:
            self._categories = await get_payment_target_categories()
        except Exception as exc:
            show_critical(self, "Error", f"Could not load items:\n{exc}")
            return False
        return True

    async def _load_initial(self) -> None:
        if self._loading:
            return
        self._loading = True
        self._loading_overlay.show_loading("Loading mappings…")
        self._update_status()
        try:
            from tahmeed.services.description_mapping_service import (
                count_mappings, list_mappings,
            )

            search = self._search.text().strip()
            rows, total = await asyncio.gather(
                list_mappings(search=search, limit=_SCROLL_CHUNK, skip=0),
                count_mappings(search=search),
            )
            self._rows = rows
            self._total = total
            self._rebuild_table()
        except Exception as exc:
            show_critical(self, "Error", f"Could not load mappings:\n{exc}")
        finally:
            self._loading = False
            self._loading_overlay.hide_loading()
            self._update_status()
            self._fill_if_needed()

    async def _load_more(self) -> None:
        if self._loading or len(self._rows) >= self._total:
            return
        self._loading = True
        self._update_status()
        try:
            from tahmeed.services.description_mapping_service import list_mappings

            search = self._search.text().strip()
            rows = await list_mappings(
                search=search, limit=_SCROLL_CHUNK, skip=len(self._rows),
            )
        except Exception as exc:
            self._loading = False
            self._update_status()
            show_critical(self, "Error", f"Could not load more mappings:\n{exc}")
            return
        if rows:
            start = len(self._rows)
            self._rows.extend(rows)
            self._append_rows(rows, start)
        self._loading = False
        self._update_status()
        self._fill_if_needed()

    def _rebuild_table(self) -> None:
        self._table.setRowCount(0)
        self._append_rows(self._rows, 0)
        self._on_selection_changed()

    def _append_rows(
        self, rows: List[DescriptionMapping], start_index: int,
    ) -> None:
        for i, row in enumerate(rows):
            idx = start_index + i
            self._table.insertRow(idx)
            row_bg = _stripe_bg(idx)
            self._table.setItem(
                idx, 0,
                _cell(str(idx + 1), Qt.AlignCenter | Qt.AlignVCenter, bg=row_bg),
            )
            self._table.setItem(idx, 1, _cell(row.description, bg=row_bg))
            self._table.setItem(idx, 2, _cell(row.category_name, bg=row_bg))
            _finish_table_row(self._table, idx, row_bg)

    async def _load(self) -> None:
        await self._load_initial()

    def _on_selection_changed(self) -> None:
        selected = self._selected_entries()
        n = len(selected)
        self._delete_btn.setEnabled(n > 0)
        self._reassign_btn.setEnabled(n > 0)
        if n <= 1:
            self._delete_btn.setText("  Delete Selected")
            self._reassign_btn.setText("  Re-assign Selected")
        else:
            self._delete_btn.setText(f"  Delete Selected ({n})")
            self._reassign_btn.setText(f"  Re-assign Selected ({n})")

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
        selected = self._selected_entries()
        if len(selected) > 1:
            reassign_act = menu.addAction(f"Re-assign Selected ({len(selected)})…")
            edit_act = None
        else:
            reassign_act = None
            edit_act = menu.addAction("Edit Item…")
        try:
            act = reassign_act or edit_act
            if act is not None:
                act.setIcon(qta.icon("mdi.swap-horizontal", color=_T1))
        except Exception:
            pass
        menu.addSeparator()
        if len(selected) > 1:
            delete_act = menu.addAction(f"Delete Selected ({len(selected)})")
        else:
            delete_act = menu.addAction("Delete")
        try:
            delete_act.setIcon(qta.icon("mdi.delete-outline", color=_RED))
        except Exception:
            pass
        chosen = menu.exec(self._table.viewport().mapToGlobal(pos))
        if reassign_act is not None and chosen == reassign_act:
            self._reassign_selected()
        elif edit_act is not None and chosen == edit_act:
            asyncio.ensure_future(self._edit_mapping(entry))
        elif chosen == delete_act:
            self._delete_selected()

    def _on_double_click(self, index) -> None:
        row = index.row()
        if 0 <= row < len(self._rows):
            asyncio.ensure_future(self._edit_mapping(self._rows[row]))

    def _add_mapping(self) -> None:
        asyncio.ensure_future(self._do_add())

    def _reassign_selected(self) -> None:
        entries = self._selected_entries()
        if not entries:
            show_info(self, "Re-assign", "Select one or more mappings to re-assign.")
            return
        asyncio.ensure_future(self._do_reassign(entries))

    async def _do_add(self) -> None:
        if not await self._ensure_categories(refresh=True):
            return
        dlg = _MappingEditorDialog(self._categories, parent=self)
        if dlg.exec() != QDialog.Accepted:
            return
        if not dlg.result_assignment or not dlg.result_description:
            return
        await self._save_assignment([dlg.result_description], dlg.result_assignment)

    async def _edit_mapping(self, entry: DescriptionMapping) -> None:
        await self._do_reassign([entry])

    async def _do_reassign(self, entries: List[DescriptionMapping]) -> None:
        if not await self._ensure_categories(refresh=True):
            return
        dlg = _MappingEditorDialog(
            self._categories, parent=self, mappings=entries,
        )
        if dlg.exec() != QDialog.Accepted:
            return
        if not dlg.result_assignment:
            return
        descriptions = [e.description for e in entries]
        if len(entries) > 1:
            name = dlg.result_assignment.item_name
            box = QMessageBox(self)
            box.setWindowTitle("Re-assign Mappings")
            box.setIcon(QMessageBox.Question)
            box.setText(
                f'Re-assign {len(entries):,} mapping(s) to "{name}"?'
            )
            box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
            box.setDefaultButton(QMessageBox.Yes)
            from tahmeed.ui.dialog_theme import style_message_box
            style_message_box(box)
            if box.exec() != QMessageBox.Yes:
                return
        await self._save_assignment(descriptions, dlg.result_assignment)

    async def _save_assignment(
        self,
        descriptions: List[str],
        assignment: MappingAssignment,
    ) -> None:
        from tahmeed.services.mapping_assignment_service import (
            apply_assignment_to_descriptions,
        )

        bulk = len(descriptions) > 1
        self._loading_overlay.show_loading(
            "Re-assigning mappings…" if bulk else "Saving mapping…"
        )
        try:
            chosen, failed = await apply_assignment_to_descriptions(
                descriptions, assignment, self._categories,
            )
        except Exception as exc:
            self._loading_overlay.hide_loading()
            show_critical(self, "Error", f"Could not save mapping:\n{exc}")
            return
        self._loading_overlay.hide_loading()
        saved = len(descriptions) - failed
        if failed:
            show_warning(
                self,
                "Re-assign",
                f"Updated {saved:,} mapping(s); {failed:,} failed.",
            )
        elif bulk:
            show_info(
                self,
                "Re-assigned",
                f'Re-assigned {saved:,} mapping(s) to "{chosen.name}".',
            )
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
        await self._load()
