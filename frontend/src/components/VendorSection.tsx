/**
 * The generic browser-vendor section of the create-store form, rendered
 * from the vendor's metadata (GET /api/browser-vendors): the machine-login
 * row (for one-account-per-machine vendors, with only a [+]), the
 * credential form (fields from the spec, secrets masked with an eye
 * toggle), and the profile picker (stores from the generic route, with
 * the structured statuses decoded into text).
 *
 * Every derived string resolves through a fallback chain: the vendor's
 * own key `<i18n_prefix><Suffix>` first (its override), else the SHARED
 * `browserVendor.<Suffix>` key interpolating `{{vendor}}` with the
 * display name — so a new vendor ships only its credential field labels
 * plus the overrides it wants, and no branch in this file names a
 * vendor. A status neither key covers falls back to the backend's own
 * message.
 *
 * Profiles bind by `ref || name` (the stable id the vendor reports;
 * vendors without one use the name), and an env that exists but cannot
 * be opened (`available === false`) is shown greyed out with its
 * reason instead of being hidden.
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

/** The value a profile binds and reopens by. */
const bindValue = (s: VendorStoreRef) => s.ref || s.name

export function VendorSection(props: {
  vendor: BrowserVendor
  selectedRef: string; setSelectedRef: (v: string) => void
  stores: VendorStoreRef[]; error: string; errorMessage?: string
  fetching: boolean; fetchStores: () => void
  allStores: Store[]
}) {
  const { t, i18n } = useTranslation()
  const v = props.vendor
  const p = props
  const [status, setStatus] = useState<VendorLoginStatus | null>(null)
  const [showForm, setShowForm] = useState(false)
  const [values, setValues] = useState<Record<string, string>>({})
  const [showSecret, setShowSecret] = useState(false)
  const [busy, setBusy] = useState(false)
  const [formErr, setFormErr] = useState('')

  const zh = (i18n.language || 'en').startsWith('zh')
  const displayName = zh ? v.display_name_zh : v.display_name_en
  /** The fallback chain: the vendor's own override, else the shared
   *  `browserVendor.<Suffix>` key with {{vendor}} interpolated. */
  const vt = (suffix: string) => {
    const own = t(`${v.i18n_prefix}${suffix}`, {
      defaultValue: '', vendor: displayName,
    })
    if (own) return own
    return t(`browserVendor.${suffix}`, {
      defaultValue: '', vendor: displayName,
    })
  }

  /** Why a greyed-out env cannot be opened: the contract's stable
   *  token translated via the shared browserVendor.unavailable.<token>
   *  key, with the vendor's own detail as the fallback (and always as
   *  the tooltip). Free text never leaks into the label untranslated —
   *  a token with no translation falls back to the detail, then to the
   *  generic 'other' string. */
  const unavailableLabel = (s: VendorStoreRef): string => {
    if (s.available !== false) return ''
    const detail = s.unavailable_detail || ''
    if (s.unavailable_reason) {
      const token = t(`browserVendor.unavailable.${s.unavailable_reason}`, {
        defaultValue: '',
      })
      if (token) return token
    }
    if (detail) return detail
    return t('browserVendor.unavailable.other', { defaultValue: '' })
  }

  // A 'single' vendor has a machine login to configure (the account row
  // + form); a 'none' vendor has none — the user signs in once in the
  // client and the local API acts as that login — so its stores load
  // straight away.
  const needsAccount = v.account_cardinality === 'single'

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
  // profiles on account-select; no separate "Load" button. A vendor
  // without an account row fetches on mount directly.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (needsAccount) void refresh(true)
    else void p.fetchStores()
  }, [v.key])

  // One account per machine, like ziniao's picker but with only [+]:
  // adding OVERWRITES the single account. If any of this vendor's stores
  // is bound it belongs to the current account, so [+] is blocked —
  // surfaced the moment it's clicked — until the user deletes those
  // stores. There is no separate edit/delete; re-adding via [+] is how
  // you change the account.
  const hasVendorStores = p.allStores.some(s => s.browser_backend === v.key)
  const openAdd = () => {
    if (hasVendorStores) {
      setShowForm(false); setFormErr(vt('OneAccount')); return
    }
    setValues({}); setFormErr(''); setShowForm(true)
  }

  const save = async () => {
    const missing = v.credential_fields.filter(
      (f) => f.required && !(values[f.key] || '').trim(),
    )
    if (missing.length > 0) { setFormErr(vt('LoginAllRequired')); return }
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
      setFormErr(code === 'one_account' ? vt('OneAccount') : msg)
    } finally { setBusy(false) }
  }

  // The docs section that says how to fix what the picker shows.
  const oneAccount = formErr === vt('OneAccount')
  // A status resolves: the vendor's override key, else the shared
  // per-code key, else the backend's own message, else the load-failed
  // default. (no_stores is a UI state, not an error code; connect_error
  // has neither key nor message.)
  const statusHint = (() => {
    if (!p.error) return ''
    const s = p.error.replace(`${v.key}:`, '')
    const text = vt(camel(s))
    if (text) return text
    if (p.errorMessage) return p.errorMessage
    return vt('LoadFailed')
  })()

  const accountLabel = status?.configured
    ? v.credential_fields
        .filter(f => !f.secret)
        .map(f => String(status[f.key] ?? ''))
        .join(' / ')
    : vt('NoAccount')

  return (
    <div className="mb-2 space-y-2">
      {/* Account row — only for vendors with a machine login (one per
          machine, like ziniao's account select), with only [+]: it
          toggles the add form, which overwrites the account. */}
      {needsAccount && (
      <div className="flex items-center gap-1">
        <div className="min-w-0 flex-1 px-2 py-1.5 border border-gray-300 rounded text-xs bg-gray-50 truncate">
          {accountLabel}
        </div>
        <button type="button" onClick={() => (showForm ? setShowForm(false) : openAdd())} title={t('settings.addAccount')} className="flex-shrink-0 w-7 h-7 flex items-center justify-center text-sm rounded border border-gray-300 text-indigo-600 hover:bg-indigo-50">+</button>
      </div>
      )}
      {needsAccount && showForm && (
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
            <button type="button" onClick={save} disabled={busy} className="px-2 py-1 bg-indigo-600 text-white rounded text-xs disabled:opacity-50">{busy ? vt('LoginSaving') : t('common.save')}</button>
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
      ) : (v.account_cardinality === 'none' || status?.configured) && p.fetching ? (
        <div className="flex flex-col items-center justify-center py-6 px-4 bg-indigo-50 rounded-lg border border-indigo-200">
          <div className="w-6 h-6 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin mb-3"></div>
          <p className="text-sm font-medium text-indigo-700">{vt('Loading')}</p>
        </div>
      ) : p.stores.length > 0 ? (
        <select
          value={p.selectedRef}
          onChange={e => p.setSelectedRef(e.target.value)}
          className="w-full px-2 py-1.5 border border-gray-300 rounded text-xs bg-white"
        >
          <option value="">{vt('SelectStore')}</option>
          {p.stores.map(s => (
            <option
              key={bindValue(s)}
              value={bindValue(s)}
              disabled={s.available === false}
              title={s.available === false ? s.unavailable_detail || undefined : undefined}
            >
              {s.name}{s.platform ? ` (${s.platform} ${s.remote_env})` : ''}
              {s.available === false && unavailableLabel(s)
                ? ` — ${unavailableLabel(s)}`
                : ''}
            </option>
          ))}
        </select>
      ) : null}
    </div>
  )
}
