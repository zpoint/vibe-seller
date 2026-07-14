import React, { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../../api'
import type { AuthUser } from '../../types'

interface AccountPanelProps {
  currentUser: AuthUser
  setCurrentUser: React.Dispatch<React.SetStateAction<AuthUser | null>>
  authRequired: boolean
  setAuthRequired: (v: boolean) => void
  allUsers: AuthUser[]
  showAddUser: boolean
  setShowAddUser: (v: boolean) => void
  newUserForm: { username: string; email: string; password: string; role: string }
  setNewUserForm: React.Dispatch<React.SetStateAction<{ username: string; email: string; password: string; role: string }>>
  createUser: () => void
  deleteUser: (userId: string, username: string) => void
  loadUsers: () => void
}

export function AccountPanel({
  currentUser,
  setCurrentUser,
  authRequired,
  setAuthRequired,
  allUsers,
  showAddUser,
  setShowAddUser,
  newUserForm,
  setNewUserForm,
  createUser,
  deleteUser,
  loadUsers,
}: AccountPanelProps) {
  const { t } = useTranslation()
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [pwMsg, setPwMsg] = useState('')
  const [profileUsername, setProfileUsername] = useState(currentUser.username)
  const [profileEmail, setProfileEmail] = useState(currentUser.email || '')
  const [editingUserId, setEditingUserId] = useState<string | null>(null)
  const [editForm, setEditForm] = useState({ username: '', email: '', password: '', role: '' })
  const [profileMsg, setProfileMsg] = useState('')
  const [profileMsgIsError, setProfileMsgIsError] = useState(false)

  const startEditUser = (u: AuthUser) => {
    setEditingUserId(u.id)
    setEditForm({ username: u.username, email: u.email || '', password: '', role: u.role })
  }

  const handleSaveUser = async () => {
    if (!editingUserId) return
    const payload: Record<string, unknown> = {}
    const orig = allUsers.find(u => u.id === editingUserId)
    if (!orig) return
    if (editForm.username !== orig.username) payload.username = editForm.username
    if (editForm.role !== orig.role) payload.role = editForm.role
    const emailChanged = editForm.email !== (orig.email || '')
    if (emailChanged) payload.email = editForm.email.trim() || null
    if (editForm.password.trim()) payload.password = editForm.password
    if (Object.keys(payload).length === 0) { setEditingUserId(null); return }
    try {
      await api.put(`/api/users/${editingUserId}`, payload)
      setEditingUserId(null)
      await loadUsers()
    } catch { /* ignore */ }
  }

  const handleToggleAuth = async (enabled: boolean) => {
    try {
      await api.put('/api/settings', { auth_required: enabled ? 'true' : 'false' })
      setAuthRequired(enabled)
    } catch { /* ignore */ }
  }

  const handleChangePassword = async () => {
    setPwMsg('')
    if (newPassword !== confirmPassword) {
      setPwMsg(t('settings.passwordMismatch'))
      return
    }
    if (!newPassword.trim()) return
    try {
      await api.patch('/api/auth/me/password', {
        current_password: authRequired ? currentPassword : undefined,
        new_password: newPassword,
      })
      setPwMsg(t('settings.passwordChanged'))
      setCurrentPassword('')
      setNewPassword('')
      setConfirmPassword('')
    } catch (e) {
      setPwMsg(e instanceof Error ? e.message : 'Error')
    }
  }

  const handleUpdateProfile = async () => {
    setProfileMsg(''); setProfileMsgIsError(false)
    // Client-side email format check
    if (profileEmail.trim() && !profileEmail.includes('@')) {
      setProfileMsg(t('auth.invalidEmail')); setProfileMsgIsError(true)
      return
    }
    try {
      const emailChanged = profileEmail !== (currentUser.email || '')
      await api.patch('/api/auth/me/profile', {
        username: profileUsername !== currentUser.username ? profileUsername : undefined,
        email: emailChanged ? (profileEmail.trim() || null) : undefined,
      })
      // Refresh currentUser so sidebar/header reflect changes
      const updated = await api.get('/api/auth/me')
      setCurrentUser(updated)
      setProfileMsg(t('settings.profileUpdated'))
    } catch (e) {
      setProfileMsg(e instanceof Error ? e.message : 'Error'); setProfileMsgIsError(true)
    }
  }

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (currentUser.role === 'admin') loadUsers()
  }, [currentUser.role])

  return (
    <div className="space-y-4">
      {/* Auth Toggle (admin only) */}
      {currentUser.role === 'admin' && (
        <div className="bg-white rounded-lg border border-gray-200 p-4">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="font-semibold text-sm">{t('settings.requireLogin')}</h3>
              <p className="text-xs text-gray-500">{t('settings.requireLoginDesc')}</p>
            </div>
            <button
              onClick={() => handleToggleAuth(!authRequired)}
              className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${authRequired ? 'bg-indigo-600' : 'bg-gray-300'}`}
            >
              <span className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${authRequired ? 'translate-x-6' : 'translate-x-1'}`} />
            </button>
          </div>
        </div>
      )}

      {/* Profile Update */}
      <div className="bg-white rounded-lg border border-gray-200 p-4">
        <h3 className="font-semibold text-sm mb-3">{t('settings.myProfile')}</h3>
        <div className="space-y-2">
          <div>
            <label className="text-xs text-gray-500">{t('auth.username')}</label>
            <input value={profileUsername} onChange={e => setProfileUsername(e.target.value)} className="w-full px-3 py-2 border border-gray-300 rounded text-sm" placeholder={t('auth.usernamePlaceholder')} />
            <p className="text-xs text-gray-400 mt-0.5">{t('auth.usernameRules')}</p>
          </div>
          <div>
            <label className="text-xs text-gray-500">{t('auth.email')} ({t('common.optional')})</label>
            <input value={profileEmail} onChange={e => setProfileEmail(e.target.value)} type="email" className="w-full px-3 py-2 border border-gray-300 rounded text-sm" placeholder={t('auth.emailPlaceholder')} />
          </div>
          {profileMsg && <p className={`text-xs ${profileMsgIsError ? 'text-red-600' : 'text-green-600'}`}>{profileMsg}</p>}
          <button onClick={handleUpdateProfile} className="px-3 py-1.5 bg-indigo-600 text-white rounded text-sm hover:bg-indigo-700">{t('common.save')}</button>
        </div>
      </div>

      {/* Password Change */}
      <div className="bg-white rounded-lg border border-gray-200 p-4">
        <h3 className="font-semibold text-sm mb-3">{t('settings.changePassword')}</h3>
        <div className="space-y-2">
          {authRequired && (
            <input value={currentPassword} onChange={e => setCurrentPassword(e.target.value)} placeholder={t('settings.currentPassword')} type="password" className="w-full px-3 py-2 border border-gray-300 rounded text-sm" />
          )}
          <input value={newPassword} onChange={e => setNewPassword(e.target.value)} placeholder={t('settings.newPassword')} type="password" className="w-full px-3 py-2 border border-gray-300 rounded text-sm" />
          <input value={confirmPassword} onChange={e => setConfirmPassword(e.target.value)} placeholder={t('settings.confirmPassword')} type="password" className="w-full px-3 py-2 border border-gray-300 rounded text-sm" />
          {pwMsg && <p className={`text-xs ${pwMsg === t('settings.passwordChanged') ? 'text-green-600' : 'text-red-600'}`}>{pwMsg}</p>}
          <button onClick={handleChangePassword} className="px-3 py-1.5 bg-indigo-600 text-white rounded text-sm hover:bg-indigo-700">{t('settings.changePassword')}</button>
        </div>
      </div>

      {/* User Management (admin only) */}
      {currentUser.role === 'admin' && (
        <div className="bg-white rounded-lg border border-gray-200 p-4">
          <div className="flex items-center justify-between mb-4">
            <h3 className="font-semibold text-sm">{t('settings.users')}</h3>
            <button onClick={() => setShowAddUser(true)} className="px-3 py-1.5 bg-indigo-600 text-white rounded-lg text-sm hover:bg-indigo-700">+ {t('settings.newUser')}</button>
          </div>
          {showAddUser && (
            <div className="mb-4 p-3 bg-gray-50 rounded-lg border border-gray-200 space-y-2">
              <input value={newUserForm.username} onChange={e => setNewUserForm(p => ({ ...p, username: e.target.value }))} placeholder={t('auth.username')} className="w-full px-3 py-2 border border-gray-300 rounded text-sm" />
              <input value={newUserForm.email} onChange={e => setNewUserForm(p => ({ ...p, email: e.target.value }))} placeholder={`${t('auth.email')} (${t('common.optional')})`} type="email" className="w-full px-3 py-2 border border-gray-300 rounded text-sm" />
              <input value={newUserForm.password} onChange={e => setNewUserForm(p => ({ ...p, password: e.target.value }))} placeholder={t('auth.password')} type="password" className="w-full px-3 py-2 border border-gray-300 rounded text-sm" />
              <select value={newUserForm.role} onChange={e => setNewUserForm(p => ({ ...p, role: e.target.value }))} className="w-full px-3 py-2 border border-gray-300 rounded text-sm bg-white">
                <option value="member">{t('settings.user')}</option>
                <option value="admin">{t('settings.admin')}</option>
              </select>
              <div className="flex gap-2">
                <button onClick={createUser} className="px-3 py-1.5 bg-green-600 text-white rounded text-sm">{t('common.create')}</button>
                <button onClick={() => setShowAddUser(false)} className="px-3 py-1.5 bg-gray-200 rounded text-sm">{t('common.cancel')}</button>
              </div>
            </div>
          )}
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-gray-500 border-b">
                <th className="pb-2">{t('auth.username')}</th>
                <th className="pb-2">{t('settings.role')}</th>
                <th className="pb-2"></th>
              </tr>
            </thead>
            <tbody>
              {allUsers.map(u => (
                <React.Fragment key={u.id}>
                  <tr className="border-b border-gray-100">
                    <td className="py-2">{u.username}</td>
                    <td className="py-2"><span className={`px-2 py-0.5 rounded-full text-xs ${u.role === 'admin' ? 'bg-indigo-100 text-indigo-700' : u.role === 'ai_bot' ? 'bg-indigo-100 text-indigo-700' : 'bg-gray-100 text-gray-700'}`}>{u.role}</span></td>
                    <td className="py-2 text-right space-x-2">
                      {u.role !== 'ai_bot' && u.id !== currentUser.id && (<>
                        <button onClick={() => editingUserId === u.id ? setEditingUserId(null) : startEditUser(u)} className="text-xs text-indigo-600 hover:text-indigo-800">
                          {editingUserId === u.id ? t('common.cancel') : t('common.edit')}
                        </button>
                        <button onClick={() => deleteUser(u.id, u.username)} className="text-xs text-gray-500 hover:text-red-600">
                          {t('common.delete')}
                        </button>
                      </>)}
                    </td>
                  </tr>
                  {editingUserId === u.id && (
                    <tr className="border-b border-gray-100 bg-gray-50">
                      <td colSpan={3} className="py-2 px-2">
                        <div className="space-y-2">
                          <div className="grid grid-cols-2 gap-2">
                            <input value={editForm.username} onChange={e => setEditForm(p => ({ ...p, username: e.target.value }))} placeholder={t('auth.username')} className="px-2 py-1 border border-gray-300 rounded text-sm" />
                            <input value={editForm.email} onChange={e => setEditForm(p => ({ ...p, email: e.target.value }))} placeholder={`${t('auth.email')} (${t('common.optional')})`} type="email" className="px-2 py-1 border border-gray-300 rounded text-sm" />
                          </div>
                          <div className="grid grid-cols-2 gap-2">
                            <input value={editForm.password} onChange={e => setEditForm(p => ({ ...p, password: e.target.value }))} placeholder={`${t('auth.password')} (${t('settings.leaveBlank')})`} type="password" className="px-2 py-1 border border-gray-300 rounded text-sm" />
                            <select value={editForm.role} onChange={e => setEditForm(p => ({ ...p, role: e.target.value }))} className="px-2 py-1 border border-gray-300 rounded text-sm bg-white">
                              <option value="member">{t('settings.user')}</option>
                              <option value="admin">{t('settings.admin')}</option>
                            </select>
                          </div>
                          <button onClick={handleSaveUser} className="px-3 py-1 bg-indigo-600 text-white rounded text-sm hover:bg-indigo-700">{t('common.save')}</button>
                        </div>
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
