"""Print the version a tester build is stamped with.

``<next patch>.dev<UTC YYYYMMDDHHMM>`` after the highest stable
``vX.Y.Z`` tag — the shape ``app/release_channel.py`` reads as the
``nightly`` channel. An install of it is offered newer nightlies and
the next stable release, and stable installs never see it (they only
read PyPI's ``info.version`` and ``releases/latest``, which never name
a pre-release).

The highest tag, not the nearest ancestor: a branch cut before the last
release still ships in the next one, and a build numbered below the
last release would be offered that older release the moment it starts.

Reads the tags from ``origin``, so a shallow CI checkout works:

    python scripts/nightly_version.py   # e.g. 0.3.6.dev202611240550
"""

import datetime
import re
import subprocess

_STABLE_TAG = re.compile(r'refs/tags/v(\d+)\.(\d+)\.(\d+)$')


def next_nightly_version(refs: list[str], now: datetime.datetime) -> str:
    """The nightly version after the highest stable tag in ``refs``.

    ``refs`` are ``git ls-remote`` lines (``<sha>\\trefs/tags/v0.3.5``);
    anything that is not a plain ``vX.Y.Z`` tag is ignored.
    """
    stable = [
        tuple(int(part) for part in m.groups())
        for ref in refs
        if (m := _STABLE_TAG.search(ref.strip()))
    ]
    if not stable:
        raise ValueError('no stable vX.Y.Z tag')
    major, minor, patch = max(stable)
    stamp = now.astimezone(datetime.UTC).strftime('%Y%m%d%H%M')
    return f'{major}.{minor}.{patch + 1}.dev{stamp}'


def main() -> None:
    refs = subprocess.run(
        ['git', 'ls-remote', '--tags', '--refs', 'origin', 'refs/tags/v*'],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    print(next_nightly_version(refs, datetime.datetime.now(datetime.UTC)))


if __name__ == '__main__':
    main()
