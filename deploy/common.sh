#!/usr/bin/env bash
# Helpers shared by the deployment scripts. Source this file; do not run it.
# Written for bash 3.2+ (no associative arrays, no ${var,,}) so it also runs on macOS.

SERVICE_NAME="lab-seminar-planner"
SERVICE_USER="${SERVICE_USER:-lab-seminar}"
ENV_FILE="${ENV_FILE:-/etc/lab-seminar-planner/env}"
STATE_DIR="${STATE_DIR:-/var/lib/lab-seminar-planner}"
# The checkout this file belongs to (deploy/ is one level below it).
APP_DIR="${APP_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

info() { printf '==> %s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

# Export the settings from the configuration file (plain KEY=VALUE lines). A missing file is fine (a
# development checkout). A file that exists but cannot be read is an error, unless the settings were
# supplied some other way (systemd passes them in): silently using defaults would act on the wrong database.
load_env() {
  if [ -r "$ENV_FILE" ]; then
    set -a
    # shellcheck source=/dev/null
    . "$ENV_FILE"
    set +a
  elif [ -e "$ENV_FILE" ] && [ -z "${LSP_DATABASE_URL:-}" ]; then
    die "cannot read $ENV_FILE (it is readable by root only): run this with sudo, or through systemctl"
  fi
}

# Run a command as the service user when we are root and that user exists; otherwise as ourselves.
# (The data directory belongs to the service user, so database files must be created by it.)
run_as_service_user() {
  if [ "$(id -u)" -eq 0 ] && id "$SERVICE_USER" >/dev/null 2>&1; then
    if command -v runuser >/dev/null 2>&1; then
      runuser -u "$SERVICE_USER" -- "$@"
    else
      sudo -u "$SERVICE_USER" -- "$@"
    fi
  else
    "$@"
  fi
}

# True when the systemd unit for the application is installed.
service_installed() {
  command -v systemctl >/dev/null 2>&1 && systemctl cat "$SERVICE_NAME.service" >/dev/null 2>&1
}
