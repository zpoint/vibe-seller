"""Load skill-bundled gate scripts, with mtime-gated hot-reload.

A skill declares its exit gates in SKILL.md frontmatter
(``gates: [name]``) and ships each as ``<skill>/gates/<name>.py``
exposing ``check(result_text, task_id=None, rules=None) -> GateDeny |
None`` — the same contract as the core gates in ``app.ai.stop_gates``.

Unlike the core gates (imported once by ``builtin_plugin`` at boot),
skill-bundled gates are loaded from the file at resolve time and
**hot-reloaded**: each gate is cached by its file mtime and re-executed
only when the file changed. So a gate update delivered by ``skills_sync``
(which rewrites the runtime skills dir) takes effect on the next
``set_task_result`` **without a server restart** — matching the
hot-update cadence of the skill markdown.

Why re-exec, not ``importlib.reload``: ``reload`` fails on a
path-loaded module ("spec not found" — the synthetic module name has no
finder). Re-running ``spec.loader.exec_module`` on a fresh
``spec_from_file_location`` reliably picks up the new file.

Source of truth: gates load from the sync-managed runtime skills dir
(``~/.vibe-seller/.claude/skills/<skill>/gates/``) — the same trusted,
repo-derived tree ``skills_sync`` writes. Gate-author constraints:
- keep gates **stateless** — module-level state resets on re-exec; put
  durable counters in core (``stop_gates`` owns ``_attempts``);
- do **not** define classes used in cross-module ``isinstance`` — a
  re-exec makes new class objects; import shared types (``GateDeny``)
  from ``app.ai.stop_gates``.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import types

# Import the path constant from the LEAF app.config (not
# app.workspace.manager, which pulls the DB/models/stop_gates and would
# make this module non-importable from stop_gates). Keeping this a leaf
# — stdlib + app.config only, like skill_gate_utils — is what lets
# stop_gates import it at module top with no cycle.
from app.config import VIBE_SELLER_DIR

# Runtime skills tree that skills_sync keeps current. All skills live
# here (not just the ones a given task loaded), so a gate declared by
# one skill (e.g. noon-ads) can resolve to its canonical file shipped by
# another (e.g. amazon-ads/gates/ad_completeness_review.py).
_RUNTIME_SKILLS = VIBE_SELLER_DIR / '.claude' / 'skills'

# path -> (content_sha1, module). Cache for hot-reload; shared
# process-wide. Keyed on the file's content hash (not mtime): mtime
# granularity is coarse on some filesystems, and a same-size edit within
# one tick would be missed. Reading a small gate file per resolve is
# negligible and detects any change reliably.
_module_cache: dict[str, tuple[str, object]] = {}


class HotGate:
    """Registry-compatible gate proxy that hot-reloads its module file.

    Exposes ``check(result_text, task_id=None, rules=None)`` — the exact
    signature ``set_task_result`` calls positionally — and re-execs the
    backing file whenever its mtime changes.
    """

    def __init__(self, name: str, path: Path):
        self._name = name
        self._path = path

    def _module(self):
        p = str(self._path)
        try:
            data = self._path.read_bytes()
        except OSError:
            return None
        digest = hashlib.sha1(data).hexdigest()
        cached = _module_cache.get(p)
        if cached is None or cached[0] != digest:
            # compile+exec the EXACT bytes we hashed — do NOT go through
            # importlib's SourceFileLoader, whose .pyc cache is keyed on
            # mtime+size and would return stale bytecode for a same-size
            # edit within one mtime tick (the hot-reload bug).
            mod = types.ModuleType(f'skillgate_{self._name}')
            mod.__file__ = p
            exec(compile(data, p, 'exec'), mod.__dict__)  # noqa: S102
            _module_cache[p] = (digest, mod)
        return _module_cache[p][1]

    def check(self, result_text, task_id=None, rules=None):
        mod = self._module()
        if mod is None or not hasattr(mod, 'check'):
            return None
        return mod.check(result_text, task_id, rules)


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
    """Return a :class:`HotGate` for ``gate_name`` if a skill ships it.

    Returns None if no skill in the tree provides ``gates/<name>.py`` —
    the caller then falls back to the plugin registry (core gates).
    """
    path = discover_skill_gates(skills_root).get(gate_name)
    if path is None:
        return None
    return HotGate(gate_name, path)
