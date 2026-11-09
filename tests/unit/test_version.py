"""``get_version`` reports the code the process is running."""

from unittest.mock import patch

import pytest

from app import version

pytestmark = pytest.mark.unit


def test_version_is_what_the_process_loaded_not_what_is_on_disk():
    # An in-place upgrade rewrites the metadata under a running server.
    # The server must keep reporting the version it actually loaded, or
    # an un-restarted server passes for an upgraded one.
    version.get_version.cache_clear()
    try:
        with patch.object(version, '_pkg_version', return_value='1.0.0'):
            assert version.get_version() == '1.0.0'
        with patch.object(version, '_pkg_version', return_value='2.0.0'):
            assert version.get_version() == '1.0.0'
    finally:
        version.get_version.cache_clear()
