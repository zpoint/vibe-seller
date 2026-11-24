"""A tester build's version must land on the nightly channel.

A ``windows-installer.yml`` run that does not verify (a plain dispatch,
built for people to try) stamps the installer with
``scripts/nightly_version.py``. The contract is the channel's: people
who install it are offered the next release, and no stable install is
ever offered it.
"""

import datetime

import pytest

from app.release_channel import best_upgrade, channel_of, is_newer
from scripts.nightly_version import next_nightly_version

pytestmark = pytest.mark.unit

_NOW = datetime.datetime(2026, 11, 24, 13, 50, tzinfo=datetime.UTC)

_REFS = [
    'aaa\trefs/tags/v0.3.9',
    'bbb\trefs/tags/v0.3.10',
    'ccc\trefs/tags/v0.4.0rc1',
    'ddd\trefs/tags/v1.0.0-beta',
    'eee\trefs/tags/nightly',
]


def test_follows_the_highest_stable_tag():
    # Numerically highest (0.3.10, not 0.3.9); pre-release and rolling
    # tags are not releases.
    assert next_nightly_version(_REFS, _NOW) == '0.3.11.dev202611241350'


def test_timestamp_is_utc():
    beijing = datetime.timezone(datetime.timedelta(hours=8))
    now = datetime.datetime(2026, 11, 24, 21, 50, tzinfo=beijing)
    assert next_nightly_version(_REFS, now) == '0.3.11.dev202611241350'


def test_installs_on_the_nightly_channel_and_moves_to_the_next_release():
    version = next_nightly_version(_REFS, _NOW)
    assert channel_of(version) == 'nightly'
    # Above the last release, so that release is never offered...
    assert best_upgrade(['0.3.10'], version) is None
    # ...below the next one, which is.
    assert best_upgrade(['0.3.10', '0.3.11'], version) == '0.3.11'
    # A later nightly is an upgrade too.
    later = next_nightly_version(_REFS, _NOW + datetime.timedelta(days=1))
    assert is_newer(later, version)


def test_no_stable_tag_is_an_error():
    with pytest.raises(ValueError):
        next_nightly_version(['eee\trefs/tags/nightly'], _NOW)
