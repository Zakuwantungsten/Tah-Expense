"""Admin Audit Log viewer for operation_events."""

from __future__ import annotations

import asyncio
import csv
import json
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
import qtawesome as qta

from tahmeed.services.audit_log_service import AuditEvent, list_audit_events
from tahmeed.ui.widgets.loading_overlay import LoadingOverlay

# Preset chips: label → action substring passed to the API regex filter.
_FILTER_CHIPS: tuple[tuple[str, str], ...] = (
    ("All", ""),
    ("Auth", "auth"),
    ("Users", "user"),
    ("Transactions", "txn"),
    ("Settings", "settings"),
)

_CHIP_STYLE = """
QPushButton {{
    background: {bg};
    color: {fg};
    border: 1px solid {border};
    border-radius: 12px;
    padding: 3px 12px;
    font: 12px 'Segoe UI';
}}
QPushButton:hover {{
    background: {hover};
}}
"""


def _chip_ss(active: bool) -> str:
    if active:
        return _CHIP_STYLE.format(
            bg="#1E293B", fg="#F8FAFC", border="#1E293B", hover="#334155"
        )
    return _CHIP_STYLE.format(
        bg="#F8FAFC", fg="#374151", border="#D1D5DB", hover="#F3F4F6"
    )


class AuditTab(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._events: list[AuditEvent] = []
        self._busy = False
        self._chip_action = ""
        self._build()
        asyncio.ensure_future(self._load())

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)

        title = QLabel("Audit Log")
        title.setStyleSheet("color:#1F2937; font:700 22px 'Segoe UI';")
        root.addWidget(title)

        description = QLabel(
            "Append-only record of sign-ins, admin changes, and business operations. "
            "Newest events appear first."
        )
        description.setWordWrap(True)
        description.setStyleSheet("color:#6B7280; font:13px 'Segoe UI';")
        root.addWidget(description)

        chips = QHBoxLayout()
        chips.setSpacing(6)
        self._chip_group = QButtonGroup(self)
        self._chip_group.setExclusive(True)
        self._chip_buttons: dict[str, QPushButton] = {}
        for label, action in _FILTER_CHIPS:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(26)
            btn.setChecked(action == "")
            btn.setStyleSheet(_chip_ss(action == ""))
            btn.clicked.connect(lambda _=False, a=action: self._on_chip(a))
            self._chip_group.addButton(btn)
            self._chip_buttons[action] = btn
            chips.addWidget(btn)
        chips.addStretch()
        root.addLayout(chips)

        filters = QHBoxLayout()
        filters.setSpacing(8)
        self._action = QLineEdit()
        self._action.setPlaceholderText("Action contains… (e.g. auth.login)")
        self._action.setFixedWidth(220)
        self._action.returnPressed.connect(self.refresh)
        filters.addWidget(self._action)

        self._actor = QLineEdit()
        self._actor.setPlaceholderText("Actor username…")
        self._actor.setFixedWidth(160)
        self._actor.returnPressed.connect(self.refresh)
        filters.addWidget(self._actor)

        self._outcome = QComboBox()
        self._outcome.addItem("Any outcome", "")
        self._outcome.addItem("ok", "ok")
        self._outcome.addItem("fail", "fail")
        self._outcome.setFixedWidth(130)
        filters.addWidget(self._outcome)

        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.setIcon(qta.icon("mdi.refresh", color="#374151"))
        self._refresh_btn.setFixedWidth(110)
        self._refresh_btn.clicked.connect(self.refresh)
        filters.addWidget(self._refresh_btn)

        self._export_btn = QPushButton("Export CSV")
        self._export_btn.setIcon(qta.icon("mdi.file-delimited-outline", color="#374151"))
        self._export_btn.setFixedWidth(120)
        self._export_btn.clicked.connect(self._export_csv)
        filters.addWidget(self._export_btn)
        filters.addStretch()
        root.addLayout(filters)

        self._table = QTableWidget()
        self._table.setColumnCount(7)
        self._table.setHorizontalHeaderLabels(
            ["When", "Action", "Actor", "Role", "Outcome", "Entity", "Details"]
        )
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.Stretch)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        root.addWidget(self._table, 1)

        self._empty = QLabel("No audit events match the current filters.")
        self._empty.setAlignment(Qt.AlignCenter)
        self._empty.setStyleSheet("color:#9CA3AF; font:13px 'Segoe UI';")
        root.addWidget(self._empty)
        self._overlay = LoadingOverlay(self)

    def _on_chip(self, action: str) -> None:
        self._chip_action = action
        for key, btn in self._chip_buttons.items():
            active = key == action
            btn.setChecked(active)
            btn.setStyleSheet(_chip_ss(active))
        # Chip drives the action filter; clear free-text so they don't fight.
        self._action.blockSignals(True)
        self._action.setText(action)
        self._action.blockSignals(False)
        self.refresh()

    def focus_actor(self, username: str) -> None:
        """Deep-link from Users: filter audit rows for this actor."""
        self._chip_action = ""
        for key, btn in self._chip_buttons.items():
            active = key == ""
            btn.setChecked(active)
            btn.setStyleSheet(_chip_ss(active))
        self._action.setText("")
        self._actor.setText(username.strip())
        self._outcome.setCurrentIndex(0)
        self.refresh()

    def refresh(self) -> None:
        if not self._busy:
            asyncio.ensure_future(self._load())

    def _action_filter(self) -> str | None:
        typed = self._action.text().strip()
        if typed:
            return typed
        return self._chip_action or None

    async def _load(self) -> None:
        self._busy = True
        self._refresh_btn.setEnabled(False)
        self._export_btn.setEnabled(False)
        self._overlay.show_loading("Loading audit log…")
        try:
            self._events = await list_audit_events(
                limit=200,
                action=self._action_filter(),
                actor=self._actor.text().strip() or None,
                outcome=self._outcome.currentData() or None,
            )
            self._render()
        except Exception as exc:
            QMessageBox.critical(self, "Audit Log Error", str(exc))
        finally:
            self._busy = False
            self._refresh_btn.setEnabled(True)
            self._export_btn.setEnabled(True)
            self._overlay.hide_loading()

    def _render(self) -> None:
        self._table.setRowCount(len(self._events))
        for row, event in enumerate(self._events):
            when = (
                event.ts.astimezone().strftime("%d %b %Y  %H:%M:%S")
                if event.ts
                else ""
            )
            entity = event.entity_type
            if event.entity_ids:
                entity = f"{entity} ({', '.join(event.entity_ids[:3])})"
            details = event.error or ""
            if event.details:
                try:
                    details = (details + " " if details else "") + json.dumps(
                        event.details, default=str
                    )
                except TypeError:
                    details = str(event.details)
            values = [
                when,
                event.action,
                event.actor_username,
                event.actor_role,
                event.outcome,
                entity.strip(),
                details[:500],
            ]
            for col, value in enumerate(values):
                self._table.setItem(row, col, QTableWidgetItem(value))
        self._empty.setVisible(not self._events)
        self._table.setVisible(bool(self._events))

    def _export_csv(self) -> None:
        if not self._events:
            QMessageBox.information(
                self,
                "Export CSV",
                "No events to export. Adjust filters and refresh first.",
            )
            return
        stamp = datetime.now().strftime("%Y%m%d_%H%M")
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Audit Log",
            f"tahmeed_audit_{stamp}.csv",
            "CSV Files (*.csv)",
        )
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        try:
            self._write_csv(Path(path), self._events)
        except OSError as exc:
            QMessageBox.critical(self, "Export Error", str(exc))
            return
        QMessageBox.information(
            self,
            "Export Complete",
            f"Exported {len(self._events):,} event(s) to:\n{path}",
        )

    @staticmethod
    def _write_csv(path: Path, events: list[AuditEvent]) -> None:
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(
                [
                    "ts",
                    "action",
                    "actor_username",
                    "actor_role",
                    "outcome",
                    "entity_type",
                    "entity_ids",
                    "error",
                    "client",
                    "details",
                ]
            )
            for event in events:
                ts = event.ts.isoformat() if event.ts else ""
                details = ""
                if event.details:
                    try:
                        details = json.dumps(event.details, default=str)
                    except TypeError:
                        details = str(event.details)
                writer.writerow(
                    [
                        ts,
                        event.action,
                        event.actor_username,
                        event.actor_role,
                        event.outcome,
                        event.entity_type,
                        "|".join(event.entity_ids),
                        event.error,
                        event.client,
                        details,
                    ]
                )
