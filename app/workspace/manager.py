"""WorkspaceManager: manages the ~/.vibe-seller/ directory.

Git-managed, contains ``.claude/skills/`` (auto-discovered via
``--add-dir``), ``knowledge/`` (shared platform knowledge), and
``stores/<slug>/`` (per-store profiles and accumulated knowledge).
"""

import asyncio
import logging
import os
from pathlib import Path
import shutil
import time

import git as gitlib

from app.config import VIBE_SELLER_DIR
from app.platform import agent_venv_python
from app.workspace import task_links, venv_bootstrap
from app.workspace.skills_manager import SkillsMixin
from app.workspace.store_data_migrate import migrate_store_data
from app.workspace.store_seed import write_catalog_stub
from app.workspace.structured_stores import collect_store_entries
from app.workspace.templates import WORKSPACE_CLAUDE_MD

logger = logging.getLogger(__name__)


class WorkspaceManager(SkillsMixin):
    """File operations + git auto-commit for ~/.vibe-seller/."""

    def __init__(self, root: Path | None = None):
        self.root = root or VIBE_SELLER_DIR
        self._repo: gitlib.Repo | None = None
        # Serialise git subprocesses; concurrent tasks race on index.lock.
        self._git_lock = asyncio.Lock()
        # Serialise shared-venv creation (boot build vs agent ensure_init).
        self._venv_lock = asyncio.Lock()

    async def ensure_init(self, *, create_venv: bool = True):
        """Ensure workspace directory exists and is a git repo.

        ``create_venv=False`` skips the slow cold venv build so server
        boot never blocks ``/api/health`` — it's built in the background
        (``ensure_shared_venv``); agent runs use the default and await
        the venv before launching, so nothing runs without it.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / '.claude' / 'skills').mkdir(parents=True, exist_ok=True)
        (self.root / 'knowledge').mkdir(parents=True, exist_ok=True)
        (self.root / 'stores').mkdir(parents=True, exist_ok=True)
        # Per-store RUN DATA lives outside stores/ (never surfaces as
        # knowledge, still git-tracked). First boot after a layout bump
        # relocates old artifacts → store-data/<YYYY-MM>/; then O(1).
        migrate_store_data(self.root)

        # Generate workspace CLAUDE.md (write-once)
        claude_md = self.root / 'CLAUDE.md'
        if not claude_md.exists():
            claude_md.write_text(WORKSPACE_CLAUDE_MD)

        git_dir = self.root / '.git'
        if not git_dir.exists():
            await self._run_git('init')
            gitignore = self.root / '.gitignore'
            if not gitignore.exists():
                gitignore.write_text(
                    '*.pyc\n__pycache__/\n.DS_Store\n'
                    '.venv/\nnode_modules/\nconfig/\ntask_history/\n'
                    'data/\n*.db-journal\n*.db-wal\n*.db-shm\n'
                )
            await self._run_git('add', '-A')
            await self._run_git('commit', '-m', 'Initial workspace setup')

        # Ensure transient/generated paths are in .gitignore
        gitignore = self.root / '.gitignore'
        if gitignore.exists():
            content = gitignore.read_text()
            additions = []
            for entry in (
                'task_history/',
                'data/',
                '*.db-journal',
                'tasks/',
                'node_modules/',
            ):
                if entry not in content:
                    additions.append(entry)
            if additions:
                gitignore.write_text(
                    content.rstrip() + '\n' + '\n'.join(additions) + '\n'
                )

        # Ensure shared agent venv exists (slow on a cold first boot —
        # skipped at server startup, built in the background instead).
        if create_venv:
            await self._ensure_venv()

    async def ensure_shared_venv(self):
        """Build the shared agent venv (run as a boot background task so a
        cold ``uv venv`` doesn't block readiness; idempotent + lock-
        guarded, races safely with an agent run's ``ensure_init()``)."""
        await self._ensure_venv()

    async def _ensure_venv(self):
        """Create the shared agent venv at ~/.vibe-seller/.venv/ (uv venv
        + pip/uv bootstrap; re-bootstraps if tools are missing).
        Lock-guarded against concurrent ``uv venv`` invocations."""
        async with self._venv_lock:
            await self._ensure_venv_locked()

    async def _ensure_venv_locked(self):
        venv_dir = self.root / '.venv'
        if venv_dir.exists():
            if await venv_bootstrap.venv_tools_ok(venv_dir):
                return
            # Is python3 itself broken (not just missing tools)?
            if not await venv_bootstrap.python_runnable(venv_dir):
                logger.warning(
                    'Venv python3 broken, recreating: %s',
                    venv_dir,
                )
                broken = venv_dir.with_name(f'.venv.broken.{time.time_ns()}')
                try:
                    venv_dir.rename(broken)
                except (FileNotFoundError, FileExistsError):
                    pass  # concurrent caller handled it
                try:
                    shutil.rmtree(broken)
                except Exception:
                    logger.warning(
                        'Failed to remove broken venv: %s',
                        broken,
                        exc_info=True,
                    )
                # Fall through to create fresh venv
            else:
                logger.info('Venv missing tools, bootstrapping pip/uv')
                await venv_bootstrap.bootstrap_venv_tools(venv_dir)
                return
        logger.info('Creating shared agent venv at %s', venv_dir)
        proc = await asyncio.create_subprocess_exec(
            'uv',
            'venv',
            str(venv_dir),
            '--python',
            agent_venv_python(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr_bytes = await proc.communicate()
        if proc.returncode != 0:
            # Race: another task may have created it concurrently
            if venv_dir.exists():
                logger.info('Venv appeared concurrently, reusing')
                if not await venv_bootstrap.venv_tools_ok(venv_dir):
                    await venv_bootstrap.bootstrap_venv_tools(venv_dir)
                return
            stderr = stderr_bytes.decode() if stderr_bytes else ''
            raise RuntimeError(
                f'Failed to create agent venv at {venv_dir}: {stderr}'
            )

        await venv_bootstrap.bootstrap_venv_tools(venv_dir)

    async def list_tree(self) -> list[dict]:
        """Return a flat list of all files in the workspace."""
        await self.ensure_init()
        items = []
        for path in sorted(self.root.rglob('*')):
            if (
                '.git' in path.parts
                or '.venv' in path.parts
                or 'tasks' in path.parts
            ):
                continue
            rel = path.relative_to(self.root)
            items.append({
                'path': str(rel),
                'is_dir': path.is_dir(),
                'size': path.stat().st_size if path.is_file() else 0,
            })
        return items

    async def read_file(self, rel_path: str) -> str:
        """Read a file from the workspace.

        Skill reads (``.claude/skills/<slug>``) use a lax check that
        follows symlinks so maintainer-installed external skills are
        viewable in the UI. Every other tree keeps the strict
        ``_safe_path`` guard.
        """
        file_path = self.resolve_file(rel_path)
        try:
            return file_path.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            # Platform exports (csv/tsv) may be GB18030.
            try:
                return file_path.read_text(encoding='gb18030')
            except UnicodeDecodeError:
                raise ValueError(
                    f'Binary file (use /api/workspace/file/raw): {rel_path}'
                )

    def resolve_file(self, rel_path: str) -> Path:
        """Validated absolute path for a workspace file.

        Skill paths (``.claude/skills/<slug>``) use a lax check that
        follows symlinks so maintainer-installed external skills are
        viewable in the UI; every other tree keeps ``_safe_path``.
        """
        p = Path(rel_path)
        if p.parts[:2] == ('.claude', 'skills'):
            if p.is_absolute() or any(part == '..' for part in p.parts):
                raise ValueError(f'Path traversal not allowed: {rel_path}')
            file_path = self.root / p
        else:
            file_path = self._safe_path(rel_path)
        if not file_path.is_file():
            raise FileNotFoundError(f'File not found: {rel_path}')
        return file_path

    def _reject_l1_write(self, rel_path: str) -> None:
        """Raise if rel_path targets read-only L1 knowledge."""
        resolved = self._safe_path(rel_path)
        try:
            resolved.relative_to(self.root.resolve() / 'knowledge' / 'project')
        except ValueError:
            return  # Not under knowledge/project/ — allowed
        raise ValueError(
            f'Cannot modify read-only L1 path: {rel_path}. '
            'Write to knowledge/ (L2) or '
            'stores/<slug>/ (L3).'
        )

    async def write_file(self, rel_path: str, content: str) -> None:
        """Write a file and auto-commit."""
        await self.ensure_init()
        file_path = self._safe_path(rel_path)
        self._reject_l1_write(rel_path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding='utf-8')
        await self._auto_commit(f'Update {rel_path}')

    async def delete_file(self, rel_path: str) -> None:
        """Delete a file and auto-commit."""
        file_path = self._safe_path(rel_path)
        self._reject_l1_write(rel_path)
        if file_path.is_file():
            file_path.unlink()
        elif file_path.is_dir():
            shutil.rmtree(file_path)
        else:
            raise FileNotFoundError(f'Not found: {rel_path}')
        await self._auto_commit(f'Delete {rel_path}')

    def _parse_yaml_frontmatter(self, text: str) -> dict:
        """Parse YAML frontmatter from a markdown file (--- delimited)."""
        if not text.startswith('---'):
            return {}
        end = text.find('---', 3)
        if end == -1:
            return {}
        frontmatter = text[3:end].strip()
        result = {}
        for line in frontmatter.split('\n'):
            if ':' in line:
                key, _, value = line.partition(':')
                result[key.strip()] = value.strip()
        return result

    async def get_structured(self) -> dict:
        """Return workspace organized by section for the UI."""
        await self.ensure_init()

        knowledge_dir = self.root / 'knowledge'
        skills_dir = self.root / '.claude' / 'skills'

        # Collect skills (built-in from _builtin/ + user top-level)
        skills = []
        if skills_dir.is_dir():
            lockfile = self._read_lockfile()

            # Helper to collect one skill directory
            def _collect_skill(skill_path, source, origin_url=''):
                files = []
                description = ''
                for f in sorted(skill_path.rglob('*')):
                    if f.is_file() and '.git' not in f.parts:
                        if '.venv' in f.parts:
                            continue
                        if '__pycache__' in f.parts:
                            continue
                        if f.suffix == '.pyc':
                            continue
                        rel = f.relative_to(self.root)
                        files.append({
                            'path': str(rel),
                            'name': f.name,
                            'size': f.stat().st_size,
                        })
                        if f.name == 'SKILL.md':
                            try:
                                fm = self._parse_yaml_frontmatter(
                                    f.read_text(encoding='utf-8')
                                )
                                description = fm.get(
                                    'description',
                                    '',
                                )
                            except Exception:
                                pass
                return {
                    'slug': skill_path.name,
                    'path': str(skill_path.relative_to(self.root)),
                    'files': files,
                    'file_count': len(files),
                    'description': description,
                    'source': source,
                    'origin_url': origin_url,
                }

            # All skills are direct children of skills/
            synced = self._read_synced_skills(skills_dir)
            for skill_path in sorted(skills_dir.iterdir()):
                if not skill_path.is_dir():
                    continue
                if skill_path.name.startswith('.'):
                    continue
                lock_entry = lockfile['skills'].get(skill_path.name, {})
                if skill_path.name in synced:
                    source = 'builtin'
                    origin_url = ''
                elif lock_entry.get('source') == 'url':
                    source = 'imported'
                    origin_url = lock_entry.get('origin_url', '')
                else:
                    source = 'custom'
                    origin_url = ''
                skills.append(_collect_skill(skill_path, source, origin_url))

        # One entry per store: stores/ (knowledge) + store-data/ (run
        # data) joined by slug in the backend — see structured_stores.
        store_profiles = collect_store_entries(self.root)

        # Collect knowledge files, split into project (synced from repo) and local
        project_knowledge_dir = knowledge_dir / 'project'
        project_knowledge = []
        local_knowledge = []
        if knowledge_dir.is_dir():
            for f in sorted(knowledge_dir.rglob('*')):
                if not f.is_file():
                    continue
                if '.git' in f.parts or '__pycache__' in f.parts:
                    continue
                rel = f.relative_to(self.root)
                entry = {
                    'path': str(rel),
                    'name': f.name,
                    'size': f.stat().st_size,
                }
                if project_knowledge_dir.is_dir() and f.is_relative_to(
                    project_knowledge_dir
                ):
                    project_knowledge.append(entry)
                else:
                    local_knowledge.append(entry)

        # Collect root-level files (gitignore, etc.)
        root_files = []
        for f in sorted(self.root.iterdir()):
            if f.is_file() and f.name != '.DS_Store':
                root_files.append({
                    'path': f.name,
                    'name': f.name,
                    'size': f.stat().st_size,
                })

        return {
            'skills': skills,
            'store_profiles': store_profiles,
            'project_knowledge': project_knowledge,
            'local_knowledge': local_knowledge,
            'root_files': root_files,
        }

    async def create_store_profile(
        self,
        slug: str,
        name: str,
        platform: str = '',
        country: str = '',
        backend: str = 'chrome',
    ) -> str:
        """Scaffold store profile directory."""
        store_dir = self.root / 'stores' / slug
        store_dir.mkdir(parents=True, exist_ok=True)

        store_md = store_dir / 'STORE.md'
        if not store_md.exists():
            store_md.write_text(
                f"""---
browser: {backend}
---

# Store: {name}

""",
                encoding='utf-8',
            )

        notes_md = store_dir / 'notes.md'
        if not notes_md.exists():
            notes_md.write_text(f'# Notes for {name}\n\n', encoding='utf-8')

        logistics_md = store_dir / 'logistics.md'
        if not logistics_md.exists():
            logistics_md.write_text(
                f'# Logistics for {name}\n\n', encoding='utf-8'
            )

        write_catalog_stub(store_dir, slug, name)

        if backend == 'ziniao':
            routing_md = store_dir / 'browser-routing.md'
            if not routing_md.exists():
                routing_md.write_text(
                    '# Browser Routing Rules\n\n'
                    '<!-- Custom routing rules for this store. '
                    'These override the default dual-browser '
                    'routing. -->\n\n'
                    '## Examples\n\n'
                    '<!-- Uncomment and edit as needed:\n'
                    '- logistics.example.com → Chrome aux\n'
                    '- sellercentral.amazon.* → Ziniao\n'
                    '-->\n',
                    encoding='utf-8',
                )

        await self._auto_commit(f'Create store profile: {name}')
        return str(store_dir.relative_to(self.root))

    def _get_repo(self) -> gitlib.Repo:
        """Get or create GitPython Repo for the workspace."""
        if self._repo is None or not (self.root / '.git').exists():
            self._repo = gitlib.Repo(str(self.root))
        return self._repo

    async def file_history(
        self, rel_path: str, max_count: int = 50
    ) -> list[dict]:
        """Return git log for a file (commit sha, message, date, author)."""
        self._safe_path(rel_path)  # validate path
        repo = self._get_repo()

        def _log():
            commits = list(
                repo.iter_commits(paths=rel_path, max_count=max_count)
            )
            return [
                {
                    'sha': c.hexsha[:12],
                    'message': c.message.strip(),
                    'date': c.committed_datetime.isoformat(),
                    'author': str(c.author),
                }
                for c in commits
            ]

        return await asyncio.get_event_loop().run_in_executor(None, _log)

    async def file_at_commit(self, rel_path: str, commit_sha: str) -> str:
        """Return file content at a specific commit."""
        self._safe_path(rel_path)  # validate path
        repo = self._get_repo()

        def _show():
            commit = repo.commit(commit_sha)
            # Use forward slashes for git tree paths
            blob = commit.tree / rel_path.replace('\\', '/')
            return blob.data_stream.read().decode('utf-8')

        return await asyncio.get_event_loop().run_in_executor(None, _show)

    async def reset_file_to_commit(
        self, rel_path: str, commit_sha: str
    ) -> None:
        """Reset a file to a specific commit version and auto-commit."""
        self._reject_l1_write(rel_path)
        content = await self.file_at_commit(rel_path, commit_sha)
        file_path = self._safe_path(rel_path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding='utf-8')
        await self._auto_commit(f'Revert {rel_path} to {commit_sha[:12]}')

    def _safe_path(self, rel_path: str) -> Path:
        """Resolve path and ensure it's within workspace."""
        resolved = (self.root / rel_path).resolve()
        if not str(resolved).startswith(str(self.root.resolve())):
            raise ValueError(f'Path traversal not allowed: {rel_path}')
        return resolved

    async def _auto_commit(self, message: str):
        """Stage all changes and commit."""
        try:
            await self._run_git('add', '-A')
            # Check if there are changes to commit
            result = await self._run_git(
                'diff', '--cached', '--quiet', check=False
            )
            if result.returncode != 0:  # There are staged changes
                await self._run_git('commit', '-m', message)
        except Exception as e:
            logger.warning(f'Auto-commit failed: {e}')

    async def _run_git(
        self, *args, check: bool = True
    ) -> asyncio.subprocess.Process:
        """Run git in self.root; serialised on ``self._git_lock``."""
        # GIT_*_NAME/EMAIL via env (setdefault; a real identity wins)
        # keeps initial-commit working on hosts with no global
        # `git config user.email/name` (#181), without touching .git/config.
        git_env = dict(os.environ)
        git_env.setdefault('GIT_AUTHOR_NAME', 'Vibe Seller')
        git_env.setdefault('GIT_AUTHOR_EMAIL', 'agent@vibe-seller.local')
        git_env.setdefault('GIT_COMMITTER_NAME', 'Vibe Seller')
        git_env.setdefault('GIT_COMMITTER_EMAIL', 'agent@vibe-seller.local')
        async with self._git_lock:
            proc = await asyncio.create_subprocess_exec(
                'git',
                *args,
                cwd=str(self.root),
                env=git_env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.wait()
            if check and proc.returncode != 0:
                err = (await proc.stderr.read()).decode() if proc.stderr else ''
                raise RuntimeError(f'git {" ".join(args)} failed: {err}')
            return proc

    async def prepare_task_workspace(
        self,
        task_id: str,
        *,
        clean: bool = False,
    ) -> Path:
        """Create a per-task working directory linked to shared dirs.

        Returns the absolute path to ``tasks/{task_id}/``. Shared
        resources (knowledge, stores, store-data, CLAUDE.md) are linked
        to the shared root — POSIX symlinks, or Windows directory
        junctions plus a CLAUDE.md copy, since ``os.symlink`` needs a
        privilege stock Windows lacks (see ``app/workspace/task_links``).
        Task-specific files stay isolated.

        The ``browser-use`` skill is copied for every task, including
        no-store (orchestrator) tasks — they now have the store-less
        ``web`` browser for neutral public web work, so they need the
        skill's CLI reference too.
        """
        await self.ensure_init()
        task_dir = self.root / 'tasks' / task_id
        if clean and task_dir.exists():
            task_links.remove_task_workspace(task_dir)
        task_dir.mkdir(parents=True, exist_ok=True)

        links: dict[str, Path] = {
            'knowledge': self.root / 'knowledge',
            'stores': self.root / 'stores',
            'store-data': self.root / 'store-data',
            'CLAUDE.md': self.root / 'CLAUDE.md',
        }
        for name, target in links.items():
            link_path = task_dir / name
            task_links._clear_workspace_link(link_path)
            if target.exists():
                task_links._link_shared_into_task(link_path, target)

        # .claude is copied (not symlinked) because Claude Code's
        # Glob doesn't follow symlinks when traversing ** patterns.
        # Exclude __pycache__ and stale .venv dirs (skills use the
        # shared workspace venv at ~/.vibe-seller/.venv/ instead).
        def _ignore(directory: str, contents: list[str]) -> set[str]:
            return {
                name for name in contents if name in ('__pycache__', '.venv')
            }

        claude_src = self.root / '.claude'
        claude_dst = task_dir / '.claude'
        task_links._clear_workspace_link(claude_dst)
        if claude_src.is_dir():
            shutil.copytree(
                claude_src,
                claude_dst,
                ignore=_ignore,
            )

        return task_dir


# Singleton
workspace_manager = WorkspaceManager()
