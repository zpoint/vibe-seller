import { useRef, useState } from 'react'
import { api } from '../api'
import { fetchBrowserProfiles as fetchBrowserProfilesHandler } from '../handlers/fetchBrowserProfiles'
import type { ZiniaoAccount, ZiniaoBrowserProfile } from '../types'

const EMPTY_ACCOUNT = { name: '', company: '', username: '', password: '' }

/**
 * Ziniao account + browser-profile state for the sidebar's store and
 * account forms. Saved accounts stay installation-wide; reset only drops
 * this browser session's sensitive UI state and invalidates its responses.
 */
export function useZiniaoAccounts() {
  const [ziniaoAccounts, setZiniaoAccounts] = useState<ZiniaoAccount[]>([])
  const [selectedZiniaoAccountId, setSelectedZiniaoAccountId] = useState('')
  const [ziniaoBrowsers, setZiniaoBrowsers] = useState<ZiniaoBrowserProfile[]>([])
  const [selectedBrowserOauth, setSelectedBrowserOauth] = useState('')
  const [fetchingBrowsers, setFetchingBrowsers] = useState(false)
  const [browserFetchError, setBrowserFetchError] = useState('')
  const [ziniaoStartingSecs, setZiniaoStartingSecs] = useState(0)
  const [showAddAccount, setShowAddAccount] = useState(false)
  const [showAccountPassword, setShowAccountPassword] = useState(false)
  const [editingAccountId, setEditingAccountId] = useState('')
  const [newAccount, setNewAccount] = useState(EMPTY_ACCOUNT)
  const generation = useRef(0)

  const loadZiniaoAccounts = async () => {
    const request = generation.current
    try {
      const accounts = await api.get('/api/ziniao-accounts')
      if (request === generation.current) setZiniaoAccounts(accounts)
    } catch { /* ignore */ }
  }
  const createZiniaoAccount = async () => {
    if (!newAccount.company.trim() || !newAccount.username.trim() || !newAccount.password.trim()) return
    const request = generation.current
    await api.post('/api/ziniao-accounts', { ...newAccount, name: newAccount.name.trim() || newAccount.company.trim() })
    if (request !== generation.current) return
    setNewAccount(EMPTY_ACCOUNT); setShowAddAccount(false); await loadZiniaoAccounts()
  }
  const updateZiniaoAccount = async () => {
    if (!editingAccountId || !newAccount.company.trim() || !newAccount.username.trim()) return
    const request = generation.current
    const accountId = editingAccountId
    const body: Record<string, string> = { company: newAccount.company, username: newAccount.username, name: newAccount.name.trim() || newAccount.company.trim() }
    if (newAccount.password.trim()) body.password = newAccount.password
    await api.put(`/api/ziniao-accounts/${accountId}`, body)
    if (request !== generation.current) return
    setNewAccount(EMPTY_ACCOUNT); setEditingAccountId(''); setShowAddAccount(false); setBrowserFetchError('')
    await loadZiniaoAccounts()
    if (selectedZiniaoAccountId === accountId) fetchBrowserProfiles(accountId)
  }
  const deleteZiniaoAccount = async (accountId: string) => {
    const request = generation.current
    await api.del(`/api/ziniao-accounts/${accountId}`)
    if (request !== generation.current) return
    if (selectedZiniaoAccountId === accountId) { setSelectedZiniaoAccountId(''); setZiniaoBrowsers([]); setBrowserFetchError('') }
    await loadZiniaoAccounts()
  }
  const browserProfilesDeps = { api, setFetchingBrowsers, setZiniaoBrowsers, setSelectedBrowserOauth, setBrowserFetchError, setZiniaoStartingSecs }
  const fetchBrowserProfiles = (accountId: string) => {
    const request = generation.current
    return fetchBrowserProfilesHandler(accountId, {
      ...browserProfilesDeps,
      isCurrent: () => request === generation.current,
    })
  }
  const reset = () => {
    generation.current += 1
    setZiniaoAccounts([])
    setSelectedZiniaoAccountId('')
    setZiniaoBrowsers([])
    setSelectedBrowserOauth('')
    setFetchingBrowsers(false)
    setBrowserFetchError('')
    setZiniaoStartingSecs(0)
    setShowAddAccount(false)
    setShowAccountPassword(false)
    setEditingAccountId('')
    setNewAccount(EMPTY_ACCOUNT)
  }

  return {
    ziniaoAccounts, selectedZiniaoAccountId, setSelectedZiniaoAccountId,
    ziniaoBrowsers, setZiniaoBrowsers, selectedBrowserOauth, setSelectedBrowserOauth,
    fetchingBrowsers, browserFetchError, setBrowserFetchError, ziniaoStartingSecs,
    showAddAccount, setShowAddAccount, showAccountPassword, setShowAccountPassword,
    editingAccountId, setEditingAccountId, newAccount, setNewAccount,
    loadZiniaoAccounts, createZiniaoAccount, updateZiniaoAccount, deleteZiniaoAccount,
    fetchBrowserProfiles, reset,
  }
}
