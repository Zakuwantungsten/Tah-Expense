"""Batch review when multiple register rows match recent transactions."""

from __future__ import annotations

from typing import List, Set

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from tahmeed.services.duplicate_review import (
    DuplicateReviewItem,
    format_existing_date,
)

_WHITE = "#FFFFFF"
_BG = "#F4F6F8"
_T1 = "#111827"
_T2 = "#6B7280"
_BLUE = "#0077C5"
_BORDER = "#E5E7EB"
_AMBER = "#B45309"


def _cell(text: object, *, align=Qt.AlignLeft | Qt.AlignVCenter) -> QTableWidgetItem:
    item = QTableWidgetItem("" if text is None else str(text))
    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    item.setTextAlignment(align)
    return item


class DuplicateReviewDialog(QDialog):
    """Review all possible duplicates in one place with batch selection."""

    def __init__(
        self,
        items: List[DuplicateReviewItem],
        *,
        dup_days: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._items = list(items)
        self._dup_days = max(1, int(dup_days))
        self._save_anyway_rows: Set[int] = set()
        self.setWindowTitle("Possible Duplicate Entries")
        self.setMinimumSize(920, 420)
        self.setStyleSheet(f"background:{_WHITE}; color:{_T1};")
        self._build()

    def save_anyway_rows(self) -> Set[int]:
        return set(self._save_anyway_rows)

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        n = len(self._items)
        title = QLabel(
            f"{n} new entr{'y' if n == 1 else 'ies'} match recent transaction"
            f"{'s' if n != 1 else ''}"
        )
        title.setStyleSheet(
            f"font-size:16px;font-weight:700;color:{_T1};"
            "font-family:'Segoe UI',sans-serif;"
        )
        root.addWidget(title)

        window = (
            f"last {self._dup_days} day"
            if self._dup_days == 1
            else f"last {self._dup_days} days"
        )
        subtitle = QLabel(
            f"Checked the {window}. Tick rows you still want to save; "
            "unchecked rows are skipped. Saved duplicates are flagged for the accountant."
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(
            f"font-size:12px;color:{_T2};font-family:'Segoe UI',sans-serif;"
        )
        root.addWidget(subtitle)

        toggle_row = QHBoxLayout()
        toggle_row.setSpacing(8)
        self._select_all = QPushButton("Select all")
        self._select_none = QPushButton("Select none")
        for btn in (self._select_all, self._select_none):
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(
                f"QPushButton{{background:transparent;color:{_BLUE};border:1px solid {_BORDER};"
                "border-radius:4px;padding:4px 12px;font-size:11px;font-family:'Segoe UI';}}"
                f"QPushButton:hover{{background:{_BG};}}"
            )
        self._select_all.clicked.connect(self._check_all)
        self._select_none.clicked.connect(self._uncheck_all)
        toggle_row.addWidget(self._select_all)
        toggle_row.addWidget(self._select_none)
        toggle_row.addStretch()
        root.addLayout(toggle_row)

        headers = [
            "Save",
            "Row",
            "Description",
            "Truck",
            "Amount",
            "Item",
            "Existing date",
            "Existing description",
        ]
        self._table = QTableWidget(len(self._items), len(headers))
        self._table.setHorizontalHeaderLabels(headers)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setSelectionMode(QTableWidget.NoSelection)
        self._table.setAlternatingRowColors(True)
        self._table.setStyleSheet(
            f"QTableWidget{{border:1px solid {_BORDER};gridline-color:{_BORDER};"
            "font-family:'Segoe UI';font-size:12px;}}"
            "QHeaderView::section{background:#F9FAFB;padding:6px;border:none;"
            f"border-bottom:1px solid {_BORDER};font-weight:600;color:{_T2};}}"
        )
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(6, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(7, QHeaderView.Stretch)

        self._checks: list[QCheckBox] = []
        for i, item in enumerate(self._items):
            wrap = QWidget()
            lay = QHBoxLayout(wrap)
            lay.setContentsMargins(8, 0, 8, 0)
            lay.setAlignment(Qt.AlignCenter)
            cb = QCheckBox()
            cb.setToolTip("Save this row anyway and flag as possible duplicate")
            lay.addWidget(cb)
            self._checks.append(cb)
            self._table.setCellWidget(i, 0, wrap)

            self._table.setItem(i, 1, _cell(str(item.row_display), align=Qt.AlignCenter))
            self._table.setItem(i, 2, _cell(item.description))
            self._table.setItem(i, 3, _cell(item.truck_number or "—"))
            self._table.setItem(i, 4, _cell(item.amount_label))
            self._table.setItem(i, 5, _cell(item.item or "—"))
            ex = item.existing
            self._table.setItem(i, 6, _cell(format_existing_date(ex.date)))
            self._table.setItem(i, 7, _cell(ex.description or "—"))

        root.addWidget(self._table, 1)

        hint = QLabel(
            f"Tip: same truck and service on different days may be legitimate "
            f"(e.g. parking). Use Save checked to keep them."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(
            f"font-size:11px;color:{_AMBER};font-family:'Segoe UI',sans-serif;"
        )
        root.addWidget(hint)

        buttons = QDialogButtonBox()
        save_btn = buttons.addButton("Save checked", QDialogButtonBox.AcceptRole)
        skip_btn = buttons.addButton("Skip all duplicates", QDialogButtonBox.ActionRole)
        cancel_btn = buttons.addButton("Cancel save", QDialogButtonBox.RejectRole)
        save_btn.setStyleSheet(
            f"QPushButton{{background:{_BLUE};color:#FFF;border:none;border-radius:4px;"
            "padding:8px 16px;font-weight:600;font-family:'Segoe UI';}}"
            "QPushButton:hover{background:#005EA3;}"
        )
        skip_btn.setStyleSheet(
            f"QPushButton{{background:transparent;color:{_T1};border:1px solid {_BORDER};"
            "border-radius:4px;padding:8px 16px;font-family:'Segoe UI';}}"
            f"QPushButton:hover{{background:{_BG};}}"
        )
        cancel_btn.setStyleSheet(
            "QPushButton{background:transparent;color:#DC2626;border:1px solid #FECACA;"
            "border-radius:4px;padding:8px 16px;font-family:'Segoe UI';}"
            "QPushButton:hover{background:#FEF2F2;}"
        )
        save_btn.clicked.connect(self._accept_checked)
        skip_btn.clicked.connect(self._accept_skip_all)
        cancel_btn.clicked.connect(self.reject)
        root.addWidget(buttons)

    def _checked_rows(self) -> Set[int]:
        rows: Set[int] = set()
        for cb, item in zip(self._checks, self._items):
            if cb.isChecked():
                rows.add(item.row)
        return rows

    def _check_all(self) -> None:
        for cb in self._checks:
            cb.setChecked(True)

    def _uncheck_all(self) -> None:
        for cb in self._checks:
            cb.setChecked(False)

    def _accept_checked(self) -> None:
        self._save_anyway_rows = self._checked_rows()
        self.accept()

    def _accept_skip_all(self) -> None:
        self._save_anyway_rows = set()
        self.accept()
