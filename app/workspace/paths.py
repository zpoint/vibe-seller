"""Which workspace paths a caller may reach — the confinement policy.

Split out of ``app/workspace/manager.py`` (which had grown past the
repo's 800-line limit) because it is a POLICY of its own, not a
mechanism of the manager: three rules, one place, each with the
reasoning that makes it auditable.

1. **Containment** (:meth:`WorkspacePaths.safe_path`) — a workspace
   path must RESOLVE inside the workspace root. The check is
   ``is_relative_to`` on the resolved pair, never a string-prefix
   comparison: a prefix check accepts any sibling that shares the
   root's name (``<root>-evil``), and ``resolve()`` is what makes a
   symlink planted under ``knowledge/`` point somewhere honest.

2. **The skills branch** (:meth:`WorkspacePaths.resolve_file`) — a
   skill directory may be a SYMLINK to a maintainer-installed skill
   outside the workspace; that is an explicitly supported feature
   (it is why this branch is lax at all). But the skills tree is also
   writable by task agents (bash can create a symlink there even
   where it cannot write files), so the lax branch is NOT
   unconditional: for a caller authenticated with an API token (an
   external agent — the credential class every hosted surface keys
   on), the resolved path must still sit under the workspace root,
   exactly like :meth:`safe_path`. A session/no-auth caller is the
   UI's own viewer; a token caller reading through a planted symlink
   to anywhere on the host (the SQLite DB included) is an escape,
   not a feature.

3. **Read-only L1 knowledge** (:meth:`WorkspacePaths.reject_l1_write`)
   — ``knowledge/project/`` is synced from the shared repo; writing
   it locally would be overwritten at the next sync, so it is
   refused.

The manager keeps its method names (``_safe_path``, ``resolve_file``,
``_reject_l1_write``) as thin delegations, so every existing import
site — ``routers/workspace.py`` passes ``workspace_manager.resolve_file``
as a callable, ``catalog_validation`` reads through it — keeps
working unchanged.
"""

from pathlib import Path

from app.workspace.actor import current_actor


class WorkspacePaths:
    """The confinement policy for one workspace root."""

    def __init__(self, root: Path):
        self.root = root

    def safe_path(self, rel_path: str) -> Path:
        """Resolve *rel_path* and require it to sit inside the root.

        Containment is ``is_relative_to`` on the RESOLVED pair — the
        pattern ``routers/tasks_files.py`` uses — never a string-prefix
        comparison: a prefix check accepts any sibling that shares the
        root's name (``<root>-evil``), and ``resolve()`` is what makes
        a symlink planted under knowledge/ point somewhere honest.
        """
        resolved = (self.root / rel_path).resolve()
        if not self.under_root(resolved):
            raise ValueError(f'Path traversal not allowed: {rel_path}')
        return resolved

    def resolve_file(self, rel_path: str) -> Path:
        """The absolute path of an existing workspace file.

        Skill paths (``.claude/skills/<slug>``) follow symlinks so
        maintainer-installed external skills (a hand-placed ``ln -s``)
        stay viewable in the web UI — an explicitly supported feature,
        and the reason this branch is lax at all. But the skills tree
        is also writable by task agents (bash can create a symlink
        there even where it cannot write files), so the lax branch is
        NOT unconditional: for a caller authenticated with an API
        token (an external agent — the credential class every hosted
        surface keys on), the resolved path must still sit under the
        workspace root, exactly like :meth:`safe_path`. A
        session/no-auth caller is the UI's own viewer; a token caller
        reading through a planted symlink to anywhere on the host
        (the SQLite DB included) is an escape, not a feature.
        """
        p = Path(rel_path)
        if p.parts[:2] == ('.claude', 'skills'):
            if p.is_absolute() or any(part == '..' for part in p.parts):
                raise ValueError(f'Path traversal not allowed: {rel_path}')
            file_path = self.root / p
            if self.is_api_token_caller() and not self.under_root(file_path):
                raise ValueError(f'Path traversal not allowed: {rel_path}')
        else:
            file_path = self.safe_path(rel_path)
        if not file_path.is_file():
            raise FileNotFoundError(f'File not found: {rel_path}')
        return file_path

    def reject_l1_write(self, rel_path: str) -> None:
        """Raise if *rel_path* targets read-only L1 knowledge."""
        resolved = self.safe_path(rel_path)
        try:
            resolved.relative_to(self.root.resolve() / 'knowledge' / 'project')
        except ValueError:
            return  # Not under knowledge/project/ — allowed
        raise ValueError(
            f'Cannot modify read-only L1 path: {rel_path}. '
            'Write to knowledge/ (L2) or '
            'stores/<slug>/ (L3).'
        )

    def under_root(self, candidate: Path) -> bool:
        """Whether *candidate* (resolved) sits inside the workspace."""
        return candidate.resolve().is_relative_to(self.root.resolve())

    def is_api_token_caller(self) -> bool:
        """Whether an API-token caller's request is on the stack.

        The same signal ``_auto_commit`` attributes commits by
        (``app.workspace.actor``): set by ``get_current_user`` for
        HTTP callers, cleared by the task-pipeline entry points, and
        absent for internal (non-request) callers — so the lax skills
        branch keeps working for the UI and for background jobs while
        refusing the external-agent credential class.
        """
        actor = current_actor()
        return actor is not None and actor.via == 'api_token'
