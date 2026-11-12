"""Tests for app/release_notes — the merged release-notes parser.

Bodies are synthetic and shaped exactly like the real release format
(see ``gh api repos/zpoint/vibe-seller/releases/tags/v0.3.1``); no real
customer data anywhere.
"""

import pytest

from app.release_notes import merged_notes, parse_release_body

pytestmark = pytest.mark.unit

_REALISH = """# Vibe Seller v0.2.0

One-line intro.

## Highlights

- **功能一。** 中文说明第一行
  中文说明的续行。
  - 子条目甲
  - 子条目乙
- **功能二。** 单独的中文说明。

---

- **Feature one.** English text line one
  continuation line two.
  - sub bullet
- **Feature two.** Standalone English text.

## Notes

- Version is derived from the git tag (`setuptools_scm`).
- Upgrading from v0.1.1 is a standard in-place update
  (`vibe-seller upgrade`).

**Full changelog:** https://github.com/zpoint/vibe-seller/compare/x

Footer text that must NOT become a note.
"""


class TestParseReleaseBody:
    def test_realish_shape(self):
        parsed = parse_release_body(_REALISH)
        assert parsed is not None
        assert len(parsed['highlights']) == 2
        zh0 = parsed['highlights'][0]['zh']
        assert zh0.startswith('- **功能一。**')
        # Continuation line joined into the same item (single newlines
        # inside a markdown paragraph stay part of it).
        assert '中文说明的续行。' in zh0.splitlines()[1]
        # Sub-bullets kept as indented markdown list lines.
        assert '  - 子条目甲' in zh0
        assert '  - 子条目乙' in zh0
        en0 = parsed['highlights'][0]['en']
        assert 'continuation line two.' in en0
        assert '  - sub bullet' in en0
        assert parsed['highlights'][1]['zh'].startswith('- **功能二。**')

    def test_notes_end_at_full_changelog_line(self):
        parsed = parse_release_body(_REALISH)
        assert parsed is not None
        assert len(parsed['notes']) == 2
        assert 'git tag' in parsed['notes'][0]
        # A continuation line joins its bullet; the footer after
        # **Full changelog** is dropped entirely.
        assert 'in-place update' in parsed['notes'][1]
        assert not any('Footer text' in n for n in parsed['notes'])

    def test_notes_end_at_next_heading(self):
        body = _REALISH.replace(
            '**Full changelog:** https://github.com/zpoint/vibe-seller/compare/x',
            '## Other\n- not a note',
        )
        parsed = parse_release_body(body)
        assert parsed is not None
        assert len(parsed['notes']) == 2

    def test_no_separator_means_english_half_empty(self):
        body = '# t\n\n## Highlights\n\n- 只有中文\n'
        parsed = parse_release_body(body)
        assert parsed == {
            'highlights': [{'zh': '- 只有中文', 'en': ''}],
            'notes': [],
        }

    def test_body_without_highlights_section_is_not_the_format(self):
        assert parse_release_body('# Title\n\n- just some prose\n') is None

    def test_empty_highlights_section_is_not_the_format(self):
        assert parse_release_body('## Highlights\n\n## Notes\n\n- x\n') is None

    def test_crlf_body_parses(self):
        parsed = parse_release_body(_REALISH.replace('\n', '\r\n'))
        assert parsed is not None
        assert len(parsed['highlights']) == 2


class TestMergedNotes:
    def _release(self, version, body):
        return {'version': version, 'body': body}

    def test_items_carry_their_version_and_are_newest_first(self):
        releases = [
            self._release('0.3.1', _REALISH),
            self._release(
                '0.2.0',
                '## Highlights\n\n- 老功能\n---\n- Old feature\n\n'
                '## Notes\n\n- old note\n',
            ),
        ]
        merged = merged_notes(releases)
        assert [h['version'] for h in merged['highlights']] == [
            '0.3.1',
            '0.3.1',
            '0.2.0',
        ]
        assert [n['version'] for n in merged['notes']] == [
            '0.3.1',
            '0.3.1',
            '0.2.0',
        ]
        assert merged['notes'][-1]['text'] == '- old note'

    def test_malformed_body_becomes_one_highlight_item(self):
        merged = merged_notes([self._release('0.2.0', '# T\nweird prose')])
        assert merged['highlights'] == [
            {
                'version': '0.2.0',
                'zh': '# T\nweird prose',
                'en': '# T\nweird prose',
            }
        ]
        assert merged['notes'] == []

    def test_release_is_never_dropped_silently(self):
        # An EMPTY body has nothing to show (the UI lists the version in
        # its header); every other body produces at least one item.
        merged = merged_notes([
            self._release('0.2.0', ''),
            self._release('0.1.0', 'no sections at all'),
        ])
        assert [h['version'] for h in merged['highlights']] == ['0.1.0']

    def test_halves_are_paired_by_index(self):
        # zh and en lists pair 1:1 (the release format's intent); a
        # longer side pads the missing entries with '' — the UI falls
        # back to the other language there.
        body = '## Highlights\n\n- A\n---\n- B\n- C\n'
        merged = merged_notes([self._release('0.2.0', body)])
        assert merged['highlights'] == [
            {'version': '0.2.0', 'zh': '- A', 'en': '- B'},
            {'version': '0.2.0', 'zh': '', 'en': '- C'},
        ]
