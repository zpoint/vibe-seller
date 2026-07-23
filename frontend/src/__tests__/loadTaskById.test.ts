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
})
