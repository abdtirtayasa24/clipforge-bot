#!/usr/bin/env bash
# Provision or update ClipForge Bot on the Ubuntu VPS. Run as root from a repository checkout:
#   sudo ./deploy/install.sh
# Safe to re-run: every step checks current state first, and services restart only on change.
set -euo pipefail

UV_VERSION="0.11.7"
SERVICE_USER="clipforge"
APP_HOME="/opt/clipforge-bot"
BACKEND_DIR="${APP_HOME}/backend"
VENV_DIR="${BACKEND_DIR}/.venv"
RELEASES_DIR="${APP_HOME}/releases"
STATE_DIR="/var/lib/clipforge-bot"
WEB_DIR="/var/www/clipforge-bot"
CONFIG_DIR="/etc/clipforge-bot"
SYSTEMD_DIR="/etc/systemd/system"
# fonts-dejavu-core: the burned-in subtitle font (ADR-013).
APT_PACKAGES=(ffmpeg fonts-dejavu-core git curl ca-certificates rsync build-essential libpq-dev)
UNITS=(clipforge-api.service clipforge-worker.service clipforge-discovery.service clipforge-discovery.timer)
# Started on first install; the discovery service itself only runs from its timer.
ENABLE_UNITS=(clipforge-api.service clipforge-worker.service clipforge-discovery.timer)

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHANGED=0

log() { printf '[install] %s\n' "$*"; }
fail() { printf '[install] ERROR: %s\n' "$*" >&2; exit 1; }
as_service_user() { runuser -u "${SERVICE_USER}" -- env HOME="${APP_HOME}" "$@"; }

require_root() {
    [[ "${EUID}" -eq 0 ]] || fail "run as root (sudo ./deploy/install.sh)"
}

install_packages() {
    local missing=()
    local pkg
    for pkg in "${APT_PACKAGES[@]}"; do
        dpkg-query -W -f='${Status}' "${pkg}" 2>/dev/null | grep -q "install ok installed" \
            || missing+=("${pkg}")
    done
    if ((${#missing[@]})); then
        log "installing packages: ${missing[*]}"
        apt-get update
        DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${missing[@]}"
    fi
}

install_uv() {
    if command -v uv >/dev/null && [[ "$(uv --version | awk '{print $2}')" == "${UV_VERSION}" ]]; then
        return
    fi
    log "installing uv ${UV_VERSION}"
    curl -LsSf "https://astral.sh/uv/${UV_VERSION}/install.sh" \
        | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh
}

create_service_user() {
    if ! id -u "${SERVICE_USER}" >/dev/null 2>&1; then
        log "creating service account ${SERVICE_USER}"
        useradd --system --home "${APP_HOME}" --shell /usr/sbin/nologin "${SERVICE_USER}"
    fi
}

create_directories() {
    install -d -o "${SERVICE_USER}" -g "${SERVICE_USER}" -m 0755 "${APP_HOME}" "${BACKEND_DIR}"
    install -d -o "${SERVICE_USER}" -g "${SERVICE_USER}" -m 0750 "${RELEASES_DIR}" "${STATE_DIR}"
    install -d -o "${SERVICE_USER}" -g "${SERVICE_USER}" -m 0700 "${STATE_DIR}/workspace"
    install -d -o root -g root -m 0755 "${WEB_DIR}"
    install -d -o root -g "${SERVICE_USER}" -m 0750 "${CONFIG_DIR}"
}

# Copies an env template on first run; the operator must fill it in before continuing.
require_env_file() {
    local name="$1" owner="$2" mode="$3"
    local target="${CONFIG_DIR}/${name}"
    if [[ ! -f "${target}" ]]; then
        install -o root -g "${owner}" -m "${mode}" "${REPO_DIR}/deploy/${name}.example" "${target}"
        fail "created ${target} from template; fill in real values and re-run"
    fi
    if grep -q "CHANGE_ME" "${target}"; then
        fail "${target} still contains CHANGE_ME placeholders"
    fi
}

sync_backend() {
    local changes
    changes="$(rsync -a --delete --itemize-changes \
        --exclude '.venv/' --exclude '__pycache__/' --exclude '.pytest_cache/' \
        --exclude '.mypy_cache/' --exclude '.ruff_cache/' --exclude 'tests/' \
        --chown="${SERVICE_USER}:${SERVICE_USER}" \
        "${REPO_DIR}/backend/" "${BACKEND_DIR}/")"
    if [[ -n "${changes}" ]]; then
        log "backend source updated"
        CHANGED=1
    fi
}

install_python_env() {
    if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
        log "creating virtual environment"
        as_service_user uv venv --python 3.12 "${VENV_DIR}"
        CHANGED=1
    fi
    if ((CHANGED)) || ! "${VENV_DIR}/bin/python" -c "import clipforge" 2>/dev/null; then
        log "installing backend into ${VENV_DIR}"
        (cd "${BACKEND_DIR}" && as_service_user env VIRTUAL_ENV="${VENV_DIR}" uv pip install .)
    fi
}

record_release() {
    local freeze latest
    freeze="$(cd "${BACKEND_DIR}" && as_service_user env VIRTUAL_ENV="${VENV_DIR}" uv pip freeze)"
    latest="$(find "${RELEASES_DIR}" -name '*.txt' | sort | tail -n 1)"
    if [[ -z "${latest}" ]] || [[ "$(cat "${latest}")" != "${freeze}" ]]; then
        local release
        release="${RELEASES_DIR}/$(date -u +%Y%m%dT%H%M%SZ)-$(git -C "${REPO_DIR}" rev-parse --short HEAD).txt"
        printf '%s\n' "${freeze}" | as_service_user tee "${release}" >/dev/null
        log "recorded installed packages in ${release}"
    fi
}

# Reads one KEY=value line like systemd's EnvironmentFile; sourcing the file would make bash
# treat the "&" inside database URLs as a background operator.
env_file_value() {
    local file="$1" key="$2" value
    value="$(sed -n "s/^${key}=//p" "${file}" | tail -n 1)"
    value="${value%\"}"
    printf '%s' "${value#\"}"
}

run_migrations() {
    log "applying database migrations"
    local url
    url="$(env_file_value "${CONFIG_DIR}/migration.env" CLIPFORGE_MIGRATION_DATABASE_URL)"
    [[ -n "${url}" ]] || fail "CLIPFORGE_MIGRATION_DATABASE_URL missing in ${CONFIG_DIR}/migration.env"
    (cd "${BACKEND_DIR}" && CLIPFORGE_MIGRATION_DATABASE_URL="${url}" "${VENV_DIR}/bin/alembic" upgrade head)
}

install_units() {
    local unit
    for unit in "${UNITS[@]}"; do
        if ! cmp -s "${REPO_DIR}/deploy/systemd/${unit}" "${SYSTEMD_DIR}/${unit}"; then
            install -o root -g root -m 0644 "${REPO_DIR}/deploy/systemd/${unit}" "${SYSTEMD_DIR}/${unit}"
            log "installed ${unit}"
            CHANGED=1
        fi
    done
    systemctl daemon-reload
}

restart_active_units() {
    ((CHANGED)) || return 0
    local unit
    for unit in "${UNITS[@]}"; do
        if systemctl is-active --quiet "${unit}"; then
            log "restarting ${unit}"
            systemctl restart "${unit}"
        fi
    done
}

main() {
    require_root
    install_packages
    install_uv
    create_service_user
    create_directories
    require_env_file backend.env "${SERVICE_USER}" 0640
    require_env_file migration.env root 0600
    sync_backend
    install_units
    install_python_env
    record_release
    run_migrations
    restart_active_units
    log "done; first install: systemctl enable --now ${ENABLE_UNITS[*]}"
}

main "$@"
