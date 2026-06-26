import asyncio

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QFormLayout, QComboBox,
    QSpinBox, QLabel, QPushButton, QHBoxLayout, QMessageBox,
)
from PySide6.QtCore import Qt

from tahmeed.services.settings_service import get_all_settings, set_setting


class SettingsTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()
        asyncio.ensure_future(self._load())

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.setAlignment(Qt.AlignTop)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setHorizontalSpacing(20)
        form.setVerticalSpacing(14)

        # Default currency (TZS only)
        self._currency = QComboBox()
        self._currency.setFixedWidth(120)
        self._currency.addItems(["TZS"])
        form.addRow("Default currency:", self._currency)

        # Confidence threshold
        threshold_row = QHBoxLayout()
        self._threshold = QSpinBox()
        self._threshold.setRange(50, 100)
        self._threshold.setSuffix(" %")
        self._threshold.setFixedWidth(90)
        self._threshold.valueChanged.connect(self._update_threshold_hint)
        self._threshold_hint = QLabel("")
        self._threshold_hint.setStyleSheet("color: #888; font-size: 12px;")
        threshold_row.addWidget(self._threshold)
        threshold_row.addWidget(self._threshold_hint)
        threshold_row.addStretch()
        form.addRow("Auto-assign threshold:", threshold_row)

        threshold_note = QLabel(
            "Transactions with a confidence score at or above this threshold are auto-assigned to a category.\n"
            "Below it, they go to the review queue for manual confirmation."
        )
        threshold_note.setStyleSheet("color: #888; font-size: 12px;")
        threshold_note.setWordWrap(True)
        form.addRow("", threshold_note)

        # Duplicate check window
        dup_row = QHBoxLayout()
        self._dup_days = QSpinBox()
        self._dup_days.setRange(1, 30)
        self._dup_days.setSuffix(" days")
        self._dup_days.setFixedWidth(100)
        dup_row.addWidget(self._dup_days)
        dup_row.addStretch()
        form.addRow("Duplicate check window:", dup_row)

        dup_note = QLabel(
            "When saving a new entry, the system looks back this many days for an identical "
            "transaction (same truck, amount, item, and description). The cashier is warned "
            "but can still save — the entry is then flagged for the accountant."
        )
        dup_note.setStyleSheet("color: #888; font-size: 12px;")
        dup_note.setWordWrap(True)
        form.addRow("", dup_note)

        outer.addLayout(form)
        outer.addSpacing(20)

        # Save button
        save_row = QHBoxLayout()
        self._save_btn = QPushButton("Save Settings")
        self._save_btn.setFixedWidth(140)
        self._save_btn.clicked.connect(self._on_save)
        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: #27ae60; font-size: 12px;")
        save_row.addWidget(self._save_btn)
        save_row.addWidget(self._status_label)
        save_row.addStretch()
        outer.addLayout(save_row)

    def _update_threshold_hint(self, value: int) -> None:
        if value >= 90:
            hint = "Very strict — many transactions go to review"
        elif value >= 75:
            hint = "Recommended — good balance"
        else:
            hint = "Lenient — most transactions auto-assigned"
        self._threshold_hint.setText(hint)

    async def _load(self) -> None:
        try:
            settings = await get_all_settings()
            currency = settings.get("default_currency", "TZS")
            threshold = int(settings.get("confidence_threshold", 75))
            dup_days = int(settings.get("duplicate_check_days", 5))

            idx = self._currency.findText(currency)
            if idx >= 0:
                self._currency.setCurrentIndex(idx)
            self._threshold.setValue(threshold)
            self._update_threshold_hint(threshold)
            self._dup_days.setValue(dup_days)
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to load settings:\n{exc}")

    def _on_save(self) -> None:
        asyncio.ensure_future(self._do_save())

    async def _do_save(self) -> None:
        try:
            await set_setting("default_currency", self._currency.currentText())
            await set_setting("confidence_threshold", self._threshold.value())
            await set_setting("duplicate_check_days", self._dup_days.value())
            self._status_label.setText("Saved.")
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to save settings:\n{exc}")
