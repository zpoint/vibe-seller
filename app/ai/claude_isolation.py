"""Keep the machine user's own Claude Code setup out of agent sessions.

Task agents and the workspace assistant run the ``claude`` CLI, which
by default loads every setting source the machine user has: the
user's ``~/.claude/settings.json`` (``env``, hooks, plugins,
permissions), ``~/.claude/skills``, user-scope MCP servers, and their
private ``~/.claude/CLAUDE.md`` + ``~/.claude/rules``. None of that is
written for a store agent, and all of it is sent to whatever provider
the profile names.

Worse, the user settings ``env`` OUTRANKS the env we spawn the CLI
with. A provider switcher such as cc-switch writes ``ANTHROPIC_*``
there, so a non-default profile was silently routed to the switcher's
provider.

The contract, per profile kind:

- **non-default profiles** run on Vibe Seller's own config:
  ``--setting-sources project,local`` drops the user source (its
  ``env``, skills, plugins, hooks and user-scope MCP servers). The
  task workspace's own ``CLAUDE.md`` / ``.claude/skills`` /
  ``.mcp.json`` are project sources and keep loading.
- **the default profile** means "this machine's Claude Code setup":
  the user source stays on, so a login or a cc-switch ``env`` keeps
  working.
- **every launch** excludes the user's CLAUDE.md files through
  ``claudeMdExcludes``. ``--setting-sources`` alone is not enough: the
  CLI also walks UP from the cwd reading ``CLAUDE.md`` /
  ``.claude/CLAUDE.md`` / ``.claude/rules`` as *project* memory, and
  the workspace lives under the home directory, so the walk reaches
  ``~/.claude/CLAUDE.md`` again. Every ancestor ABOVE the workspace
  root is excluded; the workspace's own files are not.
- **every launch** locks Claude Code's auto memory off in the
  ``--settings`` payload, which outranks user and project settings.
- **"load global MCP"** with the user source off: Vibe Seller reads
  the user-scope servers itself (the CLI's ``~/.claude.json``
  ``mcpServers``) and hands them over with ``--mcp-config``.
"""

from collections.abc import Mapping
import json
import logging
import os
from pathlib import Path, PurePath

from app.ai.profiles import DEFAULT_PROFILE_ID

logger = logging.getLogger(__name__)

# The sources a non-default profile loads: the user source is the
# machine user's own Claude Code setup.
ISOLATED_SETTING_SOURCES = 'project,local'


def _env(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if env is None else env


def _home(env: Mapping[str, str] | None) -> Path:
    home = _env(env).get('HOME')
    return Path(home) if home else Path.home()


def claude_config_home(env: Mapping[str, str] | None = None) -> Path:
    """``$CLAUDE_CONFIG_DIR``, else ``~/.claude`` — the CLI's own rule
    (``getClaudeConfigHomeDir``). *env* is the env claude is spawned
    with (a profile can set ``CLAUDE_CONFIG_DIR``); defaults to ours.
    """
    config_dir = _env(env).get('CLAUDE_CONFIG_DIR')
    return Path(config_dir) if config_dir else _home(env) / '.claude'


def claude_settings_path(env: Mapping[str, str] | None = None) -> Path:
    """The user settings file the CLI reads."""
    return claude_config_home(env) / 'settings.json'


def claude_global_config_path(env: Mapping[str, str] | None = None) -> Path:
    """The CLI's global config (where user-scope MCP servers live).

    The CLI's ``getGlobalClaudeFile``: a legacy
    ``<config home>/.config.json`` when it exists, else ``.claude.json``
    in ``$CLAUDE_CONFIG_DIR`` — or in the HOME directory (NOT
    ``~/.claude``) when that is unset. Touches the disk: call it off
    the event loop.
    """
    legacy = claude_config_home(env) / '.config.json'
    if legacy.exists():
        return legacy
    config_dir = _env(env).get('CLAUDE_CONFIG_DIR')
    base = Path(config_dir) if config_dir else _home(env)
    return base / '.claude.json'


def uses_user_settings(profile_id: str | None) -> bool:
    """Only the default profile runs on the machine's Claude Code
    setup; every other profile is Vibe Seller's own."""
    return (profile_id or DEFAULT_PROFILE_ID) == DEFAULT_PROFILE_ID


def setting_sources_flags(profile_id: str | None) -> list[str]:
    """The ``--setting-sources`` flag for a launch (none for default)."""
    if uses_user_settings(profile_id):
        return []
    return ['--setting-sources', ISOLATED_SETTING_SOURCES]


def _glob_path(path: PurePath) -> str:
    # The CLI matches with picomatch against '/'-separated paths.
    return path.as_posix()


def claude_md_excludes(
    workspace_root: Path, env: Mapping[str, str] | None = None
) -> list[str]:
    """``claudeMdExcludes`` patterns: every CLAUDE.md that is not ours.

    The user's memory file and rules in the config home, plus each
    memory file the CLI's upward walk would read in a directory ABOVE
    *workspace_root* (that walk reaches ``~/.claude/CLAUDE.md`` and
    ``~/CLAUDE.md`` because the workspace sits under HOME). The
    workspace root and everything below it stay loaded. Pure path
    math; the CLI resolves symlinks in the patterns itself.
    """
    config_home = claude_config_home(env)
    patterns = [
        _glob_path(config_home / 'CLAUDE.md'),
        _glob_path(config_home / 'rules') + '/**',
    ]
    root = Path(os.path.abspath(workspace_root))
    for parent in root.parents:
        patterns.extend([
            _glob_path(parent / 'CLAUDE.md'),
            _glob_path(parent / 'CLAUDE.local.md'),
            _glob_path(parent / '.claude' / 'CLAUDE.md'),
            _glob_path(parent / '.claude' / 'rules') + '/**',
        ])
    return list(dict.fromkeys(patterns))


def isolation_settings(
    workspace_root: Path, env: Mapping[str, str] | None = None
) -> dict:
    """The isolation keys every launch's ``--settings`` payload carries.

    A FRESH dict every call (callers merge per-launch extras into it).
    """
    return {
        'claudeMdExcludes': claude_md_excludes(workspace_root, env),
        'autoMemoryEnabled': False,
        'env': {'CLAUDE_CODE_DISABLE_AUTO_MEMORY': '1'},
    }


def read_user_mcp_servers(env: Mapping[str, str] | None = None) -> dict:
    """The user-scope MCP servers from the CLI's global config.

    Empty when the file is missing, unreadable, malformed, or has no
    ``mcpServers`` object: a broken user file must never stop a task
    from launching — the agent just runs without those servers.
    Touches the disk: call it off the event loop.
    """
    path = claude_global_config_path(env)
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        logger.warning('Ignoring user MCP servers in %s: %s', path, e)
        return {}
    servers = data.get('mcpServers') if isinstance(data, dict) else None
    if not isinstance(servers, dict):
        return {}
    return {
        name: cfg
        for name, cfg in servers.items()
        if isinstance(name, str) and isinstance(cfg, dict)
    }
