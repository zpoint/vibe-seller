#!/bin/bash
# Every dependency must install from a prebuilt wheel on every platform
# install.sh supports. A user machine has no Rust/C toolchain, so a
# dependency whose newest release drops a platform's wheel turns
# `uv tool install vibe-seller` into a source build that fails there.
# (cryptography 49 dropped Intel-macOS wheels; an Intel Mac could not
# install vibe-seller at all.)
#
# For each platform this resolves pyproject.toml twice: as a user's
# install resolves it, and wheels-only. The two must pick the same
# versions; a difference names the package that would be built from
# source. Resolution is cross-platform, so one Linux runner checks all.
#
#   scripts/check_wheel_coverage.sh [pyproject.toml]
set -euo pipefail

PROJECT="${1:-pyproject.toml}"
PLATFORMS=(
    x86_64-apple-darwin
    aarch64-apple-darwin
    x86_64-manylinux_2_31
    aarch64-manylinux_2_31
    x86_64-pc-windows-msvc
)
PYTHONS=(3.11 3.13)

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

fail=0
for py in "${PYTHONS[@]}"; do
    for plat in "${PLATFORMS[@]}"; do
        # Fresh files per run: `uv pip compile -o` reads an existing
        # output file as version preferences, which would carry one
        # platform's pins into the next.
        any="$tmp/$plat-$py-any.txt" whl="$tmp/$plat-$py-whl.txt"
        uv pip compile "$PROJECT" --quiet --no-header --no-annotate \
            --python-version "$py" --python-platform "$plat" -o "$any"
        if ! uv pip compile "$PROJECT" --quiet --no-header \
            --no-annotate --only-binary :all: \
            --python-version "$py" --python-platform "$plat" -o "$whl"
        then
            echo "FAIL $plat py$py: no wheels-only resolution exists"
            fail=1
            continue
        fi
        if ! diff_out=$(diff "$any" "$whl"); then
            echo "FAIL $plat py$py: these would build from source:"
            grep '^<' <<<"$diff_out" | sed 's/^< /    /'
            fail=1
        else
            echo "ok   $plat py$py"
        fi
    done
done
exit "$fail"
