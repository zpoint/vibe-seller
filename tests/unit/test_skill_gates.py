"""Skill-declared exit gates: the SKILL.md frontmatter names WHICH
reviewers apply to that skill's outputs (``gates: [...]``), resolved
through ``stop_gates.get_registered_gates`` at ``set_task_result``.
Tasks that loaded no gate-declaring skill get only the generic gates
— ad reviewers must never fire for a listing/email task again."""

from pathlib import Path

import pytest

from app.ai.claude_backend_manager import agent_backend
from app.ai.skill_gate_utils import (
    parse_skill_gates,
    skill_name_from_read,
)
import app.ai.stop_gates as sg
from app.ai.stop_gates import (
    clear_skill_bindings,
    get_registered_gates,
    record_skill_load,
    recorded_skills,
    resolve_skill_gates,
)

pytestmark = pytest.mark.unit


def _mk_skill(root: Path, name: str, frontmatter: str) -> Path:
    d = root / '.claude' / 'skills' / name
    d.mkdir(parents=True)
    p = d / 'SKILL.md'
    p.write_text(f'---\n{frontmatter}\n---\n\n# {name}\n')
    return p


class TestParseSkillGates:
    def test_parses_inline_list(self, tmp_path):
        p = _mk_skill(tmp_path, 'x', 'name: x\ngates: [ad_completeness_review]')
        assert parse_skill_gates(p) == ['ad_completeness_review']

    def test_no_gates_field(self, tmp_path):
        p = _mk_skill(tmp_path, 'x', 'name: x')
        assert parse_skill_gates(p) == []

    def test_missing_file(self, tmp_path):
        assert parse_skill_gates(tmp_path / 'nope.md') == []


class TestResolveSkillGates:
    def test_loaded_skill_brings_its_gate(self, tmp_path):
        _mk_skill(tmp_path, 'ads', 'name: ads\ngates: [ad_completeness_review]')
        gates = resolve_skill_gates({'ads'}, tmp_path)
        assert [name for name, _ in gates] == ['ad_completeness_review']

    def test_unknown_gate_name_ignored(self, tmp_path):
        # A typo in a skill must not break submits.
        _mk_skill(tmp_path, 'ads', 'name: ads\ngates: [no_such_gate]')
        assert resolve_skill_gates({'ads'}, tmp_path) == []

    def test_no_loaded_skills_no_domain_gates(self, tmp_path):
        # The general-task case (listing, email, ad-hoc): nothing runs.
        assert resolve_skill_gates(set(), tmp_path) == []

    def test_duplicate_declarations_collapse(self, tmp_path):
        for n in ('a', 'b'):
            _mk_skill(
                tmp_path, n, f'name: {n}\ngates: [ad_completeness_review]'
            )
        gates = resolve_skill_gates({'a', 'b'}, tmp_path)
        assert len(gates) == 1

    def test_registry_exposes_check(self):
        for name, mod in get_registered_gates().items():
            assert callable(mod.check), name


class TestSkillNameFromRead:
    def test_read_of_skill_md_tracked(self):
        assert (
            skill_name_from_read(
                'Read',
                {'file_path': '/x/.claude/skills/amazon-ads/SKILL.md'},
            )
            == 'amazon-ads'
        )

    def test_other_reads_ignored(self):
        assert (
            skill_name_from_read(
                'Read', {'file_path': '/x/skills/a/references/b.md'}
            )
            is None
        )
        assert (
            skill_name_from_read('Bash', {'command': 'cat skills/a/SKILL.md'})
            is None
        )


class TestDurableSkillBindings:
    """Skill loads bind to the TASK, not the session: a retry-resume
    session that never re-Reads SKILL.md must still face the gates the
    original session bound (the hole that let a surgical fix session
    submit with zero skill gates)."""

    def _patch_dir(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sg, 'GATE_BINDINGS_DIR', tmp_path / 'gb')

    def test_record_and_recall_roundtrip(self, monkeypatch, tmp_path):
        self._patch_dir(monkeypatch, tmp_path)
        assert recorded_skills('t1') == frozenset()
        record_skill_load('t1', 'amazon-ads')
        record_skill_load('t1', 'noon-ads')
        record_skill_load('t1', 'amazon-ads')  # idempotent
        assert recorded_skills('t1') == {'amazon-ads', 'noon-ads'}
        clear_skill_bindings('t1')
        assert recorded_skills('t1') == frozenset()

    def test_manager_unions_durable_without_session(
        self, monkeypatch, tmp_path
    ):
        # No live session (server restarted between iterations): the
        # durable bindings alone must drive gate resolution.
        self._patch_dir(monkeypatch, tmp_path)
        sg.record_skill_load('t-no-session', 'amazon-ads')
        loaded, ws = agent_backend.loaded_skills_and_workspace('t-no-session')
        assert 'amazon-ads' in loaded and ws is None

    def test_unsafe_task_id_sanitized(self, monkeypatch, tmp_path):
        self._patch_dir(monkeypatch, tmp_path)
        record_skill_load('../../evil', 'amazon-ads')
        assert recorded_skills('../../evil') == {'amazon-ads'}
        # Nothing escaped the bindings dir.
        assert not (tmp_path.parent / 'evil').exists()
