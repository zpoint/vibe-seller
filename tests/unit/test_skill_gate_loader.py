"""Skill-gate loader: discovery, load-once, shared-owner resolution.

These pin the behaviour that makes gates part of a skill: a gate ships
as ``<skill>/gates/<name>.py`` and is (a) discovered by name across the
tree, (b) loaded once per process and cached — a later edit to the file
is IGNORED until a server restart, so synced (possibly remote) gate code
can't hot-reload into a running server — and (c) resolvable by a skill
that declares the name even when the file lives in another skill.
"""

import pytest

from app.ai import skill_gate_loader
from app.ai.skill_gate_loader import (
    discover_skill_gates,
    load_gate_from_path,
    load_skill_gate,
    preload_skill_gates,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clear_module_cache():
    # The loader caches by absolute path for the process lifetime; each
    # test uses a fresh tmp_path so paths don't collide, but clear anyway
    # so a test that rewrites the SAME path sees deterministic behaviour.
    skill_gate_loader._module_cache.clear()
    yield
    skill_gate_loader._module_cache.clear()


def _write_gate(root, skill, name, body):
    d = root / skill / 'gates'
    d.mkdir(parents=True, exist_ok=True)
    (d / f'{name}.py').write_text(body)
    return d / f'{name}.py'


def test_discover_maps_gate_name_to_its_file(tmp_path):
    _write_gate(
        tmp_path,
        'my-skill',
        'my_gate',
        'def check(r, t=None, ru=None): return None\n',
    )
    found = discover_skill_gates(tmp_path)
    assert 'my_gate' in found
    assert found['my_gate'].name == 'my_gate.py'
    assert found['my_gate'].parent.parent.name == 'my-skill'


def test_load_unknown_gate_returns_none(tmp_path):
    # Loader returns None so resolve_skill_gates falls back to the registry.
    assert load_skill_gate('does_not_exist', tmp_path) is None


def test_loaded_gate_is_a_real_module_exposing_the_gate_api(tmp_path):
    # set_task_result uses check()/is_stalled()/reset_progress()/GATE_NAME —
    # the loader returns the executed module directly, so all are present.
    _write_gate(
        tmp_path,
        's',
        'apis',
        (
            'GATE_NAME = "apis"\n'
            'STALL_CAP = 2\n'
            'def is_stalled(task_id): return task_id == "stuck"\n'
            'def reset_progress(task_id): return "reset:" + task_id\n'
            'def check(r, t=None, ru=None): return None\n'
        ),
    )
    gate = load_skill_gate('apis', tmp_path)
    assert gate is not None
    assert gate.GATE_NAME == 'apis'
    assert gate.STALL_CAP == 2
    assert gate.is_stalled('stuck') is True
    assert gate.is_stalled('ok') is False
    assert gate.reset_progress('t1') == 'reset:t1'


def test_loaded_once_ignores_later_file_change(tmp_path):
    """The security property: once loaded, a gate never re-executes.

    Edit the backing file and reload — the loader returns the SAME cached
    module with the ORIGINAL behaviour. Only a fresh process (server
    restart) would pick up the change. This is what prevents synced/remote
    gate code from injecting into a running server.
    """
    path = _write_gate(
        tmp_path, 's', 'g', 'def check(r, t=None, ru=None): return "v1:" + r\n'
    )
    first = load_skill_gate('g', tmp_path)
    assert first.check('x') == 'v1:x'

    path.write_text('def check(r, t=None, ru=None): return "v2:" + r\n')

    again = load_skill_gate('g', tmp_path)
    assert again is first  # same cached module object, not re-executed
    assert again.check('x') == 'v1:x'  # old behaviour, no hot-reload


def test_shared_gate_resolves_from_its_canonical_owner(tmp_path):
    # The gate file lives in skill-a; skill-b would just declare the name
    # in its frontmatter. load_skill_gate resolves by name across the tree.
    _write_gate(
        tmp_path,
        'skill-a',
        'shared_gate',
        'def check(r, t=None, ru=None): return "from-a"\n',
    )
    gate = load_skill_gate('shared_gate', tmp_path)
    assert gate is not None
    assert gate.check('x') == 'from-a'


def test_check_signature_is_positional_safe(tmp_path):
    # set_task_result calls gate.check(result, task_id, rules) positionally.
    _write_gate(
        tmp_path,
        's',
        'sig',
        'def check(result_text, task_id=None, rules=None):\n'
        '    return (result_text, task_id, rules)\n',
    )
    gate = load_skill_gate('sig', tmp_path)
    assert gate.check('r', 't', {'k': 1}) == ('r', 't', {'k': 1})


def test_malformed_gate_fails_open_to_none(tmp_path):
    # A gate file that raises at import must not crash the loader — it
    # returns None (caller skips the gate / falls back to the registry).
    _write_gate(tmp_path, 's', 'boom', 'raise RuntimeError("bad gate")\n')
    assert load_skill_gate('boom', tmp_path) is None


def test_preload_loads_every_gate_once(tmp_path):
    _write_gate(
        tmp_path, 'a', 'g1', 'def check(r, t=None, ru=None): return None\n'
    )
    _write_gate(
        tmp_path, 'b', 'g2', 'def check(r, t=None, ru=None): return None\n'
    )
    _write_gate(tmp_path, 'c', 'bad', 'raise ValueError("nope")\n')
    # 2 good + 1 malformed → 2 loaded; the malformed one is skipped.
    assert preload_skill_gates(tmp_path) == 2
    # Preloaded modules are the cached instances load_gate_from_path returns.
    g1_path = discover_skill_gates(tmp_path)['g1']
    assert load_gate_from_path('g1', g1_path) is not None
