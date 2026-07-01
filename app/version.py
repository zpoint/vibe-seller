"""Single source of truth for the installed package version.

A leaf module (stdlib only) so the CLI, the FastAPI app, and the
``/api/version`` endpoint all report the same value without importing
the app graph.
"""

from importlib.metadata import PackageNotFoundError, version as _pkg_version


def get_version() -> str:
    """Installed wheel version.

    ``setuptools-scm`` bakes this in at build time from the ``v*`` git
    tag (release) or a dev string like ``0.0.7.dev2+g<sha>``. Falls
    back to a dev marker when running from a clone with no install.
    """
    try:
        return _pkg_version('vibe-seller')
    except PackageNotFoundError:
        return '0.0.0+dev'
