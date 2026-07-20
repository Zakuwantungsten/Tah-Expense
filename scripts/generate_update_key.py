"""Generate an externally held Ed25519 release key and commit-safe public key."""

from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    BestAvailableEncryption,
    Encoding,
    PrivateFormat,
    PublicFormat,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--key-id", required=True)
    parser.add_argument("--private-key", type=Path, required=True)
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    private_path = args.private_key.expanduser().resolve()
    try:
        private_path.relative_to(root)
    except ValueError:
        pass
    else:
        raise SystemExit("Private keys must be stored outside the repository.")
    if private_path.exists():
        raise SystemExit(f"Refusing to overwrite {private_path}")
    password = os.getenv("UPDATE_KEY_PASSWORD", "").encode("utf-8")
    if len(password) < 12:
        raise SystemExit("Set UPDATE_KEY_PASSWORD to at least 12 characters.")

    key = Ed25519PrivateKey.generate()
    private_path.parent.mkdir(parents=True, exist_ok=True)
    private_path.write_bytes(
        key.private_bytes(
            Encoding.PEM,
            PrivateFormat.PKCS8,
            BestAvailableEncryption(password),
        )
    )
    try:
        private_path.chmod(0o600)
    except OSError:
        pass

    public_path = root / "tahmeed" / "assets" / "update_public_keys.json"
    try:
        keys = json.loads(public_path.read_text("utf-8"))
    except FileNotFoundError:
        keys = {}
    if args.key_id in keys:
        private_path.unlink()
        raise SystemExit(f"Key id {args.key_id!r} already exists.")
    raw_public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    keys[args.key_id] = base64.b64encode(raw_public).decode("ascii")
    public_path.write_text(json.dumps(keys, indent=2, sort_keys=True) + "\n", "utf-8")
    print(f"Wrote encrypted private key outside repository: {private_path}")
    print(f"Updated committed public key set: {public_path}")


if __name__ == "__main__":
    main()
