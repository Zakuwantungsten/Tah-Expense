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
    before = panel._opacity.opacity()
    for _ in range(12):
        panel._tick_fade()
    after = panel._opacity.opacity()
    assert 0.34 <= after <= 1.0
    assert before != after or abs(after - 0.35) < 0.02 or abs(after - 1.0) < 0.02
    panel.stop()
    assert panel._opacity.opacity() == pytest.approx(1.0)


def test_loading_overlay_uses_brand_panel() -> None:
    _app()
    host = BrandLoaderPanel(None)  # any QWidget host
    host.show()
    overlay = LoadingOverlay(host, "Loading payees…")
    overlay.show_loading("Loading payees…")
    assert overlay._panel.base_message() == "Loading payees"
    assert overlay.isVisible()
    overlay.hide_loading()
    assert not overlay.isVisible()
    host.close()
