"""Resolve daily-import rows that need a date/description before continuing."""

from __future__ import annotations

from datetime import date, datetime
from typing import List

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QBrush, QColor, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDateEdit,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from tahmeed.services.daily_import_service import (
    DailyImportPreview,
    DailyImportProblemRow,
    REASON_EMPTY_DESCRIPTION,
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
# Match Master Expenses / Import Preview zebra + header.
_HDR_BG = "#F1F5F9"
_ROW_EVEN = "#FFFFFF"
_ROW_ODD = "#F1F5F9"
_ROW_H = 26

_HEADERS = ("Excel row", "Issue", "Description", "Truck", "Amount", "Set date", "Skip")


def _stripe_bg(row_idx: int) -> str:
    return _ROW_ODD if row_idx % 2 else _ROW_EVEN


def _force_light_text(widget: QWidget) -> None:
    """Keep readable dark text when Windows is in dark mode."""
    palette = widget.palette()
    for role, color in (
        (QPalette.Base, _WHITE),
        (QPalette.Window, _WHITE),
        (QPalette.Text, _T1),
        (QPalette.WindowText, _T1),
        (QPalette.Button, _WHITE),
        (QPalette.ButtonText, _T1),
        (QPalette.PlaceholderText, _T2),
        (QPalette.Highlight, _BLUE),
        (QPalette.HighlightedText, _WHITE),
    ):
        palette.setColor(role, QColor(color))
    widget.setPalette(palette)


def _reason_label(reason: str) -> str:
    if reason == REASON_MISSING_DATE:
        return "Missing / invalid date"
    if reason == REASON_EMPTY_DESCRIPTION:
        return "Missing description"
    return reason.replace("_", " ").title()


def _default_qdate(preview: DailyImportPreview) -> QDate:
    d = preview.primary_date or date.today()
    return QDate(d.year, d.month, d.day)


def _problem_qdate(problem: DailyImportProblemRow, fallback: QDate) -> QDate:
    if problem.date is not None:
        d = problem.date
        return QDate(d.year, d.month, d.day)
    return fallback


def _date_edit_style(bg: str) -> str:
    # Explicit text color — Windows dark mode otherwise paints white on light bg.
    return (
        f"QDateEdit {{ background: {_WHITE}; color: {_T1};"
        f" border: 1px solid {_BORDER}; border-radius: 3px;"
        " padding: 0 4px; min-height: 20px;"
        " font-family:'Segoe UI'; font-size: 12px; }"
        f"QDateEdit:disabled {{ background: {bg}; color: {_T2}; }}"
        f"QDateEdit::drop-down {{ border: none; width: 16px; }}"
        f"QDateEdit QLineEdit {{ background: {_WHITE}; color: {_T1}; }}"
    )


def _desc_edit_style(bg: str) -> str:
    return (
        f"QLineEdit {{ background: {_WHITE}; color: {_T1};"
        f" border: 1px solid {_BORDER}; border-radius: 3px;"
        " padding: 0 4px; min-height: 20px;"
        " font-family:'Segoe UI'; font-size: 12px; }"
        f"QLineEdit:disabled {{ background: {bg}; color: {_T2}; }}"
        f"QLineEdit::placeholder {{ color: {_T2}; }}"
    )


def _skip_btn_style(*, skipped: bool, bg: str) -> str:
    # Wide enough for both "Skip" and "Undo skip" without clipping.
    if skipped:
        return (
            f"QPushButton {{ background: #ECFDF5; color: #059669;"
            f" border: 1px solid #A7F3D0; border-radius: 4px;"
            " font-family:'Segoe UI'; font-size: 11px; font-weight: 600;"
            " padding: 0 8px; min-height: 22px; min-width: 78px; }"
            "QPushButton:hover { background: #D1FAE5; }"
        )
    return (
        f"QPushButton {{ background: {_WHITE}; color: {_AMBER};"
        f" border: 1px solid {_BORDER}; border-radius: 4px;"
        " font-family:'Segoe UI'; font-size: 11px; font-weight: 600;"
        " padding: 0 8px; min-height: 22px; min-width: 78px; }"
        f"QPushButton:hover {{ background: {_HDR_BG}; border-color: {_AMBER}; }}"
        f"QPushButton:disabled {{ background: {bg}; color: {_T2}; }}"
    )


def _summary_copy(problems: List[DailyImportProblemRow]) -> tuple[str, str]:
    n = len(problems)
    missing_date = sum(1 for p in problems if p.reason == REASON_MISSING_DATE)
    missing_desc = sum(1 for p in problems if p.reason == REASON_EMPTY_DESCRIPTION)
    if missing_date and missing_desc:
        title = "Some rows need a date or description before import"
        info = (
            f"<b>{n}</b> row(s) need attention "
            f"({missing_date} missing date, {missing_desc} missing description). "
            "Fill the blank fields to keep them, or skip so they are dropped "
            "on purpose — nothing is left out silently."
        )
    elif missing_desc:
        title = "Some rows need a description before import"
        info = (
            f"<b>{n}</b> row(s) have an amount but a blank Description cell. "
            "Enter a description to keep them, or skip so they are dropped "
            "on purpose — nothing is left out silently."
        )
    else:
        title = "Some rows need a date before import"
        info = (
            f"<b>{n}</b> row(s) have a description but a missing or invalid "
            "Date cell. Set a date to keep them, or skip so they are dropped "
            "on purpose — nothing is left out silently."
        )
    return title, info


class ImportProblemRowsDialog(QDialog):
    """Flag problem import rows: fix date/description, skip one, or skip all."""

    def __init__(self, preview: DailyImportPreview, parent=None) -> None:
        super().__init__(parent)
        self._preview = preview
        self._problems: List[DailyImportProblemRow] = list(preview.problem_rows)
        self._desc_edits: List[QLineEdit] = []
        self._date_edits: List[QDateEdit] = []
        self._skip_btns: List[QPushButton] = []
        self._skip_flags: List[bool] = [False] * len(self._problems)
        self.setWindowTitle("Fix import rows")
        # Compact fixed footprint — avoids a sparse full-width sheet on large monitors.
        self.setMinimumSize(640, 300)
        self.setMaximumWidth(760)
        self.resize(700, 360)
        self.setModal(True)
        self._build()

    def _build(self) -> None:
        self.setStyleSheet(
            f"QDialog {{ background: {_BG}; color: {_T1}; }}"
            f"QLabel {{ border: none; background: transparent; color: {_T1}; }}"
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(8)

        title_text, info_text = _summary_copy(self._problems)
        title = QLabel(title_text)
        title.setStyleSheet(
            f"color: {_T1}; font-size: 15px; font-weight: 700;"
        )
        root.addWidget(title)

        info = QLabel(info_text)
        info.setWordWrap(True)
        info.setStyleSheet(f"color: {_T2}; font-size: 12px;")
        root.addWidget(info)

        card = QFrame()
        card.setObjectName("problemCard")
        card.setStyleSheet(
            f"QFrame#problemCard {{ background: {_WHITE};"
            f" border: 1px solid {_BORDER}; border-radius: 8px; }}"
        )
        cvl = QVBoxLayout(card)
        cvl.setContentsMargins(8, 8, 8, 8)
        cvl.setSpacing(0)

        n = len(self._problems)
        table = QTableWidget(n, len(_HEADERS))
        table.setHorizontalHeaderLabels(list(_HEADERS))
        table.setSelectionMode(QAbstractItemView.NoSelection)
        table.setFocusPolicy(Qt.NoFocus)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(_ROW_H)
        # Manual zebra — same white / slate-100 as Master Expenses.
        table.setAlternatingRowColors(False)
        table.setStyleSheet(
            f"QTableWidget {{ background: {_WHITE}; gridline-color: {_BORDER};"
            f" border: 1px solid {_BORDER}; border-radius: 6px;"
            " font-family:'Segoe UI'; font-size: 12px; color: #111827; }"
            f"QHeaderView::section {{ background: {_HDR_BG}; color: {_T1};"
            f" padding: 4px 6px; border: none; border-bottom: 1px solid {_BORDER};"
            " font-weight: 600; }"
            f"QTableWidget::item {{ padding: 1px 6px; color: {_T1}; }}"
        )
        hh = table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)  # Excel row
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)  # Issue
        hh.setSectionResizeMode(2, QHeaderView.Stretch)  # Description
        hh.setSectionResizeMode(3, QHeaderView.ResizeToContents)  # Truck
        hh.setSectionResizeMode(4, QHeaderView.ResizeToContents)  # Amount
        hh.setSectionResizeMode(5, QHeaderView.Fixed)  # Set date
        hh.resizeSection(5, 118)
        hh.setSectionResizeMode(6, QHeaderView.Fixed)  # Skip
        hh.resizeSection(6, 92)

        default_qd = _default_qdate(self._preview)
        for i, problem in enumerate(self._problems):
            bg_hex = _stripe_bg(i)
            bg = QBrush(QColor(bg_hex))
            vals = [
                str(problem.excel_row),
                _reason_label(problem.reason),
                problem.truck_number or "—",
                f"{problem.amount:,.0f}" if problem.amount else "—",
            ]
            # Columns: 0 Excel row, 1 Issue, 3 Truck, 4 Amount
            # (2 Description and 5/6 actions use cell widgets)
            col_map = {0: vals[0], 1: vals[1], 3: vals[2], 4: vals[3]}
            for c, text in col_map.items():
                it = QTableWidgetItem(text)
                it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                it.setBackground(bg)
                if c == 1:
                    it.setForeground(QColor(_AMBER))
                if c == 4:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                table.setItem(i, c, it)

            for c in (2, 5, 6):
                spacer = QTableWidgetItem("")
                spacer.setFlags(Qt.ItemIsEnabled)
                spacer.setBackground(bg)
                table.setItem(i, c, spacer)

            desc_edit = QLineEdit()
            desc_edit.setText(problem.description or "")
            desc_edit.setPlaceholderText("Enter description")
            _force_light_text(desc_edit)
            desc_edit.setStyleSheet(_desc_edit_style(bg_hex))
            self._desc_edits.append(desc_edit)
            table.setCellWidget(i, 2, desc_edit)

            date_edit = QDateEdit()
            date_edit.setCalendarPopup(True)
            date_edit.setDisplayFormat("dd/MM/yyyy")
            date_edit.setDate(_problem_qdate(problem, default_qd))
            _force_light_text(date_edit)
            date_edit.setStyleSheet(_date_edit_style(bg_hex))
            self._date_edits.append(date_edit)
            table.setCellWidget(i, 5, date_edit)

            skip_btn = QPushButton("Skip")
            skip_btn.setCursor(Qt.PointingHandCursor)
            skip_btn.setFlat(False)
            skip_btn.setAutoDefault(False)
            skip_btn.setDefault(False)
            _force_light_text(skip_btn)
            skip_btn.setStyleSheet(_skip_btn_style(skipped=False, bg=bg_hex))
            skip_btn.clicked.connect(lambda _=False, idx=i: self._toggle_skip(idx))
            self._skip_btns.append(skip_btn)
            table.setCellWidget(i, 6, skip_btn)
            table.setRowHeight(i, _ROW_H)

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
            " font-size: 13px; padding: 0 14px; min-height: 30px; }"
        )
        cancel.clicked.connect(self.reject)
        btn_row.addWidget(cancel)
        btn_row.addStretch()

        skip_all = QPushButton("Skip All Problems")
        skip_all.setCursor(Qt.PointingHandCursor)
        skip_all.setStyleSheet(
            f"QPushButton {{ background: {_WHITE}; color: {_T1};"
            f" border: 1px solid {_BORDER}; border-radius: 5px;"
            " font-size: 13px; padding: 0 14px; min-height: 30px; }"
        )
        skip_all.clicked.connect(self._skip_all)
        btn_row.addWidget(skip_all)

        ok = QPushButton("Apply & Continue")
        ok.setCursor(Qt.PointingHandCursor)
        ok.setStyleSheet(
            f"QPushButton {{ background: {_BLUE}; color: #FFF; border: none;"
            " border-radius: 5px; font-size: 13px; font-weight: 600;"
            " padding: 0 14px; min-height: 30px; }"
            "QPushButton:hover { background: #005EA3; }"
        )
        ok.clicked.connect(self._on_apply)
        btn_row.addWidget(ok)
        root.addLayout(btn_row)

    def _toggle_skip(self, idx: int) -> None:
        self._skip_flags[idx] = not self._skip_flags[idx]
        skipped = self._skip_flags[idx]
        self._desc_edits[idx].setEnabled(not skipped)
        self._date_edits[idx].setEnabled(not skipped)
        btn = self._skip_btns[idx]
        btn.setText("Undo skip" if skipped else "Skip")
        btn.setStyleSheet(
            _skip_btn_style(skipped=skipped, bg=_stripe_bg(idx))
        )
        self._status.setText("")

    def _skip_all(self) -> None:
        for i in range(len(self._problems)):
            if not self._skip_flags[i]:
                self._skip_flags[i] = True
                self._desc_edits[i].setEnabled(False)
                self._date_edits[i].setEnabled(False)
        self._apply_to_problems()
        self.accept()

    def _on_apply(self) -> None:
        self._apply_to_problems()
        for problem in self._problems:
            if problem.skipped:
                continue
            if not (problem.description or "").strip():
                self._status.setText(
                    "Every row must have a description or be skipped before continuing."
                )
                return
            if problem.date is None:
                self._status.setText(
                    "Every row must have a date or be skipped before continuing."
                )
                return
        self.accept()

    def _apply_to_problems(self) -> None:
        for i, problem in enumerate(self._problems):
            if self._skip_flags[i]:
                problem.skipped = True
                continue
            problem.skipped = False
            problem.description = self._desc_edits[i].text().strip().upper()
            qd = self._date_edits[i].date()
            problem.date = datetime(qd.year(), qd.month(), qd.day())


async def prompt_import_problems(
    preview: DailyImportPreview,
    parent=None,
) -> bool:
    """Show the problem dialog when needed; absorb fixes into the preview.

    Returns False if the user cancels the import.

    The modal ``exec()`` runs via ``await_ui`` so this asyncio task is
    suspended (not current) while Qt pumps events — otherwise Python 3.14
    + qasync raises ``Cannot enter into task … while another task …``.
    """
    if not preview.problem_rows:
        return True
    from tahmeed.ui.async_utils import pause_background_polls
    from tahmeed.ui.widgets.export_runner import await_ui

    def _show() -> bool:
        dlg = ImportProblemRowsDialog(preview, parent=parent)
        return dlg.exec() == ImportProblemRowsDialog.Accepted

    with pause_background_polls(parent):
        accepted = await await_ui(_show)
    if not accepted:
        return False
    await absorb_import_problems(preview)
    return True
