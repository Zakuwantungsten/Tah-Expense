"""Fluent ConfirmDialog (Clear Table mockup)."""

from __future__ import annotations

import sys

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from tahmeed.ui.dialogs.confirm_dialog import (
    ConfirmDialog,
    _split_message,
    ask_confirm,
    ask_standard_question,
)


@pytest.fixture
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def test_split_message_three_parts():
    heading, body, note = _split_message(
        "Clear this table?\n\n"
        "All unsaved rows and typed data will be removed.\n\n"
        "Saved entries on this register will not be affected."
    )
    assert heading == "Clear this table?"
    assert "unsaved rows" in body
    assert "Saved entries" in note


def _find_card(dlg: ConfirmDialog):
    from PySide6.QtWidgets import QWidget

    for child in dlg.findChildren(QWidget):
        if child.objectName() == "confirmCard":
            return child
    return None


def test_confirm_dialog_clear_table_labels(qapp, monkeypatch):
    monkeypatch.setattr(
        "tahmeed.ui.dialogs.confirm_dialog.is_dark_theme",
        lambda: False,
    )
    dlg = ConfirmDialog(
        None,
        title="Clear Table",
        heading="Clear this table?",
        body="All unsaved rows and typed data will be removed.",
        note="Saved entries on this register will not be affected.",
        accept_label="Clear table",
        cancel_label="Cancel",
        destructive=True,
        default_cancel=True,
    )
    assert dlg._cancel_btn.text() == "Cancel"
    assert dlg._accept_btn.text() == "Clear table"
    assert dlg._cancel_btn.isDefault()
    card = _find_card(dlg)
    assert card is not None
    assert card.width() == 380
    assert "#C42B1C" in dlg._accept_btn.styleSheet()
    dlg.close()


def test_confirm_dialog_dark_colors(qapp, monkeypatch):
    monkeypatch.setattr(
        "tahmeed.ui.dialogs.confirm_dialog.is_dark_theme",
        lambda: True,
    )
    dlg = ConfirmDialog(
        None,
        title="Clear Table",
        heading="Clear this table?",
        body="Body",
        note="Note",
        accept_label="Clear table",
    )
    card = _find_card(dlg)
    assert card is not None
    assert "#2B2B2B" in (card.styleSheet() or "")
    assert "#C42B1C" in dlg._accept_btn.styleSheet()
    dlg.close()


def test_ask_confirm_accept_reject(qapp, monkeypatch):
    monkeypatch.setattr(
        ConfirmDialog,
        "exec",
        lambda self: QDialog.DialogCode.Accepted,
    )
    assert ask_confirm(
        None,
        title="Clear Table",
        heading="Clear this table?",
        accept_label="Clear table",
    ) is True

    monkeypatch.setattr(
        ConfirmDialog,
        "exec",
        lambda self: QDialog.DialogCode.Rejected,
    )
    assert ask_confirm(
        None,
        title="Clear Table",
        heading="Clear this table?",
        accept_label="Clear table",
    ) is False


def test_ask_standard_question_maps_cancel_to_no(qapp, monkeypatch):
    monkeypatch.setattr(
        "tahmeed.ui.dialogs.confirm_dialog.ask_confirm",
        lambda *a, **k: False,
    )
    result = ask_standard_question(
        None,
        "Clear Table",
        "Clear this table?\n\nAll unsaved rows will be removed.",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    assert result == QMessageBox.No

    monkeypatch.setattr(
        "tahmeed.ui.dialogs.confirm_dialog.ask_confirm",
        lambda *a, **k: True,
    )
    result = ask_standard_question(
        None,
        "Clear Table",
        "Clear this table?",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    assert result == QMessageBox.Yes
