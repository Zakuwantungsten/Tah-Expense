"""QuickBooks-style not-in-list prompt helpers."""

from __future__ import annotations

from tahmeed.services.settings_service import DEFAULTS
from tahmeed.ui.dialogs import not_in_list_dialog as nil


def test_restrict_payees_default_mirrors_items_style() -> None:
    assert "restrict_payees" in DEFAULTS
    assert DEFAULTS["restrict_payees"] is False
    assert DEFAULTS["restrict_items"] is False


def test_ask_not_in_list_cancel(monkeypatch) -> None:
    monkeypatch.setattr(nil, "ask_confirm", lambda *a, **k: False)
    assert nil.ask_not_in_list(None, entity="Item", name="WIDGETS") is False


def test_ask_not_in_list_add(monkeypatch) -> None:
    captured = {}

    def _fake_confirm(parent, **kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr(nil, "ask_confirm", _fake_confirm)
    assert nil.ask_not_in_list(None, entity="Payee", name="  city council  ") is True
    assert captured["title"] == "Payee Not Found"
    assert captured["heading"] == '"city council" is not in the Payee list.'
    assert captured["accept_label"] == "Add Payee"
    assert captured["destructive"] is False


def test_ask_not_in_list_empty_name(monkeypatch) -> None:
    monkeypatch.setattr(nil, "ask_confirm", lambda *a, **k: True)
    assert nil.ask_not_in_list(None, entity="Item", name="   ") is False
