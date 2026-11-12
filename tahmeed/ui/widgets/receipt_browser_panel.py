"""Accountant receipt storage — Explorer (bucket/folder) + Pack & download."""

from __future__ import annotations

import asyncio
import io
import zipfile
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

import qtawesome as qta
from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDateEdit,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from tahmeed.services.receipt_chase_service import (
    download_consolidate_pack,
    fetch_receipt_file_bytes,
    list_receipt_library,
    rematerialize_files,
)
from tahmeed.services.receipt_storage_service import (
    list_storage_objects,
    list_storage_tree,
)
from tahmeed.ui.widgets.receipt_file_viewer import open_receipt_viewer

_BG = "#F3F4F6"
_CARD = "#FFFFFF"
_TEXT = "#1F2937"
_MUTED = "#6B7280"
_BORDER = "#E5E7EB"
_PRIMARY = "#1B2B4B"
_FOLDER = "#D97706"


def _ghost(text: str, icon: str) -> QPushButton:
    btn = QPushButton(qta.icon(icon, color=_PRIMARY), text)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setFixedHeight(30)
    btn.setStyleSheet(
        f"QPushButton{{background:{_CARD};color:{_TEXT};border:1px solid {_BORDER};"
        f"border-radius:6px;padding:0 10px;font:600 12px 'Segoe UI';}}"
        f"QPushButton:hover{{background:{_BG};}}"
    )
    return btn


def _primary(text: str, icon: str) -> QPushButton:
    btn = QPushButton(qta.icon(icon, color="#FFFFFF"), text)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setFixedHeight(30)
    btn.setStyleSheet(
        f"QPushButton{{background:{_PRIMARY};color:#FFFFFF;border:none;"
        f"border-radius:6px;padding:0 12px;font:600 12px 'Segoe UI';}}"
        f"QPushButton:hover{{background:#243a63;}}"
    )
    return btn


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


class ReceiptBrowserPanel(QWidget):
    """Windows/bucket-style browse of the receipt disk + pack/download tools."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._prefix = ""
        self._objects: list[dict[str, Any]] = []
        self._day_rows: list[dict[str, Any]] = []
        self._tree_loading = False
        self._build()
        asyncio.ensure_future(self._bootstrap())

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        title = QLabel("Receipt storage")
        title.setStyleSheet(
            f"color:{_TEXT}; font:700 18px 'Segoe UI'; background:transparent;"
        )
        root.addWidget(title)
        hint = QLabel(
            "Browse the API receipt disk like a folder / bucket (files · approved · "
            "quarantine · parked). Pack & download ships filtered or selected receipts."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{_MUTED}; font:12px 'Segoe UI'; background:transparent;")
        root.addWidget(hint)

        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        self._tabs.setStyleSheet(
            f"QTabWidget::pane{{border:1px solid {_BORDER}; border-radius:8px; background:{_CARD};}}"
            f"QTabBar::tab{{padding:8px 14px; font:600 12px 'Segoe UI'; color:{_MUTED};}}"
            f"QTabBar::tab:selected{{color:{_TEXT};}}"
        )
        self._tabs.addTab(self._build_explorer_tab(), "Explorer")
        self._tabs.addTab(self._build_pack_tab(), "Pack & download")
        root.addWidget(self._tabs, 1)

        self._status = QLabel("")
        self._status.setStyleSheet(f"color:{_MUTED}; font:11px 'Segoe UI';")
        root.addWidget(self._status)

    # ── Explorer (directory / bucket) ─────────────────────────────────────

    def _build_explorer_tab(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        bar = QHBoxLayout()
        self._crumb = QLabel("This PC \\ Receipt disk")
        self._crumb.setStyleSheet(
            f"color:{_TEXT}; font:600 12px 'Segoe UI'; background:transparent;"
        )
        self._crumb.setTextInteractionFlags(Qt.TextSelectableByMouse)
        up_btn = _ghost("Up", "mdi.arrow-up")
        up_btn.clicked.connect(lambda: asyncio.ensure_future(self._go_up()))
        root_btn = _ghost("Root", "mdi.harddisk")
        root_btn.clicked.connect(lambda: asyncio.ensure_future(self._go_prefix("")))
        refresh_btn = _ghost("Refresh", "mdi.refresh")
        refresh_btn.clicked.connect(lambda: asyncio.ensure_future(self._reload_explorer()))
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search filename or sha256 in this folder…")
        self._search.setClearButtonEnabled(True)
        self._search.returnPressed.connect(
            lambda: asyncio.ensure_future(self._load_objects())
        )
        search_btn = _ghost("Search", "mdi.magnify")
        search_btn.clicked.connect(lambda: asyncio.ensure_future(self._load_objects()))
        view_btn = _ghost("View", "mdi.eye-outline")
        view_btn.clicked.connect(self._view_selected)
        remat_btn = _ghost("Rematerialize", "mdi.folder-sync-outline")
        remat_btn.clicked.connect(
            lambda: asyncio.ensure_future(self._rematerialize_selected())
        )
        dl_sel = _ghost("Download selected", "mdi.download-outline")
        dl_sel.clicked.connect(
            lambda: asyncio.ensure_future(self._download_selected())
        )
        bar.addWidget(up_btn)
        bar.addWidget(root_btn)
        bar.addWidget(self._crumb, 1)
        bar.addWidget(self._search, 2)
        bar.addWidget(search_btn)
        bar.addWidget(view_btn)
        bar.addWidget(dl_sel)
        bar.addWidget(remat_btn)
        bar.addWidget(refresh_btn)
        lay.addLayout(bar)

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)

        left = QWidget()
        left.setStyleSheet(
            f"background:{_CARD}; border:1px solid {_BORDER}; border-radius:8px;"
        )
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(6, 6, 6, 6)
        folders_lbl = QLabel("Folders")
        folders_lbl.setStyleSheet(f"color:{_MUTED}; font:600 11px 'Segoe UI';")
        left_lay.addWidget(folders_lbl)
        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setAnimated(True)
        self._tree.setStyleSheet(
            "QTreeWidget{border:none; font:13px 'Segoe UI';}"
            "QTreeWidget::item{padding:4px 2px;}"
            "QTreeWidget::item:selected{background:#E8EEF7; color:#111827;}"
        )
        self._tree.itemExpanded.connect(self._on_tree_expand)
        self._tree.itemClicked.connect(self._on_tree_click)
        left_lay.addWidget(self._tree, 1)
        split.addWidget(left)

        right = QWidget()
        right.setStyleSheet(
            f"background:{_CARD}; border:1px solid {_BORDER}; border-radius:8px;"
        )
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(6, 6, 6, 6)
        files_lbl = QLabel("Files in folder  (multi-select for download)")
        files_lbl.setStyleSheet(f"color:{_MUTED}; font:600 11px 'Segoe UI';")
        right_lay.addWidget(files_lbl)
        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(
            ["Name", "Type / status", "Size", "Indexed", "Path"]
        )
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setShowGrid(False)
        self._table.setIconSize(self._table.iconSize())
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.Stretch)
        hdr.setStretchLastSection(True)
        self._table.setStyleSheet(
            f"QTableWidget{{border:none; font:12px 'Segoe UI';}}"
            f"QHeaderView::section{{background:{_BG}; border:none; padding:6px; "
            f"font:600 11px 'Segoe UI'; color:{_MUTED};}}"
        )
        self._table.cellDoubleClicked.connect(lambda *_: self._view_selected())
        right_lay.addWidget(self._table, 1)
        split.addWidget(right)
        split.setSizes([280, 720])
        lay.addWidget(split, 1)
        return page

    # ── Pack & download ───────────────────────────────────────────────────

    def _build_pack_tab(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(10)

        tip = QLabel(
            "Ship receipts for a date range / item / payee into one ZIP or folder. "
            "Day list below uses Library (upload date) so you can see volume before packing "
            "(pack itself uses transaction date, same as evidence consolidate)."
        )
        tip.setWordWrap(True)
        tip.setStyleSheet(f"color:{_MUTED}; font:11px 'Segoe UI';")
        lay.addWidget(tip)

        filters = QHBoxLayout()
        self._pack_from = QDateEdit()
        self._pack_from.setCalendarPopup(True)
        self._pack_from.setDisplayFormat("yyyy-MM-dd")
        self._pack_from.setDate(QDate.currentDate().addMonths(-1))
        self._pack_to = QDateEdit()
        self._pack_to.setCalendarPopup(True)
        self._pack_to.setDisplayFormat("yyyy-MM-dd")
        self._pack_to.setDate(QDate.currentDate())
        self._pack_item = QLineEdit()
        self._pack_item.setPlaceholderText("Item (optional)")
        self._pack_payee = QLineEdit()
        self._pack_payee.setPlaceholderText("Payee (optional)")
        self._pack_own = QLineEdit()
        self._pack_own.setPlaceholderText("Ownership (optional)")
        self._pack_approved = QCheckBox("Approved only")
        self._pack_approved.setChecked(True)
        filters.addWidget(QLabel("From"))
        filters.addWidget(self._pack_from)
        filters.addWidget(QLabel("To"))
        filters.addWidget(self._pack_to)
        filters.addWidget(self._pack_item, 1)
        filters.addWidget(self._pack_payee, 1)
        filters.addWidget(self._pack_own, 1)
        filters.addWidget(self._pack_approved)
        lay.addLayout(filters)

        year_row = QHBoxLayout()
        year_row.addWidget(QLabel("Quick year:"))
        this_year = QDate.currentDate().year()
        for y in (this_year, this_year - 1, this_year - 2):
            btn = _ghost(str(y), "mdi.calendar")
            btn.clicked.connect(lambda _=False, year=y: self._set_year(year))
            year_row.addWidget(btn)
        year_row.addStretch()
        scan_btn = _ghost("Scan days in range", "mdi.chart-bar")
        scan_btn.clicked.connect(lambda: asyncio.ensure_future(self._scan_days()))
        year_row.addWidget(scan_btn)
        lay.addLayout(year_row)

        actions = QHBoxLayout()
        zip_btn = _primary("Download ZIP", "mdi.zip-box-outline")
        zip_btn.clicked.connect(
            lambda: asyncio.ensure_future(self._pack_download(to_folder=False))
        )
        folder_btn = _ghost("Save to folder…", "mdi.folder-download-outline")
        folder_btn.clicked.connect(
            lambda: asyncio.ensure_future(self._pack_download(to_folder=True))
        )
        day_zip = _ghost("Pack selected days", "mdi.calendar-export")
        day_zip.clicked.connect(
            lambda: asyncio.ensure_future(self._pack_selected_days())
        )
        actions.addWidget(zip_btn)
        actions.addWidget(folder_btn)
        actions.addWidget(day_zip)
        actions.addStretch()
        lay.addLayout(actions)

        days_lbl = QLabel("Receipts by upload day (select rows, then Pack selected days)")
        days_lbl.setStyleSheet(f"color:{_TEXT}; font:600 12px 'Segoe UI';")
        lay.addWidget(days_lbl)
        self._days = QTableWidget(0, 4)
        self._days.setHorizontalHeaderLabels(
            ["Day", "Files", "Approved", "Items (sample)"]
        )
        self._days.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._days.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._days.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._days.setAlternatingRowColors(True)
        self._days.verticalHeader().setVisible(False)
        self._days.setShowGrid(False)
        self._days.horizontalHeader().setStretchLastSection(True)
        self._days.setStyleSheet(
            f"QTableWidget{{border:1px solid {_BORDER}; border-radius:6px; font:12px 'Segoe UI';}}"
            f"QHeaderView::section{{background:{_BG}; border:none; padding:6px; "
            f"font:600 11px 'Segoe UI'; color:{_MUTED};}}"
        )
        lay.addWidget(self._days, 1)
        return page

    # ── Bootstrap / navigation ────────────────────────────────────────────

    def refresh(self) -> None:
        asyncio.ensure_future(self._reload_explorer())

    async def _bootstrap(self) -> None:
        await self._reload_explorer()

    def _set_year(self, year: int) -> None:
        self._pack_from.setDate(QDate(year, 1, 1))
        self._pack_to.setDate(QDate(year, 12, 31))

    def _crumb_text(self) -> str:
        if not self._prefix:
            return "This PC \\ Receipt disk"
        return "This PC \\ Receipt disk \\ " + self._prefix.replace("/", " \\ ")

    async def _reload_explorer(self) -> None:
        await self._rebuild_tree()
        await self._load_objects()

    async def _go_prefix(self, prefix: str) -> None:
        self._prefix = prefix or ""
        self._search.clear()
        self._crumb.setText(self._crumb_text())
        self._select_tree_prefix(self._prefix)
        await self._load_objects()

    async def _go_up(self) -> None:
        if not self._prefix:
            return
        parts = self._prefix.split("/")
        await self._go_prefix("/".join(parts[:-1]))

    async def _rebuild_tree(self) -> None:
        self._tree_loading = True
        try:
            tree = await list_storage_tree("")
        except Exception as exc:
            QMessageBox.critical(self, "Receipt storage", str(exc))
            self._tree_loading = False
            return
        # Build only after await so we never touch items cleared by a concurrent reload.
        self._tree.clear()
        root = QTreeWidgetItem(["Receipt disk"])
        root.setIcon(0, qta.icon("mdi.harddisk", color=_PRIMARY))
        root.setData(0, Qt.UserRole, {"prefix": "", "loaded": True})
        for folder in tree.get("folders") or []:
            child = QTreeWidgetItem([str(folder.get("name") or "")])
            child.setIcon(0, qta.icon("mdi.folder", color=_FOLDER))
            child.setData(
                0,
                Qt.UserRole,
                {"prefix": str(folder.get("prefix") or ""), "loaded": False},
            )
            child.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
            # Placeholder so expand chevron appears
            child.addChild(QTreeWidgetItem(["…"]))
            root.addChild(child)
        self._tree.addTopLevelItem(root)
        root.setExpanded(True)
        self._tree_loading = False
        self._select_tree_prefix(self._prefix)

    def _select_tree_prefix(self, prefix: str) -> None:
        def walk(item: QTreeWidgetItem) -> QTreeWidgetItem | None:
            data = item.data(0, Qt.UserRole) or {}
            if str(data.get("prefix") or "") == (prefix or ""):
                return item
            for i in range(item.childCount()):
                found = walk(item.child(i))
                if found is not None:
                    return found
            return None

        for i in range(self._tree.topLevelItemCount()):
            hit = walk(self._tree.topLevelItem(i))
            if hit is not None:
                self._tree.setCurrentItem(hit)
                return

    def _on_tree_click(self, item: QTreeWidgetItem, _col: int) -> None:
        data = item.data(0, Qt.UserRole) or {}
        prefix = str(data.get("prefix") or "")
        asyncio.ensure_future(self._go_prefix(prefix))

    def _on_tree_expand(self, item: QTreeWidgetItem) -> None:
        if self._tree_loading:
            return
        data = item.data(0, Qt.UserRole) or {}
        if data.get("loaded"):
            return
        asyncio.ensure_future(self._load_tree_children(item))

    async def _load_tree_children(self, item: QTreeWidgetItem) -> None:
        data = dict(item.data(0, Qt.UserRole) or {})
        prefix = str(data.get("prefix") or "")
        try:
            tree = await list_storage_tree(prefix)
        except Exception as exc:
            QMessageBox.critical(self, "Receipt storage", str(exc))
            return
        # Clear placeholder children
        while item.childCount():
            item.removeChild(item.child(0))
        for folder in tree.get("folders") or []:
            child = QTreeWidgetItem([str(folder.get("name") or "")])
            child.setIcon(0, qta.icon("mdi.folder", color=_FOLDER))
            child.setData(
                0,
                Qt.UserRole,
                {"prefix": str(folder.get("prefix") or ""), "loaded": False},
            )
            child.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
            child.addChild(QTreeWidgetItem(["…"]))
            item.addChild(child)
        data["loaded"] = True
        item.setData(0, Qt.UserRole, data)

    async def _load_objects(self) -> None:
        q = self._search.text().strip()
        try:
            objects = await list_storage_objects(prefix=self._prefix, q=q)
        except Exception as exc:
            QMessageBox.critical(self, "Receipt storage", str(exc))
            return
        self._objects = list(objects.get("items") or [])
        self._crumb.setText(self._crumb_text())
        self._table.setRowCount(0)
        for row in self._objects:
            r = self._table.rowCount()
            self._table.insertRow(r)
            name_item = QTableWidgetItem(
                qta.icon("mdi.file-document-outline", color=_PRIMARY),
                str(row.get("name") or ""),
            )
            name_item.setData(Qt.UserRole, row)
            status = str(row.get("inbox_status") or "")
            ctype = row.get("content_type") or ""
            type_s = status or (str(ctype).split("/")[-1] if ctype else "file")
            values = [
                name_item,
                QTableWidgetItem(type_s),
                QTableWidgetItem(_fmt_size(int(row.get("size") or 0))),
                QTableWidgetItem("yes" if row.get("indexed") else "disk only"),
                QTableWidgetItem(str(row.get("key") or "")),
            ]
            for c, cell in enumerate(values):
                cell.setData(Qt.UserRole, row)
                self._table.setItem(r, c, cell)
        self._status.setText(
            f"{self._prefix or 'root'} · {len(self._objects)} file(s)"
            + (f" · search “{q}”" if q else "")
        )

    def _selected_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in self._table.selectedItems():
            row = item.data(Qt.UserRole) or {}
            if not isinstance(row, dict):
                continue
            key = str(row.get("file_id") or row.get("key") or id(row))
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
        return rows

    def _view_selected(self) -> None:
        rows = self._selected_rows()
        if not rows:
            QMessageBox.information(self, "Receipt storage", "Select a file first.")
            return
        row = rows[0]
        fid = str(row.get("file_id") or "").strip()
        if not fid:
            QMessageBox.information(
                self,
                "Receipt storage",
                "This disk object has no receipt_files id, so it cannot open in the viewer.",
            )
            return
        open_receipt_viewer(self, fid, title_hint=str(row.get("name") or ""))

    async def _rematerialize_selected(self) -> None:
        ids = [
            str(r.get("file_id") or "").strip()
            for r in self._selected_rows()
            if r.get("file_id")
        ]
        if not ids:
            QMessageBox.information(
                self,
                "Receipt storage",
                "Select one or more indexed files to rematerialize.",
            )
            return
        try:
            result = await rematerialize_files(file_ids=ids)
            count = int(result.get("materialized") or 0)
            self._status.setText(f"Rematerialized {count} file set(s)")
            await self._load_objects()
            QMessageBox.information(
                self,
                "Rematerialize",
                f"Refreshed approved/ links.\nMaterialized sets: {count}",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Rematerialize failed", str(exc))

    async def _download_selected(self) -> None:
        rows = [r for r in self._selected_rows() if r.get("file_id")]
        if not rows:
            QMessageBox.information(
                self,
                "Download selected",
                "Select indexed file(s) with a receipt id first.",
            )
            return
        dest = QFileDialog.getExistingDirectory(
            self, "Save selected receipts to folder"
        )
        if not dest:
            return
        out_dir = Path(dest) / "tahmeed-selected-receipts"
        out_dir.mkdir(parents=True, exist_ok=True)
        ok = 0
        failed = 0
        for row in rows:
            fid = str(row.get("file_id") or "")
            name = str(row.get("name") or f"{fid}.bin")
            safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in name)[:100]
            try:
                payload, _meta = await fetch_receipt_file_bytes(fid)
                (out_dir / f"{fid[:8]}_{safe}").write_bytes(payload)
                ok += 1
            except Exception:
                failed += 1
        self._status.setText(f"Downloaded {ok} file(s)" + (f", {failed} failed" if failed else ""))
        QMessageBox.information(
            self,
            "Download selected",
            f"Saved {ok} file(s) to:\n{out_dir}"
            + (f"\nFailed: {failed}" if failed else ""),
        )

    # ── Pack tab actions ──────────────────────────────────────────────────

    async def _scan_days(self) -> None:
        d0 = self._pack_from.date().toPython()
        d1 = self._pack_to.date().toPython()
        try:
            rows = await list_receipt_library(
                date_from=d0.isoformat(),
                date_to=d1.isoformat(),
                item=self._pack_item.text().strip(),
                payee=self._pack_payee.text().strip(),
                ownership=self._pack_own.text().strip(),
                status="approved" if self._pack_approved.isChecked() else "all",
                limit=500,
            )
        except Exception as exc:
            QMessageBox.critical(self, "Scan days", str(exc))
            return
        by_day: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"files": 0, "approved": 0, "items": set()}
        )
        for row in rows:
            raw = row.get("uploaded_at")
            if hasattr(raw, "date"):
                day = raw.date().isoformat()
            else:
                day = str(raw or "")[:10] or "unknown"
            bucket = by_day[day]
            bucket["files"] += 1
            if str(row.get("inbox_status") or "") == "approved":
                bucket["approved"] += 1
            item = str(row.get("item") or "").strip()
            if item:
                bucket["items"].add(item.split(",")[0].strip())
        self._day_rows = []
        self._days.setRowCount(0)
        for day in sorted(by_day.keys(), reverse=True):
            info = by_day[day]
            sample = ", ".join(sorted(info["items"])[:3])
            rec = {
                "day": day,
                "files": info["files"],
                "approved": info["approved"],
                "items": sample,
            }
            self._day_rows.append(rec)
            r = self._days.rowCount()
            self._days.insertRow(r)
            for c, value in enumerate(
                [day, str(info["files"]), str(info["approved"]), sample]
            ):
                cell = QTableWidgetItem(value)
                cell.setData(Qt.UserRole, rec)
                self._days.setItem(r, c, cell)
        self._status.setText(
            f"Scan: {len(rows)} file(s) across {len(self._day_rows)} day(s)"
        )

    def _selected_days(self) -> list[str]:
        days: list[str] = []
        seen: set[str] = set()
        for item in self._days.selectedItems():
            row = item.data(Qt.UserRole) or {}
            day = str(row.get("day") or "")
            if day and day not in seen and day != "unknown":
                seen.add(day)
                days.append(day)
        return days

    async def _pack_download(self, *, to_folder: bool) -> None:
        d0 = self._pack_from.date().toPython()
        d1 = self._pack_to.date().toPython()
        if d1 < d0:
            QMessageBox.information(self, "Pack", "End date must be on or after start.")
            return
        await self._run_pack(
            date_from=d0,
            date_to=d1,
            to_folder=to_folder,
        )

    async def _pack_selected_days(self) -> None:
        days = self._selected_days()
        if not days:
            QMessageBox.information(
                self,
                "Pack selected days",
                "Scan the range first, then select one or more day rows.",
            )
            return
        # Pack each selected calendar day (transaction-date filter = that day)
        combined = io.BytesIO()
        total_files = 0
        with zipfile.ZipFile(combined, "w", compression=zipfile.ZIP_DEFLATED) as zout:
            for day in days:
                try:
                    payload, meta = await download_consolidate_pack(
                        date_from=day,
                        date_to=day,
                        item=self._pack_item.text().strip(),
                        payee=self._pack_payee.text().strip(),
                        ownership=self._pack_own.text().strip(),
                        approved_only=self._pack_approved.isChecked(),
                    )
                except Exception as exc:
                    QMessageBox.warning(
                        self, "Pack selected days", f"{day} failed:\n{exc}"
                    )
                    continue
                total_files += int(meta.get("files") or 0) if str(meta.get("files") or "").isdigit() else 0
                with zipfile.ZipFile(io.BytesIO(payload), "r") as zin:
                    for info in zin.infolist():
                        if info.is_dir():
                            continue
                        name = info.filename
                        if name.endswith("SUMMARY.txt"):
                            name = f"{day}/SUMMARY.txt"
                        else:
                            name = f"{day}/{name}"
                        zout.writestr(name, zin.read(info))
        data = combined.getvalue()
        if not data:
            QMessageBox.information(self, "Pack selected days", "Nothing packed.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save multi-day pack",
            f"tahmeed-days_{days[-1]}_{days[0]}.zip"
            if len(days) > 1
            else f"tahmeed-day_{days[0]}.zip",
            "ZIP files (*.zip)",
        )
        if not path:
            return
        out = Path(path)
        if out.suffix.lower() != ".zip":
            out = out.with_suffix(".zip")
        out.write_bytes(data)
        self._status.setText(f"Saved multi-day pack · {len(days)} day(s)")
        QMessageBox.information(
            self,
            "Pack selected days",
            f"Saved {len(days)} day folder(s) to:\n{out}\nFiles reported: {total_files}",
        )

    async def _run_pack(
        self, *, date_from: date, date_to: date, to_folder: bool
    ) -> None:
        try:
            payload, meta = await download_consolidate_pack(
                date_from=date_from.isoformat(),
                date_to=date_to.isoformat(),
                item=self._pack_item.text().strip(),
                payee=self._pack_payee.text().strip(),
                ownership=self._pack_own.text().strip(),
                approved_only=self._pack_approved.isChecked(),
            )
        except Exception as exc:
            QMessageBox.critical(self, "Pack failed", str(exc))
            return
        default_name = (
            meta.get("filename")
            or f"tahmeed-receipts_{date_from.isoformat()}_{date_to.isoformat()}.zip"
        )
        summary = (
            f"Rows: {meta.get('rows') or '?'}\n"
            f"With file: {meta.get('with_file') or '?'}\n"
            f"Files: {meta.get('files') or '?'}\n"
            f"Missing: {meta.get('missing') or '0'}\n"
            f"SHA-256: {meta.get('sha256') or ''}"
        )
        if to_folder:
            dest = QFileDialog.getExistingDirectory(self, "Save pack to folder")
            if not dest:
                return
            out_dir = Path(dest) / Path(default_name).stem
            out_dir.mkdir(parents=True, exist_ok=True)
            zip_path = out_dir / default_name
            zip_path.write_bytes(payload)
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(out_dir)
            self._status.setText(f"Packed to {out_dir}")
            QMessageBox.information(
                self, "Pack", f"Saved and extracted to:\n{out_dir}\n\n{summary}"
            )
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save pack ZIP", default_name, "ZIP files (*.zip)"
        )
        if not path:
            return
        out = Path(path)
        if out.suffix.lower() != ".zip":
            out = out.with_suffix(".zip")
        out.write_bytes(payload)
        self._status.setText(f"Saved {out.name}")
        QMessageBox.information(self, "Pack", f"Saved to:\n{out}\n\n{summary}")
