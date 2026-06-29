"""Unit tests for the deterministic catalog-stub contract.

Pins the bug that made the catalog-first e2e flake: the sync agent
stubbing a file that actually has a describable topic, so a catalog
reader can't navigate to it and falls back to grep.
"""

import pytest

from app.workspace.catalog_validation import (
    STUB_SUMMARY,
    find_wrongly_stubbed,
    is_near_empty,
    reject_wrong_stubs,
)

pytestmark = pytest.mark.unit


class TestIsNearEmpty:
    def test_empty_string(self):
        assert is_near_empty('') is True

    def test_heading_only(self):
        assert is_near_empty('# Logistics for my-store\n\n') is True

    def test_frontmatter_only(self):
        assert is_near_empty('---\nbrowser: chrome\n---\n\n# Store\n') is True

    def test_one_word_marker(self):
        assert is_near_empty('# Notes\n\nTODO\n') is True

    def test_describable_paragraph_is_substantive(self):
        # The catalog-first fixture file — must NOT be near-empty.
        content = (
            '# Catalog Verification\n\n'
            'This knowledge file exists to verify the catalog-first '
            'lookup flow end to end. Its topic is the verification '
            'secret that a catalog-driven task should return.\n\n'
            'SECRET: VERIFY-CATALOG-XYZ-d6152e339dd8\n'
        )
        assert is_near_empty(content) is False

    def test_short_sentence_is_substantive(self):
        # A real sentence (>= 5 body words) has a topic to summarize.
        assert (
            is_near_empty(
                '# Store A Notes\n\nStore A uses premium pricing on widgets.\n'
            )
            is False
        )

    def test_three_word_fragment_is_near_empty(self):
        # Conservative threshold: a 3-word fragment is too thin to force
        # a summary, so stubbing it stays legitimate (no redo loop).
        assert is_near_empty('# Store A Notes\n\nStore A specific.\n') is True


class TestFindWronglyStubbed:
    def _catalog(self, *rows: str) -> str:
        header = '| File | Relevance | Summary |\n|---|---|---|\n'
        return header + ''.join(rows)

    def test_substantive_file_stubbed_is_flagged(self):
        catalog = self._catalog(
            f'| knowledge/amazon/topic.md | | {STUB_SUMMARY} |\n'
        )
        files = {
            'knowledge/amazon/topic.md': (
                '# Topic\n\nA real paragraph describing the topic here.\n'
            )
        }
        bad = find_wrongly_stubbed(catalog, files.get)
        assert bad == ['knowledge/amazon/topic.md']

    def test_empty_file_stubbed_is_ok(self):
        catalog = self._catalog(
            f'| stores/s/logistics.md | | {STUB_SUMMARY} |\n'
        )
        files = {'stores/s/logistics.md': '# Logistics for s\n\n'}
        assert find_wrongly_stubbed(catalog, files.get) == []

    def test_summarized_substantive_file_is_ok(self):
        catalog = self._catalog(
            '| knowledge/amazon/topic.md | amazon | Bid tuning playbook |\n'
        )
        files = {
            'knowledge/amazon/topic.md': '# Topic\n\nLots of real content.\n'
        }
        assert find_wrongly_stubbed(catalog, files.get) == []

    def test_unreadable_file_is_skipped(self):
        catalog = self._catalog(
            f'| knowledge/amazon/missing.md | | {STUB_SUMMARY} |\n'
        )
        assert find_wrongly_stubbed(catalog, lambda _p: None) == []

    def test_header_and_separator_rows_ignored(self):
        # Only the data row matters; header/separator must not blow up.
        catalog = self._catalog(
            f'| knowledge/x/topic.md | | {STUB_SUMMARY} |\n'
        )
        files = {'knowledge/x/topic.md': 'plain body with several words here'}
        assert find_wrongly_stubbed(catalog, files.get) == [
            'knowledge/x/topic.md'
        ]


class TestRejectWrongStubs:
    """The function the write boundary (router) calls to enforce."""

    def _catalog(self, row: str) -> str:
        return '| File | Relevance | Summary |\n|---|---|---|\n' + row

    def test_raises_for_substantive_stub(self, tmp_path):
        f = tmp_path / 'topic.md'
        f.write_text('# T\n\nA real paragraph with several words here.\n')
        catalog = self._catalog(f'| knowledge/topic.md | | {STUB_SUMMARY} |\n')
        with pytest.raises(ValueError, match='real content'):
            reject_wrong_stubs(catalog, lambda _rel: f)

    def test_ok_for_empty_stub(self, tmp_path):
        f = tmp_path / 'empty.md'
        f.write_text('# Empty\n\n')
        catalog = self._catalog(f'| stores/s/empty.md | | {STUB_SUMMARY} |\n')
        reject_wrong_stubs(catalog, lambda _rel: f)  # no raise

    def test_ok_when_unreadable(self):
        def boom(_rel):
            raise FileNotFoundError(_rel)

        catalog = self._catalog(f'| knowledge/x.md | | {STUB_SUMMARY} |\n')
        reject_wrong_stubs(catalog, boom)  # no raise — can't disprove
