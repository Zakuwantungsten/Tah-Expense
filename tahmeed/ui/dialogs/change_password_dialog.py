from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QHBoxLayout,
    QLineEdit, QPushButton, QLabel,
)
from PySide6.QtCore import Qt


class ChangePasswordDialog(QDialog):
    """Collect the current and new password for the signed-in user.

    After accept(), read result_data dict: {"current", "new"}.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.result_data: dict = {}
        self.setWindowTitle("Change Password")
        self.setFixedWidth(360)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)

        self._current = QLineEdit()
        self._current.setFixedHeight(32)
        self._current.setEchoMode(QLineEdit.Password)
        form.addRow("Current Password:", self._current)

        self._new = QLineEdit()
        self._new.setFixedHeight(32)
        self._new.setEchoMode(QLineEdit.Password)
        self._new.setPlaceholderText("Min 6 characters")
        form.addRow("New Password:", self._new)

        self._confirm = QLineEdit()
        self._confirm.setFixedHeight(32)
        self._confirm.setEchoMode(QLineEdit.Password)
        form.addRow("Confirm Password:", self._confirm)

        layout.addLayout(form)

        self._error = QLabel("")
        self._error.setStyleSheet("color: #e74c3c; font-size: 12px;")
        self._error.setWordWrap(True)
        layout.addWidget(self._error)

        btns = QHBoxLayout()
        btns.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("Update Password")
        save_btn.setDefault(True)
        save_btn.clicked.connect(self._validate)
        btns.addWidget(cancel_btn)
        btns.addWidget(save_btn)
        layout.addLayout(btns)

    def _validate(self) -> None:
        current = self._current.text()
        new = self._new.text()
        confirm = self._confirm.text()

        if not current:
            self._error.setText("Enter your current password.")
            return
        if len(new) < 6:
            self._error.setText("New password must be at least 6 characters.")
            return
        if new != confirm:
            self._error.setText("New password and confirmation do not match.")
            return
        if new == current:
            self._error.setText("New password must be different from the current one.")
            return

        self.result_data = {"current": current, "new": new}
        self.accept()
