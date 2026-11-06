"""Unit tests for the MCP support layer — the call_route chokepoint.

A direct call to a FastAPI route function bypasses dependency
injection: a parameter the route takes via ``Depends(...)`` or a
param marker would silently receive the marker object. ``call_route``
must refuse such a call instead.

It must also mirror FastAPI's def/async split: ``async def`` routes
run on the loop, plain ``def`` routes (the blocking-fs pattern) run
in a thread — so an MCP adapter never freezes the event loop with
work the web UI would have offloaded.
"""

import inspect

from fastapi import Body, Depends, Query
import pytest

from app.agent_mcp.support import RouteCallError, call_route
from app.database import get_db

pytestmark = pytest.mark.unit


async def _dummy_route(
    x: int,
    db=Depends(lambda: 'session'),
    q=Query(None),
    flag: bool = True,
):
    """A route shaped like the real ones: marker params the adapter
    must supply, and a plain default it may honour."""
    return (x, db, q, flag)


def _dummy_sync_route(x: int, db=Depends(lambda: 'session')):
    """A plain ``def`` route — FastAPI's blocking-work pattern."""
    return (x, db)


async def test_missing_marker_param_raises():
    """`db` supplied but `q` (a Query marker) is not → refuse."""
    with pytest.raises(RouteCallError, match="'q'"):
        await call_route(_dummy_route, x=1, db='sess')


async def test_missing_required_param_raises():
    """No default at all and not supplied → refuse."""
    with pytest.raises(RouteCallError, match="'x'"):
        await call_route(_dummy_route, db='sess', q=None)


async def test_all_markers_supplied_passes():
    """Every marker satisfied; plain defaults are the route's own."""
    result = await call_route(_dummy_route, x=1, db='sess', q='v')
    assert result == (1, 'sess', 'v', True)


async def test_plain_default_may_be_omitted():
    """`flag: bool = True` is not a marker — omitting it is fine."""
    result = await call_route(_dummy_route, x=1, db='sess', q=None)
    # The route itself still sees its own default.
    assert result == (1, 'sess', None, True)
    sig = inspect.signature(_dummy_route)
    assert sig.parameters['flag'].default is True


async def test_sync_route_is_awaitable_and_runs():
    """A plain ``def`` route goes through the same seam."""
    assert await call_route(_dummy_sync_route, x=2, db='sess') == (2, 'sess')


async def test_route_gaining_a_depends_is_caught():
    """The regression the guard exists for: a route that worked
    yesterday gains a ``Depends`` parameter today."""

    async def route_that_grew(task_id: str):
        return task_id

    # Before: the adapter's call was fine.
    assert await call_route(route_that_grew, task_id='t1') == 't1'

    async def route_that_grew_now(task_id: str, db=Depends(get_db)):
        return task_id

    # After: the same adapter call must fail loudly, not pass the
    # Depends marker object in as `db`.
    with pytest.raises(RouteCallError, match="'db'"):
        await call_route(route_that_grew_now, task_id='t1')


async def _route_with_body(x: int, payload=Body(None)):
    """A route that takes a JSON body — Body is NOT a Param subclass,
    so a check that only looks for Param/Depends would miss it."""
    return (x, payload)


async def test_body_marker_is_caught():
    """The marker that does NOT inherit from Param."""
    with pytest.raises(RouteCallError, match="'payload'"):
        await call_route(_route_with_body, x=1)


async def test_body_marker_is_caught_even_with_the_other_params_supplied():
    """`x` is supplied; the Body one is still refused."""
    with pytest.raises(RouteCallError, match="'payload'"):
        await call_route(_route_with_body, x=1)


async def test_body_route_works_when_supplied():
    """Supplying the body explicitly is fine."""
    result = await call_route(_route_with_body, x=1, payload={'a': 1})
    assert result == (1, {'a': 1})
