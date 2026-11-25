"""Inbox — view linked expense rows + still-request carve-out."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from tahmeed.ui.widgets.popup_styles import with_light_popups

_BORDER = "#E5E7EB"
_TEXT = "#111827"
_MUTED = "#6B7280"
_CARD = "#FFFFFF"


class InboxLinkedEntriesDialog(QDialog):
    """Show entries linked to an inbox file; optionally carve out still-request rows."""

    def __init__(
        self,
        parent=None,
        *,
        file_row: dict[str, Any],
        linked_entries: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(parent)
        self._file_row = file_row or {}
        self._entries = list(linked_entries or file_row.get("linked_entries") or [])
        self._still_request_ids: list[str] = []
        self._still_request_note = ""
        self._do_still_request = False

        name = str(
            file_row.get("original_filename") or file_row.get("id") or "Receipt"
        )
        self.setWindowTitle(f"Linked entries — {name}")
        self.setMinimumSize(720, 360)
        self.setStyleSheet(
            with_light_popups(
                f"QDialog{{background:{_CARD};}}"
                f"QLabel{{color:{_TEXT};font:12px 'Segoe UI';background:transparent;}}"
            )
        )

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(10)

        n = len(self._entries)
        summary = QLabel(
            f"{n} linked entr{'y' if n == 1 else 'ies'} · "
            f"status={file_row.get('inbox_status') or '—'} · "
            f"source={file_row.get('source') or '—'}"
        )
        summary.setStyleSheet(f"color:{_MUTED}; font:12px 'Segoe UI';")
        lay.addWidget(summary)

        tip = QLabel(
            "Check rows that still need a real receipt, then Still request… "
            "Other linked rows can be completed when you Approve the file."
        )
        tip.setWordWrap(True)
        tip.setStyleSheet(f"color:{_MUTED}; font:11px 'Segoe UI';")
        lay.addWidget(tip)

        self._table = QTableWidget(0, 8)
        self._table.setHorizontalHeaderLabels(
            ["", "Date", "Item", "Payee", "Ownership", "Amount", "Status", "Note"]
        )
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setStyleSheet(
            with_light_popups(
                f"QTableWidget{{border:1px solid {_BORDER};border-radius:6px;"
                f"font:12px 'Segoe UI';color:{_TEXT};background:{_CARD};}}"
            )
        )
        lay.addWidget(self._table, 1)
        self._populate()

        actions = QHBoxLayout()
        self._still_btn = QPushButton("Still request selected…")
        self._still_btn.setCursor(Qt.PointingHandCursor)
        self._still_btn.clicked.connect(self._on_still_request)
        actions.addWidget(self._still_btn)
        actions.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        close_btn = buttons.button(QDialogButtonBox.Close)
        if close_btn is not None:
            close_btn.clicked.connect(self.accept)
        actions.addWidget(buttons)
        lay.addLayout(actions)

    def _populate(self) -> None:
        self._table.setRowCount(0)
        for entry in self._entries:
            r = self._table.rowCount()
            self._table.insertRow(r)
            check = QTableWidgetItem()
            check.setFlags(
                Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable
            )
            check.setCheckState(Qt.Unchecked)
            check.setData(Qt.UserRole, entry)
            self._table.setItem(r, 0, check)
            amount = entry.get("amount")
            cur = str(entry.get("currency") or "TZS")
            try:
                amount_s = f"{cur} {float(amount):,.0f}" if amount is not None else "—"
            except (TypeError, ValueError):
                amount_s = f"{amount} {cur}".strip()
            values = [
                str(entry.get("date") or "—"),
                str(entry.get("item") or "—"),
                str(entry.get("payee") or "—"),
                str(entry.get("ownership") or "—"),
                amount_s,
                str(entry.get("display_status") or entry.get("display_status_key") or "—"),
                str(entry.get("partial_note") or ""),
            ]
            for c, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setData(Qt.UserRole, entry)
                self._table.setItem(r, c + 1, item)
        self._table.resizeColumnsToContents()

    def _checked_entries(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for r in range(self._table.rowCount()):
            check = self._table.item(r, 0)
            if check is None:
                continue
            state = check.checkState()
            checked = state == Qt.Checked or state == getattr(Qt.Checked, "value", 2)
            if not checked:
                continue
            entry = check.data(Qt.UserRole) or {}
            if not isinstance(entry, dict):
                continue
            tid = str(entry.get("id") or "").strip()
            if not tid or tid in seen:
                continue
            seen.add(tid)
            rows.append(entry)
        # Also include highlighted rows
        for item in self._table.selectedItems():
            entry = item.data(Qt.UserRole) or {}
            if not isinstance(entry, dict):
                continue
            tid = str(entry.get("id") or "").strip()
            if not tid or tid in seen:
                continue
            seen.add(tid)
            rows.append(entry)
        return rows

    def _on_still_request(self) -> None:
        rows = self._checked_entries()
        if not rows:
            QMessageBox.information(
                self,
                "Still request",
                "Check ☐ or highlight the linked entr"
                "y/ies that still need a receipt.",
            )
            return
        from PySide6.QtWidgets import QInputDialog

        note, ok = QInputDialog.getText(
            self,
            "Still request receipt",
            f"Note for {len(rows)} entr"
            f"{'y' if len(rows) == 1 else 'ies'} "
            "(sent back to ownership), e.g. wrong slip / missing fuel page:",
        )
        if not ok:
            return
        note = (note or "").strip()
        if len(note) < 3:
            QMessageBox.information(
                self, "Still request", "Enter a short note (3+ characters)."
            )
            return
        self._still_request_ids = [str(r.get("id")) for r in rows if r.get("id")]
        self._still_request_note = note
        self._do_still_request = True
        self.accept()

    @property
    def should_still_request(self) -> bool:
        return bool(self._do_still_request and self._still_request_ids)

    @property
    def still_request_transaction_ids(self) -> list[str]:
        return list(self._still_request_ids)

    @property
    def still_request_note(self) -> str:
        return self._still_request_note
