"""Skill-declared exit gates: the SKILL.md frontmatter names WHICH
gates apply to that skill's outputs (``gates: [...]``), resolved
through ``stop_gates.get_registered_gates`` at ``set_task_result``.
Tasks that loaded no gate-declaring skill get only the generic gates
— ad gates must never fire for a listing/email task again."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.ai.claude_backend import AgentSession
from app.ai.claude_backend_manager import agent_backend
from app.ai.claude_backend_utils import AUTO_APPROVE_CALLBACK
from app.ai.skill_gate_utils import (
    parse_skill_gates,
    skill_name_from_read,
    unloaded_skill_read_hint,
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
        p = _mk_skill(tmp_path, 'x', 'name: x\ngates: [ad_coverage]')
        assert parse_skill_gates(p) == ['ad_coverage']

    def test_no_gates_field(self, tmp_path):
        p = _mk_skill(tmp_path, 'x', 'name: x')
        assert parse_skill_gates(p) == []

    def test_missing_file(self, tmp_path):
        assert parse_skill_gates(tmp_path / 'nope.md') == []


class TestResolveSkillGates:
    def test_loaded_skill_brings_its_gate(self, tmp_path):
        _mk_skill(tmp_path, 'ads', 'name: ads\ngates: [ad_coverage]')
        gates = resolve_skill_gates({'ads'}, tmp_path)
        assert [name for name, _ in gates] == ['ad_coverage']

    def test_unknown_gate_name_ignored(self, tmp_path):
        # A typo in a skill must not break submits.
        _mk_skill(tmp_path, 'ads', 'name: ads\ngates: [no_such_gate]')
        assert resolve_skill_gates({'ads'}, tmp_path) == []

    def test_no_loaded_skills_no_domain_gates(self, tmp_path):
        # The general-task case (listing, email, ad-hoc): nothing runs.
        assert resolve_skill_gates(set(), tmp_path) == []

    def test_duplicate_declarations_collapse(self, tmp_path):
        for n in ('a', 'b'):
            _mk_skill(tmp_path, n, f'name: {n}\ngates: [ad_coverage]')
        gates = resolve_skill_gates({'a', 'b'}, tmp_path)
        assert len(gates) == 1

    def test_registry_exposes_check(self):
        for name, mod in get_registered_gates().items():
            assert callable(mod.check), name


class TestSkillNameFromRead:
    """Which SKILL.md a tool call reads as a file — detection only.

    Reading never loads a skill (see TestOnlyTheSkillToolBindsGates);
    this only decides when the agent is told which path does.
    """

    def test_read_of_skill_md_detected(self):
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
            skill_name_from_read('Bash', {'command': 'ls skills/*/SKILL.md'})
            is None
        )

    @pytest.mark.parametrize(
        'command',
        [
            'grep -rn -i "ad console" .claude/skills/amazon-ads/SKILL.md '
            '.claude/skills/amazon-reports/SKILL.md',
            'rg -n bid .claude/skills/amazon-ads/SKILL.md',
            'ls -la .claude/skills/amazon-ads/SKILL.md',
            'wc -l .claude/skills/amazon-ads/SKILL.md',
        ],
    )
    def test_a_search_or_listing_is_not_a_read(self, command):
        assert skill_name_from_read('Bash', {'command': command}) is None

    @pytest.mark.parametrize(
        'command',
        [
            'cat /x/.claude/skills/amazon-ads/SKILL.md',
            "sed -n '68,90p' .claude/skills/amazon-ads/SKILL.md",
            "head -50 '/x/.claude/skills/amazon-ads/SKILL.md'",
        ],
    )
    def test_a_shell_read_is_detected(self, command):
        assert (
            skill_name_from_read('Bash', {'command': command}) == 'amazon-ads'
        )


class TestUnloadedSkillReadHint:
    """A read of a skill the session hasn't loaded says how to load it."""

    PEEK = {
        'command': "sed -n '68,90p' /x/.claude/skills/review-collect/SKILL.md"
    }

    def test_points_an_unloaded_read_at_the_skill_tool(self):
        hint = unloaded_skill_read_hint('Bash', self.PEEK, set())
        assert hint and "skill='review-collect'" in hint
        assert 'carry on' in hint  # a lookup is not told to load it

    def test_silent_once_the_skill_is_loaded(self):
        assert (
            unloaded_skill_read_hint('Bash', self.PEEK, {'review-collect'})
            is None
        )

    def test_silent_for_other_files(self):
        other = {'file_path': '/x/.claude/skills/amazon-ads/references/a.md'}
        assert unloaded_skill_read_hint('Read', other, set()) is None


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


class TestOnlyTheSkillToolBindsGates:
    """Claude Code has one load path — the Skill tool — and gates bind
    on it alone. In CI an ad audit `sed`-ed 22 lines out of
    review-collect for a metadata shape; when reads bound skills, that
    attached review-collect's gate, which refused the ad report and
    sent the run off collecting product reviews."""

    @staticmethod
    def _hook(tmp_path, tool, tool_input):
        skill_dir = tmp_path / '.claude' / 'skills' / 'review-collect'
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / 'SKILL.md').write_text('---\nname: review-collect\n---\n')
        s = AgentSession(task_id='t-bind', prompt='p', mode='auto')
        s.task_dir = tmp_path
        sent = []
        s._send_hook_response = AsyncMock(
            side_effect=lambda rid, out: sent.append(out)
        )
        request = {
            'callback_id': AUTO_APPROVE_CALLBACK,
            'input': {'tool_name': tool, 'tool_input': tool_input},
        }
        with (
            patch(
                'app.ai.claude_backend_hooks.ProfileManager'
                '.get_env_for_profile',
                return_value={},
            ),
            patch('app.ai.claude_backend_hooks.record_skill_load') as rec,
        ):
            asyncio.run(s._handle_hook_callback('r1', request))
        out = sent[0]['hookSpecificOutput']
        return out, s._loaded_skills, [c.args[1] for c in rec.call_args_list]

    @pytest.mark.parametrize(
        ('tool', 'tool_input'),
        [
            (
                'Bash',
                {
                    'command': "sed -n '68,90p' "
                    '.claude/skills/review-collect/SKILL.md'
                },
            ),
            ('Bash', {'command': 'cat .claude/skills/review-collect/SKILL.md'}),
            (
                'Read',
                {'file_path': '/x/.claude/skills/review-collect/SKILL.md'},
            ),
        ],
    )
    def test_a_read_binds_nothing_and_names_the_load_path(
        self, tmp_path, tool, tool_input
    ):
        out, loaded, recorded = self._hook(tmp_path, tool, tool_input)
        assert out['permissionDecision'] == 'allow'
        assert loaded == set() and recorded == []
        assert "skill='review-collect'" in out['additionalContext']

    def test_the_skill_tool_binds(self, tmp_path):
        out, loaded, recorded = self._hook(
            tmp_path, 'Skill', {'skill': 'review-collect'}
        )
        assert out['permissionDecision'] == 'allow'
        assert 'additionalContext' not in out
        assert loaded == {'review-collect'}
        assert recorded == ['review-collect']
