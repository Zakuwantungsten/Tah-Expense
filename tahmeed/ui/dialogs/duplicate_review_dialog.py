"""Batch review when register rows match recent transactions.

Each match is shown as a compact pair: the new row on top, the existing
expense underneath (expense/saved tint) so both can be compared at a glance.
"""

from __future__ import annotations

from typing import List, Set

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
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
    format_amount_label,
    format_existing_date,
)

_WHITE = "#FFFFFF"
_BG = "#F4F6F8"
_T1 = "#111827"
_T2 = "#6B7280"
_BLUE = "#0077C5"
_BORDER = "#E5E7EB"
_AMBER = "#B45309"
_NEW_BG = "#FEE2E2"       # light red — candidate being saved
_EXISTING_BG = "#fff8f0"  # matches register saved/expense row tint
_ROW_H = 22

_HEADERS = (
    "Save",
    "",
    "#",
    "Date",
    "Description",
    "Truck",
    "Amount",
    "Item",
)


def _cell(
    text: object,
    *,
    bg: str,
    align=Qt.AlignLeft | Qt.AlignVCenter,
    bold: bool = False,
) -> QTableWidgetItem:
    item = QTableWidgetItem("" if text is None else str(text))
    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    item.setTextAlignment(align)
    item.setBackground(QBrush(QColor(bg)))
    if bold:
        font = item.font()
        font.setBold(True)
        item.setFont(font)
    return item


class DuplicateReviewDialog(QDialog):
    """Review possible duplicates — new row + matching existing expense."""

    def __init__(
        self,
        items: List[DuplicateReviewItem],
        *,
        dup_days: int,
        parent: QWidget | None = None,
        pending_total: int | None = None,
    ) -> None:
        super().__init__(parent)
        self._items = list(items)
        self._dup_days = max(1, int(dup_days))
        self._pending_total = (
            int(pending_total)
            if pending_total is not None
            else len(self._items)
        )
        self._save_anyway_rows: Set[int] = set()
        self._keep_for_later = False
        self.setWindowTitle("Possible duplicates")
        self.setMinimumSize(740, 300)
        self.resize(820, 360)
        self.setStyleSheet(f"background:{_WHITE}; color:{_T1};")
        self._build()

    def save_anyway_rows(self) -> Set[int]:
        return set(self._save_anyway_rows)

    def deferred_duplicate_rows(self) -> Set[int]:
        """Dup rows intentionally left on the table (not saved in this pass)."""
        all_dup = {item.row for item in self._items}
        return all_dup - set(self._save_anyway_rows)

    def kept_for_later(self) -> bool:
        """True when the cashier chose Keep for later (defer all dups)."""
        return bool(self._keep_for_later)

    def _looks_like_reimport(self) -> bool:
        n = len(self._items)
        if n < 2:
            return False
        return n >= max(2, int(self._pending_total * 0.8))

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        n = len(self._items)
        window = (
            f"last {self._dup_days} day"
            if self._dup_days == 1
            else f"last {self._dup_days} days"
        )
        reimport = self._looks_like_reimport()
        if reimport:
            title = QLabel(
                f"Looks like a reimport — {n} of {self._pending_total} new "
                f"row{'s' if self._pending_total != 1 else ''} already exist "
                f"({window})"
            )
            title.setStyleSheet(
                f"font-size:14px;font-weight:700;color:{_AMBER};"
                "font-family:'Segoe UI',sans-serif;"
            )
        else:
            title = QLabel(
                f"{n} possible duplicate{'s' if n != 1 else ''} · checked {window}"
            )
            title.setStyleSheet(
                f"font-size:14px;font-weight:700;color:{_T1};"
                "font-family:'Segoe UI',sans-serif;"
            )
        root.addWidget(title)

        if reimport:
            subtitle = QLabel(
                "Most of these rows already match expenses in the system — "
                "often from re-importing the same Excel file. Prefer "
                "Keep for later unless you are sure each new row is a separate "
                "expense. Each pair shows the new row (red) above the existing "
                "expense (cream)."
            )
        else:
            subtitle = QLabel(
                "Each pair shows the new row (red) above the existing expense (cream). "
                "Tick Save to keep the new row anyway — it will be flagged for "
                "Verify → Issues. Unchecked pairs stay on the table for later "
                "(or use Keep for later)."
            )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(
            f"font-size:11px;color:{_T2};font-family:'Segoe UI',sans-serif;"
        )
        root.addWidget(subtitle)

        toggle_row = QHBoxLayout()
        toggle_row.setSpacing(6)
        self._select_all = QPushButton("Select all")
        self._select_none = QPushButton("Select none")
        for btn in (self._select_all, self._select_none):
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(24)
            btn.setStyleSheet(
                f"QPushButton{{background:transparent;color:{_BLUE};border:1px solid {_BORDER};"
                "border-radius:3px;padding:2px 10px;font-size:11px;font-family:'Segoe UI';}"
                f"QPushButton:hover{{background:{_BG};}}"
            )
        self._select_all.clicked.connect(self._check_all)
        self._select_none.clicked.connect(self._uncheck_all)
        toggle_row.addWidget(self._select_all)
        toggle_row.addWidget(self._select_none)
        toggle_row.addStretch()
        tip = QLabel("Same truck/service on different days can be legitimate.")
        tip.setStyleSheet(
            f"font-size:11px;color:{_AMBER};font-family:'Segoe UI',sans-serif;"
        )
        toggle_row.addWidget(tip)
        root.addLayout(toggle_row)

        table_rows = len(self._items) * 2
        self._table = QTableWidget(table_rows, len(_HEADERS))
        self._table.setHorizontalHeaderLabels(list(_HEADERS))
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setSelectionMode(QTableWidget.NoSelection)
        self._table.setShowGrid(True)
        self._table.setStyleSheet(
            f"QTableWidget{{border:1px solid {_BORDER};gridline-color:{_BORDER};"
            "font-family:'Segoe UI';font-size:11px;}"
            "QHeaderView::section{background:#F9FAFB;padding:4px 6px;border:none;"
            f"border-bottom:1px solid {_BORDER};font-weight:600;color:{_T2};"
            "font-size:11px;}"
        )
        self._table.verticalHeader().setDefaultSectionSize(_ROW_H)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.Stretch)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(6, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(7, QHeaderView.ResizeToContents)

        self._checks: list[QCheckBox] = []
        for i, item in enumerate(self._items):
            new_r = i * 2
            old_r = new_r + 1
            self._fill_new_row(new_r, item)
            self._fill_existing_row(old_r, item)
            self._table.setRowHeight(new_r, _ROW_H)
            self._table.setRowHeight(old_r, _ROW_H)

        root.addWidget(self._table, 1)

        buttons = QDialogButtonBox()
        save_btn = buttons.addButton("Save checked", QDialogButtonBox.AcceptRole)
        keep_btn = buttons.addButton("Keep for later", QDialogButtonBox.ActionRole)
        keep_btn.setToolTip(
            "Leave these possible duplicates on the table without saving them. "
            "Click Save again when you are ready."
        )
        cancel_btn = buttons.addButton("Cancel", QDialogButtonBox.RejectRole)
        for btn, style in (
            (
                save_btn,
                f"QPushButton{{background:{_BLUE};color:#FFF;border:none;border-radius:4px;"
                "padding:6px 14px;font-weight:600;font-family:'Segoe UI';font-size:12px;}"
                "QPushButton:hover{background:#005EA3;}",
            ),
            (
                keep_btn,
                f"QPushButton{{background:transparent;color:{_T1};border:1px solid {_BORDER};"
                "border-radius:4px;padding:6px 14px;font-family:'Segoe UI';font-size:12px;}"
                f"QPushButton:hover{{background:{_BG};}}",
            ),
            (
                cancel_btn,
                "QPushButton{background:transparent;color:#DC2626;border:1px solid #FECACA;"
                "border-radius:4px;padding:6px 14px;font-family:'Segoe UI';font-size:12px;}"
                "QPushButton:hover{background:#FEF2F2;}",
            ),
        ):
            btn.setStyleSheet(style)
        save_btn.clicked.connect(self._accept_checked)
        keep_btn.clicked.connect(self._accept_keep_for_later)
        cancel_btn.clicked.connect(self.reject)
        root.addWidget(buttons)

    def _fill_new_row(self, r: int, item: DuplicateReviewItem) -> None:
        wrap = QWidget()
        wrap.setStyleSheet(f"background:{_NEW_BG};")
        lay = QHBoxLayout(wrap)
        lay.setContentsMargins(6, 0, 6, 0)
        lay.setAlignment(Qt.AlignCenter)
        cb = QCheckBox()
        cb.setToolTip("Save this new row anyway and flag as possible duplicate")
        lay.addWidget(cb)
        self._checks.append(cb)
        self._table.setCellWidget(r, 0, wrap)

        self._table.setItem(
            r, 1, _cell("New", bg=_NEW_BG, bold=True, align=Qt.AlignCenter)
        )
        self._table.setItem(
            r, 2, _cell(str(item.row_display), bg=_NEW_BG, align=Qt.AlignCenter)
        )
        self._table.setItem(
            r,
            3,
            _cell(item.date_label or "—", bg=_NEW_BG, align=Qt.AlignCenter),
        )
        self._table.setItem(r, 4, _cell(item.description or "—", bg=_NEW_BG))
        self._table.setItem(r, 5, _cell(item.truck_number or "—", bg=_NEW_BG))
        self._table.setItem(r, 6, _cell(item.amount_label, bg=_NEW_BG))
        self._table.setItem(r, 7, _cell(item.item or "—", bg=_NEW_BG))

    def _fill_existing_row(self, r: int, item: DuplicateReviewItem) -> None:
        bg = _EXISTING_BG
        empty = QWidget()
        empty.setStyleSheet(f"background:{bg};")
        self._table.setCellWidget(r, 0, empty)

        ex = item.existing
        self._table.setItem(
            r, 1, _cell("Existing", bg=bg, bold=True, align=Qt.AlignCenter)
        )
        self._table.setItem(r, 2, _cell("—", bg=bg, align=Qt.AlignCenter))
        self._table.setItem(
            r, 3, _cell(format_existing_date(ex.date), bg=bg, align=Qt.AlignCenter)
        )
        self._table.setItem(r, 4, _cell(ex.description or "—", bg=bg))
        self._table.setItem(r, 5, _cell(ex.truck_number or "—", bg=bg))
        self._table.setItem(r, 6, _cell(format_amount_label(ex), bg=bg))
        self._table.setItem(r, 7, _cell(ex.item or "—", bg=bg))

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
        self._keep_for_later = False
        self._save_anyway_rows = self._checked_rows()
        self.accept()

    def _accept_keep_for_later(self) -> None:
        """Defer all duplicate rows — leave them on the table for a later Save."""
        self._keep_for_later = True
        self._save_anyway_rows = set()
        self.accept()

    # Back-compat alias for older callers / tests.
    def _accept_skip_all(self) -> None:
        self._accept_keep_for_later()
