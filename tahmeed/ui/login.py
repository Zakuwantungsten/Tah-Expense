import asyncio
import os

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QLineEdit,
    QPushButton, QStackedWidget, QApplication,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPalette, QPixmap

from tahmeed.services.auth import authenticate, any_user_exists, create_user

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
_LOGO_LIGHT = os.path.join(_ROOT, "logo.png")
_LOGO_DARK = os.path.join(_ROOT, "ChatGPT_Image_Jun_10__2026__09_07_13_AM-removebg-preview.png")

_BTN_STYLE = """
    QPushButton {
        background-color: #E85D04;
        color: white;
        border: none;
        border-radius: 6px;
        font-size: 14px;
        font-weight: bold;
    }
    QPushButton:hover { background-color: #F48C06; }
    QPushButton:pressed { background-color: #DC2F02; }
    QPushButton:disabled { background-color: #888; color: #ccc; }
"""


def _is_dark() -> bool:
    app = QApplication.instance()
    return app is not None and app.palette().color(QPalette.Window).lightness() < 128


def _logo_pixmap(width: int = 200) -> QPixmap:
    path = _LOGO_DARK if _is_dark() else _LOGO_LIGHT
    pix = QPixmap(path)
    return pix.scaledToWidth(width, Qt.SmoothTransformation) if not pix.isNull() else pix


def _title_html(text: str, size_pt: int) -> str:
    """Returns HTML for the title with the trailing 's' in brand orange."""
    base = text[:-1]
    s = text[-1]
    style = f"font-size:{size_pt}pt; font-weight:700; font-family:'Segoe UI';"
    return (
        f'<span style="{style}">{base}</span>'
        f'<span style="{style} color:#E85D04;">{s}</span>'
    )


class LoginWindow(QWidget):
    login_successful = Signal(object)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Tahmeed Expenses")
        self.setFixedSize(460, 520)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_login_page())
        self._stack.addWidget(self._build_setup_page())
        root.addWidget(self._stack)

    def _page_bg_style(self, name: str) -> str:
        if _is_dark():
            return ""
        return f"#{name} {{ background-color: #ffffff; }}"

    def _build_login_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("loginPage")
        style = self._page_bg_style("loginPage")
        if style:
            page.setStyleSheet(style)

        layout = QVBoxLayout(page)
        layout.setContentsMargins(60, 36, 60, 36)
        layout.setSpacing(10)

        logo = QLabel()
        logo.setAlignment(Qt.AlignCenter)
        pix = _logo_pixmap(200)
        if not pix.isNull():
            logo.setPixmap(pix)
        layout.addWidget(logo)
        layout.addSpacing(8)

        title = QLabel(_title_html("Tahmeed Expenses", 20))
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        sub = QLabel("Sign in to continue")
        sub.setAlignment(Qt.AlignCenter)
        sub.setStyleSheet("color: #888; font-size: 13px;")
        layout.addWidget(sub)
        layout.addSpacing(14)

        self._username = QLineEdit()
        self._username.setPlaceholderText("Username")
        self._username.setFixedHeight(40)
        layout.addWidget(self._username)

        self._password = QLineEdit()
        self._password.setPlaceholderText("Password")
        self._password.setEchoMode(QLineEdit.Password)
        self._password.setFixedHeight(40)
        self._password.returnPressed.connect(self._on_login)
        layout.addWidget(self._password)

        self._login_error = QLabel("")
        self._login_error.setStyleSheet("color: #e74c3c; font-size: 12px;")
        self._login_error.setAlignment(Qt.AlignCenter)
        self._login_error.setWordWrap(True)
        layout.addWidget(self._login_error)

        self._login_btn = QPushButton("Sign In")
        self._login_btn.setFixedHeight(42)
        self._login_btn.setCursor(Qt.PointingHandCursor)
        self._login_btn.setStyleSheet(_BTN_STYLE)
        self._login_btn.clicked.connect(self._on_login)
        layout.addWidget(self._login_btn)

        layout.addStretch()
        return page

    def _build_setup_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("setupPage")
        style = self._page_bg_style("setupPage")
        if style:
            page.setStyleSheet(style)

        layout = QVBoxLayout(page)
        layout.setContentsMargins(60, 24, 60, 24)
        layout.setSpacing(10)

        logo = QLabel()
        logo.setAlignment(Qt.AlignCenter)
        pix = _logo_pixmap(160)
        if not pix.isNull():
            logo.setPixmap(pix)
        layout.addWidget(logo)
        layout.addSpacing(4)

        title = QLabel(_title_html("Tahmeed Expenses", 16))
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        sub = QLabel("No accounts found — create the admin account to get started.")
        sub.setAlignment(Qt.AlignCenter)
        sub.setStyleSheet("color: #888; font-size: 13px;")
        sub.setWordWrap(True)
        layout.addWidget(sub)
        layout.addSpacing(6)

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
        self._setup_btn.setFixedHeight(40)
        self._setup_btn.setCursor(Qt.PointingHandCursor)
        self._setup_btn.setStyleSheet(_BTN_STYLE)
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
        asyncio.ensure_future(self._check_for_updates())

    async def _check_for_updates(self) -> None:
        try:
            from tahmeed.services.update_service import check_for_update
            from tahmeed.ui.dialogs.update_dialog import UpdateDialog

            info = await check_for_update()
            if info:
                UpdateDialog(info, self).exec()
        except Exception:
            pass

    # ------------------------------------------------------------------
    def clear_fields(self) -> None:
        self._username.clear()
        self._password.clear()
        self._login_error.setText("")

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
