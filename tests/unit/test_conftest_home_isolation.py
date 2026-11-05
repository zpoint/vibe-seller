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

from tests.conftest import (
    assert_test_home_isolated,
    e2e_requested,
)

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

    def test_any_other_home_passes_without_a_forced_home(self, tmp_path):
        home = _home(tmp_path)
        # With no forced home known (the historical call shape), the
        # guard can only refuse the default real home.
        assert_test_home_isolated(
            tmp_path / 'vibe-seller-test-home-x', home, e2e_run=False
        )

    def test_a_home_that_is_not_the_forced_one_is_refused(self, tmp_path):
        """The 2026-11 review hole: an exported VIBE_HOME latched by a
        pre-conftest ``app.*`` import made the suite run against a
        REAL (non-default) home, and the old guard — which only knew
        the default-home spelling — passed it. With the forced home
        known, anything else is refused."""
        home = _home(tmp_path)
        with pytest.raises(RuntimeError, match='neither the real'):
            assert_test_home_isolated(
                tmp_path / 'exported-e2e-home' / '.vibe-seller',
                home,
                e2e_run=False,
                forced_home=str(tmp_path / 'vibe-seller-test-home-x'),
            )

    def test_the_forced_home_passes(self, tmp_path):
        home = _home(tmp_path)
        assert_test_home_isolated(
            tmp_path / 'vibe-seller-test-home-x',
            home,
            e2e_run=False,
            forced_home=str(tmp_path / 'vibe-seller-test-home-x'),
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


class TestE2eDetection:
    """``--e2e`` must be detected the way pytest itself reads it —
    including PYTEST_ADDOPTS, which never appears in ``sys.argv``.
    Pre-fix, a ``PYTEST_ADDOPTS='--e2e' pytest tests/e2e`` run executed
    e2e tests while the conftest took the NON-e2e branch (throwaway
    home, no E2E env derivation)."""

    def test_plain_argv_token(self, monkeypatch):
        monkeypatch.setattr('sys.argv', ['pytest', 'tests/e2e', '--e2e'])
        monkeypatch.delenv('PYTEST_ADDOPTS', raising=False)
        assert e2e_requested() is True

    def test_argv_without_the_flag(self, monkeypatch):
        monkeypatch.setattr('sys.argv', ['pytest', 'tests/unit'])
        monkeypatch.delenv('PYTEST_ADDOPTS', raising=False)
        assert e2e_requested() is False

    def test_pytest_addopts_is_read_too(self, monkeypatch):
        """The CI-relevant shape: options injected via PYTEST_ADDOPTS
        are invisible in sys.argv."""
        monkeypatch.setattr('sys.argv', ['pytest', 'tests/e2e/test_x.py'])
        monkeypatch.setenv('PYTEST_ADDOPTS', '--e2e')
        assert e2e_requested() is True

    def test_addopts_without_the_flag(self, monkeypatch):
        monkeypatch.setattr('sys.argv', ['pytest'])
        monkeypatch.setenv('PYTEST_ADDOPTS', '-k foo')
        assert e2e_requested() is False

    def test_addopts_may_carry_several_tokens(self, monkeypatch):
        monkeypatch.setattr('sys.argv', ['pytest'])
        monkeypatch.setenv('PYTEST_ADDOPTS', '-n 4 --e2e -vv')
        assert e2e_requested() is True

    def test_an_xdist_worker_inherits_the_decision(self, monkeypatch):
        """CI's e2e job runs ``-n 4``. An xdist worker's sys.argv is
        execnet's command line — no ``--e2e`` — so re-parsing it there
        took the NON-e2e branch: each worker forced a throwaway home and
        test_catalog_sync looked for CATALOG.md under /tmp instead of the
        server's home. The controller publishes its decision in the
        environment the workers inherit; a worker reads that."""
        monkeypatch.setattr('sys.argv', ['-c'])  # what a worker sees
        monkeypatch.delenv('PYTEST_ADDOPTS', raising=False)
        monkeypatch.setenv('VIBE_TEST_E2E', '1')
        assert e2e_requested() is True

    def test_the_inherited_marker_must_say_yes(self, monkeypatch):
        monkeypatch.setattr('sys.argv', ['-c'])
        monkeypatch.delenv('PYTEST_ADDOPTS', raising=False)
        monkeypatch.setenv('VIBE_TEST_E2E', '0')
        assert e2e_requested() is False
