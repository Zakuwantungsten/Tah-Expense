"""Backend backup job history, with admin/accountant restore."""

from __future__ import annotations

import asyncio

import qtawesome as qta
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from tahmeed.services.api_client import api_client
from tahmeed.services.backup_service import BackupJob, list_backup_jobs, restore_backup
from tahmeed.ui.widgets.loading_overlay import LoadingOverlay


class BackupWidget(QWidget):
    """Emits logout_requested after a successful restore (sessions are wiped)."""

    logout_requested = Signal()

    def __init__(self, parent=None, *, allow_restore: bool = False) -> None:
        super().__init__(parent)
        self._allow_restore = allow_restore
        self._jobs: list[BackupJob] = []
        self._busy = False
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)

        title = QLabel("Database Backup Status")
        title.setStyleSheet("color:#1F2937; font:700 22px 'Segoe UI';")
        root.addWidget(title)

        description = QLabel(
            "Backups are created and stored by the server. This page shows "
            "their current status and recent history."
            + (
                " You can restore an Uploaded backup into the live database."
                if self._allow_restore
                else ""
            )
        )
        description.setWordWrap(True)
        description.setStyleSheet("color:#6B7280; font:13px 'Segoe UI';")
        root.addWidget(description)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self._refresh_button = QPushButton("Refresh")
        self._refresh_button.setIcon(qta.icon("mdi.refresh", color="#374151"))
        self._refresh_button.setFixedWidth(110)
        self._refresh_button.clicked.connect(self.refresh)
        actions.addWidget(self._refresh_button)

        self._restore_button = QPushButton("Restore selected…")
        self._restore_button.setIcon(qta.icon("mdi.database-refresh", color="#FFFFFF"))
        self._restore_button.setEnabled(False)
        self._restore_button.setVisible(self._allow_restore)
        self._restore_button.setStyleSheet(
            "QPushButton{background:#B91C1C;color:#FFFFFF;border:none;"
            "border-radius:6px;padding:6px 14px;font:600 12px 'Segoe UI';}"
            "QPushButton:disabled{background:#FCA5A5;}"
            "QPushButton:hover:!disabled{background:#991B1B;}"
        )
        self._restore_button.clicked.connect(self._confirm_restore)
        actions.addWidget(self._restore_button)
        actions.addStretch()
        root.addLayout(actions)

        self._table = QTableWidget()
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels(
            ["Backup", "Created (local time)", "Status", "Size", "Details"]
        )
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.itemSelectionChanged.connect(self._update_restore_enabled)
        root.addWidget(self._table, 1)

        self._empty = QLabel("Open this page or click Refresh to load backup jobs.")
        self._empty.setAlignment(Qt.AlignCenter)
        self._empty.setStyleSheet("color:#9CA3AF; font:13px 'Segoe UI';")
        root.addWidget(self._empty)
        self._overlay = LoadingOverlay(self)

    def refresh(self) -> None:
        if not self._busy:
            asyncio.ensure_future(self._load())

    def _selected_job(self) -> BackupJob | None:
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            return None
        index = rows[0].row()
        if index < 0 or index >= len(self._jobs):
            return None
        return self._jobs[index]

    def _update_restore_enabled(self) -> None:
        job = self._selected_job()
        self._restore_button.setEnabled(
            self._allow_restore
            and not self._busy
            and job is not None
            and job.status == "uploaded"
        )

    def _confirm_restore(self) -> None:
        job = self._selected_job()
        if job is None or job.status != "uploaded":
            return
        warning = QMessageBox.warning(
            self,
            "Restore live database?",
            (
                "This replaces the live company database with:\n\n"
                f"{job.filename}\n\n"
                "All data written after this backup will be lost.\n"
                "Do not continue unless you intend to recover from disaster."
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if warning != QMessageBox.Yes:
            return
        typed, ok = QInputDialog.getText(
            self,
            "Confirm restore",
            "Type the exact backup filename to continue:",
            text="",
        )
        if not ok or typed.strip() != job.filename:
            QMessageBox.information(
                self,
                "Restore cancelled",
                "Filename did not match. No changes were made.",
            )
            return
        asyncio.ensure_future(self._run_restore(job.filename))

    async def _load(self) -> None:
        self._busy = True
        self._refresh_button.setEnabled(False)
        self._update_restore_enabled()
        self._overlay.show_loading("Loading backup status…")
        try:
            self._jobs = await list_backup_jobs()
            self._render()
        except Exception as exc:
            QMessageBox.critical(self, "Backup Status Error", str(exc))
        finally:
            self._busy = False
            self._refresh_button.setEnabled(True)
            self._overlay.hide_loading()
            self._update_restore_enabled()

    async def _run_restore(self, filename: str) -> None:
        self._busy = True
        self._refresh_button.setEnabled(False)
        self._update_restore_enabled()
        self._overlay.show_loading("Restoring database from backup…")
        try:
            # A 200 from restore means mongorestore already ran with --drop.
            # auth_sessions are gone; do not call other APIs or treat follow-up
            # auth errors as a failed restore.
            result = await restore_backup(filename)
        except Exception as exc:
            self._busy = False
            self._refresh_button.setEnabled(True)
            self._overlay.hide_loading()
            self._update_restore_enabled()
            QMessageBox.critical(self, "Restore failed", str(exc))
            return

        self._busy = False
        self._refresh_button.setEnabled(True)
        self._overlay.hide_loading()
        self._update_restore_enabled()

        completed = result.get("completed_at", "")
        # Drop local tokens immediately so background polls stop and logout
        # skips unsaved-register prompts (pre-restore drafts are obsolete).
        api_client.clear_tokens()
        QMessageBox.information(
            self,
            "Restore completed",
            (
                f"Restored {filename}.\n"
                f"Finished at {completed or 'now'}.\n\n"
                "You will be signed out. Sign back in to verify restored data."
            ),
        )
        self.logout_requested.emit()

    def _render(self) -> None:
        self._table.setRowCount(len(self._jobs))
        for row, job in enumerate(self._jobs):
            self._table.setItem(row, 0, QTableWidgetItem(job.filename))
            created = job.created_at.astimezone().strftime("%d %b %Y  %H:%M:%S")
            self._table.setItem(row, 1, QTableWidgetItem(created))
            self._table.setItem(row, 2, QTableWidgetItem(job.status.replace("_", " ").title()))
            self._table.setItem(row, 3, QTableWidgetItem(self._format_size(job.size)))
            self._table.setItem(row, 4, QTableWidgetItem(job.error))
        self._empty.setVisible(not self._jobs)
        self._table.setVisible(bool(self._jobs))
        self._update_restore_enabled()

    @staticmethod
    def _format_size(size: int) -> str:
        value = float(size)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if value < 1024 or unit == "TB":
                return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
            value /= 1024
        return f"{size} B"
