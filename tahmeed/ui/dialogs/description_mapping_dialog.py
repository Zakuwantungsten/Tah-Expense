"""Dialog to assign an item to an unmapped description (import / verify)."""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QPushButton, QFrame, QCompleter, QProgressBar,
)
from PySide6.QtCore import Qt

from tahmeed.models.category import Category
from tahmeed.services.category_service import sort_payment_targets
from tahmeed.services.mapping_assignment_service import MappingAssignment
from tahmeed.ui.dialogs.item_dialog import ItemDialog

_WHITE = "#FFFFFF"
_BG = "#F4F6F8"
_BORDER = "#E5E7EB"
_BLUE = "#0077C5"
_T1 = "#111827"
_T2 = "#6B7280"

# Dialog result actions (in addition to QDialog.Accepted / Rejected)
ACTION_ASSIGN = "assign"
ACTION_SKIP = "skip"
ACTION_SKIP_ALL = "skip_all"


class DescriptionMappingDialog(QDialog):
    """Map one description to an item, or skip (leave without item).

    Assignments are remembered via description_mappings for future imports.
    Skip / Skip All apply only to the current import batch.

    Pick an existing item, or click Assign to a New Item to open the same
    Add New Item dialog used on the Items tab.
    """

    def __init__(
        self,
        description: str,
        row_count: int,
        categories: List[Category],
        remaining: int,
        parent=None,
        *,
        scope_label: str = "in this import",
        cancel_label: str = "Cancel Import",
        allow_skip: bool = False,
        total: Optional[int] = None,
    ) -> None:
        super().__init__(parent)
        self._categories = sort_payment_targets(categories)
        self._description = description
        self._selected: Optional[Category] = None
        self._action = ACTION_ASSIGN
        self._create_new = False
        self._new_item_name = ""
        self._new_item_fields: Optional[dict] = None
        self._scope_label = scope_label
        self._cancel_label = cancel_label
        self._allow_skip = allow_skip
        self.setWindowTitle("Map Description to Item")
        self.setMinimumWidth(560)
        self.setModal(True)
        self._build(description, row_count, remaining, total)

    def _build(
        self,
        description: str,
        row_count: int,
        remaining: int,
        total: Optional[int],
    ) -> None:
        self.setStyleSheet(
            f"QDialog {{ background: {_BG}; color: {_T1}; }}"
            "QLabel { border: none; background: transparent; color: #111827; }"
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(14)

        title = QLabel("Assign Item or Supplier for Description")
        title.setStyleSheet(
            f"color: {_T1}; font-size: 16px; font-weight: 700;"
            " border: none; background: transparent;"
        )
        root.addWidget(title)

        total_n = max(remaining, total or remaining)
        done = max(0, total_n - remaining)
        progress = QLabel(
            f"{remaining} unique description{'s' if remaining != 1 else ''} remaining"
            + (f"  ·  {done} of {total_n} mapped" if total_n > 1 else "")
        )
        progress.setStyleSheet(
            f"color: {_T2}; font-size: 12px; border: none; background: transparent;"
        )
        root.addWidget(progress)

        if total_n > 1:
            bar = QProgressBar()
            bar.setRange(0, total_n)
            bar.setValue(done)
            bar.setFixedHeight(10)
            bar.setTextVisible(False)
            bar.setStyleSheet(
                f"QProgressBar {{ background: {_BORDER}; border: none;"
                " border-radius: 5px; }"
                f"QProgressBar::chunk {{ background: {_BLUE}; border-radius: 5px; }}"
            )
            root.addWidget(bar)

        card = QFrame()
        card.setObjectName("descMapCard")
        card.setStyleSheet(
            f"QFrame#descMapCard {{ background-color: {_WHITE};"
            f" border: 1px solid {_BORDER}; border-radius: 12px; }}"
        )
        cvl = QVBoxLayout(card)
        cvl.setContentsMargins(16, 14, 16, 14)
        cvl.setSpacing(8)

        desc_lbl = QLabel(description)
        desc_lbl.setWordWrap(True)
        desc_lbl.setStyleSheet(
            f"color: {_T1}; font-size: 14px; font-weight: 600;"
            " border: none; background: transparent;"
        )
        cvl.addWidget(desc_lbl)

        count_lbl = QLabel(
            f"Applies to {row_count:,} row{'s' if row_count != 1 else ''} {self._scope_label}"
        )
        count_lbl.setStyleSheet(
            f"color: {_T2}; font-size: 12px; border: none; background: transparent;"
        )
        cvl.addWidget(count_lbl)
        root.addWidget(card)

        item_lbl = QLabel("Item / Supplier — remembered for next time")
        item_lbl.setStyleSheet(
            f"color: {_T2}; font-size: 12px; border: none; background: transparent;"
        )
        root.addWidget(item_lbl)

        self._combo = QComboBox()
        self._combo.setEditable(True)
        self._combo.setInsertPolicy(QComboBox.NoInsert)
        self._combo.setFixedHeight(34)
        self._combo.setStyleSheet(
            f"QComboBox {{ border: 1px solid {_BORDER}; border-radius: 5px;"
            f" background: {_WHITE}; color: {_T1}; font-size: 12px;"
            " padding: 0 8px; min-height: 34px; max-height: 34px; }}"
            f"QComboBox:focus {{ border-color: {_BLUE}; }}"
        )
        for cat in self._categories:
            self._combo.addItem(cat.name, cat._id)
        completer = QCompleter([c.name for c in self._categories])
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        self._combo.setCompleter(completer)
        self._combo.setCurrentIndex(-1)
        root.addWidget(self._combo)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        cancel_btn = QPushButton(self._cancel_label)
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.setFixedHeight(34)
        # Enter must not activate Cancel — Qt makes the first autoDefault the default.
        cancel_btn.setAutoDefault(False)
        cancel_btn.setDefault(False)
        cancel_btn.setStyleSheet(
            f"QPushButton {{ background: {_WHITE}; color: {_T2};"
            f" border: 1px solid {_BORDER}; border-radius: 5px;"
            " font-size: 13px; padding: 0 16px;"
            " min-height: 34px; max-height: 34px; }}"
            f"QPushButton:hover {{ background: {_BG}; }}"
        )
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        if self._allow_skip:
            skip_btn = QPushButton("Skip This")
            skip_btn.setToolTip("Leave this description without an item for this import only")
            skip_btn.setCursor(Qt.PointingHandCursor)
            skip_btn.setFixedHeight(34)
            skip_btn.setAutoDefault(False)
            skip_btn.setStyleSheet(
                f"QPushButton {{ background: {_WHITE}; color: {_T1};"
                f" border: 1px solid {_BORDER}; border-radius: 5px;"
                " font-size: 13px; padding: 0 14px;"
                " min-height: 34px; max-height: 34px; }}"
                f"QPushButton:hover {{ background: {_BG}; }}"
            )
            skip_btn.clicked.connect(self._on_skip)
            btn_row.addWidget(skip_btn)

            skip_all_btn = QPushButton("Skip All")
            skip_all_btn.setToolTip(
                "Leave all remaining unmapped descriptions without an item"
            )
            skip_all_btn.setCursor(Qt.PointingHandCursor)
            skip_all_btn.setFixedHeight(34)
            skip_all_btn.setAutoDefault(False)
            skip_all_btn.setStyleSheet(
                f"QPushButton {{ background: {_WHITE}; color: {_T1};"
                f" border: 1px solid {_BORDER}; border-radius: 5px;"
                " font-size: 13px; padding: 0 14px;"
                " min-height: 34px; max-height: 34px; }}"
                f"QPushButton:hover {{ background: {_BG}; }}"
            )
            skip_all_btn.clicked.connect(self._on_skip_all)
            btn_row.addWidget(skip_all_btn)

        btn_row.addStretch()

        new_item_btn = QPushButton("Assign to a New Item…")
        new_item_btn.setCursor(Qt.PointingHandCursor)
        new_item_btn.setFixedHeight(34)
        new_item_btn.setAutoDefault(False)
        new_item_btn.setDefault(False)
        new_item_btn.setToolTip(
            "Open the same Add New Item form used on the Items tab, "
            "then map this description to it."
        )
        new_item_btn.setStyleSheet(
            f"QPushButton {{ background: {_WHITE}; color: {_T1};"
            f" border: 1px solid {_BORDER}; border-radius: 5px;"
            " font-size: 13px; padding: 0 14px;"
            " min-height: 34px; max-height: 34px; }}"
            f"QPushButton:hover {{ background: {_BG}; }}"
        )
        new_item_btn.clicked.connect(self._on_assign_new)
        btn_row.addWidget(new_item_btn)

        new_supplier_btn = QPushButton("Assign to a New Supplier…")
        new_supplier_btn.setCursor(Qt.PointingHandCursor)
        new_supplier_btn.setFixedHeight(34)
        new_supplier_btn.setAutoDefault(False)
        new_supplier_btn.setDefault(False)
        new_supplier_btn.setToolTip(
            "Create a supplier payment target, then map this description to it."
        )
        new_supplier_btn.setStyleSheet(
            f"QPushButton {{ background: {_WHITE}; color: {_T1};"
            f" border: 1px solid {_BORDER}; border-radius: 5px;"
            " font-size: 13px; padding: 0 14px;"
            " min-height: 34px; max-height: 34px; }}"
            f"QPushButton:hover {{ background: {_BG}; }}"
        )
        new_supplier_btn.clicked.connect(self._on_assign_new_supplier)
        btn_row.addWidget(new_supplier_btn)

        assign_btn = QPushButton("Assign && Continue")
        assign_btn.setCursor(Qt.PointingHandCursor)
        assign_btn.setFixedHeight(34)
        assign_btn.setAutoDefault(True)
        assign_btn.setDefault(True)
        assign_btn.setStyleSheet(
            f"QPushButton {{ background: {_BLUE}; color: #FFFFFF; border: none;"
            " border-radius: 5px; font-size: 13px; font-weight: 600;"
            " padding: 0 16px;"
            " min-height: 34px; max-height: 34px; }}"
            "QPushButton:hover { background: #005EA3; }"
        )
        assign_btn.clicked.connect(self._on_assign)
        btn_row.addWidget(assign_btn)
        root.addLayout(btn_row)

        # Enter in the item field = Assign & Continue (same as the primary button).
        line = self._combo.lineEdit()
        if line is not None:
            line.returnPressed.connect(self._on_assign)
        self._combo.setFocus()

    def _on_assign_new(self) -> None:
        dlg = ItemDialog(parent=self, prefill_name=self._description)
        if dlg.exec() != QDialog.Accepted:
            return
        data = dict(dlg.result_data or {})
        name = (data.get("name") or "").strip()
        if not name:
            from tahmeed.ui.dialog_theme import show_warning
            show_warning(self, "New Item", "Item name is required.")
            return
        existing = next(
            (c for c in self._categories if c.name.strip().lower() == name.lower()),
            None,
        )
        self._create_new = existing is None
        self._new_item_name = name
        self._new_item_fields = data
        self._selected = existing or Category(name=name)
        self._action = ACTION_ASSIGN
        self.accept()

    def _on_assign_new_supplier(self) -> None:
        from tahmeed.ui.accountant.manage_suppliers import _SupplierDialog
        from tahmeed.ui.dialog_theme import show_warning

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
        self._create_new = existing is None
        self._new_item_name = name
        self._new_item_fields = data
        self._selected = existing or Category(name=name, is_supplier=True)
        self._action = ACTION_ASSIGN
        self.accept()

    def _on_assign(self) -> None:
        from tahmeed.ui.dialog_theme import show_warning

        name = self._combo.currentText().strip()
        if not name:
            show_warning(self, "Select Item", "Please choose an item or supplier for this description.")
            return
        idx = self._combo.findText(name)
        cat_id = self._combo.itemData(idx) if idx >= 0 else None
        match = next((c for c in self._categories if c.name == name), None)
        if match is None:
            match = next(
                (c for c in self._categories if c.name.strip().lower() == name.lower()),
                None,
            )
        if cat_id is None and match is None:
            show_warning(
                self,
                "Select Item",
                "Please pick an existing item or supplier from the list, "
                "or click Assign to a New Item / Supplier.",
            )
            return
        if match is not None:
            self._selected = match
        else:
            self._selected = Category(_id=cat_id, name=name)
        self._create_new = False
        self._new_item_name = ""
        self._new_item_fields = None
        self._action = ACTION_ASSIGN
        self.accept()

    def _on_skip(self) -> None:
        self._selected = None
        self._create_new = False
        self._new_item_fields = None
        self._action = ACTION_SKIP
        self.accept()

    def _on_skip_all(self) -> None:
        self._selected = None
        self._create_new = False
        self._new_item_fields = None
        self._action = ACTION_SKIP_ALL
        self.accept()

    def selected_category(self) -> Optional[Category]:
        return self._selected

    def action(self) -> str:
        return self._action

    def creates_new_item(self) -> bool:
        return self._create_new

    def assignment(self) -> MappingAssignment:
        return MappingAssignment(
            action=self._action,
            description=self._description,
            category=self._selected,
            create_new=self._create_new,
            new_item_name=self._new_item_name,
            new_item_fields=self._new_item_fields,
        )
