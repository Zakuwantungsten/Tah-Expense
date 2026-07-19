import asyncio

from PySide6.QtWidgets import QMainWindow, QStatusBar, QLabel
from PySide6.QtCore import Signal
from PySide6.QtGui import QCloseEvent

from tahmeed.models.user import User
from tahmeed.ui.admin.dashboard import AdminDashboard
from tahmeed.ui.cashier.dashboard import CashierDashboard
from tahmeed.ui.accountant.dashboard import AccountantDashboard


class MainWindow(QMainWindow):
    logout_requested = Signal()

    def __init__(self, user: User):
        super().__init__()
        self.user = user
        self._force_close = False
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

    async def prepare_to_leave(self) -> bool:
        """Ask the dashboard to save/discard unsaved work. True = OK to leave."""
        dash = self.centralWidget()
        if hasattr(dash, "prepare_to_leave"):
            return await dash.prepare_to_leave()
        return True

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._force_close:
            event.accept()
            return
        # Defer close until unsaved work is handled (async).
        event.ignore()
        asyncio.ensure_future(self._close_after_prepare())

    async def _close_after_prepare(self) -> None:
        if not await self.prepare_to_leave():
            return
        self._force_close = True
        self.close()
        # Window X / Alt+F4: leave the process. Logout sets _force_close and
        # closes without going through this path, then shows the login window.
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            app.quit()
