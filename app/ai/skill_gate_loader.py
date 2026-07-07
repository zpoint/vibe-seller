"""Load skill-bundled gate scripts once per process (restart to update).

A skill declares its exit gates in SKILL.md frontmatter
(``gates: [name]``) and ships each as ``<skill>/gates/<name>.py``
exposing ``check(result_text, task_id=None, rules=None) -> GateDeny |
None`` — the same contract as the core gates in ``app.ai.stop_gates``.

Gate modules load from the sync-managed runtime skills dir
(``~/.vibe-seller/.claude/skills/<skill>/gates/``) and are cached for the
lifetime of the process: :func:`preload_skill_gates` runs at server start
(after ``skills_sync`` has fetched the latest tree) and executes every gate
file once. A gate update delivered later by ``skills_sync`` therefore takes
effect only on the **next server restart** — never live.

This is deliberate, not a limitation. Gate code runs server-side and
``skills_sync`` can pull it from a remote GitHub source, so re-executing a
changed gate file into a running server would let anyone who can write to
that source inject live-executing code. Requiring a restart keeps every
code change behind an explicit, operator-controlled event. (Boot re-runs
``skills_sync`` before gates load, so a restart always picks up the latest
synced gate code.)

Gate-author constraint: import shared types (``GateDeny``) from
``app.ai.stop_gates`` — don't define classes used in cross-module
``isinstance``.
"""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path

# Import the path constant from the LEAF app.config (not
# app.workspace.manager, which pulls the DB/models/stop_gates and would
# make this module non-importable from stop_gates). Keeping this a leaf
# — stdlib + app.config only, like skill_gate_utils — is what lets
# stop_gates import it at module top with no cycle.
from app.config import VIBE_SELLER_DIR

logger = logging.getLogger(__name__)

# Runtime skills tree that skills_sync keeps current. All skills live
# here (not just the ones a given task loaded), so a gate declared by
# one skill (e.g. noon-ads) can resolve to its canonical file shipped by
# another (e.g. amazon-ads/gates/ad_completeness_review.py).
_RUNTIME_SKILLS = VIBE_SELLER_DIR / '.claude' / 'skills'

# path -> loaded module. Populated once (at preload / first use) and never
# refreshed on a file change — a fresh process (server restart) is what
# re-executes the file. No content hash, no hot-reload: see module docstring.
_module_cache: dict[str, object] = {}


def load_gate_from_path(gate_name: str, path: Path):
    """Load (once, cached) and return the gate module at ``path``.

    Cached by path for the process lifetime: a later edit to the file is
    ignored until the next server restart. Returns ``None`` if the file
    can't be read or executed (the caller then falls back to the plugin
    registry / skips the gate).
    """
    p = str(path)
    cached = _module_cache.get(p)
    if cached is not None:
        return cached
    try:
        spec = importlib.util.spec_from_file_location(
            f'skillgate_{gate_name}', p
        )
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except OSError:
        return None
    except Exception:
        # A malformed gate file must not take down set_task_result — skip
        # it (fail-open) and log loudly so the bad gate is fixed.
        logger.exception('Failed to load skill gate %s from %s', gate_name, p)
        return None
    _module_cache[p] = module
    return module


def discover_skill_gates(skills_root: Path | None = None) -> dict[str, Path]:
    """Map ``gate_name -> gates/<name>.py`` across every skill in the tree.

    Scans ``<skills_root>/*/gates/*.py`` (default: the runtime skills
    dir). The stem is the gate name (== the ``gates:`` declared name). A
    gate shared by several skills lives once, in its owning skill's dir,
    and resolves for any skill that declares the name.
    """
    root = skills_root or _RUNTIME_SKILLS
    found: dict[str, Path] = {}
    try:
        skill_dirs = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError:
        return found
    for skill_dir in skill_dirs:
        gates_dir = skill_dir / 'gates'
        if not gates_dir.is_dir():
            continue
        for gate_py in sorted(gates_dir.glob('*.py')):
            if gate_py.name == '__init__.py':
                continue
            # First writer wins so a stable owner keeps the canonical
            # file; deterministic because skill_dirs is sorted.
            found.setdefault(gate_py.stem, gate_py)
    return found


def load_skill_gate(gate_name: str, skills_root: Path | None = None):
    """Return the loaded gate module for ``gate_name`` if a skill ships it.

    Returns ``None`` if no skill in the tree provides ``gates/<name>.py`` —
    the caller then falls back to the plugin registry (core gates).
    """
    path = discover_skill_gates(skills_root).get(gate_name)
    if path is None:
        return None
    return load_gate_from_path(gate_name, path)


def preload_skill_gates(skills_root: Path | None = None) -> int:
    """Execute every skill-bundled gate file once and cache it.

    Called at server startup (after ``skills_sync.fetch``) so gate code is
    loaded from the known-good, just-synced tree at a single controlled
    moment — not lazily on first task, which would let a post-boot write to
    the runtime dir slip new code into a running server. Returns the count
    of gate modules successfully loaded.
    """
    loaded = 0
    for gate_name, path in discover_skill_gates(skills_root).items():
        if load_gate_from_path(gate_name, path) is not None:
            loaded += 1
    logger.info('Preloaded %d skill-bundled gate module(s)', loaded)
    return loaded
