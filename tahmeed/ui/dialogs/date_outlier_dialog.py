"""Dialogs / helpers for assigning one reconciled day to a daily Excel upload."""

from __future__ import annotations

from datetime import date
from typing import Dict, List, Optional

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QDateEdit,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from tahmeed.services.daily_import_service import (
    DailyImportPreview,
    apply_date_policy,
    suggested_reconciled_date,
)
from tahmeed.ui.accountant.date_filters import style_calendar_popup

_WHITE = "#FFFFFF"
_BG = "#F4F6F8"
_BORDER = "#E5E7EB"
_BLUE = "#0077C5"
_T1 = "#111827"
_T2 = "#6B7280"

# Legacy choice codes (kept for any older callers / tests)
KEEP_AND_FLAG = "keep_flag"
FORCE_PRIMARY = "force_primary"
KEEP_AS_IS = "keep_as_is"


class DateAllocationDialog(QDialog):
    """Require an explicit reconciled date for the upload (majority is the default)."""

    def __init__(
        self,
        candidates: List[date],
        counts: Dict[date, int],
        total_rows: int,
        parent=None,
        *,
        default_date: Optional[date] = None,
    ) -> None:
        super().__init__(parent)
        self._chosen: Optional[date] = default_date or (
            candidates[0] if candidates else date.today()
        )
        self.setWindowTitle("Choose Reconciled Date")
        self.setMinimumWidth(540)
        self.setModal(True)
        self._build(candidates, counts, total_rows, self._chosen)

    def _build(
        self,
        candidates: List[date],
        counts: Dict[date, int],
        total_rows: int,
        default_date: date,
    ) -> None:
        self.setStyleSheet(
            f"QDialog {{ background: {_BG}; color: {_T1}; }}"
            f"QLabel {{ border: none; background: transparent; color: {_T1}; }}"
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)

        title = QLabel("Reconciled Date")
        title.setStyleSheet(
            f"color: {_T1}; font-size: 16px; font-weight: 700;"
            " border: none; background: transparent;"
        )
        root.addWidget(title)

        card = QFrame()
        card.setObjectName("dateAllocCard")
        card.setStyleSheet(
            f"QFrame#dateAllocCard {{ background-color: {_WHITE};"
            f" border: 1px solid {_BORDER}; border-radius: 12px; }}"
        )
        cvl = QVBoxLayout(card)
        cvl.setContentsMargins(14, 12, 14, 12)
        cvl.setSpacing(6)
        info = QLabel(
            f"This file has <b>{total_rows}</b> row(s). Pick <b>Reconciled Date</b> "
            "for the whole upload — how it is filed and opened in Simple and Uploads. "
            "Excel row dates stay as written and still appear that way in Master "
            "and reports."
        )
        info.setWordWrap(True)
        info.setStyleSheet(f"color: {_T2}; font-size: 12px;")
        cvl.addWidget(info)

        if counts:
            bits = [
                f"{d.strftime('%d/%m/%Y')} ({n} row{'s' if n != 1 else ''})"
                for d, n in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
            ]
            breakdown = QLabel("Dates in file: " + " · ".join(bits))
            breakdown.setWordWrap(True)
            breakdown.setStyleSheet(f"color: {_T2}; font-size: 12px;")
            cvl.addWidget(breakdown)
        root.addWidget(card)

        picker_row = QHBoxLayout()
        picker_row.setSpacing(8)
        lbl = QLabel("Reconciled Date")
        lbl.setStyleSheet(
            f"color: {_T1}; font-size: 13px; font-weight: 600;"
        )
        picker_row.addWidget(lbl)

        self._date_edit = QDateEdit()
        self._date_edit.setCalendarPopup(True)
        self._date_edit.setDisplayFormat("dd MMM yyyy")
        self._date_edit.setDate(
            QDate(default_date.year, default_date.month, default_date.day)
        )
        self._date_edit.setFixedHeight(34)
        self._date_edit.setMinimumWidth(150)
        self._date_edit.setStyleSheet(
            "QDateEdit {"
            f"  border: 1px solid {_BORDER}; border-radius: 5px;"
            "  padding: 0 8px; font-size: 13px; font-weight: 600;"
            f"  color: {_T1}; background: {_WHITE};"
            "}"
            f"QDateEdit:focus {{ border-color: {_BLUE}; }}"
        )
        style_calendar_popup(self._date_edit)
        picker_row.addWidget(self._date_edit)
        picker_row.addStretch()
        root.addLayout(picker_row)

        hint = QLabel(
            "Default is the majority date in the file. Change it if this batch "
            "should open under a different reconciled day."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {_T2}; font-size: 12px;")
        root.addWidget(hint)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel = QPushButton("Cancel Import")
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.setStyleSheet(
            f"QPushButton {{ background: {_WHITE}; color: {_T2};"
            f" border: 1px solid {_BORDER}; border-radius: 5px;"
            " font-size: 13px; padding: 0 16px; min-height: 34px; }}"
        )
        cancel.clicked.connect(self.reject)
        btn_row.addWidget(cancel)

        ok = QPushButton("Use As Reconciled Date")
        ok.setCursor(Qt.PointingHandCursor)
        ok.setStyleSheet(
            f"QPushButton {{ background: {_BLUE}; color: #FFF; border: none;"
            " border-radius: 5px; font-size: 13px; font-weight: 600;"
            " padding: 0 16px; min-height: 34px; }}"
            "QPushButton:hover { background: #005EA3; }"
        )
        ok.clicked.connect(self._on_ok)
        btn_row.addWidget(ok)
        root.addLayout(btn_row)

    def _on_ok(self) -> None:
        qd = self._date_edit.date()
        if not qd.isValid():
            return
        self._chosen = date(qd.year(), qd.month(), qd.day())
        self.accept()

    def chosen_date(self) -> Optional[date]:
        return self._chosen


# Back-compat alias
class DateOutlierDialog(DateAllocationDialog):
    def __init__(
        self,
        primary_date: date,
        outlier_count: int,
        all_dates: List[date],
        total_rows: int,
        parent=None,
        counts: Optional[Dict[date, int]] = None,
    ) -> None:
        counts = counts or {d: 0 for d in all_dates}
        if primary_date not in counts:
            counts = {
                **counts,
                primary_date: max(counts.values(), default=0) + outlier_count,
            }
        super().__init__(
            candidates=[primary_date] + [d for d in all_dates if d != primary_date],
            counts=counts,
            total_rows=total_rows,
            parent=parent,
            default_date=primary_date,
        )
        self._choice = FORCE_PRIMARY
        self.setWindowTitle("Choose Reconciled Date")

    def choice(self) -> str:
        return FORCE_PRIMARY


def resolve_import_date_policy(preview: DailyImportPreview, parent=None) -> bool:
    """Require an explicit reconciled day; keep every Excel row date as-is.

    Majority (or filename fallback) is the calendar default. The user must
    confirm before the import can continue. Returns False if they cancel.
    """
    if not preview.rows:
        apply_date_policy(preview)
        return True

    suggested = suggested_reconciled_date(preview)
    candidates = sorted(
        preview.date_counts.keys(),
        key=lambda d: (-int(preview.date_counts.get(d, 0)), d),
    )
    if not candidates:
        candidates = list(preview.detected_dates)
    if not candidates and suggested is not None:
        candidates = [suggested]
    if suggested is None:
        suggested = date.today()

    dlg = DateAllocationDialog(
        candidates,
        preview.date_counts or {d: 0 for d in candidates},
        len(preview.rows),
        parent=parent,
        default_date=suggested,
    )
    if dlg.exec() != DateAllocationDialog.Accepted:
        return False
    chosen = dlg.chosen_date()
    if chosen is None:
        return False
    preview.primary_date = chosen
    preview.outlier_count = sum(
        1 for r in preview.rows if r.date.date() != chosen
    )
    apply_date_policy(preview)
    return True
