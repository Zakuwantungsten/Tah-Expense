"""Day Issues — bulk map unmapped descriptions → Payee + Item.

Mirrors the QuickBooks Expense Automation issues tab (without Ignore):
multi-select descriptions, assign one Payee + Item, apply to open rows.
"""

from __future__ import annotations

import asyncio
from datetime import date
from typing import List, Optional, Sequence

import qtawesome as qta
from bson import ObjectId
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QBrush
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QCompleter, QDialog, QFrame,
    QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
    QDateEdit,
)
from PySide6.QtCore import QDate

from tahmeed.models.category import Category
from tahmeed.models.user import User
from tahmeed.services.category_service import get_payment_target_categories, sort_payment_targets
from tahmeed.services.day_issues_service import (
    DayIssue,
    DayIssuesSnapshot,
    as_of_label,
    collect_day_issues,
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

_COL_DEFAULTS = [48, 100, 280, 70, 160, 160, 220]


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
            "font-family:'Segoe UI';padding:0 8px;}}"
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
            "font-family:'Segoe UI';padding:0 8px;}}"
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


class DayIssuesWidget(QWidget):
    """Cashier / accountant Day Issues page for one register date."""

    issues_changed = Signal(int)   # badge count
    open_register = Signal(object)  # date
    mappings_applied = Signal()     # refresh register rows

    def __init__(self, user: User, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._user = user
        self._target: date = date.today()
        self._issues: List[DayIssue] = []
        self._categories: List[Category] = []
        self._payees: List[str] = []
        self._loading = False
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
            "font-family:'Segoe UI';padding:0 8px;}}"
        )
        style_calendar_popup(self._date)
        self._date.dateChanged.connect(self._on_date_changed)
        hdr.addWidget(self._date)

        refresh = _btn("Refresh", primary=False)
        refresh.clicked.connect(lambda: asyncio.ensure_future(self.refresh()))
        hdr.addWidget(refresh)

        open_reg = _btn("Open Table", primary=False)
        open_reg.clicked.connect(lambda: self.open_register.emit(self._target))
        hdr.addWidget(open_reg)
        root.addLayout(hdr)

        hint = _lbl(
            "Select unmapped descriptions, then Map Selected → one Payee + Item "
            "for all of them. Submit is blocked while issues remain "
            "(unless the accountant allows submit with issues).",
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
        actions.addStretch()
        self._status = _lbl("", size=12, color=_T2)
        actions.addWidget(self._status)
        root.addLayout(actions)

        self._table_host = QFrame()
        self._table_host.setStyleSheet("QFrame{background:transparent;border:none;}")
        tv = QVBoxLayout(self._table_host)
        tv.setContentsMargins(0, 0, 0, 0)

        self._table = QTableWidget(0, 7)
        self._table.setHorizontalHeaderLabels(
            ["#", "Type", "Description", "Rows", "Payee", "Item", "Detail"]
        )
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(_ROW_H)
        self._table.setStyleSheet(_table_style())
        hdr_view = self._table.horizontalHeader()
        hdr_view.setStretchLastSection(True)
        for i, width in enumerate(_COL_DEFAULTS):
            self._table.setColumnWidth(i, width)
        tv.addWidget(self._table)
        root.addWidget(self._table_host, 1)

        self._loading_overlay = LoadingOverlay(self._table_host, "Loading…")
        self._footer = _lbl("", size=11, color=_TM)
        root.addWidget(self._footer)

    def set_date(self, target: date) -> None:
        self._target = target
        qd = QDate(target.year, target.month, target.day)
        self._date.blockSignals(True)
        self._date.setDate(qd)
        self._date.blockSignals(False)
        asyncio.ensure_future(self.refresh())

    def _on_date_changed(self, qd: QDate) -> None:
        self._target = date(qd.year(), qd.month(), qd.day())
        asyncio.ensure_future(self.refresh())

    async def refresh(self) -> None:
        if self._loading:
            return
        self._loading = True
        self._loading_overlay.show_loading("Scanning day issues…")
        try:
            cats, payees, snap = await asyncio.gather(
                get_payment_target_categories(),
                get_payee_names(active_only=True),
                collect_day_issues(self._target),
            )
            self._categories = sort_payment_targets(cats)
            self._payees = list(payees)
            self._issues = list(snap.issues)
            self._populate()
            self.issues_changed.emit(len(self._issues))
        except Exception as exc:
            QMessageBox.critical(self, "Day Issues", f"Could not load issues:\n{exc}")
            self.issues_changed.emit(0)
        finally:
            self._loading = False
            self._loading_overlay.hide_loading()

    def _populate(self) -> None:
        issues = self._issues
        self._table.setRowCount(0)
        n = len(issues)
        self._count_chip.setText(f"{n} issue{'s' if n != 1 else ''}")
        self._status.setText(
            f"{as_of_label(self._target)}  ·  "
            f"{sum(1 for i in issues if i.kind == 'Unmapped')} unmapped  ·  "
            f"{sum(1 for i in issues if i.kind == 'Bad map')} bad map"
            if issues else f"{as_of_label(self._target)}  ·  no issues"
        )
        self._map_btn.setEnabled(n > 0)
        self._map_all_btn.setEnabled(any(i.kind == "Unmapped" for i in issues))

        for i, issue in enumerate(issues):
            self._table.insertRow(i)
            bg = _stripe_bg(i)
            kind_bg = _AMBER_L if issue.kind == "Unmapped" else _RED_L
            self._table.setItem(
                i, 0, _cell(str(i + 1), Qt.AlignCenter | Qt.AlignVCenter, bg=bg)
            )
            self._table.setItem(
                i, 1, _cell(issue.kind, Qt.AlignCenter | Qt.AlignVCenter, bg=kind_bg)
            )
            self._table.setItem(i, 2, _cell(issue.description, bg=bg))
            self._table.setItem(
                i, 3, _cell(str(issue.count), Qt.AlignCenter | Qt.AlignVCenter, bg=bg)
            )
            self._table.setItem(i, 4, _cell(issue.payee or "—", bg=bg))
            self._table.setItem(i, 5, _cell(issue.item or "—", bg=bg))
            self._table.setItem(i, 6, _cell(issue.detail or "—", bg=bg))
            _finish_table_row(self._table, i, bg)

        self._footer.setText(
            "Select rows (Ctrl/Shift click), then Map Selected."
            if issues else "All clear — this day can be submitted."
        )

    def _selected_issues(self) -> List[DayIssue]:
        rows = sorted({idx.row() for idx in self._table.selectedIndexes()})
        return [self._issues[r] for r in rows if 0 <= r < len(self._issues)]

    def _map_selected(self) -> None:
        selected = self._selected_issues()
        if not selected:
            QMessageBox.information(
                self, "Map Selected",
                "Select one or more issue rows, then Map Selected.",
            )
            return
        asyncio.ensure_future(self._do_map([i.description for i in selected], selected))

    def _map_all_unmapped(self) -> None:
        unmapped = [i for i in self._issues if i.kind == "Unmapped"]
        if not unmapped:
            QMessageBox.information(self, "Map All", "No unmapped descriptions.")
            return
        asyncio.ensure_future(self._do_map([i.description for i in unmapped], unmapped))

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
            applied = await self._apply_to_open_rows(
                descriptions, payee, category.name, category._id
            )
        except Exception as exc:
            QMessageBox.critical(self, "Mapping Failed", str(exc))
            return
        finally:
            self._loading_overlay.hide_loading()

        QMessageBox.information(
            self,
            "Mappings saved",
            f"Mapped {len(descriptions):,} description(s) to:\n"
            f"Payee: {payee}\nItem: {category.name}\n\n"
            f"Updated {applied:,} open row(s) on {as_of_label(self._target)}.",
        )
        self.mappings_applied.emit()
        await self.refresh()

    async def _apply_to_open_rows(
        self,
        descriptions: Sequence[str],
        payee: str,
        item_name: str,
        category_id: ObjectId,
    ) -> int:
        """Patch open (unverified) rows for this day that match the descriptions."""
        from tahmeed.services.cashier_service import (
            get_transactions_by_date,
            update_transaction,
        )

        keys = {normalize_description(d) for d in descriptions}
        txs = await get_transactions_by_date(self._target, merged=True)
        updated = 0
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
