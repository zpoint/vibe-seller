"""Integration no-regression smoke — the user's «integrations must work».

Runs the six journeys a real install lives or dies by, as REAL agent
tasks (a real model profile — the runner points the server at one),
against a server the smoke runner owns:

1. Email — a mailbox read through ``vibe_seller_email_info``, a query
   over its database, then a send into the FAKE SMTP stand-in.
2. A workplace CLI — the WeCom stand-in, enabled, its read commands
   run by the agent (never a real account).
3. Custom MCP — one tool call through a stand-in server added the way
   an admin adds a vendor's.
4. Ads — one call against the fake ads service.
5. A store task through the store wrapper with ``browser-use --worker``
   slots plus a file download into the store's downloads dir.
6. A WAITING task that survives a server restart and resumes on wake.

Every item must COMPLETE with **no guard deny, no output tripwire, no
terminal stop line and no stray task process afterwards** — that is
the no-regression contract: the security layers must not break the
integrations they watch, and the reaper must leave nothing behind.

The driver owns every fake IN-PROCESS (SMTP, MCP, ads, the download
site) so the platform runners only own the server itself and its
restart. It is driven by ``tests/e2e/smoke/run_smoke.{sh,ps1}``:

    python -m tests.e2e.integration_smoke --phase main \
        --base-url http://127.0.0.1:7863 --home /path/to/home \
        --server-log /path/to/server.log --out /path/to/out \
        [--ads-port 17891] [--with-browser]

``--phase resume`` finishes item 6 after the runner has restarted the
server (the WAITING task must have survived it). The MiniMax secrets
come from the usual ``MINIMAX_*`` env vars — never arguments, never
files.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time

import httpx
import psutil

# The server URL must be known before e2e_helpers reads it at import.
_PARSER = argparse.ArgumentParser(description=__doc__)
_PARSER.add_argument('--phase', choices=['main', 'resume'], required=True)
_PARSER.add_argument('--base-url', required=True)
_PARSER.add_argument(
    '--home', required=True, help='the SERVER home (VIBE_HOME)'
)
_PARSER.add_argument('--server-log', required=True)
_PARSER.add_argument('--out', required=True, help='evidence directory')
_PARSER.add_argument('--ads-port', type=int, default=17891)
_PARSER.add_argument(
    '--with-browser',
    action='store_true',
    help='run item 5 (opens a real store browser)',
)
_ARGS = _PARSER.parse_args()
os.environ['E2E_BASE_URL'] = _ARGS.base_url

from tests.e2e import e2e_helpers as h  # noqa: E402

#: Patterns that must NEVER appear for a smoke task: a C3 tripwire
#: stop, a C2 terminal stop. (A C3 guard DENY is recorded as evidence,
#: not a failure — see _run_task: the Mac run's agent listed the vibe
#: home root out of curiosity, was correctly denied, and completed the
#: item anyway. Every core action has its own positive assertion; a
#: deny that actually broke an item fails those.)
FORBIDDEN_LOG_PATTERNS = (
    'Output tripwire:',
    'terminal browser-start failure',
    'LOGIN_REQUIRED',
)
#: Recorded per item (evidence, not failure): the guard deny lines.
DENY_LOG_PATTERN = 'Vendor guard:'

PROFILE_ID = 'smoke-minimax'
TAG = f'smoke{int(time.time())}'

# Assertion tokens — each exists nowhere but the fixture that emits it.
EMAIL_SECRET = f'SMOKE-EMAIL-CODE-{TAG}'
MCP_ANSWER = 'WGT-CODE-58213'
MCP_SKU = 'WIDGET-006'
ADS_SPEND = '123.45'
BROWSER_MARKER = f'SMOKE-MARKER-{TAG}'
CSV_NAME = 'smoke-download.csv'
CSV_BODY = 'sku,qty,price\nSMOKE-001,3,19.99\nSMOKE-002,1,49.50\n'

_HOME = Path(_ARGS.home)
_OUT = Path(_ARGS.out)
_OUT.mkdir(parents=True, exist_ok=True)
_EVIDENCE: dict = {'tag': TAG, 'items': {}}
#: live fake-server references (never garbage-collected mid-run)
_FAKES: list = []


def _log(msg: str) -> None:
    print(f'[smoke {datetime.now(UTC).strftime("%H:%M:%S")}] {msg}', flush=True)


def _save_evidence() -> None:
    path = _OUT / 'evidence.json'
    merged: dict = {'tag': TAG, 'items': {}}
    if path.is_file():
        try:
            merged = json.loads(path.read_text(encoding='utf-8'))
        except ValueError:
            pass
    items = merged.setdefault('items', {})
    items.update(_EVIDENCE['items'])
    merged.setdefault('tag', TAG)
    path.write_text(json.dumps(merged, indent=2), encoding='utf-8')


def _state_write(**kw) -> None:
    state_path = _OUT / 'state.json'
    state = (
        json.loads(state_path.read_text(encoding='utf-8'))
        if state_path.is_file()
        else {}
    )
    state.update(kw)
    state_path.write_text(json.dumps(state, indent=2), encoding='utf-8')


def _state_read() -> dict:
    p = _OUT / 'state.json'
    return json.loads(p.read_text(encoding='utf-8')) if p.is_file() else {}


# ── the process-env lens (MiniMax proof + stray-process check) ──────


def _proc_env(pid: int) -> dict:
    """A process's environment, or {} (best effort).

    psutil's own implementation everywhere — on Windows it walks the
    PEB WOW64-aware, which a hand-rolled ctypes walk got wrong twice
    (short buffers, then 32-bit-offset assumptions against a 32-bit
    interpreter: the smoke's server base python is 32-bit).
    """
    try:
        return dict(psutil.Process(pid).environ())
    except (psutil.Error, OSError, ValueError):
        return {}


def _listener_pids(port: int) -> set[int]:
    """The pids LISTENING on *port*.

    psutil's global socket walk needs root on macOS (it reads every
    process's FD list and protected daemons deny it — found live: the
    driver died at startup on a psutil.AccessDenied). lsof finds OUR
    listener without root, so it is the fallback.
    """
    try:
        return {
            c.pid
            for c in psutil.net_connections(kind='tcp')
            if c.status == psutil.CONN_LISTEN and c.laddr.port == port
        }
    except psutil.AccessDenied:
        out = subprocess.run(
            ['lsof', '-ti', f':{port}', '-sTCP:LISTEN'],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return {int(x) for x in out.stdout.split() if x.isdigit()}


def _task_procs(task_id: str) -> list[dict]:
    """Every live process whose env carries this task's VIBE_TASK_ID.

    psutil's process_iter is the cross-platform enumeration (the old
    /proc walk was silently empty on macOS — the stray check there
    was vacuous); each pid's env is the driver's own lens, which
    returns {} for anything it may not read.
    """
    found = []
    try:
        pids = [p.pid for p in psutil.process_iter()]
    except psutil.Error:
        return found
    for pid in pids:
        env = _proc_env(pid)
        if env.get('VIBE_TASK_ID') == task_id:
            found.append({'pid': pid, 'env': env})
    return found


def _watch_agent_env(task_id: str, stop: threading.Event, sink: dict):
    """Background lens: catch the agent process live and record its
    provider env — the ps-eww proof that the task ran on the smoke
    profile, not the default one."""
    deadline = time.time() + 600
    while not stop.is_set() and time.time() < deadline:
        for proc in _task_procs(task_id):
            env = proc['env']
            if 'ANTHROPIC_BASE_URL' in env or 'ANTHROPIC_MODEL' in env:
                sink.update({
                    'agent_pid': proc['pid'],
                    'ANTHROPIC_BASE_URL': env.get('ANTHROPIC_BASE_URL'),
                    'ANTHROPIC_MODEL': env.get('ANTHROPIC_MODEL'),
                    'VIBE_TASK_ID': env.get('VIBE_TASK_ID'),
                })
                return
        time.sleep(1)


# ── the server-log lens (deny / tripwire / terminal-stop count) ────


def _log_lines() -> list[str]:
    lines: list[str] = []
    for suffix in ('', '.err'):
        p = Path(_ARGS.server_log + suffix)
        if p.is_file():
            lines.extend(
                p.read_text(encoding='utf-8', errors='replace').splitlines()
            )
    return lines


def _forbidden_since(start: int) -> list[str]:
    return [
        line
        for line in _log_lines()[start:]
        for pat in FORBIDDEN_LOG_PATTERNS
        if pat in line
    ]


def _verify_server_ownership() -> None:
    """The server that answers --base-url must be OURS.

    The invariant tested is the one that matters — the listener's
    process env carries our VIBE_HOME — the same proof on every
    platform (a foreign server that won a bind race answers health
    just as well; a pid-equality check through the WSL interop
    launcher proved unreliable on Windows). Uses the driver's own
    process-env lens: /proc on POSIX, the PEB on Windows.
    """
    from urllib.parse import urlparse

    port = urlparse(_ARGS.base_url).port
    listeners = _listener_pids(port)
    for pid in listeners:
        if _proc_env(pid).get('VIBE_HOME') == str(_HOME):
            _log(f'server ownership verified: pid {pid} carries our home')
            return
    raise AssertionError(
        'the server on {_ARGS.base_url} is not ours — listeners '
        f'{listeners or "(none)"} carry no VIBE_HOME={_HOME}. A foreign '
        "server (another session's e2e) may have won the port; aborting "
        'before any task runs.'
    )


# ── task driving ───────────────────────────────────────────────────


def _run_task(
    client: httpx.Client,
    title: str,
    description: str,
    *,
    store_id: str | None = None,
    expect: set[str] = frozenset({'completed'}),
) -> dict:
    """Create a task on the smoke profile, watch its agent, verify the
    no-regression contract around it."""
    log_start = len(_log_lines())
    task = h.create_task(
        client,
        title,
        store_id=store_id,
        description=description,
        profile_id=PROFILE_ID,
        plan_mode=False,
    )
    task_id = task['id']
    _log(f'task {task_id[:8]} created: {title}')

    agent_env: dict = {}
    stop = threading.Event()
    watcher = threading.Thread(
        target=_watch_agent_env, args=(task_id, stop, agent_env), daemon=True
    )
    watcher.start()
    try:
        data = h.poll_task_status(
            client,
            task_id,
            set(expect),
            fail_statuses={'failed', 'error', 'cancelled'},
            timeout=900,
        )
    finally:
        stop.set()

    forbidden = _forbidden_since(log_start)
    denies = [
        line for line in _log_lines()[log_start:] if DENY_LOG_PATTERN in line
    ]
    strays = _task_procs(task_id)
    profile_lines = [
        line
        for line in _log_lines()[log_start:]
        if f'Starting agent for task {task_id}' in line
    ]
    entry = {
        'task_id': task_id,
        'title': title,
        'status': data.get('status'),
        'result': (data.get('result') or '')[:2000],
        'error': data.get('error'),
        'agent_env': agent_env,
        'profile_line': profile_lines[:1],
        # guard denies during the item: recorded, not fatal — the deny
        # is the guard working on an out-of-scope probe (observed: the
        # macOS agent listing the vibe home root, then completing the
        # item anyway). A deny that actually broke the item fails the
        # item's own assertions or the completion check below.
        'guard_denies': denies,
        'forbidden_log_lines': forbidden,
        'stray_processes': [p['pid'] for p in strays],
    }
    assert data.get('status') in expect, (
        f'{title}: status={data.get("status")} error={data.get("error")}'
    )
    assert not forbidden, f'{title}: forbidden log lines: {forbidden[:5]}'
    assert not strays, f'{title}: stray processes: {strays}'
    assert profile_lines, f'{title}: no agent-start line in the server log'
    return entry


# ── item 1: email ──────────────────────────────────────────────────


def _item_email(client: httpx.Client, smtp_port: int) -> dict:
    from app.email.db import init_email_db, store_emails
    from tests.e2e import fake_smtp_server

    smtp_log = str(_OUT / 'fake-smtp.jsonl')
    store = h.create_store(client, f'smoke-api-{TAG}', browser_backend='chrome')
    account = (
        client.post(
            f'{h.BASE_URL}/api/email-accounts',
            json={
                'email': f'smoke-{TAG}@example.test',
                'password': 'dummy-not-used',
                'imap_host': 'imap.invalid',
                'imap_port': 993,
                'use_ssl': True,
                'smtp_host': '127.0.0.1',
                'smtp_port': smtp_port,
                'smtp_use_tls': True,
            },
        )
    ).json()
    client.post(
        f'{h.BASE_URL}/api/stores/{store["id"]}/emails',
        json={'email_account_id': account['id']},
    ).raise_for_status()
    init_email_db(account['id'])
    store_emails(
        account['id'],
        [
            {
                'message_id': f'msg-{TAG}@example.test',
                'folder': 'INBOX',
                'subject': f'smoke inbound {TAG}',
                'sender': 'partner@example.test',
                'date': datetime.now(UTC).isoformat(),
                'body_text': f'The confirmation code is {EMAIL_SECRET}.',
            }
        ],
    )
    _state_write(store_id=store['id'], store_name=f'smoke-api-{TAG}')

    entry = _run_task(
        client,
        'smoke: mailbox read, query, send',
        (
            'This store has a mailbox connected. Do exactly this, in order:\n'
            '1. Call the vibe_seller_email_info tool and report which '
            'mailbox is linked.\n'
            "2. Open that mailbox's database file (the db_path from step 1) "
            'with sqlite3 and find the newest inbox message; report its '
            'subject and the confirmation code in its body.\n'
            f'3. Send an email with vibe_seller_send_email to '
            f'"partner@example.test", subject "smoke-email-{TAG}", body '
            '"mailbox check passed".\n'
            'Do not open a browser. Finish by reporting the subject, the '
            'confirmation code, and whether the send succeeded.'
        ),
        store_id=store['id'],
    )
    messages = fake_smtp_server.read_messages(smtp_log)
    sent = [m for m in messages if f'smoke-email-{TAG}' in m.get('body', '')]
    entry.update({
        'assert_secret_in_result': EMAIL_SECRET in entry['result'],
        'smtp_messages': messages,
        'smtp_send_landed': bool(sent),
    })
    assert entry['assert_secret_in_result'], entry['result']
    assert sent, f'no message landed in the fake SMTP: {messages}'
    return entry


# ── item 2: workplace CLI (the WeCom stand-in) ─────────────────────


def _item_wecom(client: httpx.Client) -> dict:
    from tests.e2e import wecom_standin

    wecom_standin.install(_HOME)
    r = client.post(f'{h.BASE_URL}/api/integrations/cli/wecom/enable')
    assert r.status_code == 200, r.text

    entry = _run_task(
        client,
        'smoke: workplace CLI read commands',
        (
            'The wecom-cli command is installed and signed in. Do exactly '
            'this, using the Bash tool:\n'
            '1. Run `wecom-cli --version` and report its first line.\n'
            '2. Run `wecom-cli auth show` and report the status line.\n'
            '3. Run `wecom-cli mail list` and report what it returns.\n'
            'Do not sign in, do not run any other subcommand, and do not '
            'open a browser.'
        ),
    )
    calls = wecom_standin.calls(_HOME)
    entry.update({'cli_calls': calls})
    assert any('--version' in c for c in calls), calls
    assert any('auth' in c and 'show' in c for c in calls), calls
    assert any('mail' in c for c in calls), calls
    return entry


# ── item 3: custom MCP ─────────────────────────────────────────────


def _item_mcp(client: httpx.Client, mcp_server) -> dict:
    from tests.e2e import fake_mcp_server

    # custom MCP servers are server-global and PERSIST across runs on
    # a reused home, so the name carries this run's tag
    server_name = f'smoke-widgets-{TAG}'
    r = client.post(
        f'{h.BASE_URL}/api/custom-mcp-servers',
        json={
            'config': {
                'mcpServers': {
                    'widgets': fake_mcp_server.http_config(mcp_server)
                }
            },
            'name': server_name,
        },
    )
    assert r.status_code == 201, r.text
    probe = client.post(
        f'{h.BASE_URL}/api/custom-mcp-servers/{r.json()[0]["id"]}/test'
    ).json()
    assert probe['ok'], probe

    entry = _run_task(
        client,
        'smoke: custom MCP tool call',
        (
            f'A custom MCP server "{server_name}" is available to you. '
            f'Use its lookup_widget_code tool to find the internal catalog '
            f'code of the widget with SKU {MCP_SKU}, and report that code. '
            'Do not guess it and do not use a browser — only that tool '
            'knows it.'
        ),
    )
    calls = mcp_server.tool_calls()
    entry.update({
        'mcp_calls': [(c.get('params') or {}).get('arguments') for c in calls],
        'answer_in_result': MCP_ANSWER in entry['result'],
    })
    assert entry['answer_in_result'], entry['result']
    assert calls, 'the agent never called the custom MCP server'
    return entry


# ── item 4: ads ────────────────────────────────────────────────────


def _item_ads(client: httpx.Client, store_name: str) -> dict:
    # The deployment's authorization step: an admin uploads the stores
    # and the service records which are authorized (a store whose name
    # carries the fake service's mark). Without it the server's gate
    # refuses every call — the first run proved it: the agent, denied
    # its tool, went probing the server home and the checkout (both
    # correctly denied by the guards) before giving up.
    r = client.post(f'{h.BASE_URL}/api/ads/stores/sync')
    assert r.status_code == 200, r.text
    rows = r.json().get('stores', [])
    ours = next(row for row in rows if row.get('name') == store_name)
    assert ours.get('authorized'), f'store not authorized: {ours}'
    entry = _run_task(
        client,
        'smoke: one ads call',
        (
            'This store is authorized for the ads service. Make exactly '
            'ONE call with the vibe_seller_ads_call tool: '
            'path="/facts/rollup", store="' + store_name + '". Report '
            'the total spend figure it returns, then stop — no other '
            'calls, no browser, no exploring.'
        ),
        store_id=_state_read().get('store_id'),
    )
    entry['spend_in_result'] = ADS_SPEND in entry['result']
    assert entry['spend_in_result'], entry['result']
    return entry


# ── item 5: store browser with worker slots + download ─────────────


def _serve_site() -> tuple[int, str]:
    """A local site: a page with the marker + a CSV download link."""
    import http.server

    page = (
        '<!doctype html><html><body>'
        f'<h1>integration smoke</h1><p>{BROWSER_MARKER}</p>'
        f'<a href="/{CSV_NAME}">download the smoke CSV</a>'
        '</body></html>'
    ).encode()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 — stdlib interface
            if self.path == '/':
                body, ctype = page, 'text/html'
            elif self.path == f'/{CSV_NAME}':
                body, ctype = CSV_BODY.encode(), 'text/csv'
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header('Content-Type', ctype)
            self.send_header(
                'Content-Disposition',
                f'attachment; filename="{CSV_NAME}"'
                if self.path != '/'
                else 'inline',
            )
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    return port, page.decode()


def _item_browser(client: httpx.Client) -> dict:
    port, _ = _serve_site()
    store_id = _state_read().get('store_id')
    store_name = _state_read().get('store_name')
    entry = _run_task(
        client,
        'smoke: store browser with two worker slots + download',
        (
            "Open this store's browser and work with TWO parallel "
            'browser-use workers, exactly as the browser-harness skill '
            'describes:\n'
            f'1. Start `browser-use --worker 1` and have it navigate to '
            f'http://127.0.0.1:{port}/ — report the marker text on the '
            'page (it starts with SMOKE-MARKER).\n'
            f'2. In parallel, start `browser-use --worker 2` and have it '
            f'navigate to the same page and download the linked CSV file '
            '(the link text mentions "download").\n'
            '3. When both are done, report the marker text, then read the '
            "downloaded CSV file from the store's downloads directory and "
            'report its contents.'
        ),
        store_id=store_id,
    )
    downloads = _HOME / 'downloads' / (store_name or '')
    csv_path = downloads / CSV_NAME
    got_csv = (
        csv_path.is_file()
        and csv_path.read_text(encoding='utf-8', errors='replace').strip()
        == CSV_BODY.strip()
    )
    msgs = h.get_messages(client, entry['task_id'])
    workers_used = sorted({
        m.group(1)
        for m in (
            re.search(r'--worker (\d)', str(msg.get('content') or ''))
            for msg in msgs
        )
        if m
    })
    entry.update({
        'marker_in_result': BROWSER_MARKER in entry['result'],
        'downloaded_csv': str(csv_path),
        'csv_content_ok': got_csv,
        'worker_slots_used': workers_used,
    })
    assert entry['marker_in_result'], entry['result']
    assert got_csv, f'no intact CSV at {csv_path}'
    assert workers_used == ['1', '2'], workers_used
    return entry


# ── item 6: WAITING across a restart ───────────────────────────────


def _item_waiting_a(client: httpx.Client) -> dict:
    entry = _run_task(
        client,
        'smoke: wait for an external party',
        (
            'Answer with one short sentence acknowledging this task. Then '
            'end your final result with this wait-condition block, copied '
            'verbatim (including the fences):\n'
            '```wait-condition\n'
            'reason: waiting for the external partner to confirm the '
            'smoke\n'
            'check_strategy: manual\n'
            'max_wait_days: 1\n'
            '```'
        ),
        expect={'waiting'},
    )
    _state_write(waiting_task_id=entry['task_id'])
    return entry


def _item_waiting_b(client: httpx.Client) -> dict:
    task_id = _state_read()['waiting_task_id']
    task = h.get_task(client, task_id)
    assert task['status'] == 'waiting', (
        f'the WAITING task did not survive the restart: {task["status"]}'
    )
    r = client.post(
        f'{h.BASE_URL}/api/tasks/{task_id}/wake',
        json={
            'message': 'The partner confirmed. Finish the task with a '
            'one-line summary.'
        },
    )
    assert r.status_code == 200, r.text
    data = h.poll_task_status(
        client,
        task_id,
        {'completed'},
        fail_statuses={'failed', 'error', 'cancelled'},
        timeout=900,
    )
    return {
        'task_id': task_id,
        'status_after_restart': 'waiting',
        'status': data.get('status'),
        'result': (data.get('result') or '')[:500],
        'stray_processes': [p['pid'] for p in _task_procs(task_id)],
    }


# ── the profile ────────────────────────────────────────────────────


def _setup_profile(client: httpx.Client) -> None:
    api_key = os.environ.get('MINIMAX_API_KEY', '').strip()
    assert api_key, 'set MINIMAX_API_KEY (the smoke runs real tasks)'
    presets = h.fetch_presets(client)
    preset = presets.get('minimax')
    assert preset, f'no minimax preset on the server: {sorted(presets)}'
    env = dict(preset['env'])
    env['ANTHROPIC_AUTH_TOKEN'] = api_key
    for var, out in (
        ('MINIMAX_BASE_URL', 'ANTHROPIC_BASE_URL'),
        ('MINIMAX_MODEL', 'ANTHROPIC_MODEL'),
    ):
        val = os.environ.get(var, '').strip()
        if val:
            env[out] = val
    r = client.post(
        f'{h.BASE_URL}/api/profiles',
        json={
            'id': PROFILE_ID,
            'name': 'smoke minimax',
            'env': env,
        },
    )
    if r.status_code not in (200, 201):
        # a re-run against an existing home: the profile may already be
        # there — confirm via the list, else the create failure is real
        listed = client.get(f'{h.BASE_URL}/api/profiles').json()
        ids = {p.get('id') for p in listed}
        if PROFILE_ID not in ids:
            raise AssertionError(
                f'profile create failed: {r.status_code} {r.text}'
            )
        client.put(
            f'{h.BASE_URL}/api/profiles/{PROFILE_ID}', json={'env': env}
        ).raise_for_status()
    r = client.patch(f'{h.BASE_URL}/api/profiles/{PROFILE_ID}/set-default')
    assert r.status_code == 200, r.text
    _log(f'profile {PROFILE_ID} set as default')


# ── main ───────────────────────────────────────────────────────────


def main() -> int:
    # trust_env=False: this client talks ONLY to our own 127.0.0.1
    # server — a system proxy (Clash) on the box must not sit between
    # the driver and its own server (a 502 mid-run, observed)
    client = httpx.Client(timeout=60, trust_env=False)
    h.login_client(client, _ARGS.base_url)
    _verify_server_ownership()

    if _ARGS.phase == 'main':
        from tests.e2e import (
            fake_ads_service,
            fake_mcp_server,
            fake_smtp_server,
        )

        _setup_profile(client)

        # every fake in-process; the runner owns only the server
        smtp_port = fake_smtp_server.serve_sync(
            0, str(_OUT / 'fake-smtp.jsonl')
        )
        mcp = fake_mcp_server.serve()
        _FAKES.append(fake_ads_service.serve(_ARGS.ads_port))
        _log(
            f'fakes up: smtp:{smtp_port} mcp:{mcp.server_address[1]} '
            f'ads:{_ARGS.ads_port}'
        )

        _EVIDENCE['items']['email'] = _item_email(client, smtp_port)
        _save_evidence()
        _log('ITEM 1 email: PASS')

        _EVIDENCE['items']['wecom_cli'] = _item_wecom(client)
        _save_evidence()
        _log('ITEM 2 workplace CLI: PASS')

        _EVIDENCE['items']['custom_mcp'] = _item_mcp(client, mcp)
        _save_evidence()
        _log('ITEM 3 custom MCP: PASS')

        _EVIDENCE['items']['ads'] = _item_ads(
            client, _state_read()['store_name']
        )
        _save_evidence()
        _log('ITEM 4 ads: PASS')

        if _ARGS.with_browser:
            _EVIDENCE['items']['browser'] = _item_browser(client)
            _save_evidence()
            _log('ITEM 5 browser workers + download: PASS')
        else:
            _log('ITEM 5 skipped (--with-browser not given)')

        _EVIDENCE['items']['waiting_a'] = _item_waiting_a(client)
        _save_evidence()
        _log(
            'ITEM 6a WAITING reached: PASS — restart the server, then '
            'run --phase resume'
        )
    else:
        _EVIDENCE['items']['waiting_b'] = _item_waiting_b(client)
        _save_evidence()
        _log('ITEM 6b WAITING survived restart + resumed: PASS')

    failed = [
        k
        for k, v in _EVIDENCE['items'].items()
        if isinstance(v, dict)
        and v.get('status') not in ('completed', 'waiting')
    ]
    _log('SMOKE DONE' + (f' (failed: {failed})' if failed else ' — ALL PASS'))
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
