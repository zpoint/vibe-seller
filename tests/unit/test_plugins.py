"""Plugin framework: the IoC seam core reads gates/backends/skills from.

Proves (1) the OSS builtin registers every core contribution through the
public ``ExtensionContext`` API, (2) an out-of-tree plugin can add a
gate / browser backend / service / skill source with zero core edits and
core honours it, (3) load order is deterministic and a misbehaving
plugin can't crash boot, and (4) the live call sites
(``get_registered_gates``, ``resolve_skill_gates``, ``first_bash_deny``,
``BrowserManager._get_backend``) all flow through the registry.
"""

import importlib.metadata
import types

import pytest

from app.ai.bash_safety import check_review_status, first_bash_deny
from app.ai.stop_gates import resolve_skill_gates
from app.browser.manager import BrowserManager
from app.plugins import (
    ExtensionContext,
    Plugin,
    PluginContext,
    get_extension_context,
    load_plugins,
    reset_for_tests,
)

pytestmark = pytest.mark.unit


# ── fakes ───────────────────────────────────────────────────────────


def _fake_gate_module():
    """A gate module exposing the required ``check`` signature."""
    return types.SimpleNamespace(
        check=lambda result_text, task_id=None, rules=None: None
    )


class _FakeBackend:
    """A no-arg-constructible stand-in for a BrowserBackend."""


async def _fake_service():  # pragma: no cover — never awaited in tests
    pass


class FakePlugin(Plugin):
    @property
    def name(self) -> str:
        return 'fake'

    def install(self, ctx: ExtensionContext) -> None:
        ctx.register_gate('fake_gate', _fake_gate_module())
        ctx.register_browser_backend('fakebackend', _FakeBackend)
        ctx.register_service('fake_service', _fake_service)
        ctx.register_skill_source('/tmp/fake-skills')
        ctx.register_review_marker(r'FAKE 平台审计')
        ctx.register_pretool_gate(
            'Fake guard',
            lambda cmd, task_dir, catalog_read: (
                'denied' if 'TRIPWIRE' in cmd else None
            ),
        )


class _FakeEntryPoint:
    def __init__(self, name, cls):
        self.name = name
        self.value = f'fake:{name}'
        self._cls = cls

    def load(self):
        if self._cls is None:
            raise ImportError('boom')
        return self._cls


@pytest.fixture
def install_fake_plugin(monkeypatch):
    """Make ``FakePlugin`` discoverable as an installed entry point.

    Resets the singleton so the next ``get_extension_context()`` reloads
    with the fake present, and again on teardown so the real (empty)
    entry-point set is restored for other tests.
    """

    def _factory(*eps):
        def fake_entry_points(group=None):
            return list(eps) if group == 'vibe_seller.plugins' else []

        monkeypatch.setattr(
            importlib.metadata, 'entry_points', fake_entry_points
        )
        reset_for_tests()
        return get_extension_context()

    yield _factory
    reset_for_tests()


# ── builtin (OSS dogfood) ───────────────────────────────────────────


def test_builtin_registers_core_gates():
    reset_for_tests()
    try:
        gates = get_extension_context().gates
    finally:
        reset_for_tests()
    # Ad-audit gates are still core-registered. The review-collect gates
    # moved into review-collect/gates/ (skill-bundled, discovered by the
    # skill-gate loader), so they are intentionally NOT in the registry.
    for name in (
        'ad_completeness_review',
        'ad_negation_allowlist',
        'ad_execution_fidelity',
    ):
        assert name in gates, name
        assert callable(gates[name].check)
    assert 'review_completeness_review' not in gates
    assert 'review_output_gate' not in gates


def test_builtin_registers_browser_backends():
    reset_for_tests()
    try:
        backends = get_extension_context().browser_backends
    finally:
        reset_for_tests()
    assert set(backends) >= {'chrome', 'winchrome', 'ziniao'}


def test_builtin_pretool_gates_in_historical_order():
    reset_for_tests()
    try:
        names = [n for n, _ in get_extension_context().pretool_gates]
    finally:
        reset_for_tests()
    assert names == [
        'Bash safety',
        'Bid-value sanity',
        'Report-script guard',
        'Catalog-first',
    ]


def test_builtin_registers_no_skill_source():
    # ``app/skills`` is core's own dir, synced directly by skills_sync —
    # not a plugin contribution. An OSS-only registry has no skill
    # sources; plugins add their own (see the plugin test below).
    reset_for_tests()
    try:
        sources = get_extension_context().skill_sources
    finally:
        reset_for_tests()
    assert sources == []


# ── out-of-tree plugin ──────────────────────────────────────────────


def test_fake_plugin_adds_gate_backend_service(install_fake_plugin):
    ctx = install_fake_plugin(_FakeEntryPoint('fake', FakePlugin))
    # Builtin contributions survive alongside the fake plugin's.
    assert 'fake_gate' in ctx.gates
    assert 'ad_completeness_review' in ctx.gates
    assert ctx.browser_backends['fakebackend'] is _FakeBackend
    assert [n for n, _ in ctx.services] == ['fake_service']
    assert any(str(p) == '/tmp/fake-skills' for p in ctx.skill_sources)


def test_load_order_is_deterministic():
    # Builtin first, then entry points sorted by name.
    ctx = ExtensionContext(context=PluginContext.UVICORN)

    class _PluginB(FakePlugin):
        def install(self, c):
            c.register_skill_source('/tmp/b')

    class _PluginA(FakePlugin):
        def install(self, c):
            c.register_skill_source('/tmp/a')

    def fake_entry_points(group=None):
        return [
            _FakeEntryPoint('zeta', _PluginB),
            _FakeEntryPoint('alpha', _PluginA),
        ]

    orig = importlib.metadata.entry_points
    importlib.metadata.entry_points = fake_entry_points
    try:
        names = load_plugins(ctx)
    finally:
        importlib.metadata.entry_points = orig
    # builtin first, then entry points alphabetical (alpha before zeta).
    assert names == ['builtin', 'alpha', 'zeta']
    # /tmp/a (alpha) registered before /tmp/b (zeta).
    tail = [str(p) for p in ctx.skill_sources][-2:]
    assert tail == ['/tmp/a', '/tmp/b']


def test_bad_plugin_is_skipped_not_fatal():
    ctx = ExtensionContext(context=PluginContext.UVICORN)

    def fake_entry_points(group=None):
        return [_FakeEntryPoint('broken', None)]  # .load() raises

    orig = importlib.metadata.entry_points
    importlib.metadata.entry_points = fake_entry_points
    try:
        names = load_plugins(ctx)
    finally:
        importlib.metadata.entry_points = orig
    # Builtin still loaded; broken plugin skipped (logged, not raised).
    assert names == ['builtin']


def test_builtin_failure_is_fatal(monkeypatch):
    # The builtin registers safety-critical gates/guards/backends — if it
    # can't load, startup must abort rather than run with an empty
    # registry (fail-closed).
    ctx = ExtensionContext(context=PluginContext.UVICORN)
    monkeypatch.setattr(
        'app.plugins._BUILTIN_PLUGIN', 'app.builtin_plugin:DoesNotExist'
    )
    with pytest.raises(AttributeError):
        load_plugins(ctx)


def test_entry_point_enumeration_failure_is_tolerated():
    # Corrupt entry-point metadata must not take down the server: load
    # the builtin, skip external discovery.
    ctx = ExtensionContext(context=PluginContext.UVICORN)

    def boom(group=None):
        raise RuntimeError('corrupt metadata')

    orig = importlib.metadata.entry_points
    importlib.metadata.entry_points = boom
    try:
        names = load_plugins(ctx)
    finally:
        importlib.metadata.entry_points = orig
    assert names == ['builtin']


def test_non_plugin_class_is_rejected():
    ctx = ExtensionContext(context=PluginContext.UVICORN)

    class NotAPlugin:
        pass

    def fake_entry_points(group=None):
        return [_FakeEntryPoint('bogus', NotAPlugin)]

    orig = importlib.metadata.entry_points
    importlib.metadata.entry_points = fake_entry_points
    try:
        names = load_plugins(ctx)
    finally:
        importlib.metadata.entry_points = orig
    assert names == ['builtin']


# ── live call sites flow through the registry ───────────────────────


def test_resolve_skill_gates_honors_registered_plugin_gate(
    install_fake_plugin, tmp_path
):
    install_fake_plugin(_FakeEntryPoint('fake', FakePlugin))
    skill_dir = tmp_path / '.claude' / 'skills' / 's'
    skill_dir.mkdir(parents=True)
    (skill_dir / 'SKILL.md').write_text(
        '---\nname: s\ngates: [fake_gate]\n---\n\n# s\n'
    )
    resolved = resolve_skill_gates({'s'}, tmp_path)
    assert [name for name, _ in resolved] == ['fake_gate']


def test_browser_manager_get_backend_uses_registry(install_fake_plugin):
    install_fake_plugin(_FakeEntryPoint('fake', FakePlugin))
    bm = BrowserManager()
    backend = bm._get_backend('store-1', 'fakebackend')
    assert isinstance(backend, _FakeBackend)


def test_get_backend_unknown_type_raises(install_fake_plugin):
    install_fake_plugin(_FakeEntryPoint('fake', FakePlugin))
    bm = BrowserManager()
    with pytest.raises(ValueError, match='Unsupported browser backend'):
        bm._get_backend('store-1', 'no_such_backend')


def test_first_bash_deny_runs_registered_guards(install_fake_plugin):
    install_fake_plugin(_FakeEntryPoint('fake', FakePlugin))
    # The fake plugin's guard fires on TRIPWIRE — proves the chain is
    # registry-driven, not the old hardcoded tuple.
    result = first_bash_deny('echo TRIPWIRE')
    assert result == ('Fake guard', 'denied')
    # And a benign command still passes the whole chain.
    assert first_bash_deny('echo hello') is None


def test_registered_review_marker_suppresses_legacy_gate(
    install_fake_plugin, tmp_path
):
    # A plugin that reviews its own audits server-side registers a marker;
    # core's legacy REVIEW-file Stop gate then stands down for a matching
    # AD_AUDIT (no double-gating) but still fires for an unmarked one.
    install_fake_plugin(_FakeEntryPoint('fake', FakePlugin))
    (tmp_path / 'AD_AUDIT_2026-06-27.md').write_text(
        '# FAKE 平台审计 — demo\n\n## 1. 汇总\n', encoding='utf-8'
    )
    assert check_review_status(tmp_path) is None

    other = tmp_path / 'other'
    other.mkdir()
    (other / 'AD_AUDIT_2026-06-27.md').write_text(
        '# 广告优化建议 — 某平台\n\n## 某场景\n', encoding='utf-8'
    )
    deny = check_review_status(other)
    assert deny is not None and 'ads-report-review' in deny
