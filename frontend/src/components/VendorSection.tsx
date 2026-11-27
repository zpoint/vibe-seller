/**
 * The generic browser-vendor section of the create-store form, rendered
 * from the vendor's metadata (GET /api/browser-vendors): the machine-login
 * row (for one-account-per-machine vendors, with only a [+]), the
 * credential form (fields from the spec, secrets masked with an eye
 * toggle), and the profile picker (stores from the generic route, with
 * the structured statuses decoded into per-vendor i18n strings).
 *
 * Every label and status string is derived from the spec's
 * ``i18n_prefix`` — `<prefix><Suffix>` (e.g. `ClientNotRunning`) — so a
 * vendor's strings live under its own prefix and no branch in this file
 * names a vendor.
 */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../api'
import { DocsHelpLink } from './ui/DocsHelpLink'
import type { BrowserVendor, Store, VendorStoreRef } from '../types'

/** The machine-login status: configured + the non-secret field values. */
interface VendorLoginStatus {
  configured: boolean
  [field: string]: unknown
}

/** camelCase('client_not_running') → 'ClientNotRunning'. */
const camel = (s: string) =>
  s.split('_').map((p) => p.charAt(0).toUpperCase() + p.slice(1)).join('')

const EYE_ON = (
  <svg xmlns="http://www.w3.org/2000/svg" className="w-3.5 h-3.5" viewBox="0 0 20 20" fill="currentColor"><path fillRule="evenodd" d="M3.707 2.293a1 1 0 00-1.414 1.414l14 14a1 1 0 001.414-1.414l-1.473-1.473A10.014 10.014 0 0020 10c-1.796-4.667-6-8-10-8a9.864 9.864 0 00-4.512 1.074L3.707 2.293zM10 15a9.864 9.864 0 004.512-1.074L13.06 12.474A3 3 0 017.526 6.94L5.534 4.948A7.966 7.966 0 002 10c1.194 3.1 3.672 5.457 6.676 6.725L10 15zm2.121-4.879A1 1 0 0010 9l-.707-.707A1 1 0 0010 11l2.121-2.121-.707-.707.707.707z" clipRule="evenodd" /></svg>
)
const EYE_OFF = (
  <svg xmlns="http://www.w3.org/2000/svg" className="w-3.5 h-3.5" viewBox="0 0 20 20" fill="currentColor"><path d="M10 12a2 2 0 100-4 2 2 0 000 4z" /><path fillRule="evenodd" d="M.458 10C1.732 5.943 5.522 3 10 3s8.268 2.943 9.542 7c-1.274 4.057-5.064 7-9.542 7S1.732 14.057.458 10zM14 10a4 4 0 11-8 0 4 4 0 018 0z" clipRule="evenodd" /></svg>
)

export function VendorSection(props: {
  vendor: BrowserVendor
  selectedRef: string; setSelectedRef: (v: string) => void
  stores: VendorStoreRef[]; error: string
  fetching: boolean; fetchStores: () => void
  allStores: Store[]
}) {
  const { t } = useTranslation()
  const v = props.vendor
  const p = props
  const [status, setStatus] = useState<VendorLoginStatus | null>(null)
  const [showForm, setShowForm] = useState(false)
  const [values, setValues] = useState<Record<string, string>>({})
  const [showSecret, setShowSecret] = useState(false)
  const [busy, setBusy] = useState(false)
  const [formErr, setFormErr] = useState('')

  const i18nKey = (suffix: string) => `${v.i18n_prefix}${suffix}`

  const refresh = async (thenFetch = false) => {
    try {
      const s = (await api.get(
        `/api/browser-vendors/${v.key}/credentials`,
      )) as VendorLoginStatus
      setStatus(s)
      if (thenFetch && s.configured) p.fetchStores()
    } catch { /* unsupported / not configured — leave null */ }
  }
  // Auto-load stores when an account is configured — like ziniao fetching
  // profiles on account-select; no separate "Load" button.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { void refresh(true) }, [v.key])

  // One account per machine, like ziniao's picker but with only [+]:
  // adding OVERWRITES the single account. If any of this vendor's stores
  // is bound it belongs to the current account, so [+] is blocked —
  // surfaced the moment it's clicked — until the user deletes those
  // stores. There is no separate edit/delete; re-adding via [+] is how
  // you change the account.
  const hasVendorStores = p.allStores.some(s => s.browser_backend === v.key)
  const openAdd = () => {
    if (hasVendorStores) {
      setShowForm(false); setFormErr(t(i18nKey('OneAccount'))); return
    }
    setValues({}); setFormErr(''); setShowForm(true)
  }

  const save = async () => {
    const missing = v.credential_fields.filter(
      (f) => f.required && !(values[f.key] || '').trim(),
    )
    if (missing.length > 0) { setFormErr(t(i18nKey('LoginAllRequired'))); return }
    setBusy(true); setFormErr('')
    try {
      await api.post(`/api/browser-vendors/${v.key}/credentials`, values)
      setShowForm(false)
      setValues(prev => ({
        ...prev,
        ...Object.fromEntries(v.credential_fields.filter(f => f.secret).map(f => [f.key, ''])),
      }))
      await refresh(true)
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      let code = ''
      try { const d = JSON.parse(msg); if (d?.status) code = d.status } catch { /* non-JSON */ }
      setFormErr(code === 'one_account' ? t(i18nKey('OneAccount')) : msg)
    } finally { setBusy(false) }
  }

  // The docs section that says how to fix what the picker shows.
  const oneAccount = formErr === t(i18nKey('OneAccount'))
  const statusHint = (() => {
    if (!p.error) return ''
    const s = p.error.replace(`${v.key}:`, '')
    if (['login_required', 'client_not_running', 'no_stores'].includes(s)) {
      return t(i18nKey(camel(s)))
    }
    return t(i18nKey('LoadFailed'))
  })()

  const accountLabel = status?.configured
    ? v.credential_fields
        .filter(f => !f.secret)
        .map(f => String(status[f.key] ?? ''))
        .join(' / ')
    : t(i18nKey('NoAccount'))

  return (
    <div className="mb-2 space-y-2">
      {/* Account row — always visible (like ziniao's account select), with
          only [+]: it toggles the add form, which overwrites the account. */}
      <div className="flex items-center gap-1">
        <div className="min-w-0 flex-1 px-2 py-1.5 border border-gray-300 rounded text-xs bg-gray-50 truncate">
          {accountLabel}
        </div>
        <button type="button" onClick={() => (showForm ? setShowForm(false) : openAdd())} title={t('settings.addAccount')} className="flex-shrink-0 w-7 h-7 flex items-center justify-center text-sm rounded border border-gray-300 text-indigo-600 hover:bg-indigo-50">+</button>
      </div>
      {showForm && (
        <div className="space-y-1 p-2 bg-gray-50 rounded border border-gray-200">
          <p className="text-xs font-medium text-gray-600 mb-1">{t('settings.addAccount')}</p>
          {v.credential_fields.map(f => (
            f.secret ? (
              <div className="relative" key={f.key}>
                <input type={showSecret ? 'text' : 'password'} value={values[f.key] || ''} onChange={e => setValues(prev => ({ ...prev, [f.key]: e.target.value }))} placeholder={t(f.label_i18n)} className="w-full px-2 py-1 pr-7 border border-gray-300 rounded text-xs" />
                <button type="button" onClick={() => setShowSecret(x => !x)} className="absolute right-1.5 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600" tabIndex={-1}>
                  {showSecret ? EYE_ON : EYE_OFF}
                </button>
              </div>
            ) : (
              <input key={f.key} value={values[f.key] || ''} onChange={e => setValues(prev => ({ ...prev, [f.key]: e.target.value }))} placeholder={t(f.label_i18n)} className="w-full px-2 py-1 border border-gray-300 rounded text-xs" />
            )
          ))}
          <div className="flex gap-1">
            <button type="button" onClick={save} disabled={busy} className="px-2 py-1 bg-indigo-600 text-white rounded text-xs disabled:opacity-50">{busy ? t(i18nKey('LoginSaving')) : t('common.save')}</button>
            <button type="button" onClick={() => { setShowForm(false); setFormErr('') }} className="px-2 py-1 bg-gray-200 rounded text-xs">{t('common.cancel')}</button>
          </div>
        </div>
      )}
      {formErr && (
        <p className="text-[11px] text-red-600">
          {formErr}
          {oneAccount && v.docs.one_account && <> <DocsHelpLink urls={v.docs.one_account} /></>}
        </p>
      )}
      {/* Store area — mutually exclusive (like ziniao): error, else spinner,
          else the store select. */}
      {statusHint ? (
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <p className="text-[11px] text-red-600">{statusHint}</p>
          <button type="button" onClick={p.fetchStores} disabled={p.fetching} className="shrink-0 text-[11px] text-indigo-600 hover:underline disabled:opacity-50">{t('common.refresh')}</button>
          {v.docs.errors && <DocsHelpLink urls={v.docs.errors} />}
        </div>
      ) : status?.configured && p.fetching ? (
        <div className="flex flex-col items-center justify-center py-6 px-4 bg-indigo-50 rounded-lg border border-indigo-200">
          <div className="w-6 h-6 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin mb-3"></div>
          <p className="text-sm font-medium text-indigo-700">{t(i18nKey('Loading'))}</p>
        </div>
      ) : p.stores.length > 0 ? (
        <select
          value={p.selectedRef}
          onChange={e => p.setSelectedRef(e.target.value)}
          className="w-full px-2 py-1.5 border border-gray-300 rounded text-xs bg-white"
        >
          <option value="">{t(i18nKey('SelectStore'))}</option>
          {p.stores.map(s => (
            <option key={s.name} value={s.name}>
              {s.name}{s.platform ? ` (${s.platform} ${s.remote_env})` : ''}
            </option>
          ))}
        </select>
      ) : null}
    </div>
  )
}
