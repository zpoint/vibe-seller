/**
 * Frontend tests for the AI-provider config modal (ProfileModal).
 *
 * Covers the behaviors that make the UX changes real:
 * - API key is required to submit (button gated).
 * - Save runs the endpoint validation first: a bad config blocks the
 *   save and shows the reason; a good config proceeds.
 * - "Set as default" is on by default and flows through onSave.
 * - Custom (no preset): a user can type base URL + key + model.
 * - Preset: model options render as chips with context/vision badges,
 *   and picking one auto-names the profile "Provider - Model".
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { I18nextProvider, initReactI18next } from 'react-i18next'
import i18n from 'i18next'
import enTranslation from '../i18n/locales/en/translation.json'

// Mock the api client so validation + save are controllable per test.
vi.mock('../api', () => ({
  api: { post: vi.fn(), get: vi.fn(), put: vi.fn(), patch: vi.fn(), del: vi.fn() },
  AUTH_EXPIRED_EVENT: 'auth:expired',
}))
import { api } from '../api'
import { ProfileModal } from '../components/ProfileModal'

const makeI18n = () => {
  const inst = i18n.createInstance()
  inst.use(initReactI18next).init({
    resources: { en: { translation: enTranslation } },
    lng: 'en',
    fallbackLng: 'en',
    interpolation: { escapeValue: false },
  })
  return inst
}

const DEEPSEEK_PRESET = {
  name: 'DeepSeek',
  description: 'DeepSeek V4',
  load_global_mcp: false,
  env: {
    ANTHROPIC_BASE_URL: 'https://api.deepseek.com/anthropic',
    ANTHROPIC_MODEL: 'deepseek-v4-pro[1m]',
    ANTHROPIC_DEFAULT_OPUS_MODEL: 'deepseek-v4-pro[1m]',
    ANTHROPIC_SMALL_FAST_MODEL: 'deepseek-v4-flash',
  },
  models: [
    { id: 'deepseek-v4-pro[1m]', label: 'V4 Pro (1M context)', context: '1M', vision: false },
    { id: 'deepseek-v4-flash', label: 'V4 Flash', context: '1M', vision: false },
  ],
}

const VISION_PRESET = {
  name: 'MiniMax',
  description: 'MiniMax',
  env: {
    ANTHROPIC_BASE_URL: 'https://api.minimaxi.com/anthropic',
    ANTHROPIC_MODEL: 'MiniMax-M3[1m]',
  },
  models: [
    { id: 'MiniMax-M3[1m]', label: 'M3 (1M context)', context: '1M', vision: true },
    { id: 'MiniMax-M2.5', label: 'M2.5 (cheaper)', context: '200K', vision: true },
  ],
}

const ALIBABA_PAYGO = {
  name: 'Qwen (Pay-as-you-go, China)',
  group: 'Alibaba Cloud',
  variant: 'Pay-as-you-go (China)',
  env: {
    ANTHROPIC_BASE_URL: 'https://dashscope.aliyuncs.com/apps/anthropic',
    ANTHROPIC_MODEL: 'qwen3.7-max',
  },
  models: [{ id: 'qwen3.7-max', label: 'Qwen3.7-Max', context: '1M', vision: false }],
}

const ALIBABA_CODING = {
  name: 'Qwen (Coding Plan)',
  group: 'Alibaba Cloud',
  variant: 'Coding Plan',
  env: {
    ANTHROPIC_BASE_URL: 'https://coding.dashscope.aliyuncs.com/apps/anthropic',
    ANTHROPIC_MODEL: 'qwen3.7-plus',
  },
  models: [{ id: 'qwen3.7-plus', label: 'Qwen3.7-Plus', context: '1M', vision: false }],
}

const INTL_PRESET = {
  name: 'Qwen Intl',
  env: {
    ANTHROPIC_BASE_URL:
      'https://{WorkspaceId}.ap-southeast-1.maas.aliyuncs.com/apps/anthropic',
    ANTHROPIC_MODEL: 'qwen3.7-max',
  },
  models: [{ id: 'qwen3.7-max', label: 'Qwen3.7-Max', context: '1M', vision: false }],
}

function mockPresets(presets: Record<string, unknown>) {
  ;(global.fetch as unknown) = vi.fn().mockResolvedValue({
    json: async () => ({ presets }),
  })
}

function renderModal(props: Partial<React.ComponentProps<typeof ProfileModal>> = {}) {
  const onSave = vi.fn().mockResolvedValue(undefined)
  const onClose = vi.fn()
  render(
    <I18nextProvider i18n={makeI18n()}>
      <ProfileModal isOpen onClose={onClose} onSave={onSave} {...props} />
    </I18nextProvider>
  )
  return { onSave, onClose }
}

beforeEach(() => {
  vi.clearAllMocks()
  mockPresets({})
})

describe('ProfileModal', () => {
  it('gates the create button on a non-empty API key', async () => {
    renderModal()
    const createBtn = screen.getByRole('button', { name: 'Create' })
    expect(createBtn).toBeDisabled()

    fireEvent.change(screen.getByPlaceholderText('e.g., MiniMax'), {
      target: { value: 'My Provider' },
    })
    // Still disabled without a key.
    expect(createBtn).toBeDisabled()

    fireEvent.change(
      screen.getByPlaceholderText('Paste your provider API key'),
      { target: { value: 'sk-abc' } }
    )
    expect(createBtn).toBeEnabled()
  })

  it('validates a good config then saves with setAsDefault=true (custom)', async () => {
    ;(api.post as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: true })
    const { onSave, onClose } = renderModal()

    fireEvent.change(screen.getByPlaceholderText('e.g., MiniMax'), {
      target: { value: 'Custom Co' },
    })
    fireEvent.change(
      screen.getByPlaceholderText('Paste your provider API key'),
      { target: { value: 'sk-good' } }
    )
    fireEvent.change(
      screen.getByPlaceholderText('https://api.example.com/anthropic'),
      { target: { value: 'https://api.custom.com/anthropic' } }
    )
    fireEvent.change(
      screen.getByPlaceholderText('e.g., deepseek-v4-pro[1m]'),
      { target: { value: 'my-model' } }
    )

    fireEvent.click(screen.getByRole('button', { name: 'Create' }))

    await waitFor(() => expect(api.post).toHaveBeenCalledWith(
      '/api/profiles/validate',
      expect.objectContaining({
        env: expect.objectContaining({
          ANTHROPIC_AUTH_TOKEN: 'sk-good',
          ANTHROPIC_BASE_URL: 'https://api.custom.com/anthropic',
          ANTHROPIC_MODEL: 'my-model',
        }),
      })
    ))
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1))
    expect(onSave.mock.calls[0][1]).toEqual({ setAsDefault: true })
    expect(onClose).toHaveBeenCalled()
  })

  it('blocks the save and shows the reason when validation fails', async () => {
    ;(api.post as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: false,
      code: 'auth',
      error: 'Authentication failed (HTTP 401).',
    })
    const { onSave, onClose } = renderModal()

    fireEvent.change(screen.getByPlaceholderText('e.g., MiniMax'), {
      target: { value: 'Bad Key Co' },
    })
    fireEvent.change(
      screen.getByPlaceholderText('Paste your provider API key'),
      { target: { value: 'sk-bad' } }
    )
    fireEvent.change(
      screen.getByPlaceholderText('https://api.example.com/anthropic'),
      { target: { value: 'https://api.custom.com/anthropic' } }
    )

    fireEvent.click(screen.getByRole('button', { name: 'Create' }))

    await waitFor(() =>
      expect(screen.getByText(/Authentication failed/)).toBeInTheDocument()
    )
    expect(onSave).not.toHaveBeenCalled()
    expect(onClose).not.toHaveBeenCalled()
  })

  it('lets the user turn off set-as-default', async () => {
    ;(api.post as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: true })
    const { onSave } = renderModal()

    // The setAsDefault toggle is the 2nd checkbox (after load-global-mcp).
    const checkboxes = screen.getAllByRole('checkbox')
    const setDefault = checkboxes[checkboxes.length - 1]
    expect(setDefault).toBeChecked()
    fireEvent.click(setDefault)

    fireEvent.change(screen.getByPlaceholderText('e.g., MiniMax'), {
      target: { value: 'No Default' },
    })
    fireEvent.change(
      screen.getByPlaceholderText('Paste your provider API key'),
      { target: { value: 'sk-x' } }
    )
    fireEvent.click(screen.getByRole('button', { name: 'Create' }))

    await waitFor(() => expect(onSave).toHaveBeenCalled())
    expect(onSave.mock.calls[0][1]).toEqual({ setAsDefault: false })
  })

  it('renders model chips with context + vision badges from a preset', async () => {
    mockPresets({ minimax: VISION_PRESET })
    renderModal()

    const presetBtn = await screen.findByRole('button', { name: 'MiniMax' })
    fireEvent.click(presetBtn)

    // Model chips + metadata badges.
    expect(await screen.findByText('M3 (1M context)')).toBeInTheDocument()
    expect(screen.getByText('M2.5 (cheaper)')).toBeInTheDocument()
    expect(screen.getAllByText('Vision').length).toBeGreaterThan(0)
    expect(screen.getByText('200K')).toBeInTheDocument()

    // Auto-named "Provider - <default model label>".
    expect((screen.getByPlaceholderText('e.g., MiniMax') as HTMLInputElement).value)
      .toBe('MiniMax - M3 (1M context)')
  })

  it('shows a text-only badge and syncs model + name when a cheaper chip is picked', async () => {
    mockPresets({ deepseek: DEEPSEEK_PRESET })
    ;(api.post as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: true })
    const { onSave } = renderModal()

    fireEvent.click(await screen.findByRole('button', { name: 'DeepSeek' }))
    expect(screen.getAllByText('Text only').length).toBeGreaterThan(0)

    // Pick the cheaper flash model.
    fireEvent.click(await screen.findByText('V4 Flash'))
    expect((screen.getByPlaceholderText('e.g., MiniMax') as HTMLInputElement).value)
      .toBe('DeepSeek - V4 Flash')

    fireEvent.change(
      screen.getByPlaceholderText('Paste your provider API key'),
      { target: { value: 'sk-ds' } }
    )
    fireEvent.click(screen.getByRole('button', { name: 'Create' }))

    await waitFor(() => expect(onSave).toHaveBeenCalled())
    const savedEnv = onSave.mock.calls[0][0].env
    expect(savedEnv.ANTHROPIC_MODEL).toBe('deepseek-v4-flash')
    // The opus alias mirrored the old flagship, so it follows along.
    expect(savedEnv.ANTHROPIC_DEFAULT_OPUS_MODEL).toBe('deepseek-v4-flash')
    // The distinct fast tier is left untouched.
    expect(savedEnv.ANTHROPIC_SMALL_FAST_MODEL).toBe('deepseek-v4-flash')
  })

  it('collapses grouped providers under one button with a variant sub-row', async () => {
    mockPresets({ qwen: ALIBABA_PAYGO, qwen_coding: ALIBABA_CODING })
    renderModal()

    // Top level shows the GROUP, not the individual plans.
    const groupBtn = await screen.findByRole('button', { name: /Alibaba Cloud/ })
    expect(screen.queryByRole('button', { name: 'Coding Plan' })).toBeNull()

    // Selecting the group reveals its variant sub-buttons AND applies
    // the first variant immediately (fields populate, not left stale).
    fireEvent.click(groupBtn)
    expect(
      await screen.findByRole('button', { name: 'Pay-as-you-go (China)' })
    ).toBeInTheDocument()
    const nameInput = screen.getByPlaceholderText('e.g., MiniMax') as HTMLInputElement
    expect(nameInput.value).toBe('Qwen (Pay-as-you-go, China) - Qwen3.7-Max')

    // Switching to another variant re-applies that preset.
    fireEvent.click(screen.getByRole('button', { name: 'Coding Plan' }))
    expect(nameInput.value).toBe('Qwen (Coding Plan) - Qwen3.7-Plus')
    expect(screen.getByText('Qwen3.7-Plus')).toBeInTheDocument()
  })

  it('seeds the Custom advanced template and drops blank rows on save', async () => {
    // The selector (and its Custom button) render only when presets exist.
    mockPresets({ deepseek: DEEPSEEK_PRESET })
    ;(api.post as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: true })
    const { onSave } = renderModal()

    fireEvent.click(await screen.findByRole('button', { name: 'Custom' }))

    // Advanced expands with the common env keys pre-seeded (empty).
    expect(
      await screen.findByDisplayValue('API_TIMEOUT_MS')
    ).toBeInTheDocument()
    expect(screen.getByDisplayValue('CLAUDE_CODE_SUBAGENT_MODEL')).toBeInTheDocument()

    // Fill the primary fields...
    fireEvent.change(screen.getByPlaceholderText('e.g., MiniMax'), {
      target: { value: 'My Custom' },
    })
    fireEvent.change(
      screen.getByPlaceholderText('Paste your provider API key'),
      { target: { value: 'sk-c' } }
    )
    fireEvent.change(
      screen.getByPlaceholderText('https://api.example.com/anthropic'),
      { target: { value: 'https://api.c.com/anthropic' } }
    )
    fireEvent.change(
      screen.getByPlaceholderText('e.g., deepseek-v4-pro[1m]'),
      { target: { value: 'my-model' } }
    )
    // ...and exactly one advanced value (API_TIMEOUT_MS is the first row).
    fireEvent.change(screen.getAllByPlaceholderText('value')[0], {
      target: { value: '3000000' },
    })

    fireEvent.click(screen.getByRole('button', { name: 'Create' }))
    await waitFor(() => expect(onSave).toHaveBeenCalled())

    const env = onSave.mock.calls[0][0].env
    expect(env.ANTHROPIC_AUTH_TOKEN).toBe('sk-c')
    expect(env.ANTHROPIC_BASE_URL).toBe('https://api.c.com/anthropic')
    expect(env.ANTHROPIC_MODEL).toBe('my-model')
    expect(env.API_TIMEOUT_MS).toBe('3000000')
    // Untouched (blank) template keys are dropped, not persisted empty.
    expect(env.CLAUDE_CODE_SUBAGENT_MODEL).toBeUndefined()
    expect(env.ANTHROPIC_DEFAULT_OPUS_MODEL).toBeUndefined()
  })

  it('blocks save until a {placeholder} in the Base URL is replaced', async () => {
    mockPresets({ intl: INTL_PRESET })
    renderModal()

    fireEvent.click(await screen.findByRole('button', { name: 'Qwen Intl' }))
    fireEvent.change(
      screen.getByPlaceholderText('Paste your provider API key'),
      { target: { value: 'sk-i' } }
    )

    // Name + key are set, but the {WorkspaceId} placeholder blocks save.
    const createBtn = screen.getByRole('button', { name: 'Create' })
    expect(createBtn).toBeDisabled()

    // Replace the placeholder with a real host -> save unblocks.
    const baseInput = screen.getByDisplayValue(/\{WorkspaceId\}/)
    fireEvent.change(baseInput, {
      target: {
        value: 'https://ws-abc.ap-southeast-1.maas.aliyuncs.com/apps/anthropic',
      },
    })
    expect(createBtn).toBeEnabled()
  })
})
