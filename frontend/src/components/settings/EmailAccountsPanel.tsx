import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../../api'
import type { EmailAccount, Store } from '../../types'

interface StoreLink {
  link_id: string
  store_id: string
  store_name: string
}

interface EmailAccountsPanelProps {
  emailAccounts: EmailAccount[]
  loadEmailAccounts: () => void
  stores: Store[]
}

export function EmailAccountsPanel({ emailAccounts, loadEmailAccounts, stores }: EmailAccountsPanelProps) {
  const { t } = useTranslation()
  const [showAddEmail, setShowAddEmail] = useState(false)
  const [emailForm, setEmailForm] = useState({ email: '', imap_host: '', imap_port: 993, password: '', smtp_host: '', smtp_port: 465, smtp_use_tls: false })
  const [autoDetected, setAutoDetected] = useState(false)
  const [testResult, setTestResult] = useState<'success' | 'failed' | null>(null)
  const [testing, setTesting] = useState(false)
  const [saving, setSaving] = useState(false)
  const [linksMap, setLinksMap] = useState<Record<string, StoreLink[]>>({})
  const [linkingStore, setLinkingStore] = useState<Record<string, string>>({})

  // Load store links for all email accounts
  const loadAllLinks = async (accounts: EmailAccount[]) => {
    const map: Record<string, StoreLink[]> = {}
    await Promise.all(accounts.map(async (acct) => {
      try {
        const links = await api.get(`/api/email-accounts/${acct.id}/links`)
        map[acct.id] = links
      } catch { map[acct.id] = [] }
    }))
    setLinksMap(map)
  }

  useEffect(() => {
    loadEmailAccounts()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (emailAccounts.length > 0) {
      const fetchLinks = async () => {
        const map: Record<string, StoreLink[]> = {}
        await Promise.all(emailAccounts.map(async (acct) => {
          try {
            const links = await api.get(`/api/email-accounts/${acct.id}/links`)
            map[acct.id] = links
          } catch { map[acct.id] = [] }
        }))
        setLinksMap(map)
      }
      fetchLinks()
    }
  }, [emailAccounts])

  const linkStore = async (accountId: string, storeId: string) => {
    if (!storeId) return
    try {
      await api.post(`/api/stores/${storeId}/emails`, { email_account_id: accountId })
      await loadAllLinks(emailAccounts)
      setLinkingStore(prev => ({ ...prev, [accountId]: '' }))
    } catch { /* ignore */ }
  }

  const unlinkStore = async (storeId: string, linkId: string) => {
    try {
      await api.del(`/api/stores/${storeId}/emails/${linkId}`)
      await loadAllLinks(emailAccounts)
    } catch { /* ignore */ }
  }

  const discoverImap = async (email: string) => {
    if (!email || !email.includes('@')) return
    try {
      const data = await api.get(`/api/email-accounts/discover?email=${encodeURIComponent(email)}`)
      if (data.imap_host) {
        setEmailForm(prev => ({ ...prev, imap_host: data.imap_host, imap_port: data.imap_port || 993 }))
        setAutoDetected(true)
      }
      // Also discover SMTP
      const smtp = await api.get(`/api/email-accounts/discover-smtp?email=${encodeURIComponent(email)}`)
      if (smtp.smtp_host) {
        setEmailForm(prev => ({ ...prev, smtp_host: smtp.smtp_host, smtp_port: smtp.smtp_port || 465, smtp_use_tls: smtp.smtp_use_starttls ?? false }))
      }
    } catch { /* ignore */ }
  }

  const testConnection = async () => {
    if (!emailForm.email || !emailForm.imap_host || !emailForm.password) return
    setTesting(true); setTestResult(null)
    try {
      const res = await api.post('/api/email-accounts/test', {
        email: emailForm.email, imap_host: emailForm.imap_host,
        imap_port: emailForm.imap_port, password: emailForm.password,
      })
      setTestResult(res.ok ? 'success' : 'failed')
    } catch {
      setTestResult('failed')
    }
    setTesting(false)
  }

  const saveAccount = async () => {
    if (!emailForm.email || !emailForm.imap_host || !emailForm.password || saving) return
    setSaving(true)
    try {
      await api.post('/api/email-accounts', {
        email: emailForm.email, imap_host: emailForm.imap_host,
        imap_port: emailForm.imap_port, password: emailForm.password,
        smtp_host: emailForm.smtp_host || undefined,
        smtp_port: emailForm.smtp_port || undefined,
        smtp_use_tls: emailForm.smtp_use_tls,
      })
      setEmailForm({ email: '', imap_host: '', imap_port: 993, password: '', smtp_host: '', smtp_port: 465, smtp_use_tls: false })
      setShowAddEmail(false); setAutoDetected(false); setTestResult(null)
      loadEmailAccounts()
    } catch { /* ignore */ }
    setSaving(false)
  }

  const deleteAccount = async (id: string) => {
    try { await api.del(`/api/email-accounts/${id}`); loadEmailAccounts() } catch { /* ignore */ }
  }

  // Compute which stores are already linked to a given email account
  const getAvailableStores = (accountId: string) => {
    const linked = new Set((linksMap[accountId] || []).map(l => l.store_id))
    return stores.filter(s => !linked.has(s.id))
  }

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4">
      <div className="flex items-center justify-between mb-4">
        <h3 className="font-semibold">{t('email.accounts')}</h3>
        <button
          onClick={() => setShowAddEmail(!showAddEmail)}
          className="px-3 py-1.5 bg-indigo-600 text-white rounded-lg text-sm hover:bg-indigo-700"
        >
          + {t('email.addAccount')}
        </button>
      </div>

      {showAddEmail && (
        <div className="mb-4 p-4 bg-gray-50 rounded-lg border border-gray-200 space-y-3">
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">{t('auth.email')}</label>
            <input
              value={emailForm.email}
              onChange={e => { setEmailForm(prev => ({ ...prev, email: e.target.value })); setAutoDetected(false) }}
              onBlur={() => discoverImap(emailForm.email)}
              placeholder="user@example.com"
              className="w-full px-3 py-2 border border-gray-300 rounded text-sm"
              autoFocus
            />
          </div>
          <div className="grid grid-cols-3 gap-2">
            <div className="col-span-2">
              <label className="block text-xs font-medium text-gray-600 mb-1">
                {t('email.imapHost')}
                {autoDetected && <span className="ml-1 text-green-600 text-[10px]">({t('email.autoDetected')})</span>}
              </label>
              <input
                value={emailForm.imap_host}
                onChange={e => setEmailForm(prev => ({ ...prev, imap_host: e.target.value }))}
                placeholder="imap.gmail.com"
                className="w-full px-3 py-2 border border-gray-300 rounded text-sm"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">{t('email.imapPort')}</label>
              <input
                type="number"
                value={emailForm.imap_port}
                onChange={e => setEmailForm(prev => ({ ...prev, imap_port: Number(e.target.value) }))}
                className="w-full px-3 py-2 border border-gray-300 rounded text-sm"
              />
            </div>
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">{t('email.password')}</label>
            <input
              type="password"
              value={emailForm.password}
              onChange={e => setEmailForm(prev => ({ ...prev, password: e.target.value }))}
              placeholder={t('email.password')}
              className="w-full px-3 py-2 border border-gray-300 rounded text-sm"
            />
          </div>
          <div className="grid grid-cols-3 gap-2">
            <div className="col-span-2">
              <label className="block text-xs font-medium text-gray-600 mb-1">
                {t('email.smtpHost')}
                {autoDetected && <span className="ml-1 text-green-600 text-[10px]">({t('email.autoDetected')})</span>}
              </label>
              <input
                value={emailForm.smtp_host}
                onChange={e => setEmailForm(prev => ({ ...prev, smtp_host: e.target.value }))}
                placeholder="smtp.gmail.com"
                className="w-full px-3 py-2 border border-gray-300 rounded text-sm"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">{t('email.smtpPort')}</label>
              <input
                type="number"
                value={emailForm.smtp_port}
                onChange={e => setEmailForm(prev => ({ ...prev, smtp_port: Number(e.target.value) }))}
                className="w-full px-3 py-2 border border-gray-300 rounded text-sm"
              />
            </div>
          </div>
          {testResult && (
            <div className={`text-xs px-3 py-2 rounded ${testResult === 'success' ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}`}>
              {testResult === 'success' ? t('email.testSuccess') : t('email.testFailed')}
            </div>
          )}
          <div className="flex gap-2">
            <button
              onClick={testConnection}
              disabled={testing || !emailForm.email || !emailForm.imap_host || !emailForm.password}
              className="px-3 py-1.5 bg-gray-600 text-white rounded text-sm hover:bg-gray-700 disabled:opacity-50"
            >
              {testing ? t('common.loading') : t('email.testConnection')}
            </button>
            <button
              onClick={saveAccount}
              disabled={saving || !emailForm.email || !emailForm.imap_host || !emailForm.password}
              className="px-3 py-1.5 bg-green-600 text-white rounded text-sm hover:bg-green-700 disabled:opacity-50"
            >
              {saving ? t('common.loading') : t('common.save')}
            </button>
            <button
              onClick={() => { setShowAddEmail(false); setTestResult(null) }}
              className="px-3 py-1.5 bg-gray-200 rounded text-sm"
            >
              {t('common.cancel')}
            </button>
          </div>
        </div>
      )}

      {emailAccounts.length === 0 && !showAddEmail && (
        <p className="text-sm text-gray-400 text-center py-4">{t('email.noEmails')}</p>
      )}

      {emailAccounts.length > 0 && (
        <div className="space-y-3">
          {emailAccounts.map(acct => {
            const links = linksMap[acct.id] || []
            const available = getAvailableStores(acct.id)
            return (
              <div key={acct.id} className="p-3 border border-gray-200 rounded-lg">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-sm font-medium">{acct.email}</p>
                    <p className="text-xs text-gray-400">{acct.imap_host}:{acct.imap_port} {acct.use_ssl ? '(SSL)' : ''} | {acct.smtp_host ? `SMTP: ${acct.smtp_host}:${acct.smtp_port}` : t('email.noSmtp')}</p>
                  </div>
                  <button
                    onClick={() => { if (confirm(t('common.confirm'))) deleteAccount(acct.id) }}
                    className="px-2 py-1 text-xs border border-red-300 text-red-600 rounded hover:bg-red-50"
                  >
                    {t('common.delete')}
                  </button>
                </div>

                {/* Store links section */}
                <div className="mt-3 pt-3 border-t border-gray-100">
                  <p className="text-xs font-medium text-gray-500 mb-2">{t('email.linkedStores')}</p>
                  {links.length === 0 && (
                    <p className="text-xs text-gray-400 mb-2">{t('email.noStoresLinked')}</p>
                  )}
                  {links.length > 0 && (
                    <div className="flex flex-wrap gap-1.5 mb-2">
                      {links.map(link => (
                        <span key={link.link_id} className="inline-flex items-center gap-1 px-2 py-0.5 bg-indigo-50 text-indigo-700 rounded-full text-xs">
                          {link.store_name}
                          <button
                            onClick={() => unlinkStore(link.store_id, link.link_id)}
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
                        value={linkingStore[acct.id] || ''}
                        onChange={e => setLinkingStore(prev => ({ ...prev, [acct.id]: e.target.value }))}
                        className="text-xs border border-gray-300 rounded px-2 py-1 bg-white"
                      >
                        <option value="">{t('email.selectStore')}</option>
                        {available.map(s => (
                          <option key={s.id} value={s.id}>{s.name}</option>
                        ))}
                      </select>
                      <button
                        onClick={() => linkStore(acct.id, linkingStore[acct.id] || '')}
                        disabled={!linkingStore[acct.id]}
                        className="text-xs px-2 py-1 bg-indigo-600 text-white rounded hover:bg-indigo-700 disabled:opacity-50"
                      >
                        {t('email.linkToStore')}
                      </button>
                    </div>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
