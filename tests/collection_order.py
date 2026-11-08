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

The fix is ordering, not budgets: spread the long-budget tests evenly
through the collection (slot ``k * N / L``) so that when there are no
more of them than workers, each contiguous chunk of ~``N / L``…
``N / W`` holds at most one and each one starts its worker's queue at
``t=0``. Tests declare the budget with the ``long_budget`` marker —
the marker is metadata; no test body, prompt or assertion changes.

``xdist``'s initial split (for the pin's simulation)::

    pending = range(N)
    for each node in order:
        num_send = len(pending) // nodes_remaining
        node gets pending[:num_send]
"""

LONG_BUDGET_MARKER = 'long_budget'


def declares_long_budget(item) -> bool:
    """Does this item carry the ``long_budget`` marker?"""
    return LONG_BUDGET_MARKER in item.keywords


def long_budget_order(items):
    """Return *items* reordered: long-budget tests evenly spread.

    Non-long items keep their relative order and fill every other slot.
    With ``L <= workers`` no contiguous chunk of the worksteal initial
    split holds two long tests (spacing ``N/L >= N/W``); with
    ``L > workers`` the spread is still even but a worker may see two —
    unavoidable with contiguous chunks.
    """
    items = list(items)
    longs = [it for it in items if declares_long_budget(it)]
    if not longs:
        return items
    total = len(items)
    out: list = [None] * total
    for k, item in enumerate(longs):
        out[k * total // len(longs)] = item
    fill = iter(it for it in items if not declares_long_budget(it))
    for i, slot in enumerate(out):
        if slot is None:
            out[i] = next(fill)
    return out
