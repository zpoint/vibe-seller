#!/usr/bin/env bash
# Recover access to a Vibe Seller install whose admin password is unknown.
#
#   scripts/reset_auth.sh status
#   scripts/reset_auth.sh password --user admin --password 'secret'
#   scripts/reset_auth.sh password --user admin --rename   # prompts
#   scripts/reset_auth.sh disable                          # turn login off
#
# The command edits the app database in place, so the server can stay up
# and the agent tasks running under it are not interrupted. On Windows
# use scripts/reset_auth.bat instead.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

# 1. A checkout's own venv — the common case for a source install.
#    `app.auth_reset` is the module the `vibe-seller reset-auth`
#    command runs, and it needs nothing but the app's own deps
#    (bcrypt, pydantic).
if [ -x "$REPO_ROOT/.venv/bin/python" ]; then
  exec env PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}" \
    "$REPO_ROOT/.venv/bin/python" -m app.auth_reset "$@"
fi

# 2. The installed CLI (`uv tool install vibe-seller`).
if command -v vibe-seller >/dev/null 2>&1; then
  exec vibe-seller reset-auth "$@"
fi

# 3. Any python, with the checkout on the import path.
#    Under Git Bash the checkout path is POSIX (/d/a/...) but Windows
#    python needs it in Windows form — cygpath does the conversion, and
#    the PYTHONPATH separator differs too.
REPO_ROOT_IMPORT="$REPO_ROOT"
PYTHONPATH_SEP=':'
if command -v cygpath >/dev/null 2>&1; then
  REPO_ROOT_IMPORT="$(cygpath -w "$REPO_ROOT")"
  PYTHONPATH_SEP=';'
fi
for PY in python3 python; do
  if command -v "$PY" >/dev/null 2>&1; then
    exec env \
      PYTHONPATH="$REPO_ROOT_IMPORT${PYTHONPATH:+$PYTHONPATH_SEP$PYTHONPATH}" \
      "$PY" -m app.auth_reset "$@"
  fi
done

echo "reset_auth.sh: no Python found." >&2
echo "Install Python 3.11+ and try again." >&2
exit 1
