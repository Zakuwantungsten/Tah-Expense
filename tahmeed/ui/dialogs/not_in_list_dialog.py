"""QuickBooks-style “not in list” prompts for Item and Payee.

When Restrict items / Restrict payees is on and the cashier types a value that
is not in the catalog, show:

    "{name}" is not in the Item/Payee list.
    Would you like to add it?

    [Cancel]  [Add Item / Add Payee]

Cancel leaves the field empty. Add opens the create dialog and, on success,
returns the new catalog value for the caller to apply.
"""

from __future__ import annotations

from typing import Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from tahmeed.ui.dialogs.confirm_dialog import ask_confirm
from tahmeed.ui.dialogs.item_dialog import ItemDialog
from tahmeed.ui.widgets.combo_styles import with_combo_popup


_WHITE = "#FFFFFF"
_BG = "#F4F6F8"
_BORDER = "#E5E7EB"
_BLUE = "#0077C5"
_T1 = "#111827"
_T2 = "#6B7280"


def ask_not_in_list(
    parent: Optional[QWidget],
    *,
    entity: str,
    name: str,
) -> bool:
    """Return True if the user chose Add *entity*; False on Cancel/close."""
    label = (entity or "Name").strip() or "Name"
    display = " ".join((name or "").strip().split())
    if not display:
        return False
    return ask_confirm(
        parent,
        title=f"{label} Not Found",
        heading=f'"{display}" is not in the {label} list.',
        body="Would you like to add it?",
        accept_label=f"Add {label}",
        cancel_label="Cancel",
        destructive=False,
        default_cancel=True,
    )


def run_add_item_dialog(
    parent: Optional[QWidget],
    *,
    prefill_name: str = "",
) -> Optional[dict]:
    """Open Add New Item; return ``result_data`` or None if cancelled."""
    dlg = ItemDialog(parent=parent, prefill_name=(prefill_name or "").strip())
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    data = dict(dlg.result_data or {})
    name = (data.get("name") or "").strip()
    if not name:
        QMessageBox.warning(parent, "New Item", "Item name is required.")
        return None
    return data


class _AddPayeeNameDialog(QDialog):
    """Minimal Add Payee form (same fields as Manage Payees / Day Issues)."""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        prefill: str = "",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add Payee")
        self.setMinimumWidth(360)
        self.setStyleSheet(f"background:{_WHITE};")
        self.result_name: Optional[str] = None
        self.result_currency: str = "TZS"

        vl = QVBoxLayout(self)
        vl.setContentsMargins(24, 24, 24, 24)
        vl.setSpacing(12)

        title = QLabel("Add Payee")
        title.setStyleSheet(
            f"color:{_T1};font-size:15px;font-weight:700;font-family:'Segoe UI';"
        )
        vl.addWidget(title)
        tip = QLabel("Added to the Payee list and used in the register Payee column.")
        tip.setStyleSheet(
            f"color:{_T2};font-size:12px;font-family:'Segoe UI';"
        )
        tip.setWordWrap(True)
        vl.addWidget(tip)

        name_lbl = QLabel("Name *")
        name_lbl.setStyleSheet(
            f"color:{_T2};font-size:12px;font-family:'Segoe UI';"
        )
        vl.addWidget(name_lbl)

        self._inp = QLineEdit()
        self._inp.setPlaceholderText("e.g. SPARE PARTS")
        self._inp.setText(" ".join((prefill or "").upper().split()))
        self._inp.setStyleSheet(
            f"QLineEdit{{border:1px solid {_BORDER};border-radius:5px;"
            f"background:{_WHITE};color:{_T1};font-size:13px;"
            "font-family:'Segoe UI';padding:0 8px;"
            "min-height:34px;max-height:34px;}"
            f"QLineEdit:focus{{border-color:{_BLUE};}}"
        )
        self._inp.returnPressed.connect(self._accept)
        vl.addWidget(self._inp)

        cur_lbl = QLabel("Currency")
        cur_lbl.setStyleSheet(
            f"color:{_T2};font-size:12px;font-family:'Segoe UI';"
        )
        vl.addWidget(cur_lbl)
        self._currency = QComboBox()
        self._currency.addItems(["TZS", "USD"])
        self._currency.setCurrentText("TZS")
        self._currency.setFixedHeight(34)
        self._currency.setStyleSheet(with_combo_popup(
            f"QComboBox{{border:1px solid {_BORDER};border-radius:5px;"
            f"background:{_WHITE};color:{_T1};font-size:13px;"
            "font-family:'Segoe UI';padding:0 8px;}"
            f"QComboBox:focus{{border-color:{_BLUE};}}"
            "QComboBox::drop-down{border:none;width:22px;}"
        ))
        vl.addWidget(self._currency)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel = QPushButton("Cancel")
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.setFixedHeight(32)
        cancel.setStyleSheet(
            f"QPushButton{{background:{_WHITE};color:{_T1};border:1px solid {_BORDER};"
            f"border-radius:5px;font-size:12px;font-family:'Segoe UI';padding:0 14px;}}"
            f"QPushButton:hover{{background:{_BG};}}"
        )
        cancel.clicked.connect(self.reject)
        btn_row.addWidget(cancel)
        save = QPushButton("Add Payee")
        save.setCursor(Qt.PointingHandCursor)
        save.setFixedHeight(32)
        save.setStyleSheet(
            f"QPushButton{{background:{_BLUE};color:#FFF;border:none;border-radius:5px;"
            f"font-size:12px;font-weight:600;font-family:'Segoe UI';padding:0 14px;}}"
            f"QPushButton:hover{{background:#005EA3;}}"
        )
        save.clicked.connect(self._accept)
        btn_row.addWidget(save)
        vl.addLayout(btn_row)
        self._inp.setFocus()
        self._inp.selectAll()

    def _accept(self) -> None:
        val = " ".join(self._inp.text().upper().split())
        if not val:
            QMessageBox.warning(self, "Validation", "Name is required.")
            return
        self.result_name = val
        self.result_currency = (self._currency.currentText() or "TZS").strip().upper()
        self.accept()


def run_add_payee_dialog(
    parent: Optional[QWidget],
    *,
    prefill: str = "",
) -> Optional[Tuple[str, str]]:
    """Open Add Payee; return ``(name, currency)`` or None if cancelled."""
    dlg = _AddPayeeNameDialog(parent=parent, prefill=prefill)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    if not dlg.result_name:
        return None
    return dlg.result_name, dlg.result_currency or "TZS"
