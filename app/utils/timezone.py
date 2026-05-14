"""Server timezone resolution.

Leaf utility — depends only on tzlocal. Lives outside scheduler and
models so both layers can import without forming a cycle (models
need a default timezone for Schedule rows, scheduler needs it to
build APScheduler triggers).
"""

import logging

from tzlocal import get_localzone_name

logger = logging.getLogger(__name__)


def get_server_timezone() -> str:
    """Return the server's IANA timezone name, falling back to UTC.

    On hosts where the local zone can't be resolved to a named IANA
    entry (e.g. bare Docker images with `/etc/localtime` as a plain
    file, not a symlink), fall back to UTC rather than raising.
    """
    try:
        name = get_localzone_name()
        if name:
            return name
    except Exception:
        logger.debug('Failed to resolve server timezone', exc_info=True)
    return 'UTC'
