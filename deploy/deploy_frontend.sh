#!/usr/bin/env bash
# Build the Telegram Mini App and publish it to /var/www/clipforge-bot. Run as your normal user
# from a repository checkout (sudo is used only to copy files and reload Nginx):
#   ./deploy/deploy_frontend.sh
# Needs Node.js >= 22.22 with npm.
set -euo pipefail

WEB_DIR="/var/www/clipforge-bot"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND_DIR="${REPO_DIR}/frontend"
DIST_DIR="${FRONTEND_DIR}/dist"

log() { printf '[frontend] %s\n' "$*"; }
fail() { printf '[frontend] ERROR: %s\n' "$*" >&2; exit 1; }

[[ "${EUID}" -ne 0 ]] || fail "run as your normal user; the script calls sudo where needed"
command -v npm >/dev/null || fail "npm not found; install Node.js >= 22.22"

log "building"
(cd "${FRONTEND_DIR}" && npm ci --no-audit --no-fund && npm run build)
[[ -f "${DIST_DIR}/index.html" ]] || fail "build produced no dist/index.html"

# New hashed assets first, then index.html, then drop stale assets: an open Mini App never
# loads an index.html that points at files which are not there yet.
log "publishing to ${WEB_DIR}"
sudo install -d -o root -g root -m 0755 "${WEB_DIR}"
sudo rsync -rt --chown=root:root --chmod=D755,F644 --exclude index.html "${DIST_DIR}/" "${WEB_DIR}/"
sudo install -o root -g root -m 0644 "${DIST_DIR}/index.html" "${WEB_DIR}/index.html"
sudo rsync -rt --delete --chown=root:root --chmod=D755,F644 "${DIST_DIR}/" "${WEB_DIR}/"

log "checking Nginx configuration"
sudo nginx -t
sudo systemctl reload nginx
log "done"
