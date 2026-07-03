import functools
import json
import logging
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.browser.bookmarks import read_bookmarks, read_ziniao_bookmarks
from app.browser.manager import store_slug as _store_slug
from app.database import async_session
from app.events_system.syncer import load_backend_config
from app.models.email_account import EmailAccount
from app.models.store import Store
from app.models.store_email_link import StoreEmailLink
from app.models.task import Task
from app.prompts import (
    DUAL_BROWSER_PROMPT,
    TICKTICK_TOOLS_PROMPT,
)
from app.task_states import TaskStatus
from app.workspace.manager import VIBE_SELLER_DIR

logger = logging.getLogger(__name__)


# ── Context builders ────────────────────────────────────────


def detect_language_hint(title: str, description: str | None = None) -> str:
    """Detect if task text contains Chinese and return a language hint.

    Covers prose cells in tables (e.g. Recommendation columns) too —
    those are the place agents most often drift back to English under
    context pressure, and the language gate at set_task_result will
    deny results whose prose runs below 85% in the expected script.
    """
    text = title + (description or '')
    has_chinese = any('\u4e00' <= ch <= '\u9fff' for ch in text)
    if has_chinese:
        return (
            '\n\nIMPORTANT: The user writes in Chinese. Respond in '
            'Chinese (中文) for ALL prose you produce — plans, results,'
            ' chat messages, AND prose cells inside tables (e.g. the '
            'Recommendation column of every audit / analysis table). '
            'Identifiers, IDs, SKUs, ASINs, verbatim search terms, '
            'URLs, and metric names (ROAS, ACOS, CTR, CVR, CPC, etc.)'
            ' stay in their original form. Mixed-language prose is a'
            ' defect: the server enforces this at set_task_result and'
            ' will reject results whose prose is < 85% Chinese.\n'
        )
    return (
        '\n\nIMPORTANT: Respond in English for plans, results, and '
        'messages. The server enforces this at set_task_result and '
        'will reject results whose prose is < 85% English.\n'
    )


# -- Platform seller-center URL lookup (parsed once) --
# Scans knowledge/common/*-sites.md for markdown tables with
# | ... | CODE | `URL` | rows.  Returns {platform: {code: url}}.


@functools.lru_cache(maxsize=1)
def _get_platform_sites() -> dict[str, dict[str, str]]:
    """Parse *-sites.md → {platform: {country_code: url}}."""
    knowledge_dir = VIBE_SELLER_DIR / 'knowledge' / 'project' / 'common'
    result: dict[str, dict[str, str]] = {}
    if knowledge_dir.is_dir():
        for path in knowledge_dir.glob('*-sites.md'):
            platform = path.stem.rsplit('-sites', 1)[0]
            sites: dict[str, str] = {}
            for m in re.finditer(
                r'\|\s*[^|]+\|\s*(\w+)\s*\|\s*`([^`]+)`'
                r'\s*\|',
                path.read_text(),
            ):
                sites[m.group(1)] = m.group(2)
            if sites:
                result[platform] = sites
    return result


def build_store_context(
    store: Store,
    email_addresses: list[str] | None = None,
    task_platform: str | None = None,
    task_country: str | None = None,
) -> str:
    slug = _store_slug(store.name, store.id)
    backend = store.browser_backend
    if backend == 'ziniao':
        ctx = (
            f"You are working on store '{store.name}'.\n"
            f'Store profile: stores/{slug}/ — read-only during '
            f'task execution. Read STORE.md, notes.md, '
            f'logistics.md, browser-routing.md, metadata.json '
            f'for context. All files you produce (audit reports, '
            f'improvement plans, captures, scripts, downloads) '
            f'go to your CWD per the system prompt. Knowledge '
            f'updates to stores/{slug}/ happen at reflection, '
            f'not during the task.\n'
            f'\n## BROWSER\n'
            f'Drive the browser by piping Python to the '
            f'`browser-use` CLI via a heredoc (0.13 has no '
            f'subcommands — e.g. '
            f'`browser-use <<PY` / `new_tab(url)` / `PY`).\n'
            f'Your store slug is `{slug}` — this exact string, '
            f'NOT the display name. The wrapper is '
            f'`~/.vibe-seller/bin/{slug}/browser-use` and browser '
            f'downloads land in `~/.vibe-seller/downloads/{slug}/` '
            f'(`ls -lt` it after triggering a download). Ignore '
            f'any other slug spelling from the task text or stale '
            f'directories.\n'
            f'IMPORTANT: Before using any browser-use commands, '
            f'load the browser-harness skill first '
            f'(`/browser-harness`). Do NOT guess helper names or '
            f'syntax — the skill documents the heredoc API.\n'
            f'IMPORTANT: Authenticated pages (seller center, '
            f'email providers, admin panels) require JavaScript '
            f'and login cookies. Do NOT use WebFetch or curl '
            f'for these — always use browser-use.\n'
            f'SECURITY: Ziniao provides IP isolation for this '
            f"store. NEVER extract this store's cookies (via a "
            f'browser-use cookies helper, `js("document.cookie")`, '
            f'or any other means) and reuse them with curl, '
            f'requests, Python http clients, or any tool outside '
            f'browser-use. This bypasses IP isolation and will '
            f'trigger platform security alerts. ALL authenticated '
            f'page access MUST go through browser-use.\n'
            f'\n## DUAL BROWSER SYSTEM\n'
            f'- **Ziniao Browser** (default): '
            f'For seller center URLs\n'
            f'- **Chrome Auxiliary**: '
            f'`browser-use --session {slug}-aux` '
            f'(for everything else)\n'
        )
        ctx += '\n' + DUAL_BROWSER_PROMPT
    else:
        ctx = (
            f"You are working on store '{store.name}'.\n"
            f'Store profile: stores/{slug}/ — read-only during '
            f'task execution. Read STORE.md, notes.md, '
            f'logistics.md, browser-routing.md, metadata.json '
            f'for context. All files you produce (audit reports, '
            f'improvement plans, captures, scripts, downloads) '
            f'go to your CWD per the system prompt. Knowledge '
            f'updates to stores/{slug}/ happen at reflection, '
            f'not during the task.\n'
            f'\n## BROWSER\n'
            f'Drive the browser by piping Python to the '
            f'`browser-use` CLI via a heredoc (0.13 has no '
            f'subcommands — e.g. '
            f'`browser-use <<PY` / `new_tab(url)` / `PY`).\n'
            f'Your store slug is `{slug}` — this exact string, '
            f'NOT the display name. The wrapper is '
            f'`~/.vibe-seller/bin/{slug}/browser-use` and browser '
            f'downloads land in `~/.vibe-seller/downloads/{slug}/` '
            f'(`ls -lt` it after triggering a download). Ignore '
            f'any other slug spelling from the task text or stale '
            f'directories.\n'
            f'IMPORTANT: Before using any browser-use commands, '
            f'load the browser-harness skill first '
            f'(`/browser-harness`). Do NOT guess helper names or '
            f'syntax — the skill documents the heredoc API.\n'
            f'IMPORTANT: Authenticated pages (seller center, '
            f'email providers, admin panels) require JavaScript '
            f'and login cookies. Do NOT use WebFetch or curl '
            f'for these — always use browser-use.\n'
        )

    # Inject platform-countries metadata. This is a best-effort
    # cache written by prior tasks — not an authoritative list.
    # See design_system.md "Store profile & metadata are reference,
    # NOT a gate" for how the agent should treat this.
    pc = (
        json.loads(store.platform_countries) if store.platform_countries else {}
    )
    if pc:
        parts = [f'{p}: {", ".join(cs)}' for p, cs in pc.items()]
        ctx += (
            f'\nPreviously observed platform-countries '
            f'(reference only — task may target others): '
            f'{" | ".join(parts)}'
        )
        # Resolve seller-center URLs for known platforms
        all_sites = _get_platform_sites()
        url_lines: list[str] = []
        for platform, countries in pc.items():
            sites = all_sites.get(platform, {})
            for country in countries:
                url = sites.get(country)
                if url:
                    url_lines.append(f'  - {platform} {country}: {url}')
        if url_lines:
            ctx += (
                '\n\nSeller center URLs for the platforms above '
                '(use these exact URLs when the task targets one '
                'of them; for other platforms, consult the '
                'platform skill):\n' + '\n'.join(url_lines)
            )
    else:
        ctx += (
            '\nThis store has no recorded platforms yet. If the'
            ' task targets a known platform, load its skill for'
            ' URLs and flows. Only ask the user when no skill'
            ' covers the platform and no credentials are bound.'
            ' Update `stores/<slug>/metadata.json` at the end of'
            ' the task to record what you used.'
        )

    # Task-level platform/country selection
    if task_platform:
        ctx += f'\nThis task targets platform: {task_platform}'
        if task_country:
            ctx += f', country: {task_country}'
    if backend == 'ziniao':
        ctx += (
            '\n\nZINIAO AUTO-FILL: Ziniao auto-fills login '
            'credentials and OTP codes.\n'
            'IMPORTANT: a screenshot / page_info() may NOT show '
            'auto-filled input VALUES — a field can look empty '
            'even when filled.\n'
            'Read a field value with '
            '`js("return document.querySelector(SEL).value")` '
            'to check whether it was filled (passwords may be '
            'redacted).\n\n'
            'Login flow (all inside a `browser-use` heredoc):\n'
            '1. new_tab(login_url); wait_for_load(); sleep 3 '
            'for auto-fill\n'
            '2. capture_screenshot() to see the form\n'
            '3. js(...) to read the password/OTP field value '
            '— non-empty means filled\n'
            '4. If filled, click Sign-in/Submit '
            '(click_at_xy) — do NOT re-type credentials\n'
            '5. For OTP/2FA: sleep 5 for Ziniao to fetch the '
            'code, re-read the value via js(...), then click '
            'Submit\n'
            '6. Only ask the user if the value is still empty '
            'after 10+ seconds'
        )

    # Inject bookmarks as initial context.
    # NOTE: Ziniao encrypts its Bookmarks file on disk, so
    # read_ziniao_bookmarks() always returns [].
    bookmarks: list[dict] = []
    if backend == 'chrome':
        bookmarks = read_bookmarks(slug)
    elif backend == 'ziniao' and store.browser_oauth:
        bookmarks = read_ziniao_bookmarks(store.browser_oauth)
    if bookmarks:
        ctx += '\n\n## Browser Bookmarks\n'
        ctx += (
            'The user has saved these bookmarks — '
            'they likely represent important pages:\n'
        )
        for bm in bookmarks[:20]:  # Limit to 20
            ctx += f'- {bm["name"]}: {bm["url"]}\n'
    elif backend == 'ziniao':
        ctx += (
            '\n\nNote: Ziniao bookmarks are encrypted and '
            'not readable from disk. If you need the seller '
            'center URL, check the store profile above or '
            'ask the user.'
        )

    # Inject email tool context
    if email_addresses:
        n = len(email_addresses)
        emails_str = ', '.join(email_addresses)
        ctx += (
            f'\n\n## Email System\n'
            f'This store has {n} connected email account(s): '
            f'{emails_str}.\n'
            f'Emails are synced to local SQLite DBs every '
            f'5 minutes automatically.\n\n'
            f'**To get DB paths and schema**, call:\n'
            f'  vibe_seller_email_info('
            f'store_id="{store.id}")\n'
            f'Then query directly via sqlite3 CLI, e.g.:\n'
            f'  sqlite3 <db_path> "SELECT subject, sender, '
            f"date FROM emails WHERE folder='INBOX' "
            f'ORDER BY date DESC LIMIT 20"\n\n'
            f'Available columns: message_id, folder, '
            f'subject, sender, recipient, date, body_text, '
            f'body_html, raw_headers, attachments, flags, '
            f'fetched_at, email_account\n\n'
            f'**To sync emails immediately** (instead of '
            f'waiting for the 5-min auto-sync), call:\n'
            f'  vibe_seller_sync_email_now('
            f'account_email="<email>")\n'
            f'Then query the DB as usual.\n\n'
            f'**To send email**, call:\n'
            f'  vibe_seller_send_email('
            f'account_email="...", to="...", '
            f'subject="...", body="...")\n\n'
            f'Prefer reading and analyzing email content '
            f'yourself using your language understanding '
            f'over writing scripts or regex to '
            f'classify/filter emails.'
        )

    return ctx


def ticktick_context() -> str:
    """Return TickTick prompt if connected, else empty."""
    config = load_backend_config('dida365')
    if config.get('access_token_enc'):
        return '\n\n' + TICKTICK_TOOLS_PROMPT
    return ''


async def build_system_context(task: Task) -> str:
    """Build system-level context block for agent prompts.

    Opens its own DB session to avoid closed-session issues
    at call sites.
    """
    lines = ['\n\n## System Context']

    # Task control MCP tools (task_id is implicit — do not pass it)
    lines.append(
        'Available task control MCP tools '
        '(operate on the current task automatically):\n'
        '- `vibe_seller_set_task_error(error)` — '
        'call when the task could not complete its primary '
        "objective (e.g. browser won't start, service down, "
        'page never loads, recovery exhausted). The error '
        'shows in the UI red banner. Does NOT transition task '
        'status; after calling, end your session and the '
        'infrastructure will mark the task FAILED during '
        'cleanup.\n'
        '- `vibe_seller_set_task_result(result)` — '
        'record a final result summary for the user. Use this '
        'when the text you want to show as the result differs '
        'from your last chat message (e.g. a structured summary, '
        'file paths, JSON). Optional — if you never call this, '
        'your last assistant message is saved as the result. '
        'Does NOT mark the task completed; completion is '
        'automatic when your session ends.\n'
        '\n'
        '**Picking the right tool:**\n'
        '- Success → `vibe_seller_set_task_result(summary)` or '
        'nothing. Do NOT also call `vibe_seller_set_task_error`.\n'
        '- Hard failure → `vibe_seller_set_task_error(reason)` '
        'only. The reason must describe what went wrong, never '
        'positive phrasing like "completed successfully".\n'
        '- Partial (collected useful output but the primary '
        'objective was not achieved) → call BOTH, with '
        '`vibe_seller_set_task_error` stating what was missing. '
        'This is the ONLY case where both tools are called.'
    )

    # Task type
    if task.schedule_id:
        lines.append(
            'Task type: **scheduled** (recurring). '
            'Scheduling is managed by the platform UI.'
        )
    else:
        lines.append(
            'Task type: **one-time**. '
            'Scheduling and triggering are managed by the '
            'platform UI — do not ask about them.'
        )

    # Configured integrations
    integrations = []

    # TickTick / Dida365
    dida_config = load_backend_config('dida365')
    if dida_config.get('access_token_enc'):
        integrations.append('TickTick/Dida365: connected (use MCP tools)')

    # Email accounts
    is_store_task = bool(task.store_id)
    async with async_session() as db:
        # Previous-run watermark for scheduled tasks
        if task.schedule_id:
            prev = await db.execute(
                select(Task.completed_at)
                .where(
                    Task.schedule_id == task.schedule_id,
                    Task.status == TaskStatus.COMPLETED,
                    Task.id != task.id,
                )
                .order_by(Task.completed_at.desc())
                .limit(1)
            )
            prev_completed = prev.scalar_one_or_none()
            if prev_completed:
                lines.append(
                    'Previous run completed at: '
                    f'{prev_completed}\n'
                    'Use this timestamp to determine '
                    'what is new or changed since the '
                    'last successful run.'
                )
            else:
                lines.append(
                    'This is the first run of this '
                    'schedule. No previous completion '
                    'timestamp is available.'
                )

        if is_store_task:
            # Store tasks already get email info via store context
            pass
        else:
            # Fetch all emails + linked store names in one query
            result = await db.execute(
                select(EmailAccount).order_by(EmailAccount.email)
            )
            all_emails = result.scalars().all()
            if all_emails:
                # Batch-fetch all store links
                link_rows = await db.execute(
                    select(
                        StoreEmailLink.email_account_id,
                        Store.name,
                    ).join(
                        Store,
                        Store.id == StoreEmailLink.store_id,
                    )
                )
                # Group by email_account_id
                links_map: dict[str, list[str]] = {}
                for eid, sname in link_rows.all():
                    links_map.setdefault(eid, []).append(sname)

                email_lines = []
                for ea in all_emails:
                    store_names = links_map.get(ea.id, [])
                    if store_names:
                        stores_str = ', '.join(store_names)
                        email_lines.append(f'  {ea.email} → {stores_str}')
                    else:
                        email_lines.append(
                            f'  {ea.email} (not linked to any store)'
                        )
                integrations.append(
                    f'Email: {len(all_emails)} account(s) '
                    f'(synced to SQLite DBs every 5 min):\n'
                    + '\n'.join(email_lines)
                    + '\n  Use vibe_seller_email_info('
                    'store_id=...) to get DB paths, then '
                    'query via sqlite3 CLI.\n'
                    '  Use vibe_seller_sync_email_now('
                    'account_email=...) for immediate sync.\n'
                    '  Use vibe_seller_send_email(...) to '
                    'send emails.'
                )

    if integrations:
        lines.append('')
        lines.append('Configured integrations:')
        for item in integrations:
            # Indent continuation lines for proper markdown bullets
            indented = item.replace('\n', '\n  ')
            lines.append(f'- {indented}')

    # Platform capabilities
    lines.extend([
        '',
        'Platform capabilities: browser automation '
        '(Playwright/Chrome/Ziniao), file operations, '
        'MCP tools, sub-task creation.',
        'NOT supported: webhooks, push notifications, '
        'real-time listeners, cron expressions in agent code.',
        'IMPORTANT: Authenticated web pages (seller center, '
        'email providers, admin panels) require JavaScript '
        'and login cookies. Do NOT use WebFetch or curl — '
        'store tasks should use browser-use via Bash; '
        'orchestrator tasks should use their "web" browser for '
        'public pages and delegate seller-center work to store '
        'sub-tasks.',
    ])

    return '\n'.join(lines)


_NO_STORE_WRITE_POLICY = [
    'Write policy (no-store task):',
    '- Any files you create, download, or generate for this '
    'task (reports, scripts, CSVs, PDFs, screenshots, '
    'compiled artifacts, data dumps, etc.) go in the task '
    'workspace root (`./`) — that is your CWD.',
    '- Do NOT write to `stores/<name>/…` — those directories '
    'are owned by individual stores and their L3 catalog. '
    'Writing a cross-store artifact there pollutes one '
    "store's knowledge with data about the others.",
    '- Your last assistant message is automatically persisted '
    'as `task.result` and shown in the UI; use it for the '
    'final summary / hand-off message.',
]


async def build_all_stores_context(
    db: AsyncSession,
) -> str:
    """Build cross-store summary for no-store tasks."""
    result = await db.execute(select(Store))
    stores = result.scalars().all()
    if not stores:
        # Fresh installs still need the write policy — the
        # `stores/` symlink exists even with zero stores, and a
        # no-store task would otherwise get no guidance about
        # where to put its artifacts.
        return '\n'.join([
            'You are a cross-store orchestrator with a general-purpose '
            '"web" browser (Chrome, not tied to any store) for neutral '
            'public web work — web search, tracking/logistics/carrier '
            'pages, public research. Run `browser-use` via Bash (load '
            'the /browser-harness skill first for exact CLI syntax).',
            '',
            'The web browser has NO store login/cookies and no per-store '
            'IP isolation. NEVER open a seller/merchant center or log '
            'into any store or platform account on it.',
            '',
            'No stores are configured yet, so no seller-center sub-tasks '
            'are possible until a store is created.',
            '',
            *_NO_STORE_WRITE_POLICY,
        ])

    lines = [
        'You are a cross-store orchestrator with a general-purpose '
        '"web" browser (Chrome, not tied to any store) for neutral '
        'public web work — web search, tracking/logistics/carrier '
        'pages, public research. Run `browser-use` via Bash (load the '
        '/browser-harness skill first for exact CLI syntax).',
        '',
        'The web browser has NO store login/cookies and no per-store IP '
        'isolation. NEVER open a seller/merchant center or log into any '
        'store or platform account on it — create a store sub-task for '
        'anything that touches a store backend.',
        '',
        'Available stores:',
    ]
    for s in stores:
        pc = json.loads(s.platform_countries) if s.platform_countries else {}
        if pc:
            pc_str = ', '.join(f'{p}: {", ".join(cs)}' for p, cs in pc.items())
        else:
            pc_str = 'not yet explored'
        lines.append(
            f'- "{s.name}" (id: {s.id}) '
            f'— {pc_str} '
            f'— browser: {s.browser_backend}'
        )

    lines.extend([
        '',
        'NOTE: The country/platform info above is learned from '
        'previous tasks, NOT authoritative. "not yet explored" '
        'means the store has never run a task — it does NOT '
        'mean it has no countries. Do not skip a store just '
        'because its metadata is empty.',
        '',
        'To run browser tasks, create sub-tasks for specific '
        'stores '
        "using `vibe_seller_create_task` with the store's "
        '`store_id` and your own task ID as `parent_task_id`. '
        'Each sub-task auto-runs with its own browser session.',
        '',
        'Default: create ONE sub-task per store. Each store '
        'sub-task runs in its own browser session and should '
        'handle the full scope of work for that store. '
        'Only split further if the user explicitly requests it.',
        '',
        'Stores with multiple countries handle country '
        'switching within the same browser session — '
        'do NOT create separate sub-tasks per country.',
        '',
        'You CAN: use the "web" browser (`browser-use`) for neutral '
        'public web work, read/write workspace files, create '
        'sub-tasks, list stores/tasks.',
        "You CANNOT: reach any store's seller center or authenticated "
        'merchant pages from the web browser — those live behind the '
        "store's own isolated (Ziniao/Chrome) session. Delegate that "
        'work to a store sub-task.',
        'Do NOT use WebFetch or curl for authenticated pages (they '
        'need JavaScript + login cookies) — use the web browser for '
        'public pages and delegate seller-center work to a store '
        'sub-task.',
        '',
        'IMPORTANT: You cannot wait for sub-task results '
        'in this session. Create sub-tasks with clear '
        'instructions. Check results later via '
        '`vibe_seller_list_tasks(parent_task_id=<your_id>)`.',
        '',
        'Do NOT create no-store sub-tasks. '
        'Every sub-task MUST have a store_id.',
        '',
        *_NO_STORE_WRITE_POLICY,
    ])
    return '\n'.join(lines)
