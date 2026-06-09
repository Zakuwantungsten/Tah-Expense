from typing import Optional

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QHBoxLayout,
    QLineEdit, QPushButton, QLabel, QCheckBox, QColorDialog,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

from tahmeed.models.category import Category


class CategoryDialog(QDialog):
    """
    Shared Add/Edit Category dialog.
    Used by both AdminDashboard (Categories tab) and the Cashier entry form.
    After accept(), read result_data dict:
        {"name", "color", "requires_receipt", "requires_truck"}
    """

    def __init__(self, category: Optional[Category] = None, parent=None):
        super().__init__(parent)
        self._category = category
        self.result_data: dict = {}
        self._selected_color = category.color if category else "#4A90D9"
        self.setWindowTitle("Edit Category" if category else "Add New Category")
        self.setFixedWidth(380)
        self._build_ui()
        if category:
            self._populate(category)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)

        self._name = QLineEdit()
        self._name.setFixedHeight(32)
        self._name.setPlaceholderText("e.g.  Council Fees — Kapiri")
        form.addRow("Name:", self._name)

        color_row = QHBoxLayout()
        self._color_btn = QPushButton()
        self._color_btn.setFixedSize(48, 28)
        self._color_btn.clicked.connect(self._pick_color)
        self._color_label = QLabel(self._selected_color)
        self._color_label.setStyleSheet("color: #666;")
        color_row.addWidget(self._color_btn)
        color_row.addWidget(self._color_label)
        color_row.addStretch()
        self._update_color_btn()
        form.addRow("Colour:", color_row)

        self._req_receipt = QCheckBox("Requires receipt")
        form.addRow("", self._req_receipt)

        self._req_truck = QCheckBox("Requires truck number")
        self._req_truck.setChecked(True)
        form.addRow("", self._req_truck)

        layout.addLayout(form)

        self._error = QLabel("")
        self._error.setStyleSheet("color: #e74c3c; font-size: 12px;")
        self._error.setWordWrap(True)
        layout.addWidget(self._error)

        btns = QHBoxLayout()
        btns.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("Save Category")
        save_btn.setDefault(True)
        save_btn.clicked.connect(self._validate)
        btns.addWidget(cancel_btn)
        btns.addWidget(save_btn)
        layout.addLayout(btns)

    def _populate(self, cat: Category) -> None:
        self._name.setText(cat.name)
        self._req_receipt.setChecked(cat.requires_receipt)
        self._req_truck.setChecked(cat.requires_truck)

    def _update_color_btn(self) -> None:
        self._color_btn.setStyleSheet(
            f"background-color: {self._selected_color}; "
            f"border: 1px solid #aaa; border-radius: 3px;"
        )
        self._color_label.setText(self._selected_color)

    def _pick_color(self) -> None:
        color = QColorDialog.getColor(QColor(self._selected_color), self, "Pick Category Colour")
        if color.isValid():
            self._selected_color = color.name()
            self._update_color_btn()

    def _validate(self) -> None:
        name = self._name.text().strip()
        if not name:
            self._error.setText("Category name is required.")
            return
        self.result_data = {
            "name": name,
            "color": self._selected_color,
            "requires_receipt": self._req_receipt.isChecked(),
            "requires_truck": self._req_truck.isChecked(),
        }
        self.accept()
