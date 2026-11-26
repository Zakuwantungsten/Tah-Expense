"""Person receipt dossier — stats + needed / entries / submitted / parked / rejected."""

from __future__ import annotations

from typing import Any, Callable

import qtawesome as qta
from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from tahmeed.ui.async_utils import schedule_coro
from tahmeed.ui.widgets.loading_overlay import hide_widget_loading, show_widget_loading
from tahmeed.ui.widgets.popup_styles import with_light_popups
from tahmeed.ui.widgets.receipt_file_viewer import open_receipt_viewer
from tahmeed.services.receipt_chase_service import get_person_receipt_dossier

_CARD = "#FFFFFF"
_BORDER = "#E5E7EB"
_TEXT = "#111827"
_MUTED = "#6B7280"
_ACCENT = "#0077C5"
_WARN = "#B45309"
_DANGER = "#B91C1C"
_OK = "#15803D"
_STRIPE = "#F1F5F9"

_TABLE_SS = with_light_popups(f"""
QTableWidget {{
  background:{_CARD}; border:1px solid {_BORDER}; border-radius:6px;
  gridline-color:#F3F4F6; font-family:"Segoe UI"; font-size:12px; color:{_TEXT};
  selection-background-color:#E0F2FE; selection-color:{_TEXT};
  alternate-background-color:{_STRIPE};
}}
QHeaderView::section {{
  background:#F8FAFC; color:{_MUTED}; font-family:"Segoe UI"; font-size:11px; font-weight:600;
  border:none; border-bottom:1px solid {_BORDER}; border-right:1px solid #F3F4F6;
  padding:6px 8px;
}}
""")


def _ghost(text: str, icon: str) -> QPushButton:
    btn = QPushButton(text)
    btn.setIcon(qta.icon(icon, color="#374151"))
    btn.setIconSize(QSize(14, 14))
    btn.setCursor(Qt.PointingHandCursor)
    btn.setFixedHeight(28)
    btn.setStyleSheet(
        f"QPushButton{{background:{_CARD};color:#374151;border:1px solid {_BORDER};"
        f"border-radius:6px;padding:0 10px;font:12px 'Segoe UI';}}"
        f"QPushButton:hover{{background:#F9FAFB;}}"
        f"QPushButton:disabled{{color:#9CA3AF;}}"
    )
    return btn


class _MiniKpi(QFrame):
    def __init__(self, title: str, accent: str, parent=None) -> None:
        super().__init__(parent)
        self.setStyleSheet(
            f"QFrame{{background:{_CARD};border:1px solid {_BORDER};border-radius:8px;}}"
        )
        self.setFixedHeight(64)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(2)
        lab = QLabel(title)
        lab.setStyleSheet(
            f"color:{_MUTED};font:600 11px 'Segoe UI';background:transparent;"
        )
        lay.addWidget(lab)
        self._value = QLabel("—")
        self._value.setStyleSheet(
            f"color:{accent};font:700 18px 'Segoe UI';background:transparent;"
        )
        lay.addWidget(self._value)

    def set_value(self, value: Any) -> None:
        self._value.setText(str(value))


class PersonReceiptDossierDialog(QDialog):
    """Open someone — browse their receipt stats and sections."""

    def __init__(
        self,
        parent=None,
        *,
        ownership: str,
        on_open_view_all: Callable[[str], None] | None = None,
        on_link_parked: Callable[[str, list[str]], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._ownership = " ".join((ownership or "").upper().split())
        self._on_open_view_all = on_open_view_all
        self._on_link_parked = on_link_parked
        self._dossier: dict[str, Any] = {}
        self._section_rows: dict[str, list[dict[str, Any]]] = {
            "needed": [],
            "entries": [],
            "submitted": [],
            "parked": [],
            "rejected": [],
        }
        self._tables: dict[str, QTableWidget] = {}

        self.setWindowTitle(f"Person receipts — {self._ownership or '—'}")
        self.setMinimumSize(960, 620)
        self.resize(1040, 680)
        self.setStyleSheet(
            with_light_popups(
                f"QDialog{{background:#F3F4F6;}}"
                f"QLabel{{color:{_TEXT};font:12px 'Segoe UI';background:transparent;}}"
                f"QTabWidget::pane{{border:1px solid {_BORDER};border-radius:6px;"
                f"background:{_CARD};top:-1px;}}"
                f"QTabBar::tab{{background:#F8FAFC;color:{_MUTED};padding:8px 14px;"
                f"border:1px solid transparent;font:600 12px 'Segoe UI';}}"
                f"QTabBar::tab:selected{{background:{_CARD};color:{_TEXT};"
                f"border:1px solid {_BORDER};border-bottom-color:{_CARD};}}"
            )
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        head = QHBoxLayout()
        title = QLabel(self._ownership or "—")
        title.setStyleSheet(
            f"color:{_TEXT};font:700 20px 'Segoe UI';background:transparent;"
        )
        head.addWidget(title, 1)
        self._person_meta = QLabel("")
        self._person_meta.setStyleSheet(
            f"color:{_MUTED};font:12px 'Segoe UI';background:transparent;"
        )
        head.addWidget(self._person_meta)
        refresh = _ghost("Refresh", "mdi.refresh")
        refresh.clicked.connect(lambda: schedule_coro(self.reload()))
        head.addWidget(refresh)
        root.addLayout(head)

        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(8)
        self._kpi_needed = _MiniKpi("Needed", _WARN)
        self._kpi_overdue = _MiniKpi("Overdue", _DANGER)
        self._kpi_received = _MiniKpi("Received", _OK)
        self._kpi_partial = _MiniKpi("Partial", _ACCENT)
        self._kpi_parked = _MiniKpi("Parked", _MUTED)
        self._kpi_rejected = _MiniKpi("Rejected", _DANGER)
        for card in (
            self._kpi_needed,
            self._kpi_overdue,
            self._kpi_received,
            self._kpi_partial,
            self._kpi_parked,
            self._kpi_rejected,
        ):
            kpi_row.addWidget(card, 1)
        root.addLayout(kpi_row)

        actions = QHBoxLayout()
        self._view_all_btn = _ghost("Open in View all", "mdi.table-large")
        self._view_all_btn.setToolTip("Jump to View all for this person’s needed entries.")
        self._view_all_btn.clicked.connect(self._jump_view_all)
        self._link_btn = _ghost("Link parked…", "mdi.link-variant")
        self._link_btn.setToolTip("Jump to Link with this person’s parked file(s).")
        self._link_btn.clicked.connect(self._jump_link_parked)
        self._open_btn = _ghost("Open receipt", "mdi.file-eye-outline")
        self._open_btn.clicked.connect(self._open_selected_file)
        actions.addWidget(self._view_all_btn)
        actions.addWidget(self._link_btn)
        actions.addWidget(self._open_btn)
        actions.addStretch(1)
        root.addLayout(actions)

        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        self._tables["needed"] = self._make_table(
            ["Item", "Truck", "Amount", "Age", "Reminders", "Status"]
        )
        self._tables["entries"] = self._make_table(
            ["Date", "Item", "Truck", "Amount", "Status", "Reason"]
        )
        self._tables["submitted"] = self._make_table(
            ["File", "Uploaded", "Status", "Item", "Linked"]
        )
        self._tables["parked"] = self._make_table(
            ["File", "Uploaded", "Status", "Source", "Size"]
        )
        self._tables["rejected"] = self._make_table(
            ["File", "Uploaded", "Reason", "Source"]
        )
        self._tabs.addTab(self._wrap_table(self._tables["needed"]), "Needed")
        self._tabs.addTab(self._wrap_table(self._tables["entries"]), "Entries")
        self._tabs.addTab(self._wrap_table(self._tables["submitted"]), "Submitted")
        self._tabs.addTab(self._wrap_table(self._tables["parked"]), "Parked")
        self._tabs.addTab(self._wrap_table(self._tables["rejected"]), "Rejected")
        root.addWidget(self._tabs, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        close_btn = buttons.button(QDialogButtonBox.Close)
        if close_btn is not None:
            close_btn.clicked.connect(self.accept)
        root.addWidget(buttons)

        schedule_coro(self.reload())

    @staticmethod
    def _make_table(headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setStyleSheet(_TABLE_SS)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.setShowGrid(False)
        hdr = table.horizontalHeader()
        hdr.setStretchLastSection(True)
        for col in range(len(headers)):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        return table

    @staticmethod
    def _wrap_table(table: QTableWidget) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.addWidget(table, 1)
        return page

    async def reload(self) -> None:
        if not self._ownership:
            QMessageBox.information(self, "Person receipts", "Ownership is required.")
            return
        show_widget_loading(self, f"Loading {self._ownership}…")
        try:
            self._dossier = await get_person_receipt_dossier(self._ownership)
            self._apply_dossier(self._dossier)
        except Exception as exc:
            QMessageBox.critical(self, "Person receipts", f"Load failed:\n{exc}")
        finally:
            hide_widget_loading(self)

    def _apply_dossier(self, payload: dict[str, Any]) -> None:
        stats = dict(payload.get("stats") or {})
        person = payload.get("person") if isinstance(payload.get("person"), dict) else None
        if person:
            bits = []
            if person.get("user_id"):
                bits.append("linked login")
            if person.get("receives_receipt_requests") is False:
                bits.append("requests off")
            self._person_meta.setText(" · ".join(bits) if bits else "In people directory")
        else:
            self._person_meta.setText("No people directory row")

        self._kpi_needed.set_value(stats.get("still_needed", 0))
        self._kpi_overdue.set_value(stats.get("overdue", 0))
        self._kpi_received.set_value(stats.get("received", 0))
        self._kpi_partial.set_value(stats.get("partial", 0))
        self._kpi_parked.set_value(stats.get("parked_uploads", 0))
        self._kpi_rejected.set_value(stats.get("rejected_files", 0))

        self._section_rows["needed"] = list(payload.get("needed") or [])
        self._section_rows["entries"] = list(payload.get("entries") or [])
        self._section_rows["submitted"] = list(payload.get("submitted") or [])
        self._section_rows["parked"] = list(payload.get("parked") or [])
        self._section_rows["rejected"] = list(payload.get("rejected") or [])

        self._fill_needed(self._section_rows["needed"])
        self._fill_entries(self._section_rows["entries"])
        self._fill_files(self._tables["submitted"], self._section_rows["submitted"])
        self._fill_parked(self._section_rows["parked"])
        self._fill_rejected(self._section_rows["rejected"])

        self._tabs.setTabText(0, f"Needed ({len(self._section_rows['needed'])})")
        self._tabs.setTabText(1, f"Entries ({len(self._section_rows['entries'])})")
        self._tabs.setTabText(2, f"Submitted ({len(self._section_rows['submitted'])})")
        self._tabs.setTabText(3, f"Parked ({len(self._section_rows['parked'])})")
        self._tabs.setTabText(4, f"Rejected ({len(self._section_rows['rejected'])})")
        self._link_btn.setEnabled(bool(self._section_rows["parked"]))

    def _set_cell(
        self, table: QTableWidget, row: int, col: int, text: str, data: Any = None
    ) -> None:
        item = QTableWidgetItem(text)
        item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        if data is not None and col == 0:
            item.setData(Qt.UserRole, data)
        table.setItem(row, col, item)

    def _fill_needed(self, rows: list[dict[str, Any]]) -> None:
        table = self._tables["needed"]
        table.setRowCount(0)
        for row in rows:
            r = table.rowCount()
            table.insertRow(r)
            age = row.get("age_hours")
            age_txt = "—"
            if age is not None:
                try:
                    hours = float(age)
                    age_txt = f"{hours:.0f}h" if hours < 24 else f"{hours / 24.0:.1f}d"
                except (TypeError, ValueError):
                    age_txt = str(age)
            values = [
                str(row.get("item") or "—"),
                str(row.get("truck_number") or "—"),
                self._fmt_amount(row),
                age_txt,
                str(row.get("reminder_count") or 0),
                str(row.get("status") or row.get("display_status") or "—"),
            ]
            for c, text in enumerate(values):
                self._set_cell(table, r, c, text, row if c == 0 else None)

    def _fill_entries(self, rows: list[dict[str, Any]]) -> None:
        table = self._tables["entries"]
        table.setRowCount(0)
        for row in rows:
            r = table.rowCount()
            table.insertRow(r)
            reason = ""
            if str(row.get("display_status_key") or "") == "waived":
                reason = str(row.get("waiver_reason") or "").strip()
            values = [
                str(row.get("date") or "—"),
                str(row.get("item") or "—"),
                str(row.get("truck_number") or "—"),
                self._fmt_amount(row),
                str(row.get("display_status") or row.get("display_status_key") or "—"),
                reason or "—",
            ]
            for c, text in enumerate(values):
                self._set_cell(table, r, c, text, row if c == 0 else None)

    def _fill_files(self, table: QTableWidget, rows: list[dict[str, Any]]) -> None:
        table.setRowCount(0)
        for row in rows:
            r = table.rowCount()
            table.insertRow(r)
            values = [
                str(row.get("original_filename") or row.get("id") or "—"),
                str(row.get("uploaded_at") or "—")[:16].replace("T", " "),
                str(row.get("inbox_status") or "—"),
                str(row.get("item") or "—"),
                str(row.get("linked_count") or 0),
            ]
            for c, text in enumerate(values):
                self._set_cell(table, r, c, text, row if c == 0 else None)

    def _fill_parked(self, rows: list[dict[str, Any]]) -> None:
        table = self._tables["parked"]
        table.setRowCount(0)
        for row in rows:
            r = table.rowCount()
            table.insertRow(r)
            size = int(row.get("size") or 0)
            size_txt = f"{size / 1024:.0f} KB" if size else "—"
            values = [
                str(row.get("original_filename") or row.get("id") or "—"),
                str(row.get("uploaded_at") or "—")[:16].replace("T", " "),
                str(row.get("inbox_status") or "—"),
                str(row.get("source") or "—"),
                size_txt,
            ]
            for c, text in enumerate(values):
                self._set_cell(table, r, c, text, row if c == 0 else None)

    def _fill_rejected(self, rows: list[dict[str, Any]]) -> None:
        table = self._tables["rejected"]
        table.setRowCount(0)
        for row in rows:
            r = table.rowCount()
            table.insertRow(r)
            values = [
                str(row.get("original_filename") or row.get("id") or "—"),
                str(row.get("uploaded_at") or "—")[:16].replace("T", " "),
                str(row.get("rejection_reason") or "—"),
                str(row.get("source") or "—"),
            ]
            for c, text in enumerate(values):
                self._set_cell(table, r, c, text, row if c == 0 else None)

    @staticmethod
    def _fmt_amount(row: dict[str, Any]) -> str:
        try:
            amount = float(row.get("amount") or 0)
        except (TypeError, ValueError):
            return str(row.get("amount") or "—")
        currency = str(row.get("currency") or "TZS")
        return f"{amount:,.0f} {currency}"

    def _current_table(self) -> QTableWidget | None:
        idx = self._tabs.currentIndex()
        keys = ("needed", "entries", "submitted", "parked", "rejected")
        if idx < 0 or idx >= len(keys):
            return None
        return self._tables.get(keys[idx])

    def _selected_rows(self) -> list[dict[str, Any]]:
        table = self._current_table()
        if table is None:
            return []
        rows: list[dict[str, Any]] = []
        seen: set[int] = set()
        for item in table.selectedItems():
            r = item.row()
            if r in seen:
                continue
            seen.add(r)
            sn = table.item(r, 0)
            data = sn.data(Qt.UserRole) if sn else None
            if isinstance(data, dict):
                rows.append(data)
        return rows

    def _jump_view_all(self) -> None:
        if self._on_open_view_all is None:
            return
        self._on_open_view_all(self._ownership)
        self.accept()

    def _jump_link_parked(self) -> None:
        parked = self._section_rows.get("parked") or []
        ids = [str(r.get("id") or "").strip() for r in parked if r.get("id")]
        ids = [fid for fid in ids if fid]
        if not ids:
            QMessageBox.information(
                self, "Person receipts", "No parked files for this person."
            )
            return
        if self._on_link_parked is None:
            return
        self._on_link_parked(self._ownership, ids)
        self.accept()

    def _open_selected_file(self) -> None:
        rows = self._selected_rows()
        if not rows:
            QMessageBox.information(
                self, "Person receipts", "Select a submitted, parked, or rejected file."
            )
            return
        row = rows[0]
        fid = str(row.get("id") or row.get("primary_file_id") or "").strip()
        if not fid:
            QMessageBox.information(self, "Person receipts", "No file id on this row.")
            return
        open_receipt_viewer(
            self,
            fid,
            title_hint=str(
                row.get("original_filename") or row.get("primary_filename") or ""
            ),
        )
