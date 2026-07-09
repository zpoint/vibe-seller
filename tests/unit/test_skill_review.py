"""Parsing the per-skill ``review:`` Definition-of-Done block (Phase 2)."""

import pytest

from app.ai.skill_review import (
    SkillReview,
    parse_skill_review,
    skills_requiring_review,
)


def _write_skill(root, name, frontmatter_body):
    d = root / '.claude' / 'skills' / name
    d.mkdir(parents=True, exist_ok=True)
    (d / 'SKILL.md').write_text(
        f'---\n{frontmatter_body}\n---\n\n# {name}\nbody\n', encoding='utf-8'
    )
    return d / 'SKILL.md'


@pytest.mark.unit
class TestParseSkillReview:
    def test_none_when_no_file(self, tmp_path):
        assert parse_skill_review(tmp_path / 'nope.md') is None

    def test_none_when_no_review_block(self, tmp_path):
        p = _write_skill(tmp_path, 'amazon-reports', 'name: amazon-reports')
        assert parse_skill_review(p) is None

    def test_empty_review_block_opts_in(self, tmp_path):
        # ``review:`` present but no mapping under it → opt-in, empty.
        p = _write_skill(tmp_path, 's', 'name: s\nreview:')
        got = parse_skill_review(p)
        assert isinstance(got, SkillReview)
        assert got.criteria == '' and got.evidence == ()

    def test_full_block_parsed(self, tmp_path):
        p = _write_skill(
            tmp_path,
            'amazon-listing',
            'name: amazon-listing\n'
            'gates: [listing_submitted]\n'
            'review:\n'
            '  criteria: |\n'
            '    - Every SKU is live with a real ASIN.\n'
            '  evidence:\n'
            '    - "*REPORT*.xlsm"\n'
            '    - "LISTING_*.md"\n'
            '  verify_by: |\n'
            '    Open Manage Inventory and confirm each SKU exists.\n',
        )
        got = parse_skill_review(p)
        assert got is not None
        assert 'real ASIN' in got.criteria
        assert got.evidence == ('*REPORT*.xlsm', 'LISTING_*.md')
        assert 'Manage Inventory' in got.verify_by

    def test_evidence_scalar_coerced_to_tuple(self, tmp_path):
        p = _write_skill(tmp_path, 's', 'name: s\nreview:\n  evidence: "*.pdf"')
        assert parse_skill_review(p).evidence == ('*.pdf',)

    def test_malformed_yaml_returns_none(self, tmp_path):
        d = tmp_path / '.claude' / 'skills' / 'bad'
        d.mkdir(parents=True)
        (d / 'SKILL.md').write_text(
            '---\nname: bad\n  : : bad\n\treview:\n---\n', encoding='utf-8'
        )
        # Malformed frontmatter must degrade to None, never raise.
        assert parse_skill_review(d / 'SKILL.md') is None


@pytest.mark.unit
class TestSkillsRequiringReview:
    def test_only_review_declaring_skills_returned(self, tmp_path):
        _write_skill(
            tmp_path, 'amazon-listing', 'name: amazon-listing\nreview:'
        )
        _write_skill(tmp_path, 'amazon-shared', 'name: amazon-shared')  # none
        got = skills_requiring_review(
            frozenset({'amazon-listing', 'amazon-shared', 'not-installed'}),
            tmp_path,
        )
        assert set(got) == {'amazon-listing'}

    def test_empty_when_none_declare(self, tmp_path):
        _write_skill(tmp_path, 'amazon-shared', 'name: amazon-shared')
        assert (
            skills_requiring_review(frozenset({'amazon-shared'}), tmp_path)
            == {}
        )
