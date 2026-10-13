"""Ref_Float refund matcher used by register footer / notes_flag."""

from __future__ import annotations

from tahmeed.ui.cashier.register_delegates import _is_refund_float


def test_is_refund_float_exact_and_colloquial() -> None:
    assert _is_refund_float("REFUND TO FLOAT") is True
    assert _is_refund_float("refund to float") is True
    assert _is_refund_float("  Refund To Float  ") is True
    assert _is_refund_float("REFUND TO MY FLOAT") is True
    assert _is_refund_float("refund to my float") is True


def test_is_refund_float_rejects_unrelated() -> None:
    assert _is_refund_float("") is False
    assert _is_refund_float("   ") is False
    assert _is_refund_float("OTHER") is False
    assert _is_refund_float("refund only") is False
    assert _is_refund_float("float only") is False
