#!/bin/bash
set -euo pipefail

# Vibe Seller installer.
#
# Two modes:
#
#   1. Default (end users) — installs the published `vibe-seller`
#      package from PyPI via `uv tool install`, then fetches the
#      Playwright Chromium binary. The wheel ships the built frontend
#      so you get a working web UI with no clone.
#
#        curl -sSL https://raw.githubusercontent.com/zpoint/vibe-seller/main/install.sh | bash
#
#   2. --dev (contributors) — clones the repo, installs system deps,
#      creates the venv, runs `uv pip install -e .`, sets up Playwright,
#      builds the frontend from source. When run via curl|bash, the
#      repo is cloned to $VIBE_SELLER_HOME (default ~/vibe-seller) and
#      this script is re-executed from inside the clone.
#
#        curl -sSL https://raw.githubusercontent.com/zpoint/vibe-seller/main/install.sh | bash -s -- --dev
#
# Other flags:
#   --apt-fallback    Only add a reachable apt mirror if a configured
#                     source is unreachable, then exit (keeps the file;
#                     used by CI before `playwright install --with-deps`).
#   --check-only      Check dev-mode dependencies without installing.
#                     Implies --dev (only meaningful in a clone).
#   --help, -h        Print this help and exit.

# -- Parse flags first (bootstrap behaviour depends on --dev) --
DEV=false
CHECK_ONLY=false
HELP=false
PRINT_PATH=false
TEST_PYPI=false
NIGHTLY=false
APT_FALLBACK=false
VERSION=""
# `while + shift` so we can take an argument after --version. Other
# flags stay positional-agnostic.
while [ $# -gt 0 ]; do
    case "$1" in
        --dev)         DEV=true ;;
        --check-only)  CHECK_ONLY=true; DEV=true ;;  # check-only is dev-only
        --print-path)  PRINT_PATH=true ;;  # emit the tool PATH, for start.sh
        --test-pypi)   TEST_PYPI=true ;;
        --nightly)     NIGHTLY=true ;;
        --apt-fallback) APT_FALLBACK=true ;;  # CI: only add a reachable apt mirror
        --version)     VERSION="${2:-}"; shift ;;
        --version=*)   VERSION="${1#*=}" ;;
        --help|-h)     HELP=true ;;
    esac
    shift
done
# Normalize: strip any leading `v` (so callers can pass `v0.0.1` or `0.0.1`).
VERSION="${VERSION#v}"

# -- Dev-mode bootstrap: clone repo if not already in one --
# Detection requires a *vibe-seller* checkout, not just any directory
# with a pyproject.toml — running `curl ... | bash` from inside an
# unrelated Python project (or `cwd` resolving there) must not be
# mistaken for an in-tree run.
_SCRIPT_PATH="${BASH_SOURCE[0]:-$0}"
SCRIPT_DIR="$(cd "$(dirname "$_SCRIPT_PATH")" 2>/dev/null && pwd)" || SCRIPT_DIR=""
_is_vibe_seller_checkout() {
    local dir="$1"
    [ -n "$dir" ] || return 1
    [ -f "$dir/pyproject.toml" ] || return 1
    [ -f "$dir/start.sh" ] || return 1
    # Confirm the project metadata identifies as vibe-seller, not a
    # different package whose script happens to share a directory.
    grep -qE '^name *= *"vibe-seller"' "$dir/pyproject.toml" 2>/dev/null
}

if [ "$DEV" = true ] && ! _is_vibe_seller_checkout "$SCRIPT_DIR"; then
    INSTALL_DIR="${VIBE_SELLER_HOME:-$HOME/vibe-seller}"
    REPO_URL="${VIBE_SELLER_REPO:-https://github.com/zpoint/vibe-seller}"

    printf "==> Vibe Seller dev bootstrap (target: %s)\n" "$INSTALL_DIR"

    if ! command -v git >/dev/null 2>&1; then
        echo "Error: git is required for --dev install. Install git and re-run." >&2
        exit 1
    fi
    if ! command -v curl >/dev/null 2>&1; then
        echo "Error: curl is required for --dev install. Install curl and re-run." >&2
        exit 1
    fi
    if ! command -v uv >/dev/null 2>&1; then
        printf "==> Installing uv (Python toolchain)\n"
        # Pin uv version — see install_uv() below for rationale.
        curl -LsSf https://astral.sh/uv/0.10.4/install.sh | sh
        # uv installer drops the binary in ~/.local/bin or ~/.cargo/bin
        # depending on platform/shell init — try both.
        export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
        if ! command -v uv >/dev/null 2>&1; then
            echo "Error: uv install succeeded but binary not on PATH." >&2
            echo "  Add ~/.local/bin or ~/.cargo/bin to your PATH and re-run." >&2
            exit 1
        fi
    fi

    if [ -d "$INSTALL_DIR/.git" ]; then
        printf "==> Updating existing checkout at %s\n" "$INSTALL_DIR"
        git -C "$INSTALL_DIR" pull --ff-only
    elif [ -d "$INSTALL_DIR" ] && [ -n "$(ls -A "$INSTALL_DIR" 2>/dev/null)" ]; then
        # Directory exists, isn't a git repo, and isn't empty — we
        # don't know what's in it; refuse to clobber.
        echo "Error: $INSTALL_DIR exists and is not a vibe-seller checkout." >&2
        echo "  Pick a different location with VIBE_SELLER_HOME=<path>" >&2
        echo "  or move/remove $INSTALL_DIR before re-running." >&2
        exit 1
    else
        printf "==> Cloning %s into %s\n" "$REPO_URL" "$INSTALL_DIR"
        git clone "$REPO_URL" "$INSTALL_DIR"
    fi

    printf "==> Delegating to %s/install.sh\n" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
    exec bash "./install.sh" "$@"
fi

# -- Color output (only when interactive) --
if [[ -t 2 ]]; then
    _R=$'\033[31m' _G=$'\033[32m' _B=$'\033[34m'
    _Y=$'\033[33m' _W=$'\033[1m' _Z=$'\033[0m'
else
    _R='' _G='' _B='' _Y='' _W='' _Z=''
fi

_info()    { printf "%s==>%s %s%s\n" "$_B$_W" "$_Z$_W" "$*" "$_Z"; }
_success() { printf "%s[ok]%s %s\n" "$_G" "$_Z" "$*"; }
_warn()    { printf "%s[!]%s %s\n" "$_Y" "$_Z" "$*" >&2; }
_error()   { printf "%s[error]%s %s\n" "$_R" "$_Z" "$*" >&2; }
_check()   { command -v "$1" > /dev/null 2>&1; }

# Canonical set of bin dirs where our installers drop binaries:
# uv → ~/.local/bin or ~/.cargo/bin; node/pnpm → Homebrew's bin on
# macOS (`install_node` runs `brew install node`, and pnpm then lands in
# that node's npm prefix) or the npm prefix's bin elsewhere. Prints a
# PATH value with these prepended.
#
# SINGLE SOURCE OF TRUTH: main()'s dependency checks AND start.sh (via
# `install.sh --print-path`) prepend the *same* dirs. Without this, a
# passing `--check-only` would not guarantee the tools are runnable
# where they are used — install.sh finds uv only because it bootstraps
# this PATH internally, and that export dies with the subprocess, so a
# login shell (or start.sh) that lacks ~/.local/bin can't run uv.
#
# Homebrew's bin is load-bearing, not belt-and-braces. A non-interactive
# `ssh host './restart.sh'` gets a minimal PATH with no /opt/homebrew/bin,
# so on the project's own documented macOS deployment target the check
# reported uv/node/pnpm "missing" while the server was at that moment
# running under /opt/homebrew/bin/uv. restart.sh stops before start.sh
# checks, so that false negative took the service DOWN and left it down.
# Resolve it from `brew` when available (honours a custom prefix) and
# fall back to the two canonical locations: /opt/homebrew (Apple
# Silicon) and /usr/local (Intel).
_compute_bootstrap_path() {
    local p="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

    # Homebrew bin dirs first — see the note above. `brew --prefix` is
    # only consulted if brew already resolves; a bare command
    # substitution of a missing command exits 127, which `2>/dev/null`
    # silences but does NOT neutralize under `set -e`.
    local brew_bins=() brew_prefix=''
    if command -v brew >/dev/null 2>&1; then
        brew_prefix="$(brew --prefix 2>/dev/null || true)"
        [[ -n "$brew_prefix" ]] && brew_bins+=("$brew_prefix/bin")
    fi
    brew_bins+=(/opt/homebrew/bin /usr/local/bin)
    local d
    for d in "${brew_bins[@]}"; do
        if [[ -d "$d" && ":$p:" != *":$d:"* ]]; then
            p="$d:$p"
        fi
    done

    # Only probe npm's prefix if npm resolves under the augmented PATH.
    # Same 127-under-`set -e` reasoning as above.
    local npm_bin=''
    if PATH="$p" command -v npm >/dev/null 2>&1; then
        npm_bin="$(PATH="$p" npm config get prefix 2>/dev/null || true)/bin"
    fi
    if [[ -n "$npm_bin" && -d "$npm_bin" && ":$p:" != *":$npm_bin:"* ]]; then
        p="$npm_bin:$p"
    fi
    printf '%s' "$p"
}

# `--print-path` is a pure query used by start.sh: emit the tool PATH
# and exit before any install side effect (no clone, no sudo, no deps).
if [[ "$PRINT_PATH" == true ]]; then
    _compute_bootstrap_path
    exit 0
fi

# -- Platform detection --
OS="unknown"
ARCH="$(uname -m 2>/dev/null || echo unknown)"
IS_WSL=false

detect_platform() {
    if [[ "${OSTYPE:-}" == darwin* ]]; then
        OS="macos"
    elif [[ -n "${WSL_DISTRO_NAME:-}" ]]; then
        OS="linux"
        IS_WSL=true
    elif [[ "${OSTYPE:-}" == linux-gnu* ]]; then
        OS="linux"
    fi

    if [[ "$OS" == "unknown" ]]; then
        _error "Unsupported platform: $(uname -s 2>/dev/null || echo unknown)"
        echo "This installer supports macOS and Linux (including WSL)." >&2
        echo "For native Windows, use WSL: https://learn.microsoft.com/en-us/windows/wsl/install" >&2
        exit 1
    fi

    local label="$OS"
    if [[ "$IS_WSL" == true ]]; then
        label="WSL ($WSL_DISTRO_NAME)"
    fi
    _success "Platform: $label ($ARCH)"
}

# -- Sudo handling (one-time prompt, cached) --
is_root() {
    [[ "$(id -u)" -eq 0 ]]
}

require_sudo() {
    if [[ "$OS" != "linux" ]]; then
        return 0
    fi
    if is_root; then
        return 0
    fi
    if command -v sudo &> /dev/null; then
        if ! sudo -n true >/dev/null 2>&1; then
            _info "Administrator privileges required; enter your password"
            sudo -v
        fi
        return 0
    fi
    _error "sudo is required for system installs on Linux"
    echo "  Install sudo or re-run as root." >&2
    exit 1
}

# -- npm permissions (user-local prefix on Linux) --
fix_npm_permissions() {
    if [[ "$OS" != "linux" ]]; then
        return 0
    fi
    if ! _check npm; then
        return 0
    fi
    local npm_prefix
    npm_prefix="$(npm config get prefix 2>/dev/null || true)"
    if [[ -z "$npm_prefix" ]]; then
        return 0
    fi
    # Already writable — no fix needed
    if [[ -w "$npm_prefix" || -w "$npm_prefix/lib" ]]; then
        return 0
    fi
    _info "Configuring npm for user-local installs"
    mkdir -p "$HOME/.npm-global"
    npm config set prefix "$HOME/.npm-global"
    # Add to shell rc files if not already present
    # shellcheck disable=SC2016
    local path_line='export PATH="$HOME/.npm-global/bin:$PATH"'
    for rc in "$HOME/.bashrc" "$HOME/.zshrc"; do
        if [[ -f "$rc" ]] && ! grep -q ".npm-global" "$rc"; then
            echo "$path_line" >> "$rc"
            _info "Added npm-global PATH to $rc"
        fi
    done
    export PATH="$HOME/.npm-global/bin:$PATH"
    _success "npm configured for user-local installs (~/.npm-global)"
}

# -- Linux package manager detection --
_apt()    { _check apt-get; }
_brew()   { _check brew; }
_pacman() { _check pacman; }
_dnf()    { _check dnf; }
_yum()    { _check yum; }
_apk()    { _check apk; }

# -- apt mirror fallback --
# If a configured apt source cannot be resolved or reached (observed:
# GitHub-hosted runners' DNS failing for *.ubuntu.com while every other
# domain resolved; also common for users whose default mirror is blocked),
# add the first reachable fallback mirror for this run only. All sources
# reachable -> nothing is written. Overridable for tests:
# APT_FALLBACK_ROOT (/etc/apt), APT_FALLBACK_OS_RELEASE (/etc/os-release),
# APT_FALLBACK_PROBE (a command run with a host, exit 0 = reachable),
# APT_FALLBACK_SKIP_UPDATE=1.
_APT_ROOT="${APT_FALLBACK_ROOT:-/etc/apt}"
_APT_FALLBACK_FILE="$_APT_ROOT/sources.list.d/vibe-seller-fallback.list"
_APT_MIRRORS_MAIN="$_APT_ROOT/vibe-seller-mirrors.txt"
_APT_MIRRORS_SECURITY="$_APT_ROOT/vibe-seller-mirrors-security.txt"
_apt_fallback_checked=false

_apt_host_reachable() {
    local host="$1"
    if [[ -n "${APT_FALLBACK_PROBE:-}" ]]; then
        "$APT_FALLBACK_PROBE" "$host"
        return
    fi
    timeout 3 getent hosts "$host" >/dev/null 2>&1 \
        && timeout 3 bash -c "exec 3<>/dev/tcp/$host/80" 2>/dev/null
}

# Every host the configured apt sources point at: classic `deb` lines,
# deb822 `URIs:` fields, and `mirror+file:` lists (GitHub runners).
_apt_source_hosts() {
    local files=() f
    for f in "$_APT_ROOT/sources.list" "$_APT_ROOT"/sources.list.d/*.list \
        "$_APT_ROOT"/sources.list.d/*.sources; do
        [[ -f "$f" && "$f" != "$_APT_FALLBACK_FILE" ]] && files+=("$f")
    done
    [[ ${#files[@]} -eq 0 ]] && return 0
    {
        cat "${files[@]}"
        grep -ohE 'mirror\+file:[^ ]+' "${files[@]}" 2>/dev/null \
            | sed 's/^mirror+file://' | while read -r m; do
                [[ -f "$m" ]] && cat "$m"
            done
    } | grep -vE '^[[:space:]]*#' \
        | grep -oE 'https?://[^/[:space:]]+' \
        | sed -E 's#https?://##; s#:.*##' | sort -u
}

_as_root() {  # run as root: directly when already root (containers), else sudo
    if [[ "$(id -u)" -eq 0 ]]; then "$@"; else sudo "$@"; fi
}

_as_root_write() {  # $1 = file; content on stdin
    if [[ -w "$(dirname "$1")" ]]; then
        cat > "$1"
    else
        _as_root tee "$1" >/dev/null
    fi
}

_ensure_apt_reachable() {
    [[ "$_apt_fallback_checked" == true ]] && return 0
    _apt_fallback_checked=true
    local osr="${APT_FALLBACK_OS_RELEASE:-/etc/os-release}"
    [[ -r "$osr" ]] || return 0
    local id like codename
    # shellcheck disable=SC1090  # os-release is a plain KEY=value file
    id=$(. "$osr"; echo "${ID:-}")
    like=$(. "$osr"; echo "${ID_LIKE:-}")
    codename=$(. "$osr"; echo "${VERSION_CODENAME:-${UBUNTU_CODENAME:-}}")
    local distro=""
    if [[ "$id" == ubuntu || " $like " == *" ubuntu "* ]]; then
        distro=ubuntu
        codename=$(. "$osr"; echo "${UBUNTU_CODENAME:-$codename}")
    elif [[ "$id" == debian || " $like " == *" debian "* ]]; then
        distro=debian
    fi
    [[ -n "$distro" && -n "$codename" ]] || return 0

    local host dead=""
    while read -r host; do
        [[ -z "$host" ]] && continue
        _apt_host_reachable "$host" || dead="$dead $host"
    done < <(_apt_source_hosts)
    [[ -z "$dead" ]] && return 0

    # Every reachable fallback goes into one apt mirror list, so a download
    # that fails on one mirror is retried on the next (apt's mirror+file
    # method) — observed: a mirror served the indexes, then timed out on
    # the packages ten minutes later.
    local mirror ok=()
    for mirror in mirrors.edge.kernel.org mirrors.aliyun.com; do
        _apt_host_reachable "$mirror" && ok+=("$mirror")
    done
    if [[ ${#ok[@]} -gt 0 ]]; then
        local main_path sec_path comps lines
        if [[ "$distro" == ubuntu ]]; then
            # amd64/i386 live under /ubuntu; every other arch (arm64...)
            # under /ubuntu-ports.
            local arch
            arch=$(dpkg --print-architecture 2>/dev/null || echo amd64)
            main_path=ubuntu
            [[ "$arch" == amd64 || "$arch" == i386 ]] || main_path=ubuntu-ports
            sec_path=$main_path
            comps="main restricted universe multiverse"
        else
            main_path=debian
            sec_path=debian-security
            comps="main contrib non-free non-free-firmware"
        fi
        for mirror in "${ok[@]}"; do
            printf 'http://%s/%s/\n' "$mirror" "$main_path"
        done | _as_root_write "$_APT_MIRRORS_MAIN"
        for mirror in "${ok[@]}"; do
            printf 'http://%s/%s/\n' "$mirror" "$sec_path"
        done | _as_root_write "$_APT_MIRRORS_SECURITY"
        lines="deb mirror+file:$_APT_MIRRORS_MAIN $codename $comps
deb mirror+file:$_APT_MIRRORS_MAIN $codename-updates $comps
deb mirror+file:$_APT_MIRRORS_SECURITY $codename-security $comps"
        printf '%s\n' "$lines" | _as_root_write "$_APT_FALLBACK_FILE"
        _warn "apt source(s) unreachable:$dead — using ${ok[*]} for this install"
        if [[ "${APT_FALLBACK_SKIP_UPDATE:-}" != 1 ]]; then
            _as_root apt-get update -qq || true
            _apt_updated=true
        fi
        return 0
    fi
    _warn "apt source(s) unreachable:$dead — no fallback mirror reachable either"
}

_apt_fallback_cleanup() {
    local f
    for f in "$_APT_FALLBACK_FILE" "$_APT_MIRRORS_MAIN" "$_APT_MIRRORS_SECURITY"; do
        [[ -f "$f" ]] || continue
        if [[ -w "$(dirname "$f")" ]]; then rm -f "$f"; else _as_root rm -f "$f"; fi
    done
}

_apt_updated=false
_apt_update() {
    _ensure_apt_reachable
    if [[ "$_apt_updated" == false ]]; then
        sudo apt-get update -qq
        _apt_updated=true
    fi
}

_pkg_install() {
    # Install a package using the first available package manager
    local pkg="$1"
    if _apt; then
        _apt_update
        sudo apt-get install -y -qq "$pkg"
    elif _pacman; then
        sudo pacman -S --noconfirm "$pkg"
    elif _dnf; then
        sudo dnf install -y -q "$pkg"
    elif _yum; then
        sudo yum install -y -q "$pkg"
    elif _apk; then
        sudo apk add --no-cache "$pkg"
    elif _brew; then
        brew install "$pkg"
    else
        return 1
    fi
}

print_usage() {
    cat <<'EOF'
Vibe Seller Installer

Default (end-user) mode:
  ./install.sh
  curl -sSL https://raw.githubusercontent.com/zpoint/vibe-seller/main/install.sh | bash

  Installs uv (if missing), runs `uv tool install vibe-seller`, then
  fetches Playwright Chromium. After this completes, run:

      vibe-seller

  The wheel ships the built frontend, so the web UI works without a
  clone.

Dev mode (--dev):
  ./install.sh --dev
  curl -sSL https://raw.githubusercontent.com/zpoint/vibe-seller/main/install.sh | bash -s -- --dev

  Installs system dependencies (curl, git, uv, node, pnpm, sqlite3,
  lsof, claude CLI), clones the repo if needed, creates ./.venv,
  runs `uv pip install -e .`, sets up Playwright, builds the frontend
  from source. For contributors.

Other flags:
  --test-pypi       Install from TestPyPI (https://test.pypi.org) instead
                    of PyPI. Used by the release pipeline to verify a
                    build before publishing to production. Dependencies
                    are still pulled from PyPI as a fallback index.
  --version <ver>   Pin to a specific version (e.g. `0.0.1` or `v0.0.1`).
                    Combine with --test-pypi to test a release candidate.
  --nightly         Install the nightly channel: the newest nightly build
                    (or stable, if a newer stable exists). `vibe-seller
                    upgrade` then keeps it on nightly; `vibe-seller
                    upgrade --stable` goes back.
  --check-only      Check dev-mode dependencies without installing.
                    Used by start.sh during local development.
  --print-path      Print the PATH (user-local tool dirs prepended)
                    and exit, without installing anything. start.sh
                    uses this so it resolves uv/pnpm exactly as the
                    dependency check did.
  --help, -h        Show this help.
EOF
}

# ============================================================
# Dependency: curl (needed by uv and node installers)
# ============================================================
check_curl() {
    if _check curl; then
        _success "curl found"
        return 0
    fi
    return 1
}

install_curl() {
    _info "Installing curl..."
    if [[ "$OS" == "macos" ]]; then
        _success "curl ships with macOS"
        return 0
    fi
    _pkg_install curl || {
        _error "Could not install curl"
        exit 1
    }
    _success "curl installed"
}

# ============================================================
# Dependency: git
# ============================================================
check_git() {
    if _check git; then
        _success "git $(git --version | awk '{print $3}')"
        return 0
    fi
    return 1
}

install_git() {
    _info "Installing git..."
    if [[ "$OS" == "macos" ]]; then
        # macOS: xcode-select provides git
        if ! xcode-select -p >/dev/null 2>&1; then
            xcode-select --install 2>/dev/null || true
            _warn "Xcode Command Line Tools installing — re-run after dialog completes"
            exit 1
        fi
    else
        _pkg_install git
    fi
    _success "git installed"
}

# ============================================================
# Dependency: uv
# ============================================================
check_uv() {
    if _check uv; then
        _success "uv $(uv --version 2>/dev/null | awk '{print $2}')"
        return 0
    fi
    return 1
}

install_uv() {
    _info "Installing uv..."
    # Pin uv version. uv 0.11.x has a regression where
    # `uv tool install --index-url <testpypi-url> vibe-seller==X.Y.devZ`
    # claims "no version of vibe-seller==X.Y.devZ" even when the
    # version is present in the simple index (--prerelease=allow and
    # --refresh don't help). 0.10.4 resolves the same spec correctly.
    # Bump this once a uv release fixes the regression.
    curl -LsSf https://astral.sh/uv/0.10.4/install.sh | sh
    # Reload PATH so uv is available immediately
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    if ! _check uv; then
        _error "uv install succeeded but binary not found on PATH"
        _warn "Add ~/.local/bin or ~/.cargo/bin to your PATH and re-run"
        exit 1
    fi
    _success "uv installed"
}

# ============================================================
# Dependency: node (>= 22)
# ============================================================
# pnpm@latest (v11+) imports `node:sqlite`, a built-in module added in
# Node 22. Older Node throws ERR_UNKNOWN_BUILTIN_MODULE on the very
# first `pnpm` invocation, so we require >=22 here. The apt installer
# below already pulls NodeSource setup_22.x; Homebrew is on Node 25+.
NODE_MIN_MAJOR=22

check_node() {
    if _check node; then
        local ver
        ver="$(node -v 2>/dev/null | sed 's/^v//')"
        local major
        major="$(echo "$ver" | cut -d. -f1)"
        if [[ "$major" -ge "$NODE_MIN_MAJOR" ]] 2>/dev/null; then
            _success "node v${ver}"
            return 0
        fi
        _warn "node v${ver} found but v${NODE_MIN_MAJOR}+ required"
        return 1
    fi
    return 1
}

install_node() {
    _info "Installing Node.js..."
    if [[ "$OS" == "macos" ]]; then
        if _brew; then
            brew install node
        else
            _error "Homebrew not found — install Homebrew first: https://brew.sh"
            exit 1
        fi
    else
        # Linux: try NodeSource for latest LTS
        if _apt; then
            local tmpdir
            tmpdir="$(mktemp -d)"
            curl -fsSL https://deb.nodesource.com/setup_22.x -o "$tmpdir/nodesource_setup.sh"
            sudo -E bash "$tmpdir/nodesource_setup.sh"
            rm -rf "$tmpdir"
            _apt_updated=true  # NodeSource setup already ran apt-get update
            # Ubuntu LTS ships nodejs 12 + libnode-dev which OWN
            # /usr/include/node/common.gypi and other paths that
            # nodejs 22 also installs. dpkg refuses to overwrite
            # files owned by another package, so the install fails
            # with "trying to overwrite ... which is also in
            # package libnode-dev". Purge the OS nodejs stack first
            # — NodeSource is the source of truth from here on.
            sudo apt-get remove -y -qq \
                nodejs nodejs-doc libnode-dev libnode72 npm 2>/dev/null || true
            sudo apt-get install -y -qq nodejs
        elif _pacman; then
            sudo pacman -S --noconfirm nodejs npm
        elif _dnf; then
            sudo dnf install -y -q nodejs
        elif _brew; then
            brew install node
        else
            _error "No supported package manager found for Node.js"
            _warn "Install manually: https://nodejs.org"
            exit 1
        fi
    fi
    _success "node installed: $(node -v 2>/dev/null)"
}

# ============================================================
# Dependency: pnpm
# ============================================================
check_pnpm() {
    if _check pnpm; then
        _success "pnpm $(pnpm --version 2>/dev/null)"
        return 0
    fi
    return 1
}

install_pnpm() {
    _info "Installing pnpm..."
    # fix_npm_permissions ensures npm global dir is user-writable on Linux
    # so we don't need sudo for npm install -g.
    #
    # We deliberately skip `corepack enable` here: it tries to symlink
    # into the corepack install's bin dir (typically `/usr/bin/` for
    # apt-installed Node), which fails with EACCES on hosts where the
    # user isn't root (e.g. self-hosted CI runners or any non-root
    # install). `npm install -g pnpm` honours the user-local prefix
    # we just configured and works without sudo on the same hosts.
    npm install -g pnpm
    # If fix_npm_permissions returned early (npm prefix already
    # writable, common when /usr/local is owned by the runner user),
    # nothing extended PATH; pnpm installed to the npm prefix's bin
    # which may not be on the caller's PATH. Add it explicitly so
    # the subsequent _check picks it up.
    local npm_bin
    npm_bin="$(npm config get prefix 2>/dev/null)/bin"
    if [[ -d "$npm_bin" && ":$PATH:" != *":$npm_bin:"* ]]; then
        export PATH="$npm_bin:$PATH"
    fi
    if ! _check pnpm; then
        _error "pnpm install failed"
        exit 1
    fi
    _success "pnpm installed: $(pnpm --version 2>/dev/null)"
}

# ============================================================
# Dependency: sqlite3
# ============================================================
check_sqlite3() {
    if _check sqlite3; then
        _success "sqlite3 $(sqlite3 --version 2>/dev/null | awk '{print $1}')"
        return 0
    fi
    return 1
}

install_sqlite3() {
    _info "Installing sqlite3..."
    if [[ "$OS" == "macos" ]]; then
        # sqlite3 ships with macOS, but just in case:
        if _brew; then
            brew install sqlite3
        else
            _warn "sqlite3 should ship with macOS — if missing, install Xcode CLT or Homebrew"
        fi
    else
        if _apt; then
            _apt_update
            sudo apt-get install -y -qq sqlite3
        elif _pacman; then
            sudo pacman -S --noconfirm sqlite
        elif _dnf; then
            sudo dnf install -y -q sqlite
        elif _brew; then
            brew install sqlite3
        else
            _error "No supported package manager found for sqlite3"
            exit 1
        fi
    fi
    _success "sqlite3 installed"
}

# ============================================================
# Dependency: lsof
# ============================================================
check_lsof() {
    if _check lsof; then
        _success "lsof found"
        return 0
    fi
    return 1
}

install_lsof() {
    _info "Installing lsof..."
    if [[ "$OS" == "macos" ]]; then
        _success "lsof ships with macOS"
        return 0
    fi
    _pkg_install lsof || {
        _error "Could not install lsof"
        exit 1
    }
    _success "lsof installed"
}

# ============================================================
# Dependency: claude CLI (optional)
# ============================================================
check_claude() {
    # Specifically check for the project-local install — that's what
    # the daemon's resolve_claude_binary() prefers, and the whole
    # point of this design is that vibe-seller owns the copy it runs,
    # whatever the global claude is. Returning true on "global exists"
    # would skip install_claude() for every existing user and leave
    # them on whatever global version they happen to have. The
    # daemon's PATH fallback is a runtime safety net, NOT a signal
    # that we don't need the local install. Use VIBE_HOME for the
    # same reason — must match the daemon (see app/config.py).
    local data_dir="${VIBE_HOME:-$HOME/.vibe-seller}"
    if [[ -x "$data_dir/node_modules/.bin/claude" ]]; then
        _success "claude CLI found (project-local, $data_dir/node_modules/.bin/claude)"
        return 0
    fi
    # System claude on PATH is acknowledged but does NOT satisfy the
    # check — install_claude() still runs to lay down the
    # project-local copy. Logged only as info so the user
    # understands why the install runs.
    if _check claude; then
        _info "system claude on PATH detected — installing a project-local copy alongside (does not affect global)"
    fi
    return 1
}

install_claude() {
    _info "Installing Claude Code CLI (project-local)..."
    if ! _check npm; then
        _warn "npm not available — skipping claude CLI install"
        return 1
    fi
    # Install Claude Code into ~/.vibe-seller/node_modules/, NOT
    # globally. The daemon resolves <VIBE_SELLER_DIR>/node_modules/.bin/
    # claude before the system PATH, so the version we install here is
    # the one every agent task uses — independent of whatever the user
    # has globally. `vibe-seller upgrade` moves it to the latest release.
    #
    # Match the daemon's resolver (VIBE_HOME — see app/config.py). If
    # this env var name drifts apart from the Python side, installer
    # and daemon disagree on the location and the project-local copy
    # silently isn't the one that runs.
    local data_dir="${VIBE_HOME:-$HOME/.vibe-seller}"
    mkdir -p "$data_dir"
    npm install --prefix "$data_dir" --no-save \
        @anthropic-ai/claude-code || {
        _warn "Claude CLI install failed (non-fatal)"
        return 1
    }
    _success "claude CLI installed (project-local)"
}

# ============================================================
# Playwright Chromium — download from the faster source
# ============================================================
# `playwright install chromium` fetches ~250 MB from cdn.playwright.dev,
# which is slow or unreachable from mainland China. npmmirror (Alibaba)
# mirrors the same files and is usually far faster there; elsewhere the
# official CDN tends to win. Measure both rather than guess from locale.
PLAYWRIGHT_MIRROR_HOST="https://cdn.npmmirror.com/binaries/playwright"

# The URLs `playwright install chromium` would fetch with
# PLAYWRIGHT_DOWNLOAD_HOST=$2 ('' = official CDN), one per line.
_pw_download_urls() {
    PLAYWRIGHT_DOWNLOAD_HOST="$2" "$1" install --dry-run chromium \
        2>/dev/null | awk '/Download url:/ {print $3}' | sort -u
}

# Bytes/sec fetching the first 4 MB of $1; 0 when it doesn't answer
# 200/206 in time.
_pw_probe_speed() {
    local out code speed
    out=$(curl -sL -r 0-4194303 -o /dev/null --connect-timeout 5 \
        --max-time 10 -w '%{http_code} %{speed_download}' "$1" \
        2>/dev/null) || true
    code=${out%% *}
    speed=${out##* }
    speed=${speed%%.*}
    case "$code" in
        200|206) ;;
        *) speed=0 ;;
    esac
    case "$speed" in
        ''|*[!0-9]*) speed=0 ;;
    esac
    echo "$speed"
}

# Echo the PLAYWRIGHT_DOWNLOAD_HOST to use ('' = official CDN). A source
# qualifies only if EVERY file this Playwright version would fetch is
# there — the mirror's path layout has changed across Playwright
# versions, and a half-matching mirror would fail mid-install — then
# the faster qualifying source wins. Progress goes to stderr.
_pick_playwright_host() {
    local pw="$1" host label urls u code ok speed best="" best_speed=-1
    for host in "" "$PLAYWRIGHT_MIRROR_HOST"; do
        label=${host:-cdn.playwright.dev}
        urls=$(_pw_download_urls "$pw" "$host") || urls=""
        if [ -z "$urls" ]; then
            _warn "  $label: could not list the files to download"
            continue
        fi
        ok=1
        for u in $urls; do
            code=$(curl -sIL -o /dev/null --connect-timeout 5 \
                --max-time 10 -w '%{http_code}' "$u" 2>/dev/null) \
                || code=000
            if [ "$code" != 200 ]; then
                ok=0
                break
            fi
        done
        if [ "$ok" != 1 ]; then
            _info "  $label: unavailable (HTTP $code for ${u##*/})" >&2
            continue
        fi
        speed=$(_pw_probe_speed "$(echo "$urls" | grep -m1 '/chrome-' \
            || echo "$urls" | head -n1)")
        _info "  $label: $(awk -v s="$speed" \
            'BEGIN { printf "%.1f", s / 1048576 }') MB/s" >&2
        if [ "$speed" -gt "$best_speed" ]; then
            best="$host"
            best_speed="$speed"
        fi
    done
    echo "$best"
}

# Install Playwright's Chromium (with its system libs on Linux) from the
# faster source. A PLAYWRIGHT_DOWNLOAD_HOST the user already exported
# wins, unprobed. If the mirror was picked but fails mid-install, retry
# once from the official CDN — the probe may only make an install
# faster, never break one that would have worked.
_install_playwright_chromium() {
    local pw="$1" host
    local args=(install chromium)
    if [[ "$OS" == "linux" ]]; then
        args=(install --with-deps chromium)
        _ensure_apt_reachable
    fi
    if [ -n "${PLAYWRIGHT_DOWNLOAD_HOST:-}" ]; then
        _info "Downloading Chromium from $PLAYWRIGHT_DOWNLOAD_HOST" \
            "(PLAYWRIGHT_DOWNLOAD_HOST)"
        "$pw" "${args[@]}"
        return
    fi
    _info "Checking which Chromium download source is faster..."
    host=$(_pick_playwright_host "$pw")
    if [ -z "$host" ]; then
        _info "Downloading Chromium from cdn.playwright.dev"
        "$pw" "${args[@]}"
        return
    fi
    _info "Downloading Chromium from $host"
    if PLAYWRIGHT_DOWNLOAD_HOST="$host" "$pw" "${args[@]}"; then
        return 0
    fi
    _warn "Download from $host failed — retrying from cdn.playwright.dev"
    "$pw" "${args[@]}"
}

# ============================================================
# Default (end-user) install path
# ============================================================
# Pulls `vibe-seller` from PyPI via `uv tool install`. The wheel ships
# the built frontend so the web UI works without a clone. Then
# downloads the Playwright Chromium binary (which ships separately
# from the Python package). Skipped if --dev is set.
_install_via_pip() {
    _info "Installing vibe-seller from PyPI"
    echo ""

    if ! check_curl; then
        install_curl
    fi
    if ! check_uv; then
        install_uv
    fi

    # `playwright install --with-deps` on Linux (and WSL, which we
    # detect as OS=linux) apt-installs Chromium's system libs — needs
    # sudo. macOS skips the --with-deps branch below, so no sudo
    # prompt there.
    if [[ "$OS" == "linux" ]]; then
        require_sudo
    fi

    # Runtime deps the daemon's agents need but the wheel can't ship:
    # claude CLI (the AI backend's subprocess), sqlite3 (agents query
    # per-account email DBs via `sqlite3 <path>`), node (claude CLI is
    # an npm package), lsof (vibe-seller stop). Without these,
    # `vibe-seller start` succeeds but the first agent task fails
    # with FileNotFoundError. --dev mode installs them via main()'s
    # DEPS loop; the default mode `exit 0`s before reaching that loop,
    # so the deps have to be installed here explicitly. This block
    # was dropped in an install.sh refactor that introduced a
    # half-working default install, observed as a CI verify-testpypi
    # failure on v0.0.6 (FileNotFoundError on `claude` spawn).
    if ! check_lsof; then
        install_lsof
    fi
    if ! check_sqlite3; then
        install_sqlite3
    fi
    if ! check_node; then
        install_node
    fi
    fix_npm_permissions
    if ! check_claude; then
        install_claude \
            || _warn "claude CLI install failed — AI agent features will not work"
    fi

    # Compose `uv tool install` args from --test-pypi / --version.
    # TestPyPI only hosts our project — pull dependencies (FastAPI,
    # uvicorn, etc.) from real PyPI via --extra-index-url so the
    # install doesn't fail on missing deps.
    if [[ "$TEST_PYPI" == true ]]; then
        # Download the wheel from TestPyPI directly and install from a
        # local file. We deliberately bypass uv's resolver here:
        #   - `--prerelease=allow` is needed for `==X.dev` pins, but
        #     leaks into transitive deps (drags apscheduler 4.0.0aN).
        #   - uv 0.10.4 + `--refresh` on Linux has been observed
        #     short-circuiting before hitting the network and claiming
        #     "no version found" 200ms in, even when the simple index
        #     clearly lists the version.
        # Installing from a wheel file sidesteps both: uv installs the
        # given file, then resolves transitive deps from default PyPI
        # (stable releases only).
        if [[ -z "$VERSION" ]]; then
            _error "--test-pypi requires --version <ver> to identify the wheel to download"
            exit 1
        fi
        _info "Source: TestPyPI (wheel download for vibe-seller==$VERSION)"
        local wheel_url tmp_wheel
        wheel_url=$(
            curl -fsS "https://test.pypi.org/pypi/vibe-seller/$VERSION/json" \
                | python3 -c "import sys,json; print(next(u['url'] for u in json.load(sys.stdin)['urls'] if u['packagetype']=='bdist_wheel'))"
        ) || { _error "couldn't find a wheel for vibe-seller==$VERSION on TestPyPI"; exit 1; }
        # Save under the wheel's REAL filename — uv reads the package
        # name/version/tags from the filename, so a mangled mktemp
        # name like `vibe-seller-kWBzRr.whl` is rejected with
        # "Must have an ABI tag".
        local tmp_dir wheel_filename tmp_wheel
        tmp_dir="$(mktemp -d -t vibe-seller-wheel-XXXXXX)"
        wheel_filename="$(basename "$wheel_url")"
        tmp_wheel="$tmp_dir/$wheel_filename"
        _info "Downloading $wheel_url"
        curl -fsSL "$wheel_url" -o "$tmp_wheel" || { _error "wheel download failed"; rm -rf "$tmp_dir"; exit 1; }
        _info "Running 'uv tool install $tmp_wheel'"
        uv tool install "$tmp_wheel"
        rm -rf "$tmp_dir"
        return 0
    fi

    local spec="vibe-seller"
    if [[ "$NIGHTLY" == true ]]; then
        if [[ -n "$VERSION" ]]; then
            _error "--nightly picks the version itself; drop --version"
            exit 1
        fi
        # Pin the exact version rather than `--prerelease=allow`, which
        # would also let alpha builds of dependencies in (see above).
        # Same pick as `vibe-seller upgrade`: the highest stable or
        # nightly (`X.Y.Z.dev<timestamp>`) release on PyPI. Run through
        # uv's Python: a fresh machine may have no python3 on PATH, and
        # the default install never needs one.
        VERSION=$(
            curl -fsS https://pypi.org/pypi/vibe-seller/json \
                | uv run --no-project --quiet python -c '
import json, re, sys
rels = json.load(sys.stdin)["releases"]
def key(v):
    m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:\.dev(\d{8,}))?", v)
    if not m:
        return None
    a, b, c, dev = m.groups()
    return (int(a), int(b), int(c), 0 if dev else 1, int(dev or 0))
live = [v for v, f in rels.items() if f and not all(x.get("yanked") for x in f)]
ok = [v for v in live if key(v)]
print(max(ok, key=key) if ok else "")
'
        ) || { _error "couldn't read vibe-seller releases from PyPI"; exit 1; }
        if [[ -z "$VERSION" ]]; then
            _error "no vibe-seller release found on PyPI"
            exit 1
        fi
        _info "Channel: nightly (newest: $VERSION)"
        # A stable release newer than every nightly installs unpinned:
        # uv records the requirement, and a `==` pin would freeze every
        # later `uv tool upgrade`. Nightlies must stay pinned.
        if [[ "$VERSION" != *.dev* ]]; then
            VERSION=""
        fi
    fi
    if [[ -n "$VERSION" ]]; then
        spec="vibe-seller==$VERSION"
        _info "Version pin: $VERSION"
    fi

    _info "Running 'uv tool install $spec'"
    uv tool install "$spec"

    _info "Installing Playwright Chromium browser"
    # Call playwright from the tool's installed venv directly.
    # `uv tool run --from vibe-seller` would re-resolve vibe-seller
    # against the default index, which fails for --test-pypi installs
    # (vibe-seller isn't on real PyPI yet during a release-pipeline
    # verify step). Direct path skips resolution entirely — playwright
    # is already in the venv as a vibe-seller dep.
    local tool_dir
    tool_dir="$(uv tool dir 2>/dev/null)/vibe-seller"
    if [ ! -x "$tool_dir/bin/playwright" ]; then
        _error "Couldn't find $tool_dir/bin/playwright after install"
        exit 1
    fi
    _install_playwright_chromium "$tool_dir/bin/playwright"

    echo ""
    _success "Vibe Seller installed!"
    cat <<EOF

Make sure ${_W}~/.local/bin${_Z} is on your PATH (uv tool puts binaries
there), then start the server with:

    ${_W}vibe-seller start${_Z}

Open ${_W}http://localhost:7777${_Z}.

Upgrade later:    ${_W}vibe-seller upgrade${_Z}
Uninstall later:  ${_W}uv tool uninstall vibe-seller${_Z}

EOF
}

# ============================================================
# Main
# ============================================================
main() {
    if [[ "$HELP" == true ]]; then
        print_usage
        exit 0
    fi

    if [[ "$APT_FALLBACK" == true ]]; then
        _ensure_apt_reachable
        exit 0
    fi
    # A fallback mirror added for this run is removed when it ends.
    trap _apt_fallback_cleanup EXIT

    echo ""
    if [[ "$DEV" == true ]]; then
        _info "Vibe Seller — dev dependency check"
    else
        _info "Vibe Seller — installing from PyPI"
    fi
    echo ""

    detect_platform

    # Default (end-user) path: pip install from PyPI, done.
    if [[ "$DEV" == false ]]; then
        _install_via_pip
        exit 0
    fi

    # --dev path follows: validate sudo, install system deps, set up venv.
    # On Linux, validate sudo access once upfront (cached for session)
    if [[ "$CHECK_ONLY" == false ]]; then
        require_sudo
    fi

    # Bootstrap PATH with common user-local binary dirs so check_*
    # functions find tools installed by previous runs even in
    # non-interactive shells (SSH sessions, WSL launched from Windows,
    # CI runners). start.sh applies the IDENTICAL set via `--print-path`
    # so a passing check guarantees the tools are runnable there too.
    export PATH="$(_compute_bootstrap_path)"
    # In GitHub Actions, PATH exports inside a script don't persist to
    # subsequent steps. When we find tools via the bootstrap above and skip
    # their installers (which would normally write to $GITHUB_PATH), register
    # the paths ourselves so the next step can find them.
    if [[ -n "${GITHUB_PATH:-}" ]]; then
        echo "$HOME/.local/bin" >> "$GITHUB_PATH"
        echo "$HOME/.cargo/bin" >> "$GITHUB_PATH"
        local _npm_bin=""
        if _check npm; then
            _npm_bin="$(npm config get prefix 2>/dev/null || true)/bin"
        fi
        [[ -n "$_npm_bin" && -d "$_npm_bin" ]] && echo "$_npm_bin" >> "$GITHUB_PATH"
    fi

    # Required dependencies
    local FAILED=0
    local DEPS=(curl git uv node pnpm sqlite3 lsof)

    for dep in "${DEPS[@]}"; do
        if ! "check_${dep}"; then
            if [[ "$CHECK_ONLY" == true ]]; then
                FAILED=$((FAILED + 1))
            else
                "install_${dep}"
                # Re-check after install
                if ! "check_${dep}"; then
                    _error "${dep} still not available after install attempt"
                    FAILED=$((FAILED + 1))
                fi
            fi
        fi
        # After node is available, fix npm permissions before pnpm install
        if [[ "$dep" == "node" && "$CHECK_ONLY" == false ]]; then
            fix_npm_permissions
        fi
    done

    # Optional: claude CLI
    echo ""
    if ! check_claude; then
        if [[ "$CHECK_ONLY" == true ]]; then
            _warn "claude CLI not found — AI agent features will not work"
            _warn "Install: ./install.sh (installs the project-local copy)"
        else
            _info "Claude CLI is optional (needed for AI agent features)"
            # Auto-install only if npm is available; don't prompt
            install_claude || true
        fi
    fi

    echo ""
    if [[ "$FAILED" -gt 0 ]]; then
        if [[ "$CHECK_ONLY" == true ]]; then
            _error "Missing ${FAILED} required tool(s). Run ./install.sh to install them."
        else
            _error "${FAILED} tool(s) could not be installed."
        fi
        exit 1
    fi

    # Python venv + deps (from source)
    # Override venv location: VIBE_SELLER_VENV=/path/to/venv ./install.sh
    local VENV_DIR="${VIBE_SELLER_VENV:-$SCRIPT_DIR/.venv}"
    if [[ "$CHECK_ONLY" == false && -f "$SCRIPT_DIR/pyproject.toml" ]]; then
        if ! [ -f "$VENV_DIR/bin/activate" ]; then
            _info "Creating Python virtual environment at $VENV_DIR..."
            uv venv --python ">=3.11" "$VENV_DIR"
        fi
        _info "Installing Python dependencies (editable)..."
        # shellcheck disable=SC1091
        source "$VENV_DIR/bin/activate"
        uv pip install -e "$SCRIPT_DIR" --quiet
        _success "Python deps installed — 'vibe-seller' CLI available in venv"

        # Install Playwright browser binaries (required for
        # ChromeBackend)
        if [ -f "$VENV_DIR/bin/playwright" ]; then
            _info "Installing Playwright browser binaries..."
            _install_playwright_chromium "$VENV_DIR/bin/playwright" \
                || _warn "Playwright browser install failed"
            _success "Playwright browsers installed"
        fi

        # Build the frontend bundle so the dev checkout matches what
        # `pip install vibe-seller` ships (a wheel with prebuilt
        # static assets under app/static/). README documents
        # `./install.sh --dev` as the line that does this; start.sh's
        # own pnpm-build step then becomes a no-op until a source
        # file changes. Only runs when pnpm is on PATH (it should be
        # — installed in the DEPS loop above).
        if [ -f "$SCRIPT_DIR/frontend/package.json" ] \
            && _check pnpm; then
            _info "Building frontend (pnpm build → frontend/dist/)..."
            (cd "$SCRIPT_DIR/frontend" \
                && pnpm install --frozen-lockfile \
                && pnpm build) \
                || _warn "Frontend build failed — start.sh will retry"
        fi
    elif [[ "$CHECK_ONLY" == true && -f "$SCRIPT_DIR/pyproject.toml" ]]; then
        if [ -f "$VENV_DIR/bin/vibe-seller" ]; then
            _success "vibe-seller CLI registered"
        else
            _warn "Python venv not set up — run ./install.sh to create it"
        fi
    fi

    _success "All required dependencies are installed!"
    echo ""
}

# Tests source this file for its functions without running the installer.
if [[ "${VIBE_SELLER_INSTALL_SH_NO_MAIN:-}" != 1 ]]; then
    main
fi
