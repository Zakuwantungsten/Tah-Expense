"""Warn when a daily Excel file looks like it was already imported."""

from __future__ import annotations

from typing import List

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from tahmeed.services.daily_import_service import PriorDailyImport

_WHITE = "#FFFFFF"
_BG = "#F4F6F8"
_T1 = "#111827"
_T2 = "#6B7280"
_BLUE = "#0077C5"
_BORDER = "#E5E7EB"
_AMBER = "#B45309"
_RED = "#DC2626"

_HEADERS = ("#", "Prior file", "Rows", "Imported", "Match")


def _fmt_when(dt) -> str:
    if dt is None:
        return "—"
    try:
        return dt.strftime("%d %b %Y  %H:%M")
    except Exception:
        return str(dt)


def _fmt_day(dt) -> str:
    if dt is None:
        return "—"
    try:
        return dt.strftime("%d %b %Y")
    except Exception:
        return str(dt)


def _cell(text: object, *, align=Qt.AlignLeft | Qt.AlignVCenter) -> QTableWidgetItem:
    item = QTableWidgetItem("" if text is None else str(text))
    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    item.setTextAlignment(align)
    return item


class DailyReimportDialog(QDialog):
    """Show prior daily uploads that match this Excel file."""

    def __init__(
        self,
        matches: List[PriorDailyImport],
        *,
        source_filename: str = "",
        row_count: int = 0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._matches = list(matches or [])
        self.setWindowTitle("File already imported?")
        self.setMinimumSize(680, 300)
        self.resize(720, 340)
        self.setStyleSheet(f"background:{_WHITE}; color:{_T1};")
        self._build(source_filename, row_count)

    def _build(self, source_filename: str, row_count: int) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(8)

        exact = any(m.reason == "exact_content" for m in self._matches)
        title = QLabel(
            "This exact file was already imported"
            if exact
            else "This file name was already imported"
        )
        title.setStyleSheet(
            f"font-size:14px;font-weight:700;color:{_RED if exact else _AMBER};"
            "font-family:'Segoe UI',sans-serif;"
        )
        root.addWidget(title)

        bits = []
        if source_filename:
            bits.append(f"File: {source_filename}")
        if row_count:
            bits.append(f"Rows in file: {row_count:,}")
        bits.append(f"Prior upload(s): {len(self._matches):,}")
        summary = QLabel("   ·   ".join(bits))
        summary.setStyleSheet(
            f"font-size:12px;color:{_T2};font-family:'Segoe UI',sans-serif;"
        )
        summary.setWordWrap(True)
        root.addWidget(summary)

        if exact:
            hint = (
                "The spreadsheet body matches a previous daily import exactly. "
                "Loading it again will stage the same expenses a second time — "
                "Save will then flag them as possible duplicates. "
                "Prefer opening the prior upload from Browse → Uploads unless you "
                "intentionally need a fresh copy."
            )
        else:
            hint = (
                "A file with this name is already in the system. Confirm it is a "
                "different spreadsheet (or an updated export) before continuing. "
                "If it is the same file, cancel and open the prior upload instead."
            )
        tip = QLabel(hint)
        tip.setWordWrap(True)
        tip.setStyleSheet(
            f"font-size:11px;color:{_T2};font-family:'Segoe UI',sans-serif;"
        )
        root.addWidget(tip)

        table = QTableWidget(0, len(_HEADERS))
        table.setHorizontalHeaderLabels(list(_HEADERS))
        table.setAlternatingRowColors(True)
        table.setSelectionMode(QTableWidget.NoSelection)
        table.verticalHeader().setVisible(False)
        table.setStyleSheet(
            f"QTableWidget{{border:1px solid {_BORDER};gridline-color:{_BORDER};"
            "font-family:'Segoe UI';font-size:11px;}"
            "QHeaderView::section{background:#F9FAFB;padding:4px 6px;border:none;"
            f"border-bottom:1px solid {_BORDER};font-weight:600;color:{_T2};}}"
        )
        hdr = table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeToContents)

        for i, m in enumerate(self._matches, start=1):
            r = table.rowCount()
            table.insertRow(r)
            match_label = (
                "Exact content" if m.reason == "exact_content" else "Same filename"
            )
            when = _fmt_when(m.created_at)
            if m.primary_date is not None:
                when = f"{when} · day {_fmt_day(m.primary_date)}"
            vals = [
                (str(i), Qt.AlignCenter),
                (m.source_filename or "—", Qt.AlignLeft),
                (f"{m.row_count:,}", Qt.AlignRight | Qt.AlignVCenter),
                (when, Qt.AlignLeft),
                (match_label, Qt.AlignCenter),
            ]
            for c, (text, align) in enumerate(vals):
                table.setItem(r, c, _cell(text, align=align | Qt.AlignVCenter))

        root.addWidget(table, 1)

        buttons = QDialogButtonBox()
        cancel = buttons.addButton("Cancel import", QDialogButtonBox.RejectRole)
        cont = buttons.addButton("Continue anyway", QDialogButtonBox.AcceptRole)
        cancel.setStyleSheet(
            f"QPushButton{{background:transparent;color:{_T1};border:1px solid {_BORDER};"
            "border-radius:4px;padding:6px 14px;font-family:'Segoe UI';}}"
            f"QPushButton:hover{{background:{_BG};}}"
        )
        cont.setStyleSheet(
            f"QPushButton{{background:{_BLUE};color:#FFF;border:none;border-radius:4px;"
            "padding:6px 14px;font-weight:600;font-family:'Segoe UI';}}"
            "QPushButton:hover{background:#005EA3;}"
        )
        cancel.clicked.connect(self.reject)
        cont.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(buttons)
        root.addLayout(row)
