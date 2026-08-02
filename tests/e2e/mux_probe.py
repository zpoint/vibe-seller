"""Read the CDP mux's behaviour back out of the server log.

A "hook" on a proxy that lives inside the running FastAPI process: the
tests here drive real agents against a real server, so the proxy object
is out of reach and its log is the only observation point.

This is only usable because ``short_cid`` keeps the ``-wN`` suffix
(``app/browser/cdp_mux_types.py``). Ownership lines truncate the client
id, and every slot of one task shares the task uuid's first characters —
head-truncation alone renders a parent and all its workers as one
string, which is precisely when you need them apart.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import re

from app.browser.cdp_mux_types import short_cid

# 2026-08-01 18:39:26,749 app.browser.cdp_mux_routing INFO <msg>
_LINE = re.compile(
    r'^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),(\d{3}) .*? (?:INFO|DEBUG|WARNING|ERROR) (.*)$'
)
_CONNECT = re.compile(r'CDPMuxProxy client connected: (\S+) \(total: (\d+)\)')
_DISCONNECT = re.compile(r'CDPMuxProxy client (\S+) disconnected')
_SESSION = re.compile(r'Session ([0-9A-Fa-f]+) -> client (\S+) \(auto-attach\)')
_TARGET = re.compile(r'CDP target (\S+) -> client (\S+)')
_COLLISION = re.compile(r'client id collision: (\S+)')
_REJECT = re.compile(r'CDPMuxProxy rejecting client (\S+)')
_CROSS = re.compile(r'Target owned by another client')


@dataclass
class ClientTrace:
    # The CANONICAL key: always the `short_cid` rendering. One client
    # shows up under two spellings in the log — full id on
    # connect/disconnect, short id on ownership lines — so everything is
    # folded onto the short form. Without that, a client's tabs land on
    # a different dict entry than its connect, every disjointness check
    # compares two halves of the SAME client and trivially passes, and
    # the test reports isolation it never observed.
    client_id: str
    full_id: str | None = None
    connects: list[datetime] = field(default_factory=list)
    disconnects: list[datetime] = field(default_factory=list)
    targets: set[str] = field(default_factory=set)
    sessions: set[str] = field(default_factory=set)
    events: list[datetime] = field(default_factory=list)

    @property
    def slot(self) -> int | None:
        """The ``-wN`` slot number, or ``None`` for a main client."""
        head, sep, tail = self.client_id.rpartition('-')
        if sep and tail.startswith('w') and tail[1:].isdigit():
            return int(tail[1:])
        return None

    @property
    def window(self) -> tuple[datetime, datetime] | None:
        if not self.events:
            return None
        return min(self.events), max(self.events)


@dataclass
class MuxTrace:
    clients: dict[str, ClientTrace] = field(default_factory=dict)
    collisions: list[str] = field(default_factory=list)
    rejections: list[str] = field(default_factory=list)
    cross_client_denials: int = 0
    peak_total: int = 0

    def for_task(self, task_id: str) -> dict[str, ClientTrace]:
        """Clients belonging to one task: the main one and its slots.

        Keys are canonical (``short_cid``), so this matches the task's
        head and any ``head-wN``.
        """
        head = short_cid(task_id)
        return {
            cid: c
            for cid, c in self.clients.items()
            if cid == head or re.fullmatch(rf'{re.escape(head)}-w\d+', cid)
        }

    def concurrent_at_any_instant(self, ids: list[str]) -> bool:
        """True if every id in ``ids`` had activity inside one window.

        Uses the tightest overlap of their activity spans, which is the
        strongest statement the log supports: connect/disconnect alone
        would count a client that connected and went idle.
        """
        wins = [self.clients[i].window for i in ids if i in self.clients]
        if len(wins) != len(ids) or any(w is None for w in wins):
            return False
        return max(w[0] for w in wins) <= min(w[1] for w in wins)


def _ts(day: str, ms: str) -> datetime:
    return datetime.strptime(f'{day}.{ms}', '%Y-%m-%d %H:%M:%S.%f')


def parse(log_path: Path, since: datetime | None = None) -> MuxTrace:
    """Parse mux activity out of a backend log.

    ``since`` skips prior runs — backend.log is appended across
    restarts, and a stale client id from an earlier task would
    otherwise look live.
    """
    tr = MuxTrace()

    def get(raw: str) -> ClientTrace:
        """Fold a log-rendered id onto its canonical short form."""
        key = short_cid(raw)
        c = tr.clients.setdefault(key, ClientTrace(client_id=key))
        if len(raw) > len(key):
            c.full_id = raw
        return c

    with open(log_path, errors='replace') as fh:
        for raw in fh:
            m = _LINE.match(raw)
            if not m:
                continue
            when = _ts(m.group(1), m.group(2))
            if since and when < since:
                continue
            body = m.group(3)
            if c := _CONNECT.search(body):
                t = get(c.group(1))
                t.connects.append(when)
                t.events.append(when)
                tr.peak_total = max(tr.peak_total, int(c.group(2)))
            elif c := _DISCONNECT.search(body):
                t = get(c.group(1))
                t.disconnects.append(when)
                t.events.append(when)
            elif c := _SESSION.search(body):
                t = get(c.group(2))
                t.sessions.add(c.group(1))
                t.events.append(when)
            elif c := _TARGET.search(body):
                t = get(c.group(2))
                t.targets.add(c.group(1))
                t.events.append(when)
            elif c := _COLLISION.search(body):
                tr.collisions.append(c.group(1))
            elif c := _REJECT.search(body):
                tr.rejections.append(c.group(1))
            elif _CROSS.search(body):
                tr.cross_client_denials += 1
    return tr


def assert_slots_isolated(
    tr: MuxTrace, task_id: str, expected_slots: set[int]
) -> dict[str, ClientTrace]:
    """Assert one task's main client and slots ran isolated and live.

    Returns the traces so a caller can make further, test-specific
    assertions (e.g. joining agent timestamps onto client windows).
    """
    fam = tr.for_task(task_id)
    slots = {c.slot for c in fam.values() if c.slot is not None}
    assert expected_slots <= slots, (
        f'expected worker slots {sorted(expected_slots)}, saw '
        f'{sorted(slots)} for task {task_id[:8]} — if this is empty the '
        'agent never used --worker, which means the prompt/skill '
        'guidance regressed, not the plumbing'
    )
    mains = [c for c in fam.values() if c.slot is None]
    assert mains, f'no main client for task {task_id[:8]}'

    # Guard against a FALSE pass on a pre-`short_cid` log. Those logs
    # truncate ownership lines to the bare head, so every worker's tabs
    # were recorded against the main client: the slot looks tab-less and
    # the disjointness checks below hold vacuously. A slot that opened
    # CDP sessions but owns no target is that signature.
    blind = [
        c
        for c in fam.values()
        if c.slot is not None and c.sessions and not c.targets
    ]
    if blind and any(m.targets for m in mains):
        raise AssertionError(
            f'slot client(s) {[c.client_id for c in blind]} show sessions '
            'but zero targets while the main client owns '
            f'{sum(len(m.targets) for m in mains)} — this log predates the '
            'short_cid fix, so target ownership is attributed to the wrong '
            'client and isolation CANNOT be verified from it. Restart the '
            'server on current code and re-run.'
        )

    assert not tr.collisions, f'client id collisions: {tr.collisions}'
    assert not tr.rejections, f'clients refused (capacity): {tr.rejections}'
    assert tr.cross_client_denials == 0, (
        f'{tr.cross_client_denials} cross-client target accesses were '
        'blocked — a driver reached for a tab it does not own'
    )

    ids = list(fam)
    for a in ids:
        for b in ids:
            if a < b:
                shared_t = fam[a].targets & fam[b].targets
                shared_s = fam[a].sessions & fam[b].sessions
                assert not shared_t, f'{a} and {b} share tabs {shared_t}'
                assert not shared_s, f'{a} and {b} share sessions {shared_s}'
    return fam
