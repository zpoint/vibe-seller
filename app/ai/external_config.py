"""Detect external config overrides on ``~/.claude/settings.json``.

Tools like cc-switch (https://github.com/farion1231/cc-switch) write
``ANTHROPIC_*`` env entries into the user's ``~/.claude/settings.json``
to route Claude Code globally. Claude Code applies those env entries
**with higher precedence than the process env we pass at subprocess
spawn time**, so when both are set our AI-profile selection is
silently a no-op and the agent runs against whatever endpoint the
external tool configured.

The customer-visible symptom: tasks fail in unexplained ways (e.g.
DeepSeek ``API Error: 400 The content[].thinking in the thinking
mode must be passed back to the API``) regardless of which provider
the user picks in our Settings UI — because the user's selection
is being overridden silently.

This module gives the backend a single, reusable predicate so the
profile config page and the task runner both refuse to proceed with
the same clear message:

- The profile router blocks the user from selecting a non-default
  profile while a settings.json override is present (the override
  would make the selection useless).
- The task runner fails fast before launching the agent with the
  same explanation when an override appears after the profile was
  saved.

The escape hatch is the ``default`` profile: it passes no
``ANTHROPIC_*`` env so the external tool's settings.json fully owns
provider routing, which is what the user is presumably trying to do
by installing cc-switch in the first place.
"""

import json
import logging
from pathlib import Path

from app.ai.profiles import DEFAULT_PROFILE_ID

logger = logging.getLogger(__name__)

# Anthropic / Claude Code reserves the ``ANTHROPIC_*`` namespace for
# everything that steers the SDK (endpoint, credentials, model
# selection, model aliases, future flags). If any of those keys land
# in ``~/.claude/settings.json`` ``env``, the SDK applies them with
# higher precedence than our subprocess env, silently overriding a
# non-default profile. We don't enumerate the keys — the prefix
# alone is the contract, and a static list would go stale every
# time Anthropic adds a new env var.
_OVERRIDE_PREFIX = 'ANTHROPIC_'


def claude_settings_path() -> Path:
    """Return the platform-standard Claude Code settings file path.

    Indirected through a helper so tests can override via monkeypatch.
    """
    return Path.home() / '.claude' / 'settings.json'


def detect_claude_settings_overrides() -> list[str]:
    """Return the list of ``ANTHROPIC_*`` env keys defined in
    ``~/.claude/settings.json``'s ``env`` block.

    Empty list when there's no settings.json, no ``env`` block, or no
    overlapping keys. Parse failures degrade to empty list — a
    silently-unreadable settings.json should not block the user.
    """
    path = claude_settings_path()
    try:
        if not path.is_file():
            return []
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as e:
        logger.debug('Failed to read %s for override detection: %s', path, e)
        return []
    env = data.get('env') or {}
    if not isinstance(env, dict):
        return []
    return sorted(k for k in env if k.startswith(_OVERRIDE_PREFIX))


class ExternalConfigOverrideError(RuntimeError):
    """Raised when a non-default profile is in use but
    ``~/.claude/settings.json`` env entries would override the
    profile's routing.

    Carries the overriding key list so the API layer can surface
    them in the user-facing message.
    """

    def __init__(self, profile_id: str, overriding_keys: list[str]):
        self.profile_id = profile_id
        self.overriding_keys = overriding_keys
        super().__init__(self.user_message())

    def to_api_detail(self) -> dict:
        """Structured ``HTTPException.detail`` payload.

        The frontend renders this in the user's current locale via
        i18n (``errors.externalConfigOverride.*`` keys). The raw
        ``message`` is the English fallback for logs and non-i18n
        consumers; do not assume it is end-user facing.
        """
        return {
            'code': 'external_config_override',
            'profile_id': self.profile_id,
            'overriding_keys': self.overriding_keys,
            'settings_path': str(claude_settings_path()),
            'clear_command': self._clear_command(),
            'message': self.user_message(),
        }

    def _clear_command(self) -> str:
        return (
            'python3 -c "import json,pathlib;'
            "p=pathlib.Path.home()/'.claude'/'settings.json';"
            'd=json.loads(p.read_text());'
            "env=d.get('env') or {};"
            "[env.pop(k,None) for k in ('"
            + "','".join(self.overriding_keys)
            + "')];"
            "d['env']=env;"
            'p.write_text(json.dumps(d,indent=2))"'
        )

    def user_message(self) -> str:
        keys = ', '.join(self.overriding_keys)
        path = claude_settings_path()
        clear_cmd = self._clear_command()
        return (
            f'Your selected AI profile "{self.profile_id}" cannot be '
            f'used: {path} has env entries ({keys}) that take '
            'precedence over the profile and silently route the '
            'agent to whatever endpoint they configure. Pick one:\n\n'
            '  (a) Switch your profile to "Claude" (default) and let '
            'the external tool (e.g. cc-switch) own provider routing.\n\n'
            "  (b) Clear the conflicting env block so Vibe Seller's "
            'profile takes effect — run this once AND quit the tool '
            'that wrote it (cc-switch / similar) so it does not '
            're-write on next launch:\n\n'
            f'      {clear_cmd}\n'
        )


def assert_profile_compatible(profile_id: str | None) -> None:
    """Raise ``ExternalConfigOverrideError`` if *profile_id* is
    non-default AND ``~/.claude/settings.json`` overrides
    ``ANTHROPIC_*``. No-op otherwise.

    Called from the task runner (fail-fast before agent spawn) and
    from the profile routes (block the user from picking a
    non-default profile while overrides exist).
    """
    if not profile_id or profile_id == DEFAULT_PROFILE_ID:
        return
    overriding = detect_claude_settings_overrides()
    if overriding:
        raise ExternalConfigOverrideError(profile_id, overriding)
