"""Which release channel an installed version belongs to.

Three channels, told apart by the version string alone (no stored
state, so installing a build is all it takes to switch):

- ``stable``  — a tagged release, e.g. ``0.2.0``. Published to PyPI and
  as a full GitHub release. Only ever offered newer stable releases.
- ``nightly`` — ``<next patch>.dev<UTC timestamp>``, e.g.
  ``0.2.1.dev202610161800``, built by ``.github/workflows/nightly.yml``.
  PEP 440 sorts it above the last stable and below the next one, and
  marks it a pre-release, so pip/uv never pick it without
  ``--prerelease allow`` and PyPI's ``info.version`` ignores it — which
  is what keeps stable installs from ever seeing a nightly. Offered the
  highest of newer nightlies and newer stable releases.
- ``dev``     — anything else: a source checkout (``0.2.1.dev3+g<sha>``),
  the ``0.0.0+dev`` fallback, a PR build, or an unparseable string.
  Never checked: no published build corresponds to an arbitrary commit.

The nightly test is "a dev release whose dev number is a timestamp,
with no local segment". setuptools-scm's own dev numbers count commits
since the last tag and always carry ``+g<sha>``, so they can't collide.
"""

from __future__ import annotations

from typing import Literal

from packaging.version import InvalidVersion, Version

Channel = Literal['stable', 'nightly', 'dev']

# The rolling GitHub prerelease every nightly replaces. It must contain
# no digit: setuptools-scm only reads tags matching ``*[0-9]*``, which is
# what keeps this tag out of a stable build's version, even on the same
# commit as a ``v*`` tag.
NIGHTLY_TAG = 'nightly'

# Smallest dev number that reads as a UTC timestamp (2020-01-01, at
# YYYYMMDD precision or finer). Commit counts never get near it.
_MIN_TIMESTAMP_DEV = 20200101


def channel_of(version: str) -> Channel:
    try:
        parsed = Version(version)
    except InvalidVersion:
        return 'dev'
    if parsed.local is not None:
        return 'dev'
    if parsed.is_devrelease:
        if (
            parsed.dev is not None
            and parsed.dev >= _MIN_TIMESTAMP_DEV
            and parsed.pre is None
            and parsed.post is None
        ):
            return 'nightly'
        return 'dev'
    # Everything else, rc/beta included, is checked as stable — exactly
    # what ``update_check.is_dev_version`` did before nightlies existed.
    return 'stable'


def is_newer(latest: str, current: str) -> bool:
    """True if *latest* is a strictly newer version than *current*."""
    try:
        return Version(latest) > Version(current)
    except InvalidVersion:
        return False


def offered_versions(versions: list[str], channel: Channel) -> list[str]:
    """The subset of *versions* an install on *channel* may upgrade to.

    Stable sees only stable; nightly sees stable and nightly; dev sees
    nothing.
    """
    if channel == 'dev':
        return []
    allowed = {'stable'} if channel == 'stable' else {'stable', 'nightly'}
    return [v for v in versions if channel_of(v) in allowed and _publishable(v)]


def _publishable(version: str) -> bool:
    """A final release or a timestamped nightly — never an rc/beta.

    ``channel_of`` files an *installed* rc under stable (so it keeps
    being checked), but as an upgrade *target* an rc is a pre-release
    no channel asked for.
    """
    if channel_of(version) == 'nightly':
        return True
    try:
        return not Version(version).is_prerelease
    except InvalidVersion:
        return False


def best_upgrade(
    versions: list[str], current: str, channel: Channel | None = None
) -> str | None:
    """Highest version in *versions* the current install should move to,
    or None when nothing on its channel is newer.

    *channel* defaults to the current version's own; pass ``'nightly'``
    to switch a stable install onto the nightly channel.
    """
    candidates = [
        v
        for v in offered_versions(versions, channel or channel_of(current))
        if is_newer(v, current)
    ]
    return max(candidates, key=Version, default=None)
