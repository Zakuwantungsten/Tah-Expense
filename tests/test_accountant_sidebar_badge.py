import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from tahmeed.ui.accountant.sidebar import _NavItem


def test_badge_visibility_survives_sidebar_collapse_and_expand() -> None:
    app = QApplication.instance() or QApplication([])
    item = _NavItem("verify", "Verify", "mdi.inbox-arrow-down", badge=True)

    item.set_badge(5)
    assert item._badge_lbl is not None
    assert not item._badge_lbl.isHidden()

    item.set_collapsed(True)
    assert item._badge_lbl.isHidden()

    item.set_collapsed(False)
    assert not item._badge_lbl.isHidden()

    item.set_badge(0)
    assert item._badge_lbl.isHidden()
    app.processEvents()
