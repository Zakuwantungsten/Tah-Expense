import asyncio

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QLineEdit,
    QPushButton, QStackedWidget,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont

from tahmeed.services.auth import authenticate, any_user_exists, create_user


class LoginWindow(QWidget):
    login_successful = Signal(object)   # emits User on success

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Tahmeed Expense")
        self.setFixedSize(420, 380)
        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_login_page())   # index 0
        self._stack.addWidget(self._build_setup_page())   # index 1
        root.addWidget(self._stack)

    def _build_login_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(52, 52, 52, 52)
        layout.setSpacing(14)

        title = QLabel("Tahmeed Expense")
        title.setFont(QFont("Segoe UI", 20, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        sub = QLabel("Sign in to continue")
        sub.setAlignment(Qt.AlignCenter)
        sub.setStyleSheet("color: #888; font-size: 13px;")
        layout.addWidget(sub)
        layout.addSpacing(8)

        self._username = QLineEdit()
        self._username.setPlaceholderText("Username")
        self._username.setFixedHeight(38)
        layout.addWidget(self._username)

        self._password = QLineEdit()
        self._password.setPlaceholderText("Password")
        self._password.setEchoMode(QLineEdit.Password)
        self._password.setFixedHeight(38)
        self._password.returnPressed.connect(self._on_login)
        layout.addWidget(self._password)

        self._login_error = QLabel("")
        self._login_error.setStyleSheet("color: #e74c3c; font-size: 12px;")
        self._login_error.setAlignment(Qt.AlignCenter)
        self._login_error.setWordWrap(True)
        layout.addWidget(self._login_error)

        self._login_btn = QPushButton("Sign In")
        self._login_btn.setFixedHeight(38)
        self._login_btn.clicked.connect(self._on_login)
        layout.addWidget(self._login_btn)

        layout.addStretch()
        return page

    def _build_setup_page(self) -> QWidget:
        """Shown only on first run — creates the admin account."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(52, 40, 52, 40)
        layout.setSpacing(12)

        title = QLabel("Welcome to Tahmeed Expense")
        title.setFont(QFont("Segoe UI", 16, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        sub = QLabel("No accounts found — create the admin account to get started.")
        sub.setAlignment(Qt.AlignCenter)
        sub.setStyleSheet("color: #888; font-size: 13px;")
        sub.setWordWrap(True)
        layout.addWidget(sub)
        layout.addSpacing(4)

        self._setup_fullname = QLineEdit()
        self._setup_fullname.setPlaceholderText("Full Name")
        self._setup_fullname.setFixedHeight(36)
        layout.addWidget(self._setup_fullname)

        self._setup_username = QLineEdit()
        self._setup_username.setPlaceholderText("Username")
        self._setup_username.setFixedHeight(36)
        layout.addWidget(self._setup_username)

        self._setup_password = QLineEdit()
        self._setup_password.setPlaceholderText("Password (min 6 chars)")
        self._setup_password.setEchoMode(QLineEdit.Password)
        self._setup_password.setFixedHeight(36)
        layout.addWidget(self._setup_password)

        self._setup_password2 = QLineEdit()
        self._setup_password2.setPlaceholderText("Confirm Password")
        self._setup_password2.setEchoMode(QLineEdit.Password)
        self._setup_password2.setFixedHeight(36)
        layout.addWidget(self._setup_password2)

        self._setup_error = QLabel("")
        self._setup_error.setStyleSheet("color: #e74c3c; font-size: 12px;")
        self._setup_error.setAlignment(Qt.AlignCenter)
        self._setup_error.setWordWrap(True)
        layout.addWidget(self._setup_error)

        self._setup_btn = QPushButton("Create Admin Account")
        self._setup_btn.setFixedHeight(38)
        self._setup_btn.clicked.connect(self._on_setup)
        layout.addWidget(self._setup_btn)

        return page

    # ------------------------------------------------------------------
    # First-run detection
    # ------------------------------------------------------------------

    def showEvent(self, event) -> None:
        super().showEvent(event)
        asyncio.ensure_future(self._check_first_run())

    async def _check_first_run(self) -> None:
        try:
            has_users = await any_user_exists()
            self._stack.setCurrentIndex(0 if has_users else 1)
        except Exception as exc:
            self._login_error.setText(f"Database connection error: {exc}")
            self._stack.setCurrentIndex(0)

    # ------------------------------------------------------------------
    # Login flow
    # ------------------------------------------------------------------

    def _on_login(self) -> None:
        asyncio.ensure_future(self._do_login())

    async def _do_login(self) -> None:
        self._login_btn.setEnabled(False)
        self._login_error.setText("")

        username = self._username.text().strip()
        password = self._password.text()

        if not username or not password:
            self._login_error.setText("Enter username and password.")
            self._login_btn.setEnabled(True)
            return

        user = await authenticate(username, password)
        if user is None:
            self._login_error.setText("Invalid username or password.")
            self._password.clear()
            self._login_btn.setEnabled(True)
            return

        self.login_successful.emit(user)

    # ------------------------------------------------------------------
    # First-run setup flow
    # ------------------------------------------------------------------

    def _on_setup(self) -> None:
        asyncio.ensure_future(self._do_setup())

    async def _do_setup(self) -> None:
        self._setup_btn.setEnabled(False)
        self._setup_error.setText("")

        full_name = self._setup_fullname.text().strip()
        username = self._setup_username.text().strip()
        password = self._setup_password.text()
        password2 = self._setup_password2.text()

        if not all([full_name, username, password, password2]):
            self._setup_error.setText("All fields are required.")
            self._setup_btn.setEnabled(True)
            return
        if password != password2:
            self._setup_error.setText("Passwords do not match.")
            self._setup_btn.setEnabled(True)
            return
        if len(password) < 6:
            self._setup_error.setText("Password must be at least 6 characters.")
            self._setup_btn.setEnabled(True)
            return

        try:
            user = await create_user(username, password, "admin", full_name)
            self.login_successful.emit(user)
        except Exception as exc:
            self._setup_error.setText(f"Error creating account: {exc}")
            self._setup_btn.setEnabled(True)
