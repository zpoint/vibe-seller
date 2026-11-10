"""Prompt templates loaded from markdown files.

Each prompt is read once at import time. No runtime file I/O.

Prompt templates may contain ``<placeholder>`` markers that are
replaced at runtime via :func:`render_prompt`.  See
``app/prompts/TEMPLATES.md`` for the full list of placeholders
and where they are substituted.
"""

from pathlib import Path
import re

from app.workspace.catalog_validation import STUB_SUMMARY

_DIR = Path(__file__).parent


def _load(name: str) -> str:
    return (_DIR / name).read_text(encoding='utf-8').strip()


def render_prompt(
    template: str,
    *,
    store_slug: str | None = None,
) -> str:
    """Replace template placeholders in a prompt string.

    Supported placeholders:

    ``<slug>``
        Replaced with *store_slug* when provided.  Must be
        called for every store-scoped task so the agent sees
        the real path (e.g. ``stores/acme-test/CATALOG.md``).

    Args:
        template: Prompt text (usually one of the module-level
            ``*_PROMPT`` constants).
        store_slug: Store directory slug (e.g. ``acme-test``).

    Returns:
        The prompt with all known placeholders resolved.
    """
    if store_slug:
        template = template.replace('<slug>', store_slug)
    return template


DESIGN_SYSTEM_PROMPT: str = _load('design_system.md')

# Auto mode: same prompt minus plan-mode-only sections
# (Phase 5 plan output format + ExitPlanMode rules).
DESIGN_SYSTEM_PROMPT_AUTO: str = re.sub(
    r'<!-- PLAN_MODE_ONLY_START -->.*?<!-- PLAN_MODE_ONLY_END -->',
    '',
    DESIGN_SYSTEM_PROMPT,
    flags=re.DOTALL,
).strip()
SCHEDULED_PRETASK_PROMPT: str = _load('scheduled_pretask.md')
EXTRACTION_PROMPT: str = _load('event_extraction.md')
WAITING_INSTRUCTION_PROMPT: str = _load('waiting_instruction.md')
DUAL_BROWSER_PROMPT: str = _load('dual_browser.md')
TICKTICK_TOOLS_PROMPT: str = _load('ticktick_tools.md')
WORKSPACE_ASSISTANT_PROMPT: str = _load('workspace_assistant.md')
VISION_SETUP_BREADCRUMB: str = _load('vision_setup_breadcrumb.md')
CATALOG_RESTRICTION_PROMPT_L2: str = (
    'You are regenerating ONLY the L2 global catalog '
    '(knowledge/CATALOG.md). Do not launch subagents, create '
    'skills, or write any file other than the catalog, and skip '
    '"Keeping knowledge correct" below: this run edits nothing '
    'else.'
)
CATALOG_RESTRICTION_PROMPT_L3: str = (
    'You are regenerating ONLY the L3 store catalog '
    '(stores/<slug>/CATALOG.md). Do not modify '
    'knowledge/CATALOG.md, launch subagents, create skills, or '
    'write any file other than the catalog, and skip "Keeping '
    'knowledge correct" below: this run edits nothing else.'
)


# Replaces the catalog restriction, once per scope, after an upgrade
# that changes the harness (app/knowledge_contract.py) — one rule for
# what this run may write, instead of a "write nothing else" followed
# by an exception. The server refuses a write in this run that adds or
# rewords a line, or touches a file outside the tier.
def _catalog_cleanup_prompt(catalog: str, files: str, also: str) -> str:
    return (
        f'You are regenerating the catalog {catalog}, after a one-time '
        f'cleanup of the notes it lists. Do not launch subagents{also} '
        'or create skills; skip "Keeping knowledge correct" below.\n\n'
        'Cleanup first: this app was upgraded. Notes written before may '
        'describe how the platform itself checked or reviewed work — a '
        'review pass or reviewer subagent, what a gate refused, when an '
        'approval froze, freshness or completeness checks, which file a '
        'check reads. The skills now describe that behaviour, so these '
        'descriptions are outdated and mislead later tasks. In this '
        f"tier's own files — {files} — delete exactly those passages "
        'and nothing else. Keep store facts, business rules, standards '
        'the user stated (`key: value` lines), page paths, and UI and '
        'data gotchas for the marketplaces.\n\n'
        'Write each changed file with vibe_seller_write_workspace_file, '
        'removing lines only; the server refuses a write that adds or '
        'changes a line, or touches a file outside this tier, and after '
        'the run puts back any file changed some other way. Write no '
        'other file. Then build the catalog, and list in your result '
        'each file you changed and what you removed.'
    )


CATALOG_CLEANUP_PROMPT_L2: str = _catalog_cleanup_prompt(
    'knowledge/CATALOG.md (L2, global)',
    'files under knowledge/, except knowledge/project/ (read-only)',
    '',
)
CATALOG_CLEANUP_PROMPT_L3: str = _catalog_cleanup_prompt(
    'stores/<slug>/CATALOG.md (L3, this store)',
    'files under stores/<slug>/; shared knowledge/ files are cleaned '
    'by the global run, not this one',
    ', modify anything under knowledge/',
)

# The result format both catalog sync prompts point to. One source so
# the L2/L3 prompts and the server-side gate (catalog_validation)
# cannot drift apart: the gate enforces exactly this table.
CATALOG_FORMAT_SPEC: str = (
    'Result format: a markdown table with the header row '
    '`| File | Relevance | Summary |` and exactly 3 cells per '
    'row. Paths are workspace-relative. Convention: copied rows '
    "first (L1/L2), then the tier's own files.\n"
    '- Relevance: comma-separated platform tags (e.g. `amazon`, '
    '`noon`) when the file is clearly about those platforms, '
    'else empty. Task prompts treat every row tagged with their '
    'platform as a mandatory read, so an invented or mistyped '
    'tag changes what later tasks must read.\n'
    "- Summary: one line, <=80 chars, naming the file's topic. "
    'For a file too thin to summarize, write exactly: '
    f'`{STUB_SUMMARY}`.\n'
    'The server validates this table on write (accumulation, '
    'completeness, isolation, format) and rejects it naming the '
    'exact rows to fix — re-read those files and write it again.'
)

# Task descriptions injected by fanout.py for each catalog phase.
CATALOG_DESC_L2: str = (
    'Regenerate the L2 global knowledge catalog '
    '(knowledge/CATALOG.md): one row per shared knowledge file, '
    'so tasks can navigate knowledge/ without searching.\n'
    '\n'
    'Inputs:\n'
    '1. knowledge/project/CATALOG.md (L1, repo-maintained, '
    "read-only). Copy each row's Relevance and Summary verbatim "
    'and prefix its path with `knowledge/project/` — L1 is what '
    'every install ships, so re-deriving it only risks drift.\n'
    "2. The L2 files: `Bash(\"find -L knowledge -name '*.md' "
    "! -path 'knowledge/project/*' ! -name CATALOG.md\")` "
    '(`-L` is required: knowledge/ is a symlink in the task '
    'workspace, and Glob/Grep do not follow symlinks).\n'
    '\n'
    'Read every found file and give each a row, however thin its '
    'content looks — a dropped row hides the file from every '
    'reader. Write the result with vibe_seller_write_workspace_file '
    'to knowledge/CATALOG.md.\n'
    '\n'
    '\n' + CATALOG_FORMAT_SPEC
)

CATALOG_DESC_L3: str = (
    'Regenerate the L3 store catalog (stores/<slug>/CATALOG.md): '
    "this store's complete reading list — every L2 row plus the "
    "store's own files.\n"
    '\n'
    'Inputs:\n'
    '1. knowledge/CATALOG.md (L2, just rebuilt by the global '
    'phase). Copy every row verbatim — path, Relevance, Summary. '
    'Do not re-derive or filter L2 rows, even ones that look '
    'odd: L2 is the shared contract L3 must carry whole.\n'
    '2. The store\'s files: `Bash("find -L stores/<slug> -type f '
    "! -name CATALOG.md ! -name '.*'\")` (same `-L` symlink "
    'rule).\n'
    '\n'
    'Classify found files by FILENAME only, never by content:\n'
    '- INCLUDE: lowercase topic names (notes.md, browser-tips.md, '
    'logistics.md), the canonical STORE.md, nested platform/country '
    'paths (amazon/<country>/...). When a file matches no exclude '
    'rule, include it — an extra row costs little, a missing row '
    'hides the file.\n'
    '- EXCLUDE: task outputs and system files, which are not '
    'knowledge — ALL_CAPS_WITH_UNDERSCORE names (*_PLAN_*, '
    '*_REPORT_*, WIDGET_PLAN.md), dated suffixes '
    '(*_YYYY-MM-DD.md, *-YYYYMMDD.md), non-markdown system files '
    '(metadata.json, *.json, *.lock, *.cache).\n'
    '\n'
    'Read every included file and give each a row. Never include '
    "another store's files. Write the result with "
    'vibe_seller_write_workspace_file to '
    'stores/<slug>/CATALOG.md.\n'
    '\n'
    '\n' + CATALOG_FORMAT_SPEC
)
