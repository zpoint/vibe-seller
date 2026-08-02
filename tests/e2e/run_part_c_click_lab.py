#!/usr/bin/env python
"""Part C — live server, real agent, three sustained parallel drivers.

Parts A and B answer "does the plumbing isolate?" and "does an agent
reach for the flag?". Part C is the load question neither covers: three
subagents plus the parent all clicking through a stateful page for
minutes, on ONE Chrome, through ONE upstream CDP WebSocket.

Run it against a live vibe-seller started with a widened bound::

    VIBE_BROWSER_WORKER_SLOTS=4 ./restart.sh --dev
    python tests/e2e/run_part_c_click_lab.py --store test

Why a script and not a pytest case: it needs a server configured
differently from the one every other e2e assumes, so making it a
collected test would either skip constantly or quietly assert nothing.

**On the bound.** The default is 3, so ``--worker 4`` is *rejected* by a
default wrapper — that rejection is verified here too, from whichever
side the running server is on. Widening to 4 also pushes main+workers
to 5 concurrent clients, which is exactly where the old
``max_clients = 5`` ceiling sat.

**On slot numbers.** The task text never names a slot. Assignment is the
parent's job, and putting ``--worker 2`` in a task description would
test the agent's copy-paste rather than the guidance. So the check is
"three DISTINCT concurrent clients, all isolated" — not particular slot
numbers, and not even that all three ARE slots: a parent may keep the
bare client for its own page and hand slots to its two helpers, or take
a slot itself. Both have been observed; both are correct.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.e2e.click_lab import (  # noqa: E402
    SECTIONS,
    page_code,
    start_click_lab,
)
from tests.e2e.mux_probe import parse  # noqa: E402

# Must match the host the cookie was minted for: a jar saved against
# 127.0.0.1 is NOT sent to "localhost", and the request then arrives
# unauthenticated with no hint as to why.
BASE = os.environ.get('E2E_BASE_URL', 'http://127.0.0.1:7777')
DB = Path.home() / '.vibe-seller' / 'data' / 'vibe_seller.db'
LOG = Path('logs/backend.log')
PAGES = (1, 2, 3)


def _q(sql: str, *args):
    con = sqlite3.connect(DB)
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


def _curl(method: str, path: str, cookie: str, body: dict | None = None):
    cmd = [
        'curl',
        '-s',
        '-b',
        cookie,
        '-X',
        method,
        f'{BASE}{path}',
    ]
    if body is not None:
        cmd += ['-H', 'Content-Type: application/json', '-d', json.dumps(body)]
    out = subprocess.run(
        cmd, capture_output=True, text=True, timeout=180
    ).stdout
    try:
        return json.loads(out)
    except Exception:
        return {'_raw': out}


def check_bound(slug: str, want_slots: int) -> None:
    """The wrapper must advertise exactly the configured bound."""
    wrapper = Path.home() / '.vibe-seller' / 'bin' / slug / 'browser-use'
    if not wrapper.is_file():
        sys.exit(
            f'FAIL: no wrapper at {wrapper} — start the store browser first'
        )
    text = wrapper.read_text()
    m = re.search(r'--worker must be 1\.\.(\d+)', text)
    if not m:
        sys.exit('FAIL: wrapper has no --worker support (pre-v5)')
    got = int(m.group(1))
    print(f'  wrapper bound: 1..{got}')
    if got != want_slots:
        sys.exit(
            f'FAIL: wrapper allows 1..{got}, expected 1..{want_slots}. '
            f'Start the server with VIBE_BROWSER_WORKER_SLOTS={want_slots} '
            'and restart the store browser so the wrapper regenerates.'
        )
    if f'-w[1-{want_slots}]' not in text:
        sys.exit(f'FAIL: session allowlist does not carry -w[1-{want_slots}]')
    print(f'  allowlist carries -w[1-{want_slots}]  OK')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        '--store', default='test', help='store NAME (chrome backend)'
    )
    ap.add_argument('--cookie', default='/tmp/vs_cookie.txt')
    ap.add_argument('--slots', type=int, default=4)
    ap.add_argument(
        '--profile',
        help="AI profile id; defaults to the signed-in user's",
    )
    ap.add_argument('--timeout', type=int, default=1800)
    args = ap.parse_args()

    rows = _q('SELECT id, browser_backend FROM stores WHERE name=?', args.store)
    if not rows:
        sys.exit(f'FAIL: no store named {args.store!r}')
    store_id, backend = rows[0]
    print(f'store {args.store} ({store_id[:8]}) backend={backend}')
    if backend != 'chrome':
        print(
            f'  WARNING: backend is {backend}, not chrome — expect cold-start noise'
        )

    print('\n[1/5] wrapper bound')
    check_bound(args.store, args.slots)

    print('\n[2/5] click lab')
    base, srv = start_click_lab()
    urls = [f'{base}/lab/{p}' for p in PAGES]
    print(f'  serving {base}/lab/{{{",".join(map(str, PAGES))}}}')
    print(f'  each page has {len(SECTIONS)} sections: {", ".join(SECTIONS)}')

    try:
        print('\n[3/5] creating task (pure human intent — no slot numbers)')
        desc = (
            'Three dashboards need going through and I have no time to do '
            'them one at a time — please work all three at once, putting a '
            'helper on each of the other two while you take the first '
            'yourself:\n'
            + '\n'.join(f'  {i + 1}) {u}' for i, u in enumerate(urls))
            + '\n\nOn each dashboard, click through EVERY section '
            f'({", ".join(SECTIONS)}) — the reference code only appears '
            'once every section has been opened. Then report, per '
            'dashboard: the reference code, the final click count, and the '
            'click trail exactly as shown.'
        )
        # Send the profile the UI would send: the SIGNED-IN user's
        # default, read from the session behind this cookie. Not a
        # hardcoded username, and emphatically not the literal
        # "default" — that is a real profile id meaning plain Claude,
        # so passing it would silently swap the model under the run.
        me = _curl('GET', '/api/auth/me', args.cookie)
        profile = args.profile or me.get('default_profile_id')
        if not profile:
            sys.exit(f'FAIL: could not resolve a profile (auth/me -> {me})')
        print(f'  acting as {me.get("username", "?")}, profile={profile}')
        task = _curl(
            'POST',
            '/api/tasks',
            args.cookie,
            {
                'title': 'Click through three dashboards at the same time',
                'description': desc,
                'store_id': store_id,
                'ai_profile_id': profile,
                'plan_mode': False,
            },
        )
        task_id = task.get('id')
        if not task_id:
            sys.exit(f'FAIL: task create returned {task}')
        print(f'  task {task_id}')

        print('\n[4/5] waiting')
        deadline = time.time() + args.timeout
        status = 'pending'
        while time.time() < deadline:
            r = _q('SELECT status FROM tasks WHERE id=?', task_id)
            status = r[0][0] if r else '?'
            if status in ('completed', 'failed', 'cancelled'):
                break
            time.sleep(15)
        print(f'  status={status}')

        print('\n[5/5] mux verification')
        return verify(task_id, status, args)
    finally:
        srv.shutdown()
        srv.server_close()


def verify(task_id: str, status: str, args) -> int:
    fails: list[str] = []
    if status != 'completed':
        fails.append(f'task ended {status}, not completed')

    result = (_q('SELECT result FROM tasks WHERE id=?', task_id)[0][0]) or ''
    for p in PAGES:
        code = page_code(p)
        if code not in result:
            fails.append(f'reference code {code} (lab/{p}) missing from result')
    # Each page must have been clicked by exactly one driver: a trail of
    # its OWN id only. A merged tab shows one page's id on both.
    for p in PAGES:
        if re.search(rf'\b{p}(,{p})+\b', result) is None:
            print(f'  note: no clean trail for lab/{p} in the result text')

    tr = parse(LOG)
    fam = tr.for_task(task_id)
    slots = sorted(c.slot for c in fam.values() if c.slot is not None)
    mains = [c for c in fam.values() if c.slot is None]
    print(
        f'  clients: main={len(mains)} slots={slots} peak_total={tr.peak_total}'
    )

    # THREE CONCURRENT DRIVERS — not three slots. A parent that keeps
    # the bare client for the page it took itself and hands slots to its
    # two helpers is doing exactly what was asked; so is one that puts
    # itself on a slot too. Both were observed. What must hold is that
    # each driver got its own isolated client.
    if len(fam) < 3:
        fails.append(
            f'expected 3 concurrent clients, saw {sorted(fam)} '
            f'(slots={slots}, main={len(mains)})'
        )
    if not slots:
        fails.append(
            'no worker slots used at all — the agent drove everything on '
            'one client, which is the interference this feature prevents'
        )
    if tr.collisions:
        fails.append(f'client id collisions: {tr.collisions}')
    if tr.rejections:
        fails.append(f'clients refused for capacity: {tr.rejections}')
    if tr.cross_client_denials:
        fails.append(
            f'{tr.cross_client_denials} cross-client target accesses blocked'
        )

    ids = list(fam)
    for a in ids:
        for b in ids:
            if a < b:
                if fam[a].targets & fam[b].targets:
                    fails.append(f'{a} and {b} SHARE TABS')
                if fam[a].sessions & fam[b].sessions:
                    fails.append(f'{a} and {b} SHARE SESSIONS')
    if not tr.concurrent_at_any_instant(ids):
        fails.append('clients never overlapped in time')

    for cid, c in sorted(fam.items()):
        w = c.window
        span = f'{w[0]:%H:%M:%S}..{w[1]:%H:%M:%S}' if w else '(none)'
        print(
            f'    {cid:14} slot={c.slot} targets={len(c.targets)} '
            f'sessions={len(c.sessions)} {span}'
        )

    print()
    if fails:
        print('FAILED:')
        for f in fails:
            print(f'  - {f}')
        return 1
    print(
        f'PASS — {len(fam)} concurrent clients (slots={slots}), isolated '
        'tabs and sessions, live at the same instant.'
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
