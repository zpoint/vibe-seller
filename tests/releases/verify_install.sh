#!/usr/bin/env bash
#
# Release-pipeline smoke test. The SAME script runs against the
# TestPyPI install and the PyPI install — only the version + source
# differ (caller chose those via `install.sh --test-pypi --version <ver>`
# or `install.sh --version <ver>`).
#
# Usage:
#   tests/releases/verify_install.sh <expected_version>
#
#   <expected_version>   e.g. "0.0.1" or "0.0.1.dev20260514120555".
#                        Asserted against `vibe-seller --version`.
#                        Pass an empty string to skip the version check.
#
# Optional env:
#   VIBE_SELLER_BASE_URL  default http://127.0.0.1:7777
#   HEALTH_TIMEOUT_S      default 30
#
# What it verifies, end-to-end, against the just-installed wheel:
#   1. `vibe-seller --version` matches the expected version
#   2. /api/health returns 200
#   3. POST /api/stores creates a Chrome-backend store, GET confirms
#   4. POST /api/profiles creates an AI profile, GET confirms
#
# Exits 0 on full success, non-zero on the first failure. Requires:
#   curl, jq, vibe-seller on PATH (the install step puts it there).
#
# Auth assumption: the default DB seed sets auth_required=false, so
# unauthenticated requests go through as the default admin. If that
# default ever flips, this script will need to fetch a JWT cookie
# from /api/auth/login first.

set -euo pipefail

EXPECTED_VERSION="${1:-}"
BASE="${VIBE_SELLER_BASE_URL:-http://127.0.0.1:7777}"
HEALTH_TIMEOUT_S="${HEALTH_TIMEOUT_S:-30}"

_log() { printf '==> %s\n' "$*"; }
_die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# --- 0. vibe-seller --version matches expected -------------------
# argparse `action='version'` prints "vibe-seller <ver>" to stdout and
# exits 0. We grab the trailing token as the version string.
if [ -n "$EXPECTED_VERSION" ]; then
    _log "vibe-seller --version (expecting: $EXPECTED_VERSION)"
    version_line=$(vibe-seller --version 2>/dev/null || true)
    actual_version=$(printf '%s' "$version_line" | awk '{print $NF}')
    if [ "$actual_version" != "$EXPECTED_VERSION" ]; then
        _die "version mismatch: expected '$EXPECTED_VERSION', got '$version_line'"
    fi
    _log "  version OK: $actual_version"
else
    _log "skipping version check (no expected version supplied)"
fi

# --- 1. wait for /api/health -------------------------------------
_log "wait for $BASE/api/health"
for _ in $(seq 1 "$HEALTH_TIMEOUT_S"); do
    code=$(curl -fsS -o /dev/null -w "%{http_code}" "$BASE/api/health" || true)
    if [ "$code" = "200" ]; then
        _log "  health OK"
        break
    fi
    sleep 1
done
[ "$code" = "200" ] || _die "/api/health didn't reach 200 within ${HEALTH_TIMEOUT_S}s (last=$code)"

# --- 2. POST /api/stores (Chrome backend, no Ziniao) -------------
_log "POST /api/stores"
store_json=$(
    curl -fsS -X POST "$BASE/api/stores" \
        -H 'Content-Type: application/json' \
        -d '{"name":"ci-verify-store","browser_backend":"chrome","platforms":["amazon"],"countries":["US"]}'
)
echo "$store_json" | jq .
store_id=$(echo "$store_json" | jq -r '.id // empty')
[ -n "$store_id" ] || _die "POST /api/stores response had no .id field"
_log "  created store id: $store_id"

# --- 3. GET /api/stores — assert the new store is listed ---------
_log "GET /api/stores — confirm store is listed"
curl -fsS "$BASE/api/stores" \
    | jq -e --arg id "$store_id" '. | map(select(.id == $id)) | length == 1' \
    > /dev/null \
    || _die "store $store_id missing from GET /api/stores"
_log "  store listed OK"

# --- 4. POST /api/profiles ---------------------------------------
_log "POST /api/profiles"
profile_json=$(
    curl -fsS -X POST "$BASE/api/profiles" \
        -H 'Content-Type: application/json' \
        -d '{"id":"ci-verify-profile","name":"CI Verify","env":{"ANTHROPIC_API_KEY":"test-key"}}'
)
echo "$profile_json" | jq .

# --- 5. GET /api/profiles — assert the new profile is listed -----
# Response shape: {"profiles": [{"id": "...", "name": "...", ...}, ...]}.
# `.profiles` is a list of objects, so look for one with matching id.
_log "GET /api/profiles — confirm profile is listed"
curl -fsS "$BASE/api/profiles" \
    | jq -e '.profiles | any(.id == "ci-verify-profile")' \
    > /dev/null \
    || _die "profile 'ci-verify-profile' missing from GET /api/profiles"
_log "  profile listed OK"

_log "smoke test passed — server stands up and API works end-to-end"
