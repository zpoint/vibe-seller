"""Store folder names (slugs) through the API: create, rename, delete.

The bug: a store's folder name was the ASCII left over from its name, so
「示例甲1店」 and 「示例乙1店」 were both ``1`` and the second store was
refused ("both reduce to the same folder name '1'"). The slug is now
stored and unique (app/store_slugs.py), and nothing else a user sees
changes: the same folder names while they are free, the same 409 for
names that really are alike, the same rename behaviour.
"""

import asyncio

import pytest
from sqlalchemy import select

from app.config import VIBE_SELLER_DIR
from app.models.store import Store
from app.models.task import Task
import app.routers.tasks as tasks_mod
from tests.workflow.conftest import wait_for_task

pytestmark = pytest.mark.workflow


async def _create(client, name, **extra):
    return await client.post('/api/stores', json={'name': name, **extra})


async def _slug_of(store_id: str) -> str | None:
    async with tasks_mod.async_session() as db:
        store = await db.get(Store, store_id)
        return store.slug if store else None


class TestCreate:
    async def test_two_chinese_names_with_the_same_digit(
        self, admin_client, mock_workspace
    ):
        """The reported bug. Before the fix the second create was a 409
        naming the folder "1"."""
        r1 = await _create(admin_client, '示例甲1店')
        r2 = await _create(admin_client, '示例乙1店')
        assert r1.status_code == 200, r1.text
        assert r2.status_code == 200, r2.text
        a, b = r1.json(), r2.json()
        assert a['slug'] == '1'  # the folder name it always had
        assert b['slug'] != a['slug']
        for store in (a, b):
            profile = mock_workspace.root / 'stores' / store['slug']
            assert store['name'] in (profile / 'STORE.md').read_text()

    async def test_both_at_once(self, admin_client):
        r1, r2 = await asyncio.gather(
            _create(admin_client, '示例甲1店'),
            _create(admin_client, '示例乙1店'),
        )
        assert (r1.status_code, r2.status_code) == (200, 200)
        assert r1.json()['slug'] != r2.json()['slug']

    @pytest.mark.parametrize(
        'names',
        [
            ['xx1店', 'xx2店', 'xy1店', 'xy店', '店1', '1店'],
            ['Acme 沙特', 'Acme 阿联酋', 'Acme'],
            ['悦互联店', '悦互联店'],  # an identical all-Chinese name
            ['东京ストア', 'متجر 1', 'Café Shop', '🛒 Shop', '空格 店铺'],
            ['  前后空格  ', '中\u3000全角空格', 'Tab\t店'],
            ['a/b', '../evil', 'C:\\shop', 'CON', 'x' * 120],
        ],
    )
    async def test_extreme_names_each_get_their_own_folder(
        self, admin_client, mock_workspace, names
    ):
        stores = []
        for name in names:
            r = await _create(admin_client, name)
            assert r.status_code == 200, (name, r.text)
            assert r.json()['name'] == name  # saved as typed
            stores.append(r.json())
        slugs = [s['slug'] for s in stores]
        assert len(set(slugs)) == len(slugs), slugs
        root = mock_workspace.root / 'stores'
        for s in stores:
            folder = root / s['slug']
            assert folder.parent == root  # never outside stores/
            assert (folder / 'STORE.md').is_file()
            assert len(f'{s["slug"]}-deadbeef-w9') <= 64

    @pytest.mark.parametrize(
        ('first', 'second'),
        [
            ('Acme', 'acme!'),
            ('My Shop', 'my_shop'),
            ('Acme', 'ACME'),
            ('示例1店', '示例1店'),  # the same name twice
            ('示例1店', ' 示例1店 '),
        ],
    )
    async def test_names_that_were_refused_still_are(
        self, admin_client, first, second
    ):
        """Unchanged for users: alike ASCII names and the same name."""
        assert (await _create(admin_client, first)).status_code == 200
        r = await _create(admin_client, second)
        assert r.status_code == 409
        assert 'Name collides with existing store' in r.json()['detail']


class TestRename:
    async def test_rename_keeps_tasks_files_and_moves_the_folder(
        self, admin_client, mock_workspace, install_fake_agent
    ):
        r = await _create(admin_client, '示例甲1店')
        store = r.json()
        sid, old = store['id'], store['slug']
        root = mock_workspace.root
        (root / 'stores' / old / 'notes.md').write_text(
            '# Notes for 示例甲1店\nkeep me\n', 'utf-8'
        )
        data = root / 'store-data' / old / 'reports' / 'oct.csv'
        data.parent.mkdir(parents=True)
        data.write_text('a,b\n1,2\n')
        t = await admin_client.post(
            '/api/tasks', json={'title': 'before rename', 'store_id': sid}
        )
        await wait_for_task(admin_client, t.json()['id'])

        r = await admin_client.put(
            f'/api/stores/{sid}', json={'name': '示例甲2店'}
        )
        assert r.status_code == 200, r.text
        new = r.json()['slug']
        assert new == '2' == await _slug_of(sid)

        # Everything followed the store.
        notes = (root / 'stores' / new / 'notes.md').read_text('utf-8')
        assert notes == '# Notes for 示例甲2店\nkeep me\n'
        assert (root / 'store-data' / new / 'reports' / 'oct.csv').is_file()
        assert not (root / 'stores' / old).exists()
        assert not (root / 'store-data' / old).exists()
        listed = (
            await admin_client.get('/api/tasks', params={'store_id': sid})
        ).json()
        assert [x['title'] for x in listed] == ['before rename']

    async def test_rename_onto_a_digit_another_store_has(self, admin_client):
        """「示例乙2店」 would have been refused: "2" is taken."""
        await _create(admin_client, '示例丙2店')
        r = await _create(admin_client, '示例乙1店')
        sid = r.json()['id']
        r = await admin_client.put(
            f'/api/stores/{sid}', json={'name': '示例乙2店'}
        )
        assert r.status_code == 200, r.text
        assert r.json()['slug'] not in ('1', '2')

    async def test_a_rename_that_keeps_the_folder_moves_nothing(
        self, admin_client, mock_workspace
    ):
        r = await _create(admin_client, '示例甲1店')
        sid, slug = r.json()['id'], r.json()['slug']
        r = await admin_client.put(
            f'/api/stores/{sid}', json={'name': '示例甲 1 店'}
        )
        assert r.status_code == 200
        assert r.json()['slug'] == slug
        assert (mock_workspace.root / 'stores' / slug / 'STORE.md').is_file()

    async def test_rename_to_an_alike_ascii_name_is_still_refused(
        self, admin_client
    ):
        await _create(admin_client, 'Store Q')
        r = await _create(admin_client, 'Store R')
        r = await admin_client.put(
            f'/api/stores/{r.json()["id"]}', json={'name': 'store_q'}
        )
        assert r.status_code == 409


class TestDeleteAndRecreate:
    async def test_delete_removes_the_row_and_its_tasks(
        self, admin_client, install_fake_agent
    ):
        r = await _create(admin_client, '示例乙1店')
        sid = r.json()['id']
        t = await admin_client.post(
            '/api/tasks', json={'title': 'x', 'store_id': sid}
        )
        await wait_for_task(admin_client, t.json()['id'])

        r = await admin_client.delete(f'/api/stores/{sid}')
        assert r.status_code == 200
        r = await admin_client.get(f'/api/stores/{sid}')
        assert r.status_code == 404
        listed = (await admin_client.get('/api/stores')).json()
        assert sid not in {s['id'] for s in listed}
        async with tasks_mod.async_session() as db:
            assert await db.get(Store, sid) is None
            left = await db.execute(select(Task).where(Task.store_id == sid))
            assert left.scalars().all() == []

    async def test_the_same_name_can_be_created_again(
        self, admin_client, mock_workspace
    ):
        for name in ('示例乙1店', 'Reusable Shop'):
            r = await _create(admin_client, name)
            first = r.json()
            folder = mock_workspace.root / 'stores' / first['slug']
            (folder / 'notes.md').write_text('old store notes', 'utf-8')
            await admin_client.delete(f'/api/stores/{first["id"]}')

            r = await _create(admin_client, name)
            assert r.status_code == 200, (name, r.text)
            again = r.json()
            assert again['id'] != first['id']
            # The new store starts fresh — never the deleted one's notes.
            mine = mock_workspace.root / 'stores' / again['slug']
            assert not (mine / 'notes.md').exists()

    async def test_delete_with_files_removes_its_folders(
        self, admin_client, mock_workspace
    ):
        r = await _create(admin_client, '示例乙1店')
        store = r.json()
        wrapper_dir = VIBE_SELLER_DIR / 'bin' / store['slug']
        wrapper_dir.mkdir(parents=True, exist_ok=True)
        r = await admin_client.delete(
            f'/api/stores/{store["id"]}', params={'delete_files': True}
        )
        assert r.status_code == 200
        assert not (mock_workspace.root / 'stores' / store['slug']).exists()
        assert not wrapper_dir.exists()
