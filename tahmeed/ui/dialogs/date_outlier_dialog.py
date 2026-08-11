"""Dialogs / helpers for assigning one register day to a daily Excel upload."""

from __future__ import annotations

from datetime import date
from typing import Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
)

from tahmeed.services.daily_import_service import (
    DailyImportPreview,
    apply_date_policy,
)

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
    """Ask which register day to file the upload under when majority is unclear."""

    def __init__(
        self,
        candidates: List[date],
        counts: Dict[date, int],
        total_rows: int,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._chosen: Optional[date] = candidates[0] if candidates else None
        self.setWindowTitle("Choose Register Date")
        self.setMinimumWidth(540)
        self.setModal(True)
        self._build(candidates, counts, total_rows)

    def _build(
        self,
        candidates: List[date],
        counts: Dict[date, int],
        total_rows: int,
    ) -> None:
        self.setStyleSheet(
            f"QDialog {{ background: {_BG}; color: {_T1}; }}"
            f"QLabel {{ border: none; background: transparent; color: {_T1}; }}"
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)

        title = QLabel("No clear majority register date")
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
            f"This file has <b>{total_rows}</b> rows and several dates are tied "
            "for most common. Pick <b>one register date</b> for the whole upload "
            "(how it is filed and opened). Excel row dates stay as written — "
            "they are not changed and are not treated as discrepancies."
        )
        info.setWordWrap(True)
        info.setStyleSheet(f"color: {_T2}; font-size: 12px;")
        cvl.addWidget(info)
        root.addWidget(card)

        self._group = QButtonGroup(self)
        ordered = list(candidates)
        for d in sorted(counts.keys()):
            if d not in ordered:
                ordered.append(d)
        for i, d in enumerate(ordered):
            n = int(counts.get(d, 0))
            label = f"{d.strftime('%d/%m/%Y')}  ({n} row{'s' if n != 1 else ''})"
            rb = QRadioButton(label)
            rb.setProperty("alloc_date", d.isoformat())
            rb.setStyleSheet(
                f"color: {_T1}; font-size: 13px; font-weight: 600;"
            )
            if i == 0:
                rb.setChecked(True)
            self._group.addButton(rb, i)
            root.addWidget(rb)

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

        ok = QPushButton("Use As Register Date")
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
        btn = self._group.checkedButton()
        if btn is not None:
            raw = btn.property("alloc_date")
            if raw:
                self._chosen = date.fromisoformat(str(raw))
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
        )
        self._choice = FORCE_PRIMARY
        self.setWindowTitle("Choose Register Date")

    def choice(self) -> str:
        return FORCE_PRIMARY


def resolve_import_date_policy(preview: DailyImportPreview, parent=None) -> bool:
    """Pick the upload's register day; keep every Excel row date as-is.

    - Clear majority (or single date): that day is the register date.
    - Unclear tie: ask which register date to file the batch under.
    Returns False if the user cancels.
    """
    if not preview.rows:
        apply_date_policy(preview)
        return True

    if preview.outlier_count == 0 and preview.primary_date is not None:
        apply_date_policy(preview)
        return True

    if preview.date_majority_clear and preview.primary_date is not None:
        apply_date_policy(preview)
        return True

    candidates = sorted(
        preview.date_counts.keys(),
        key=lambda d: (-int(preview.date_counts.get(d, 0)), d),
    )
    if not candidates:
        candidates = list(preview.detected_dates)
    if not candidates and preview.primary_date is not None:
        candidates = [preview.primary_date]
    if not candidates:
        apply_date_policy(preview)
        return True

    if preview.date_counts:
        max_n = max(preview.date_counts.values())
        tied = sorted(d for d, n in preview.date_counts.items() if n == max_n)
        prompt_candidates = tied or candidates
    else:
        prompt_candidates = candidates

    dlg = DateAllocationDialog(
        prompt_candidates,
        preview.date_counts or {d: 0 for d in candidates},
        len(preview.rows),
        parent=parent,
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
