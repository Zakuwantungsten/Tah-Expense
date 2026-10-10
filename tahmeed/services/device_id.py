"""Client-reported device MAC for soft allowlisting against the API."""

from __future__ import annotations

import uuid


def primary_mac_address() -> str:
    """Return the primary adapter MAC as ``AA:BB:CC:DD:EE:FF`` (best-effort)."""
    node = uuid.getnode()
    hex_value = f"{node & ((1 << 48) - 1):012X}"
    return ":".join(hex_value[i : i + 2] for i in range(0, 12, 2))
