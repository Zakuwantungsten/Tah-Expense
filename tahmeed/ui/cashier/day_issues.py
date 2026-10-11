"""Day Issues — bulk map unmapped descriptions → Payee + Item.

Mirrors the QuickBooks Expense Automation issues tab (without Ignore):
multi-select descriptions, assign one Payee + Item, apply to open rows.
"""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Callable, List, Optional, Sequence, Tuple

import qtawesome as qta
from bson import ObjectId
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QBrush
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QCompleter, QDialog, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QStyledItemDelegate,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
    QDateEdit,
)
from PySide6.QtCore import QDate

from tahmeed.ui.widgets.completer_line_edit import CompleterLineEdit

from tahmeed.models.category import Category
from tahmeed.models.user import User
from tahmeed.services.category_service import get_payment_target_categories, sort_payment_targets
from tahmeed.services.day_issues_service import (
    DayIssue,
    DayIssuesSnapshot,
    as_of_label,
    collect_day_issues,
    collect_day_issues_all_days,
    filter_rows_for_cashier_scope,
)
from tahmeed.services.description_mapping_service import (
    normalize_description,
    save_mapping,
)
from tahmeed.services.mapping_assignment_service import (
    MappingAssignment,
    materialize_mapping_assignment,
    remember_category,
)
from tahmeed.services.payee_service import get_payee_names
from tahmeed.ui.accountant.date_filters import style_calendar_popup
from tahmeed.ui.accountant.separate_expenses import (
    _finish_table_row, _stripe_bg, _table_style, _ROW_H,
)
from tahmeed.ui.dialogs.item_dialog import ItemDialog
from tahmeed.ui.widgets.loading_overlay import LoadingOverlay

_WHITE   = "#FFFFFF"
_BG      = "#F4F6F8"
_BORDER  = "#E5E7EB"
_BLUE    = "#0077C5"
_BLUE_L  = "#E8F4FD"
_GOLD    = "#B18E5E"
_AMBER   = "#D97706"
_AMBER_L = "#FEF3C7"
_RED     = "#DC2626"
_RED_L   = "#FEE2E2"
_T1      = "#111827"
_T2      = "#6B7280"
_TM      = "#9CA3AF"
_NAVY    = "#1B2B4B"

_COL_CHECK = 0
_COL_NUM = 1
_COL_KIND = 2
_COL_DESC = 3
_COL_ROWS = 4
_COL_PAYEE = 5
_COL_ITEM = 6
_COL_DETAIL = 7
_COL_DEFAULTS = [36, 40, 90, 250, 55, 140, 140, 200]


def filter_day_issues(
    issues: Sequence[DayIssue],
    *,
    search: str = "",
    value_filter: Optional[set] = None,
) -> List[DayIssue]:
    """Apply search + optional VALUE multi-select (description keys)."""
    needle = (search or "").strip().lower()
    out: List[DayIssue] = []
    for issue in issues:
        if value_filter is not None and issue.description_key not in value_filter:
            continue
        if needle and needle not in (issue.description or "").lower():
            continue
        out.append(issue)
    return out


def map_target_issues(
    visible: Sequence[DayIssue],
    *,
    checked_keys: set,
    selected_keys: set,
) -> List[DayIssue]:
    """Union of checked and highlighted (selected) rows among *visible*."""
    wanted = checked_keys | selected_keys
    if not wanted:
        return []
    by_key = {i.description_key: i for i in visible}
    return [by_key[k] for k in by_key if k in wanted]


def _lbl(text: str = "", size: int = 13, weight: int = 400,
         color: str = _T1) -> QLabel:
    w = QLabel(text)
    w.setStyleSheet(
        f"color:{color};font-size:{size}px;font-weight:{weight};"
        "font-family:'Segoe UI';background:transparent;"
    )
    return w


def _btn(text: str, *, primary: bool = True, height: int = 32) -> QPushButton:
    b = QPushButton(text)
    b.setCursor(Qt.PointingHandCursor)
    b.setFixedHeight(height)
    if primary:
        b.setStyleSheet(
            f"QPushButton{{background:{_BLUE};color:#FFF;border:none;border-radius:5px;"
            f"font-size:12px;font-weight:600;font-family:'Segoe UI';padding:0 14px;}}"
            f"QPushButton:hover{{background:#005EA3;}}"
            f"QPushButton:disabled{{background:#93C5FD;}}"
        )
    else:
        b.setStyleSheet(
            f"QPushButton{{background:{_WHITE};color:{_T1};border:1px solid {_BORDER};"
            f"border-radius:5px;font-size:12px;font-family:'Segoe UI';padding:0 14px;}}"
            f"QPushButton:hover{{background:{_BG};}}"
            f"QPushButton:disabled{{color:{_TM};}}"
        )
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


def _editable_cell(text: str, *, bg: str = "") -> QTableWidgetItem:
    display = "" if text in (None, "—") else str(text)
    item = QTableWidgetItem(display)
    item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    item.setFont(QFont("Segoe UI", 11))
    if bg:
        item.setBackground(QBrush(QColor(bg)))
    item.setFlags(
        Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable
    )
    return item


class _IssuesCompleterDelegate(QStyledItemDelegate):
    """Inline Payee / Item editor with contains completer."""

    def __init__(self, suggestions_getter, parent=None, *, uppercase: bool = False):
        super().__init__(parent)
        self._suggestions_getter = suggestions_getter
        self._uppercase = uppercase

    def createEditor(self, parent, option, index):
        ed = CompleterLineEdit(
            self._suggestions_getter() or [],
            parent=parent,
            ranked_contains=True,
        )
        ed.setStyleSheet("QLineEdit { color: #111827; background: #ffffff; }")
        return ed

    def setEditorData(self, editor, index):
        editor.setText(index.data() or "")
        editor.selectAll()

    def setModelData(self, editor, model, index):
        text = (editor.canonical(editor.text()) or editor.text() or "").strip()
        if self._uppercase:
            text = " ".join(text.upper().split())
        model.setData(index, text)

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect)


class MapDescriptionsDialog(QDialog):
    """Map one or many descriptions → the same Payee + Item."""

    def __init__(
        self,
        descriptions: Sequence[str],
        categories: List[Category],
        payees: Sequence[str],
        parent: Optional[QWidget] = None,
        *,
        prefill_payee: str = "",
        prefill_item: str = "",
    ) -> None:
        super().__init__(parent)
        self._descriptions = [d for d in descriptions if str(d).strip()]
        self._categories = sort_payment_targets(list(categories))
        self._payees = [str(p).strip().upper() for p in payees if str(p).strip()]
        self.result_payee: str = ""
        self.result_category: Optional[Category] = None
        self.result_assignment: Optional[MappingAssignment] = None
        n = len(self._descriptions)
        self.setWindowTitle(
            f"Map {n} Description{'s' if n != 1 else ''}"
            if n else "Map Description"
        )
        self.setMinimumWidth(520)
        self.setStyleSheet(f"QDialog{{background:{_WHITE};color:{_T1};}}")
        self._build(prefill_payee=prefill_payee, prefill_item=prefill_item)

    def _build(self, *, prefill_payee: str, prefill_item: str) -> None:
        vl = QVBoxLayout(self)
        vl.setContentsMargins(24, 24, 24, 24)
        vl.setSpacing(12)

        n = len(self._descriptions)
        vl.addWidget(_lbl(
            f"Map {n} description{'s' if n != 1 else ''} → Payee + Item",
            size=15, weight=700,
        ))
        vl.addWidget(_lbl(
            "Type to search. All selected descriptions get the same Payee and Item.",
            size=12, color=_T2,
        ))

        preview = self._descriptions[:8]
        extra = n - len(preview)
        lines = "\n".join(f"• {name}" for name in preview)
        if extra > 0:
            lines += f"\n• … and {extra:,} more"
        summary = _lbl(lines, size=12, color=_T2)
        summary.setWordWrap(True)
        vl.addWidget(summary)

        vl.addWidget(_lbl("PAYEE *", size=12, color=_T2))
        self._payee = QComboBox()
        self._payee.setEditable(True)
        self._payee.setInsertPolicy(QComboBox.NoInsert)
        self._payee.setFixedHeight(34)
        self._payee.addItems(self._payees)
        payee_completer = QCompleter(self._payees, self)
        payee_completer.setCaseSensitivity(Qt.CaseInsensitive)
        payee_completer.setFilterMode(Qt.MatchContains)
        self._payee.setCompleter(payee_completer)
        self._payee.setStyleSheet(
            f"QComboBox{{border:1px solid {_BORDER};border-radius:5px;"
            f"background:{_WHITE};color:{_T1};font-size:12px;"
            f"font-family:'Segoe UI';padding:0 8px;}}"
            f"QComboBox:focus{{border-color:{_BLUE};}}"
        )
        if prefill_payee:
            idx = self._payee.findText(prefill_payee.upper())
            if idx >= 0:
                self._payee.setCurrentIndex(idx)
            else:
                self._payee.setEditText(prefill_payee.upper())
        else:
            self._payee.setCurrentIndex(-1)
            self._payee.setEditText("")
        vl.addWidget(self._payee)

        vl.addWidget(_lbl("ITEM *", size=12, color=_T2))
        self._item = QComboBox()
        self._item.setEditable(True)
        self._item.setInsertPolicy(QComboBox.NoInsert)
        self._item.setFixedHeight(34)
        for cat in self._categories:
            self._item.addItem(cat.name, cat._id)
        item_completer = QCompleter([c.name for c in self._categories], self)
        item_completer.setCaseSensitivity(Qt.CaseInsensitive)
        item_completer.setFilterMode(Qt.MatchContains)
        self._item.setCompleter(item_completer)
        self._item.setStyleSheet(
            f"QComboBox{{border:1px solid {_BORDER};border-radius:5px;"
            f"background:{_WHITE};color:{_T1};font-size:12px;"
            f"font-family:'Segoe UI';padding:0 8px;}}"
            f"QComboBox:focus{{border-color:{_BLUE};}}"
        )
        if prefill_item:
            idx = self._item.findText(prefill_item)
            if idx >= 0:
                self._item.setCurrentIndex(idx)
            else:
                self._item.setEditText(prefill_item)
        else:
            self._item.setCurrentIndex(-1)
            self._item.setEditText("")
        vl.addWidget(self._item)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        cancel = _btn("Cancel", primary=False)
        cancel.clicked.connect(self.reject)
        btn_row.addWidget(cancel)
        btn_row.addStretch()
        new_item = _btn("New Item…", primary=False)
        new_item.clicked.connect(self._on_new_item)
        btn_row.addWidget(new_item)
        save = _btn("Save Mapping", primary=True)
        save.clicked.connect(self._accept)
        btn_row.addWidget(save)
        vl.addLayout(btn_row)

        self._payee.setFocus()

    def _on_new_item(self) -> None:
        prefill = self._descriptions[0] if len(self._descriptions) == 1 else ""
        dlg = ItemDialog(parent=self, prefill_name=prefill)
        if dlg.exec() != QDialog.Accepted:
            return
        data = dict(dlg.result_data or {})
        name = (data.get("name") or "").strip()
        if not name:
            QMessageBox.warning(self, "New Item", "Item name is required.")
            return
        existing = next(
            (c for c in self._categories if c.name.strip().lower() == name.lower()),
            None,
        )
        self.result_payee = " ".join(self._payee.currentText().upper().split())
        self.result_category = existing or Category(name=name)
        self.result_assignment = MappingAssignment(
            action="assign",
            description=self._descriptions[0] if self._descriptions else name,
            category=self.result_category,
            create_new=existing is None,
            new_item_name=name,
            new_item_fields=data,
        )
        if not self.result_payee:
            QMessageBox.warning(self, "Validation", "Payee is required.")
            return
        self.accept()

    def _accept(self) -> None:
        payee = " ".join(self._payee.currentText().upper().split())
        item_name = self._item.currentText().strip()
        if not self._descriptions:
            QMessageBox.warning(self, "Validation", "Select at least one description.")
            return
        if not payee:
            QMessageBox.warning(self, "Validation", "Payee is required.")
            return
        if not item_name:
            QMessageBox.warning(self, "Validation", "Item is required.")
            return
        if self._payees and payee not in {p.upper() for p in self._payees}:
            tip = next((p for p in self._payees if payee in p or p in payee), "")
            extra = f"\nDid you mean: {tip}?" if tip else ""
            if QMessageBox.question(
                self,
                "Payee not in list",
                f"Payee {payee!r} was not found in the Payee list.{extra}\n\n"
                "Save anyway?",
            ) != QMessageBox.Yes:
                return
        idx = self._item.findText(item_name)
        cat_id = self._item.itemData(idx) if idx >= 0 else None
        cat = next(
            (c for c in self._categories if c.name.strip().lower() == item_name.lower()),
            None,
        )
        if cat is None and cat_id is not None:
            cat = Category(name=item_name, _id=ObjectId(str(cat_id)))
        if cat is None:
            QMessageBox.warning(
                self,
                "Select Item",
                "Choose an existing item, or click New Item… to create one.",
            )
            return
        self.result_payee = payee
        self.result_category = cat
        self.result_assignment = MappingAssignment(
            action="assign",
            description=self._descriptions[0],
            category=cat,
            create_new=False,
            new_item_name="",
        )
        self.accept()


class ValueFilterDialog(QDialog):
    """Multi-select filter for Unmapped / Bad map description values."""

    def __init__(
        self,
        issues: Sequence[DayIssue],
        selected_keys: Optional[set] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Filter VALUE")
        self.setMinimumWidth(420)
        self.setMinimumHeight(360)
        self.setStyleSheet(f"QDialog{{background:{_WHITE};color:{_T1};}}")
        self.result_keys: Optional[set] = None
        self._build(issues, selected_keys or set())

    def _build(self, issues: Sequence[DayIssue], selected_keys: set) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(10)
        root.addWidget(_lbl("Filter by description (VALUE)", size=15, weight=700))
        root.addWidget(_lbl(
            "Unmapped first, then Bad map. Clear filter shows all.",
            size=12, color=_T2,
        ))

        self._list = QListWidget()
        self._list.setStyleSheet(
            f"QListWidget{{border:1px solid {_BORDER};border-radius:5px;"
            f"background:{_WHITE};font-size:12px;font-family:'Segoe UI';}}"
        )
        ordered = sorted(
            issues,
            key=lambda i: (0 if i.kind == "Unmapped" else 1, i.description.lower()),
        )
        for issue in ordered:
            label = f"[{issue.kind}] {issue.description}  ({issue.count})"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, issue.description_key)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(
                Qt.Checked if issue.description_key in selected_keys else Qt.Unchecked
            )
            self._list.addItem(item)
        root.addWidget(self._list, 1)

        row = QHBoxLayout()
        sel_all = _btn("Select All", primary=False)
        sel_all.clicked.connect(lambda: self._set_all(True))
        row.addWidget(sel_all)
        clear = _btn("Clear", primary=False)
        clear.clicked.connect(lambda: self._set_all(False))
        row.addWidget(clear)
        row.addStretch()
        cancel = _btn("Cancel", primary=False)
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        apply_btn = _btn("Apply Filter", primary=True)
        apply_btn.clicked.connect(self._accept)
        row.addWidget(apply_btn)
        root.addLayout(row)

    def _set_all(self, checked: bool) -> None:
        state = Qt.Checked if checked else Qt.Unchecked
        for i in range(self._list.count()):
            self._list.item(i).setCheckState(state)

    def _accept(self) -> None:
        keys = set()
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.checkState() == Qt.Checked:
                keys.add(item.data(Qt.UserRole))
        self.result_keys = keys
        self.accept()


class DayIssuesWidget(QWidget):
    """Cashier / accountant Day Issues page for one register date."""

    issues_changed = Signal(int)   # badge count
    open_register = Signal(object)  # date
    mappings_applied = Signal()     # refresh register rows

    def __init__(
        self,
        user: User,
        parent: Optional[QWidget] = None,
        *,
        row_source: Optional[Callable[[date], Tuple[Optional[list], Optional[list]]]] = None,
        apply_live: Optional[Callable[..., int]] = None,
    ) -> None:
        super().__init__(parent)
        self._user = user
        self._target: date = date.today()
        self._scope: str = "day"  # "day" | "all"
        self._issues_all: List[DayIssue] = []
        self._issues: List[DayIssue] = []
        self._issue_dates: dict = {}  # description_key -> list[date]
        self._days_scanned: List[date] = []
        self._categories: List[Category] = []
        self._payees: List[str] = []
        # description_key -> {"payee": str, "item": str} inline drafts (Phase 6)
        self._mapping_drafts: dict = {}
        self._checked_keys: set = set()
        self._search_text: str = ""
        self._value_filter: Optional[set] = None  # description keys; None = all
        self._loading = False
        self._populating = False
        # Optional: (replace_transactions, extra_transactions) for live/unsaved rows.
        self._row_source = row_source
        # Optional: stamp Payee+Item onto live Table / draft rows (Phase 3).
        self._apply_live = apply_live
        # Cashier UI: own rows only. Accountant / admin: everyone.
        self._cashier_scope_id: Optional[ObjectId] = (
            user._id if getattr(user, "role", "") == "cashier" else None
        )
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        hdr = QHBoxLayout()
        hdr.setSpacing(10)
        try:
            icon_lbl = QLabel()
            icon_lbl.setPixmap(
                qta.icon("mdi.alert-decagram-outline", color=_AMBER).pixmap(24, 24)
            )
            icon_lbl.setFixedSize(24, 24)
            icon_lbl.setStyleSheet("background:transparent;")
            hdr.addWidget(icon_lbl)
        except Exception:
            pass
        hdr.addWidget(_lbl("Day Issues", size=18, weight=700))

        self._count_chip = QLabel("—")
        self._count_chip.setStyleSheet(
            f"background:{_AMBER_L};color:{_AMBER};font-size:11px;font-weight:700;"
            "border-radius:10px;padding:2px 10px;font-family:'Segoe UI';"
        )
        hdr.addWidget(self._count_chip)
        hdr.addStretch()

        hdr.addWidget(_lbl("SHOW", size=12, color=_T2))
        self._scope_combo = QComboBox()
        self._scope_combo.addItem("This day", "day")
        self._scope_combo.addItem("All days", "all")
        self._scope_combo.setFixedHeight(32)
        self._scope_combo.setFixedWidth(110)
        self._scope_combo.setStyleSheet(
            f"QComboBox{{border:1px solid {_BORDER};border-radius:5px;"
            f"background:{_WHITE};color:{_T1};font-size:12px;"
            f"font-family:'Segoe UI';padding:0 8px;}}"
        )
        self._scope_combo.currentIndexChanged.connect(self._on_scope_changed)
        hdr.addWidget(self._scope_combo)

        hdr.addWidget(_lbl("Date", size=12, color=_T2))
        self._date = QDateEdit()
        self._date.setCalendarPopup(True)
        self._date.setDisplayFormat("dd MMM yyyy")
        self._date.setDate(QDate.currentDate())
        self._date.setFixedHeight(32)
        self._date.setFixedWidth(140)
        self._date.setStyleSheet(
            f"QDateEdit{{border:1px solid {_BORDER};border-radius:5px;"
            f"background:{_WHITE};color:{_T1};font-size:12px;"
            f"font-family:'Segoe UI';padding:0 8px;}}"
        )
        style_calendar_popup(self._date)
        self._date.dateChanged.connect(self._on_date_changed)
        hdr.addWidget(self._date)

        refresh = _btn("Refresh", primary=False)
        refresh.clicked.connect(lambda: self._schedule_refresh())
        hdr.addWidget(refresh)

        self._open_reg_btn = _btn("Open Table", primary=False)
        self._open_reg_btn.clicked.connect(
            lambda: self.open_register.emit(self._target)
        )
        hdr.addWidget(self._open_reg_btn)
        root.addLayout(hdr)

        scope_note = (
            "Showing your incomplete rows only."
            if self._cashier_scope_id is not None
            else "Showing incomplete rows for all cashiers."
        )
        hint = _lbl(
            f"{scope_note} Double-click Payee / Item to fill inline, then "
            "Save Filled Mappings — or Map Selected for a dialog. Submit is "
            "blocked while issues remain (unless the accountant allows it).",
            size=12, color=_T2,
        )
        hint.setWordWrap(True)
        root.addWidget(hint)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self._map_btn = _btn("Map Selected", primary=True)
        self._map_btn.clicked.connect(self._map_selected)
        actions.addWidget(self._map_btn)
        self._map_all_btn = _btn("Map All Unmapped…", primary=False)
        self._map_all_btn.clicked.connect(self._map_all_unmapped)
        actions.addWidget(self._map_all_btn)
        self._save_filled_btn = _btn("Save Filled Mappings", primary=True)
        self._save_filled_btn.clicked.connect(self._schedule_save_filled)
        self._save_filled_btn.setEnabled(False)
        actions.addWidget(self._save_filled_btn)
        check_hl = _btn("Check Highlighted", primary=False)
        check_hl.clicked.connect(self._check_highlighted)
        actions.addWidget(check_hl)
        clear_chk = _btn("Clear Checks", primary=False)
        clear_chk.clicked.connect(self._clear_checks)
        actions.addWidget(clear_chk)
        actions.addStretch()
        self._status = _lbl("", size=12, color=_T2)
        actions.addWidget(self._status)
        root.addLayout(actions)

        search_row = QHBoxLayout()
        search_row.setSpacing(8)
        search_row.addWidget(_lbl("SEARCH", size=12, color=_T2))
        self._search = QLineEdit()
        self._search.setPlaceholderText("Filter descriptions…")
        self._search.setFixedHeight(32)
        self._search.setClearButtonEnabled(True)
        self._search.setStyleSheet(
            f"QLineEdit{{border:1px solid {_BORDER};border-radius:5px;"
            f"background:{_WHITE};color:{_T1};font-size:12px;"
            f"font-family:'Segoe UI';padding:0 8px;}}"
            f"QLineEdit:focus{{border-color:{_BLUE};}}"
        )
        self._search.textChanged.connect(self._on_search_changed)
        search_row.addWidget(self._search, 1)
        self._filter_btn = _btn("Filter VALUE…", primary=False)
        self._filter_btn.clicked.connect(self._open_value_filter)
        search_row.addWidget(self._filter_btn)
        self._clear_filter_btn = _btn("Clear VALUE Filter", primary=False)
        self._clear_filter_btn.clicked.connect(self._clear_value_filter)
        self._clear_filter_btn.setEnabled(False)
        search_row.addWidget(self._clear_filter_btn)
        root.addLayout(search_row)

        self._table_host = QFrame()
        self._table_host.setStyleSheet("QFrame{background:transparent;border:none;}")
        tv = QVBoxLayout(self._table_host)
        tv.setContentsMargins(0, 0, 0, 0)

        self._table = QTableWidget(0, 8)
        self._table.setHorizontalHeaderLabels(
            ["", "#", "Type", "Description", "Rows", "Payee", "Item", "Detail"]
        )
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._table.setEditTriggers(
            QAbstractItemView.DoubleClicked
            | QAbstractItemView.EditKeyPressed
            | QAbstractItemView.AnyKeyPressed
        )
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(_ROW_H)
        self._table.setStyleSheet(_table_style())
        self._table.setItemDelegateForColumn(
            _COL_PAYEE,
            _IssuesCompleterDelegate(
                lambda: list(self._payees), self._table, uppercase=True
            ),
        )
        self._table.setItemDelegateForColumn(
            _COL_ITEM,
            _IssuesCompleterDelegate(
                lambda: [c.name for c in self._categories],
                self._table,
                uppercase=False,
            ),
        )
        self._table.itemChanged.connect(self._on_issue_cell_changed)
        hdr_view = self._table.horizontalHeader()
        hdr_view.setStretchLastSection(True)
        for i, width in enumerate(_COL_DEFAULTS):
            self._table.setColumnWidth(i, width)
        tv.addWidget(self._table)
        root.addWidget(self._table_host, 1)

        self._loading_overlay = LoadingOverlay(self._table_host, "Loading…")
        self._footer = _lbl("", size=11, color=_TM)
        root.addWidget(self._footer)

    def _schedule_refresh(self) -> None:
        from tahmeed.ui.async_utils import schedule_coro
        schedule_coro(self.refresh())

    def _schedule_save_filled(self) -> None:
        from tahmeed.ui.async_utils import schedule_coro
        schedule_coro(self._save_filled_mappings())

    def set_date(self, target: date) -> None:
        self._target = target
        qd = QDate(target.year, target.month, target.day)
        self._date.blockSignals(True)
        self._date.setDate(qd)
        self._date.blockSignals(False)
        self._schedule_refresh()

    def _on_date_changed(self, qd: QDate) -> None:
        self._target = date(qd.year(), qd.month(), qd.day())
        if self._scope == "day":
            self._schedule_refresh()

    def _on_scope_changed(self, _index: int = 0) -> None:
        self._scope = self._scope_combo.currentData() or "day"
        self._date.setEnabled(self._scope == "day")
        self._open_reg_btn.setEnabled(self._scope == "day")
        self._schedule_refresh()

    def _row_source_args(self) -> tuple:
        replace_txs = None
        extra_txs = None
        if self._row_source is not None:
            try:
                replace_txs, extra_txs = self._row_source(self._target)
            except Exception:
                replace_txs, extra_txs = None, None
        return replace_txs, extra_txs

    async def refresh(self) -> None:
        if self._loading:
            return
        self._loading = True
        self._loading_overlay.show_loading("Scanning day issues…")
        try:
            replace_txs, extra_txs = self._row_source_args()
            if self._scope == "all":
                cats, payees, snap = await asyncio.gather(
                    get_payment_target_categories(),
                    get_payee_names(active_only=True),
                    collect_day_issues_all_days(
                        cashier_id=self._cashier_scope_id,
                        anchor_date=self._target,
                        live_target=self._target,
                        live_transactions=replace_txs,
                        live_extras=extra_txs,
                    ),
                )
            else:
                cats, payees, snap = await asyncio.gather(
                    get_payment_target_categories(),
                    get_payee_names(active_only=True),
                    collect_day_issues(
                        self._target,
                        transactions=replace_txs,
                        extra_transactions=extra_txs,
                        cashier_id=self._cashier_scope_id,
                    ),
                )
            self._categories = sort_payment_targets(cats)
            self._payees = list(payees)
            self._issues_all = list(snap.issues)
            self._issue_dates = dict(getattr(snap, "issue_dates", None) or {})
            self._days_scanned = list(getattr(snap, "days_scanned", None) or [])
            if self._scope == "day":
                self._days_scanned = [self._target]
                self._issue_dates = {
                    i.description_key: [self._target] for i in self._issues_all
                }
            live_keys = {i.description_key for i in self._issues_all}
            self._checked_keys &= live_keys
            self._apply_filters(repaint=False)
            self._populate()
            self.issues_changed.emit(len(self._issues_all))
        except Exception as exc:
            QMessageBox.critical(self, "Day Issues", f"Could not load issues:\n{exc}")
            self.issues_changed.emit(0)
        finally:
            self._loading = False
            self._loading_overlay.hide_loading()

    def _apply_filters(self, *, repaint: bool = True) -> None:
        self._issues = filter_day_issues(
            self._issues_all,
            search=self._search_text,
            value_filter=self._value_filter,
        )
        if repaint:
            self._populate()

    def _on_search_changed(self, text: str) -> None:
        self._search_text = text or ""
        self._apply_filters()

    def _open_value_filter(self) -> None:
        if not self._issues_all:
            QMessageBox.information(self, "Filter VALUE", "No issues to filter.")
            return
        pre = self._value_filter or set()
        dlg = ValueFilterDialog(self._issues_all, selected_keys=pre, parent=self)
        if dlg.exec() != QDialog.Accepted:
            return
        keys = dlg.result_keys or set()
        if not keys or len(keys) >= len(self._issues_all):
            self._value_filter = None
        else:
            self._value_filter = keys
        self._clear_filter_btn.setEnabled(self._value_filter is not None)
        self._apply_filters()

    def _clear_value_filter(self) -> None:
        self._value_filter = None
        self._clear_filter_btn.setEnabled(False)
        self._apply_filters()

    def _check_highlighted(self) -> None:
        for issue in self._selected_issues():
            self._checked_keys.add(issue.description_key)
        self._populate()

    def _clear_checks(self) -> None:
        self._checked_keys.clear()
        self._populate()

    def _draft_key(self, description: str) -> str:
        return normalize_description(description)

    def _store_draft(self, description: str, payee: str, item: str) -> None:
        key = self._draft_key(description)
        if not key:
            return
        payee_text = " ".join((payee or "").strip().upper().split())
        item_text = (item or "").strip()
        if payee_text in {"—", "-"}:
            payee_text = ""
        if item_text in {"—", "-"}:
            item_text = ""
        if not payee_text and not item_text:
            self._mapping_drafts.pop(key, None)
            return
        self._mapping_drafts[key] = {
            "description": description,
            "payee": payee_text,
            "item": item_text,
        }

    def _filled_drafts(self) -> List[tuple]:
        """Return (description, payee, item) for drafts with both fields set."""
        out: List[tuple] = []
        for issue in self._issues_all:
            key = issue.description_key
            draft = self._mapping_drafts.get(key) or {}
            payee = (draft.get("payee") or "").strip()
            item = (draft.get("item") or "").strip()
            if payee and item:
                out.append((issue.description, payee, item))
        return out

    def _update_save_filled_enabled(self) -> None:
        filled = self._filled_drafts()
        self._save_filled_btn.setEnabled(bool(filled))
        if filled:
            base = self._status.text().split("  ·  ready")[0]
            self._status.setText(f"{base}  ·  {len(filled)} ready to Save Filled")

    def _on_issue_cell_changed(self, item: QTableWidgetItem) -> None:
        if self._populating or item is None:
            return
        if item.column() == _COL_CHECK:
            row = item.row()
            if 0 <= row < len(self._issues):
                key = self._issues[row].description_key
                if item.checkState() == Qt.Checked:
                    self._checked_keys.add(key)
                else:
                    self._checked_keys.discard(key)
            return
        if item.column() not in (_COL_PAYEE, _COL_ITEM):
            return
        row = item.row()
        if row < 0 or row >= len(self._issues):
            return
        issue = self._issues[row]
        payee_it = self._table.item(row, _COL_PAYEE)
        item_it = self._table.item(row, _COL_ITEM)
        payee = (payee_it.text() if payee_it else "").strip()
        item_name = (item_it.text() if item_it else "").strip()
        self._store_draft(issue.description, payee, item_name)
        self._update_save_filled_enabled()

    def _end_inline_edit(self) -> None:
        """Close any open cell editor before the table is rebuilt."""
        idx = self._table.currentIndex()
        if idx.isValid() and self._table.isPersistentEditorOpen(idx):
            self._table.closePersistentEditor(idx)
        focus = QApplication.focusWidget()
        if focus is not None and self._table.isAncestorOf(focus):
            self._table.setFocus(Qt.OtherFocusReason)

    def _populate(self) -> None:
        issues = self._issues
        self._populating = True
        self._end_inline_edit()
        self._table.blockSignals(True)
        try:
            self._table.setRowCount(0)
            n = len(issues)
            total = len(self._issues_all)
            self._count_chip.setText(
                f"{n} shown" if n != total else f"{n} issue{'s' if n != 1 else ''}"
            )
            scope_label = (
                f"All days ({len(self._days_scanned)} day(s))"
                if self._scope == "all"
                else as_of_label(self._target)
            )
            extras = []
            if self._value_filter is not None:
                extras.append(f"VALUE filter {len(self._value_filter)}")
            if self._search_text.strip():
                extras.append(f"search {self._search_text.strip()!r}")
            if self._checked_keys:
                extras.append(f"{len(self._checked_keys)} checked")
            extra_txt = ("  ·  " + "  ·  ".join(extras)) if extras else ""
            self._status.setText(
                f"{scope_label}  ·  "
                f"{sum(1 for i in self._issues_all if i.kind == 'Unmapped')} unmapped  ·  "
                f"{sum(1 for i in self._issues_all if i.kind == 'Bad map')} bad map"
                f"{extra_txt}"
                if self._issues_all else f"{scope_label}  ·  no issues"
            )
            self._map_btn.setEnabled(total > 0)
            self._map_all_btn.setEnabled(
                any(i.kind == "Unmapped" for i in self._issues_all)
            )

            # Drop drafts for descriptions no longer in the issue list.
            live_keys = {i.description_key for i in self._issues_all}
            for key in list(self._mapping_drafts.keys()):
                if key not in live_keys:
                    self._mapping_drafts.pop(key, None)

            for i, issue in enumerate(issues):
                self._table.insertRow(i)
                bg = _stripe_bg(i)
                kind_bg = _AMBER_L if issue.kind == "Unmapped" else _RED_L
                draft = self._mapping_drafts.get(issue.description_key) or {}
                payee = (draft.get("payee") or issue.payee or "").strip()
                item_name = (draft.get("item") or issue.item or "").strip()

                check = QTableWidgetItem()
                check.setFlags(
                    Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable
                )
                check.setCheckState(
                    Qt.Checked
                    if issue.description_key in self._checked_keys
                    else Qt.Unchecked
                )
                check.setBackground(QBrush(QColor(bg)))
                self._table.setItem(i, _COL_CHECK, check)

                self._table.setItem(
                    i, _COL_NUM,
                    _cell(str(i + 1), Qt.AlignCenter | Qt.AlignVCenter, bg=bg),
                )
                self._table.setItem(
                    i, _COL_KIND,
                    _cell(issue.kind, Qt.AlignCenter | Qt.AlignVCenter, bg=kind_bg),
                )
                self._table.setItem(i, _COL_DESC, _cell(issue.description, bg=bg))
                self._table.setItem(
                    i, _COL_ROWS,
                    _cell(str(issue.count), Qt.AlignCenter | Qt.AlignVCenter, bg=bg),
                )
                self._table.setItem(i, _COL_PAYEE, _editable_cell(payee, bg=bg))
                self._table.setItem(i, _COL_ITEM, _editable_cell(item_name, bg=bg))
                self._table.setItem(
                    i, _COL_DETAIL, _cell(issue.detail or "—", bg=bg)
                )
                _finish_table_row(self._table, i, bg)
                for col in (_COL_PAYEE, _COL_ITEM):
                    it = self._table.item(i, col)
                    if it is not None:
                        it.setFlags(
                            Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable
                        )
                chk = self._table.item(i, _COL_CHECK)
                if chk is not None:
                    chk.setFlags(
                        Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable
                    )
                    chk.setCheckState(
                        Qt.Checked
                        if issue.description_key in self._checked_keys
                        else Qt.Unchecked
                    )

            self._footer.setText(
                "Check ☐ and/or highlight rows, then Map Selected — or edit "
                "Payee/Item inline and Save Filled."
                if issues else "All clear — this day can be submitted."
            )
        finally:
            self._table.blockSignals(False)
            self._populating = False
        self._update_save_filled_enabled()

    def _selected_issues(self) -> List[DayIssue]:
        rows = sorted({idx.row() for idx in self._table.selectedIndexes()})
        return [self._issues[r] for r in rows if 0 <= r < len(self._issues)]

    def _map_selected(self) -> None:
        selected_keys = {i.description_key for i in self._selected_issues()}
        targets = map_target_issues(
            self._issues,
            checked_keys=self._checked_keys,
            selected_keys=selected_keys,
        )
        if not targets:
            QMessageBox.information(
                self, "Map Selected",
                "Check ☐ rows and/or click-drag to highlight rows, then Map Selected.",
            )
            return
        from tahmeed.ui.async_utils import schedule_coro
        schedule_coro(self._do_map([i.description for i in targets], targets))

    def _map_all_unmapped(self) -> None:
        unmapped = [i for i in self._issues_all if i.kind == "Unmapped"]
        if not unmapped:
            QMessageBox.information(self, "Map All", "No unmapped descriptions.")
            return
        from tahmeed.ui.async_utils import schedule_coro
        schedule_coro(self._do_map([i.description for i in unmapped], unmapped))

    async def _save_filled_mappings(self) -> None:
        """Commit inline drafts that have both Payee and Item (skip incomplete)."""
        filled = self._filled_drafts()
        if not filled:
            QMessageBox.information(
                self,
                "Save Filled",
                "Fill both Payee and Item on one or more rows first.\n"
                "Incomplete drafts (only one field) are not saved.",
            )
            return

        resolved: List[tuple] = []
        unknown_items: List[str] = []
        for desc, payee, item_name in filled:
            cat = next(
                (
                    c for c in self._categories
                    if c.name.strip().lower() == item_name.strip().lower()
                ),
                None,
            )
            if cat is None or cat._id is None:
                unknown_items.append(item_name)
                continue
            resolved.append((desc, payee, cat))

        if unknown_items:
            sample = ", ".join(sorted(set(unknown_items))[:6])
            QMessageBox.warning(
                self,
                "Unknown Item",
                f"These Item name(s) are not in the item list and were skipped:\n"
                f"{sample}\n\n"
                "Pick an existing item (or use Map Selected → New Item…).",
            )
            if not resolved:
                return

        if self._payees:
            payee_set = {p.upper() for p in self._payees}
            odd = sorted({p for _d, p, _c in resolved if p not in payee_set})
            if odd:
                tip = "\n".join(f"  · {p}" for p in odd[:8])
                if QMessageBox.question(
                    self,
                    "Payee not in list",
                    f"Some payees are not in the Payee list:\n{tip}\n\n"
                    "Save anyway?",
                ) != QMessageBox.Yes:
                    return

        self._loading_overlay.show_loading("Saving filled mappings…")
        saved_n = 0
        live_n = 0
        committed = 0
        try:
            for desc, payee, cat in resolved:
                await save_mapping(desc, cat._id, cat.name, payee=payee)
                s, l = await self._apply_mapping_everywhere(
                    [desc], payee, cat.name, cat._id
                )
                saved_n += s
                live_n += l
                committed += 1
                self._mapping_drafts.pop(normalize_description(desc), None)
        except Exception as exc:
            QMessageBox.critical(self, "Save Filled Failed", str(exc))
            return
        finally:
            self._loading_overlay.hide_loading()

        applied = max(saved_n, live_n) if live_n else saved_n
        QMessageBox.information(
            self,
            "Mappings saved",
            f"Saved {committed:,} filled mapping(s) to Description Maps.\n"
            f"Updated {applied:,} open row(s)"
            + (f" ({live_n:,} on Table/draft)." if live_n else "."),
        )
        self.mappings_applied.emit()
        await self.refresh()

    async def _apply_mapping_everywhere(
        self,
        descriptions: Sequence[str],
        payee: str,
        item_name: str,
        category_id: ObjectId,
    ) -> tuple:
        apply_days = self._dates_for_descriptions(descriptions)
        saved_n = await self._apply_to_saved_rows(
            descriptions,
            payee,
            item_name,
            category_id,
            days=apply_days,
        )
        live_n = 0
        if self._apply_live is not None:
            for day in apply_days:
                try:
                    live_n += int(
                        self._apply_live(
                            day,
                            descriptions,
                            payee=payee,
                            item_name=item_name,
                            category_id=category_id,
                            cashier_id=self._cashier_scope_id,
                        )
                        or 0
                    )
                except Exception:
                    continue
        return saved_n, live_n

    async def _do_map(
        self,
        descriptions: List[str],
        source_issues: List[DayIssue],
    ) -> None:
        prefill_payee = ""
        prefill_item = ""
        payees = {i.payee for i in source_issues if i.payee}
        items = {i.item for i in source_issues if i.item}
        if len(payees) == 1:
            prefill_payee = next(iter(payees))
        if len(items) == 1:
            prefill_item = next(iter(items))

        dlg = MapDescriptionsDialog(
            descriptions,
            self._categories,
            self._payees,
            parent=self,
            prefill_payee=prefill_payee,
            prefill_item=prefill_item,
        )
        if dlg.exec() != QDialog.Accepted:
            return
        assignment = dlg.result_assignment
        payee = dlg.result_payee
        if assignment is None or not payee:
            return
        try:
            category = await materialize_mapping_assignment(assignment, self._categories)
        except Exception as exc:
            QMessageBox.warning(self, "Could Not Save Item", str(exc))
            return
        if category is None or category._id is None:
            QMessageBox.warning(self, "Select Item", "Please choose or create an item.")
            return
        remember_category(self._categories, category)

        self._loading_overlay.show_loading("Saving mappings…")
        try:
            for desc in descriptions:
                await save_mapping(
                    desc, category._id, category.name, payee=payee
                )
            saved_n, live_n = await self._apply_mapping_everywhere(
                descriptions, payee, category.name, category._id
            )
            for desc in descriptions:
                self._mapping_drafts.pop(normalize_description(desc), None)
        except Exception as exc:
            QMessageBox.critical(self, "Mapping Failed", str(exc))
            return
        finally:
            self._loading_overlay.hide_loading()

        applied = max(saved_n, live_n) if live_n else saved_n
        detail = (
            f" ({live_n:,} on Table/draft including unsaved)."
            if live_n
            else "."
        )
        apply_days = self._dates_for_descriptions(descriptions)
        scope_txt = (
            f"{len(apply_days)} day(s)"
            if self._scope == "all"
            else as_of_label(self._target)
        )
        QMessageBox.information(
            self,
            "Mappings saved",
            f"Mapped {len(descriptions):,} description(s) to:\n"
            f"Payee: {payee}\nItem: {category.name}\n\n"
            f"Saved to Description Maps.\n"
            f"Updated {applied:,} open row(s) across {scope_txt}"
            f"{detail}",
        )
        self.mappings_applied.emit()
        await self.refresh()

    def _dates_for_descriptions(self, descriptions: Sequence[str]) -> List[date]:
        keys = {normalize_description(d) for d in descriptions}
        days: set[date] = set()
        for key in keys:
            for d in self._issue_dates.get(key) or []:
                if isinstance(d, date):
                    days.add(d)
        if not days:
            days.add(self._target)
        return sorted(days)

    async def _apply_to_saved_rows(
        self,
        descriptions: Sequence[str],
        payee: str,
        item_name: str,
        category_id: ObjectId,
        *,
        days: Optional[Sequence[date]] = None,
    ) -> int:
        """Patch saved open (unverified) rows for the given day(s)."""
        from tahmeed.services.cashier_service import (
            get_transactions_by_date,
            update_transaction,
        )

        keys = {normalize_description(d) for d in descriptions}
        target_days = list(days) if days else [self._target]
        updated = 0
        for day in target_days:
            if self._cashier_scope_id is not None:
                txs = await get_transactions_by_date(
                    day,
                    merged=False,
                    cashier_id=self._cashier_scope_id,
                )
            else:
                txs = await get_transactions_by_date(day, merged=True)
            txs = filter_rows_for_cashier_scope(txs, self._cashier_scope_id)
            for tx in txs:
                if getattr(tx, "verified", False) or getattr(tx, "trashed", False):
                    continue
                if getattr(tx, "discarded", False):
                    continue
                key = normalize_description(getattr(tx, "description", "") or "")
                if key not in keys:
                    continue
                if tx._id is None:
                    continue
                body = {
                    "payee": payee,
                    "item": item_name,
                    "category_name": item_name,
                    "category_id": category_id,
                }
                try:
                    ok = await update_transaction(tx._id, body)
                except Exception:
                    continue
                if ok:
                    updated += 1
        return updated
