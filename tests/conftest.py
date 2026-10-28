"""Shared desktop test hygiene."""

from __future__ import annotations

import pytest

# Widgets from finished tests. Letting Python garbage-collect them while Qt
# still has queued callbacks for them crashes later tests (access violation).
_RETIRED_WIDGETS: list = []


def _drain_without_modals(app) -> None:
    """Run callbacks queued by the test while its widgets are still alive."""
    from PySide6.QtWidgets import QDialog, QMessageBox

    saved = {
        (QDialog, "exec"): QDialog.exec,
        (QMessageBox, "exec"): QMessageBox.exec,
        (QMessageBox, "information"): QMessageBox.information,
        (QMessageBox, "warning"): QMessageBox.warning,
        (QMessageBox, "critical"): QMessageBox.critical,
        (QMessageBox, "question"): QMessageBox.question,
    }
    try:
        QDialog.exec = lambda *_a, **_k: 0
        QMessageBox.exec = lambda *_a, **_k: 0
        for name in ("information", "warning", "critical"):
            setattr(QMessageBox, name, staticmethod(lambda *_a, **_k: QMessageBox.Ok))
        QMessageBox.question = staticmethod(lambda *_a, **_k: QMessageBox.No)
        try:
            app.processEvents()
        except Exception:
            # A leftover callback failing here belongs to no test.
            pass
    finally:
        for (cls, name), value in saved.items():
            setattr(cls, name, value)


def _reset_keyboard_modifiers() -> None:
    """Qt keeps the last key *press* modifiers app-wide (releases do not clear
    them), so a simulated Ctrl+C would make later selectRow() calls toggle."""
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication, QWidget

    sink = QWidget()
    QApplication.sendEvent(
        sink, QKeyEvent(QEvent.Type.KeyPress, Qt.Key_unknown, Qt.NoModifier)
    )
    sink.deleteLater()


def pytest_configure(config) -> None:
    config.addinivalue_line(
        "markers", "real_await_ui: keep DayIssuesWidget._await_ui on its QTimer path"
    )


@pytest.fixture(autouse=True)
def _day_issues_dialogs_inline(request, monkeypatch):
    """pytest-asyncio loops do not pump Qt, so an ``_await_ui`` tick never fires."""
    if request.node.get_closest_marker("real_await_ui") is not None:
        return
    try:
        from tahmeed.ui.cashier.day_issues import DayIssuesWidget
    except Exception:
        return

    async def _inline(_self, fn):
        return fn()

    monkeypatch.setattr(DayIssuesWidget, "_await_ui", _inline)


@pytest.fixture(autouse=True)
def _quiesce_qt_widgets(monkeypatch):
    # Depends on monkeypatch so this teardown runs before the test's patches
    # (e.g. a no-op asyncio.ensure_future) are undone.
    yield
    try:
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
    except Exception:
        return
    app = QApplication.instance()
    if app is None:
        return
    _drain_without_modals(app)
    _reset_keyboard_modifiers()
    for widget in QApplication.topLevelWidgets():
        try:
            for timer in widget.findChildren(QTimer):
                timer.stop()
            widget.hide()
        except RuntimeError:
            continue
        _RETIRED_WIDGETS.append(widget)
