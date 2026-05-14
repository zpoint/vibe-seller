import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../api'

export interface WeComBotSummary {
  id: string
  name: string
  webhook_url_masked: string
  created_at: string
  updated_at: string
}

export interface WeComBot extends Omit<WeComBotSummary, 'webhook_url_masked'> {
  webhook_url: string
}

export function WeComBotSection() {
  const { t } = useTranslation()
  const [bots, setBots] = useState<WeComBotSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [form, setForm] = useState<{ name: string; webhook_url: string }>({ name: '', webhook_url: '' })
  const [saving, setSaving] = useState(false)
  const [testingId, setTestingId] = useState<string | null>(null)
  const [toast, setToast] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)

  const refresh = async () => {
    setLoading(true)
    setError(null)
    try {
      const list = (await api.get('/api/wecom-bots')) as WeComBotSummary[]
      setBots(list)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { refresh() }, [])

  const startCreate = () => {
    setEditingId(null)
    setCreating(true)
    setForm({ name: '', webhook_url: '' })
  }

  const startEdit = async (bot: WeComBotSummary) => {
    setCreating(false)
    setEditingId(bot.id)
    // Populate name immediately; fetch full URL so the edit form
    // shows the real webhook (the list only exposed a mask).
    setForm({ name: bot.name, webhook_url: '' })
    try {
      const full = (await api.get(`/api/wecom-bots/${bot.id}`)) as WeComBot
      setForm({ name: full.name, webhook_url: full.webhook_url })
    } catch (e) {
      setToast({ kind: 'err', text: e instanceof Error ? e.message : String(e) })
    }
  }

  const cancelEdit = () => {
    setCreating(false)
    setEditingId(null)
    setForm({ name: '', webhook_url: '' })
  }

  const save = async () => {
    if (!form.name.trim()) { setToast({ kind: 'err', text: t('integrations.wecom_bot.nameRequired') }); return }
    if (!form.webhook_url.trim()) { setToast({ kind: 'err', text: t('integrations.wecom_bot.urlRequired') }); return }
    setSaving(true)
    setToast(null)
    try {
      if (editingId) {
        await api.put(`/api/wecom-bots/${editingId}`, form)
      } else {
        await api.post('/api/wecom-bots', form)
      }
      cancelEdit()
      await refresh()
    } catch (e) {
      setToast({ kind: 'err', text: e instanceof Error ? e.message : String(e) })
    } finally {
      setSaving(false)
    }
  }

  const remove = async (bot: WeComBotSummary) => {
    if (!confirm(t('integrations.wecom_bot.deleteConfirm', { name: bot.name }))) return
    try {
      await api.del(`/api/wecom-bots/${bot.id}`)
      await refresh()
    } catch (e) {
      setToast({ kind: 'err', text: e instanceof Error ? e.message : String(e) })
    }
  }

  const testBot = async (bot: WeComBotSummary) => {
    setTestingId(bot.id)
    setToast(null)
    try {
      const r = (await api.post(`/api/wecom-bots/${bot.id}/test`, {})) as { ok: boolean; message: string }
      if (r.ok) {
        setToast({ kind: 'ok', text: t('integrations.wecom_bot.testSuccess') })
      } else {
        setToast({ kind: 'err', text: t('integrations.wecom_bot.testFailed', { message: r.message }) })
      }
    } catch (e) {
      setToast({ kind: 'err', text: t('integrations.wecom_bot.testFailed', { message: e instanceof Error ? e.message : String(e) }) })
    } finally {
      setTestingId(null)
    }
  }

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4" data-testid="wecom-bot-section">
      <div className="flex items-start justify-between mb-3">
        <div>
          <h3 className="font-semibold mb-1">{t('integrations.wecom_bot.title')}</h3>
          <p className="text-xs text-gray-500">{t('integrations.wecom_bot.description')}</p>
        </div>
        {!creating && editingId === null && (
          <button
            data-testid="wecom-bot-add"
            onClick={startCreate}
            className="px-3 py-1.5 text-sm rounded font-medium bg-blue-600 text-white hover:bg-blue-700"
          >
            {t('integrations.wecom_bot.add')}
          </button>
        )}
      </div>

      {error && <p className="text-sm text-red-600 mb-2" data-testid="wecom-bot-error">{error}</p>}

      {(creating || editingId) && (
        <div className="p-3 bg-gray-50 rounded-lg space-y-2 mb-3" data-testid="wecom-bot-form">
          <div>
            <label className="block text-xs text-gray-600 mb-1">{t('integrations.wecom_bot.name')}</label>
            <input
              type="text"
              data-testid="wecom-bot-input-name"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder={t('integrations.wecom_bot.namePlaceholder')}
              className="w-full px-2 py-1.5 text-sm border border-gray-300 rounded"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-600 mb-1">{t('integrations.wecom_bot.webhookUrl')}</label>
            <input
              type="text"
              data-testid="wecom-bot-input-url"
              value={form.webhook_url}
              onChange={(e) => setForm({ ...form, webhook_url: e.target.value })}
              placeholder={t('integrations.wecom_bot.webhookUrlPlaceholder')}
              className="w-full px-2 py-1.5 text-sm border border-gray-300 rounded font-mono"
            />
            <p className="text-xs text-gray-500 mt-1">{t('integrations.wecom_bot.webhookHint')}</p>
          </div>
          <div className="flex gap-2">
            <button
              data-testid="wecom-bot-save"
              onClick={save}
              disabled={saving}
              className="px-3 py-1.5 text-sm rounded font-medium bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-40"
            >
              {saving ? '…' : t('integrations.wecom_bot.save')}
            </button>
            <button
              data-testid="wecom-bot-cancel"
              onClick={cancelEdit}
              disabled={saving}
              className="px-3 py-1.5 text-sm rounded font-medium bg-gray-200 text-gray-800 hover:bg-gray-300"
            >
              {t('integrations.wecom_bot.cancel')}
            </button>
          </div>
        </div>
      )}

      {loading ? (
        <p className="text-sm text-gray-500" data-testid="wecom-bot-loading">{t('common.loading')}</p>
      ) : bots.length === 0 && !creating ? (
        <p className="text-sm text-gray-500" data-testid="wecom-bot-empty">{t('integrations.wecom_bot.empty')}</p>
      ) : (
        <div className="space-y-2" data-testid="wecom-bot-list">
          {bots.map((bot) => (
            <div key={bot.id} data-testid={`wecom-bot-row-${bot.id}`} className="p-3 bg-gray-50 rounded-lg flex items-center justify-between gap-3">
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium truncate">{bot.name}</p>
                <p className="text-xs text-gray-500 font-mono truncate" title={t('integrations.wecom_bot.maskedHint')}>{bot.webhook_url_masked}</p>
              </div>
              <div className="flex gap-1.5 shrink-0">
                <button
                  data-testid={`wecom-bot-test-${bot.id}`}
                  onClick={() => testBot(bot)}
                  disabled={testingId === bot.id}
                  className="px-2.5 py-1 text-xs rounded font-medium bg-white border border-gray-300 text-gray-700 hover:bg-gray-100 disabled:opacity-40"
                >
                  {testingId === bot.id ? t('integrations.wecom_bot.testing') : t('integrations.wecom_bot.test')}
                </button>
                <button
                  data-testid={`wecom-bot-edit-${bot.id}`}
                  onClick={() => startEdit(bot)}
                  className="px-2.5 py-1 text-xs rounded font-medium bg-white border border-gray-300 text-gray-700 hover:bg-gray-100"
                >
                  {t('integrations.wecom_bot.edit')}
                </button>
                <button
                  data-testid={`wecom-bot-delete-${bot.id}`}
                  onClick={() => remove(bot)}
                  className="px-2.5 py-1 text-xs rounded font-medium bg-white border border-red-300 text-red-600 hover:bg-red-50"
                >
                  {t('integrations.wecom_bot.delete')}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {toast && (
        <p
          data-testid="wecom-bot-toast"
          className={`text-sm mt-3 ${toast.kind === 'ok' ? 'text-green-700' : 'text-red-600'}`}
        >
          {toast.text}
        </p>
      )}
    </div>
  )
}
