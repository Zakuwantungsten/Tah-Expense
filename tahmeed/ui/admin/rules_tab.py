import asyncio
from typing import List, Optional
from bson import ObjectId

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QDialog, QFormLayout, QLineEdit,
    QLabel, QMessageBox, QComboBox, QSpinBox, QGroupBox,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

from tahmeed.models.keyword_rule import KeywordRule
from tahmeed.models.category import Category
from tahmeed.services.rule_service import (
    get_all_rules, create_rule, update_rule, toggle_rule, test_description
)
from tahmeed.services.category_service import get_all_categories

MATCH_TYPES = ["contains", "startswith", "exact", "fuzzy"]
MATCH_LABELS = {
    "contains": "Contains",
    "startswith": "Starts with",
    "exact": "Exact match",
    "fuzzy": "Fuzzy (typo-tolerant)",
}


class _RuleDialog(QDialog):
    def __init__(
        self,
        categories: List[Category],
        rule: Optional[KeywordRule] = None,
        parent=None,
    ):
        super().__init__(parent)
        self._rule = rule
        self._categories = categories
        self.result_data: dict = {}
        self.setWindowTitle("Edit Rule" if rule else "Add Rule")
        self.setFixedWidth(420)
        self._build_ui()
        if rule:
            self._populate(rule)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)

        self._pattern = QLineEdit()
        self._pattern.setFixedHeight(32)
        self._pattern.setPlaceholderText("e.g.  kapiri council")
        form.addRow("Pattern:", self._pattern)

        self._category = QComboBox()
        self._category.setFixedHeight(32)
        for cat in self._categories:
            self._category.addItem(cat.name, userData=cat._id)
        form.addRow("Category:", self._category)

        self._match_type = QComboBox()
        self._match_type.setFixedHeight(32)
        for mt in MATCH_TYPES:
            self._match_type.addItem(MATCH_LABELS[mt], userData=mt)
        form.addRow("Match Type:", self._match_type)

        self._priority = QSpinBox()
        self._priority.setFixedHeight(32)
        self._priority.setRange(1, 10)
        self._priority.setValue(5)
        self._priority.setToolTip("1 = lowest, 10 = highest. Higher rules win when multiple match.")
        form.addRow("Priority (1–10):", self._priority)

        layout.addLayout(form)

        # Match type hint
        self._hint = QLabel("")
        self._hint.setStyleSheet("color: #888; font-size: 11px;")
        self._hint.setWordWrap(True)
        layout.addWidget(self._hint)
        self._match_type.currentIndexChanged.connect(self._update_hint)
        self._update_hint()

        self._error = QLabel("")
        self._error.setStyleSheet("color: #e74c3c; font-size: 12px;")
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

    def _update_hint(self) -> None:
        hints = {
            "contains":   'Description contains this pattern anywhere (e.g. "council" matches "KAPIRI COUNCIL GOING")',
            "startswith": 'Description must start with this pattern',
            "exact":      'Description must match this pattern exactly (after lowercasing)',
            "fuzzy":      'Typo-tolerant match — catches "KONSIL" matching "council"',
        }
        mt = self._match_type.currentData()
        self._hint.setText(hints.get(mt, ""))

    def _populate(self, rule: KeywordRule) -> None:
        self._pattern.setText(rule.pattern)
        self._priority.setValue(rule.priority)
        # Set category
        for i in range(self._category.count()):
            if self._category.itemData(i) == rule.category_id:
                self._category.setCurrentIndex(i)
                break
        # Set match type
        for i in range(self._match_type.count()):
            if self._match_type.itemData(i) == rule.match_type:
                self._match_type.setCurrentIndex(i)
                break

    def _validate(self) -> None:
        pattern = self._pattern.text().strip()
        if not pattern:
            self._error.setText("Pattern is required.")
            return
        self.result_data = {
            "pattern": pattern,
            "category_id": self._category.currentData(),
            "category_name": self._category.currentText(),
            "priority": self._priority.value(),
            "match_type": self._match_type.currentData(),
        }
        self.accept()


class RulesTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._rules: List[KeywordRule] = []
        self._categories: List[Category] = []
        self._build_ui()
        asyncio.ensure_future(self._load())

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        bar = QHBoxLayout()
        add_btn = QPushButton("+ Add Rule")
        add_btn.setFixedWidth(110)
        add_btn.clicked.connect(self._on_add)
        bar.addWidget(add_btn)
        bar.addStretch()
        layout.addLayout(bar)

        self._table = QTableWidget()
        self._table.setColumnCount(6)
        self._table.setHorizontalHeaderLabels(
            ["Pri.", "Pattern", "Category", "Match Type", "Status", "Actions"]
        )
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Fixed)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(5, QHeaderView.Fixed)
        self._table.setColumnWidth(0, 45)
        self._table.setColumnWidth(5, 160)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        layout.addWidget(self._table)

        # Rule tester
        tester = QGroupBox("Rule Tester — type a description to see what category it matches")
        tester_layout = QHBoxLayout(tester)

        self._test_input = QLineEdit()
        self._test_input.setPlaceholderText('e.g.  "KONSIL KAPIRI GOING"')
        self._test_input.setFixedHeight(32)
        self._test_input.returnPressed.connect(self._on_test)

        test_btn = QPushButton("Test")
        test_btn.setFixedWidth(70)
        test_btn.clicked.connect(self._on_test)

        self._test_result = QLabel("")
        self._test_result.setMinimumWidth(300)
        self._test_result.setWordWrap(True)

        tester_layout.addWidget(self._test_input)
        tester_layout.addWidget(test_btn)
        tester_layout.addWidget(self._test_result)
        layout.addWidget(tester)

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------

    async def _load(self) -> None:
        try:
            self._rules, self._categories = await asyncio.gather(
                get_all_rules(include_inactive=True),
                get_all_categories(include_inactive=False),
            )
            self._refresh()
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to load rules:\n{exc}")

    def _refresh(self) -> None:
        self._table.setRowCount(len(self._rules))
        for i, rule in enumerate(self._rules):
            pri_item = QTableWidgetItem(str(rule.priority))
            pri_item.setTextAlignment(Qt.AlignCenter)
            self._table.setItem(i, 0, pri_item)
            self._table.setItem(i, 1, QTableWidgetItem(rule.pattern))
            self._table.setItem(i, 2, QTableWidgetItem(rule.category_name))
            self._table.setItem(i, 3, QTableWidgetItem(MATCH_LABELS.get(rule.match_type, rule.match_type)))

            status_item = QTableWidgetItem("Active" if rule.active else "Inactive")
            status_item.setForeground(
                QColor("#27ae60") if rule.active else QColor("#e74c3c")
            )
            self._table.setItem(i, 4, status_item)
            self._table.setCellWidget(i, 5, self._make_actions(rule))

        self._table.resizeRowsToContents()

    def _make_actions(self, rule: KeywordRule) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(4)

        edit_btn = QPushButton("Edit")
        edit_btn.setFixedWidth(55)
        edit_btn.clicked.connect(lambda _, r=rule: self._on_edit(r))

        label = "Disable" if rule.active else "Enable"
        toggle_btn = QPushButton(label)
        toggle_btn.setFixedWidth(65)
        toggle_btn.clicked.connect(lambda _, r=rule: self._on_toggle(r))

        lay.addWidget(edit_btn)
        lay.addWidget(toggle_btn)
        return w

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _on_add(self) -> None:
        if not self._categories:
            QMessageBox.information(
                self, "No Categories",
                "Add at least one category in the Categories tab before creating rules."
            )
            return
        dlg = _RuleDialog(categories=self._categories, parent=self)
        if dlg.exec() == QDialog.Accepted:
            asyncio.ensure_future(self._do_add(dlg.result_data))

    async def _do_add(self, data: dict) -> None:
        try:
            await create_rule(
                data["pattern"], data["category_id"],
                data["category_name"], data["priority"], data["match_type"]
            )
            await self._load()
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to create rule:\n{exc}")

    def _on_edit(self, rule: KeywordRule) -> None:
        dlg = _RuleDialog(categories=self._categories, rule=rule, parent=self)
        if dlg.exec() == QDialog.Accepted:
            asyncio.ensure_future(self._do_edit(rule._id, dlg.result_data))

    async def _do_edit(self, rule_id: ObjectId, data: dict) -> None:
        try:
            await update_rule(rule_id, **data)
            await self._load()
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to update rule:\n{exc}")

    def _on_toggle(self, rule: KeywordRule) -> None:
        asyncio.ensure_future(self._do_toggle(rule._id, not rule.active))

    async def _do_toggle(self, rule_id: ObjectId, active: bool) -> None:
        try:
            await toggle_rule(rule_id, active)
            await self._load()
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to update rule:\n{exc}")

    # ------------------------------------------------------------------
    # Rule tester
    # ------------------------------------------------------------------

    def _on_test(self) -> None:
        description = self._test_input.text().strip()
        if not description:
            return
        asyncio.ensure_future(self._do_test(description))

    async def _do_test(self, description: str) -> None:
        try:
            result = await test_description(description)
            if result:
                cat_name, _cat_id, confidence = result
                pct = int(confidence * 100)
                self._test_result.setStyleSheet("color: #27ae60; font-weight: bold;")
                self._test_result.setText(f"Matched: {cat_name}  ({pct}% confidence)")
            else:
                self._test_result.setStyleSheet("color: #e67e22; font-weight: bold;")
                self._test_result.setText("No match — would go to review queue")
        except Exception as exc:
            self._test_result.setStyleSheet("color: #e74c3c;")
            self._test_result.setText(f"Error: {exc}")
