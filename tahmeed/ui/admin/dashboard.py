from PySide6.QtWidgets import QWidget, QVBoxLayout, QTabWidget

from tahmeed.models.user import User
from tahmeed.ui.admin.users_tab import UsersTab
from tahmeed.ui.admin.categories_tab import CategoriesTab
from tahmeed.ui.admin.rules_tab import RulesTab
from tahmeed.ui.admin.settings_tab import SettingsTab


class AdminDashboard(QWidget):
    def __init__(self, user: User, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        tabs = QTabWidget()
        tabs.addTab(UsersTab(), "Users")
        tabs.addTab(CategoriesTab(), "Categories")
        tabs.addTab(RulesTab(), "Keyword Rules")
        tabs.addTab(SettingsTab(), "Settings")
        layout.addWidget(tabs)
