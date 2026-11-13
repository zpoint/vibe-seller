import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { GUIDE_SEEN_KEY } from './useOnboardingGuide'
import type {
  AuthUser,
  ReleaseNotesRange,
  UpdateCheckResult,
  UpdateStatus,
} from '../types'

const DISMISSED_KEY = 'vibe-seller:update-dismissed-version'
/** Which (from → to) pair the "已更新到" panel has already shown. It
 *  has to outlive the reload the update itself causes AND every tab
 *  opened after it — the record it is keyed on stays until the next
 *  update, so a per-tab store would show it again in each new tab.
 *  So this is localStorage, unlike the per-session dismissal. */
const APPLIED_KEY = 'vibe-seller:update-applied-shown'

type Store = 'session' | 'local'

function storage(store: Store): Storage {
  return store === 'local' ? localStorage : sessionStorage
}

function readStored(key: string, store: Store = 'session'): string | null {
  try {
    return storage(store).getItem(key)
  } catch {
    // Storage blocked (private mode, cleared site data): fall back to
    // the in-memory state, which still keeps one page load quiet.
    return null
  }
}

function writeStored(
  key: string,
  value: string,
  store: Store = 'session'
): void {
  try {
    storage(store).setItem(key, value)
  } catch {
    /* same: once per page load is the best storage can do */
  }
}

/**
 * The whole in-app update UI state for one logged-in session:
 *
 * - fetches `/api/system/update-check` once per login (what exists /
 *   is new / merged notes), then
 * - polls `/api/system/update-status` (the background download's
 *   state machine) fast while it moves and slowly otherwise;
 * - shows the modal automatically only when the server says it is
 *   worth asking — `popup_due`. That flag is the whole difference
 *   between the two apply paths: a release that changes the bundled
 *   runtime pops as soon as it is staged (nothing else will apply it),
 *   while a package-only release stays quiet on the sidebar because
 *   the server restarts onto it by itself the moment it is idle, and a
 *   popup would be a question nobody needs to answer. It pops that one
 *   after a day of the machine never being idle, and the timer for it
 *   lives server-side (`app/update_idle.py`) because it has to outlast
 *   the process that started it — nothing here keeps a clock. The chip
 *   is visible either way, and a chip click opens the modal regardless
 *   of the flag. "稍后" hides it for this browser session only
 *   (sessionStorage), per version.
 * - never auto-pops while the first-run guide is still un-seen (a
 *   fresh install gets the guide first; the update pops once the
 *   guide is closed — the chip stays visible meanwhile).
 * - after an update lands, fetches the merged notes for the range just
 *   crossed (`/api/system/release-notes?since=&until=`) and exposes
 *   them once per (from → to) pair — `/update-check` cannot produce
 *   that list, since it only describes releases newer than the version
 *   answering.
 * - reloads the page when a poll shows the server running a DIFFERENT
 *   version than the one this tab first saw: any other open tab
 *   (another admin, a member) picks up the restarted server's new
 *   frontend the same way the restarting tab does.
 * - if an apply fails (the poll reports `error` while the overlay is
 *   up), the overlay is dropped and the error is shown — a failed
 *   upgrade must not look like a three-minute hang. The record of what
 *   the last apply did (`status.last_update`) is what the chip and the
 *   modal report a rollback from; it needs no state here.
 * - `restart()` POSTs the apply endpoint and then watches
 *   `/api/version` until the server answers on the new version, at
 *   which point the page reloads itself. While the server is down the
 *   fetches simply fail — tolerated. After a platform-specific
 *   timeout (Windows 10 min, macOS/Linux 3 min) `restartTimedOut`
 *   flips and the overlay shows the manual fallback.
 */
export function useUpdateFlow(currentUser: AuthUser | null) {
  const [updateCheck, setUpdateCheck] = useState<UpdateCheckResult | null>(null)
  const [polled, setPolled] = useState<UpdateStatus | null>(null)
  const [userOpen, setUserOpen] = useState(false)
  const [applying, setApplying] = useState(false)
  const [restartError, setRestartError] = useState<string | null>(null)
  const [restartTimedOut, setRestartTimedOut] = useState(false)
  const [appliedNotes, setAppliedNotes] = useState<ReleaseNotesRange | null>(
    null
  )
  const [dismissedVersion, setDismissedVersion] = useState<string | null>(() =>
    readStored(DISMISSED_KEY)
  )
  const applyingRef = useRef({ from: '', platform: 'linux' as string })
  const applyingNowRef = useRef(false)
  const firstVersionRef = useRef<string | null>(null)
  const lastPolledKeyRef = useRef('')
  const appliedFetchRef = useRef<string | null>(null)
  // Is this page load still showing the flow? A one-shot request that
  // lands after unmount has nowhere left to put its answer.
  const mountedRef = useRef(true)
  const [guideSeen, setGuideSeen] = useState(() => {
    try {
      return !!localStorage.getItem(GUIDE_SEEN_KEY)
    } catch {
      // Storage broken: the guide itself shows once per load; do not
      // let it block update popups forever.
      return true
    }
  })

  // The guide writes its seen-key when it closes; re-check on a short
  // interval only while it is pending (a fresh install, transient).
  useEffect(() => {
    if (guideSeen) return
    const iv = window.setInterval(() => {
      let seen = false
      try {
        seen = !!localStorage.getItem(GUIDE_SEEN_KEY)
      } catch {
        seen = true
      }
      if (seen) setGuideSeen(true)
    }, 500)
    return () => window.clearInterval(iv)
  }, [guideSeen])

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  useEffect(() => {
    if (!currentUser) return
    fetch('/api/system/update-check', { credentials: 'include' })
      .then(r => (r.ok ? r.json() : null))
      .then((result: UpdateCheckResult | null) => {
        if (result) setUpdateCheck(result)
      })
      .catch(() => {})
  }, [currentUser])

  // ── update-status polling ──────────────────────────
  useEffect(() => {
    if (!currentUser) return
    let stop = false
    let timer: number | undefined
    const stateRef = { current: '' }
    const tick = async () => {
      try {
        const r = await fetch('/api/system/update-status', {
          credentials: 'include',
        })
        if (r.ok) {
          const s = (await r.json()) as UpdateStatus
          if (!stop) {
            // An unchanged payload (the idle steady state polls every
            // 10 s) must not re-render the app — fewer renders, and
            // the render-count harness tests around the composer
            // stay clean.
            const key = JSON.stringify(s)
            if (key !== lastPolledKeyRef.current) {
              lastPolledKeyRef.current = key
              setPolled(s)
            }
            if (firstVersionRef.current === null) {
              firstVersionRef.current = s.current_version
            } else if (s.current_version !== firstVersionRef.current) {
              window.location.reload()
              return
            }
            // Review SHOULD 6: a failed apply leaves the server up
            // reporting error — leave the overlay and show it.
            if (applyingNowRef.current && s.state === 'error') {
              setApplying(false)
              // Keep the modal up — it is now showing the error and
              // the manual fallback.
              setUserOpen(true)
            }
            stateRef.current = s.state
          }
        }
      } catch {
        /* server briefly down (restart) or unreachable — keep the last
           state; retry on the next tick */
      }
      if (!stop) {
        const active =
          applying ||
          stateRef.current === 'downloading' ||
          stateRef.current === 'restarting'
        timer = window.setTimeout(tick, active ? 2000 : 10000)
      }
    }
    void tick()
    return () => {
      stop = true
      if (timer) window.clearTimeout(timer)
    }
    // Re-run when the restart starts: the poll must go fast so a
    // failed apply is visible within seconds, not one slow interval.
  }, [currentUser, applying])

  // While the restart is running the server cannot answer; the UI
  // stays on the overlay even if a late poll disagrees.
  const status: UpdateStatus | null = useMemo(
    () =>
      applying && polled ? { ...polled, state: 'restarting' as const } : polled,
    [applying, polled]
  )

  useEffect(() => {
    applyingNowRef.current = applying
  }, [applying])

  const supported = status?.supported === true
  const readyVisible =
    !!currentUser &&
    guideSeen &&
    supported &&
    !applying &&
    status?.state === 'ready' &&
    !!status?.version &&
    status.popup_due &&
    dismissedVersion !== status?.version
  const manualVisible =
    !!currentUser &&
    guideSeen &&
    !supported &&
    !!updateCheck?.update_available &&
    updateCheck?.in_app_update === false &&
    dismissedVersion !== updateCheck?.latest_version
  const open = userOpen || readyVisible || manualVisible || applying

  // ── notes for the range the last apply crossed ───────────
  const appliedKey =
    status?.last_update && status.last_update.status === 'ok'
      ? `${status.last_update.from}->${status.last_update.to}`
      : null

  useEffect(() => {
    const last = status?.last_update
    // Not while the guide is up: this panel is the guide's screen to
    // have first, and burning the pair before it was ever seen would
    // lose it.
    if (!guideSeen || !appliedKey || !last) return
    if (readStored(APPLIED_KEY, 'local') === appliedKey) return
    if (appliedFetchRef.current === appliedKey) return
    appliedFetchRef.current = appliedKey
    const query =
      `since=${encodeURIComponent(last.from)}` +
      `&until=${encodeURIComponent(last.to)}`
    // No cancellation on cleanup: a poll that happens to change while
    // this is in flight re-runs the effect, and the answer that is
    // already on its way is still the one to show.
    const unspent = () => {
      if (appliedFetchRef.current === appliedKey) {
        appliedFetchRef.current = null
      }
    }
    fetch(`/api/system/release-notes?${query}`, { credentials: 'include' })
      .then(r => (r.ok ? (r.json() as Promise<ReleaseNotesRange>) : null))
      .then(range => {
        if (!mountedRef.current) return
        if (!range) {
          // A silence is not an answer: leave the pair unspent, so the
          // next status change asks again.
          unspent()
          return
        }
        writeStored(APPLIED_KEY, appliedKey, 'local')
        setAppliedNotes(range)
      })
      .catch(unspent)
  }, [guideSeen, appliedKey, status])

  const openModal = useCallback(() => {
    setRestartError(null)
    setUserOpen(true)
  }, [])

  const closeModal = useCallback(() => {
    setUserOpen(false)
    // "稍后" / "知道了": keep the modal from auto-popping again for
    // this version in this browser session.
    const v = status?.version ?? updateCheck?.latest_version
    if (v) {
      setDismissedVersion(v)
      writeStored(DISMISSED_KEY, v)
    }
  }, [status, updateCheck])

  const closeUpdated = useCallback(() => setAppliedNotes(null), [])

  const restart = useCallback(async () => {
    if (!status) return
    setRestartError(null)
    try {
      const r = await fetch('/api/system/update/restart', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
      })
      if (!r.ok) {
        const body = await r.json().catch(() => ({}))
        const detail = body?.detail
        throw new Error(
          typeof detail === 'string'
            ? detail
            : typeof detail?.message === 'string'
              ? detail.message
              : `restart failed (${r.status})`
        )
      }
      applyingRef.current = {
        from: status.current_version,
        platform: status.platform,
      }
      setApplying(true)
    } catch (e) {
      setRestartError(e instanceof Error ? e.message : String(e))
    }
  }, [status])

  // ── after restart: poll /api/version, reload when it moved ──
  useEffect(() => {
    if (!applying) return
    const { from, platform } = applyingRef.current
    const timeoutMs = platform === 'windows' ? 10 * 60_000 : 3 * 60_000
    const started = Date.now()
    let stop = false
    const check = async () => {
      if (stop) return
      try {
        const r = await fetch('/api/version', {
          credentials: 'include',
          cache: 'no-store',
        })
        if (r.ok) {
          const body = (await r.json()) as { version?: string }
          if (body.version && body.version !== from) {
            window.location.reload()
            return
          }
        }
      } catch {
        /* the server is mid-restart — the page must simply wait */
      }
      if (Date.now() - started > timeoutMs) {
        stop = true
        setRestartTimedOut(true)
        return
      }
      if (!stop) window.setTimeout(check, 2000)
    }
    const first = window.setTimeout(check, 2000)
    return () => {
      stop = true
      window.clearTimeout(first)
    }
  }, [applying])

  return {
    updateCheck,
    status,
    open,
    // The "已更新到" panel: shown while nothing else is open, so the
    // two never stack into two overlays.
    updatedNotes: appliedNotes,
    updatedOpen: !!appliedNotes && !open,
    closeUpdated,
    openModal,
    closeModal,
    restart,
    restartError,
    restartTimedOut,
  }
}
