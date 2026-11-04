"""Accountant — shared Uploads Trash for Separate Expenses / Fuel imports."""

from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional

import qtawesome as qta
from PySide6.QtCore import Qt, QSize
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

_COL_CHK = 0
_COL_NUM = 1
_COL_FEED = 2
_COL_FILE = 3
_COL_RECS = 4
_COL_IMPORTED = 5
_COL_TRASHED = 6

_HEADERS = ["", "#", "Feed", "File", "Records", "Imported", "Trashed"]
_COL_DEFAULTS = [36, 40, 130, 280, 80, 110, 110]


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


class FeedsTrashWidget(QWidget):
    """Soft-deleted feed uploads: restore or permanently purge."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._rows: List[dict] = []
        self._busy = False
        self._feed_options: List[tuple[str, str]] = []
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
            "Soft-deleted import batches from Separate Expenses and Fuel.",
            size=11, color=_T2,
        )
        title_box.addWidget(self._subtitle)
        header.addLayout(title_box, 1)
        root.addLayout(header)

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

        self._table = QTableWidget(0, len(_HEADERS))
        self._table.setHorizontalHeaderLabels(_HEADERS)
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
        hdr.setSectionResizeMode(_COL_FILE, QHeaderView.Stretch)
        for i, w in enumerate(_COL_DEFAULTS):
            self._table.setColumnWidth(i, w)
        self._table.itemSelectionChanged.connect(self._sync_actions)
        self._table.cellChanged.connect(self._on_cell_changed)
        root.addWidget(self._table, 1)

        self._status = _lbl("", size=11, color=_T2)
        root.addWidget(self._status)

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

    def _populate_feed_filter(self, feeds: List[dict]) -> None:
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
        # Restore prior selection when possible.
        idx = self._feed_cb.findData(current)
        self._feed_cb.setCurrentIndex(idx if idx >= 0 else 0)
        self._feed_cb.blockSignals(False)

    async def _reload(self) -> None:
        if self._busy:
            return
        self._busy = True
        self._sync_actions()
        show_widget_loading(self, "Loading uploads trash…")
        try:
            from tahmeed.services import feed_service as feeds

            payload = await feeds.list_trashed_uploads(
                feed=self._selected_feed(),
                search=self._search.text().strip(),
                skip=0,
                limit=500,
            )
            self._populate_feed_filter(list(payload.get("feeds") or []))
            self._rows = list(payload.get("items") or [])
            total = int(payload.get("total") or len(self._rows))
            self._fill_table()
            self._subtitle.setText(
                f"{total} soft-deleted upload{'s' if total != 1 else ''}. "
                "Restore to bring them back, or delete forever."
            )
            self._status.setText(
                f"Showing {len(self._rows):,} of {total:,}"
                if total
                else "Trash is empty."
            )
        except Exception as exc:
            self._rows = []
            self._table.setRowCount(0)
            self._status.setText(f"Failed to load trash: {exc}")
        finally:
            hide_widget_loading(self)
            self._busy = False
            self._sync_actions()

    def _fill_table(self) -> None:
        t = self._table
        t.blockSignals(True)
        t.setRowCount(0)
        if not self._rows:
            t.setRowCount(1)
            empty = QTableWidgetItem("Trash is empty.")
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

    def _on_bulk_restore(self) -> None:
        rows = self._selected_rows()
        if not rows:
            return
        if QMessageBox.question(
            self,
            "Restore uploads",
            f"Restore {len(rows)} upload{'s' if len(rows) != 1 else ''} "
            "back to their feed?",
        ) != QMessageBox.Yes:
            return
        schedule_coro(self._restore_rows(rows))

    def _on_bulk_purge(self) -> None:
        rows = self._selected_rows()
        if not rows:
            return
        if QMessageBox.question(
            self,
            "Delete forever",
            f"Permanently delete {len(rows)} upload{'s' if len(rows) != 1 else ''}?\n"
            "This cannot be undone.",
        ) != QMessageBox.Yes:
            return
        schedule_coro(self._purge_rows(rows))

    async def _restore_rows(self, rows: List[dict]) -> None:
        from tahmeed.services import feed_service as feeds

        self._busy = True
        self._sync_actions()
        show_widget_loading(self, "Restoring…")
        ok = 0
        try:
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
        QMessageBox.information(
            self, "Restored", f"Restored {ok} upload{'s' if ok != 1 else ''}."
        )
        await self._reload()

    async def _purge_rows(self, rows: List[dict]) -> None:
        from tahmeed.services import feed_service as feeds

        self._busy = True
        self._sync_actions()
        show_widget_loading(self, "Deleting…")
        ok = 0
        try:
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
        QMessageBox.information(
            self,
            "Deleted",
            f"Permanently deleted {ok} upload{'s' if ok != 1 else ''}.",
        )
        await self._reload()
