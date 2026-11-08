"""Long-budget tests may not sit adjacent in the collection order.

Brief #15 problem 1 (CI run 201h): ``--dist worksteal`` gives each
worker a contiguous chunk of the collection and a running test cannot
be stolen (``MIN_PENDING`` also pins the head), so the 1120s
follow-up scoping test and the 1200s unscoped audit — adjacent in one
module — both landed in gw0 and ran back to back. The step timed out
at 96% with three workers idle for its last ten minutes.

The simulation below is worksteal's OWN initial split
(``xdist/scheduler/worksteal.py::check_schedule``: a floor-division
chain over collection order), so the pin is against the scheduler's
real behavior, not a guess.
"""

from tests.collection_order import (
    declares_long_budget,
    long_budget_order,
)


class _FakeItem:
    def __init__(self, name, *, long_budget=False):
        self.name = name
        self.keywords = {'long_budget': True} if long_budget else {}

    def __repr__(self):
        return self.name


def _worksteal_initial_chunks(items, workers):
    """xdist worksteal's initial distribution, replicated."""
    pending = list(range(len(items)))
    chunks = []
    for i in range(workers):
        nodes_remaining = workers - i
        num_send = len(pending) // nodes_remaining
        chunks.append(pending[:num_send])
        del pending[:num_send]
    return chunks


def _raw_suite():
    # The CI shape: two long-budget tests ADJACENT in one module at
    # the head of the collection, then the usual short tests.
    return [
        _FakeItem('narrow-follow-up', long_budget=True),
        _FakeItem('unscoped-audit', long_budget=True),
        *(_FakeItem(f'short-{i}') for i in range(18)),
    ]


def test_the_raw_order_strands_both_long_tests_on_one_worker():
    """Negative control — the bug this fixes. Both long tests in the
    same initial chunk (gw0), running back to back."""
    items = _raw_suite()
    chunks = _worksteal_initial_chunks(items, 4)
    first_chunk_longs = [
        items[i] for i in chunks[0] if declares_long_budget(items[i])
    ]
    assert len(first_chunk_longs) == 2, 'the premise of the fix'
    assert all(
        len([items[i] for i in chunk if declares_long_budget(items[i])]) == 0
        for chunk in chunks[1:]
    ), 'and no other worker ever sees one'


def test_the_ordered_collection_holds_at_most_one_long_per_worker():
    items = long_budget_order(_raw_suite())
    chunks = _worksteal_initial_chunks(items, 4)
    per_chunk = [
        [items[i] for i in chunk if declares_long_budget(items[i])]
        for chunk in chunks
    ]
    assert all(len(longs) <= 1 for longs in per_chunk), per_chunk
    assert sum(len(longs) for longs in per_chunk) == 2


def test_each_long_test_starts_its_workers_queue():
    """The critical path needs them running from t=0, not queued
    behind a worker's earlier tests."""
    items = long_budget_order(_raw_suite())
    chunks = _worksteal_initial_chunks(items, 4)
    firsts = [
        declares_long_budget(items[chunk[0]]) for chunk in chunks if chunk
    ]
    assert sum(firsts) == 2, 'both long tests must lead their chunk'


def test_short_tests_keep_their_relative_order():
    raw = _raw_suite()
    ordered = long_budget_order(raw)
    shorts_raw = [i.name for i in raw if not declares_long_budget(i)]
    shorts_ord = [i.name for i in ordered if not declares_long_budget(i)]
    assert shorts_raw == shorts_ord


def test_an_unmarked_collection_is_untouched():
    items = [_FakeItem(f's{i}') for i in range(5)]
    assert long_budget_order(items) == items


def test_heavy_tests_land_on_distinct_provider_workers():
    """CI 201i's collision: two heavy generations on workers that
    share one provider key queue at the provider (the wide task's
    turns measured 2x the gaps of the same test solo). The map's
    distinct keys get the longs."""
    providers = ['provider-a', 'provider-a', 'provider-a', 'provider-b']
    items = long_budget_order(_raw_suite(), providers)
    chunks = _worksteal_initial_chunks(items, 4)
    long_chunks = [
        w
        for w, chunk in enumerate(chunks)
        if any(declares_long_budget(items[i]) for i in chunk)
    ]
    assert len(long_chunks) == 2
    assert len({providers[w] for w in long_chunks}) == 2, long_chunks
    # and the stranding property still holds
    for chunk in chunks:
        assert len([i for i in chunk if declares_long_budget(items[i])]) <= 1


def test_the_plain_spread_collides_on_a_three_same_key_map():
    """Negative control — the 201i shape. Without the provider map the
    even spread lands both longs on same-key workers."""
    providers = ['provider-a', 'provider-a', 'provider-a', 'provider-b']
    items = long_budget_order(_raw_suite())
    chunks = _worksteal_initial_chunks(items, 4)
    long_chunks = [
        w
        for w, chunk in enumerate(chunks)
        if any(declares_long_budget(items[i]) for i in chunk)
    ]
    assert {providers[w] for w in long_chunks} == {'provider-a'}


def test_more_longs_than_distinct_keys_falls_back_to_the_spread():
    providers = ['provider-a', 'provider-a']
    raw = [_FakeItem(f'long-{i}', long_budget=True) for i in range(3)] + [
        _FakeItem(f's{i}') for i in range(9)
    ]
    items = long_budget_order(raw, providers, workers=2)
    chunks = _worksteal_initial_chunks(items, 2)
    for chunk in chunks:
        assert len([i for i in chunk if declares_long_budget(items[i])]) <= 2
    assert sum(declares_long_budget(i) for i in items) == 3


def test_the_real_suite_shape_nine_candidates_on_four_workers():
    """The 201j shape: nine measured candidates (p50 >= 240s) on four
    workers. Every chunk holds its share (ceil(L/W) = 3) and the
    candidates are spread through the collection — none sits in a
    worker's tail behind a queue of shorts (the 201j failure: the
    547s skill test started at minute 22)."""
    raw = [_FakeItem(f'long-{i}', long_budget=True) for i in range(9)]
    raw += [_FakeItem(f's{i}') for i in range(62)]
    items = long_budget_order(raw, ['pa', 'pa', 'pa', 'pb'])
    chunks = _worksteal_initial_chunks(items, 4)
    per_chunk = [
        [items[i] for i in chunk if declares_long_budget(items[i])]
        for chunk in chunks
    ]
    assert all(len(longs) <= 3 for longs in per_chunk), [
        len(longs) for longs in per_chunk
    ]
    assert sum(len(longs) for longs in per_chunk) == 9
    # every chunk's LAST third holds no candidate — nothing is buried
    for chunk in chunks:
        tail = chunk[len(chunk) * 2 // 3 :]
        assert not any(declares_long_budget(items[i]) for i in tail), chunk
