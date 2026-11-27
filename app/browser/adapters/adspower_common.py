"""Platform-neutral pieces shared by the AdsPower win/mac layers.

The Windows (:mod:`adspower_win`) and macOS (:mod:`adspower_mac`)
layers never import each other; what is true on BOTH platforms — the
kernel-process record, the user-data-dir attribution regex and the
``local_api`` URL→port parse — lives here (REVIEW-2 nit).
"""

from __future__ import annotations

import re

#: The Local API's default port (docs). Only used when free — the real
#: port is discovered from the ``local_api`` file on every connect.
DEFAULT_API_PORT = 50325

# ``...cache/<profile_id>_<inviteCode>[_flower]`` — the profile id is a
# path segment PREFIX, so ``<id>_`` matches both kernel spellings and
# never a longer id that merely starts with this one.
USER_DATA_DIR_RE = re.compile(
    r'--user-data-dir=(?P<dir>\S*[/\\]cache[/\\](?P<id>[^\s/\\]+))',
    re.IGNORECASE,
)


class KernelProcess:
    """One store-browser kernel process attributed to a profile."""

    def __init__(self, pid: int, name: str, user_data_dir: str):
        self.pid = pid
        #: Image name lowercased (platform-specific spelling).
        self.name = name.lower()
        #: The ``--user-data-dir`` value (carries the profile id).
        self.user_data_dir = user_data_dir

    @property
    def is_firefox(self) -> bool:
        """FlowerBrowser = the Firefox kernel (no CDP, marionette).

        The user-data-dir's ``_flower`` suffix is the cross-platform
        marker (vendor source: firefox appends it); the image name is a
        second signal for platforms where it is known.
        """
        return 'flower' in self.name or self.user_data_dir.rstrip(
            '/\\'
        ).endswith('_flower')

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f'KernelProcess({self.pid}, {self.name})'


def parse_local_api_port_text(text: str) -> int | None:
    """The port from a ``local_api`` file's URL text (``:NNNNN`` end).

    Only the port is taken — ``local.adspower.com`` is not depended on
    (it resolves through public DNS).
    """
    match = re.search(r':(\d+)\s*/?\s*$', (text or '').strip())
    return int(match.group(1)) if match else None
