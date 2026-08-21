"""Confirm preview before staging a daily Excel import into the table."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
)

from tahmeed.services.daily_import_service import DailyImportPreview
from tahmeed.ui.cashier.register_delegates import format_register_date

_WHITE = "#FFFFFF"
_BG = "#F4F6F8"
_BORDER = "#E5E7EB"
_BLUE = "#0077C5"
_T1 = "#111827"
_T2 = "#6B7280"

_PREVIEW_HEADERS = ("Date", "Description", "Truck", "Amount", "Item")
_PREVIEW_LIMIT = 10


class DailyImportPreviewDialog(QDialog):
    """Show import summary + sample rows; Confirm continues the import flow."""

    def __init__(
        self,
        preview: DailyImportPreview,
        parent=None,
        *,
        title: str = "Review import before loading",
        note: str = (
            "Confirm to load these rows into the Daily Register table. "
            "Nothing is saved until you click Save."
        ),
        confirm_label: str | None = None,
    ) -> None:
        super().__init__(parent)
        self._preview = preview
        self._title_text = title
        self._note_text = note
        self._confirm_label = confirm_label or f"Load {len(preview.rows):,} into Table"
        self.setWindowTitle("Import Preview")
        self.setMinimumSize(720, 420)
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

        title = QLabel(self._title_text)
        title.setStyleSheet(
            f"color: {_T1}; font-size: 16px; font-weight: 700;"
        )
        root.addWidget(title)

        p = self._preview
        primary = (
            p.primary_date.strftime("%d/%m/%Y") if p.primary_date else "—"
        )
        skipped = p.skipped_blank
        unmapped = sum(p.unmapped.values()) if p.unmapped else 0
        reasons = p.skip_reasons or {}
        reason_bits = []
        if reasons.get("missing_date_user_skip"):
            reason_bits.append(
                f"{reasons['missing_date_user_skip']:,} date problem(s) skipped"
            )
        if reasons.get("empty_description"):
            reason_bits.append(
                f"{reasons['empty_description']:,} blank description"
            )
        if reasons.get("total_row"):
            reason_bits.append(f"{reasons['total_row']:,} total line(s)")
        summary = QLabel(
            f"<b>{p.source_filename}</b><br>"
            f"{len(p.rows):,} row(s) ready · Register date <b>{primary}</b>"
            + (
                f" · Excel dates kept"
                f" ({p.outlier_count:,} row(s) on other dates)"
                if p.outlier_count > 0
                else ""
            )
            + (
                f" · {skipped:,} blank/skipped"
                + (f" ({', '.join(reason_bits)})" if reason_bits else "")
                if skipped
                else ""
            )
            + (f" · {unmapped:,} without item" if unmapped else "")
        )
        summary.setStyleSheet(f"color: {_T2}; font-size: 12px;")
        summary.setWordWrap(True)
        root.addWidget(summary)

        card = QFrame()
        card.setObjectName("previewCard")
        card.setStyleSheet(
            f"QFrame#previewCard {{ background: {_WHITE};"
            f" border: 1px solid {_BORDER}; border-radius: 10px; }}"
        )
        cvl = QVBoxLayout(card)
        cvl.setContentsMargins(12, 10, 12, 10)
        cvl.setSpacing(8)

        sample_n = min(_PREVIEW_LIMIT, len(p.rows))
        hint = QLabel(f"Preview (first {sample_n} of {len(p.rows):,})")
        hint.setStyleSheet(f"color: {_T1}; font-size: 12px; font-weight: 600;")
        cvl.addWidget(hint)

        table = QTableWidget(sample_n, len(_PREVIEW_HEADERS))
        table.setHorizontalHeaderLabels(list(_PREVIEW_HEADERS))
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
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
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.ResizeToContents)

        for i, row in enumerate(p.rows[:_PREVIEW_LIMIT]):
            amt = f"{row.amount:,.2f} {row.currency}" if row.amount else ""
            vals = (
                format_register_date(row.date),
                row.description or "",
                row.truck_number or "",
                amt,
                row.category_name or "—",
            )
            for c, text in enumerate(vals):
                it = QTableWidgetItem(text)
                it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                if c == 3:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                table.setItem(i, c, it)

        cvl.addWidget(table)
        root.addWidget(card, 1)

        note = QLabel(self._note_text)
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {_T2}; font-size: 11px;")
        root.addWidget(note)

        btns = QHBoxLayout()
        btns.addStretch()
        cancel = QPushButton("Cancel")
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.setStyleSheet(
            f"QPushButton{{background:#fff;color:{_T1};border:1px solid {_BORDER};"
            "border-radius:6px;padding:8px 16px;font-weight:600;}}"
            "QPushButton:hover{background:#f9fafb;}"
        )
        cancel.clicked.connect(self.reject)
        confirm = QPushButton(self._confirm_label)
        confirm.setCursor(Qt.PointingHandCursor)
        confirm.setDefault(True)
        confirm.setStyleSheet(
            f"QPushButton{{background:{_BLUE};color:#fff;border:none;"
            "border-radius:6px;padding:8px 16px;font-weight:600;}}"
            "QPushButton:hover{background:#0066a8;}"
        )
        confirm.clicked.connect(self.accept)
        btns.addWidget(cancel)
        btns.addWidget(confirm)
        root.addLayout(btns)
