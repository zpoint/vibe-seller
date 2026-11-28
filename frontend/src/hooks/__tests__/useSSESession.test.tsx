import { renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useSSE } from '../useSSE'

type Source = {
  onmessage: ((event: MessageEvent) => void) | null
  close: ReturnType<typeof vi.fn>
}

const sources: Source[] = []

function params(enabled: boolean, appView = 'tasks'): Parameters<typeof useSSE>[0] {
  return {
    enabled,
    selectedTaskId: undefined,
    appView,
    selectedStoreId: null,
    showAllTasks: true,
    setTasks: vi.fn(),
    setSelectedTask: vi.fn(),
    setSteps: vi.fn(),
    setScreenshots: vi.fn(),
    setAgentMessages: vi.fn(),
    setTodoItems: vi.fn(),
    setConversationItems: vi.fn(),
    setPendingQuestions: vi.fn(),
    setSelectedAnswers: vi.fn(),
    setOtherInputs: vi.fn(),
    setShowOtherInput: vi.fn(),
    setLogs: vi.fn(),
    questionBannerRef: { current: null },
  }
}

function emit(source: Source, content: string) {
  source.onmessage?.({ data: JSON.stringify({
    type: 'task_created',
    task: { id: content, store_id: null },
  }) } as MessageEvent)
}

describe('SSE session lifecycle', () => {
  beforeEach(() => {
    sources.length = 0
    vi.stubGlobal('EventSource', vi.fn(function () {
      const source: Source = { onmessage: null, close: vi.fn() }
      sources.push(source)
      return source
    }))
  })

  it('never connects anonymously, closes on logout, and ignores late events', () => {
    const initial = params(false)
    const { rerender } = renderHook(props => useSSE(props), {
      initialProps: initial,
    })
    expect(sources).toHaveLength(0)

    const authenticated = params(true)
    rerender(authenticated)
    expect(sources).toHaveLength(1)
    const oldSource = sources[0]
    emit(oldSource, 'before-logout')
    expect(authenticated.setTasks).toHaveBeenCalledTimes(1)

    rerender(params(false))
    expect(oldSource.close).toHaveBeenCalledTimes(1)
    emit(oldSource, 'after-logout')
    expect(authenticated.setTasks).toHaveBeenCalledTimes(1)

    rerender(params(false))
    expect(sources).toHaveLength(1)
    rerender(params(true))
    expect(sources).toHaveLength(2)
  })

  it('keeps one continuous connection across same-user navigation', () => {
    const initial = params(true, 'tasks')
    const { rerender } = renderHook(props => useSSE(props), {
      initialProps: initial,
    })
    expect(sources).toHaveLength(1)

    rerender(params(true, 'workspace'))
    rerender(params(true, 'settings'))

    expect(sources).toHaveLength(1)
    expect(sources[0].close).not.toHaveBeenCalled()
    emit(sources[0], 'same-user-event')
    expect(initial.setTasks).toHaveBeenCalledTimes(1)
  })
})
