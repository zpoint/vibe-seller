/**
 * "Set up AI first" (lib/aiSetup.ts, hooks/useAiSetupGate.tsx,
 * components/AiSetupPrompt.tsx, components/AiSetupGuide.tsx).
 *
 * Pins, against the real App: every "new task" / "new schedule" button
 * asks the server first, and while no AI is set up shows the dialog
 * instead of the form (nothing to type, nothing created); once AI is
 * set up (or the server cannot tell) the form opens and creates as
 * before, with no check on the create itself; "Set up AI" lands on the AI tab with the
 * guide open; the docs link is the page for the UI language; the AI tab
 * highlights "Add Model" on a first visit while AI isn't set up (not
 * after it was dismissed, nor once AI is set up), clicking that button
 * ends it, and it closes when the user's default model becomes usable.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import type { Mock } from 'vitest'
import { act, fireEvent, screen, within } from '@testing-library/react'
import { bootApp } from '../test/appHarness'
import { aiSetupStore, aiDocsUrl } from '../lib/aiSetup'
import { AI_GUIDE_SEEN_KEY } from '../hooks/useAiSetupGuide'
import { sendEvent } from '../lib/telemetry'
import { FrontendEvent } from '../lib/telemetryEvents'
import zh from '../i18n/locales/zh/translation.json'
import en from '../i18n/locales/en/translation.json'

vi.mock('../lib/telemetry', () => ({
  sendEvent: vi.fn(), lengthBucket: () => 'small', ageBucket: () => 'new',
  registerUserHash: vi.fn(), unregisterUserHash: vi.fn(), apiRouteShape: (p: string) => p,
}))

const NOT_READY = { ready: false }
const READY = { ready: true }
const CREATED = { id: 'task-new', title: 'Check ad spend', status: 'pending', store_id: null }

const settle = async () => {
  for (let i = 0; i < 10; i++) {
    await act(async () => { await new Promise(r => setTimeout(r, 0)) })
  }
}

interface BootOpts {
  path?: string
  /** What POST /api/tasks answers. */
  create?: () => unknown
  readiness?: () => unknown
}

async function boot({ path = '/tasks', create = () => CREATED, readiness = () => NOT_READY }: BootOpts = {}) {
  window.history.pushState({}, '', path)
  return bootApp({
    guideSeen: true,
    tasks: [],
    route: (url, init) => {
      const method = init?.method ?? 'GET'
      if (url === '/api/tasks' && method === 'POST') return create()
      if (url.startsWith('/api/profiles/readiness')) return readiness()
      return undefined
    },
  })
}

const createPosts = (fetchMock: Mock) =>
  fetchMock.mock.calls.filter(([u, i]) => u === '/api/tasks' && i?.method === 'POST')

async function clickNewTask() {
  fireEvent.click(screen.getAllByRole('button', { name: /New Task/ })[0])
  await settle()
}

async function typeAndCreate(title: string) {
  await clickNewTask()
  fireEvent.change(await screen.findByLabelText(en.tasks.promptLabel), { target: { value: title } })
  fireEvent.click(screen.getByRole('button', { name: /^Create$/ }))
  await settle()
}

beforeEach(() => {
  aiSetupStore.reset()
  localStorage.removeItem(AI_GUIDE_SEEN_KEY)
  localStorage.setItem('vibe-seller-language', 'en')
  ;(sendEvent as Mock).mockClear()
})
afterEach(() => { vi.unstubAllGlobals() })

describe('"New Task" while no AI is set up', () => {
  it('shows the dialog instead of the form', async () => {
    const { fetchMock } = await boot()
    await clickNewTask()

    const dialog = await screen.findByTestId('ai-setup-prompt')
    expect(dialog).toHaveTextContent(en.aiSetup.prompt.title)
    expect(screen.queryByLabelText(en.tasks.promptLabel)).toBeNull()  // no form
    expect(createPosts(fetchMock)).toHaveLength(0)

    fireEvent.click(within(dialog).getByRole('button', { name: en.aiSetup.prompt.gotIt }))
    expect(screen.queryByTestId('ai-setup-prompt')).toBeNull()
    expect(sendEvent).toHaveBeenCalledWith(FrontendEvent.AI_SETUP_PROMPT_SHOWN, {})
    expect(sendEvent).toHaveBeenCalledWith(FrontendEvent.AI_SETUP_PROMPT_CHOICE, { choice: 'later' })

    // Asked again on the next click, not remembered.
    await clickNewTask()
    expect(screen.getByTestId('ai-setup-prompt')).toBeInTheDocument()
  })

  it('"New Schedule" shows the dialog too', async () => {
    await boot()
    const taskType = document.querySelector('[data-tour="task-type"]') as HTMLElement
    fireEvent.click(within(taskType).getByRole('button', { name: en.tasks.scheduled }))
    const buttons = screen.getAllByRole('button', { name: new RegExp(en.schedules.newSchedule) })
    expect(buttons.length).toBeGreaterThan(1)  // header + empty list
    for (const button of buttons) {
      fireEvent.click(button)
      await settle()
      const dialog = screen.getByTestId('ai-setup-prompt')
      expect(screen.queryByText(en.schedules.newScheduleSubtitle)).toBeNull()  // no form
      fireEvent.click(within(dialog).getByRole('button', { name: en.aiSetup.prompt.gotIt }))
    }
  })

  it('"Set up AI" opens the AI tab with the guide', async () => {
    const { router } = await boot()
    await clickNewTask()
    fireEvent.click(screen.getByRole('button', { name: en.aiSetup.prompt.goToSettings }))
    await settle()
    expect(router.state.location.pathname).toBe('/settings/ai')
    const guide = await screen.findByTestId('ai-setup-guide')
    expect(guide).toHaveTextContent(en.aiSetup.guide.title)
    expect(sendEvent).toHaveBeenCalledWith(FrontendEvent.AI_SETUP_GUIDE_SHOWN, { trigger: 'prompt' })
    const docs = within(guide).getByRole('link', { name: new RegExp(en.aiSetup.guide.docs.slice(0, 10)) })
    expect(docs).toHaveAttribute('href', 'https://docs.vibe-sellers.com/en/llm-keys/')
    expect(docs).toHaveAttribute('target', '_blank')
  })

  it('the dialog links the docs page for the UI language', async () => {
    await boot()
    await clickNewTask()
    const link = screen.getByRole('link', { name: new RegExp(en.aiSetup.prompt.docs) })
    expect(link).toHaveAttribute('href', 'https://docs.vibe-sellers.com/en/llm-keys/')
    fireEvent.click(link)
    // Docs open in a new tab; the dialog stays for the other choice.
    expect(screen.getByTestId('ai-setup-prompt')).toBeInTheDocument()
    expect(aiDocsUrl('zh')).toBe('https://docs.vibe-sellers.com/llm-keys/')
    expect(aiDocsUrl('zh-CN')).toBe('https://docs.vibe-sellers.com/llm-keys/')
  })
})

describe('"New Task" once AI is set up', () => {
  it('opens the form and creates the task', async () => {
    const { fetchMock } = await boot({ readiness: () => READY })
    await typeAndCreate('Check ad spend')
    const posts = createPosts(fetchMock)
    expect(posts).toHaveLength(1)
    expect(JSON.parse(posts[0][1].body)).not.toHaveProperty('require_ai_ready')
    expect(screen.queryByTestId('ai-setup-prompt')).toBeNull()
    expect(sendEvent).not.toHaveBeenCalledWith(FrontendEvent.AI_SETUP_PROMPT_SHOWN, {})
  })

  it('opens the form when the server cannot answer', async () => {
    await boot({ readiness: () => [] })
    await clickNewTask()
    expect(await screen.findByLabelText(en.tasks.promptLabel)).toBeInTheDocument()
    expect(screen.queryByTestId('ai-setup-prompt')).toBeNull()
  })

})

describe('AI tab guide', () => {
  it('opens on a first visit while AI is not set up', async () => {
    await boot({ path: '/settings/ai' })
    await screen.findByTestId('ai-setup-guide')
    expect(sendEvent).toHaveBeenCalledWith(FrontendEvent.AI_SETUP_GUIDE_SHOWN, { trigger: 'first_visit' })
  })

  it('stays closed after it was dismissed in this browser', async () => {
    const v = await boot({ path: '/settings/ai' })
    const guide = await screen.findByTestId('ai-setup-guide')
    fireEvent.click(within(guide).getByRole('button', { name: en.aiSetup.guide.gotIt }))
    expect(localStorage.getItem(AI_GUIDE_SEEN_KEY)).toBeTruthy()
    await act(async () => { v.unmount() })
    await boot({ path: '/settings/ai' })
    await settle()
    expect(screen.queryByTestId('ai-setup-guide')).toBeNull()
  })

  it('never opens when AI is set up', async () => {
    await boot({ path: '/settings/ai', readiness: () => READY })
    await settle()
    expect(screen.queryByTestId('ai-setup-guide')).toBeNull()
  })

  it('never opens when the server cannot answer', async () => {
    await boot({ path: '/settings/ai', readiness: () => [] })
    await settle()
    expect(screen.queryByTestId('ai-setup-guide')).toBeNull()
  })

  it('highlights "Add Model"; clicking it opens the form and ends the guide', async () => {
    await boot({ path: '/settings/ai' })
    await screen.findByTestId('ai-setup-guide')
    const button = document.querySelector('[data-tour="add-model"]') as HTMLElement
    expect(button).toHaveTextContent(en.profiles.create)
    fireEvent.click(button)
    expect(await screen.findByText(en.profiles.createTitle)).toBeInTheDocument()
    expect(screen.queryByTestId('ai-setup-guide')).toBeNull()
    expect(localStorage.getItem(AI_GUIDE_SEEN_KEY)).toBeTruthy()
    expect(sendEvent).toHaveBeenCalledWith(FrontendEvent.AI_SETUP_GUIDE_CLOSED, { via: 'add_model' })
  })
})

describe('AI tab guide, setup finished', () => {
  it('closes once AI is set up', async () => {
    let ready = false
    window.history.pushState({}, '', '/tasks')
    await bootApp({
      guideSeen: true,
      tasks: [],
      route: url => {
        if (url === '/api/profiles') return { profiles: [{ id: 'default', name: 'Claude' }, { id: 'deepseek', name: 'DeepSeek', env: {} }] }
        if (url === '/api/profiles/deepseek/set-default') { ready = true; return { ok: true } }
        if (url.startsWith('/api/profiles/readiness')) return ready ? READY : NOT_READY
        return undefined
      },
    })
    await clickNewTask()
    fireEvent.click(screen.getByRole('button', { name: en.aiSetup.prompt.goToSettings }))
    await screen.findByTestId('ai-setup-guide')

    fireEvent.click(screen.getByRole('button', { name: en.profiles.setDefault }))
    await settle()
    expect(screen.queryByTestId('ai-setup-guide')).toBeNull()
    expect(sendEvent).toHaveBeenCalledWith(FrontendEvent.AI_SETUP_COMPLETED, { from_prompt: true })
  })
})

describe('copy', () => {
  it('en and zh carry the same aiSetup keys', () => {
    const keys = (o: object, p = ''): string[] => Object.entries(o).flatMap(([k, v]) =>
      typeof v === 'object' ? keys(v, `${p}${k}.`) : [`${p}${k}`])
    expect(keys(zh.aiSetup).sort()).toEqual(keys(en.aiSetup).sort())
  })
})
