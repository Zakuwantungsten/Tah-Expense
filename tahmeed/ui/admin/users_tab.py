import asyncio
from typing import List, Optional

from bson import ObjectId
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QMenu,
)

from tahmeed.models.user import User
from tahmeed.services.auth import create_user
from tahmeed.ui.widgets.loading_overlay import hide_widget_loading, show_widget_loading
from tahmeed.services.password_policy import (
    password_placeholder,
    password_policy_errors,
)
from tahmeed.services.settings_service import DEFAULTS, get_all_settings
from tahmeed.services.user_service import (
    ban_user,
    delete_user,
    force_logout_user,
    get_all_users,
    reset_password,
    toggle_active,
    unban_user,
    update_user,
)


def _editable_roles(user: Optional[User]) -> list[str]:
    roles = ["cashier", "accountant"]
    if user and user.role == "admin":
        roles.append("admin")
    return roles


class _UserDialog(QDialog):
    def __init__(
        self,
        user: Optional[User] = None,
        parent=None,
        *,
        password_policy: Optional[dict] = None,
    ):
        super().__init__(parent)
        self._user = user
        self._password_policy = dict(password_policy or DEFAULTS)
        self.result_data: dict = {}
        self.setWindowTitle("Edit User" if user else "Add User")
        self.setFixedWidth(360)
        self._build_ui()
        if user:
            self._populate(user)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)

        self._fullname = QLineEdit()
        self._fullname.setFixedHeight(32)
        form.addRow("Full Name:", self._fullname)

        self._username = QLineEdit()
        self._username.setFixedHeight(32)
        form.addRow("Username:", self._username)

        self._role = QComboBox()
        self._role.setFixedHeight(32)
        self._role.addItems(_editable_roles(self._user))
        form.addRow("Role:", self._role)

        self._password = QLineEdit()
        self._password.setFixedHeight(32)
        self._password.setEchoMode(QLineEdit.Password)
        self._password.setPlaceholderText(
            password_placeholder(
                editing=bool(self._user),
                settings=self._password_policy,
            )
        )
        form.addRow("Password:", self._password)

        layout.addLayout(form)

        self._error = QLabel("")
        self._error.setStyleSheet("color: #e74c3c; font-size: 12px;")
        self._error.setWordWrap(True)
        layout.addWidget(self._error)

        btns = QHBoxLayout()
        btns.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("Save")
        save_btn.setDefault(True)
        save_btn.clicked.connect(self._validate)
        btns.addWidget(cancel_btn)
        btns.addWidget(save_btn)
        layout.addLayout(btns)

    def _populate(self, user: User) -> None:
        self._fullname.setText(user.full_name)
        self._username.setText(user.username)
        self._username.setReadOnly(True)
        idx = self._role.findText(user.role)
        if idx >= 0:
            self._role.setCurrentIndex(idx)

    def _validate(self) -> None:
        full_name = self._fullname.text().strip()
        username = self._username.text().strip()
        password = self._password.text()

        if not full_name:
            self._error.setText("Full name is required.")
            return
        if not username:
            self._error.setText("Username is required.")
            return
        if not self._user and not password:
            self._error.setText("Password is required for new users.")
            return
        if password:
            errors = password_policy_errors(password, self._password_policy)
            if errors:
                self._error.setText(errors[0])
                return

        self.result_data = {
            "full_name": full_name,
            "username": username,
            "password": password,
            "role": self._role.currentText(),
        }
        self.accept()


class _ResetPasswordDialog(QDialog):
    def __init__(
        self,
        user: User,
        parent=None,
        *,
        password_policy: Optional[dict] = None,
    ):
        super().__init__(parent)
        self._password_policy = dict(password_policy or DEFAULTS)
        self.result_password = ""
        self.setWindowTitle(f"Reset password — {user.username}")
        self.setFixedWidth(360)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self._password = QLineEdit()
        self._password.setEchoMode(QLineEdit.Password)
        min_hint = password_placeholder(settings=self._password_policy).lower()
        self._password.setPlaceholderText(f"New password ({min_hint})")
        form.addRow("New password:", self._password)
        self._confirm = QLineEdit()
        self._confirm.setEchoMode(QLineEdit.Password)
        form.addRow("Confirm:", self._confirm)
        layout.addLayout(form)
        self._error = QLabel("")
        self._error.setStyleSheet("color:#e74c3c;font-size:12px;")
        layout.addWidget(self._error)
        row = QHBoxLayout()
        row.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("Reset")
        ok.setDefault(True)
        ok.clicked.connect(self._validate)
        row.addWidget(cancel)
        row.addWidget(ok)
        layout.addLayout(row)

    def _validate(self) -> None:
        password = self._password.text()
        errors = password_policy_errors(password, self._password_policy)
        if errors:
            self._error.setText(errors[0])
            return
        if password != self._confirm.text():
            self._error.setText("Passwords do not match.")
            return
        self.result_password = password
        self.accept()


class UsersTab(QWidget):
    view_sessions_requested = Signal(str)  # username
    view_audit_requested = Signal(str)  # username

    def __init__(self, parent=None):
        super().__init__(parent)
        self._users: List[User] = []
        self._build_ui()
        asyncio.ensure_future(self._load())

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        bar = QHBoxLayout()
        add_btn = QPushButton("+ Add User")
        add_btn.setFixedWidth(110)
        add_btn.clicked.connect(self._on_add)
        bar.addWidget(add_btn)
        bar.addStretch()
        layout.addLayout(bar)

        self._table = QTableWidget()
        self._table.setColumnCount(6)
        self._table.setHorizontalHeaderLabels(
            ["Full Name", "Username", "Role", "Status", "Last Login", "Actions"]
        )
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(5, QHeaderView.Fixed)
        self._table.setColumnWidth(5, 120)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        layout.addWidget(self._table)

    async def _load(self) -> None:
        show_widget_loading(self, "Loading users…")
        try:
            self._users = await get_all_users()
            self._refresh()
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to load users:\n{exc}")
        finally:
            hide_widget_loading(self)

    def _status_color(self, user: User) -> QColor:
        label = user.status_label
        if label == "Active":
            return QColor("#27ae60")
        if label == "Banned":
            return QColor("#b91c1c")
        if label == "Deleted":
            return QColor("#6b7280")
        return QColor("#e74c3c")

    def _refresh(self) -> None:
        self._table.setRowCount(len(self._users))
        for i, user in enumerate(self._users):
            self._table.setItem(i, 0, QTableWidgetItem(user.full_name))
            self._table.setItem(i, 1, QTableWidgetItem(user.username))
            self._table.setItem(i, 2, QTableWidgetItem(user.role.title()))

            status_item = QTableWidgetItem(user.status_label)
            status_item.setForeground(self._status_color(user))
            if user.banned and user.ban_reason:
                status_item.setToolTip(user.ban_reason)
            self._table.setItem(i, 3, status_item)

            last = (
                user.last_login.strftime("%d %b %Y  %H:%M")
                if user.last_login
                else "Never"
            )
            self._table.setItem(i, 4, QTableWidgetItem(last))
            self._table.setCellWidget(i, 5, self._make_actions(user))

        self._table.resizeRowsToContents()

    def _make_actions(self, user: User) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(4)

        menu_btn = QToolButton()
        menu_btn.setText("Actions")
        menu_btn.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(menu_btn)
        menu.addAction("Edit…", lambda u=user: self._on_edit(u))
        menu.addAction("Reset password…", lambda u=user: self._on_reset_password(u))
        menu.addSeparator()
        if user.banned:
            menu.addAction("Unban", lambda u=user: self._on_unban(u))
        else:
            menu.addAction("Ban…", lambda u=user: self._on_ban(u))
        if user.active and not user.banned:
            menu.addAction("Deactivate", lambda u=user: self._on_toggle(u))
        elif not user.banned:
            menu.addAction("Activate", lambda u=user: self._on_toggle(u))
        menu.addAction("Force logout", lambda u=user: self._on_force_logout(u))
        menu.addSeparator()
        menu.addAction(
            "Sessions for this user…",
            lambda u=user: self.view_sessions_requested.emit(u.username),
        )
        menu.addAction(
            "Audit for this user…",
            lambda u=user: self.view_audit_requested.emit(u.username),
        )
        menu.addSeparator()
        delete_act = menu.addAction("Delete…", lambda u=user: self._on_delete(u))
        delete_act.setEnabled(not bool(user.deleted_at))
        menu_btn.setMenu(menu)
        lay.addWidget(menu_btn)
        return w

    def _on_add(self) -> None:
        asyncio.ensure_future(self._open_add())

    async def _password_policy(self) -> dict:
        try:
            return await get_all_settings()
        except Exception:
            return dict(DEFAULTS)

    async def _open_add(self) -> None:
        dlg = _UserDialog(parent=self, password_policy=await self._password_policy())
        if dlg.exec() == QDialog.Accepted:
            await self._do_add(dlg.result_data)

    async def _do_add(self, data: dict) -> None:
        try:
            await create_user(
                data["username"], data["password"], data["role"], data["full_name"]
            )
            await self._load()
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to create user:\n{exc}")

    def _on_edit(self, user: User) -> None:
        asyncio.ensure_future(self._open_edit(user))

    async def _open_edit(self, user: User) -> None:
        dlg = _UserDialog(
            user=user, parent=self, password_policy=await self._password_policy()
        )
        if dlg.exec() == QDialog.Accepted:
            await self._do_edit(user._id, dlg.result_data)

    async def _do_edit(self, user_id: ObjectId, data: dict) -> None:
        try:
            await update_user(user_id, full_name=data["full_name"], role=data["role"])
            if data["password"]:
                await reset_password(user_id, data["password"])
            await self._load()
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to update user:\n{exc}")

    def _on_reset_password(self, user: User) -> None:
        asyncio.ensure_future(self._open_reset_password(user))

    async def _open_reset_password(self, user: User) -> None:
        dlg = _ResetPasswordDialog(
            user, parent=self, password_policy=await self._password_policy()
        )
        if dlg.exec() == QDialog.Accepted:
            await self._do_reset_password(user._id, dlg.result_password)

    async def _do_reset_password(self, user_id: ObjectId, password: str) -> None:
        try:
            await reset_password(user_id, password)
            QMessageBox.information(
                self,
                "Password reset",
                "Password updated. All of that user’s sessions were signed out.",
            )
            await self._load()
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to reset password:\n{exc}")

    def _on_toggle(self, user: User) -> None:
        action = "deactivate" if user.active else "activate"
        if (
            QMessageBox.question(
                self, "Confirm", f"Are you sure you want to {action} {user.full_name}?"
            )
            != QMessageBox.Yes
        ):
            return
        asyncio.ensure_future(self._do_toggle(user._id, not user.active))

    async def _do_toggle(self, user_id: ObjectId, active: bool) -> None:
        try:
            await toggle_active(user_id, active)
            await self._load()
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to update user:\n{exc}")

    def _on_ban(self, user: User) -> None:
        reason, ok = QInputDialog.getText(
            self,
            "Ban user",
            f"Reason for banning {user.full_name} (required):",
        )
        if not ok:
            return
        reason = reason.strip()
        if len(reason) < 3:
            QMessageBox.warning(self, "Ban user", "Enter a ban reason (at least 3 characters).")
            return
        if (
            QMessageBox.warning(
                self,
                "Confirm ban",
                f"Ban {user.full_name}? They cannot sign in until unbanned and re-activated.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            != QMessageBox.Yes
        ):
            return
        asyncio.ensure_future(self._do_ban(user._id, reason))

    async def _do_ban(self, user_id: ObjectId, reason: str) -> None:
        try:
            await ban_user(user_id, reason)
            await self._load()
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to ban user:\n{exc}")

    def _on_unban(self, user: User) -> None:
        if (
            QMessageBox.question(
                self,
                "Unban user",
                f"Unban {user.full_name}? They stay inactive until you Activate them.",
            )
            != QMessageBox.Yes
        ):
            return
        asyncio.ensure_future(self._do_unban(user._id))

    async def _do_unban(self, user_id: ObjectId) -> None:
        try:
            await unban_user(user_id)
            await self._load()
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to unban user:\n{exc}")

    def _on_force_logout(self, user: User) -> None:
        if (
            QMessageBox.question(
                self,
                "Force logout",
                f"End all sessions for {user.full_name}?",
            )
            != QMessageBox.Yes
        ):
            return
        asyncio.ensure_future(self._do_force_logout(user._id))

    async def _do_force_logout(self, user_id: ObjectId) -> None:
        try:
            await force_logout_user(user_id)
            QMessageBox.information(self, "Done", "Sessions revoked.")
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to force logout:\n{exc}")

    def _on_delete(self, user: User) -> None:
        typed, ok = QInputDialog.getText(
            self,
            "Delete user",
            f"Type the username “{user.username}” to permanently soft-delete this account:",
        )
        if not ok or typed.strip() != user.username:
            if ok:
                QMessageBox.information(self, "Cancelled", "Username did not match.")
            return
        asyncio.ensure_future(self._do_delete(user._id))

    async def _do_delete(self, user_id: ObjectId) -> None:
        try:
            await delete_user(user_id)
            await self._load()
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to delete user:\n{exc}")
