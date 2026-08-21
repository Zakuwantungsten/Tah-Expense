"""Resolve daily-import rows that are missing/invalid dates before continuing."""

from __future__ import annotations

from datetime import date, datetime
from typing import List

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDateEdit,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from tahmeed.services.daily_import_service import (
    DailyImportPreview,
    DailyImportProblemRow,
    REASON_MISSING_DATE,
    absorb_import_problems,
)

_WHITE = "#FFFFFF"
_BG = "#F4F6F8"
_BORDER = "#E5E7EB"
_BLUE = "#0077C5"
_T1 = "#111827"
_T2 = "#6B7280"
_AMBER = "#B45309"

_HEADERS = ("Excel row", "Issue", "Description", "Truck", "Amount", "Set date", "Skip")


def _reason_label(reason: str) -> str:
    if reason == REASON_MISSING_DATE:
        return "Missing / invalid date"
    return reason.replace("_", " ").title()


def _default_qdate(preview: DailyImportPreview) -> QDate:
    d = preview.primary_date or date.today()
    return QDate(d.year, d.month, d.day)


class ImportProblemRowsDialog(QDialog):
    """Flag problem import rows: assign a date, skip one, or skip all."""

    def __init__(self, preview: DailyImportPreview, parent=None) -> None:
        super().__init__(parent)
        self._preview = preview
        self._problems: List[DailyImportProblemRow] = list(preview.problem_rows)
        self._date_edits: List[QDateEdit] = []
        self._skip_flags: List[bool] = [False] * len(self._problems)
        self.setWindowTitle("Fix import rows")
        self.setMinimumSize(820, 420)
        self.setModal(True)
        self._build()

    def _build(self) -> None:
        self.setStyleSheet(
            f"QDialog {{ background: {_BG}; color: {_T1}; }}"
            f"QLabel {{ border: none; background: transparent; color: {_T1}; }}"
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)

        title = QLabel("Some rows need a date before import")
        title.setStyleSheet(
            f"color: {_T1}; font-size: 16px; font-weight: 700;"
        )
        root.addWidget(title)

        n = len(self._problems)
        info = QLabel(
            f"<b>{n}</b> row(s) have a description but a missing or invalid "
            "Date cell. Set a date to keep them, or skip so they are dropped "
            "on purpose — nothing is left out silently."
        )
        info.setWordWrap(True)
        info.setStyleSheet(f"color: {_T2}; font-size: 12px;")
        root.addWidget(info)

        card = QFrame()
        card.setObjectName("problemCard")
        card.setStyleSheet(
            f"QFrame#problemCard {{ background: {_WHITE};"
            f" border: 1px solid {_BORDER}; border-radius: 10px; }}"
        )
        cvl = QVBoxLayout(card)
        cvl.setContentsMargins(12, 10, 12, 10)

        table = QTableWidget(n, len(_HEADERS))
        table.setHorizontalHeaderLabels(list(_HEADERS))
        table.setSelectionMode(QAbstractItemView.NoSelection)
        table.setFocusPolicy(Qt.NoFocus)
        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(True)
        table.setStyleSheet(
            "QTableWidget { background: #fff; gridline-color: #e5e7eb;"
            " border: 1px solid #e5e7eb; border-radius: 6px; }"
            "QHeaderView::section { background: #253A5C; color: #F9FAFB;"
            " padding: 6px; border: none; font-weight: 600; }"
        )
        hh = table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(6, QHeaderView.ResizeToContents)

        default_qd = _default_qdate(self._preview)
        for i, problem in enumerate(self._problems):
            vals = [
                str(problem.excel_row),
                _reason_label(problem.reason),
                problem.description or "—",
                problem.truck_number or "—",
                f"{problem.amount:,.0f}" if problem.amount else "—",
            ]
            for c, text in enumerate(vals):
                it = QTableWidgetItem(text)
                it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                if c == 1:
                    it.setForeground(QColor(_AMBER))
                table.setItem(i, c, it)

            date_edit = QDateEdit()
            date_edit.setCalendarPopup(True)
            date_edit.setDisplayFormat("dd/MM/yyyy")
            date_edit.setDate(default_qd)
            date_edit.setStyleSheet(
                f"QDateEdit {{ background: {_WHITE}; border: 1px solid {_BORDER};"
                " border-radius: 4px; padding: 2px 6px; min-height: 26px; }"
            )
            self._date_edits.append(date_edit)
            table.setCellWidget(i, 5, date_edit)

            skip_btn = QPushButton("Skip")
            skip_btn.setCursor(Qt.PointingHandCursor)
            skip_btn.setStyleSheet(
                f"QPushButton {{ background: {_WHITE}; color: {_AMBER};"
                f" border: 1px solid {_BORDER}; border-radius: 4px;"
                " font-size: 12px; padding: 0 10px; min-height: 26px; }}"
            )
            skip_btn.clicked.connect(lambda _=False, idx=i: self._toggle_skip(idx))
            table.setCellWidget(i, 6, skip_btn)

        table.resizeRowsToContents()
        cvl.addWidget(table)
        root.addWidget(card, 1)

        self._status = QLabel("")
        self._status.setStyleSheet(f"color: {_AMBER}; font-size: 12px;")
        self._status.setWordWrap(True)
        root.addWidget(self._status)

        btn_row = QHBoxLayout()
        cancel = QPushButton("Cancel Import")
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.setStyleSheet(
            f"QPushButton {{ background: {_WHITE}; color: {_T2};"
            f" border: 1px solid {_BORDER}; border-radius: 5px;"
            " font-size: 13px; padding: 0 16px; min-height: 34px; }}"
        )
        cancel.clicked.connect(self.reject)
        btn_row.addWidget(cancel)
        btn_row.addStretch()

        skip_all = QPushButton("Skip All Problems")
        skip_all.setCursor(Qt.PointingHandCursor)
        skip_all.setStyleSheet(
            f"QPushButton {{ background: {_WHITE}; color: {_T1};"
            f" border: 1px solid {_BORDER}; border-radius: 5px;"
            " font-size: 13px; padding: 0 16px; min-height: 34px; }}"
        )
        skip_all.clicked.connect(self._skip_all)
        btn_row.addWidget(skip_all)

        ok = QPushButton("Apply & Continue")
        ok.setCursor(Qt.PointingHandCursor)
        ok.setStyleSheet(
            f"QPushButton {{ background: {_BLUE}; color: #FFF; border: none;"
            " border-radius: 5px; font-size: 13px; font-weight: 600;"
            " padding: 0 16px; min-height: 34px; }}"
            "QPushButton:hover { background: #005EA3; }"
        )
        ok.clicked.connect(self._on_apply)
        btn_row.addWidget(ok)
        root.addLayout(btn_row)

    def _toggle_skip(self, idx: int) -> None:
        self._skip_flags[idx] = not self._skip_flags[idx]
        skipped = self._skip_flags[idx]
        self._date_edits[idx].setEnabled(not skipped)
        btn = self.sender()
        if isinstance(btn, QPushButton):
            btn.setText("Undo skip" if skipped else "Skip")
            btn.setStyleSheet(
                f"QPushButton {{ background: {_WHITE}; color: "
                f"{'#059669' if skipped else _AMBER};"
                f" border: 1px solid {_BORDER}; border-radius: 4px;"
                " font-size: 12px; padding: 0 10px; min-height: 26px; }}"
            )
        self._status.setText("")

    def _skip_all(self) -> None:
        for i in range(len(self._problems)):
            if not self._skip_flags[i]:
                self._skip_flags[i] = True
                self._date_edits[i].setEnabled(False)
        self._apply_to_problems()
        self.accept()

    def _on_apply(self) -> None:
        self._apply_to_problems()
        for problem in self._problems:
            if not problem.skipped and problem.date is None:
                self._status.setText(
                    "Every row must have a date or be skipped before continuing."
                )
                return
        self.accept()

    def _apply_to_problems(self) -> None:
        for i, problem in enumerate(self._problems):
            if self._skip_flags[i]:
                problem.skipped = True
                problem.date = None
                continue
            problem.skipped = False
            qd = self._date_edits[i].date()
            problem.date = datetime(qd.year(), qd.month(), qd.day())


async def prompt_import_problems(
    preview: DailyImportPreview,
    parent=None,
) -> bool:
    """Show the problem dialog when needed; absorb fixes into the preview.

    Returns False if the user cancels the import.
    """
    if not preview.problem_rows:
        return True
    dlg = ImportProblemRowsDialog(preview, parent=parent)
    if dlg.exec() != ImportProblemRowsDialog.Accepted:
        return False
    await absorb_import_problems(preview)
    return True
