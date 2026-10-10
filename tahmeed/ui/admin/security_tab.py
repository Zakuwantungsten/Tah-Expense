"""Admin security settings: allowlists, password policy, lockout, sessions."""

from __future__ import annotations

import asyncio

import qtawesome as qta
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from tahmeed.services.device_id import primary_mac_address
from tahmeed.services.session_service import (
    SessionInfo,
    list_sessions,
    revoke_all_sessions,
    revoke_session,
    revoke_user_sessions,
)
from tahmeed.services.settings_service import get_all_settings, set_setting


def _lines_to_list(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _list_to_lines(values) -> str:
    if not values:
        return ""
    return "\n".join(str(item) for item in values)


def _fmt_dt(value) -> str:
    if not value:
        return "—"
    return value.astimezone().strftime("%d %b %Y  %H:%M")


class SecurityTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._sessions: list[SessionInfo] = []
        self._sessions_busy = False
        self._build_ui()
        asyncio.ensure_future(self._load())
        asyncio.ensure_future(self._load_sessions())

    def _build_ui(self) -> None:
        shell = QVBoxLayout(self)
        shell.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        shell.addWidget(scroll)

        body = QWidget()
        scroll.setWidget(body)
        outer = QVBoxLayout(body)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.setSpacing(16)
        outer.setAlignment(Qt.AlignTop)

        title = QLabel("Security")
        title.setStyleSheet("color:#1F2937; font:700 20px 'Segoe UI';")
        outer.addWidget(title)

        note = QLabel(
            "IP allowlisting uses Cloudflare’s client IP. MAC allowlisting uses the "
            "desktop-reported adapter address (soft control — spoofable). Register "
            "office IPs and MACs before enabling either toggle."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color:#6B7280; font:12px 'Segoe UI';")
        outer.addWidget(note)

        self._this_mac = QLabel(f"This PC’s MAC: {primary_mac_address()}")
        self._this_mac.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._this_mac.setStyleSheet("color:#111827; font:600 12px 'Consolas','Segoe UI';")
        outer.addWidget(self._this_mac)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setHorizontalSpacing(20)
        form.setVerticalSpacing(12)

        self._ip_enabled = QCheckBox("Enforce IP allowlist")
        form.addRow("IP allowlist:", self._ip_enabled)
        self._ips = QTextEdit()
        self._ips.setPlaceholderText("One IP or CIDR per line\n203.0.113.10\n198.51.100.0/24")
        self._ips.setFixedHeight(90)
        form.addRow("Allowed IPs:", self._ips)

        self._mac_enabled = QCheckBox("Enforce MAC allowlist")
        form.addRow("MAC allowlist:", self._mac_enabled)
        self._macs = QTextEdit()
        self._macs.setPlaceholderText("One MAC per line\nAA:BB:CC:DD:EE:FF")
        self._macs.setFixedHeight(90)
        form.addRow("Allowed MACs:", self._macs)

        self._pw_min = QSpinBox()
        self._pw_min.setRange(6, 128)
        form.addRow("Password min length:", self._pw_min)
        self._pw_upper = QCheckBox("Require uppercase")
        self._pw_lower = QCheckBox("Require lowercase")
        self._pw_digit = QCheckBox("Require digit")
        self._pw_special = QCheckBox("Require special character")
        form.addRow("Password rules:", self._pw_upper)
        form.addRow("", self._pw_lower)
        form.addRow("", self._pw_digit)
        form.addRow("", self._pw_special)

        self._lock_attempts = QSpinBox()
        self._lock_attempts.setRange(1, 50)
        form.addRow("Max login failures:", self._lock_attempts)
        self._lock_minutes = QSpinBox()
        self._lock_minutes.setRange(1, 1440)
        self._lock_minutes.setSuffix(" min")
        form.addRow("Lockout duration:", self._lock_minutes)

        self._max_sessions = QSpinBox()
        self._max_sessions.setRange(0, 50)
        self._max_sessions.setSpecialValueText("Unlimited")
        form.addRow("Max sessions / user:", self._max_sessions)

        self._idle_days = QSpinBox()
        self._idle_days.setRange(0, 365)
        self._idle_days.setSpecialValueText("Off")
        self._idle_days.setSuffix(" days")
        form.addRow("Idle session expire:", self._idle_days)

        self._docs = QCheckBox("Enable API /docs (not recommended in production)")
        form.addRow("API docs:", self._docs)

        self._show_sessions = QCheckBox("Show live sessions panel below")
        form.addRow("Sessions panel:", self._show_sessions)

        outer.addLayout(form)

        save_row = QHBoxLayout()
        self._save_btn = QPushButton("Save Security Settings")
        self._save_btn.setFixedWidth(180)
        self._save_btn.clicked.connect(self._on_save)
        self._status = QLabel("")
        self._status.setStyleSheet("color:#27ae60; font-size:12px;")
        save_row.addWidget(self._save_btn)
        save_row.addWidget(self._status)
        save_row.addStretch()
        outer.addLayout(save_row)

        # ── Sessions panel ────────────────────────────────────────────
        self._sessions_box = QWidget()
        sessions_layout = QVBoxLayout(self._sessions_box)
        sessions_layout.setContentsMargins(0, 12, 0, 0)
        sessions_layout.setSpacing(10)

        sess_title = QLabel("Active login sessions")
        sess_title.setStyleSheet("color:#1F2937; font:700 16px 'Segoe UI';")
        sessions_layout.addWidget(sess_title)

        sess_note = QLabel(
            "Force logout kills the refresh token immediately. An access token may "
            "still work until it expires (usually within 15 minutes)."
        )
        sess_note.setWordWrap(True)
        sess_note.setStyleSheet("color:#6B7280; font:12px 'Segoe UI';")
        sessions_layout.addWidget(sess_note)

        toolbar = QHBoxLayout()
        self._user_filter = QLineEdit()
        self._user_filter.setPlaceholderText("Filter by username…")
        self._user_filter.setFixedWidth(180)
        self._user_filter.returnPressed.connect(self._render_sessions)
        toolbar.addWidget(self._user_filter)

        refresh_btn = QPushButton("Refresh")
        refresh_btn.setIcon(qta.icon("mdi.refresh", color="#374151"))
        refresh_btn.setFixedWidth(100)
        refresh_btn.clicked.connect(self.refresh_sessions)
        toolbar.addWidget(refresh_btn)

        revoke_user_btn = QPushButton("Force logout selected user")
        revoke_user_btn.setFixedWidth(190)
        revoke_user_btn.clicked.connect(self._on_revoke_selected_user)
        toolbar.addWidget(revoke_user_btn)

        revoke_all_btn = QPushButton("Force logout everyone else")
        revoke_all_btn.setFixedWidth(190)
        revoke_all_btn.setStyleSheet(
            "QPushButton{background:#B91C1C;color:#fff;border:none;border-radius:4px;padding:4px 8px;}"
        )
        revoke_all_btn.clicked.connect(self._on_revoke_all)
        toolbar.addWidget(revoke_all_btn)
        toolbar.addStretch()
        sessions_layout.addLayout(toolbar)

        self._sess_table = QTableWidget()
        self._sess_table.setColumnCount(8)
        self._sess_table.setHorizontalHeaderLabels(
            ["User", "Role", "IP", "Device MAC", "Client", "Created", "Last used", "Actions"]
        )
        header = self._sess_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(7, QHeaderView.Fixed)
        self._sess_table.setColumnWidth(7, 120)
        self._sess_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._sess_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._sess_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._sess_table.verticalHeader().setVisible(False)
        self._sess_table.setAlternatingRowColors(True)
        self._sess_table.setMinimumHeight(220)
        sessions_layout.addWidget(self._sess_table)

        self._sess_empty = QLabel("No active sessions.")
        self._sess_empty.setAlignment(Qt.AlignCenter)
        self._sess_empty.setStyleSheet("color:#9CA3AF; font:13px 'Segoe UI';")
        sessions_layout.addWidget(self._sess_empty)

        outer.addWidget(self._sessions_box)

    async def _load(self) -> None:
        try:
            settings = await get_all_settings()
            self._ip_enabled.setChecked(bool(settings.get("security_ip_allowlist_enabled")))
            self._ips.setPlainText(_list_to_lines(settings.get("security_allowed_ips") or []))
            self._mac_enabled.setChecked(bool(settings.get("security_mac_allowlist_enabled")))
            self._macs.setPlainText(_list_to_lines(settings.get("security_allowed_macs") or []))
            self._pw_min.setValue(int(settings.get("security_password_min_length") or 10))
            self._pw_upper.setChecked(bool(settings.get("security_password_require_upper", True)))
            self._pw_lower.setChecked(bool(settings.get("security_password_require_lower", True)))
            self._pw_digit.setChecked(bool(settings.get("security_password_require_digit", True)))
            self._pw_special.setChecked(bool(settings.get("security_password_require_special", False)))
            self._lock_attempts.setValue(int(settings.get("security_login_max_attempts") or 5))
            self._lock_minutes.setValue(int(settings.get("security_login_lockout_minutes") or 15))
            self._max_sessions.setValue(int(settings.get("security_max_sessions_per_user") or 0))
            self._idle_days.setValue(int(settings.get("security_idle_session_days") or 0))
            self._docs.setChecked(bool(settings.get("security_docs_enabled")))
            show = bool(settings.get("security_show_sessions_in_security_tab", True))
            self._show_sessions.setChecked(show)
            self._sessions_box.setVisible(show)
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to load security settings:\n{exc}")

    def _on_save(self) -> None:
        asyncio.ensure_future(self._do_save())

    async def _do_save(self) -> None:
        try:
            await set_setting("security_ip_allowlist_enabled", self._ip_enabled.isChecked())
            await set_setting("security_allowed_ips", _lines_to_list(self._ips.toPlainText()))
            await set_setting("security_mac_allowlist_enabled", self._mac_enabled.isChecked())
            await set_setting("security_allowed_macs", _lines_to_list(self._macs.toPlainText()))
            await set_setting("security_password_min_length", self._pw_min.value())
            await set_setting("security_password_require_upper", self._pw_upper.isChecked())
            await set_setting("security_password_require_lower", self._pw_lower.isChecked())
            await set_setting("security_password_require_digit", self._pw_digit.isChecked())
            await set_setting("security_password_require_special", self._pw_special.isChecked())
            await set_setting("security_login_max_attempts", self._lock_attempts.value())
            await set_setting("security_login_lockout_minutes", self._lock_minutes.value())
            await set_setting("security_max_sessions_per_user", self._max_sessions.value())
            await set_setting("security_idle_session_days", self._idle_days.value())
            await set_setting("security_docs_enabled", self._docs.isChecked())
            await set_setting(
                "security_show_sessions_in_security_tab", self._show_sessions.isChecked()
            )
            self._sessions_box.setVisible(self._show_sessions.isChecked())
            self._status.setText("Saved.")
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to save security settings:\n{exc}")

    def focus_user(self, username: str) -> None:
        """Deep-link from Users: show sessions panel filtered to this username."""
        self._show_sessions.setChecked(True)
        self._sessions_box.setVisible(True)
        self._user_filter.setText(username.strip())
        self.refresh_sessions()

    def refresh_sessions(self) -> None:
        if not self._sessions_busy:
            asyncio.ensure_future(self._load_sessions())

    async def _load_sessions(self) -> None:
        self._sessions_busy = True
        try:
            self._sessions = await list_sessions(active_only=True, limit=200)
            self._render_sessions()
        except Exception as exc:
            QMessageBox.critical(self, "Sessions Error", str(exc))
        finally:
            self._sessions_busy = False

    def _filtered_sessions(self) -> list[SessionInfo]:
        needle = self._user_filter.text().strip().lower()
        if not needle:
            return list(self._sessions)
        return [
            s
            for s in self._sessions
            if needle in s.username.lower() or needle in s.full_name.lower()
        ]

    def _render_sessions(self) -> None:
        rows = self._filtered_sessions()
        self._sess_table.setRowCount(len(rows))
        for i, session in enumerate(rows):
            label = session.full_name or session.username
            if session.is_current:
                label = f"{label} (you)"
            ua = session.user_agent
            if len(ua) > 48:
                ua = ua[:45] + "…"
            values = [
                label,
                session.role,
                session.ip,
                session.device_mac or "—",
                ua or "—",
                _fmt_dt(session.created_at),
                _fmt_dt(session.last_used_at),
            ]
            for col, value in enumerate(values):
                self._sess_table.setItem(i, col, QTableWidgetItem(value))
            self._sess_table.setCellWidget(i, 7, self._session_actions(session))
        self._sess_empty.setVisible(not rows)
        self._sess_table.setVisible(bool(rows))

    def _session_actions(self, session: SessionInfo) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(2, 2, 2, 2)
        btn = QPushButton("Force logout")
        btn.setFixedWidth(100)
        btn.setEnabled(not session.is_current)
        btn.clicked.connect(lambda _, s=session: self._on_revoke_one(s))
        lay.addWidget(btn)
        return w

    def _on_revoke_one(self, session: SessionInfo) -> None:
        if (
            QMessageBox.question(
                self,
                "Force logout?",
                f"End session for {session.username or session.full_name}?\nIP {session.ip}",
            )
            != QMessageBox.Yes
        ):
            return
        asyncio.ensure_future(self._do_revoke_one(session.id))

    async def _do_revoke_one(self, session_id: str) -> None:
        try:
            await revoke_session(session_id)
            await self._load_sessions()
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))

    def _selected_session(self) -> SessionInfo | None:
        rows = self._sess_table.selectionModel().selectedRows()
        if not rows:
            return None
        filtered = self._filtered_sessions()
        index = rows[0].row()
        if index < 0 or index >= len(filtered):
            return None
        return filtered[index]

    def _on_revoke_selected_user(self) -> None:
        session = self._selected_session()
        if session is None or not session.user_id:
            QMessageBox.information(self, "Select a row", "Select a session first.")
            return
        if (
            QMessageBox.question(
                self,
                "Force logout user?",
                f"End all sessions for {session.username}? Your own session is kept if it is theirs.",
            )
            != QMessageBox.Yes
        ):
            return
        asyncio.ensure_future(self._do_revoke_user(session.user_id))

    async def _do_revoke_user(self, user_id: str) -> None:
        try:
            await revoke_user_sessions(user_id)
            await self._load_sessions()
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))

    def _on_revoke_all(self) -> None:
        if (
            QMessageBox.warning(
                self,
                "Force logout everyone else?",
                "This ends every active session except yours. Continue?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            != QMessageBox.Yes
        ):
            return
        asyncio.ensure_future(self._do_revoke_all())

    async def _do_revoke_all(self) -> None:
        try:
            await revoke_all_sessions()
            await self._load_sessions()
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))
