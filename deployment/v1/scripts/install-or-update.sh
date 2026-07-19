#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run as root: sudo $0 [source-tree] [version]" >&2
  exit 1
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ASSET_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
DEFAULT_SOURCE="$(cd -- "${ASSET_DIR}/../.." && pwd)"
SOURCE_DIR="${1:-${DEFAULT_SOURCE}}"
VERSION="${2:-$(date -u +%Y%m%dT%H%M%SZ)}"
APP_ROOT=/opt/tahmeed-api
RELEASE_DIR="${APP_ROOT}/releases/${VERSION}"
CURRENT_LINK="${APP_ROOT}/current"
PREVIOUS_LINK="${APP_ROOT}/previous"
ENV_FILE=/etc/tahmeed-api/tahmeed-api.env
KEEP_RELEASES="${TAHMEED_KEEP_RELEASES:-5}"
BUILD_DIR=
OLD_CURRENT=
RELEASE_CREATED=0
INSTALL_COMPLETE=0

cleanup() {
  local status=$?
  set +e
  if [[ -n "${BUILD_DIR}" && -d "${BUILD_DIR}" ]]; then
    rm -rf -- "${BUILD_DIR}"
  fi
  if (( status != 0 && RELEASE_CREATED == 1 && INSTALL_COMPLETE == 0 )); then
    if [[ -n "${OLD_CURRENT}" && -d "${OLD_CURRENT}" ]]; then
      activate_release "${OLD_CURRENT}"
      systemctl restart tahmeed-api.service
    elif [[ "$(readlink -f "${CURRENT_LINK}" 2>/dev/null || true)" == "${RELEASE_DIR}" ]]; then
      rm -f -- "${CURRENT_LINK}"
      rm -f -- /etc/systemd/system/tahmeed-*.service /etc/systemd/system/tahmeed-*.timer
      systemctl daemon-reload
    fi
    [[ "${RELEASE_DIR}" == "${APP_ROOT}/releases/"* ]] && rm -rf -- "${RELEASE_DIR}"
  fi
  exit "${status}"
}
trap cleanup EXIT

install_units() {
  local release=$1
  install -o root -g root -m 0644 "${release}"/deployment/systemd/*.service /etc/systemd/system/
  install -o root -g root -m 0644 "${release}"/deployment/systemd/*.timer /etc/systemd/system/
  systemctl daemon-reload
}

activate_release() {
  local release=$1
  ln -sfn "${release}" "${CURRENT_LINK}.next"
  mv -Tf "${CURRENT_LINK}.next" "${CURRENT_LINK}"
  install_units "${release}"
}

prune_releases() {
  local current previous kept=0
  current="$(readlink -f "${CURRENT_LINK}" 2>/dev/null || true)"
  previous="$(readlink -f "${PREVIOUS_LINK}" 2>/dev/null || true)"
  while IFS= read -r candidate; do
    [[ -d "${candidate}" ]] || continue
    if [[ "${candidate}" == "${current}" || "${candidate}" == "${previous}" ]]; then
      continue
    fi
    kept=$((kept + 1))
    if (( kept > KEEP_RELEASES - 2 )); then
      [[ "${candidate}" == "${APP_ROOT}/releases/"* ]] || {
        echo "Refusing to prune unexpected path: ${candidate}" >&2
        continue
      }
      rm -rf -- "${candidate}"
    fi
  done < <(find "${APP_ROOT}/releases" -mindepth 1 -maxdepth 1 -type d \
    ! -name '.build-*' -printf '%T@ %p\n' | sort -rn | cut -d' ' -f2-)
}

[[ "${VERSION}" =~ ^[A-Za-z0-9._-]+$ ]] || {
  echo "Version may only contain letters, numbers, dot, underscore, and dash." >&2
  exit 1
}
[[ "${KEEP_RELEASES}" =~ ^[0-9]+$ ]] && (( KEEP_RELEASES >= 3 )) || {
  echo "TAHMEED_KEEP_RELEASES must be an integer of at least 3." >&2
  exit 1
}
[[ -f "${SOURCE_DIR}/backend/pyproject.toml" ]] || {
  echo "Source tree must contain backend/pyproject.toml: ${SOURCE_DIR}" >&2
  exit 1
}
[[ -d "${SOURCE_DIR}/deployment/v1/systemd" ]] || {
  echo "Source tree must contain deployment/v1/systemd: ${SOURCE_DIR}" >&2
  exit 1
}
[[ ! -e "${RELEASE_DIR}" ]] || {
  echo "Release already exists: ${RELEASE_DIR}" >&2
  exit 1
}
for command in python3 rsync curl systemctl mongodump find sort cut; do
  command -v "${command}" >/dev/null || {
    echo "Required command is missing: ${command}" >&2
    exit 1
  }
done

if ! getent passwd tahmeed-api >/dev/null; then
  useradd --system --home-dir /nonexistent --no-create-home --shell /usr/sbin/nologin tahmeed-api
fi

install -d -o root -g root -m 0755 "${APP_ROOT}/releases"
install -d -o root -g tahmeed-api -m 0750 /etc/tahmeed-api
install -d -o tahmeed-api -g tahmeed-api -m 0750 /var/lib/tahmeed-api/backups
install -d -o tahmeed-api -g tahmeed-api -m 0750 /var/lock/tahmeed-api
BUILD_DIR="$(mktemp -d "${APP_ROOT}/releases/.build-${VERSION}.XXXXXX")"
install -d -o root -g root -m 0755 \
  "${BUILD_DIR}/backend" "${BUILD_DIR}/deployment/systemd" "${BUILD_DIR}/deployment/env"

rsync -a --delete \
  --exclude='.env' --exclude='.venv' --exclude='__pycache__' \
  --exclude='.pytest_cache' --exclude='.ruff_cache' --exclude='var' \
  "${SOURCE_DIR}/backend/" "${BUILD_DIR}/backend/"
install -o root -g root -m 0644 \
  "${SOURCE_DIR}"/deployment/v1/systemd/*.service "${BUILD_DIR}/deployment/systemd/"
install -o root -g root -m 0644 \
  "${SOURCE_DIR}"/deployment/v1/systemd/*.timer "${BUILD_DIR}/deployment/systemd/"
install -o root -g root -m 0644 \
  "${SOURCE_DIR}/deployment/v1/env/tahmeed-api.env.example" "${BUILD_DIR}/deployment/env/"
python3 -m venv "${BUILD_DIR}/venv"
"${BUILD_DIR}/venv/bin/python" -m pip install --disable-pip-version-check --upgrade pip
if [[ -f "${BUILD_DIR}/backend/requirements.lock" ]]; then
  "${BUILD_DIR}/venv/bin/python" -m pip install --disable-pip-version-check \
    --require-hashes -r "${BUILD_DIR}/backend/requirements.lock"
  "${BUILD_DIR}/venv/bin/python" -m pip install --disable-pip-version-check \
    --no-deps "${BUILD_DIR}/backend"
else
  echo "WARNING: backend/requirements.lock is absent; recording, but not pre-locking, resolution." >&2
  "${BUILD_DIR}/venv/bin/python" -m pip install --disable-pip-version-check "${BUILD_DIR}/backend"
fi
"${BUILD_DIR}/venv/bin/python" -m pip freeze --all \
  >"${BUILD_DIR}/resolved-requirements.txt"
chown -R root:root "${BUILD_DIR}"
chmod -R go-w "${BUILD_DIR}"
mv -T "${BUILD_DIR}" "${RELEASE_DIR}"
BUILD_DIR=
RELEASE_CREATED=1

if [[ ! -e "${ENV_FILE}" ]]; then
  install -o root -g tahmeed-api -m 0640 "${ASSET_DIR}/env/tahmeed-api.env.example" "${ENV_FILE}"
  ENV_CREATED=1
else
  ENV_CREATED=0
fi

OLD_CURRENT="$(readlink -f "${CURRENT_LINK}" 2>/dev/null || true)"
activate_release "${RELEASE_DIR}"
if [[ -n "${OLD_CURRENT}" && -d "${OLD_CURRENT}" ]]; then
  ln -sfn "${OLD_CURRENT}" "${PREVIOUS_LINK}"
fi

if [[ ${ENV_CREATED} -eq 1 ]] || grep -q 'REPLACE_' "${ENV_FILE}"; then
  echo "Release ${VERSION} installed but not started."
  echo "Populate ${ENV_FILE}, keep it root:tahmeed-api mode 0640, then rerun this script with a new version."
  exit 0
fi

if ! systemctl start tahmeed-backup-preflight.service; then
  journalctl -u tahmeed-backup-preflight.service -n 50 --no-pager >&2 || true
  if [[ -n "${OLD_CURRENT}" && -d "${OLD_CURRENT}" ]]; then
    activate_release "${OLD_CURRENT}"
  fi
  echo "Backup preflight failed; previous release and units restored when available." >&2
  exit 1
fi

systemctl enable tahmeed-api.service \
  tahmeed-backup-daily.timer tahmeed-backup-weekly.timer tahmeed-upload-retry.timer
systemctl restart tahmeed-api.service
systemctl restart \
  tahmeed-backup-daily.timer tahmeed-backup-weekly.timer tahmeed-upload-retry.timer

HEALTHY=0
for _ in {1..30}; do
  if curl --fail --silent --show-error --max-time 2 http://127.0.0.1:8000/health/ready >/dev/null; then
    HEALTHY=1
    break
  fi
  sleep 2
done

if [[ ${HEALTHY} -ne 1 ]]; then
  journalctl -u tahmeed-api.service -n 50 --no-pager >&2 || true
  if [[ -n "${OLD_CURRENT}" && -d "${OLD_CURRENT}" ]]; then
    activate_release "${OLD_CURRENT}"
    systemctl restart tahmeed-api.service
  else
    systemctl stop tahmeed-api.service
  fi
  echo "Health check failed; previous release restored when available." >&2
  exit 1
fi

prune_releases
INSTALL_COMPLETE=1
echo "Tahmeed API ${VERSION} is healthy on 127.0.0.1:8000."
