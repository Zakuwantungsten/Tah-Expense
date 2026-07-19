#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

test "$(stat -c '%U:%G:%a' tahmeed-api.env)" = "root:root:600" || {
  echo "tahmeed-api.env must be root:root mode 0600" >&2
  exit 1
}

docker compose config --quiet
docker compose run --rm api tahmeed-migrate --check

if [[ "${1:-}" == "--apply-indexes" ]]; then
  docker compose run --rm api tahmeed-migrate
else
  echo "Duplicate check passed. Re-run with --apply-indexes during the approved maintenance window."
  exit 0
fi

docker compose run --rm scheduler tahmeed-backup preflight
