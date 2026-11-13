"""Resolve the floating bundle pins (Claude Code CLI, Playwright) for a build.

The repo leaves both at ``latest`` (``app/bundle_pins.json``) and
``playwright`` bare in ``pyproject.toml``. A build calls this once,
before it builds anything, and every artifact it produces then carries
the same exact versions: the wheel requires ``playwright==X`` and ships
a manifest naming the claude CLI, and the installer bundles that claude
and the Chromium of that Playwright (``app/bundle.py``).

Which version each pin gets, first match wins:

1. ``--claude`` / ``--playwright``;
2. ``BUNDLE_CLAUDE_VERSION`` / ``BUNDLE_PLAYWRIGHT_VERSION`` — what CI
   sets from a workflow input or repository variable;
3. a version the tree already carries (a build step that runs after a
   workflow resolved the tree keeps that answer);
4. the newest release: npm's ``latest`` tag for
   ``@anthropic-ai/claude-code`` (what ``vibe-seller upgrade`` installs
   from on macOS/Linux, and what the official installer serves), PyPI's
   newest ``playwright``.

``latest`` or an empty value at steps 1–2 means "no override".

    python scripts/resolve_bundle_pins.py
    python scripts/resolve_bundle_pins.py --claude 2.1.323

Prints the result; with ``GITHUB_OUTPUT`` set it also writes
``claude=`` / ``playwright=`` outputs, so a release resolves once and
hands the same answer to every job that builds. Exit 2 when a version
cannot be resolved or is malformed — a build must not guess.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import bundle  # noqa: E402 — the repo root goes on sys.path above

NPM_LATEST = 'https://registry.npmjs.org/@anthropic-ai%2fclaude-code/latest'
PYPI_PLAYWRIGHT = 'https://pypi.org/pypi/playwright/json'


def _get_json(url: str) -> dict:
    last: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                return json.loads(resp.read().decode('utf-8'))
        except (OSError, ValueError) as exc:
            last = exc
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f'could not read {url}: {last}')


def newest(name: str) -> str:
    if name == 'claude_code':
        return str(_get_json(NPM_LATEST)['version'])
    return str(_get_json(PYPI_PLAYWRIGHT)['info']['version'])


def _override(value: str | None) -> str | None:
    value = (value or '').strip()
    return None if value in ('', bundle.LATEST) else value


def choose(
    name: str,
    arg: str | None,
    env: dict[str, str],
    current: str,
    fetch=newest,
) -> tuple[str, str]:
    """``(version, where it came from)`` for one floating pin."""
    explicit = _override(arg)
    if explicit:
        return bundle.checked_version(name, explicit), 'argument'
    from_env = _override(env.get(bundle.PIN_ENV[name]))
    if from_env:
        return (
            bundle.checked_version(name, from_env),
            bundle.PIN_ENV[name],
        )
    if current not in ('', bundle.LATEST):
        return bundle.checked_version(name, current), 'already resolved'
    return bundle.checked_version(name, fetch(name)), 'newest release'


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--claude', help='exact Claude Code CLI version')
    parser.add_argument('--playwright', help='exact Playwright version')
    parser.add_argument(
        '--root',
        type=Path,
        default=bundle.repo_root(),
        help='the tree to stamp (default: this checkout)',
    )
    args = parser.parse_args(argv)
    try:
        pins = bundle.load_pins(args.root)
        chosen: dict[str, str] = {}
        for name, arg in (
            ('claude_code', args.claude),
            ('playwright', args.playwright),
        ):
            current = str(pins[name]['version'])
            version, source = choose(name, arg, dict(os.environ), current)
            chosen[name] = version
            print(f'{name} {version} ({source})')
        bundle.stamp_pins(args.root, chosen)
    except (bundle.PinMismatch, RuntimeError, ValueError, OSError) as exc:
        print(f'cannot resolve the bundle pins: {exc}', file=sys.stderr)
        return 2
    out = os.environ.get('GITHUB_OUTPUT')
    if out:
        with open(out, 'a', encoding='utf-8') as fh:
            fh.write(f'claude={chosen["claude_code"]}\n')
            fh.write(f'playwright={chosen["playwright"]}\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
