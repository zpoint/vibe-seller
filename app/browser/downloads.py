"""The store download dir, emptied at browser start so "newest" == "only".

``~/.vibe-seller/downloads/<slug>/`` used to accumulate every file the
store had ever downloaded, and the platform filenames carry no month:

    Aug 3 22:35  2026JulMonthlyTransaction.csv                 <- this run
    Jul 30 17:56 fbn_finance_monthlystoragechargereport.csv    <- LAST month
    Jul 30 17:56 fbn_finance_longtermstoragechargereport.csv   <- LAST month

A month-old report sits under *exactly* the name this run's download will
have. An agent that picks "the newest ``fbn_finance_*``" is fine; one
that globs by name is not, and nothing downstream can tell the
difference — the same silent-wrong-data class as the transaction CSV
that spent a month holding another marketplace's numbers. It also fools
humans: a ``grep -c`` over that directory reported "12 noon files
landed" during a run that had produced none.

**Why archive at BROWSER start rather than task start.** The obvious fix
— a per-task download dir — is not reachable from here.
``Browser.setDownloadBehavior`` is browser-WIDE and last-writer-wins
(see ``cdp_mux_proxy``), and per-store browsers deliberately serve
concurrent tasks, so two tasks setting different download paths would
mean one task's file landing in the other's directory. Sweeping at task
start has the same flaw from the other side: it would delete a
concurrent task's in-flight download.

Browser start has neither problem. It runs under ``BrowserManager``'s
global lock and only when the store has no session, so no CDP client
exists and nothing can be mid-download. And it is the right granularity
for the bug actually reported: the collisions that mattered were a month
apart, and no browser session lives that long. Within one session the
skills already require renaming each file immediately after download,
because names collide there too.

**Archived, never deleted.** These are a seller's financial reports, and
a retry that rmtree'd a task workspace once destroyed exactly this kind
of collected output. Prior contents move to ``_prev/<timestamp>/``; the
directory grows, which is the correct trade against silently discarding
someone's downloads.
"""

from __future__ import annotations

from datetime import UTC, datetime
import logging
from pathlib import Path
import shutil

from app.config import DOWNLOADS_DIR

logger = logging.getLogger(__name__)

# Where prior sessions' files go. Excluded from the sweep so archives
# never nest inside each other.
ARCHIVE_NAME = '_prev'


def prepare_download_dir(slug: str) -> Path:
    """Return the store's download dir, with prior contents archived."""
    return archive_previous(DOWNLOADS_DIR / slug)


def archive_previous(dl_dir: Path) -> Path:
    """Empty *dl_dir* into ``_prev/<timestamp>/`` and return it.

    Takes the directory rather than a slug because the winchrome backend
    keeps the real directory on the Windows side and only symlinks
    ``downloads/<slug>`` to it — archiving through the symlink would
    write the archive into a path the agent sees but Chrome does not.

    Best-effort: a directory that cannot be archived is still returned,
    because failing to start a browser over a housekeeping problem is
    strictly worse than the stale-file hazard this avoids.
    """
    dl_dir.mkdir(parents=True, exist_ok=True)
    try:
        stale = [p for p in dl_dir.iterdir() if p.name != ARCHIVE_NAME]
        if not stale:
            return dl_dir
        stamp = datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')
        dest = dl_dir / ARCHIVE_NAME / stamp
        dest.mkdir(parents=True, exist_ok=True)
        for p in stale:
            # str(): shutil.move's Path handling differs across versions
            # for the dir-into-dir case.
            shutil.move(str(p), str(dest / p.name))
        logger.info(
            'Archived %d file(s) from a previous session to %s',
            len(stale),
            dest,
        )
    except OSError:
        logger.warning(
            'Could not archive previous downloads in %s; a stale file may '
            'still shadow this run',
            dl_dir,
            exc_info=True,
        )
    return dl_dir
