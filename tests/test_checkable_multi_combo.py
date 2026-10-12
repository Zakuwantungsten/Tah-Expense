"""Popup sizing for long fuel-station file names in CheckableMultiCombo."""

from time import monotonic

from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import QApplication

from tahmeed.ui.widgets.checkable_multi_combo import (
    CheckableMultiCombo,
    _POPUP_MAX_WIDTH,
    desired_popup_width,
)


def test_desired_popup_width_grows_for_long_file_names() -> None:
    _app = QApplication.instance() or QApplication([])
    fm = QFontMetrics(QFont("Segoe UI", 12))
    short = desired_popup_width(
        ["All File Names"], combo_width=180, font_metrics=fm,
    )
    long_name = (
        "INFINITY DIESEL RECONCILIATION 16th - 31st March 2026.xlsx"
    )
    long = desired_popup_width(
        ["All File Names", long_name], combo_width=180, font_metrics=fm,
    )
    assert short >= 180
    assert long > short
    assert long <= _POPUP_MAX_WIDTH
    assert _app is not None


def test_desired_popup_width_caps_extreme_names() -> None:
    _app = QApplication.instance() or QApplication([])
    fm = QFontMetrics(QFont("Segoe UI", 12))
    huge = "X" * 400
    width = desired_popup_width([huge], combo_width=180, font_metrics=fm)
    assert width == _POPUP_MAX_WIDTH
    assert _app is not None


def test_file_name_options_keep_full_tooltip() -> None:
    _app = QApplication.instance() or QApplication([])
    combo = CheckableMultiCombo("All File Names", noun_plural="file names")
    name = "Lake Zambia Diesel 1st - 15th April 2026 workbook.xlsx"
    combo.set_options([name], emit=False)
    item = combo.model().item(1)
    assert item is not None
    assert item.text() == name
    assert item.toolTip() == name
    assert _app is not None


def test_hide_popup_ignored_immediately_after_show() -> None:
    """Opening click must not dismiss the list (press-show / release-hide race)."""
    _app = QApplication.instance() or QApplication([])
    combo = CheckableMultiCombo("All Items", noun_plural="items")
    combo.set_options(["Fuel", "Parking"], emit=False)
    combo.showPopup()
    assert combo._popup_open
    combo.hidePopup()  # same-tick dismiss attempt
    assert combo._popup_open
    combo._block_hide_until = monotonic() - 1
    combo.hidePopup()
    assert not combo._popup_open
    assert _app is not None


def test_set_options_deferred_while_popup_open() -> None:
    _app = QApplication.instance() or QApplication([])
    combo = CheckableMultiCombo("All Items", noun_plural="items")
    combo.set_options(["A", "B"], emit=False)
    combo.showPopup()
    combo.set_options(["C", "D"], emit=False)
    # Still showing original rows; rebuild waits until hide is allowed.
    assert combo.model().item(1).text() == "A"
    assert combo._pending_options is not None
    combo._block_hide_until = monotonic() - 1
    combo.hidePopup()
    assert combo.model().item(1).text() == "C"
    assert combo.model().item(2).text() == "D"
    assert combo._pending_options is None
    assert _app is not None
