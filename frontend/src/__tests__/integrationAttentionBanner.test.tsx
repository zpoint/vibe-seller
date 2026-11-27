/**
 * The top bar's last kind: a workplace platform that was signed in and
 * whose sign-in, or one of whose permissions, expired
 * (GET /api/integrations/cli/attention — the server lists only enabled
 * platforms, for admins only). It names the platform and opens
 * Settings → Integrations; anything that waits on a reply comes first.
 */
import { describe, it, expect, vi } from 'vitest'
import { act, fireEvent, screen } from '@testing-library/react'
import { bootApp } from '../test/appHarness'
import { makeStore } from '../test/factories'
import type { WaitingTaskItem } from '../types'
import zh from '../i18n/locales/zh/translation.json'
import en from '../i18n/locales/en/translation.json'

vi.mock('../lib/telemetry', () => ({
  sendEvent: vi.fn(), lengthBucket: () => 'small', ageBucket: () => 'new',
  registerUserHash: vi.fn(), unregisterUserHash: vi.fn(),
}))

const STORES = [makeStore({ id: 'store-a', name: 'Demo A' })]
const WAITING: WaitingTaskItem = {
  task_id: 't-1', title: 'Task t-1', store_id: null, kind: 'question',
  since: '2026-11-01T00:00:00Z', deadline: null,
}

function routeWith(state: { tasks: WaitingTaskItem[]; expired: unknown[] }) {
  return (url: string) => {
    if (url === '/api/stores') return STORES
    if (url === '/api/schedules') return []
    if (url === '/api/schedules/attention') return { plan_approvals: [], planner_questions: [] }
    if (url === '/api/tasks/attention') return { waiting_tasks: state.tasks }
    if (url === '/api/integrations/cli/attention') return { items: state.expired }
    if (url === '/api/settings') return {}
    return undefined
  }
}

const settle = async () => {
  for (let i = 0; i < 5; i++) await act(async () => { await new Promise(r => setTimeout(r, 0)) })
}

describe('expired workplace platform in the top bar', () => {
  it('names the platform and opens Settings → Integrations', async () => {
    window.history.pushState({}, '', '/tasks')
    const { router } = await bootApp({
      route: routeWith({ tasks: [], expired: [{ key: 'wecom', title: 'WeCom (企业微信)', expired: ['mail'] }] }),
    })
    const bar = await screen.findByTestId('integration-expired-banner')
    const name = en.integrations.cli.platform.wecom
    expect(bar.textContent).toContain(en.integrationBanner.message.replace('{{name}}', name))
    expect(bar.textContent).toContain(en.integrationBanner.action)
    await act(async () => { fireEvent.click(bar) })
    await settle()
    expect(router.state.location.pathname).toBe('/settings/integrations')
  })

  it('a task waiting for a reply comes first', async () => {
    window.history.pushState({}, '', '/tasks')
    await bootApp({
      route: routeWith({ tasks: [WAITING], expired: [{ key: 'feishu', title: 'Feishu', expired: [] }] }),
    })
    await screen.findByTestId('waiting-task-banner')
    await settle()
    expect(screen.queryByTestId('integration-expired-banner')).toBeNull()
  })

  it('nothing expired: no bar', async () => {
    window.history.pushState({}, '', '/tasks')
    await bootApp({ route: routeWith({ tasks: [], expired: [] }) })
    await settle()
    expect(screen.queryByTestId('integration-expired-banner')).toBeNull()
  })

  it('says it in Chinese too', () => {
    expect(zh.integrationBanner.message.replace('{{name}}', zh.integrations.cli.platform.wecom))
      .toBe('企业微信授权已过期，需要重新授权')
  })
})
