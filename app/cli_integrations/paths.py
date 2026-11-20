"""Where a managed workplace CLI lives on disk — the one source of truth.

    <home>/tools/<key>/install.json            current version + source
    <home>/tools/<key>/versions/<version>/     the CLI we downloaded
    <home>/tools/<key>/state/                  its config + login (stable
                                               across versions)
    <home>/.claude/skills/<key>/SKILL.md       the umbrella skill

*Installed* means ``install.json`` names a version whose binary is on
disk. *Enabled* means the umbrella skill is there too: ``.claude/`` is
copied wholesale into every task workspace, so the skill reaching the
agent and the CLI's dir joining the agent's ``PATH``
(``registry.enabled_bin_dirs``) are decided by the same files — there is
no DB flag that could disagree with the disk.

One dir per VERSION, never an in-place swap: a running CLI holds its
executable (and, for some, a native library it unpacks next to itself)
open, which Windows refuses to rename or delete. An update lands in a
new dir and flips ``install.json``; the old dir goes when nothing holds
it any more.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from app import config
from app.platform import IS_WINDOWS


def vibe_home() -> Path:
    """The runtime root, read at call time (tests point it elsewhere)."""
    return config.VIBE_SELLER_DIR


def tool_dir(key: str, home: Path | None = None) -> Path:
    """Everything we own for integration *key*."""
    return (home or vibe_home()) / 'tools' / key


def versions_dir(key: str, home: Path | None = None) -> Path:
    return tool_dir(key, home) / 'versions'


def version_dir(key: str, version: str, home: Path | None = None) -> Path:
    return versions_dir(key, home) / version


def state_dir(key: str, home: Path | None = None) -> Path:
    """The CLI's own config + credentials, private to this Vibe Seller
    home (never the machine user's personal CLI login)."""
    return tool_dir(key, home) / 'state'


def manifest_path(key: str, home: Path | None = None) -> Path:
    """``install.json`` — which version is current, and from where."""
    return tool_dir(key, home) / 'install.json'


def exe_name(binary: str) -> str:
    """``wecom-cli`` → ``wecom-cli.exe`` on Windows."""
    return f'{binary}.exe' if IS_WINDOWS else binary


def read_manifest(key: str, home: Path | None = None) -> dict | None:
    """The current install's manifest (blocking), or None."""
    try:
        data = json.loads(manifest_path(key, home).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get('version'), str):
        return None
    return data


def write_manifest(key: str, data: dict, home: Path | None = None) -> None:
    """Replace ``install.json`` atomically (blocking)."""
    path = manifest_path(key, home)
    tmp = path.with_name('.install.json.tmp')
    tmp.write_text(json.dumps(data, indent=2), encoding='utf-8')
    os.replace(tmp, path)


def bin_dir(key: str, home: Path | None = None) -> Path | None:
    """The current version's dir (what joins the agent's PATH)."""
    manifest = read_manifest(key, home)
    if manifest is None:
        return None
    return version_dir(key, manifest['version'], home)


def binary_path(key: str, binary: str, home: Path | None = None) -> Path | None:
    """The current executable of integration *key*, if installed."""
    d = bin_dir(key, home)
    return d / exe_name(binary) if d is not None else None


def skills_root(home: Path | None = None) -> Path:
    """The skills tree every task workspace copies."""
    return (home or vibe_home()) / '.claude' / 'skills'


def skill_dir(key: str, home: Path | None = None) -> Path:
    """The umbrella skill of integration *key*."""
    return skills_root(home) / key


#: Inside the umbrella: the CLI version its sub-skills were taken from.
SKILLS_VERSION_FILE = '.cli-version'


def skills_version(key: str, home: Path | None = None) -> str | None:
    """The version the umbrella of *key* documents (None: no umbrella)."""
    try:
        text = (skill_dir(key, home) / SKILLS_VERSION_FILE).read_text(
            encoding='utf-8'
        )
    except OSError:
        return None
    return text.strip() or None


def is_installed(key: str, binary: str, home: Path | None = None) -> bool:
    """The current version's binary is on disk."""
    exe = binary_path(key, binary, home)
    return exe is not None and exe.is_file()


def is_enabled(key: str, binary: str, home: Path | None = None) -> bool:
    """Installed AND its umbrella skill is in the skills tree."""
    return (skill_dir(key, home) / 'SKILL.md').is_file() and is_installed(
        key, binary, home
    )
