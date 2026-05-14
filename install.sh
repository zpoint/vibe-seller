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
#   --check-only      Check dev-mode dependencies without installing.
#                     Implies --dev (only meaningful in a clone).
#   --help, -h        Print this help and exit.

# -- Parse flags first (bootstrap behaviour depends on --dev) --
DEV=false
CHECK_ONLY=false
HELP=false
TEST_PYPI=false
VERSION=""
# `while + shift` so we can take an argument after --version. Other
# flags stay positional-agnostic.
while [ $# -gt 0 ]; do
    case "$1" in
        --dev)         DEV=true ;;
        --check-only)  CHECK_ONLY=true; DEV=true ;;  # check-only is dev-only
        --test-pypi)   TEST_PYPI=true ;;
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

_apt_updated=false
_apt_update() {
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
  --check-only      Check dev-mode dependencies without installing.
                    Used by start.sh during local development.
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
# Dependency: node (>= 18)
# ============================================================
NODE_MIN_MAJOR=18

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
    # so we don't need sudo for npm install -g
    if _check corepack; then
        corepack enable 2>/dev/null || true
        corepack prepare pnpm@latest --activate 2>/dev/null || npm install -g pnpm
    else
        npm install -g pnpm
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
    if _check claude; then
        _success "claude CLI found"
        return 0
    fi
    return 1
}

install_claude() {
    _info "Installing Claude Code CLI..."
    if ! _check npm; then
        _warn "npm not available — skipping claude CLI install"
        return 1
    fi
    # npm global prefix is user-writable after fix_npm_permissions
    npm install -g @anthropic-ai/claude-code || {
        _warn "Claude CLI install failed (non-fatal)"
        return 1
    }
    _success "claude CLI installed"
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
    if [[ "$OS" == "linux" ]]; then
        "$tool_dir/bin/playwright" install --with-deps chromium
    else
        "$tool_dir/bin/playwright" install chromium
    fi

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
            _warn "Install: ./install.sh  or  npm install -g @anthropic-ai/claude-code"
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
            if [[ "$OS" == "linux" ]]; then
                "$VENV_DIR/bin/playwright" install \
                    --with-deps chromium \
                    || _warn "Playwright browser install failed"
            else
                "$VENV_DIR/bin/playwright" install chromium \
                    || _warn "Playwright browser install failed"
            fi
            _success "Playwright browsers installed"
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

main
