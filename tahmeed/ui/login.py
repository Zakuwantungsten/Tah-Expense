import asyncio

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QLineEdit,
    QPushButton, QStackedWidget,
)
from PySide6.QtCore import Qt, QTimer, Signal

from tahmeed.services.auth import authenticate, any_user_exists, create_user
from tahmeed.services.api_client import ApiError
from tahmeed.ui.branding import is_dark_theme, load_brand_logo

# App brand blues (match accountant / cashier UI)
_BLUE = "#0077C5"
_BLUE_HOVER = "#005EA3"
_BLUE_PRESS = "#004B82"
_BLUE_LIGHT = "#E8F4FD"
_NAVY = "#1B2B4B"

_BTN_STYLE = f"""
    QPushButton {{
        background-color: {_BLUE};
        color: white;
        border: none;
        border-radius: 6px;
        font-size: 14px;
        font-weight: bold;
    }}
    QPushButton:hover {{ background-color: {_BLUE_HOVER}; }}
    QPushButton:pressed {{ background-color: {_BLUE_PRESS}; }}
    QPushButton:disabled {{ background-color: #94A3B8; color: #E2E8F0; }}
"""

_FIELD_STYLE = f"""
    QLineEdit {{
        border: 1px solid #CBD5E1;
        border-radius: 6px;
        padding: 0 12px;
        font-size: 14px;
        background: #FFFFFF;
        color: {_NAVY};
    }}
    QLineEdit:focus {{
        border-color: {_BLUE};
    }}
"""


def _is_dark() -> bool:
    return is_dark_theme()


def _logo_pixmap(width: int = 200):
    # Match ink to the page: dark OS theme → dark login chrome → light wordmark.
    return load_brand_logo(width, for_dark_bg=_is_dark())


def _title_html(text: str, size_pt: int) -> str:
    """Title with trailing letter in brand blue."""
    base = text[:-1]
    s = text[-1]
    base_color = "#F1F5F9" if _is_dark() else _NAVY
    style = f"font-size:{size_pt}pt; font-weight:700; font-family:'Segoe UI'; color:{base_color};"
    return (
        f'<span style="{style}">{base}</span>'
        f'<span style="{style} color:{_BLUE};">{s}</span>'
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
        # Soft blue wash so login matches the in-app blue theme
        return (
            f"#{name} {{"
            f" background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,"
            f" stop:0 {_BLUE_LIGHT}, stop:0.35 #FFFFFF, stop:1 #FFFFFF);"
            f" }}"
        )

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
        logo.setAttribute(Qt.WA_TranslucentBackground, True)
        logo.setStyleSheet("background: transparent;")
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
        sub.setStyleSheet("color: #64748B; font-size: 13px;")
        layout.addWidget(sub)
        layout.addSpacing(14)

        self._username = QLineEdit()
        self._username.setPlaceholderText("Username")
        self._username.setFixedHeight(40)
        self._username.setStyleSheet(_FIELD_STYLE)
        layout.addWidget(self._username)

        self._password = QLineEdit()
        self._password.setPlaceholderText("Password")
        self._password.setEchoMode(QLineEdit.Password)
        self._password.setFixedHeight(40)
        self._password.setStyleSheet(_FIELD_STYLE)
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
        logo.setAttribute(Qt.WA_TranslucentBackground, True)
        logo.setStyleSheet("background: transparent;")
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
        sub.setStyleSheet("color: #64748B; font-size: 13px;")
        sub.setWordWrap(True)
        layout.addWidget(sub)
        layout.addSpacing(6)

        self._setup_fullname = QLineEdit()
        self._setup_fullname.setPlaceholderText("Full Name")
        self._setup_fullname.setFixedHeight(36)
        self._setup_fullname.setStyleSheet(_FIELD_STYLE)
        layout.addWidget(self._setup_fullname)

        self._setup_username = QLineEdit()
        self._setup_username.setPlaceholderText("Username")
        self._setup_username.setFixedHeight(36)
        self._setup_username.setStyleSheet(_FIELD_STYLE)
        layout.addWidget(self._setup_username)

        self._setup_password = QLineEdit()
        self._setup_password.setPlaceholderText("Password (min 10 chars)")
        self._setup_password.setEchoMode(QLineEdit.Password)
        self._setup_password.setFixedHeight(36)
        self._setup_password.setStyleSheet(_FIELD_STYLE)
        layout.addWidget(self._setup_password)

        self._setup_password2 = QLineEdit()
        self._setup_password2.setPlaceholderText("Confirm Password")
        self._setup_password2.setEchoMode(QLineEdit.Password)
        self._setup_password2.setFixedHeight(36)
        self._setup_password2.setStyleSheet(_FIELD_STYLE)
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
        # Defer off any active asyncio task (splash reveal / logout return).
        # Python 3.14 + qasync forbids create_task while another Task is running.
        QTimer.singleShot(0, self._kick_first_run_check)

    def _kick_first_run_check(self) -> None:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        asyncio.ensure_future(self._check_first_run())

    async def _check_first_run(self) -> None:
        try:
            has_users = await any_user_exists()
            self._stack.setCurrentIndex(0 if has_users else 1)
        except Exception:
            self._login_error.setText(
                "Cannot reach the server. Check your connection and try again."
            )
            self._stack.setCurrentIndex(0)

    # ------------------------------------------------------------------
    def clear_fields(self) -> None:
        self._username.clear()
        self._password.clear()
        self._login_error.setText("")
        # Sign In is disabled during authenticate(); re-enable so logout → login works.
        self._login_btn.setEnabled(True)
        self._login_btn.setText("Sign In")

    # Login flow
    # ------------------------------------------------------------------

    def _on_login(self) -> None:
        asyncio.ensure_future(self._do_login())

    async def _do_login(self) -> None:
        self._login_btn.setEnabled(False)
        self._login_btn.setText("Signing in…")
        self._login_error.setText("")

        username = self._username.text().strip()
        password = self._password.text()

        if not username or not password:
            self._login_error.setText("Enter username and password.")
            self._login_btn.setEnabled(True)
            self._login_btn.setText("Sign In")
            return

        try:
            user = await authenticate(username, password)
        except ApiError as exc:
            # Prefer short auth messages over raw transport dumps in the login UI.
            detail = str(exc).strip()
            if len(detail) > 120 or "\n" in detail or "Traceback" in detail:
                detail = "Sign-in failed. Please try again."
            self._login_error.setText(detail or "Sign-in failed. Please try again.")
            self._login_btn.setEnabled(True)
            self._login_btn.setText("Sign In")
            return
        if user is None:
            self._login_error.setText("Invalid username or password.")
            self._password.clear()
            self._login_btn.setEnabled(True)
            self._login_btn.setText("Sign In")
            return

        self.login_successful.emit(user)
        # Keep button disabled while this window is hidden; clear_fields()
        # re-enables it when the user logs out and returns here.

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
        if len(password) < 10:
            self._setup_error.setText("Password must be at least 10 characters.")
            self._setup_btn.setEnabled(True)
            return

        try:
            user = await create_user(username, password, "admin", full_name)
            self.login_successful.emit(user)
        except Exception as exc:
            self._setup_error.setText(f"Error creating account: {exc}")
            self._setup_btn.setEnabled(True)
