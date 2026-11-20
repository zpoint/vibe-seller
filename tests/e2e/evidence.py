"""Evidence an e2e run leaves behind for a reviewer: real screenshots,
task rows, transcripts, the SSE stream and the server log by task id.

Root: ``$E2E_EVIDENCE_DIR`` (outside the repo) or
``tests/e2e/artifacts/evidence``. One directory per test.
"""

from datetime import UTC, datetime
import json
import os
from pathlib import Path
import shutil
import sqlite3
import threading

import httpx

_ROW_FIELDS = (
    'id',
    'title',
    'status',
    'schedule_id',
    'is_plan_only',
    'session_id',
    'wait_condition',
    'result',
    'error',
    'error_category',
    'started_at',
    'completed_at',
    'updated_at',
)


def _root() -> Path:
    env = os.environ.get('E2E_EVIDENCE_DIR')
    if env:
        return Path(env)
    return Path(__file__).parent / 'artifacts' / 'evidence'


class Evidence:
    """Collects one test's evidence under ``<root>/<test_name>/``."""

    def __init__(self, test_name: str, home: Path | None = None):
        self.dir = _root() / test_name
        # One run per directory: a rerun replaces the last one.
        shutil.rmtree(self.dir, ignore_errors=True)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.home = Path(home) if home else None
        self._timeline = self.dir / 'timeline.jsonl'
        self._sse_stop: threading.Event | None = None
        self._sse_thread: threading.Thread | None = None
        self.sse_events: list[dict] = []

    def mark(self, event: str, **fields) -> None:
        line = {'at': datetime.now(UTC).isoformat(), 'event': event, **fields}
        with self._timeline.open('a') as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + '\n')

    def shot(self, page, name: str) -> Path:
        path = self.dir / f'{name}.png'
        page.screenshot(path=str(path), full_page=True)
        self.mark('screenshot', file=path.name)
        return path

    def task_row(self, task_id: str, label: str) -> dict:
        """The task's row, read-only, straight from the server's DB."""
        row: dict = {}
        if self.home is not None:
            db = self.home / 'data' / 'vibe_seller.db'
            con = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
            try:
                cur = con.execute(
                    f'SELECT {", ".join(_ROW_FIELDS)} FROM tasks WHERE id = ?',
                    (task_id,),
                )
                found = cur.fetchone()
                if found:
                    row = dict(zip(_ROW_FIELDS, found, strict=True))
            finally:
                con.close()
        path = self.dir / f'task-{label}.json'
        path.write_text(json.dumps(row, ensure_ascii=False, indent=2))
        self.mark('task_row', label=label, status=row.get('status'))
        return row

    def messages(
        self, client: httpx.Client, base_url: str, task_id: str, label: str
    ) -> list[dict]:
        msgs = client.get(f'{base_url}/api/tasks/{task_id}/messages').json()
        (self.dir / f'messages-{label}.json').write_text(
            json.dumps(msgs, ensure_ascii=False, indent=2)
        )
        return msgs

    def write_json(self, name: str, data) -> None:
        (self.dir / name).write_text(
            json.dumps(data, ensure_ascii=False, indent=2)
        )

    def start_sse(self, client: httpx.Client, base_url: str) -> None:
        """Record every SSE event the server sends, to ``sse.jsonl``."""
        stop = threading.Event()
        path = self.dir / 'sse.jsonl'
        cookies = dict(client.cookies)

        def run():
            with (
                httpx.Client(timeout=None, cookies=cookies) as c,
                c.stream('GET', f'{base_url}/api/sse') as resp,
                path.open('a') as fh,
            ):
                for line in resp.iter_lines():
                    if stop.is_set():
                        return
                    if not line.startswith('data:'):
                        continue
                    try:
                        data = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue
                    data['_received_at'] = datetime.now(UTC).isoformat()
                    self.sse_events.append(data)
                    fh.write(json.dumps(data, ensure_ascii=False) + '\n')
                    fh.flush()

        self._sse_stop = stop
        self._sse_thread = threading.Thread(target=run, daemon=True)
        self._sse_thread.start()

    def stop_sse(self) -> None:
        if self._sse_stop is not None:
            self._sse_stop.set()

    def server_log(self, log_path: Path, task_ids: dict[str, str]) -> None:
        """The server's whole log, plus one excerpt per task id."""
        if not log_path.exists():
            self.mark('server_log_missing', path=str(log_path))
            return
        text = log_path.read_text(errors='replace')
        (self.dir / log_path.name).write_text(text)
        for label, tid in task_ids.items():
            lines = [
                ln
                for ln in text.splitlines()
                if (tid in ln or tid[:8] in ln)
                and 'AGENT_DEBUG' not in ln
                and 'aiosqlite' not in ln
            ]
            (self.dir / f'server-{label}-{tid[:8]}.log').write_text(
                '\n'.join(lines) + '\n'
            )
