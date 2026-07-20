"""Signed, stable-channel desktop update checks and staged downloads."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from tahmeed.config import APP_NAME, APP_VERSION, UPDATE_MANIFEST_URL

MANIFEST_LIMIT = 128 * 1024
SIGNATURE_LIMIT = 4096
ARTIFACT_LIMIT = 2 * 1024 * 1024 * 1024
CHUNK_SIZE = 256 * 1024
NETWORK_TIMEOUT = 15
SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MANIFEST_KEYS = {
    "schema_version",
    "channel",
    "sequence",
    "version",
    "published_at",
    "minimum_supported_version",
    "notes",
    "artifact",
}
ARTIFACT_KEYS = {"kind", "name", "url", "size", "sha256"}


class UpdateError(RuntimeError):
    """A safe, user-displayable updater failure."""


class DownloadCancelled(UpdateError):
    pass


@dataclass(frozen=True)
class Artifact:
    kind: str
    name: str
    url: str
    size: int
    sha256: str


@dataclass(frozen=True)
class UpdateInfo:
    schema_version: int
    channel: str
    sequence: int
    version: str
    published_at: str
    minimum_supported_version: str
    notes: str
    artifact: Artifact

    @property
    def required(self) -> bool:
        return _semver_tuple(APP_VERSION) < _semver_tuple(
            self.minimum_supported_version
        )

    # Compatibility for callers that previously opened a browser.
    @property
    def download_url(self) -> str:
        return self.artifact.url

    @property
    def release_notes(self) -> str:
        return self.notes


def _semver_tuple(version: str) -> tuple[int, int, int]:
    match = SEMVER_RE.fullmatch(version)
    if not match:
        raise UpdateError(f"Invalid stable SemVer: {version!r}")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def is_newer_version(remote: str, local: str) -> bool:
    return _semver_tuple(remote) > _semver_tuple(local)


def update_root() -> Path:
    base = os.getenv("LOCALAPPDATA")
    if not base:
        base = str(Path.home() / "AppData" / "Local")
    return Path(base) / APP_NAME / "updates"


def _resource_path(name: str) -> Path:
    import sys

    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "tahmeed" / "assets" / name
    return Path(__file__).resolve().parent.parent / "assets" / name


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def _state() -> dict:
    try:
        value = json.loads((update_root() / "state.json").read_text("utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_sequence(sequence: int) -> None:
    state = _state()
    try:
        highest = int(state.get("highest_sequence", 0))
    except (TypeError, ValueError):
        highest = 0
    state["highest_sequence"] = max(highest, sequence)
    _atomic_json(update_root() / "state.json", state)


def _validate_https(url: str, approved_host: str) -> None:
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.hostname.lower() != approved_host.lower()
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise UpdateError("Update URL is not on the approved HTTPS host")


def _bounded_fetch(url: str, limit: int, approved_host: str) -> bytes:
    _validate_https(url, approved_host)
    request = Request(
        url,
        headers={
            "Accept": "application/json, application/octet-stream",
            "User-Agent": f"TahmeedExpense/{APP_VERSION}",
        },
    )
    with urlopen(request, timeout=NETWORK_TIMEOUT) as response:
        _validate_https(response.geturl(), approved_host)
        length = response.headers.get("Content-Length")
        if length and int(length) > limit:
            raise UpdateError("Update response exceeds its size limit")
        body = response.read(limit + 1)
    if len(body) > limit:
        raise UpdateError("Update response exceeds its size limit")
    return body


def _load_public_keys() -> dict[str, Ed25519PublicKey]:
    try:
        values = json.loads(_resource_path("update_public_keys.json").read_text("utf-8"))
        if not isinstance(values, dict) or not values:
            raise ValueError
        return {
            key_id: Ed25519PublicKey.from_public_bytes(
                base64.b64decode(encoded, validate=True)
            )
            for key_id, encoded in values.items()
            if isinstance(key_id, str) and isinstance(encoded, str)
        }
    except (OSError, ValueError, TypeError) as exc:
        raise UpdateError("Committed update public keys are invalid") from exc


def _verify_detached(manifest_bytes: bytes, signature_bytes: bytes) -> None:
    # The detached signature format is ASCII: <key-id>:<base64-signature>.
    try:
        key_id, encoded = signature_bytes.decode("ascii").strip().split(":", 1)
        signature = base64.b64decode(encoded, validate=True)
        if len(signature) != 64:
            raise ValueError
        public_key = _load_public_keys()[key_id]
        public_key.verify(signature, manifest_bytes)
    except (UnicodeError, ValueError, KeyError, InvalidSignature) as exc:
        raise UpdateError("Update manifest signature is invalid") from exc


def parse_manifest(raw: bytes, *, approved_host: str) -> UpdateInfo:
    """Strictly parse a manifest. Call only after signature verification."""
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise UpdateError("Update manifest JSON is invalid") from exc
    if not isinstance(data, dict) or set(data) != MANIFEST_KEYS:
        raise UpdateError("Update manifest fields do not match schema version 1")
    artifact = data.get("artifact")
    if not isinstance(artifact, dict) or set(artifact) != ARTIFACT_KEYS:
        raise UpdateError("Update artifact fields do not match schema version 1")
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise UpdateError("Unsupported update manifest schema")
    if data["channel"] != "stable":
        raise UpdateError("Only the stable update channel is accepted")
    if type(data["sequence"]) is not int or data["sequence"] < 1:
        raise UpdateError("Update sequence must be a positive integer")
    version = data["version"]
    minimum = data["minimum_supported_version"]
    if not isinstance(version, str) or not isinstance(minimum, str):
        raise UpdateError("Update versions must be strings")
    _semver_tuple(version)
    _semver_tuple(minimum)
    if _semver_tuple(minimum) > _semver_tuple(version):
        raise UpdateError("Minimum supported version exceeds published version")
    if not isinstance(data["notes"], str) or len(data["notes"]) > 20_000:
        raise UpdateError("Update notes are invalid")
    try:
        published = datetime.fromisoformat(data["published_at"].replace("Z", "+00:00"))
        if published.tzinfo is None:
            raise ValueError
    except (AttributeError, ValueError) as exc:
        raise UpdateError("published_at must be an ISO-8601 timestamp with timezone") from exc
    if artifact["kind"] != "inno-installer":
        raise UpdateError("Only the Inno Setup installer artifact is accepted")
    if (
        not isinstance(artifact["name"], str)
        or Path(artifact["name"]).name != artifact["name"]
        or not artifact["name"].endswith(".exe")
    ):
        raise UpdateError("Installer artifact name is invalid")
    if type(artifact["size"]) is not int or not 1 <= artifact["size"] <= ARTIFACT_LIMIT:
        raise UpdateError("Installer artifact size is invalid")
    if not isinstance(artifact["sha256"], str) or not SHA256_RE.fullmatch(
        artifact["sha256"]
    ):
        raise UpdateError("Installer SHA-256 is invalid")
    if not isinstance(artifact["url"], str):
        raise UpdateError("Installer URL is invalid")
    _validate_https(artifact["url"], approved_host)
    if Path(urlparse(artifact["url"]).path).name != artifact["name"]:
        raise UpdateError("Installer URL name does not match artifact name")
    return UpdateInfo(
        schema_version=1,
        channel="stable",
        sequence=data["sequence"],
        version=version,
        published_at=data["published_at"],
        minimum_supported_version=minimum,
        notes=data["notes"],
        artifact=Artifact(**artifact),
    )


def fetch_update() -> UpdateInfo | None:
    if not UPDATE_MANIFEST_URL:
        return None
    manifest_url = UPDATE_MANIFEST_URL.strip()
    parsed = urlparse(manifest_url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.query
        or Path(parsed.path).name != "version.json"
    ):
        raise UpdateError("UPDATE_MANIFEST_URL must be an HTTPS version.json URL")
    host = parsed.hostname
    raw = _bounded_fetch(manifest_url, MANIFEST_LIMIT, host)
    signature = _bounded_fetch(f"{manifest_url}.sig", SIGNATURE_LIMIT, host)
    _verify_detached(raw, signature)  # Deliberately before JSON parsing.
    info = parse_manifest(raw, approved_host=host)
    try:
        highest = int(_state().get("highest_sequence", 0))
    except (TypeError, ValueError):
        highest = 0
    if info.sequence < highest:
        raise UpdateError("Update manifest sequence is older than one already seen")
    _save_sequence(info.sequence)
    return info if is_newer_version(info.version, APP_VERSION) else None


async def check_for_update() -> UpdateInfo | None:
    """Perform the bounded network check without blocking the Qt event loop."""
    if not UPDATE_MANIFEST_URL:
        return None
    return await asyncio.to_thread(fetch_update)


def download_update(
    info: UpdateInfo,
    *,
    progress: Callable[[int, int], None] | None = None,
    cancel: threading.Event | None = None,
) -> Path:
    """Stream, verify, and atomically mark an installer ready."""
    root = update_root()
    root.mkdir(parents=True, exist_ok=True)
    part = root / f"{info.artifact.name}.part"
    ready_installer = root / info.artifact.name
    ready_metadata = root / "ready.json"
    parsed_manifest = urlparse(UPDATE_MANIFEST_URL)
    host = parsed_manifest.hostname
    if parsed_manifest.scheme != "https" or not host:
        raise UpdateError("UPDATE_MANIFEST_URL must be HTTPS")
    _validate_https(info.artifact.url, host)
    digest = hashlib.sha256()
    received = 0
    request = Request(
        info.artifact.url,
        headers={"User-Agent": f"TahmeedExpense/{APP_VERSION}"},
    )
    try:
        with urlopen(request, timeout=NETWORK_TIMEOUT) as response, part.open("wb") as out:
            _validate_https(response.geturl(), host)
            length = response.headers.get("Content-Length")
            if length is not None and int(length) != info.artifact.size:
                raise UpdateError("Installer Content-Length does not match manifest")
            while True:
                if cancel is not None and cancel.is_set():
                    raise DownloadCancelled("Update download cancelled")
                chunk = response.read(min(CHUNK_SIZE, info.artifact.size - received + 1))
                if not chunk:
                    break
                received += len(chunk)
                if received > info.artifact.size:
                    raise UpdateError("Installer is larger than its signed size")
                digest.update(chunk)
                out.write(chunk)
                if progress:
                    progress(received, info.artifact.size)
            out.flush()
            os.fsync(out.fileno())
        if received != info.artifact.size:
            raise UpdateError("Installer size does not match signed manifest")
        if digest.hexdigest() != info.artifact.sha256:
            raise UpdateError("Installer SHA-256 does not match signed manifest")
        os.replace(part, ready_installer)
        _atomic_json(
            ready_metadata,
            {
                "version": info.version,
                "sequence": info.sequence,
                "installer": str(ready_installer),
                "size": info.artifact.size,
                "sha256": info.artifact.sha256,
                "install_on_exit": False,
            },
        )
        return ready_installer
    except Exception:
        try:
            part.unlink()
        except FileNotFoundError:
            pass
        raise


def recover_ready_update() -> Path | None:
    """Remove abandoned partials and return a still-valid ready installer."""
    root = update_root()
    if root.exists():
        for partial in root.glob("*.part"):
            try:
                partial.unlink()
            except OSError:
                pass
    metadata_path = root / "ready.json"
    try:
        metadata = json.loads(metadata_path.read_text("utf-8"))
        installer = Path(metadata["installer"])
        if installer.parent.resolve() != root.resolve() or not installer.is_file():
            raise ValueError
        if installer.stat().st_size != int(metadata["size"]):
            raise ValueError
        digest = hashlib.sha256()
        with installer.open("rb") as handle:
            for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
                digest.update(chunk)
        if digest.hexdigest() != metadata["sha256"]:
            raise ValueError
        return installer
    except (OSError, ValueError, KeyError, TypeError):
        try:
            if "installer" in locals() and installer.parent.resolve() == root.resolve():
                installer.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            metadata_path.unlink()
        except FileNotFoundError:
            pass
        return None


def set_install_on_exit(enabled: bool) -> None:
    path = update_root() / "ready.json"
    try:
        metadata = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as exc:
        raise UpdateError("No verified update is ready") from exc
    metadata["install_on_exit"] = bool(enabled)
    _atomic_json(path, metadata)


def install_on_exit_path() -> Path | None:
    installer = recover_ready_update()
    if installer is None:
        return None
    try:
        metadata = json.loads((update_root() / "ready.json").read_text("utf-8"))
        return installer if metadata.get("install_on_exit") is True else None
    except (OSError, ValueError):
        return None
