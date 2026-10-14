"""Lightweight structured logs for register save / outbox / reconcile."""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Iterator

logger = logging.getLogger("tahmeed.register")


@contextmanager
def timed_op(op: str, **fields) -> Iterator[dict]:
    """Time a register operation and log duration + outcome fields."""
    started = time.perf_counter()
    meta: dict = {"op": op, **fields}
    try:
        yield meta
        meta.setdefault("ok", True)
    except Exception as exc:
        meta["ok"] = False
        meta["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        meta["duration_ms"] = int((time.perf_counter() - started) * 1000)
        _log_event(meta)


def _log_event(meta: dict) -> None:
    parts = [f"{k}={meta[k]}" for k in sorted(meta) if k != "op"]
    msg = f"{meta.get('op', 'register')} " + " ".join(parts)
    if meta.get("ok", True):
        logger.info(msg)
    else:
        logger.warning(msg)


def log_register_event(op: str, **fields) -> None:
    """One-shot register metric line (no timing)."""
    meta = {"op": op, "ok": True, **fields}
    _log_event(meta)
