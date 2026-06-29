import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { Store, ZiniaoAccount, ZiniaoBrowserProfile } from '../types'

interface StoreFormProps {
  mode: 'create' | 'edit'
  initialValues?: Partial<Store>
  onSubmit: (data: {
    name: string
    browser_backend: string
    browser_config: Record<string, string>
    ziniao_account_id?: string
    browser_oauth?: string
    platforms: string[]
    countries: string[]
    platform_countries: Record<string, string[]>
  }) => void
  onCancel: () => void
  // Ziniao props (only needed for create mode)
  ziniaoAccounts?: ZiniaoAccount[]
  selectedZiniaoAccountId?: string
  setSelectedZiniaoAccountId?: (v: string) => void
  ziniaoBrowsers?: ZiniaoBrowserProfile[]
  selectedBrowserOauth?: string
  setSelectedBrowserOauth?: (v: string) => void
  fetchingBrowsers?: boolean
  browserFetchError?: string
  setBrowserFetchError?: (v: string) => void
  fetchBrowserProfiles?: (accountId: string) => void
  showAddAccount?: boolean
  setShowAddAccount?: (v: boolean) => void
  showAccountPassword?: boolean
  setShowAccountPassword?: React.Dispatch<React.SetStateAction<boolean>>
  editingAccountId?: string
  setEditingAccountId?: (v: string) => void
  newAccount?: { name: string; company: string; username: string; password: string }
  setNewAccount?: React.Dispatch<React.SetStateAction<{ name: string; company: string; username: string; password: string }>>
  createZiniaoAccount?: () => void
  updateZiniaoAccount?: () => void
  deleteZiniaoAccount?: (id: string) => void
}

export function StoreForm({ mode, initialValues, onSubmit, onCancel, ...ziniaoProps }: StoreFormProps) {
  const { t } = useTranslation()
  const [name, setName] = useState(initialValues?.name || '')
  const [backend] = useState(initialValues?.browser_backend || 'chrome')
  const [showProxy, setShowProxy] = useState(
    !!(initialValues?.browser_config?.proxy_server || initialValues?.browser_config?.proxy_bypass)
  )
  const [proxyServer, setProxyServer] = useState(
    (initialValues?.browser_config?.proxy_server as string) || ''
  )
  const [proxyBypass, setProxyBypass] = useState(
    (initialValues?.browser_config?.proxy_bypass as string) || ''
  )

  const handleSubmit = () => {
    if (!name.trim()) return
    const bc: Record<string, string> = {}
    if (backend === 'chrome' && proxyServer.trim()) bc.proxy_server = proxyServer.trim()
    if (backend === 'chrome' && proxyBypass.trim()) bc.proxy_bypass = proxyBypass.trim()
    onSubmit({
      name: name.trim(),
      browser_backend: backend,
      browser_config: bc,
      platforms: initialValues?.platforms || ['amazon'],
      countries: initialValues?.countries || ['US'],
      platform_countries: initialValues?.platform_countries || {},
      ...(backend === 'ziniao' && ziniaoProps.selectedZiniaoAccountId ? {
        ziniao_account_id: ziniaoProps.selectedZiniaoAccountId,
        browser_oauth: ziniaoProps.selectedBrowserOauth,
      } : {}),
    })
  }

  const nameEmpty = !name.trim()

  return (
    <div className="space-y-3">
      <div>
        <label className="block text-xs font-medium text-gray-600 mb-1">{t('settings.storeName')}</label>
        <input
          value={name}
          onChange={e => setName(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && !nameEmpty && backend === 'chrome' && handleSubmit()}
          placeholder={t('settings.storeName') + '...'}
          className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
          autoFocus
        />
      </div>

      {mode === 'edit' && (
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1">{t('tasks.browserBackend')}</label>
          <div className="px-3 py-2 bg-gray-100 rounded-lg text-sm text-gray-600">
            {backend === 'chrome' ? 'Chrome (Playwright)' : `${t('tasks.ziniao')} (${t('tasks.browserBackend')})`}
            <span className="text-xs text-gray-400 ml-2">({t('stores.immutable')})</span>
          </div>
        </div>
      )}

      {mode === 'create' && (
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1">{t('tasks.browserBackend')}</label>
          <div className="px-3 py-2 bg-gray-100 rounded-lg text-sm text-gray-600">
            {backend === 'chrome' ? 'Chrome (Playwright)' : `${t('tasks.ziniao')} (${t('tasks.browserBackend')})`}
          </div>
        </div>
      )}

      {backend === 'chrome' && (
        <div>
          <label className="flex items-center gap-2 text-xs text-gray-600 cursor-pointer mb-1">
            <input
              type="checkbox"
              checked={showProxy}
              onChange={e => setShowProxy(e.target.checked)}
              className="rounded border-gray-300"
            />
            {t('settings.needProxy')}
          </label>
          {showProxy && (
            <div className="space-y-1">
              <input
                value={proxyServer}
                onChange={e => setProxyServer(e.target.value)}
                placeholder={`${t('settings.proxyServer')} (http://127.0.0.1:7890)`}
                className="w-full px-2 py-1.5 border border-gray-300 rounded text-xs"
              />
              <input
                value={proxyBypass}
                onChange={e => setProxyBypass(e.target.value)}
                placeholder={`${t('settings.proxyBypass')} (.amazon.com,.google.com)`}
                className="w-full px-2 py-1.5 border border-gray-300 rounded text-xs"
              />
              <p className="text-[10px] text-gray-400">{t('settings.proxyHint')}</p>
            </div>
          )}
        </div>
      )}

      {mode === 'edit' && backend === 'ziniao' && initialValues?.browser_oauth && (
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1">{t('settings.ziniaoSelectProfile')}</label>
          <div className="px-3 py-2 bg-gray-100 rounded-lg text-sm text-gray-600">
            {initialValues.browser_oauth}
            <span className="text-xs text-gray-400 ml-2">({t('stores.immutable')})</span>
          </div>
        </div>
      )}

      <div className="flex gap-2 pt-1">
        <button
          onClick={handleSubmit}
          disabled={nameEmpty}
          className={`px-4 py-2 rounded-lg text-sm font-medium ${nameEmpty ? 'bg-gray-300 text-gray-500 cursor-not-allowed' : 'bg-blue-600 text-white hover:bg-blue-700'}`}
        >
          {mode === 'create' ? t('common.create') : t('common.save')}
        </button>
        <button onClick={onCancel} className="px-4 py-2 bg-gray-200 rounded-lg text-sm">{t('common.cancel')}</button>
      </div>
    </div>
  )
}
