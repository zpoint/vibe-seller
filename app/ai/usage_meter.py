"""Live token / cost / context accounting from the CLI's stream-json.

One :class:`UsageMeter` per agent process. It is fed every stdout event
and answers, per API call, "what changed": the token and cost DELTA to
add to the task's running totals, and — for the main agent — how full
the context window now is.

Why per call and not per ``result``: the ``result`` event's
``total_cost_usd`` / ``modelUsage`` are cumulative for the PROCESS (a
follow-up in the same process reports the earlier turns again), and its
cost prices every model as Claude. Per-call usage is exact and ours to
price.

Sources, both deduplicated by the API message id:

* ``stream_event`` ``message_start`` / ``message_delta`` (partial
  messages are on — ``CLAUDE_CODE_INCLUDE_PARTIAL_MESSAGES``): the
  start carries the prompt-side counts, the delta the FINAL output
  count. This is the exact source.
* ``assistant`` events: one per content block, each repeating the
  message's usage as of when the block closed (output not yet final).
  They cover subagents and any run without partial messages.

A field only ever grows (``max``): the delta's output count supersedes
the block's provisional one, and a block repeating a count is a no-op.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from app.ai.model_pricing import (
    BASIS_UNPRICED,
    ModelPrice,
    cost_usd,
)

_FIELDS = (
    ('input_tokens', 'input_tokens'),
    ('output_tokens', 'output_tokens'),
    ('cache_read_tokens', 'cache_read_input_tokens'),
    ('cache_write_tokens', 'cache_creation_input_tokens'),
)


@dataclass
class _Call:
    model: str | None = None
    main: bool = False
    counts: dict[str, int] = field(
        default_factory=lambda: {name: 0 for name, _ in _FIELDS}
    )
    cost: float = 0.0
    #: Of cache_write_tokens, those written with a one-hour TTL.
    one_hour: int = 0


@dataclass(frozen=True)
class UsageDelta:
    """What one event changed; add the token/cost fields to totals."""

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: float
    model: str | None
    basis: str
    #: Main-agent prompt + output size after this call, else ``None``.
    context_tokens: int | None
    context_window: int | None


def _int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0
    return max(0, int(value))


class UsageMeter:
    """Per-process meter. ``price_of(model)`` resolves rates/window."""

    def __init__(self, price_of: Callable[[str | None], ModelPrice]):
        self._price_of = price_of
        self._calls: dict[str, _Call] = {}
        # Open message per agent (None = main), for message_delta,
        # which carries no message id.
        self._open: dict[str | None, str] = {}
        self._anon = 0
        #: Model of the main agent's latest call (``modelUsage`` key).
        self.main_model: str | None = None
        #: Context windows the CLI itself reported, which beat the
        #: catalog's (see app.task_usage.meter_event for when).
        self.cli_windows: dict[str, int] = {}

    def feed(self, event: dict) -> UsageDelta | None:
        etype = event.get('type')
        parent = event.get('parent_tool_use_id') or None
        if etype == 'stream_event':
            inner = event.get('event')
            if not isinstance(inner, dict):
                return None
            if inner.get('type') == 'message_start':
                msg = inner.get('message')
                if not isinstance(msg, dict):
                    return None
                key = msg.get('id') or self._anon_key()
                self._open[parent] = key
                return self._merge(
                    key, msg.get('model'), msg.get('usage'), main=parent is None
                )
            if inner.get('type') == 'message_delta':
                key = self._open.get(parent)
                if key is None:
                    return None
                return self._merge(
                    key, None, inner.get('usage'), main=parent is None
                )
            return None
        if etype == 'assistant':
            msg = event.get('message')
            # No id → cannot be told apart from the next block of the
            # same message; the stream_event path still counts it.
            if not isinstance(msg, dict) or not msg.get('id'):
                return None
            return self._merge(
                msg['id'],
                msg.get('model'),
                msg.get('usage'),
                main=parent is None,
            )
        return None

    def _anon_key(self) -> str:
        self._anon += 1
        return f'anon-{self._anon}'

    def _merge(
        self, key: str, model: object, usage: object, *, main: bool
    ) -> UsageDelta | None:
        if not isinstance(usage, dict):
            return None
        call = self._calls.get(key)
        if call is None:
            call = self._calls[key] = _Call(main=main)
        if isinstance(model, str) and model and call.model is None:
            call.model = model
        if call.main and call.model:
            self.main_model = call.model
        before = dict(call.counts)
        for name, wire in _FIELDS:
            call.counts[name] = max(call.counts[name], _int(usage.get(wire)))
        split = usage.get('cache_creation')
        if isinstance(split, dict):
            call.one_hour = max(
                call.one_hour, _int(split.get('ephemeral_1h_input_tokens'))
            )
        if call.counts == before:
            return None
        price = self._price_of(call.model)
        cost = cost_usd(
            price, **call.counts, cache_write_1h_tokens=call.one_hour
        )
        new_cost = cost if cost is not None else 0.0
        cost_delta, call.cost = new_cost - call.cost, new_cost
        context = None
        if call.main:
            context = (
                call.counts['input_tokens']
                + call.counts['cache_read_tokens']
                + call.counts['cache_write_tokens']
                + call.counts['output_tokens']
            )
        return UsageDelta(
            **{n: call.counts[n] - before[n] for n, _ in _FIELDS},
            cost_usd=cost_delta,
            model=call.model,
            basis=price.basis if call.model else BASIS_UNPRICED,
            context_tokens=context,
            context_window=(
                self.cli_windows.get(call.model or '') or price.context
                if call.main
                else None
            ),
        )
