"""Read-only backend backup job history."""

from __future__ import annotations

import asyncio

import qtawesome as qta
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from tahmeed.services.backup_service import BackupJob, list_backup_jobs
from tahmeed.ui.widgets.loading_overlay import LoadingOverlay


class BackupWidget(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
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
        )
        description.setWordWrap(True)
        description.setStyleSheet("color:#6B7280; font:13px 'Segoe UI';")
        root.addWidget(description)

        self._refresh_button = QPushButton("Refresh")
        self._refresh_button.setIcon(qta.icon("mdi.refresh", color="#374151"))
        self._refresh_button.setFixedWidth(110)
        self._refresh_button.clicked.connect(self.refresh)
        root.addWidget(self._refresh_button, 0, Qt.AlignLeft)

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
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        root.addWidget(self._table, 1)

        self._empty = QLabel("Open this page or click Refresh to load backup jobs.")
        self._empty.setAlignment(Qt.AlignCenter)
        self._empty.setStyleSheet("color:#9CA3AF; font:13px 'Segoe UI';")
        root.addWidget(self._empty)
        self._overlay = LoadingOverlay(self)

    def refresh(self) -> None:
        if not self._busy:
            asyncio.ensure_future(self._load())

    async def _load(self) -> None:
        self._busy = True
        self._refresh_button.setEnabled(False)
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

    @staticmethod
    def _format_size(size: int) -> str:
        value = float(size)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if value < 1024 or unit == "TB":
                return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
            value /= 1024
        return f"{size} B"
