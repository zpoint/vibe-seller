import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../../api'
import { WeComBotSection } from '../WeComBotSection'
import { Dida365Panel } from './Dida365Panel'

interface GwsStatusDetail {
  auth_method?: string
  storage?: string | null
  keyring_backend?: string | null
  project_id?: string | null
  account_hint?: string | null
  token_cache_exists?: boolean
  needs_login?: boolean
  needs_relogin?: boolean
  encryption_error?: string | null
}

interface GwsStatus {
  binary: boolean
  auth: boolean
  auth_reason?: string | null
  version: string | null
  detail?: GwsStatusDetail
  enabled: boolean
  installed: boolean
}

export function IntegrationsPanel() {
  const { t } = useTranslation()
  const [status, setStatus] = useState<GwsStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)

  const refresh = async () => {
    setLoading(true)
    setError(null)
    try {
      const s = (await api.get('/api/settings/google-workspace/status')) as GwsStatus
      setStatus(s)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { refresh() }, [])

  const toggle = async () => {
    if (!status) return
    setError(null)
    setMessage(null)
    setBusy(true)
    try {
      if (status.enabled) {
        await api.post('/api/settings/google-workspace/disable', {})
        setMessage(t('integrations.gws.disabled'))
      } else {
        const r = (await api.post('/api/settings/google-workspace/enable', {})) as { count: number }
        setMessage(t('integrations.gws.enabledMsg', { count: r.count }))
      }
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const canToggle = status && (status.enabled || (status.binary && status.auth)) && !busy

  return (
    <div className="space-y-4">
      <div className="bg-white rounded-lg border border-gray-200 p-4">
        <h3 className="font-semibold mb-1">{t('integrations.gws.title')}</h3>
        <p className="text-xs text-gray-500 mb-3">{t('integrations.gws.description')}</p>

        {loading ? (
          <p className="text-sm text-gray-500">{t('common.loading')}</p>
        ) : status ? (
          <div className="space-y-3">
            <div className="flex items-center justify-between p-3 bg-gray-50 rounded-lg">
              <div>
                <p className="text-sm font-medium">{t('integrations.gws.enable')}</p>
                <p className="text-xs text-gray-500">
                  {status.enabled
                    ? t('integrations.gws.installedHint')
                    : t('integrations.gws.notInstalledHint')}
                </p>
              </div>
              <button
                onClick={toggle}
                disabled={!canToggle}
                className={`px-3 py-1.5 text-sm rounded font-medium transition-colors ${
                  status.enabled
                    ? 'bg-gray-200 text-gray-800 hover:bg-gray-300'
                    : 'bg-blue-600 text-white hover:bg-blue-700'
                } disabled:opacity-40 disabled:cursor-not-allowed`}
              >
                {busy
                  ? '…'
                  : status.enabled
                  ? t('integrations.gws.disable')
                  : t('integrations.gws.enable')}
              </button>
            </div>

            <div className="p-3 bg-gray-50 rounded-lg space-y-1.5">
              <div className="flex items-center justify-between text-sm">
                <span className="text-gray-600">{t('integrations.gws.binary')}</span>
                <span className={status.binary ? 'text-green-700' : 'text-red-600'}>
                  {status.binary ? (status.version || '✓') : t('integrations.gws.binaryMissing')}
                </span>
              </div>
              <div className="flex items-center justify-between text-sm">
                <span className="text-gray-600">{t('integrations.gws.auth')}</span>
                <span className={status.auth ? 'text-green-700' : 'text-red-600'}>
                  {status.auth
                    ? t('integrations.gws.loggedIn')
                    : t('integrations.gws.loginRequired')}
                </span>
              </div>
              {status.auth && status.detail?.account_hint && (
                <div className="flex items-center justify-between text-xs text-gray-500">
                  <span>{t('integrations.gws.account')}</span>
                  <span className="font-mono">{status.detail.account_hint}</span>
                </div>
              )}
              {status.auth && status.detail?.project_id && (
                <div className="flex items-center justify-between text-xs text-gray-500">
                  <span>{t('integrations.gws.project')}</span>
                  <span className="font-mono">{status.detail.project_id}</span>
                </div>
              )}
              {status.auth && status.detail?.storage && (
                <div className="flex items-center justify-between text-xs text-gray-500">
                  <span>{t('integrations.gws.storage')}</span>
                  <span className="font-mono">
                    {status.detail.storage}
                    {status.detail.keyring_backend &&
                      ` (${status.detail.keyring_backend})`}
                  </span>
                </div>
              )}
            </div>

            {!status.binary && (
              <p className="text-xs text-gray-500">
                {t('integrations.gws.binaryHelp')}{' '}
                <a
                  href="https://github.com/googleworkspace/cli/releases"
                  target="_blank"
                  rel="noreferrer"
                  className="text-blue-600 hover:underline"
                >
                  github.com/googleworkspace/cli
                </a>
              </p>
            )}
            {status.binary && !status.auth && (
              <p className="text-xs text-gray-500 font-mono">
                gws auth login
              </p>
            )}
            {busy && !status.enabled && (
              <p className="text-xs text-blue-600">
                {t('integrations.gws.installing')}
              </p>
            )}

            {error && (
              <p className="text-sm text-red-600">{error}</p>
            )}
            {message && !error && (
              <p className="text-sm text-green-700">{message}</p>
            )}
          </div>
        ) : null}
      </div>
      <WeComBotSection />
      <Dida365Panel />
    </div>
  )
}
