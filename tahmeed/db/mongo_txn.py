"""MongoDB multi-document transaction helper with standalone fallback.

Multi-doc transactions require a replica set (Atlas / initiated RS). Host Mongo
deployed with ``directConnection=true`` is often standalone — in that case we
fall back to sequential writes so the app keeps working.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Optional, TypeVar

from pymongo.errors import OperationFailure

from tahmeed.db.connection import get_client

T = TypeVar("T")

# None = unknown, True/False = probed
_txn_supported: Optional[bool] = None

# Mongo error codes that mean transactions are unavailable on this deployment.
_TXN_UNSUPPORTED_CODES = frozenset({
    20,    # IllegalOperation
    263,   # OperationNotSupportedInTransaction (misuse)
    303,   # NoSuchTransaction
    11600, # InterruptedAtShutdown
    251,   # NoReplicationEnabled / Transaction numbers are only allowed on a replica set
})


def reset_transaction_support_cache() -> None:
    """Test helper — clear the cached replica-set probe."""
    global _txn_supported
    _txn_supported = None


async def supports_transactions() -> bool:
    """Return True when the connected MongoDB topology supports multi-doc txns."""
    global _txn_supported
    if _txn_supported is not None:
        return _txn_supported
    try:
        client = get_client()
        hello = await client.admin.command("hello")
        _txn_supported = bool(hello.get("setName"))
    except Exception:
        _txn_supported = False
    return bool(_txn_supported)


def _is_txn_unsupported(exc: BaseException) -> bool:
    if isinstance(exc, OperationFailure):
        if getattr(exc, "code", None) in _TXN_UNSUPPORTED_CODES:
            return True
        msg = str(exc).lower()
        return (
            "transaction numbers are only allowed on a replica set" in msg
            or "transactions are not supported" in msg
            or "replica set" in msg and "transaction" in msg
        )
    return False


async def run_in_transaction(fn: Callable[[Any], Awaitable[T]]) -> T:
    """Run *fn(session)* inside a Mongo transaction when possible.

    *fn* receives a ClientSession, or ``None`` when falling back to sequential
    writes (standalone Mongo / probe failure / tests without a live client).
    """
    global _txn_supported

    if not await supports_transactions():
        return await fn(None)

    client = get_client()
    entered = False
    try:
        async with await client.start_session() as session:
            async with session.start_transaction():
                entered = True
                return await fn(session)
    except Exception as exc:
        # Fall back only if we never entered the body (no partial writes).
        if entered:
            raise
        if _is_txn_unsupported(exc):
            _txn_supported = False
            return await fn(None)
        if isinstance(exc, (RuntimeError, AttributeError)):
            # Motor/session start can fail in unit tests (closed loop) or
            # odd client states — sequential path is still correct.
            return await fn(None)
        raise


def session_kwargs(session: Any) -> dict:
    """Build kwargs for Motor/PyMongo calls — omit session when None."""
    return {"session": session} if session is not None else {}
