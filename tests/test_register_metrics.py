"""Phase 5 — structured register metrics helper."""

from __future__ import annotations

import logging

import pytest

from tahmeed.services.register_metrics import log_register_event, timed_op


def test_timed_op_logs_duration(caplog):
    caplog.set_level(logging.INFO, logger="tahmeed.register")
    with timed_op("unit_op", rows=3) as meta:
        meta["saved"] = 2
    assert any("unit_op" in r.message for r in caplog.records)
    assert any("duration_ms=" in r.message for r in caplog.records)
    assert any("saved=2" in r.message for r in caplog.records)


def test_timed_op_logs_failure(caplog):
    caplog.set_level(logging.WARNING, logger="tahmeed.register")
    with pytest.raises(RuntimeError):
        with timed_op("boom"):
            raise RuntimeError("x")
    assert any("ok=False" in r.message for r in caplog.records)


def test_log_register_event(caplog):
    caplog.set_level(logging.INFO, logger="tahmeed.register")
    log_register_event("draft_reconcile", skipped_by_id=2, restored=1)
    assert any("draft_reconcile" in r.message for r in caplog.records)
    assert any("skipped_by_id=2" in r.message for r in caplog.records)
