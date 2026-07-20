import { useState, useEffect, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../api'

interface Profile {
  id: string
  name: string
  description: string
  env: Record<string, string>
  load_global_mcp?: boolean
}

interface ModelOption {
  id: string
  label: string
  context?: string
  vision?: boolean
}

interface ProviderPreset {
  name: string
  description: string
  env: Record<string, string>
  load_global_mcp?: boolean
  models?: ModelOption[]
  // Presets sharing a `group` (e.g. "Alibaba Cloud", "GLM") collapse
  // into one top-level button that reveals its `variant` sub-buttons.
  group?: string
  variant?: string
}

interface ProfileModalProps {
  isOpen: boolean
  onClose: () => void
  onSave: (
    profile: Profile,
    opts: { setAsDefault: boolean }
  ) => void | Promise<void>
  editingProfile?: Profile
}

// The three fields worth promoting to the top of the form. Everything
// else (timeouts, per-tier model overrides, CLAUDE_CODE_* flags) is
// preset-filled noise the user rarely touches, so it lives collapsed.
const BASE_URL_KEY = 'ANTHROPIC_BASE_URL'
const MODEL_KEY = 'ANTHROPIC_MODEL'
// Claude Code accepts either token env var; presets seed AUTH_TOKEN.
const API_KEY_CANDIDATES = ['ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_API_KEY']
// The per-tier model aliases a preset points at the flagship. When the
// user switches the main model, every slot that mirrored the previous
// main value follows it (so e.g. Kimi's all-same slots stay coherent),
// while a distinct fast/haiku tier (e.g. deepseek-v4-flash) is left be.
const MODEL_TIER_KEYS = [
  'ANTHROPIC_DEFAULT_OPUS_MODEL',
  'ANTHROPIC_DEFAULT_SONNET_MODEL',
  'ANTHROPIC_DEFAULT_HAIKU_MODEL',
  'ANTHROPIC_SMALL_FAST_MODEL',
  'CLAUDE_CODE_SUBAGENT_MODEL',
]
// For a from-scratch (Custom) profile the Advanced section is otherwise
// empty; we seed it with the keys presets commonly set so the user only
// fills values. Blank rows are dropped on save, so leaving any untouched
// is fine. (Excludes the promoted primary fields: base URL, model, key.)
const CUSTOM_ENV_TEMPLATE = [
  'API_TIMEOUT_MS',
  'ANTHROPIC_SMALL_FAST_MODEL',
  'ANTHROPIC_DEFAULT_SONNET_MODEL',
  'ANTHROPIC_DEFAULT_OPUS_MODEL',
  'ANTHROPIC_DEFAULT_HAIKU_MODEL',
  'CLAUDE_CODE_SUBAGENT_MODEL',
]

type EnvRow = { key: string; value: string }

export function ProfileModal({
  isOpen,
  onClose,
  onSave,
  editingProfile,
}: ProfileModalProps) {
  if (!isOpen) return null

  return (
    <div className="fixed inset-0 bg-black/50 flex items-end sm:items-center justify-center z-50 sm:p-4">
      <ProfileForm
        key={editingProfile?.id ?? '__new__'}
        editingProfile={editingProfile}
        onClose={onClose}
        onSave={onSave}
      />
    </div>
  )
}

/** Inner form component — remounted via key prop when profile changes. */
function ProfileForm({
  editingProfile,
  onClose,
  onSave,
}: {
  editingProfile?: Profile
  onClose: () => void
  onSave: (
    profile: Profile,
    opts: { setAsDefault: boolean }
  ) => void | Promise<void>
}) {
  const { t } = useTranslation()

  const [name, setName] = useState(editingProfile?.name ?? '')
  // Track manual name edits so auto-naming ("Kimi - K3") never
  // clobbers a name the user typed; editing an existing profile starts
  // "edited" so we never rename it out from under them.
  const [nameEdited, setNameEdited] = useState(!!editingProfile)
  const [modelCustom, setModelCustom] = useState(false)
  const [envVars, setEnvVars] = useState<EnvRow[]>(
    editingProfile
      ? Object.entries(editingProfile.env).map(([k, v]) => ({ key: k, value: v }))
      : []
  )
  const [loadGlobalMcp, setLoadGlobalMcp] = useState(
    editingProfile?.load_global_mcp ?? false
  )
  const [presets, setPresets] = useState<Record<string, ProviderPreset>>({})
  const [selectedPreset, setSelectedPreset] = useState('')
  // Which grouped provider's variant row is expanded (e.g. "Alibaba
  // Cloud"). Empty when a standalone/custom top-level entry is active.
  const [selectedGroup, setSelectedGroup] = useState('')
  const [error, setError] = useState('')
  // Default to on: the whole point is to save the extra click.
  const [setAsDefault, setSetAsDefault] = useState(true)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [validating, setValidating] = useState(false)

  // Fetch provider presets once on mount
  useEffect(() => {
    let cancelled = false
    fetch('/api/profiles/presets', { credentials: 'include' })
      .then((r) => r.json())
      .then((data) => {
        if (!cancelled) setPresets(data.presets || {})
      })
      .catch(() => {})
    return () => { cancelled = true }
  }, [])

  // Which env var holds the API key for this profile: prefer an
  // existing AUTH_TOKEN/API_KEY row, else default to AUTH_TOKEN.
  const apiKeyName = useMemo(() => {
    for (const candidate of API_KEY_CANDIDATES) {
      if (envVars.some((e) => e.key === candidate)) return candidate
    }
    return API_KEY_CANDIDATES[0]
  }, [envVars])

  const getEnvValue = (key: string) =>
    envVars.find((e) => e.key === key)?.value ?? ''

  const setEnvValue = (key: string, value: string) => {
    setEnvVars((prev) => {
      const idx = prev.findIndex((e) => e.key === key)
      if (idx >= 0) {
        const next = [...prev]
        next[idx] = { ...next[idx], value }
        return next
      }
      return [...prev, { key, value }]
    })
  }

  // Advanced rows = everything not surfaced as a primary field. Carry
  // the original index so edits/removes hit the right envVars entry.
  const primaryKeys = new Set([BASE_URL_KEY, MODEL_KEY, apiKeyName])
  const advancedRows = envVars
    .map((row, idx) => ({ row, idx }))
    .filter(({ row }) => !primaryKeys.has(row.key))

  // Which built-in preset (if any) this config's base URL matches —
  // drives the model dropdown for both preset-created and edited
  // profiles. A custom base URL matches nothing → free-text model only.
  const matchedPreset = useMemo(() => {
    const base = (
      envVars.find((e) => e.key === BASE_URL_KEY)?.value ?? ''
    ).trim()
    if (!base) return undefined
    return Object.values(presets).find(
      (p) => (p.env?.ANTHROPIC_BASE_URL ?? '') === base
    )
  }, [presets, envVars])
  const modelOptions = matchedPreset?.models ?? []
  const currentModel = getEnvValue(MODEL_KEY)
  const isKnownModel = modelOptions.some((m) => m.id === currentModel)
  const showModelText =
    modelOptions.length === 0 || modelCustom || !isKnownModel

  const applyAutoName = (modelLabel: string) => {
    if (nameEdited) return
    const provider = matchedPreset?.name
    if (provider && modelLabel) setName(`${provider} - ${modelLabel}`)
  }

  // Switch the main model, dragging along any per-tier alias that
  // mirrored the previous main value (see MODEL_TIER_KEYS) so a
  // provider whose slots are all-the-same stays coherent.
  const syncModel = (newModel: string) => {
    setEnvVars((prev) => {
      const oldModel = prev.find((e) => e.key === MODEL_KEY)?.value ?? ''
      let found = false
      const next = prev.map((row) => {
        if (row.key === MODEL_KEY) {
          found = true
          return { ...row, value: newModel }
        }
        if (
          MODEL_TIER_KEYS.includes(row.key) &&
          oldModel !== '' &&
          row.value === oldModel
        ) {
          return { ...row, value: newModel }
        }
        return row
      })
      if (!found) next.push({ key: MODEL_KEY, value: newModel })
      return next
    })
  }

  const chooseModel = (opt: ModelOption) => {
    setModelCustom(false)
    syncModel(opt.id)
    applyAutoName(opt.label)
  }

  const applyPreset = (presetId: string) => {
    const preset = presets[presetId]
    if (!preset) return

    setSelectedPreset(presetId)
    setSelectedGroup(preset.group ?? '')
    setLoadGlobalMcp(preset.load_global_mcp ?? false)
    setError('')
    setShowAdvanced(false)
    setModelCustom(false)
    setNameEdited(false)

    // Fill all env vars from preset + empty ANTHROPIC_AUTH_TOKEN row
    const vars = Object.entries(preset.env).map(([k, v]) => ({ key: k, value: v }))
    vars.push({ key: 'ANTHROPIC_AUTH_TOKEN', value: '' })
    setEnvVars(vars)

    // Auto-name "Provider - <default model label>".
    const defaultModel = preset.env.ANTHROPIC_MODEL ?? ''
    const label =
      preset.models?.find((m) => m.id === defaultModel)?.label ?? defaultModel
    setName(label ? `${preset.name} - ${label}` : preset.name)
  }

  // Top-level provider entries: standalone presets stay as-is; presets
  // sharing a `group` collapse into one entry (first occurrence wins
  // ordering). Selecting a group reveals its variant row below.
  const providerEntries = useMemo(() => {
    const seen = new Set<string>()
    const entries: Array<
      | { kind: 'preset'; id: string; label: string }
      | { kind: 'group'; group: string }
    > = []
    for (const [id, p] of Object.entries(presets)) {
      if (p.group) {
        if (!seen.has(p.group)) {
          seen.add(p.group)
          entries.push({ kind: 'group', group: p.group })
        }
      } else {
        entries.push({ kind: 'preset', id, label: p.name })
      }
    }
    return entries
  }, [presets])

  const groupVariants = (group: string) =>
    Object.entries(presets)
      .filter(([, p]) => p.group === group)
      .map(([id, p]) => ({ id, label: p.variant || p.name }))

  const selectCustom = () => {
    setSelectedPreset('custom')
    setSelectedGroup('')
    setName('')
    setNameEdited(false)
    setLoadGlobalMcp(false)
    setError('')
    setModelCustom(false)
    // Seed the common env keys (empty) so Advanced isn't blank — the
    // user just fills values; blanks are dropped on save. Reveal it.
    setEnvVars([
      { key: 'ANTHROPIC_AUTH_TOKEN', value: '' },
      { key: BASE_URL_KEY, value: '' },
      { key: MODEL_KEY, value: '' },
      ...CUSTOM_ENV_TEMPLATE.map((key) => ({ key, value: '' })),
    ])
    setShowAdvanced(true)
  }

  const addEnvVar = () => {
    setEnvVars([...envVars, { key: '', value: '' }])
    setShowAdvanced(true)
  }

  const updateEnvKey = (idx: number, newKey: string) => {
    const updated = [...envVars]
    updated[idx].key = newKey
    setEnvVars(updated)
  }

  const updateEnvValue = (idx: number, newValue: string) => {
    const updated = [...envVars]
    updated[idx].value = newValue
    setEnvVars(updated)
  }

  const removeEnv = (idx: number) => {
    setEnvVars(envVars.filter((_, i) => i !== idx))
  }

  const buildEnv = (): Record<string, string> => {
    const env: Record<string, string> = {}
    envVars.forEach(({ key, value }) => {
      // Drop blank-valued rows: the Custom template and any cleared
      // preset key count as "not set" rather than persisting an empty.
      if (key.trim() && value.trim()) env[key.trim()] = value
    })
    return env
  }

  const apiKeyMissing = !getEnvValue(apiKeyName).trim()
  const canSubmit = !!name.trim() && !apiKeyMissing && !validating

  const handleSave = async () => {
    setError('')
    if (!name.trim()) {
      setError(t('profiles.nameRequired') || 'Profile name is required')
      return
    }
    if (apiKeyMissing) {
      setError(t('profiles.apiKeyRequired') || 'API key is required')
      return
    }

    const env = buildEnv()

    // Gate: probe the endpoint before persisting. An unreachable base
    // URL, a wrong key, or a retired model id fails here rather than on
    // the next agent run.
    setValidating(true)
    try {
      const res = await api.post('/api/profiles/validate', { env })
      if (!res.ok) {
        setError(
          `${t('profiles.validationFailed')}: ${res.error || ''}`.trim()
        )
        // The usual culprit (base URL) lives in Advanced — reveal it.
        setShowAdvanced(true)
        return
      }
    } catch (e) {
      setError(
        `${t('profiles.validationError')}: ${(e as Error).message}`.trim()
      )
      return
    } finally {
      setValidating(false)
    }

    // Persist. onSave may throw (e.g. external-config 409) — keep the
    // modal open and surface the reason.
    try {
      await onSave(
        {
          id:
            editingProfile?.id ||
            name
              .toLowerCase()
              .replace(/[^a-z0-9]+/g, '-')
              .replace(/^-+|-+$/g, ''),
          name: name.trim(),
          description: '',
          env,
          load_global_mcp: loadGlobalMcp,
        },
        { setAsDefault: editingProfile ? false : setAsDefault }
      )
      onClose()
    } catch (e) {
      setError((e as Error).message || 'Save failed')
    }
  }

  return (
    <div className="bg-white rounded-t-2xl sm:rounded-lg shadow-xl w-full sm:max-w-2xl max-h-[90vh] overflow-y-auto">
      <div className="p-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-xl font-semibold">
            {editingProfile
              ? t('profiles.editTitle')
              : t('profiles.createTitle')}
          </h2>
          <button
            onClick={onClose}
            className="text-gray-500 hover:text-gray-700 text-2xl leading-none"
          >
            &times;
          </button>
        </div>

        <p className="text-sm text-gray-500 mb-2">{t('profiles.modalHint')}</p>

        {error && (
          <div className="mb-4 p-3 bg-red-100 text-red-700 rounded whitespace-pre-wrap break-words">
            {error}
          </div>
        )}

        <div className="space-y-4">
          {/* Provider preset selector — only when creating. Grouped
              providers (Alibaba Cloud, GLM) show a variant sub-row. */}
          {!editingProfile && Object.keys(presets).length > 0 && (
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                {t('profiles.envPresets')}
              </label>
              <div className="flex flex-wrap gap-2">
                {providerEntries.map((entry) =>
                  entry.kind === 'preset' ? (
                    <button
                      key={entry.id}
                      onClick={() => applyPreset(entry.id)}
                      className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                        selectedPreset === entry.id
                          ? 'bg-indigo-600 text-white'
                          : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                      }`}
                    >
                      {entry.label}
                    </button>
                  ) : (
                    <button
                      key={entry.group}
                      onClick={() => setSelectedGroup(entry.group)}
                      className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                        selectedGroup === entry.group
                          ? 'bg-indigo-600 text-white'
                          : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                      }`}
                    >
                      {entry.group} ›
                    </button>
                  )
                )}
                <button
                  onClick={selectCustom}
                  className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                    selectedPreset === 'custom'
                      ? 'bg-indigo-600 text-white'
                      : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                  }`}
                >
                  {t('profiles.custom')}
                </button>
              </div>

              {/* Variant sub-row for the selected group */}
              {selectedGroup && (
                <div className="flex flex-wrap gap-2 mt-2 pl-3 border-l-2 border-indigo-200">
                  {groupVariants(selectedGroup).map((v) => (
                    <button
                      key={v.id}
                      onClick={() => applyPreset(v.id)}
                      className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
                        selectedPreset === v.id
                          ? 'bg-indigo-600 text-white'
                          : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                      }`}
                    >
                      {v.label}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Profile Name */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              {t('profiles.profileName')}
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => {
                setName(e.target.value)
                setNameEdited(true)
              }}
              className="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
              placeholder={t('profiles.namePlaceholder')}
            />
          </div>

          {/* API Key — the one required credential, front and center */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              {t('profiles.apiKey')} <span className="text-red-500">*</span>
            </label>
            <input
              type="password"
              value={getEnvValue(apiKeyName)}
              onChange={(e) => setEnvValue(apiKeyName, e.target.value)}
              autoComplete="off"
              className={`w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 ${
                apiKeyMissing ? 'border-red-300' : ''
              }`}
              placeholder={t('profiles.apiKeyPlaceholder')}
            />
          </div>

          {/* Model — chips (with context/vision badges) when the base
              URL matches a known provider, else free-text. */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              {t('profiles.model')}
            </label>
            {modelOptions.length > 0 && (
              <div className="flex flex-wrap gap-2 mb-2">
                {modelOptions.map((m) => {
                  const active = !showModelText && currentModel === m.id
                  return (
                    <button
                      key={m.id}
                      type="button"
                      onClick={() => chooseModel(m)}
                      className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors flex items-center gap-1.5 ${
                        active
                          ? 'bg-indigo-600 text-white'
                          : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                      }`}
                    >
                      <span>{m.label}</span>
                      {m.context && (
                        <span
                          className={`text-[10px] px-1 rounded ${
                            active ? 'bg-indigo-500' : 'bg-gray-200 text-gray-600'
                          }`}
                        >
                          {m.context}
                        </span>
                      )}
                      {m.vision === true && (
                        <span
                          className={`text-[10px] px-1 rounded ${
                            active
                              ? 'bg-indigo-500'
                              : 'bg-emerald-100 text-emerald-700'
                          }`}
                        >
                          {t('profiles.vision')}
                        </span>
                      )}
                      {m.vision === false && (
                        <span
                          className={`text-[10px] px-1 rounded ${
                            active ? 'bg-indigo-500' : 'bg-gray-200 text-gray-500'
                          }`}
                        >
                          {t('profiles.textOnly')}
                        </span>
                      )}
                    </button>
                  )
                })}
                <button
                  type="button"
                  onClick={() => setModelCustom(true)}
                  className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
                    showModelText
                      ? 'bg-indigo-600 text-white'
                      : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                  }`}
                >
                  {t('profiles.customModel')}
                </button>
              </div>
            )}
            {showModelText && (
              <input
                type="text"
                value={currentModel}
                onChange={(e) => {
                  syncModel(e.target.value)
                  applyAutoName(e.target.value)
                }}
                className="w-full px-3 py-2 border rounded-lg text-sm font-mono focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
                placeholder={t('profiles.modelPlaceholder')}
              />
            )}
          </div>

          {/* Base URL */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              {BASE_URL_KEY}
            </label>
            <input
              type="text"
              value={getEnvValue(BASE_URL_KEY)}
              onChange={(e) => setEnvValue(BASE_URL_KEY, e.target.value)}
              className="w-full px-3 py-2 border rounded-lg text-sm font-mono focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
              placeholder="https://api.example.com/anthropic"
            />
          </div>

          {/* Advanced env vars — collapsed by default */}
          <div className="border rounded-lg">
            <button
              type="button"
              onClick={() => setShowAdvanced((v) => !v)}
              className="w-full flex items-center justify-between px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 rounded-lg"
            >
              <span>
                {t('profiles.advancedEnv')}
                {advancedRows.length > 0 && (
                  <span className="ml-2 text-xs text-gray-400">
                    {t('profiles.advancedCount', { count: advancedRows.length })}
                  </span>
                )}
              </span>
              <span className={`transition-transform ${showAdvanced ? 'rotate-90' : ''}`}>
                &#8250;
              </span>
            </button>

            {showAdvanced && (
              <div className="px-3 pb-3 pt-1 border-t">
                <p className="text-xs text-gray-500 mb-2">
                  {t('profiles.advancedEnvHint')}
                </p>
                {advancedRows.length === 0 ? (
                  <p className="text-sm text-gray-500 italic">
                    {t('profiles.noVariables')}
                  </p>
                ) : (
                  <div className="space-y-2">
                    {advancedRows.map(({ row, idx }) => (
                      <div key={idx} className="flex gap-2">
                        <input
                          type="text"
                          placeholder="KEY"
                          value={row.key}
                          onChange={(e) => updateEnvKey(idx, e.target.value)}
                          className="flex-1 px-3 py-2 border rounded-lg text-sm font-mono focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
                        />
                        <input
                          type="text"
                          placeholder="value"
                          value={row.value}
                          onChange={(e) => updateEnvValue(idx, e.target.value)}
                          className="flex-[2] px-3 py-2 border rounded-lg text-sm focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
                        />
                        <button
                          onClick={() => removeEnv(idx)}
                          className="px-3 py-2 text-red-600 hover:text-red-700 hover:bg-red-50 rounded-lg transition-colors"
                          title={t('common.remove')}
                        >
                          &times;
                        </button>
                      </div>
                    ))}
                  </div>
                )}
                <button
                  onClick={addEnvVar}
                  className="mt-2 text-sm text-indigo-600 hover:text-indigo-700 font-medium"
                >
                  + {t('profiles.addVariable')}
                </button>
              </div>
            )}
          </div>

          {/* Load Global MCP toggle */}
          <div className="flex items-center gap-3">
            <label className="relative inline-flex items-center cursor-pointer">
              <input
                type="checkbox"
                checked={loadGlobalMcp}
                onChange={(e) => setLoadGlobalMcp(e.target.checked)}
                className="sr-only peer"
              />
              <div className="w-9 h-5 bg-gray-200 peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-indigo-300 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-indigo-600" />
            </label>
            <div>
              <span className="text-sm font-medium text-gray-700">
                {t('profiles.loadGlobalMcp')}
              </span>
              <p className="text-xs text-gray-500">
                {t('profiles.loadGlobalMcpHint')}
              </p>
            </div>
          </div>

          {/* Set-as-default — only when creating; saves the extra click */}
          {!editingProfile && (
            <div className="flex items-center gap-3">
              <label className="relative inline-flex items-center cursor-pointer">
                <input
                  type="checkbox"
                  checked={setAsDefault}
                  onChange={(e) => setSetAsDefault(e.target.checked)}
                  className="sr-only peer"
                />
                <div className="w-9 h-5 bg-gray-200 peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-indigo-300 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-indigo-600" />
              </label>
              <div>
                <span className="text-sm font-medium text-gray-700">
                  {t('profiles.setAsDefault')}
                </span>
                <p className="text-xs text-gray-500">
                  {t('profiles.setAsDefaultHint')}
                </p>
              </div>
            </div>
          )}

          {/* Actions */}
          <div className="flex justify-end gap-3 pt-4 border-t">
            <button
              onClick={onClose}
              className="px-4 py-2 text-gray-700 hover:text-gray-900 hover:bg-gray-100 rounded-lg transition-colors"
            >
              {t('common.cancel')}
            </button>
            <button
              onClick={handleSave}
              disabled={!canSubmit}
              className="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {validating
                ? t('profiles.validating')
                : editingProfile
                  ? t('common.save')
                  : t('common.create')}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
