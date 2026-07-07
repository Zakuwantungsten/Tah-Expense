"""Check for app updates from a remote version manifest."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from urllib.error import URLError
from urllib.request import Request, urlopen

from tahmeed.config import APP_VERSION, UPDATE_MANIFEST_URL


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    download_url: str
    release_notes: str


def _parse_version(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for piece in version.strip().lstrip("v").split("."):
        digits = ""
        for ch in piece:
            if ch.isdigit():
                digits += ch
            else:
                break
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def is_newer_version(remote: str, local: str) -> bool:
    return _parse_version(remote) > _parse_version(local)


def _fetch_manifest() -> dict:
    req = Request(
        UPDATE_MANIFEST_URL,
        headers={"User-Agent": f"TahmeedExpense/{APP_VERSION}"},
    )
    with urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


async def check_for_update() -> UpdateInfo | None:
    """Return update info when a newer version is published, else None."""
    if not UPDATE_MANIFEST_URL:
        return None

    try:
        data = await asyncio.get_event_loop().run_in_executor(None, _fetch_manifest)
    except (URLError, OSError, json.JSONDecodeError, TimeoutError):
        return None

    remote_version = str(data.get("version", "")).strip()
    if not remote_version or not is_newer_version(remote_version, APP_VERSION):
        return None

    return UpdateInfo(
        version=remote_version,
        download_url=str(data.get("download_url", "")).strip(),
        release_notes=str(data.get("release_notes", "")).strip(),
    )
