"""Every MCP tool — and every action of an action-enum tool — is called
by at least one behavioural test.

The docs drift guard (test_mcp_tools_reference_drift.py) keeps the docs in
step with the served schemas; this one keeps the TESTS in step. Adding a
tool, or a new value to a tool's ``action`` enum, without a workflow or
e2e test that actually calls it fails here, naming what is uncovered.

How a call is recognised (static, from the test sources' AST, so it works
on any subset run and needs no server):

* workflow style — ``call_tool(client, 'manage_store', {'action': 'get'})``
  (any call whose name ends in ``call_tool`` / ``_tool_call``), the tool
  name as a string positional argument and, for action tools, a dict
  literal with an ``'action'`` key;
* e2e style — ``mcp.call_tool('manage_store', action='get', ...)``, the
  action as a keyword argument.

An action passed through a variable (a parametrized test) is resolved
when the variable is a ``pytest.mark.parametrize`` argument with literal
values; otherwise the call does not count — write the action literally.
"""

import ast
from pathlib import Path

import pytest

from app.agent_mcp.registry import tool_definitions

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_DIRS = (REPO_ROOT / 'tests' / 'workflow', REPO_ROOT / 'tests' / 'e2e')


def _served() -> dict[str, tuple[str, ...]]:
    """Tool name → its action enum (empty for tools without one)."""
    out = {}
    for tool in tool_definitions():
        props = tool['inputSchema'].get('properties', {})
        enum = props.get('action', {}).get('enum') or ()
        out[tool['name']] = tuple(a for a in enum if isinstance(a, str))
    return out


def _is_tool_call(node: ast.Call) -> bool:
    func = node.func
    name = (
        func.attr
        if isinstance(func, ast.Attribute)
        else func.id
        if isinstance(func, ast.Name)
        else ''
    )
    return name.endswith('call_tool') or name.endswith('_tool_call')


def _str(node) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _parametrize_values(func: ast.AST) -> dict[str, list[str]]:
    """``@pytest.mark.parametrize('x', ['a', 'b'])`` → {'x': ['a', 'b']}."""
    values: dict[str, list[str]] = {}
    for deco in getattr(func, 'decorator_list', []):
        if not (
            isinstance(deco, ast.Call)
            and isinstance(deco.func, ast.Attribute)
            and deco.func.attr == 'parametrize'
            and len(deco.args) >= 2
        ):
            continue
        argname = _str(deco.args[0])
        if argname is None or ',' in argname:
            continue
        if isinstance(deco.args[1], ast.List | ast.Tuple):
            literals = [_str(e) for e in deco.args[1].elts]
            values[argname.strip()] = [v for v in literals if v]
    return values


def _actions_of(node: ast.AST, params: dict[str, list[str]]) -> list[str]:
    if (s := _str(node)) is not None:
        return [s]
    if isinstance(node, ast.Name) and node.id in params:
        return params[node.id]
    return []


def _calls_in_tests() -> set[tuple[str, str | None]]:
    """Every (tool, action-or-None) a behavioural test calls."""
    found: set[tuple[str, str | None]] = set()
    for test_dir in TEST_DIRS:
        for path in sorted(test_dir.glob('test_*.py')):
            tree = ast.parse(path.read_text(encoding='utf-8'))
            funcs = [
                n
                for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
            ]
            scopes = [(f, _parametrize_values(f)) for f in funcs]
            scopes.append((tree, {}))
            for scope, params in scopes:
                for node in ast.walk(scope):
                    if not (isinstance(node, ast.Call) and _is_tool_call(node)):
                        continue
                    tool = next(
                        (s for a in node.args if (s := _str(a)) is not None),
                        None,
                    )
                    if tool is None:
                        continue
                    found.add((tool, None))
                    for arg in node.args:
                        if not isinstance(arg, ast.Dict):
                            continue
                        for key, value in zip(arg.keys, arg.values):
                            if key is not None and _str(key) == 'action':
                                for act in _actions_of(value, params):
                                    found.add((tool, act))
                    for kw in node.keywords:
                        if kw.arg == 'action':
                            for act in _actions_of(kw.value, params):
                                found.add((tool, act))
    return found


def test_every_tool_and_action_is_called_by_a_test():
    served = _served()
    called = _calls_in_tests()
    missing = []
    for tool, actions in sorted(served.items()):
        if (tool, None) not in called:
            missing.append(tool)
            continue
        missing.extend(
            f'{tool}(action={act!r})'
            for act in actions
            if (tool, act) not in called
        )
    assert not missing, (
        'MCP tools/actions with no workflow or e2e test calling them '
        '(add a test under tests/workflow or tests/e2e that calls each):\n  '
        + '\n  '.join(missing)
    )


def test_the_scanner_sees_both_call_styles():
    """Pin the recogniser itself, so a refactor of the helpers can't
    silently make every tool look uncovered (or covered)."""
    called = _calls_in_tests()
    # workflow style: call_tool(client, 'manage_store', {'action': ...})
    assert ('manage_store', 'bookmarks') in called
    # a plain tool with no action enum
    assert ('list_stores', None) in called
