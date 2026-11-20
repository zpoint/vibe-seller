"""The operations Settings → Integrations runs on a workplace CLI.

install  download the current release for this OS/arch (official host,
         then mirror, over the network path that reaches it), verify the
         published checksum, prove it runs, land it in its own version
         dir and flip ``install.json`` to it (``paths``)
login    run the CLI's own login without a terminal (``login``)
enable   fetch the official skills of the INSTALLED version and swap
         the umbrella into the skills tree — from then on every task
         workspace gets the skill and the agent's PATH gets the binary
disable  remove the umbrella (the binary and the login stay)
update   the daily sync job (``app.sync.jobs``): move every INSTALLED
         CLI to the vendor's current release, with its skills

Every run of a CLI — ours and the agent's — carries ``integ.env(home)``:
its config + login live in ``tools/<key>/state``, private to this
Vibe Seller home, so the team's identity never mixes with a personal
CLI login on the same machine. One lock per integration serialises the
mutations, so a double click cannot interleave two installs.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime
import hashlib
import logging
import os
from pathlib import Path
import shutil
import subprocess

from app.cli_integrations import download, paths, skills
from app.cli_integrations.base import (
    AuthStatus,
    CliIntegration,
    LoginView,
    host_platform,
)
from app.cli_integrations.login import LoginSession, login_manager
from app.cli_integrations.registry import INTEGRATIONS
from app.platform import IS_WINDOWS, prepend_to_path, safe_chmod
from app.workspace import skills_tree

logger = logging.getLogger(__name__)

STATUS_TIMEOUT_S = 30.0

_locks: dict[str, asyncio.Lock] = {}


class IntegrationError(RuntimeError):
    """A precondition the page should show verbatim (HTTP 400)."""


def _lock(key: str) -> asyncio.Lock:
    return _locks.setdefault(key, asyncio.Lock())


def get(key: str) -> CliIntegration:
    return INTEGRATIONS[key]


def _home(home: Path | None) -> Path:
    return home or paths.vibe_home()


# ── running the managed binary ─────────────────────────


def run_env(integ: CliIntegration, home: Path | None = None) -> dict:
    """Env for the server's own runs (blocking: reads install.json)."""
    h = _home(home)
    env = {**os.environ, **integ.env(h)}
    current = paths.bin_dir(integ.key, h)
    if current is not None:
        prepend_to_path(env, current)
    return env


def _creationflags() -> dict:
    if IS_WINDOWS:
        return {'creationflags': subprocess.CREATE_NO_WINDOW}  # type: ignore[attr-defined]
    return {}


async def _runner(integ: CliIntegration, home: Path):
    """``run(args)`` for ``CliIntegration.auth_status``."""
    exe = await asyncio.to_thread(
        paths.binary_path, integ.key, integ.binary, home
    )
    env = await asyncio.to_thread(run_env, integ, home)

    async def run(args: list[str]) -> tuple[int, str, str]:
        if exe is None:
            return 127, '', 'not installed'
        try:
            proc = await asyncio.create_subprocess_exec(
                str(exe),
                *args,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=str(paths.state_dir(integ.key, home)),
                **_creationflags(),
            )
        except OSError as e:
            return 127, '', str(e)
        try:
            out, err = await asyncio.wait_for(
                proc.communicate(), STATUS_TIMEOUT_S
            )
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
            return 124, '', 'timeout'
        return (
            proc.returncode or 0,
            out.decode('utf-8', 'replace'),
            err.decode('utf-8', 'replace'),
        )

    return run


# ── state ──────────────────────────────────────────────


async def auth_status(
    integ: CliIntegration, home: Path | None = None
) -> AuthStatus:
    h = _home(home)
    try:
        return await integ.auth_status(await _runner(integ, h))
    except Exception as e:  # a parser surprise must not 500 the page
        logger.exception('%s auth status failed', integ.key)
        return AuthStatus(False, 'error', detail={'error': str(e)})


async def status(integ: CliIntegration, home: Path | None = None) -> dict:
    """One card of the Settings page."""
    h = _home(home)
    installed = await asyncio.to_thread(
        paths.is_installed, integ.key, integ.binary, h
    )
    manifest = await asyncio.to_thread(paths.read_manifest, integ.key, h)
    enabled = await asyncio.to_thread(
        paths.is_enabled, integ.key, integ.binary, h
    )
    auth = await auth_status(integ, h) if installed else None
    session = login_manager.get(integ.key)
    return {
        'key': integ.key,
        'title': integ.title,
        'binary': integ.binary,
        'homepage': integ.homepage,
        'installed': installed,
        'version': (manifest or {}).get('version') if installed else None,
        'enabled': enabled,
        'auth': (
            {
                'usable': auth.usable,
                'reason': auth.reason,
                'identity': auth.identity,
                'detail': auth.detail,
            }
            if auth is not None
            else None
        ),
        'login': session.view.public() if session is not None else None,
    }


# ── install ────────────────────────────────────────────


def _proves_it_runs(exe: Path, integ: CliIntegration, home: Path) -> None:
    """The fresh binary answers ``--version`` here, on this OS."""
    try:
        done = subprocess.run(
            [str(exe), '--version'],
            capture_output=True,
            # The CLIs print UTF-8; the locale codec (GBK on a Chinese
            # Windows) cannot decode it.
            encoding='utf-8',
            errors='replace',
            timeout=60,
            env={**os.environ, **integ.env(home)},
            cwd=str(paths.state_dir(integ.key, home)),
            stdin=subprocess.DEVNULL,
            **_creationflags(),
        )
    except (OSError, subprocess.SubprocessError) as e:
        raise IntegrationError(f'{integ.binary} does not run here: {e}') from e
    if done.returncode != 0:
        raise IntegrationError(
            f'{integ.binary} --version exited {done.returncode}: '
            f'{(done.stderr or done.stdout).strip()[:300]}'
        )


def _holds(exe: Path, sha: str) -> bool:
    """*exe* is exactly the build with *sha*."""
    try:
        return hashlib.sha256(exe.read_bytes()).hexdigest() == sha
    except OSError:
        return False


def _place(target: Path, exe: Path, body: bytes, sha: str) -> bool:
    """Put the build into its version dir; True when this call made it.

    The dir is written where it will run and never moved afterwards:
    Windows refuses to rename a dir while any file in it is open, and
    a CLI that just ran there (``dws`` unpacks a native library next to
    itself) is held open by the antivirus scanning it for seconds after.
    ``install.json`` is the commit point — a dir it does not name is
    never on anyone's PATH, so writing it in place is not visible early.

    A dir already holding this exact build is reused as it is (a task
    may run it: the previous version, or a release the vendor went back
    to). Anything else there is a leftover of a failed attempt.
    """
    if _holds(exe, sha):
        return False
    if target.exists():
        try:
            shutil.rmtree(target)
        except OSError as e:
            raise IntegrationError(
                f'{target.name} is in use by a running task; retry when it '
                f'finishes ({e})'
            ) from e
    target.mkdir(parents=True)
    try:
        exe.write_bytes(body)
        safe_chmod(exe, 0o755)
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise
    return True


def _prune(integ: CliIntegration, keep: set[str], home: Path) -> None:
    """Drop version dirs older than the current and the previous one.

    The previous one stays: a task that started before the update still
    has it on its PATH (and its skills copy documents it), and Linux /
    macOS would let us delete it from under that task.
    """
    root = paths.versions_dir(integ.key, home)
    for d in root.iterdir() if root.is_dir() else ():
        if d.name not in keep:
            shutil.rmtree(d, ignore_errors=True)


def _install_blocking(
    integ: CliIntegration, home: Path, version: str | None = None
) -> dict:
    """Install *version* (default: the current release)."""
    previous = (paths.read_manifest(integ.key, home) or {}).get('version')
    version = version or integ.latest_version()
    plat = host_platform()
    try:
        asset = integ.binary_asset(version, plat)
    except ValueError as e:
        raise IntegrationError(str(e)) from e
    data, url = download.fetch(asset.urls, verify=asset.verify)
    body = download.archive_member(data, asset.member)
    sha = hashlib.sha256(body).hexdigest()
    paths.state_dir(integ.key, home).mkdir(parents=True, exist_ok=True)
    target = paths.version_dir(integ.key, version, home)
    exe = target / paths.exe_name(integ.binary)
    made = _place(target, exe, body, sha)
    try:
        _proves_it_runs(exe, integ, home)
    except Exception:
        if made:
            shutil.rmtree(target, ignore_errors=True)
        raise
    manifest = {
        'version': version,
        'platform': plat,
        'source': url,
        'sha256': sha,
        'installed_at': datetime.now(UTC).isoformat(),
    }
    paths.write_manifest(integ.key, manifest, home)
    _prune(integ, {version, previous or version}, home)
    return manifest


async def install(integ: CliIntegration, home: Path | None = None) -> dict:
    """Install (or update to) the current release.

    An enabled integration also gets the skills of the new version, so
    the docs the agent reads always match the binary it runs.
    """
    h = _home(home)
    async with _lock(integ.key):
        was_enabled = await asyncio.to_thread(
            (paths.skill_dir(integ.key, h) / 'SKILL.md').is_file
        )
        manifest = await asyncio.to_thread(_install_blocking, integ, h)
        if was_enabled:
            await _install_skills(integ, manifest['version'], h)
        return manifest


async def update(
    integ: CliIntegration, home: Path | None = None
) -> dict | None:
    """Keep an installed CLI on the vendor's current release.

    ``None`` when it is not installed: only a platform an admin
    connected is ever downloaded. Otherwise ``{'from', 'to', 'skills'}``
    — ``to == from`` when it already was current. A release the vendor
    withdrew is followed back too: "current" is whatever the vendor
    publishes as latest, not the highest number seen.

    An enabled platform leaves with the skills of the version it now
    runs. That is checked against the umbrella's own record, not only
    after a download: when an earlier run moved the binary but its
    skills download failed, this run brings the skills level.
    """
    h = _home(home)
    async with _lock(integ.key):
        manifest = await asyncio.to_thread(paths.read_manifest, integ.key, h)
        if manifest is None:
            return None
        current = manifest['version']
        latest = await asyncio.to_thread(integ.latest_version)
        if latest != current:
            await asyncio.to_thread(_install_blocking, integ, h, latest)
            logger.info('%s CLI updated %s -> %s', integ.key, current, latest)
        enabled = await asyncio.to_thread(
            (paths.skill_dir(integ.key, h) / 'SKILL.md').is_file
        )
        documented = await asyncio.to_thread(paths.skills_version, integ.key, h)
        refreshed = enabled and documented != latest
        if refreshed:
            await _install_skills(integ, latest, h)
        return {'from': current, 'to': latest, 'skills': refreshed}


async def update_installed(home: Path | None = None) -> dict[str, dict]:
    """``update`` every platform, each on its own: one vendor's outage
    (or a release that does not run here) never holds the others back,
    and never touches the version that platform is running."""
    outcome: dict[str, dict] = {}
    for integ in INTEGRATIONS.values():
        try:
            result = await update(integ, home)
        except Exception as e:
            logger.warning('%s CLI update failed: %s', integ.key, e)
            outcome[integ.key] = {'error': str(e)}
            continue
        if result is not None:
            outcome[integ.key] = result
    return outcome


# ── enable / disable ───────────────────────────────────


def _fetch_skills_blocking(
    integ: CliIntegration, version: str
) -> dict[str, dict[str, bytes]]:
    return skills.select(integ.fetch_skills(version), integ.layout)


async def _install_skills(
    integ: CliIntegration, version: str, home: Path
) -> list[str]:
    subs = await asyncio.to_thread(_fetch_skills_blocking, integ, version)
    async with skills_tree.hold():
        return await asyncio.to_thread(
            skills.install, integ, version, subs, home
        )


async def enable(integ: CliIntegration, home: Path | None = None) -> dict:
    """Give agents this CLI: requires an installed binary that is
    signed in (an agent must never be the one to log in)."""
    h = _home(home)
    async with _lock(integ.key):
        if not await asyncio.to_thread(
            paths.is_installed, integ.key, integ.binary, h
        ):
            raise IntegrationError(f'{integ.title} is not installed')
        auth = await auth_status(integ, h)
        if not auth.usable:
            raise IntegrationError(
                f'{integ.title} is not signed in ({auth.reason})'
            )
        manifest = await asyncio.to_thread(paths.read_manifest, integ.key, h)
        version = (manifest or {}).get('version') or 'unknown'
        subs = await _install_skills(integ, version, h)
        return {'enabled': True, 'version': version, 'skills': subs}


async def disable(integ: CliIntegration, home: Path | None = None) -> dict:
    """Take the CLI away from agents (binary + login are kept)."""
    h = _home(home)
    async with _lock(integ.key):
        async with skills_tree.hold():
            removed = await asyncio.to_thread(skills.uninstall, integ, h)
        return {'enabled': False, 'removed': removed}


# ── login ──────────────────────────────────────────────


async def start_login(
    integ: CliIntegration,
    home: Path | None = None,
    *,
    enable_after: bool = False,
) -> LoginView:
    """Start (or restart) the login; the page polls its view.

    With *enable_after*, a login that succeeds enables the integration
    from the session itself — closing the page mid-login still ends
    with the platform enabled.
    """
    h = _home(home)
    exe = await asyncio.to_thread(paths.binary_path, integ.key, integ.binary, h)
    if exe is None or not await asyncio.to_thread(exe.is_file):
        raise IntegrationError(f'{integ.title} is not installed')
    auth = await auth_status(integ, h)
    steps = integ.login_steps(auth)

    async def _signed_in() -> bool:
        return (await auth_status(integ, h)).usable

    async def _done(session: LoginSession) -> None:
        if session.view.state != 'succeeded' or not enable_after:
            return
        try:
            await enable(integ, h)
        except Exception as e:
            logger.exception('%s enable after login failed', integ.key)
            session.view.message = f'signed in, but enabling failed: {e}'

    session = await login_manager.start(
        integ,
        exe,
        steps,
        paths.tool_dir(integ.key, h) / 'login',
        await asyncio.to_thread(run_env, integ, h),
        on_done=_done,
        verify=_signed_in,
    )
    return session.view
