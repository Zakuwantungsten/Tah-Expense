"""Description column Excel-style autocomplete (system-wide history)."""

from types import SimpleNamespace

import pytest
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


@pytest.mark.asyncio
async def test_resolve_item_prefers_saved_mapping(monkeypatch) -> None:
    async def mapped(description: str, cache=None):
        return ("cat-id", "Parking")

    monkeypatch.setattr(
        "tahmeed.services.description_mapping_service.resolve_category_for_description",
        mapped,
    )
    name = await cs.resolve_item_name_for_description("PARKING CONGO")
    assert name == "Parking"


@pytest.mark.asyncio
async def test_resolve_item_falls_back_to_verified_history(monkeypatch) -> None:
    async def none_mapped(description: str, cache=None):
        return None

    monkeypatch.setattr(
        "tahmeed.services.description_mapping_service.resolve_category_for_description",
        none_mapped,
    )

    class _Cursor:
        def __init__(self, docs):
            self._docs = docs

        def sort(self, *_a, **_k):
            return self

        def limit(self, *_a, **_k):
            return self

        async def to_list(self, length=1):
            return self._docs[:length]

    class _Coll:
        def __init__(self):
            self.queries = []

        def find(self, query, *_a, **_k):
            self.queries.append(query)
            if query.get("verified") is True:
                return _Cursor([{"item": "Diesel CSH", "category_name": "Diesel CSH"}])
            return _Cursor([])

    coll = _Coll()
    monkeypatch.setattr(cs, "get_db", lambda: SimpleNamespace(transactions=coll))
    name = await cs.resolve_item_name_for_description("DIESEL")
    assert name == "Diesel CSH"
    assert any(q.get("verified") is True for q in coll.queries)
