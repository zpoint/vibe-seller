import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { LanguageSwitcher, WsFileItem } from './ui'
import { SkillItem } from './SkillItem'
import { StoreFilesSection } from './WorkspaceStoreSections'
import { api } from '../api'
import { sendEvent } from '../lib/telemetry'
import { FrontendEvent } from '../lib/telemetryEvents'
import type { Store, AuthUser, AppView, ServerPlatform, WsStructured, ZiniaoAccount, ZiniaoBrowserProfile } from '../types'

interface SidebarProps {
  // Mobile drawer control (desktop leaves these at defaults)
  isMobile: boolean
  navOpen: boolean
  closeNav: () => void
  currentUser: AuthUser
  appView: AppView
  setAppView: (v: AppView) => void
  handleLogout: () => void
  authRequired: boolean
  // Store list
  stores: Store[]
  selectedStore: Store | null
  showAllTasks: boolean
  selectStore: (store: Store) => void
  selectAllTasks: () => void
  // Store creation
  showCreateStore: boolean
  setShowCreateStore: (v: boolean) => void
  createStore: () => void
  newStoreName: string
  setNewStoreName: (v: string) => void
  newStoreBackend: string
  setNewStoreBackend: (v: string) => void
  showProxy: boolean
  setShowProxy: (v: boolean) => void
  newStoreProxyServer: string
  setNewStoreProxyServer: (v: string) => void
  newStoreProxyBypass: string
  setNewStoreProxyBypass: (v: string) => void
  // Ziniao
  ziniaoAccounts: ZiniaoAccount[]
  selectedZiniaoAccountId: string
  setSelectedZiniaoAccountId: (v: string) => void
  ziniaoBrowsers: ZiniaoBrowserProfile[]
  selectedBrowserOauth: string
  setSelectedBrowserOauth: (v: string) => void
  fetchingBrowsers: boolean
  browserFetchError: string
  setBrowserFetchError: (v: string) => void
  fetchBrowserProfiles: (accountId: string) => void
  restartZiniao: (accountId: string) => void
  ziniaoRetried: boolean
  serverPlatform: ServerPlatform | null; serverVersion: string
  showAddAccount: boolean
  setShowAddAccount: (v: boolean) => void
  showAccountPassword: boolean
  setShowAccountPassword: React.Dispatch<React.SetStateAction<boolean>>
  editingAccountId: string
  setEditingAccountId: (v: string) => void
  newAccount: { name: string; company: string; username: string; password: string }
  setNewAccount: React.Dispatch<React.SetStateAction<{ name: string; company: string; username: string; password: string }>>
  createZiniaoAccount: () => void
  updateZiniaoAccount: () => void
  deleteZiniaoAccount: (id: string) => void
  // Workspace
  wsStructured: WsStructured | null
  wsSelectedFile: string | null
  wsExpandedStores: Set<string>
  wsExpandedSkills: Set<string>
  toggleStoreExpanded: (slug: string) => void
  toggleSkillExpanded: (slug: string) => void
  openWsFile: (path: string) => void
  deleteWsFile: (path: string) => void
  wsNewFileName: string
  setWsNewFileName: (v: string) => void
  wsNewFileSection: string | null
  setWsNewFileSection: (v: string | null) => void
  createWsFile: (section: string, fileName: string) => void
  syncProjectKnowledge: () => void
  wsSyncing: boolean
  wsSyncMeta: Record<string, unknown> | null
  loadWsStructured: () => void
  syncBuiltinSkills: () => void
  wsSkillsSyncing: boolean
}

export function Sidebar(props: SidebarProps) {
  const { t } = useTranslation()
  const {
    isMobile, navOpen, closeNav,
    currentUser, appView, setAppView, handleLogout, authRequired,
    stores, selectedStore, showAllTasks, selectStore, selectAllTasks,
    showCreateStore, setShowCreateStore, createStore,
    newStoreName, setNewStoreName, newStoreBackend, setNewStoreBackend,
    showProxy, setShowProxy,
    newStoreProxyServer, setNewStoreProxyServer, newStoreProxyBypass, setNewStoreProxyBypass,
    ziniaoAccounts, selectedZiniaoAccountId, setSelectedZiniaoAccountId,
    ziniaoBrowsers, selectedBrowserOauth, setSelectedBrowserOauth,
    fetchingBrowsers, browserFetchError, setBrowserFetchError,
    fetchBrowserProfiles, restartZiniao, ziniaoRetried,
    serverPlatform, serverVersion,
    showAddAccount, setShowAddAccount, showAccountPassword, setShowAccountPassword,
    editingAccountId, setEditingAccountId, newAccount, setNewAccount,
    createZiniaoAccount, updateZiniaoAccount, deleteZiniaoAccount,
    wsStructured, wsSelectedFile, wsExpandedStores, wsExpandedSkills,
    toggleStoreExpanded, toggleSkillExpanded, openWsFile, deleteWsFile,
    wsNewFileName, setWsNewFileName, wsNewFileSection, setWsNewFileSection,
    createWsFile, syncProjectKnowledge, wsSyncing, wsSyncMeta, loadWsStructured,
    syncBuiltinSkills, wsSkillsSyncing,
  } = props

  /* Resizable sidebar: drag the right edge; width persists locally. */
  const [sidebarWidth, setSidebarWidth] = useState<number>(() => {
    const saved = Number(localStorage.getItem('vs.sidebarWidth'))
    return saved >= 200 && saved <= 560 ? saved : 256
  })
  const startSidebarResize = (e: React.MouseEvent) => {
    e.preventDefault()
    const startX = e.clientX
    const startW = sidebarWidth
    const onMove = (ev: MouseEvent) => {
      const w = Math.min(560, Math.max(200, startW + ev.clientX - startX))
      setSidebarWidth(w)
    }
    const onUp = (ev: MouseEvent) => {
      const w = Math.min(560, Math.max(200, startW + ev.clientX - startX))
      localStorage.setItem('vs.sidebarWidth', String(w))
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }

  // Mobile: fixed slide-in drawer (off-canvas when closed). Desktop:
  // resident, resizable column. Width/resize-handle apply to desktop only.
  const rootClass = isMobile
    ? `fixed inset-y-0 left-0 z-50 w-[86%] max-w-xs bg-white border-r border-gray-200 flex flex-col transform transition-transform duration-200 ${navOpen ? 'translate-x-0' : '-translate-x-full'}`
    : 'relative bg-white border-r border-gray-200 flex flex-col flex-shrink-0'
  return (
    <div className={rootClass} style={isMobile ? undefined : { width: sidebarWidth }}>
      {!isMobile && (
        <div
          onMouseDown={startSidebarResize}
          className="absolute top-0 right-0 w-1.5 h-full cursor-col-resize z-10 hover:bg-indigo-200/60 active:bg-indigo-300/60"
          title="drag to resize"
        />
      )}
      {isMobile && (
        <button
          onClick={closeNav}
          className="absolute top-3 right-3 z-10 w-8 h-8 flex items-center justify-center rounded-lg text-gray-400 hover:bg-gray-100"
          aria-label={t('common.close', 'Close')}
        >
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" /></svg>
        </button>
      )}
      <div className="p-4 border-b border-gray-200">
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-baseline gap-1"><h1 className="text-lg font-bold leading-tight">Vibe Seller</h1>{serverVersion && <span className="text-[10px] text-gray-400" title={`Server: ${serverVersion}`}>v{serverVersion.slice(0, 14)}</span>}</div>
          <div className="flex items-center gap-1">
            <LanguageSwitcher />
            {currentUser.role === 'admin' && (
              <button data-testid="nav-settings" onClick={() => { sendEvent(FrontendEvent.VIEW_CHANGED, { view: 'settings' }); setAppView('settings') }} className="p-1 text-gray-400 hover:text-gray-700" title={t('settings.title')}>
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" /><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" /></svg>
              </button>
            )}
            {authRequired && (
              <button onClick={handleLogout} className="p-1 text-gray-400 hover:text-red-600" title={t('auth.signOut')}>
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" /></svg>
              </button>
            )}
          </div>
        </div>
        <div className="text-xs text-gray-500 mb-2 truncate">{currentUser.username} <span className="text-gray-400">({currentUser.role})</span></div>
        <div className="flex bg-gray-100 rounded-lg p-0.5">
          {(['tasks', 'workspace'] as const).map(v => (
            <button
              key={v}
              onClick={() => { sendEvent(FrontendEvent.VIEW_CHANGED, { view: v }); setAppView(v); closeNav() }}
              className={`flex-1 px-2 py-1 text-xs font-medium rounded-md transition-colors ${appView === v ? 'bg-white shadow text-gray-900' : 'text-gray-500 hover:text-gray-700'}`}
            >
              {t(`navigation.${v}`)}
            </button>
          ))}
        </div>
      </div>

      {appView === 'tasks' ? (
        <>
          <div className="p-3">
            <button
              onClick={() => setShowCreateStore(true)}
              className="w-full px-3 py-2 bg-indigo-600 text-white rounded-lg text-sm font-medium hover:bg-indigo-700"
            >
              + {t('settings.newStore')}
            </button>
          </div>
          {showCreateStore && (
            <StoreCreationForm
              newStoreName={newStoreName} setNewStoreName={setNewStoreName}
              newStoreBackend={newStoreBackend} setNewStoreBackend={setNewStoreBackend}
              showProxy={showProxy} setShowProxy={setShowProxy}
              newStoreProxyServer={newStoreProxyServer} setNewStoreProxyServer={setNewStoreProxyServer}
              newStoreProxyBypass={newStoreProxyBypass} setNewStoreProxyBypass={setNewStoreProxyBypass}
              ziniaoAccounts={ziniaoAccounts}
              selectedZiniaoAccountId={selectedZiniaoAccountId} setSelectedZiniaoAccountId={setSelectedZiniaoAccountId}
              ziniaoBrowsers={ziniaoBrowsers}
              selectedBrowserOauth={selectedBrowserOauth} setSelectedBrowserOauth={setSelectedBrowserOauth}
              fetchingBrowsers={fetchingBrowsers} browserFetchError={browserFetchError}
              setBrowserFetchError={setBrowserFetchError}
              fetchBrowserProfiles={fetchBrowserProfiles} restartZiniao={restartZiniao} ziniaoRetried={ziniaoRetried}
              serverPlatform={serverPlatform}
              showAddAccount={showAddAccount} setShowAddAccount={setShowAddAccount}
              showAccountPassword={showAccountPassword} setShowAccountPassword={setShowAccountPassword}
              editingAccountId={editingAccountId} setEditingAccountId={setEditingAccountId}
              newAccount={newAccount} setNewAccount={setNewAccount}
              createZiniaoAccount={createZiniaoAccount} updateZiniaoAccount={updateZiniaoAccount}
              deleteZiniaoAccount={deleteZiniaoAccount}
              createStore={createStore} setShowCreateStore={setShowCreateStore}
            />
          )}
          <div className="flex-1 overflow-y-auto">
            {stores.map(store => (
              <button
                key={store.id}
                onClick={() => { sendEvent(FrontendEvent.STORE_SWITCHED, { is_all_stores: false, backend: store.browser_backend }); selectStore(store) }}
                className={`w-full text-left px-4 py-3 hover:bg-gray-50 border-b border-gray-100 ${selectedStore?.id === store.id && !showAllTasks ? 'bg-indigo-50 border-l-4 border-l-indigo-600' : ''}`}
              >
                <div className="font-medium text-sm">{store.name}</div>
                <div className="text-xs text-gray-500">{store.browser_backend}</div>
              </button>
            ))}
            {stores.length === 0 && (
              <div className="p-4 text-sm text-gray-400 text-center">{t('navigation.stores')}</div>
            )}
            <button
              onClick={() => { sendEvent(FrontendEvent.STORE_SWITCHED, { is_all_stores: true }); selectAllTasks() }}
              className={`w-full text-left px-4 py-3 hover:bg-gray-50 border-b border-gray-100 ${showAllTasks ? 'bg-indigo-50 border-l-4 border-l-indigo-600' : ''}`}
            >
              <div className="font-medium text-sm">{t('tasks.allStores')}</div>
              <div className="text-xs text-gray-500">{t('tasks.noStore')}</div>
            </button>
          </div>
        </>
      ) : appView === 'workspace' ? (
        <WorkspaceSidebar
          wsStructured={wsStructured} wsSelectedFile={wsSelectedFile}
          wsExpandedStores={wsExpandedStores} wsExpandedSkills={wsExpandedSkills}
          toggleStoreExpanded={toggleStoreExpanded} toggleSkillExpanded={toggleSkillExpanded}
          openWsFile={openWsFile} deleteWsFile={deleteWsFile}
          wsNewFileName={wsNewFileName} setWsNewFileName={setWsNewFileName}
          wsNewFileSection={wsNewFileSection} setWsNewFileSection={setWsNewFileSection}
          createWsFile={createWsFile}
          syncProjectKnowledge={syncProjectKnowledge} wsSyncing={wsSyncing}
          wsSyncMeta={wsSyncMeta} loadWsStructured={loadWsStructured}
          syncBuiltinSkills={syncBuiltinSkills} wsSkillsSyncing={wsSkillsSyncing}
        />
      ) : (
        // Settings navigates via the top tabs in the main panel; the
        // sidebar body stays empty here (the old lone "user management"
        // link was a vestige of the pre-tab layout).
        <div className="flex-1 overflow-y-auto" />
      )}
    </div>
  )
}

// ─── Store creation sub-component (inline) ───────────
function StoreCreationForm(props: {
  newStoreName: string; setNewStoreName: (v: string) => void
  newStoreBackend: string; setNewStoreBackend: (v: string) => void
  showProxy: boolean; setShowProxy: (v: boolean) => void
  newStoreProxyServer: string; setNewStoreProxyServer: (v: string) => void
  newStoreProxyBypass: string; setNewStoreProxyBypass: (v: string) => void
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
  createStore: () => void; setShowCreateStore: (v: boolean) => void
}) {
  const { t } = useTranslation()
  const p = props
  return (
    <div className="px-3 pb-3">
      <input
        value={p.newStoreName}
        onChange={e => p.setNewStoreName(e.target.value)}
        onKeyDown={e => e.key === 'Enter' && p.newStoreBackend === 'chrome' && p.createStore()}
        placeholder={t('settings.storeName') + '...'}
        className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm mb-2"
        autoFocus
      />
      {!p.newStoreName.trim() && (
        <p className="text-xs text-amber-600 mb-1">{t('settings.enterStoreNameFirst')}</p>
      )}
      <select
        value={p.newStoreBackend}
        onChange={e => p.setNewStoreBackend(e.target.value)}
        disabled={!p.newStoreName.trim()}
        className={`w-full px-3 py-2 border border-gray-300 rounded-lg text-sm mb-2 bg-white ${!p.newStoreName.trim() ? 'opacity-50 cursor-not-allowed' : ''}`}
      >
        <option value="chrome">Chrome (Playwright)</option>
        <option value="ziniao">{t('tasks.ziniao')} ({t('tasks.browserBackend')})</option>
      </select>
      {p.newStoreBackend === 'chrome' && (
        <div className="mb-2">
          <label className="flex items-center gap-2 text-xs text-gray-600 cursor-pointer mb-1">
            <input
              type="checkbox"
              checked={p.showProxy}
              onChange={e => p.setShowProxy(e.target.checked)}
              className="rounded border-gray-300"
            />
            {t('settings.needProxy')}
          </label>
          {p.showProxy && (
            <div className="space-y-1">
              <input
                value={p.newStoreProxyServer}
                onChange={e => p.setNewStoreProxyServer(e.target.value)}
                placeholder={`${t('settings.proxyServer')} (http://127.0.0.1:7890)`}
                className="w-full px-2 py-1.5 border border-gray-300 rounded text-xs"
              />
              <input
                value={p.newStoreProxyBypass}
                onChange={e => p.setNewStoreProxyBypass(e.target.value)}
                placeholder={`${t('settings.proxyBypass')} (.amazon.com,.google.com)`}
                className="w-full px-2 py-1.5 border border-gray-300 rounded text-xs"
              />
              <p className="text-[10px] text-gray-400">{t('settings.proxyHint')}</p>
            </div>
          )}
        </div>
      )}
      {p.newStoreBackend === 'ziniao' && (
        <ZiniaoSection {...p} />
      )}
      <div className="flex gap-2">
        {(() => {
          const nameEmpty = !p.newStoreName.trim()
          const ziniaoIncomplete = p.newStoreBackend === 'ziniao' && (!p.selectedZiniaoAccountId || !p.selectedBrowserOauth || p.fetchingBrowsers || !!p.browserFetchError)
          const disabled = nameEmpty || ziniaoIncomplete
          return <button
            onClick={p.createStore}
            disabled={disabled}
            className={`px-3 py-1 rounded text-sm ${disabled ? 'bg-gray-300 text-gray-500 cursor-not-allowed' : 'bg-green-600 text-white hover:bg-green-700'}`}
          >{t('common.create')}</button>
        })()}
        <button onClick={() => p.setShowCreateStore(false)} className="px-3 py-1 bg-gray-300 rounded text-sm">{t('common.cancel')}</button>
      </div>
    </div>
  )
}

// ─── Ziniao account + browser picker ─────────────────
function ZiniaoSection(props: {
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

// ─── Reusable skill item renderer ─────────────────────
// ─── Workspace sidebar tree ──────────────────────────
function WorkspaceSidebar(props: {
  wsStructured: WsStructured | null; wsSelectedFile: string | null
  wsExpandedStores: Set<string>; wsExpandedSkills: Set<string>
  toggleStoreExpanded: (slug: string) => void; toggleSkillExpanded: (slug: string) => void
  openWsFile: (path: string) => void; deleteWsFile: (path: string) => void
  wsNewFileName: string; setWsNewFileName: (v: string) => void
  wsNewFileSection: string | null; setWsNewFileSection: (v: string | null) => void
  createWsFile: (section: string, fileName: string) => void
  syncProjectKnowledge: () => void; wsSyncing: boolean
  wsSyncMeta: Record<string, unknown> | null
  loadWsStructured: () => void
  syncBuiltinSkills: () => void; wsSkillsSyncing: boolean
}) {
  const { t } = useTranslation()
  const p = props
  const [skillsTab, setSkillsTab] = useState<'builtin' | 'myskills'>('myskills')

  const builtinSkills = p.wsStructured?.skills.filter(s => s.source === 'builtin') ?? []
  const customSkills = p.wsStructured?.skills.filter(s => s.source === 'custom') ?? []
  const importedSkills = p.wsStructured?.skills.filter(s => s.source === 'imported') ?? []

  const uninstallSkill = async (slug: string) => {
    if (!confirm(t('workspace.uninstallSkillConfirm', { name: slug }))) return
    await api.del(`/api/workspace/skills/${encodeURIComponent(slug)}`)
    await p.loadWsStructured()
  }

  const getOriginDomain = (url?: string): string => {
    if (!url) return t('workspace.importedBadge')
    try { return new URL(url).hostname } catch { return t('workspace.importedBadge') }
  }

  return (
    <div className="flex-1 overflow-y-auto">
      {/* Skills section */}
      <div className="border-b border-gray-100">
        <div className="px-3 pt-2 pb-1 flex items-center justify-between">
          <div className="flex items-center gap-0">
            <button
              onClick={() => setSkillsTab('builtin')}
              className={`px-2 py-0.5 text-xs font-semibold uppercase tracking-wider border-b-2 transition-colors ${skillsTab === 'builtin' ? 'text-indigo-600 border-indigo-600' : 'text-gray-400 border-transparent hover:text-gray-600'}`}
            >{t('workspace.builtinTab')}</button>
            <button
              onClick={() => setSkillsTab('myskills')}
              className={`px-2 py-0.5 text-xs font-semibold uppercase tracking-wider border-b-2 transition-colors ${skillsTab === 'myskills' ? 'text-indigo-600 border-indigo-600' : 'text-gray-400 border-transparent hover:text-gray-600'}`}
            >{t('workspace.mySkillsTab')}</button>
          </div>
          <div className="flex items-center gap-1">
            {skillsTab === 'builtin' && (
              <button
                onClick={p.syncBuiltinSkills}
                disabled={p.wsSkillsSyncing}
                className={`text-gray-400 hover:text-indigo-600 text-[10px] leading-none ${p.wsSkillsSyncing ? 'animate-spin' : ''}`}
                title={t('workspace.syncSkills')}
              >&#8635;</button>
            )}
            {skillsTab === 'myskills' && (
              <button
                onClick={() => p.setWsNewFileSection(p.wsNewFileSection === '_skill_create' ? null : '_skill_create')}
                className="text-gray-400 hover:text-indigo-600 text-sm leading-none"
                title={t('workspace.createSkill')}
              >+</button>
            )}
          </div>
        </div>
        <p className="px-3 pb-2 text-[10px] text-gray-400 leading-tight">{t('workspace.skillsHint')}</p>

        {/* Built-in tab content */}
        {skillsTab === 'builtin' && (
          <>
            {builtinSkills.map(skill => (
              <SkillItem
                key={skill.slug + skill.source}
                skill={skill}
                badge={<span className="text-[9px] bg-indigo-100 text-indigo-700 px-1.5 py-0.5 rounded-full flex-shrink-0">{t('workspace.builtinBadge')}</span>}
                expanded={p.wsExpandedSkills.has(skill.slug)}
                toggleExpanded={() => p.toggleSkillExpanded(skill.slug)}
                wsSelectedFile={p.wsSelectedFile}
                openWsFile={p.openWsFile}
                deleteWsFile={p.deleteWsFile}
              />
            ))}
            {builtinSkills.length === 0 && (
              <div className="px-4 py-2 text-xs text-gray-400 italic">{t('workspace.noBuiltinSkills')}</div>
            )}
          </>
        )}

        {/* My Skills tab content */}
        {skillsTab === 'myskills' && (
          <>
            {p.wsNewFileSection === '_skill_create' && (
              <div className="px-3 pb-2 flex gap-1">
                <input
                  value={p.wsNewFileName}
                  onChange={e => p.setWsNewFileName(e.target.value)}
                  onKeyDown={async e => {
                    if (e.key === 'Enter' && p.wsNewFileName.trim()) {
                      await api.post('/api/workspace/skill', { name: p.wsNewFileName.trim(), description: '' })
                      await p.loadWsStructured()
                      p.setWsNewFileName('')
                      p.setWsNewFileSection(null)
                    }
                    if (e.key === 'Escape') p.setWsNewFileSection(null)
                  }}
                  placeholder={t('workspace.skillNamePlaceholder')}
                  className="flex-1 px-2 py-1 border border-gray-300 rounded text-xs"
                  autoFocus
                />
              </div>
            )}

            {/* CUSTOM section */}
            <p className="px-3 pt-2 pb-1 text-[10px] font-semibold uppercase text-gray-400">{t('workspace.customSkillsSection')}</p>
            {customSkills.map(skill => (
              <SkillItem
                key={skill.slug + skill.source}
                skill={skill}
                badge={<span className="text-[9px] bg-green-100 text-green-700 px-1.5 py-0.5 rounded-full flex-shrink-0">{t('workspace.customBadge')}</span>}
                expanded={p.wsExpandedSkills.has(skill.slug)}
                toggleExpanded={() => p.toggleSkillExpanded(skill.slug)}
                wsSelectedFile={p.wsSelectedFile}
                openWsFile={p.openWsFile}
                deleteWsFile={p.deleteWsFile}
                onDelete={uninstallSkill}
              />
            ))}
            {customSkills.length === 0 && (
              <div className="px-4 py-2 text-xs text-gray-400 italic">{t('workspace.noCustomSkills')}</div>
            )}

            {/* IMPORTED section */}
            <p className="px-3 pt-2 pb-1 text-[10px] font-semibold uppercase text-gray-400">{t('workspace.importedSkillsSection')}</p>
            {importedSkills.map(skill => (
              <SkillItem
                key={skill.slug + skill.source}
                skill={skill}
                badge={<span className="text-[9px] bg-indigo-100 text-indigo-700 px-1.5 py-0.5 rounded-full flex-shrink-0">{getOriginDomain(skill.origin_url)}</span>}
                expanded={p.wsExpandedSkills.has(skill.slug)}
                toggleExpanded={() => p.toggleSkillExpanded(skill.slug)}
                wsSelectedFile={p.wsSelectedFile}
                openWsFile={p.openWsFile}
                deleteWsFile={p.deleteWsFile}
                onDelete={uninstallSkill}
              />
            ))}
            {importedSkills.length === 0 && (
              <div className="px-4 py-2 text-xs text-gray-400 italic">{t('workspace.noImportedSkills')}</div>
            )}
          </>
        )}
      </div>

      {/* Project Knowledge section */}
      <div className="border-b border-gray-100">
        <div className="px-3 pt-2 pb-1 flex items-center justify-between">
          <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider">{t('workspace.projectKnowledge')}</p>
          <button
            onClick={p.syncProjectKnowledge}
            disabled={p.wsSyncing}
            className={`text-gray-400 hover:text-indigo-600 text-[10px] leading-none ${p.wsSyncing ? 'animate-spin' : ''}`}
            title={p.wsSyncing ? t('workspace.syncing') : t('workspace.syncKnowledge')}
          >&#8635;</button>
        </div>
        <p className="px-3 pb-1 text-[10px] text-gray-400 leading-tight">{t('workspace.projectKnowledgeHint')}</p>
        {p.wsSyncMeta && (
          <div className="px-3 pb-2">
            {p.wsSyncMeta.status === 'failed' && (
              <p className="text-[10px] text-red-500 leading-tight">{t('workspace.syncFailed', { error: p.wsSyncMeta.error as string })}</p>
            )}
            <p className="text-[10px] text-gray-400 leading-tight">
              {p.wsSyncMeta.last_sync_at
                ? t('workspace.lastSynced', { time: new Date(p.wsSyncMeta.last_sync_at as string).toLocaleString() })
                : t('workspace.neverSynced')}
            </p>
          </div>
        )}
        {p.wsStructured?.project_knowledge.map(f => (
          <WsFileItem key={f.path} file={f} selected={p.wsSelectedFile === f.path} onSelect={p.openWsFile} onDelete={p.deleteWsFile} displayPrefix="knowledge/project/" />
        ))}
        {p.wsStructured && p.wsStructured.project_knowledge.length === 0 && (
          <div className="px-4 py-2 text-xs text-gray-400 italic">{t('workspace.noProjectKnowledge')}</div>
        )}
      </div>

      {/* Local Knowledge section */}
      <div className="border-b border-gray-100">
        <div className="px-3 pt-2 pb-1 flex items-center justify-between">
          <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider">{t('workspace.localKnowledge')}</p>
          <button
            onClick={() => p.setWsNewFileSection(p.wsNewFileSection === 'knowledge' ? null : 'knowledge')}
            className="text-gray-400 hover:text-indigo-600 text-sm leading-none"
            title={t('workspace.addKnowledgeFile')}
          >+</button>
        </div>
        <p className="px-3 pb-2 text-[10px] text-gray-400 leading-tight">{t('workspace.localKnowledgeHint')}</p>
        {p.wsNewFileSection === 'knowledge' && (
          <div className="px-3 pb-2 flex gap-1">
            <input
              value={p.wsNewFileName}
              onChange={e => p.setWsNewFileName(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') p.createWsFile('knowledge', p.wsNewFileName); if (e.key === 'Escape') p.setWsNewFileSection(null) }}
              placeholder="filename.md"
              className="flex-1 px-2 py-1 border border-gray-300 rounded text-xs"
              autoFocus
            />
          </div>
        )}
        {p.wsStructured?.local_knowledge.map(f => (
          <WsFileItem key={f.path} file={f} selected={p.wsSelectedFile === f.path} onSelect={p.openWsFile} onDelete={p.deleteWsFile} />
        ))}
        {p.wsStructured && p.wsStructured.local_knowledge.length === 0 && !p.wsNewFileSection && (
          <div className="px-4 py-2 text-xs text-gray-400 italic">{t('workspace.noLocalKnowledge')}</div>
        )}
      </div>

      <StoreFilesSection {...p} />
    </div>
  )
}
