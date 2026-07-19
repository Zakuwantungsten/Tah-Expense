#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 1 || ! "$1" =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "Usage: sudo scripts/deploy.sh RELEASE_TAG" >&2
  exit 2
fi

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
TAG="$1"
CURRENT="$(cat .deployed-tag 2>/dev/null || true)"

export TAHMEED_IMAGE_TAG="$TAG"
docker compose config --quiet
docker compose build --pull
docker compose run --rm api tahmeed-migrate --check
docker compose run --rm scheduler tahmeed-backup preflight

if [[ -n "$CURRENT" && "$CURRENT" != "$TAG" ]]; then
  printf '%s\n' "$CURRENT" > .previous-tag
fi
docker compose up -d --no-build --remove-orphans

for _ in {1..30}; do
  if curl --fail --silent --show-error --max-time 3 \
    http://127.0.0.1:8000/health/ready >/dev/null; then
    printf '%s\n' "$TAG" > .deployed-tag
    chmod 0600 .deployed-tag
    echo "Deployed $TAG; API is ready."
    exit 0
  fi
  sleep 2
done

echo "Deployment did not become ready." >&2
docker compose ps >&2
if [[ -n "$CURRENT" ]] && docker image inspect "tahmeed-api:${CURRENT}" >/dev/null 2>&1; then
  echo "Restoring previous image ${CURRENT}." >&2
  export TAHMEED_IMAGE_TAG="$CURRENT"
  docker compose up -d --no-build --remove-orphans
else
  echo "No previous image is available; stopping failed services." >&2
  docker compose down
fi
exit 1
