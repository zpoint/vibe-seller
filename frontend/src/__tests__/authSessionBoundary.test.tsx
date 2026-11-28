import { act, fireEvent, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { AUTH_EXPIRED_EVENT } from '../api'
import { bootApp } from '../test/appHarness'

vi.mock('../lib/telemetry', () => ({
  sendEvent: vi.fn(), lengthBucket: () => 'small', ageBucket: () => 'new',
  registerUserHash: vi.fn(), unregisterUserHash: vi.fn(),
}))

const USER_A = { id: 'user-a', username: 'user-a', role: 'admin', default_profile_id: 'default' }
const USER_B = { id: 'user-b', username: 'user-b', role: 'admin', default_profile_id: 'default' }
const VENDORS = [
  { key: 'chrome', selectable: true, kind: 'plain', display_name_en: 'Chrome', platforms: ['windows', 'wsl', 'mac', 'linux'] },
  { key: 'ziniao', selectable: true, kind: 'antidetect', display_name_en: 'Ziniao', platforms: ['windows', 'wsl', 'mac', 'linux'] },
]
const WORKSPACE = {
  skills: [], store_profiles: [], project_knowledge: [], root_files: [],
  local_knowledge: [{ path: 'knowledge/draft.txt', name: 'draft.txt', size: 1 }],
}
const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body })

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(res => { resolve = res })
  return { promise, resolve }
}

async function settle(times = 6) {
  for (let i = 0; i < times; i++) {
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)) })
  }
}

async function signInB() {
  fireEvent.change(screen.getByPlaceholderText('Username or email'), { target: { value: 'user-b' } })
  fireEvent.change(screen.getByPlaceholderText('Enter your password'), { target: { value: 'pw' } })
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Sign In' })) })
  await settle()
}

function authenticatedRoute(active: { current: typeof USER_A | typeof USER_B }) {
  return (url: string): unknown => {
    const path = url.split('?')[0]
    if (path === '/api/auth/status') return { auth_required: true }
    if (path === '/api/auth/me') return active.current
    if (path === '/api/auth/login') { active.current = USER_B; return USER_B }
    if (path === '/api/browser-vendors') return VENDORS
    if (path === '/api/workspace/structured') return WORKSPACE
    if (path === '/api/workspace/file') return { content: active.current === USER_A ? 'A file original' : 'B file original' }
    return undefined
  }
}

describe('login request boundary', () => {
  afterEach(() => { vi.unstubAllGlobals() })

  it('coalesces Enter and click while a login request is pending', async () => {
    window.history.pushState({}, '', '/tasks')
    const active = { current: USER_A }
    const response = deferred<ReturnType<typeof ok>>()
    const route = authenticatedRoute(active)
    const { fetchMock } = await bootApp({
      route: url => url === '/api/auth/login' ? response.promise : route(url),
    })
    await act(async () => { fireEvent.click(screen.getByTitle('Sign Out')) })
    await settle()

    const identifier = screen.getByPlaceholderText('Username or email')
    const password = screen.getByPlaceholderText('Enter your password')
    fireEvent.change(identifier, { target: { value: 'user-b' } })
    fireEvent.change(password, { target: { value: 'pw' } })
    fireEvent.keyDown(password, { key: 'Enter' })
    fireEvent.click(screen.getByRole('button', { name: 'Sign In' }))
    fireEvent.keyDown(identifier, { key: 'Enter' })

    const loginCalls = fetchMock.mock.calls.filter(([url]) => url === '/api/auth/login')
    expect(loginCalls).toHaveLength(1)

    await act(async () => { response.resolve(ok(USER_B)); await response.promise })
    await settle()
    expect(screen.getByText('user-b', { exact: false })).toBeInTheDocument()
  })

  it('a delayed 401 from the old session cannot expire the new login', async () => {
    window.history.pushState({}, '', '/tasks')
    const active = { current: USER_A }
    const route = authenticatedRoute(active)
    const oldAccounts = deferred<ReturnType<typeof ok>>()
    let accountRequests = 0
    await bootApp({
      route: url => {
        if (url === '/api/ziniao-accounts' && accountRequests++ === 0) return oldAccounts.promise
        return route(url)
      },
    })
    await act(async () => { fireEvent.click(screen.getByTitle('Sign Out')) })
    await settle()
    await signInB()
    expect(screen.getByText('user-b', { exact: false })).toBeInTheDocument()

    await act(async () => {
      oldAccounts.resolve({ ok: false, status: 401, json: async () => ({}) })
      await oldAccounts.promise
    })
    await settle()

    expect(screen.getByText('user-b', { exact: false })).toBeInTheDocument()
    expect(screen.queryByPlaceholderText('Enter your password')).not.toBeInTheDocument()
  })

  it('a pending 401 from the current session still expires that session', async () => {
    window.history.pushState({}, '', '/tasks')
    const active = { current: USER_A }
    const accounts = deferred<ReturnType<typeof ok>>()
    await bootApp({
      route: url => url === '/api/ziniao-accounts'
        ? accounts.promise
        : authenticatedRoute(active)(url),
    })

    await act(async () => {
      accounts.resolve({ ok: false, status: 401, json: async () => ({}) })
      await accounts.promise
    })
    await settle()

    expect(screen.getByPlaceholderText('Enter your password')).toBeInTheDocument()
  })
})

const MARKERS = {
  conversation: 'A-private-conversation',
  draft: 'A-private-draft',
  store: 'A-private-store',
  proxy: 'http://a-private-proxy.invalid',
  account: 'A-private-account',
  password: 'A-private-password',
  user: 'A-private-new-user',
  workspace: 'A-private-workspace-edit',
  preview: 'A-private-history-preview',
  assistant: 'A-private-assistant-message',
}

function markerRoute(active: { current: typeof USER_A | typeof USER_B }) {
  const base = authenticatedRoute(active)
  return (url: string): unknown => {
    const path = url.split('?')[0]
    if (path === '/api/tasks/task-1/messages') {
      return active.current === USER_A ? [{ role: 'assistant', content: MARKERS.conversation }] : []
    }
    if (path === '/api/ziniao-accounts') {
      return [{ id: 'account-a', name: 'Example account', company: 'acme', username: 'ops' }]
    }
    if (path === '/api/ziniao-accounts/account-a/browsers') {
      return [{ browser_name: 'Example profile', browser_oauth: 'profile-a' }]
    }
    if (path === '/api/workspace/file/history') {
      return { commits: [{ sha: 'abc123', message: 'Example history', date: '2026-01-01', author: 'A' }] }
    }
    if (path === '/api/workspace/file/version') return { content: MARKERS.preview }
    if (path === '/api/workspace/assistant/message') return new Promise(() => {})
    return base(url)
  }
}

async function populateAState() {
  expect(screen.getByText(MARKERS.conversation)).toBeInTheDocument()
  fireEvent.change(screen.getByPlaceholderText('Send a follow-up message...'), {
    target: { value: MARKERS.draft },
  })

  fireEvent.click(screen.getByRole('button', { name: '+ New Store' }))
  const storeName = screen.getByPlaceholderText('Store Name...')
  fireEvent.change(storeName, { target: { value: MARKERS.store } })
  const form = storeName.parentElement as HTMLElement
  fireEvent.click(form.querySelector('input[type="checkbox"]') as HTMLInputElement)
  fireEvent.change(screen.getByPlaceholderText(/Proxy Server/), { target: { value: MARKERS.proxy } })
  fireEvent.change(form.querySelector('select') as HTMLSelectElement, { target: { value: 'ziniao' } })
  const accountSelect = form.querySelectorAll('select')[1]
  fireEvent.change(accountSelect, { target: { value: 'account-a' } })
  await settle()
  fireEvent.click(screen.getByTitle('Add account'))
  fireEvent.change(screen.getByPlaceholderText('Company'), { target: { value: 'acme' } })
  fireEvent.change(screen.getByPlaceholderText('Username'), { target: { value: MARKERS.account } })
  const accountPassword = screen.getByPlaceholderText('Password')
  fireEvent.change(accountPassword, { target: { value: MARKERS.password } })
  fireEvent.click(accountPassword.parentElement?.querySelector('button') as HTMLButtonElement)

  fireEvent.click(screen.getByTestId('nav-settings'))
  await settle(2)
  fireEvent.click(screen.getByTestId('settings-tab-account'))
  await settle(2)
  fireEvent.click(screen.getByRole('button', { name: '+ New User' }))
  fireEvent.change(screen.getByPlaceholderText('Username'), { target: { value: MARKERS.user } })
  fireEvent.change(screen.getByPlaceholderText('Password'), { target: { value: MARKERS.password } })

  fireEvent.click(screen.getByRole('button', { name: 'Knowledge' }))
  await settle()
  fireEvent.click(screen.getByText(/draft\.txt/, { selector: 'span' }))
  await settle()
  fireEvent.change(screen.getByDisplayValue('A file original'), { target: { value: MARKERS.workspace } })
  fireEvent.click(screen.getByRole('button', { name: 'History' }))
  await settle()
  fireEvent.click(screen.getByText('Example history'))
  await settle()
  expect(screen.getByDisplayValue(MARKERS.preview)).toBeInTheDocument()

  fireEvent.click(screen.getByRole('button', { name: /AI Chat/ }))
  fireEvent.change(screen.getByPlaceholderText('Tell the AI something about your stores...'), {
    target: { value: MARKERS.assistant },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Send' }))
  expect(screen.getByText(MARKERS.assistant)).toBeInTheDocument()
}

function expectNoAState() {
  for (const marker of Object.values(MARKERS)) {
    expect(document.body.textContent).not.toContain(marker)
    for (const field of document.querySelectorAll('input, textarea')) {
      expect((field as HTMLInputElement | HTMLTextAreaElement).value).not.toContain(marker)
    }
  }
}

describe('cross-user sensitive-state teardown', () => {
  afterEach(() => { vi.unstubAllGlobals() })

  it.each(['logout', 'expiry'] as const)('%s clears A state before B can use the app', async path => {
    window.history.pushState({}, '', '/tasks/task-1')
    const active = { current: USER_A }
    const { sources } = await bootApp({ route: markerRoute(active) })
    await populateAState()
    const oldSource = sources[0]

    if (path === 'logout') {
      await act(async () => { fireEvent.click(screen.getByTitle('Sign Out')) })
    } else {
      act(() => { window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT)) })
    }
    await settle()
    expect(oldSource.close).toHaveBeenCalled()
    await signInB()
    expectNoAState()

    fireEvent.click(screen.getByRole('button', { name: '+ New Store' }))
    expectNoAState()
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    fireEvent.click(screen.getByText('Example task'))
    await settle()
    expect(screen.getByPlaceholderText('Send a follow-up message...')).toHaveValue('')
    expectNoAState()

    fireEvent.click(screen.getByRole('button', { name: 'Knowledge' }))
    expectNoAState()
    fireEvent.click(screen.getByRole('button', { name: /Files/ }))
    expectNoAState()
    fireEvent.click(screen.getByTestId('nav-settings'))
    await settle(2)
    expectNoAState()
    fireEvent.click(screen.getByTestId('settings-tab-account'))
    await settle(2)
    fireEvent.click(screen.getByRole('button', { name: '+ New User' }))
    expectNoAState()
  })
})

describe('same-user behavior remains intact', () => {
  afterEach(() => { vi.unstubAllGlobals() })

  it('navigation and opening or closing forms preserve drafts and task state', async () => {
    window.history.pushState({}, '', '/tasks/task-1')
    const active = { current: USER_A }
    await bootApp({ route: markerRoute(active) })
    const draft = screen.getByPlaceholderText('Send a follow-up message...')
    fireEvent.change(draft, { target: { value: 'same-user draft' } })

    fireEvent.click(screen.getByRole('button', { name: '+ New Store' }))
    const name = screen.getByPlaceholderText('Store Name...')
    fireEvent.change(name, { target: { value: 'same-user store' } })
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    fireEvent.click(screen.getByRole('button', { name: '+ New Store' }))
    expect(screen.getByPlaceholderText('Store Name...')).toHaveValue('same-user store')
    expect(screen.getByPlaceholderText('Send a follow-up message...')).toHaveValue('same-user draft')
    fireEvent.click(screen.getByRole('button', { name: 'Knowledge' }))
    await settle()
    fireEvent.click(screen.getByText(/draft\.txt/, { selector: 'span' }))
    await settle()
    fireEvent.change(screen.getByDisplayValue('A file original'), { target: { value: 'same-user edit' } })
    fireEvent.click(screen.getByRole('button', { name: /AI Chat/ }))
    fireEvent.click(screen.getByRole('button', { name: /Files/ }))
    expect(screen.getByDisplayValue('same-user edit')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Tasks' }))
    await settle(2)
    fireEvent.click(screen.getByText('Example task'))
    await settle()
    expect(screen.getByText(MARKERS.conversation)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '+ New Store' }))
    expect(screen.getByPlaceholderText('Store Name...')).toHaveValue('same-user store')
  })
})
