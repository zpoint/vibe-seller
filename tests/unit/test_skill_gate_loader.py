"""Skill-gate loader: discovery, hot-reload, shared-owner resolution.

These pin the behaviour that makes gates part of a skill: a gate ships
as ``<skill>/gates/<name>.py`` and is (a) discovered by name across the
tree, (b) hot-reloaded when its file changes — so a synced/pulled gate
update takes effect without a server restart — and (c) resolvable by a
skill that declares the name even when the file lives in another skill.
"""


import pytest

from app.ai.skill_gate_loader import (
    HotGate,
    discover_skill_gates,
    load_skill_gate,
)

pytestmark = pytest.mark.unit


def _write_gate(root, skill, name, body):
    d = root / skill / 'gates'
    d.mkdir(parents=True, exist_ok=True)
    (d / f'{name}.py').write_text(body)
    return d / f'{name}.py'


def test_discover_maps_gate_name_to_its_file(tmp_path):
    _write_gate(tmp_path, 'my-skill', 'my_gate',
                'def check(r, t=None, ru=None): return None\n')
    found = discover_skill_gates(tmp_path)
    assert 'my_gate' in found
    assert found['my_gate'].name == 'my_gate.py'
    assert found['my_gate'].parent.parent.name == 'my-skill'


def test_load_unknown_gate_returns_none(tmp_path):
    # Loader returns None so resolve_skill_gates falls back to the registry.
    assert load_skill_gate('does_not_exist', tmp_path) is None


def test_hot_reload_on_content_change(tmp_path):
    """The core promise: edit the gate file → new behaviour, no restart.

    Detection is content-hash based (not mtime): here v1 and v2 are the
    SAME size and the edit lands within one mtime tick — an mtime- or
    size-keyed cache (incl. importlib's .pyc cache) would miss it. The
    loader compile+execs the exact bytes it hashed, so it can't go stale.
    """
    path = _write_gate(tmp_path, 's', 'g',
                       'def check(r, t=None, ru=None): return "v1:" + r\n')
    gate = load_skill_gate('g', tmp_path)
    assert isinstance(gate, HotGate)
    assert gate.check('x') == 'v1:x'

    # Same-size rewrite, immediately (no sleep — mtime may not advance):
    path.write_text('def check(r, t=None, ru=None): return "v2:" + r\n')

    # Same HotGate instance, no reload call, no process restart:
    assert gate.check('x') == 'v2:x'


def test_no_reexec_when_file_unchanged(tmp_path):
    # A counter that increments on each module execution proves we only
    # re-exec when the file actually changed (mtime-gated).
    _write_gate(tmp_path, 's', 'counted', (
        'import itertools\n'
        '_n = next(itertools.count())  # 0 at import time\n'
        'def check(r, t=None, ru=None): return _n\n'
    ))
    gate = load_skill_gate('counted', tmp_path)
    first = gate.check('')
    # Unchanged file → cache hit → same module → same _n (no re-exec).
    assert gate.check('') == first


def test_shared_gate_resolves_from_its_canonical_owner(tmp_path):
    # The gate file lives in skill-a; skill-b would just declare the name
    # in its frontmatter. load_skill_gate resolves by name across the tree.
    _write_gate(tmp_path, 'skill-a', 'shared_gate',
                'def check(r, t=None, ru=None): return "from-a"\n')
    gate = load_skill_gate('shared_gate', tmp_path)
    assert gate is not None
    assert gate.check('x') == 'from-a'


def test_check_signature_is_positional_safe(tmp_path):
    # set_task_result calls gate.check(result, task_id, rules) positionally.
    _write_gate(tmp_path, 's', 'sig',
                'def check(result_text, task_id=None, rules=None):\n'
                '    return (result_text, task_id, rules)\n')
    gate = load_skill_gate('sig', tmp_path)
    assert gate.check('r', 't', {'k': 1}) == ('r', 't', {'k': 1})
