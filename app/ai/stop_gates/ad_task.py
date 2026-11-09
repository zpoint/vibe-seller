"""What the ad gates need to know about a task: its dir, parent, store.

Gates run synchronously at ``set_task_result``; these read the task row
through a read-only SQLite connection and never raise.
"""

from __future__ import annotations

from pathlib import Path
import sqlite3

from app.browser.manager import store_slug
from app.config import DATA_DIR, VIBE_SELLER_DIR


def task_dir(task_id: str) -> Path:
    return VIBE_SELLER_DIR / 'tasks' / task_id


def _query(sql: str, args: tuple) -> tuple | None:
    db = DATA_DIR / 'vibe_seller.db'
    if not db.exists():
        return None
    try:
        con = sqlite3.connect(f'file:{db}?mode=ro', uri=True, timeout=2)
        try:
            return con.execute(sql, args).fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return None


def parent_task_id(task_id: str) -> str | None:
    row = _query('SELECT parent_task_id FROM tasks WHERE id = ?', (task_id,))
    return row[0] if row and row[0] else None


def store_slug_for(task_id: str) -> str | None:
    """The task's store slug, or None for a store-less task."""
    row = _query(
        'SELECT s.name, s.id FROM tasks t JOIN stores s ON s.id = t.store_id '
        'WHERE t.id = ?',
        (task_id,),
    )
    return store_slug(row[0], row[1]) if row else None


def task_started_at(task_id: str) -> str | None:
    """ISO ``created_at`` of the task row, or None."""
    row = _query('SELECT created_at FROM tasks WHERE id = ?', (task_id,))
    return row[0] if row and row[0] else None
