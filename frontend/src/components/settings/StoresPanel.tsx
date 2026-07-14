import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../../api'
import { StoreForm } from '../StoreForm'
import type { EmailAccount, Store, StoreEmailLink } from '../../types'

interface StoresPanelProps {
  stores: Store[]
  loadStores: () => void
  emailAccounts: EmailAccount[]
  loadEmailAccounts: () => void
}

export function StoresPanel({ stores, loadStores, emailAccounts, loadEmailAccounts }: StoresPanelProps) {
  const { t } = useTranslation()
  const [editingStore, setEditingStore] = useState<Store | null>(null)
  const [deletingStore, setDeletingStore] = useState<Store | null>(null)
  const [deleteFiles, setDeleteFiles] = useState(false)
  const [error, setError] = useState('')
  const [linksMap, setLinksMap] = useState<Record<string, StoreEmailLink[]>>({})
  const [linkingEmail, setLinkingEmail] = useState<Record<string, string>>({})

  useEffect(() => {
    loadEmailAccounts()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (stores.length > 0) loadAllLinks()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stores])

  const loadAllLinks = async () => {
    const map: Record<string, StoreEmailLink[]> = {}
    await Promise.all(stores.map(async (store) => {
      try {
        map[store.id] = await api.get(`/api/stores/${store.id}/emails`)
      } catch { map[store.id] = [] }
    }))
    setLinksMap(map)
  }

  const updateStore = async (storeId: string, data: Record<string, unknown>) => {
    setError('')
    try {
      await api.put(`/api/stores/${storeId}`, data)
      setEditingStore(null)
      loadStores()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Update failed')
    }
  }

  const handleDelete = async () => {
    if (!deletingStore) return
    setError('')
    try {
      await api.del(`/api/stores/${deletingStore.id}?delete_files=${deleteFiles}`)
      setDeletingStore(null)
      setDeleteFiles(false)
      loadStores()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Delete failed')
    }
  }

  const linkEmail = async (storeId: string, emailAccountId: string) => {
    if (!emailAccountId) return
    try {
      await api.post(`/api/stores/${storeId}/emails`, { email_account_id: emailAccountId })
      await loadAllLinks()
      setLinkingEmail(prev => ({ ...prev, [storeId]: '' }))
    } catch { /* ignore */ }
  }

  const unlinkEmail = async (storeId: string, linkId: string) => {
    try {
      await api.del(`/api/stores/${storeId}/emails/${linkId}`)
      await loadAllLinks()
    } catch { /* ignore */ }
  }

  const getAvailableEmails = (storeId: string) => {
    const linked = new Set((linksMap[storeId] || []).map(l => l.email_account_id))
    return emailAccounts.filter(e => !linked.has(e.id))
  }

  return (
    <div className="space-y-4">
      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-3 text-sm text-red-700">
          {error}
          <button onClick={() => setError('')} className="ml-2 text-red-500 hover:text-red-700">&times;</button>
        </div>
      )}

      {stores.length === 0 && (
        <div className="bg-white rounded-lg border border-gray-200 p-8 text-center">
          <p className="text-sm text-gray-400">{t('stores.noStores')}</p>
        </div>
      )}

      {stores.map(store => {
        const links = linksMap[store.id] || []
        const available = getAvailableEmails(store.id)
        const bc = store.browser_config || {}

        if (editingStore?.id === store.id) {
          return (
            <div key={store.id} className="bg-white rounded-lg border border-indigo-300 p-4">
              <h4 className="font-semibold mb-3">{t('settings.editStore')}</h4>
              <StoreForm
                mode="edit"
                initialValues={store}
                onSubmit={(data) => updateStore(store.id, data)}
                onCancel={() => { setEditingStore(null); setError('') }}
              />
            </div>
          )
        }

        return (
          <div key={store.id} className="bg-white rounded-lg border border-gray-200 p-4">
            <div className="flex items-start justify-between">
              <div className="flex-1">
                <div className="flex items-center gap-2 mb-1">
                  <h4 className="font-semibold text-sm">{store.name}</h4>
                  <span className={`px-2 py-0.5 rounded-full text-[10px] font-medium ${
                    store.browser_backend === 'chrome'
                      ? 'bg-indigo-100 text-indigo-700'
                      : 'bg-indigo-100 text-indigo-700'
                  }`}>
                    {store.browser_backend === 'chrome' ? 'Chrome' : t('tasks.ziniao')}
                  </span>
                </div>
                <div className="flex flex-wrap gap-3 text-xs text-gray-500">
                  {store.platform_countries && Object.keys(store.platform_countries).length > 0 ? (
                    <span>
                      {Object.entries(store.platform_countries).map(([p, cs]) => `${p}: ${cs.join(', ')}`).join(' | ')}
                    </span>
                  ) : (
                    <span className="text-gray-400 italic">{t('stores.noPlatformsLearned')}</span>
                  )}
                  {store.browser_backend === 'chrome' && !!bc.proxy_server && (
                    <span>{t('settings.proxyServer')}: {String(bc.proxy_server)}</span>
                  )}
                </div>
              </div>
              <div className="flex gap-2 ml-4">
                <button
                  onClick={() => { setEditingStore(store); setError('') }}
                  className="px-2 py-1 text-xs border border-gray-300 rounded hover:bg-gray-50"
                >
                  {t('common.edit')}
                </button>
                <button
                  onClick={() => { setDeletingStore(store); setDeleteFiles(false); setError('') }}
                  className="px-2 py-1 text-xs border border-red-300 text-red-600 rounded hover:bg-red-50"
                >
                  {t('common.delete')}
                </button>
              </div>
            </div>

            {/* Email links */}
            <div className="mt-3 pt-3 border-t border-gray-100">
              <p className="text-xs font-medium text-gray-500 mb-2">{t('stores.linkedEmails')}</p>
              {links.length === 0 && (
                <p className="text-xs text-gray-400 mb-2">{t('stores.noLinkedEmails')}</p>
              )}
              {links.length > 0 && (
                <div className="flex flex-wrap gap-1.5 mb-2">
                  {links.map(link => (
                    <span key={link.id} className="inline-flex items-center gap-1 px-2 py-0.5 bg-indigo-50 text-indigo-700 rounded-full text-xs">
                      {link.email}
                      <button
                        onClick={() => unlinkEmail(store.id, link.id)}
                        className="text-indigo-400 hover:text-red-500 ml-0.5"
                        title={t('email.disconnect')}
                      >
                        &times;
                      </button>
                    </span>
                  ))}
                </div>
              )}
              {available.length > 0 && (
                <div className="flex gap-2 items-center">
                  <select
                    value={linkingEmail[store.id] || ''}
                    onChange={e => setLinkingEmail(prev => ({ ...prev, [store.id]: e.target.value }))}
                    className="text-xs border border-gray-300 rounded px-2 py-1 bg-white"
                  >
                    <option value="">{t('stores.selectEmail')}</option>
                    {available.map(e => (
                      <option key={e.id} value={e.id}>{e.email}</option>
                    ))}
                  </select>
                  <button
                    onClick={() => linkEmail(store.id, linkingEmail[store.id] || '')}
                    disabled={!linkingEmail[store.id]}
                    className="text-xs px-2 py-1 bg-indigo-600 text-white rounded hover:bg-indigo-700 disabled:opacity-50"
                  >
                    {t('stores.linkEmail')}
                  </button>
                </div>
              )}
            </div>
          </div>
        )
      })}

      {/* Delete confirmation dialog */}
      {deletingStore && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg p-6 max-w-md w-full mx-4 shadow-xl">
            <h3 className="font-semibold text-lg mb-2">{t('stores.deleteTitle')}</h3>
            <p className="text-sm text-gray-600 mb-4">
              {t('stores.deleteWarning', { name: deletingStore.name })}
            </p>

            <label className="flex items-start gap-2 mb-4 p-3 bg-amber-50 border border-amber-200 rounded-lg cursor-pointer">
              <input
                type="checkbox"
                checked={deleteFiles}
                onChange={e => setDeleteFiles(e.target.checked)}
                className="mt-0.5 rounded border-gray-300"
              />
              <div>
                <p className="text-sm font-medium text-amber-800">{t('stores.deleteFilesCheckbox')}</p>
                <p className="text-xs text-amber-600 mt-1">{t('stores.deleteFilesHint')}</p>
              </div>
            </label>

            {error && (
              <div className="mb-3 text-sm text-red-600 bg-red-50 rounded p-2">{error}</div>
            )}

            <div className="flex gap-2 justify-end">
              <button
                onClick={() => { setDeletingStore(null); setError('') }}
                className="px-4 py-2 bg-gray-200 rounded-lg text-sm"
              >
                {t('common.cancel')}
              </button>
              <button
                onClick={handleDelete}
                className="px-4 py-2 bg-red-600 text-white rounded-lg text-sm hover:bg-red-700"
              >
                {t('common.delete')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
