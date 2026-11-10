"""Every third-party package the server imports is declared in pyproject.

A package that only arrives through another dependency disappears the day
that dependency drops it. browser-use 1.0.0rc5 stopped depending on
aiohttp; a newer uv resolved to it, and every import of
``app.browser.chrome`` failed with ``ModuleNotFoundError: aiohttp``.
"""

import ast
import importlib.metadata
import pathlib
import re
import sys
import tomllib

import pytest

pytestmark = pytest.mark.unit

ROOT = pathlib.Path(__file__).resolve().parents[2]

# Skill scripts run in the agent's own environment and ship their own
# requirements.txt; they are not server imports.
SKIP_DIRS = {'skills', 'skills_v2'}

# Optional, imported lazily inside try/except with a fallback.
OPTIONAL = {'PIL'}


def _norm(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def _declared():
    with open(ROOT / 'pyproject.toml', 'rb') as f:
        deps = tomllib.load(f)['project']['dependencies']
    return {_norm(re.split(r'[\s\[<>=!~;]', d, maxsplit=1)[0]) for d in deps}


def _imports():
    for path in (ROOT / 'app').rglob('*.py'):
        if SKIP_DIRS & set(path.relative_to(ROOT / 'app').parts):
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module] if node.module else []
            else:
                continue
            for name in names:
                yield (
                    name.split('.')[0],
                    f'{path.relative_to(ROOT)}:{node.lineno}',
                )


def test_browser_use_is_capped_below_the_next_minor():
    with open(ROOT / 'pyproject.toml', 'rb') as f:
        deps = tomllib.load(f)['project']['dependencies']
    (spec,) = [d for d in deps if _norm(d).startswith('browser-use')]
    assert '<0.14' in spec.replace(' ', ''), spec


def test_every_server_import_is_declared():
    declared = _declared()
    provides = importlib.metadata.packages_distributions()
    undeclared = {}
    for top, where in _imports():
        if top in sys.stdlib_module_names or top == 'app' or top in OPTIONAL:
            continue
        dists = {_norm(d) for d in provides.get(top, [])}
        if not dists & declared:
            undeclared.setdefault(top, []).append(where)
    assert not undeclared, (
        'imported by app/ but not declared in pyproject dependencies: '
        + ', '.join(f'{k} ({v[0]})' for k, v in sorted(undeclared.items()))
    )
