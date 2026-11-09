"""Collection ordering so worksteal's split cannot strand long tests.

``--dist worksteal`` hands each worker a CONTIGUOUS chunk of the
collection (a floor-division chain over collection order — verified in
xdist's ``scheduler/worksteal.py::check_schedule``), a running test can
never be stolen, and ``MIN_PENDING = 2`` keeps each busy worker's head
unstealable. Two long-budget tests adjacent in collection order
therefore land in ONE worker's chunk and run back to back while the
other workers idle (CI run 201h: the 1120s follow-up scoping test and
the 1200s unscoped audit sat together in gw0 — the step hit its 32-min
timeout at 96% with three workers idle for the last ten minutes).

Spreading them evenly fixed the stranding but created a second
collision (CI run 201i): two HEAVY generations started together on
workers that share ONE provider key (``E2E_PROVIDER_MAP`` is
three workers on one key and a fourth on another), and the wide task's turns measured ~2x the gap of
the same test running solo (p90 126s vs 65s) while a small concurrent
test on the same key was unaffected — two long generations queueing at
the provider. The rule this module implements:

* every long-budget test leads its worker's queue (starts at t=0) and
  no worker's chunk holds two of them — the stranding fix; AND
* long-budget tests land on workers with DISTINCT provider keys
  (``E2E_PROVIDER_MAP`` order, per worker) whenever the map allows —
  the congestion fix. With more longs than distinct keys the leftover
  ones fall back to the even spread.

Tests declare the budget with the ``long_budget`` marker — metadata
only; no test body, prompt or assertion changes. The criterion for
marking is MEASURED wall time, not guesswork: a test carries the
marker (with its measured p50 seconds as the marker argument) when its
p50 across the recorded CI runs is >= 240s. As of CI 201j + the
pre-#200 baseline that is nine tests: the two ad-scoping ones (602s,
530s), ads-mixed-fanout (552s), skill-save-reuse (547s),
followup-review-scoping (382s), session-reuse (296s), llm-responds
(289s), two-browser-tasks (277s) and conversation-lifecycle E4
(268s). The simulation lives in ``tests/unit/test_schedule_sim.py``
(run it to reproduce these — it reads the width, the provider map and
the step budget from ci.yml): with all nine marked and LPT placement
at the 6-worker width, the p50 critical path is 1362s (22.7 min) and
the MAX-duration path 1546s (25.8 min), both inside the 2400s
(40-min) step. At the old 4-worker width the same calculation gave
1907s/2112s against a 1920s step — the numbers that showed 32 min too
tight and moved the budget.

``xdist``'s initial split (for the pins' simulation)::

    pending = range(N)
    for each node in order:
        num_send = len(pending) // nodes_remaining
        node gets pending[:num_send]
"""

LONG_BUDGET_MARKER = 'long_budget'


def declares_long_budget(item) -> bool:
    """Does this item carry the ``long_budget`` marker?"""
    return LONG_BUDGET_MARKER in item.keywords


def declared_budget(item) -> float:
    """The measured p50 seconds the marker carries (0 when bare)."""
    getter = getattr(item, 'get_closest_marker', None)
    if getter is None:
        return 0.0
    marker = getter(LONG_BUDGET_MARKER)
    return float(marker.args[0]) if marker and marker.args else 0.0


def chunk_starts(total: int, workers: int) -> list[int]:
    """Where worksteal's floor-div chain starts each worker's chunk."""
    starts: list[int] = []
    pos = 0
    pending = total
    for i in range(workers):
        starts.append(pos)
        take = pending // (workers - i)
        pos += take
        pending -= take
    return starts


def long_budget_order(items, providers=None, workers=4):
    """Return *items* reordered per the rules above.

    *providers* is the per-worker provider-key list (the
    ``E2E_PROVIDER_MAP`` order) and *workers* the split width — the
    placement is chunk-aware: candidates are assigned to worker chunks
    (distinct provider keys first), then placed at the START of each
    chunk and through its early region — never in a tail, where a
    candidate would sit behind a queue of shorts and begin minutes
    late (the 201j failure). Non-long items keep their relative order.
    """
    items = list(items)
    longs = [it for it in items if declares_long_budget(it)]
    if not longs:
        return items
    # Longest-processing-time first: the heaviest candidates claim the
    # first chunks, so the chunk that ends up with the most work carries
    # the least extra (the marker's measured seconds decide the order —
    # placement from measured durations, not guesses).
    longs.sort(key=declared_budget, reverse=True)
    total = len(items)
    budgets = [declared_budget(it) for it in longs]
    slots = _slots(total, len(longs), workers, providers, budgets)
    out: list = [None] * total
    for slot, item in zip(slots, longs):
        out[slot] = item
    fill = iter(it for it in items if not declares_long_budget(it))
    for i, slot in enumerate(out):
        if slot is None:
            out[i] = next(fill)
    return out


def _slots(
    total: int,
    count: int,
    workers: int,
    providers,
    budgets: list[float],
) -> list[int]:
    """Slot per long: chunk heads first (provider-distinct), then each
    assigned chunk's early region."""
    workers = max(1, workers)
    starts = chunk_starts(total, workers)
    bounds = starts[1:] + [total]
    # Chunk visit order: a fresh provider key first (so two heavy
    # generations never share one key when the map can separate them).
    order = list(range(workers))
    if providers:
        providers = list(providers)[:workers] + [
            f'__unmapped{i}' for i in range(max(0, workers - len(providers)))
        ]
        seen: set = set()
        fresh = [
            i
            for i in order
            if providers[i] not in seen and not seen.add(providers[i])
        ]
        order = fresh + [i for i in range(workers) if i not in fresh]
    per_chunk: dict[int, int] = {c: 0 for c in range(workers)}
    load: dict[int, float] = {c: 0.0 for c in range(workers)}
    assigned: list[int] = []
    for k in range(count):
        # LPT: the longest remaining candidate goes to the chunk with
        # the least accumulated budget — but the first visits prefer a
        # fresh provider key (two heavy generations must not share one
        # key when the map can separate them).
        if k < len(order):
            chunk = order[k]
        else:
            chunk = min(load, key=lambda c: (load[c], per_chunk[c], c))
        assigned.append(chunk)
        per_chunk[chunk] += 1
        load[chunk] += budgets[k]
    within: dict[int, int] = {c: 0 for c in range(workers)}
    slots: list[int] = []
    for chunk in assigned:
        j = within[chunk]
        within[chunk] += 1
        m = per_chunk[chunk]
        size = bounds[chunk] - starts[chunk]
        region = max(m, 2 * size // 3)  # early two-thirds of the chunk
        slots.append(starts[chunk] + (j * region) // m)
    return slots
