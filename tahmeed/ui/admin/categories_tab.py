import asyncio
from typing import List, Optional
from bson import ObjectId

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QDialog, QLabel, QMessageBox, QCheckBox,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

from tahmeed.models.category import Category
from tahmeed.services.category_service import (
    get_all_categories, create_category, update_category, toggle_category
)
from tahmeed.ui.dialogs.category_dialog import CategoryDialog


class CategoriesTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._categories: List[Category] = []
        self._build_ui()
        asyncio.ensure_future(self._load())

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        bar = QHBoxLayout()
        add_btn = QPushButton("+ Add Category")
        add_btn.setFixedWidth(130)
        add_btn.clicked.connect(self._on_add)

        self._show_inactive = QCheckBox("Show inactive")
        self._show_inactive.stateChanged.connect(lambda _: asyncio.ensure_future(self._load()))

        bar.addWidget(add_btn)
        bar.addWidget(self._show_inactive)
        bar.addStretch()
        layout.addLayout(bar)

        self._table = QTableWidget()
        self._table.setColumnCount(6)
        self._table.setHorizontalHeaderLabels(
            ["Name", "Color", "Req. Receipt", "Req. Truck", "Status", "Actions"]
        )
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.Fixed)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(5, QHeaderView.Fixed)
        self._table.setColumnWidth(1, 60)
        self._table.setColumnWidth(5, 160)
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
            include_inactive = self._show_inactive.isChecked()
            self._categories = await get_all_categories(include_inactive=include_inactive)
            self._refresh()
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to load categories:\n{exc}")

    def _refresh(self) -> None:
        self._table.setRowCount(len(self._categories))
        for i, cat in enumerate(self._categories):
            self._table.setItem(i, 0, QTableWidgetItem(cat.name))

            # Color swatch cell
            color_item = QTableWidgetItem("")
            color_item.setBackground(QColor(cat.color))
            self._table.setItem(i, 1, color_item)

            yes_no = lambda b: "Yes" if b else "No"
            self._table.setItem(i, 2, QTableWidgetItem(yes_no(cat.requires_receipt)))
            self._table.setItem(i, 3, QTableWidgetItem(yes_no(cat.requires_truck)))

            status_item = QTableWidgetItem("Active" if cat.active else "Inactive")
            status_item.setForeground(
                QColor("#27ae60") if cat.active else QColor("#e74c3c")
            )
            self._table.setItem(i, 4, status_item)
            self._table.setCellWidget(i, 5, self._make_actions(cat))

        self._table.resizeRowsToContents()

    def _make_actions(self, cat: Category) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(4)

        edit_btn = QPushButton("Edit")
        edit_btn.setFixedWidth(55)
        edit_btn.clicked.connect(lambda _, c=cat: self._on_edit(c))

        label = "Deactivate" if cat.active else "Activate"
        toggle_btn = QPushButton(label)
        toggle_btn.setFixedWidth(80)
        toggle_btn.clicked.connect(lambda _, c=cat: self._on_toggle(c))

        lay.addWidget(edit_btn)
        lay.addWidget(toggle_btn)
        return w

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _on_add(self) -> None:
        dlg = CategoryDialog(parent=self)
        if dlg.exec() == QDialog.Accepted:
            asyncio.ensure_future(self._do_add(dlg.result_data))

    async def _do_add(self, data: dict) -> None:
        try:
            await create_category(
                data["name"], data["color"],
                data["requires_receipt"], data["requires_truck"]
            )
            await self._load()
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to create category:\n{exc}")

    def _on_edit(self, cat: Category) -> None:
        dlg = CategoryDialog(category=cat, parent=self)
        if dlg.exec() == QDialog.Accepted:
            asyncio.ensure_future(self._do_edit(cat._id, dlg.result_data))

    async def _do_edit(self, cat_id: ObjectId, data: dict) -> None:
        try:
            await update_category(cat_id, **data)
            await self._load()
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to update category:\n{exc}")

    def _on_toggle(self, cat: Category) -> None:
        action = "deactivate" if cat.active else "activate"
        if (
            QMessageBox.question(
                self, "Confirm", f"Are you sure you want to {action} '{cat.name}'?"
            )
            == QMessageBox.Yes
        ):
            asyncio.ensure_future(self._do_toggle(cat._id, not cat.active))

    async def _do_toggle(self, cat_id: ObjectId, active: bool) -> None:
        try:
            await toggle_category(cat_id, active)
            await self._load()
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to update category:\n{exc}")
