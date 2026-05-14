"""Google Workspace CLI (`gws`) optional skill bundle.

Off by default.  When the user enables the toggle in Settings, we:
  1. Verify `gws` is on $PATH and authenticated (`gws auth status`).
  2. Shell out to `gws generate-skills --output-dir <tmp>`.
  3. Filter to the Amazon-seller core subset (see GWS_SUBSET).
  4. Rewrite cross-refs (`../gws-<x>/SKILL.md` → `../<x>/SKILL.md`)
     so the 19 sub-skills slot under one umbrella folder.
  5. Write an umbrella `gws/SKILL.md` that catalogs the sub-skills.
  6. Atomic swap into ~/.vibe-seller/.claude/skills/gws/.

Layout after enable:

    ~/.vibe-seller/.claude/skills/gws/
    ├── SKILL.md              ← umbrella + catalog
    ├── shared/SKILL.md
    ├── sheets/SKILL.md
    ├── ... (19 sub-skills total)
    └── calendar-insert/SKILL.md

The agent sees exactly ONE `gws` skill in its short index (~20 tokens
always-on), then reads sub-SKILL.md files on demand via the Read tool.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
import re
import shutil
import tempfile

from app.workspace.manager import VIBE_SELLER_DIR

logger = logging.getLogger(__name__)

# Source-name (as emitted by `gws generate-skills`) → destination
# subdir name (stripped `gws-` prefix) under the umbrella gws/ dir.
# This doubles as the allowlist — any dir not in this dict is discarded.
GWS_SUBSET: dict[str, str] = {
    'gws-shared': 'shared',
    # Sheets — inventory, reports, price tracking
    'gws-sheets': 'sheets',
    'gws-sheets-append': 'sheets-append',
    'gws-sheets-read': 'sheets-read',
    # Drive — product media, invoices, brand certs
    'gws-drive': 'drive',
    'gws-drive-upload': 'drive-upload',
    # Gmail — supplier + customer email
    'gws-gmail': 'gmail',
    'gws-gmail-forward': 'gmail-forward',
    'gws-gmail-read': 'gmail-read',
    'gws-gmail-reply': 'gmail-reply',
    'gws-gmail-reply-all': 'gmail-reply-all',
    'gws-gmail-send': 'gmail-send',
    'gws-gmail-triage': 'gmail-triage',
    'gws-gmail-watch': 'gmail-watch',
    # Docs — listing copy, SOPs
    'gws-docs': 'docs',
    'gws-docs-write': 'docs-write',
    # Calendar — launches, promos
    'gws-calendar': 'calendar',
    'gws-calendar-agenda': 'calendar-agenda',
    'gws-calendar-insert': 'calendar-insert',
}

_UMBRELLA_BODY = """---
name: gws
description: "Google Workspace (Sheets, Drive, Gmail, Docs, Calendar) via the gws CLI. Read ./shared/SKILL.md first for auth and global flags; then open the specific sub-skill you need."
allowed-tools: Bash(gws:*), Read
---

# Google Workspace CLI (umbrella)

All commands run through the `gws` binary. Before using any sub-skill,
read `./shared/SKILL.md` for authentication, global flags, and
output-format rules.

## Sub-skills (read via the Read tool when needed)

| Purpose | File |
|---------|------|
| Shared auth, flags, security | `./shared/SKILL.md` |
| Sheets — full API reference | `./sheets/SKILL.md` |
| Sheets — append a row | `./sheets-append/SKILL.md` |
| Sheets — read values | `./sheets-read/SKILL.md` |
| Drive — files/folders/shared drives | `./drive/SKILL.md` |
| Drive — upload a file | `./drive-upload/SKILL.md` |
| Gmail — full API reference | `./gmail/SKILL.md` |
| Gmail — send | `./gmail-send/SKILL.md` |
| Gmail — read a message | `./gmail-read/SKILL.md` |
| Gmail — reply | `./gmail-reply/SKILL.md` |
| Gmail — reply-all | `./gmail-reply-all/SKILL.md` |
| Gmail — forward | `./gmail-forward/SKILL.md` |
| Gmail — unread inbox summary | `./gmail-triage/SKILL.md` |
| Gmail — watch for new mail (NDJSON stream) | `./gmail-watch/SKILL.md` |
| Docs — full API reference | `./docs/SKILL.md` |
| Docs — append text | `./docs-write/SKILL.md` |
| Calendar — full API reference | `./calendar/SKILL.md` |
| Calendar — agenda across calendars | `./calendar-agenda/SKILL.md` |
| Calendar — create event | `./calendar-insert/SKILL.md` |

## Typical Amazon-seller flows

- **Inventory sync**: `./sheets/SKILL.md` — `gws sheets spreadsheets.values update` / `+append` / `+read`.
- **Invoice archive**: `./drive-upload/SKILL.md` — `gws drive +upload --file invoice.pdf`.
- **Supplier email**: `./gmail-send/SKILL.md` + `./gmail-reply/SKILL.md`.
- **Listing copy updates**: `./docs-write/SKILL.md`.
- **Launch calendar**: `./calendar-insert/SKILL.md`.
"""

# Match `../gws-<name>/SKILL.md` inside Markdown/text content.
# We only rewrite `../gws-X/SKILL.md` style paths; we leave prose
# that mentions the string "gws-sheets" without a path prefix alone.
_CROSS_REF_RE = re.compile(r'\.\./gws-([a-z0-9][a-z0-9-]*)/SKILL\.md')


def _gws_dir() -> Path:
    """Destination dir for the umbrella."""
    return VIBE_SELLER_DIR / '.claude' / 'skills' / 'gws'


def _skills_dir() -> Path:
    """Parent .claude/skills dir (needed for atomic staging)."""
    return VIBE_SELLER_DIR / '.claude' / 'skills'


# ── Prereq checks ──────────────────────────────────────


async def _run(
    cmd: list[str],
    *,
    env: dict | None = None,
    timeout: float = 15.0,
    cwd: str | None = None,
) -> tuple[int, str, str]:
    """Run a subprocess, return (rc, stdout, stderr)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=cwd,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
        return (
            proc.returncode or 0,
            stdout.decode(errors='replace'),
            stderr.decode(errors='replace'),
        )
    except TimeoutError:
        return 124, '', 'timeout'
    except FileNotFoundError:
        return 127, '', 'not found'
    except Exception as e:
        return 1, '', str(e)


def _parse_auth_status(stdout: str) -> dict:
    """Parse `gws auth status` JSON (ignoring non-JSON preamble).

    The CLI prints `Using keyring backend: ...` lines before the
    JSON object, so we find the first `{` and parse from there.
    Returns an empty dict if JSON can't be located/parsed.
    """
    start = stdout.find('{')
    if start < 0:
        return {}
    try:
        return json.loads(stdout[start:])
    except json.JSONDecodeError:
        return {}


def _auth_state_from_json(data: dict) -> tuple[bool, str | None, dict]:
    """Determine usable-auth state from parsed `gws auth status`.

    `gws auth status` always exits 0 — usability is signalled via
    JSON fields. Returns (usable, reason, detail) where:

    - usable: True iff credentials can be decrypted/read on this
      machine. Relies on the same fields the CLI exposes to agents
      (see gws source: handle_status in auth_commands.rs).
    - reason: short human-readable status bucket (used by the UI).
    - detail: whitelisted fields worth surfacing to the frontend
      (account hint, storage type, keyring backend, project id).
    """
    if not data:
        return False, 'unparseable', {}

    auth_method = data.get('auth_method', 'none')
    if auth_method == 'none':
        return (
            False,
            'not_logged_in',
            {
                'auth_method': 'none',
                'needs_login': True,
            },
        )

    has_env_token = bool(data.get('token_env_var'))
    has_plain = bool(data.get('plain_credentials_exists'))
    has_encrypted = bool(data.get('encrypted_credentials_exists'))
    encryption_valid = bool(data.get('encryption_valid'))

    detail = {
        'auth_method': auth_method,
        'storage': data.get('storage'),
        'keyring_backend': data.get('keyring_backend'),
        'project_id': data.get('project_id'),
        'account_hint': (data.get('client_id') or data.get('config_client_id')),
        'token_cache_exists': bool(data.get('token_cache_exists')),
    }

    if has_env_token or has_plain or (has_encrypted and encryption_valid):
        return True, 'logged_in', detail

    # Credentials exist but can't be read on this machine — most
    # common when the keyring-encrypted file was created elsewhere.
    if has_encrypted and not encryption_valid:
        detail['needs_relogin'] = True
        detail['encryption_error'] = data.get('encryption_error')
        return False, 'encryption_invalid', detail

    return False, 'not_logged_in', {**detail, 'needs_login': True}


async def check_status() -> dict:
    """Return prereq state for the UI gate.

    Shape:
        {
          binary: bool,          # gws on $PATH
          auth: bool,            # credentials usable on this machine
          auth_reason: str,      # one of: logged_in, encryption_invalid,
                                 # not_logged_in, unparseable
          version: str | None,
          detail: dict,          # whitelisted fields for the UI
        }

    `gws auth status` always exits 0, so we parse its JSON output
    (see `handle_status` in gws' `auth_commands.rs`) to decide
    whether creds are *usable*, not just *present*.
    """
    bin_path = shutil.which('gws')
    if not bin_path:
        return {
            'binary': False,
            'auth': False,
            'auth_reason': 'no_binary',
            'version': None,
            'detail': {},
        }

    rc_v, out_v, _ = await _run(['gws', '--version'])
    version = out_v.strip() if rc_v == 0 else None

    rc_a, out_a, _ = await _run(['gws', 'auth', 'status'])
    if rc_a != 0:
        return {
            'binary': True,
            'auth': False,
            'auth_reason': 'status_failed',
            'version': version,
            'detail': {},
        }

    parsed = _parse_auth_status(out_a)
    usable, reason, detail = _auth_state_from_json(parsed)
    return {
        'binary': True,
        'auth': usable,
        'auth_reason': reason,
        'version': version,
        'detail': detail,
    }


# ── Install / uninstall ────────────────────────────────


def _rewrite_cross_refs(body: str) -> str:
    """Rewrite `../gws-<x>/SKILL.md` → `../<x>/SKILL.md` in one pass."""
    return _CROSS_REF_RE.sub(r'../\1/SKILL.md', body)


def _populate_staging(src_root: Path, staging: Path) -> None:
    """Copy allowed sub-dirs from `src_root` into `staging`,
    rewriting cross-refs in every SKILL.md body."""
    for src_name, dest_sub in GWS_SUBSET.items():
        src = src_root / src_name
        if not src.is_dir():
            # The user's gws version may not ship this sub-skill.
            # Skip silently; install proceeds with what's available.
            logger.warning(
                'gws install: source skill %s missing, skipping', src_name
            )
            continue
        dest = staging / dest_sub
        shutil.copytree(src, dest)

        # Rewrite cross-refs in every SKILL.md we just copied
        for md in dest.rglob('SKILL.md'):
            try:
                original = md.read_text(encoding='utf-8')
            except Exception as e:
                logger.warning('gws install: failed to read %s: %s', md, e)
                continue
            rewritten = _rewrite_cross_refs(original)
            if rewritten != original:
                md.write_text(rewritten, encoding='utf-8')

    # Write the umbrella SKILL.md
    (staging / 'SKILL.md').write_text(_UMBRELLA_BODY, encoding='utf-8')


async def install_skills() -> dict:
    """Materialize ~/.vibe-seller/.claude/skills/gws/.

    Caller is responsible for verifying prereqs via check_status()
    first — this will raise FileNotFoundError if `gws` is missing.
    """
    skills_dir = _skills_dir()
    skills_dir.mkdir(parents=True, exist_ok=True)
    gws_dir = _gws_dir()

    # Step 1: run `gws generate-skills` into an OS-level tempdir
    # (not under skills/, to avoid confusing Claude Code's discovery).
    # gws >=0.22 validates that --output-dir is a *relative* path, so
    # we spawn with cwd=<parent> and pass just the basename. The
    # effective filesystem location is identical to an absolute path.
    with tempfile.TemporaryDirectory(prefix='gws_gen_') as gen_tmp:
        gen_path = Path(gen_tmp)
        rc, _, err = await _run(
            ['gws', 'generate-skills', '--output-dir', gen_path.name],
            timeout=60.0,
            cwd=str(gen_path.parent),
        )
        if rc != 0:
            raise RuntimeError(f'gws generate-skills failed: {err.strip()}')

        # Step 2: build a staging dir as a sibling of the final gws/
        # (same filesystem → rename is atomic).
        staging = Path(
            tempfile.mkdtemp(dir=str(skills_dir), prefix='.tmp_gws_')
        )
        backup = skills_dir / '.bak_gws'
        backed_up = False
        try:
            # mkdtemp already created it; copytree needs the
            # destination to not exist, so populate into
            # subdirs that don't yet exist.
            _populate_staging(Path(gen_tmp), staging)

            # Step 3: atomic swap. Remove stale backup from any prior
            # interrupted install first.
            if backup.exists():
                shutil.rmtree(backup)
            if gws_dir.exists():
                gws_dir.rename(backup)
                backed_up = True
            staging.rename(gws_dir)
            # Success — drop the backup.
            if backup.exists():
                shutil.rmtree(backup)
            backed_up = False
        except Exception:
            # Rollback in reverse order: if we backed up the old
            # install but the staging rename failed, restore backup
            # so the system doesn't end up with neither gws/ nor a
            # recoverable copy.
            if backed_up and backup.exists() and not gws_dir.exists():
                try:
                    backup.rename(gws_dir)
                except Exception:
                    logger.exception('gws install: backup restore failed')
            # Clean staging if still present
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            raise

    installed = sorted(p.name for p in gws_dir.iterdir() if p.is_dir())
    return {
        'installed': True,
        'subskills': installed,
        'count': len(installed),
    }


async def uninstall_skills() -> dict:
    """Remove ~/.vibe-seller/.claude/skills/gws/. Idempotent."""
    gws_dir = _gws_dir()
    if gws_dir.exists():
        shutil.rmtree(gws_dir)
        return {'removed': True}
    return {'removed': False}


def is_installed() -> bool:
    """Cheap check: does the umbrella dir exist with a SKILL.md?"""
    return (_gws_dir() / 'SKILL.md').is_file()
