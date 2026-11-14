"""One store per slug: a name another store's slug already has is refused.

Everything a store owns on disk and in the browser layer is keyed by the
slug its name reduces to — the browser-use wrapper, the browser profile
and its CDP proxy, the downloads dir, ``stores/<slug>/``. Two stores
with one slug share all of it, and deleting one removes the other's
wrapper and stops its browser (measured in the e2e suite: two workers
created ``worker-e2e-<same second>`` and one test's browser was stopped
by the other test's teardown).
"""

from fastapi import HTTPException
import pytest

from app.routers import stores

pytestmark = pytest.mark.unit


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _Db:
    """The one query the check makes: every store's (id, name)."""

    def __init__(self, rows):
        self.rows = list(rows)

    async def execute(self, _query):
        return _Rows(self.rows)


async def test_a_name_with_another_stores_slug_is_refused():
    db = _Db([('id-1', 'Acme')])
    with pytest.raises(HTTPException) as err:
        await stores._refuse_taken_slug(db, 'acme!')
    assert err.value.status_code == 409
    assert "'acme'" in err.value.detail


async def test_an_identical_name_is_refused():
    db = _Db([('id-1', 'worker-e2e-100')])
    with pytest.raises(HTTPException):
        await stores._refuse_taken_slug(db, 'worker-e2e-100')


async def test_a_distinct_slug_is_allowed():
    db = _Db([('id-1', 'Acme')])
    await stores._refuse_taken_slug(db, 'Acme Two')


async def test_a_rename_may_keep_its_own_slug():
    """Renaming "Acme" to "ACME" is the same store keeping its folder."""
    db = _Db([('id-1', 'Acme')])
    await stores._refuse_taken_slug(db, 'ACME', store_id='id-1')


async def test_a_name_with_no_ascii_is_unique_by_its_id():
    db = _Db([('id-1', '店铺')])
    await stores._refuse_taken_slug(db, '店铺')
