"""Helpers for PyMongo AsyncMongoClient quirks.

``AsyncCollection.aggregate`` is a coroutine that returns a cursor. Motor-style
chaining ``await coll.aggregate(pipeline).to_list(...)`` fails with
``AttributeError: 'coroutine' object has no attribute 'to_list'``.

Test doubles may still return a cursor synchronously; handle both.
"""

from __future__ import annotations

import inspect
from typing import Any


async def aggregate_list(
    collection: Any,
    pipeline: list[dict[str, Any]],
    length: int | None = None,
) -> list[dict[str, Any]]:
    result = collection.aggregate(pipeline)
    cursor = await result if inspect.isawaitable(result) else result
    listed = cursor.to_list(length)
    return await listed if inspect.isawaitable(listed) else listed
