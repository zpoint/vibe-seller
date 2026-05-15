#!/bin/bash
# Vibe Seller container entrypoint.
# Installs deps (root for apt), then drops to non-root for everything else.
# Claude CLI rejects bypassPermissions as root, so the server and tests
# must run as the non-root 'vibe' user.
set -euo pipefail

# ── Root phase: system deps only ──────────────────

rm -rf /root/.vibe-seller

# Git worktree .git file points to a host path that doesn't exist
# in the container.  Copy to a temp workspace to avoid mutating host.
if [ -f /app/.git ] && grep -q "gitdir:" /app/.git 2>/dev/null; then
  WORK=/tmp/vibe-seller-workspace
  rm -rf "$WORK"
  cp -a /app/. "$WORK"/
  cd "$WORK"
  rm -f .git
  # Symlink logs dir back to the volume mount so host can read them.
  rm -rf "$WORK/logs"
  mkdir -p /app/logs
  ln -sf /app/logs "$WORK/logs"
  echo "Working in copy: $WORK"
fi

WORK_DIR="$(pwd)"

# When the host bind-mounts /app, its files retain host UIDs that
# don't exist inside the container. Git's "dubious ownership"
# guard then refuses every introspection call (including the one
# `uv pip install -e .` runs via setuptools-scm/vcs-versioning).
# Mark every path safe — `GIT_CONFIG_*` env vars are inherited by
# uv's isolated build subprocess; a file-based `git config` is not.
export GIT_CONFIG_COUNT=1
export GIT_CONFIG_KEY_0="safe.directory"
export GIT_CONFIG_VALUE_0="*"
git config --global --add safe.directory '*'

# Install Python deps as root (needs write to global cache)
rm -rf .venv
uv venv --python 3.11
uv pip install -e ".[dev]"
uv pip install pytest pytest-asyncio httpx pytest-playwright pytest-xdist
# Playwright browser+deps are baked into the Docker image at
# /opt/pw-browsers (set via PLAYWRIGHT_BROWSERS_PATH in Dockerfile).

# Build frontend
(cd frontend && npm install && npm run build)

# Hand everything to non-root user
chown -R vibe:vibe "$WORK_DIR"
chown -R vibe:vibe /home/vibe
# Claude CLI stores session data here
mkdir -p /home/vibe/.claude
chown -R vibe:vibe /home/vibe/.claude

# ── Non-root phase: server + tests ────────────────

# Git config for non-root user (same safe.directory rule — vibe
# has its own ~/.gitconfig and doesn't inherit root's).
gosu vibe git config --global user.email "docker@vibe-seller.test"
gosu vibe git config --global user.name "Docker E2E"
gosu vibe git config --global --add safe.directory '*'
gosu vibe git init -q

# Start server in background as non-root
mkdir -p logs
chown -R vibe:vibe logs
# Timestamped log file so reruns don't overwrite previous logs.
# Also symlink logs/server_stdout.log → latest for convenience.
LOG_FILE="logs/server_$(date +%Y%m%d_%H%M%S).log"
BACKEND_PORT="${BACKEND_PORT:-7777}" \
  gosu vibe env HOME=/home/vibe \
  uv run uvicorn app.main:app \
  --host 0.0.0.0 --port "${BACKEND_PORT:-7777}" --log-level info \
  > "$LOG_FILE" 2>&1 &
ln -sf "$(basename "$LOG_FILE")" logs/server_stdout.log

# Wait for healthy
for i in $(seq 1 30); do
  sleep 1
  if curl -sf "http://127.0.0.1:${BACKEND_PORT:-7777}/api/health" >/dev/null 2>&1; then
    echo "Server healthy after ${i}s"
    break
  fi
  if [ "$i" -eq 30 ]; then
    echo "ERROR: Server failed health check" >&2
    cat logs/server_stdout.log >&2
    exit 1
  fi
done

# If running pytest with xdist, inject -n workers (mirrors CI)
if [ "$1" = "uv" ] && [[ "$*" == *"pytest"* ]]; then
  E2E_N="${E2E_WORKERS:-0}"
  if [ "$E2E_N" -gt 1 ] 2>/dev/null; then
    # Append xdist args if not already present
    if [[ "$*" != *"-n "* ]]; then
      set -- "$@" -n "$E2E_N" --dist loadgroup
    fi
  fi
fi

# Run command as non-root user
exec gosu vibe env HOME=/home/vibe "$@"
