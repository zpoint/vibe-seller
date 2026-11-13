"""Installing an EXACT version of the tool, and un-pinning afterwards.

`app/uv_tool.py` exists because those two halves cannot both be free: uv
writes the requirement it was given into `uv-receipt.toml`, and a `==`
sitting there freezes every later `uv tool upgrade` — which is why the
CLI's plain upgrade deliberately installs unpinned, and why the update
path (which must name a version, review 4's sibling bug) has to repair
the receipt right after.
"""

from unittest.mock import MagicMock, patch

import pytest

from app import uv_tool

pytestmark = pytest.mark.unit


class TestInstallExactly:
    """`upgrade --version X` installs X.

    The in-app update reaches this through ``app/pip_update.py``
    building the argv, because the update check has ALREADY decided
    which release to move to: it fingerprinted it, warmed its wheels
    and pre-installed the browser its Playwright pins. The plain
    upgrade's answer to "what is newest in the sources right now" is a
    different question, and answering it as if it were the same one let
    a newer, broken wheel in a find-links directory be installed while
    the record named the version that was never touched (measured on
    2026-11-13 in tests/releases/test_auto_update.sh leg s1).
    """

    RECEIPT = (
        '[tool]\n'
        'requirements = [{ name = "vibe-seller", specifier = "==99.0.2" }]\n'
        'entrypoints = [\n'
        '    {{ name = "vibe-seller", install-path = "/x/bin/vibe-seller",'
        ' from = "vibe-seller" }},\n'
        ']\n'
        '\n'
        '[tool.options]\n'
        'find-links = ["file:///x/dist"]\n'
    )

    def _receipt(self, monkeypatch, tmp_path):
        prefix = tmp_path / 'toolenv'
        prefix.mkdir()
        path = prefix / 'uv-receipt.toml'
        path.write_text(self.RECEIPT, encoding='utf-8')
        monkeypatch.setattr(uv_tool.sys, 'prefix', str(prefix))
        return path

    def test_uv_gets_the_pinned_requirement_not_an_upgrade(self, monkeypatch):
        unpinned = []
        monkeypatch.setattr(
            uv_tool, 'unpin_receipt', lambda: unpinned.append(1)
        )
        with (
            patch.object(uv_tool.shutil, 'which', return_value='/usr/bin/uv'),
            patch.object(uv_tool.subprocess, 'run') as run,
        ):
            run.return_value = MagicMock(returncode=0)
            rc = uv_tool.install_exactly('99.0.2')
        assert rc == 0
        assert run.call_args_list[0][0][0] == [
            'uv',
            'tool',
            'install',
            '--force',
            'vibe-seller==99.0.2',
        ]
        assert unpinned == [1], (
            'a pinned install leaves the pin in the receipt, which '
            'freezes every later `uv tool upgrade` here'
        )

    def test_the_pip_fallback_is_pinned_too(self, monkeypatch):
        monkeypatch.setattr(uv_tool, 'unpin_receipt', lambda: None)
        with (
            patch.object(uv_tool.shutil, 'which', return_value=None),
            patch.object(uv_tool.subprocess, 'run') as run,
        ):
            run.return_value = MagicMock(returncode=0)
            uv_tool.install_exactly('99.0.2')
        cmd = run.call_args_list[0][0][0]
        assert cmd[-2:] == ['install', 'vibe-seller==99.0.2']

    def test_a_failed_install_leaves_the_receipt_alone(
        self, monkeypatch, tmp_path
    ):
        path = self._receipt(monkeypatch, tmp_path)
        with (
            patch.object(uv_tool.shutil, 'which', return_value='/usr/bin/uv'),
            patch.object(uv_tool.subprocess, 'run') as run,
        ):
            run.return_value = MagicMock(returncode=1)
            rc = uv_tool.install_exactly('99.0.2')
        assert rc == 1
        assert path.read_text(encoding='utf-8') == self.RECEIPT

    def test_only_vibe_sellers_own_pin_is_removed(self, monkeypatch, tmp_path):
        path = self._receipt(monkeypatch, tmp_path)
        uv_tool.unpin_receipt()
        text = path.read_text(encoding='utf-8')
        assert (
            text.splitlines()[1] == 'requirements = [{ name = "vibe-seller" }]'
        )
        # Everything else the receipt records — the entrypoints, the
        # find-links the tool was installed from — survives, because
        # `uv tool upgrade` needs them.
        assert 'entrypoints = [' in text
        assert 'find-links = ["file:///x/dist"]' in text

    def test_a_dependency_pin_is_not_touched(self, monkeypatch, tmp_path):
        path = self._receipt(monkeypatch, tmp_path)
        path.write_text(
            self.RECEIPT
            + 'extra = [{ name = "fastapi", specifier = "==0.115.0" }]\n',
            encoding='utf-8',
        )
        uv_tool.unpin_receipt()
        assert '{ name = "fastapi", specifier = "==0.115.0" }' in (
            path.read_text(encoding='utf-8')
        )

    def test_a_missing_receipt_is_not_an_upgrade_failure(
        self, monkeypatch, tmp_path, caplog
    ):
        """No receipt means no uv tool install (a pip one, say): there is
        nothing to repair, and saying so must not fail the upgrade."""
        monkeypatch.setattr(uv_tool.sys, 'prefix', str(tmp_path / 'nowhere'))
        assert uv_tool.unpin_receipt() is False  # must not raise
        assert 'could not read the uv receipt' in caplog.text

    def test_an_already_unpinned_receipt_is_left_writing_free(
        self, monkeypatch, tmp_path
    ):
        path = self._receipt(monkeypatch, tmp_path)
        path.write_text(
            '[tool]\nrequirements = [{ name = "vibe-seller" }]\n',
            encoding='utf-8',
        )
        before = path.stat().st_mtime_ns
        uv_tool.unpin_receipt()
        assert path.read_text(encoding='utf-8') == (
            '[tool]\nrequirements = [{ name = "vibe-seller" }]\n'
        )
        assert path.stat().st_mtime_ns == before
