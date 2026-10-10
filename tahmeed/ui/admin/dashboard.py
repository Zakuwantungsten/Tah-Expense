from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTabWidget, QLabel, QToolButton,
)
from PySide6.QtCore import Qt, Signal
import qtawesome as qta

from tahmeed.models.user import User
from tahmeed.ui.admin.audit_tab import AuditTab
from tahmeed.ui.admin.backup_tab import BackupTab
from tahmeed.ui.admin.users_tab import UsersTab
from tahmeed.ui.admin.categories_tab import CategoriesTab
from tahmeed.ui.admin.rules_tab import RulesTab
from tahmeed.ui.admin.settings_tab import SettingsTab
from tahmeed.ui.admin.security_tab import SecurityTab


class AdminDashboard(QWidget):
    logout_requested = Signal()

    def __init__(self, user: User, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        top_bar = QWidget()
        top_bar.setFixedHeight(48)
        top_bar.setStyleSheet("background: #1E293B;")
        tbl = QHBoxLayout(top_bar)
        tbl.setContentsMargins(16, 0, 16, 0)
        title = QLabel(f"Admin Panel  ·  {user.full_name}")
        title.setStyleSheet(
            "color:#F8FAFC;font-size:13px;font-weight:600;"
            "font-family:'Segoe UI',sans-serif;background:transparent;"
        )
        tbl.addWidget(title)
        tbl.addStretch()
        logout_btn = QToolButton()
        logout_btn.setIcon(qta.icon("mdi.logout", color="#EF4444"))
        logout_btn.setText("  Log Out")
        logout_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        logout_btn.setStyleSheet(
            "QToolButton{background:transparent;border:none;color:#EF4444;"
            "font-size:12px;font-family:'Segoe UI',sans-serif;padding:0 8px;}"
            "QToolButton:hover{background:rgba(239,68,68,0.15);border-radius:4px;}"
        )
        logout_btn.setCursor(Qt.PointingHandCursor)
        logout_btn.clicked.connect(self.logout_requested)
        tbl.addWidget(logout_btn)
        layout.addWidget(top_bar)

        self._tabs = QTabWidget()
        self._users = UsersTab()
        self._security = SecurityTab()
        self._audit = AuditTab()
        self._tabs.addTab(self._users, "Users")
        self._tabs.addTab(CategoriesTab(), "Categories")
        self._tabs.addTab(RulesTab(), "Keyword Rules")
        self._tabs.addTab(SettingsTab(), "Settings")
        self._tabs.addTab(self._security, "Security")
        self._tabs.addTab(self._audit, "Audit Log")
        backup = BackupTab()
        backup.logout_requested.connect(self.logout_requested)
        self._tabs.addTab(backup, "Backups")
        layout.addWidget(self._tabs)

        self._users.view_sessions_requested.connect(self._goto_user_sessions)
        self._users.view_audit_requested.connect(self._goto_user_audit)

    def _goto_user_sessions(self, username: str) -> None:
        self._tabs.setCurrentWidget(self._security)
        self._security.focus_user(username)

    def _goto_user_audit(self, username: str) -> None:
        self._tabs.setCurrentWidget(self._audit)
        self._audit.focus_actor(username)
