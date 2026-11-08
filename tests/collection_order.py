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
only; no test body, prompt or assertion changes.

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


def long_budget_order(items, providers=None):
    """Return *items* reordered per the two rules above.

    *providers* is the per-worker provider-key list (the
    ``E2E_PROVIDER_MAP`` order); ``None`` falls back to the plain even
    spread. Non-long items keep their relative order everywhere.
    """
    items = list(items)
    longs = [it for it in items if declares_long_budget(it)]
    if not longs:
        return items
    total = len(items)
    slots = _slots(total, len(longs), providers)
    out: list = [None] * total
    for slot, item in zip(slots, longs):
        out[slot] = item
    fill = iter(it for it in items if not declares_long_budget(it))
    for i, slot in enumerate(out):
        if slot is None:
            out[i] = next(fill)
    return out


def _slots(total: int, count: int, providers) -> list[int]:
    """Slot per long: chunk starts with fresh provider keys first."""
    slots: list[int] = []
    if providers:
        starts = chunk_starts(total, len(providers))
        seen: set = set()
        for key, start in zip(providers, starts):
            if key in seen:
                continue
            seen.add(key)
            slots.append(start)
            if len(slots) == count:
                return slots
    # Even spread for the remainder (and the no-map fallback).
    for k in range(len(slots), count):
        slot = k * total // count
        while slot in slots:
            slot += 1
        slots.append(slot % total)
    return slots
