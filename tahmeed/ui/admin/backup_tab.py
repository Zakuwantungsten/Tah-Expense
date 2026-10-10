"""Admin-only backups: schedule controls + job history / restore."""

from __future__ import annotations

import asyncio

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from tahmeed.services.settings_service import get_all_settings, set_setting
from tahmeed.ui.accountant.backup import BackupWidget


class BackupTab(QWidget):
    logout_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        schedule = QWidget()
        schedule_layout = QVBoxLayout(schedule)
        schedule_layout.setContentsMargins(24, 20, 24, 8)
        schedule_layout.setSpacing(10)

        heading = QLabel("Backup schedule")
        heading.setStyleSheet("color:#1F2937; font:700 16px 'Segoe UI';")
        schedule_layout.addWidget(heading)

        hint = QLabel(
            "Choose how often the server creates dumps. Retention keeps the newest "
            "N uploaded backups (default 30). Changes apply on the next scheduler poll."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#6B7280; font:12px 'Segoe UI';")
        schedule_layout.addWidget(hint)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(10)

        self._mode = QComboBox()
        self._mode.addItem("Hourly", "hourly")
        self._mode.addItem("Daily (at UTC time)", "daily")
        self._mode.addItem("Disabled", "disabled")
        form.addRow("Schedule mode:", self._mode)

        self._daily_time = QLineEdit()
        self._daily_time.setPlaceholderText("HH:MM UTC, e.g. 00:00")
        self._daily_time.setFixedWidth(120)
        form.addRow("Daily time (UTC):", self._daily_time)

        self._keep = QSpinBox()
        self._keep.setRange(1, 365)
        self._keep.setFixedWidth(100)
        form.addRow("Keep newest:", self._keep)

        schedule_layout.addLayout(form)

        save_row = QHBoxLayout()
        self._save_btn = QPushButton("Save schedule")
        self._save_btn.setFixedWidth(130)
        self._save_btn.clicked.connect(self._on_save_schedule)
        self._status = QLabel("")
        self._status.setStyleSheet("color:#27ae60; font-size:12px;")
        save_row.addWidget(self._save_btn)
        save_row.addWidget(self._status)
        save_row.addStretch()
        schedule_layout.addLayout(save_row)

        layout.addWidget(schedule)

        self._jobs = BackupWidget(allow_restore=True)
        self._jobs.logout_requested.connect(self.logout_requested)
        layout.addWidget(self._jobs, 1)

        asyncio.ensure_future(self._load_schedule())

    async def _load_schedule(self) -> None:
        try:
            settings = await get_all_settings()
            mode = str(settings.get("backup_schedule_mode") or "hourly")
            index = self._mode.findData(mode)
            if index >= 0:
                self._mode.setCurrentIndex(index)
            self._daily_time.setText(str(settings.get("backup_daily_time_utc") or "00:00"))
            self._keep.setValue(int(settings.get("backup_keep_count") or 30))
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to load backup schedule:\n{exc}")

    def _on_save_schedule(self) -> None:
        asyncio.ensure_future(self._do_save_schedule())

    async def _do_save_schedule(self) -> None:
        try:
            await set_setting("backup_schedule_mode", self._mode.currentData())
            await set_setting("backup_daily_time_utc", self._daily_time.text().strip() or "00:00")
            await set_setting("backup_keep_count", self._keep.value())
            self._status.setText("Schedule saved.")
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to save backup schedule:\n{exc}")
