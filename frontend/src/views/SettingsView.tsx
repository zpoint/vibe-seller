import React, { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../api'
import { AccountPanel } from '../components/settings/AccountPanel'
import { GeneralPanel } from '../components/settings/GeneralPanel'
import { StoresPanel } from '../components/settings/StoresPanel'
import { EmailAccountsPanel } from '../components/settings/EmailAccountsPanel'
import { IntegrationsPanel } from '../components/settings/IntegrationsPanel'
import { ExternalConfigOverrideModal } from '../components/ExternalConfigOverrideModal'
import {
  isExternalConfigOverrideDetail,
  type ExternalConfigOverrideDetail,
} from '../components/externalConfigOverrideDetail'
import type { AuthUser, Profile, EmailAccount, Store } from '../types'

export type SettingsTab = 'stores' | 'general' | 'aiAgent' | 'email' | 'account' | 'integrations'

interface SettingsViewProps {
  currentUser: AuthUser
  settingsTab: SettingsTab
  setSettingsTab: (tab: SettingsTab) => void
  allUsers: AuthUser[]
  showAddUser: boolean
  setShowAddUser: (v: boolean) => void
  newUserForm: { username: string; email: string; password: string; role: string }
  setNewUserForm: React.Dispatch<React.SetStateAction<{ username: string; email: string; password: string; role: string }>>
  createUser: () => void
  deleteUser: (userId: string, username: string) => void
  profiles: Profile[]
  setEditingProfile: (p: Profile | undefined) => void
  setShowProfileModal: (v: boolean) => void
  setCurrentUser: React.Dispatch<React.SetStateAction<AuthUser | null>>
  setSelectedProfileId: (id: string) => void
  deleteProfile: (id: string) => void
  emailAccounts: EmailAccount[]
  loadEmailAccounts: () => void
  stores: Store[]
  loadStores: () => void
  authRequired: boolean
  setAuthRequired: (v: boolean) => void
  loadUsers: () => void
}

export function SettingsView({
  currentUser,
  settingsTab,
  setSettingsTab,
  allUsers,
  showAddUser,
  setShowAddUser,
  newUserForm,
  setNewUserForm,
  createUser,
  deleteUser,
  profiles,
  setEditingProfile,
  setShowProfileModal,
  setCurrentUser,
  setSelectedProfileId,
  deleteProfile,
  emailAccounts,
  loadEmailAccounts,
  stores,
  loadStores,
  authRequired,
  setAuthRequired,
  loadUsers,
}: SettingsViewProps) {
  const { t } = useTranslation()
  const tabs: SettingsTab[] = ['stores', 'general', 'aiAgent', 'email', 'account', 'integrations']
  const [overrideError, setOverrideError] = useState<ExternalConfigOverrideDetail | null>(null)

  // Centralized handler — surfaces the cc-switch / external-config
  // override conflict from any profile-management API call.
  // ``api.patch`` / ``api.post`` throw on non-2xx with the parsed
  // ``detail`` attached as ``err.detail``.
  const handleProfileApiError = (err: unknown): boolean => {
    const detail = (err as { detail?: unknown })?.detail
    if (isExternalConfigOverrideDetail(detail)) {
      setOverrideError(detail)
      return true
    }
    return false
  }

  const useDefaultAndDismiss = async () => {
    try {
      await api.patch('/api/profiles/default/set-default')
      setCurrentUser(prev => prev ? { ...prev, default_profile_id: 'default' } : prev)
      setSelectedProfileId('default')
    } catch { /* ignore — modal stays open */ }
    setOverrideError(null)
  }

  return (
    <div className="flex-1 flex flex-col bg-gray-50 p-4 sm:p-6 overflow-y-auto">
      <h2 className="text-lg font-bold mb-4">{t('settings.title')}</h2>
      <div className="flex gap-2 mb-4 border-b border-gray-200 overflow-x-auto">
        {tabs.map(tab => (
          <button
            key={tab}
            onClick={() => setSettingsTab(tab)}
            className={`px-3 py-2 text-sm font-medium border-b-2 -mb-px transition-colors whitespace-nowrap flex-shrink-0 ${
              settingsTab === tab
                ? 'border-indigo-600 text-indigo-600'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            {t(`settings.tab_${tab}`)}
          </button>
        ))}
      </div>

      {settingsTab === 'stores' ? (
        <StoresPanel stores={stores} loadStores={loadStores} emailAccounts={emailAccounts} loadEmailAccounts={loadEmailAccounts} />
      ) : settingsTab === 'general' ? (
        <GeneralPanel currentUser={currentUser} setCurrentUser={setCurrentUser} />
      ) : settingsTab === 'account' ? (
        <AccountPanel
          currentUser={currentUser}
          setCurrentUser={setCurrentUser}
          authRequired={authRequired}
          setAuthRequired={setAuthRequired}
          allUsers={allUsers}
          showAddUser={showAddUser}
          setShowAddUser={setShowAddUser}
          newUserForm={newUserForm}
          setNewUserForm={setNewUserForm}
          createUser={createUser}
          deleteUser={deleteUser}
          loadUsers={loadUsers}
        />
      ) : settingsTab === 'email' ? (
        <EmailAccountsPanel emailAccounts={emailAccounts} loadEmailAccounts={loadEmailAccounts} stores={stores} />
      ) : settingsTab === 'aiAgent' ? (
        <div className="space-y-4">
          <div className="bg-white rounded-lg border border-gray-200 p-4">
            <h3 className="font-semibold text-sm mb-3">{t('settings.aiAgentConfig')}</h3>
            <div className="space-y-3">
              <div className="flex items-center justify-between p-3 bg-gray-50 rounded-lg">
                <div>
                  <p className="text-sm font-medium">{t('settings.aiBackend')}</p>
                  <p className="text-xs text-gray-500">Claude Code CLI</p>
                </div>
                <span className="px-2 py-1 text-xs bg-green-100 text-green-700 rounded-full">{t('settings.connected')}</span>
              </div>
              <div className="p-3 bg-gray-50 rounded-lg">
                <p className="text-sm font-medium mb-1">{t('settings.aiWorkspace')}</p>
                <p className="text-xs text-gray-500 font-mono">~/.vibe-seller/</p>
              </div>
            </div>
          </div>

          <div className="bg-white rounded-lg border border-gray-200 p-4">
            <div className="flex items-center justify-between mb-3">
              <h3 className="font-semibold text-sm">{t('settings.aiProfiles')}</h3>
              <button
                onClick={() => { setEditingProfile(undefined); setShowProfileModal(true) }}
                className="px-3 py-1 bg-indigo-600 text-white text-sm rounded hover:bg-indigo-700"
              >
                {t('profiles.create')}
              </button>
            </div>
            <p className="text-xs text-gray-500 mb-3">{t('profiles.description')}</p>

            {/* Default Profile */}
            <div className={`border rounded-lg p-3 mb-2 ${currentUser?.default_profile_id === 'default' ? 'border-indigo-300 bg-indigo-50/30' : 'border-gray-200'}`}>
              <div className="flex items-center justify-between">
                <div>
                  <p className="font-medium text-sm">{t('profiles.default')}{currentUser?.default_profile_id === 'default' && <span className="ml-1.5 text-xs text-indigo-600">★ {t('profiles.isDefault')}</span>}</p>
                  <p className="text-xs text-gray-500">{t('profiles.defaultDescription')}</p>
                </div>
                <div className="flex gap-2 items-center">
                  {currentUser?.default_profile_id !== 'default' && (
                    <button
                      onClick={async () => {
                        try {
                          await api.patch('/api/profiles/default/set-default')
                          setCurrentUser(prev => prev ? { ...prev, default_profile_id: 'default' } : prev)
                          setSelectedProfileId('default')
                        } catch (err) { if (!handleProfileApiError(err)) { /* swallow other errors */ } }
                      }}
                      className="px-2 py-1 text-xs border border-indigo-300 text-indigo-600 rounded hover:bg-indigo-50"
                    >
                      {t('profiles.setDefault')}
                    </button>
                  )}
                  <span className="text-xs text-gray-400">{t('profiles.builtIn')}</span>
                </div>
              </div>
            </div>

            {/* Custom Profiles */}
            {profiles.filter(p => p.id !== 'default').map(profile => (
              <div key={profile.id} className={`border rounded-lg p-3 mb-2 ${currentUser?.default_profile_id === profile.id ? 'border-indigo-300 bg-indigo-50/30' : 'border-gray-200'}`}>
                <div className="flex items-center justify-between">
                  <div>
                    <p className="font-medium text-sm">{profile.name}{currentUser?.default_profile_id === profile.id && <span className="ml-1.5 text-xs text-indigo-600">★ {t('profiles.isDefault')}</span>}</p>
                    <p className="text-xs text-gray-500">{profile.description || t('profiles.noDescription')}</p>
                    {profile.env && Object.keys(profile.env).length > 0 && (
                      <p className="text-xs text-gray-400 mt-1">
                        {Object.keys(profile.env).length} {t('profiles.envVars')}
                      </p>
                    )}
                  </div>
                  <div className="flex gap-2">
                    {currentUser?.default_profile_id !== profile.id && (
                      <button
                        onClick={async () => {
                          try {
                            await api.patch(`/api/profiles/${profile.id}/set-default`)
                            setCurrentUser(prev => prev ? { ...prev, default_profile_id: profile.id } : prev)
                            setSelectedProfileId(profile.id)
                          } catch (err) { if (!handleProfileApiError(err)) { /* swallow other errors */ } }
                        }}
                        className="px-2 py-1 text-xs border border-indigo-300 text-indigo-600 rounded hover:bg-indigo-50"
                      >
                        {t('profiles.setDefault')}
                      </button>
                    )}
                    <button
                      onClick={() => { setEditingProfile(profile); setShowProfileModal(true) }}
                      className="px-2 py-1 text-xs border border-gray-300 rounded hover:bg-gray-50"
                    >
                      {t('common.edit')}
                    </button>
                    <button
                      onClick={() => {
                        if (confirm(t('profiles.confirmDelete', { name: profile.name }))) {
                          deleteProfile(profile.id)
                        }
                      }}
                      className="px-2 py-1 text-xs border border-red-300 text-red-600 rounded hover:bg-red-50"
                    >
                      {t('common.delete')}
                    </button>
                  </div>
                </div>
              </div>
            ))}

            {profiles.length === 0 && (
              <p className="text-sm text-gray-500 text-center py-4">{t('profiles.noProfiles')}</p>
            )}
          </div>
        </div>
      ) : settingsTab === 'integrations' ? (
        <IntegrationsPanel />
      ) : null}

      {overrideError && (
        <ExternalConfigOverrideModal
          detail={overrideError}
          onUseDefault={useDefaultAndDismiss}
          onClose={() => setOverrideError(null)}
        />
      )}
    </div>
  )
}
