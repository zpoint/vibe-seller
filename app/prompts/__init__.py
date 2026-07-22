"""Prompt templates loaded from markdown files.

Each prompt is read once at import time. No runtime file I/O.

Prompt templates may contain ``<placeholder>`` markers that are
replaced at runtime via :func:`render_prompt`.  See
``app/prompts/TEMPLATES.md`` for the full list of placeholders
and where they are substituted.
"""

from pathlib import Path
import re

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
REFLECTION_PROMPT: str = _load('reflection.md')
SCHEDULED_PRETASK_PROMPT: str = _load('scheduled_pretask.md')
SCHEDULED_WATERMARK_PROMPT: str = _load('scheduled_watermark.md')
EXTRACTION_PROMPT: str = _load('event_extraction.md')
WAITING_INSTRUCTION_PROMPT: str = _load('waiting_instruction.md')
DUAL_BROWSER_PROMPT: str = _load('dual_browser.md')
TICKTICK_TOOLS_PROMPT: str = _load('ticktick_tools.md')
WORKSPACE_ASSISTANT_PROMPT: str = _load('workspace_assistant.md')
VISION_SETUP_BREADCRUMB: str = _load('vision_setup_breadcrumb.md')
CATALOG_RESTRICTION_PROMPT_L2: str = (
    'You are regenerating ONLY the L2 global catalog '
    '(knowledge/CATALOG.md). Do NOT touch any store '
    'files or directories. Do not launch subagents. '
    'Do not create skills, scripts, or any files '
    'other than the catalog.'
)
CATALOG_RESTRICTION_PROMPT_L3: str = (
    'You are regenerating ONLY the L3 store catalog '
    '(stores/<slug>/CATALOG.md). Do NOT modify '
    'knowledge/CATALOG.md. Do not launch subagents. '
    'Do not create skills, scripts, or any files '
    'other than the catalog.'
)

# Task descriptions injected by fanout.py for each catalog phase.
CATALOG_DESC_L2: str = (
    'Regenerate the L2 global knowledge catalog.\n'
    '\n'
    '1. Read knowledge/project/CATALOG.md (L1, maintained '
    'by repo — do NOT modify it). L1 has 3 columns '
    '(File | Relevance | Summary) — KEEP all 3 columns '
    'and COPY each Relevance + Summary as-is (do NOT '
    're-derive or re-read L1 files). L1 paths are '
    'relative to project/ (e.g. common/amazon-sites.md) '
    '— prefix each with knowledge/project/ to make them '
    'absolute.\n'
    '\n'
    '2. List L2 files with '
    "`Bash(\"find -L knowledge -name '*.md' ! -path 'knowledge/project/*' "
    '! -name CATALOG.md")` — '
    'the `-L` flag is required because `knowledge/` is a symlink '
    'and without `-L`, find returns nothing. Glob/Grep also do '
    'not follow symlinks; use this Bash form. '
    'EVERY file the `find` returns MUST appear as a row in the '
    'catalog. Do NOT skip files because the content looks like '
    'a stub, a token, a verification marker, or "not substantive '
    'enough" — that is content judgment, and the rule is '
    '**every found file gets a row**. If a file is empty or '
    'near-empty, write `"Empty/stub — knowledge accumulates '
    'here"` as the summary rather than dropping the row. '
    'For non-stub files, read each and write a one-line summary '
    '(≤80 chars) describing its topic. Leave its Relevance '
    'cell empty (just `| |`) unless the file is clearly '
    'tagged to a platform.\n'
    '\n'
    '3. Use vibe_seller_write_workspace_file to write '
    'knowledge/CATALOG.md (L2) as a markdown table '
    '(File | Relevance | Summary). First all L1 rows '
    '(with knowledge/project/ prefix, copied Relevance + '
    'Summary), then L2 files (with knowledge/ prefix).\n'
    '\n'
    'CRITICAL: Every path MUST start with knowledge/.\n'
    'CRITICAL: Preserve the Relevance column — the per-task '
    'system prompt uses it to decide which rows are '
    'mandatory reads.\n'
    '\n'
    'Do NOT touch any store files or directories.'
)

CATALOG_DESC_L3: str = (
    'Regenerate the L3 store catalog for this store.\n'
    '\n'
    '1. Read knowledge/CATALOG.md (L2, already updated by '
    'the global sync). It has 3 columns (File | Relevance | '
    'Summary). Copy EVERY row with its Relevance + Summary '
    'as-is (do NOT re-derive). Keep paths EXACTLY as they '
    'appear in L2. '
    'Do NOT skip rows because the path, summary, or '
    'filename looks like a stub, a token, a hash, a '
    'verification marker, a dated suffix, or "not '
    'substantive enough" — that is content judgment, and '
    'the rule is **every L2 row gets carried into L3**. '
    'The EXCLUDE filename rules below apply ONLY to step 2 '
    '(store files); they do NOT filter L2 rows in this '
    'step.\n'
    '\n'
    '2. List store files with '
    '`Bash("find -L stores/<slug> -type f '
    "! -name CATALOG.md ! -name '.*'\")` — "
    'the `-L` flag is required because `stores/` is a symlink '
    'and without `-L`, find returns nothing. Glob/Grep also do '
    'not follow symlinks; use this Bash form.\n'
    '\n'
    'Classify each candidate **by filename only** — do NOT let '
    'the contents (stub, empty, looks like a label, "minimal '
    'content") override the filename rule. Today\'s stub is '
    "tomorrow's reference.\n"
    '\n'
    '**INCLUDE** if the filename matches any of:\n'
    '- A lowercase topic name (e.g. `notes.md`, '
    '`browser-tips.md`, `fbn-quirks.md`, `logistics.md`).\n'
    '- The canonical `STORE.md` (the one capital-letter '
    'exception).\n'
    '- A nested platform/country path (e.g. `amazon/<country>/*.md`, '
    '`noon/<country>/*.md`).\n'
    '\n'
    '**EXCLUDE** if the filename matches any of:\n'
    '- ALL_CAPS prefix with underscore — `*_PLAN_*`, '
    '`*_REPORT_*`, `*_AUDIT_*`, `*_PLAN.md`, `*_REPORT.md`.\n'
    '- A dated suffix — `*_YYYY-MM-DD.md` or `*-YYYYMMDD.md`.\n'
    '- A non-markdown system file — `metadata.json`, `*.json`, '
    '`*.lock`, `*.cache`.\n'
    '\n'
    'Files matching neither pattern: include by default; the '
    'cost of an extra row in the catalog is far smaller than '
    'the cost of dropping a real knowledge file. **Do not '
    'reason about whether the content is "transferable" or '
    '"actionable" — that is content judgment and the rule is '
    'filename-only.**\n'
    '\n'
    'For every INCLUDED file, read it and write a one-line '
    'summary (≤80 chars) describing its topic. Set its '
    'Relevance cell to the matching platform tag if any '
    '(e.g. `amazon`, `noon`, `amazon, noon`); leave empty '
    'otherwise. If the file is empty or near-empty, write '
    '`"Empty/stub — knowledge accumulates here"` rather '
    'than dropping the row. Do NOT include files from '
    'other stores.\n'
    '\n'
    '3. Use vibe_seller_write_workspace_file to write '
    'stores/<slug>/CATALOG.md as a markdown table '
    '(File | Relevance | Summary). L2 rows first (copied '
    'with their Relevance column intact), then store '
    'rows.\n'
    '\n'
    'CRITICAL: Preserve the Relevance column — the per-task '
    'system prompt uses it to decide which rows are '
    'mandatory reads.\n'
    '\n'
    'Do NOT modify knowledge/CATALOG.md.'
)
