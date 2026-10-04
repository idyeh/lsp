#!/usr/bin/env bash
# One-time preparation of a Linux server (run as root, from the checkout, e.g. /opt/lab-seminar-planner):
#
#   sudo deploy/setup-server.sh [--no-https]
#
# It creates
#   - a system user that runs the service and nothing else (no login shell, no home directory),
#   - the data directory (database and backups), owned by that user and closed to everyone else,
#   - the configuration file /etc/lab-seminar-planner/env with a freshly generated secret key (root only),
#   - the systemd service, plus a daily backup timer.
# It does not start anything: run deploy/deploy.sh next. Running it again is safe; an existing
# configuration file is never changed.
#
# Options:
#   --no-https   serve over plain HTTP (a trusted internal network only). The session cookie is then
#                not marked Secure; with the default (HTTPS) logging in over plain HTTP does not work.
#
# For testing, DESTDIR=/some/dir installs the files below that directory and skips the system changes
# (user, ownership, systemctl), so no root access is needed.
set -euo pipefail
# shellcheck source=deploy/common.sh
. "$(dirname "${BASH_SOURCE[0]}")/common.sh"

DESTDIR="${DESTDIR:-}"
COOKIE_SECURE=true
while [ $# -gt 0 ]; do
  case "$1" in
    --no-https) COOKIE_SECURE=false; shift ;;
    -h | --help) sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown option: $1 (see --help)" ;;
  esac
done

[ -n "$DESTDIR" ] || [ "$(id -u)" -eq 0 ] || die "run as root: sudo deploy/setup-server.sh"
for value in "$APP_DIR" "$STATE_DIR" "$ENV_FILE" "$SERVICE_USER"; do
  case "$value" in *[!A-Za-z0-9._/-]*) die "unsupported character in '$value' (use letters, digits and . _ / -)" ;; esac
done
[ -f "$APP_DIR/requirements.txt" ] || die "$APP_DIR does not look like the application checkout"

# --- service user -----------------------------------------------------------------------------------
if [ -z "$DESTDIR" ]; then
  if id "$SERVICE_USER" >/dev/null 2>&1; then
    info "User $SERVICE_USER already exists"
  else
    nologin=/usr/sbin/nologin
    [ -x "$nologin" ] || nologin=/sbin/nologin
    info "Creating system user $SERVICE_USER"
    useradd --system --user-group --home-dir "$STATE_DIR" --no-create-home --shell "$nologin" "$SERVICE_USER"
  fi
fi

# --- data directory ---------------------------------------------------------------------------------
info "Data directory $STATE_DIR"
install -d -m 0700 "$DESTDIR$STATE_DIR"
[ -n "$DESTDIR" ] || chown "$SERVICE_USER:$SERVICE_USER" "$STATE_DIR"

# --- configuration file ----------------------------------------------------------------------------------
install -d -m 0755 "$(dirname "$DESTDIR$ENV_FILE")"
if [ -e "$DESTDIR$ENV_FILE" ]; then
  info "Keeping the existing configuration $ENV_FILE"
else
  info "Writing $ENV_FILE with a new secret key"
  SECRET=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')
  (
    umask 077
    cat >"$DESTDIR$ENV_FILE" <<CONFIG
# Lab Seminar Planner configuration. Read by systemd and by deploy/lsp, deploy/deploy.sh, deploy/backup.sh.
# Plain KEY=VALUE lines: no quotes, no spaces around "=", no shell syntax.
# Signs login cookies. Keep it secret; changing it logs everybody out.
LSP_SECRET_KEY=$SECRET
LSP_DATABASE_URL=sqlite:///$STATE_DIR/seminar.db
# true when users reach the site over HTTPS (the cookie is then never sent over plain HTTP).
LSP_COOKIE_SECURE=$COOKIE_SECURE
LSP_SESSION_MAX_AGE=604800
# Where the application listens. Keep it on 127.0.0.1 and put a reverse proxy with HTTPS in front of it.
LSP_BIND_HOST=127.0.0.1
LSP_BIND_PORT=8000
CONFIG
  )
fi
[ -n "$DESTDIR" ] || chown root:root "$ENV_FILE"

# --- systemd units ------------------------------------------------------------------------------------
UNIT_DIR="$DESTDIR/etc/systemd/system"
install -d -m 0755 "$UNIT_DIR"
for unit in "$SERVICE_NAME.service" "$SERVICE_NAME-backup.service" "$SERVICE_NAME-backup.timer"; do
  info "Installing $unit"
  sed -e "s|@APP_DIR@|$APP_DIR|g" \
      -e "s|@SERVICE_USER@|$SERVICE_USER|g" \
      -e "s|@ENV_FILE@|$ENV_FILE|g" \
      -e "s|@STATE_DIR@|$STATE_DIR|g" \
      "$APP_DIR/deploy/$unit" >"$UNIT_DIR/$unit"
  chmod 0644 "$UNIT_DIR/$unit"
done
if [ -z "$DESTDIR" ]; then
  systemctl daemon-reload
  systemctl enable "$SERVICE_NAME.service"
  systemctl enable --now "$SERVICE_NAME-backup.timer"
fi

cat <<NEXT

Server prepared. Next steps:
  1. sudo $APP_DIR/deploy/deploy.sh --ref <tag or branch>   install dependencies and start the service
  2. sudo $APP_DIR/deploy/lsp create-user <name> --display-name "<Full Name>" --role organiser
  3. Put a reverse proxy with HTTPS in front of 127.0.0.1:8000 (examples: deploy/Caddyfile.example,
     deploy/nginx.conf.example)
Configuration: $ENV_FILE    Data and backups: $STATE_DIR    Logs: journalctl -u $SERVICE_NAME
NEXT
