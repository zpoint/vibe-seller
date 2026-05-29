"""Workflow tests for AI profile CRUD and presets."""

import pytest

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
        assert r.json()['env']['ANTHROPIC_MODEL'] == 'kimi-k2.5'

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
            'qwen_coding',
        ):
            assert name in presets
            assert 'name' in presets[name]
            assert 'env' in presets[name]

    async def test_presets_match_vendor_docs(self, admin_client):
        """Pin the load-bearing values for the vendor-doc-aligned
        presets so a typo or accidental revert is caught by CI rather
        than only surfacing on a real agent run. Asserts:

        - DeepSeek: ``[1m]`` suffix on opus/sonnet (1M-context variant),
          ``deepseek-v4-flash`` for haiku/small-fast/subagent, and the
          new ``CLAUDE_CODE_EFFORT_LEVEL=max`` flag.
        - Qwen split: pay-as-you-go vs Coding Plan have DIFFERENT
          base URLs and DIFFERENT model name tiers.
        """
        r = await admin_client.get('/api/profiles/presets')
        presets = r.json()['presets']

        ds = presets['deepseek']['env']
        assert ds['ANTHROPIC_BASE_URL'] == (
            'https://api.deepseek.com/anthropic'
        )
        assert ds['ANTHROPIC_MODEL'] == 'deepseek-v4-pro[1m]'
        assert ds['ANTHROPIC_DEFAULT_OPUS_MODEL'] == 'deepseek-v4-pro[1m]'
        assert ds['ANTHROPIC_DEFAULT_SONNET_MODEL'] == 'deepseek-v4-pro[1m]'
        assert ds['ANTHROPIC_DEFAULT_HAIKU_MODEL'] == 'deepseek-v4-flash'
        assert ds['ANTHROPIC_SMALL_FAST_MODEL'] == 'deepseek-v4-flash'
        assert ds['CLAUDE_CODE_SUBAGENT_MODEL'] == 'deepseek-v4-flash'
        assert ds['CLAUDE_CODE_EFFORT_LEVEL'] == 'max'

        qwen = presets['qwen']['env']
        assert qwen['ANTHROPIC_BASE_URL'] == (
            'https://dashscope.aliyuncs.com/apps/anthropic'
        )
        assert qwen['ANTHROPIC_MODEL'] == 'qwen3.7-max'
        assert qwen['ANTHROPIC_DEFAULT_HAIKU_MODEL'] == 'qwen3.6-flash'
        assert qwen['ANTHROPIC_SMALL_FAST_MODEL'] == 'qwen3.6-flash'

        qwen_cp = presets['qwen_coding']['env']
        assert qwen_cp['ANTHROPIC_BASE_URL'] == (
            'https://coding.dashscope.aliyuncs.com/apps/anthropic'
        )
        # Coding Plan uses one model uniformly across tiers + subagent
        assert qwen_cp['ANTHROPIC_MODEL'] == 'qwen3.6-plus'
        assert qwen_cp['ANTHROPIC_DEFAULT_OPUS_MODEL'] == 'qwen3.6-plus'
        assert qwen_cp['ANTHROPIC_DEFAULT_SONNET_MODEL'] == 'qwen3.6-plus'
        assert qwen_cp['ANTHROPIC_DEFAULT_HAIKU_MODEL'] == 'qwen3.6-plus'
        assert qwen_cp['CLAUDE_CODE_SUBAGENT_MODEL'] == 'qwen3.6-plus'

        # The two qwen tiers MUST diverge on base URL — collapsing them
        # back into one preset is what this PR's split was undoing.
        assert qwen['ANTHROPIC_BASE_URL'] != qwen_cp['ANTHROPIC_BASE_URL']

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
