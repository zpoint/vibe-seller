"""Which ads skill a task gets, and how a store key is resolved.

Both are decided before the agent sees anything, which is the point: a
task that carried both skills would leave the agent to pick between
"drive the console" and "call the API", and a store key guessed from a
store with two marketplaces reports one market's spend as another's.
"""

import pytest

from app.ads_client import AdsServiceError, resolve_store_key, store_keys
from app.ads_routing import API_SKILL, BROWSER_SKILL, skills_to_exclude

pytestmark = pytest.mark.unit


class _Store:
    def __init__(self, keys: str, name: str = 'acme'):
        self.ads_store_keys = keys
        self.name = name


class TestSkillRouting:
    def test_authorized_store_drops_the_browser_skill(self):
        assert skills_to_exclude([True]) == {BROWSER_SKILL}

    def test_unauthorized_store_drops_the_api_skill(self):
        assert skills_to_exclude([False]) == {API_SKILL}

    def test_no_store_keeps_the_browser_skill(self):
        """A no-store task still has the store-less web browser."""
        assert skills_to_exclude([]) == {API_SKILL}

    def test_a_mixed_task_keeps_both(self):
        """Real mid-migration case, and a reason to prefer sub-tasks.

        With both present the union of their exit gates applies, and the
        browser skill's gates ask an API-path result to prove it read a
        page it never opened.
        """
        assert skills_to_exclude([True, False]) == set()

    def test_all_authorized_across_several_stores(self):
        assert skills_to_exclude([True, True, True]) == {BROWSER_SKILL}


class TestStoreKeys:
    def test_missing_or_broken_json_reads_as_unbound(self):
        assert store_keys(_Store('')) == {}
        assert store_keys(_Store('not json')) == {}

    def test_single_marketplace_needs_no_name(self):
        store = _Store('{"SA": "sk_one"}')
        assert resolve_store_key(store, None) == 'sk_one'

    def test_several_marketplaces_refuse_to_guess(self):
        """Guessing is how one market's spend is filed under another."""
        store = _Store('{"SA": "sk_sa", "AE": "sk_ae"}')
        with pytest.raises(AdsServiceError) as exc:
            resolve_store_key(store, None)
        message = str(exc.value)
        assert 'SA' in message and 'AE' in message

    def test_named_marketplace_selects(self):
        store = _Store('{"SA": "sk_sa", "AE": "sk_ae"}')
        assert resolve_store_key(store, 'ae') == 'sk_ae'

    def test_unknown_marketplace_lists_what_is_authorized(self):
        store = _Store('{"SA": "sk_sa"}')
        with pytest.raises(AdsServiceError) as exc:
            resolve_store_key(store, 'US')
        assert 'SA' in str(exc.value)

    def test_unbound_store_says_where_to_fix_it(self):
        with pytest.raises(AdsServiceError) as exc:
            resolve_store_key(_Store('{}'), None)
        assert 'Settings' in str(exc.value)
