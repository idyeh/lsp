#!/usr/bin/env bash
# Install or update the application from this checkout and (re)start the service.
#
#   sudo deploy/deploy.sh                    # deploy what is checked out now
#   sudo deploy/deploy.sh --ref v0.3.0       # switch to a tag, branch or commit first
#   deploy/deploy.sh --no-restart            # prepare only (no root, no service)
#
# Steps: switch version (optional) -> virtual environment and dependencies -> create/upgrade the
# database tables (never drops anything) -> restart the service -> check that the running server
# answers (scripts/smoke_test.py). If the check fails, the log is shown with the command that
# returns to the previous version. Run deploy/setup-server.sh once before the first deployment.
#
# Options:
#   --ref REF       fetch and check out a tag, branch or commit (refuses to run over uncommitted changes)
#   --no-restart    do not touch the service
#   -h, --help      show this help
# Environment: PYTHON (interpreter used to create the virtual environment, default python3).
set -euo pipefail
# shellcheck source=deploy/common.sh
. "$(dirname "${BASH_SOURCE[0]}")/common.sh"

REF=""
RESTART=1
while [ $# -gt 0 ]; do
  case "$1" in
    --ref) [ $# -ge 2 ] || die "--ref needs a value"; REF="$2"; shift 2 ;;
    --no-restart) RESTART=0; shift ;;
    -h | --help) sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown option: $1 (see --help)" ;;
  esac
done

PYTHON="${PYTHON:-python3}"
VENV="${VENV:-$APP_DIR/.venv}"
git_cmd() { git -c safe.directory="$APP_DIR" -C "$APP_DIR" "$@"; }

# Fail early, before changing anything.
if [ "$RESTART" -eq 1 ] && service_installed && [ "$(id -u)" -ne 0 ]; then
  die "restarting the service needs root: run with sudo, or pass --no-restart"
fi
"$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' ||
  die "Python 3.11 or newer is required ($PYTHON is $("$PYTHON" -c 'import sys; print(sys.version.split()[0])' 2>/dev/null || echo 'not usable'))"

# --- 1. version -------------------------------------------------------------------------------------------
PREVIOUS=""
if [ -d "$APP_DIR/.git" ]; then
  PREVIOUS=$(git_cmd rev-parse --short HEAD)
fi
if [ -n "$REF" ]; then
  [ -d "$APP_DIR/.git" ] || die "--ref needs a git checkout in $APP_DIR"
  if ! git_cmd diff --quiet || ! git_cmd diff --cached --quiet; then
    die "there are uncommitted changes in $APP_DIR; refusing to switch versions"
  fi
  info "Fetching versions"
  git_cmd fetch --quiet --tags --prune origin || die "git fetch failed (is the 'origin' remote reachable?)"
  if git_cmd show-ref --verify --quiet "refs/remotes/origin/$REF"; then
    git_cmd checkout --quiet "$REF" 2>/dev/null || git_cmd checkout --quiet -b "$REF" "origin/$REF"
    git_cmd merge --quiet --ff-only "origin/$REF" || die "local branch $REF has diverged from origin/$REF"
  else
    git_cmd checkout --quiet --detach "$REF" 2>/dev/null || die "unknown version: $REF (not a branch, tag or commit)"
  fi
fi
VERSION=$(git_cmd describe --tags --always 2>/dev/null || echo "unversioned")
info "Deploying $VERSION from $APP_DIR"

# --- 2. dependencies ----------------------------------------------------------------------------------------
info "Virtual environment $VENV"
[ -x "$VENV/bin/python" ] || "$PYTHON" -m venv "$VENV"
"$VENV/bin/python" -m pip install --quiet --disable-pip-version-check -r "$APP_DIR/requirements.txt"

# --- 3. database ---------------------------------------------------------------------------------------------------
load_env
info "Database tables"
(cd "$APP_DIR" && run_as_service_user "$VENV/bin/python" -m app.cli init-db)

# --- 4. restart and check ---------------------------------------------------------------------------------------------
if [ "$RESTART" -eq 0 ]; then
  info "Not restarting (--no-restart)"
elif ! service_installed; then
  warn "the $SERVICE_NAME service is not installed; run deploy/setup-server.sh first. Nothing was restarted."
else
  info "Restarting $SERVICE_NAME"
  systemctl restart "$SERVICE_NAME.service"
  URL="http://${LSP_BIND_HOST:-127.0.0.1}:${LSP_BIND_PORT:-8000}"
  info "Checking $URL"
  if ! "$VENV/bin/python" "$APP_DIR/scripts/smoke_test.py" --url "$URL" --wait 40; then
    journalctl -u "$SERVICE_NAME" -n 40 --no-pager >&2 || true
    [ -z "$PREVIOUS" ] || printf '\nTo go back to the previous version:\n  sudo %s/deploy/deploy.sh --ref %s\n' "$APP_DIR" "$PREVIOUS" >&2
    die "the new version did not pass the health check"
  fi
fi
info "Done: $VERSION"
