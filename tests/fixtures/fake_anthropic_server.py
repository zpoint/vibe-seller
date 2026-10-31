"""Deterministic fake Anthropic Messages API — no network, no model.

A stdlib HTTP server that speaks just enough of the Anthropic
``/v1/messages`` protocol for the Claude Code CLI to run a full task
against it. Behaviour is selected by the requested MODEL name, so one
server covers every scenario without a side channel:

- ``fake-instant``         reply immediately (control/aux requests)
- ``fake-delay-<SECONDS>`` accept the request, hold it <SECONDS>
                           before the first SSE byte, then stream one
                           short text reply — a healthy but slow model
                           whose first token is late while the request
                           is still in flight
- ``fake-hang``            accept the request and never answer — a
                           provider that dropped the stream
- ``fake-tool-then-delay-<SECONDS>``
                          request 1 answers instantly with one
                          ``Read`` tool_use (a missing file is fine —
                          the error IS the tool_result); request 2+
                          wait <SECONDS> then reply — the "large
                          tool_result, then minutes of silence while
                          the model composes" phase
- ``fake-silent-after-delta``
                          headers, ``message_start`` and ONE text
                          delta, then the connection stays open and
                          silent forever — the provider "dropped the
                          stream" mid-body (issue #141): the CLI is
                          alive and the request is in flight, but
                          nothing more ever arrives

Text replies all carry the same text (``FAKE-REPLY-OK``); the task
prompt tells the agent to reply with exactly that, so a run ends in a
deterministic result with no browser (``fake-tool-then-delay`` adds
one deterministic tool hop first — its reply shape differs, not its
outcome).

The SSE framing (headers first, pause, then events) is what a real
provider looks like from the CLI's seat when the model is composing:
connection accepted, zero events until the first token is ready. Node's
undici body-timeout only fires on byte silence, so the pause is visible
to the CLI as "request in flight", not as a transport error.

Usage::

    python tests/fixtures/fake_anthropic_server.py PORT

Endpoints:

- ``GET /_health`` — rig readiness probe (200)
- ``POST /v1/messages`` — SSE stream when ``stream`` is set, JSON else
- ``POST /v1/messages/count_tokens`` — ``{"input_tokens": 1}``
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import sys
import time

REPLY_TEXT = 'FAKE-REPLY-OK'

# Longest a fake-hang connection is held before giving up on its own
# (24 h). Rig teardown kills the server long before this matters; it
# exists so a forgotten thread cannot leak forever.
_HANG_CAP_S = 24 * 3600


def _parse_mode(model: str) -> tuple[str, float]:
    """Model name -> (mode, seconds). Unknown models reply instantly."""
    if model == 'fake-hang':
        return ('hang', 0.0)
    if model == 'fake-silent-after-delta':
        return ('silent_after_delta', 0.0)
    if model.startswith('fake-tool-then-delay-'):
        tail = model[len('fake-tool-then-delay-') :]
        try:
            secs = float(tail)
        except ValueError:
            return ('instant', 0.0)
        return ('tool_then_delay', max(0.0, secs))
    if model.startswith('fake-delay-'):
        tail = model[len('fake-delay-') :]
        try:
            secs = float(tail)
        except ValueError:
            return ('instant', 0.0)
        return ('delay', max(0.0, secs))
    return ('instant', 0.0)


# Per-model ordinal of the request we are serving, so
# fake-tool-then-delay can answer request 1 with a tool_use and the
# rest with the delayed text reply. One task at a time per rig run;
# ordinality is only used to pick the reply shape, never timing.
_request_ordinal: dict[str, int] = {}


def _sse_tool_use(model: str) -> bytes:
    """One valid streaming tool_use reply (``Read`` of a file that
    does not exist — the error result is still a tool_result)."""

    def _event(payload: dict) -> bytes:
        return (
            f'event: {payload["type"]}\ndata: {json.dumps(payload)}\n\n'
        ).encode()

    parts = [
        _event({
            'type': 'message_start',
            'message': {
                'id': 'msg_fake_tool1',
                'type': 'message',
                'role': 'assistant',
                'model': model,
                'content': [],
                'stop_reason': None,
                'stop_sequence': None,
                'usage': {'input_tokens': 25, 'output_tokens': 0},
            },
        }),
        _event({
            'type': 'content_block_start',
            'index': 0,
            'content_block': {
                'type': 'tool_use',
                'id': 'toolu_fake_read1',
                'name': 'Read',
                'input': {},
            },
        }),
        _event({
            'type': 'content_block_delta',
            'index': 0,
            'delta': {
                'type': 'input_json_delta',
                'partial_json': json.dumps({
                    'file_path': '/tmp/fake-anthropic-missing-note.txt'
                }),
            },
        }),
        _event({'type': 'content_block_stop', 'index': 0}),
        _event({
            'type': 'message_delta',
            'delta': {'stop_reason': 'tool_use', 'stop_sequence': None},
            'usage': {'output_tokens': 8},
        }),
        _event({'type': 'message_stop'}),
    ]
    return b''.join(parts)


def _sse_silent_after_delta(model: str) -> bytes:
    """A stream that starts, sends ONE delta, then goes silent.

    The provider answered (headers + ``message_start`` + a text
    delta) and then dropped the stream mid-body: the connection
    stays open, but no further byte ever arrives. This is the
    issue #141 shape as seen from the CLI — the request is in
    flight, the process is alive, and stdout goes quiet.
    """

    def _event(payload: dict) -> bytes:
        return (
            f'event: {payload["type"]}\ndata: {json.dumps(payload)}\n\n'
        ).encode()

    parts = [
        _event({
            'type': 'message_start',
            'message': {
                'id': 'msg_fake_silent',
                'type': 'message',
                'role': 'assistant',
                'model': model,
                'content': [],
                'stop_reason': None,
                'stop_sequence': None,
                'usage': {'input_tokens': 25, 'output_tokens': 0},
            },
        }),
        _event({
            'type': 'content_block_start',
            'index': 0,
            'content_block': {'type': 'text', 'text': ''},
        }),
        _event({
            'type': 'content_block_delta',
            'index': 0,
            'delta': {'type': 'text_delta', 'text': REPLY_TEXT},
        }),
    ]
    return b''.join(parts)


def _sse_events(model: str) -> bytes:
    """One valid Anthropic streaming text reply, as raw SSE bytes."""

    def _event(payload: dict) -> bytes:
        return (
            f'event: {payload["type"]}\ndata: {json.dumps(payload)}\n\n'
        ).encode()

    parts = [
        _event({
            'type': 'message_start',
            'message': {
                'id': 'msg_fake_0001',
                'type': 'message',
                'role': 'assistant',
                'model': model,
                'content': [],
                'stop_reason': None,
                'stop_sequence': None,
                'usage': {'input_tokens': 25, 'output_tokens': 0},
            },
        }),
        _event({
            'type': 'content_block_start',
            'index': 0,
            'content_block': {'type': 'text', 'text': ''},
        }),
        _event({'type': 'ping'}),
        _event({
            'type': 'content_block_delta',
            'index': 0,
            'delta': {'type': 'text_delta', 'text': REPLY_TEXT},
        }),
        _event({'type': 'content_block_stop', 'index': 0}),
        _event({
            'type': 'message_delta',
            'delta': {'stop_reason': 'end_turn', 'stop_sequence': None},
            'usage': {'output_tokens': 6},
        }),
        _event({'type': 'message_stop'}),
    ]
    return b''.join(parts)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):  # quieter default logging
        sys.stderr.write(
            f'[fake-anthropic] {self.log_date_time_string()} {fmt % args}\n'
        )

    def do_GET(self):
        if self.path == '/_health':
            body = b'{"ok": true}'
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def do_POST(self):
        length = int(self.headers.get('Content-Length') or 0)
        raw = self.rfile.read(length) if length else b'{}'
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = {}
        model = str(body.get('model') or 'fake-instant')
        mode, secs = _parse_mode(model)
        ordinal = _request_ordinal.get(model, 0) + 1
        _request_ordinal[model] = ordinal

        if self.path.endswith('/count_tokens'):
            payload = json.dumps({'input_tokens': 1}).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        sys.stderr.write(
            f'[fake-anthropic] {self.log_date_time_string()} '
            f'POST /v1/messages model={model} mode={mode} '
            f'secs={secs:.0f} stream={bool(body.get("stream"))}\n'
        )
        try:
            self._reply(model, mode, secs, ordinal, bool(body.get('stream')))
        except (BrokenPipeError, ConnectionResetError):
            # Client hung up mid-pause (interrupt/timeout) — expected.
            pass

    def _reply(self, model, mode, secs, ordinal, stream):
        if stream:
            # Headers go out immediately; the pause sits before the
            # first event byte (see module docstring).
            self.close_connection = True  # EOF ends the SSE body
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Connection', 'close')
            self.end_headers()
            if mode == 'hang':
                deadline = time.monotonic() + _HANG_CAP_S
                while time.monotonic() < deadline:
                    time.sleep(30)
                return
            if mode == 'silent_after_delta':
                # Answered, then dropped the stream: flush the prefix
                # and hold the connection open with nothing more.
                self.wfile.write(_sse_silent_after_delta(model))
                self.wfile.flush()
                deadline = time.monotonic() + _HANG_CAP_S
                while time.monotonic() < deadline:
                    time.sleep(30)
                return
            if mode == 'tool_then_delay' and ordinal == 1:
                self.wfile.write(_sse_tool_use(model))
                self.wfile.flush()
                return
            if mode == 'delay' or (mode == 'tool_then_delay' and ordinal > 1):
                time.sleep(secs)
            self.wfile.write(_sse_events(model))
            self.wfile.flush()
            return
        # Non-streaming (rare from the CLI; kept for completeness).
        # The CLI's stream-idle watchdog recovers into a non-streaming
        # fallback request, so the dead-provider modes must be dead
        # there too — otherwise the fallback would "succeed" and the
        # scenario would end via recovery instead of detection.
        if mode in ('hang', 'silent_after_delta'):
            time.sleep(_HANG_CAP_S)
            return
        if mode == 'delay':
            time.sleep(secs)
        payload = json.dumps({
            'id': 'msg_fake_0001',
            'type': 'message',
            'role': 'assistant',
            'model': model,
            'content': [{'type': 'text', 'text': REPLY_TEXT}],
            'stop_reason': 'end_turn',
            'stop_sequence': None,
            'usage': {'input_tokens': 25, 'output_tokens': 6},
        }).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8791
    server = ThreadingHTTPServer(('127.0.0.1', port), _Handler)
    server.daemon_threads = True
    sys.stderr.write(f'[fake-anthropic] listening on 127.0.0.1:{port}\n')
    # Serve until killed; every connection has its own daemon thread.
    server.serve_forever()


if __name__ == '__main__':
    main()
