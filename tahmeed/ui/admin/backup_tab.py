"""Admin-only backups: schedule controls + manual run + job history / restore."""

from __future__ import annotations

import asyncio

import qtawesome as qta
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from tahmeed.services.backup_service import run_backup_now
from tahmeed.services.settings_service import get_all_settings, set_setting
from tahmeed.ui.accountant.backup import BackupWidget
from tahmeed.ui.widgets.loading_overlay import LoadingOverlay


class BackupTab(QWidget):
    logout_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._run_busy = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        heading = QLabel("Backups")
        heading.setStyleSheet("color:#111827; font:700 18px 'Segoe UI';")
        layout.addWidget(heading)

        hint = QLabel(
            "Choose how often the server creates dumps, or run one immediately. "
            "Retention keeps the newest N uploaded backups (default 30). "
            "Schedule changes apply on the next scheduler poll."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#6B7280; font:12px 'Segoe UI';")
        layout.addWidget(hint)

        fields = QHBoxLayout()
        fields.setSpacing(16)

        mode_col = QVBoxLayout()
        mode_col.setSpacing(4)
        mode_lbl = QLabel("Schedule mode")
        mode_lbl.setStyleSheet("color:#6B7280; font:12px 'Segoe UI';")
        mode_col.addWidget(mode_lbl)
        self._mode = QComboBox()
        self._mode.addItem("Hourly", "hourly")
        self._mode.addItem("Daily (at UTC time)", "daily")
        self._mode.addItem("Disabled", "disabled")
        self._mode.setMinimumWidth(180)
        mode_col.addWidget(self._mode)
        fields.addLayout(mode_col)

        time_col = QVBoxLayout()
        time_col.setSpacing(4)
        time_lbl = QLabel("Daily time (UTC)")
        time_lbl.setStyleSheet("color:#6B7280; font:12px 'Segoe UI';")
        time_col.addWidget(time_lbl)
        self._daily_time = QLineEdit()
        self._daily_time.setPlaceholderText("HH:MM UTC, e.g. 00:00")
        self._daily_time.setFixedWidth(120)
        time_col.addWidget(self._daily_time)
        fields.addLayout(time_col)

        keep_col = QVBoxLayout()
        keep_col.setSpacing(4)
        keep_lbl = QLabel("Keep newest")
        keep_lbl.setStyleSheet("color:#6B7280; font:12px 'Segoe UI';")
        keep_col.addWidget(keep_lbl)
        self._keep = QSpinBox()
        self._keep.setRange(1, 365)
        self._keep.setFixedWidth(100)
        keep_col.addWidget(self._keep)
        fields.addLayout(keep_col)

        fields.addStretch()
        layout.addLayout(fields)

        self._jobs = BackupWidget(
            allow_restore=True, show_chrome=False, show_actions=False
        )
        self._jobs.logout_requested.connect(self.logout_requested)

        # Save / Backup now / Refresh / Restore share one toolbar row.
        actions = QHBoxLayout()
        actions.setSpacing(8)

        self._save_btn = QPushButton("Save schedule")
        self._save_btn.setFixedWidth(130)
        self._save_btn.clicked.connect(self._on_save_schedule)
        actions.addWidget(self._save_btn)

        self._run_btn = QPushButton("Backup now")
        self._run_btn.setIcon(qta.icon("mdi.cloud-upload-outline", color="#FFFFFF"))
        self._run_btn.setFixedWidth(130)
        self._run_btn.setStyleSheet(
            "QPushButton{background:#0077C5;color:#FFFFFF;border:none;"
            "border-radius:6px;padding:6px 14px;font:600 12px 'Segoe UI';}"
            "QPushButton:disabled{background:#93C5FD;}"
            "QPushButton:hover:!disabled{background:#005EA3;}"
        )
        self._run_btn.clicked.connect(self._on_run_backup)
        actions.addWidget(self._run_btn)

        actions.addWidget(self._jobs.refresh_button)
        actions.addWidget(self._jobs.restore_button)

        self._status = QLabel("")
        self._status.setStyleSheet("color:#16A34A; font-size:12px;")
        actions.addWidget(self._status)
        actions.addStretch()
        layout.addLayout(actions)

        layout.addWidget(self._jobs, 1)
        self._overlay = LoadingOverlay(self, "Creating backup…")

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

    def _on_run_backup(self) -> None:
        if self._run_busy:
            return
        confirm = QMessageBox.question(
            self,
            "Run backup now?",
            (
                "Create a database backup immediately?\n\n"
                "This dumps the live database on the server (and uploads it when "
                "cloud storage is configured). It can take several minutes."
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        asyncio.ensure_future(self._do_run_backup())

    async def _do_run_backup(self) -> None:
        if self._run_busy:
            return
        self._run_busy = True
        self._run_btn.setEnabled(False)
        self._save_btn.setEnabled(False)
        self._status.setText("")
        self._overlay.show_loading("Creating database backup…")
        try:
            result = await run_backup_now()
            filename = str(result.get("filename") or "backup")
            status = str(result.get("status") or "created").replace("_", " ")
            self._status.setText(f"Backup created: {filename} ({status})")
            self._jobs.refresh()
        except Exception as exc:
            QMessageBox.critical(self, "Backup failed", str(exc))
        finally:
            self._run_busy = False
            self._run_btn.setEnabled(True)
            self._save_btn.setEnabled(True)
            self._overlay.hide_loading()
