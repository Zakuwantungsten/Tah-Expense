"""Admin / accountant panel: see and choose the server receipt disk."""

from __future__ import annotations

import asyncio
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from tahmeed.services.receipt_storage_service import (
    clear_storage_override,
    get_storage_status,
    probe_storage,
    set_storage_path,
)


class ReceiptStoragePanel(QWidget):
    """Shows API-host path, free space, candidates, and apply / probe actions."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._status: dict[str, Any] = {}
        self._busy = False
        self._build()
        asyncio.ensure_future(self.refresh())

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)

        heading = QLabel("Receipt disk")
        heading.setStyleSheet("color:#111827; font:700 18px 'Segoe UI';")
        root.addWidget(heading)

        hint = QLabel(
            "Receipt files are stored on the API server, not on this PC. "
            "Pick a connected drive or folder on that host, test a write, "
            "then apply. Changing the path does not move files already stored "
            "under the old directory."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#6B7280; font:12px 'Segoe UI';")
        root.addWidget(hint)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(8)

        self._host = QLabel("—")
        self._platform = QLabel("—")
        self._effective = QLabel("—")
        self._effective.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._source = QLabel("—")
        self._env = QLabel("—")
        self._env.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._space = QLabel("—")
        self._writable = QLabel("—")
        self._s3 = QLabel("—")
        self._pending = QLabel("—")

        for widget in (
            self._host,
            self._platform,
            self._effective,
            self._source,
            self._env,
            self._space,
            self._writable,
            self._s3,
            self._pending,
        ):
            widget.setStyleSheet("color:#111827; font:13px 'Segoe UI';")

        form.addRow("API host:", self._host)
        form.addRow("Platform:", self._platform)
        form.addRow("Active path:", self._effective)
        form.addRow("Configured by:", self._source)
        form.addRow("Env default:", self._env)
        form.addRow("Disk space:", self._space)
        form.addRow("Writable:", self._writable)
        form.addRow("S3 backup:", self._s3)
        form.addRow("Rows pending S3:", self._pending)
        root.addLayout(form)

        path_row = QHBoxLayout()
        path_row.setSpacing(8)
        self._path = QLineEdit()
        self._path.setPlaceholderText("Absolute path on the API host, e.g. E:\\tahmeed-receipts")
        path_row.addWidget(self._path, 1)
        self._create = QCheckBox("Create folder if missing")
        self._create.setChecked(True)
        path_row.addWidget(self._create)
        root.addLayout(path_row)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.clicked.connect(lambda: asyncio.ensure_future(self.refresh()))
        self._probe_btn = QPushButton("Test write")
        self._probe_btn.clicked.connect(self._on_probe)
        self._apply_btn = QPushButton("Apply path")
        self._apply_btn.setStyleSheet(
            "QPushButton{background:#0077C5;color:#FFFFFF;border:none;"
            "border-radius:6px;padding:6px 14px;font:600 12px 'Segoe UI';}"
            "QPushButton:hover:!disabled{background:#005EA3;}"
            "QPushButton:disabled{background:#93C5FD;}"
        )
        self._apply_btn.clicked.connect(self._on_apply)
        self._clear_btn = QPushButton("Use env default")
        self._clear_btn.clicked.connect(self._on_clear)
        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet("color:#16A34A; font-size:12px;")
        for btn in (
            self._refresh_btn,
            self._probe_btn,
            self._apply_btn,
            self._clear_btn,
        ):
            actions.addWidget(btn)
        actions.addWidget(self._status_lbl)
        actions.addStretch()
        root.addLayout(actions)

        cand_lbl = QLabel("Connected drives / mounts on the API host")
        cand_lbl.setStyleSheet("color:#374151; font:600 13px 'Segoe UI';")
        root.addWidget(cand_lbl)

        pick_row = QHBoxLayout()
        pick_row.setSpacing(8)
        self._candidate = QComboBox()
        self._candidate.setMinimumWidth(280)
        self._candidate.currentIndexChanged.connect(self._on_candidate_picked)
        pick_row.addWidget(self._candidate, 1)
        use_btn = QPushButton("Use suggested folder")
        use_btn.clicked.connect(self._use_suggested)
        pick_row.addWidget(use_btn)
        root.addLayout(pick_row)

        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(
            ["Label", "Path", "Free", "Total", "Writable"]
        )
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.cellClicked.connect(self._on_row_clicked)
        root.addWidget(self._table, 1)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        for btn in (
            self._refresh_btn,
            self._probe_btn,
            self._apply_btn,
            self._clear_btn,
        ):
            btn.setEnabled(not busy)

    def _fill(self, status: dict[str, Any]) -> None:
        self._status = status
        probe = status.get("probe") or {}
        self._host.setText(str(status.get("host") or "—"))
        self._platform.setText(str(status.get("platform") or "—"))
        self._effective.setText(str(status.get("effective_path") or "—"))
        source = status.get("source") or "env"
        self._source.setText(
            "App override (saved in database)"
            if source == "override"
            else "Server env (ATTACHMENTS_DIRECTORY)"
        )
        self._env.setText(str(status.get("env_path") or "—"))
        free = probe.get("free_human") or "—"
        total = probe.get("total_human") or "—"
        mount = probe.get("mount_hint") or probe.get("drive") or ""
        space = f"{free} free of {total}"
        if mount:
            space = f"{space} · {mount}"
        self._space.setText(space)
        if probe.get("write_ok"):
            self._writable.setText("Yes")
            self._writable.setStyleSheet("color:#16A34A; font:13px 'Segoe UI';")
        else:
            err = probe.get("error") or "No"
            self._writable.setText(str(err))
            self._writable.setStyleSheet("color:#DC2626; font:13px 'Segoe UI';")
        if status.get("s3_configured"):
            bucket = status.get("s3_bucket") or ""
            prefix = status.get("receipt_s3_prefix") or "receipts"
            self._s3.setText(f"Configured · {bucket}/{prefix}/…")
        else:
            self._s3.setText("Not configured (local disk only)")
        self._pending.setText(str(status.get("pending_s3_rows") or 0))
        self._path.setText(str(status.get("effective_path") or ""))

        candidates = list(status.get("candidates") or [])
        self._candidate.blockSignals(True)
        self._candidate.clear()
        for item in candidates:
            label = item.get("label") or item.get("path")
            free_h = item.get("free_human") or "?"
            self._candidate.addItem(f"{label}  ({free_h} free)", item)
        self._candidate.blockSignals(False)

        self._table.setRowCount(0)
        for item in candidates:
            row = self._table.rowCount()
            self._table.insertRow(row)
            values = [
                str(item.get("label") or ""),
                str(item.get("path") or ""),
                str(item.get("free_human") or "—"),
                str(item.get("total_human") or "—"),
                "Yes" if item.get("writable") else "No",
            ]
            for col, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setData(Qt.UserRole, item)
                self._table.setItem(row, col, cell)
        self._table.resizeColumnsToContents()

    async def refresh(self) -> None:
        if self._busy:
            return
        self._set_busy(True)
        self._status_lbl.setText("")
        try:
            status = await get_storage_status()
            self._fill(status)
        except Exception as exc:
            QMessageBox.critical(self, "Receipt disk", f"Failed to load status:\n{exc}")
        finally:
            self._set_busy(False)

    def _on_candidate_picked(self, index: int) -> None:
        if index < 0:
            return
        item = self._candidate.itemData(index)
        if isinstance(item, dict):
            suggested = item.get("suggested_receipts_path") or item.get("path")
            if suggested:
                self._path.setText(str(suggested))

    def _use_suggested(self) -> None:
        self._on_candidate_picked(self._candidate.currentIndex())

    def _on_row_clicked(self, row: int, _col: int) -> None:
        item = self._table.item(row, 0)
        if item is None:
            return
        data = item.data(Qt.UserRole)
        if isinstance(data, dict):
            suggested = data.get("suggested_receipts_path") or data.get("path")
            if suggested:
                self._path.setText(str(suggested))
            index = self._candidate.findData(data)
            if index < 0:
                # Match by path string if object identity differs.
                path = str(data.get("path") or "")
                for i in range(self._candidate.count()):
                    payload = self._candidate.itemData(i)
                    if isinstance(payload, dict) and payload.get("path") == path:
                        index = i
                        break
            if index >= 0:
                self._candidate.setCurrentIndex(index)

    def _on_probe(self) -> None:
        asyncio.ensure_future(self._do_probe())

    async def _do_probe(self) -> None:
        path = self._path.text().strip()
        if not path:
            QMessageBox.information(self, "Receipt disk", "Enter a path first.")
            return
        self._set_busy(True)
        try:
            probe = await probe_storage(path, create=self._create.isChecked())
            if probe.get("write_ok"):
                free = probe.get("free_human") or "?"
                self._status_lbl.setText(f"Write OK · {free} free")
                self._status_lbl.setStyleSheet("color:#16A34A; font-size:12px;")
            else:
                self._status_lbl.setText(str(probe.get("error") or "Not writable"))
                self._status_lbl.setStyleSheet("color:#DC2626; font-size:12px;")
        except Exception as exc:
            QMessageBox.warning(self, "Test write failed", str(exc))
        finally:
            self._set_busy(False)

    def _on_apply(self) -> None:
        asyncio.ensure_future(self._do_apply())

    async def _do_apply(self) -> None:
        path = self._path.text().strip()
        if not path:
            QMessageBox.information(self, "Receipt disk", "Enter a path first.")
            return
        confirm = QMessageBox.question(
            self,
            "Apply receipt path?",
            (
                f"Store new receipts on the API host at:\n\n{path}\n\n"
                "Existing files stay in the previous folder until moved manually."
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        self._set_busy(True)
        try:
            status = await set_storage_path(path, create=self._create.isChecked())
            self._fill(status)
            self._status_lbl.setText("Path applied.")
            self._status_lbl.setStyleSheet("color:#16A34A; font-size:12px;")
        except Exception as exc:
            QMessageBox.critical(self, "Apply failed", str(exc))
        finally:
            self._set_busy(False)

    def _on_clear(self) -> None:
        asyncio.ensure_future(self._do_clear())

    async def _do_clear(self) -> None:
        confirm = QMessageBox.question(
            self,
            "Use env default?",
            "Clear the database override and use ATTACHMENTS_DIRECTORY from the server env?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        self._set_busy(True)
        try:
            status = await clear_storage_override()
            self._fill(status)
            self._status_lbl.setText("Using env default.")
            self._status_lbl.setStyleSheet("color:#16A34A; font-size:12px;")
        except Exception as exc:
            QMessageBox.critical(self, "Clear failed", str(exc))
        finally:
            self._set_busy(False)
