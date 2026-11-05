"""Unit tests for the MCP support layer — the call_route chokepoint.

A direct call to a FastAPI route function bypasses dependency
injection: a parameter the route takes via ``Depends(...)`` or a
param marker would silently receive the marker object. ``call_route``
must refuse such a call instead.
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


def test_missing_marker_param_raises():
    """`db` supplied but `q` (a Query marker) is not → refuse."""
    with pytest.raises(RouteCallError, match="'q'"):
        call_route(_dummy_route, x=1, db='sess')


def test_missing_required_param_raises():
    """No default at all and not supplied → refuse."""
    with pytest.raises(RouteCallError, match="'x'"):
        call_route(_dummy_route, db='sess', q=None)


def test_all_markers_supplied_passes():
    """Every marker satisfied; plain defaults are the route's own."""
    result = call_route(_dummy_route, x=1, db='sess', q='v')
    assert result is not None  # the route's coroutine, awaited by the caller


def test_plain_default_may_be_omitted():
    """`flag: bool = True` is not a marker — omitting it is fine."""
    coro = call_route(_dummy_route, x=1, db='sess', q=None)
    coro.close()
    # The route itself still sees its own default.
    sig = inspect.signature(_dummy_route)
    assert sig.parameters['flag'].default is True


def test_route_gaining_a_depends_is_caught():
    """The regression the guard exists for: a route that worked
    yesterday gains a ``Depends`` parameter today."""

    async def route_that_grew(task_id: str):
        return task_id

    # Before: the adapter's call was fine.
    call_route(route_that_grew, task_id='t1').close()

    async def route_that_grew_now(task_id: str, db=Depends(get_db)):
        return task_id

    # After: the same adapter call must fail loudly, not pass the
    # Depends marker object in as `db`.
    with pytest.raises(RouteCallError, match="'db'"):
        call_route(route_that_grew_now, task_id='t1')


async def _route_with_body(x: int, payload=Body(None)):
    """A route that takes a JSON body — Body is NOT a Param subclass,
    so a check that only looks for Param/Depends would miss it."""
    return (x, payload)


def test_body_marker_is_caught():
    """The marker that does NOT inherit from Param."""
    with pytest.raises(RouteCallError, match="'payload'"):
        call_route(_route_with_body, x=1)


def test_body_marker_is_caught_even_with_the_other_params_supplied():
    """`x` is supplied; the Body one is still refused."""
    with pytest.raises(RouteCallError, match="'payload'"):
        call_route(_route_with_body, x=1)


def test_body_route_works_when_supplied():
    """Supplying the body explicitly is fine."""
    coro = call_route(_route_with_body, x=1, payload={'a': 1})
    coro.close()
