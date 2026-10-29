"""Workflow tests for AI profile CRUD and presets."""

import pytest

from app.ai.profile_validation import ProfileValidationResult
from tests.workflow.conftest import wait_for_task

pytestmark = pytest.mark.workflow


class TestProfileCrud:
    async def test_create_profile_roundtrip(self, admin_client):
        r = await admin_client.post(
            '/api/profiles',
            json={
                'id': 'test-profile',
                'name': 'Test',
                'env': {'FOO': 'bar'},
                'description': 'A test profile',
            },
        )
        assert r.status_code == 200
        created = r.json()
        assert created['id'] == 'test-profile'
        assert created['env'] == {'FOO': 'bar'}

        # List and verify present
        r = await admin_client.get('/api/profiles')
        assert r.status_code == 200
        ids = [p['id'] for p in r.json()['profiles']]
        assert 'test-profile' in ids

    async def test_create_with_preset_env(self, admin_client):
        # Get presets
        r = await admin_client.get('/api/profiles/presets')
        presets = r.json()['presets']
        assert 'kimi' in presets

        # Create profile using preset env vars
        kimi_env = presets['kimi']['env']
        r = await admin_client.post(
            '/api/profiles',
            json={
                'id': 'my-kimi',
                'name': 'My Kimi',
                'env': {**kimi_env, 'ANTHROPIC_API_KEY': 'sk-xxx'},
            },
        )
        assert r.status_code == 200
        assert r.json()['env']['ANTHROPIC_MODEL'] == 'k3.1-pro[1m]'

    async def test_update_profile_env_vars(self, admin_client):
        # Create
        await admin_client.post(
            '/api/profiles',
            json={
                'id': 'upd-profile',
                'name': 'Update Me',
                'env': {'A': '1'},
            },
        )
        # Update
        r = await admin_client.put(
            '/api/profiles/upd-profile',
            json={'env': {'A': '2', 'B': '3'}},
        )
        assert r.status_code == 200
        assert r.json()['env'] == {'A': '2', 'B': '3'}

    async def test_delete_custom_profile(self, admin_client):
        await admin_client.post(
            '/api/profiles',
            json={
                'id': 'del-me',
                'name': 'Delete Me',
                'env': {},
            },
        )
        r = await admin_client.delete('/api/profiles/del-me')
        assert r.status_code == 200

        # Verify gone
        r = await admin_client.get('/api/profiles')
        ids = [p['id'] for p in r.json()['profiles']]
        assert 'del-me' not in ids

    async def test_cannot_delete_default(self, admin_client):
        r = await admin_client.delete('/api/profiles/default')
        assert r.status_code == 400

    async def test_presets_returns_known_providers(self, admin_client):
        r = await admin_client.get('/api/profiles/presets')
        assert r.status_code == 200
        presets = r.json()['presets']
        for name in (
            'kimi',
            'minimax',
            'glm',
            'glm_intl',
            'deepseek',
            'qwen',
            'qwen_intl',
            'qwen_coding',
            'qwen_token',
        ):
            assert name in presets
            assert 'name' in presets[name]
            assert 'env' in presets[name]

    async def test_grouped_presets_expose_group_and_variant(self, admin_client):
        """The Alibaba Cloud and GLM variants carry group + variant so
        the UI can render them under one collapsible top-level entry."""
        r = await admin_client.get('/api/profiles/presets')
        presets = r.json()['presets']
        alibaba = {
            pid
            for pid, p in presets.items()
            if p.get('group') == 'Alibaba Cloud'
        }
        assert alibaba == {'qwen', 'qwen_intl', 'qwen_coding', 'qwen_token'}
        glm = {pid for pid, p in presets.items() if p.get('group') == 'GLM'}
        assert glm == {'glm', 'glm_intl'}
        for pid in alibaba | glm:
            assert presets[pid]['variant'], f'{pid} missing variant label'
        # Standalone providers stay ungrouped.
        assert not presets['kimi'].get('group')
        assert not presets['deepseek'].get('group')

    async def test_presets_expose_model_options(self, admin_client):
        """Every preset ships a non-empty model dropdown whose first
        (default) option matches the env's ANTHROPIC_MODEL — the UI and
        the injected env must agree on the default model."""
        r = await admin_client.get('/api/profiles/presets')
        presets = r.json()['presets']
        for pid, preset in presets.items():
            models = preset.get('models')
            assert models, f'{pid} has no model options'
            for opt in models:
                assert opt['id'] and opt['label']
            assert models[0]['id'] == preset['env']['ANTHROPIC_MODEL'], (
                f'{pid}: default model option must equal ANTHROPIC_MODEL'
            )

    async def test_vision_labels_match_live_probe(self, admin_client):
        """Vision flags are pinned to what a live 2-color image probe
        (64x64 red + blue, x3) actually found — NOT web search:

        - Qwen plus / flash / VL read both colors -> vision
          (live-verified on the pay-go endpoint, 2026-10).
        - qwen3.7-max (400 on images) and glm-5.3 (misnamed both
          colors) -> text-only.
        - 2026-10-29 re-probe: glm-5.3-flash (Z.AI), deepseek-flash,
          MiniMax-M3.1-Flash and MiniMax-M3 (api.minimax.cn) all named
          red+blue correctly x3 -> vision. This settles the old
          MiniMax-M3 doc-vs-live conflict in favor of the doc (the
          earlier contradicting probe predates the M3 rollout).
        - Kimi has no usable key (lapsed Kimi Code subscription), so
          its vision is doc-verified (Moonshot's vision guide lists
          kimi-k3.1-pro / kimi-k3 / kimi-k2.7-code) -> True.
        - qwen3.8-max / qwen3.8-flash have no key to probe -> vision
          OMITTED (None) rather than guessed.
        """
        r = await admin_client.get('/api/profiles/presets')
        presets = r.json()['presets']

        def vis(pid, mid):
            m = next(o for o in presets[pid]['models'] if o['id'] == mid)
            return m.get('vision')

        # Live-verified vision-capable
        assert vis('qwen', 'qwen3.7-plus') is True
        assert vis('qwen', 'qwen3.6-flash') is True
        assert vis('qwen', 'qwen3-vl-plus') is True
        assert vis('deepseek', 'deepseek-flash[1m]') is True
        assert vis('deepseek', 'deepseek-flash') is True
        assert vis('minimax', 'MiniMax-M3.1-Flash[1m]') is True
        assert vis('minimax', 'MiniMax-M3[1m]') is True
        assert vis('glm', 'glm-5.3-flash[1m]') is True
        # Live-verified text-only
        assert vis('qwen', 'qwen3.7-max') is False
        assert vis('deepseek', 'deepseek-v4-pro[1m]') is False
        assert vis('glm', 'glm-5.3[1m]') is False
        assert vis('glm', 'glm-5.2[1m]') is False
        assert vis('glm', 'glm-4.7') is False
        # Kimi: doc-verified vision (Moonshot vision guide), no key.
        assert vis('kimi', 'k3.1-pro[1m]') is True
        assert vis('kimi', 'kimi-for-coding') is True
        # New Qwen ids with no key: omitted, not guessed.
        assert vis('qwen', 'qwen3.8-max') is None
        assert vis('qwen_token', 'auto') is None

    async def test_presets_match_vendor_docs(self, admin_client):
        """Pin the load-bearing values for the vendor-doc-aligned
        presets so a typo or accidental revert is caught by CI rather
        than only surfacing on a real agent run. Asserts:

        - Kimi: api.kimi.ai endpoint + k3.1-pro[1m] in every slot
          (incl. the FABLE tier) with the 1M window vars.
        - MiniMax: api.minimax.cn endpoint + M3.1-Flash everywhere and
          the 512K auto-compact window + effort=max per its doc.
        - DeepSeek: deepseek-flash[1m] main/sonnet/opus (the doc's
          current recommendation; flash reads images, v4-pro does
          not), bare deepseek-flash for haiku/subagent, and the 768K
          auto-compact window.
        - Qwen split: pay-as-you-go vs Coding Plan vs Token Plan have
          DIFFERENT base URLs and DIFFERENT model name tiers.
        """
        r = await admin_client.get('/api/profiles/presets')
        presets = r.json()['presets']

        kimi = presets['kimi']['env']
        assert kimi['ANTHROPIC_BASE_URL'] == 'https://api.kimi.ai/coding/'
        assert kimi['ANTHROPIC_MODEL'] == 'k3.1-pro[1m]'
        assert kimi['ANTHROPIC_DEFAULT_FABLE_MODEL'] == 'k3.1-pro[1m]'
        assert kimi['ANTHROPIC_DEFAULT_HAIKU_MODEL'] == 'k3.1-pro[1m]'
        assert kimi['CLAUDE_CODE_SUBAGENT_MODEL'] == 'k3.1-pro[1m]'
        assert kimi['CLAUDE_CODE_EFFORT_LEVEL'] == 'high'
        assert kimi['CLAUDE_CODE_AUTO_COMPACT_WINDOW'] == '1048576'

        mm = presets['minimax']['env']
        assert mm['ANTHROPIC_BASE_URL'] == 'https://api.minimax.cn/anthropic'
        assert mm['ANTHROPIC_MODEL'] == 'MiniMax-M3.1-Flash[1m]'
        assert mm['ANTHROPIC_DEFAULT_HAIKU_MODEL'] == ('MiniMax-M3.1-Flash[1m]')
        assert mm['CLAUDE_CODE_AUTO_COMPACT_WINDOW'] == '524288'
        assert mm['CLAUDE_CODE_EFFORT_LEVEL'] == 'max'

        glm = presets['glm']['env']
        assert glm['ANTHROPIC_MODEL'] == 'glm-5.3[1m]'
        assert glm['ANTHROPIC_DEFAULT_SONNET_MODEL'] == 'glm-5.3[1m]'
        assert glm['ANTHROPIC_DEFAULT_HAIKU_MODEL'] == 'glm-5.3-flash[1m]'
        glm_intl = presets['glm_intl']['env']
        assert glm_intl['ANTHROPIC_BASE_URL'] == (
            'https://api.z.ai/api/anthropic'
        )
        assert glm_intl['ANTHROPIC_MODEL'] == 'glm-5.3[1m]'
        assert glm_intl['ANTHROPIC_DEFAULT_HAIKU_MODEL'] == (
            'glm-5.3-flash[1m]'
        )

        ds = presets['deepseek']['env']
        assert ds['ANTHROPIC_BASE_URL'] == (
            'https://api.deepseek.com/anthropic'
        )
        assert ds['ANTHROPIC_MODEL'] == 'deepseek-flash[1m]'
        assert ds['ANTHROPIC_DEFAULT_OPUS_MODEL'] == 'deepseek-flash[1m]'
        assert ds['ANTHROPIC_DEFAULT_SONNET_MODEL'] == 'deepseek-flash[1m]'
        assert ds['ANTHROPIC_DEFAULT_HAIKU_MODEL'] == 'deepseek-flash'
        assert ds['CLAUDE_CODE_SUBAGENT_MODEL'] == 'deepseek-flash'
        assert ds['CLAUDE_CODE_EFFORT_LEVEL'] == 'max'
        assert ds['CLAUDE_CODE_AUTO_COMPACT_WINDOW'] == '786432'
        # The legacy var is gone from every preset (no provider doc
        # still sets it).
        for preset in presets.values():
            assert 'ANTHROPIC_SMALL_FAST_MODEL' not in preset['env']

        qwen = presets['qwen']['env']
        assert qwen['ANTHROPIC_BASE_URL'] == (
            'https://dashscope.aliyuncs.com/apps/anthropic'
        )
        assert qwen['ANTHROPIC_MODEL'] == 'qwen3.7-max'
        assert qwen['ANTHROPIC_DEFAULT_HAIKU_MODEL'] == 'qwen3.6-flash'
        # Qwen gets its 1M window from the token count, not a [1m]
        # suffix (that 400s on DashScope — live-verified). No Qwen id
        # may carry the suffix.
        assert qwen['CLAUDE_CODE_MAX_CONTEXT_TOKENS'] == '1000000'
        assert not any('[1m]' in v for v in qwen.values()), (
            'Qwen ids must not use the [1m] suffix'
        )

        qwen_cp = presets['qwen_coding']['env']
        assert qwen_cp['ANTHROPIC_BASE_URL'] == (
            'https://coding.dashscope.aliyuncs.com/apps/anthropic'
        )
        # Coding Plan uses one model uniformly across tiers + subagent
        assert qwen_cp['ANTHROPIC_MODEL'] == 'qwen3.7-plus'
        assert qwen_cp['ANTHROPIC_DEFAULT_OPUS_MODEL'] == 'qwen3.7-plus'
        assert qwen_cp['ANTHROPIC_DEFAULT_SONNET_MODEL'] == 'qwen3.7-plus'
        assert qwen_cp['ANTHROPIC_DEFAULT_HAIKU_MODEL'] == 'qwen3.7-plus'
        assert qwen_cp['CLAUDE_CODE_SUBAGENT_MODEL'] == 'qwen3.7-plus'

        # Token Plan: distinct base URL + Alibaba's current tier
        # routing (auto main/subagent, qwen3.8 tiers) + the explicit
        # ~960K context window per Alibaba docs.
        qwen_tp = presets['qwen_token']['env']
        assert qwen_tp['ANTHROPIC_BASE_URL'] == (
            'https://token-plan.cn-beijing.maas.aliyuncs.com/apps/anthropic'
        )
        assert qwen_tp['ANTHROPIC_MODEL'] == 'auto'
        assert qwen_tp['ANTHROPIC_DEFAULT_HAIKU_MODEL'] == 'qwen3.6-flash'
        assert qwen_tp['ANTHROPIC_DEFAULT_SONNET_MODEL'] == 'qwen3.8-flash'
        assert qwen_tp['ANTHROPIC_DEFAULT_OPUS_MODEL'] == 'qwen3.8-max'
        assert qwen_tp['CLAUDE_CODE_SUBAGENT_MODEL'] == 'auto'
        assert qwen_tp['CLAUDE_CODE_MAX_CONTEXT_TOKENS'] == '983616'

        # International pay-go carries the per-workspace placeholder host.
        qwen_intl = presets['qwen_intl']['env']
        assert '{WorkspaceId}' in qwen_intl['ANTHROPIC_BASE_URL']
        assert qwen_intl['ANTHROPIC_MODEL'] == 'qwen3.7-max'

        # The four Alibaba plans MUST all diverge on base URL.
        bases = {
            qwen['ANTHROPIC_BASE_URL'],
            qwen_cp['ANTHROPIC_BASE_URL'],
            qwen_tp['ANTHROPIC_BASE_URL'],
            qwen_intl['ANTHROPIC_BASE_URL'],
        }
        assert len(bases) == 4

    async def test_profile_env_injection_roundtrip(self, admin_client):
        env = {
            'ANTHROPIC_API_KEY': 'sk-test-key',
            'ANTHROPIC_BASE_URL': 'https://example.com',
        }
        r = await admin_client.post(
            '/api/profiles',
            json={
                'id': 'inject-test',
                'name': 'Inject',
                'env': env,
            },
        )
        assert r.status_code == 200

        # Read back via list
        r = await admin_client.get('/api/profiles')
        profile = next(
            p for p in r.json()['profiles'] if p['id'] == 'inject-test'
        )
        assert profile['env'] == env

    async def test_profile_used_in_task(self, admin_client, install_fake_agent):
        """Task creation uses default profile; agent receives it."""
        store_r = await admin_client.post(
            '/api/stores', json={'name': 'Profile Store'}
        )
        assert store_r.status_code == 200
        store_id = store_r.json()['id']

        r = await admin_client.post(
            '/api/tasks',
            json={'title': 'Profile task', 'store_id': store_id},
        )
        assert r.status_code == 200
        task_id = r.json()['id']

        # Wait for pipeline to complete
        data = await wait_for_task(admin_client, task_id)
        assert data['status'] == 'completed'

        # Verify agent received the default profile_id
        calls = install_fake_agent.get_calls(task_id=task_id, action='run')
        assert len(calls) >= 1
        assert any(c.profile_id == 'default' for c in calls)


class TestDefaultProfile:
    async def test_set_default_profile(self, admin_client):
        """Set a custom profile as the user's default."""
        await admin_client.post(
            '/api/profiles',
            json={'id': 'my-prof', 'name': 'Mine', 'env': {}},
        )
        r = await admin_client.patch('/api/profiles/my-prof/set-default')
        assert r.status_code == 200
        assert r.json()['default_profile_id'] == 'my-prof'

        # Verify via /auth/me
        me = await admin_client.get('/api/auth/me')
        assert me.json()['default_profile_id'] == 'my-prof'

    async def test_set_default_nonexistent_404(self, admin_client):
        """Cannot set a nonexistent profile as default."""
        r = await admin_client.patch(
            '/api/profiles/no-such-profile/set-default'
        )
        assert r.status_code == 404

    async def test_delete_default_resets_to_builtin(self, admin_client):
        """Deleting the user's default profile resets to 'default'."""
        await admin_client.post(
            '/api/profiles',
            json={'id': 'del-def', 'name': 'Del Def', 'env': {}},
        )
        await admin_client.patch('/api/profiles/del-def/set-default')
        # Verify set
        me = await admin_client.get('/api/auth/me')
        assert me.json()['default_profile_id'] == 'del-def'

        # Delete it
        await admin_client.delete('/api/profiles/del-def')

        # Should reset to 'default'
        me2 = await admin_client.get('/api/auth/me')
        assert me2.json()['default_profile_id'] == 'default'


class TestProfileValidateEndpoint:
    """``POST /api/profiles/validate`` — the pre-save endpoint probe.

    The actual network round-trip is unit-tested in
    ``test_profile_validation.py``; here we pin the endpoint contract:
    always 200, verdict in the body, no persistence.
    """

    async def test_validate_ok_passthrough(self, admin_client, monkeypatch):
        async def fake(env, **kwargs):
            assert env['ANTHROPIC_MODEL'] == 'deepseek-v4-pro[1m]'
            return ProfileValidationResult(
                ok=True, reported_model='deepseek-v4-pro'
            )

        monkeypatch.setattr('app.routers.profiles.validate_profile_env', fake)
        r = await admin_client.post(
            '/api/profiles/validate',
            json={
                'env': {
                    'ANTHROPIC_BASE_URL': 'https://api.deepseek.com/anthropic',
                    'ANTHROPIC_AUTH_TOKEN': 'sk-x',
                    'ANTHROPIC_MODEL': 'deepseek-v4-pro[1m]',
                }
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body['ok'] is True
        assert body['reported_model'] == 'deepseek-v4-pro'

    async def test_validate_failure_is_200_with_reason(
        self, admin_client, monkeypatch
    ):
        async def fake(env, **kwargs):
            return ProfileValidationResult(
                ok=False, code='auth', error='authentication failed'
            )

        monkeypatch.setattr('app.routers.profiles.validate_profile_env', fake)
        r = await admin_client.post(
            '/api/profiles/validate',
            json={'env': {'ANTHROPIC_BASE_URL': 'https://x/anthropic'}},
        )
        # A bad config is a normal 200 verdict, not an HTTP error — the
        # frontend reads ``ok`` directly instead of branching on status.
        assert r.status_code == 200
        body = r.json()
        assert body['ok'] is False
        assert body['code'] == 'auth'
        assert 'authentication failed' in body['error']

    async def test_validate_does_not_persist(self, admin_client, monkeypatch):
        async def fake(env, **kwargs):
            return ProfileValidationResult(ok=True)

        monkeypatch.setattr('app.routers.profiles.validate_profile_env', fake)
        before = {
            p['id']
            for p in (await admin_client.get('/api/profiles')).json()[
                'profiles'
            ]
        }
        await admin_client.post(
            '/api/profiles/validate',
            json={
                'env': {'ANTHROPIC_BASE_URL': 'https://x/anthropic'},
                'id': 'should-not-be-created',
                'name': 'Ghost',
            },
        )
        after = {
            p['id']
            for p in (await admin_client.get('/api/profiles')).json()[
                'profiles'
            ]
        }
        assert before == after
