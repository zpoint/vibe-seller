import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../../api'

export function Dida365Panel() {
  const { t } = useTranslation()
  const [dida365Status, setDida365Status] = useState<{
    connected: boolean
    service_type: string
    project_id: string
  } | null>(null)
  const [serviceType, setServiceType] = useState<'dida365' | 'ticktick'>('dida365')
  const [clientId, setClientId] = useState('')
  const [clientSecret, setClientSecret] = useState('')
  const [projects, setProjects] = useState<{ id: string; name: string }[]>([])
  const [selectedProject, setSelectedProject] = useState('')
  const [connecting, setConnecting] = useState(false)
  const [installing, setInstalling] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [polling, setPolling] = useState(false)
  const [popupBlocked, setPopupBlocked] = useState(false)
  const [authUrl, setAuthUrl] = useState('')

  const loadStatus = async () => {
    try {
      const data = await api.get('/api/dida365/status')
      setDida365Status(data)
      if (data.connected) {
        if (data.service_type) setServiceType(data.service_type as 'dida365' | 'ticktick')
        if (data.project_id) setSelectedProject(data.project_id)
      }
    } catch { /* ignore */ }
  }

  const loadProjects = async () => {
    try {
      const data = await api.get('/api/dida365/projects')
      setProjects(data)
    } catch { /* ignore */ }
  }

  useEffect(() => {
    loadStatus() // eslint-disable-line react-hooks/set-state-in-effect
  }, [])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (dida365Status?.connected) loadProjects()
  }, [dida365Status?.connected])

  const handleConnect = async () => {
    if (!clientId || !clientSecret) {
      setError('Client ID and Client Secret are required')
      return
    }
    setError('')
    setConnecting(true)

    // Step 1: Install ticktick-mcp if needed
    setInstalling(true)
    try {
      await api.post('/api/dida365/setup-mcp')
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      setError(t('settings.dida365SetupFailed', { error: msg }))
      setConnecting(false)
      setInstalling(false)
      return
    }
    setInstalling(false)

    // Step 2: Start OAuth flow
    try {
      const data = await api.post('/api/dida365/authorize', {
        client_id: clientId,
        client_secret: clientSecret,
        service_type: serviceType,
      })
      setAuthUrl(data.auth_url)
      setPopupBlocked(false)
      const popup = window.open(data.auth_url, '_blank', 'noopener,noreferrer')
      if (!popup || popup.closed) {
        setPopupBlocked(true)
      }
      // Start polling for connection
      setPolling(true)
      const interval = setInterval(async () => {
        try {
          const status = await api.get('/api/dida365/status')
          if (status.connected) {
            clearInterval(interval)
            setPolling(false)
            setConnecting(false)
            setDida365Status(status)
            // Save initial config after connection
            try {
              await api.post('/api/dida365/configure', {
                project_id: '',
              })
            } catch { /* ignore */ }
          }
        } catch { /* ignore */ }
      }, 2000)
      // Stop polling after 5 minutes
      setTimeout(() => {
        clearInterval(interval)
        setPolling(false)
        setConnecting(false)
      }, 300000)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Connection failed')
      setConnecting(false)
    }
  }

  const handleSave = async () => {
    setSaving(true)
    setError('')
    try {
      await api.post('/api/dida365/configure', {
        project_id: selectedProject,
      })
      await loadStatus()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed')
    }
    setSaving(false)
  }

  const handleDisconnect = async () => {
    if (!confirm(t('settings.dida365DisconnectConfirm'))) return
    try {
      await api.del('/api/dida365/disconnect')
      setDida365Status({ connected: false, service_type: '', project_id: '' })
      setClientId('')
      setClientSecret('')
      setProjects([])
      setSelectedProject('')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Disconnect failed')
    }
  }

  const callbackUrl = `${window.location.origin}/api/dida365/callback`
  const devPortalUrl = serviceType === 'dida365'
    ? 'https://developer.dida365.com/manage'
    : 'https://developer.ticktick.com/manage'
  const hostname = window.location.hostname
  const isValidOAuthHost = hostname === 'localhost'
    || hostname === '127.0.0.1'
    || hostname === '::1'
    || hostname.includes('.')

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4">
      <h3 className="font-semibold mb-3">{t('settings.dida365')}</h3>

      {error && (
        <div className="mb-3 bg-red-50 border border-red-200 rounded-lg p-3 text-sm text-red-700">
          {error}
          <button onClick={() => setError('')} className="ml-2 text-red-500 hover:text-red-700">&times;</button>
        </div>
      )}

      {dida365Status?.connected ? (
        <div className="space-y-4">
          <div className="flex items-center gap-2 mb-3">
            <span className="px-2 py-1 text-xs bg-green-100 text-green-700 rounded-full">
              {t('settings.dida365Connected')}
            </span>
            <span className="text-xs text-gray-500">
              {dida365Status.service_type === 'dida365' ? 'Dida365' : 'TickTick'}
            </span>
          </div>

          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">{t('settings.dida365Project')}</label>
            <p className="text-xs text-gray-400 mb-1">{t('settings.dida365ProjectHint')}</p>
            <select
              value={selectedProject}
              onChange={e => setSelectedProject(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded text-sm bg-white"
            >
              <option value="">--</option>
              {projects.map(p => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          </div>

          <p className="text-xs text-gray-500">{t('settings.dida365AgentInfo')}</p>

          <div className="flex items-center gap-2">
            <button
              onClick={handleSave}
              disabled={saving}
              className="px-3 py-1.5 bg-blue-600 text-white rounded text-sm hover:bg-blue-700 disabled:opacity-50"
            >
              {saving ? t('common.loading') : t('settings.dida365SaveConfig')}
            </button>
            <button
              onClick={handleDisconnect}
              className="px-3 py-1.5 bg-red-50 text-red-600 border border-red-300 rounded text-sm hover:bg-red-100"
            >
              {t('settings.dida365Disconnect')}
            </button>
            <span className="text-xs text-gray-400">{t('settings.dida365DisconnectHint')}</span>
          </div>
        </div>
      ) : (
        <div className="space-y-4">
          {/* Step 1: Choose service */}
          <div className="flex items-start gap-3">
            <span className="flex-shrink-0 w-6 h-6 rounded-full bg-blue-100 text-blue-700 text-xs font-bold flex items-center justify-center mt-0.5">1</span>
            <div className="flex-1">
              <p className="text-sm font-medium text-gray-700 mb-1">{t('settings.dida365GuideStep1')}</p>
              <div className="flex gap-4">
                <label className="flex items-center gap-1.5 text-sm">
                  <input
                    type="radio"
                    name="serviceType"
                    value="dida365"
                    checked={serviceType === 'dida365'}
                    onChange={() => setServiceType('dida365')}
                  />
                  Dida365 ({t('settings.dida365ChinaVersion')})
                </label>
                <label className="flex items-center gap-1.5 text-sm">
                  <input
                    type="radio"
                    name="serviceType"
                    value="ticktick"
                    checked={serviceType === 'ticktick'}
                    onChange={() => setServiceType('ticktick')}
                  />
                  TickTick ({t('settings.dida365IntlVersion')})
                </label>
              </div>
            </div>
          </div>

          {/* Step 2: Register app on developer portal */}
          <div className="flex items-start gap-3">
            <span className="flex-shrink-0 w-6 h-6 rounded-full bg-blue-100 text-blue-700 text-xs font-bold flex items-center justify-center mt-0.5">2</span>
            <div className="flex-1">
              <p className="text-sm font-medium text-gray-700 mb-1">{t('settings.dida365GuideStep2')}</p>
              <p className="text-xs text-gray-500 mb-2">{t('settings.dida365GuideStep2Hint')}</p>
              <a href={devPortalUrl} target="_blank" rel="noopener noreferrer"
                className="inline-flex items-center gap-1 px-3 py-1.5 bg-gray-100 hover:bg-gray-200 text-sm text-blue-600 rounded border border-gray-200 transition-colors">
                {serviceType === 'dida365' ? 'developer.dida365.com' : 'developer.ticktick.com'} &rarr;
              </a>
            </div>
          </div>

          {/* Step 3: Set callback URL */}
          <div className="flex items-start gap-3">
            <span className="flex-shrink-0 w-6 h-6 rounded-full bg-blue-100 text-blue-700 text-xs font-bold flex items-center justify-center mt-0.5">3</span>
            <div className="flex-1">
              <p className="text-sm font-medium text-gray-700 mb-1">{t('settings.dida365GuideStep3')}</p>
              <p className="text-xs text-gray-500 mb-1">{t('settings.dida365GuideStep3Hint')}</p>
              <code className="block text-xs bg-white border border-gray-200 rounded px-2 py-1.5 select-all">
                {callbackUrl}
              </code>
              {!isValidOAuthHost && (
                <div className="mt-2 bg-amber-50 border border-amber-200 rounded-lg p-2.5">
                  <p className="text-xs font-medium text-amber-800">{t('settings.dida365BareHostWarning', { hostname })}</p>
                  <p className="text-xs text-amber-700 mt-1">{t('settings.dida365BareHostHint', { port: window.location.port || '80' })}</p>
                </div>
              )}
              <p className="text-xs text-gray-400 mt-1">{t('settings.dida365PortNote')}</p>
            </div>
          </div>

          {/* Step 4: Paste credentials */}
          <div className="flex items-start gap-3">
            <span className="flex-shrink-0 w-6 h-6 rounded-full bg-blue-100 text-blue-700 text-xs font-bold flex items-center justify-center mt-0.5">4</span>
            <div className="flex-1">
              <p className="text-sm font-medium text-gray-700 mb-2">{t('settings.dida365GuideStep4')}</p>
              <div className="space-y-2">
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">{t('settings.dida365ClientId')}</label>
                  <input
                    value={clientId}
                    onChange={e => setClientId(e.target.value)}
                    className="w-full px-3 py-2 border border-gray-300 rounded text-sm"
                    placeholder={t('settings.dida365ClientIdPlaceholder')}
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">{t('settings.dida365ClientSecret')}</label>
                  <input
                    type="password"
                    value={clientSecret}
                    onChange={e => setClientSecret(e.target.value)}
                    className="w-full px-3 py-2 border border-gray-300 rounded text-sm"
                    placeholder={t('settings.dida365ClientSecretPlaceholder')}
                  />
                </div>
              </div>
            </div>
          </div>

          {/* Step 5: Connect */}
          <div className="flex items-start gap-3">
            <span className="flex-shrink-0 w-6 h-6 rounded-full bg-blue-100 text-blue-700 text-xs font-bold flex items-center justify-center mt-0.5">5</span>
            <div className="flex-1">
              <p className="text-sm font-medium text-gray-700 mb-1">{t('settings.dida365GuideStep5')}</p>
              <p className="text-xs text-gray-400 mb-2">{t('settings.dida365PopupHint')}</p>
              {installing ? (
                <div className="flex items-center gap-2 p-3 bg-blue-50 border border-blue-200 rounded-lg">
                  <svg className="animate-spin h-4 w-4 text-blue-600" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                  </svg>
                  <span className="text-sm text-blue-700">{t('settings.dida365Installing')}</span>
                </div>
              ) : polling ? (
                <div className="space-y-2">
                  {popupBlocked ? (
                    <div className="p-3 bg-orange-50 border border-orange-200 rounded-lg">
                      <p className="text-sm text-orange-700 font-medium mb-1">{t('settings.dida365PopupBlocked')}</p>
                      <p className="text-xs text-orange-600 mb-2">{t('settings.dida365PopupBlockedHint')}</p>
                      <button
                        onClick={() => { const w = window.open(authUrl, '_blank', 'noopener,noreferrer'); if (w && !w.closed) setPopupBlocked(false) }}
                        className="px-3 py-1.5 bg-orange-600 text-white rounded text-sm hover:bg-orange-700"
                      >
                        {t('settings.dida365OpenAuthWindow')}
                      </button>
                    </div>
                  ) : (
                    <div className="flex items-center gap-2 p-3 bg-yellow-50 border border-yellow-200 rounded-lg">
                      <svg className="animate-spin h-4 w-4 text-yellow-600" viewBox="0 0 24 24">
                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                      </svg>
                      <span className="text-sm text-yellow-700">{t('settings.dida365Connecting')}</span>
                    </div>
                  )}
                </div>
              ) : (
                <button
                  onClick={handleConnect}
                  disabled={connecting || !clientId || !clientSecret}
                  className="px-4 py-2 bg-blue-600 text-white rounded text-sm hover:bg-blue-700 disabled:opacity-50"
                >
                  {t('settings.dida365Connect')}
                </button>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
