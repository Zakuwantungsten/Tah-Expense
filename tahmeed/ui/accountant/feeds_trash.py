"""Accountant — shared Uploads Trash for Separate Expenses / Fuel imports."""

from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional

import qtawesome as qta
from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtGui import QColor, QBrush
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QLineEdit, QMessageBox, QComboBox, QCheckBox,
)

from tahmeed.ui.accountant.separate_expenses import (
    _ROW_H, _finish_table_row, _stripe_bg, _table_cell_font, _table_style,
)
from tahmeed.ui.async_utils import schedule_coro
from tahmeed.ui.widgets.loading_overlay import hide_widget_loading, show_widget_loading

_BG = "#F4F6F8"
_WHITE = "#FFFFFF"
_BORDER = "#E5E7EB"
_BLUE = "#0077C5"
_RED = "#DC2626"
_RED_L = "#FEF2F2"
_T1 = "#111827"
_T2 = "#6B7280"
_TM = "#9CA3AF"

_MODE_UPLOADS = 0
_MODE_SKIPPED = 1

_COL_CHK = 0
_COL_NUM = 1
_COL_FEED = 2
_COL_FILE = 3
_COL_RECS = 4
_COL_IMPORTED = 5
_COL_TRASHED = 6

_UPLOAD_HEADERS = ["", "#", "Feed", "File", "Records", "Imported", "Trashed"]
_UPLOAD_COL_DEFAULTS = [36, 40, 130, 280, 80, 110, 110]

_SKIP_HEADERS = [
    "", "#", "Feed", "Truck", "File", "Sheet", "Reason", "Trashed",
]
_SKIP_COL_DEFAULTS = [36, 40, 130, 110, 220, 80, 120, 110]

_SKIPPED_FEED_LABELS: list[tuple[str, str]] = [
    ("toll_plaza", "Toll Plaza"),
    ("parking_congo", "Parking Congo"),
    ("congo_expenses", "Congo Expenses"),
    ("ahmed_kimvi", "Ahmed Kimvi (Klesa)"),
    ("zambia_parking", "Zambia Parking"),
    ("afritrack", "Afritrack"),
    ("third_party", "Third Party Covers"),
    ("comesa", "COMESA Covers"),
    ("rahntech", "RahnTech"),
    ("rpa_schedule", "RPA Schedule"),
    ("bonds", "Bonds"),
    ("diesel_infinity", "Infinity"),
    ("diesel_lake_zambia", "Lake Zambia"),
    ("diesel_lake_tunduma", "Lake Tunduma"),
    ("diesel_gbp", "GBP Diesel"),
]

_FEED_KEY_LABEL = {k: v for k, v in _SKIPPED_FEED_LABELS}


def _lbl(text="", size=13, weight=400, color=_T1) -> QLabel:
    w = QLabel(text)
    w.setStyleSheet(
        f"color:{color};font-size:{size}px;font-weight:{weight};"
        "font-family:'Segoe UI';background:transparent;"
    )
    return w


def _btn(text: str, icon: str = "", *, primary=False, danger=False) -> QPushButton:
    btn = QPushButton(text)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setFixedHeight(30)
    if icon:
        btn.setIcon(qta.icon(icon, color="#FFF" if primary else (_RED if danger else _T1)))
        btn.setIconSize(QSize(14, 14))
    if primary:
        btn.setStyleSheet(
            f"QPushButton{{background:{_BLUE};color:#FFF;border:none;border-radius:5px;"
            "font-size:12px;font-weight:600;font-family:'Segoe UI';"
            "padding:0 12px;}"
            "QPushButton:hover{background:#005EA3;}"
            "QPushButton:disabled{background:#93C5FD;}"
        )
    elif danger:
        btn.setStyleSheet(
            f"QPushButton{{background:transparent;color:{_RED};border:1px solid #FECACA;"
            "border-radius:5px;font-size:12px;font-weight:600;"
            "font-family:'Segoe UI';padding:0 12px;}"
            f"QPushButton:hover{{background:{_RED_L};}}"
            "QPushButton:disabled{color:#FCA5A5;border-color:#FEE2E2;}"
        )
    else:
        btn.setStyleSheet(
            f"QPushButton{{background:transparent;color:{_T1};border:1px solid {_BORDER};"
            "border-radius:5px;font-size:12px;font-weight:500;"
            "font-family:'Segoe UI';padding:0 12px;}"
            f"QPushButton:hover{{background:{_BG};}}"
            "QPushButton:disabled{color:#D1D5DB;}"
        )
    return btn


def _fmt_dt(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%d %b %Y")
    if isinstance(value, str) and value.strip():
        return value.strip()[:16]
    return "—"


def _reason_label(reason: str) -> str:
    if reason == "not_in_registry":
        return "Not in registry"
    if reason == "invalid_format":
        return "Invalid format"
    return reason or "—"


def _cell(
    text: str,
    align: Qt.AlignmentFlag = Qt.AlignLeft | Qt.AlignVCenter,
    *,
    bg: str = "",
) -> QTableWidgetItem:
    item = QTableWidgetItem(str(text) if text is not None else "—")
    item.setTextAlignment(align)
    item.setFont(_table_cell_font())
    item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
    if bg:
        item.setBackground(QBrush(QColor(bg)))
    item.setForeground(QBrush(QColor(_T1)))
    return item


class _TrashModeBar(QWidget):
    """Uploads | Skipped trucks segment control."""

    mode_changed = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._index = _MODE_UPLOADS
        self._buttons: list[QPushButton] = []
        hl = QHBoxLayout(self)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(4)
        for i, label in enumerate(("Uploads", "Skipped trucks")):
            btn = QPushButton(label)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(30)
            btn.clicked.connect(lambda _=False, idx=i: self.set_index(idx))
            self._buttons.append(btn)
            hl.addWidget(btn)
        hl.addStretch()
        self._apply()

    def set_index(self, idx: int, *, emit: bool = True) -> None:
        if idx not in (_MODE_UPLOADS, _MODE_SKIPPED) or idx == self._index:
            if idx == self._index:
                self._apply()
            return
        self._index = idx
        self._apply()
        if emit:
            self.mode_changed.emit(idx)

    def current_index(self) -> int:
        return self._index

    def _apply(self) -> None:
        active = (
            f"QPushButton{{background:{_BLUE};color:#FFF;border:none;border-radius:5px;"
            "font-size:12px;font-weight:600;font-family:'Segoe UI';padding:0 14px;}}"
        )
        inactive = (
            f"QPushButton{{background:{_WHITE};color:{_T1};border:1px solid {_BORDER};"
            "border-radius:5px;font-size:12px;font-family:'Segoe UI';padding:0 14px;}}"
            f"QPushButton:hover{{background:{_BG};}}"
        )
        for i, btn in enumerate(self._buttons):
            btn.setStyleSheet(active if i == self._index else inactive)


class FeedsTrashWidget(QWidget):
    """Soft-deleted feed uploads and skipped truck rows: restore or purge."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._rows: List[dict] = []
        self._busy = False
        self._feed_options: List[tuple[str, str]] = []
        self._mode = _MODE_UPLOADS
        self._build()
        schedule_coro(self._reload())

    def refresh(self) -> None:
        schedule_coro(self._reload())

    def _build(self) -> None:
        self.setStyleSheet(f"background:{_BG};")
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 14, 20, 14)
        root.setSpacing(10)

        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        title_box.addWidget(_lbl("Uploads Trash", size=17, weight=700))
        self._subtitle = _lbl(
            "Soft-deleted import batches and skipped truck rows.",
            size=11, color=_T2,
        )
        title_box.addWidget(self._subtitle)
        header.addLayout(title_box, 1)
        root.addLayout(header)

        self._mode_bar = _TrashModeBar()
        self._mode_bar.mode_changed.connect(self._on_mode)
        root.addWidget(self._mode_bar)

        tools = QFrame()
        tools.setStyleSheet(
            f"QFrame{{background:{_WHITE};border:1px solid {_BORDER};border-radius:6px;}}"
        )
        tools_hl = QHBoxLayout(tools)
        tools_hl.setContentsMargins(10, 7, 10, 7)
        tools_hl.setSpacing(8)

        self._feed_cb = QComboBox()
        self._feed_cb.setFixedHeight(30)
        self._feed_cb.setFixedWidth(160)
        self._feed_cb.setStyleSheet(
            f"QComboBox{{border:1px solid {_BORDER};border-radius:5px;"
            f"background:#F9FAFB;color:{_T1};font-size:12px;"
            "font-family:'Segoe UI';padding:0 8px;}"
        )
        self._feed_cb.addItem("All feeds", "")
        self._feed_cb.currentIndexChanged.connect(lambda _i: self.refresh())
        tools_hl.addWidget(self._feed_cb)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search file name…")
        self._search.setClearButtonEnabled(True)
        self._search.setFixedHeight(30)
        self._search.setFixedWidth(200)
        self._search.setStyleSheet(
            f"QLineEdit{{border:1px solid {_BORDER};border-radius:5px;"
            f"background:#F9FAFB;color:{_T1};font-size:12px;"
            "font-family:'Segoe UI';padding:0 10px;}"
            f"QLineEdit:focus{{border-color:{_BLUE};background:{_WHITE};}}"
        )
        self._search.returnPressed.connect(self.refresh)
        tools_hl.addWidget(self._search)

        search_btn = _btn("Search", "mdi.magnify")
        search_btn.clicked.connect(self.refresh)
        tools_hl.addWidget(search_btn)

        self._restore_btn = _btn("Restore", "mdi.restore", primary=True)
        self._restore_btn.setEnabled(False)
        self._restore_btn.clicked.connect(self._on_bulk_restore)
        tools_hl.addWidget(self._restore_btn)

        self._purge_btn = _btn("Delete forever", "mdi.delete-forever", danger=True)
        self._purge_btn.setEnabled(False)
        self._purge_btn.clicked.connect(self._on_bulk_purge)
        tools_hl.addWidget(self._purge_btn)

        tools_hl.addStretch(1)

        refresh_btn = _btn("Refresh", "mdi.refresh")
        refresh_btn.clicked.connect(self.refresh)
        tools_hl.addWidget(refresh_btn)
        root.addWidget(tools)

        self._table = QTableWidget(0, len(_UPLOAD_HEADERS))
        self._table.setHorizontalHeaderLabels(_UPLOAD_HEADERS)
        self._table.setStyleSheet(_table_style())
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.setShowGrid(False)
        self._table.setAlternatingRowColors(False)
        self._table.setFocusPolicy(Qt.StrongFocus)
        hdr = self._table.horizontalHeader()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        for i, w in enumerate(_UPLOAD_COL_DEFAULTS):
            self._table.setColumnWidth(i, w)
        self._table.itemSelectionChanged.connect(self._sync_actions)
        self._table.cellChanged.connect(self._on_cell_changed)
        root.addWidget(self._table, 1)

        self._status = _lbl("", size=11, color=_T2)
        root.addWidget(self._status)

    def _on_mode(self, idx: int) -> None:
        self._mode = idx
        if idx == _MODE_SKIPPED:
            self._search.setPlaceholderText("Search truck, file, sheet…")
            self._configure_skipped_columns()
        else:
            self._search.setPlaceholderText("Search file name…")
            self._configure_upload_columns()
        self.refresh()

    def _configure_upload_columns(self) -> None:
        t = self._table
        t.clearSpans()
        t.setColumnCount(len(_UPLOAD_HEADERS))
        t.setHorizontalHeaderLabels(_UPLOAD_HEADERS)
        hdr = t.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        hdr.setSectionResizeMode(3, QHeaderView.Stretch)  # File
        for i, w in enumerate(_UPLOAD_COL_DEFAULTS):
            t.setColumnWidth(i, w)

    def _configure_skipped_columns(self) -> None:
        t = self._table
        t.clearSpans()
        t.setColumnCount(len(_SKIP_HEADERS))
        t.setHorizontalHeaderLabels(_SKIP_HEADERS)
        hdr = t.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        hdr.setSectionResizeMode(4, QHeaderView.Stretch)  # File
        for i, w in enumerate(_SKIP_COL_DEFAULTS):
            t.setColumnWidth(i, w)

    def _selected_feed(self) -> str:
        return str(self._feed_cb.currentData() or "")

    def _selected_rows(self) -> List[dict]:
        out: List[dict] = []
        for r in range(self._table.rowCount()):
            w = self._table.cellWidget(r, _COL_CHK)
            if isinstance(w, QCheckBox) and w.isChecked():
                if 0 <= r < len(self._rows):
                    out.append(self._rows[r])
        if out:
            return out
        for idx in self._table.selectionModel().selectedRows():
            r = idx.row()
            if 0 <= r < len(self._rows):
                out.append(self._rows[r])
        return out

    def _sync_actions(self) -> None:
        n = len(self._selected_rows())
        self._restore_btn.setEnabled(n > 0 and not self._busy)
        self._purge_btn.setEnabled(n > 0 and not self._busy)

    def _on_cell_changed(self, _row: int, _col: int) -> None:
        self._sync_actions()

    def _populate_feed_filter_uploads(self, feeds: List[dict]) -> None:
        current = self._selected_feed()
        self._feed_cb.blockSignals(True)
        self._feed_cb.clear()
        self._feed_cb.addItem("All feeds", "")
        self._feed_options = []
        for row in feeds or []:
            slug = str(row.get("slug") or "")
            label = str(row.get("label") or slug)
            if not slug:
                continue
            self._feed_options.append((slug, label))
            self._feed_cb.addItem(label, slug)
        idx = self._feed_cb.findData(current)
        self._feed_cb.setCurrentIndex(idx if idx >= 0 else 0)
        self._feed_cb.blockSignals(False)

    def _populate_feed_filter_skipped(self) -> None:
        current = self._selected_feed()
        self._feed_cb.blockSignals(True)
        self._feed_cb.clear()
        self._feed_cb.addItem("All feeds", "")
        for key, label in _SKIPPED_FEED_LABELS:
            self._feed_cb.addItem(label, key)
        idx = self._feed_cb.findData(current)
        self._feed_cb.setCurrentIndex(idx if idx >= 0 else 0)
        self._feed_cb.blockSignals(False)

    async def _reload(self) -> None:
        if self._busy:
            return
        self._busy = True
        self._sync_actions()
        loading = (
            "Loading skipped trash…"
            if self._mode == _MODE_SKIPPED
            else "Loading uploads trash…"
        )
        show_widget_loading(self, loading)
        try:
            if self._mode == _MODE_SKIPPED:
                await self._reload_skipped()
            else:
                await self._reload_uploads()
        except Exception as exc:
            self._rows = []
            self._table.setRowCount(0)
            self._status.setText(f"Failed to load trash: {exc}")
        finally:
            hide_widget_loading(self)
            self._busy = False
            self._sync_actions()

    async def _reload_uploads(self) -> None:
        from tahmeed.services import feed_service as feeds

        payload = await feeds.list_trashed_uploads(
            feed=self._selected_feed(),
            search=self._search.text().strip(),
            skip=0,
            limit=500,
        )
        self._populate_feed_filter_uploads(list(payload.get("feeds") or []))
        self._rows = list(payload.get("items") or [])
        total = int(payload.get("total") or len(self._rows))
        self._fill_uploads_table()
        self._subtitle.setText(
            f"{total} soft-deleted upload{'s' if total != 1 else ''}. "
            "Restore to bring them back, or delete forever."
        )
        self._status.setText(
            f"Showing {len(self._rows):,} of {total:,}"
            if total
            else "Uploads trash is empty."
        )

    async def _reload_skipped(self) -> None:
        from tahmeed.services import accountant_service as svc

        self._populate_feed_filter_skipped()
        payload = await svc.list_trashed_skipped_import_rows(
            feed_key=self._selected_feed(),
            search=self._search.text().strip(),
            skip=0,
            limit=500,
        )
        self._rows = list(payload.get("items") or [])
        total = int(payload.get("total") or len(self._rows))
        self._fill_skipped_table()
        self._subtitle.setText(
            f"{total} soft-deleted skipped truck row"
            f"{'' if total == 1 else 's'}. "
            "Restore returns them to that feed’s Skipped tab until the truck is fixed."
        )
        self._status.setText(
            f"Showing {len(self._rows):,} of {total:,}"
            if total
            else "Skipped trash is empty."
        )

    def _fill_uploads_table(self) -> None:
        t = self._table
        t.blockSignals(True)
        t.setRowCount(0)
        if not self._rows:
            t.setRowCount(1)
            empty = QTableWidgetItem("Uploads trash is empty.")
            empty.setFlags(Qt.ItemIsEnabled)
            empty.setForeground(QBrush(QColor(_TM)))
            t.setItem(0, _COL_FILE, empty)
            t.setSpan(0, _COL_FILE, 1, 2)
            t.blockSignals(False)
            return

        for i, row in enumerate(self._rows):
            r = t.rowCount()
            t.insertRow(r)
            t.setRowHeight(r, _ROW_H)
            bg = _stripe_bg(r)

            chk = QCheckBox()
            chk.setStyleSheet("background:transparent;margin-left:10px;")
            chk.stateChanged.connect(lambda _s: self._sync_actions())
            t.setCellWidget(r, _COL_CHK, chk)

            t.setItem(r, _COL_NUM, _cell(str(i + 1), Qt.AlignCenter, bg=bg))
            t.setItem(r, _COL_FEED, _cell(str(row.get("feed_label") or "—"), bg=bg))
            t.setItem(r, _COL_FILE, _cell(str(row.get("source_filename") or "—"), bg=bg))
            t.setItem(
                r,
                _COL_RECS,
                _cell(
                    f"{int(row.get('record_count') or 0):,}",
                    Qt.AlignRight | Qt.AlignVCenter,
                    bg=bg,
                ),
            )
            t.setItem(r, _COL_IMPORTED, _cell(_fmt_dt(row.get("import_date")), bg=bg))
            t.setItem(r, _COL_TRASHED, _cell(_fmt_dt(row.get("trashed_at")), bg=bg))
            _finish_table_row(t, r, bg=bg)
        t.blockSignals(False)

    def _fill_skipped_table(self) -> None:
        t = self._table
        t.blockSignals(True)
        t.setRowCount(0)
        if not self._rows:
            t.setRowCount(1)
            empty = QTableWidgetItem("Skipped trash is empty.")
            empty.setFlags(Qt.ItemIsEnabled)
            empty.setForeground(QBrush(QColor(_TM)))
            t.setItem(0, 4, empty)
            t.setSpan(0, 4, 1, 2)
            t.blockSignals(False)
            return

        for i, row in enumerate(self._rows):
            r = t.rowCount()
            t.insertRow(r)
            t.setRowHeight(r, _ROW_H)
            bg = _stripe_bg(r)
            feed_key = str(row.get("feed_key") or "")
            feed_label = _FEED_KEY_LABEL.get(feed_key, feed_key or "—")

            chk = QCheckBox()
            chk.setStyleSheet("background:transparent;margin-left:10px;")
            chk.stateChanged.connect(lambda _s: self._sync_actions())
            t.setCellWidget(r, _COL_CHK, chk)

            vals = [
                (str(i + 1), Qt.AlignCenter),
                (feed_label, Qt.AlignLeft | Qt.AlignVCenter),
                (str(row.get("truck_value") or "—"), Qt.AlignLeft | Qt.AlignVCenter),
                (str(row.get("source_filename") or "—"), Qt.AlignLeft | Qt.AlignVCenter),
                (str(row.get("sheet_label") or "—"), Qt.AlignLeft | Qt.AlignVCenter),
                (_reason_label(str(row.get("reason") or "")), Qt.AlignLeft | Qt.AlignVCenter),
                (_fmt_dt(row.get("trashed_at")), Qt.AlignLeft | Qt.AlignVCenter),
            ]
            for c, (text, align) in enumerate(vals, start=1):
                t.setItem(r, c, _cell(text, align, bg=bg))
            _finish_table_row(t, r, bg=bg)
        t.blockSignals(False)

    def _on_bulk_restore(self) -> None:
        rows = self._selected_rows()
        if not rows:
            return
        if self._mode == _MODE_SKIPPED:
            msg = (
                f"Restore {len(rows)} skipped row{'s' if len(rows) != 1 else ''} "
                "back to Skipped?\nThey stay in Skipped until the truck is fixed "
                "and re-uploaded."
            )
            title = "Restore skipped"
        else:
            msg = (
                f"Restore {len(rows)} upload{'s' if len(rows) != 1 else ''} "
                "back to their feed?"
            )
            title = "Restore uploads"
        if QMessageBox.question(self, title, msg) != QMessageBox.Yes:
            return
        schedule_coro(self._restore_rows(rows))

    def _on_bulk_purge(self) -> None:
        rows = self._selected_rows()
        if not rows:
            return
        kind = "skipped row" if self._mode == _MODE_SKIPPED else "upload"
        if QMessageBox.question(
            self,
            "Delete forever",
            f"Permanently delete {len(rows)} {kind}"
            f"{'' if len(rows) == 1 else 's'}?\nThis cannot be undone.",
        ) != QMessageBox.Yes:
            return
        schedule_coro(self._purge_rows(rows))

    async def _restore_rows(self, rows: List[dict]) -> None:
        self._busy = True
        self._sync_actions()
        show_widget_loading(self, "Restoring…")
        ok = 0
        try:
            if self._mode == _MODE_SKIPPED:
                from tahmeed.services import accountant_service as svc

                ids = [str(r.get("_id") or "") for r in rows if r.get("_id")]
                ok = await svc.restore_skipped_import_rows(ids)
            else:
                from tahmeed.services import feed_service as feeds

                for row in rows:
                    feed = str(row.get("feed") or "")
                    uid = str(row.get("upload_id") or "")
                    if not feed or not uid:
                        continue
                    n = await feeds.restore_upload(feed, uid)
                    if n > 0:
                        ok += 1
        except Exception as exc:
            hide_widget_loading(self)
            self._busy = False
            QMessageBox.critical(self, "Restore failed", str(exc))
            self._sync_actions()
            return
        hide_widget_loading(self)
        self._busy = False
        if self._mode == _MODE_SKIPPED:
            QMessageBox.information(
                self,
                "Restored",
                f"Restored {ok} skipped row{'s' if ok != 1 else ''} "
                "to Skipped.",
            )
        else:
            QMessageBox.information(
                self, "Restored", f"Restored {ok} upload{'s' if ok != 1 else ''}."
            )
        await self._reload()

    async def _purge_rows(self, rows: List[dict]) -> None:
        self._busy = True
        self._sync_actions()
        show_widget_loading(self, "Deleting…")
        ok = 0
        try:
            if self._mode == _MODE_SKIPPED:
                from tahmeed.services import accountant_service as svc

                ids = [str(r.get("_id") or "") for r in rows if r.get("_id")]
                ok = await svc.purge_skipped_import_rows(ids)
            else:
                from tahmeed.services import feed_service as feeds

                for row in rows:
                    feed = str(row.get("feed") or "")
                    uid = str(row.get("upload_id") or "")
                    if not feed or not uid:
                        continue
                    n = await feeds.purge_upload(feed, uid)
                    if n > 0:
                        ok += 1
        except Exception as exc:
            hide_widget_loading(self)
            self._busy = False
            QMessageBox.critical(self, "Delete failed", str(exc))
            self._sync_actions()
            return
        hide_widget_loading(self)
        self._busy = False
        kind = "skipped row" if self._mode == _MODE_SKIPPED else "upload"
        QMessageBox.information(
            self,
            "Deleted",
            f"Permanently deleted {ok} {kind}{'s' if ok != 1 else ''}.",
        )
        await self._reload()
