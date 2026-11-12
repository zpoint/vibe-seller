import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { AuthUser, UpdateCheckResult, UpdateStatus } from '../types'

const DISMISSED_KEY = 'vibe-seller:update-dismissed-version'

/**
 * The whole in-app update UI state for one logged-in session:
 *
 * - fetches `/api/system/update-check` once per login (what exists /
 *   is new / merged notes), then
 * - polls `/api/system/update-status` (the background download's
 *   state machine) fast while it moves and slowly otherwise;
 * - shows the modal automatically when an update reaches `ready` (or,
 *   for an install that cannot update itself, when a version is
 *   available — the popup then shows the manual copy-command /
 *   download-link flow). "稍后" hides it for this browser session
 *   only (sessionStorage), per version. The sidebar chip re-opens it
 *   at any time.
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
  const [dismissedVersion, setDismissedVersion] = useState<string | null>(() => {
    try {
      return sessionStorage.getItem(DISMISSED_KEY)
    } catch {
      return null
    }
  })
  const applyingRef = useRef({ from: '', platform: 'linux' as string })

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
            stateRef.current = s.state
            setPolled(s)
          }
        }
      } catch {
        /* server briefly down (restart) or unreachable — keep the last
           state; retry on the next tick */
      }
      if (!stop) {
        const active =
          stateRef.current === 'downloading' || stateRef.current === 'restarting'
        timer = window.setTimeout(tick, active ? 2000 : 10000)
      }
    }
    void tick()
    return () => {
      stop = true
      if (timer) window.clearTimeout(timer)
    }
  }, [currentUser])

  // While the restart is running the server cannot answer; the UI
  // stays on the overlay even if a late poll disagrees.
  const status: UpdateStatus | null = useMemo(
    () =>
      applying && polled ? { ...polled, state: 'restarting' as const } : polled,
    [applying, polled]
  )

  const supported = status?.supported === true
  const readyVisible =
    !!currentUser &&
    supported &&
    !applying &&
    status?.state === 'ready' &&
    !!status?.version &&
    dismissedVersion !== status?.version
  const manualVisible =
    !!currentUser &&
    !supported &&
    !!updateCheck?.update_available &&
    updateCheck?.in_app_update === false &&
    dismissedVersion !== updateCheck?.latest_version
  const open = userOpen || readyVisible || manualVisible || applying

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
      try {
        sessionStorage.setItem(DISMISSED_KEY, v)
      } catch {
        /* private mode: fine */
      }
    }
  }, [status, updateCheck])

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
    openModal,
    closeModal,
    restart,
    restartError,
    restartTimedOut,
  }
}
