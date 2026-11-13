import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../api'
import { SecretReveal } from './SecretReveal'

/** Registry metadata from GET /api/chat-bots/platforms.
 *
 * `security_options` / `security_required` carry what the platform's
 * OWN console offers for a custom bot, and whether it insists on one:
 * the form below renders its whole security section from them and
 * never names a platform, so the labels, fields and rules a user sees
 * are the same data the server validates against.
 */
export interface ChatPlatform {
  name: string
  label: string
  hosts: string[]
  path_prefix: string
  uses_secret: boolean
  uses_keyword: boolean
  security_options: string[]
  security_required: boolean
  supports_file: boolean
  max_message: number
  limits: Record<string, number>
  limit_unit: string
  doc_url: string
}

export interface ChatBotSummary {
  id: string
  name: string
  platform: string
  webhook_url_masked: string
  has_secret: boolean
  /** Security keyword (DingTalk/Feishu) — not a secret, shown on the row. */
  keyword: string | null
  can_send_file: boolean
  created_at: string
  updated_at: string
}

export interface ChatBot extends Omit<ChatBotSummary, 'webhook_url_masked' | 'has_secret' | 'can_send_file' | 'keyword'> {
  webhook_url: string
  secret: string | null
  keyword: string | null
  /** Which of the platform's security settings the row relies on —
   *  a comma-joined list of 'sign' / 'keyword' / 'ip', or null when
   *  the row never recorded one. */
  security_mode: string | null
}

/** The list form the server's `security_mode` string arrives in. */
function parseMode(mode: string | null | undefined): string[] {
  return (mode ?? '')
    .split(',')
    .map(part => part.trim())
    .filter(Boolean)
}

/**
 * Client-side platform detection for the live badge while typing.
 *
 * Mirrors the server's detect_platform using the SAME registry the
 * /platforms endpoint serves — but it is purely cosmetic: the server
 * re-derives the platform from the URL on every create/update and is
 * the only authority on what gets stored.
 */
function detectClientPlatform(url: string, platforms: ChatPlatform[]): ChatPlatform | undefined {
  const trimmed = url.trim()
  if (!trimmed) return undefined
  let host = ''
  let path = ''
  try {
    const parsed = new URL(trimmed)
    if (parsed.protocol !== 'https:') return undefined
    if (parsed.username || parsed.password) return undefined
    if (parsed.port && parsed.port !== '443') return undefined
    host = parsed.hostname.toLowerCase()
    path = parsed.pathname
  } catch {
    return undefined
  }
  return platforms.find(
    p => p.hosts.includes(host) && path.startsWith(p.path_prefix),
  )
}

const PLATFORM_COLORS: Record<string, string> = {
  wecom: 'bg-sky-100 text-sky-800',
  dingtalk: 'bg-blue-100 text-blue-800',
  feishu: 'bg-teal-100 text-teal-800',
  slack: 'bg-violet-100 text-violet-800',
}

export function ChatBotSection({ isAdmin = false }: { isAdmin?: boolean }) {
  const { t } = useTranslation()
  const [bots, setBots] = useState<ChatBotSummary[]>([])
  const [platforms, setPlatforms] = useState<ChatPlatform[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [editingId, setEditingId] = useState<string | null>(null)
  /** The stored platform of the bot being edited — the URL field
   *  holds the server's mask ('****'), which parses as no platform,
   *  so the secret/keyword fields and badge fall back to this until
   *  the user types a new URL. */
  const [editingPlatform, setEditingPlatform] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [form, setForm] = useState<{
    name: string
    webhook_url: string
    secret: string
    keyword: string
  }>({ name: '', webhook_url: '', secret: '', keyword: '' })
  /**
   * The security settings the form declares, tagged with the platform
   * they were chosen FOR. A stale selection must never survive a move
   * to another platform's URL — including the edit form's, where the
   * stored platform shows until a new URL parses.
   */
  const [security, setSecurity] = useState<{ platform: string; options: string[] }>({
    platform: '',
    options: [],
  })
  /** Inline refusal of the security section, named per platform. */
  const [securityError, setSecurityError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [testingId, setTestingId] = useState<string | null>(null)
  const [toast, setToast] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)

  const refresh = async () => {
    setLoading(true)
    setError(null)
    try {
      const list = (await api.get('/api/chat-bots')) as ChatBotSummary[]
      setBots(list)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    refresh()
    api.get('/api/chat-bots/platforms').then(setPlatforms).catch(() => {
      // The badge is cosmetic; the server re-derives the platform
      // from the URL on save, so a failed metadata fetch only means
      // no live badge — not a broken form.
    })
  }, [])

  const detected = useMemo(
    () => detectClientPlatform(form.webhook_url, platforms),
    [form.webhook_url, platforms],
  )
  /** Which platform's fields to show: the URL the user is typing, or
   *  — while the edit form still holds the masked URL — the stored
   *  platform of the bot being edited. */
  const effectivePlatform =
    detected ??
    (editingId && editingPlatform
      ? platforms.find(p => p.name === editingPlatform)
      : undefined)

  /** The settings this form declares for the platform it shows. */
  const options = effectivePlatform?.security_options ?? []
  const securityRequired = !!effectivePlatform?.security_required
  const selected =
    effectivePlatform && security.platform === effectivePlatform.name
      ? security.options
      : []

  const resetSecurity = (platform: string, mode: string | null) => {
    setSecurity({ platform, options: parseMode(mode) })
    setSecurityError(null)
  }

  const toggleOption = (option: string, on: boolean) => {
    setSecurityError(null)
    setSecurity((current) => {
      const mine =
        effectivePlatform && current.platform === effectivePlatform.name
          ? current.options
          : []
      // Tick as many as the bot really uses: the platform enforces
      // EVERY setting configured on it (a robot with 加签 AND 自定义
      // 关键词 rejects a message that only satisfies one), so the
      // form must record all of them. "Required" is at least one —
      // enforced on save, not by restricting the ticks.
      const next = on
        ? [...mine.filter((o) => o !== option), option]
        : mine.filter((o) => o !== option)
      return { platform: effectivePlatform?.name ?? '', options: next }
    })
  }

  /** What the platform's own rules say is missing, or ''. */
  const securityIssue = (): string => {
    if (!effectivePlatform || options.length === 0) return ''
    if (securityRequired && selected.length === 0) {
      return t('integrations.chat_bot.security.pickRequired')
    }
    const optionLabel = (option: string) =>
      t(`integrations.chat_bot.security.option.${effectivePlatform.name}.${option}`)
    if (selected.includes('sign') && !form.secret.trim()) {
      return t('integrations.chat_bot.security.needsSecret', {
        option: optionLabel('sign'),
      })
    }
    if (selected.includes('keyword') && !form.keyword.trim()) {
      return t('integrations.chat_bot.security.needsKeyword', {
        option: optionLabel('keyword'),
      })
    }
    return ''
  }

  const startCreate = () => {
    setEditingId(null)
    setEditingPlatform(null)
    setCreating(true)
    setForm({ name: '', webhook_url: '', secret: '', keyword: '' })
    resetSecurity('', null)
  }

  const startEdit = async (bot: ChatBotSummary) => {
    setCreating(false)
    setEditingId(bot.id)
    setEditingPlatform(bot.platform)
    // Populate name/platform immediately; fetch the bot — the values
    // arrive MASKED (the server returns masks to every caller), and
    // leaving them untouched in the form keeps the stored secret.
    setForm({ name: bot.name, webhook_url: '', secret: '', keyword: '' })
    resetSecurity(bot.platform, null)
    try {
      const full = (await api.get(`/api/chat-bots/${bot.id}`)) as ChatBot
      setForm({
        name: full.name,
        webhook_url: full.webhook_url,
        secret: full.secret ?? '',
        keyword: full.keyword ?? '',
      })
      // The edit form opens on the setting the row actually uses.
      resetSecurity(full.platform, full.security_mode)
    } catch (e) {
      setToast({ kind: 'err', text: e instanceof Error ? e.message : String(e) })
    }
  }

  const cancelEdit = () => {
    setCreating(false)
    setEditingId(null)
    setEditingPlatform(null)
    setForm({ name: '', webhook_url: '', secret: '', keyword: '' })
    resetSecurity('', null)
  }

  const save = async () => {
    if (!form.name.trim()) { setToast({ kind: 'err', text: t('integrations.chat_bot.nameRequired') }); return }
    if (!form.webhook_url.trim()) { setToast({ kind: 'err', text: t('integrations.chat_bot.urlRequired') }); return }
    const issue = securityIssue()
    if (issue) {
      // Inline, next to the choice it is about — not a toast over the
      // whole card.
      setSecurityError(issue)
      return
    }
    setSaving(true)
    setToast(null)
    try {
      const body = {
        name: form.name,
        webhook_url: form.webhook_url,
        // A setting that is not selected posts '', and the server —
        // told which settings ARE selected — clears whatever it
        // carried. Unticking 加签 is how the form turns a secret off.
        secret: selected.includes('sign') ? form.secret : '',
        keyword: selected.includes('keyword') ? form.keyword : '',
        security_mode: selected.join(','),
      }
      if (editingId) {
        await api.put(`/api/chat-bots/${editingId}`, body)
      } else {
        await api.post('/api/chat-bots', body)
      }
      cancelEdit()
      await refresh()
    } catch (e) {
      setToast({ kind: 'err', text: e instanceof Error ? e.message : String(e) })
    } finally {
      setSaving(false)
    }
  }

  const remove = async (bot: ChatBotSummary) => {
    if (!confirm(t('integrations.chat_bot.deleteConfirm', { name: bot.name }))) return
    try {
      await api.del(`/api/chat-bots/${bot.id}`)
      await refresh()
    } catch (e) {
      setToast({ kind: 'err', text: e instanceof Error ? e.message : String(e) })
    }
  }

  const testBot = async (bot: ChatBotSummary) => {
    setTestingId(bot.id)
    setToast(null)
    try {
      const r = (await api.post(`/api/chat-bots/${bot.id}/test`, {})) as { ok: boolean; message: string }
      if (r.ok) {
        setToast({ kind: 'ok', text: t('integrations.chat_bot.testSuccess') })
      } else {
        setToast({ kind: 'err', text: t('integrations.chat_bot.testFailed', { message: r.message }) })
      }
    } catch (e) {
      setToast({ kind: 'err', text: t('integrations.chat_bot.testFailed', { message: e instanceof Error ? e.message : String(e) }) })
    } finally {
      setTestingId(null)
    }
  }

  const platformLabel = (name: string) =>
    platforms.find(p => p.name === name)?.label ?? name

  const hintKey = effectivePlatform
    ? `integrations.chat_bot.hint_${effectivePlatform.name}`
    : null

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4" data-testid="chat-bot-section">
      <div className="flex items-start justify-between mb-3">
        <div>
          <h3 className="font-semibold mb-1">{t('integrations.chat_bot.title')}</h3>
          <p className="text-xs text-gray-500">{t('integrations.chat_bot.description')}</p>
        </div>
        {!creating && editingId === null && (
          <button
            data-testid="chat-bot-add"
            onClick={startCreate}
            className="px-3 py-1.5 text-sm rounded font-medium bg-indigo-600 text-white hover:bg-indigo-700"
          >
            {t('integrations.chat_bot.add')}
          </button>
        )}
      </div>

      {error && <p className="text-sm text-red-600 mb-2" data-testid="chat-bot-error">{error}</p>}

      {(creating || editingId) && (
        <div className="p-3 bg-gray-50 rounded-lg space-y-2 mb-3" data-testid="chat-bot-form">
          <div>
            <label className="block text-xs text-gray-600 mb-1">{t('integrations.chat_bot.name')}</label>
            <input
              type="text"
              data-testid="chat-bot-name"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder={t('integrations.chat_bot.namePlaceholder')}
              className="w-full px-2 py-1.5 text-sm border border-gray-300 rounded"
            />
          </div>
          <div>
            <div className="flex items-center justify-between gap-2 mb-1">
              <label className="block text-xs text-gray-600">{t('integrations.chat_bot.webhookUrl')}</label>
              {isAdmin && editingId && (
                <SecretReveal resource="chat_bot" id={editingId} field="webhook_url" compact />
              )}
            </div>
            <input
              type="text"
              data-testid="chat-bot-url"
              value={form.webhook_url}
              onChange={(e) => setForm({ ...form, webhook_url: e.target.value })}
              placeholder={t('integrations.chat_bot.webhookUrlPlaceholder')}
              className="w-full px-2 py-1.5 text-sm border border-gray-300 rounded font-mono"
            />
            <div className="flex items-center gap-2 mt-1">
              {effectivePlatform ? (
                <span
                  data-testid="chat-bot-platform-badge"
                  className={`px-2 py-0.5 text-xs rounded-full font-medium ${PLATFORM_COLORS[effectivePlatform.name] ?? 'bg-gray-100 text-gray-700'}`}
                >
                  {effectivePlatform.label}
                </span>
              ) : (
                <span data-testid="chat-bot-platform-badge" className="text-xs text-gray-400">
                  {t('integrations.chat_bot.platformDetecting')}
                </span>
              )}
              {effectivePlatform && (
                <a
                  data-testid="chat-bot-doc-link"
                  href={effectivePlatform.doc_url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-xs text-indigo-600 hover:underline"
                >
                  {t('integrations.chat_bot.docLink')}
                </a>
              )}
            </div>
            <p className="text-xs text-gray-500 mt-1" data-testid="chat-bot-url-hint">
              {hintKey ? t(hintKey) : t('integrations.chat_bot.webhookHint')}
            </p>
          </div>
          {options.length > 0 && (
            <div data-testid="chat-bot-security" className="space-y-2">
              <label className="block text-xs text-gray-600">
                {t(
                  securityRequired
                    ? 'integrations.chat_bot.security.titleRequired'
                    : 'integrations.chat_bot.security.titleOptional',
                )}
              </label>
              <div
                className="flex flex-wrap gap-x-4 gap-y-1"
                role="group"
              >
                {options.map((option) => (
                  <label
                    key={option}
                    className="flex items-center gap-1.5 text-xs text-gray-700"
                  >
                    <input
                      type="checkbox"
                      data-testid={`chat-bot-mode-${option}`}
                      checked={selected.includes(option)}
                      onChange={(e) => toggleOption(option, e.target.checked)}
                    />
                    {t(`integrations.chat_bot.security.option.${effectivePlatform?.name}.${option}`)}
                  </label>
                ))}
              </div>
              {securityRequired && selected.length === 0 && !securityError && (
                <p className="text-xs text-gray-500" data-testid="chat-bot-security-note">
                  {t('integrations.chat_bot.security.pickRequired')}
                </p>
              )}
              {selected.includes('sign') && (
                <div>
                  <div className="flex items-center justify-between gap-2 mb-1">
                    <label className="block text-xs text-gray-600">
                      {t(`integrations.chat_bot.security.option.${effectivePlatform?.name}.sign`)}
                    </label>
                    {isAdmin && editingId && (
                      <SecretReveal resource="chat_bot" id={editingId} field="secret" compact />
                    )}
                  </div>
                  <input
                    type="password"
                    data-testid="chat-bot-secret"
                    value={form.secret}
                    onChange={(e) => setForm({ ...form, secret: e.target.value })}
                    placeholder={t('integrations.chat_bot.secretPlaceholder')}
                    className="w-full px-2 py-1.5 text-sm border border-gray-300 rounded font-mono"
                  />
                  <p className="text-xs text-gray-500 mt-1" data-testid="chat-bot-secret-hint">
                    {t(`integrations.chat_bot.security.hint.${effectivePlatform?.name}.sign`)}
                  </p>
                </div>
              )}
              {selected.includes('keyword') && (
                <div>
                  <label className="block text-xs text-gray-600 mb-1">
                    {t(`integrations.chat_bot.security.option.${effectivePlatform?.name}.keyword`)}
                  </label>
                  <input
                    type="text"
                    data-testid="chat-bot-keyword"
                    value={form.keyword}
                    onChange={(e) => setForm({ ...form, keyword: e.target.value })}
                    placeholder={t('integrations.chat_bot.keywordPlaceholder')}
                    className="w-full px-2 py-1.5 text-sm border border-gray-300 rounded font-mono"
                  />
                  <p className="text-xs text-gray-500 mt-1" data-testid="chat-bot-keyword-hint">
                    {t(`integrations.chat_bot.security.hint.${effectivePlatform?.name}.keyword`)}
                  </p>
                </div>
              )}
              {selected.includes('ip') && (
                <p className="text-xs text-gray-500" data-testid="chat-bot-ip-note">
                  {t(`integrations.chat_bot.security.hint.${effectivePlatform?.name}.ip`)}
                </p>
              )}
              {securityError && (
                <p className="text-xs text-red-600" data-testid="chat-bot-security-error">
                  {securityError}
                </p>
              )}
            </div>
          )}
          <div className="flex gap-2">
            <button
              data-testid="chat-bot-save"
              onClick={save}
              disabled={saving}
              className="px-3 py-1.5 text-sm rounded font-medium bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-40"
            >
              {saving ? '…' : t('integrations.chat_bot.save')}
            </button>
            <button
              data-testid="chat-bot-cancel"
              onClick={cancelEdit}
              disabled={saving}
              className="px-3 py-1.5 text-sm rounded font-medium bg-gray-200 text-gray-800 hover:bg-gray-300"
            >
              {t('integrations.chat_bot.cancel')}
            </button>
          </div>
        </div>
      )}

      {loading ? (
        <p className="text-sm text-gray-500" data-testid="chat-bot-loading">{t('common.loading')}</p>
      ) : bots.length === 0 && !creating ? (
        <p className="text-sm text-gray-500" data-testid="chat-bot-empty">{t('integrations.chat_bot.empty')}</p>
      ) : (
        <div className="space-y-2" data-testid="chat-bot-list">
          {bots.map((bot) => (
            <div key={bot.id} data-testid={`chat-bot-row-${bot.id}`} className="p-3 bg-gray-50 rounded-lg flex items-center justify-between gap-3">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 flex-wrap">
                  <p className="text-sm font-medium truncate">{bot.name}</p>
                  <span
                    data-testid={`chat-bot-platform-badge-${bot.id}`}
                    className={`px-2 py-0.5 text-xs rounded-full font-medium shrink-0 ${PLATFORM_COLORS[bot.platform] ?? 'bg-gray-100 text-gray-700'}`}
                  >
                    {platformLabel(bot.platform)}
                  </span>
                  {bot.has_secret && (
                    <span
                      data-testid={`chat-bot-signed-${bot.id}`}
                      className="px-2 py-0.5 text-xs rounded-full bg-amber-100 text-amber-800 shrink-0"
                      title={t('integrations.chat_bot.signedHint')}
                    >
                      {t('integrations.chat_bot.signed')}
                    </span>
                  )}
                  {bot.keyword && (
                    <span
                      data-testid={`chat-bot-keyword-pill-${bot.id}`}
                      className="px-2 py-0.5 text-xs rounded-full bg-emerald-100 text-emerald-800 font-mono shrink-0"
                      title={t('integrations.chat_bot.keywordPillHint')}
                    >
                      {t('integrations.chat_bot.keywordPill', { keyword: bot.keyword })}
                    </span>
                  )}
                </div>
                <p className="text-xs text-gray-500 font-mono truncate" title={t('integrations.chat_bot.maskedHint')}>{bot.webhook_url_masked}</p>
              </div>
              <div className="flex gap-1.5 shrink-0">
                <button
                  data-testid={`chat-bot-test-${bot.id}`}
                  onClick={() => testBot(bot)}
                  disabled={testingId === bot.id}
                  className="px-2.5 py-1 text-xs rounded font-medium bg-white border border-gray-300 text-gray-700 hover:bg-gray-100 disabled:opacity-40"
                >
                  {testingId === bot.id ? t('integrations.chat_bot.testing') : t('integrations.chat_bot.test')}
                </button>
                <button
                  data-testid={`chat-bot-edit-${bot.id}`}
                  onClick={() => startEdit(bot)}
                  className="px-2.5 py-1 text-xs rounded font-medium bg-white border border-gray-300 text-gray-700 hover:bg-gray-100"
                >
                  {t('integrations.chat_bot.edit')}
                </button>
                <button
                  data-testid={`chat-bot-delete-${bot.id}`}
                  onClick={() => remove(bot)}
                  className="px-2.5 py-1 text-xs rounded font-medium bg-white border border-red-300 text-red-600 hover:bg-red-50"
                >
                  {t('integrations.chat_bot.delete')}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {toast && (
        <p
          data-testid="chat-bot-toast"
          className={`text-sm mt-3 ${toast.kind === 'ok' ? 'text-green-700' : 'text-red-600'}`}
        >
          {toast.text}
        </p>
      )}
    </div>
  )
}
