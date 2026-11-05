"""Hosted MCP endpoint — external agents drive vibe-seller over HTTP.

This package serves the Model Context Protocol on ``POST /api/mcp``
(Streamable HTTP, stateless / JSON-response mode) so an external agent
(Claude Code, Codex, …) that knows a URL and a token can list stores,
create tasks, follow their progress, answer the agent's questions,
approve or reject plans, and read knowledge — through the SAME service
code the web UI drives.

Why a hand-rolled JSON-RPC handler and not the official ``mcp`` SDK's
HTTP transport
------------------------------------------------------------------------
The official SDK is the right dependency in the abstract (and it is
already installed on every vibe-seller box, transitively through
``browser-use``), but its Streamable-HTTP transport is an **ASGI
application**, not a FastAPI route. Mounting it means:

* auth cannot be a FastAPI dependency. ``Depends(get_current_user)``
  only resolves inside a FastAPI route; a mounted sub-app would have
  to re-implement the auth call (open a session, call the dependency
  by hand) — a fork of the auth path. The token model lives
  *inside* ``get_current_user`` and must not be forked.
* the endpoint path needs an ASGI intercept anyway: Starlette's
  ``Mount('/api/mcp', …)`` answers ``POST /api/mcp`` with a 307 to
  ``/api/mcp/``, and a redirect is exactly the kind of thing a bearer
  token must not be sent across.
* the SDK's transport owns response shaping (Accept negotiation, SSE
  streaming, session ids). We serve one JSON document per call.

What the endpoint actually needs is small and stable: ``initialize``,
``tools/list``, ``tools/call``, ``notifications/initialized`` and
``ping``. Hand-rolling that surface keeps the whole endpoint inside
FastAPI — the auth dependency, the DB session and the JSON-RPC
framing in one place — and keeps the wire format a plain
``application/json`` response, which ``curl``, Claude Code's
``--transport http`` client and Codex's HTTP client all accept.

The protocol details that DO matter are taken from the reference
implementation rather than from memory: the supported protocol
revisions (``app/agent_mcp/protocol.py``) mirror the SDK's
``SUPPORTED_PROTOCOL_VERSIONS``, and the negotiation rule (answer with
the version the client asked for when we speak it, else our newest)
is the SDK's own rule.

Tools never touch the database directly for anything that changes
state: they call the REST route handlers in ``app/routers/`` so the
task state machine (``app/task_states.py``), the plan lifecycle, the
permission checks, the launch path and the SSE events are the SAME
code the web UI runs. See ``app/agent_mcp/tools.py``.

This is NOT ``app/mcp_server.py``. That module is the *in-task* stdio
MCP server: it runs inside a task's agent process, requires
``--task-id``, and exposes tools that can forge a task result. It is
never imported or exposed here.
"""

from app.agent_mcp.router import router

__all__ = ['router']
