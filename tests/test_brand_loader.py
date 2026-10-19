"""Branded horse fade-pulse loader panel."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from tahmeed.ui.widgets.brand_loader import (
    DOT_CYCLE,
    LOGIN_HORSE_WIDTH,
    BrandLoaderPanel,
    load_gallop_frames,
    load_mark_pixmap,
    strip_loader_ellipsis,
)
from tahmeed.ui.widgets.loading_overlay import LoadingOverlay


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_strip_loader_ellipsis() -> None:
    assert strip_loader_ellipsis("Loading…") == "Loading"
    assert strip_loader_ellipsis("Saving rows...") == "Saving rows"
    assert strip_loader_ellipsis("Importing.") == "Importing"
    assert strip_loader_ellipsis("") == "Loading"


def test_mark_pixmap_loads() -> None:
    _app()
    mark = load_mark_pixmap(LOGIN_HORSE_WIDTH)
    assert not mark.isNull()
    # Login horse is wide (mane streaks) — not a square circle badge.
    assert mark.width() == LOGIN_HORSE_WIDTH
    assert mark.width() > mark.height()
    # Compat: old gallop helper now returns a single static frame.
    frames = load_gallop_frames(LOGIN_HORSE_WIDTH)
    assert len(frames) == 1
    assert not frames[0].isNull()


def test_mark_has_transparent_corners() -> None:
    from PySide6.QtGui import QImage

    _app()
    mark = load_mark_pixmap(LOGIN_HORSE_WIDTH)
    img = mark.toImage().convertToFormat(QImage.Format_ARGB32)
    # No opaque circular badge fill in the corners.
    assert img.pixelColor(0, 0).alpha() < 20
    assert img.pixelColor(img.width() - 1, 0).alpha() < 20


def test_brand_loader_cycles_dots() -> None:
    _app()
    panel = BrandLoaderPanel(None, "Loading items…")
    assert panel.base_message() == "Loading items"
    assert panel.display_text() == f"Loading items{DOT_CYCLE[0]}"
    panel._tick_dots()
    assert panel.display_text() == f"Loading items{DOT_CYCLE[1]}"
    panel._tick_dots()
    assert panel.display_text() == f"Loading items{DOT_CYCLE[2]}"
    panel._tick_dots()
    assert panel.display_text() == f"Loading items{DOT_CYCLE[0]}"
    panel.stop()


def test_brand_loader_progress_shows_percent() -> None:
    _app()
    panel = BrandLoaderPanel(None, "Importing…", show_progress=True)
    # isHidden() is reliable before the widget is shown; isVisible() is not.
    assert not panel._bar.isHidden()
    panel.set_progress(value=0, maximum=100)
    assert panel.percent_text() == "0%"
    panel.set_progress(value=42, maximum=100)
    assert panel.percent_text() == "42%"
    panel.set_progress(value=3, maximum=4)
    assert panel.percent_text() == "75%"
    panel.reset_progress()
    # Reset keeps the bar visible (never blank) but clears the percent label.
    assert not panel._bar.isHidden()
    assert panel.percent_text() == ""
    panel.stop()


def test_brand_loader_fade_pulse_changes_opacity() -> None:
    _app()
    panel = BrandLoaderPanel(None, "Loading…")
    panel.start()
    before = panel.fade_opacity()
    for _ in range(12):
        panel._tick_fade()
    after = panel.fade_opacity()
    assert 0.34 <= after <= 1.0
    assert before != after or abs(after - 0.35) < 0.02 or abs(after - 1.0) < 0.02
    panel.stop()
    assert panel.fade_opacity() == pytest.approx(1.0)
    assert panel._horse.graphicsEffect() is None


def test_loading_overlay_uses_brand_panel() -> None:
    _app()
    host = BrandLoaderPanel(None)  # any QWidget host
    host.show()
    overlay = LoadingOverlay(host, "Loading payees…")
    overlay.show_loading("Loading payees…")
    assert overlay._panel.base_message() == "Loading payees"
    assert overlay.isVisible()
    # Overlay path: horse + message only — no progress chrome.
    assert overlay._panel._bar.isHidden()
    assert overlay._panel._pct.isHidden()
    overlay.hide_loading()
    assert not overlay.isVisible()
    host.close()


def test_brand_loader_opaque_for_dialogs() -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QDialog, QVBoxLayout

    _app()
    dlg = QDialog()
    dlg.setWindowTitle("Importing Daily Register")
    lay = QVBoxLayout(dlg)
    panel = BrandLoaderPanel(
        dlg, "Opening import date…", show_progress=True, opaque=True
    )
    lay.addWidget(panel)
    panel.set_progress(value=20, maximum=100)
    dlg.show()
    assert panel._opaque is True
    assert not panel.testAttribute(Qt.WA_TranslucentBackground)
    assert not panel._horse.pixmap().isNull()
    assert not panel._bar.isHidden()
    assert panel.percent_text() == "20%"
    assert "Opening import date" in panel.display_text()
    dlg.close()


def test_brand_loader_and_overlay_never_use_translucent_hwnd() -> None:
    """Regression: translucent / opacity-effect flashed a tiny meedexpe window."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QWidget

    _app()
    host = QWidget()
    host.resize(400, 300)
    host.show()
    overlay = LoadingOverlay(host, "Loading overview…")
    overlay.show_loading("Loading overview…")
    assert not overlay.isWindow()
    assert not overlay._panel.isWindow()
    assert not overlay.testAttribute(Qt.WA_TranslucentBackground)
    assert not overlay._panel.testAttribute(Qt.WA_TranslucentBackground)
    assert not overlay._panel._horse.testAttribute(Qt.WA_TranslucentBackground)
    assert overlay._panel._horse.graphicsEffect() is None
    assert overlay._panel._bar.isHidden()
    assert overlay.parentWidget() is host
    overlay.hide_loading()
    host.close()
