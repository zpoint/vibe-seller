/**
 * Boot the real App (router + every hook) on an open task, against a
 * stubbed backend and a controllable EventSource. For tests that pin
 * whole-app behaviour — what a keystroke or an SSE event re-renders —
 * which a single-component render cannot show.
 *
 * Call `bootApp()` after setting the URL with `window.history`; the
 * caller's module must import `../router` lazily (inside the test) so
 * the router reads that URL.
 */
import { act, render } from '@testing-library/react'
import { vi } from 'vitest'
import type { Task } from '../types'
import { makeTask } from './factories'

type Listener = ((evt: MessageEvent) => void) | null
export interface FakeEventSource { onmessage: Listener; close: () => void }

export interface BootOptions {
  task?: Partial<Task>
  /** Persisted transcript returned by GET /api/tasks/{id}/messages. */
  messages?: { role: string; content: string }[]
  /** Rows in the task list. */
  tasks?: Task[]
  /** Override a URL's response; return undefined to fall through. */
  route?: (url: string, init?: RequestInit) => unknown
  /**
   * Mark the first-run guide as seen before booting. The guide
   * auto-shows from a rAF callback inside Sidebar (useOnboardingGuide)
   * and re-renders it whenever the frame lands — noise for the
   * render-count assertions. The guide has its own component test.
   */
  guideSeen?: boolean
}

/** A long task: ~1000 transcript entries and a large markdown report. */
export function longTranscript(n = 1000): { role: string; content: string }[] {
  const out: { role: string; content: string }[] = [
    { role: 'user', content: 'Review the example store listings' },
  ]
  for (let i = 0; i < n; i++) {
    if (i % 3 === 0) out.push({ role: 'tool_use', content: JSON.stringify({ tool: 'Bash', input: { command: `echo step-${i}` } }) })
    else if (i % 3 === 1) out.push({ role: 'assistant', content: `Checked item **widget-${i}**` })
    else out.push({ role: 'thinking', content: `Considering widget-${i}` })
  }
  out.push({ role: 'result', content: '# Report\n\n' + '| sku | note |\n|---|---|\n' + '| WIDGET-001 | ok |\n'.repeat(2000) })
  return out
}

export async function bootApp(opts: BootOptions = {}) {
  const task = makeTask({
    id: 'task-1', store_id: null, status: 'completed', title: 'Example task',
    ...opts.task,
  })
  const tasks = opts.tasks ?? [task]
  const respond = (url: string, init?: RequestInit): unknown => {
    const custom = opts.route?.(url, init)
    if (custom !== undefined) return custom
    const path = url.split('?')[0]
    if (path === '/api/auth/status') return { auth_required: false }
    if (path === '/api/auth/me') return { id: 'user-1', username: 'admin', role: 'admin', default_profile_id: 'default' }
    if (path === '/api/profiles') return { profiles: [{ id: 'default', name: 'Default' }] }
    if (path === `/api/tasks/${task.id}`) return task
    if (path === `/api/tasks/${task.id}/messages`) return opts.messages ?? []
    if (path === `/api/tasks/${task.id}/questions/pending`) return { pending: false }
    if (path === `/api/tasks/${task.id}/image/pending`) return { pending: false }
    if (path === '/api/tasks' && url.includes('store_id=')) return tasks
    if (path === '/api/system/info' || path === '/api/system/update-check') return {}
    return []
  }
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const body = respond(url, init)
    if (body instanceof Promise) return body
    return { ok: true, status: 200, json: async () => body, blob: async () => new Blob() } as unknown as Response
  })
  vi.stubGlobal('fetch', fetchMock)

  if (opts.guideSeen) localStorage.setItem('vibe-seller-guide-seen', 'harness')

  const sources: FakeEventSource[] = []
  vi.stubGlobal('EventSource', vi.fn().mockImplementation(function () {
    const es: FakeEventSource = { onmessage: null, close: vi.fn() }
    sources.push(es)
    return es
  }))

  const { RouterProvider } = await import('@tanstack/react-router')
  const { router } = await import('../router')
  await act(async () => { await router.navigate({ href: window.location.pathname }) })
  const view = render(<RouterProvider router={router} />)
  // Let auth → task load → transcript load settle.
  for (let i = 0; i < 20; i++) await act(async () => { await new Promise(r => setTimeout(r, 0)) })

  const emit = (data: Record<string, unknown>) => {
    act(() => {
      for (const es of sources) es.onmessage?.({ data: JSON.stringify(data) } as MessageEvent)
    })
  }
  return { ...view, task, fetchMock, emit, router }
}
