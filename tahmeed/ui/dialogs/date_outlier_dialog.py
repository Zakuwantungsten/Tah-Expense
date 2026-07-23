"""Dialog when an Excel import has a few rows on dates other than the main day."""

from __future__ import annotations

from datetime import date
from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame, QRadioButton,
    QButtonGroup,
)

_WHITE = "#FFFFFF"
_BG = "#F4F6F8"
_BORDER = "#E5E7EB"
_BLUE = "#0077C5"
_T1 = "#111827"
_T2 = "#6B7280"
_AMBER = "#B45309"


# Result codes
KEEP_AND_FLAG = "keep_flag"      # leave Excel dates; flag outliers for Issues
FORCE_PRIMARY = "force_primary"  # rewrite all to primary date
KEEP_AS_IS = "keep_as_is"        # leave Excel dates; no Issues flag


class DateOutlierDialog(QDialog):
    """Ask how to treat rows whose date differs from the import's main date."""

    def __init__(
        self,
        primary_date: date,
        outlier_count: int,
        all_dates: List[date],
        total_rows: int,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._choice = KEEP_AND_FLAG
        self.setWindowTitle("Mixed Dates in Excel")
        self.setMinimumWidth(520)
        self.setModal(True)
        self._build(primary_date, outlier_count, all_dates, total_rows)

    def _build(
        self,
        primary_date: date,
        outlier_count: int,
        all_dates: List[date],
        total_rows: int,
    ) -> None:
        self.setStyleSheet(
            f"QDialog {{ background: {_BG}; }}"
            "QLabel { border: none; background: transparent; }"
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)

        title = QLabel("Some rows use a different date")
        title.setStyleSheet(
            f"color: {_T1}; font-size: 16px; font-weight: 700;"
            " border: none; background: transparent;"
        )
        root.addWidget(title)

        card = QFrame()
        card.setObjectName("dateOutlierCard")
        card.setStyleSheet(
            f"QFrame#dateOutlierCard {{ background-color: {_WHITE};"
            f" border: 1px solid {_BORDER}; border-radius: 12px; }}"
        )
        cvl = QVBoxLayout(card)
        cvl.setContentsMargins(14, 12, 14, 12)
        cvl.setSpacing(6)

        main_lbl = QLabel(
            f"Main date (most rows): <b>{primary_date.strftime('%d/%m/%Y')}</b>"
        )
        main_lbl.setStyleSheet(f"color: {_T1}; font-size: 13px;")
        cvl.addWidget(main_lbl)

        other = [d for d in all_dates if d != primary_date]
        other_txt = ", ".join(d.strftime("%d/%m/%Y") for d in other) or "—"
        out_lbl = QLabel(
            f"<span style='color:{_AMBER}'>{outlier_count}</span> of {total_rows} rows "
            f"use other date(s): <b>{other_txt}</b>"
        )
        out_lbl.setWordWrap(True)
        out_lbl.setStyleSheet(f"color: {_T2}; font-size: 12px;")
        cvl.addWidget(out_lbl)
        root.addWidget(card)

        self._group = QButtonGroup(self)
        opts = [
            (
                KEEP_AND_FLAG,
                "Keep Excel dates and flag for accountant Issues",
                "Rows keep their dates; outliers appear under Verify → Issues.",
            ),
            (
                FORCE_PRIMARY,
                f"Change all rows to {primary_date.strftime('%d/%m/%Y')}",
                "Overwrite every row date with the main detected date.",
            ),
            (
                KEEP_AS_IS,
                "Leave dates as they are (no Issues flag)",
                "Rows keep their Excel dates and are not marked as a discrepancy.",
            ),
        ]
        for i, (code, label, hint) in enumerate(opts):
            rb = QRadioButton(label)
            rb.setProperty("choice", code)
            rb.setStyleSheet(
                f"color: {_T1}; font-size: 13px; font-weight: 600;"
            )
            if i == 0:
                rb.setChecked(True)
            self._group.addButton(rb, i)
            root.addWidget(rb)
            hint_lbl = QLabel(hint)
            hint_lbl.setStyleSheet(
                f"color: {_T2}; font-size: 11px; margin-left: 22px;"
            )
            hint_lbl.setWordWrap(True)
            root.addWidget(hint_lbl)

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

        ok = QPushButton("Continue")
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
            self._choice = btn.property("choice") or KEEP_AND_FLAG
        self.accept()

    def choice(self) -> str:
        return self._choice
