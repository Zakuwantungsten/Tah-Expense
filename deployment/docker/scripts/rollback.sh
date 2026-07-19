#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
TAG="${1:-$(cat .previous-tag 2>/dev/null || true)}"

if [[ -z "$TAG" || ! "$TAG" =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "Usage: sudo scripts/rollback.sh RELEASE_TAG (or retain .previous-tag)" >&2
  exit 2
fi
docker image inspect "tahmeed-api:${TAG}" >/dev/null

CURRENT="$(cat .deployed-tag 2>/dev/null || true)"
export TAHMEED_IMAGE_TAG="$TAG"
docker compose up -d --no-build --remove-orphans

for _ in {1..30}; do
  if curl --fail --silent --show-error --max-time 3 \
    http://127.0.0.1:8000/health/ready >/dev/null; then
    [[ -n "$CURRENT" ]] && printf '%s\n' "$CURRENT" > .previous-tag
    printf '%s\n' "$TAG" > .deployed-tag
    chmod 0600 .deployed-tag .previous-tag 2>/dev/null || true
    echo "Rolled back to $TAG; API is ready."
    exit 0
  fi
  sleep 2
done

echo "Rollback image started but did not become ready; inspect docker compose logs." >&2
exit 1
