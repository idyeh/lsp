#!/usr/bin/env bash
# Continuous-integration entry point: the same checks locally and on any CI service.
#
#   scripts/ci.sh                    # uses python3
#   PYTHON=python3.11 scripts/ci.sh  # test with a specific interpreter
#
# Steps, stopping at the first failure: dependencies -> lint -> shell scripts -> tests -> smoke test.
# Needs Python 3.11+ with the venv module; nothing else is installed outside the virtual environment.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

PYTHON="${PYTHON:-python3}"
step() { printf '\n==> %s\n' "$*"; }

step "Python"
"$PYTHON" -c 'import sys
assert sys.version_info >= (3, 11), "Python 3.11 or newer is required, found " + sys.version.split()[0]
print(sys.version.split()[0])'

# One environment per interpreter version, so running with another PYTHON never reuses a mismatched venv.
VENV="${CI_VENV:-.venv-ci-$("$PYTHON" -c 'import sys; print("%d.%d" % sys.version_info[:2])')}"
step "Dependencies (virtual environment: $VENV)"
[ -x "$VENV/bin/python" ] || "$PYTHON" -m venv "$VENV"
"$VENV/bin/python" -m pip install --quiet --disable-pip-version-check -r requirements-dev.txt

step "Lint (ruff)"
"$VENV/bin/ruff" check .

step "Shell scripts"
# (find exits non-zero if a directory is missing; that is fine, there is then just nothing to check there)
shell_scripts=$(find scripts deploy -type f \( -name '*.sh' -o -name 'lsp' \) 2>/dev/null | sort || true)
[ -n "$shell_scripts" ] || echo "no shell scripts found"
for script in $shell_scripts; do
  bash -n "$script"
  echo "syntax ok: $script"
done
if command -v shellcheck >/dev/null 2>&1; then
  # shellcheck disable=SC2086  # the list is whitespace-separated file names without spaces
  shellcheck $shell_scripts
  echo "shellcheck ok"
else
  echo "shellcheck not installed; skipped"
fi

step "Tests"
"$VENV/bin/python" -m pytest

step "Smoke test (real server process)"
"$VENV/bin/python" scripts/smoke_test.py

printf '\nCI passed\n'
