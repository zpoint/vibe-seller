import { useState } from 'react'
import { api } from '../api'
import {
  fetchBrowserProfiles as fetchBrowserProfilesHandler,
  restartZiniao as restartZiniaoHandler,
} from '../handlers/fetchBrowserProfiles'
import type { ZiniaoAccount, ZiniaoBrowserProfile } from '../types'

const EMPTY_ACCOUNT = { name: '', company: '', username: '', password: '' }

/**
 * Ziniao account + browser-profile state for the sidebar's store and
 * account forms. Moved out of App.tsx unchanged (line limit).
 */
export function useZiniaoAccounts() {
  const [ziniaoAccounts, setZiniaoAccounts] = useState<ZiniaoAccount[]>([])
  const [selectedZiniaoAccountId, setSelectedZiniaoAccountId] = useState('')
  const [ziniaoBrowsers, setZiniaoBrowsers] = useState<ZiniaoBrowserProfile[]>([])
  const [selectedBrowserOauth, setSelectedBrowserOauth] = useState('')
  const [fetchingBrowsers, setFetchingBrowsers] = useState(false)
  const [browserFetchError, setBrowserFetchError] = useState('')
  const [ziniaoRetried, setZiniaoRetried] = useState(false)
  const [showAddAccount, setShowAddAccount] = useState(false)
  const [showAccountPassword, setShowAccountPassword] = useState(false)
  const [editingAccountId, setEditingAccountId] = useState('')
  const [newAccount, setNewAccount] = useState(EMPTY_ACCOUNT)

  const loadZiniaoAccounts = async () => { try { setZiniaoAccounts(await api.get('/api/ziniao-accounts')) } catch { /* ignore */ } }
  const createZiniaoAccount = async () => {
    if (!newAccount.company.trim() || !newAccount.username.trim() || !newAccount.password.trim()) return
    await api.post('/api/ziniao-accounts', { ...newAccount, name: newAccount.name.trim() || newAccount.company.trim() })
    setNewAccount(EMPTY_ACCOUNT); setShowAddAccount(false); await loadZiniaoAccounts()
  }
  const updateZiniaoAccount = async () => {
    if (!editingAccountId || !newAccount.company.trim() || !newAccount.username.trim()) return
    const body: Record<string, string> = { company: newAccount.company, username: newAccount.username, name: newAccount.name.trim() || newAccount.company.trim() }
    if (newAccount.password.trim()) body.password = newAccount.password
    await api.put(`/api/ziniao-accounts/${editingAccountId}`, body)
    setNewAccount(EMPTY_ACCOUNT); setEditingAccountId(''); setShowAddAccount(false); setBrowserFetchError('')
    await loadZiniaoAccounts()
    if (selectedZiniaoAccountId === editingAccountId) fetchBrowserProfiles(editingAccountId)
  }
  const deleteZiniaoAccount = async (accountId: string) => {
    await api.del(`/api/ziniao-accounts/${accountId}`)
    if (selectedZiniaoAccountId === accountId) { setSelectedZiniaoAccountId(''); setZiniaoBrowsers([]); setBrowserFetchError('') }
    await loadZiniaoAccounts()
  }
  const browserProfilesDeps = { api, browserFetchError, setFetchingBrowsers, setZiniaoBrowsers, setSelectedBrowserOauth, setBrowserFetchError, setZiniaoRetried }
  const fetchBrowserProfiles = (accountId: string) => fetchBrowserProfilesHandler(accountId, browserProfilesDeps)
  const restartZiniao = (accountId: string) => restartZiniaoHandler(accountId, browserProfilesDeps)

  return {
    ziniaoAccounts, selectedZiniaoAccountId, setSelectedZiniaoAccountId,
    ziniaoBrowsers, setZiniaoBrowsers, selectedBrowserOauth, setSelectedBrowserOauth,
    fetchingBrowsers, browserFetchError, setBrowserFetchError, ziniaoRetried,
    showAddAccount, setShowAddAccount, showAccountPassword, setShowAccountPassword,
    editingAccountId, setEditingAccountId, newAccount, setNewAccount,
    loadZiniaoAccounts, createZiniaoAccount, updateZiniaoAccount, deleteZiniaoAccount,
    fetchBrowserProfiles, restartZiniao,
  }
}
