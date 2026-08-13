"""Dialog when a Zambia Parking file's exact contents were already uploaded."""

from __future__ import annotations

from typing import List, Optional

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

_WHITE = "#FFFFFF"
_BG = "#F4F6F8"
_T1 = "#111827"
_T2 = "#6B7280"
_BLUE = "#0077C5"
_BORDER = "#E5E7EB"

_HEADERS = [
    "#",
    "Upload file",
    "Sheet",
    "Uploaded at",
    "Rows",
    "Upload id",
]


def _fmt_when(dt) -> str:
    if dt is None:
        return "—"
    try:
        return dt.strftime("%d %b %Y  %H:%M")
    except Exception:
        return str(dt)


def _cell(text: object, *, align=Qt.AlignLeft | Qt.AlignVCenter) -> QTableWidgetItem:
    item = QTableWidgetItem("" if text is None else str(text))
    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    item.setTextAlignment(align)
    return item


class ZambiaDuplicateReviewDialog(QDialog):
    """Show prior upload batch(es) that match this file's exact statement content."""

    def __init__(
        self,
        matching_uploads: List[dict],
        *,
        sheet_label: str = "",
        source_filename: str = "",
        row_count: int = 0,
        sheet_uploads: Optional[List[dict]] = None,  # unused; kept for call compat
        matches: Optional[List[dict]] = None,  # unused legacy kw
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        # Accept either new kw or legacy positional list of uploads.
        self._uploads = list(matching_uploads or sheet_uploads or [])
        self.setWindowTitle("Exact file already uploaded — Zambia Parking")
        self.setMinimumSize(760, 360)
        self.setStyleSheet(f"background:{_WHITE}; color:{_T1};")
        self._build(sheet_label, source_filename, row_count)

    def _build(self, sheet_label: str, source_filename: str, row_count: int) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(10)

        title = QLabel("This exact statement was already uploaded")
        title.setStyleSheet(f"font-size:16px; font-weight:700; color:{_T1};")
        root.addWidget(title)

        bits = []
        if source_filename:
            bits.append(f"File: {source_filename}")
        if sheet_label:
            bits.append(f"Sheet: {sheet_label}")
        if row_count:
            bits.append(f"Rows in file: {row_count:,}")
        bits.append(f"Matching upload(s): {len(self._uploads):,}")
        summary = QLabel("   ·   ".join(bits))
        summary.setStyleSheet(f"font-size:12px; color:{_T2};")
        summary.setWordWrap(True)
        root.addWidget(summary)

        hint = QLabel(
            "Import is blocked because every row in this file matches an existing "
            "Zambia Parking upload (exact content). Ticket numbers are not checked "
            "individually — only the whole statement body. Truck registry checks "
            "still run on new imports."
        )
        hint.setStyleSheet(f"font-size:12px; color:{_T2};")
        hint.setWordWrap(True)
        root.addWidget(hint)

        table = QTableWidget(0, len(_HEADERS))
        table.setHorizontalHeaderLabels(_HEADERS)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setSelectionMode(QTableWidget.SingleSelection)
        table.verticalHeader().setVisible(False)
        table.setStyleSheet(
            f"QTableWidget {{ background:{_WHITE}; gridline-color:{_BORDER};"
            f" border:1px solid {_BORDER}; border-radius:6px; font-size:12px; }}"
            f"QHeaderView::section {{ background:{_BG}; color:{_T1};"
            f" padding:6px; border:none; border-bottom:1px solid {_BORDER};"
            f" font-weight:600; }}"
        )
        hdr = table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        hdr.setStretchLastSection(True)
        table.setColumnWidth(0, 40)
        table.setColumnWidth(1, 260)
        table.setColumnWidth(2, 100)
        table.setColumnWidth(3, 140)
        table.setColumnWidth(4, 70)

        for i, up in enumerate(self._uploads, start=1):
            r = table.rowCount()
            table.insertRow(r)
            vals = [
                (str(i), Qt.AlignCenter),
                (up.get("source_filename") or "—", Qt.AlignLeft),
                (up.get("sheet_label") or "—", Qt.AlignCenter),
                (_fmt_when(up.get("import_date")), Qt.AlignLeft),
                (f"{int(up.get('record_count') or 0):,}", Qt.AlignRight),
                (up.get("upload_id") or "—", Qt.AlignLeft),
            ]
            for c, (text, align) in enumerate(vals):
                table.setItem(r, c, _cell(text, align=align | Qt.AlignVCenter))

        root.addWidget(table, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        close_btn = buttons.button(QDialogButtonBox.Close)
        if close_btn is not None:
            close_btn.setDefault(True)
            close_btn.setStyleSheet(
                f"QPushButton {{ background:{_BLUE}; color:#fff; border:none;"
                f" border-radius:5px; padding:6px 16px; font-weight:600; }}"
                f"QPushButton:hover {{ background:#005EA3; }}"
            )
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(buttons)
        root.addLayout(row)
