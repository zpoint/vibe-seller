/**
 * The first-run guide (see OnboardingGuide.tsx / useOnboardingGuide.ts).
 *
 * "Seen" is ONE localStorage key per browser, written only when the
 * user clicks Done / Skip / ×. These tests pin: auto-show while the
 * key is absent (and the key NOT written by showing alone), a
 * re-mount before any click shows it again, each dismissal writes it,
 * a second user in the same storage gets no auto-show, replay leaves
 * the key untouched, and a throwing storage still shows once without
 * looping. Plus locale parity and the user-approved copy.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import type { Mock } from 'vitest'
import { act, fireEvent, screen, within } from '@testing-library/react'
import { bootApp } from '../test/appHarness'
import { GUIDE_SEEN_KEY } from '../hooks/useOnboardingGuide'
import { sendEvent } from '../lib/telemetry'
import { FrontendEvent } from '../lib/telemetryEvents'
import zh from '../i18n/locales/zh/translation.json'
import en from '../i18n/locales/en/translation.json'

vi.mock('../lib/telemetry', () => ({
  sendEvent: vi.fn(), lengthBucket: () => 'small', ageBucket: () => 'new',
  registerUserHash: vi.fn(), unregisterUserHash: vi.fn(),
}))

const GUIDE_ID = 'onboarding-guide'

const ME = {
  id: 'user-1', username: 'admin', role: 'admin',
  default_profile_id: 'default',
}

async function boot(me: Record<string, unknown> = ME) {
  window.history.pushState({}, '', '/tasks')
  return bootApp({
    route: (url, init) => {
      if (url === '/api/auth/me' && (init?.method ?? 'GET') === 'GET') return me
      return undefined
    },
  })
}

const freshBrowser = () => localStorage.removeItem(GUIDE_SEEN_KEY)
const seenValue = () => localStorage.getItem(GUIDE_SEEN_KEY)
const settle = async () => {
  for (let i = 0; i < 5; i++) {
    await act(async () => { await new Promise(r => setTimeout(r, 0)) })
  }
}

describe('first-run guide seen-state', () => {
  afterEach(() => { vi.unstubAllGlobals() })

  it('auto-shows while the key is absent, and showing writes nothing', async () => {
    freshBrowser()
    await boot()
    const g = await screen.findByTestId(GUIDE_ID)
    expect(g).toHaveTextContent('Step 1 of 3')
    expect(seenValue()).toBeNull()
  })

  it('a re-mount before any click shows it again', async () => {
    freshBrowser()
    const v1 = await boot()
    await screen.findByTestId(GUIDE_ID)
    await act(async () => { v1.unmount() })
    await boot()
    expect(await screen.findByTestId(GUIDE_ID)).toBeInTheDocument()
    expect(seenValue()).toBeNull()
  })

  it('Done writes the key, and a later mount shows nothing', async () => {
    freshBrowser()
    const v = await boot()
    await screen.findByTestId(GUIDE_ID)
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    fireEvent.click(screen.getByRole('button', { name: 'Done' }))
    expect(screen.queryByTestId(GUIDE_ID)).toBeNull()
    expect(seenValue()).toBeTruthy()
    await act(async () => { v.unmount() })
    await boot()
    await settle()
    expect(screen.queryByTestId(GUIDE_ID)).toBeNull()
  })

  it('Skip and × each write the key', async () => {
    freshBrowser()
    const v1 = await boot()
    await screen.findByTestId(GUIDE_ID)
    fireEvent.click(screen.getByRole('button', { name: 'Skip' }))
    expect(seenValue()).toBeTruthy()
    await act(async () => { v1.unmount() })

    freshBrowser()
    const v2 = await boot()
    await screen.findByTestId(GUIDE_ID)
    fireEvent.click(
      within(screen.getByTestId(GUIDE_ID)).getByRole('button', { name: 'Close' }),
    )
    expect(seenValue()).toBeTruthy()
    await act(async () => { v2.unmount() })

    await boot()
    await settle()
    expect(screen.queryByTestId(GUIDE_ID)).toBeNull()
  })

  it('a second signed-in user in the same storage gets no auto-show', async () => {
    localStorage.setItem(GUIDE_SEEN_KEY, '2026-01-01T00:00:00+00:00')
    await boot({ ...ME, id: 'user-2', username: 'other' })
    await screen.findByText('Example task')
    await settle()
    expect(screen.queryByTestId(GUIDE_ID)).toBeNull()
  })

  it('replay neither writes nor clears the key', async () => {
    const stamp = '2026-01-01T00:00:00+00:00'
    localStorage.setItem(GUIDE_SEEN_KEY, stamp)
    await boot()
    await screen.findByText('Example task')
    fireEvent.click(screen.getByTestId('nav-guide'))
    const g = await screen.findByTestId(GUIDE_ID)
    expect(g).toHaveTextContent('Step 1 of 3')
    fireEvent.click(within(g).getByRole('button', { name: 'Close' }))
    expect(screen.queryByTestId(GUIDE_ID)).toBeNull()
    expect(seenValue()).toBe(stamp)
  })

  it('storage that throws: shows once, dismissal works, no re-show loop', async () => {
    const real = window.localStorage
    real.removeItem(GUIDE_SEEN_KEY)
    // Only the guide key throws — the rest of the app still reads
    // its own localStorage entries normally.
    const guarded = (fn: unknown) => (k: string, v?: string) => {
      if (k === GUIDE_SEEN_KEY) throw new Error('blocked')
      return (fn as (k: string, v?: string) => unknown)(k, v)
    }
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      value: {
        getItem: guarded(real.getItem.bind(real)),
        setItem: guarded(real.setItem.bind(real)),
        removeItem: guarded(real.removeItem.bind(real)),
        key: real.key.bind(real),
        get length() { return real.length },
      },
    })
    try {
      await boot()
      await screen.findByTestId(GUIDE_ID)
      fireEvent.click(screen.getByRole('button', { name: 'Skip' }))
      expect(screen.queryByTestId(GUIDE_ID)).toBeNull()
      // Re-render the app (tab navigation): the in-memory guard must
      // keep it closed even though the key never persisted.
      fireEvent.click(screen.getByRole('button', { name: 'Scheduled' }))
      await settle()
      expect(screen.queryByTestId(GUIDE_ID)).toBeNull()
    } finally {
      Object.defineProperty(window, 'localStorage', {
        configurable: true, value: real,
      })
    }
  })
})

describe('first-run guide copy', () => {
  afterEach(() => { vi.unstubAllGlobals() })

  it('zh and en carry identical onboarding keys', () => {
    const flatKeys = (obj: Record<string, unknown>, prefix = ''): string[] =>
      Object.entries(obj).flatMap(([k, v]) =>
        typeof v === 'object' && v !== null
          ? flatKeys(v as Record<string, unknown>, `${prefix}${k}.`)
          : [`${prefix}${k}`],
      )
    const zhKeys = flatKeys(zh.onboarding).sort()
    const enKeys = flatKeys(en.onboarding).sort()
    expect(zhKeys).toEqual(enKeys)
    expect(zhKeys).toEqual(
      expect.arrayContaining([
        'allStores.body', 'store.body',
        'taskType.inStore', 'taskType.inAllStores',
        'taskType.inStoreLabel', 'taskType.inAllStoresLabel',
      ]),
    )
  })

  it('carries the approved user-reviewed copy (zh)', () => {
    const o = zh.onboarding
    expect(o.allStores.body).toBe(
      '全部店铺下的任务不属于任何店铺，本身进不了店铺后台，适合公开网页查询、跨店铺汇总等工作。',
    )
    expect(o.store.body).toBe(
      '要在某个店铺后台操作，先「新建店铺」绑定它的浏览器（只需操作一次），之后直接点进这个店铺新建任务。',
    )
    expect(o.taskType.inStoreLabel).toBe('在店铺里：')
    expect(o.taskType.inStore).toBe(
      '常规任务创建后立即执行一次，定时任务按设定时间重复执行（如每周一），都只作用于这个店铺。',
    )
    expect(o.taskType.inAllStoresLabel).toBe('在全部店铺里：')
    expect(o.taskType.inAllStores).toBe(
      '常规和定时任务可以为每个店铺创建子任务，一次操作所有店铺。',
    )
  })

  it('step 3 renders two labeled blocks', async () => {
    freshBrowser()
    await boot()
    await screen.findByTestId(GUIDE_ID)
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    const g = screen.getByTestId(GUIDE_ID)
    expect(within(g).getByText('In a store:', { exact: false }))
      .toBeInTheDocument()
    expect(within(g).getByText('In All Stores:', { exact: false }))
      .toBeInTheDocument()
    expect(within(g).getByText(/Both act on that store only/))
      .toBeInTheDocument()
    expect(within(g).getByText(/operate every store in one go/))
      .toBeInTheDocument()
  })
})

describe('first-run guide language switch', () => {
  afterEach(() => { vi.unstubAllGlobals() })

  it('the card header switch re-renders the step and leaves the key alone', async () => {
    freshBrowser()
    await boot()
    const g = await screen.findByTestId(GUIDE_ID)
    fireEvent.click(within(g).getByRole('button', { name: '中文' }))
    expect(await screen.findByText('第 1/3 步')).toBeInTheDocument()
    expect(screen.queryByText('Step 1 of 3')).toBeNull()
    expect(seenValue()).toBeNull()
    fireEvent.click(within(g).getByRole('button', { name: 'English' }))
    expect(await screen.findByText('Step 1 of 3')).toBeInTheDocument()
    expect(seenValue()).toBeNull()
  })
})

describe('guide usage events', () => {
  afterEach(() => { vi.unstubAllGlobals() })
  beforeEach(() => { (sendEvent as unknown as Mock).mockClear() })

  const calls = (name: string) =>
    (sendEvent as unknown as Mock).mock.calls
      .filter((c: unknown[]) => c[0] === name)
      .map((c: unknown[]) => c[1])

  it('auto-show fires shown{auto} once; Skip fires closed{skip, step 1}', async () => {
    freshBrowser()
    await boot()
    await screen.findByTestId(GUIDE_ID)
    expect(calls(FrontendEvent.GUIDE_SHOWN)).toEqual([{ trigger: 'auto' }])
    fireEvent.click(screen.getByRole('button', { name: 'Skip' }))
    expect(calls(FrontendEvent.GUIDE_CLOSED)).toEqual([{ via: 'skip', step: 1 }])
  })

  it('step changes do not re-fire shown; Done fires closed{done, step 3}', async () => {
    freshBrowser()
    await boot()
    await screen.findByTestId(GUIDE_ID)
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    expect(calls(FrontendEvent.GUIDE_SHOWN)).toHaveLength(1)
    fireEvent.click(screen.getByRole('button', { name: 'Done' }))
    expect(calls(FrontendEvent.GUIDE_CLOSED)).toEqual([{ via: 'done', step: 3 }])
  })

  it('replay fires shown{replay}; the X fires closed{close, step 1}', async () => {
    localStorage.setItem(GUIDE_SEEN_KEY, '2026-01-01T00:00:00+00:00')
    await boot()
    await screen.findByText('Example task')
    fireEvent.click(screen.getByTestId('nav-guide'))
    await screen.findByTestId(GUIDE_ID)
    expect(calls(FrontendEvent.GUIDE_SHOWN)).toEqual([{ trigger: 'replay' }])
    fireEvent.click(screen.getByRole('button', { name: 'Close' }))
    expect(calls(FrontendEvent.GUIDE_CLOSED)).toEqual([{ via: 'close', step: 1 }])
  })

  it('the in-card language switch fires switched{to} per click', async () => {
    freshBrowser()
    await boot()
    const g = await screen.findByTestId(GUIDE_ID)
    fireEvent.click(within(g).getByRole('button', { name: '中文' }))
    fireEvent.click(within(g).getByRole('button', { name: 'English' }))
    expect(calls(FrontendEvent.GUIDE_LANGUAGE_SWITCHED)).toEqual([
      { to: 'zh' }, { to: 'en' },
    ])
  })
})
