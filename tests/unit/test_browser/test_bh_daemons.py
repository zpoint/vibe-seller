"""The ``bu-<BU_NAME>.pid`` record has two shapes; both must parse.

browser_harness gained a second pid-record shape mid-0.13: alongside
the plain integer every version writes, browser-use >= 0.13.9
publishes the spawn-lock record ``{"pid": N, "started": <fingerprint>}``
(local-Chrome spawn path) and the daemon retains it. A parser that
assumed the plain int read those records as corrupt — and the callers
then unlinked a live daemon's identity files without killing it (the
daemon leaked, invisible to the reaper). These tests pin the parser
against every shape the runtime actually writes, plus the garbage that
must never yield a wrong pid.
"""

import json

import pytest

from app.browser.bh_daemons import iter_pidfiles, parse_pid_record

pytestmark = pytest.mark.unit


class TestParsePidRecord:
    def test_plain_int_record(self):
        # browser-harness <= 0.1.9 (browser-use <= 0.13.8) and the
        # daemon's own rewrite.
        assert parse_pid_record('12345\n') == 12345

    def test_json_fingerprint_record(self):
        # browser-harness >= 0.1.12 (browser-use >= 0.13.9):
        # admin._publish_pid writes this, daemon._publish_own_pid keeps it.
        record = json.dumps({'pid': 12345, 'started': 'Mon Jan 1'})
        assert parse_pid_record(record) == 12345

    def test_json_record_with_string_pid(self):
        assert parse_pid_record('{"pid": "12345"}') == 12345

    def test_first_field_wins_for_multi_field_plain_record(self):
        assert parse_pid_record('12345 Mon Jan 1') == 12345

    def test_garbage_yields_none_not_a_wrong_pid(self):
        for text in ('', '   ', 'not-a-pid', '{"pid": "abc"}', '[]', '{}'):
            assert parse_pid_record(text) is None, text


class TestIterPidfiles:
    def test_yields_parsed_pid_for_both_record_shapes(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr('app.browser.bh_daemons.BH_RUNTIME_DIR', tmp_path)
        (tmp_path / 'bu-store-a1b2c3d4.pid').write_text('111')
        (tmp_path / 'bu-store-a1b2c3d4.sock').touch()
        (tmp_path / 'bu-store-aux.pid').write_text(
            json.dumps({'pid': 222, 'started': 'x'})
        )
        got = {name: pid for name, pid, _pf, _sock in iter_pidfiles()}
        assert got == {'store-a1b2c3d4': 111, 'store-aux': 222}
