"""Ref_Float refund matcher used by register footer / notes_flag."""

from __future__ import annotations

from tahmeed.services.ref_float import canonical_ref_float, is_refund_float
from tahmeed.ui.cashier.register_delegates import _is_refund_float


def test_is_refund_float_exact_and_colloquial() -> None:
    assert is_refund_float("REFUND TO FLOAT") is True
    assert is_refund_float("refund to float") is True
    assert is_refund_float("  Refund To Float  ") is True
    assert is_refund_float("REFUND TO MY FLOAT") is True
    assert is_refund_float("refund to my float") is True


def test_is_refund_float_typos() -> None:
    assert is_refund_float("REFUND TO FLOA") is True
    assert is_refund_float("RFUND TO FLOAT") is True
    assert is_refund_float("refundtofloat") is True


def test_is_refund_float_rejects_unrelated() -> None:
    assert is_refund_float("") is False
    assert is_refund_float("   ") is False
    assert is_refund_float("OTHER") is False
    assert is_refund_float("refund only") is False
    assert is_refund_float("float only") is False


def test_register_delegate_reexports_matcher() -> None:
    assert _is_refund_float("REFUND TO FLOAT") is True
    assert _is_refund_float("REFUND TO FLOA") is True
    assert _is_refund_float("OTHER") is False


def test_canonical_ref_float() -> None:
    assert canonical_ref_float("refund to my float") == "REFUND TO FLOAT"
    assert canonical_ref_float("REFUND TO FLOA") == "REFUND TO FLOAT"
    assert canonical_ref_float("  other note  ") == "OTHER NOTE"
    assert canonical_ref_float("") == ""
    assert canonical_ref_float("   ") == ""
