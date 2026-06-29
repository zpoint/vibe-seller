"""Per-campaign approved-negation allowlist derived from the audit report.

The audit report (``AD_AUDIT_<date>.md``) is the single source of truth
for which search terms may be negated. Each per-campaign section
(``### <campaign_id> | <name> | ...``) carries a search-term table whose
recommendation (last) cell reads ``**否定搜索词**`` for terms the audit
approved for negation. This module extracts that into
``{campaign_id: {normalized_term}}`` so two enforcers share ONE contract:

  * the ``vibe_seller_check_negation`` MCP tool (pre-edit advisory check),
  * the ``ad_negation_allowlist`` stop-gate (``set_task_result`` backstop).

Why an allowlist instead of letting the agent judge waste live: on a
production store the agent over-negated 12 RELEVANT terms (treating
0-order as waste) by freelancing off-report. Removing the judgment
surface — execute the report's list, negate nothing else — makes that
bug class impossible. A genuinely-irrelevant term that appears AFTER the
report snapshot (a new query) is flagged for human review, never
auto-negated: over-pruning relevant traffic is the expensive failure.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import sqlite3

from app.config import VIBE_SELLER_DIR

# "### 100000000000000 | kw-018-manual-keyword | Manual | ..."
_SECTION_RE = re.compile(r'^###\s+(\S+)\s*\|', re.MULTILINE)

# Any recommendation cell containing 否定 is a negation directive
# (否定搜索词 = negate search term; 否定该 ASIN = negate ASIN — both are
# legitimate negation targets whose subject is the row's first column).
_NEGATE_MARK = '否定'

# Recommendation directives that mean the OPPOSITE of negation — "this
# term converts / is valuable, keep serving it (or bid it up)". A
# search-term negative blocks the query globally, so a term the report
# marks BOTH 否定 AND keep within one campaign is self-contradictory:
# negating it also kills the converting row. Parsed as an action HEAD
# (startswith), symmetric with _NEGATE_MARK, so prose mentions don't
# count. CJK matched exactly; English matched case-insensitively.
_KEEP_MARKS_CJK = ('维持', '保持', '提高', '上调', '加投')
_KEEP_MARKS_EN = ('raise', 'scale', 'keep', 'maintain', 'increase', 'hold')

_HEADER_CELLS = frozenset({
    '搜索词',
    '客户搜索词',
    'customer search term',
    '关键词',
    'keyword',
    '',
})


def normalize_term(term: str) -> str:
    """Canonical form for matching: de-bold, strip, lower, collapse WS.

    Amazon also renders a non-breaking space (U+00A0) in some cells; we
    fold it to a normal space so report text and live/log text compare
    equal.
    """
    t = term.replace('*', '').replace(' ', ' ').strip().lower()
    t = re.sub(r'\s+', ' ', t)
    return t


def _is_header_cell(term: str) -> bool:
    if term in _HEADER_CELLS:
        return True
    # markdown separator row like ---|:--:
    return bool(term) and set(term) <= {'-', ':', ' '}


def _row_verdict(cells: list[str]) -> str | None:
    """``'negate'`` | ``'keep'`` | None from a row's recommendation cell.

    The recommendation ACTION must BE the directive, not merely mention
    it. A 维持/降低出价 row often explains itself with prose like "源于已
    否定的 4 个垃圾变体词" — matching 否定 anywhere would wrongly read
    those KEEP rows as negations (the over-prune bug). The real directive
    is the bolded ``**否定搜索词**`` / ``**维持**`` / ``**提高至…**`` at the
    HEAD of the cell.
    """
    if len(cells) < 2:
        return None
    rec = cells[-1].replace('*', '').strip()
    if not rec:
        return None
    if rec.startswith(_NEGATE_MARK):
        return 'negate'
    if rec.startswith(_KEEP_MARKS_CJK) or rec.lower().startswith(
        _KEEP_MARKS_EN
    ):
        return 'keep'
    return None


def _campaign_verdicts(report_text: str) -> dict[str, dict[str, set[str]]]:
    """``{campaign_id: {term: {'negate'|'keep', …}}}`` from report sections.

    Walks each ``### <campaign_id> |`` section and records, per term, the
    set of recommendation verdicts seen across the campaign's tables. A
    term can collect both verdicts when it appears in multiple rows (e.g.
    a converting row marked 维持 AND a zero-order row marked 否定) — that
    co-occurrence is the self-contradiction the gate must catch.
    """
    out: dict[str, dict[str, set[str]]] = {}
    matches = list(_SECTION_RE.finditer(report_text))
    for i, m in enumerate(matches):
        campaign_id = m.group(1).strip()
        start = m.end()
        end = (
            matches[i + 1].start() if i + 1 < len(matches) else len(report_text)
        )
        terms = out.setdefault(campaign_id, {})
        for raw in report_text[start:end].splitlines():
            line = raw.strip()
            if not line.startswith('|'):
                continue
            cells = [c.strip() for c in line.strip('|').split('|')]
            verdict = _row_verdict(cells)
            if verdict is None:
                continue
            term = normalize_term(cells[0])
            if term and not _is_header_cell(term):
                terms.setdefault(term, set()).add(verdict)
    return out


def find_negation_contradictions(report_text: str) -> dict[str, set[str]]:
    """``{campaign_id: {term}}`` where the report BOTH negates AND keeps it.

    A search-term negative blocks the customer query globally, so a term
    the audit marks 否定 in one row while marking the SAME term 维持/提高
    (it converts) in another row is a logically invalid recommendation:
    executing the negation kills the converting traffic. These terms must
    never reach the allowlist, and the report author should resolve them
    (negate only terms that net zero orders across ALL their rows).
    """
    out: dict[str, set[str]] = {}
    for campaign_id, terms in _campaign_verdicts(report_text).items():
        bad = {t for t, v in terms.items() if 'negate' in v and 'keep' in v}
        if bad:
            out[campaign_id] = bad
    return out


def build_allowlist(report_text: str) -> dict[str, set[str]]:
    """``{campaign_id: {normalized approved-negation term}}`` from report.

    A term is approved for a campaign iff the report marks it 否定 and
    does NOT also mark it 维持/提高 (keep/raise) anywhere in the same
    campaign. Excluding the self-contradicting terms (see
    ``find_negation_contradictions``) keeps the allowlist coherent: a term
    the report itself says converts can never be blessed for negation, so
    even a frozen, already-flawed report cannot authorize negating
    profitable traffic — the execution stop-gate flags it as a stray.
    """
    out: dict[str, set[str]] = {}
    for campaign_id, terms in _campaign_verdicts(report_text).items():
        out[campaign_id] = {
            t for t, v in terms.items() if 'negate' in v and 'keep' not in v
        }
    return out


def term_allowed(
    allowlist: dict[str, set[str]],
    campaign_id: str,
    term: str,
) -> bool:
    """True iff ``term`` is an approved negation for ``campaign_id``."""
    approved = allowlist.get(str(campaign_id))
    if not approved:
        return False
    return normalize_term(term) in approved


# ── Task-scoped helpers (locate report, cache, executed-log parse) ──────


def _task_dir(task_id: str) -> Path:
    return VIBE_SELLER_DIR / 'tasks' / task_id


def _parent_task_id(task_id: str) -> str | None:
    """Look up ``parent_task_id`` for a task (read-only), or None.

    Execution batches are CHILD tasks whose report lives in the PARENT's
    dir; without this the gate's ``find_report`` returned None for every
    batch and no-op'd. Best-effort, never raises.
    """

    db = VIBE_SELLER_DIR / 'data' / 'vibe_seller.db'
    if not db.exists():
        return None
    try:
        con = sqlite3.connect(f'file:{db}?mode=ro', uri=True, timeout=2)
        try:
            row = con.execute(
                'SELECT parent_task_id FROM tasks WHERE id = ?', (task_id,)
            ).fetchone()
        finally:
            con.close()
        return row[0] if row and row[0] else None
    except sqlite3.Error:
        return None


def task_scope_text(task_id: str) -> str:
    """``title + '\\n' + description`` for a task (read-only), or ''.

    Used to determine which campaigns an execution task was SUPPOSED to
    cover, so the completeness gate can tell a stopped-early run from a
    genuinely finished one. Best-effort, never raises.
    """

    db = VIBE_SELLER_DIR / 'data' / 'vibe_seller.db'
    if not db.exists():
        return ''
    try:
        con = sqlite3.connect(f'file:{db}?mode=ro', uri=True, timeout=2)
        try:
            row = con.execute(
                'SELECT title, description FROM tasks WHERE id = ?',
                (task_id,),
            ).fetchone()
        finally:
            con.close()
        return '\n'.join(c for c in (row or ()) if c) if row else ''
    except sqlite3.Error:
        return ''


def find_report(task_id: str) -> Path | None:
    """Newest ``AD_AUDIT_*.md`` reachable from the task, or None.

    Checks the task dir first, then walks ``parent_task_id`` (execution
    batches inherit their parent's report) up to a few levels.
    """
    seen: set[str] = set()
    cur: str | None = task_id
    for _ in range(4):
        if not cur or cur in seen:
            break
        seen.add(cur)
        try:
            cands = sorted(_task_dir(cur).glob('AD_AUDIT_*.md'))
        except OSError:
            cands = []
        if cands:
            return cands[-1]
        cur = _parent_task_id(cur)
    return None


def load_allowlist(task_id: str) -> dict[str, set[str]]:
    """Build the allowlist for ``task_id`` from its report (+ cache JSON).

    Returns ``{}`` when there is no report (non-ad task → gate no-ops).
    """
    report = find_report(task_id)
    if report is None:
        return {}
    try:
        text = report.read_text(encoding='utf-8')
    except OSError:
        return {}
    allow = build_allowlist(text)
    try:  # best-effort cache for the MCP tool / inspection
        cache = _task_dir(task_id) / 'NEGATION_ALLOWLIST.json'
        cache.write_text(
            json.dumps(
                {k: sorted(v) for k, v in allow.items()},
                ensure_ascii=False,
                indent=0,
            ),
            encoding='utf-8',
        )
    except OSError:  # pragma: no cover — cache is non-essential
        pass
    return allow


# An executed-negation row in EXECUTION_LOG.md, e.g.
#   | 100000000000000 | 否定 | usb-c cable | Negative exact | ✅ |
#   | N13 | cable organizer | Negative phrase | ✅ |
# We only treat a row as an EXECUTED negation when it both names a
# negation match type (Negative exact/phrase, 否定) AND is marked done
# (✅). Conservative: a row without ✅ is a plan, not an execution.
_EXEC_DONE = '✅'
_NEG_MATCH_RE = re.compile(r'negative\s+(?:exact|phrase)|否定', re.IGNORECASE)
_CAMPAIGN_ID_RE = re.compile(r'\b(\d{12,})\b')

# Category / verb / column-header cells that can sit in the term column
# of a log table but are NOT search terms — never treat them as negated.
_NON_TERM_CELLS = frozenset({
    '搜索词否定',
    '否定搜索词',
    '搜索词',
    '关键词',
    '操作',
    '活动',
    '匹配',
    'negative',
    '改出价',
    '改价',
    '加价',
    '降价',
    '暂停',
    '暂停活动',
    '暂停定向词',
})

# Substrings that mark a cell as flow/recipe/summary prose, not a term.
_NON_TERM_SUBSTR = ('→', 'click', 'index', '验证', '未滚动', '待执行', '待做')
_CURRENCY_RE = re.compile(
    r'\d\s*(?:sar|aed|usd)|(?:sar|aed|usd)\s*\d', re.IGNORECASE
)


def _is_term_like(cell: str) -> bool:
    """True if ``cell`` plausibly holds a search term (not prose/number).

    A search term is short-ish, free of flow arrows / recipe verbs /
    currency, and carries actual letters (Latin or Arabic). This keeps
    recipe-doc and summary rows from being misread as executed negations.
    """
    s = cell.strip()
    if not (2 <= len(s) <= 60):
        return False
    low = s.lower()
    if any(x in low for x in _NON_TERM_SUBSTR) or _CURRENCY_RE.search(low):
        return False
    return bool(re.search(r'[a-z؀-ۿ]', low))


def extract_executed_negations(
    log_text: str,
) -> list[tuple[str | None, str]]:
    """Best-effort ``[(campaign_id|None, normalized_term)]`` from the log.

    Scans EXECUTION_LOG table rows that are marked done (✅) and carry a
    negation match type. The term is taken from the first cell that is
    neither an index token (``N13``), a campaign id, a status glyph, nor
    the match-type/verb itself. Conservative by design: a row we cannot
    confidently parse contributes nothing (the gate would rather miss a
    stray than block a clean submit). The live-state Sonnet review is the
    exhaustive check; this is the cheap structural backstop.
    """
    found: list[tuple[str | None, str]] = []
    current_campaign: str | None = None
    for raw in log_text.splitlines():
        line = raw.strip()
        # Track the campaign a sub-table belongs to from ### headers.
        if line.startswith('###') or line.startswith('##'):
            cm = _CAMPAIGN_ID_RE.search(line)
            if cm:
                current_campaign = cm.group(1)
            continue
        if not line.startswith('|') or _EXEC_DONE not in line:
            continue
        cells = [c.strip() for c in line.strip('|').split('|')]
        # A real executed-negation row carries a STANDALONE match-type
        # cell (``Negative exact/phrase`` or exactly ``否定``). Rows where
        # 否定 only appears EMBEDDED in a longer cell are recipe docs
        # (``state → click Add as …``) or summaries (``前13词已全部否定``),
        # not executions — skip them.
        if not any(
            _NEG_MATCH_RE.fullmatch(c.lower()) or c == '否定' for c in cells
        ):
            continue
        row_campaign = None
        term = None
        for c in cells:
            if _CAMPAIGN_ID_RE.fullmatch(c):
                row_campaign = c
                continue
            low = c.lower()
            if (
                not c
                or _EXEC_DONE in c
                or re.fullmatch(r'n?\d{1,3}', low)  # index token N13/27
                or re.fullmatch(r'p\d', low)  # priority label P0/P1/P2
                or _NEG_MATCH_RE.fullmatch(low)
                or c in _NON_TERM_CELLS
                or not _is_term_like(c)
            ):
                continue
            if term is None:  # first content cell = the term
                term = c
        if term:
            found.append((
                row_campaign or current_campaign,
                normalize_term(term),
            ))
    return found


# Reversal markers: a negation the log records as later undone (archived
# in the Negative targeting tab) is no longer a live stray.
_REVERT_RE = re.compile(
    r'回退|回滚|已移除|已撤销|已回退|archived?|removed|reverted',
    re.IGNORECASE,
)


def extract_reverted_terms(log_text: str) -> set[str]:
    """Normalized terms the log records as reversed / archived.

    Recognizes two shapes, both gated on a reversal keyword being on the
    line so unrelated text is never swept in:
      * a **prose list** — ``回退以下: a / b / c`` (split on / 、 , and
        cut at a 保留/keep clause), and
      * a **table row** whose cells carry a revert marker (term = first
        content cell).
    Used to exclude already-undone negations from the stray check, so the
    legitimate negate→review→archive workflow does not trip the gate.
    """
    reverted: set[str] = set()
    for raw in log_text.splitlines():
        line = raw.strip()
        if not _REVERT_RE.search(line):
            continue
        if line.startswith('|'):
            for c in (x.strip() for x in line.strip('|').split('|')):
                low = c.lower()
                if (
                    not c
                    or _CAMPAIGN_ID_RE.fullmatch(c)
                    or _EXEC_DONE in c
                    or _REVERT_RE.fullmatch(low)
                    or _NEG_MATCH_RE.fullmatch(low)
                    or low in {'否定', 'negative'}
                    or re.fullmatch(r'n?\d{1,3}', low)
                ):
                    continue
                reverted.add(normalize_term(c))
                break
            continue
        seg = re.split(r'[:：]', line)[-1]
        seg = re.split(r'保留|→', seg)[0]
        for tok in re.split(r'[/、,，]', seg):
            t = normalize_term(tok)
            if t and (' ' in t or len(t) >= 4):
                reverted.add(t)
    return reverted


def load_exemptions(task_id: str) -> set[str]:
    """Human-approved off-report negations (the clear-waste escape valve).

    A genuinely-irrelevant search term that appears AFTER the report
    snapshot (a new query, a competitor brand, a wrong-category term) is
    not on the report allowlist but is still a valid negation once a human
    confirms it. Such terms are listed — one normalized term per line, ``#``
    comments allowed — in ``NEGATION_EXEMPTIONS.txt`` in the task dir, and
    bypass the gate. This keeps the allowlist strict (no auto-negating
    relevant traffic) while not blocking obvious waste a person OK'd.
    """
    try:
        text = (_task_dir(task_id) / 'NEGATION_EXEMPTIONS.txt').read_text(
            encoding='utf-8'
        )
    except OSError:
        return set()
    return {
        normalize_term(line)
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith('#')
    }
