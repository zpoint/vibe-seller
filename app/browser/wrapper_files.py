"""Wrapper FILE maintenance: format versioning, removal, boot wipe.

Split out of :mod:`app.browser.wrapper` (generation) so each stays
focused; the generation module imports the version constants and the
legacy-dir cleanup from here, and the boot path
(:meth:`~app.browser.manager.BrowserManager.cleanup_stale_sessions`)
imports the wipe. No cycle: this module imports nothing from the
generator.
"""

import logging
from pathlib import Path
import re
import shutil

from app.config import BROWSER_USE_BIN_DIR, DOWNLOADS_DIR

logger = logging.getLogger(__name__)

# Directory for per-store browser-use wrapper scripts (shared with
# wrapper.py).
_BIN_DIR = BROWSER_USE_BIN_DIR

# Monotonic wrapper-format version. BUMP on any BREAKING change to the
# generated wrapper's contract (env vars, CLI shape, PATH assumptions).
#   1 = pre-0.13 (0.12 subcommand CLI: open/click/state)
#   2 = 0.13 heredoc + BU_CDP_WS env-injection (current)
# Boot cleanup (``wipe_generated_wrappers`` below) deletes wrappers
# with a version BELOW this and never touches equal-or-higher ones. So:
#   - a stale pre-0.13 wrapper (v1 / unmarked) is removed on upgrade;
#   - the current version's own wrappers SURVIVE a restart (no wrapper-less
#     window → the agent can't fall through to a local Chrome);
#   - a running vN never nukes a wrapper written by a newer vN+1
#     (rollback / mixed-process safety).
# See docs/browser-use-0.13-migration.md § wrapper-format versioning.
# v3: killed the Ziniao-aux "Chrome direct" exemption — aux now gets an
# explicit BU_CDP_WS on its own store's proxy (client-aux). The old
# no-endpoint aux fell back to browser_harness's ambient local-Chrome
# discovery, which attached to another store's browser (wrong Amazon
# account, wrong downloads dir) — observed live with two Ziniao stores.
# v4: Ziniao-aux is a DEDICATED login-less Chromium (per-store, lazy):
# the aux branch calls /browser/aux/start and attaches to the returned
# aux-proxy ws. Chrome-backend aux stays a client on the main proxy.
# v5: `--worker N` parallel slots. A subagent driving the browser
# CONCURRENTLY with its parent used to inherit VIBE_TASK_ID, land on the
# same daemon (browser_harness serves every socket connection in its own
# unguarded asyncio task) and drive the same single tab. The flag gives
# each concurrent caller its own daemon AND its own mux client on the
# same logged-in browser. Wrappers written by v4 don't know the flag.
# v6: escalating wedge recovery. v5 answered every 120s timeout with the
# same daemon reload, which cannot cure a browser that is itself wedged —
# so the caller saw an unbounded run of identical transient-looking
# errors and kept paying for them. v6 escalates to a forced browser
# recycle and then reports the browser as unrecoverable.
# v9: ownership-checked liveness (PR C1). The auto-start probe now
# requires THIS server instance's id from the proxy's /vibe/instance
# endpoint — a listener of any other server (or an agent-spawned CDP
# bridge) no longer reads as "proxy up", which is what cross-attached
# one server's agent onto another server's browser. The port became a
# runtime variable: after a start-API call the server may have
# REALLOCATED the port and rewritten this very file, and the wrapper
# re-reads its own port marker.
# v10: exports VIBE_STORE_DOWNLOADS — the store's download dir resolved
# for THIS server's VIBE_HOME (downloads PR). Skills reference it
# instead of spelling a default-home path (which a multi-home machine
# made wrong: an agent following the old doc wrote into another
# server's home). Additive, but bumped ABOVE v9 so a wrapper written by
# C1's v9 (no export) is reaped and regenerated instead of surviving
# without the export forever.
WRAPPER_FORMAT_VERSION = 10
WRAPPER_FORMAT_MARKER = 'vibe-seller-wrapper-format:'


def _cleanup_legacy_store_dirs(
    store_name: str,
    slug: str,
    bin_dir: Path = _BIN_DIR,
    downloads_dir: Path = DOWNLOADS_DIR,
) -> None:
    """Remove pre-slug-guard artifacts named after the raw store name.

    Before the slug gained its ASCII guard, non-ASCII store names
    produced wrapper/download dirs named after the raw name. The fixed
    slug regenerates everything under the id-fallback dir but the stale
    dirs survive — and mislead agents into watching an empty downloads
    dir while real downloads land in the slug dir. Delete them whenever
    the raw name differs from the active slug.

    The stale wrapper dir is only removed when its ``browser-use`` script
    carries our auto-generation header for this store, so a user-created
    dir that happens to share the name is never touched.
    """
    if store_name == slug:
        return
    if store_name in ('.', '..'):
        return  # would resolve to the base dir itself or its parent
    if store_name.isascii():
        # Only non-ASCII names ever had raw-name dirs. An ASCII one
        # ("1", "ACME") names some store's SLUG dir — on a case-blind
        # disk too — possibly ANOTHER store's, now that a store whose
        # readable slug is taken gets a different one.
        return
    legacy_bin = bin_dir / store_name
    if legacy_bin.parent != bin_dir:
        return  # name contains path separators — never traverse
    try:
        if legacy_bin.exists() and legacy_bin.samefile(bin_dir / slug):
            # Case-insensitive filesystem (macOS default): the "legacy"
            # dir IS the active slug dir — deleting it would destroy the
            # wrapper we just wrote.
            return
    except OSError:
        return
    legacy_wrapper = legacy_bin / 'browser-use'
    if legacy_wrapper.is_file():
        try:
            head = legacy_wrapper.read_text(errors='replace')[:512]
        except OSError:
            head = ''
        # The exact header line: a substring match took the wrapper of
        # "云帆科技二店" for one of "云帆科技".
        if f'wrapper for store: {store_name}\n' in head:
            shutil.rmtree(legacy_bin, ignore_errors=True)
            logger.info(
                'Removed stale pre-slug-guard wrapper dir: %s '
                '(active slug: %s)',
                legacy_bin,
                slug,
            )
    legacy_downloads = downloads_dir / store_name
    if legacy_downloads.is_dir():
        slug_downloads = downloads_dir / slug
        try:
            if slug_downloads.exists() and legacy_downloads.samefile(
                slug_downloads
            ):
                return  # case-insensitive FS — same dir as active slug
        except OSError:
            return
        try:
            legacy_downloads.rmdir()  # only succeeds when empty
            logger.info(
                'Removed stale empty downloads dir: %s (active slug: %s)',
                legacy_downloads,
                slug,
            )
        except OSError:
            pass  # non-empty — leave user files alone


def remove_browser_use_wrapper(slug: str):
    """Remove the per-store browser-use wrapper directory."""
    wrapper_dir = _BIN_DIR / slug
    if wrapper_dir.exists():
        shutil.rmtree(wrapper_dir)
        logger.info('Removed browser-use wrapper dir: %s', wrapper_dir)


def wipe_generated_wrappers(live_store_ids: set[str] | None = None) -> int:
    """Delete OUTDATED or ORPHANED auto-generated wrappers on boot.

    Two independent reasons to remove a wrapper:

    1. **Outdated format** (in-place-upgrade safety): a wrapper left by an
       OLDER version drives a stale CLI/env contract and would misbehave
       if invoked before the next task launch regenerates it. So we remove
       wrappers whose embedded format version is BELOW the current
       ``WRAPPER_FORMAT_VERSION`` (and unmarked/pre-versioning ones,
       treated as version 0).

    2. **Orphaned** — the store it was generated for no longer exists.
       Version alone never reaps these, so a deleted store's wrapper
       survived forever while holding a FROZEN ``proxy_port``. Ports are
       re-allocated from ``_BASE_PROXY_PORT`` every boot, so that number
       eventually lands on a *live* store; the orphan then finds the port
       already up, skips the start API (so its dead ``store_id`` never
       404s) and exports ``BU_CDP_WS`` straight at another store's
       browser — wrong account, wrong downloads dir. Same cross-store
       hole wrapper v3 closed for aux sessions (docs/browser.md).
       Pass ``live_store_ids`` to enable this check.

    We KEEP current-or-newer wrappers belonging to a live store: they
    survive a restart (no wrapper-less window → no local-Chrome fallback,
    see docs/ziniao-concurrency.md), and a running vN never deletes a
    newer vN+1's (rollback safety). User-created wrappers (no auto-gen
    header) are untouched, and so are non-store wrappers such as
    ``_web``/``_guard`` (no embedded store id — fail safe, keep).
    See docs/browser-use-0.13-migration.md.
    """
    removed = 0
    orphaned = 0
    if not _BIN_DIR.is_dir():
        return 0
    # An EMPTY live set is far more likely "wrong/unloaded DB" than
    # "the user deleted every store", and acting on it would delete the
    # wrappers of stores that are actively in use. Never orphan-reap on
    # no evidence — fall back to version-only.
    if live_store_ids is not None and not live_store_ids:
        live_store_ids = None
    for sub in _BIN_DIR.iterdir():
        wrapper = sub / 'browser-use'
        if not wrapper.is_file():
            continue
        try:
            text = wrapper.read_text(errors='replace')
        except OSError:
            continue
        head = text[:400]
        if 'Auto-generated browser-use wrapper' not in head:
            continue  # user-created wrapper — never touch
        m = re.search(rf'{re.escape(WRAPPER_FORMAT_MARKER)}\s*(\d+)', head)
        version = int(m.group(1)) if m else 0
        stale_format = version < WRAPPER_FORMAT_VERSION
        # Ownership: only judge a wrapper we can actually attribute to a
        # store. No parseable id (e.g. the store-less `_web` wrapper) →
        # keep, so a format change here can never orphan-reap everything.
        is_orphan = False
        if live_store_ids is not None:
            # Don't assume a UUID shape — match whatever the wrapper
            # actually embeds, so a non-UUID id is still attributable.
            owner = re.search(r'/api/stores/([^/"\s]+)/browser/', text)
            if owner and owner.group(1) not in live_store_ids:
                is_orphan = True
        if not stale_format and not is_orphan:
            continue
        try:
            wrapper.unlink()
            removed += 1
            if is_orphan:
                orphaned += 1
                # Drop the now-empty dir so `ls bin/` reflects reality.
                try:
                    sub.rmdir()
                except OSError:
                    pass
        except OSError:
            pass
    if removed:
        logger.info(
            'Boot: wiped %d browser-use wrapper(s) (%d outdated, '
            '%d orphaned — store no longer exists)',
            removed,
            removed - orphaned,
            orphaned,
        )
    return removed
