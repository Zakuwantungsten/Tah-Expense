"""Dialog to assign an item to an unmapped description (import / verify)."""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QPushButton, QFrame, QMessageBox, QCompleter,
)
from PySide6.QtCore import Qt

from tahmeed.models.category import Category

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
    ) -> None:
        super().__init__(parent)
        self._categories = categories
        self._selected: Optional[Category] = None
        self._action = ACTION_ASSIGN
        self._scope_label = scope_label
        self._cancel_label = cancel_label
        self._allow_skip = allow_skip
        self.setWindowTitle("Map Description to Item")
        self.setMinimumWidth(560)
        self.setModal(True)
        self._build(description, row_count, remaining)

    def _build(self, description: str, row_count: int, remaining: int) -> None:
        self.setStyleSheet(
            f"QDialog {{ background: {_BG}; }}"
            "QLabel { border: none; background: transparent; }"
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(14)

        title = QLabel("Assign Item for Description")
        title.setStyleSheet(
            f"color: {_T1}; font-size: 16px; font-weight: 700;"
            " border: none; background: transparent;"
        )
        root.addWidget(title)

        progress = QLabel(
            f"{remaining} unique description{'s' if remaining != 1 else ''} remaining"
        )
        progress.setStyleSheet(
            f"color: {_T2}; font-size: 12px; border: none; background: transparent;"
        )
        root.addWidget(progress)

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

        item_lbl = QLabel("Item (from Items tab) — remembered for next time")
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

        assign_btn = QPushButton("Assign && Continue")
        assign_btn.setCursor(Qt.PointingHandCursor)
        assign_btn.setFixedHeight(34)
        assign_btn.setStyleSheet(
            f"QPushButton {{ background: {_BLUE}; color: #FFF; border: none;"
            " border-radius: 5px; font-size: 13px; font-weight: 600;"
            " padding: 0 16px;"
            " min-height: 34px; max-height: 34px; }}"
            "QPushButton:hover { background: #005EA3; }"
        )
        assign_btn.clicked.connect(self._on_assign)
        btn_row.addWidget(assign_btn)
        root.addLayout(btn_row)

    def _on_assign(self) -> None:
        name = self._combo.currentText().strip()
        if not name:
            QMessageBox.warning(self, "Select Item", "Please choose an item for this description.")
            return
        idx = self._combo.findText(name)
        cat_id = self._combo.itemData(idx) if idx >= 0 else None
        if cat_id is None:
            match = next((c for c in self._categories if c.name == name), None)
            if match is None:
                QMessageBox.warning(
                    self,
                    "Select Item",
                    "Please pick an existing item from the Items list.",
                )
                return
            cat_id = match._id
        self._selected = Category(_id=cat_id, name=name)
        self._action = ACTION_ASSIGN
        self.accept()

    def _on_skip(self) -> None:
        self._selected = None
        self._action = ACTION_SKIP
        self.accept()

    def _on_skip_all(self) -> None:
        self._selected = None
        self._action = ACTION_SKIP_ALL
        self.accept()

    def selected_category(self) -> Optional[Category]:
        return self._selected

    def action(self) -> str:
        return self._action
