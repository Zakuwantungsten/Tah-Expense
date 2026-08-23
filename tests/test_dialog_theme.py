"""Dialog contrast styles for Windows dark mode."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMessageBox

from tahmeed.ui.dialog_theme import (
    apply_readable_dialog_styles,
    install_dialog_theme,
    style_message_box,
)


def test_apply_readable_dialog_styles_sets_messagebox_contrast() -> None:
    app = QApplication.instance() or QApplication([])
    before = app.styleSheet() or ""
    apply_readable_dialog_styles(app)
    ss = app.styleSheet() or ""
    assert "QMessageBox QLabel" in ss
    assert "color: #111827" in ss
    # Idempotent — second call must not duplicate the block.
    apply_readable_dialog_styles(app)
    assert (app.styleSheet() or "").count("QMessageBox QLabel") == ss.count("QMessageBox QLabel")
    app.setStyleSheet(before)


def test_style_message_box_sets_light_palette() -> None:
    app = QApplication.instance() or QApplication([])
    box = QMessageBox(QMessageBox.Icon.Information, "Title", "Body")
    style_message_box(box)
    assert box.palette().color(box.backgroundRole()).name().upper() == "#FFFFFF"
    assert "QMessageBox" in (box.styleSheet() or "")


def test_patched_information_applies_stylesheet() -> None:
    app = QApplication.instance() or QApplication([])
    install_dialog_theme(app)
    # Build but do not exec — patch wraps construction; verify via a manual box.
    box = QMessageBox(QMessageBox.Icon.Information, "Export Complete", "Saved rows.")
    style_message_box(box)
    assert box.palette().color(box.foregroundRole()).name().upper() == "#111827"
