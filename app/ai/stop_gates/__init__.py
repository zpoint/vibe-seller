"""Generic, deterministic gates that run at ``set_task_result``.

These are task-agnostic — every task's declared result is validated
the same way. The ad-tuning reviewer gates in ``bash_safety.py``
remain for the audit-specific structure checks; these gates target
problems that span every task (malformed markdown tables, prose in
the wrong language).

Each gate returns either ``None`` (pass / not applicable) or a
``GateDeny`` carrying a structured reason the agent will see when
``set_task_result`` raises 400. Per-task attempt counts live in
``_attempts`` so each gate stays soft: the agent gets one chance to
fix, then the second call is allowed through.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from app.ai.skill_gate_loader import HotGate, discover_skill_gates
from app.ai.skill_gate_utils import find_skill_md, parse_skill_gates
from app.config import DATA_DIR
from app.plugins import registered_gates

# Module-level attempt counter, keyed by (task_id, gate_name). Lost
# on server restart, which is fine — the agent session would also be
# torn down.
_attempts: dict[tuple[str, str], int] = {}


@dataclass(frozen=True)
class GateDeny:
    """Reason a gate denied the result."""

    gate: str
    reason: str


def record_attempt(task_id: str, gate: str) -> int:
    """Increment and return the attempt count for (task_id, gate)."""
    key = (task_id, gate)
    _attempts[key] = _attempts.get(key, 0) + 1
    return _attempts[key]


def reset_attempts(task_id: str) -> None:
    """Drop all attempt counters for a task.

    Called by ``app/routers/tasks.py``:
    - after ``set_task_result`` persists a result (terminal success)
    - after ``delete_task`` removes a task

    Both paths guarantee no further gates will fire for that task,
    so the in-memory counters are dead weight from then on. This
    keeps the dict bounded over a long-running server.
    """
    for key in [k for k in _attempts if k[0] == task_id]:
        _attempts.pop(key, None)


# Cap: after this many denials per gate per task, the gate becomes a
# pass-through (the agent gets a warning, but the task finishes). The
# user explicitly asked for "let agent fix once but not mandatory" —
# 1 is the smallest value that still gives the agent feedback.
SOFT_GATE_MAX_DENIALS = 1


# ── Durable per-task skill bindings ──────────────────────────────────
#
# Skill loads are tracked per SESSION (claude_backend hooks), but the
# gate contract is per TASK: a retry-resume session that goes straight
# to editing the report without re-Reading SKILL.md must still face
# the gates the original session bound. (The hole this closes: a
# surgical fix session submitted with ZERO skill gates because nothing
# in that session had Read the skill file — gate coverage depended on
# a runtime accident.) Bindings persist as one newline-separated file
# per task under ``data/gate_bindings/`` — server-owned space the
# agent never touches (unlike the task dir, which the workspace-
# hygiene step tells the agent to clean before the final submit).

GATE_BINDINGS_DIR = DATA_DIR / 'gate_bindings'
_TASK_ID_SAFE_RE = re.compile(r'[^A-Za-z0-9_-]')


def _bindings_path(task_id: str):
    safe = _TASK_ID_SAFE_RE.sub('_', task_id)
    return GATE_BINDINGS_DIR / safe


def record_skill_load(task_id: str, skill: str) -> None:
    """Durably bind ``skill`` to ``task_id`` (idempotent, best-effort).

    Called from the PreToolUse hook when a session loads a skill; IO
    errors are swallowed — a failed write degrades to the old
    session-only behaviour, never blocks the agent.
    """
    if not task_id or not skill:
        return
    try:
        path = _bindings_path(task_id)
        existing = recorded_skills(task_id)
        if skill in existing:
            return
        GATE_BINDINGS_DIR.mkdir(parents=True, exist_ok=True)
        with path.open('a', encoding='utf-8') as f:
            f.write(skill + '\n')
    except OSError:  # pragma: no cover — best-effort persistence
        pass


def recorded_skills(task_id: str) -> frozenset[str]:
    """All skills ever bound to ``task_id``, across sessions/restarts."""
    try:
        text = _bindings_path(task_id).read_text(encoding='utf-8')
    except OSError:
        return frozenset()
    return frozenset(s.strip() for s in text.splitlines() if s.strip())


def clear_skill_bindings(task_id: str) -> None:
    """Remove the binding file (task deletion)."""
    try:
        _bindings_path(task_id).unlink(missing_ok=True)
    except OSError:  # pragma: no cover
        pass


# ── Skill-declared gate registry ────────────────────────────────────
#
# Generic gates (markdown_format, result_language) run for EVERY task.
# Domain gates run only when a skill the session loaded DECLARES them
# in its SKILL.md frontmatter (``gates: [ad_completeness_review]``).
# This keeps ``set_task_result`` free of task-type special cases: a
# listing skill, an email skill, etc. each bring their own reviewers,
# and a task that loaded no gate-declaring skill gets only the
# generic checks. Names below are the public contract skills use.


def get_registered_gates() -> dict[str, object]:
    """Name → gate module for every skill-declarable gate.

    Gate modules MUST expose exactly
    ``check(result_text, task_id=None, rules=None) -> GateDeny | None``.
    ``set_task_result`` always calls it positionally with all three args
    (``gate.check(final_result, task_id, rules)``); a gate that ignores
    ``task_id``/``rules`` keeps them as defaulted params rather than
    dropping them, so the positional call can never raise ``TypeError``.

    The set is populated by plugins through the
    :mod:`app.plugins` registry (OSS gates via the builtin plugin;
    any customer gates via their own externally-installed plugin wheels) — core no longer
    hardcodes the gate list, so excising a customer is "don't install
    its wheel", not "edit this dict".
    """
    return registered_gates()


def resolve_skill_gates(
    loaded_skills,
    workspace_dir,
) -> list:
    """Return (name, module) for every gate declared by a loaded skill.

    Reads each loaded skill's SKILL.md frontmatter ``gates:`` list
    (same inline-list format as ``requires:``) and maps the names
    through :func:`get_registered_gates`. Unknown names are ignored
    (a skill must not be able to break submits by typo). Order is
    deterministic (sorted by gate name) and duplicates collapse.
    """

    registry = get_registered_gates()
    # Scan the skill tree ONCE per resolve (not once per declared gate) —
    # gate name -> file for every skill-bundled gate.
    skill_gate_files = discover_skill_gates()
    resolved: dict[str, object] = {}
    for skill in sorted(loaded_skills):
        skill_md = find_skill_md(workspace_dir, skill)
        if skill_md is None:
            continue
        for gate_name in parse_skill_gates(skill_md):
            # Prefer a skill-bundled gate (hot-reloaded from its file);
            # fall back to a core/plugin gate in the registry.
            path = skill_gate_files.get(gate_name)
            module = (
                HotGate(gate_name, path) if path else registry.get(gate_name)
            )
            if module is not None:
                resolved[gate_name] = module
    return sorted(resolved.items())
