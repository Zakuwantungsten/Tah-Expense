import asyncio
from typing import List, Optional
from bson import ObjectId

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QDialog, QFormLayout, QLineEdit,
    QComboBox, QLabel, QMessageBox,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

from tahmeed.models.user import User
from tahmeed.services.auth import create_user
from tahmeed.services.user_service import get_all_users, update_user, reset_password, toggle_active


def _editable_roles(user: Optional[User]) -> list[str]:
    roles = ["cashier", "accountant"]
    if user and user.role == "admin":
        roles.append("admin")
    return roles


class _UserDialog(QDialog):
    def __init__(self, user: Optional[User] = None, parent=None):
        super().__init__(parent)
        self._user = user
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
            "Leave blank to keep current" if self._user else "Min 10 characters"
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
        if password and len(password) < 10:
            self._error.setText("Password must be at least 10 characters.")
            return

        self.result_data = {
            "full_name": full_name,
            "username": username,
            "password": password,
            "role": self._role.currentText(),
        }
        self.accept()


class UsersTab(QWidget):
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
        self._table.setColumnWidth(5, 190)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        layout.addWidget(self._table)

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------

    async def _load(self) -> None:
        try:
            self._users = await get_all_users()
            self._refresh()
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to load users:\n{exc}")

    def _refresh(self) -> None:
        self._table.setRowCount(len(self._users))
        for i, user in enumerate(self._users):
            self._table.setItem(i, 0, QTableWidgetItem(user.full_name))
            self._table.setItem(i, 1, QTableWidgetItem(user.username))
            self._table.setItem(i, 2, QTableWidgetItem(user.role.title()))

            status_item = QTableWidgetItem("Active" if user.active else "Inactive")
            status_item.setForeground(
                QColor("#27ae60") if user.active else QColor("#e74c3c")
            )
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

        edit_btn = QPushButton("Edit")
        edit_btn.setFixedWidth(55)
        edit_btn.clicked.connect(lambda _, u=user: self._on_edit(u))

        label = "Deactivate" if user.active else "Activate"
        toggle_btn = QPushButton(label)
        toggle_btn.setFixedWidth(88)
        toggle_btn.clicked.connect(lambda _, u=user: self._on_toggle(u))

        lay.addWidget(edit_btn)
        lay.addWidget(toggle_btn)
        return w

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _on_add(self) -> None:
        dlg = _UserDialog(parent=self)
        if dlg.exec() == QDialog.Accepted:
            asyncio.ensure_future(self._do_add(dlg.result_data))

    async def _do_add(self, data: dict) -> None:
        try:
            await create_user(data["username"], data["password"], data["role"], data["full_name"])
            await self._load()
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to create user:\n{exc}")

    def _on_edit(self, user: User) -> None:
        dlg = _UserDialog(user=user, parent=self)
        if dlg.exec() == QDialog.Accepted:
            asyncio.ensure_future(self._do_edit(user._id, dlg.result_data))

    async def _do_edit(self, user_id: ObjectId, data: dict) -> None:
        try:
            await update_user(user_id, full_name=data["full_name"], role=data["role"])
            if data["password"]:
                await reset_password(user_id, data["password"])
            await self._load()
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to update user:\n{exc}")

    def _on_toggle(self, user: User) -> None:
        action = "deactivate" if user.active else "activate"
        if (
            QMessageBox.question(
                self, "Confirm", f"Are you sure you want to {action} {user.full_name}?"
            )
            == QMessageBox.Yes
        ):
            asyncio.ensure_future(self._do_toggle(user._id, not user.active))

    async def _do_toggle(self, user_id: ObjectId, active: bool) -> None:
        try:
            await toggle_active(user_id, active)
            await self._load()
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to update user:\n{exc}")
