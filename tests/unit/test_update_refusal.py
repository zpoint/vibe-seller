"""The memory of a release this machine applied and had to put back.

`app/update_refusal.py` is what stops the six-hour outage loop (review
4): a release that cannot boot is offered again by the next check,
prepared again, marked ready again — and applied again, unless something
remembers. The tests are about WHO gets remembered, that the memory
outlives the record it came from, and what clears it.
"""

import pytest

import app.pip_update as pu
import app.update_refusal as ur

pytestmark = pytest.mark.unit


class TestRefusal:
    """The memory of a release this machine applied and had to put back.

    Review 4's loop: the check cycle offers the same broken build every
    six hours, so without this file every idle minute pays for it again.
    The tests are about WHO gets remembered (a pip apply that ended in a
    rollback or a failed install — not a prefetch miss, not the
    installer path, which never applies itself) and WHAT clears it (a
    newer release, never an older one).
    """

    def _dir(self, monkeypatch, tmp_path):
        monkeypatch.setattr(pu, 'updates_dir', lambda: tmp_path)
        return tmp_path

    def _record(
        self, status='rolled_back', to='0.3.9', path='pip', stage='health'
    ):
        return {
            'status': status,
            'from': '0.3.8',
            'to': to,
            'path': path,
            'trigger': 'auto_idle',
            'stage': stage,
            'error': 'the server never reported ' + to,
            'at': '2026-11-13T04:00:00Z',
        }

    def test_a_rolled_back_pip_apply_is_remembered(self, monkeypatch, tmp_path):
        self._dir(monkeypatch, tmp_path)
        entry = ur.note_apply_result(self._record())
        assert entry['version'] == '0.3.9'
        assert entry['stage'] == 'health'
        assert ur.current() == entry
        assert ur.applies_to('0.3.9') == entry

    def test_a_failed_install_is_remembered_too(self, monkeypatch, tmp_path):
        """Not only the boot that failed: an install that died halfway
        left the venv in whatever state it died in, and that release is
        no more worth retrying on a timer."""
        self._dir(monkeypatch, tmp_path)
        entry = ur.note_apply_result(
            self._record(status='failed', stage='install')
        )
        assert entry['status'] == 'failed'

    def test_a_successful_apply_remembers_nothing(self, monkeypatch, tmp_path):
        self._dir(monkeypatch, tmp_path)
        assert (
            ur.note_apply_result(self._record(status='ok', stage=None)) is None
        )
        assert ur.current() is None

    def test_the_installer_path_is_not_remembered(self, monkeypatch, tmp_path):
        """An installer update is inert until clicked, so it cannot
        repeat anything; and its failure is a different outage."""
        self._dir(monkeypatch, tmp_path)
        assert ur.note_apply_result(self._record(path='installer')) is None
        assert ur.current() is None

    def test_the_memory_is_not_the_record(self, monkeypatch, tmp_path):
        """Two files, because `last.json` is overwritten by the NEXT
        apply — and the reason to refuse is exactly the one that apply
        has not caused yet."""
        where = self._dir(monkeypatch, tmp_path)
        ur.note_apply_result(self._record())
        pu.write_record({'status': 'ok', 'to': '0.4.0'})
        assert pu.read_record()['to'] == '0.4.0'
        assert ur.applies_to('0.3.9') is not None
        assert (where / ur.FILE_NAME).is_file()

    def test_another_version_is_not_blocked_by_this_one(
        self, monkeypatch, tmp_path
    ):
        self._dir(monkeypatch, tmp_path)
        ur.note_apply_result(self._record())
        assert ur.applies_to('0.4.0') is None
        assert ur.applies_to(None) is None

    def test_a_newer_release_clears_it(self, monkeypatch, tmp_path):
        self._dir(monkeypatch, tmp_path)
        ur.note_apply_result(self._record(to='0.3.9'))
        assert ur.supersede('0.4.0') is None
        assert ur.current() is None

    def test_an_older_offer_does_not_clear_it(self, monkeypatch, tmp_path):
        """Rolling BACK past a bad release must not make it eligible
        again: the question here is whether this machine has moved on."""
        self._dir(monkeypatch, tmp_path)
        ur.note_apply_result(self._record(to='0.3.9'))
        assert ur.supersede('0.3.8') is not None
        assert ur.current() is not None
        assert ur.supersede('0.3.9') is not None

    def test_a_corrupt_memory_is_no_memory(self, monkeypatch, tmp_path):
        self._dir(monkeypatch, tmp_path)
        (tmp_path / ur.FILE_NAME).write_text('{oops', encoding='utf-8')
        assert ur.current() is None
        assert ur.applies_to('0.3.9') is None
        # and a broken file is cleared by the next newer release
        assert ur.supersede('0.4.0') is None
