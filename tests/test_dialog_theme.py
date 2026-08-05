"""Dialog contrast styles for Windows dark mode."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from tahmeed.ui.dialog_theme import apply_readable_dialog_styles


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
    # Restore prior stylesheet for other tests in the same process.
    app.setStyleSheet(before)
