"""Shared QComboBox popup colors — Fusion/OS dark lists otherwise hide dark text."""

from __future__ import annotations

# Append to any filter/field stylesheet that styles QComboBox.
COMBO_POPUP_SS = """
QComboBox QAbstractItemView {
    border: 1px solid #E5E7EB;
    background: #FFFFFF;
    color: #111827;
    selection-background-color: #E8F4FD;
    selection-color: #111827;
    outline: none;
    padding: 2px;
}
QComboBox QLineEdit {
    background: #FFFFFF;
    color: #111827;
    border: none;
    selection-background-color: #DBEAFE;
    selection-color: #111827;
}
"""


def with_combo_popup(stylesheet: str) -> str:
    """Return ``stylesheet`` with readable combo popup rules appended."""
    base = stylesheet.rstrip()
    if "QComboBox QAbstractItemView" in base:
        return f"{base}\n"
    return f"{base}\n{COMBO_POPUP_SS.strip()}\n"
