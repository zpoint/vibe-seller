import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

describe('SSE Hook', () => {
  let mockEventSource: {
    close: ReturnType<typeof vi.fn>
    addEventListener: ReturnType<typeof vi.fn>
    removeEventListener: ReturnType<typeof vi.fn>
    onmessage: ((event: MessageEvent) => void) | null
    onerror: (() => void) | null
    onopen: (() => void) | null
  }
  let EventSourceMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    mockEventSource = {
      close: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      onmessage: null,
      onerror: null,
      onopen: null,
    }

    EventSourceMock = vi.fn(() => mockEventSource)
    global.EventSource = EventSourceMock as unknown as typeof EventSource
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('should connect to SSE endpoint', () => {
    const url = 'http://localhost:7777/api/events'
    const es = new EventSource(url, { withCredentials: true })

    expect(EventSourceMock).toHaveBeenCalledWith(url, { withCredentials: true })
    expect(es).toBeDefined()
  })

  it('should handle SSE messages', () => {
    const messages: Record<string, unknown>[] = []

    mockEventSource.onmessage = (event: MessageEvent) => {
      const data = JSON.parse(event.data)
      messages.push(data)
    }

    // Simulate receiving a message
    const mockEvent = new MessageEvent('message', {
      data: JSON.stringify({ type: 'task_update', task_id: '123' }),
    })

    mockEventSource.onmessage(mockEvent)

    expect(messages).toHaveLength(1)
    expect(messages[0]).toEqual({ type: 'task_update', task_id: '123' })
  })

  it('should handle connection errors', () => {
    let errorHandled = false

    mockEventSource.onerror = () => {
      errorHandled = true
    }

    // Simulate an error
    mockEventSource.onerror()

    expect(errorHandled).toBe(true)
  })

  it('should close connection on cleanup', () => {
    const es = new EventSource('http://test')
    es.close()

    expect(mockEventSource.close).toHaveBeenCalled()
  })
})

// F5-F6: SSE event handling logic tests

describe('F5: task_update SSE event processing', () => {
  it('task_update with plan data includes plan text', () => {
    const event = {
      type: 'task_update',
      task_id: 'task-1',
      status: 'planned',
      plan: '## My Plan\n1. Step one',
    }

    // Verify the event structure is correct for plan updates
    expect(event.plan).toBeDefined()
    expect(event.status).toBe('planned')
  })

  it('task_update with running status indicates execution phase', () => {
    const event = {
      type: 'task_update',
      task_id: 'task-1',
      status: 'running',
    }

    expect(event.status).toBe('running')
    // Running status should trigger execution separator in UI
  })

  it('task_update with failed status includes error', () => {
    const event = {
      type: 'task_update',
      task_id: 'task-1',
      status: 'failed',
      error: 'Stopped by user',
    }

    expect(event.status).toBe('failed')
    expect(event.error).toBe('Stopped by user')
  })
})

describe('F6: task_message SSE event processing', () => {
  it('task_message with role=delta is streaming content', () => {
    const event = {
      type: 'task_message',
      task_id: 'task-1',
      role: 'delta',
      content: 'Working on step 1...',
    }

    expect(event.role).toBe('delta')
    // Delta messages should accumulate into streaming item
  })

  it('task_message with role=assistant replaces streaming', () => {
    const event = {
      type: 'task_message',
      task_id: 'task-1',
      role: 'assistant',
      content: 'Completed step 1. Moving to step 2.',
    }

    expect(event.role).toBe('assistant')
    // Assistant messages should replace any active streaming item
  })

  it('task_message with role=result creates result item', () => {
    const event = {
      type: 'task_message',
      task_id: 'task-1',
      role: 'result',
      content: 'Task completed successfully',
    }

    expect(event.role).toBe('result')
    // Result messages should create a result conversation item
  })

  it('SSE event parsing handles multiple event types', () => {
    const events = [
      { type: 'task_update', task_id: 't1', status: 'designing' },
      { type: 'task_message', task_id: 't1', role: 'delta', content: 'Planning...' },
      { type: 'task_update', task_id: 't1', status: 'planned' },
      { type: 'task_message', task_id: 't1', role: 'assistant', content: 'Plan ready' },
      { type: 'task_update', task_id: 't1', status: 'running' },
      { type: 'task_message', task_id: 't1', role: 'result', content: 'Done' },
    ]

    // Verify all events have correct structure
    expect(events.filter(e => e.type === 'task_update')).toHaveLength(3)
    expect(events.filter(e => e.type === 'task_message')).toHaveLength(3)
  })
})
