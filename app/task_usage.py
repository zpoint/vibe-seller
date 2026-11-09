"""Persist the live usage meter's deltas and shape them for the UI.

The meter (app/ai/usage_meter.py) says what one API call changed; this
adds it to the task row's running totals and returns the snapshot that
both the ``task_usage`` SSE event and ``TaskResponse`` carry.
"""

from __future__ import annotations

import logging

from app.ai.model_pricing import BASIS_CATALOG, BASIS_INCLUDED
from app.ai.usage_meter import UsageDelta, UsageMeter
from app.database import async_session
from app.events.bus import event_bus
from app.models.task import Task

logger = logging.getLogger(__name__)

#: A run that saw any priced call is priced; one that saw only plan
#: calls is included; nothing priced at all is unpriced.
_BASIS_RANK = {BASIS_CATALOG: 2, BASIS_INCLUDED: 1}

USAGE_FIELDS = (
    'cost_usd',
    'input_tokens',
    'output_tokens',
    'cache_read_tokens',
    'cache_write_tokens',
    'context_tokens',
    'context_window',
    'usage_model',
    'cost_basis',
    'num_turns',
)


def usage_snapshot(task: Task) -> dict:
    return {name: getattr(task, name) for name in USAGE_FIELDS}


def _merge_basis(current: str | None, new: str) -> str:
    if current is None:
        return new
    return (
        new
        if _BASIS_RANK.get(new, 0) > _BASIS_RANK.get(current, 0)
        else (current)
    )


def apply_delta(task: Task, delta: UsageDelta) -> None:
    """Add one call's delta to the row (pure — no I/O)."""
    task.cost_usd = (task.cost_usd or 0.0) + delta.cost_usd
    task.input_tokens = (task.input_tokens or 0) + delta.input_tokens
    task.output_tokens = (task.output_tokens or 0) + delta.output_tokens
    task.cache_read_tokens = (
        task.cache_read_tokens or 0
    ) + delta.cache_read_tokens
    task.cache_write_tokens = (
        task.cache_write_tokens or 0
    ) + delta.cache_write_tokens
    task.cost_basis = _merge_basis(task.cost_basis, delta.basis)
    if delta.context_tokens is not None:
        task.context_tokens = delta.context_tokens
        task.usage_model = delta.model or task.usage_model
        if delta.context_window:
            task.context_window = delta.context_window


async def record_usage(task_id: str, delta: UsageDelta) -> dict | None:
    """Apply ``delta`` to the task row; the new snapshot, or ``None``."""
    try:
        async with async_session() as db:
            task = await db.get(Task, task_id)
            if task is None:
                return None
            apply_delta(task, delta)
            await db.commit()
            return usage_snapshot(task)
    except Exception:
        logger.warning('usage record failed for %s', task_id, exc_info=True)
        return None


async def set_context_window(task_id: str, window: int) -> dict | None:
    """Override the window with the CLI's own figure (see caller)."""
    try:
        async with async_session() as db:
            task = await db.get(Task, task_id)
            if task is None or task.context_window == window:
                return None
            task.context_window = window
            await db.commit()
            return usage_snapshot(task)
    except Exception:
        logger.warning('context window update failed', exc_info=True)
        return None


def _cli_window(model_usage: object, model: str | None) -> int:
    """The CLI's contextWindow for ``model``, else 0.

    ``modelUsage`` is keyed by the id the CLI REQUESTED
    (``claude-x[1m]``); the API echoes it without the suffix.
    """
    if not isinstance(model_usage, dict) or not model:
        return 0
    for key, usage in model_usage.items():
        if (
            isinstance(key, str)
            and key.split('[', 1)[0] == model
            and isinstance(usage, dict)
            and isinstance(usage.get('contextWindow'), int)
        ):
            return usage['contextWindow']
    return 0


async def meter_event(
    task_id: str, meter: UsageMeter, provider: str | None, event: dict
) -> None:
    """Feed one stream-json event; persist + push what a call changed.

    One DB write and one ``task_usage`` SSE per call update (a
    message_start and its message_delta), never per text delta.
    """
    snapshot = None
    delta = meter.feed(event)
    if delta is not None:
        snapshot = await record_usage(task_id, delta)
    elif event.get('type') == 'result' and provider == 'anthropic':
        # On api.anthropic.com the CLI knows the window it actually runs
        # (200K vs 1M) better than the catalog's model maximum. Other
        # providers' windows are unknown to the CLI (it assumes Claude's),
        # so there the catalog stays authoritative.
        window = _cli_window(event.get('modelUsage'), meter.main_model)
        if isinstance(window, int) and window > 0 and meter.main_model:
            meter.cli_windows[meter.main_model] = window
            snapshot = await set_context_window(task_id, window)
    if snapshot is not None:
        await event_bus.emit('task_usage', {'task_id': task_id, **snapshot})
