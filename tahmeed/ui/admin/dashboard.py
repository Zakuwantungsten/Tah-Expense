"""Admin panel shell: light sidebar + stacked pages."""

from __future__ import annotations

import asyncio

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from tahmeed.models.user import User
from tahmeed.ui.admin.audit_tab import AuditTab
from tahmeed.ui.admin.backup_tab import BackupTab
from tahmeed.ui.admin.security_tab import SecurityTab
from tahmeed.ui.admin.settings_tab import SettingsTab
from tahmeed.ui.admin.sidebar import AdminSidebar
from tahmeed.ui.widgets.table_contrast import apply_light_tables_under
from tahmeed.ui.admin.users_tab import UsersTab
from tahmeed.ui.widgets.circuit_geometry import wrap_circuit_page
from tahmeed.ui.widgets.receipt_storage_panel import ReceiptStoragePanel

_ADMIN_BG = "#F8FAFC"


class AdminDashboard(QWidget):
    logout_requested = Signal()

    def __init__(self, user: User, parent=None):
        super().__init__(parent)
        self.setStyleSheet("AdminDashboard{background:#F8FAFC;}")

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._sidebar = AdminSidebar(user.full_name or user.username)
        self._sidebar.navigate.connect(self._on_navigate)
        self._sidebar.logout_requested.connect(self.logout_requested)
        root.addWidget(self._sidebar)

        right = QWidget()
        right.setStyleSheet("background:#F8FAFC;")
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(0)

        header = QWidget()
        header.setFixedHeight(52)
        header.setStyleSheet(
            "background:#FFFFFF;border-bottom:1px solid #E5E7EB;"
        )
        hl = QHBoxLayout(header)
        hl.setContentsMargins(24, 0, 24, 0)
        self._page_title = QLabel("Users")
        self._page_title.setStyleSheet(
            "color:#111827;font:700 16px 'Segoe UI';background:transparent;"
        )
        hl.addWidget(self._page_title)
        hl.addStretch()
        self._page_sub = QLabel(f"{user.full_name} · Admin")
        self._page_sub.setStyleSheet(
            "color:#6B7280;font:12px 'Segoe UI';background:transparent;"
        )
        self._page_sub.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        hl.addWidget(self._page_sub)
        right_lay.addWidget(header)

        self._stack = QStackedWidget()
        from tahmeed.ui.widgets.combo_styles import with_combo_popup
        # Do not force every descendant QWidget opaque — that would hide the
        # circuit page canvas. Style concrete controls instead.
        self._stack.setStyleSheet(with_combo_popup(
            "QStackedWidget{background:#F8FAFC;}"
            "QLabel{color:#111827;}"
            "QLineEdit,QComboBox,QSpinBox,QTextEdit,QPlainTextEdit{"
            "background:#FFFFFF;color:#111827;border:1px solid #D1D5DB;"
            "border-radius:6px;padding:4px 8px;}"
            "QPushButton{background:#FFFFFF;color:#111827;border:1px solid #D1D5DB;"
            "border-radius:6px;padding:6px 12px;}"
            "QPushButton:hover{background:#F3F4F6;}"
            "QTableWidget{background:#FFFFFF;alternate-background-color:#F9FAFB;"
            "color:#111827;gridline-color:#E5E7EB;border:1px solid #E5E7EB;"
            "selection-background-color:#DBEAFE;selection-color:#111827;}"
            "QTableWidget::item{color:#111827;}"
            "QTableWidget::item:selected{background:#DBEAFE;color:#111827;}"
            "QHeaderView::section{background:#F3F4F6;color:#374151;"
            "border:none;border-bottom:1px solid #E5E7EB;padding:6px;}"
            "QCheckBox{color:#111827;}"
        ))

        self._users = UsersTab()
        self._settings = SettingsTab()
        self._receipts = ReceiptStoragePanel()
        self._security = SecurityTab()
        self._audit = AuditTab()
        self._backups = BackupTab()
        self._backups.logout_requested.connect(self.logout_requested)

        # Keep direct refs for signal wiring; stack holds circuit hosts.
        self._pages: dict[str, tuple[QWidget, str]] = {
            "users": (wrap_circuit_page(self._users, seed="users", bg=_ADMIN_BG), "Users"),
            "settings": (
                wrap_circuit_page(self._settings, seed="settings", bg=_ADMIN_BG),
                "Settings",
            ),
            "receipts": (
                wrap_circuit_page(self._receipts, seed="receipts", bg=_ADMIN_BG),
                "Receipt disk",
            ),
            "security": (
                wrap_circuit_page(self._security, seed="security", bg=_ADMIN_BG),
                "Security",
            ),
            "audit": (
                wrap_circuit_page(self._audit, seed="audit", bg=_ADMIN_BG),
                "Audit Log",
            ),
            "backups": (
                wrap_circuit_page(self._backups, seed="backups", bg=_ADMIN_BG),
                "Backups",
            ),
        }
        for key, (widget, _) in self._pages.items():
            self._stack.addWidget(widget)

        # Windows dark mode leaves palette Text white while admin sheets force
        # light row backgrounds — pin every table under the stack.
        apply_light_tables_under(self._stack)

        right_lay.addWidget(self._stack, 1)
        root.addWidget(right, 1)

        self._users.view_sessions_requested.connect(self._goto_user_sessions)
        self._users.view_audit_requested.connect(self._goto_user_audit)

        self._sidebar.set_active("users")
        self._on_navigate("users")

        self._storage_health_timer = QTimer(self)
        self._storage_health_timer.setInterval(30_000)
        self._storage_health_timer.timeout.connect(
            lambda: asyncio.ensure_future(self._refresh_storage_health())
        )
        self._storage_health_timer.start()
        asyncio.ensure_future(self._refresh_storage_health())

    async def _refresh_storage_health(self) -> None:
        from tahmeed.services.receipt_storage_service import get_storage_health

        try:
            health = await get_storage_health()
        except Exception:
            return
        down = 0 if health.get("healthy", True) else 1
        if hasattr(self._sidebar, "set_receipt_storage_badge"):
            self._sidebar.set_receipt_storage_badge(down)

    def _on_navigate(self, key: str) -> None:
        page = self._pages.get(key)
        if page is None:
            return
        widget, title = page
        self._stack.setCurrentWidget(widget)
        self._page_title.setText(title)

    def _goto_user_sessions(self, username: str) -> None:
        self._sidebar.set_active("security")
        self._on_navigate("security")
        self._security.focus_user(username)

    def _goto_user_audit(self, username: str) -> None:
        self._sidebar.set_active("audit")
        self._on_navigate("audit")
        self._audit.focus_actor(username)
