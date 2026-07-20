import base64
import hashlib
import json
import threading
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tahmeed.services import update_service as updater


def manifest_bytes(
    *,
    sequence: int = 2,
    version: str = "1.0.1",
    size: int = 7,
    sha256: str | None = None,
    url: str = "https://updates.example.com/tahmeed/TahmeedExpenseSetup-1.0.1.exe",
) -> bytes:
    digest = sha256 or hashlib.sha256(b"payload").hexdigest()
    return (
        json.dumps(
            {
                "schema_version": 1,
                "channel": "stable",
                "sequence": sequence,
                "version": version,
                "published_at": "2026-07-19T15:00:00Z",
                "minimum_supported_version": "1.0.0",
                "notes": "Security and reliability fixes.",
                "artifact": {
                    "kind": "inno-installer",
                    "name": Path(url).name,
                    "url": url,
                    "size": size,
                    "sha256": digest,
                },
            },
            separators=(",", ":"),
        )
        + "\n"
    ).encode()


def test_parse_manifest_accepts_strict_stable_schema() -> None:
    info = updater.parse_manifest(
        manifest_bytes(), approved_host="updates.example.com"
    )
    assert info.version == "1.0.1"
    assert info.channel == "stable"
    assert info.sequence == 2
    assert info.artifact.kind == "inno-installer"


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value.update(extra=True), "fields"),
        (lambda value: value.update(channel="beta"), "stable"),
        (lambda value: value.update(version="1.0.1-beta.1"), "SemVer"),
        (lambda value: value.update(sequence=0), "sequence"),
        (
            lambda value: value["artifact"].update(
                url="https://evil.example/TahmeedExpenseSetup-1.0.1.exe"
            ),
            "approved HTTPS host",
        ),
        (lambda value: value["artifact"].update(kind="zip"), "Inno"),
    ],
)
def test_parse_manifest_rejects_schema_and_channel_violations(
    mutation, message: str
) -> None:
    value = json.loads(manifest_bytes())
    mutation(value)
    with pytest.raises(updater.UpdateError, match=message):
        updater.parse_manifest(json.dumps(value).encode(), approved_host="updates.example.com")


def test_signature_is_checked_before_manifest_json(monkeypatch) -> None:
    private = Ed25519PrivateKey.generate()
    monkeypatch.setattr(
        updater, "_load_public_keys", lambda: {"release": private.public_key()}
    )
    with pytest.raises(updater.UpdateError, match="signature"):
        updater._verify_detached(b"{not json", b"release:" + base64.b64encode(b"x" * 64))


def test_fetch_rejects_manifest_sequence_rollback(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(
        updater, "UPDATE_MANIFEST_URL", "https://updates.example.com/tahmeed/version.json"
    )
    private = Ed25519PrivateKey.generate()
    raw = manifest_bytes(sequence=9)
    signature = b"release:" + base64.b64encode(private.sign(raw))
    monkeypatch.setattr(
        updater, "_load_public_keys", lambda: {"release": private.public_key()}
    )
    monkeypatch.setattr(
        updater,
        "_bounded_fetch",
        lambda url, limit, host: signature if url.endswith(".sig") else raw,
    )
    assert updater.fetch_update().sequence == 9

    older = manifest_bytes(sequence=8)
    older_signature = b"release:" + base64.b64encode(private.sign(older))
    monkeypatch.setattr(
        updater,
        "_bounded_fetch",
        lambda url, limit, host: older_signature if url.endswith(".sig") else older,
    )
    with pytest.raises(updater.UpdateError, match="older"):
        updater.fetch_update()


class FakeResponse:
    def __init__(self, data: bytes, url: str, content_length: int | None = None):
        self.data = data
        self.url = url
        self.position = 0
        self.headers = {}
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def geturl(self) -> str:
        return self.url

    def read(self, count: int = -1) -> bytes:
        if count < 0:
            count = len(self.data)
        chunk = self.data[self.position : self.position + count]
        self.position += len(chunk)
        return chunk


def test_download_stages_only_exact_verified_installer(monkeypatch, tmp_path) -> None:
    payload = b"verified installer bytes"
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(
        updater, "UPDATE_MANIFEST_URL", "https://updates.example.com/tahmeed/version.json"
    )
    raw = manifest_bytes(size=len(payload), sha256=hashlib.sha256(payload).hexdigest())
    info = updater.parse_manifest(raw, approved_host="updates.example.com")
    monkeypatch.setattr(
        updater,
        "urlopen",
        lambda request, timeout: FakeResponse(
            payload, info.artifact.url, content_length=len(payload)
        ),
    )
    progress = []
    ready = updater.download_update(
        info, progress=lambda current, total: progress.append((current, total))
    )
    assert ready.read_bytes() == payload
    assert updater.recover_ready_update() == ready
    assert progress[-1] == (len(payload), len(payload))
    assert not list(updater.update_root().glob("*.part"))


def test_download_cancellation_removes_partial(monkeypatch, tmp_path) -> None:
    payload = b"verified installer bytes"
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(
        updater, "UPDATE_MANIFEST_URL", "https://updates.example.com/tahmeed/version.json"
    )
    info = updater.parse_manifest(
        manifest_bytes(size=len(payload), sha256=hashlib.sha256(payload).hexdigest()),
        approved_host="updates.example.com",
    )
    monkeypatch.setattr(
        updater,
        "urlopen",
        lambda request, timeout: FakeResponse(payload, info.artifact.url, len(payload)),
    )
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(updater.DownloadCancelled):
        updater.download_update(info, cancel=cancelled)
    assert not list(updater.update_root().glob("*.part"))
    assert updater.recover_ready_update() is None


def test_recovery_deletes_tampered_ready_installer(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    root = updater.update_root()
    root.mkdir(parents=True)
    installer = root / "TahmeedExpenseSetup-1.0.1.exe"
    installer.write_bytes(b"tampered")
    (root / "ready.json").write_text(
        json.dumps(
            {
                "installer": str(installer),
                "size": 7,
                "sha256": hashlib.sha256(b"payload").hexdigest(),
            }
        ),
        "utf-8",
    )
    assert updater.recover_ready_update() is None
    assert not (root / "ready.json").exists()
