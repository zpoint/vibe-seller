"""The workplace CLIs Vibe Seller can manage, and which are enabled.

Kept light (no server imports) — ``apply_agent_venv_path`` reads
``enabled_bin_dirs`` / ``enabled_env`` / ``ensure_guard`` at every agent
launch.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from app.cli_integrations.base import CliIntegration
from app.cli_integrations.dingtalk import DingTalk
from app.cli_integrations.feishu import Feishu
from app.cli_integrations.paths import (
    bin_dir,
    is_enabled,
    read_manifest,
    vibe_home,
)
from app.cli_integrations.wecom import WeCom

logger = logging.getLogger(__name__)

INTEGRATIONS: dict[str, CliIntegration] = {
    i.key: i for i in (WeCom(), Feishu(), DingTalk())
}


def installed(home: Path | None = None) -> list[CliIntegration]:
    """The integrations an admin has connected (blocking: reads each
    ``install.json``) — the only ones the daily update touches."""
    return [i for i in INTEGRATIONS.values() if read_manifest(i.key, home)]


def enabled(home: Path | None = None) -> list[CliIntegration]:
    """The ENABLED integrations (blocking: stats two files each)."""
    return [
        i for i in INTEGRATIONS.values() if is_enabled(i.key, i.binary, home)
    ]


def enabled_bin_dirs(home: Path | None = None) -> list[Path]:
    """``bin`` dirs of the enabled integrations, for the agent's PATH.

    Enabling one, two or all three platforms adds one, two or three
    dirs and nothing else — an agent finds exactly the CLIs whose
    umbrella skill its workspace got.
    """
    dirs = (bin_dir(i.key, home) for i in enabled(home))
    return [d for d in dirs if d is not None]


def enabled_env(home: Path | None = None) -> dict[str, str]:
    """The CLI switches (``CliIntegration.env``) of the enabled ones."""
    h = home or vibe_home()
    env: dict[str, str] = {}
    for i in enabled(h):
        env.update(i.env(h))
    return env


_GUARD_HEADER = '# Auto-generated workplace-CLI guard (vibe-seller)'


def _guard_script(integ: CliIntegration) -> str:
    return f"""#!/usr/bin/env bash
{_GUARD_HEADER} — do not edit.
echo "ERROR: {integ.binary} ({integ.title}) is not enabled for agents on" >&2
echo "this Vibe Seller. Tell the user, in plain words and without command" >&2
echo "names: an admin can connect {integ.title} in Settings → Integrations" >&2
echo "(设置 → 集成) by clicking Connect (开通) on its row." >&2
echo "Do not install it or run another copy: a copy installed elsewhere" >&2
echo "would act as someone's personal account, not the team's." >&2
exit 127
"""


def ensure_guard(home: Path | None = None) -> Path | None:
    """Write (idempotently) an erroring ``<binary>`` for every platform.

    It sits on the agent's PATH BELOW the enabled platforms' dirs and
    ABOVE the PATH the server inherited: an enabled platform's managed
    build wins, and a disabled one's name hits this guard instead of a
    copy someone installed for themselves (``npm i -g @larksuite/cli``)
    — which would run as THEIR personal login. "Only enabled platforms
    reach an agent" therefore holds on a machine that has personal
    installs too. Blocking (writes files); returns the guard dir.
    """
    guard = (home or vibe_home()) / 'tools' / '_guard'
    try:
        guard.mkdir(parents=True, exist_ok=True)
        for integ in INTEGRATIONS.values():
            path = guard / integ.binary
            text = _guard_script(integ)
            current = (
                path.read_text(encoding='utf-8', errors='replace')
                if path.is_file()
                else None
            )
            if current != text:
                path.write_text(text, encoding='utf-8', newline='\n')
                os.chmod(path, 0o755)
        return guard
    except OSError as e:
        logger.warning('Could not write the workplace-CLI guard: %s', e)
        return None
