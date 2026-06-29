from PySide6.QtWidgets import QMainWindow, QStatusBar, QLabel
from PySide6.QtCore import Signal

from tahmeed.models.user import User
from tahmeed.ui.admin.dashboard import AdminDashboard
from tahmeed.ui.cashier.dashboard import CashierDashboard
from tahmeed.ui.accountant.dashboard import AccountantDashboard


class MainWindow(QMainWindow):
    logout_requested = Signal()

    def __init__(self, user: User):
        super().__init__()
        self.user = user
        self.setWindowTitle(f"Tahmeed Expense — {user.full_name}")
        self.setMinimumSize(1100, 700)
        self._build_ui()

    def _build_ui(self) -> None:
        if self.user.role == "admin":
            dash = AdminDashboard(self.user)
        elif self.user.role == "cashier":
            dash = CashierDashboard(self.user)
        elif self.user.role == "accountant":
            dash = AccountantDashboard(self.user)
        else:
            dash = QLabel(f"{self.user.role.title()} dashboard — coming soon")

        if hasattr(dash, "logout_requested"):
            dash.logout_requested.connect(self.logout_requested)
        self.setCentralWidget(dash)

        bar = QStatusBar()
        bar.showMessage(f"  {self.user.full_name}  ·  {self.user.role.title()}")
        self.setStatusBar(bar)
