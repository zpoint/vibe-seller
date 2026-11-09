"""Single source of truth for the running package version.

A leaf module (stdlib only) so the CLI, the FastAPI app, and the
``/api/version`` endpoint all report the same value without importing
the app graph.
"""

from functools import cache
from importlib.metadata import PackageNotFoundError, version as _pkg_version


@cache
def get_version() -> str:
    """The version of the code THIS process loaded.

    Read once per process, never re-read: the metadata on disk names
    whatever was installed LAST, which after an in-place upgrade is not
    the code a long-running server has in memory. Re-reading it per
    call made an un-restarted server report the new version from
    ``/api/version`` — so the UI's update banner cleared and every
    "did the upgrade take effect?" check passed, all while the old code
    kept running.

    ``setuptools-scm`` bakes this in at build time from the ``v*`` git
    tag (release) or a dev string like ``0.0.7.dev2+g<sha>``. Falls
    back to a dev marker when running from a clone with no install.
    """
    try:
        return _pkg_version('vibe-seller')
    except PackageNotFoundError:
        return '0.0.0+dev'
