"""Dispatcher-level guarantees of ``POST /api/mcp``.

The dispatcher is where four review findings were moved from
per-call-site convention to property-of-the-layer:

* S6 — every tool result is redacted IN ``_call_tool`` before
  serialisation: a fake tool returning a raw secret field answers
  masked on the wire, with no ``redact()`` call of its own;
* M5 — argument TYPES are validated against the advertised schema
  (``limit: {}`` is ``INVALID_PARAMS`` naming the argument, not "Tool
  failed unexpectedly" from a crash inside ``int()``);
* M3 — the body reader stops at the cap instead of buffering whatever
  arrives (declared Content-Length refused unread; a stream that
  crosses the cap refused mid-read), and a batch over the cap is one
  clear error;
* M3 attachments — the call-level caps (count, total decoded bytes).
"""

import base64
import json
import sys

import pytest
from starlette.requests import Request

from app.agent_mcp.files import (
    MAX_ATTACHMENTS_PER_CALL,
    MAX_ATTACHMENTS_TOTAL_BYTES,
    decode_uploads,
)
from app.agent_mcp.registry import ToolDef
import app.agent_mcp.router  # noqa: F401 — loads the submodule
from app.agent_mcp.router import MAX_BODY_BYTES
from app.agent_mcp.support import McpContext, ToolError
from app.agent_mcp.tools import find_tool
from app.models.user import User

# The package's __init__ re-exports the APIRouter *object* as
# ``router``, shadowing the submodule attribute — resolve the module
# through sys.modules to reach the dispatcher's internals.
router_mod = sys.modules['app.agent_mcp.router']

pytestmark = pytest.mark.unit

SECRET = 'sk-live-secret-value-1234'


def _ctx() -> McpContext:
    scope = {
        'type': 'http',
        'method': 'POST',
        'path': '/api/mcp',
        'headers': [(b'host', b'testserver')],
        'query_string': b'',
    }
    return McpContext(
        user=User(username='ci'), db=None, request=Request(scope)
    )


def _fake_tool(monkeypatch, handler) -> ToolDef:
    definition = ToolDef(
        name='fake_tool',
        description='scratch',
        input_schema={'type': 'object', 'properties': {}},
        handler=handler,
    )
    monkeypatch.setattr(router_mod, 'find_tool', lambda name: definition)
    return definition


class TestDispatcherRedactsEveryResult:
    async def test_a_tool_that_forgets_redact_is_masked_anyway(
        self, monkeypatch
    ):
        """THE S6 pin: a handler returning a stored secret in the raw
        cannot put it on the wire — the dispatcher redacts once, for
        every tool, by construction."""

        async def handler(ctx, args):
            return {
                'name': 'acme',
                'api_key': SECRET,
                'env': {'MY_PROVIDER_KEY': SECRET, 'BASE_URL': 'https://x'},
            }

        _fake_tool(monkeypatch, handler)
        response = await router_mod._call_tool(
            {
                'jsonrpc': '2.0',
                'id': 7,
                'method': 'tools/call',
                'params': {'name': 'fake_tool', 'arguments': {}},
            },
            _ctx(),
        )
        text = response['result']['content'][0]['text']
        assert SECRET not in text
        payload = json.loads(text)
        assert payload['api_key'].startswith('****')
        assert payload['env']['MY_PROVIDER_KEY'].startswith('****')
        # plaintext neighbours survive untouched, at both levels
        assert payload['name'] == 'acme'
        assert payload['env']['BASE_URL'] == 'https://x'


class TestArgumentTypeValidation:
    """M5: the schema is enforced — types, enums, min/max, maxItems."""

    def test_an_object_where_an_integer_belongs(self):
        problem = router_mod._argument_problem(find_tool('list_tasks'), {'limit': {}})
        assert problem and 'limit' in problem and 'integer' in problem

    def test_a_string_where_an_integer_belongs(self):
        problem = router_mod._argument_problem(find_tool('list_tasks'), {'limit': '50'})
        assert problem and 'integer' in problem

    def test_a_bool_is_not_an_integer(self):
        problem = router_mod._argument_problem(find_tool('list_tasks'), {'limit': True})
        assert problem and 'boolean' in problem

    def test_an_enum_violation_names_the_choices(self):
        problem = router_mod._argument_problem(
            find_tool('task_files'), {'task_id': 'x', 'action': 'bogus'}
        )
        assert problem and 'list' in problem and 'zip_url' in problem

    def test_a_number_above_the_maximum(self):
        problem = router_mod._argument_problem(
            find_tool('get_task_log'), {'task_id': 'x', 'limit': 999_999}
        )
        assert problem and '<=' in problem

    def test_a_number_below_the_minimum(self):
        problem = router_mod._argument_problem(
            find_tool('get_task_log'), {'task_id': 'x', 'offset': -1}
        )
        assert problem and '>=' in problem

    def test_max_items_on_an_array_argument(self):
        problem = router_mod._argument_problem(
            find_tool('create_task'),
            {'description': 'd', 'attachments': [{}] * 11},
        )
        assert problem and 'at most' in problem

    def test_a_null_optional_argument_counts_as_absent(self):
        """The tools treat null optionals as absent
        (``args.get('limit') or DEFAULT``); the protocol check must
        not punish a client that sends explicit nulls."""
        assert (
            router_mod._argument_problem(find_tool('list_tasks'), {'limit': None})
            is None
        )

    def test_a_well_formed_call_passes(self):
        assert (
            router_mod._argument_problem(
                find_tool('list_tasks'), {'limit': 5, 'status': 'completed'}
            )
            is None
        )


def _request(chunks: list[bytes], content_length: str | None = None):
    headers = [(b'host', b'testserver')]
    if content_length is not None:
        headers.append((b'content-length', content_length.encode()))
    scope = {
        'type': 'http',
        'method': 'POST',
        'path': '/api/mcp',
        'headers': headers,
        'query_string': b'',
    }
    stream = iter(chunks)

    async def receive():
        try:
            return {
                'type': 'http.request',
                'body': next(stream),
                'more_body': True,
            }
        except StopIteration:
            return {
                'type': 'http.request',
                'body': b'',
                'more_body': False,
            }

    return Request(scope, receive)


class TestCappedBodyRead:
    async def test_an_honest_body_under_the_cap_arrives(self):
        request = _request([b'{"a":', b'1}'])
        assert await router_mod._read_capped_body(request) == b'{"a":1}'

    async def test_a_declared_length_past_the_cap_is_refused_unread(self):
        request = _request(
            [b'{}'], content_length=str(MAX_BODY_BYTES + 1)
        )
        assert await router_mod._read_capped_body(request) is None

    async def test_a_stream_crossing_the_cap_stops_mid_read(self):
        chunk = b'x' * 1024
        # Enough chunks to cross the cap (references to one bytes
        # object — the reader must stop before materialising them all).
        needed = MAX_BODY_BYTES // len(chunk) + 2
        request = _request([chunk] * needed)
        assert await router_mod._read_capped_body(request) is None


def _attachment(size_kb: int, name='file.png') -> dict:

    return {
        'filename': name,
        'mime_type': 'image/png',
        'content_base64': base64.b64encode(
            b'x' * (size_kb * 1024)
        ).decode('ascii'),
    }


class TestAttachmentCaps:
    """The call-level caps are enforced at the DISPATCHER (a property
    of the transport, so every tool that accepts attachments is
    bounded the same way); the per-file cap is enforced where the
    bytes are decoded."""

    def test_within_the_caps_passes(self):
        uploads = decode_uploads([_attachment(1), _attachment(1)])
        assert len(uploads) == 2

    def test_more_than_ten_entries_is_refused(self):
        with pytest.raises(ToolError, match='at most'):
            decode_uploads([_attachment(1)] * (MAX_ATTACHMENTS_PER_CALL + 1))

    def test_the_total_decoded_cap_is_enforced(self):
        each = MAX_ATTACHMENTS_TOTAL_BYTES // (
            MAX_ATTACHMENTS_PER_CALL - 1
        ) + 1024
        with pytest.raises(ToolError, match='per-call ceiling'):
            decode_uploads(
                [_attachment(each // 1024)] * (MAX_ATTACHMENTS_PER_CALL - 1)
            )

    def test_the_per_file_cap_still_applies(self):
        with pytest.raises(ToolError, match='10 MB'):
            decode_uploads([_attachment(11 * 1024)])

    def test_a_non_list_is_refused(self):
        with pytest.raises(ToolError, match='array'):
            decode_uploads({'filename': 'x'})

    def test_the_dispatcher_refuses_too_many_attachments(self):
        """The transport-level enforcement: the call is refused BEFORE
        any tool runs, with the argument named."""
        problem = router_mod._argument_problem(
            find_tool('create_task'),
            {
                'description': 'd',
                'attachments': [_attachment(1)]
                * (MAX_ATTACHMENTS_PER_CALL + 1),
            },
        )
        assert problem and 'attachments' in problem
        assert 'at most' in problem

    def test_the_dispatcher_refuses_an_oversized_total(self):
        each = MAX_ATTACHMENTS_TOTAL_BYTES // (
            MAX_ATTACHMENTS_PER_CALL - 1
        ) + 1024
        problem = router_mod._argument_problem(
            find_tool('create_task'),
            {
                'description': 'd',
                'attachments': [_attachment(each // 1024)]
                * (MAX_ATTACHMENTS_PER_CALL - 1),
            },
        )
        assert problem and 'per-call ceiling' in problem
