/**
 * Request errors carry no names into exception telemetry.
 *
 * Observed (review): RequestTimeoutError put the full URL, query and all
 * (`?path=stores/<slug>/…`), in its message, and the workspace Save
 * button passed `saveWsFile` straight to onClick, so a failed save was an
 * uncaught rejection — which PostHog's exception capture reports. The
 * timeout message now has no URL (it stays on a non-enumerable field),
 * and the workspace file actions catch and tell the user.
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { renderHook } from '@testing-library/react'
import { api } from '../api'
import { DEFAULT_TIMEOUT_MS } from '../lib/requestDeadline'
import { useWsFiles } from '../hooks/useWsFiles'

vi.mock('../lib/telemetry', async (orig) => ({
  ...(await orig<typeof import('../lib/telemetry')>()),
  sendEvent: vi.fn(),
}))

const URL_WITH_NAME = '/api/workspace/file?path=stores%2Facme-shop%2Fnotes.md'

describe('request error privacy', () => {
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks() })

  it('a timeout error does not carry the URL in its message or own keys', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('fetch', vi.fn((_u: string, init?: RequestInit) => new Promise((_r, reject) => {
      init?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
    })))
    const p = api.get(URL_WITH_NAME).catch(e => e)
    await vi.advanceTimersByTimeAsync(DEFAULT_TIMEOUT_MS)
    const err = await p
    expect(err.message).not.toMatch(/acme|path=|\/api\//)
    expect(JSON.stringify(err)).not.toMatch(/acme/)
    expect(Object.keys(err)).not.toContain('url')
    expect(err.url).toBe(URL_WITH_NAME) // still there for debugging
  })

  it.each(['saveWsFile', 'deleteWsFile', 'createWsFile'] as const)(
    '%s does not reject when the request fails; it tells the user', async (name) => {
      vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Failed to fetch') }))
      const alert = vi.spyOn(window, 'alert').mockImplementation(() => {})
      const noop = vi.fn()
      const { result } = renderHook(() => useWsFiles({
        t: (k: string) => k, wsSelectedFile: 'stores/acme-shop/notes.md', wsEditorContent: 'x', wsSaving: false,
        setWsSelectedFile: noop, setWsFileContent: noop, setWsEditorContent: noop, setWsEditorDirty: noop,
        setWsShowHistory: noop, setWsPreviewCommit: noop, setWsPreviewContent: noop, setWsSaving: noop,
        setWsSyncing: noop, setWsSyncMeta: noop, setWsSkillsSyncing: noop, setWsFileHistory: noop,
        setWsNewFileName: noop, setWsNewFileSection: noop, loadWsStructured: vi.fn(async () => {}),
      } as unknown as Parameters<typeof useWsFiles>[0]))

      const call = name === 'saveWsFile' ? result.current.saveWsFile()
        : name === 'deleteWsFile' ? result.current.deleteWsFile('stores/acme-shop/notes.md')
          : result.current.createWsFile('knowledge', 'notes')
      await expect(call).resolves.toBeUndefined()
      expect(alert).toHaveBeenCalledTimes(1)
    })
})
