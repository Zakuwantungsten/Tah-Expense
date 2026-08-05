"""Skipped-trucks follow-up tab for Separate Expenses / Fuel imports."""

from __future__ import annotations

import asyncio
import csv
from datetime import datetime
from typing import List

import qtawesome as qta
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from tahmeed.services.import_truck_check import truck_field_for
from tahmeed.services.truck_format import (
    DEFAULT_PLACE_LABELS,
    normalize_truck_number,
    try_match_fleet,
)
from tahmeed.ui.dialogs.truck_correction_dialog import TruckCorrectionDialog, TruckIssue

try:
    import openpyxl
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    _HAS_OPENPYXL = True
except ImportError:
    openpyxl = None  # type: ignore
    _HAS_OPENPYXL = False

_BG = "#F4F6F8"
_WHITE = "#FFFFFF"
_BORDER = "#E5E7EB"
_T1 = "#111827"
_T2 = "#6B7280"
_BLUE = "#0077C5"

_HEADERS = [
    "Skipped", "Truck", "Original", "Reason", "Source file", "Sheet", "Upload id",
]

_EXPORT_HEADERS = [
    "Skipped At",
    "Truck",
    "Original Truck",
    "Reason",
    "Source File",
    "Sheet",
    "Upload ID",
    "Ledger / Receipt",
    "Payment / Date",
    "Type",
    "Amount",
    "Details",
]


def _lbl(text: str, *, size: int = 12, weight: int = 400, color: str = _T1) -> QLabel:
    lab = QLabel(text)
    lab.setStyleSheet(
        f"color:{color};font-size:{size}px;font-weight:{weight};"
        "font-family:'Segoe UI';border:none;background:transparent;"
    )
    return lab


def _btn(text: str, icon: str = "", *, primary: bool = True, height: int = 32) -> QPushButton:
    b = QPushButton(text)
    b.setCursor(Qt.PointingHandCursor)
    b.setFixedHeight(height)
    if icon:
        try:
            b.setIcon(qta.icon(icon, color="#FFF" if primary else _T1))
        except Exception:
            pass
    if primary:
        b.setStyleSheet(
            f"QPushButton{{background:{_BLUE};color:#FFF;border:none;border-radius:5px;"
            f"font-size:12px;font-weight:600;padding:0 12px;}}"
            "QPushButton:hover{background:#005EA3;}"
            "QPushButton:disabled{background:#93C5FD;}"
        )
    else:
        b.setStyleSheet(
            f"QPushButton{{background:{_WHITE};color:{_T1};border:1px solid {_BORDER};"
            f"border-radius:5px;font-size:12px;font-weight:600;padding:0 12px;}}"
            f"QPushButton:hover{{background:{_BG};}}"
        )
    return b


def _cell(text: str) -> QTableWidgetItem:
    item = QTableWidgetItem(str(text) if text is not None else "")
    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    return item


def _reason_label(reason: str) -> str:
    if reason == "not_in_registry":
        return "Not in registry"
    if reason == "invalid_format":
        return "Invalid format"
    return reason or ""


def _export_row_values(doc: dict) -> List[str]:
    """Flatten a skipped row into export columns for follow-up tracking."""
    skipped_at = doc.get("skipped_at")
    if isinstance(skipped_at, datetime):
        when = skipped_at.strftime("%d %b %Y  %H:%M")
    else:
        when = str(skipped_at or "")
    rec = doc.get("record") or {}
    ledger = (
        rec.get("ledger_id")
        or rec.get("receipt_no")
        or rec.get("lpo_no")
        or rec.get("serial")
        or ""
    )
    pay_date = (
        rec.get("payment_date")
        or rec.get("toll_date")
        or rec.get("date")
        or ""
    )
    tx_type = rec.get("transaction_type") or rec.get("type") or ""
    amount = rec.get("amount") or rec.get("tender_amount") or ""
    details = (
        rec.get("transaction_details")
        or rec.get("description")
        or rec.get("details")
        or ""
    )
    return [
        when,
        str(doc.get("truck_value") or ""),
        str(doc.get("original_truck") or ""),
        _reason_label(str(doc.get("reason") or "")),
        str(doc.get("source_filename") or ""),
        str(doc.get("sheet_label") or ""),
        str(doc.get("target_upload_id") or ""),
        str(ledger),
        str(pay_date),
        str(tx_type),
        str(amount),
        str(details),
    ]


def _write_skipped_csv(path: str, docs: List[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(_EXPORT_HEADERS)
        for doc in docs:
            writer.writerow(_export_row_values(doc))


def _write_skipped_xlsx(path: str, docs: List[dict]) -> None:
    if not _HAS_OPENPYXL:
        raise RuntimeError("openpyxl is required for Excel export.")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Skipped Trucks"

    thin = Side(border_style="thin", color="E5E7EB")
    bdr = Border(left=thin, right=thin, top=thin, bottom=thin)
    hdr_fill = PatternFill("solid", fgColor="EFF6FF")
    hdr_font = Font(name="Calibri", bold=True, size=11, color="1E3A5F")
    alt_fill = PatternFill("solid", fgColor="F9FAFB")
    nrm_font = Font(name="Calibri", size=11)

    for c, label in enumerate(_EXPORT_HEADERS, 1):
        cell = ws.cell(row=1, column=c, value=label)
        cell.font = hdr_font
        cell.fill = hdr_fill
        cell.border = bdr
        cell.alignment = Alignment(horizontal="center", vertical="center")

    for ri, doc in enumerate(docs, 2):
        for ci, val in enumerate(_export_row_values(doc), 1):
            cell = ws.cell(row=ri, column=ci, value=val)
            cell.font = nrm_font
            cell.border = bdr
            cell.alignment = Alignment(vertical="center")
            if ri % 2 == 0:
                cell.fill = alt_fill

    widths = [18, 14, 14, 16, 28, 12, 38, 16, 20, 14, 12, 28]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[ws.cell(1, i).column_letter].width = w

    wb.save(path)


class SkippedTrucksTab(QWidget):
    """List parked import rows; edit truck and re-upload into the original batch."""

    changed = Signal()

    def __init__(self, feed_key: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._feed_key = feed_key
        self._truck_field = truck_field_for(feed_key) or "truck_no"
        self._rows: List[dict] = []
        self._build()

    def _build(self) -> None:
        self.setStyleSheet("background:transparent;")
        vl = QVBoxLayout(self)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(8)

        hint = _lbl(
            "Rows skipped during import because the truck was unknown or mistyped. "
            "Edit the truck number after follow-up, then re-upload — they join the "
            "original upload batch. Export Excel/CSV to share the skip list.",
            size=11,
            color=_T2,
        )
        hint.setWordWrap(True)
        vl.addWidget(hint)

        tb = QWidget()
        tb.setStyleSheet("background:transparent;")
        tbl = QHBoxLayout(tb)
        tbl.setContentsMargins(0, 0, 0, 0)
        tbl.setSpacing(8)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search truck, file, upload id…")
        self._search.setFixedWidth(280)
        self._search.setStyleSheet(
            f"QLineEdit{{background:{_WHITE};border:1px solid {_BORDER};border-radius:5px;"
            f"padding:0 10px;min-height:32px;font-size:12px;}}"
        )
        self._search.returnPressed.connect(self.refresh)
        tbl.addWidget(self._search)

        refresh_btn = _btn("Refresh", "mdi.refresh", primary=False)
        refresh_btn.clicked.connect(self.refresh)
        tbl.addWidget(refresh_btn)

        export_xlsx_btn = _btn("Export Excel", "mdi.file-excel-outline", primary=False)
        export_xlsx_btn.clicked.connect(lambda: self._export("xlsx"))
        tbl.addWidget(export_xlsx_btn)

        export_csv_btn = _btn("Export CSV", "mdi.file-delimited-outline", primary=False)
        export_csv_btn.clicked.connect(lambda: self._export("csv"))
        tbl.addWidget(export_csv_btn)

        tbl.addStretch()

        edit_btn = _btn("Edit truck", primary=False)
        edit_btn.clicked.connect(self._edit_selected)
        tbl.addWidget(edit_btn)

        reup_btn = _btn("Re-upload selected", "mdi.upload-outline")
        reup_btn.clicked.connect(self._reupload_selected)
        tbl.addWidget(reup_btn)

        del_btn = _btn("Delete", "mdi.trash-can-outline", primary=False)
        del_btn.clicked.connect(self._delete_selected)
        tbl.addWidget(del_btn)
        vl.addWidget(tb)

        self._table = QTableWidget(0, len(_HEADERS))
        self._table.setHorizontalHeaderLabels(_HEADERS)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setStyleSheet(
            f"QTableWidget{{background:{_WHITE};border:1px solid {_BORDER};"
            f"border-radius:6px;gridline-color:{_BORDER};}}"
            f"QHeaderView::section{{background:{_BG};color:{_T2};font-weight:600;"
            f"border:none;border-bottom:1px solid {_BORDER};padding:6px;}}"
        )
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeToContents)
        hdr.setStretchLastSection(True)
        vl.addWidget(self._table, 1)

        self._status = _lbl("", size=11, color=_T2)
        vl.addWidget(self._status)

    def refresh(self) -> None:
        asyncio.ensure_future(self._load())

    async def _load(self) -> None:
        from tahmeed.services import accountant_service as svc

        search = self._search.text().strip()
        try:
            total = await svc.count_skipped_import_rows(self._feed_key, search)
            self._rows = await svc.list_skipped_import_rows(
                self._feed_key, search=search, limit=500, skip=0
            )
        except Exception as exc:
            QMessageBox.critical(self, "Skipped", str(exc))
            return
        self._fill()
        self._status.setText(f"{total:,} skipped row(s)")

    def _fill(self) -> None:
        t = self._table
        t.setRowCount(0)
        for doc in self._rows:
            r = t.rowCount()
            t.insertRow(r)
            skipped_at = doc.get("skipped_at")
            if isinstance(skipped_at, datetime):
                when = skipped_at.strftime("%d %b %Y  %H:%M")
            else:
                when = str(skipped_at or "—")
            reason_lbl = _reason_label(str(doc.get("reason") or ""))
            vals = [
                when,
                doc.get("truck_value") or "",
                doc.get("original_truck") or "",
                reason_lbl,
                doc.get("source_filename") or "",
                doc.get("sheet_label") or "—",
                (doc.get("target_upload_id") or "")[:8] + "…",
            ]
            for c, val in enumerate(vals):
                t.setItem(r, c, _cell(val))
            # stash full id on first cell
            t.item(r, 0).setData(Qt.UserRole, str(doc.get("_id") or ""))

    def _export(self, fmt: str) -> None:
        if not self._rows:
            QMessageBox.information(
                self, "Export", "No skipped rows to export. Refresh or clear search.",
            )
            return
        stamp = datetime.now().strftime("%Y%m%d_%H%M")
        default = f"skipped_trucks_{self._feed_key}_{stamp}.{fmt}"
        if fmt == "xlsx":
            filt = "Excel Files (*.xlsx)"
        else:
            filt = "CSV Files (*.csv)"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Skipped Trucks", default, filt,
        )
        if not path:
            return
        if fmt == "xlsx" and not path.lower().endswith(".xlsx"):
            path += ".xlsx"
        elif fmt == "csv" and not path.lower().endswith(".csv"):
            path += ".csv"
        try:
            if fmt == "xlsx":
                _write_skipped_xlsx(path, self._rows)
            else:
                _write_skipped_csv(path, self._rows)
        except Exception as exc:
            QMessageBox.critical(self, "Export Error", str(exc))
            return
        QMessageBox.information(
            self,
            "Export Complete",
            f"Exported {len(self._rows):,} skipped row(s) to:\n{path}",
        )

    def _selected_ids(self) -> List[str]:
        ids: List[str] = []
        for idx in self._table.selectionModel().selectedRows():
            item = self._table.item(idx.row(), 0)
            if item:
                rid = item.data(Qt.UserRole)
                if rid:
                    ids.append(str(rid))
        return ids

    def _selected_docs(self) -> List[dict]:
        ids = set(self._selected_ids())
        return [d for d in self._rows if str(d.get("_id") or "") in ids]

    def _edit_selected(self) -> None:
        docs = self._selected_docs()
        if len(docs) != 1:
            QMessageBox.information(
                self, "Edit truck", "Select exactly one skipped row to edit."
            )
            return
        doc = docs[0]
        current = str(doc.get("truck_value") or "")
        text, ok = QInputDialog.getText(
            self,
            "Edit truck number",
            "Correct truck number (after follow-up with the report author):",
            text=current,
        )
        if not ok:
            return
        value = text.strip()
        if not value:
            QMessageBox.warning(self, "Edit truck", "Truck number cannot be empty.")
            return
        asyncio.ensure_future(self._save_truck(str(doc.get("_id")), value))

    async def _save_truck(self, doc_id: str, value: str) -> None:
        from tahmeed.services import accountant_service as svc

        try:
            await svc.update_skipped_import_truck(doc_id, value)
        except Exception as exc:
            QMessageBox.critical(self, "Edit truck", str(exc))
            return
        await self._load()

    def _delete_selected(self) -> None:
        ids = self._selected_ids()
        if not ids:
            return
        if QMessageBox.question(
            self,
            "Delete skipped",
            f"Permanently delete {len(ids):,} skipped row(s)?",
        ) != QMessageBox.Yes:
            return
        asyncio.ensure_future(self._do_delete(ids))

    async def _do_delete(self, ids: List[str]) -> None:
        from tahmeed.services import accountant_service as svc

        try:
            await svc.delete_skipped_import_rows(ids)
        except Exception as exc:
            QMessageBox.critical(self, "Delete", str(exc))
            return
        self.changed.emit()
        await self._load()

    def _reupload_selected(self) -> None:
        docs = self._selected_docs()
        if not docs:
            QMessageBox.information(self, "Re-upload", "Select one or more skipped rows.")
            return
        asyncio.ensure_future(self._do_reupload(docs))

    async def _do_reupload(self, docs: List[dict]) -> None:
        """Re-check fleet, then insert into each row's original target_upload_id."""
        from tahmeed.services import accountant_service as svc
        from tahmeed.services.truck_service import get_fleet_numbers, add_truck, add_trailer
        from tahmeed.services.truck_format import merge_allowed_labels
        from tahmeed.services import settings_service

        try:
            fleet = await get_fleet_numbers()
        except Exception:
            fleet = set()
        try:
            from tahmeed.services.truck_service import get_fleet_kinds
            fleet_kinds = await get_fleet_kinds()
        except Exception:
            fleet_kinds = {}
        try:
            stored = await settings_service.get_setting("allowed_truck_labels")
        except Exception:
            stored = []
        labels = merge_allowed_labels(DEFAULT_PLACE_LABELS, stored or [])

        ready_ids: List[str] = []
        issues: List[TruckIssue] = []
        issue_docs: List[dict] = []

        for doc in docs:
            field = doc.get("truck_field") or self._truck_field
            raw = str(doc.get("truck_value") or "")
            norm = normalize_truck_number(raw, allowed_labels=labels)
            if norm.status == "place_label":
                await svc.update_skipped_import_truck(str(doc["_id"]), norm.value)
                ready_ids.append(str(doc["_id"]))
                continue
            if norm.status in ("ok", "normalized"):
                matched = try_match_fleet(norm.value, fleet)
                if matched is not None:
                    await svc.update_skipped_import_truck(str(doc["_id"]), matched)
                    ready_ids.append(str(doc["_id"]))
                    continue
                issues.append(TruckIssue(
                    row=len(issue_docs),
                    original=raw,
                    kind="not_in_registry",
                ))
                issue_docs.append(doc)
                continue
            issues.append(TruckIssue(
                row=len(issue_docs),
                original=raw,
                kind="invalid_format",
            ))
            issue_docs.append(doc)

        if issues:
            dlg = TruckCorrectionDialog(
                issues,
                fleet,
                can_add=True,
                allowed_labels=labels,
                import_mode=True,
                fleet_kinds=fleet_kinds,
                parent=self,
            )
            dlg.exec()
            for kind, number in dlg.pending_registry_adds:
                try:
                    if kind == "trailers":
                        await add_trailer(number)
                    else:
                        await add_truck(number)
                except Exception as exc:
                    QMessageBox.warning(
                        self, "Registry", f"Could not add {number}:\n{exc}"
                    )
            by_row = {iss.row: iss for iss in dlg.issues}
            for i, doc in enumerate(issue_docs):
                iss = by_row.get(i)
                if iss is None or iss.omit_row or (iss.skip and not iss.corrected):
                    continue
                value = iss.corrected or iss.original
                await svc.update_skipped_import_truck(str(doc["_id"]), value)
                ready_ids.append(str(doc["_id"]))

        if not ready_ids:
            QMessageBox.information(
                self,
                "Re-upload",
                "No rows were ready to re-upload (still skipped or unresolved).",
            )
            await self._load()
            return

        try:
            saved = await svc.reupload_skipped_import_rows(ready_ids)
        except Exception as exc:
            QMessageBox.critical(self, "Re-upload", str(exc))
            await self._load()
            return

        QMessageBox.information(
            self,
            "Re-upload complete",
            f"Re-uploaded {saved:,} row(s) into their original upload batch(es).",
        )
        self.changed.emit()
        await self._load()
