"""Unit tests for the test-home isolation guard (tests/conftest.py).

The guard is the loud backstop for the env-forcing block at the top
of the root conftest: if anything imports ``app.*`` before conftest
sets ``VIBE_HOME``, the suite would silently run against the
developer's real ``~/.vibe-seller`` — the guard turns that into a
failed session. Its logic is a pure function so it can be pinned
here.
"""

from pathlib import Path

import pytest

from tests.conftest import assert_test_home_isolated

pytestmark = pytest.mark.unit


def _home(tmp_path: Path) -> Path:
    return tmp_path / 'home'


class TestGuard:
    def test_the_real_home_is_refused_in_a_non_e2e_run(self, tmp_path):
        home = _home(tmp_path)
        with pytest.raises(RuntimeError, match='REAL ~/.vibe-seller'):
            assert_test_home_isolated(
                home / '.vibe-seller', home, e2e_run=False
            )

    def test_any_other_home_passes_in_a_non_e2e_run(self, tmp_path):
        home = _home(tmp_path)
        # The forced temp home, a developer-provided home, anything.
        assert_test_home_isolated(
            tmp_path / 'vibe-seller-test-home-x', home, e2e_run=False
        )

    def test_an_e2e_run_may_target_the_default_home(self, tmp_path):
        """CI's server lives at ~/.vibe-seller; an e2e run pointed at
        it resolves there on purpose."""
        home = _home(tmp_path)
        assert_test_home_isolated(home / '.vibe-seller', home, e2e_run=True)

    def test_paths_are_compared_resolved(self, tmp_path):
        """A symlinked or duplicated spelling of the real home must
        not slip past the guard."""
        home = _home(tmp_path)
        home.mkdir()
        real = home / '.vibe-seller'
        real.mkdir()
        alias = home / 'link-to-real'
        alias.symlink_to(real)
        with pytest.raises(RuntimeError):
            # The UNRESOLVED alias — the guard must resolve it itself.
            assert_test_home_isolated(alias, home, e2e_run=False)
