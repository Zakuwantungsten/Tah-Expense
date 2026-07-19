#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run as root: sudo $0 [version]" >&2
  exit 1
fi

APP_ROOT=/opt/tahmeed-api
CURRENT_LINK="${APP_ROOT}/current"
PREVIOUS_LINK="${APP_ROOT}/previous"
REQUESTED_VERSION="${1:-}"
OLD_CURRENT="$(readlink -f "${CURRENT_LINK}" 2>/dev/null || true)"

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

if [[ -n "${REQUESTED_VERSION}" ]]; then
  [[ "${REQUESTED_VERSION}" =~ ^[A-Za-z0-9._-]+$ ]] || {
    echo "Invalid version." >&2
    exit 1
  }
  TARGET="${APP_ROOT}/releases/${REQUESTED_VERSION}"
else
  TARGET="$(readlink -f "${PREVIOUS_LINK}" 2>/dev/null || true)"
fi

[[ -n "${TARGET}" && -x "${TARGET}/venv/bin/uvicorn" \
  && -f "${TARGET}/backend/app/main.py" \
  && -d "${TARGET}/deployment/systemd" ]] || {
  echo "Rollback release is missing or incomplete: ${TARGET:-not set}" >&2
  exit 1
}
[[ "${TARGET}" != "${OLD_CURRENT}" ]] || {
  echo "Requested release is already current." >&2
  exit 1
}

activate_release "${TARGET}"
[[ -z "${OLD_CURRENT}" ]] || ln -sfn "${OLD_CURRENT}" "${PREVIOUS_LINK}"
systemctl restart tahmeed-api.service

for _ in {1..30}; do
  if curl --fail --silent --show-error --max-time 2 http://127.0.0.1:8000/health/ready >/dev/null; then
    echo "Rolled back to $(basename -- "${TARGET}")."
    exit 0
  fi
  sleep 2
done

if [[ -n "${OLD_CURRENT}" && -d "${OLD_CURRENT}" ]]; then
  activate_release "${OLD_CURRENT}"
  systemctl restart tahmeed-api.service
fi
echo "Rollback target failed health checks; original release restored." >&2
exit 1
