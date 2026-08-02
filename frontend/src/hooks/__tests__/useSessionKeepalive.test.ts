import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useSessionKeepalive } from '../useSessionKeepalive'

// Mock the shared API client — we only assert which endpoint each tick
// hits. The 401 → AUTH_EXPIRED_EVENT behavior lives in api.ts and is
// covered by apiAuthExpired.test.ts.
// Typed to ACCEPT arguments even though the body ignores them: the
// forwarders below spread the real call's arguments in, and a
// zero-arity mock cannot be spread into. The assertions then read those
// arguments back off `.mock.calls`.
type ApiCall = (...args: unknown[]) => Promise<object>
const get = vi.fn<ApiCall>(() => Promise.resolve({}))
const post = vi.fn<ApiCall>(() => Promise.resolve({}))
vi.mock('../../api', () => ({ api: { get: (...a: unknown[]) => get(...a), post: (...a: unknown[]) => post(...a) } }))

describe('useSessionKeepalive', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    get.mockClear()
    post.mockClear()
  })
  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('does nothing when disabled', () => {
    renderHook(() => useSessionKeepalive(false))
    vi.advanceTimersByTime(5 * 60_000)
    expect(get).not.toHaveBeenCalled()
    expect(post).not.toHaveBeenCalled()
  })

  it('heartbeats /api/auth/me when there is no activity', () => {
    renderHook(() => useSessionKeepalive(true))
    vi.advanceTimersByTime(60_000)
    expect(get).toHaveBeenCalledWith('/api/auth/me')
    expect(post).not.toHaveBeenCalled()
  })

  it('rolls the session via /api/auth/refresh after user activity', () => {
    renderHook(() => useSessionKeepalive(true))
    // Simulate a real interaction, then let a tick fire.
    window.dispatchEvent(new Event('pointerdown'))
    vi.advanceTimersByTime(60_000)
    expect(post).toHaveBeenCalledWith('/api/auth/refresh')
    expect(get).not.toHaveBeenCalled()
  })

  it('reverts to heartbeat once activity stops', () => {
    renderHook(() => useSessionKeepalive(true))
    window.dispatchEvent(new Event('keydown'))
    vi.advanceTimersByTime(60_000) // refresh (activity since last roll)
    vi.advanceTimersByTime(60_000) // no new activity → heartbeat
    expect(post).toHaveBeenCalledTimes(1)
    expect(get).toHaveBeenCalledTimes(1)
    expect(get).toHaveBeenCalledWith('/api/auth/me')
  })

  it('stops polling after unmount', () => {
    const { unmount } = renderHook(() => useSessionKeepalive(true))
    unmount()
    vi.advanceTimersByTime(5 * 60_000)
    expect(get).not.toHaveBeenCalled()
    expect(post).not.toHaveBeenCalled()
  })
})
