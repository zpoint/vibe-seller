/**
 * Regression: switching tasks must not be clobbered by a slower earlier
 * load. Selecting a task fans out several awaited fetches; if the user
 * clicks a second task before the first resolves, the first (slower)
 * load's responses used to land LAST and revert the selection — the
 * detail pane snapped back to the task you navigated away from
 * ("clicking the other task does nothing"), most visibly when leaving a
 * running task whose message/step load is heavy.
 *
 * loadTaskById claims a monotonic seq and drops its state writes once a
 * newer load has superseded it. These tests pin that guard.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { loadTaskById, type LoadTaskDeps } from '../handlers/loadTaskById'

function deferred<T>() {
  let resolve!: (v: T) => void
  const promise = new Promise<T>(r => { resolve = r })
  return { promise, resolve }
}

function makeDeps(getImpl: (url: string) => Promise<unknown>) {
  const seqRef = { current: 0 }
  const fns = {
    setSelectedTask: vi.fn(), setLogs: vi.fn(), setScreenshots: vi.fn(),
    setAgentMessages: vi.fn(), setTodoItems: vi.fn(), setSelectedAnswers: vi.fn(),
    setOtherInputs: vi.fn(), setShowOtherInput: vi.fn(), setChatInput: vi.fn(),
    setChatAttachments: vi.fn(), setPendingQuestions: vi.fn(),
    setConversationItems: vi.fn(), setSteps: vi.fn(),
  }
  const deps = { api: { get: getImpl }, seqRef, ...fns } as unknown as LoadTaskDeps
  return { deps, seqRef, fns }
}

// Immediate responses for the secondary fetches; the task GET is routed
// per-id so a specific task can be made slow.
function router(taskGet: (id: string) => Promise<unknown>) {
  return (url: string): Promise<unknown> => {
    if (/\/api\/tasks\/[^/]+$/.test(url)) return taskGet(url.split('/').pop() as string)
    if (url.endsWith('/questions/pending')) return Promise.resolve({ pending: false })
    if (url.endsWith('/messages')) return Promise.resolve([])
    if (url.endsWith('/steps')) return Promise.resolve([])
    return Promise.resolve({})
  }
}

describe('loadTaskById stale-response guard', () => {
  beforeEach(() => vi.clearAllMocks())

  it('a slow earlier load does NOT clobber a newer selection', async () => {
    const slowA = deferred<unknown>()
    const { deps, fns } = makeDeps(router(id =>
      id === 'A' ? slowA.promise : Promise.resolve({ id, todos: null }),
    ))

    // Click A (its task GET hangs), then click B (resolves immediately).
    const pA = loadTaskById('A', deps)
    const pB = loadTaskById('B', deps)
    await pB

    // B is fully applied.
    expect(fns.setSelectedTask).toHaveBeenLastCalledWith(
      expect.objectContaining({ id: 'B' }))

    // Now A finally resolves — it must apply NOTHING (superseded).
    fns.setSelectedTask.mockClear()
    fns.setConversationItems.mockClear()
    fns.setSteps.mockClear()
    slowA.resolve({ id: 'A', todos: null })
    await pA

    expect(fns.setSelectedTask).not.toHaveBeenCalled()
    expect(fns.setConversationItems).not.toHaveBeenCalled()
    expect(fns.setSteps).not.toHaveBeenCalled()
  })

  it('the latest selection always wins regardless of resolve order', async () => {
    const slowA = deferred<unknown>()
    const { deps, fns } = makeDeps(router(id =>
      id === 'A' ? slowA.promise : Promise.resolve({ id, todos: null }),
    ))
    const pA = loadTaskById('A', deps)
    const pB = loadTaskById('B', deps)
    await pB
    slowA.resolve({ id: 'A', todos: null })
    await pA
    // Every setSelectedTask call that happened was for B, never A.
    for (const call of fns.setSelectedTask.mock.calls) {
      expect(call[0]).toEqual(expect.objectContaining({ id: 'B' }))
    }
    expect(fns.setSelectedTask).toHaveBeenCalled()
  })

  it('a single (uncontested) load applies its full state', async () => {
    const { deps, fns } = makeDeps(router(id => Promise.resolve({ id, todos: null })))
    await loadTaskById('solo', deps)
    expect(fns.setSelectedTask).toHaveBeenCalledWith(
      expect.objectContaining({ id: 'solo' }))
    expect(fns.setConversationItems).toHaveBeenCalled()
    expect(fns.setSteps).toHaveBeenCalledWith([])
  })

  it('recovers a pending image-confirm card on load', async () => {
    const get = (url: string): Promise<unknown> => {
      if (url.endsWith('/image/pending')) return Promise.resolve({
        pending: true, request_id: 'r9', prompt: 'white bg',
        model: 'nano-banana-pro', models: ['nano-banana-pro'],
        reference_images: ['uploads/a.png'], kind: 'main',
      })
      if (/\/api\/tasks\/[^/]+$/.test(url)) return Promise.resolve({ id: 'T', todos: null })
      if (url.endsWith('/questions/pending')) return Promise.resolve({ pending: false })
      return Promise.resolve([])  // messages, steps
    }
    const { deps, fns } = makeDeps(get)
    await loadTaskById('T', deps)
    // The final conversation set includes the re-rendered confirm card.
    const items = fns.setConversationItems.mock.calls.at(-1)![0] as { type: string; imageRequest?: { requestId: string; resolved?: boolean; referenceImages?: string[] } }[]
    const card = items.find(i => i.type === 'image_request')
    expect(card).toBeTruthy()
    expect(card!.imageRequest!.requestId).toBe('r9')
    expect(card!.imageRequest!.resolved).toBe(false)
    expect(card!.imageRequest!.referenceImages).toEqual(['uploads/a.png'])
  })

  it('does not add a card when no image is pending', async () => {
    const { deps, fns } = makeDeps(router(id => Promise.resolve({ id, todos: null })))
    await loadTaskById('none', deps)
    const items = fns.setConversationItems.mock.calls.at(-1)![0] as { type: string }[]
    expect(items.some(i => i.type === 'image_request')).toBe(false)
  })

  it('clears the previous task detail up front so the switch is instant', async () => {
    const { deps, fns } = makeDeps(router(id => Promise.resolve({ id, todos: null })))
    await loadTaskById('X', deps)
    // Conversation + steps are cleared to [] before the (async) refill,
    // so a slow link never shows the old task's content under the new head.
    expect(fns.setConversationItems).toHaveBeenNthCalledWith(1, [])
    expect(fns.setSteps).toHaveBeenNthCalledWith(1, [])
  })

  it('a superseded load stops fetching screenshots (frees the connection pool)', async () => {
    // Global fetch counts screenshot requests; first one hangs so we can
    // supersede mid-loop.
    const firstFetch = deferred<unknown>()
    let fetchCalls = 0
    const fetchSpy = vi.fn(() => {
      fetchCalls++
      const body = { ok: true, blob: async () => ({}) }
      return fetchCalls === 1 ? (firstFetch.promise as Promise<unknown>) : Promise.resolve(body)
    })
    vi.stubGlobal('fetch', fetchSpy)
    vi.stubGlobal('FileReader', class { onload: (() => void) | null = null; readAsDataURL() {} } as unknown as typeof FileReader)

    const steps = [
      { id: 's1', screenshot_id: 'a' }, { id: 's2', screenshot_id: 'b' },
      { id: 's3', screenshot_id: 'c' },
    ]
    const get = (url: string): Promise<unknown> => {
      if (url.endsWith('/steps')) return Promise.resolve(steps)
      if (/\/api\/tasks\/[^/]+$/.test(url)) return Promise.resolve({ id: 'A', todos: null })
      if (url.endsWith('/questions/pending')) return Promise.resolve({ pending: false })
      return Promise.resolve([])
    }
    const { deps, seqRef } = makeDeps(get)

    const pA = loadTaskById('A', deps)      // reaches the screenshot loop, hangs on fetch #1
    // Wait until A has issued its first screenshot fetch (it's now in the loop).
    while (fetchCalls === 0) await new Promise(r => setTimeout(r, 0))
    seqRef.current++                        // a newer selection supersedes A
    firstFetch.resolve({ ok: true, blob: async () => ({}) })
    await pA

    // Only the in-flight first screenshot was requested; the loop bailed
    // instead of fetching s2 and s3.
    expect(fetchCalls).toBe(1)
    vi.unstubAllGlobals()
  })
})
