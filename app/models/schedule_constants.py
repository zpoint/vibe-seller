"""Centralized constants for the Schedule model.

These replace scattered magic strings (historically `'_catalog_sync'`
checked by equality in many places) with a single source of truth
so behaviors are driven by named columns, not hardcoded IDs.
"""

import enum

# ID of the system-seeded catalog regeneration schedule.
# Stable primary key across installs — import from here wherever
# the row needs to be named by ID.
SYSTEM_CATALOG_SYNC_ID = '_catalog_sync'


class PhaseMode(enum.StrEnum):
    """Orchestration shape for an all-stores schedule tick.

    Only consulted when `store_id is None`. Store-bound schedules
    always produce exactly one store-scoped task per tick.
    """

    FANOUT = 'fanout'
    # One Task per active store, all fired in parallel.

    SINGLE = 'single'
    # One Task with store_id=None per tick. For work that is not
    # per-store (shared IMAP mailbox sweeps, account health checks,
    # housekeeping jobs, etc.) where fanning out per store would be
    # wasteful or semantically wrong.

    TWO_PHASE = 'two_phase'
    # Single prerequisite Task (no store) awaited to completion,
    # then per-store fanout. Used by catalog sync where the L2
    # (global) catalog must be rebuilt before any L3 (per-store).
    # System-only: not exposed in the user-facing create/edit UI.


# Phase modes a client may request when creating/editing a schedule.
# `two_phase` is reserved for system seeds (catalog sync).
USER_SELECTABLE_PHASE_MODES: frozenset[str] = frozenset({
    PhaseMode.FANOUT.value,
    PhaseMode.SINGLE.value,
})


class StalenessCheck(enum.StrEnum):
    """Pre-flight check that may short-circuit task execution.

    When set, `auto_run_task` runs the named check before spawning
    the agent; if source files are unchanged since the last output,
    the task completes immediately with an "up-to-date" result.
    """

    CATALOG = 'catalog'
    # Compare mtimes of files in knowledge/ (for L2) or
    # stores/<slug>/ (for L3) against the CATALOG.md output.
