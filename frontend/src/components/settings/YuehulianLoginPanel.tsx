import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../../api'

interface YuehulianLoginStatus {
  configured: boolean
  team_name: string
  subaccount: string
}

/**
 * 悦互联 team-login credentials (one login per machine, shared by all
 * yuehulian stores). Stored server-side with the password encrypted; the
 * backend auto-logs the client in with these so an expired session never
 * strands a task. The password is never returned by the API, so the field
 * starts blank and must be re-entered to save a change.
 */
export function YuehulianLoginPanel() {
  const { t } = useTranslation()
  const [status, setStatus] = useState<YuehulianLoginStatus | null>(null)
  const [team, setTeam] = useState('')
  const [sub, setSub] = useState('')
  const [pw, setPw] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')

  const refresh = async () => {
    try {
      const s = (await api.get(
        '/api/yuehulian/credentials'
      )) as YuehulianLoginStatus
      setStatus(s)
      setTeam(s.team_name || '')
      setSub(s.subaccount || '')
    } catch {
      /* not configured yet / unsupported — leave the form empty */
    }
  }

  useEffect(() => {
    void refresh()
  }, [])

  const save = async () => {
    if (!team.trim() || !sub.trim() || !pw) {
      setErr(t('settings.yuehulianLoginAllRequired'))
      return
    }
    setBusy(true)
    setErr('')
    setMsg('')
    try {
      await api.post('/api/yuehulian/credentials', {
        team_name: team,
        subaccount: sub,
        password: pw,
      })
      setPw('')
      setMsg(t('settings.yuehulianLoginSaved'))
      await refresh()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="border border-gray-200 rounded-lg p-4 space-y-2">
      <h3 className="text-sm font-semibold text-gray-800">
        {t('settings.yuehulianLoginTitle')}
      </h3>
      <p className="text-xs text-gray-500">
        {t('settings.yuehulianLoginHint')}
      </p>
      {status?.configured && (
        <p className="text-xs text-green-600">
          {t('settings.yuehulianLoginConfigured')}
        </p>
      )}
      <input
        value={team}
        onChange={e => setTeam(e.target.value)}
        placeholder={t('settings.yuehulianTeamName')}
        className="w-full px-2 py-1.5 border border-gray-300 rounded text-xs"
      />
      <input
        value={sub}
        onChange={e => setSub(e.target.value)}
        placeholder={t('settings.yuehulianSubaccount')}
        className="w-full px-2 py-1.5 border border-gray-300 rounded text-xs"
      />
      <input
        type="password"
        value={pw}
        onChange={e => setPw(e.target.value)}
        placeholder={
          status?.configured
            ? t('settings.yuehulianPasswordUnchanged')
            : t('settings.yuehulianPassword')
        }
        className="w-full px-2 py-1.5 border border-gray-300 rounded text-xs"
      />
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={save}
          disabled={busy}
          className="px-3 py-1.5 text-xs rounded bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-50"
        >
          {busy ? t('settings.yuehulianLoginSaving') : t('common.save')}
        </button>
        {msg && <span className="text-xs text-green-600">{msg}</span>}
        {err && <span className="text-xs text-red-600">{err}</span>}
      </div>
    </div>
  )
}
