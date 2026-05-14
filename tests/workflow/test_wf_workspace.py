"""Workflow tests for workspace file CRUD, skills, and store profiles."""

import pytest

pytestmark = pytest.mark.workflow


class TestFileOps:
    async def test_write_read_file(self, admin_client):
        # Write
        r = await admin_client.put(
            '/api/workspace/file?path=test.txt',
            json={'content': 'hello world'},
        )
        assert r.status_code == 200

        # Read
        r = await admin_client.get('/api/workspace/file?path=test.txt')
        assert r.status_code == 200
        assert r.json()['content'] == 'hello world'

    async def test_read_nonexistent_404(self, admin_client):
        r = await admin_client.get('/api/workspace/file?path=no-such-file.txt')
        assert r.status_code == 404

    async def test_delete_file(self, admin_client):
        # Create
        await admin_client.put(
            '/api/workspace/file?path=del.txt',
            json={'content': 'bye'},
        )
        # Delete
        r = await admin_client.delete('/api/workspace/file?path=del.txt')
        assert r.status_code == 200

        # Confirm gone
        r = await admin_client.get('/api/workspace/file?path=del.txt')
        assert r.status_code == 404

    async def test_path_traversal_blocked(self, admin_client):
        r = await admin_client.get('/api/workspace/file?path=../../etc/passwd')
        assert r.status_code == 400


class TestTree:
    async def test_tree_lists_contents(self, admin_client):
        # Write a file first
        await admin_client.put(
            '/api/workspace/file?path=tree-test.txt',
            json={'content': 'data'},
        )
        r = await admin_client.get('/api/workspace/tree')
        assert r.status_code == 200
        paths = [item['path'] for item in r.json()]
        assert 'tree-test.txt' in paths


class TestSkills:
    async def test_create_skill(self, admin_client):
        r = await admin_client.post(
            '/api/workspace/skill',
            json={
                'name': 'test-skill',
                'description': 'A test skill',
            },
        )
        assert r.status_code == 200
        assert r.json()['status'] == 'created'
        assert 'test-skill' in r.json()['path']


class TestStoreProfiles:
    async def test_create_store_profile(self, admin_client):
        r = await admin_client.post(
            '/api/workspace/store-profile',
            json={
                'slug': 'my-store',
                'name': 'My Store',
                'platform': 'amazon',
                'country': 'US',
                'backend': 'chrome',
            },
        )
        assert r.status_code == 200
        assert r.json()['status'] == 'created'
        assert 'my-store' in r.json()['path']
