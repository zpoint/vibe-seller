"""Server-side contract for the 3-tier knowledge catalog.

The catalog is the navigation surface every task reads before acting
(``design_system.md`` § Knowledge Recall): L1 ``knowledge/project/``
(repo-shipped, read-only), L2 ``knowledge/``, L3 ``stores/<slug>/``,
each tier cumulative — L3 ⊇ L2 ⊇ L1 — as a ``File | Relevance |
Summary`` table whose ``Relevance`` names platforms and decides
mandatory reads.

Summaries and Relevance tags are an LLM's judgement and stay in the
sync task's prompt — an unfamiliar tag is harmless (no task's
platform matches it, so it makes no read mandatory). What must never
depend on the model is the structure — which rows exist and which
tier they belong to. This module enforces exactly that at the
write boundary (``routers/workspace.py`` → :func:`validate_catalog_write`),
so a wrong catalog is rejected naming the rows to fix, instead of
silently hiding knowledge from every later task:

- **format** — header row, 3 columns per row, tier path prefixes;
- **accumulation** — L2 carries every L1 row (``knowledge/project/``
  prefix, same Relevance); L3 carries every L2 row;
- **completeness** — every file of the tier appears exactly once.
  Names only decide which rows are REQUIRED (the run-output default);
  they never forbid one — a row naming an existing store file is the
  writer declaring it knowledge, which is how the user's own
  ``PROMO_RULES.md`` gets into the reading list despite its ALL_CAPS
  name. Machine files (``*.json``/``*.lock``/``*.cache``) are the one
  refusal: the server wrote them, they are not prose;
- **isolation** — no other store's files, no machine files;
- **stub contract** — a substantive file must not be stubbed
  (:func:`is_near_empty`; the deterministic check this module grew
  from).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import posixpath
import re

from app.workspace.fs_walk import tolerant_rglob

# Must match the summary string the sync prompt instructs the agent to
# write for empty files (app/prompts/__init__.py CATALOG_FORMAT_SPEC).
STUB_SUMMARY = 'Empty/stub — knowledge accumulates here'

# A file with fewer than this many body words (after stripping YAML
# frontmatter, markdown headings, and blank lines) has nothing to
# summarize — a legitimate stub. At/above it the file has a topic and
# MUST be summarized, not stubbed. Deliberately low so genuinely tiny
# placeholders (a bare heading, a one-word marker) still stub cleanly
# and only files with real prose are protected.
_MIN_BODY_WORDS = 5

_HEADER = ('file', 'relevance', 'summary')

# Max examples per error category — enough to act on, short enough to
# read inside one tool error.
_MAX_EXAMPLES = 5

# Max total error lines in one rejection.
_MAX_ERRORS = 15


def is_near_empty(content: str) -> bool:
    """True when a file has no describable body.

    Deterministic replacement for the sync model's "empty or near-empty"
    judgment: drop YAML frontmatter, markdown heading lines, and blank
    lines, then count the remaining body words.
    """
    s = content
    if s.startswith('---'):
        end = s.find('\n---', 3)
        if end != -1:
            s = s[end + 4 :]
    words: list[str] = []
    for line in s.splitlines():
        t = line.strip()
        if not t or t.startswith('#'):
            continue
        words.extend(t.split())
    return len(words) < _MIN_BODY_WORDS


# ── Parsing ────────────────────────────────────────────


@dataclass(frozen=True)
class CatalogRow:
    """One data row of a catalog table."""

    path: str
    relevance: str
    summary: str


def parse_catalog(catalog_text: str) -> list[CatalogRow]:
    """Parse a CATALOG.md table into data rows.

    Requires the ``| File | Relevance | Summary |`` header row and
    exactly 3 cells per data row; raises ``ValueError`` naming the
    first shape problem so the writer can fix exactly that. Lines that
    are not table rows (headings, comments, prose) are skipped, and
    rows whose first cell is not a path (no ``/``) are treated as
    labels and ignored.
    """
    rows: list[CatalogRow] = []
    header_seen = False
    for line in catalog_text.splitlines():
        t = line.strip()
        if not t.startswith('|'):
            continue
        cells = [c.strip() for c in t.strip('|').split('|')]
        # Separator row (only dashes/colons/spaces) — not data.
        if not ''.join(cells).strip('-: '):
            continue
        if not header_seen:
            if tuple(c.lower() for c in cells) != _HEADER:
                raise ValueError(
                    'the table must start with the header row '
                    '`| File | Relevance | Summary |`'
                )
            header_seen = True
            continue
        if len(cells) != 3:
            raise ValueError(
                f'row {len(rows) + 1} has {len(cells)} cells (expected 3): {t}'
            )
        if '/' not in cells[0]:
            continue  # a label row, not a file row
        rows.append(
            CatalogRow(path=cells[0], relevance=cells[1], summary=cells[2])
        )
    if not header_seen:
        raise ValueError(
            'the table must start with the header row '
            '`| File | Relevance | Summary |`'
        )
    return rows


def find_wrongly_stubbed(
    catalog_text: str, read_text: Callable[[str], str | None]
) -> list[str]:
    """Paths the catalog stubs although the file is substantive.

    ``read_text(rel_path)`` returns the referenced file's text, or None if
    it can't be read (missing/binary) — those are skipped, since a stub
    can't be disproven without the content.
    """
    bad: list[str] = []
    for row in parse_catalog(catalog_text):
        if STUB_SUMMARY not in row.summary:
            continue
        try:
            content = read_text(row.path)
        except Exception:
            content = None
        if content is not None and not is_near_empty(content):
            bad.append(row.path)
    return bad


def reject_wrong_stubs(
    catalog_text: str, resolve_file: Callable[[str], Path]
) -> None:
    """Raise ValueError if the catalog stubs a substantive file.

    ``resolve_file(rel_path)`` returns the workspace Path for a row's
    file (may raise OSError/ValueError if missing/unsafe — treated as
    unreadable). Called from the CATALOG.md write path so the contract
    is enforced server-side rather than trusted to the sync agent.
    """

    def _read(rel: str) -> str | None:
        try:
            return resolve_file(rel).read_text(encoding='utf-8')
        except (OSError, ValueError):
            return None

    bad = find_wrongly_stubbed(catalog_text, _read)
    if bad:
        listed = ', '.join(bad[:10])
        raise ValueError(
            f'CATALOG marks {len(bad)} file(s) as "{STUB_SUMMARY}" but '
            f'they have real content: {listed}. Read each and write a '
            'one-line topic summary (≤80 chars) instead of the stub, '
            'then write CATALOG.md again.'
        )


# ── Tier context (filesystem truth the gate compares against) ──
#
# Knowledge vs task output is the WRITER's call, not a name guess:
# a row naming an existing file of the tier IS the declaration that
# the file is knowledge, and the server honours it whatever the file
# is called. Names only decide the REQUIREMENT default — the run-
# output shapes (ALL_CAPS, dated) do not force a row, so a leaked
# report does not turn every catalog sync into a hunt for it; but
# they never forbid one either, which is how a user's own
# ``PROMO_RULES.md`` (ALL_CAPS by name, knowledge by content) gets
# onto the reading list. Machine files are the one true exemption:
# the server wrote them and no reader is meant to read them.


def _read_optional(resolve_file: Callable[[str], Path], rel: str) -> str | None:
    """Text of a workspace file, or None when missing/unreadable."""
    try:
        return resolve_file(rel).read_text(encoding='utf-8')
    except (OSError, ValueError):
        return None


def _read_reference(
    resolve_file: Callable[[str], Path], rel: str
) -> list[CatalogRow] | None:
    """The on-disk catalog a write is compared against, or None.

    Missing AND unparseable both mean "no reference", like
    :func:`_read_optional` means "no file". The row that fails to parse
    belongs to the reference, not to the writer: an error quoting it
    sends the agent to fix a table it was told to copy verbatim, and
    because this fires before the one-round accounting it is a permanent
    wall — one stray ``|`` in a shared summary stopped the catalog sync
    of every store on the install. Nothing is copied from a catalog that
    cannot be read, so the write is judged on what it does owe.
    """
    text = _read_optional(resolve_file, rel)
    if text is None:
        return None
    try:
        return parse_catalog(text)
    except ValueError:
        return None


def _parse_l1(
    resolve_file: Callable[[str], Path],
) -> list[CatalogRow] | None:
    """Rows of the shipped L1 catalog (paths relative to project/)."""
    text = _read_optional(resolve_file, 'knowledge/project/CATALOG.md')
    if text is None:
        return None
    try:
        return parse_catalog(text)
    except ValueError as e:
        raise ValueError(f'L1 catalog is malformed: {e}') from e


def is_machine_file_name(name: str) -> bool:
    """True when *name* is a file the machine writes, never prose.

    ``metadata.json`` is server-owned store state and ``*.lock`` /
    ``*.cache`` are tooling files: no row is due for them and none may
    name them — a catalog row is a READING instruction, and these are
    not prose. Unlike the old run-output rule, this excludes nothing
    a human could mean to read: it is about the writer (the server /
    the tools), not about guessing knowledge from a name shape.
    """
    return name.endswith(('.json', '.lock', '.cache'))


# The DEFAULT the L3 sync prompt works from (mirrored so the gate
# never FORCES a row down a run-output name; a name that looks like
# per-run output exempts a file from requiring a row — it does not
# forbid one. Whether such a file is actually knowledge is the
# writer's call, made by giving it a row: the user's own
# ``PROMO_RULES.md`` is exactly a knowledge file whose ALL_CAPS name
# the old rule read as "task output" and could never catalog.
# Dated: *_YYYY-MM-DD.md / *-YYYYMMDD.md.
_DATED_NAME = (
    re.compile(r'_\d{4}-\d{2}-\d{2}\.md$'),
    re.compile(r'-\d{8}\.md$'),
)


def is_run_output_name(name: str) -> bool:
    """True when *name* looks like a per-run output: run-output names
    do not REQUIRE a catalog row (the sync prompt's cheap default).

    Never call this to REFUSE a row — a row naming an existing file
    is the writer declaring that file knowledge, and the server does
    not second-guess that from a name. ``STORE.md`` is the canonical
    exception (a single ALL-CAPS word, the store profile itself).
    """
    stem = name.rsplit('.', 1)[0] if '.' in name else name
    parts = stem.split('_')
    if len(parts) > 1 and all(
        p and not any(c.islower() for c in p) for p in parts
    ):
        return True
    return any(pat.search(name) for pat in _DATED_NAME)


def _l2_files(root: Path) -> set[str]:
    """Every L2 file that must have a row: ``knowledge/**/*.md``.

    Excludes the L1 subtree and both CATALOG.md files — the same set
    the sync prompt's ``find -L knowledge -name '*.md' ! -path
    'knowledge/project/*' ! -name CATALOG.md`` produces.
    """
    files: set[str] = set()
    knowledge = root / 'knowledge'
    if not knowledge.is_dir():
        return files
    for f in tolerant_rglob(knowledge, '*.md'):
        if not f.is_file():
            continue
        rel = f.relative_to(root).as_posix()
        if rel == 'knowledge/CATALOG.md' or rel.startswith(
            'knowledge/project/'
        ):
            continue
        files.add(rel)
    return files


def _store_files(root: Path, slug: str) -> set[str]:
    """Store files that MUST have an L3 row, per the default rules.

    Machine files never do (the server wrote them); names that look
    like run output are not FORCED into a row (the sync prompt's cheap
    default). Neither exemption forbids a row: a row naming an
    existing file is the writer declaring it knowledge — see
    :func:`_validate_l3`.
    """
    files: set[str] = set()
    store_dir = root / 'stores' / slug
    if not store_dir.is_dir():
        return files
    for f in tolerant_rglob(store_dir):
        if not f.is_file() or f.name.startswith('.'):
            continue
        rel = f.relative_to(root).as_posix()
        if rel == f'stores/{slug}/CATALOG.md':
            continue
        if is_machine_file_name(f.name) or is_run_output_name(f.name):
            continue
        files.add(rel)
    return files


# ── The gate ───────────────────────────────────────────


def _cap(items: list[str]) -> str:
    listed = sorted(items)[:_MAX_EXAMPLES]
    out = ', '.join(listed)
    if len(items) > _MAX_EXAMPLES:
        out += f' (+{len(items) - _MAX_EXAMPLES} more)'
    return out


def _duplicate_errors(rows: list[CatalogRow]) -> list[str]:
    seen: set[str] = set()
    dupes: set[str] = set()
    for row in rows:
        if row.path in seen:
            dupes.add(row.path)
        seen.add(row.path)
    return [f'duplicate row: {_cap(list(dupes))}'] if dupes else []


def _validate_l2(
    content: str,
    *,
    root: Path,
    resolve_file: Callable[[str], Path],
) -> tuple[list[str], list[str]]:
    rows = parse_catalog(content)
    hard: list[str] = []
    errors: list[str] = []
    l1_rows = _parse_l1(resolve_file)

    # Format: tier prefix.
    for i, row in enumerate(rows, 1):
        if not row.path.startswith('knowledge/'):
            hard.append(
                f'row {i} path must start with knowledge/ (L2 holds no '
                f'store files): {row.path}'
            )
    errors.extend(_duplicate_errors(rows))

    # Accumulation: every L1 row, prefixed and Relevance-unchanged.
    if l1_rows is not None:
        have = {r.path: r.relevance.lower() for r in rows}
        for row in l1_rows:
            want = f'knowledge/project/{row.path}'
            if want not in have:
                errors.append(
                    f'missing L1 row: {want} '
                    f'(Relevance "{row.relevance or "—"}", copied verbatim)'
                )
            elif have[want] != row.relevance.lower():
                errors.append(
                    f'{want}: Relevance must stay "{row.relevance}" '
                    f'(copied from L1), got "{have[want]}"'
                )
        l1_paths = {f'knowledge/project/{r.path}' for r in l1_rows}
        for row in rows:
            if row.path.startswith('knowledge/project/') and (
                row.path not in l1_paths
            ):
                errors.append(
                    f'row {row.path} is under knowledge/project/ but not '
                    'in the L1 catalog'
                )

    # Completeness: L2's own files, exactly once each.
    expected = _l2_files(root)
    own = [r.path for r in rows if not r.path.startswith('knowledge/project/')]
    missing = expected - set(own)
    if missing:
        errors.append(f'missing row(s): {_cap(list(missing))}')
    for p in sorted(set(own) - expected):
        if not (root / p).exists():
            errors.append(f'row references a file that does not exist: {p}')
        else:
            errors.append(f'row is not a knowledge/*.md file: {p}')

    return hard, errors


def _validate_l3(
    content: str,
    *,
    slug: str,
    root: Path,
    resolve_file: Callable[[str], Path],
) -> tuple[list[str], list[str]]:
    rows = parse_catalog(content)
    hard: list[str] = []
    errors: list[str] = []
    prefix = f'stores/{slug}/'
    # Format + isolation: only knowledge rows and this store's rows.
    for i, row in enumerate(rows, 1):
        if not (
            row.path.startswith('knowledge/') or row.path.startswith(prefix)
        ):
            hard.append(
                f'row {i} path must start with knowledge/ or '
                f"stores/{slug}/ (no other store's files): {row.path}"
            )
    errors.extend(_duplicate_errors(rows))

    # Accumulation: every L2 row, path and Relevance unchanged.
    l2_rows = _read_reference(resolve_file, 'knowledge/CATALOG.md')
    if l2_rows is not None:
        have = {r.path: r.relevance.lower() for r in rows}
        for row in l2_rows:
            if row.path not in have:
                errors.append(
                    f'missing L2 row: {row.path} '
                    f'(Relevance "{row.relevance or "—"}", copied verbatim)'
                )
            elif have[row.path] != row.relevance.lower():
                errors.append(
                    f'{row.path}: Relevance must stay "{row.relevance}" '
                    f'(copied from L2), got "{have[row.path]}"'
                )

    # Completeness: the store's knowledge files, exactly once each.
    # A row naming an existing file of this store is ALWAYS valid —
    # the row is the writer's declaration it is knowledge, whatever
    # its name shape (run-output names only skip the REQUIREMENT).
    # Rows for machine files, and rows naming nothing, are the
    # exceptions the server can state without guessing.
    expected = _store_files(root, slug)
    store_rows = [r.path for r in rows if r.path.startswith(prefix)]
    missing = expected - set(store_rows)
    if missing:
        errors.append(f'missing row(s): {_cap(list(missing))}')
    for p in sorted(set(store_rows) - expected):
        if (root / p).exists():
            if is_machine_file_name(Path(p).name):
                errors.append(f'machine file must not be cataloged: {p}')
        else:
            errors.append(f'row references a file that does not exist: {p}')

    return hard, errors


def canonical_rel(path: str) -> str | None:
    """The workspace-relative spelling of *path*, or None if it escapes.

    The gate decides WHICH file a write is by this spelling, and the
    write itself lands by the resolved path
    (``WorkspacePaths.safe_path``) — the two must agree, or a tier
    catalog written as ``./stores/<slug>/CATALOG.md`` or
    ``stores//<slug>/CATALOG.md`` slips through unvalidated (and one
    round key per spelling would turn the "exactly one round" rule into
    one round per spelling). A ``..`` that leaves the workspace, or an
    absolute path, names a tier this function cannot work out from the
    string; callers refuse those the way
    ``knowledge_contract.tier_error`` does.
    """
    rel = posixpath.normpath(path.replace('\\', '/'))
    if rel.startswith('../') or rel == '..' or posixpath.isabs(rel):
        return None
    return rel


def catalog_problems(
    path: str,
    content: str,
    *,
    root: Path,
    resolve_file: Callable[[str], Path],
) -> tuple[list[str], list[str]]:
    """``(hard, content)`` problems with a CATALOG.md write.

    *hard* are rows outside the tier (another store's files in a store
    catalog, a store file in the shared one): always refused, they break
    store isolation. *content* are the contract rules — accumulation,
    completeness, Relevance, stubs — which the writer is told about once
    (see ``routers/workspace.py``).

    Dispatches on the exact tier path — ``knowledge/CATALOG.md`` (L2)
    and ``stores/<slug>/CATALOG.md`` (L3). Any other path (including
    ``knowledge/project/CATALOG.md``, whose write the manager refuses
    as read-only L1) is not a tier catalog and passes through. Callers
    on a request path pass :func:`canonical_rel`, so the dispatch and
    the file the write lands on name the same tier.

    Pure sync filesystem/string work; callers on the event loop wrap
    this in ``asyncio.to_thread``.
    """
    rel = path.replace('\\', '/')
    if rel == 'knowledge/CATALOG.md':
        hard, errors = _validate_l2(
            content, root=root, resolve_file=resolve_file
        )
    else:
        m = re.fullmatch(r'stores/([^/]+)/CATALOG\.md', rel)
        if not m:
            return [], []  # not a tier catalog
        hard, errors = _validate_l3(
            content, slug=m.group(1), root=root, resolve_file=resolve_file
        )

    # Stub contract (kept from the original gate): a substantive file
    # must be summarized, not stubbed.
    def _read(rel_path: str) -> str | None:
        try:
            return resolve_file(rel_path).read_text(encoding='utf-8')
        except (OSError, ValueError):
            return None

    stubbed = find_wrongly_stubbed(content, _read)
    if stubbed:
        errors.append(
            f'files with real content must be summarized, not stubbed: '
            f'{_cap(stubbed)}'
        )

    return hard, errors


def catalog_rejection(path: str, errors: list[str]) -> str:
    """One message naming every fix, so a single rewrite can fix all."""
    unique = list(dict.fromkeys(errors))
    lines = [f'- {e}' for e in unique[:_MAX_ERRORS]]
    if len(unique) > _MAX_ERRORS:
        lines.append(f'- (+{len(unique) - _MAX_ERRORS} more — fix these first)')
    return f'{path} rejected — fix every item and write again:\n' + '\n'.join(
        lines
    )


def validate_catalog_write(
    path: str,
    content: str,
    *,
    root: Path,
    resolve_file: Callable[[str], Path],
) -> None:
    """Raise ValueError naming every problem, hard or content."""
    hard, errors = catalog_problems(
        path, content, root=root, resolve_file=resolve_file
    )
    if hard or errors:
        raise ValueError(catalog_rejection(path, hard + errors))
