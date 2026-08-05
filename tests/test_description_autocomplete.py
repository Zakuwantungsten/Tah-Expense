"""Description column Excel-style autocomplete (system-wide history)."""

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLineEdit, QWidget

from tahmeed.services import cashier_service as cs
from tahmeed.ui.widgets.truck_autocomplete import TruckLineEdit


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _seed_descriptions(values: dict[str, int]) -> None:
    cs._desc_counts = dict(values)
    cs._rebuild_desc_ranked()


def test_search_descriptions_sync_prefix_frequency_order() -> None:
    cs.invalidate_description_cache()
    _seed_descriptions({
        "PARKING CONGO": 5,
        "PARKING ZAMBIA": 2,
        "DIESEL CSH": 10,
    })
    assert cs.search_descriptions_sync("park") == ["PARKING CONGO", "PARKING ZAMBIA"]
    assert cs.search_descriptions_sync("DIE") == ["DIESEL CSH"]
    assert cs.search_descriptions_sync("xyz") == []
    cs.invalidate_description_cache()
    assert cs.search_descriptions_sync("park") is None


def test_remember_description_boosts_same_session() -> None:
    cs.invalidate_description_cache()
    _seed_descriptions({"OLD FEE": 3})
    cs.remember_description("new parking fee")
    assert cs.search_descriptions_sync("new") == ["NEW PARKING FEE"]
    assert "NEW PARKING FEE" in (cs.search_descriptions_sync("N") or [])
    cs.invalidate_description_cache()


def test_description_editor_does_not_use_fleet_when_sync_fn_set(monkeypatch) -> None:
    """Regression: warm fleet cache must not poison description suggestions."""
    _app()
    calls = {"fleet": 0}

    def fake_fleet(prefix: str, limit: int = 10):
        calls["fleet"] += 1
        return ["T688 EAF"]

    monkeypatch.setattr(
        "tahmeed.services.truck_service.search_fleet_sync",
        fake_fleet,
    )
    cs.invalidate_description_cache()
    _seed_descriptions({"PARKING CONGO": 1})

    ed = TruckLineEdit(
        fetch_fn=cs.search_descriptions,
        sync_fn=cs.search_descriptions_sync,
    )
    assert ed._sync_suggestions("PAR") == ["PARKING CONGO"]
    assert calls["fleet"] == 0


def test_description_inline_preview_and_tab_accept() -> None:
    _app()
    cs.invalidate_description_cache()
    _seed_descriptions({"PARKING CONGO": 4, "PARKING ZAMBIA": 1})

    host = QWidget()
    layout = QHBoxLayout(host)
    ed = TruckLineEdit(
        fetch_fn=cs.search_descriptions,
        sync_fn=cs.search_descriptions_sync,
    )
    nxt = QLineEdit()
    layout.addWidget(ed)
    layout.addWidget(nxt)
    QWidget.setTabOrder(ed, nxt)
    host.show()
    ed.setFocus()
    QApplication.processEvents()

    QTest.keyClicks(ed, "PAR")
    QTest.qWait(80)
    QApplication.processEvents()

    assert ed.text().upper().startswith("PAR")
    assert ed._preview_active is True
    assert "PARKING" in ed.text().upper()

    QTest.keyClick(ed, Qt.Key_Tab)
    QTest.qWait(80)
    QApplication.processEvents()

    assert ed.text() == "PARKING CONGO"
    assert not ed._completer.popup().isVisible()
    assert nxt.hasFocus()
    cs.invalidate_description_cache()
