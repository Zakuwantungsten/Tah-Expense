"""Create and sign the strict updater manifest for an existing Inno installer."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
    load_pem_private_key,
)


def stable_version(value: str) -> tuple[int, int, int]:
    parts = value.split(".")
    if len(parts) != 3 or any(not p.isdigit() for p in parts):
        raise ValueError(f"Not a stable SemVer: {value}")
    if any(len(p) > 1 and p.startswith("0") for p in parts):
        raise ValueError(f"Not a canonical stable SemVer: {value}")
    return tuple(map(int, parts))  # type: ignore[return-value]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--installer", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--sequence", type=int, required=True)
    parser.add_argument("--minimum-supported-version", required=True)
    parser.add_argument("--notes-file", type=Path, required=True)
    parser.add_argument("--manifest-url", required=True)
    parser.add_argument("--key-id", required=True)
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    stable_version(args.version)
    if stable_version(args.minimum_supported_version) > stable_version(args.version):
        raise SystemExit("Minimum supported version cannot exceed release version.")
    if args.sequence < 1:
        raise SystemExit("Sequence must be positive.")
    parsed_url = urlparse(args.manifest_url)
    if parsed_url.scheme != "https" or not parsed_url.hostname:
        raise SystemExit("Manifest URL must use an HTTPS R2 custom domain.")
    artifact_url = urljoin(args.manifest_url, args.installer.name)
    if urlparse(artifact_url).hostname != parsed_url.hostname:
        raise SystemExit("Artifact and manifest must use the same host.")

    password = os.getenv("UPDATE_KEY_PASSWORD", "").encode("utf-8")
    if not password:
        raise SystemExit("Set UPDATE_KEY_PASSWORD for the encrypted private key.")
    private_key = load_pem_private_key(args.private_key.read_bytes(), password=password)
    raw_public = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    root = Path(__file__).resolve().parent.parent
    public_keys = json.loads(
        (root / "tahmeed/assets/update_public_keys.json").read_text("utf-8")
    )
    expected_public = base64.b64decode(public_keys.get(args.key_id, ""), validate=True)
    if raw_public != expected_public:
        raise SystemExit("Private key does not match the committed key id.")

    digest = hashlib.sha256()
    size = 0
    with args.installer.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    notes = args.notes_file.read_text("utf-8")
    if len(notes) > 20_000:
        raise SystemExit("Release notes exceed 20,000 characters.")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output_dir / "version.json"
    manifest = {
        "schema_version": 1,
        "channel": "stable",
        "sequence": args.sequence,
        "version": args.version,
        "published_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "minimum_supported_version": args.minimum_supported_version,
        "notes": notes,
        "artifact": {
            "kind": "inno-installer",
            "name": args.installer.name,
            "url": artifact_url,
            "size": size,
            "sha256": digest.hexdigest(),
        },
    }
    if manifest_path.exists():
        try:
            prior = json.loads(manifest_path.read_text("utf-8"))
            prior_sequence = int(prior["sequence"])
            if args.sequence < prior_sequence:
                raise SystemExit("Sequence must not be lower than the prior local manifest.")
            if args.sequence == prior_sequence:
                comparable_prior = dict(prior)
                comparable_new = dict(manifest)
                comparable_prior.pop("published_at", None)
                comparable_new.pop("published_at", None)
                if comparable_prior != comparable_new:
                    raise SystemExit(
                        "Reusing a sequence is allowed only for an identical local draft."
                    )
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            raise SystemExit("Existing local manifest is invalid; archive it explicitly.") from exc
    raw = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    manifest_path.write_bytes(raw)
    signature = private_key.sign(raw)
    (args.output_dir / "version.json.sig").write_text(
        f"{args.key_id}:{base64.b64encode(signature).decode('ascii')}\n",
        "ascii",
    )
    print(f"SHA256 {digest.hexdigest()}  {args.installer.name}")


if __name__ == "__main__":
    main()
