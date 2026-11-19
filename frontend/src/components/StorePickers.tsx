/**
 * Store backend pickers extracted from Sidebar (keeps files under the
 * line limit): the Ziniao account + profile picker and the Yuehulian
 * account + store picker. Rendered by StoreCreationForm in Sidebar.
 */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../api'
import { SecretReveal } from './SecretReveal'
import type { ServerPlatform, YuehulianStore, ZiniaoAccount, ZiniaoBrowserProfile } from '../types'

interface YuehulianLoginStatus { configured: boolean; team_name: string; subaccount: string }

// ─── Yuehulian account + store picker (Windows; one account per machine) ──
// Mirrors the ziniao account UX (select/add/edit/delete + load stores),
// with the single difference the user asked for: 悦互联 allows only ONE
// account per machine, so adding a *different* account while yuehulian
// stores exist is refused (delete those stores first, then add again).
export function YuehulianSection(props: {
  selectedBrowserOauth: string; setSelectedBrowserOauth: (v: string) => void
  yuehulianStores: YuehulianStore[]; yuehulianError: string
  fetchingYuehulian: boolean; fetchYuehulianStores: () => void
}) {
  const { t } = useTranslation()
  const p = props
  const [status, setStatus] = useState<YuehulianLoginStatus | null>(null)
  const [showForm, setShowForm] = useState(false)
  const [editing, setEditing] = useState(false)
  const [team, setTeam] = useState('')
  const [sub, setSub] = useState('')
  const [pw, setPw] = useState('')
  const [busy, setBusy] = useState(false)
  const [formErr, setFormErr] = useState('')

  const refresh = async (thenFetch = false) => {
    try {
      const s = (await api.get('/api/yuehulian/credentials')) as YuehulianLoginStatus
      setStatus(s)
      if (thenFetch && s.configured) p.fetchYuehulianStores()
    } catch { /* unsupported / not configured — leave null */ }
  }
  // Auto-load stores when an account is configured — same as ziniao fetching
  // profiles on account-select; no separate "Load" button.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { void refresh(true) }, [])

  const openAdd = () => { setEditing(false); setTeam(''); setSub(''); setPw(''); setFormErr(''); setShowForm(true) }
  const openEdit = () => { setEditing(true); setTeam(status?.team_name || ''); setSub(status?.subaccount || ''); setPw(''); setFormErr(''); setShowForm(true) }

  const save = async () => {
    if (!team.trim() || !sub.trim() || !pw) { setFormErr(t('settings.yuehulianLoginAllRequired')); return }
    setBusy(true); setFormErr('')
    try {
      await api.post('/api/yuehulian/credentials', { team_name: team, subaccount: sub, password: pw })
      setShowForm(false); setPw(''); await refresh(true)
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      let code = ''
      try { const d = JSON.parse(msg); if (d?.status) code = d.status } catch { /* non-JSON */ }
      setFormErr(code === 'one_account' ? t('settings.yuehulianOneAccount') : msg)
    } finally { setBusy(false) }
  }

  const del = async () => {
    if (!confirm(t('settings.yuehulianDeleteAccountConfirm'))) return
    setBusy(true); setFormErr('')
    try { await api.del('/api/yuehulian/credentials'); setShowForm(false); await refresh() }
    catch (e) { setFormErr(e instanceof Error ? e.message : String(e)) }
    finally { setBusy(false) }
  }

  const statusHint = (() => {
    if (!p.yuehulianError) return ''
    const s = p.yuehulianError.replace('yuehulian:', '')
    if (s === 'login_required') return t('settings.yuehulianLoginRequired')
    if (s === 'client_not_running') return t('settings.yuehulianClientNotRunning')
    if (s === 'no_stores') return t('settings.yuehulianNoStores')
    return t('settings.yuehulianLoadFailed')
  })()

  const btn = 'flex-shrink-0 w-7 h-7 flex items-center justify-center text-sm rounded border border-gray-300'
  return (
    <div className="mb-2 space-y-2">
      {!showForm && (
        <div className="flex items-center gap-1">
          <div className="min-w-0 flex-1 px-2 py-1.5 border border-gray-300 rounded text-xs bg-gray-50 truncate">
            {status?.configured ? `${status.team_name} / ${status.subaccount}` : t('settings.yuehulianNoAccount')}
          </div>
          {status?.configured && (
            <>
              <button type="button" onClick={openEdit} title={t('common.edit')} className={`${btn} text-gray-500 hover:bg-gray-100`}>&#9998;</button>
              <button type="button" onClick={del} disabled={busy} title={t('common.delete')} className={`${btn} text-red-500 hover:bg-red-50 disabled:opacity-50`}>&times;</button>
            </>
          )}
          <button type="button" onClick={openAdd} title={t('settings.yuehulianAddAccount')} className={`${btn} text-indigo-600 hover:bg-indigo-50`}>+</button>
        </div>
      )}
      {showForm && (
        <div className="space-y-1 p-2 bg-gray-50 rounded border border-gray-200">
          <p className="text-xs font-medium text-gray-600 mb-1">{editing ? t('settings.yuehulianEditAccount') : t('settings.yuehulianAddAccount')}</p>
          <input value={team} onChange={e => setTeam(e.target.value)} placeholder={t('settings.yuehulianTeamName')} className="w-full px-2 py-1 border border-gray-300 rounded text-xs" />
          <input value={sub} onChange={e => setSub(e.target.value)} placeholder={t('settings.yuehulianSubaccount')} className="w-full px-2 py-1 border border-gray-300 rounded text-xs" />
          <input type="password" value={pw} onChange={e => setPw(e.target.value)} placeholder={editing ? t('settings.yuehulianPasswordUnchanged') : t('settings.yuehulianPassword')} className="w-full px-2 py-1 border border-gray-300 rounded text-xs" />
          <div className="flex gap-1">
            <button type="button" onClick={save} disabled={busy} className="px-2 py-1 bg-indigo-600 text-white rounded text-xs disabled:opacity-50">{busy ? t('settings.yuehulianLoginSaving') : t('common.save')}</button>
            <button type="button" onClick={() => { setShowForm(false); setFormErr('') }} className="px-2 py-1 bg-gray-200 rounded text-xs">{t('common.cancel')}</button>
          </div>
          {formErr && <p className="text-[11px] text-red-600">{formErr}</p>}
        </div>
      )}
      {status?.configured && p.fetchingYuehulian && (
        <p className="text-[11px] text-gray-500">{t('settings.yuehulianLoading')}</p>
      )}
      {p.yuehulianStores.length > 0 && (
        <select
          value={p.selectedBrowserOauth}
          onChange={e => p.setSelectedBrowserOauth(e.target.value)}
          className="w-full px-2 py-1.5 border border-gray-300 rounded text-xs bg-white"
        >
          <option value="">{t('settings.yuehulianSelectStore')}</option>
          {p.yuehulianStores.map(s => (
            <option key={s.name} value={s.name}>
              {s.name}{s.platform ? ` (${s.platform} ${s.remote_env})` : ''}
            </option>
          ))}
        </select>
      )}
      {statusHint && (
        <div className="flex items-center gap-2">
          <p className="text-[11px] text-red-600">{statusHint}</p>
          <button
            type="button"
            onClick={p.fetchYuehulianStores}
            disabled={p.fetchingYuehulian}
            className="text-[11px] text-indigo-600 hover:underline disabled:opacity-50"
          >{t('common.refresh')}</button>
        </div>
      )}
    </div>
  )
}

// ─── Ziniao account + browser picker ─────────────────
export function ZiniaoSection(props: {
  ziniaoAccounts: ZiniaoAccount[]
  selectedZiniaoAccountId: string; setSelectedZiniaoAccountId: (v: string) => void
  ziniaoBrowsers: ZiniaoBrowserProfile[]
  selectedBrowserOauth: string; setSelectedBrowserOauth: (v: string) => void
  fetchingBrowsers: boolean; browserFetchError: string
  setBrowserFetchError: (v: string) => void
  fetchBrowserProfiles: (accountId: string) => void
  restartZiniao: (accountId: string) => void
  ziniaoRetried: boolean; serverPlatform: ServerPlatform | null
  showAddAccount: boolean; setShowAddAccount: (v: boolean) => void
  showAccountPassword: boolean; setShowAccountPassword: React.Dispatch<React.SetStateAction<boolean>>
  editingAccountId: string; setEditingAccountId: (v: string) => void
  newAccount: { name: string; company: string; username: string; password: string }
  setNewAccount: React.Dispatch<React.SetStateAction<{ name: string; company: string; username: string; password: string }>>
  createZiniaoAccount: () => void; updateZiniaoAccount: () => void
  deleteZiniaoAccount: (id: string) => void
  /** Admins get the Show/Copy reveal for a stored Ziniao password. */
  isAdmin: boolean
}) {
  const { t } = useTranslation()
  const p = props
  return (
    <div className="space-y-2 mb-2">
      <div className="flex items-center gap-1">
        <select
          value={p.selectedZiniaoAccountId}
          onChange={e => { p.setSelectedZiniaoAccountId(e.target.value); p.setBrowserFetchError(''); if (e.target.value) p.fetchBrowserProfiles(e.target.value) }}
          className="min-w-0 flex-1 px-2 py-1.5 border border-gray-300 rounded text-xs bg-white"
        >
          <option value="">{t('settings.ziniaoSelectAccount')}</option>
          {p.ziniaoAccounts.map(a => (
            <option key={a.id} value={a.id}>{a.name} ({a.company})</option>
          ))}
        </select>
        {p.selectedZiniaoAccountId && (
          <>
            <button onClick={() => {
              const acct = p.ziniaoAccounts.find(a => a.id === p.selectedZiniaoAccountId)
              if (acct) { p.setNewAccount({ name: acct.name, company: acct.company, username: acct.username, password: '' }); p.setEditingAccountId(acct.id); p.setShowAddAccount(true) }
            }} className="flex-shrink-0 w-7 h-7 flex items-center justify-center text-sm text-gray-500 hover:bg-gray-100 rounded border border-gray-300" title={t('common.edit')}>&#9998;</button>
            <button onClick={() => { if (confirm(t('settings.deleteAccountConfirm'))) p.deleteZiniaoAccount(p.selectedZiniaoAccountId) }} className="flex-shrink-0 w-7 h-7 flex items-center justify-center text-sm text-red-500 hover:bg-red-50 rounded border border-gray-300" title={t('common.delete')}>&times;</button>
          </>
        )}
        <button onClick={() => { p.setEditingAccountId(''); p.setNewAccount({ name: '', company: '', username: '', password: '' }); p.setShowAddAccount(!p.showAddAccount) }} className="flex-shrink-0 w-7 h-7 flex items-center justify-center text-sm text-indigo-600 hover:bg-indigo-50 rounded border border-gray-300" title="Add account">+</button>
      </div>
      {p.showAddAccount && (
        <div className="space-y-1 p-2 bg-gray-50 rounded border border-gray-200">
          <p className="text-xs font-medium text-gray-600 mb-1">{p.editingAccountId ? t('settings.editZiniaoAccount') : t('settings.addZiniaoAccount')}</p>
          <input value={p.newAccount.company} onChange={e => p.setNewAccount(prev => ({ ...prev, company: e.target.value }))} placeholder={t('settings.ziniaoCompany')} className="w-full px-2 py-1 border border-gray-300 rounded text-xs" />
          <input value={p.newAccount.username} onChange={e => p.setNewAccount(prev => ({ ...prev, username: e.target.value }))} placeholder={t('settings.ziniaoUsername')} className="w-full px-2 py-1 border border-gray-300 rounded text-xs" />
          <div className="relative">
            <input value={p.newAccount.password} onChange={e => p.setNewAccount(prev => ({ ...prev, password: e.target.value }))} placeholder={p.editingAccountId ? t('settings.passwordLeaveBlank') : t('auth.password')} type={p.showAccountPassword ? 'text' : 'password'} className="w-full px-2 py-1 pr-7 border border-gray-300 rounded text-xs" />
            <button type="button" onClick={() => p.setShowAccountPassword(v => !v)} className="absolute right-1.5 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600" tabIndex={-1}>
              {p.showAccountPassword ? <svg xmlns="http://www.w3.org/2000/svg" className="w-3.5 h-3.5" viewBox="0 0 20 20" fill="currentColor"><path fillRule="evenodd" d="M3.707 2.293a1 1 0 00-1.414 1.414l14 14a1 1 0 001.414-1.414l-1.473-1.473A10.014 10.014 0 0020 10c-1.796-4.667-6-8-10-8a9.864 9.864 0 00-4.512 1.074L3.707 2.293zM10 15a9.864 9.864 0 004.512-1.074L13.06 12.474A3 3 0 017.526 6.94L5.534 4.948A7.966 7.966 0 002 10c1.194 3.1 3.672 5.457 6.676 6.725L10 15zm2.121-4.879A1 1 0 0010 9l-.707-.707A1 1 0 0010 11l2.121-2.121-.707-.707.707.707z" clipRule="evenodd" /></svg> : <svg xmlns="http://www.w3.org/2000/svg" className="w-3.5 h-3.5" viewBox="0 0 20 20" fill="currentColor"><path d="M10 12a2 2 0 100-4 2 2 0 000 4z" /><path fillRule="evenodd" d="M.458 10C1.732 5.943 5.522 3 10 3s8.268 2.943 9.542 7c-1.274 4.057-5.064 7-9.542 7S1.732 14.057.458 10zM14 10a4 4 0 11-8 0 4 4 0 018 0z" clipRule="evenodd" /></svg>}
            </button>
          </div>
          {p.isAdmin && p.editingAccountId && (
            <div className="flex items-center gap-1">
              <SecretReveal resource="ziniao_account" id={p.editingAccountId} field="password" compact />
            </div>
          )}
          <div className="flex gap-1">
            <button onClick={p.editingAccountId ? p.updateZiniaoAccount : p.createZiniaoAccount} className="px-2 py-1 bg-indigo-600 text-white rounded text-xs">{t('common.save')}</button>
            <button onClick={() => { p.setShowAddAccount(false); p.setEditingAccountId(''); p.setShowAccountPassword(false) }} className="px-2 py-1 bg-gray-200 rounded text-xs">{t('common.cancel')}</button>
          </div>
          <p className="text-[10px] text-gray-400">Credentials stored in local SQLite, not in the cloud.</p>
        </div>
      )}
      {p.selectedZiniaoAccountId && (() => {
        // "ziniao:STATUS[:BASE64_MSG]" — the optional 3rd segment is
        // Ziniao's own err text (base64'd to survive colons).
        const zs = p.browserFetchError.startsWith('ziniao:') ? (() => {
          const [, status, encoded] = p.browserFetchError.split(':')
          let message = ''
          try { if (encoded) message = decodeURIComponent(escape(atob(encoded))) } catch { /* leave empty */ }
          return { status, message }
        })() : null
        const isMac = p.serverPlatform === 'mac', isWsl = p.serverPlatform === 'wsl', isWindows = p.serverPlatform === 'windows'
        const editSelected = () => { const a = p.ziniaoAccounts.find(x => x.id === p.selectedZiniaoAccountId); if (a) { p.setNewAccount({ name: a.name, company: a.company, username: a.username, password: '' }); p.setEditingAccountId(a.id); p.setShowAddAccount(true) } }

        return p.fetchingBrowsers ? (
          <div className="flex flex-col items-center justify-center py-6 px-4 bg-indigo-50 rounded-lg border border-indigo-200">
            <div className="w-6 h-6 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin mb-3"></div>
            <p className="text-sm font-medium text-indigo-700">{t('settings.ziniaoLoading')}</p>
          </div>
        ) : zs?.status === 'running_normal' && (isMac || isWsl || isWindows) ? (
          // Mac/Windows/WSL: Ziniao running in normal mode
          <div className="flex flex-col items-center justify-center py-6 px-4 bg-amber-50 rounded-lg border border-amber-200">
            <p className="text-sm font-medium text-amber-700 mb-2">{p.ziniaoRetried ? t('settings.ziniaoStillRunning') : t('settings.ziniaoRunningNormalMode')}</p>
            <p className="text-xs text-amber-600 mb-3">{p.ziniaoRetried ? '' : t('settings.ziniaoNormalModeHint')}</p>
            <div className="flex gap-2">
              {p.ziniaoRetried && <button onClick={() => p.restartZiniao(p.selectedZiniaoAccountId)} className="px-3 py-1 bg-amber-600 text-white rounded text-xs hover:bg-amber-700">{isWsl ? t('settings.ziniaoForceKill') : t('settings.ziniaoForceRestart')}</button>}
              <button onClick={() => p.fetchBrowserProfiles(p.selectedZiniaoAccountId)} className="px-3 py-1 bg-gray-600 text-white rounded text-xs hover:bg-gray-700">{t('common.refresh')}</button>
            </div>
          </div>
        ) : zs?.status === 'no_permission' ? (
          // -10003: surface Ziniao's own err if present; otherwise generic
          <div className="flex flex-col items-center justify-center py-6 px-4 bg-red-50 rounded-lg border border-red-200">
            {zs.message ? <p className="text-sm font-medium text-red-700 mb-3 break-all">{zs.message}</p> : <><p className="text-sm font-medium text-red-700 mb-2">{t('settings.ziniaoNoPermission')}</p><p className="text-xs text-red-600 mb-3">{t('settings.ziniaoNoPermissionHint')}</p></>}
            <div className="flex gap-2">
              {!zs.message && <a href="https://open.ziniao.com/docSupport?docId=99" target="_blank" rel="noopener noreferrer" className="px-3 py-1 bg-indigo-600 text-white rounded text-xs hover:bg-indigo-700">{t('settings.ziniaoEnableWebDriver')}</a>}
              <button onClick={() => p.fetchBrowserProfiles(p.selectedZiniaoAccountId)} className="px-3 py-1 bg-red-600 text-white rounded text-xs hover:bg-red-700">{t('common.refresh')}</button>
            </div>
          </div>
        ) : zs?.status === 'credentials_error' ? (
          // Stored password can't be decrypted (e.g. DB copied across installs).
          <div className="flex flex-col items-center justify-center py-6 px-4 bg-red-50 rounded-lg border border-red-200">
            <p className="text-sm font-medium text-red-700 mb-3 break-all">{t('settings.ziniaoCredentialsError')}</p>
            <div className="flex gap-2">
              <button onClick={editSelected} className="px-3 py-1 bg-indigo-600 text-white rounded text-xs hover:bg-indigo-700">{t('settings.editZiniaoAccount')}</button>
              <button onClick={() => p.fetchBrowserProfiles(p.selectedZiniaoAccountId)} className="px-3 py-1 bg-red-600 text-white rounded text-xs hover:bg-red-700">{t('common.refresh')}</button>
            </div>
          </div>
        ) : zs?.status === 'not_installed' && isMac ? (
          // Mac: Ziniao not installed
          <div className="flex flex-col items-center justify-center py-6 px-4 bg-red-50 rounded-lg border border-red-200">
            <p className="text-sm font-medium text-red-700 mb-2">{t('settings.ziniaoNotInstalled')}</p>
            <p className="text-xs text-red-600 mb-3">{t('settings.ziniaoNotInstalledHint')}</p>
            <div className="flex gap-2">
              <a href="https://www.ziniao.com/download" target="_blank" rel="noopener noreferrer" className="px-3 py-1 bg-indigo-600 text-white rounded text-xs hover:bg-indigo-700">{t('settings.ziniaoDownloadZiniao')}</a>
              <button onClick={() => p.fetchBrowserProfiles(p.selectedZiniaoAccountId)} className="px-3 py-1 bg-red-600 text-white rounded text-xs hover:bg-red-700">{t('common.refresh')}</button>
            </div>
          </div>
        ) : zs?.status === 'new_terminal_login' ? (
          // Ziniao new-device security check: manual login approval needed.
          <div className="flex flex-col items-center justify-center py-6 px-4 bg-amber-50 rounded-lg border border-amber-200">
            <p className="text-sm font-medium text-amber-700 mb-3 break-words text-center">{t('settings.ziniaoNewTerminalLogin')}</p>
            <button onClick={() => p.fetchBrowserProfiles(p.selectedZiniaoAccountId)} className="px-3 py-1 bg-amber-600 text-white rounded text-xs hover:bg-amber-700">{t('common.refresh')}</button>
          </div>
        ) : p.browserFetchError === 'connect_error' || (zs && !['no_profiles'].includes(zs.status)) ? (
          // Fallback connect error. Platform UI keys off serverPlatform.
          <div className="flex flex-col items-center justify-center py-6 px-4 bg-red-50 rounded-lg border border-red-200">
            <p className="text-sm font-medium text-red-700 mb-2">{isMac ? t('settings.ziniaoConnectErrorMac') : t('settings.ziniaoConnectError')}</p>
            {isWsl && <p className="text-xs text-red-600 mb-3">{t('settings.ziniaoLaunchHint')}</p>}
            <div className="flex gap-2">
              {isWsl && <a href="/api/ziniao/launcher" download className="px-3 py-1 bg-indigo-600 text-white rounded text-xs hover:bg-indigo-700">{t('settings.ziniaoDownloadLauncher')}</a>}
              <button onClick={editSelected} className="px-3 py-1 bg-indigo-600 text-white rounded text-xs hover:bg-indigo-700">{t('settings.editZiniaoAccount')}</button>
              <button onClick={() => p.fetchBrowserProfiles(p.selectedZiniaoAccountId)} className="px-3 py-1 bg-red-600 text-white rounded text-xs hover:bg-red-700">{t('common.refresh')}</button>
            </div>
          </div>
        ) : p.browserFetchError.startsWith('api_error:') ? (
          <div className="flex flex-col items-center justify-center py-6 px-4 bg-red-50 rounded-lg border border-red-200">
            <p className="text-sm font-medium text-red-700 mb-2">{p.browserFetchError.slice('api_error:'.length)}</p>
            <button onClick={() => p.fetchBrowserProfiles(p.selectedZiniaoAccountId)} className="px-3 py-1 bg-red-600 text-white rounded text-xs hover:bg-red-700">{t('common.refresh')}</button>
          </div>
        ) : p.browserFetchError.startsWith('restart_failed:') ? (
          <div className="flex flex-col items-center justify-center py-6 px-4 bg-red-50 rounded-lg border border-red-200">
            <p className="text-sm font-medium text-red-700 mb-2">{t('settings.ziniaoRestartFailed') || 'Failed to restart. Please close Ziniao manually and try again.'}</p>
            <button onClick={() => p.fetchBrowserProfiles(p.selectedZiniaoAccountId)} className="px-3 py-1 bg-red-600 text-white rounded text-xs hover:bg-red-700">{t('common.refresh')}</button>
          </div>
        ) : p.browserFetchError === 'no_profiles' ? (
          <div className="flex flex-col items-center justify-center py-6 px-4 bg-amber-50 rounded-lg border border-amber-200">
            <p className="text-sm font-medium text-amber-700">{t('settings.ziniaoNoProfiles')}</p>
          </div>
        ) : p.ziniaoBrowsers.length > 0 ? (
          <select
            value={p.selectedBrowserOauth}
            onChange={e => p.setSelectedBrowserOauth(e.target.value)}
            className="w-full px-2 py-1.5 border border-gray-300 rounded text-xs bg-white"
          >
            <option value="">{t('settings.ziniaoSelectProfile')}</option>
            {p.ziniaoBrowsers.map(b => (
              <option key={b.browser_oauth} value={b.browser_oauth}>{b.browser_name}</option>
            ))}
          </select>
        ) : null
      })()}
    </div>
  )
}
