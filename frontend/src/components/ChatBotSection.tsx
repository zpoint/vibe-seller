import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../api'

/** Registry metadata from GET /api/chat-bots/platforms. */
export interface ChatPlatform {
  name: string
  label: string
  hosts: string[]
  path_prefix: string
  uses_secret: boolean
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
  can_send_file: boolean
  created_at: string
  updated_at: string
}

export interface ChatBot extends Omit<ChatBotSummary, 'webhook_url_masked' | 'has_secret' | 'can_send_file'> {
  webhook_url: string
  secret: string | null
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

export function ChatBotSection() {
  const { t } = useTranslation()
  const [bots, setBots] = useState<ChatBotSummary[]>([])
  const [platforms, setPlatforms] = useState<ChatPlatform[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [form, setForm] = useState<{ name: string; webhook_url: string; secret: string }>(
    { name: '', webhook_url: '', secret: '' },
  )
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

  const startCreate = () => {
    setEditingId(null)
    setCreating(true)
    setForm({ name: '', webhook_url: '', secret: '' })
  }

  const startEdit = async (bot: ChatBotSummary) => {
    setCreating(false)
    setEditingId(bot.id)
    // Populate name/platform immediately; fetch the full URL + secret
    // so the edit form shows the real values (the list only exposes
    // masks).
    setForm({ name: bot.name, webhook_url: '', secret: '' })
    try {
      const full = (await api.get(`/api/chat-bots/${bot.id}`)) as ChatBot
      setForm({ name: full.name, webhook_url: full.webhook_url, secret: full.secret ?? '' })
    } catch (e) {
      setToast({ kind: 'err', text: e instanceof Error ? e.message : String(e) })
    }
  }

  const cancelEdit = () => {
    setCreating(false)
    setEditingId(null)
    setForm({ name: '', webhook_url: '', secret: '' })
  }

  const save = async () => {
    if (!form.name.trim()) { setToast({ kind: 'err', text: t('integrations.chat_bot.nameRequired') }); return }
    if (!form.webhook_url.trim()) { setToast({ kind: 'err', text: t('integrations.chat_bot.urlRequired') }); return }
    setSaving(true)
    setToast(null)
    try {
      const body = { name: form.name, webhook_url: form.webhook_url, secret: form.secret }
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

  const hintKey = detected ? `integrations.chat_bot.hint_${detected.name}` : null

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
            <label className="block text-xs text-gray-600 mb-1">{t('integrations.chat_bot.webhookUrl')}</label>
            <input
              type="text"
              data-testid="chat-bot-url"
              value={form.webhook_url}
              onChange={(e) => setForm({ ...form, webhook_url: e.target.value })}
              placeholder={t('integrations.chat_bot.webhookUrlPlaceholder')}
              className="w-full px-2 py-1.5 text-sm border border-gray-300 rounded font-mono"
            />
            <div className="flex items-center gap-2 mt-1">
              {detected ? (
                <span
                  data-testid="chat-bot-platform-badge"
                  className={`px-2 py-0.5 text-xs rounded-full font-medium ${PLATFORM_COLORS[detected.name] ?? 'bg-gray-100 text-gray-700'}`}
                >
                  {detected.label}
                </span>
              ) : (
                <span data-testid="chat-bot-platform-badge" className="text-xs text-gray-400">
                  {t('integrations.chat_bot.platformDetecting')}
                </span>
              )}
              {detected && (
                <a
                  data-testid="chat-bot-doc-link"
                  href={detected.doc_url}
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
          {detected?.uses_secret && (
            <div>
              <label className="block text-xs text-gray-600 mb-1">
                {t('integrations.chat_bot.secretLabel')}
              </label>
              <input
                type="password"
                data-testid="chat-bot-secret"
                value={form.secret}
                onChange={(e) => setForm({ ...form, secret: e.target.value })}
                placeholder={t('integrations.chat_bot.secretPlaceholder')}
                className="w-full px-2 py-1.5 text-sm border border-gray-300 rounded font-mono"
              />
              <p className="text-xs text-gray-500 mt-1">{t('integrations.chat_bot.secretHint')}</p>
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
