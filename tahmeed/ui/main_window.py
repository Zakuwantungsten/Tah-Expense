from PySide6.QtWidgets import QMainWindow, QStatusBar, QLabel

from tahmeed.models.user import User
from tahmeed.ui.admin.dashboard import AdminDashboard
from tahmeed.ui.cashier.dashboard import CashierDashboard


class MainWindow(QMainWindow):
    def __init__(self, user: User):
        super().__init__()
        self.user = user
        self.setWindowTitle(f"Tahmeed Expense — {user.full_name}")
        self.setMinimumSize(1100, 700)
        self._build_ui()

    def _build_ui(self) -> None:
        if self.user.role == "admin":
            self.setCentralWidget(AdminDashboard(self.user))
        elif self.user.role == "cashier":
            self.setCentralWidget(CashierDashboard(self.user))
        else:
            placeholder = QLabel(f"{self.user.role.title()} dashboard — coming soon")
            self.setCentralWidget(placeholder)

        bar = QStatusBar()
        bar.showMessage(f"  {self.user.full_name}  ·  {self.user.role.title()}")
        self.setStatusBar(bar)
